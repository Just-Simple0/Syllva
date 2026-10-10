"""P-A: durable classification ledgers, additive columns and the generation=1 backfill."""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from uls.intake.classification import (
    RULE_TABLE_VERSION,
    CourseCalendar,
    RecordingEntry,
    SemesterRange,
    build_calendar,
)
from uls.intake.classification.calendar import CalendarEntry, EntryStatus
from uls.state.sqlite import SQLiteStateStore

pytestmark = pytest.mark.unit

PRE_V2_DUMP = Path(__file__).resolve().parents[1] / "fixtures/state/pre_icv2_head_0c1fe7f.sql"


@pytest.fixture(autouse=True)
def _close_stores():
    yield
    import contextlib

    while _OPENED:
        with contextlib.suppress(Exception):  # a test may already have closed it
            _OPENED.pop().close()


def _store(tmp_path) -> SQLiteStateStore:
    store = SQLiteStateStore(tmp_path / "state.sqlite3")
    _OPENED.append(store)
    return store


_OPENED: list[SQLiteStateStore] = []


def _seed_intake(state: SQLiteStateStore, intake_id: str = "intake-1") -> None:
    state.upsert_intake_item(
        intake_id=intake_id, provider="google_drive", provider_file_id=f"file-{intake_id}",
        semester="2026-2", original_parent_id="upload", observed_parent_id="upload",
        original_name="x.pdf", mime_type="application/pdf", source_hash="md5:abc", source_version=1,
    )


def _record_kwargs(intake_id: str = "intake-1", **overrides):
    kwargs = {
        "intake_id": intake_id, "provider_file_id": f"file-{intake_id}", "byte_sha256": "b" * 64,
        "byte_md5": "m" * 32, "snapshot_sha256": "s" * 64, "source_version": 1,
        "workspace_fingerprint": "wf", "config_fingerprint": "cf",
        "rule_table_version": RULE_TABLE_VERSION, "decision": {"type": "rule", "rule_id": "P3:lecture"},
        "kind": "LECTURE_SLIDES", "origin": "PROFESSOR_SOURCE", "course_key": "2026-2_LMS67535-001",
        "week": 3, "course_basis": {"type": "canvas_map"},
    }
    kwargs.update(overrides)
    return kwargs


def _receipt(store: SQLiteStateStore, key: str, request_type: str, intake_ids: str, **extra) -> None:
    store.create_request_receipt(
        provider="notion", input_requests_data_source_id="ds", request_key=key,
        request_revision_hash="rev", workspace_fingerprint="wf", request_type=request_type,
        target_snapshot_hash="t", intake_ids_json=intake_ids, **extra,
    )


def test_new_columns_default_and_update(tmp_path) -> None:
    state = _store(tmp_path)
    _seed_intake(state)
    item = state.get_intake_item("intake-1")
    assert item is not None
    assert (item.origin, item.classification_state, item.classified_kind) == ("UNKNOWN", "NONE", None)
    updated = state.update_intake_item(
        "intake-1", origin="PROFESSOR_SOURCE", classified_kind="LECTURE_SLIDES",
        classification_source="rule:P3:lecture", inferred_week=3, calendar_match="MATCHED",
        classification_state="CLASSIFIED",
    )
    assert updated.classified_kind == "LECTURE_SLIDES" and updated.inferred_week == 3
    with pytest.raises(ValueError):
        state.update_intake_item("intake-1", nonsense=1)


def test_classification_record_is_immutable_and_hash_canonical(tmp_path) -> None:
    state = _store(tmp_path)
    _seed_intake(state)
    kwargs = _record_kwargs()
    first = state.create_classification_record(**kwargs)
    again = state.create_classification_record(**kwargs)
    assert first.record_id == again.record_id and first.classification_revision_hash == again.classification_revision_hash
    # Canonical form (R15): type-equivalent inputs hash identically; a real change does not.
    same = state.create_classification_record(**{**kwargs, "week": "3", "source_version": "1"})
    assert same.record_id == first.record_id
    changed = state.create_classification_record(**{**kwargs, "byte_sha256": "c" * 64})
    assert changed.record_id != first.record_id
    assert state.latest_classification_record("intake-1").record_id == changed.record_id
    with pytest.raises(ValueError):
        state.create_classification_record(**{**kwargs, "kind": None})


def test_auto_plan_starts_pending_bound_to_a_real_record_and_jobs_bind_to_plans(tmp_path) -> None:
    state = _store(tmp_path)
    _seed_intake(state)
    _seed_intake(state, "intake-2")
    record = state.create_classification_record(**_record_kwargs())
    base = {"intake_id": "intake-1", "request_revision_hash": "cls", "plan_revision": "p1",
            "resolved_workspace_fingerprint": "wf", "target_snapshot_json": {}, "plan_hash": "h1",
            "plan_authority": "AUTO_CLASSIFICATION"}
    with pytest.raises(ValueError):
        state.create_intake_plan(**base)  # no revision hash
    with pytest.raises(ValueError):
        state.create_intake_plan(**base, classification_revision_hash="x" * 64)  # unknown record
    with pytest.raises(ValueError):
        state.create_intake_plan(**{**base, "intake_id": "intake-2"},
                                 classification_revision_hash=record.classification_revision_hash)
    with pytest.raises(ValueError):
        state.create_intake_plan(**base, status="PLANNED",
                                 classification_revision_hash=record.classification_revision_hash)
    plan = state.create_intake_plan(**base, classification_revision_hash=record.classification_revision_hash)
    assert (plan.plan_authority, plan.status) == ("AUTO_CLASSIFICATION", "AUTO_PENDING")
    # Stage B: a live HUMAN request over the intake blocks promotion (R10).
    _receipt(state, "k-live", "FILE_DETAILS", '["intake-1"]')
    with pytest.raises(ValueError):
        state.promote_auto_plan("p1")
    # A locally AutoResolved receipt counts only with a DONE closure intent bound to
    # this classification record and its terminal snapshot (P-A r2 #6).
    state.update_request_receipt("k-live", state="AutoResolved")
    with pytest.raises(ValueError):
        state.promote_auto_plan("p1")
    state.update_request_receipt("k-live", state="Draft")
    intent = state.create_auto_resolve_intent(record_id=record.record_id, request_key="k-live",
                                              expected_user_snapshot_hash="h")
    state.update_request_receipt("k-live", state="AutoResolved")
    with pytest.raises(ValueError):
        state.promote_auto_plan("p1")  # intent still PENDING
    state.update_request_receipt("k-live", state="Draft")
    state.transition_auto_resolve_intent(intent.intent_id, "DONE", terminal_snapshot_hash="t")
    assert state.get_request_receipt("k-live").state == "AutoResolved"
    # A multi-intake receipt that somehow reads AutoResolved never promotes an AUTO plan (r5 #3).
    _receipt(state, "k-group", "ASSIGN_COURSE", '["intake-1", "intake-2"]', state="AutoResolved")
    with pytest.raises(ValueError):
        state.promote_auto_plan("p1")
    state.update_request_receipt("k-group", state="Cancelled")
    # A receipt whose intake binding cannot be parsed is possibly live: no promotion (r7 R2).
    state.create_request_receipt(provider="notion", input_requests_data_source_id="ds", request_key="k-broken",
                                 request_revision_hash="rev", workspace_fingerprint="wf", request_type="FILE_DETAILS",
                                 target_snapshot_hash="t", intake_ids_json="not json")
    with pytest.raises(ValueError):
        state.promote_auto_plan("p1")
    state.update_request_receipt("k-broken", state="Cancelled")
    # Structurally unfit bindings ([], [""], duplicates, NULL) are never "another intake" (r8 R1);
    # a well-formed binding to another intake does not block.
    for index, payload in enumerate(("[]", '[""]', '["intake-1", "intake-1"]', None, '["intake-1", 7]')):
        key = f"k-unfit-{index}"
        state.create_request_receipt(provider="notion", input_requests_data_source_id="ds", request_key=key,
                                     request_revision_hash="rev", workspace_fingerprint="wf",
                                     request_type="ASSIGN_COURSE", target_snapshot_hash="t", intake_ids_json=payload)
        with pytest.raises(ValueError):
            state.promote_auto_plan("p1")
        state.update_request_receipt(key, state="Cancelled")
    _receipt(state, "k-other", "FILE_DETAILS", '["intake-2"]')
    # A closure intent still in flight, even before its receipt exists, blocks promotion (r7 R2).
    pending = state.create_auto_resolve_intent(record_id=record.record_id, request_key="k-early",
                                               expected_user_snapshot_hash="h")
    with pytest.raises(ValueError):
        state.promote_auto_plan("p1")
    state.transition_auto_resolve_intent(pending.intent_id, "ABORTED")
    assert state.promote_auto_plan("p1").status == "PLANNED"
    assert state.get_request_receipt("k-other").state == "Draft"  # untouched, other intake
    # A later source version gets a new record and plan; the earlier AutoResolved receipt,
    # closed by the earlier record's DONE intent, does not block it (r11 R5).
    record2 = state.create_classification_record(**_record_kwargs(source_version=2, byte_sha256="2" * 64))
    plan2 = state.create_intake_plan(**{**base, "plan_revision": "p1b", "plan_hash": "h1b"},
                                     classification_revision_hash=record2.classification_revision_hash)
    assert plan2.status == "AUTO_PENDING"
    # A live intent of the *earlier* record of this intake (receipt not yet created) still blocks (r12 R1).
    stale = state.create_auto_resolve_intent(record_id=record.record_id, request_key="k-early2",
                                             expected_user_snapshot_hash="h")
    with pytest.raises(ValueError):
        state.promote_auto_plan("p1b")
    state.transition_auto_resolve_intent(stale.intent_id, "ABORTED")
    # An unresolved HUMAN request generation of this intake blocks activation (r14 R1):
    # a reservation without a key, or a bound key with no receipt yet, even after restart.
    assert state.reserve_request_generation("intake-1", "FILE_DETAILS") == 1
    with pytest.raises(ValueError):
        state.promote_auto_plan("p1b")
    state.bind_request_generation_key("intake-1", "FILE_DETAILS", 1, "k-gen-1")
    with pytest.raises(ValueError):
        state.promote_auto_plan("p1b")
    state.close()
    state = _store(tmp_path)
    with pytest.raises(ValueError):
        state.promote_auto_plan("p1b")
    _receipt(state, "k-gen-1", "FILE_DETAILS", '["intake-1"]', state="Cancelled")
    # A generation whose key resolves to a receipt of another intake or another request
    # type proves nothing for this intake: no activation whatever that receipt's state (r15 R1).
    first = state.reserve_request_generation("intake-1", "USAGE_RANGE")
    state.bind_request_generation_key("intake-1", "USAGE_RANGE", first, "shared-key")
    for receipt_state in ("Draft", "Applied", "Cancelled"):
        if receipt_state == "Draft":
            _receipt(state, "shared-key", "USAGE_RANGE", '["intake-2"]')
        else:
            state.update_request_receipt("shared-key", state=receipt_state)
        with pytest.raises(ValueError):
            state.promote_auto_plan("p1b")
    state.close()
    state = _store(tmp_path)
    with pytest.raises(ValueError):
        state.promote_auto_plan("p1b")
    _receipt(state, "typed-key", "FILE_DETAILS", '["intake-1"]', state="Cancelled")
    second = state.reserve_request_generation("intake-1", "USAGE_RANGE", superseded_request_key="shared-key")
    state.bind_request_generation_key("intake-1", "USAGE_RANGE", second, "typed-key")
    with pytest.raises(ValueError):  # request type mismatch between generation and receipt
        state.promote_auto_plan("p1b")
    state._connection.execute("DELETE FROM intake_request_generations WHERE request_key IN ('shared-key', 'typed-key')")
    state._connection.commit()
    assert state.promote_auto_plan("p1b").status == "PLANNED"
    assert state.get_request_receipt("k-live").state == "AutoResolved"
    # ... but an AutoResolved receipt closed by a record of another intake still blocks.
    other_record = state.create_classification_record(**_record_kwargs("intake-2", byte_sha256="3" * 64))
    _receipt(state, "k-foreign", "FILE_DETAILS", '["intake-1"]')
    with pytest.raises(ValueError):
        state.create_auto_resolve_intent(record_id=other_record.record_id, request_key="k-foreign",
                                         expected_user_snapshot_hash="h")
    state.update_request_receipt("k-foreign", state="AutoResolved")
    record3 = state.create_classification_record(**_record_kwargs(source_version=3, byte_sha256="4" * 64))
    state.create_intake_plan(**{**base, "plan_revision": "p1c", "plan_hash": "h1c"},
                             classification_revision_hash=record3.classification_revision_hash)
    with pytest.raises(ValueError):
        state.promote_auto_plan("p1c")
    with pytest.raises(ValueError):
        state.promote_auto_plan("p1")  # not pending any more
    human = state.create_intake_plan(intake_id="intake-1", request_revision_hash="r2", plan_revision="p2",
                                     resolved_workspace_fingerprint="wf", target_snapshot_json={}, plan_hash="h2")
    assert human.plan_authority == "HUMAN_REQUEST" and human.status == "PLANNED"
    with pytest.raises(ValueError):
        state.promote_auto_plan("p2")
    job = state.create_job(job_key="sha256:" + "a" * 64, operation="intake_material", stage="plan",
                           target_entity_id="intake-1", plan_revision="p1", plan_authority="AUTO_CLASSIFICATION")
    assert (job.plan_revision, job.plan_authority, job.is_void) == ("p1", "AUTO_CLASSIFICATION", False)
    other = state.create_job(job_key="sha256:" + "b" * 64, operation="intake_material", stage="plan",
                             target_entity_id="intake-1", plan_revision="p2", plan_authority="HUMAN_REQUEST")
    assert state.void_jobs_for_plan("p1", "SUPERSEDED") == 1
    voided = state.get_job(job.id)
    assert voided is not None and voided.is_void and voided.is_terminal and voided.void_reason == "SUPERSEDED"
    claimed = state.claim_job()
    assert claimed is not None and claimed.id == other.id
    assert state.claim_job(job.id) is None
    assert state.void_jobs_for_plan("p1", "SUPERSEDED") == 0


