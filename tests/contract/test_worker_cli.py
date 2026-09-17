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
    raw = yaml.safe_load((asset_root() / 'config.example.yaml').read_text(encoding='utf-8'))
    raw['system']['workspace_dir'] = 'state'
    raw['behavior_contract']['path'] = str(asset_root() / 'contracts/study-behavior.md')
    raw['worker']['enabled'] = False
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    original = path.read_bytes()
    assert main(['--config', str(path), 'init']) == 0
    assert path.read_bytes() == original
    assert (tmp_path / 'state/state.sqlite3').is_file()
    assert json.loads((tmp_path / 'state/sources.json').read_text(encoding='utf-8')) == []
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
    assert plist['ProgramArguments'][0].endswith('/uls')
    assert plist['ProgramArguments'][-1] == 'run'
    task = ET.parse(root / 'deployment/windows/uls-task.xml')
    ns = {'s': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}
    assert task.find('.//s:Command', ns).text.endswith('uls.exe')
    assert task.find('.//s:Arguments', ns).text.endswith(' run')
    assert task.find('.//s:MultipleInstancesPolicy', ns).text == 'IgnoreNew'


def test_windows_task_scheduler_runs_unattended_while_logged_out():
    """Regression: InteractiveToken only runs during an active console logon session.

    A prior template shipped LogonType=InteractiveToken, which silently fails to
    satisfy "runs whenever the PC is on" for a worker meant to run while the
    configured account is logged out. Password logon type runs regardless of
    logon state once the account password is registered with Task Scheduler
    (out of band, never checked into this XML).
    """
    import xml.etree.ElementTree as ET

    root = Path(__file__).resolve().parents[2]
    task = ET.parse(root / 'deployment/windows/uls-task.xml')
    ns = {'s': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}
    logon_type = task.find('.//s:Principal/s:LogonType', ns)
    assert logon_type is not None
    assert logon_type.text == 'Password'
    assert logon_type.text != 'InteractiveToken'
    # UserId must be present for Password logon type, and must never carry a
    # real credential in the checked-in template.
    user_id = task.find('.//s:Principal/s:UserId', ns)
    assert user_id is not None and user_id.text
    # Password logon type never stores the secret in the XML itself; only an
    # out-of-band registration step (schtasks /rp or Register-ScheduledTask
    # -Password) may supply it. No <Password> element should exist here.
    assert task.find('.//s:Principal/s:Password', ns) is None


def test_google_credentials_substitution_race_prevention(tmp_path, monkeypatch):
    """Contract: verifying and loading Google credentials binds to the
    in-memory payload acquired at diagnosis time (docs/plans/credential-secret-file-launcher.md rev5 §8.2).
    If the file on disk is replaced or tampered with immediately after diagnose(),
    downstream provider consumption uses only the payload verified at diagnosis time.
    """
    import json

    import google.auth

    from uls.config._secure_file import write_secure_file
    from uls.config.credentials import CredentialResolver
    from uls.runtime import google_service

    captured_info = []
    def fake_load_dict(info, scopes=None, **kwargs):
        captured_info.append(info)
        return None, None

    monkeypatch.setattr(google.auth, 'load_credentials_from_dict', fake_load_dict)
    import googleapiclient.discovery
    monkeypatch.setattr(googleapiclient.discovery, 'build', lambda *args, **kwargs: 'fake_service')

    creds_file = tmp_path / 'credentials.json'
    original_payload = {'type': 'service_account', 'client_email': 'original@example.com'}
    write_secure_file(creds_file, json.dumps(original_payload).encode('utf-8'))

    # Diagnose once through CredentialResolver
    resolver = CredentialResolver({}, environ={'GOOGLE_MCP_CREDENTIALS_FILE': str(creds_file)})
    snapshot = resolver.resolve(required=frozenset({'GOOGLE_MCP_CREDENTIALS_FILE'}))
    payload = snapshot.get_google_payload('GOOGLE_MCP_CREDENTIALS_FILE')
    assert payload is not None

    # Disk file is now replaced with an attacker file / deleted / modified
    attacker_payload = {'type': 'service_account', 'client_email': 'attacker@example.com'}
    creds_file.write_text(json.dumps(attacker_payload), encoding='utf-8')
    creds_file.chmod(0o644)  # world readable

    # Provider consumption must use the payload captured at diagnosis time
    google_service(payload, read_only=True)
    assert len(captured_info) == 1
    assert captured_info[0]['client_email'] == 'original@example.com'


def test_cli_credential_set_guidance_and_execution(tmp_path, monkeypatch, capsys):
    """Contract: uls credential set NAME returns guidance when undeclared,
    and does not accept a --value CLI argument.
    """
    raw = yaml.safe_load((asset_root() / 'config.example.yaml').read_text(encoding='utf-8'))
    raw['system']['workspace_dir'] = 'state'
    raw['behavior_contract']['path'] = str(asset_root() / 'contracts/study-behavior.md')
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(raw), encoding='utf-8')

    # When undeclared, outputs guidance only and exits 0
    assert main(['--config', str(path), 'credential', 'set', 'NOTION_WORKER_TOKEN']) == 0
    out = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert out['status'] == 'guidance_only'
    assert 'credentials:' in out['add_to_config']

    # Attempting to pass --value fails with CLI parse error (SystemExit 2)
    with pytest.raises(SystemExit) as exc_info:
        main(['--config', str(path), 'credential', 'set', 'NOTION_WORKER_TOKEN', '--value', 'leak'])
    assert exc_info.value.code == 2
