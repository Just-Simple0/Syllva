"""Permanent Phase4 rev6 recovery regressions.

These cases exercise recovery through a fresh applier so the result cannot
depend on process-local state from the failed attempt.
"""

from __future__ import annotations

import pathlib
import sys
from datetime import UTC, datetime

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from phase4 import phase4_applier_kwargs, phase4_proposal, ready_phase4

from uls.adapters.notion.base import (
    _PHASE4_APPLY_MARKER_PREFIX,
    HumanApprovalApplier,
    QueueState,
    _phase4_apply_marker_text,
    enforce_write_policy,
    upsert_proposal,
)
from uls.domain.enums import AutomationActor
from uls.domain.errors import PolicyViolation
from uls.domain.page_range import PageRange


def _approved_phase4(operation: str):
    reader, writer, drive, resolver = ready_phase4()
    proposal = phase4_proposal(
        reader,
        operation=operation,
        desired_range=PageRange(2, 2) if operation == "update_range" else PageRange(1, 2),
    )
    upsert_proposal(writer, proposal)
    writer.queue[proposal["Proposal ID"]].update(
        {"Decision": "Approve", "State": "APPROVED"}
    )
    return reader, writer, drive, resolver, proposal


def _apply(reader, writer, drive, resolver, proposal):
    """Apply through a newly constructed applier on every call."""

    return HumanApprovalApplier(
        writer,
        decision_by="reviewer@example.edu",
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        **phase4_applier_kwargs(reader, drive, resolver),
    ).apply(proposal["Proposal ID"])


