"""Public Queue create, current approval attribution, and write recovery."""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from phase4 import phase4_applier_kwargs, phase4_proposal, ready_phase4

from uls.adapters.notion.base import HumanApprovalApplier, QueueState, upsert_proposal
from uls.adapters.notion.guarded import GuardedNotionWriter
from uls.domain.errors import PolicyViolation, ProviderUnavailableError
from uls.domain.page_range import PageRange


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
@pytest.mark.parametrize("timeout", [False, True])
def test_direct_queue_create_reuses_one_physical_row(wrapped, operation, timeout) -> None:
    reader, backend, *_ = ready_phase4()
    writer = GuardedNotionWriter(backend) if wrapped else backend
    proposal = phase4_proposal(reader, operation=operation, desired_range=PageRange(1, 1))
    backend.create_then_raise = timeout
    first = writer.create_entity("Automation Queue", proposal)
    second = writer.create_entity("Automation Queue", proposal)
    assert first["record_id"] == second["record_id"]
    assert backend.create_calls == 1
    assert len(backend.queue_rows) == 1


@pytest.mark.parametrize("wrapped", [False, True])
def test_direct_create_denies_tampered_existing_row_without_another_write(wrapped) -> None:
    reader, backend, *_ = ready_phase4()
    writer = GuardedNotionWriter(backend) if wrapped else backend
    proposal = phase4_proposal(reader)
    writer.create_entity("Automation Queue", proposal)
    backend.queue_rows[0]["Source Hash"] = "tampered"
    with pytest.raises(PolicyViolation):
        writer.create_entity("Automation Queue", proposal)
    assert backend.create_calls == 1


def test_direct_queue_create_requires_physical_lookup_before_backend_write() -> None:
    reader, _, *_ = ready_phase4()

    class Backend:
        calls = 0

        def create_entity(self, *_args, **_kwargs):
            self.calls += 1

    backend = Backend()
    with pytest.raises(PolicyViolation):
        GuardedNotionWriter(backend).create_entity("Automation Queue", phase4_proposal(reader))
    assert backend.calls == 0


def _approved():
    reader, writer, drive, resolver = ready_phase4()
    proposal = phase4_proposal(reader)
    upsert_proposal(writer, proposal)
    row = writer.queue[proposal["Proposal ID"]]
    row.update({"Decision": "Approve", "State": "APPROVED"})
    applier = HumanApprovalApplier(
        GuardedNotionWriter(writer), decision_by="trusted-reviewer",
        **phase4_applier_kwargs(reader, drive, resolver),
    )
    return reader, writer, drive, resolver, proposal, row, applier


@pytest.mark.parametrize("initial", [None, "reviewer-A"])
@pytest.mark.parametrize("latest", ["reviewer-B", "automation"])
def test_latest_human_attribution_wins_after_source_reads(initial, latest) -> None:
    _, writer, drive, _, proposal, row, applier = _approved()
    if initial is not None:
        row["Decision By"] = initial
    original = drive.read_derived

    def read(ref):
        row["Decision By"] = latest
        return original(ref)

    drive.read_derived = read
    result = applier.apply(proposal["Proposal ID"])
    assert result.state is QueueState.APPLIED
    assert writer.target_mutations == 1
    assert row["Decision By"] == (latest if latest == "reviewer-B" else "trusted-reviewer")


@pytest.mark.parametrize("boundary", ["graph", "fingerprint", "derivative", "binding", "preflight"])
def test_trusted_provider_outage_is_retryable_without_any_write(boundary) -> None:
    reader, writer, drive, _, proposal, row, applier = _approved()

    def unavailable(*_args, **_kwargs):
        raise TimeoutError("temporary")

    if boundary == "graph":
        reader.get_session = unavailable
    elif boundary == "fingerprint":
        drive.get_current_fingerprint = unavailable
    elif boundary == "derivative":
        drive.read_derived = unavailable
    elif boundary == "binding":
        drive.lookup_source_binding = unavailable
    else:
        original = reader.get_session
        calls = 0

        def second_call(entity_id):
            nonlocal calls
            calls += 1
            if calls >= 2:
                raise TimeoutError("preflight")
            return original(entity_id)

        reader.get_session = second_call
    before = writer.update_calls
    with pytest.raises(ProviderUnavailableError):
        applier.apply(proposal["Proposal ID"])
    assert writer.update_calls == before
    assert writer.target_mutations == 0
    assert row["State"] == "APPROVED"


@pytest.mark.parametrize("raises", [False, True])
@pytest.mark.parametrize("readback", ["unavailable", "divergent"])
def test_inconclusive_target_outcome_never_audits_success_or_overwrites_human(raises, readback) -> None:
    reader, writer, _, _, proposal, row, applier = _approved()
    real_update = writer.update_properties
    real_read = reader.get_material_usage
    blocked = False

    def read(session_id):
        if blocked:
            raise TimeoutError("readback unavailable")
        return real_read(session_id)

    def update(target_db, entity_id, patch, **kwargs):
        nonlocal blocked
        result = real_update(target_db, entity_id, patch, **kwargs)
        if writer._target(target_db)[0] == "materialusage":
            if readback == "unavailable":
                blocked = True
            else:
                reader.material_usage["COMP319-S05"][0]["Role"] = "Supporting"
            if raises:
                raise TimeoutError("write outcome unknown")
        return result

    reader.get_material_usage = read
    writer.update_properties = update
    first = applier.apply(proposal["Proposal ID"])
    assert first.state is QueueState.APPROVED
    assert row["State"] == "APPROVED"
    assert "Applied At" not in row
    assert writer.target_mutations == 1
    blocked = False
    second = applier.apply(proposal["Proposal ID"])
    # No exact-desired read-back was observed during the actual target call,
    # so only prepared intent survived. A later desired value cannot prove
    # attribution across attempts (it could be an independent human edit).
    assert second.state is (QueueState.APPROVED if readback == "unavailable" else QueueState.SUPERSEDED)
    assert second.mutated is False
    assert "Applied At" not in row
    assert "Decision By" not in row
    assert "Decision At" not in row
    assert writer.target_mutations == 1


def test_readable_failure_before_mutation_retries_once() -> None:
    _, writer, _, _, proposal, _, applier = _approved()
    writer.raise_before_target = True
    assert applier.apply(proposal["Proposal ID"]).state is QueueState.APPROVED
    assert writer.target_mutations == 0
    assert applier.apply(proposal["Proposal ID"]).state is QueueState.APPLIED
    assert writer.target_mutations == 1


def test_committed_then_raised_readable_target_is_applied_once() -> None:
    _, writer, _, _, proposal, _, applier = _approved()
    writer.mutate_then_raise_target = True
    assert applier.apply(proposal["Proposal ID"]).state is QueueState.APPLIED
    assert applier.apply(proposal["Proposal ID"]).state is QueueState.APPLIED
    assert writer.target_mutations == 1