def test_request_generations_reserve_idempotently_and_span_intakes(tmp_path) -> None:
    state = _store(tmp_path)
    _seed_intake(state)
    _seed_intake(state, "intake-2")
    assert state.reserve_request_generation("intake-1", "FILE_DETAILS") == 1
    # A retry before the key is bound reuses the pending reservation (R9).
    assert state.reserve_request_generation("intake-1", "FILE_DETAILS") == 1
    assert state.pending_request_generation("intake-1", "FILE_DETAILS") == 1
    state.bind_request_generation_key("intake-1", "FILE_DETAILS", 1, "key-1")
    assert state.pending_request_generation("intake-1", "FILE_DETAILS") is None
    assert state.get_request_generation("key-1")["generation"] == 1
    assert state.reserve_request_generation("intake-1", "FILE_DETAILS", superseded_request_key="key-1") == 2
    with pytest.raises(ValueError):
        state.reserve_request_generation("intake-1", "FILE_DETAILS", superseded_request_key="key-other")
    assert state.reserve_request_generation("intake-1", "FILE_DETAILS", superseded_request_key="key-1") == 2
    # After the key is bound a retry of the same supersession basis (lost provider
    # response) still comes back to generation 2, never 3 (P-A r2 #5).
    state.bind_request_generation_key("intake-1", "FILE_DETAILS", 2, "key-2")
    assert state.reserve_request_generation("intake-1", "FILE_DETAILS", superseded_request_key="key-1") == 2
    assert state.reserve_request_generation("intake-1", "FILE_DETAILS") == 1
    # Only the latest bound key can be superseded.
    with pytest.raises(ValueError):
        state.reserve_request_generation("intake-1", "FILE_DETAILS", superseded_request_key="key-nope")
    assert state.reserve_request_generation("intake-1", "FILE_DETAILS", superseded_request_key="key-2") == 3
    with pytest.raises(ValueError):
        state.bind_request_generation_key("intake-1", "FILE_DETAILS", 1, "key-other")
    # A generation never reuses the superseded key nor another generation's key; a
    # FILE_DETAILS key is single-intake (r3 #4).  ASSIGN_COURSE keys may span intakes.
    with pytest.raises(ValueError):
        state.bind_request_generation_key("intake-1", "FILE_DETAILS", 3, "key-2")
    with pytest.raises(ValueError):
        state.bind_request_generation_key("intake-1", "FILE_DETAILS", 3, "key-1")
    state.reserve_request_generation("intake-2", "FILE_DETAILS")
    with pytest.raises(ValueError):
        state.bind_request_generation_key("intake-2", "FILE_DETAILS", 1, "key-1")
    state.bind_request_generation_key("intake-1", "FILE_DETAILS", 3, "key-3")
    state.bind_request_generation_key("intake-1", "FILE_DETAILS", 3, "key-3")  # idempotent
    # One ASSIGN_COURSE key may cover several intakes (R8).
    for intake in ("intake-1", "intake-2"):
        state.reserve_request_generation(intake, "ASSIGN_COURSE")
        state.bind_request_generation_key(intake, "ASSIGN_COURSE", 1, "key-assign")
    assert [r["intake_id"] for r in state.list_request_generations("key-assign")] == ["intake-1", "intake-2"]
    assert state.get_request_generation("key-assign", "intake-2")["intake_id"] == "intake-2"
    # Backfill of a pre-v2 multi-intake receipt covers every intake, idempotently;
    # an intake that already owns generation 1 of that type gets the next generation
    # superseding it (r8 R3), never an overwrite.
    _seed_intake(state, "intake-3")
    _receipt(state, "legacy-key", "ASSIGN_COURSE", '["intake-2", "intake-3"]')
    state.close()
    reopened = _store(tmp_path)
    rows = reopened.list_request_generations("legacy-key")
    assert [(r["intake_id"], r["generation"], r["superseded_request_key"]) for r in rows] \
        == [("intake-2", 2, "key-assign"), ("intake-3", 1, None)]
    assert reopened.get_request_generation("key-assign", "intake-2")["generation"] == 1
    reopened.apply_migrations()
    assert len(reopened.list_request_generations("legacy-key")) == 2


