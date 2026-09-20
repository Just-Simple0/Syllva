"""C5 uses the actual HAA marker/write path and independent SQLite connections."""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from uls.adapters.notion.base import ApprovalReader, HumanApprovalApplier, NotionAdapter, QueueState
from uls.adapters.notion.guarded import GuardedNotionWriter
from uls.domain.approval_identity import (
    build_usage_proposal_envelope,
    c5_usage_snapshot_json,
    canonical_action_json,
    canonical_semantics_from_queue,
    canonical_usage_proposal_envelope_json,
    derive_proposal_id_for_create,
    derive_usage_slot_key,
    parse_c5_usage_snapshot,
    prove_c5_usage_outcome,
)
from uls.domain.errors import PolicyViolation, ProviderWriteNotAppliedError
from uls.domain.page_range import PageRange
from uls.state.sqlite import _INTAKE_SCHEMA, SQLiteStateStore

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from tests.fixtures.phase4 import phase4_proposal, ready_phase4

REQUEST = "550e8400-e29b-41d4-a716-446655440000"
PHYSICAL = "11111111111111111111111111111111"


def setup_v2(path: Path, operation: str = "update_range") -> tuple[Any, Any, SQLiteStateStore, str, str, HumanApprovalApplier]:
    reader, writer, drive, resolver = ready_phase4()
    proposal = phase4_proposal(
        reader, operation=operation,
        desired_range=PageRange(2, 2) if operation == "update_range" else PageRange(1, 2),
    )
    reader.material_usage["COMP319-S05"][0]["page_id"] = PHYSICAL
    state = SQLiteStateStore(path)
    semantics = canonical_semantics_from_queue(proposal)
    slot = derive_usage_slot_key("COMP319-S05", "COMP319-M03", "Primary")
    state.claim_range_intent_generation(
        usage_slot_key=slot, session_app_id="COMP319-S05", material_app_id="COMP319-M03",
        usage_role="Primary", request_id=REQUEST, receipt_id="receipt-a", receipt_hash="hash-a",
        operation=operation, target_entity_id="MU:existing",
        adopted_usage_app_id="MU:existing" if operation == "update_range" else None,
        slot_occupant_count=1 if operation == "update_range" else 0,
        adopted_usage_provider="notion" if operation == "update_range" else None,
        adopted_usage_provider_row_id=PHYSICAL if operation == "update_range" else None,
    )
    envelope = build_usage_proposal_envelope(slot, REQUEST, 1)
    pid = derive_proposal_id_for_create("PAGE_RANGE" if operation == "update_range" else "MATERIAL_USAGE", envelope, semantics)
    proposal.update({"Proposal ID": pid, "Proposal Envelope": canonical_usage_proposal_envelope_json(envelope)})
    guarded = GuardedNotionWriter(writer)
    guarded.create_entity("Automation Queue", proposal)
    row = writer.queue[pid]
    row["Decision"] = "Approve"  # simulate the human's UI action only
    # HAA uses the guarded writer's supported Queue/read/write subset of this broad protocol.
    adapter = cast(NotionAdapter, guarded)
    ApprovalReader(adapter).sync_state(pid)
    state.bind_range_intent_proposal(usage_slot_key=slot, generation=1, proposal_id=pid)
    state.record_usage_proposal_outbox(
        proposal_id=pid, usage_slot_key=slot, request_id=REQUEST, intent_generation=1,
        action_json=canonical_action_json(semantics), envelope_json=canonical_usage_proposal_envelope_json(envelope),
    )
    state.mark_usage_proposal_published(proposal_id=pid, queue_page_id=row["record_id"])
    applier = HumanApprovalApplier(
        adapter, decision_by="reviewer@example.edu", graph_reader=reader, source_reader=drive,
        source_binding_resolver=resolver, usage_intent_state=state,
    )
    return reader, writer, state, slot, pid, applier


def claim_b(state: SQLiteStateStore, slot: str) -> None:
    state.claim_range_intent_generation(
        usage_slot_key=slot, session_app_id="COMP319-S05", material_app_id="COMP319-M03",
        usage_role="Primary", request_id="660e8400-e29b-41d4-a716-446655440001",
        receipt_id="receipt-b", receipt_hash="hash-b", operation="update_range",
        target_entity_id="MU:existing", adopted_usage_app_id="MU:existing", slot_occupant_count=1,
    )


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_haa_success_persists_physical_binding_and_preserves_decision(tmp_path: Path, operation: str) -> None:
    _, writer, state, slot, pid, applier = setup_v2(tmp_path / "state.db", operation)
    result = applier.apply(pid)
    assert result.state is QueueState.APPLIED
    assert writer.target_mutations == 1
    assert writer.queue[pid]["Decision"] == "Approve"
    head = state.get_range_intent_head(slot)
    assert head is not None and head.current_usage_provider_row_id == PHYSICAL
    claim_b(state, slot)


def test_haa_stale_generation_never_arms_marker(tmp_path: Path) -> None:
    _, writer, state, slot, pid, applier = setup_v2(tmp_path / "state.db")
    claim_b(state, slot)
    with pytest.raises(PolicyViolation):
        applier.apply(pid)
    assert writer.target_mutations == 0
    assert not writer.queue[pid].get("Last Error")
    assert writer.queue[pid]["Decision"] == "Approve"


