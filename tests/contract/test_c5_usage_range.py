"""Receipt, physical identity and production range lifecycle contracts."""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from uls.domain.approval_identity import UsageSlotIdentity, c5_usage_snapshot_json
from uls.domain.errors import ProviderWriteNotAppliedError
from uls.intake.usage_range import ValidatedRangeRequest, parse_usage_range_input
from uls.state.sqlite import SQLiteStateStore

COURSE = "10000000-0000-0000-0000-000000000001"
SESSION = "20000000-0000-0000-0000-000000000001"
MATERIAL = "30000000-0000-0000-0000-000000000001"
REQUEST = "40000000-0000-0000-0000-000000000001"
SOURCE = "50000000-0000-0000-0000-000000000001"


def fields(**updates: Any) -> dict[str, Any]:
    return {"Request Type": "USAGE_RANGE", "Usage Operation": "CREATE",
            "Session": {"relation": [SESSION]}, "Material": {"relation": [MATERIAL]},
            "Usage Role": "Primary", "Range Mode": "BOUNDED", "Start Page": 1, "End Page": 2, **updates}


def test_physical_slot_has_no_app_id_or_range_substitute() -> None:
    slot = UsageSlotIdentity(COURSE, SESSION, MATERIAL, "Primary")
    assert UsageSlotIdentity(COURSE.replace("-", ""), SESSION, MATERIAL, "Primary").key == slot.key
    assert UsageSlotIdentity.from_json(slot.canonical_json) == slot
    assert replace(slot, role="Supporting").key != slot.key
    assert replace(slot, material_page_id=REQUEST).key != slot.key
    with pytest.raises(ValueError):
        replace(slot, session_page_id="COMP319-S05")