def test_pre_v2_database_upgrades_in_place(tmp_path) -> None:
    """A store written by HEAD 0c1fe7f opens, migrates and keeps its rows (P-A O3)."""

    path = tmp_path / "state.sqlite3"
    raw = sqlite3.connect(path)
    raw.executescript(PRE_V2_DUMP.read_text(encoding="utf-8"))
    # A later legacy Draft of the same intake/type (and a second terminal one) must not vanish (r8 R3).
    raw.execute("INSERT INTO request_receipts VALUES('receipt_v2','notion','ds','k-details-v2','rev3',NULL,NULL,'t',"
                "NULL,NULL,'Draft','wf','FILE_DETAILS','[\"intake-c\"]')")
    raw.execute("INSERT INTO request_receipts VALUES('receipt_a2','notion','ds','k-assign-2','rev4',NULL,NULL,'t',"
                "NULL,NULL,'Cancelled','wf','ASSIGN_COURSE','[\"intake-a\"]')")
    # Malformed legacy bindings never produce a generation (r13 R2); a valid multi-intake one does.
    raw.execute("INSERT INTO request_receipts VALUES('receipt_bad','notion','ds','k-bad','rev5',NULL,NULL,'t',"
                "NULL,NULL,'Applied','wf','ASSIGN_COURSE','[\"intake-a\", \"intake-a\"]')")
    raw.execute("INSERT INTO request_receipts VALUES('receipt_bad2','notion','ds','k-bad2','rev6',NULL,NULL,'t',"
                "NULL,NULL,'Applied','wf','FILE_DETAILS','[\"intake-a\", \"intake-b\"]')")
    raw.commit()
    raw.close()
    state = SQLiteStateStore(path)
    assert state.get_intake_item("intake-a") is not None
    rows = state.list_request_generations("k-assign")
    assert [(r["intake_id"], r["generation"]) for r in rows] == [("intake-a", 1), ("intake-b", 1)]
    assert state.get_request_generation("k-details")["intake_id"] == "intake-c"
    second = state.get_request_generation("k-details-v2")
    assert (second["generation"], second["superseded_request_key"]) == (2, "k-details")
    assert state.get_request_generation("k-assign-2")["generation"] == 2
    assert state.list_request_generations("k-bad") == [] and state.list_request_generations("k-bad2") == []
    # Every backfilled receipt keeps the legacy key derivation whatever its generation (r9 R2).
    assert {r["derivation_version"] for r in state.list_request_generations("k-details")} == {"LEGACY"}
    assert second["derivation_version"] == "LEGACY"
    assert state.get_request_generation("k-assign-2")["derivation_version"] == "LEGACY"
    # The latest bound legacy key is the supersession basis for the first v2 generation;
    # the older basis only re-reads the legacy generation it already produced.
    assert state.reserve_request_generation("intake-c", "FILE_DETAILS", superseded_request_key="k-details") == 2
    assert state.reserve_request_generation("intake-c", "FILE_DETAILS", superseded_request_key="k-details-v2") == 3
    # HUMAN-decided items are labelled so the automatic path never reclassifies them (R17);
    # a selected Kind without a bound plan is not proof.
    assert state.get_intake_item("intake-c").classification_source == "human"
    assert state.get_intake_item("intake-a").classification_source is None
    assert state.get_intake_plan("plan-c").plan_authority == "HUMAN_REQUEST"
    state.close()
    again = SQLiteStateStore(path)  # second open is a no-op
    assert len(again.list_request_generations("k-assign")) == 2
    assert again.get_request_generation("k-details-v2")["derivation_version"] == "LEGACY"
    assert again.pending_request_generation("intake-c", "FILE_DETAILS") == 3  # reservation survives
    again.bind_request_generation_key("intake-c", "FILE_DETAILS", 3, "k-v2-3")
    assert again.get_request_generation("k-v2-3")["derivation_version"] == "V2"
    assert again.get_intake_item("intake-a").classification_source is None


def test_pre_marker_generation_rows_are_classified_not_guessed(tmp_path) -> None:
    """An earlier P-A schema without derivation_version upgrades legacy rows to LEGACY (r10 R4)."""

    path = tmp_path / "state.sqlite3"
    state = SQLiteStateStore(path)
    _seed_intake(state, "i1")
    _receipt(state, "k-legacy-1", "FILE_DETAILS", '["i1"]', state="Applied")
    _receipt(state, "k-legacy-2", "FILE_DETAILS", '["i1"]')
    state.close()
    raw = sqlite3.connect(path)
    raw.executescript(
        "DROP TABLE intake_request_generations;"
        "CREATE TABLE intake_request_generations (intake_id TEXT NOT NULL, request_type TEXT NOT NULL,"
        " generation INTEGER NOT NULL, request_key TEXT, superseded_request_key TEXT, created_at TEXT NOT NULL,"
        " PRIMARY KEY (intake_id, request_type, generation));"
        "INSERT INTO intake_request_generations VALUES ('i1', 'FILE_DETAILS', 1, 'k-legacy-1', NULL, 't');"
        "INSERT INTO intake_request_generations VALUES ('i1', 'FILE_DETAILS', 2, 'k-legacy-2', 'k-legacy-1', 't');"
        "INSERT INTO intake_request_generations VALUES ('i1', 'ASSIGN_COURSE', 1, NULL, NULL, 't');"
        "INSERT INTO intake_request_generations VALUES ('i1', 'USAGE_RANGE', 1, 'k-orphan', NULL, 't');"
        "INSERT INTO intake_request_generations VALUES ('i2', 'FILE_DETAILS', 1, 'k-legacy-1', NULL, 't');"
        "INSERT INTO intake_request_generations VALUES ('i1', 'ASSIGN_COURSE', 2, 'k-legacy-2', NULL, 't');"
        "INSERT INTO intake_request_generations VALUES ('i1', 'FILE_DETAILS', 3, 'k-dup', 'k-legacy-2', 't');"
        "INSERT INTO intake_request_generations VALUES ('i1', 'FILE_DETAILS', 4, 'k-null', 'k-dup', 't');"
        "INSERT INTO intake_request_generations VALUES ('i1', 'FILE_DETAILS', 5, 'k-num', 'k-null', 't');"
        "INSERT INTO intake_request_generations VALUES ('i1', 'FILE_DETAILS', 6, 'k-multi', 'k-num', 't');"
        "INSERT INTO request_receipts VALUES('rc-dup','notion','ds','k-dup','r',NULL,NULL,'t',NULL,NULL,'Applied','wf',"
        "'FILE_DETAILS','[\"i1\", \"i1\"]');"
        "INSERT INTO request_receipts VALUES('rc-null','notion','ds','k-null','r',NULL,NULL,'t',NULL,NULL,'Applied','wf',"
        "'FILE_DETAILS','[\"i1\", null]');"
        "INSERT INTO request_receipts VALUES('rc-num','notion','ds','k-num','r',NULL,NULL,'t',NULL,NULL,'Applied','wf',"
        "'FILE_DETAILS','[\"i1\", 5]');"
        "INSERT INTO request_receipts VALUES('rc-multi','notion','ds','k-multi','r',NULL,NULL,'t',NULL,NULL,'Applied','wf',"
        "'FILE_DETAILS','[\"i1\", \"i2\"]');"
    )
    raw.commit()
    raw.close()
    for _ in range(2):  # upgrade, then a restart: same result
        state = SQLiteStateStore(path)
        assert state.get_request_generation("k-legacy-1", "i1")["derivation_version"] == "LEGACY"
        assert state.get_request_generation("k-legacy-2", "i1")["derivation_version"] == "LEGACY"
        assert state.get_request_generation("k-orphan")["derivation_version"] == "RECONCILE"
        # The same key under another intake or another request type proves nothing (r12 R7).
        assert state.get_request_generation("k-legacy-1", "i2")["derivation_version"] == "RECONCILE"
        assert [r["derivation_version"] for r in state.list_request_generations("k-legacy-2")
                if r["request_type"] == "ASSIGN_COURSE"] == ["RECONCILE"]
        # Structurally malformed receipt bindings never prove LEGACY (r13 R2).
        for key in ("k-dup", "k-null", "k-num", "k-multi"):
            assert state.get_request_generation(key)["derivation_version"] == "RECONCILE", key
        pending = state.pending_request_generation("i1", "ASSIGN_COURSE")
        assert pending == 1
        state.close()
    state = SQLiteStateStore(path)
    rows = [r for r in state.list_request_generations("k-legacy-1")]
    assert rows and rows[0]["derivation_version"] == "LEGACY"


def test_generations_table_with_unique_key_is_rebuilt(tmp_path) -> None:
    path = tmp_path / "state.sqlite3"
    state = SQLiteStateStore(path)
    state.close()
    raw = sqlite3.connect(path)
    raw.executescript(
        "DROP TABLE intake_request_generations;"
        "CREATE TABLE intake_request_generations (intake_id TEXT NOT NULL, request_type TEXT NOT NULL,"
        " generation INTEGER NOT NULL, request_key TEXT, superseded_request_key TEXT, created_at TEXT NOT NULL,"
        " PRIMARY KEY (intake_id, request_type, generation), UNIQUE (request_key));"
        "INSERT INTO intake_request_generations VALUES ('i1', 'ASSIGN_COURSE', 1, 'k', NULL, 't');"
    )
    raw.commit()
    raw.close()
    state = SQLiteStateStore(path)
    # The rebuild carries provenance: an unprovable bound key is RECONCILE, never V2 (r10 R4).
    assert state.get_request_generation("k")["derivation_version"] == "RECONCILE"
    state.close()
    # A UNIQUE table that already carries classified markers keeps them through the rebuild (r11 R4).
    raw = sqlite3.connect(path)
    raw.executescript(
        "DROP TABLE intake_request_generations;"
        "CREATE TABLE intake_request_generations (intake_id TEXT NOT NULL, request_type TEXT NOT NULL,"
        " generation INTEGER NOT NULL, request_key TEXT, superseded_request_key TEXT,"
        " derivation_version TEXT NOT NULL DEFAULT 'V2', created_at TEXT NOT NULL,"
        " PRIMARY KEY (intake_id, request_type, generation), UNIQUE (request_key));"
        "INSERT INTO intake_request_generations VALUES ('i1', 'ASSIGN_COURSE', 1, 'k', NULL, 'RECONCILE', 't');"
        "INSERT INTO intake_request_generations VALUES ('i1', 'FILE_DETAILS', 1, 'k-l', NULL, 'LEGACY', 't');"
        "INSERT INTO intake_request_generations VALUES ('i1', 'FILE_DETAILS', 2, NULL, 'k-l', 'V2', 't');"
    )
    raw.commit()
    raw.close()
    _receipt_keys = None
    for _ in range(2):
        state = SQLiteStateStore(path)
        _receipt(state, "k", "ASSIGN_COURSE", '["i1"]') if _receipt_keys is None else None
        _receipt_keys = True
        assert state.get_request_generation("k")["derivation_version"] == "RECONCILE"  # a receipt never upgrades it
        assert state.get_request_generation("k-l")["derivation_version"] == "LEGACY"
        assert state.pending_request_generation("i1", "FILE_DETAILS") == 2
        state.close()
    state = SQLiteStateStore(path)
    _seed_intake(state, "i2")
    state.reserve_request_generation("i2", "ASSIGN_COURSE")
    state.bind_request_generation_key("i2", "ASSIGN_COURSE", 1, "k")  # no longer UNIQUE
    assert len(state.list_request_generations("k")) == 2