def test_haa_marker_contention_blocks_other_connection_and_same_applier(tmp_path: Path) -> None:
    _, writer, state, slot, pid, applier = setup_v2(tmp_path / "state.db")
    other = SQLiteStateStore(tmp_path / "state.db")
    original = writer.update_properties
    observed: list[bool] = []

    def update(target_db: str, entity_id: str, patch: Any, **kwargs: Any) -> Any:
        if target_db != "Material Usage" and patch.get("Last Error") and not observed:
            observed.append(True)
            with pytest.raises(ValueError, match="ownership"):
                claim_b(other, slot)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(applier.apply, pid)
                with pytest.raises(PolicyViolation, match="ownership"):
                    future.result(timeout=5)
        return original(target_db, entity_id, patch, **kwargs)

    writer.update_properties = update
    assert applier.apply(pid).state is QueueState.APPLIED
    assert observed and writer.target_mutations == 1
    assert writer.queue[pid]["Decision"] == "Approve"
    state.close()
    other.close()


@pytest.mark.parametrize("boundary", ["marker", "target", "validation"])
def test_haa_ambiguous_or_post_marker_failure_stays_owned_after_restart(tmp_path: Path, boundary: str) -> None:
    _, writer, state, slot, pid, applier = setup_v2(tmp_path / "state.db")
    original = writer.update_properties

    def update(target_db: str, entity_id: str, patch: Any, **kwargs: Any) -> Any:
        if boundary == "marker" and patch.get("Last Error"):
            raise RuntimeError("ambiguous marker transport")
        if boundary == "target" and target_db == "Material Usage":
            raise RuntimeError("ambiguous target transport")
        result = original(target_db, entity_id, patch, **kwargs)
        if boundary == "validation" and patch.get("Last Error"):
            writer.queue[pid]["Decision"] = "Reject"
        return result

    writer.update_properties = update
    try:
        result = applier.apply(pid)
        assert result.state is not QueueState.APPLIED
    except PolicyViolation:
        assert boundary == "validation"
    state.close()
    reopened = SQLiteStateStore(tmp_path / "state.db")
    with pytest.raises(ValueError, match="ownership"):
        claim_b(reopened, slot)
    assert writer.target_mutations == 0
    assert writer.queue[pid]["Decision"] == ("Reject" if boundary == "validation" else "Approve")
    reopened.close()


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
@pytest.mark.parametrize("evidence", ["unchanged", "changed", "unreadable", "pre_callback"])
def test_typed_marker_no_effect_requires_own_attempt_and_unchanged_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, evidence: str,
) -> None:
    path = tmp_path / "state.db"
    reader, writer, state, slot, pid, applier = setup_v2(path, operation)
    guarded = applier._adapter
    assert isinstance(guarded, GuardedNotionWriter)
    preserved = json.loads(json.dumps(writer.queue[pid]))
    attempts: list[str] = []
    proofs: list[tuple[str, str | None]] = []
    original_record = state.record_apply_outcome

    def record(**kwargs: Any) -> None:
        original_record(**kwargs)
        proofs.append(guard_status(path))

    def unreadable(*args: Any, **kwargs: Any) -> str:
        raise TimeoutError("physical readback unavailable")

    def backend(*args: Any, **kwargs: Any) -> Any:
        attempts.append("marker")
        assert guard_status(path) == ("MUTATING", None)
        if evidence == "changed":
            reader.material_usage["COMP319-S05"][0]["page_id"] = "2" * 32
        elif evidence == "unreadable":
            monkeypatch.setattr(applier, "_c5_snapshot", unreadable)
        raise ProviderWriteNotAppliedError("marker backend confirms no write")

    def pre_callback(*args: Any, **kwargs: Any) -> Any:
        assert guard_status(path) == ("HELD", None)
        raise ProviderWriteNotAppliedError("pre-dispatch validation refusal")

    monkeypatch.setattr(state, "record_apply_outcome", record)
    monkeypatch.setattr(writer, "update_properties", backend)
    if evidence == "pre_callback":
        monkeypatch.setattr(guarded, "_current_queue_for_write", pre_callback)
    assert applier.apply(pid).state is QueueState.APPROVED
    assert attempts == ([] if evidence == "pre_callback" else ["marker"])
    assert writer.queue[pid] == preserved and writer.target_mutations == 0
    if evidence in {"changed", "unreadable"}:
        assert proofs == [] and guard_status(path) == ("MUTATING", None)
    elif evidence == "pre_callback":
        assert proofs == [] and guard_status(path) == ("RELEASED", "pre_mutation")
    else:
        assert proofs == [("RESOLVED", "ProviderWriteNotAppliedError")]
        assert guard_status(path) == ("RELEASED", "ProviderWriteNotAppliedError")
    reopened = SQLiteStateStore(path)

    def next_claim() -> None:
        reopened.claim_range_intent_generation(
            usage_slot_key=slot, session_app_id="COMP319-S05", material_app_id="COMP319-M03", usage_role="Primary",
            request_id="660e8400-e29b-41d4-a716-446655440000", receipt_id="receipt-b", receipt_hash="hash-b",
            operation=operation, target_entity_id="MU:existing",
            adopted_usage_app_id="MU:existing" if operation == "update_range" else None,
            slot_occupant_count=1 if operation == "update_range" else 0,
        )

    if evidence in {"changed", "unreadable"}:
        with pytest.raises(ValueError, match="ownership"):
            next_claim()
    else:
        next_claim()
    reopened.close()


def test_haa_trusted_not_applied_releases_only_after_proof(tmp_path: Path) -> None:
    _, writer, state, slot, pid, applier = setup_v2(tmp_path / "state.db")
    original = writer.update_properties

    def update(target_db: str, entity_id: str, patch: Any, **kwargs: Any) -> Any:
        if target_db == "Material Usage":
            raise ProviderWriteNotAppliedError("adapter confirms no effect")
        return original(target_db, entity_id, patch, **kwargs)

    writer.update_properties = update
    assert applier.apply(pid).state is QueueState.APPROVED
    assert writer.queue[pid]["Decision"] == "Approve"
    claim_b(state, slot)