def _restore_old_target(reader, operation: str) -> None:
    target = reader.get_material_usage("COMP319-S05")[0]
    target.update({"Role": "Primary", "Verified": False})
    if operation == "update_range":
        target.update({"Start Page": 1, "End Page": 2})


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_audit_pending_fresh_replay_cannot_reapply_after_human_restores_old(
    operation: str,
) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    writer.fail_audit_once = True

    first = _apply(reader, writer, drive, resolver, proposal)
    assert first.state is QueueState.APPROVED
    assert writer.target_mutations == 1

    # A human edit restores the exact pre-application snapshot before the
    # audit-pending Queue row is retried by a fresh worker process.
    _restore_old_target(reader, operation)
    second = _apply(reader, writer, drive, resolver, proposal)

    assert second.state is QueueState.APPROVED
    assert second.mutated is False
    assert writer.target_mutations == 1
    assert writer.queue[proposal["Proposal ID"]]["State"] == "APPROVED"
    assert "Applied At" not in writer.queue[proposal["Proposal ID"]]
    target = reader.get_material_usage("COMP319-S05")[0]
    assert target["Role"] == "Primary"
    assert target["Verified"] is False
    if operation == "update_range":
        assert target["Start Page"] == 1
        assert target["End Page"] == 2


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_audit_pending_fresh_replay_finishes_audit_without_target_write(
    operation: str,
) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    writer.fail_audit_once = True

    first = _apply(reader, writer, drive, resolver, proposal)
    assert first.state is QueueState.APPROVED
    assert writer.target_mutations == 1

    second = _apply(reader, writer, drive, resolver, proposal)

    assert second.state is QueueState.APPLIED
    assert second.mutated is False
    assert writer.target_mutations == 1
    assert writer.queue[proposal["Proposal ID"]]["State"] == "APPLIED"


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_confirmed_before_write_failure_remains_retryable(
    operation: str,
) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    writer.raise_before_target = True

    first = _apply(reader, writer, drive, resolver, proposal)
    assert first.state is QueueState.APPROVED
    assert first.mutated is False
    assert writer.target_mutations == 0
    assert writer.queue[proposal["Proposal ID"]]["State"] == "APPROVED"

    second = _apply(reader, writer, drive, resolver, proposal)

    assert second.state is QueueState.APPLIED
    assert second.mutated is True
    assert writer.target_mutations == 1


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
@pytest.mark.parametrize("readback", ["unreadable", "divergent"])
def test_unknown_mutation_recovery_to_old_snapshot_never_reapplies(
    operation: str,
    readback: str,
) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    real_update = writer.update_properties
    real_read = reader.get_material_usage
    blocked = False

    def read(session_id: str):
        if blocked:
            raise TimeoutError("target read-back unavailable")
        return real_read(session_id)

    def update(target_db, entity_id, patch, **kwargs):
        nonlocal blocked
        result = real_update(target_db, entity_id, patch, **kwargs)
        if writer._target(target_db)[0] == "materialusage":
            if readback == "unreadable":
                blocked = True
            else:
                reader.material_usage["COMP319-S05"][0]["Role"] = "Supporting"
            raise TimeoutError("target write outcome is unknown")
        return result

    reader.get_material_usage = read
    writer.update_properties = update

    first = _apply(reader, writer, drive, resolver, proposal)
    assert first.state is QueueState.APPROVED
    assert first.mutated is False
    assert writer.target_mutations == 1
    assert writer.queue[proposal["Proposal ID"]]["State"] == "APPROVED"

    blocked = False
    _restore_old_target(reader, operation)
    second = _apply(reader, writer, drive, resolver, proposal)

    assert second.state is QueueState.APPROVED
    assert second.mutated is False
    assert writer.target_mutations == 1
    assert writer.queue[proposal["Proposal ID"]]["State"] == "APPROVED"
    assert "Applied At" not in writer.queue[proposal["Proposal ID"]]
    target = reader.get_material_usage("COMP319-S05")[0]
    assert target["Role"] == "Primary"
    assert target["Verified"] is False
    if operation == "update_range":
        assert target["Start Page"] == 1
        assert target["End Page"] == 2


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_normal_apply_clears_the_durable_marker(operation: str) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)

    result = _apply(reader, writer, drive, resolver, proposal)

    assert result.state is QueueState.APPLIED
    assert writer.target_mutations == 1
    assert writer.queue[proposal["Proposal ID"]].get("Last Error") is None


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_audit_persists_terminal_state_before_marker_cleanup(operation: str) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    real_update = writer.update_properties
    audit_reached = False

    def update(target_db, entity_id, patch, **kwargs):
        nonlocal audit_reached
        logical, _ = writer._target(target_db)
        if logical == "automationqueue" and patch.get("State") == "APPLIED":
            audit_reached = True
            # This is the provider failure mode that made the old combined
            # patch unsafe: Last Error could be cleared before APPLIED was
            # durably recorded.
            if "Last Error" in patch:
                writer.queue[proposal["Proposal ID"]]["Last Error"] = None
                raise TimeoutError("partial audit clear before terminal state")
        return real_update(target_db, entity_id, patch, **kwargs)

    writer.update_properties = update
    first = _apply(reader, writer, drive, resolver, proposal)

    assert audit_reached is True
    assert first.state is QueueState.APPLIED
    assert writer.target_mutations == 1
    audit_patches = [
        patch
        for _db, _entity_id, patch, _actor in writer.writes
        if patch.get("State") == "APPLIED"
    ]
    assert audit_patches
    assert "Last Error" not in audit_patches[-1]

    _restore_old_target(reader, operation)
    second = _apply(reader, writer, drive, resolver, proposal)
    assert second.state is QueueState.APPLIED
    assert second.mutated is False
    assert writer.target_mutations == 1


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_audit_commit_then_raise_is_confirmed_without_target_replay(
    operation: str,
) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    real_update = writer.update_properties
    audit_raise = True

    def update(target_db, entity_id, patch, **kwargs):
        nonlocal audit_raise
        logical, _ = writer._target(target_db)
        if audit_raise and logical == "automationqueue" and patch.get("State") == "APPLIED":
            real_update(target_db, entity_id, patch, **kwargs)
            audit_raise = False
            raise TimeoutError("audit committed before provider timeout")
        return real_update(target_db, entity_id, patch, **kwargs)

    writer.update_properties = update
    first = _apply(reader, writer, drive, resolver, proposal)

    assert first.state is QueueState.APPLIED
    assert first.mutated is True
    assert writer.target_mutations == 1
    assert writer.queue[proposal["Proposal ID"]]["Last Error"].startswith(
        _PHASE4_APPLY_MARKER_PREFIX
    )

    _restore_old_target(reader, operation)
    second = _apply(reader, writer, drive, resolver, proposal)
    assert second.state is QueueState.APPLIED
    assert second.mutated is False
    assert writer.target_mutations == 1


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_state_only_partial_audit_requires_desired_target_before_recovery(
    operation: str,
) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    real_update = writer.update_properties
    partial = True

    def update(target_db, entity_id, patch, **kwargs):
        nonlocal partial
        logical, _ = writer._target(target_db)
        if partial and logical == "automationqueue" and patch.get("State") == "APPLIED":
            partial = False
            row = writer.queue[proposal["Proposal ID"]]
            row["State"] = "APPLIED"
            raise TimeoutError("audit committed State without audit fields")
        return real_update(target_db, entity_id, patch, **kwargs)

    writer.update_properties = update
    first = _apply(reader, writer, drive, resolver, proposal)

    assert first.state is QueueState.APPROVED
    assert first.mutated is True
    assert writer.target_mutations == 1
    row = writer.queue[proposal["Proposal ID"]]
    assert row["State"] == "APPLIED"
    assert isinstance(row["Last Error"], str)
    assert "Decision By" not in row
    assert "Decision At" not in row
    assert "Applied At" not in row

    _restore_old_target(reader, operation)
    second = _apply(reader, writer, drive, resolver, proposal)

    assert second.state is QueueState.APPROVED
    assert second.mutated is False
    assert writer.target_mutations == 1
    assert "Decision By" not in row
    assert "Decision At" not in row
    assert "Applied At" not in row

    target = reader.get_material_usage("COMP319-S05")[0]
    if operation == "create_usage":
        target["Verified"] = True
    else:
        target.update({"Start Page": 2, "End Page": 2})
    third = _apply(reader, writer, drive, resolver, proposal)

    assert third.state is QueueState.APPLIED
    assert third.mutated is False
    assert writer.target_mutations == 1
    assert isinstance(row.get("Decision By"), str)
    assert isinstance(row.get("Decision At"), str)
    assert isinstance(row.get("Applied At"), str)