def test_auto_resolve_intent_closes_only_a_draft_with_the_terminal_snapshot(tmp_path) -> None:
    state = _store(tmp_path)
    _seed_intake(state)
    _seed_intake(state, "intake-2")

    def record(intake: str, tag: str):
        return state.create_classification_record(**_record_kwargs(intake, byte_sha256=tag * 64)).record_id

    rec1 = record("intake-1", "1")
    _receipt(state, "k1", "FILE_DETAILS", '["intake-1"]')
    # The record must exist (r6 R1): an arbitrary id never closes a HUMAN Draft.
    with pytest.raises(ValueError):
        state.create_auto_resolve_intent(record_id="cls_fake", request_key="k1", expected_user_snapshot_hash="h")
    intent = state.create_auto_resolve_intent(record_id=rec1, request_key="k1", expected_user_snapshot_hash="h")
    assert intent.state == "PENDING"
    assert state.create_auto_resolve_intent(record_id=rec1, request_key="k1", expected_user_snapshot_hash="h").intent_id == intent.intent_id
    # Immutable inputs and one live intent per request (P-A r2 #7).
    with pytest.raises(ValueError):
        state.create_auto_resolve_intent(record_id=rec1, request_key="k1", expected_user_snapshot_hash="other")
    with pytest.raises(ValueError):
        state.create_auto_resolve_intent(record_id=record("intake-1", "2"), request_key="k1", expected_user_snapshot_hash="h")
    with pytest.raises(TypeError):
        state.transition_auto_resolve_intent(intent.intent_id, "DONE", receipt_state="AutoResolved")
    with pytest.raises(ValueError):
        state.transition_auto_resolve_intent(intent.intent_id, "DONE")  # no terminal snapshot
    snap = state.record_auto_resolve_snapshots(intent.intent_id, pre_close_snapshot_hash="pre")
    assert snap.pre_close_snapshot_hash == "pre" and snap.terminal_snapshot_hash is None
    with pytest.raises(ValueError):
        state.record_auto_resolve_snapshots(intent.intent_id, pre_close_snapshot_hash="other")
    # The receipt moved off Draft (human submitted meanwhile): DONE is refused, state intact,
    # and the live intent is not handed back as a normal re-entry either (r7 R1).
    for human_state in ("Submitted", "Claimed"):
        state.update_request_receipt("k1", state=human_state)
        with pytest.raises(ValueError):
            state.create_auto_resolve_intent(record_id=rec1, request_key="k1", expected_user_snapshot_hash="h")
    with pytest.raises(ValueError):
        state.transition_auto_resolve_intent(intent.intent_id, "DONE", terminal_snapshot_hash="term")
    assert state.get_auto_resolve_intent("k1").state == "PENDING"
    assert state.get_auto_resolve_intent("k1").terminal_snapshot_hash is None
    state.update_request_receipt("k1", state="Draft")
    state.record_auto_resolve_snapshots(intent.intent_id, terminal_snapshot_hash="term")
    with pytest.raises(ValueError):  # write-once also on the DONE transition
        state.transition_auto_resolve_intent(intent.intent_id, "DONE", terminal_snapshot_hash="other")
    assert state.get_request_receipt("k1").state == "Draft"
    done = state.transition_auto_resolve_intent(intent.intent_id, "DONE", terminal_snapshot_hash="term")
    assert done.state == "DONE" and done.terminal_snapshot_hash == "term"
    # DONE re-read is idempotent only for the same readback (r5 #2).
    assert state.transition_auto_resolve_intent(intent.intent_id, "DONE", terminal_snapshot_hash="term") == done
    assert state.transition_auto_resolve_intent(intent.intent_id, "DONE") == done
    with pytest.raises(ValueError):
        state.transition_auto_resolve_intent(intent.intent_id, "DONE", terminal_snapshot_hash="contradictory")
    assert state.get_auto_resolve_intent("k1").intent_id == intent.intent_id
    assert state.get_request_receipt("k1").state == "AutoResolved"
    with pytest.raises(ValueError):
        state.transition_auto_resolve_intent(intent.intent_id, "PENDING")
    # A closed request never gets another closure intent nor a rollback (r3 #3).
    with pytest.raises(ValueError):
        state.create_auto_resolve_intent(record_id=record("intake-1", "3"), request_key="k1", expected_user_snapshot_hash="h")
    assert state.get_auto_resolve_intent("k1").intent_id == intent.intent_id
    with pytest.raises(ValueError):
        state.create_auto_resolve_rollback(intent.intent_id, "Draft")
    # Only a Draft receipt takes an intent: Applied, Submitted and Claimed are refused (r6 R1).
    for receipt_state in ("Applied", "Submitted", "Claimed"):
        _receipt(state, f"k-{receipt_state}", "FILE_DETAILS", '["intake-1"]', state=receipt_state)
        with pytest.raises(ValueError):
            state.create_auto_resolve_intent(record_id=rec1, request_key=f"k-{receipt_state}", expected_user_snapshot_hash="h")
    # Only upload-intake request kinds close automatically: USAGE_RANGE and unknown kinds are
    # refused at intent creation and at DONE (r17 R1); ASSIGN_COURSE stays allowed.
    for kind in ("USAGE_RANGE", "SOMETHING_ELSE"):
        _receipt(state, f"k-{kind}", kind, '["intake-1"]')
        with pytest.raises(ValueError):
            state.create_auto_resolve_intent(record_id=rec1, request_key=f"k-{kind}", expected_user_snapshot_hash="h")
        assert state.get_request_receipt(f"k-{kind}").state == "Draft"
    late_kind = state.create_auto_resolve_intent(record_id=record("intake-1", "8"), request_key="k-late-kind",
                                                 expected_user_snapshot_hash="hk")
    _receipt(state, "k-late-kind", "USAGE_RANGE", '["intake-1"]')
    with pytest.raises(ValueError):
        state.transition_auto_resolve_intent(late_kind.intent_id, "DONE", terminal_snapshot_hash="t")
    assert state.get_request_receipt("k-late-kind").state == "Draft"
    _receipt(state, "k-assign-ok", "ASSIGN_COURSE", '["intake-1"]')
    assert state.create_auto_resolve_intent(record_id=record("intake-1", "9"), request_key="k-assign-ok",
                                            expected_user_snapshot_hash="ha").state == "PENDING"
    # A multi-intake Draft (ASSIGN_COURSE over two files) never closes automatically (r5 #3),
    # and a record of another intake never closes a request.
    _receipt(state, "k-multi", "ASSIGN_COURSE", '["intake-1", "intake-2"]')
    with pytest.raises(ValueError):
        state.create_auto_resolve_intent(record_id=rec1, request_key="k-multi", expected_user_snapshot_hash="h")
    assert state.get_request_receipt("k-multi").state == "Draft"
    rec2 = record("intake-2", "4")
    _receipt(state, "k-one", "FILE_DETAILS", '["intake-1"]')
    with pytest.raises(ValueError):
        state.create_auto_resolve_intent(record_id=rec2, request_key="k-one", expected_user_snapshot_hash="h")
    # A DONE transition against a missing receipt rolls the whole transaction back.
    missing = state.create_auto_resolve_intent(record_id=rec2, request_key="k-missing", expected_user_snapshot_hash="hm")
    with pytest.raises(KeyError):
        state.transition_auto_resolve_intent(missing.intent_id, "DONE", terminal_snapshot_hash="t")
    assert state.get_auto_resolve_intent("k-missing").state == "PENDING"
    with pytest.raises(ValueError):  # no local receipt at all: nothing verified to roll back to (r4 #3)
        state.create_auto_resolve_rollback(missing.intent_id, "Submitted")
    # A receipt that appears later with two intakes is re-checked inside DONE (r6 R1 B).
    _receipt(state, "k-missing", "ASSIGN_COURSE", '["intake-1", "intake-2"]')
    with pytest.raises(ValueError):
        state.transition_auto_resolve_intent(missing.intent_id, "DONE", terminal_snapshot_hash="t")
    assert state.get_request_receipt("k-missing").state == "Draft"
    # ... and one that appears for another intake than the record is refused as well.
    late = state.create_auto_resolve_intent(record_id=record("intake-2", "5"), request_key="k-late", expected_user_snapshot_hash="hl")
    _receipt(state, "k-late", "FILE_DETAILS", '["intake-1"]')
    with pytest.raises(ValueError):
        state.transition_auto_resolve_intent(late.intent_id, "DONE", terminal_snapshot_hash="t")
    assert state.get_request_receipt("k-late").state == "Draft"
    # Rollback intent (§3.5 iii) is idempotent and aborts the pending intent.
    _receipt(state, "k-roll", "FILE_DETAILS", '["intake-1"]')
    second = state.create_auto_resolve_intent(record_id=record("intake-1", "6"), request_key="k-roll", expected_user_snapshot_hash="h2")
    rollback = state.create_auto_resolve_rollback(second.intent_id, "Submitted")
    assert rollback["state"] == "PENDING"
    assert state.create_auto_resolve_rollback(second.intent_id, "Submitted") == rollback
    with pytest.raises(ValueError):  # a different recovery target is a conflict (P-A r2 #8)
        state.create_auto_resolve_rollback(second.intent_id, "Draft")
    with pytest.raises(ValueError):
        state.create_auto_resolve_rollback(second.intent_id, "Applied")
    finished = state.complete_auto_resolve_rollback(second.intent_id)
    assert finished["state"] == "DONE"
    assert state.get_auto_resolve_intent("k-roll").state == "ABORTED"
    assert state.complete_auto_resolve_rollback(second.intent_id) == finished  # idempotent completion
    with pytest.raises(KeyError):
        state.complete_auto_resolve_rollback("ari_unknown")
    # A rollback needs the local receipt to still be a Draft.
    _receipt(state, "k-sub", "FILE_DETAILS", '["intake-1"]')
    third = state.create_auto_resolve_intent(record_id=record("intake-1", "7"), request_key="k-sub", expected_user_snapshot_hash="h3")
    state.update_request_receipt("k-sub", state="Submitted")
    with pytest.raises(ValueError):
        state.create_auto_resolve_rollback(third.intent_id, "Submitted")
    # Re-reading a live intent whose receipt went terminal is not a valid idempotent read.
    state.update_request_receipt("k-sub", state="Applied")
    with pytest.raises(ValueError):
        state.create_auto_resolve_intent(record_id=third.record_id, request_key="k-sub", expected_user_snapshot_hash="h3")
    ledger = state.record_terminal_change("rcpt", "hash-a", created_generation=2, created_request_key="k2", state="CREATED")
    assert state.record_terminal_change("rcpt", "hash-a", created_generation=2, created_request_key="k2", state="CREATED") == ledger
    # The initial state is not part of the identity: re-recording the same event with
    # another state neither conflicts nor moves it (r3 #7 superseded by r13 R1).
    assert state.record_terminal_change("rcpt", "hash-a", created_generation=2, created_request_key="k2",
                                        state="RECONCILE")["state"] == "CREATED"
    with pytest.raises(ValueError):
        state.record_terminal_change("rcpt", "hash-a", created_generation=9, created_request_key="k9", state="CREATED")
    moved = state.transition_terminal_change("rcpt", "hash-a", expected_state="CREATED", state="RECONCILE")
    assert moved["state"] == "RECONCILE"
    assert state.transition_terminal_change("rcpt", "hash-a", expected_state="X", state="RECONCILE") == moved
    with pytest.raises(ValueError):
        state.transition_terminal_change("rcpt", "hash-a", expected_state="CREATED", state="DONE")
    # Re-observing the original event after the entry progressed returns the progressed
    # row: no new generation, no state regression, three retries and a restart alike (r13 R1).
    for _ in range(3):
        again = state.record_terminal_change("rcpt", "hash-a", created_generation=2, created_request_key="k2", state="CREATED")
        assert (again["created_generation"], again["created_request_key"], again["state"]) == (2, "k2", "RECONCILE")
    state.transition_terminal_change("rcpt", "hash-a", expected_state="RECONCILE", state="DONE")
    state.close()
    state = _store(tmp_path)
    again = state.record_terminal_change("rcpt", "hash-a", created_generation=2, created_request_key="k2", state="CREATED")
    assert again["state"] == "DONE"
    with pytest.raises(ValueError):  # a different generation/key for the same event is still a conflict
        state.record_terminal_change("rcpt", "hash-a", created_generation=3, created_request_key="k3", state="CREATED")
    assert state.get_terminal_change("rcpt", "hash-a")["created_request_key"] == "k2"
    handover = state.record_handover("p1", "p2", {"session": "S1"})
    assert handover["adopted_effects_json"] == '{"session":"S1"}'
    assert state.record_handover("p1", "p2", {"session": "S1"}) == handover
    with pytest.raises(ValueError):
        state.record_handover("p1", "p2", {"session": "S2"})