@pytest.mark.parametrize("update", [
    {"Start Page": True}, {"End Page": 2.0}, {"Range Mode": "WHOLE"},
    {"Range Mode": "UNKNOWN"}, {"Usage Role": None}, {"Course": [COURSE]},
    {"Session": [SESSION, REQUEST]}, {"Usage Operation": "UPDATE", "Target Usage": [REQUEST]},
    {"Target Usage": [REQUEST]}, {"Unrecognized": None},
])
def test_exact_request_matrix_rejects_residual_or_ambiguous_input(update: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        parse_usage_range_input(fields(**update), submitted=True, cancelled=False)


def test_unknown_is_not_whole_and_system_fields_do_not_change_hash() -> None:
    unknown = parse_usage_range_input(fields(**{"Range Mode": "UNKNOWN", "Start Page": None, "End Page": None}),
                                      submitted=True, cancelled=False)
    assert unknown.page_range(None) is None
    whole = replace(unknown, mode="WHOLE")
    assert whole.user_hash != unknown.user_hash and whole.page_range(None) is not None
    first = parse_usage_range_input(fields(), submitted=True, cancelled=False)
    second = parse_usage_range_input(fields(**{"Result Reference": "status only"}), submitted=True, cancelled=False)
    assert first.user_hash == second.user_hash
    with pytest.raises(ValueError, match="coverage"):
        first.page_range(1)


def test_batch_claim_evidence_requires_two_stable_timestamped_reads() -> None:
    value = parse_usage_range_input(fields(), submitted=True, cancelled=False)
    now = datetime.now(UTC)
    request = ValidatedRangeRequest(
        REQUEST, SOURCE, now, value, UsageSlotIdentity(COURSE, SESSION, MATERIAL, "Primary"),
        "COMP319-S05", "COMP319-M03", now, now + timedelta(seconds=1),
        value.user_hash, value.user_hash, "{}", "human", "human",
    )
    with pytest.raises(ValueError, match="one second"):
        replace(request, second_read_at=now)
    with pytest.raises(ValueError, match="changed"):
        replace(request, second_hash="changed")
    with pytest.raises(ValueError, match="timezone"):
        replace(request, first_read_at=now.replace(tzinfo=None))


def unknown_request(request_id: str = REQUEST) -> ValidatedRangeRequest:
    value = parse_usage_range_input(
        fields(**{"Range Mode": "UNKNOWN", "Start Page": None, "End Page": None}),
        submitted=True, cancelled=False,
    )
    now = datetime.now(UTC)
    slot = UsageSlotIdentity(COURSE, SESSION, MATERIAL, "Primary")
    return ValidatedRangeRequest(
        request_id, SOURCE, now, value, slot, "COMP319-S05", "COMP319-M03",
        now, now + timedelta(seconds=1), value.user_hash, value.user_hash,
        slot.canonical_json, "human", "human",
    )


def test_unknown_receipt_is_monotone_across_independent_connections(tmp_path: Path) -> None:
    left = SQLiteStateStore(tmp_path / "range.db")
    right = SQLiteStateStore(tmp_path / "range.db")
    request = unknown_request()
    args: dict[str, Any] = {"workspace": "semester", "candidate_id": None,
                            "baseline_json": c5_usage_snapshot_json([]), "expected_json": None}
    first = left.claim_usage_request(request, **args)
    newer = right.claim_usage_request(unknown_request("40000000-0000-0000-0000-000000000002"), **args)
    assert first["generation"] == 1 and newer["generation"] == 2
    assert left.claim_usage_request(request, **args) == first
    assert not left.invalidate_usage_request(receipt_id=first["receipt_id"], slot=request.slot.key,
                                             generation=1, reason="old cancellation")
    assert right.invalidate_usage_request(receipt_id=newer["receipt_id"], slot=request.slot.key,
                                          generation=2, reason="withdrawn")
    right.close()
    reopened = SQLiteStateStore(tmp_path / "range.db")
    head = reopened.get_range_intent_head(request.slot.key)
    assert head is not None and head.intent_generation == 2 and not head.active
    assert head.current_proposal_id is None and head.current_usage_app_id is None
    assert reopened.claim_usage_request(unknown_request("40000000-0000-0000-0000-000000000002"), **args) == newer
    inactive = reopened.get_range_intent_head(request.slot.key)
    assert inactive is not None and not inactive.active
    reopened.close()
    left.close()


@pytest.mark.parametrize("outcome", ["success", "different_app", "typed_no_effect", "ambiguous"])
def test_producer_owner_survives_restart_until_exact_outcome(tmp_path: Path, outcome: str) -> None:
    path = tmp_path / "producer.db"
    left, right = SQLiteStateStore(path), SQLiteStateStore(path)
    unknown = unknown_request()
    value = parse_usage_range_input(fields(), submitted=True, cancelled=False)
    request = replace(unknown, input=value, first_hash=value.user_hash, second_hash=value.user_hash)
    expected = {"usage_app_id": "MU:deterministic", "session_app_id": "COMP319-S05",
                "material_app_id": "COMP319-M03", "usage_role": "Primary",
                "start_page": 1, "end_page": 2, "verified": False}
    empty = c5_usage_snapshot_json([])
    from uls.domain.approval_identity import build_material_usage_semantics, canonical_action_json
    from uls.domain.page_range import PageRange

    semantics = build_material_usage_semantics(
        operation="create_usage", target_entity_id="MU:deterministic", session_id="COMP319-S05",
        material_id="COMP319-M03", course_relation_page_id=COURSE, course_key="2026-1_COMP319-002",
        usage_role="Primary", material_type="Lecture Slides", source_class="professor_material",
        old_snapshot={"usage_id": "MU:deterministic", "session_id": "COMP319-S05", "material_id": "COMP319-M03",
                      "role": "Primary", "start_page": 1, "end_page": 2, "verified": False},
        desired_range=PageRange(1, 2),
        session_dependency={"source_ref": {"provider": "google_drive", "file_id": "session"}, "source_hash": "session-hash", "source_version": 1},
        material_dependency={"source_ref": {"provider": "google_drive", "file_id": "material"}, "source_hash": "material-hash", "source_version": 1},
        review_reason="human request",
    )
    claim = left.claim_usage_request(request, workspace="semester", candidate_id="MU:deterministic",
                                    baseline_json=empty, expected_json=json.dumps(expected), action_json=canonical_action_json(semantics))
    token = left.acquire_usage_creation(slot=request.slot.key, generation=1,
                                       receipt_hash=value.user_hash, baseline_json=empty)
    left.mark_usage_creation_mutating(token, baseline_json=empty)
    left.close()
    assert not right.release_usage_creation(token)
    with pytest.raises(ValueError, match="ownership"):
        right.claim_usage_request(unknown_request("40000000-0000-0000-0000-000000000002"),
                                  workspace="semester", candidate_id=None,
                                  baseline_json=empty, expected_json=None)
    if outcome == "typed_no_effect":
        right.record_usage_creation_outcome(token, readback_json=empty,
                                            not_applied_error=ProviderWriteNotAppliedError("not invoked"))
        assert right.release_usage_creation(token)
    elif outcome != "ambiguous":
        row = dict(expected, provider="notion", provider_row_id="6" * 32)
        if outcome == "different_app":
            row["usage_app_id"] = "MU:external"
            with pytest.raises(ValueError, match="unproven"):
                right.record_usage_creation_outcome(token, readback_json=c5_usage_snapshot_json([row]))
            assert not right.release_usage_creation(token)
        else:
            right.record_usage_creation_outcome(token, readback_json=c5_usage_snapshot_json([row]))
            head = right.get_range_intent_head(request.slot.key)
            assert head is not None and head.current_usage_provider_row_id == "6" * 32
            assert head.current_usage_app_id == "MU:deterministic" and head.current_proposal_id is None
            assert not right.release_usage_creation(token)  # Until seal or explicit cancellation.
            right.invalidate_usage_request(receipt_id=claim["receipt_id"], slot=request.slot.key,
                                            generation=1, reason="cancelled after create")
            assert right.release_usage_creation(token)
    right.close()


def test_opt_in_profile_preserves_legacy_and_does_not_expand_intake_write_authority() -> None:
    from uls.adapters.notion.intake import InMemoryNotionWorker, NotionIntakeWriter, intake_schemas
    from uls.domain.errors import PolicyDeniedError

    legacy = intake_schemas()
    extended = intake_schemas("c5-range-v1")
    assert "Usage Operation" not in legacy["input_request"]
    assert "Usage Operation" in extended["input_request"]
    assert set(extended) == set(legacy) | {"material_usage", "automation_queue"}
    extended["sessions"].clear()
    assert intake_schemas()["sessions"] == legacy["sessions"]
    writer = NotionIntakeWriter(InMemoryNotionWorker({}), {"automation_queue": SOURCE}, schema_profile="c5-range-v1")
    with pytest.raises(PolicyDeniedError):
        writer.update_system_record("automation_queue", REQUEST, {"State": "APPROVED"})
    backend = InMemoryNotionWorker({SOURCE: []})
    intake = NotionIntakeWriter(backend, {"input_request": SOURCE}, schema_profile="c5-range-v1")
    draft = intake.create_record("input_request", {
        "Name": "File intake request", "Request Key": "request-key", "Request Revision Hash": "revision",
        "Request Type": "ASSIGN_COURSE", "Intake Items": [MATERIAL], "Submitted": False, "Cancelled": False,
        "Request Status": "Draft", "Workspace Fingerprint": "workspace",
    })
    assert draft["Request Type"] == "ASSIGN_COURSE" and draft["Submitted"] is False


def test_sdk_normalization_keeps_request_observation_metadata() -> None:
    from uls.adapters.notion.intake import _normalize_page

    page = {"id": REQUEST, "created_time": "2026-09-20T00:00:00Z",
            "last_edited_time": "2026-09-20T00:00:01Z", "created_by": {"id": COURSE, "type": "person"},
            "last_edited_by": {"id": COURSE, "type": "person"}, "archived": False,
            "in_trash": False, "parent": {"data_source_id": SOURCE}, "properties": {}}
    row = _normalize_page(page)
    for key in ("created_time", "last_edited_time", "created_by", "last_edited_by", "archived", "in_trash"):
        assert row[key] == page[key]


def test_semester_bridge_keeps_physical_and_application_relations_separate() -> None:
    from uls.adapters.notion.intake import InMemoryNotionWorker
    from uls.adapters.notion.usage_range import UsageRangeNotionBridge, UsageRangeSources

    ids = [f"00000000-0000-0000-0000-{number:012d}" for number in range(1, 8)]
    sources = UsageRangeSources("semester", REQUEST, "semester", ids[0], ids[1], ids[2],
                                ids[3], ids[4], ids[5], file_intake=ids[6])
    backend = InMemoryNotionWorker({
        ids[0]: [], ids[1]: [{"id": COURSE, "Course Key": "COMP319"}],
        ids[2]: [{"id": SESSION, "ID": "COMP319-S05", "Course": {"relation": [COURSE]}}],
        ids[3]: [{"id": MATERIAL, "ID": "COMP319-M03", "Course": {"relation": [COURSE]}}],
        ids[4]: [{"id": SOURCE, "ID": "MU:existing", "Session": {"relation": [SESSION]},
                  "Material": {"relation": [MATERIAL]}, "Verified": False}],
        ids[5]: [{"id": REQUEST, "Proposal ID": "proposal", "State": "PENDING_REVIEW", "Decision": "Pending"}],
        ids[6]: [],
    }, parent_page_id=REQUEST)
    for source, records in backend.data_sources.items():
        for record in records:
            record.update(_parent_data_source_id=source, archived=False, in_trash=False)
    bridge = UsageRangeNotionBridge(backend, sources)
    assert bridge.validate_workspace().ready
    rows = bridge.graph_view().get_material_usage("COMP319-S05")
    assert rows[0]["Session"] == {"relation": [{"id": "COMP319-S05"}]}
    assert rows[0]["_physical_relations"]["Session"] == (SESSION,)
    assert rows[0]["page_id"] == SOURCE
    assert bridge.list_approval_rows()[0]["Proposal ID"] == "proposal"
    assert not hasattr(bridge.graph_view(), "update")
    assert all(event[0] in {"list", "read"} for event in backend.events or [])
    backend.data_sources[ids[5]][0].pop("archived")
    from uls.domain.errors import SourceUnavailableError
    with pytest.raises(SourceUnavailableError, match="archive evidence"):
        bridge.list_approval_rows()


@pytest.mark.parametrize("end_page", [2, 999])
def test_human_range_preparation_validates_sources_without_model_or_provider_write(end_page: int) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
    from tests.fixtures.phase4 import ready_phase4

    from uls.domain.errors import SourcePartialError
    from uls.domain.page_range import PageRange
    from uls.proposal.material_usage import MaterialUsageProposalProducer

    graph, backend, drive, resolver = ready_phase4()
    producer = MaterialUsageProposalProducer(graph_reader=graph, source_reader=drive,
                                             source_binding_resolver=resolver)
    args: dict[str, Any] = {
        "session_id": "COMP319-S05", "material_id": "COMP319-M03", "role": "Primary",
        "operation": "update_range", "target_id": "MU:existing", "desired_range": PageRange(2, end_page),
        "old_snapshot": {"usage_id": "MU:existing", "session_id": "COMP319-S05", "material_id": "COMP319-M03",
                         "role": "Primary", "start_page": 1, "end_page": 2, "verified": False},
    }
    if end_page == 999:
        with pytest.raises(SourcePartialError, match="coverage"):
            producer.prepare_human_range(**args)
    else:
        action = producer.prepare_human_range(**args)
        assert action["desired_range"] == {"start_page": 2, "end_page": 2}
        assert action["material_dependency"]["source_hash"]
    assert backend.create_calls == 0


def handler_fixture(tmp_path: Path) -> tuple[Any, Any, Any, Any]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
    from tests.fixtures.phase4 import ready_phase4

    from uls.adapters.notion.intake import InMemoryNotionWorker
    from uls.adapters.notion.usage_range import UsageRangeNotionBridge, UsageRangeSources
    from uls.config.schema import UlsConfig
    from uls.intake.coordinator import RequestCoordinator
    from uls.intake.usage_range import UsageRangeHandler

    graph, _, drive, resolver = ready_phase4()
    ids = [f"00000000-0000-0000-0000-{number:012d}" for number in range(1, 8)]
    sources = UsageRangeSources("semester", REQUEST, "semester", ids[0], ids[1], ids[2], ids[3], ids[4], ids[5], ids[6])
    course = dict(next(iter(graph.courses.values())), id=COURSE)
    session = dict(graph.sessions["COMP319-S05"], id=SESSION, Course={"relation": [COURSE]})
    material = dict(graph.materials["COMP319-M03"], id=MATERIAL, Course={"relation": [COURSE]})
    now = [datetime(2026, 9, 20, tzinfo=UTC)]
    request = dict(fields(**{"Range Mode": "UNKNOWN", "Start Page": None, "End Page": None}),
                   id=REQUEST, Submitted=True, Cancelled=False, created_time=now[0].isoformat(),
                   created_by={"id": COURSE}, last_edited_by={"id": COURSE})
    backend = InMemoryNotionWorker({ids[0]: [request], ids[1]: [course], ids[2]: [session],
                                   ids[3]: [material], ids[4]: [], ids[5]: [], ids[6]: []}, parent_page_id=REQUEST)
    for source, records in backend.data_sources.items():
        for record in records:
            record.update(_parent_data_source_id=source, archived=False, in_trash=False)
    bridge = UsageRangeNotionBridge(backend, sources)
    state = SQLiteStateStore(tmp_path / "handler.db")
    handler = UsageRangeHandler(state=state, bridge=bridge, graph_reader=bridge.graph_view(), writer=None,
                                source_reader=drive, source_binding_resolver=resolver, config=UlsConfig(),
                                clock=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + timedelta(seconds=seconds)))
    coordinator = RequestCoordinator(workspace="semester", source_ids=(ids[0],), list_records=bridge.list_rows,
                                     handlers={}, clock=lambda: now[0])
    return handler, state, backend, coordinator


