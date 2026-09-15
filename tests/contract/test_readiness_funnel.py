"""Regression tests for the readiness funnel reported by status().

C2 fix: status() now includes a readiness_funnel key that surfaces a
conservative, evidence-backed progression of what has actually been verified
in this deployment. Each stage is derived from job status counts and deliberately
avoids overclaiming readiness at stages that cannot be proven from job counts alone.

Key design constraint: PARTIAL alone must NOT advance source_archival to done.
A PARTIAL job may result from a pre-download failure (e.g. SourcePartialError
raised before any source bytes were fetched), which provides no evidence that
source bytes were actually received and hashed. Only READY provides that proof.

The fixed/unfixed boundary is verified with the revert-test-restore method
applied to _readiness_funnel directly (no SQLite required).
"""
from __future__ import annotations

import pathlib
import sys

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from uls.behavior import asset_root
from uls.cli.main import _config, _readiness_funnel, initialize, status

pytestmark = pytest.mark.contract


def test_empty_job_table_reports_not_started():
    funnel = _readiness_funnel({})
    assert funnel["source_archival"] == "not_started"
    assert funnel["text_extraction"] == "not_started"


def test_only_pending_jobs_reports_not_started():
    funnel = _readiness_funnel({"PENDING": 3, "PROCESSING": 1})
    assert funnel["source_archival"] == "not_started"
    assert funnel["text_extraction"] == "not_started"


def test_partial_alone_does_not_prove_source_archival():
    """Regression: PARTIAL alone must not advance source_archival to done.

    A PARTIAL job may result from SourcePartialError raised before any source
    bytes are fetched (e.g. file size limit exceeded before download starts).
    The job-count aggregate cannot distinguish this from a genuine partial
    extraction, so PARTIAL alone must stay not_proven, not done.
    """
    funnel = _readiness_funnel({"PARTIAL": 2})
    assert funnel["source_archival"] == "not_proven"
    assert funnel["text_extraction"] == "not_proven"


def test_ready_jobs_report_done_for_both_stages():
    funnel = _readiness_funnel({"READY": 5})
    assert funnel["source_archival"] == "done"
    assert funnel["text_extraction"] == "done"


def test_mixed_ready_and_partial_reports_done_for_both():
    """READY is sufficient to prove both stages regardless of other statuses."""
    funnel = _readiness_funnel({"READY": 1, "PARTIAL": 3, "FAILED": 2})
    assert funnel["source_archival"] == "done"
    assert funnel["text_extraction"] == "done"


def test_failed_jobs_alone_report_not_proven():
    """FAILED/NEEDS_REVIEW alone cannot prove source bytes were received."""
    funnel = _readiness_funnel({"FAILED": 10, "NEEDS_REVIEW": 2})
    assert funnel["source_archival"] == "not_proven"
    assert funnel["text_extraction"] == "not_proven"


def test_pending_with_partial_reports_not_started():
    """Active work in progress: still not_started (pending takes priority)."""
    funnel = _readiness_funnel({"PENDING": 1, "PARTIAL": 3})
    assert funnel["source_archival"] == "not_started"
    assert funnel["text_extraction"] == "not_started"


def test_retrieval_credentials_never_claims_checked():
    for counts in ({}, {"READY": 5}, {"PARTIAL": 3}):
        funnel = _readiness_funnel(counts)
        assert funnel["retrieval_credentials"] == "not_checked_here"
        assert "doctor" in funnel["retrieval_credentials_note"]


def test_ai_client_is_always_not_proven():
    for counts in ({}, {"READY": 5}, {"PARTIAL": 3}):
        funnel = _readiness_funnel(counts)
        assert funnel["ai_client"] == "not_proven"


def _write_config(tmp_path):
    raw = yaml.safe_load((asset_root() / "config.example.yaml").read_text(encoding="utf-8"))
    raw["system"]["workspace_dir"] = "state"
    raw["behavior_contract"]["path"] = str(asset_root() / "contracts/study-behavior.md")
    raw["worker"]["enabled"] = False
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    initialize(path)
    return _config(path)


def _advance_to_terminal(state, job_id, target_status):
    state.claim_job(job_id)
    state.complete_job(job_id, target_status)


def test_status_includes_readiness_funnel_key(tmp_path):
    config = _write_config(tmp_path)
    result = status(config)
    assert "readiness_funnel" in result
    funnel = result["readiness_funnel"]
    assert funnel["source_archival"] == "not_started"
    assert funnel["text_extraction"] == "not_started"
    assert funnel["retrieval_credentials"] == "not_checked_here"
    assert funnel["ai_client"] == "not_proven"


def test_status_funnel_ready_job_advances_both_stages(tmp_path):
    from uls.orchestration.jobs import derive_job_key
    from uls.runtime import state_path
    from uls.state.sqlite import SQLiteStateStore

    config = _write_config(tmp_path)

    with SQLiteStateStore(state_path(config)) as state:
        key_ready = derive_job_key("src-a", "hash-a", "INGEST", "1.2.0")
        key_partial = derive_job_key("src-b", "hash-b", "INGEST", "1.2.0")
        j_ready = state.create_job(key_ready, operation="INGEST", stage="norm", target_entity_id="ENT-1")
        j_partial = state.create_job(key_partial, operation="INGEST", stage="norm", target_entity_id="ENT-2")
        _advance_to_terminal(state, j_ready.id, "READY")
        _advance_to_terminal(state, j_partial.id, "PARTIAL")

    result = status(config)
    funnel = result["readiness_funnel"]
    assert funnel["source_archival"] == "done"
    assert funnel["text_extraction"] == "done"
    assert funnel["ai_client"] == "not_proven"


def test_status_funnel_partial_only_stays_not_proven(tmp_path):
    """Regression: pre-download PARTIAL must not advance source_archival.

    This is the concrete scenario from the C2 review: a file that exceeds the
    size limit causes SourcePartialError before any bytes are fetched, which
    records job status PARTIAL. The funnel must not interpret this as done.
    """
    from uls.orchestration.jobs import derive_job_key
    from uls.runtime import state_path
    from uls.state.sqlite import SQLiteStateStore

    config = _write_config(tmp_path)

    with SQLiteStateStore(state_path(config)) as state:
        key = derive_job_key("src-c", "hash-c", "INGEST", "1.2.0")
        j = state.create_job(key, operation="INGEST", stage="norm", target_entity_id="ENT-3")
        _advance_to_terminal(state, j.id, "PARTIAL")

    result = status(config)
    funnel = result["readiness_funnel"]
    assert funnel["source_archival"] == "not_proven"
    assert funnel["text_extraction"] == "not_proven"