def test_recording_titles_are_exactly_iso_dates(tmp_path) -> None:
    """r16 R1: only YYYY-MM-DD titles are recordings; other date-like titles never block a calendar."""

    from uls.state.classification_state import _recording_observation

    assert _recording_observation("ExternalTool", "2026-09-17")
    assert _recording_observation("ExternalTool", " 2026-09-17 ")
    for title in ("20260917", "2026-W38-4", "2026-13-01", "2026-09-17T10:00", "2026.09.17", "", None):
        assert not _recording_observation("ExternalTool", title), title
    assert not _recording_observation("Page", "2026-09-17")
    assert not _recording_observation("ExternalTool", "2026-09-17", "announcement")
    state = _store(tmp_path)
    _observe(state, "r1", 1, "2026-09-03")
    _observe(state, "r2", 2, "2026-09-10")
    for resource_id, title in (("c1", "20260917"), ("w1", "2026-W38-4"), ("b1", "2026-13-01")):
        state.record_canvas_observation(origin="canvas.knu", canvas_course_id=67535, resource_kind="module_item",
                                        resource_id=resource_id, observation_revision=1, title=title,
                                        module_name="3주차", module_week=3, item_type="ExternalTool",
                                        collection_complete=True)
    rows = state.replace_recording_calendar_current(COURSE, 67535, _cal((_entry("r1", 1, "2026-09-03"),
                                                                       _entry("r2", 2, "2026-09-10"))),
                                                    collection_complete=True)
    assert [(r.resource_id, r.status) for r in rows] == [("r1", "CALENDAR"), ("r2", "CALENDAR")]
    assert state.recording_calendar_complete(COURSE)
    with pytest.raises(ValueError):  # a malformed-title item can never enter the projection either
        state.replace_recording_calendar_current(COURSE, 67535, _cal((_entry("r1", 1, "2026-09-03"),
                                                                     _entry("r2", 2, "2026-09-10"),
                                                                     _entry("c1", 3, "2026-09-17"))),
                                                 collection_complete=True)


def test_incomplete_collection_closes_even_an_empty_complete_calendar(tmp_path) -> None:
    """r12 R5: no recordings is not proof; an incomplete module scan withdraws completeness."""

    state = _store(tmp_path)
    rows = state.replace_recording_calendar_current("c-empty", 11, build_calendar(
        "c-empty", [], semester=SEMESTER, collection_complete=True), collection_complete=True)
    assert rows == [] and state.recording_calendar_complete("c-empty")
    assert state.reconcile_canvas_collection(origin="canvas.knu", canvas_course_id=11, resource_kind="module_item",
                                             seen_resource_ids=[], collection_complete=False) == []
    assert not state.recording_calendar_complete("c-empty")
    # An incomplete scan of another resource kind (announcements) does not touch the recording calendar.
    state.replace_recording_calendar_current("c-empty", 11, build_calendar(
        "c-empty", [], semester=SEMESTER, collection_complete=True), collection_complete=True)
    state.reconcile_canvas_collection(origin="canvas.knu", canvas_course_id=11, resource_kind="announcement",
                                      seen_resource_ids=[], collection_complete=False)
    assert state.recording_calendar_complete("c-empty")


def test_observations_are_immutable_per_revision_and_never_regress(tmp_path) -> None:
    state = _store(tmp_path)
    base = {"origin": "canvas.knu", "canvas_course_id": 67535, "resource_kind": "module_item",
            "resource_id": "r1", "module_name": "1주차", "module_week": 1, "item_type": "ExternalTool",
            "collection_complete": True}
    state.record_canvas_observation(observation_revision=2, title="2026-09-03", **base)
    # Same revision, same payload: idempotent.  Same revision, different payload: conflict (R12).
    state.record_canvas_observation(observation_revision=2, title="2026-09-03", **base)
    with pytest.raises(ValueError):
        state.record_canvas_observation(observation_revision=2, title="2026-09-04", **base)
    # The provider's updated_at is part of the immutable revision metadata; observed_at is not (r12 R6).
    with pytest.raises(ValueError):
        state.record_canvas_observation(observation_revision=2, title="2026-09-03", updated_at="2026-09-05T00:00:00Z", **base)
    state.record_canvas_observation(observation_revision=2, title="2026-09-03", observed_at="2026-09-06T00:00:00Z", **base)
    # A delayed older revision is stored inactive; the newest stays active.
    state.record_canvas_observation(observation_revision=1, title="old", **base)
    active = state.list_active_canvas_observations(67535)
    assert [(o["observation_revision"], o["title"]) for o in active] == [(2, "2026-09-03")]
    state.record_canvas_observation(observation_revision=3, title="2026-09-03", **base)
    assert [o["observation_revision"] for o in state.list_active_canvas_observations(67535)] == [3]


SEMESTER = SemesterRange(date(2026, 9, 1), date(2026, 12, 20), "config")
COURSE = "2026-2_LMS67535-001"


def _entry(resource_id: str, week: int, day: str, revision: int = 1, course_id: int = 67535) -> RecordingEntry:
    return RecordingEntry(course_id, resource_id, revision, week, date.fromisoformat(day))


def _cal(entries, *, complete: bool = True, semester=SEMESTER, course_key: str = COURSE) -> CourseCalendar:
    return build_calendar(course_key, list(entries), semester=semester, collection_complete=complete)


BASE_ENTRIES = (_entry("r1", 1, "2026-09-03", 2), _entry("r2", 2, "2026-09-10"), _entry("r4", 4, "2026-12-10"))


def _observe(state, resource_id: str, week: int | None, day: str, revision: int = 1, item_type: str = "ExternalTool",
             title: str | None = None, complete: bool = True) -> None:
    state.record_canvas_observation(origin="canvas.knu", canvas_course_id=67535, resource_kind="module_item",
                                    resource_id=resource_id, observation_revision=revision, title=title or day,
                                    module_name=f"{week}주차" if week else "Intro", module_week=week,
                                    item_type=item_type, collection_complete=complete)


