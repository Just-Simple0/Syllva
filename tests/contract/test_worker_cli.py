from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from uls.behavior import asset_root
from uls.cli.main import main
from uls.domain.enums import JobStatus
from uls.domain.errors import PolicyDeniedError, ProviderUnavailableError
from uls.orchestration.jobs import derive_job_key
from uls.orchestration.runner import WorkerRunner
from uls.state.sqlite import SQLiteStateStore

pytestmark = pytest.mark.contract


def job(state, operation='TEST'):
    return state.create_job(derive_job_key('source', 'hash', operation, '1.2.0'), operation=operation, stage='test',
                            target_entity_id='COMP319-S01')


def test_runner_runs_once_preserves_partial_and_releases_lock(tmp_path):
    with SQLiteStateStore(tmp_path / 'state.db') as state:
        created = job(state)
        runner = WorkerRunner(state, {'TEST': lambda item: JobStatus.PARTIAL})
        result = runner.run_once()
        assert result['processed'] == result['partial'] == 1
        assert state.get_job(created.id).status == JobStatus.PARTIAL
        assert runner.run_once()['processed'] == 0
        assert state.acquire_local_worker_lock()
        state.release_local_worker_lock()


def test_transient_retry_is_bounded_and_policy_failure_never_retries(tmp_path):
    with SQLiteStateStore(tmp_path / 'state.db') as state:
        created = job(state)
        calls, waits = [], []
        def transient(item):
            calls.append(item.id)
            raise ProviderUnavailableError('private token must not be persisted')
        result = WorkerRunner(state, {'TEST': transient}, sleep=waits.append).run_once()
        assert len(calls) == 3 and len(waits) == 2
        assert result['failed'] == 1
        assert 'private' not in (state.get_job(created.id).last_error or '')
        denied = job(state, 'DENIED')
        def policy(item):
            raise PolicyDeniedError('human approval required')
        WorkerRunner(state, {'DENIED': policy}, sleep=waits.append).run_once()
        assert state.get_job(denied.id).attempt_count == 1
        assert state.get_job(denied.id).error_class == 'POLICY_DENIED'


def test_unhandled_operation_is_needs_review(tmp_path):
    with SQLiteStateStore(tmp_path / 'state.db') as state:
        created = job(state)
        result = WorkerRunner(state, {}).run_once()
        assert result['status'] == 'needs_review'
        assert state.get_job(created.id).status == JobStatus.NEEDS_REVIEW


def test_reprocess_is_explicit_and_preserves_job_identity(tmp_path):
    with SQLiteStateStore(tmp_path / 'state.db') as state:
        created = job(state)
        WorkerRunner(state, {'TEST': lambda _: JobStatus.READY}).run_once()
        assert state.acquire_local_worker_lock()
        updated = state.request_reprocess(created.id)
        state.release_local_worker_lock()
        assert updated.job_key == created.job_key
        assert updated.status == JobStatus.PENDING
        assert updated.attempt_count == 0
        with pytest.raises(ValueError):
            state.request_reprocess(created.id)


def test_cli_init_status_jobs_and_disabled_worker_no_credentials(tmp_path, capsys):
    raw = yaml.safe_load((asset_root() / 'config.example.yaml').read_text())
    raw['system']['workspace_dir'] = 'state'
    raw['behavior_contract']['path'] = str(asset_root() / 'contracts/study-behavior.md')
    raw['worker']['enabled'] = False
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(raw))
    original = path.read_bytes()
    assert main(['--config', str(path), 'init']) == 0
    assert path.read_bytes() == original
    assert (tmp_path / 'state/state.sqlite3').is_file()
    assert json.loads((tmp_path / 'state/sources.json').read_text()) == []
    assert main(['--config', str(path), 'status']) == 0
    assert main(['--config', str(path), 'jobs']) == 0
    assert main(['--config', str(path), 'run']) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])['status'] == 'disabled'


def test_cli_invalid_config_reports_safe_error_only(tmp_path, capsys):
    path = tmp_path / 'bad.yaml'
    path.write_text('system: fake-private-note')
    assert main(['--config', str(path), 'status']) == 2
    output = capsys.readouterr()
    assert 'fake-private-note' not in output.err
    assert 'Traceback' not in output.err


def test_both_scheduler_definitions_invoke_identical_run_command():
    import plistlib
    import xml.etree.ElementTree as ET
    root = Path(__file__).resolve().parents[2]
    plist = plistlib.loads((root / 'deployment/macos/com.syllva.uls.plist').read_bytes())
    assert plist['ProgramArguments'][-1] == 'run'
    task = ET.parse(root / 'deployment/windows/uls-task.xml')
    ns = {'s': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}
    assert task.find('.//s:Arguments', ns).text.endswith(' run')
    assert task.find('.//s:MultipleInstancesPolicy', ns).text == 'IgnoreNew'