def test_historical_retry_and_hash_conflict_survive_restart(tmp_path: Path) -> None:
    _, _, state, slot, _, _ = setup_v2(tmp_path / "state.db")
    claim_b(state, slot)
    state.close()
    state = SQLiteStateStore(tmp_path / "state.db")
    for receipt_hash, message in [("hash-a", "stale"), ("changed", "conflicts")]:
        with pytest.raises(ValueError, match=message):
            state.claim_range_intent_generation(
                usage_slot_key=slot, session_app_id="COMP319-S05", material_app_id="COMP319-M03",
                usage_role="Primary", request_id=REQUEST, receipt_id="receipt-a", receipt_hash=receipt_hash,
                operation="update_range", target_entity_id="MU:existing", adopted_usage_app_id="MU:existing",
                slot_occupant_count=1,
                adopted_usage_provider="notion", adopted_usage_provider_row_id=PHYSICAL,
            )
    head = state.get_range_intent_head(slot)
    assert head is not None and head.intent_generation == 2


def test_same_receipt_concurrent_retry_allocates_once(tmp_path: Path) -> None:
    _, _, left, slot, _, _ = setup_v2(tmp_path / "state.db")
    right = SQLiteStateStore(tmp_path / "state.db")
    barrier = threading.Barrier(2)

    def retry(store: SQLiteStateStore) -> None:
        barrier.wait(timeout=5)
        claim_b(store, slot)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(retry, [left, right]))
    head = left.get_range_intent_head(slot)
    assert head is not None and head.intent_generation == 2


def row() -> dict[str, Any]:
    return {"provider": "notion", "provider_row_id": PHYSICAL, "usage_app_id": "MU:derived",
            "session_app_id": "S01", "material_app_id": "M01", "usage_role": "Primary",
            "start_page": 1, "end_page": 2, "verified": True}


def test_adoption_binds_actual_external_app_and_physical_identity() -> None:
    expected = row()
    actual = dict(expected, usage_app_id="MU:external", provider_row_id="2" * 32)
    assert prove_c5_usage_outcome(c5_usage_snapshot_json([]), c5_usage_snapshot_json([actual]),
                                 c5_usage_snapshot_json([expected]), "effect") == actual


@pytest.mark.parametrize("defect", ["physical_replacement", "range", "verified", "extra", "malformed", "duplicate"])
def test_structured_proof_refuses_insufficient_evidence(defect: str) -> None:
    before = row()
    after = dict(before)
    if defect == "physical_replacement":
        after["provider_row_id"] = "2" * 32
    elif defect == "range":
        after["end_page"] = 3
    elif defect == "verified":
        after["verified"] = False
    elif defect == "extra":
        after["arbitrary_id_field"] = "MU:derived"
    raw = json.dumps({"schema": "uls.c5-usage-snapshot.v1", "rows": [after, after] if defect == "duplicate" else [after]})
    if defect == "malformed":
        raw = '{"MU:derived":'
    with pytest.raises(ValueError):
        prove_c5_usage_outcome(c5_usage_snapshot_json([before]), raw, c5_usage_snapshot_json([before]), "not_applied")


def test_snapshot_order_and_multiplicity() -> None:
    a = row()
    b = dict(a, provider_row_id="2" * 32, usage_app_id="MU:other", material_app_id="M02")
    assert prove_c5_usage_outcome(c5_usage_snapshot_json([a, b]), c5_usage_snapshot_json([b, a]),
                                 c5_usage_snapshot_json([a]), "not_applied") is None
    assert len(parse_c5_usage_snapshot(c5_usage_snapshot_json([a, b]))) == 2


def guard_args(applier: HumanApprovalApplier, slot: str, pid: str) -> dict[str, Any]:
    baseline = applier._c5_snapshot("COMP319-S05")
    target = parse_c5_usage_snapshot(baseline)[0]
    target["start_page"] = 2
    return {"usage_slot_key": slot, "generation": 1, "proposal_id": pid,
            "baseline_json": baseline, "expected_json": c5_usage_snapshot_json([target])}


def test_owner_cas_non_expiry_and_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, state, slot, pid, applier = setup_v2(tmp_path / "state.db")
    args = guard_args(applier, slot, pid)
    token = state.acquire_apply_lease(**args)
    assert token is not None
    assert not state.release_apply_lease(usage_slot_key=slot, generation=2, invocation_token=token)
    assert not state.release_apply_lease(usage_slot_key=slot, generation=1, invocation_token="other-owner")
    state.mark_apply_mutating(invocation_token=token)
    state.close()
    reopened = SQLiteStateStore(tmp_path / "state.db")
    monkeypatch.setattr("uls.state.sqlite._utc_now", lambda: datetime(2099, 1, 1, tzinfo=UTC).isoformat())
    assert reopened.acquire_apply_lease(**args) is None
    assert not reopened.release_apply_lease(usage_slot_key=slot, generation=1, invocation_token=token)
    with pytest.raises(ValueError, match="ownership"):
        claim_b(reopened, slot)


def test_pre_marker_validation_exception_releases_own_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, writer, state, slot, pid, applier = setup_v2(tmp_path / "state.db")

    def invalid(*args: Any, **kwargs: Any) -> Any:
        raise PolicyViolation("pre-marker validation failed")

    monkeypatch.setattr(applier, "_phase4_approved_queue", invalid)
    with pytest.raises(PolicyViolation, match="pre-marker"):
        applier.apply(pid)
    assert not writer.queue[pid].get("Last Error")
    assert writer.target_mutations == 0
    claim_b(state, slot)


