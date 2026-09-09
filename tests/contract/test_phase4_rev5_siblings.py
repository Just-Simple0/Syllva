"""Phase4 regressions for exact sibling identity independent of Verified.

Before the fix, a Usage row with the right Session, Material, Role, and full
range but a malformed ``Verified`` value disappeared during schema parsing.
These tests keep that raw identity visible to producer, approval, and
retrieval duplicate checks while preserving independent ranges.
"""

from __future__ import annotations

import pathlib
import sys
from copy import deepcopy
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from phase4 import phase4_applier_kwargs, phase4_proposal, ready_phase4

from uls.adapters.notion.base import HumanApprovalApplier, QueueState, upsert_proposal
from uls.adapters.notion.guarded import GuardedNotionWriter
from uls.config.schema import UlsConfig
from uls.domain.errors import LocatorNotAllowedError
from uls.domain.page_range import PageRange
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.proposal.material_usage import MaterialUsageProposalProducer
from uls.retrieval.engine import RetrievalEngine
from uls.retrieval.scope import (
    material_usage_identity,
    material_usage_identity_counts,
    material_usage_scopes,
)

_SESSION_ID = "COMP319-S05"
_MATERIAL_ID = "COMP319-M03"
_DEFAULT_RANGE = PageRange(1, 2)
_EXACT_KEY = (_SESSION_ID, _MATERIAL_ID, "Primary", _DEFAULT_RANGE)
_MISSING_VERIFIED = object()


def _malformed_exact_sibling(
    *,
    usage_id: str = "MU:malformed-sibling",
    role: str = "Primary",
    start_page: int | None = 1,
    end_page: int | None = 2,
    verified: Any = "invalid",
) -> dict[str, Any]:
    result = {
        "ID": usage_id,
        "Session": {"relation": [{"id": _SESSION_ID}]},
        "Material": {"relation": [{"id": _MATERIAL_ID}]},
        "Role": role,
        "Start Page": start_page,
        "End Page": end_page,
    }
    if verified is not _MISSING_VERIFIED:
        result["Verified"] = verified
    return result


def _notion_malformed_exact_sibling(
    *, usage_id: str = "MU:notion-malformed-sibling"
) -> dict[str, Any]:
    return {
        "id": "provider-malformed-sibling",
        "properties": {
            "ID": {"title": [{"plain_text": usage_id}]},
            "Session": {"relation": [{"id": _SESSION_ID}]},
            "Material": {"relation": [{"id": _MATERIAL_ID}]},
            "Role": {"select": {"name": "Primary"}},
            "Start Page": {"number": 1.0},
            "End Page": {"number": 2.0},
            "Verified": {"checkbox": "invalid"},
        },
    }


def _producer(fixture: tuple[Any, ...], candidate: dict[str, Any]) -> MaterialUsageProposalProducer:
    reader, writer, drive, resolver = fixture

    class Proposer:
        def propose_material_usage(self, **_kwargs: Any) -> list[dict[str, Any]]:
            return [candidate]

    return MaterialUsageProposalProducer(
        reader,
        drive,
        writer,
        Proposer(),
        source_binding_resolver=resolver,
        config=UlsConfig(),
    )


def _new_supporting_candidate() -> dict[str, Any]:
    return {
        "operation": "create_usage",
        "material_id": _MATERIAL_ID,
        "role": "Supporting",
        "start_page": 2,
        "end_page": 2,
        "review_reason": "human review required",
    }


def _approved(
    *,
    operation: str = "create_usage",
    desired_range: PageRange = _DEFAULT_RANGE,
) -> tuple[Any, Any, Any, Any, str]:
    reader, backend, drive, resolver = ready_phase4()
    proposal = phase4_proposal(
        reader,
        operation=operation,
        desired_range=desired_range,
    )
    upsert_proposal(backend, proposal)
    row = backend.queue[proposal["Proposal ID"]]
    row.update({"Decision": "Approve", "State": "APPROVED"})
    applier = HumanApprovalApplier(
        GuardedNotionWriter(backend),
        decision_by="trusted-reviewer",
        **phase4_applier_kwargs(reader, drive, resolver),
    )
    return reader, backend, drive, applier, proposal["Proposal ID"]


def _engine(reader: Any, drive: Any, resolver: Any) -> RetrievalEngine:
    return RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=resolver,
    )