def test_calendar_projection_and_bindings(tmp_path) -> None:
    state = _store(tmp_path)
    # The projection is built from active recording observations only (r9 R1 B).
    with pytest.raises(ValueError):
        state.replace_recording_calendar_current(COURSE, 67535, _cal(BASE_ENTRIES), collection_complete=True)
    _observe(state, "r1", 1, "2026-09-03", 2)
    _observe(state, "r2", 2, "2026-09-10")
    _observe(state, "r4", 4, "2026-12-10")
    rows = state.replace_recording_calendar_current(COURSE, 67535, _cal(BASE_ENTRIES), collection_complete=True)
    assert [(r.week, r.status) for r in rows] == [(1, "CALENDAR"), (2, "CALENDAR"), (4, "ANOMALY")]
    assert state.recording_calendar_complete(COURSE) and all(r.collection_complete for r in rows)
    # An incomplete collection never replaces the projection (R13); it marks the course
    # incomplete and the returned rows mirror that flag (r7 O1).
    rows = state.replace_recording_calendar_current(COURSE, 67535, _cal((), complete=False), collection_complete=False)
    assert [(r.week, r.status) for r in rows] == [(1, "CALENDAR"), (2, "CALENDAR"), (4, "ANOMALY")]
    assert not state.recording_calendar_complete(COURSE) and not any(r.collection_complete for r in rows)
    assert not state.recording_calendar_complete("unknown-course")
    # completeness is a real bool and must agree with the calendar it describes (r7 R3 B).
    for flag in ("false", 1, None):
        with pytest.raises(ValueError):
            state.replace_recording_calendar_current(COURSE, 67535, _cal(BASE_ENTRIES), collection_complete=flag)
    with pytest.raises(ValueError):
        state.replace_recording_calendar_current(COURSE, 67535, _cal(BASE_ENTRIES, complete=False), collection_complete=True)
    # Statuses are never asserted by the caller: a hand-built calendar whose statuses
    # differ from the verified projection is refused (r7 R3 A).
    forged = CourseCalendar(COURSE, (CalendarEntry(_entry("r1", 1, "2026-09-03", 2), EntryStatus.CALENDAR),
                                     CalendarEntry(_entry("r2", 2, "2026-12-10"), EntryStatus.CALENDAR)),
                            False, None, SEMESTER)
    with pytest.raises(ValueError):
        state.replace_recording_calendar_current(COURSE, 67535, forged, collection_complete=True)
    forged_dup = CourseCalendar(COURSE, (CalendarEntry(_entry("d1", 1, "2026-09-03"), EntryStatus.CALENDAR),
                                         CalendarEntry(_entry("d2", 1, "2026-09-03"), EntryStatus.CALENDAR),
                                         CalendarEntry(_entry("r2", 2, "2026-09-10"), EntryStatus.CALENDAR)),
                                False, None, SEMESTER)
    with pytest.raises(ValueError):
        state.replace_recording_calendar_current(COURSE, 67535, forged_dup, collection_complete=True)
    with pytest.raises(ValueError):  # wrong course key / wrong Canvas course inside the entries
        state.replace_recording_calendar_current("other", 67535, _cal(BASE_ENTRIES), collection_complete=True)
    with pytest.raises(ValueError):
        state.replace_recording_calendar_current(COURSE, 67535, _cal((_entry("x1", 1, "2026-09-03", course_id=1),
                                                                      _entry("x2", 2, "2026-09-10", course_id=1))),
                                                 collection_complete=True)
    # A malformed entry is rejected before anything changes (r2 #9, r3 #5).
    for bad in (RecordingEntry(67535, "", 1, 1, date(2026, 9, 3)),
                RecordingEntry(67535, None, 1, 1, date(2026, 9, 3)),
                RecordingEntry(67535, "r9", 1, True, date(2026, 9, 3)),
                RecordingEntry(67535, "r9", 0, 1, date(2026, 9, 3)),
                RecordingEntry(67535, "r9", 1, 1, "2026-09-03")):
        with pytest.raises((ValueError, TypeError, AttributeError)):
            state.replace_recording_calendar_current(COURSE, 67535, _cal((bad, _entry("r2", 2, "2026-09-10"))),
                                                     collection_complete=True)
    # A stale revision (r1 rev 1 after rev 2) and an unobserved extra row (r3) are refused (r6 R3, r9 R1 B).
    with pytest.raises(ValueError):
        state.replace_recording_calendar_current(COURSE, 67535, _cal((_entry("r1", 1, "2026-09-03", 1),
                                                                      BASE_ENTRIES[1], BASE_ENTRIES[2])),
                                                 collection_complete=True)
    with pytest.raises(ValueError):
        state.replace_recording_calendar_current(COURSE, 67535, _cal((*BASE_ENTRIES, _entry("r3", 3, "2026-09-17"))),
                                                 collection_complete=True)
    rows = state.list_recording_calendar_current(COURSE)
    assert [(r.week, r.status) for r in rows] == [(1, "CALENDAR"), (2, "CALENDAR"), (4, "ANOMALY")]
    assert not state.recording_calendar_complete(COURSE)
    state.replace_recording_calendar_current(COURSE, 67535, _cal(BASE_ENTRIES), collection_complete=True)
    assert state.recording_calendar_complete(COURSE)
    # A new active recording observation invalidates the completed projection (r8 R2 A) ...
    _observe(state, "r4", 4, "2026-12-10", 3)
    assert not state.recording_calendar_complete(COURSE)
    # ... and a completed projection must contain every active recording with the same
    # revision, date and week (r8 R2 B): stale r4, r4 on another date/week, are refused.
    for entries in ((BASE_ENTRIES[0], BASE_ENTRIES[1]),
                    BASE_ENTRIES,
                    (BASE_ENTRIES[0], BASE_ENTRIES[1], _entry("r4", 4, "2026-12-11", 3)),
                    (BASE_ENTRIES[0], BASE_ENTRIES[1], _entry("r4", 5, "2026-12-10", 3)),
                    (BASE_ENTRIES[0], BASE_ENTRIES[1], _entry("r4", 4, "2026-12-10", 2)),
                    (BASE_ENTRIES[0], BASE_ENTRIES[1], _entry("r4", 4, "2026-12-10", 4))):
        with pytest.raises(ValueError):
            state.replace_recording_calendar_current(COURSE, 67535, _cal(entries), collection_complete=True)
    assert [(r.resource_id, r.observation_revision) for r in state.list_recording_calendar_current(COURSE)] \
        == [("r1", 2), ("r2", 1), ("r4", 1)]
    assert not state.recording_calendar_complete(COURSE)
    # Duplicate dates come back AMBIGUOUS from the projection and are stored as such.
    _observe(state, "d3", 3, "2026-09-17")
    _observe(state, "d5", 5, "2026-09-17")
    shared = _cal((BASE_ENTRIES[0], BASE_ENTRIES[1], _entry("d3", 3, "2026-09-17"), _entry("d5", 5, "2026-09-17"),
                   _entry("r4", 4, "2026-12-10", 3)))
    rows = state.replace_recording_calendar_current(COURSE, 67535, shared, collection_complete=True)
    assert [(r.resource_id, r.status) for r in rows] == [("r1", "CALENDAR"), ("r2", "CALENDAR"), ("d3", "AMBIGUOUS"),
                                                          ("r4", "ANOMALY"), ("d5", "AMBIGUOUS")]
    assert state.recording_calendar_complete(COURSE)
    state.replace_recording_calendar_current(COURSE, 67535, _cal((), complete=False), collection_complete=False)
    # A course key binds one Canvas course id and vice versa (r4 #4): no projection mixing.
    other_entries = (_entry("z1", 1, "2026-09-03", course_id=99999), _entry("z2", 2, "2026-09-10", course_id=99999))
    with pytest.raises(ValueError):
        state.replace_recording_calendar_current(COURSE, 99999, _cal(other_entries), collection_complete=True)
    with pytest.raises(ValueError):
        state.replace_recording_calendar_current("other-course", 67535, shared, collection_complete=True)
    assert [r.canvas_course_id for r in state.list_recording_calendar_current(COURSE)] == [67535] * 5
    assert state.list_recording_calendar_current("other-course") == []
    # The immutable history fixes (course, resource, revision): a different date rolls everything back.
    with pytest.raises(ValueError):
        state.replace_recording_calendar_current(
            COURSE, 67535, _cal((_entry("r1", 1, "2026-09-04", 2), BASE_ENTRIES[1], _entry("d3", 3, "2026-09-17"),
                                 _entry("d5", 5, "2026-09-17"), _entry("r4", 4, "2026-12-10", 3))),
            collection_complete=True)
    rows = state.list_recording_calendar_current(COURSE)
    assert [(r.recorded_on, r.week) for r in rows] == [("2026-09-03", 1), ("2026-09-10", 2), ("2026-09-17", 3),
                                                        ("2026-12-10", 4), ("2026-09-17", 5)]
    assert not state.recording_calendar_complete(COURSE)
    # A recording that stops being one (new revision is a Page) also invalidates the completed
    # projection and can no longer appear in it (r9 R1 A).
    state.replace_recording_calendar_current(COURSE, 67535, shared, collection_complete=True)
    assert state.recording_calendar_complete(COURSE)
    _observe(state, "r1", 1, "2026-09-03", 3, item_type="Page", title="Some other resource")
    assert not state.recording_calendar_complete(COURSE)
    with pytest.raises(ValueError):
        state.replace_recording_calendar_current(COURSE, 67535, shared, collection_complete=True)
    without_r1 = _cal((BASE_ENTRIES[1], _entry("d3", 3, "2026-09-17"), _entry("d5", 5, "2026-09-17"),
                       _entry("r4", 4, "2026-12-10", 3)))
    rows = state.replace_recording_calendar_current(COURSE, 67535, without_r1, collection_complete=True)
    assert {r.resource_id for r in rows} == {"r2", "d3", "d5", "r4"} and all(r.status == "AMBIGUOUS" for r in rows)
    # A recording without an explicit week never gets a guessed week in a completed
    # projection (r10 R1), and an observation from an incomplete collection never backs
    # one (r10 R3).
    _observe(state, "n1", None, "2026-10-08")
    with pytest.raises(ValueError):
        state.replace_recording_calendar_current(
            COURSE, 67535, _cal((BASE_ENTRIES[1], _entry("d3", 3, "2026-09-17"), _entry("d5", 5, "2026-09-17"),
                                 _entry("r4", 4, "2026-12-10", 3), _entry("n1", 6, "2026-10-08"))),
            collection_complete=True)
    _observe(state, "n1", 6, "2026-10-08", 2, complete=False)
    with pytest.raises(ValueError):
        state.replace_recording_calendar_current(
            COURSE, 67535, _cal((BASE_ENTRIES[1], _entry("d3", 3, "2026-09-17"), _entry("d5", 5, "2026-09-17"),
                                 _entry("r4", 4, "2026-12-10", 3), _entry("n1", 6, "2026-10-08", 2))),
            collection_complete=True)
    _observe(state, "n1", 6, "2026-10-08", 3)
    full = _cal((BASE_ENTRIES[1], _entry("d3", 3, "2026-09-17"), _entry("d5", 5, "2026-09-17"),
                 _entry("r4", 4, "2026-12-10", 3), _entry("n1", 6, "2026-10-08", 3)))
    rows = state.replace_recording_calendar_current(COURSE, 67535, full, collection_complete=True)
    assert {r.resource_id for r in rows} == {"r2", "d3", "d5", "r4", "n1"} and state.recording_calendar_complete(COURSE)
    # A resource that a complete collection no longer lists is deactivated (history kept),
    # the completed projection is invalidated, and the next projection may drop it (r10 R2).
    assert state.reconcile_canvas_collection(origin="canvas.knu", canvas_course_id=67535, resource_kind="module_item",
                                             seen_resource_ids=["r1", "r2", "d3", "d5", "r4", "n1"],
                                             collection_complete=False) == []
    # An incomplete collection keeps every active observation but closes the calendar (r11 R1).
    assert not state.recording_calendar_complete(COURSE)
    assert {o["resource_id"] for o in state.list_active_canvas_observations(67535)} == {"r1", "r2", "d3", "d5", "r4", "n1"}
    rows = state.replace_recording_calendar_current(COURSE, 67535, full, collection_complete=True)
    assert state.recording_calendar_complete(COURSE)
    gone = state.reconcile_canvas_collection(origin="canvas.knu", canvas_course_id=67535, resource_kind="module_item",
                                             seen_resource_ids=["r1", "r2", "d3", "r4", "n1"], collection_complete=True)
    assert gone == ["d5"] and not state.recording_calendar_complete(COURSE)
    assert {o["resource_id"] for o in state.list_active_canvas_observations(67535)} == {"r1", "r2", "d3", "r4", "n1"}
    with pytest.raises(ValueError):  # d5 is no longer an active recording
        state.replace_recording_calendar_current(COURSE, 67535, full, collection_complete=True)
    rows = state.replace_recording_calendar_current(
        COURSE, 67535, _cal((BASE_ENTRIES[1], _entry("d3", 3, "2026-09-17"), _entry("r4", 4, "2026-12-10", 3),
                             _entry("n1", 6, "2026-10-08", 3))),
        collection_complete=True)
    assert {r.resource_id for r in rows} == {"r2", "d3", "r4", "n1"} and state.recording_calendar_complete(COURSE)
    # The deactivated revision never revives on a delayed re-observation of an older revision.
    with pytest.raises(ValueError):
        state.replace_recording_calendar_current(COURSE, 67535, full, collection_complete=True)
    # The same revision listed again by a complete collection reactivates d5 (and reopens the
    # calendar); re-observing an incomplete-collection revision with a complete one upgrades
    # its proof without a new revision (r11 R2).
    _observe(state, "d5", 5, "2026-09-17")
    assert {o["resource_id"] for o in state.list_active_canvas_observations(67535)} >= {"d5"}
    assert not state.recording_calendar_complete(COURSE)
    rows = state.replace_recording_calendar_current(COURSE, 67535, full, collection_complete=True)
    assert {r.resource_id for r in rows} == {"r2", "d3", "d5", "r4", "n1"} and state.recording_calendar_complete(COURSE)
    _observe(state, "n2", 7, "2026-10-15", complete=False)
    seven = _cal((BASE_ENTRIES[1], _entry("d3", 3, "2026-09-17"), _entry("d5", 5, "2026-09-17"),
                  _entry("r4", 4, "2026-12-10", 3), _entry("n1", 6, "2026-10-08", 3), _entry("n2", 7, "2026-10-15")))
    with pytest.raises(ValueError):
        state.replace_recording_calendar_current(COURSE, 67535, seven, collection_complete=True)
    _observe(state, "n2", 7, "2026-10-15")  # same revision, now from a complete collection
    assert state.replace_recording_calendar_current(COURSE, 67535, seven, collection_complete=True)
    # A date-titled ExternalTool recorded under another resource kind is never a recording
    # basis (r12 R3): it neither backs nor is required by a completed projection.
    state.record_canvas_observation(origin="canvas.knu", canvas_course_id=67535, resource_kind="announcement",
                                    resource_id="ann-1", observation_revision=1, title="2026-10-29",
                                    module_name="9주차", module_week=9, item_type="ExternalTool",
                                    collection_complete=True)
    assert state.replace_recording_calendar_current(COURSE, 67535, seven, collection_complete=True)
    with pytest.raises(ValueError):
        state.replace_recording_calendar_current(
            COURSE, 67535, _cal((*[p.entry for p in seven.entries], _entry("ann-1", 9, "2026-10-29"))),
            collection_complete=True)
    # Completed → incomplete projection clears the hash; re-verification restores one (r12 R4).
    assert state.recording_calendar_projection_hash(COURSE) == seven.revision_hash()
    state.replace_recording_calendar_current(COURSE, 67535, _cal((), complete=False), collection_complete=False)
    assert state.recording_calendar_projection_hash(COURSE) is None and not state.recording_calendar_complete(COURSE)
    state.replace_recording_calendar_current(COURSE, 67535, seven, collection_complete=True)
    assert state.recording_calendar_projection_hash(COURSE) == seven.revision_hash()
    # The proof is never derived from truthiness (r11 R3).
    for flag in ("false", "true", 0, 1):
        with pytest.raises(ValueError):
            state.record_canvas_observation(origin="canvas.knu", canvas_course_id=67535, resource_kind="module_item",
                                            resource_id="n3", observation_revision=1, title="2026-10-22",
                                            module_name="8주차", module_week=8, item_type="ExternalTool",
                                            collection_complete=flag)
    assert all(o["resource_id"] != "n3" for o in state.list_active_canvas_observations(67535))
    binding = state.record_canvas_drive_binding(drive_file_id="d1", canvas_course_id=67535, resource_kind="announcement",
                                                resource_id="a1", observation_revision=1, attachment_id="att-1",
                                                attachment_filename="a.pdf", attachment_size=10, byte_sha256="b" * 64)
    second = state.record_canvas_drive_binding(drive_file_id="d2", canvas_course_id=67535, resource_kind="announcement",
                                               resource_id="a1", observation_revision=1, attachment_id="att-2",
                                               attachment_filename="b.pdf", attachment_size=10, byte_sha256="c" * 64)
    assert binding["attachment_id"] == "att-1" and second["attachment_id"] == "att-2"
    with pytest.raises(sqlite3.IntegrityError):
        state.record_canvas_drive_binding(drive_file_id="d3", canvas_course_id=67535, resource_kind="announcement",
                                          resource_id="a1", observation_revision=1, attachment_id="att-1",
                                          attachment_filename="dup.pdf", byte_sha256="d" * 64)
    # Re-recording the same binding is idempotent only when every field matches (R14).
    same = state.record_canvas_drive_binding(drive_file_id="d1", canvas_course_id=67535, resource_kind="announcement",
                                             resource_id="a1", observation_revision=1, attachment_id="att-1",
                                             attachment_filename="a.pdf", attachment_size=10, byte_sha256="b" * 64)
    assert same == binding
    for patch in ({"observation_revision": 2}, {"attachment_filename": "renamed.pdf"}, {"attachment_size": 11}):
        with pytest.raises(ValueError):
            state.record_canvas_drive_binding(**{"drive_file_id": "d1", "canvas_course_id": 67535,
                                                   "resource_kind": "announcement", "resource_id": "a1",
                                                   "observation_revision": 1, "attachment_id": "att-1",
                                                   "attachment_filename": "a.pdf", "attachment_size": 10,
                                                   "byte_sha256": "b" * 64, **patch})
    assert state.get_canvas_drive_binding("d1")["byte_sha256"] == "b" * 64


