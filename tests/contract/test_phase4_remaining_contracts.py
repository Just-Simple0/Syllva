"""Permanent regression coverage for the Phase4 safety boundaries."""

from __future__ import annotations

import pathlib
import sys
from datetime import UTC, datetime

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from phase4 import phase4_applier_kwargs, phase4_proposal, ready_phase4

from uls.adapters.drive.binding import (
    InMemorySourceBindingBackend,
    SourceBindingRecord,
    ValidatedSourceBindingResolver,
)
from uls.adapters.notion.base import HumanApprovalApplier, QueueState, upsert_proposal
from uls.adapters.notion.guarded import GuardedNotionWriter
from uls.config.schema import UlsConfig
from uls.domain.enums import AutomationActor
from uls.domain.errors import PolicyViolation, UlsError
from uls.domain.models import PageLocator
from uls.domain.page_range import PageRange
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.ephemeral.models import AllowedLocator
from uls.proposal.material_usage import MaterialUsageProposalProducer
from uls.retrieval.capabilities import CapabilityManager, authorize_locator
from uls.retrieval.chunking import page_chunks
from uls.retrieval.engine import RetrievalEngine
from uls.retrieval.schemas import CapabilityBinding
from uls.retrieval.scope import material_usage_scopes


class _ReadTrap:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        def reject(*args, **kwargs):
            del args, kwargs
            self.calls.append(name)
            raise AssertionError("invalid request reached provider")

        return reject


@pytest.mark.parametrize("invalid", ["false", "true", 0, 1, None, [], {}])
def test_include_provisional_type_is_rejected_before_provider_reads(invalid) -> None:
    notion, drive = _ReadTrap(), _ReadTrap()
    engine = RetrievalEngine(notion, drive, None, MemoryEphemeralStore(), UlsConfig())
    with pytest.raises(UlsError):
        engine.get_session_context("COMP319-S05", include_provisional=invalid)
    assert notion.calls == []
    assert drive.calls == []


@pytest.mark.parametrize("invalid", ["false", "true", 0, 1, None, [], {}])
def test_runtime_provisional_config_type_is_rejected_before_provider_reads(invalid) -> None:
    notion, drive = _ReadTrap(), _ReadTrap()
    config = UlsConfig()
    config.retrieval.allow_provisional_material_usage = invalid
    engine = RetrievalEngine(notion, drive, None, MemoryEphemeralStore(), config)
    with pytest.raises(UlsError):
        engine.get_session_context("COMP319-S05", include_provisional=True)
    assert notion.calls == []
    assert drive.calls == []


@pytest.mark.parametrize("bad", [{}, None, {"id": ""}, {"id": 42}])
def test_mixed_malformed_course_relation_is_not_collapsed(bad) -> None:
    from uls.domain.course_identity import resolve_course_relation

    assert resolve_course_relation([{"id": "course-a"}, bad]) is None


def test_typed_empty_source_binding_is_rejected() -> None:
    record = SourceBindingRecord(
        "COMP319-M03",
        "normalized",
        SourceRef("google_drive", "normalized"),
        SourceRef("", ""),
    )
    with pytest.raises((UlsError, ValueError, TypeError)):
        resolver = ValidatedSourceBindingResolver(InMemorySourceBindingBackend([record]))
        resolver.resolve_derivative_ref("COMP319-M03", "normalized")


@pytest.mark.parametrize("body", ["plain unmarked material", "Page 1\nA\nPage 1\nB"])
def test_unjustified_pages_never_generate_evidence(body: str) -> None:
    try:
        chunks = page_chunks(body, entity_id="COMP319-M03")
    except (UlsError, ValueError):
        return
    assert chunks == []


@pytest.mark.parametrize("body", ["Page 1.5\nA", "Page 1\nA\nPage 0\nB\nPage 2\nC"])
def test_malformed_numeric_page_declarations_are_denied(body: str) -> None:
    try:
        chunks = page_chunks(body, entity_id="COMP319-M03")
    except (UlsError, ValueError):
        return
    assert chunks == []


def test_omitted_and_explicit_null_range_have_the_same_whole_source_sentinel() -> None:
    usage = {
        "ID": "mu",
        "Session": "COMP319-S05",
        "Material": "COMP319-M03",
        "Role": "Primary",
        "Verified": True,
    }
    omitted = material_usage_scopes([usage])
    explicit = material_usage_scopes([{**usage, "Start Page": None, "End Page": None}])
    assert len(omitted) == len(explicit) == 1
    assert omitted[0].range == explicit[0].range


