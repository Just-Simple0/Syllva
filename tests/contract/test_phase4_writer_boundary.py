"""Provider-facing writer checks must not depend on the strict Fake doing extra work."""
from __future__ import annotations

import pathlib
import sys
from copy import deepcopy

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))
from fake_notion import FakeNotionWriter
from phase4 import phase4_proposal, ready_phase4

from uls.adapters.notion.base import ApprovalReader, upsert_proposal
from uls.adapters.notion.guarded import GuardedNotionWriter
from uls.domain.enums import AutomationActor
from uls.domain.errors import PolicyViolation


@pytest.mark.parametrize("field", ["Name", "ID"])
@pytest.mark.parametrize("invalid", [None, "", 3, True])
def test_required_usage_text_rejected_before_backend(field, invalid):
    class Backend:
        calls = 0
        def create_entity(self, *args, **kwargs):
            self.calls += 1
    backend = Backend()
    properties = {"Name": "Usage", "ID": "mu", "Session": {"relation": [{"id": "s"}]},
                  "Material": {"relation": [{"id": "m"}]}, "Role": "Primary", "Verified": False}
    properties[field] = invalid
    with pytest.raises(PolicyViolation):
        GuardedNotionWriter(backend).create_entity("Material Usage", properties)
    assert backend.calls == 0


def test_strict_fake_uuid_does_not_bypass_ordinary_usage_policy():
    reader, _, _, _ = ready_phase4()
    writer = FakeNotionWriter(reader, database_ids={"Material Usage": "usage-uuid"})
    with pytest.raises(PolicyViolation):
        writer.update_properties("usage-uuid", "MU:existing", {"Role": "Reference"})
    assert writer.update_calls == 0
    assert reader.get_material_usage("COMP319-S05")[0]["Role"] == "Primary"


@pytest.mark.parametrize("tamper", ["duplicate", "semantics"])
def test_guarded_metadata_update_checks_current_physical_queue(tamper):
    reader, backend, _, _ = ready_phase4()
    row = upsert_proposal(backend, phase4_proposal(reader))
    if tamper == "duplicate":
        duplicate = deepcopy(row)
        duplicate["record_id"] = "second-physical-row"
        backend.queue_rows.append(duplicate)
    else:
        row["Review Reason"] = "changed after approval identity"
    with pytest.raises(PolicyViolation):
        GuardedNotionWriter(backend).update_properties(
            "Automation Queue", row["record_id"], {"Name": "new display name"},
        )
    assert backend.update_calls == 0


def test_approval_reader_updates_unique_physical_id():
    reader, backend, _, _ = ready_phase4()
    row = upsert_proposal(backend, phase4_proposal(reader))
    row["Decision"] = "Approve"
    original = backend.update_properties
    def physical_only(target_db, entity_id, patch, **kwargs):
        assert entity_id == row["record_id"]
        return original(target_db, entity_id, patch, **kwargs)
    backend.update_properties = physical_only
    ApprovalReader(backend).sync_state(row["Proposal ID"])
    assert row["State"] == "APPROVED"


def test_reader_capability_cannot_mutate_queue_semantics():
    reader, backend, _, _ = ready_phase4()
    row = upsert_proposal(backend, phase4_proposal(reader))
    with pytest.raises(PolicyViolation):
        GuardedNotionWriter(backend).update_properties(
            "Automation Queue", row["record_id"], {"Review Reason": "rewritten"},
            actor=AutomationActor.APPROVAL_READER,
        )
    assert backend.update_calls == 0


@pytest.mark.parametrize("response", ["object", "none"])
def test_queue_create_without_authoritative_visibility_is_not_success(response):
    from uls.domain.errors import ProviderUnavailableError
    reader, backend, _, _ = ready_phase4()
    original = backend.create_entity
    def invisible_create(*args, **kwargs):
        created = original(*args, **kwargs)
        backend.find_approval_rows = lambda proposal_id: []
        return created if response == "object" else None
    backend.create_entity = invisible_create
    with pytest.raises(ProviderUnavailableError):
        upsert_proposal(backend, phase4_proposal(reader))
    assert backend.create_calls == 1
    assert backend.update_calls == 0


