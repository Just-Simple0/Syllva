"""Typed, allowlisted Local Settings config snapshots, validation, review binding, and apply."""
from __future__ import annotations

import json
import os

import pytest
import yaml
from _settings_support import FETCH, PRIVATE_SENTINEL, make_harness, reviewed_apply

from uls.settings import config_service
from uls.settings.config_service import SettingsServiceError
from uls.settings.status import NOT_CHECKED, PARTIAL, READY, setup_steps

pytestmark = pytest.mark.contract


def test_group_snapshots_expose_only_allowlisted_values(tmp_path):
    h = make_harness(tmp_path)
    h.signed_in()
    general = h.client.get("/api/v1/settings/general", headers=FETCH).json()
    assert set(general["values"]) == {"system.timezone"}
    advanced = h.client.get("/api/v1/settings/advanced", headers=FETCH).json()
    assert set(advanced["values"]) == set(config_service.GROUP_FIELDS["advanced"])
    for group in ("connections", "academic", "automation", "remote", "storage"):
        response = h.client.get(f"/api/v1/settings/{group}", headers=FETCH)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FEATURE_DEFERRED"


@pytest.mark.parametrize(("values", "code"), [
    ({"system.workspace_dir": "/tmp/elsewhere"}, "FEATURE_DEFERRED"),
    ({"mcp.read_only": False}, "FEATURE_DEFERRED"),
    ({"notion.courses_db_id": "abc"}, "FEATURE_DEFERRED"),
    ({"remote_mcp.enabled": True}, "FEATURE_DEFERRED"),
    ({"allow_provisional_material_usage": True}, "FEATURE_DEFERRED"),
    ({"x_unknown_section.keep": 3}, "FEATURE_DEFERRED"),
    ({}, "FEATURE_DEFERRED"),
    ({"system.timezone": "Mars/Base"}, "INVALID_VALUE"),
    ({"system.timezone": 5}, "INVALID_VALUE"),
])
def test_general_rejects_read_only_unknown_and_invalid_values(tmp_path, values, code):
    h = make_harness(tmp_path)
    before = h.config_path.read_bytes()
    generation = h.store.load().generation
    for call in (
        lambda: h.store.preview("general", values, generation),
        lambda: h.store.apply("general", values, generation, h.journal, candidate_hash="0" * 64),
    ):
        with pytest.raises(SettingsServiceError) as error:
            call()
        assert error.value.code == code
    assert h.config_path.read_bytes() == before
    assert list(h.journal.directory.glob("*.json")) == []


@pytest.mark.parametrize("values", [
    {"retrieval.max_candidate_chunks": 0},
    {"retrieval.max_candidate_chunks": 10**7},
    {"retrieval.max_candidate_chunks": True},
    {"retrieval.max_candidate_chunks": "12"},
    {"retrieval.concept_mode": "semantic"},
    {"normalization.goodnotes_visual_fallback": "true"},
])
def test_advanced_rejects_out_of_range_and_wrong_types(tmp_path, values):
    h = make_harness(tmp_path)
    with pytest.raises(SettingsServiceError) as error:
        h.store.preview("advanced", values, h.store.load().generation)
    assert error.value.code == "INVALID_VALUE"
    assert [item["field"] for item in error.value.fields or []] == list(values)


def test_invalid_values_return_structured_errors_for_every_field(tmp_path):
    h = make_harness(tmp_path)
    csrf = h.signed_in()
    response = h.post("/api/v1/settings/advanced/validate",
                      {"values": {"retrieval.max_candidate_entities": 0,
                                  "retrieval.max_candidate_chunks": 12,
                                  "normalization.goodnotes_visual_fallback": "yes"},
                       "generation": h.store.load().generation}, csrf)
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "INVALID_VALUE"
    assert error["fields"] == [
        {"field": "normalization.goodnotes_visual_fallback", "code": "INVALID_VALUE",
         "message": "Enter true or false."},
        {"field": "retrieval.max_candidate_entities", "code": "INVALID_VALUE",
         "message": "Enter a whole number from 1 to 500."},
    ]
    unknown = h.post("/api/v1/settings/advanced/validate",
                     {"values": {"<script>": 1}, "generation": h.store.load().generation}, csrf)
    assert "fields" not in unknown.json()["error"]