@pytest.mark.parametrize("reverse", [False, True])
def test_selected_lower_ephemeral_entry_is_exact_and_order_independent(reverse: bool) -> None:
    old = AllowedLocator("COMP319-M03:p1-p10", "old", 1)
    current = AllowedLocator("COMP319-M03:p5-p15", "current", 2)
    entries = [old, current]
    if reverse:
        entries.reverse()
    store = MemoryEphemeralStore()
    capability = store.create_context_capability(entries, caller_scope="root")
    assert store.authorize_locator(
        capability.context_id,
        "COMP319-M03:p7",
        "root",
        current_fingerprint=SourceFingerprint(2, "current"),
        issued_entry=current,
    )
    forged = AllowedLocator("COMP319-M03:p1-p99", "current", 2)
    assert not store.authorize_locator(
        capability.context_id,
        "COMP319-M03:p70",
        "root",
        current_fingerprint=SourceFingerprint(2, "current"),
        issued_entry=forged,
    )
    assert not store.authorize_locator(
        capability.context_id,
        "COMP319-M03:p7",
        "other",
        current_fingerprint=SourceFingerprint(2, "current"),
        issued_entry=current,
    )


def test_manager_role_only_callback_cannot_authorize_managed_material() -> None:
    store = MemoryEphemeralStore()
    manager = CapabilityManager(store)
    binding = CapabilityBinding(
        entity_id="COMP319-M03",
        locator=PageLocator("COMP319-M03", 1, 1),
        source_hash="h",
        source_version=1,
        source_class="professor_material",
        source_ref=SourceRef("google_drive", "m"),
        session_id="COMP319-S05",
        material_id="COMP319-M03",
        relation_required=True,
        usage_id="mu",
        usage_role="Primary",
        material_type="Lecture Slides",
        course_relation_page_id="course",
        course_key="2026-1_COMP319-002",
        usage_range=PageRange(1, 1),
    )
    capability = manager.issue([binding])
    with pytest.raises(UlsError):
        authorize_locator(
            store,
            capability.context_id,
            "COMP319-M03:p1",
            None,
            current_fingerprint=SourceFingerprint(1, "h"),
            role_validator=lambda _: True,
            manager=manager,
        )


def test_guarded_uuid_usage_update_is_denied_before_backend() -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []

    class Backend:
        def update_properties(self, target_db, entity_id, patch, *, actor):
            calls.append((target_db, entity_id, patch))

    writer = GuardedNotionWriter(
        Backend(),
        database_ids={"Material Usage": "usage-uuid", "Automation Queue": "queue-uuid"},
    )
    with pytest.raises(UlsError):
        writer.update_properties(
            "usage-uuid",
            "mu",
            {"Role": "Reference"},
            actor=AutomationActor.AUTOMATION,
        )
    assert calls == []


def test_guarded_region_and_property_shapes_fail_closed_before_backend() -> None:
    calls: list[object] = []

    class Backend:
        def create_entity(self, *args, **kwargs):
            calls.append((args, kwargs))

        def write_ai_region(self, *args, **kwargs):
            calls.append((args, kwargs))

    writer = GuardedNotionWriter(
        Backend(),
        database_ids={"Material Usage": "usage-uuid", "Sessions": "sessions-uuid"},
    )
    with pytest.raises(UlsError):
        writer.create_entity(
            "usage-uuid",
            {
                "Name": "bad",
                "ID": "MU:bad",
                "Session": {"relation": [{"id": "COMP319-S05"}, {"id": "COMP319-S06"}]},
                "Material": {"relation": [{"id": "COMP319-M03"}]},
                "Role": "Primary",
                "Verified": False,
            },
        )
    with pytest.raises(UlsError):
        writer.write_ai_region("sessions-uuid", "COMP319-S05", {"ownership": "AI"})
    with pytest.raises(UlsError):
        writer.write_ai_region(
            "usage-uuid",
            "MU:bad",
            {"enrichment": {}, "ownership": "AI"},
        )
    assert calls == []


def _approved_phase4():
    reader, writer, drive, resolver = ready_phase4()
    proposal = phase4_proposal(reader)
    upsert_proposal(writer, proposal)
    writer.queue[proposal["Proposal ID"]]["Decision"] = "Approve"
    writer.queue[proposal["Proposal ID"]]["State"] = "APPROVED"
    return reader, writer, drive, resolver, proposal


def _apply(reader, writer, drive, resolver, proposal):
    return HumanApprovalApplier(
        writer,
        decision_by="reviewer@example.edu",
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        **phase4_applier_kwargs(reader, drive, resolver),
    ).apply(proposal)


@pytest.mark.parametrize("kind", ["null", "conflict"])
def test_present_queue_mirror_tamper_fails_before_target_write(kind: str) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4()
    row = writer.queue[proposal["Proposal ID"]]
    if kind == "null":
        row["Course"] = None
    else:
        row["source_hash"] = "different"
    with pytest.raises(PolicyViolation):
        _apply(reader, writer, drive, resolver, proposal)
    assert writer.target_mutations == 0


def test_physical_duplicate_usage_rows_are_superseded_without_target_write() -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4()
    reader.material_usage["COMP319-S05"].append(dict(reader.material_usage["COMP319-S05"][0]))
    result = _apply(reader, writer, drive, resolver, proposal)
    assert result.state is QueueState.SUPERSEDED
    assert writer.target_mutations == 0


def test_unknown_target_write_outcome_reconciles_without_a_second_mutation() -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4()
    writer.mutate_then_raise_target = True
    result = _apply(reader, writer, drive, resolver, proposal)
    assert result.state is QueueState.APPLIED
    assert result.mutated is True
    assert writer.target_mutations == 1
    assert reader.get_material_usage("COMP319-S05")[0]["Verified"] is True