def test_pre_mutation_release_cannot_clear_next_owner(tmp_path: Path) -> None:
    _, _, state, slot, pid, applier = setup_v2(tmp_path / "state.db")
    args = guard_args(applier, slot, pid)
    first = state.acquire_apply_lease(**args)
    assert first is not None
    assert state.release_apply_lease(usage_slot_key=slot, generation=1, invocation_token=first)
    second = state.acquire_apply_lease(**args)
    assert second is not None and first != second
    assert not state.release_apply_lease(usage_slot_key=slot, generation=1, invocation_token=first)
    with pytest.raises(ValueError, match="ownership"):
        claim_b(state, slot)


def test_no_effect_requires_typed_outcome_and_physical_baseline(tmp_path: Path) -> None:
    _, _, state, slot, pid, applier = setup_v2(tmp_path / "state.db")
    args = guard_args(applier, slot, pid)
    token = state.acquire_apply_lease(**args)
    assert token is not None
    state.mark_apply_mutating(invocation_token=token)
    for error in [None, RuntimeError("not proof")]:
        with pytest.raises(ValueError):
            state.record_apply_outcome(invocation_token=token, readback_json=args["baseline_json"], not_applied_error=error)
    replacement = parse_c5_usage_snapshot(args["baseline_json"])
    replacement[0]["provider_row_id"] = "2" * 32
    with pytest.raises(ValueError):
        state.record_apply_outcome(invocation_token=token, readback_json=c5_usage_snapshot_json(replacement),
                                   not_applied_error=ProviderWriteNotAppliedError("no effect"))
    assert not state.release_apply_lease(usage_slot_key=slot, generation=1, invocation_token=token)


def test_external_create_adoption_persists_actual_binding(tmp_path: Path) -> None:
    _, _, state, slot, pid, applier = setup_v2(tmp_path / "state.db", "create_usage")
    expected = parse_c5_usage_snapshot(applier._c5_snapshot("COMP319-S05"))[0]
    expected["verified"] = True
    token = state.acquire_apply_lease(
        usage_slot_key=slot, generation=1, proposal_id=pid,
        baseline_json=c5_usage_snapshot_json([]), expected_json=c5_usage_snapshot_json([expected]),
    )
    assert token is not None
    state.mark_apply_mutating(invocation_token=token)
    actual = dict(expected, usage_app_id="MU:external", provider_row_id="2" * 32)
    state.record_apply_outcome(invocation_token=token, readback_json=c5_usage_snapshot_json([actual]))
    assert state.release_apply_lease(usage_slot_key=slot, generation=1, invocation_token=token)
    state.close()
    reopened = SQLiteStateStore(tmp_path / "state.db")
    head = reopened.get_range_intent_head(slot)
    assert head is not None
    assert head.current_usage_app_id == "MU:external" and head.current_usage_provider_row_id == "2" * 32
    with pytest.raises(ValueError, match="binding"):
        claim_b(reopened, slot)


def test_marker_physical_replacement_never_reaches_target_writer(tmp_path: Path) -> None:
    reader, writer, state, slot, pid, applier = setup_v2(tmp_path / "state.db")
    original = writer.update_properties

    def update(target_db: str, entity_id: str, patch: Any, **kwargs: Any) -> Any:
        result = original(target_db, entity_id, patch, **kwargs)
        if patch.get("Last Error"):
            reader.material_usage["COMP319-S05"][0]["page_id"] = "2" * 32
        return result

    writer.update_properties = update
    assert applier.apply(pid).state is QueueState.APPROVED
    assert writer.target_mutations == 0
    with pytest.raises(ValueError, match="ownership"):
        claim_b(state, slot)


