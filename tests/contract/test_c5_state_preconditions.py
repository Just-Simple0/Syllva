"""Atomic adoption and operation-specific baseline checks across SQLite connections."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from tests.contract.test_c5_haa import PHYSICAL, REQUEST, claim_b, guard_args, setup_v2

from uls.domain.approval_identity import (
    c5_usage_snapshot_json,
    derive_usage_slot_key,
    parse_c5_usage_snapshot,
)
from uls.domain.errors import ProviderWriteNotAppliedError
from uls.state.sqlite import SQLiteStateStore


def claim_args(operation: str) -> dict[str, Any]:
    return {
        "usage_slot_key": derive_usage_slot_key("COMP319-S05", "COMP319-M03", "Primary"),
        "session_app_id": "COMP319-S05", "material_app_id": "COMP319-M03", "usage_role": "Primary",
        "request_id": REQUEST, "receipt_id": "receipt", "receipt_hash": "hash",
        "operation": operation, "target_entity_id": "MU:existing", "adopted_usage_app_id": "MU:existing",
        "slot_occupant_count": 1, "adopted_usage_provider": "notion", "adopted_usage_provider_row_id": PHYSICAL,
    }


@pytest.mark.parametrize("operation", ["update_range", "create_usage"])
@pytest.mark.parametrize("defect", ["logical_only", "zero_occupants", "wrong_app", "missing_app", "missing_provider", "missing_physical"])
def test_invalid_initial_adoption_never_persists_head_or_ledger(
    tmp_path: Path, operation: str, defect: str,
) -> None:
    path = tmp_path / "state.db"
    left = SQLiteStateStore(path)
    right = SQLiteStateStore(path)
    args = claim_args(operation)
    if defect == "logical_only":
        args.update(adopted_usage_provider=None, adopted_usage_provider_row_id=None)
    elif defect == "zero_occupants":
        args["slot_occupant_count"] = 0
    elif defect == "wrong_app":
        args["adopted_usage_app_id"] = "MU:other"
    else:
        args[{"missing_app": "adopted_usage_app_id", "missing_provider": "adopted_usage_provider",
              "missing_physical": "adopted_usage_provider_row_id"}[defect]] = None
    with pytest.raises(ValueError):
        left.claim_range_intent_generation(**args)
    assert right.get_range_intent_head(args["usage_slot_key"]) is None
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM range_intent_claims").fetchone()[0] == 0
    # The corrected tuple can use the SAME receipt: the rejected claim did not poison history.
    head = right.claim_range_intent_generation(**claim_args(operation))
    assert head.intent_generation == 1 and head.current_usage_app_id == "MU:existing"
    assert head.current_usage_provider == "notion" and head.current_usage_provider_row_id == PHYSICAL
    left.close()
    right.close()


@pytest.mark.parametrize("defect", ["empty", "physical", "app", "slot", "range", "verified", "extra_occupant"])
def test_invalid_update_baseline_creates_no_guard(tmp_path: Path, defect: str) -> None:
    path = tmp_path / "state.db"
    _, _, left, slot, pid, applier = setup_v2(path)
    right = SQLiteStateStore(path)
    baseline = parse_c5_usage_snapshot(applier._c5_snapshot("COMP319-S05"))
    expected = dict(baseline[0], start_page=2)
    bad = [dict(baseline[0])]
    if defect == "empty":
        bad = []
    elif defect == "extra_occupant":
        bad.append(dict(bad[0], provider_row_id="2" * 32, usage_app_id="MU:other"))
    else:
        key, value = {"physical": ("provider_row_id", "2" * 32), "app": ("usage_app_id", "MU:wrong"),
                      "slot": ("material_app_id", "COMP319-M04"), "range": ("start_page", 2),
                      "verified": ("verified", True)}[defect]
        bad[0][key] = value
    args: dict[str, Any] = {"usage_slot_key": slot, "generation": 1, "proposal_id": pid,
            "baseline_json": c5_usage_snapshot_json(bad), "expected_json": c5_usage_snapshot_json([expected])}
    with pytest.raises(ValueError):
        left.acquire_apply_lease(**args)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM usage_apply_guards").fetchone()[0] == 0
    args["baseline_json"] = c5_usage_snapshot_json(baseline)
    token = right.acquire_apply_lease(**args)
    assert token is not None
    assert right.release_apply_lease(usage_slot_key=slot, generation=1, invocation_token=token)
    left.close()
    right.close()


@pytest.mark.parametrize("mode", ["UPDATE_EXISTING", "CREATE_EXISTING", "CREATE_EMPTY"])
def test_valid_baseline_modes_prove_only_their_intended_operation(tmp_path: Path, mode: str) -> None:
    path = tmp_path / "state.db"
    operation = "update_range" if mode == "UPDATE_EXISTING" else "create_usage"
    _, _, left, slot, pid, applier = setup_v2(path, operation)
    right = SQLiteStateStore(path)
    rows = parse_c5_usage_snapshot(applier._c5_snapshot("COMP319-S05"))
    expected = dict(rows[0])
    if operation == "update_range":
        expected["start_page"] = 2
    else:
        expected["verified"] = True
    token = right.acquire_apply_lease(
        usage_slot_key=slot, generation=1, proposal_id=pid,
        baseline_json=c5_usage_snapshot_json([] if mode == "CREATE_EMPTY" else rows),
        expected_json=c5_usage_snapshot_json([expected]),
    )
    assert token is not None
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT baseline_mode FROM usage_apply_guards").fetchone()[0] == mode
    right.mark_apply_mutating(invocation_token=token)
    actual = dict(expected)
    if mode == "CREATE_EMPTY":
        actual.update(provider_row_id="2" * 32, usage_app_id="MU:external")
    right.record_apply_outcome(invocation_token=token, readback_json=c5_usage_snapshot_json([actual]))
    assert left.finalize_resolved_apply(usage_slot_key=slot, generation=1, proposal_id=pid)
    head = left.get_range_intent_head(slot)
    assert head is not None and head.current_usage_app_id == actual["usage_app_id"]
    assert head.current_usage_provider_row_id == actual["provider_row_id"]
    left.close()
    right.close()


@pytest.mark.parametrize("defect", ["verified", "wrong_app", "wrong_physical", "old_range", "competing_slot"])
def test_existing_create_baseline_requires_exact_unverified_target(tmp_path: Path, defect: str) -> None:
    path = tmp_path / "state.db"
    _, _, state, slot, pid, applier = setup_v2(path, "create_usage")
    baseline = parse_c5_usage_snapshot(applier._c5_snapshot("COMP319-S05"))
    expected = dict(baseline[0], verified=True)
    if defect == "competing_slot":
        baseline.append(dict(baseline[0], provider_row_id="2" * 32, usage_app_id="MU:other"))
    else:
        key, value = {"verified": ("verified", True), "wrong_app": ("usage_app_id", "MU:other"),
                      "wrong_physical": ("provider_row_id", "2" * 32), "old_range": ("start_page", 2)}[defect]
        baseline[0][key] = value
    with pytest.raises(ValueError):
        state.acquire_apply_lease(usage_slot_key=slot, generation=1, proposal_id=pid,
                                  baseline_json=c5_usage_snapshot_json(baseline), expected_json=c5_usage_snapshot_json([expected]))
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM usage_apply_guards").fetchone()[0] == 0


def test_prebound_create_accepts_existing_target_but_refuses_empty_baseline(tmp_path: Path) -> None:
    _, _, fixture_state, slot, pid, applier = setup_v2(tmp_path / "fixture.db", "create_usage")
    outbox = fixture_state.get_usage_proposal_outbox(pid)
    assert outbox is not None and outbox.queue_page_id is not None
    state = SQLiteStateStore(tmp_path / "prebound.db")
    state.claim_range_intent_generation(**claim_args("create_usage"))
    state.bind_range_intent_proposal(usage_slot_key=slot, generation=1, proposal_id=pid)
    state.record_usage_proposal_outbox(
        proposal_id=pid, usage_slot_key=slot, request_id=REQUEST, intent_generation=1,
        action_json=outbox.action_json, envelope_json=outbox.envelope_json,
    )
    state.mark_usage_proposal_published(proposal_id=pid, queue_page_id=outbox.queue_page_id)
    baseline = parse_c5_usage_snapshot(applier._c5_snapshot("COMP319-S05"))
    expected = c5_usage_snapshot_json([dict(baseline[0], verified=True)])
    with pytest.raises(ValueError, match="binding"):
        state.acquire_apply_lease(usage_slot_key=slot, generation=1, proposal_id=pid,
                                  baseline_json=c5_usage_snapshot_json([]), expected_json=expected)
    token = state.acquire_apply_lease(usage_slot_key=slot, generation=1, proposal_id=pid,
                                     baseline_json=c5_usage_snapshot_json(baseline), expected_json=expected)
    assert token is not None
    state.mark_apply_mutating(invocation_token=token)
    state.record_apply_outcome(invocation_token=token, readback_json=expected)
    assert state.finalize_resolved_apply(usage_slot_key=slot, generation=1, proposal_id=pid)


def copy_preexisting_database(source: Path, destination: Path, *, missing: tuple[str, ...]) -> None:
    """Build an old schema with real claim/dispatch/proof rows before migration opens it."""
    with sqlite3.connect(source) as original, sqlite3.connect(destination) as legacy:
        original.row_factory = sqlite3.Row
        for table in ("range_intent_heads", "range_intent_claims", "usage_proposal_outbox", "usage_apply_guards"):
            ddl = original.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
            for column in missing:
                ddl = ddl.replace(f", {column} TEXT", "").replace(f"    {column} TEXT,\n", "")
            legacy.execute(ddl)
            for row in original.execute(f"SELECT * FROM {table}"):
                columns = [column for column in row.keys() if column not in missing]  # noqa: SIM118 - sqlite Row iterates values
                legacy.execute(
                    f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                    [row[column] for column in columns],
                )


@pytest.mark.parametrize("missing", [
    ("current_usage_provider", "current_usage_provider_row_id"),
    ("current_usage_provider",), ("current_usage_provider_row_id",),
])
def test_migrated_partial_binding_with_history_cannot_consume_receipt(tmp_path: Path, missing: tuple[str, ...]) -> None:
    source, path = tmp_path / "current.db", tmp_path / "legacy.db"
    _, _, original, slot, _, _ = setup_v2(source)
    original.close()
    copy_preexisting_database(source, path, missing=missing)
    left, right = SQLiteStateStore(path), SQLiteStateStore(path)
    before = right.get_range_intent_head(slot)
    args = claim_args("update_range")
    args.update(request_id="660e8400-e29b-41d4-a716-446655440000", receipt_id="new-receipt")
    with pytest.raises(ValueError, match="permanent physical binding is incomplete"):
        left.claim_range_intent_generation(**args)
    assert right.get_range_intent_head(slot) == before
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT receipt_id FROM range_intent_claims").fetchall() == [("receipt-a",)]
    left.close()
    right.close()


def test_migrated_resolved_without_mode_cannot_release_even_with_exact_token(tmp_path: Path) -> None:
    source, path = tmp_path / "current.db", tmp_path / "legacy.db"
    _, _, original, slot, pid, applier = setup_v2(source)
    args = guard_args(applier, slot, pid)
    token = original.acquire_apply_lease(**args)
    assert token is not None
    original.mark_apply_mutating(invocation_token=token)
    original.record_apply_outcome(invocation_token=token, readback_json=args["expected_json"])
    original.close()
    copy_preexisting_database(source, path, missing=("baseline_mode",))
    left, right = SQLiteStateStore(path), SQLiteStateStore(path)
    with pytest.raises(ValueError):
        left.release_apply_lease(usage_slot_key=slot, generation=1, invocation_token=token)
    with pytest.raises(ValueError):
        right.finalize_resolved_apply(usage_slot_key=slot, generation=1, proposal_id=pid)
    with pytest.raises(ValueError, match="ownership"):
        claim_b(right, slot)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT phase, baseline_mode FROM usage_apply_guards").fetchall() == [("RESOLVED", None)]
    left.close()
    right.close()


@pytest.mark.parametrize("outcome", ["HELD", "effect", "not_applied"])
def test_current_exact_owner_cleanup_allows_next_generation(tmp_path: Path, outcome: str) -> None:
    path = tmp_path / "current.db"
    _, _, left, slot, pid, applier = setup_v2(path)
    right = SQLiteStateStore(path)
    args = guard_args(applier, slot, pid)
    token = left.acquire_apply_lease(**args)
    assert token is not None
    if outcome != "HELD":
        left.mark_apply_mutating(invocation_token=token)
        left.record_apply_outcome(
            invocation_token=token,
            readback_json=args["expected_json"] if outcome == "effect" else args["baseline_json"],
            not_applied_error=ProviderWriteNotAppliedError("confirmed no effect") if outcome == "not_applied" else None,
        )
    assert right.release_apply_lease(usage_slot_key=slot, generation=1, invocation_token=token)
    claim_b(left, slot)
    head = right.get_range_intent_head(slot)
    assert head is not None and head.intent_generation == 2
    left.close()
    right.close()