def test_actual_handler_unknown_claim_freshness_and_unseen_competitor(tmp_path: Path) -> None:
    from uls.intake.usage_range import UsageFreshnessError

    handler, state, backend, coordinator = handler_fixture(tmp_path)
    snapshot = coordinator.snapshot()
    batch = handler.validate_batch(snapshot)
    assert len(batch.claims) == 1 and not batch.workspace_blocked and not batch.blocked_slots
    result = handler.claim_ordered(batch)
    assert len(result.claims) == 1
    receipt, slot, generation = result.claims[0]
    handler.assert_current(receipt, slot, generation)
    source = handler.bridge.sources.input_requests
    competitor = dict(backend.data_sources[source][0], id="40000000-0000-0000-0000-000000000002")
    backend.data_sources[source].append(competitor)
    with pytest.raises(UsageFreshnessError, match="competing"):
        handler.assert_current(receipt, slot, generation)
    second = handler.claim_ordered(handler.validate_batch(coordinator.snapshot()))
    assert second.claims[0][2] == 2
    backend.data_sources[source][0]["Cancelled"] = True
    stale_cancel = handler.validate_batch(coordinator.snapshot())
    assert slot not in stale_cancel.blocked_slots
    stale_result = handler.claim_ordered(stale_cancel)
    assert slot not in stale_result.blocked_slots
    head = state.get_range_intent_head(slot)
    assert head is not None and head.active and head.intent_generation == 2
    assert head.current_proposal_id is None and head.current_usage_app_id is None
    state.close()