def test_preview_schema_migration_preserves_unrecoverable_history(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    columns = _INTAKE_SCHEMA.split("CREATE TABLE IF NOT EXISTS range_intent_heads (")[1].split(");")[0]
    slot = derive_usage_slot_key("COMP319-S05", "COMP319-M03", "Primary")
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE range_intent_heads (" + columns + ")")
        connection.execute(
            "INSERT INTO range_intent_heads (usage_slot_key, session_app_id, material_app_id, usage_role, "
            "current_usage_app_id, current_request_id, current_receipt_id, receipt_hash, current_operation, "
            "intent_generation, created_at, updated_at, apply_lease_expires_at) "
            "VALUES (?, 'COMP319-S05', 'COMP319-M03', 'Primary', 'MU:existing', ?, 'receipt-a', "
            "'hash-a', 'update_range', 3, 'old', 'old', '2000-01-01')",
            (slot, REQUEST),
        )
    state = SQLiteStateStore(path)
    with pytest.raises(ValueError, match="history"):
        claim_b(state, slot)
    head = state.get_range_intent_head(slot)
    assert head is not None and head.intent_generation == 3
    assert head.apply_lease_expires_at == "2000-01-01"
    assert head.current_usage_provider_row_id is None


def fresh_applier(previous: HumanApprovalApplier, state: SQLiteStateStore) -> HumanApprovalApplier:
    return HumanApprovalApplier(
        previous._adapter, decision_by="reviewer@example.edu", graph_reader=previous._graph_reader,
        source_reader=previous._source_reader, source_binding_resolver=previous._source_binding_resolver,
        config=previous._config, usage_intent_state=state,
    )


def guard_status(path: Path) -> tuple[str, str | None]:
    with sqlite3.connect(path) as connection:
        value = connection.execute("SELECT phase, outcome FROM usage_apply_guards ORDER BY rowid DESC LIMIT 1").fetchone()
    assert value is not None
    return str(value[0]), value[1]


@pytest.mark.parametrize("committed", [True, False], ids=["lost_response", "external_desired"])
def test_v2_ambiguous_desired_never_attributes_effect(tmp_path: Path, committed: bool) -> None:
    path = tmp_path / "state.db"
    reader, writer, state, slot, pid, applier = setup_v2(path)
    original = writer.update_properties

    def update(target_db: str, entity_id: str, patch: Any, **kwargs: Any) -> Any:
        if target_db == "Material Usage":
            if committed:
                original(target_db, entity_id, patch, **kwargs)
            else:
                reader.material_usage["COMP319-S05"][0].update(patch)
            raise RuntimeError("ambiguous response")
        return original(target_db, entity_id, patch, **kwargs)

    writer.update_properties = update
    result = applier.apply(pid)
    assert result.state is QueueState.APPROVED and not result.mutated
    assert guard_status(path) == ("MUTATING", None)
    state.close()
    reopened = SQLiteStateStore(path)
    assert fresh_applier(applier, reopened).apply(pid).state is QueueState.APPROVED
    assert writer.queue[pid]["State"] == "APPROVED"
    assert "Applied At" not in writer.queue[pid]
    assert writer.queue[pid]["Decision"] == "Approve"
    assert writer.target_mutations == int(committed)
    assert not reopened.finalize_resolved_apply(usage_slot_key=slot, generation=1, proposal_id=pid)
    with pytest.raises(ValueError, match="ownership"):
        claim_b(reopened, slot)


class SimulatedCrash(BaseException):
    """Bypass ordinary provider-error handling as process death would."""


@pytest.mark.parametrize("window", ["before_proof", "after_proof", "after_proof_replaced"])
def test_fresh_haa_requires_durable_physical_proof_and_finalizes_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, window: str,
) -> None:
    path = tmp_path / "state.db"
    reader, writer, state, slot, pid, applier = setup_v2(path)
    original = state.record_apply_outcome

    def crash(**kwargs: Any) -> None:
        if window != "before_proof":
            original(**kwargs)
        raise SimulatedCrash()

    monkeypatch.setattr(state, "record_apply_outcome", crash)
    # Process death does not run Python finally blocks; suppress cleanup for this crash probe.
    monkeypatch.setattr(state, "release_apply_lease", lambda **kwargs: False)
    with pytest.raises(SimulatedCrash):
        applier.apply(pid)
    assert guard_status(path)[0] == ("MUTATING" if window == "before_proof" else "RESOLVED")
    assert "effect_observed" in writer.queue[pid]["Last Error"]
    state.close()
    reopened = SQLiteStateStore(path)
    assert not reopened.finalize_resolved_apply(usage_slot_key=slot, generation=2, proposal_id=pid)
    assert not reopened.finalize_resolved_apply(usage_slot_key=slot, generation=1, proposal_id="wrong")
    if window == "after_proof_replaced":
        reader.material_usage["COMP319-S05"][0]["page_id"] = "2" * 32
    result = fresh_applier(applier, reopened).apply(pid)
    if window == "after_proof":
        assert result.state is QueueState.APPLIED
        assert guard_status(path) == ("RELEASED", "effect")
        claim_b(reopened, slot)
    else:
        assert result.state is QueueState.APPROVED
        assert writer.queue[pid]["State"] == "APPROVED"
        assert "Applied At" not in writer.queue[pid]
    if window == "before_proof":
        with pytest.raises(ValueError, match="ownership"):
            claim_b(reopened, slot)
    assert writer.target_mutations == 1
    assert writer.queue[pid]["Decision"] == "Approve"


def test_resolved_no_effect_can_finalize_after_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "state.db"
    _, writer, state, slot, pid, applier = setup_v2(path)
    original_update = writer.update_properties
    original_record = state.record_apply_outcome

    def update(target_db: str, entity_id: str, patch: Any, **kwargs: Any) -> Any:
        if target_db == "Material Usage":
            raise ProviderWriteNotAppliedError("trusted no effect")
        return original_update(target_db, entity_id, patch, **kwargs)

    def crash(**kwargs: Any) -> None:
        original_record(**kwargs)
        raise SimulatedCrash()

    writer.update_properties = update
    monkeypatch.setattr(state, "record_apply_outcome", crash)
    monkeypatch.setattr(state, "release_apply_lease", lambda **kwargs: False)
    with pytest.raises(SimulatedCrash):
        applier.apply(pid)
    assert guard_status(path) == ("RESOLVED", "ProviderWriteNotAppliedError")
    state.close()
    reopened = SQLiteStateStore(path)
    assert reopened.finalize_resolved_apply(usage_slot_key=slot, generation=1, proposal_id=pid)
    assert not reopened.finalize_resolved_apply(usage_slot_key=slot, generation=1, proposal_id=pid)
    claim_b(reopened, slot)
    assert writer.target_mutations == 0 and writer.queue[pid]["Decision"] == "Approve"