def test_post_create_tampered_queue_is_not_success():
    reader, backend, _, _ = ready_phase4()
    original = backend.create_entity
    def corrupt_create(*args, **kwargs):
        row = original(*args, **kwargs)
        row["Review Reason"] = "changed while creating"
        return row
    backend.create_entity = corrupt_create
    with pytest.raises(PolicyViolation):
        upsert_proposal(backend, phase4_proposal(reader))
    assert backend.create_calls == 1
    assert backend.update_calls == 0


@pytest.mark.parametrize("duplicate", [False, True])
def test_system_terminal_transition_rechecks_physical_identity(duplicate):
    from uls.adapters.notion.base import mark_proposal_superseded
    reader, backend, _, _ = ready_phase4()
    row = upsert_proposal(backend, phase4_proposal(reader))
    row["State"] = "APPROVED"
    row["Decision"] = "Approve"
    if duplicate:
        other = deepcopy(row)
        other["record_id"] = "duplicate"
        backend.queue_rows.append(other)
        with pytest.raises(PolicyViolation):
            mark_proposal_superseded(backend, row["Proposal ID"], "changed source")
        assert backend.update_calls == 0
    else:
        original = backend.update_properties
        def physical_only(target_db, entity_id, patch, **kwargs):
            assert entity_id == row["record_id"]
            return original(target_db, entity_id, patch, **kwargs)
        backend.update_properties = physical_only
        mark_proposal_superseded(backend, row["Proposal ID"], "changed source")
        assert row["State"] == "SUPERSEDED"


def test_reader_state_requires_current_decision_at_writer_boundary():
    reader, backend, _, _ = ready_phase4()
    row = upsert_proposal(backend, phase4_proposal(reader))
    with pytest.raises(PolicyViolation):
        GuardedNotionWriter(backend).update_properties(
            "Automation Queue", row["record_id"], {"State": "APPROVED"},
            actor=AutomationActor.APPROVAL_READER,
        )
    assert backend.update_calls == 0
    assert row["State"] == "PENDING_REVIEW"


def test_guarded_queue_create_validates_canonical_identity_before_backend():
    reader, backend, _, _ = ready_phase4()
    proposal = phase4_proposal(reader)
    proposal["Review Reason"] = "forged mirror"
    with pytest.raises(PolicyViolation):
        GuardedNotionWriter(backend).create_entity("Automation Queue", proposal)
    assert backend.create_calls == 0
    assert backend.queue_rows == []


@pytest.mark.parametrize("change", ["padded_type", "padded_role", "missing_app_id"])
def test_applier_rejects_noncanonical_current_usage_or_type(change):
    from phase4 import phase4_applier_kwargs

    from uls.adapters.notion.base import HumanApprovalApplier, QueueState
    reader, backend, drive, resolver = ready_phase4()
    row = upsert_proposal(backend, phase4_proposal(reader))
    row["State"] = "APPROVED"
    row["Decision"] = "Approve"
    usage = reader.get_material_usage("COMP319-S05")[0]
    if change == "padded_type":
        reader.materials["COMP319-M03"]["Type"] = " Lecture Slides "
    elif change == "padded_role":
        usage["Role"] = " Primary "
    else:
        usage["id"] = usage.pop("ID")
    result = HumanApprovalApplier(
        GuardedNotionWriter(backend), decision_by="reviewer",
        **phase4_applier_kwargs(reader, drive, resolver),
    ).apply(row["Proposal ID"])
    assert result.state is QueueState.SUPERSEDED
    assert backend.target_mutations == 0
    assert usage["Verified"] is False


@pytest.mark.parametrize("field", ["Created", "Updated"])
def test_queue_provider_timestamps_cannot_be_supplied(field):
    reader, backend, _, _ = ready_phase4()
    proposal = phase4_proposal(reader)
    proposal[field] = "2026-09-09T00:00:00Z"
    with pytest.raises(PolicyViolation):
        GuardedNotionWriter(backend).create_entity("Automation Queue", proposal)
    assert backend.create_calls == 0