def test_applied_marker_cleanup_failure_leaves_harmless_terminal_marker() -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4("create_usage")
    real_update = writer.update_properties
    cleanup_failed = True

    def update(target_db, entity_id, patch, **kwargs):
        nonlocal cleanup_failed
        logical, _ = writer._target(target_db)
        if cleanup_failed and logical == "automationqueue" and patch == {"Last Error": None}:
            cleanup_failed = False
            raise TimeoutError("marker cleanup outcome unknown")
        return real_update(target_db, entity_id, patch, **kwargs)

    writer.update_properties = update
    first = _apply(reader, writer, drive, resolver, proposal)

    assert first.state is QueueState.APPLIED
    assert writer.target_mutations == 1
    marker = writer.queue[proposal["Proposal ID"]]["Last Error"]
    assert isinstance(marker, str)
    assert marker.startswith(_PHASE4_APPLY_MARKER_PREFIX)

    _restore_old_target(reader, "create_usage")
    second = _apply(reader, writer, drive, resolver, proposal)
    assert second.state is QueueState.APPLIED
    assert second.mutated is False
    assert writer.target_mutations == 1


def test_marker_commit_then_raise_with_unavailable_readback_stays_conservative() -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4("create_usage")
    real_update = writer.update_properties
    real_find = writer.find_approval_rows
    readback_unavailable = False

    def update(target_db, entity_id, patch, **kwargs):
        nonlocal readback_unavailable
        result = real_update(target_db, entity_id, patch, **kwargs)
        last_error = patch.get("Last Error")
        if isinstance(last_error, str) and last_error.startswith(
            _PHASE4_APPLY_MARKER_PREFIX
        ):
            readback_unavailable = True
            raise TimeoutError("marker write committed before provider timeout")
        return result

    def find(proposal_id: str):
        if readback_unavailable:
            raise TimeoutError("marker read-back unavailable")
        return real_find(proposal_id)

    writer.update_properties = update
    writer.find_approval_rows = find

    first = _apply(reader, writer, drive, resolver, proposal)
    assert first.state is QueueState.APPROVED
    assert first.mutated is False
    assert writer.target_mutations == 0
    assert writer.queue[proposal["Proposal ID"]]["Last Error"].startswith(
        _PHASE4_APPLY_MARKER_PREFIX
    )

    readback_unavailable = False
    second = _apply(reader, writer, drive, resolver, proposal)
    assert second.state is QueueState.APPROVED
    assert second.mutated is False
    assert writer.target_mutations == 0


def test_verified_marker_after_commit_then_raise_can_continue_same_unattempted_call() -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4("create_usage")
    real_update = writer.update_properties
    marker_raise = True

    def update(target_db, entity_id, patch, **kwargs):
        nonlocal marker_raise
        result = real_update(target_db, entity_id, patch, **kwargs)
        last_error = patch.get("Last Error")
        if (
            marker_raise
            and isinstance(last_error, str)
            and last_error.startswith(_PHASE4_APPLY_MARKER_PREFIX)
        ):
            marker_raise = False
            raise TimeoutError("marker write committed before provider timeout")
        return result

    writer.update_properties = update
    result = _apply(reader, writer, drive, resolver, proposal)

    assert result.state is QueueState.APPLIED
    assert result.mutated is True
    assert writer.target_mutations == 1
    assert writer.queue[proposal["Proposal ID"]].get("Last Error") is None


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_marker_appearing_during_preflight_is_not_claimed_by_current_arm(
    operation: str,
) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    real_usage = reader.get_material_usage
    appeared = False

    def usage(session_id: str):
        nonlocal appeared
        rows = real_usage(session_id)
        if not appeared:
            from uls.domain.approval_identity import canonical_semantics_from_queue

            row = writer.queue[proposal["Proposal ID"]]
            target_patch = (
                {"Verified": True}
                if operation == "create_usage"
                else {"Start Page": 2, "End Page": 2}
            )
            row["Last Error"] = _phase4_apply_marker_text(
                proposal["Proposal ID"],
                row["Proposal Type"],
                canonical_semantics_from_queue(row),
                target_patch,
            )
            appeared = True
        return rows

    reader.get_material_usage = usage
    result = _apply(reader, writer, drive, resolver, proposal)

    assert result.state is QueueState.APPROVED
    assert result.mutated is False
    assert writer.target_mutations == 0
    assert writer.queue[proposal["Proposal ID"]]["Last Error"].startswith(
        _PHASE4_APPLY_MARKER_PREFIX
    )