@pytest.mark.parametrize("defect", ["action", "envelope", "slot", "request", "generation", "target", "operation", "proposal_id"])
def test_published_invalid_outbox_never_grants_dispatch_or_owner(tmp_path: Path, defect: str) -> None:
    path = tmp_path / "state.db"
    _, _, state, slot, pid, applier = setup_v2(path, "create_usage")
    outbox = state.get_usage_proposal_outbox(pid)
    assert outbox is not None
    action = json.loads(outbox.action_json)
    envelope = json.loads(outbox.envelope_json)
    if defect == "slot":
        action["session_id"] = "COMP319-S06"
        action["old_snapshot"]["session_id"] = "COMP319-S06"
    elif defect == "target":
        action["target_entity_id"] = "MU:wrong"
    elif defect == "operation":
        action["operation"] = "update_range"
    elif defect == "request":
        envelope["request_id"] = "660e8400-e29b-41d4-a716-446655440001"
    elif defect == "generation":
        envelope["intent_generation"] = 2
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE usage_proposal_outbox SET action_json=?, envelope_json=? WHERE proposal_id=?",
                           ('{"bad":true}' if defect == "action" else canonical_action_json(action),
                            '{}' if defect == "envelope" else canonical_usage_proposal_envelope_json(envelope), pid))
        if defect == "proposal_id":
            connection.execute("UPDATE usage_proposal_outbox SET proposal_id='arbitrary'")
            connection.execute("UPDATE range_intent_heads SET current_proposal_id='arbitrary'")
            pid = "arbitrary"
    with pytest.raises(ValueError):
        state.reserve_usage_dispatch(usage_slot_key=slot, generation=1, target_id="MU:existing",
                                     pre_dispatch_snapshot_json=applier._c5_snapshot("COMP319-S05"), provider="notion")
    expected = parse_c5_usage_snapshot(applier._c5_snapshot("COMP319-S05"))[0]
    expected["verified"] = True
    with pytest.raises(ValueError):
        state.acquire_apply_lease(usage_slot_key=slot, generation=1, proposal_id=pid,
                                  baseline_json=applier._c5_snapshot("COMP319-S05"), expected_json=c5_usage_snapshot_json([expected]))
    head = state.get_range_intent_head(slot)
    assert head is not None and head.reservation_state == "NONE" and head.dispatch_attempt_no == 0


@pytest.mark.parametrize("failure", ["timeout", "approval_change"])
def test_internal_arm_preread_failure_releases_held_without_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    from uls.adapters.notion import base

    path = tmp_path / "state.db"
    _, writer, state, slot, pid, applier = setup_v2(path)
    original_arm = applier._phase4_arm_marker
    original_read = base._read_approval
    original_update = writer.update_properties
    inside_arm = False
    prereads: list[str] = []
    marker_writes: list[str] = []

    def arm(*args: Any, **kwargs: Any) -> bool:
        nonlocal inside_arm
        inside_arm = True
        try:
            return original_arm(*args, **kwargs)
        finally:
            inside_arm = False

    def read(*args: Any, **kwargs: Any) -> Any:
        if inside_arm:
            prereads.append(failure)
            assert guard_status(path) == ("HELD", None)
            if failure == "timeout":
                raise TimeoutError("marker-arm internal pre-read timed out")
            writer.queue[pid]["Decision"] = "Reject"
        return original_read(*args, **kwargs)

    def update(target_db: str, entity_id: str, patch: Any, **kwargs: Any) -> Any:
        if "Last Error" in patch:
            marker_writes.append(entity_id)
        return original_update(target_db, entity_id, patch, **kwargs)

    monkeypatch.setattr(applier, "_phase4_arm_marker", arm)
    monkeypatch.setattr(base, "_read_approval", read)
    writer.update_properties = update
    if failure == "approval_change":
        with pytest.raises(PolicyViolation, match="approval changed"):
            applier.apply(pid)
    else:
        assert applier.apply(pid).state is QueueState.APPROVED
    assert prereads == [failure]
    assert marker_writes == [] and writer.target_mutations == 0
    assert guard_status(path) == ("RELEASED", "pre_mutation")
    assert writer.queue[pid]["Decision"] == ("Reject" if failure == "approval_change" else "Approve")
    claim_b(state, slot)


@pytest.mark.parametrize("failure", ["timeout", "reject", "signature", "backend_ambiguity"])
def test_final_guarded_marker_boundary_distinguishes_reads_from_backend_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    path = tmp_path / "state.db"
    _, writer, state, slot, pid, applier = setup_v2(path)
    guarded = applier._adapter
    assert isinstance(guarded, GuardedNotionWriter)
    original_marker = guarded._update_haa_marker
    original_find = writer.find_approval_rows
    in_final_boundary = False
    reads: list[str] = []
    backend_attempts: list[str] = []

    def marker(*args: Any, **kwargs: Any) -> Any:
        nonlocal in_final_boundary
        # This entry occurs only after the real arm's own pre-read succeeded.
        assert guard_status(path) == ("HELD", None)
        in_final_boundary = True
        try:
            return original_marker(*args, **kwargs)
        finally:
            in_final_boundary = False

    def find(proposal_id: str) -> list[dict[str, Any]]:
        if in_final_boundary:
            reads.append(proposal_id)
            assert guard_status(path) == ("HELD", None)
            if failure == "timeout":
                raise TimeoutError("final guarded Queue read")
            if failure == "reject":
                writer.queue[pid]["Decision"] = "Reject"
        return cast("list[dict[str, Any]]", original_find(proposal_id))

    def backend(*args: Any, **kwargs: Any) -> Any:
        backend_attempts.append("attempted")
        assert guard_status(path) == ("MUTATING", None)
        raise TimeoutError("backend invoked, response unknown")

    def unsupported(a: Any, b: Any, c: Any, d: Any, required_extra: Any) -> Any:
        backend_attempts.append("unsupported should never be invoked")

    monkeypatch.setattr(guarded, "_update_haa_marker", marker)
    monkeypatch.setattr(writer, "find_approval_rows", find)
    monkeypatch.setattr(writer, "update_properties", unsupported if failure == "signature" else backend)
    assert applier.apply(pid).state is QueueState.APPROVED
    assert reads and writer.target_mutations == 0
    assert writer.queue[pid]["Decision"] == ("Reject" if failure == "reject" else "Approve")
    if failure == "backend_ambiguity":
        assert backend_attempts == ["attempted"]
        assert guard_status(path) == ("MUTATING", None)
        with pytest.raises(ValueError, match="ownership"):
            claim_b(state, slot)
    else:
        assert backend_attempts == []
        assert guard_status(path) == ("RELEASED", "pre_mutation")
        claim_b(state, slot)