def test_chunk_tag_ledgers_require_coverage_for_complete(tmp_path) -> None:
    state = _store(tmp_path)
    versions = {"source_fingerprint": "fp", "normalization_version": "n1", "tag_vocab_version": "v1",
                "question_set_version": "q1", "tag_rule_version": "t1", "s4_payload_policy_version": "p1",
                "prompt_version": "pr1", "model": "jev-1.13.0"}
    rule = {"rule_id": "code_block"}
    state.upsert_chunk_tag_decision(doc_id="M01", chunk_id="c1", question_id="assignment", decision="no",
                                    source="rule", detail=rule, **versions)
    state.upsert_chunk_tag_decision(doc_id="M01", chunk_id="c1", question_id="assignment", decision="no",
                                    source="rule", detail=rule, **versions)  # idempotent
    with pytest.raises(ValueError):
        state.upsert_chunk_tag_decision(doc_id="M01", chunk_id="c1", question_id="assignment", decision="yes",
                                        source="rule", detail=rule, **versions)  # conflict (R16)
    with pytest.raises(ValueError):  # same decision, different evidence: still a conflict (r2 #10)
        state.upsert_chunk_tag_decision(doc_id="M01", chunk_id="c1", question_id="assignment", decision="no",
                                        source="rule", detail={"rule_id": "other"}, **versions)
    low = {"confidence": 0.01, "top_probability": 0.60, "probabilities": {"yes": 0.60, "no": 0.40}}
    for bad in ({"decision": "maybe", "source": "rule", "detail": rule},
                {"decision": "no", "source": "rule"},                       # rule without rule_id
                {"decision": "no", "source": "model", "detail": {}},        # model without scores
                {"decision": "no", "source": "model", "detail": {"confidence": 1.2, "top_probability": 0.9}},
                {"decision": "no", "source": "model",                      # no distribution
                 "detail": {"confidence": 0.95, "top_probability": 0.9}},
                {"decision": "no", "source": "model",                      # max != top
                 "detail": {"confidence": 0.95, "top_probability": 0.9, "probabilities": {"no": 0.7, "yes": 0.3}}},
                {"decision": "no", "source": "model",                      # answer is not the top choice (r4 #1)
                 "detail": {"confidence": 0.95, "top_probability": 0.9, "probabilities": {"yes": 0.9, "no": 0.1}}},
                {"decision": "yes", "source": "model",                     # sum != 1 / tied top
                 "detail": {"confidence": 0.95, "top_probability": 0.9, "probabilities": {"yes": 0.9, "no": 0.9}}},
                {"decision": "yes", "source": "model",                     # tied top within a valid sum
                 "detail": {"confidence": 0.95, "top_probability": 0.5, "probabilities": {"yes": 0.5, "no": 0.5}}},
                {"decision": "no", "source": "model",                      # extra option
                 "detail": {"confidence": 0.95, "top_probability": 0.9, "probabilities": {"no": 0.9, "yes": 0.05, "maybe": 0.05}}},
                {"decision": "no", "source": "model", "detail": low},      # low-confidence negative (r3 #1)
                {"decision": "yes", "source": "model", "detail": low},     # low-confidence positive
                {"decision": "unresolved", "source": "model",              # above thresholds is resolved
                 "detail": {"confidence": 0.95, "top_probability": 0.9, "probabilities": {"no": 0.9, "yes": 0.1}}},
                {"decision": "no", "source": "human", "detail": rule},
                {"decision": "unresolved", "source": "rule", "detail": rule}):
        with pytest.raises(ValueError):
            state.upsert_chunk_tag_decision(doc_id="M01", chunk_id="c9", question_id="assignment", **bad, **versions)
    # Exactly at the thresholds a decision is resolved; just below only unresolved is storable.
    state.upsert_chunk_tag_decision(doc_id="M01", chunk_id="c8", question_id="assignment", decision="no", source="model",
                                    detail={"confidence": 0.80, "top_probability": 0.70,
                                            "probabilities": {"no": 0.70, "yes": 0.30}}, **versions)
    with pytest.raises(ValueError):
        state.upsert_chunk_tag_decision(doc_id="M01", chunk_id="c7", question_id="assignment", decision="no", source="model",
                                        detail={"confidence": 0.79, "top_probability": 0.70,
                                                "probabilities": {"no": 0.70, "yes": 0.30}}, **versions)
    with pytest.raises(ValueError):  # the approved thresholds cannot be lowered per call (r5 #1)
        state.upsert_chunk_tag_decision(doc_id="M01", chunk_id="c7", question_id="assignment", decision="no", source="model",
                                        detail={"confidence": 0.10, "top_probability": 0.60,
                                                "probabilities": {"no": 0.60, "yes": 0.40}},
                                        min_confidence=0.0, min_top_probability=0.0, **versions)
    assert len(state.list_chunk_tag_decisions("M01")) == 2
    manifest = state.upsert_document_tag_manifest("M01", chunk_ids=["c1", "c2"], question_ids=["assignment"],
                                                  status="PARTIAL", **versions)
    assert manifest["status"] == "PARTIAL"
    with pytest.raises(ValueError):
        state.upsert_document_tag_manifest("M01", status="DONE", **versions)
    with pytest.raises(ValueError):  # c2 has no decision yet
        state.upsert_document_tag_manifest("M01", chunk_ids=["c1", "c2"], question_ids=["assignment"],
                                           status="COMPLETE", **versions)
    with pytest.raises(ValueError):  # shrinking the chunk set is not a way to COMPLETE (r2 #11)
        state.upsert_document_tag_manifest("M01", chunk_ids=["c1"], question_ids=["assignment"],
                                           status="COMPLETE", **versions)
    scores = {"confidence": 0.95, "top_probability": 0.9, "probabilities": {"yes": 0.9, "no": 0.1}}
    state.upsert_chunk_tag_decision(doc_id="M01", chunk_id="c2", question_id="assignment", decision="unresolved",
                                    source="model", detail={"confidence": 0.5, "top_probability": 0.6,
                                                            "probabilities": {"yes": 0.6, "no": 0.4}}, **versions)
    with pytest.raises(ValueError):  # unresolved never proves coverage
        state.upsert_document_tag_manifest("M01", chunk_ids=["c1", "c2"], question_ids=["assignment"],
                                           status="COMPLETE", **versions)
    # A low-confidence answer is an attempt, not a decision: the question stays open and a
    # later confident answer under the same version tuple resolves it (r9 R4).
    assert len(state.list_chunk_tag_attempts("M01")) == 1
    assert all(d["chunk_id"] != "c2" for d in state.list_chunk_tag_decisions("M01"))
    state.upsert_chunk_tag_decision(doc_id="M01", chunk_id="c2", question_id="assignment", decision="no",
                                    source="model", detail={"confidence": 0.95, "top_probability": 0.9,
                                                            "probabilities": {"no": 0.9, "yes": 0.1}}, **versions)
    assert state.upsert_document_tag_manifest("M01", chunk_ids=["c1", "c2"], question_ids=["assignment"],
                                              status="COMPLETE", **versions)["status"] == "COMPLETE"
    with pytest.raises(ValueError):  # a settled yes/no still never flips
        state.upsert_chunk_tag_decision(doc_id="M01", chunk_id="c2", question_id="assignment", decision="yes",
                                        source="model", detail=scores, **versions)
    state.upsert_chunk_tag_decision(doc_id="M01", chunk_id="c2", question_id="assignment", decision="yes",
                                    source="model", detail=scores, **{**versions, "prompt_version": "pr2"})
    state.upsert_chunk_tag_decision(doc_id="M01", chunk_id="c1", question_id="assignment", decision="no",
                                    source="rule", detail=rule, **{**versions, "prompt_version": "pr2"})
    # The assignment question is mandatory for COMPLETE.
    with pytest.raises(ValueError):
        state.upsert_document_tag_manifest("M01", chunk_ids=["c1", "c2"], question_ids=["reference"],
                                           status="COMPLETE", **{**versions, "prompt_version": "pr2"})
    complete = state.upsert_document_tag_manifest("M01", chunk_ids=["c2", "c1"], question_ids=["assignment"],
                                                  status="COMPLETE", **{**versions, "prompt_version": "pr2"})
    assert complete["status"] == "COMPLETE" and complete["chunk_ids_hash"] == manifest["chunk_ids_hash"]
    # Chunk and question sets are immutable under one version tuple, STALE included (r3 #2):
    # a new evidence basis needs a new version tuple.
    for status in ("PARTIAL", "STALE", "COMPLETE"):
        with pytest.raises(ValueError):
            state.upsert_document_tag_manifest("M01", chunk_ids=["c1", "c2", "c3"], question_ids=["assignment"],
                                               status=status, **{**versions, "prompt_version": "pr2"})
        with pytest.raises(ValueError):
            state.upsert_document_tag_manifest("M01", chunk_ids=["c1"], question_ids=["assignment"],
                                               status=status, **{**versions, "prompt_version": "pr2"})
    assert state.upsert_document_tag_manifest("M01", chunk_ids=["c1", "c2"], question_ids=["assignment"],
                                              status="STALE", **{**versions, "prompt_version": "pr2"})["status"] == "STALE"
    # Question-set shrink under the same tuple is refused too (reproduction B of r3 #2).
    wide = {**versions, "question_set_version": "q2"}
    state.upsert_document_tag_manifest("M01", chunk_ids=["c1"], question_ids=["assignment", "exam_hint"],
                                       status="PARTIAL", **wide)
    state.upsert_chunk_tag_decision(doc_id="M01", chunk_id="c1", question_id="assignment", decision="no",
                                    source="rule", detail=rule, **wide)
    with pytest.raises(ValueError):
        state.upsert_document_tag_manifest("M01", chunk_ids=["c1"], question_ids=["assignment"],
                                           status="COMPLETE", **wide)
    # Round trip X -> Y -> X cannot shrink X's basis either (r4 #2): the basis per version
    # tuple is persisted independently of the current manifest pointer.
    x = {**versions, "prompt_version": "px"}
    y = {**versions, "prompt_version": "py"}
    state.upsert_document_tag_manifest("M02", chunk_ids=["c1", "c2"], question_ids=["assignment", "exam_hint"],
                                       status="PARTIAL", **x)
    state.upsert_document_tag_manifest("M02", chunk_ids=["c1"], question_ids=["assignment"], status="STALE", **y)
    state.upsert_chunk_tag_decision(doc_id="M02", chunk_id="c1", question_id="assignment", decision="no",
                                    source="rule", detail=rule, **x)
    for chunks, questions in ((["c1"], ["assignment"]), (["c1", "c2"], ["assignment"]), (["c1"], ["assignment", "exam_hint"])):
        with pytest.raises(ValueError):
            state.upsert_document_tag_manifest("M02", chunk_ids=chunks, question_ids=questions, status="COMPLETE", **x)
    with pytest.raises(ValueError):  # a different version tuple has no coverage
        state.upsert_document_tag_manifest("M01", chunk_ids=["c1", "c2"], question_ids=["assignment"],
                                           status="COMPLETE", **{**versions, "prompt_version": "pr3"})
    _seed_intake(state)
    row = state.upsert_intake_suggestion("intake-1", suggested_kind="LECTURE_SLIDES", suggested_week=3,
                                         suggestion_source="rule:P3:lecture")
    assert row["suggested_kind"] == "LECTURE_SLIDES" and row["written_to_notion"] == 0
    with pytest.raises(ValueError):
        state.upsert_intake_suggestion("intake-1", kind="x")