@pytest.mark.parametrize(("boundary", "operation"), [("success", "CREATE"), ("success", "UPDATE"),
    ("verified_update", "UPDATE"),
    ("typed_no_effect", "CREATE"), ("lost_create_response", "CREATE"), ("cancel_after_create", "CREATE"),
    ("new_request_after_marker", "CREATE"), ("cancel_after_target", "CREATE"), ("proof_crash", "CREATE"),
    ("new_unknown_before_approval", "CREATE"), ("terminal_then_cancel", "CREATE")])
def test_sdk_shaped_create_outbox_and_human_approval_lifecycle(tmp_path: Path, boundary: str, operation: str) -> None:
    from types import SimpleNamespace

    from uls.adapters.notion.base import ApprovalReader, HumanApprovalApplier
    from uls.adapters.notion.guarded import GuardedNotionWriter
    from uls.adapters.notion.intake import (
        NotionAPIWorker,
        _normalize_properties,
        _wire_value,
        intake_schemas,
    )
    from uls.adapters.notion.usage_range import UsageRangeNotionBridge
    from uls.config.schema import UlsConfig
    from uls.intake.coordinator import RequestBarrier

    handler, state, memory, coordinator = handler_fixture(tmp_path)
    sources = handler.bridge.sources
    memory.data_sources[sources.input_requests][0].update({"Range Mode": "BOUNDED", "Start Page": 1, "End Page": 2})
    if operation == "UPDATE":
        memory.data_sources[sources.input_requests][0].update({"Usage Operation": "UPDATE", "Usage Role": None,
                                                             "Target Usage": {"relation": [SOURCE]}, "Start Page": 2})
        memory.data_sources[sources.material_usage].append({
            "id": SOURCE, "ID": "MU:existing", "Name": "Existing Usage", "Session": {"relation": [SESSION]},
            "Material": {"relation": [MATERIAL]}, "Role": "Primary", "Start Page": 1, "End Page": 2,
            "Verified": boundary == "verified_update", "archived": False, "in_trash": False, "_parent_data_source_id": sources.material_usage,
        })
    writes: list[str] = []
    schemas = intake_schemas("c5-range-v1")
    logical_by_source = {source: logical for logical, source in sources.mapping().items()}

    def raw(source: str, record: dict[str, Any]) -> dict[str, Any]:
        schema = schemas[logical_by_source[source]]
        props = {}
        for name, value in record.items():
            if name in schema:
                if schema[name]["type"] == "relation" and isinstance(value, dict):
                    props[name] = {"relation": [{"id": item if isinstance(item, str) else item["id"]} for item in value["relation"]]}
                else:
                    props[name] = _wire_value(schema[name]["type"], value)
        return {"id": record["id"], "parent": {"data_source_id": source}, "properties": props,
                "created_time": record.get("created_time", "2026-09-20T00:00:00Z"),
                "created_by": record.get("created_by", {"id": COURSE}),
                "last_edited_by": record.get("last_edited_by", {"id": COURSE}),
                "archived": False, "in_trash": False}

    def retrieve(*, page_id: str) -> dict[str, Any]:
        return next(raw(source, row) for source, rows in memory.data_sources.items() for row in rows if row["id"] == page_id)

    def query(*, data_source_id: str, page_size: int) -> dict[str, Any]:
        del page_size
        return {"results": [raw(data_source_id, row) for row in memory.data_sources[data_source_id]], "has_more": False}

    def create(*, parent: dict[str, str], properties: dict[str, Any]) -> dict[str, Any]:
        source = parent["data_source_id"]
        if source == sources.material_usage and boundary == "typed_no_effect":
            raise ProviderWriteNotAppliedError("provider did not attempt creation")
        writes.append(source)
        row = dict(_normalize_properties(properties), id=f"60000000-0000-0000-0000-{len(writes):012d}",
                   _parent_data_source_id=source, archived=False, in_trash=False)
        memory.data_sources[source].append(row)
        if source == sources.material_usage and boundary == "lost_create_response":
            raise TimeoutError("response lost after provider effect")
        if source == sources.material_usage and boundary == "cancel_after_create":
            memory.data_sources[sources.input_requests][0]["Cancelled"] = True
        return raw(source, row)

    def update(*, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        for source, rows in memory.data_sources.items():
            for row in rows:
                if row["id"] == page_id:
                    writes.append(source)
                    row.update(_normalize_properties(properties))
                    if (boundary == "new_request_after_marker" and source == sources.automation_queue
                        and "Last Error" in properties and "prepared" in str(row.get("Last Error"))):
                        competitor = dict(memory.data_sources[sources.input_requests][0],
                                          id="40000000-0000-0000-0000-000000000002")
                        memory.data_sources[sources.input_requests].append(competitor)
                    if boundary == "cancel_after_target" and source == sources.material_usage:
                        memory.data_sources[sources.input_requests][0]["Cancelled"] = True
                    return raw(source, row)
        raise AssertionError("unknown physical update")

    sdk = SimpleNamespace(pages=SimpleNamespace(retrieve=retrieve, create=create, update=update),
                          data_sources=SimpleNamespace(query=query))
    bridge = UsageRangeNotionBridge(NotionAPIWorker(sdk), sources)
    writer = GuardedNotionWriter(bridge, database_ids={"Material Usage": sources.material_usage,
                                                      "Automation Queue": sources.automation_queue},
                                 automation_queue_id=sources.automation_queue)
    handler.bridge, handler.graph_reader, handler.writer = bridge, bridge.graph_view(), writer
    handler.producer.graph_reader = handler.graph_reader
    coordinator.list_records = bridge.list_rows
    batch = handler.validate_batch(coordinator.snapshot())
    claims = handler.claim_ordered(batch)
    assert len(claims.claims) == 1 and not claims.blocked_slots
    barrier = RequestBarrier(batch.epoch, batch.workspace, frozenset({"usage_range"}), frozenset(), False)
    if boundary == "proof_crash":
        def crash_before_seal(**kwargs: Any) -> str:
            raise RuntimeError("simulated crash after durable physical proof")
        state.seal_usage_proposal = crash_before_seal
    result = handler.publish_pending(barrier)
    if boundary == "proof_crash":
        assert result.outcomes and not result.published
        assert len(memory.data_sources[sources.material_usage]) == 1
        assert state.connection.execute("SELECT phase FROM range_producer_attempts").fetchone()[0] == "RESOLVED"
        state.close()
        state = SQLiteStateStore(tmp_path / "handler.db")
        handler.state = state
        handler.claim_ordered(handler.validate_batch(coordinator.snapshot()))
        result = handler.publish_pending(barrier)
    if boundary in {"typed_no_effect", "lost_create_response", "cancel_after_create"}:
        assert result.outcomes and not result.published
        assert not memory.data_sources[sources.automation_queue]
        slot = claims.claims[0][1]
        owner = state.connection.execute("SELECT phase FROM range_producer_attempts WHERE usage_slot_key=?", (slot,)).fetchone()
        assert owner[0] == ("MUTATING" if boundary == "lost_create_response" else "RELEASED")
        assert len(memory.data_sources[sources.material_usage]) == (0 if boundary == "typed_no_effect" else 1)
        state.close()
        return
    assert not result.outcomes and len(result.published) == 1
    proposal_id = result.published[0][0]
    usage = memory.data_sources[sources.material_usage][0]
    assert usage["Verified"] is (boundary == "verified_update")
    assert len(handler.publish_pending(barrier).published) == 1
    assert len(memory.data_sources[sources.material_usage]) == len(memory.data_sources[sources.automation_queue]) == 1
    queue = memory.data_sources[sources.automation_queue][0]
    if boundary in {"new_unknown_before_approval", "terminal_then_cancel"}:
        if boundary == "new_unknown_before_approval":
            queue.update({"Decision": "Approve", "Decision By": "human"})
            ApprovalReader(writer, automation_queue_id=sources.automation_queue).sync_state(proposal_id)
            competitor = dict(memory.data_sources[sources.input_requests][0],
                              id="40000000-0000-0000-0000-000000000002")
            competitor.update({"Range Mode": "UNKNOWN", "Start Page": None, "End Page": None})
            memory.data_sources[sources.input_requests].append(competitor)
        else:
            queue.update({"State": "FAILED", "Last Error": "existing terminal reason"})
            memory.data_sources[sources.input_requests][0]["Cancelled"] = True
        handler.claim_ordered(handler.validate_batch(coordinator.snapshot()))
        projected = handler.publish_pending(barrier)
        assert not projected.outcomes
        if boundary == "new_unknown_before_approval":
            assert queue["State"] == "SUPERSEDED" and queue["Decision"] == "Approve"
        else:
            assert queue["State"] == "FAILED" and queue["Last Error"] == "existing terminal reason"
        assert usage["Verified"] is False and len(memory.data_sources[sources.material_usage]) == 1
        state.close()
        return
    queue.update({"Decision": "Approve", "Decision By": "human"})
    ApprovalReader(writer, automation_queue_id=sources.automation_queue).sync_state(proposal_id)
    applier = HumanApprovalApplier(writer, usage_intent_state=state, request_freshness=handler,
                                   graph_reader=handler.graph_reader, source_reader=handler.producer.source_reader,
                                   source_binding_resolver=handler.producer.source_binding_resolver,
                                   config=UlsConfig(), automation_queue_id=sources.automation_queue)
    if boundary in {"new_request_after_marker", "cancel_after_target"}:
        from uls.domain.errors import PolicyViolation
        try:
            refused = applier.apply(proposal_id)
        except PolicyViolation:
            refused = None
        assert refused is None or str(refused.state) != "APPLIED"
        assert usage["Verified"] is (boundary == "cancel_after_target")
        assert queue["State"] != "APPLIED" and queue["Decision"] == "Approve"
        owner = state.connection.execute("SELECT phase FROM usage_apply_guards").fetchone()
        assert owner is not None and owner[0] == "MUTATING"
        state.close()
        return
    outcome = applier.apply(proposal_id)
    assert str(outcome.state) == "APPLIED", outcome
    assert usage["Verified"] is (operation == "CREATE" or boundary == "verified_update") and queue["Decision"] == "Approve"
    assert usage["Start Page"] == (1 if operation == "CREATE" else 2)
    from uls.domain.approval_identity import canonical_semantics_from_queue, derive_proposal_id
    from uls.domain.errors import PolicyViolation

    legacy_id = derive_proposal_id(queue["Proposal Type"], canonical_semantics_from_queue(queue))
    queue.pop("Proposal Envelope")
    queue["Proposal ID"] = legacy_id
    count = len(writes)
    assert str(applier.apply(legacy_id).state) == "APPLIED"
    assert len(writes) == count
    queue["State"] = "APPROVED"
    with pytest.raises(PolicyViolation, match="legacy"):
        applier.apply(legacy_id)
    handler.producer.writer = writer
    with pytest.raises(PolicyViolation, match="receipt"):
        handler.producer.propose("COMP319-S05")
    assert len(writes) == count
    state.close()