def test_raw_identity_counts_malformed_verified_in_flat_and_notion_shapes() -> None:
    reader, *_ = ready_phase4()
    valid = reader.material_usage[_SESSION_ID][0]
    canonical = _malformed_exact_sibling()
    flat = {
        "ID": "MU:flat-malformed-sibling",
        "Session": _SESSION_ID,
        "Material ID": _MATERIAL_ID,
        "Role": "Primary",
        "Start Page": 1,
        "End Page": 2,
        "Verified": "invalid",
    }
    notion = _notion_malformed_exact_sibling()

    assert material_usage_identity(valid) == _EXACT_KEY
    assert material_usage_identity(canonical) == _EXACT_KEY
    assert material_usage_identity(flat) == _EXACT_KEY
    assert material_usage_identity(notion) == _EXACT_KEY
    assert material_usage_scopes([canonical, flat, notion]) == []
    assert (
        material_usage_identity_counts([valid, canonical, flat, notion])[_EXACT_KEY]
        == 4
    )


def test_raw_identity_uses_one_full_range_and_equates_omitted_with_null_bounds() -> None:
    omitted = _malformed_exact_sibling(usage_id="MU:omitted-range")
    omitted.pop("Start Page")
    omitted.pop("End Page")
    explicit_null = _malformed_exact_sibling(
        usage_id="MU:null-range",
        start_page=None,
        end_page=None,
    )
    whole_source_key = (_SESSION_ID, _MATERIAL_ID, "Primary", PageRange())

    assert material_usage_identity(omitted) == whole_source_key
    assert material_usage_identity(explicit_null) == whole_source_key
    assert material_usage_identity_counts([omitted, explicit_null])[whole_source_key] == 2


@pytest.mark.parametrize(
    "malformed",
    [
        {"Role": "not-a-role"},
        {"Start Page": 1, "End Page": None},
        {"Session": {"relation": [{"id": _SESSION_ID}, {"id": "COMP319-S06"}]}},
    ],
)
def test_malformed_non_identity_fields_do_not_create_an_exact_sibling(
    malformed: dict[str, Any],
) -> None:
    row = _malformed_exact_sibling()
    row.update(malformed)
    assert material_usage_identity(row) is None
    assert not material_usage_identity_counts([row])


@pytest.mark.parametrize(
    "verified",
    [
        pytest.param("invalid", id="nonboolean-verified"),
        pytest.param(_MISSING_VERIFIED, id="missing-verified"),
    ],
)
def test_producer_rejects_preexisting_malformed_verified_exact_sibling(
    verified: Any,
) -> None:
    fixture = ready_phase4()
    reader, writer, *_ = fixture
    reader.material_usage[_SESSION_ID].append(
        _malformed_exact_sibling(verified=verified)
    )

    result = _producer(
        fixture,
        {
            "operation": "create_usage",
            "material_id": _MATERIAL_ID,
            "role": "Primary",
            "start_page": 1,
            "end_page": 2,
            "review_reason": "human review required",
        },
    ).propose(_SESSION_ID)

    assert result.proposals == ()
    assert result.created_usage_ids == ()
    assert any("duplicate exact Material Usage" in warning for warning in result.warnings)
    assert writer.queue_rows == []
    assert len(reader.material_usage[_SESSION_ID]) == 2


def test_producer_still_reuses_a_normal_unverified_exact_row() -> None:
    fixture = ready_phase4()
    reader, writer, *_ = fixture

    result = _producer(
        fixture,
        {
            "operation": "create_usage",
            "material_id": _MATERIAL_ID,
            "role": "Primary",
            "start_page": 1,
            "end_page": 2,
            "review_reason": "human review required",
        },
    ).propose(_SESSION_ID)

    assert len(result.proposals) == 1
    assert result.proposals[0]["Target Entity ID"] == "MU:existing"
    assert result.created_usage_ids == ()
    assert len(reader.material_usage[_SESSION_ID]) == 1
    assert writer.queue_rows


def test_producer_allows_a_malformed_verified_nonidentical_overlap() -> None:
    fixture = ready_phase4()
    reader, writer, *_ = fixture
    reader.material_usage[_SESSION_ID].append(
        _malformed_exact_sibling(
            usage_id="MU:malformed-overlap",
            start_page=1,
            end_page=1,
        )
    )

    result = _producer(fixture, _new_supporting_candidate()).propose(_SESSION_ID)

    assert len(result.proposals) == 1
    assert len(result.created_usage_ids) == 1
    assert len(reader.material_usage[_SESSION_ID]) == 3
    assert writer.queue_rows


@pytest.mark.parametrize(
    "verified",
    [
        pytest.param("invalid", id="nonboolean-verified"),
        pytest.param(_MISSING_VERIFIED, id="missing-verified"),
    ],
)
def test_producer_rejects_malformed_verified_exact_sibling_added_during_final_read(
    verified: Any,
) -> None:
    fixture = ready_phase4()
    reader, writer, drive, _ = fixture
    original_read = drive.read_derived
    material_reads = 0

    def read_then_insert(source_ref: Any) -> Any:
        nonlocal material_reads
        value = original_read(source_ref)
        if getattr(source_ref, "file_id", None) == "material-m03":
            material_reads += 1
            if material_reads == 2:
                reader.material_usage[_SESSION_ID].append(
                    _malformed_exact_sibling(
                        usage_id="MU:malformed-read-time",
                        role="Supporting",
                        start_page=2,
                        end_page=2,
                        verified=verified,
                    )
                )
        return value

    drive.read_derived = read_then_insert
    result = _producer(fixture, _new_supporting_candidate()).propose(_SESSION_ID)

    assert material_reads == 2
    assert result.proposals == ()
    assert result.created_usage_ids == ()
    assert writer.queue_rows == []
    assert len(reader.material_usage[_SESSION_ID]) == 2