def test_confirmed_before_write_failure_keeps_marker_when_clear_is_unverified() -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4("create_usage")
    writer.raise_before_target = True
    real_update = writer.update_properties
    clear_failed = True

    def update(target_db, entity_id, patch, **kwargs):
        nonlocal clear_failed
        if (
            clear_failed
            and patch.get("Last Error", object()) is None
            and writer._target(target_db)[0] == "automationqueue"
        ):
            clear_failed = False
            raise TimeoutError("marker clear failed")
        return real_update(target_db, entity_id, patch, **kwargs)

    writer.update_properties = update
    first = _apply(reader, writer, drive, resolver, proposal)

    assert first.state is QueueState.APPROVED
    assert first.mutated is False
    assert writer.target_mutations == 0
    marker = writer.queue[proposal["Proposal ID"]].get("Last Error")
    assert isinstance(marker, str)
    assert marker.startswith(_PHASE4_APPLY_MARKER_PREFIX)

    second = _apply(reader, writer, drive, resolver, proposal)
    assert second.state is QueueState.APPROVED
    assert second.mutated is False
    assert writer.target_mutations == 0
    assert writer.queue[proposal["Proposal ID"]]["Last Error"] == marker


def test_malformed_reserved_marker_fails_closed_before_target_write() -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4("create_usage")
    writer.queue[proposal["Proposal ID"]]["Last Error"] = (
        _PHASE4_APPLY_MARKER_PREFIX + "{malformed"
    )

    with pytest.raises(PolicyViolation):
        _apply(reader, writer, drive, resolver, proposal)

    assert writer.target_mutations == 0
    assert writer.queue[proposal["Proposal ID"]]["State"] == "APPROVED"


def test_conflicting_reserved_marker_fails_closed_before_target_write() -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4("create_usage")
    row = writer.queue[proposal["Proposal ID"]]
    from uls.domain.approval_identity import canonical_semantics_from_queue

    row["Last Error"] = _phase4_apply_marker_text(
        proposal["Proposal ID"],
        "MATERIAL_USAGE",
        canonical_semantics_from_queue(row),
        {"Verified": False},
    )

    with pytest.raises(PolicyViolation):
        _apply(reader, writer, drive, resolver, proposal)

    assert writer.target_mutations == 0
    assert row["State"] == "APPROVED"


@pytest.mark.parametrize("alias", ["Last Error", "last_error"])
def test_ordinary_automation_cannot_clear_or_forge_the_marker(alias: str) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4("create_usage")
    writer.fail_audit_once = True
    _apply(reader, writer, drive, resolver, proposal)
    row = writer.queue[proposal["Proposal ID"]]
    marker = row["Last Error"]

    with pytest.raises(PolicyViolation):
        writer.update_properties(
            "Automation Queue",
            row["record_id"],
            {alias: None},
            actor=AutomationActor.AUTOMATION,
        )

    spoof = dict(proposal)
    spoof["Last Error"] = None
    with pytest.raises(PolicyViolation):
        upsert_proposal(writer, spoof)

    assert row["Last Error"] == marker
    assert writer.target_mutations == 1


def test_ordinary_automation_cannot_create_or_ai_write_queue_last_error() -> None:
    _reader, writer, _drive, _resolver, proposal = _approved_phase4("create_usage")
    with pytest.raises(PolicyViolation):
        writer.create_entity(
            "Automation Queue",
            {**proposal, "Last Error": None},
            actor=AutomationActor.AUTOMATION,
        )
    with pytest.raises(PolicyViolation):
        writer.write_ai_region(
            "Automation Queue",
            proposal["Proposal ID"],
            {"enrichment": {}, "ownership": "AI"},
            actor=AutomationActor.AUTOMATION,
        )
    with pytest.raises(PolicyViolation):
        enforce_write_policy(
            AutomationActor.AUTOMATION,
            "Automation Queue",
            {"LastError": None},
        )
    assert len(writer.queue_rows) == 1