def test_apply_requires_the_server_issued_candidate(tmp_path):
    h = make_harness(tmp_path)
    csrf = h.signed_in()
    generation = h.store.load().generation
    missing = h.post("/api/v1/settings/general/apply",
                     {"values": {"system.timezone": "UTC"}, "generation": generation}, csrf)
    assert missing.status_code == 400 and missing.json()["error"]["code"] == "REVIEW_REQUIRED"
    assert list(h.journal.directory.glob("*.json")) == []


def test_edit_or_revert_after_review_cannot_apply_unreviewed_values(tmp_path):
    h = make_harness(tmp_path)
    csrf = h.signed_in()
    before = h.config_path.read_bytes()
    reviewed = h.review("general", {"system.timezone": "UTC"}, csrf)
    assert reviewed["values"] == {"system.timezone": "UTC"}
    for substituted in ({"system.timezone": "Europe/Paris"}, {"system.timezone": "Asia/Seoul"}):
        response = h.apply_reviewed("general", {**reviewed, "values": substituted}, csrf)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "REVIEW_STALE"
    assert h.config_path.read_bytes() == before
    assert list(h.journal.directory.glob("*.json")) == []
    applied = h.apply_reviewed("general", reviewed, csrf)
    assert applied.status_code == 200, applied.text
    assert yaml.safe_load(h.config_path.read_text())["system"]["timezone"] == "UTC"


def test_generation_change_after_review_is_refused(tmp_path):
    h = make_harness(tmp_path)
    csrf = h.signed_in()
    reviewed = h.review("general", {"system.timezone": "UTC"}, csrf)
    reviewed_apply(h.store, "general", {"system.timezone": "Asia/Tokyo"}, h.journal)
    changed = h.config_path.read_bytes()
    response = h.apply_reviewed("general", reviewed, csrf)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFIGURATION_CHANGED"
    assert h.config_path.read_bytes() == changed
    assert h.journal.unresolved() == []


def test_preview_returns_redacted_diff_and_candidate_without_writing(tmp_path):
    h = make_harness(tmp_path)
    csrf = h.signed_in()
    before = h.config_path.read_bytes()
    result = h.review("general", {"system.timezone": "Europe/Paris"}, csrf)
    assert result["diff"] == [{"field": "system.timezone", "from": "Asia/Seoul", "to": "Europe/Paris"}]
    assert len(result["candidate_hash"]) == 64 and result["generation"] == h.store.load().generation
    assert h.config_path.read_bytes() == before


def test_apply_preserves_unknown_keys_and_reads_back(tmp_path):
    h = make_harness(tmp_path)
    csrf = h.signed_in()
    generation = h.store.load().generation
    reviewed = h.review("advanced", {"retrieval.max_evidence_items": 7,
                                     "normalization.goodnotes_visual_fallback": False}, csrf)
    response = h.apply_reviewed("advanced", reviewed, csrf)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "applied" and result["generation"] == reviewed["candidate_hash"] != generation
    data = yaml.safe_load(h.config_path.read_text(encoding="utf-8"))
    assert data["retrieval"]["max_evidence_items"] == 7
    assert data["normalization"]["goodnotes_visual_fallback"] is False
    assert data["x_unknown_section"]["keep"] == [1, 2]
    assert data["retrieval"]["x_nested_unknown"] == "kept"
    assert h.store.load().generation == result["generation"]
    if os.name != "nt":
        assert h.config_path.stat().st_mode & 0o777 == 0o600
    assert h.journal.unresolved() == []
    record = h.journal.read(result["operation_id"])
    assert record["binding"] == h.store.binding()


def test_invalid_candidate_leaves_bytes_and_journal_untouched(tmp_path, monkeypatch):
    h = make_harness(tmp_path)
    before = h.config_path.read_bytes()
    generation = h.store.load().generation

    def reject_candidate(config):
        return [] if config.system.timezone == "Asia/Seoul" else ["candidate rejected"]

    monkeypatch.setattr(config_service, "validate_config", reject_candidate)
    for call in (
        lambda: h.store.preview("general", {"system.timezone": "UTC"}, generation),
        lambda: h.store.apply("general", {"system.timezone": "UTC"}, generation, h.journal,
                              candidate_hash="0" * 64),
    ):
        with pytest.raises(SettingsServiceError) as error:
            call()
        assert error.value.code == "CONFIGURATION_INVALID"
    assert h.config_path.read_bytes() == before
    assert list(h.journal.directory.glob("*.json")) == []