@pytest.mark.parametrize("terminal", [QueueState.SUPERSEDED, QueueState.FAILED])
def test_final_marker_read_preserves_legitimate_terminal_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, terminal: QueueState,
) -> None:
    from uls.adapters.notion.base import _mark_proposal_terminal

    path = tmp_path / "state.db"
    _, writer, state, slot, pid, applier = setup_v2(path)
    guarded = applier._adapter
    assert isinstance(guarded, GuardedNotionWriter)
    original_marker = guarded._update_haa_marker
    original_update = writer.update_properties
    marker_attempts: list[str] = []
    preserved: dict[str, Any] = {}

    def update(target_db: str, entity_id: str, patch: Any, **kwargs: Any) -> Any:
        if "Last Error" in patch and "State" not in patch:
            marker_attempts.append(entity_id)
        return original_update(target_db, entity_id, patch, **kwargs)

    def marker(*args: Any, **kwargs: Any) -> Any:
        assert guard_status(path) == ("HELD", None)
        # The actual arm pre-read succeeded; a legitimate system transition now
        # finishes before the guarded marker path performs its final read.
        _mark_proposal_terminal(guarded, pid, terminal, "concurrent source invalidation")
        preserved.update(writer.queue[pid])
        return original_marker(*args, **kwargs)

    monkeypatch.setattr(writer, "update_properties", update)
    monkeypatch.setattr(guarded, "_update_haa_marker", marker)
    applier.apply(pid)
    assert preserved["State"] == terminal.value and preserved["Decision"] == "Approve"
    assert "concurrent source invalidation" in preserved["Last Error"]
    assert writer.queue[pid] == preserved
    assert marker_attempts == [] and writer.target_mutations == 0
    assert guard_status(path) == ("RELEASED", "pre_mutation")
    claim_b(state, slot)


@pytest.mark.parametrize("change", ["existing_marker", "changed_marker_identity"])
def test_final_marker_read_rechecks_arm_identity_and_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    path = tmp_path / "state.db"
    _, writer, state, slot, pid, applier = setup_v2(path)
    guarded = applier._adapter
    assert isinstance(guarded, GuardedNotionWriter)
    original_marker = guarded._update_haa_marker
    attempts: list[str] = []
    external_markers: list[str] = []

    def marker(target_db: str, entity_id: str, marker: str, **kwargs: Any) -> Any:
        if change == "existing_marker":
            writer.queue[pid]["Last Error"] = marker
            external_markers.append(marker)
        else:
            marker = marker.replace(pid, "different-proposal")
        return original_marker(target_db, entity_id, marker, **kwargs)

    def update(*args: Any, **kwargs: Any) -> Any:
        attempts.append("backend")
        raise AssertionError("rejected marker must never dispatch")

    monkeypatch.setattr(guarded, "_update_haa_marker", marker)
    monkeypatch.setattr(writer, "update_properties", update)
    applier.apply(pid)
    assert attempts == [] and writer.target_mutations == 0
    if external_markers:
        assert writer.queue[pid]["Last Error"] == external_markers[0]
    assert guard_status(path) == ("RELEASED", "pre_mutation")
    claim_b(state, slot)


@pytest.mark.parametrize("arity", [0, 1, 2])
def test_short_marker_backend_never_dispatches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arity: int) -> None:
    path = tmp_path / "state.db"
    _, writer, state, slot, pid, applier = setup_v2(path)
    attempts: list[int] = []

    def zero() -> None:
        attempts.append(0)

    def one(a: Any) -> None:
        attempts.append(1)

    def two(a: Any, b: Any) -> None:
        attempts.append(2)

    monkeypatch.setattr(writer, "update_properties", [zero, one, two][arity])
    applier.apply(pid)
    assert attempts == [] and writer.target_mutations == 0
    assert guard_status(path) == ("RELEASED", "pre_mutation")
    claim_b(state, slot)


