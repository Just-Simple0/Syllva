"""Regression tests for the readiness funnel reported by status().

C2 fix: status() includes a readiness_funnel key that surfaces a
conservative, evidence-backed progression of what has actually been verified
in this deployment.

Key design constraints:
1. PARTIAL alone must NOT advance source_archival to done: pre-download
   failures (e.g. SourcePartialError) create PARTIAL jobs without source bytes
   ever being received or hashed.
2. Non-normalization jobs (e.g. enrich_session, enrich_material) marked READY
   must NOT advance source_archival or text_extraction to done: enrichment
   records AI-zone publishing, not source archival or normalized text extraction.
3. Normalization jobs marked READY without matching durable processing records
   and source provenance must NOT advance text_extraction to done.
4. Only confirmed durable source records (source_files joined with current
   source_versions) advance source_archival to done.
5. Only confirmed normalization processing records (joined with source_files,
   jobs, and valid output_ref_json, excluding enrichment operations) advance
   text_extraction to done.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from uls.behavior import asset_root
from uls.cli.main import _config, _readiness_funnel, initialize, status
from uls.orchestration.jobs import derive_job_key
from uls.runtime import state_path
from uls.state.sqlite import SQLiteStateStore

pytestmark = pytest.mark.contract

COURSE_KEY = "2026-1_COMP319-01"
CANONICAL_ENTITY_ID = "COMP319-S01"


def test_empty_job_table_reports_not_started():
    funnel = _readiness_funnel({})
    assert funnel["source_archival"] == "not_started"
    assert funnel["text_extraction"] == "not_started"


def test_only_pending_jobs_reports_not_started():
    funnel = _readiness_funnel({"PENDING": 3, "PROCESSING": 1})
    assert funnel["source_archival"] == "not_started"
    assert funnel["text_extraction"] == "not_started"


def test_partial_alone_does_not_prove_source_archival():
    """Regression: PARTIAL alone must not advance source_archival to done."""
    funnel = _readiness_funnel({"PARTIAL": 2})
    assert funnel["source_archival"] == "not_proven"
    assert funnel["text_extraction"] == "not_proven"


def test_failed_jobs_alone_report_not_proven():
    funnel = _readiness_funnel({"FAILED": 10, "NEEDS_REVIEW": 2})
    assert funnel["source_archival"] == "not_proven"
    assert funnel["text_extraction"] == "not_proven"


def test_pending_with_partial_reports_not_started():
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


def test_status_uninitialized_includes_readiness_funnel(tmp_path):
    raw = yaml.safe_load((asset_root() / "config.example.yaml").read_text(encoding="utf-8"))
    raw["system"]["workspace_dir"] = "uninitialized_state"
    raw["behavior_contract"]["path"] = str(asset_root() / "contracts/study-behavior.md")
    raw["worker"]["enabled"] = False
    path = tmp_path / "config_uninit.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    config = _config(path)
    result = status(config)
    assert result["status"] == "not_initialized"
    assert "readiness_funnel" in result
    funnel = result["readiness_funnel"]
    assert funnel["source_archival"] == "not_started"
    assert funnel["text_extraction"] == "not_started"


def test_status_includes_readiness_funnel_key_on_fresh_install(tmp_path):
    config = _write_config(tmp_path)
    result = status(config)
    assert "readiness_funnel" in result
    funnel = result["readiness_funnel"]
    assert funnel["source_archival"] == "not_started"
    assert funnel["text_extraction"] == "not_started"
    assert funnel["retrieval_credentials"] == "not_checked_here"
    assert funnel["ai_client"] == "not_proven"


def test_enrichment_ready_alone_does_not_advance_funnel(tmp_path):
    """Regression: non-normalization jobs (e.g. enrich_session, enrich_material)
    marked READY must not advance source_archival or text_extraction.
    """
    config = _write_config(tmp_path)
    with SQLiteStateStore(state_path(config)) as store:
        for op in ("enrich_session", "enrich_material"):
            job_key = derive_job_key("enrich_target", "hash", op, "1.2.0")
            j = store.create_job(job_key, operation=op, stage="enrich",
                                 target_entity_id=CANONICAL_ENTITY_ID)
            store.claim_job(j.id)
            store.complete_job(j.id, "READY")

    result = status(config)
    funnel = result["readiness_funnel"]
    assert funnel["source_archival"] == "not_proven"
    assert funnel["text_extraction"] == "not_proven"


def test_normalization_ready_without_processing_record_does_not_advance_text_extraction(tmp_path):
    """Regression: normalization operation with READY job status but missing
    processing record or source file link must not count as verified text extraction.
    """
    config = _write_config(tmp_path)
    with SQLiteStateStore(state_path(config)) as store:
        job_key = derive_job_key("orphan_norm", "hash", "TRANSCRIPT_INGEST", "1.2.0")
        j = store.create_job(job_key, operation="TRANSCRIPT_INGEST", stage="norm",
                             target_entity_id=CANONICAL_ENTITY_ID)
        store.claim_job(j.id)
        store.complete_job(j.id, "READY")

    result = status(config)
    funnel = result["readiness_funnel"]
    assert funnel["source_archival"] == "not_proven"
    assert funnel["text_extraction"] == "not_proven"


def test_source_archival_done_when_source_version_durable_but_no_extraction(tmp_path):
    """When source bytes are registered and current source_version recorded,
    source_archival is done even before extraction completes.
    """
    config = _write_config(tmp_path)
    with SQLiteStateStore(state_path(config)) as store:
        store.register_source_file(
            "src_only", provider="google_drive", provider_file_id="gfile_only",
            course_key=COURSE_KEY, source_kind="lecture"
        )
        store.register_source_version(
            source_file_id="src_only", source_hash="sha256:sourcehash1",
            canonical_entity_id=CANONICAL_ENTITY_ID,
            source_ref_json={"provider": "google_drive", "file_id": "gfile_only"}
        )

    result = status(config)
    funnel = result["readiness_funnel"]
    assert funnel["source_archival"] == "done"
    assert funnel["text_extraction"] == "not_started"


def test_valid_normalization_advances_both_stages(tmp_path):
    """When durable source version and matching valid normalization processing record exist,
    both source_archival and text_extraction report done.
    """
    config = _write_config(tmp_path)
    file_id = "gfile_valid"
    source_hash = "sha256:validhash"
    with SQLiteStateStore(state_path(config)) as store:
        store.register_source_file(
            "src_valid", provider="google_drive", provider_file_id=file_id,
            course_key=COURSE_KEY, source_kind="lecture"
        )
        store.register_source_version(
            source_file_id="src_valid", source_hash=source_hash,
            canonical_entity_id=CANONICAL_ENTITY_ID,
            source_ref_json={"provider": "google_drive", "file_id": file_id}
        )
        job_key = derive_job_key("src_valid", source_hash, "TRANSCRIPT_INGEST", "1.2.0")
        j = store.create_job(
            job_key, operation="TRANSCRIPT_INGEST", stage="norm",
            target_entity_id=CANONICAL_ENTITY_ID, source_file_id="src_valid",
            source_hash=source_hash, processor_version="1.2.0"
        )
        store.claim_job(j.id)
        store.create_processing_record(
            job_id=j.id, operation="TRANSCRIPT_INGEST", processor_version="1.2.0",
            input_hash=source_hash,
            output_ref_json=json.dumps({"provider": "google_drive", "file_id": "out_deriv", "web_url": "https://drive.google.com/file/d/out_deriv/view"}),
            status="READY"
        )
        store.complete_job(j.id, "READY")

    result = status(config)
    funnel = result["readiness_funnel"]
    assert funnel["source_archival"] == "done"
    assert funnel["text_extraction"] == "done"
    assert funnel["ai_client"] == "not_proven"


def test_status_funnel_partial_only_stays_not_proven(tmp_path):
    """Regression: pre-download PARTIAL must not advance source_archival."""
    config = _write_config(tmp_path)
    with SQLiteStateStore(state_path(config)) as store:
        job_key = derive_job_key("src-c", "hash-c", "INGEST", "1.2.0")
        j = store.create_job(job_key, operation="INGEST", stage="norm",
                             target_entity_id="ENT-3")
        store.claim_job(j.id)
        store.complete_job(j.id, "PARTIAL")

    result = status(config)
    funnel = result["readiness_funnel"]
    assert funnel["source_archival"] == "not_proven"
    assert funnel["text_extraction"] == "not_proven"
