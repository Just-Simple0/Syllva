"""Cross-process compare-and-swap, crash injection, and target-bound recovery for config apply."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time

import pytest
import yaml
from _settings_support import make_harness, reviewed_apply

from uls.config.mutation import ConfigLockTimeout
from uls.settings import config_service
from uls.settings.config_service import ConfigStore, SettingsServiceError
from uls.settings.journal import SimulatedCrash

pytestmark = pytest.mark.contract

_WORKER = """
import json, sys, time
from pathlib import Path
from uls.settings.config_service import ConfigStore, SettingsServiceError
from uls.settings.journal import JournalStore
config, workspace, generation, zone, go = sys.argv[1:6]
store = ConfigStore(config)
values = {'system.timezone': zone}
reviewed = store.preview('general', values, generation)
while not Path(go).exists():
    time.sleep(0.001)
try:
    result = store.apply('general', values, generation, JournalStore(workspace),
                         candidate_hash=reviewed['candidate_hash'])
    print(json.dumps({'status': result['status'], 'zone': zone}))
except SettingsServiceError as exc:
    print(json.dumps({'status': exc.code, 'zone': zone}))
"""

_RECOVER = """
import json, sys
from uls.settings.config_service import ConfigStore, SettingsServiceError
from uls.settings.journal import JournalStore
journal = JournalStore(sys.argv[1])
results = []
for pending in journal.unresolved():
    record = journal.read(pending['operation_id'])
    # Only the record's own binding selects the store to reconcile against.
    store = ConfigStore(record['binding']['config_path'])
    try:
        results.append(store.recover(journal, pending['operation_id'], sys.argv[2])['status'])
    except SettingsServiceError as exc:
        results.append(exc.code)