@pytest.mark.parametrize(
    "verified",
    [
        pytest.param("invalid", id="nonboolean-verified"),
        pytest.param(_MISSING_VERIFIED, id="missing-verified"),
    ],
)
def test_producer_reports_postwrite_malformed_verified_exact_sibling_without_queue_write(
    verified: Any,
) -> None:
    fixture = ready_phase4()
    reader, writer, *_ = fixture
    original_create = writer.create_entity

    def create_then_insert(target_db: str, properties: Any, **kwargs: Any) -> Any:
        result = original_create(target_db, properties, **kwargs)
        if target_db == "Material Usage":
            reader.material_usage[_SESSION_ID].append(
                _malformed_exact_sibling(
                    usage_id="MU:malformed-postwrite",
                    role="Supporting",
                    start_page=2,
                    end_page=2,
                    verified=verified,
                )
            )
        return result

    writer.create_entity = create_then_insert
    result = _producer(fixture, _new_supporting_candidate()).propose(_SESSION_ID)

    assert result.proposals == ()
    assert len(result.created_usage_ids) == 1
    assert len(result.retry_pending) == 1
    assert result.retry_pending[0]["unverified_orphan_possible"] is True
    assert writer.queue_rows == []
    assert len(reader.material_usage[_SESSION_ID]) == 3


@pytest.mark.parametrize(
    "verified",
    [
        pytest.param("invalid", id="nonboolean-verified"),
        pytest.param(_MISSING_VERIFIED, id="missing-verified"),
    ],
)
def test_applier_supersedes_preexisting_malformed_verified_exact_sibling(
    verified: Any,
) -> None:
    reader, backend, _drive, applier, proposal_id = _approved()
    reader.material_usage[_SESSION_ID].append(
        _malformed_exact_sibling(verified=verified)
    )

    result = applier.apply(proposal_id)

    assert result.state is QueueState.SUPERSEDED
    assert result.mutated is False
    assert backend.target_mutations == 0
    assert reader.material_usage[_SESSION_ID][0]["Verified"] is False


@pytest.mark.parametrize(
    "verified",
    [
        pytest.param("invalid", id="nonboolean-verified"),
        pytest.param(_MISSING_VERIFIED, id="missing-verified"),
    ],
)
def test_applier_rechecks_malformed_verified_exact_sibling_added_during_reconcile_read(
    verified: Any,
) -> None:
    reader, backend, drive, applier, proposal_id = _approved()
    original_read = drive.read_derived
    material_reads = 0

    def read_then_insert(source_ref: Any) -> Any:
        nonlocal material_reads
        value = original_read(source_ref)
        if getattr(source_ref, "file_id", None) == "material-m03":
            material_reads += 1
            if material_reads == 2:
                    reader.material_usage[_SESSION_ID].append(
                        _malformed_exact_sibling(
                            usage_id="MU:malformed-applier-read-time",
                            verified=verified,
                        )
                )
        return value

    drive.read_derived = read_then_insert
    result = applier.apply(proposal_id)

    assert material_reads >= 2
    assert result.state is QueueState.APPROVED
    assert result.mutated is False
    assert backend.target_mutations == 0
    assert reader.material_usage[_SESSION_ID][0]["Verified"] is False


def test_applier_allows_a_legitimate_nonidentical_overlap() -> None:
    reader, backend, _drive, applier, proposal_id = _approved()
    reader.material_usage[_SESSION_ID].append(
        {
            **deepcopy(reader.material_usage[_SESSION_ID][0]),
            "ID": "MU:independent-overlap",
            "Start Page": 1,
            "End Page": 1,
            "Verified": True,
        }
    )

    result = applier.apply(proposal_id)

    assert result.state is QueueState.APPLIED
    assert result.mutated is True
    assert backend.target_mutations == 1
    assert reader.material_usage[_SESSION_ID][0]["Verified"] is True