@pytest.mark.parametrize("transition", ["effect", "no_effect", "applied_cleanup"])
@pytest.mark.parametrize("terminal", [QueueState.SUPERSEDED, QueueState.FAILED, None])
def test_marker_lifecycle_final_read_preserves_terminal_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transition: str, terminal: QueueState | None,
) -> None:
    from uls.adapters.notion.base import _mark_proposal_terminal

    path = tmp_path / "state.db"
    _, writer, state, slot, pid, applier = setup_v2(path)
    guarded = applier._adapter
    assert isinstance(guarded, GuardedNotionWriter)
    original_marker, original_update = guarded._update_haa_marker, writer.update_properties
    injected = False
    late_attempts: list[str] = []
    preserved: dict[str, Any] = {}

    def update(target_db: str, entity_id: str, patch: Any, **kwargs: Any) -> Any:
        if injected:
            late_attempts.append(entity_id)
        if transition == "no_effect" and "Start Page" in patch:
            raise ProviderWriteNotAppliedError("trusted target refusal")
        return original_update(target_db, entity_id, patch, **kwargs)

    def marker(target_db: str, entity_id: str, marker: str | None, **kwargs: Any) -> Any:
        nonlocal injected
        expected = kwargs.get("expected_marker")
        matches = (
            expected is not None and (
                (transition == "effect" and marker is not None)
                or (transition == "no_effect" and marker is None)
                or (transition == "applied_cleanup" and kwargs.get("expected_state") is QueueState.APPLIED)
            )
        )
        if matches and not injected:
            if terminal is None:
                # A concurrent actor advances/clears the marker. Even a desired
                # external state cannot turn our pre-dispatch refusal into success.
                writer.queue[pid]["Last Error"] = (
                    str(expected).replace("effect_observed", "prepared")
                    if transition == "applied_cleanup" else marker
                )
            elif transition == "applied_cleanup":
                # APPLIED is terminal in the system FSM; simulate provider/UI drift
                # here rather than pretending a legitimate system transition exists.
                writer.queue[pid].update(State=terminal.value, **{"Last Error": "terminal provider reason"})
            else:
                _mark_proposal_terminal(guarded, pid, terminal, "terminal system reason")
            preserved.update(writer.queue[pid])
            injected = True
        return original_marker(target_db, entity_id, marker, **kwargs)

    monkeypatch.setattr(writer, "update_properties", update)
    monkeypatch.setattr(guarded, "_update_haa_marker", marker)
    applier.apply(pid)
    assert injected and late_attempts == []
    assert writer.queue[pid] == preserved and preserved["Decision"] == "Approve"
    assert writer.target_mutations == (0 if transition == "no_effect" else 1)
    if transition == "applied_cleanup":
        assert guard_status(path) == ("RELEASED", "effect")
        claim_b(state, slot)
    else:
        assert guard_status(path) == ("MUTATING", None)
        with pytest.raises(ValueError, match="ownership"):
            claim_b(state, slot)


@pytest.mark.parametrize("signature_kind", ["three", "four", "args", "kwargs"])
def test_strict_marker_signature_preserves_full_payload(signature_kind: str) -> None:
    from uls.adapters.notion.base import _prepare_supported_call

    payload = ("db", "entity", {"Last Error": "marker"}, "actor")
    received: list[tuple[Any, ...]] = []

    def three(db: Any, entity: Any, patch: Any) -> None:
        received.append((db, entity, patch))

    def four(db: Any, entity: Any, patch: Any, actor: Any) -> None:
        received.append((db, entity, patch, actor))

    def args(*values: Any) -> None:
        received.append(values)

    def kwargs(**values: Any) -> None:
        received.append((values["target_db"], values["entity_id"], values["patch"], values["actor"]))

    methods: dict[str, Callable[..., Any]] = {"three": three, "four": four, "args": args, "kwargs": kwargs}
    invoke = _prepare_supported_call(
        methods[signature_kind], payload,
        dict(zip(("target_db", "entity_id", "patch", "actor"), payload, strict=True)), require_signature=True,
    )
    assert received == []
    invoke()
    assert received == [payload[:3] if signature_kind == "three" else payload]


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
@pytest.mark.parametrize("queue_state", [QueueState.APPROVED, QueueState.APPLIED])
@pytest.mark.parametrize("invocation", ["id", "supplied_record", "internal_wrapper"])
def test_raw_adapter_replay_refused_before_finalize_or_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
    queue_state: QueueState, invocation: str,
) -> None:
    path = tmp_path / "state.db"
    _, writer, state, _, pid, applier = setup_v2(path, operation)
    # Build real guarded target/effect proof; preserve the proof and marker across restart.
    monkeypatch.setattr(state, "release_apply_lease", lambda **kwargs: False)
    if queue_state is QueueState.APPROVED:
        original_record = state.record_apply_outcome

        def crash_after_proof(**kwargs: Any) -> None:
            original_record(**kwargs)
            raise SimulatedCrash()

        monkeypatch.setattr(state, "record_apply_outcome", crash_after_proof)
        with pytest.raises(SimulatedCrash):
            applier.apply(pid)
    else:
        monkeypatch.setattr(applier, "_phase4_clear_applied_marker", lambda *args: False)
        assert applier.apply(pid).state is QueueState.APPLIED
    assert guard_status(path) == ("RESOLVED", "effect")
    assert writer.queue[pid]["State"] == queue_state.value
    assert "effect_observed" in writer.queue[pid]["Last Error"]
    preserved = json.loads(json.dumps(writer.queue[pid]))
    state.close()
    reopened = SQLiteStateStore(path)
    raw_applier = HumanApprovalApplier(
        cast("NotionAdapter", writer), decision_by="reviewer@example.edu",
        graph_reader=applier._graph_reader, source_reader=applier._source_reader,
        source_binding_resolver=applier._source_binding_resolver,
        config=applier._config, usage_intent_state=reopened,
    )
    attempts: list[str] = []

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        attempts.append("write or finalize")
        raise AssertionError("raw v2 replay must stop at shared validation")

    monkeypatch.setattr(writer, "update_properties", forbidden)
    monkeypatch.setattr(reopened, "finalize_resolved_apply", forbidden)
    with pytest.raises(PolicyViolation, match="trusted GuardedNotionWriter"):
        if invocation == "internal_wrapper":
            raw_applier._apply_phase4(pid, preserved)
        else:
            raw_applier.apply(preserved if invocation == "supplied_record" else pid)
    assert attempts == [] and writer.queue[pid] == preserved
    assert writer.target_mutations == 1
    assert guard_status(path) == ("RESOLVED", "effect")
    reopened.close()