print(json.dumps(results))
"""


def test_two_processes_from_one_generation_commit_exactly_once(tmp_path):
    h = make_harness(tmp_path)
    generation = h.store.load().generation
    go = tmp_path / "go"
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", _WORKER, str(h.config_path), str(tmp_path / "workspace"),
             generation, zone, str(go)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for zone in ("Europe/Paris", "America/New_York")
    ]
    time.sleep(0.5)
    go.write_text("go")
    results = []
    for worker in workers:
        out, err = worker.communicate(timeout=30)
        assert worker.returncode == 0, err
        results.append(json.loads(out))
    statuses = sorted(result["status"] for result in results)
    assert statuses == ["CONFIGURATION_CHANGED", "applied"]
    winner = next(result["zone"] for result in results if result["status"] == "applied")
    data = yaml.safe_load(h.config_path.read_text(encoding="utf-8"))
    assert data["system"]["timezone"] == winner
    assert data["x_unknown_section"]["keep"] == [1, 2]
    # A loser that created its record before losing the CAS terminated it as
    # resolved_without_change: no unresolved recovery entry remains.
    assert h.journal.unresolved() == []
    phases = sorted(json.loads(path.read_text())["phase"] for path in h.journal.directory.glob("*.json"))
    assert phases in (["complete"], ["complete", "resolved_without_change"])


def test_cas_loss_after_record_creation_resolves_that_record(tmp_path, monkeypatch):
    h = make_harness(tmp_path)
    generation = h.store.load().generation
    preview = h.store.preview("general", {"system.timezone": "UTC"}, generation)
    original_create = h.journal.create_config_operation

    def create_then_race(**kwargs):
        operation_id = original_create(**kwargs)
        # Another writer commits between record creation and the config lock.
        h.config_path.write_text(h.config_path.read_text().replace("Asia/Seoul", "Asia/Tokyo"))
        return operation_id

    monkeypatch.setattr(h.journal, "create_config_operation", create_then_race)
    with pytest.raises(SettingsServiceError) as error:
        h.store.apply("general", {"system.timezone": "UTC"}, generation, h.journal,
                      candidate_hash=preview["candidate_hash"])
    assert error.value.code == "CONFIGURATION_CHANGED"
    assert h.journal.unresolved() == []
    [record] = [json.loads(path.read_text()) for path in h.journal.directory.glob("*.json")]
    assert record["phase"] == "resolved_without_change"


def test_config_lock_timeout_before_side_effects_resolves_record(tmp_path, monkeypatch):
    h = make_harness(tmp_path)
    before = h.config_path.read_bytes()
    generation = h.store.load().generation
    preview = h.store.preview("general", {"system.timezone": "UTC"}, generation)
    calls = {"n": 0}
    real_acquire = config_service.ConfigFileLock.acquire

    def acquire(self):
        calls["n"] += 1
        return real_acquire(self) if calls["n"] == 1 else False  # load succeeds, apply times out

    monkeypatch.setattr(config_service.ConfigFileLock, "acquire", acquire)
    with pytest.raises(ConfigLockTimeout):
        h.store.apply("general", {"system.timezone": "UTC"}, generation, h.journal,
                      candidate_hash=preview["candidate_hash"])
    assert h.config_path.read_bytes() == before
    assert h.journal.unresolved() == []


def _crash_at(point):
    def hook(name):
        if name == point:
            raise SimulatedCrash(name)
    return hook


def test_crash_before_replace_recovers_as_no_change(tmp_path):
    h = make_harness(tmp_path)
    before = h.config_path.read_bytes()
    with pytest.raises(SimulatedCrash):
        reviewed_apply(h.store, "general", {"system.timezone": "UTC"}, h.journal,
                       fault_hook=_crash_at("before_config_replace"))
    assert h.config_path.read_bytes() == before
    [pending] = h.journal.unresolved()
    assert pending["phase"] == "effect_intent"
    with pytest.raises(SettingsServiceError) as error:
        h.store.recover(h.journal, pending["operation_id"], "resume")
    assert error.value.code == "NO_COMMIT_TO_RESUME"
    assert h.store.recover(h.journal, pending["operation_id"], "leave")["status"] == "resolved_without_change"
    assert h.journal.unresolved() == []


@pytest.mark.parametrize("point", ["after_config_replace", "after_config_replace_recorded"])
def test_crash_after_replace_is_verified_in_a_fresh_process_without_rewrite(tmp_path, point):
    h = make_harness(tmp_path)
    with pytest.raises(SimulatedCrash):
        reviewed_apply(h.store, "general", {"system.timezone": "UTC"}, h.journal,
                       fault_hook=_crash_at(point))
    committed = h.config_path.read_bytes()
    assert yaml.safe_load(committed)["system"]["timezone"] == "UTC"
    proc = subprocess.run([sys.executable, "-c", _RECOVER, str(tmp_path / "workspace"), "resume"],
                          capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == ["completed"]
    assert h.config_path.read_bytes() == committed
    assert h.journal.unresolved() == []


def test_verified_then_superseded_is_not_attributed_to_the_newer_generation(tmp_path):
    h = make_harness(tmp_path)
    with pytest.raises(SimulatedCrash):
        reviewed_apply(h.store, "general", {"system.timezone": "UTC"}, h.journal,
                       fault_hook=_crash_at("after_config_replace_recorded"))
    [pending] = h.journal.unresolved()
    record = h.journal.read(pending["operation_id"])
    verified_post = record["effects"]["config_replace"]["post_state_id"]
    newer = reviewed_apply(h.store, "general", {"system.timezone": "Asia/Tokyo"}, h.journal)
    assert newer["generation"] != verified_post
    result = h.store.recover(h.journal, pending["operation_id"], "resume")
    assert result["status"] == "completed_then_superseded"
    final = h.journal.read(pending["operation_id"])
    assert final["phase"] == "completed_then_superseded"
    assert final.get("readback_generation") in (None, verified_post)
    assert final["effects"]["config_replace"]["post_state_id"] == verified_post
    assert h.journal.unresolved() == []


def test_crash_then_concurrent_edit_stays_partial_without_overwrite(tmp_path):
    h = make_harness(tmp_path)
    with pytest.raises(SimulatedCrash):
        reviewed_apply(h.store, "general", {"system.timezone": "UTC"}, h.journal,
                       fault_hook=_crash_at("before_config_replace"))
    [pending] = h.journal.unresolved()
    other = reviewed_apply(h.store, "general", {"system.timezone": "Asia/Tokyo"}, h.journal)
    edited = h.config_path.read_bytes()
    with pytest.raises(SettingsServiceError) as error:
        h.store.recover(h.journal, pending["operation_id"], "resume")
    assert error.value.code == "CONFIGURATION_STATE_CHANGED"
    assert h.store.recover(h.journal, pending["operation_id"], "leave")["status"] == "left_pending"
    assert h.config_path.read_bytes() == edited
    assert [item["operation_id"] for item in h.journal.unresolved()] == [pending["operation_id"]]
    assert other["status"] == "applied"


def test_recovery_refuses_a_record_created_for_another_config_target(tmp_path):
    h = make_harness(tmp_path)
    twin = tmp_path / "twin"
    twin.mkdir()
    twin_config = twin / "config.yaml"
    shutil.copy2(h.config_path, twin_config)  # identical bytes, same workspace/journal
    with pytest.raises(SimulatedCrash):
        reviewed_apply(h.store, "general", {"system.timezone": "UTC"}, h.journal,
                       fault_hook=_crash_at("after_config_replace"))
    [pending] = h.journal.unresolved()
    other = ConfigStore(twin_config)
    assert other.load().raw["system"]["timezone"] == "Asia/Seoul"  # twin still at G0
    for action in ("leave", "resume"):
        with pytest.raises(SettingsServiceError) as error:
            other.recover(h.journal, pending["operation_id"], action)
        assert error.value.code == "OPERATION_OTHER_TARGET"
    assert [item["operation_id"] for item in h.journal.unresolved()] == [pending["operation_id"]]
    assert h.store.recover(h.journal, pending["operation_id"], "resume")["status"] == "completed"