@pytest.mark.parametrize(
    "verified",
    [
        pytest.param("invalid", id="nonboolean-verified"),
        pytest.param(_MISSING_VERIFIED, id="missing-verified"),
    ],
)
def test_page_range_applier_rejects_malformed_verified_exact_sibling(
    verified: Any,
) -> None:
    reader, backend, _drive, applier, proposal_id = _approved(
        operation="update_range",
        desired_range=PageRange(2, 2),
    )
    reader.material_usage[_SESSION_ID].append(
        _malformed_exact_sibling(
            usage_id="MU:malformed-page-range-sibling",
            start_page=2,
            end_page=2,
            verified=verified,
        )
    )

    result = applier.apply(proposal_id)

    assert result.state is QueueState.SUPERSEDED
    assert result.mutated is False
    assert backend.target_mutations == 0
    assert reader.material_usage[_SESSION_ID][0]["Start Page"] == 1
    assert reader.material_usage[_SESSION_ID][0]["End Page"] == 2


@pytest.mark.parametrize(
    "verified",
    [
        pytest.param("invalid", id="nonboolean-verified"),
        pytest.param(_MISSING_VERIFIED, id="missing-verified"),
    ],
)
def test_retrieval_excludes_valid_twin_when_malformed_verified_exact_sibling_exists(
    verified: Any,
) -> None:
    reader, _writer, drive, resolver = ready_phase4()
    reader.material_usage[_SESSION_ID][0]["Verified"] = True
    reader.material_usage[_SESSION_ID].append(
        _malformed_exact_sibling(verified=verified)
    )
    engine = _engine(reader, drive, resolver)

    package = engine.get_session_context(_SESSION_ID)
    bindings = engine.capabilities.bindings_for(package.context_id) or ()

    assert all(item.entity_id != _MATERIAL_ID for item in package.sources)
    assert all(binding.material_id != _MATERIAL_ID for binding in bindings)


@pytest.mark.parametrize(
    "verified",
    [
        pytest.param("invalid", id="nonboolean-verified"),
        pytest.param(_MISSING_VERIFIED, id="missing-verified"),
    ],
)
def test_retrieval_rechecks_malformed_verified_exact_sibling_added_during_body_read(
    verified: Any,
) -> None:
    reader, _writer, drive, resolver = ready_phase4()
    reader.material_usage[_SESSION_ID][0]["Verified"] = True
    original_read = drive.read_derived
    inserted = False

    def read_then_insert(source_ref: Any) -> Any:
        nonlocal inserted
        value = original_read(source_ref)
        if not inserted and getattr(source_ref, "file_id", None) == "material-m03":
            inserted = True
            reader.material_usage[_SESSION_ID].append(
                _malformed_exact_sibling(
                    usage_id="MU:malformed-retrieval-read-time",
                    verified=verified,
                )
            )
        return value

    drive.read_derived = read_then_insert
    package = _engine(reader, drive, resolver).get_session_context(_SESSION_ID)

    assert inserted
    assert all(item.entity_id != _MATERIAL_ID for item in package.sources)


def test_retrieval_keeps_normal_unverified_scope_when_explicitly_included() -> None:
    reader, _writer, drive, resolver = ready_phase4()
    engine = _engine(reader, drive, resolver)

    package = engine.get_session_context(_SESSION_ID, include_provisional=True)
    bindings = engine.capabilities.bindings_for(package.context_id) or ()

    assert any(item.entity_id == _MATERIAL_ID for item in package.sources)
    assert any(
        binding.material_id == _MATERIAL_ID
        and binding.usage_id == "MU:existing"
        and binding.provisional
        for binding in bindings
    )


@pytest.mark.parametrize(
    "verified",
    [
        pytest.param("invalid", id="nonboolean-verified"),
        pytest.param(_MISSING_VERIFIED, id="missing-verified"),
    ],
)
def test_retrieval_followup_rejects_malformed_verified_exact_sibling(
    verified: Any,
) -> None:
    reader, _writer, drive, resolver = ready_phase4()
    reader.material_usage[_SESSION_ID][0]["Verified"] = True
    engine = _engine(reader, drive, resolver)
    package = engine.get_session_context(_SESSION_ID)
    material_item = next(
        item for item in package.sources if item.entity_id == _MATERIAL_ID
    )
    reader.material_usage[_SESSION_ID].append(
        _malformed_exact_sibling(
            usage_id="MU:malformed-followup-sibling",
            verified=verified,
        )
    )

    with pytest.raises(LocatorNotAllowedError):
        engine.get_source_chunk(package.context_id, str(material_item.locator))


def test_retrieval_keeps_valid_basis_when_malformed_verified_overlap_is_nonidentical() -> None:
    reader, _writer, drive, resolver = ready_phase4()
    reader.material_usage[_SESSION_ID][0]["Verified"] = True
    reader.material_usage[_SESSION_ID].append(
        _malformed_exact_sibling(
            usage_id="MU:malformed-nonidentical",
            start_page=1,
            end_page=1,
        )
    )
    engine = _engine(reader, drive, resolver)

    package = engine.get_session_context(_SESSION_ID)

    assert any(item.entity_id == _MATERIAL_ID for item in package.sources)