def test_audit_failure_replay_does_not_repeat_target_mutation() -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4()
    writer.fail_audit_once = True
    first = _apply(reader, writer, drive, resolver, proposal)
    assert first.state is QueueState.APPROVED
    assert writer.target_mutations == 1
    second = _apply(reader, writer, drive, resolver, proposal)
    assert second.state is QueueState.APPLIED
    assert writer.target_mutations == 1


def test_page_range_update_preserves_false_verified_and_exact_role() -> None:
    reader, writer, drive, resolver = ready_phase4()
    proposal = phase4_proposal(
        reader,
        operation="update_range",
        desired_range=PageRange(2, 2),
        role="Primary",
    )
    upsert_proposal(writer, proposal)
    writer.queue[proposal["Proposal ID"]]["Decision"] = "Approve"
    writer.queue[proposal["Proposal ID"]]["State"] = "APPROVED"
    result = _apply(reader, writer, drive, resolver, proposal)
    assert result.state is QueueState.APPLIED
    usage = reader.get_material_usage("COMP319-S05")[0]
    assert usage["Role"] == "Primary"
    assert usage["Start Page"] == 2
    assert usage["End Page"] == 2
    assert usage["Verified"] is False


def test_producer_rejects_update_when_candidate_role_does_not_match_target() -> None:
    reader, writer, drive, resolver = ready_phase4()

    class Proposer:
        def propose_material_usage(self, **kwargs):
            del kwargs
            return [
                {
                    "operation": "update_range",
                    "material_id": "COMP319-M03",
                    "usage_id": "MU:existing",
                    "role": "Supporting",
                    "start_page": 2,
                    "end_page": 2,
                }
            ]

    result = MaterialUsageProposalProducer(
        reader,
        drive,
        writer,
        Proposer(),
        source_binding_resolver=resolver,
        config=UlsConfig(),
    ).propose("COMP319-S05")
    assert result.proposals == ()
    assert result.skipped
    assert writer.queue == {}
    assert writer.target_mutations == 0


def test_producer_rereads_trusted_inputs_and_writes_nothing_after_source_drift() -> None:
    reader, writer, drive, resolver = ready_phase4()

    class MutatingProposer:
        def propose_material_usage(self, **kwargs):
            del kwargs
            reader.materials["COMP319-M03"]["Normalized Source"] = "changed-after-llm"
            return [
                {
                    "operation": "update_range",
                    "material_id": "COMP319-M03",
                    "usage_id": "MU:existing",
                    "role": "Primary",
                    "start_page": 2,
                    "end_page": 2,
                    "confidence": 0.9,
                }
            ]

    result = MaterialUsageProposalProducer(
        reader,
        drive,
        writer,
        MutatingProposer(),
        source_binding_resolver=resolver,
        config=UlsConfig(),
    ).propose("COMP319-S05")
    assert result.proposals == ()
    assert result.skipped
    assert writer.queue == {}
    assert writer.target_mutations == 0


def test_producer_payload_is_bounded_and_confidence_is_a_select() -> None:
    reader, writer, drive, resolver = ready_phase4()

    class Proposer:
        payload = None

        def propose_material_usage(self, **kwargs):
            self.payload = kwargs
            return [
                {
                    "operation": "update_range",
                    "material_id": "COMP319-M03",
                    "usage_id": "MU:existing",
                    "role": "Primary",
                    "start_page": 2,
                    "end_page": 2,
                    "confidence": 0.9,
                }
            ]

    proposer = Proposer()
    result = MaterialUsageProposalProducer(
        reader,
        drive,
        writer,
        proposer,
        source_binding_resolver=resolver,
        config=UlsConfig(),
        max_chars_per_item=20,
        max_total_chars=30,
    ).propose("COMP319-S05")
    assert len(result.proposals) == 1
    assert proposer.payload is not None
    assert set(proposer.payload["session"]) == {"ID", "Name", "Course Key"}
    material_payload = proposer.payload["materials"][0]["material"]
    assert set(material_payload) == {"ID", "Name", "Course Key"}
    assert all(len(chunk.content) <= 20 for chunk in proposer.payload["session_chunks"])
    assert sum(len(chunk.content) for chunk in proposer.payload["session_chunks"]) <= 30
    queue_row = writer.queue[next(iter(writer.queue))]
    assert queue_row["Confidence"] == "High"
    assert isinstance(queue_row["Proposed Action"], str)


def test_post_issue_provisional_config_revocation_denies_followup() -> None:
    reader, writer, drive, resolver = ready_phase4()
    del writer
    config = UlsConfig()
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        config,
        source_binding_resolver=resolver,
    )
    package = engine.get_session_context("COMP319-S05", include_provisional=True)
    material_item = next(
        item for item in package.sources if item.source_class == "professor_material"
    )
    config.retrieval.allow_provisional_material_usage = False
    with pytest.raises(UlsError):
        engine.get_source_chunk(package.context_id, str(material_item.locator))