def test_api_responses_never_echo_unlisted_or_secret_shaped_values(tmp_path):
    h = make_harness(tmp_path)
    csrf = h.signed_in()
    bodies = [
        h.client.get("/api/v1/overview", headers=FETCH).text,
        h.client.get("/api/v1/settings/general", headers=FETCH).text,
        h.client.get("/api/v1/settings/advanced", headers=FETCH).text,
        h.post("/api/v1/settings/general/validate",
               {"values": {"system.timezone": "UTC"}, "generation": h.store.load().generation},
               csrf).text,
    ]
    for body in bodies:
        assert PRIVATE_SENTINEL not in body
        assert "workspace_dir" not in body
        assert "config_path" not in body
    assert "credentials" not in json.loads(bodies[0])


def test_symlinked_config_target_is_rejected(tmp_path):
    h = make_harness(tmp_path)
    real = h.config_path.with_name("real.yaml")
    h.config_path.rename(real)
    h.config_path.symlink_to(real)
    with pytest.raises(OSError):
        h.store.load()
    before = real.read_bytes()
    with pytest.raises(OSError):
        h.store.apply("general", {"system.timezone": "UTC"}, "0" * 64, h.journal, candidate_hash="0" * 64)
    assert real.read_bytes() == before


def test_invalid_generation_is_rejected_before_any_record(tmp_path):
    h = make_harness(tmp_path)
    with pytest.raises(SettingsServiceError) as error:
        h.store.apply("general", {"system.timezone": "UTC"}, "not-a-hash", h.journal,
                      candidate_hash="0" * 64)
    assert error.value.code == "INVALID_GENERATION"
    assert list(h.journal.directory.glob("*.json")) == []


def _steps(config):
    return {step["name"]: step for step in setup_steps(config)}


def test_setup_steps_never_claim_ready_from_template_or_defaults(tmp_path):
    h = make_harness(tmp_path)
    config = h.store.load().config
    assert config.courses and config.remote_mcp.enabled is False and config.worker.enabled is True
    steps = _steps(config)
    assert steps["Storage"]["state"] == PARTIAL  # template placeholder IDs
    assert steps["Canvas"]["state"] == PARTIAL
    assert steps["Academic"]["state"] == NOT_CHECKED
    assert "not verified" in steps["Academic"]["reason"]
    assert steps["Automation"]["state"] == NOT_CHECKED
    assert steps["Remote"]["state"] == PARTIAL
    assert "Skip" in steps["Remote"]["reason"]
    assert steps["Check"]["state"] == PARTIAL
    assert all(step["state"] != READY for step in steps.values())


def test_setup_steps_distinguish_disabled_from_explicit_choice_and_unverified_ids(tmp_path):
    h = make_harness(tmp_path)
    config = h.store.load().config
    config.worker.enabled = False
    config.remote_mcp.enabled = True
    config.google_drive.university_root_id = "1AbCdEfGhIjKlMn"
    config.google_drive.inbox_root_id = "1ZyXwVuTsRqPoN"
    config.notion.courses_db_id = "0123456789abcdef0123456789abcdef"
    config.notion.sessions_db_id = "fedcba9876543210fedcba9876543210"
    steps = _steps(config)
    assert steps["Storage"]["state"] == NOT_CHECKED  # saved but never verified
    assert steps["Automation"]["state"] == PARTIAL  # disabled is not an explicit saved choice
    assert "no explicit" in steps["Automation"]["reason"]
    assert steps["Remote"]["state"] == NOT_CHECKED  # enabled but not verified
    assert steps["Check"]["state"] == PARTIAL
    config.courses = []
    assert _steps(config)["Academic"]["state"] == NOT_CHECKED
    assert all(step["state"] != READY for step in setup_steps(config))
