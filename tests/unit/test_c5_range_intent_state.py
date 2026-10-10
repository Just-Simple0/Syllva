from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, NotRequired, TypedDict, Unpack

import pytest

from uls.domain.approval_identity import (
    build_material_usage_semantics,
    build_usage_proposal_envelope,
    canonical_action_json,
    canonical_usage_proposal_envelope_json,
    derive_proposal_id_for_create,
    derive_proposal_id_for_read,
    derive_proposal_id_v2,
    derive_usage_slot_key,
    parse_usage_proposal_envelope,
)
from uls.domain.errors import ProposalConflictError
from uls.domain.page_range import PageRange
from uls.state.models import ProviderWriteAttempt, RangeIntentHead
from uls.state.sqlite import SQLiteStateStore

pytestmark = pytest.mark.unit

_REQUEST_ID = "550e8400-e29b-41d4-a716-446655440000"
_REQUEST_ID_2 = "660e8400-e29b-41d4-a716-446655440001"


@pytest.fixture
def state(tmp_path: Path) -> SQLiteStateStore:
    return SQLiteStateStore(tmp_path / "state.db")


def _slot() -> str:
    return derive_usage_slot_key("COMP319-S05", "COMP319-M03", "Primary")


class _ClaimArgs(TypedDict):
    usage_slot_key: str
    session_app_id: str
    material_app_id: str
    usage_role: str
    request_id: str
    receipt_id: str
    receipt_hash: str
    operation: str
    target_entity_id: str
    adopted_usage_app_id: str | None
    slot_occupant_count: int
    adopted_usage_provider: NotRequired[str]
    adopted_usage_provider_row_id: NotRequired[str]


class _OutboxArgs(TypedDict):
    proposal_id: str
    usage_slot_key: str
    request_id: str
    intent_generation: int
    action_json: str
    envelope_json: str


def _claim(
    state: SQLiteStateStore, *, proposal_id: str, **kwargs: Unpack[_ClaimArgs]
) -> RangeIntentHead:
    """Exercise the reviewed two-phase claim/bind protocol in test setup."""

    head = state.claim_range_intent_generation(**kwargs)
    if proposal_id == "dispatch":
        envelope = build_usage_proposal_envelope(head.usage_slot_key, head.current_request_id, head.intent_generation)
        proposal_id = derive_proposal_id_for_create("MATERIAL_USAGE", envelope, _dispatch_action(head))
    return state.bind_range_intent_proposal(
        usage_slot_key=head.usage_slot_key,
        generation=head.intent_generation,
        proposal_id=proposal_id,
    )


def _dispatch_action(head: RangeIntentHead) -> dict[str, Any]:
    assert head.current_target_entity_id is not None
    dependency = {"source_ref": {"provider": "google_drive", "file_id": "source"},
                  "source_hash": "hash", "source_version": 1}
    return dict(build_material_usage_semantics(
        operation=head.current_operation, target_entity_id=head.current_target_entity_id,
        session_id=head.session_app_id, material_id=head.material_app_id,
        course_relation_page_id="course-page", course_key="2026-1_COMP319-002",
        usage_role=head.usage_role, material_type="Lecture Slides", source_class="professor_material",
        old_snapshot={"usage_id": head.current_target_entity_id, "session_id": head.session_app_id,
                      "material_id": head.material_app_id, "role": head.usage_role,
                      "start_page": 1, "end_page": 2, "verified": False},
        desired_range=PageRange(1, 2), session_dependency=dependency, material_dependency=dependency,
        evidence=None, review_reason="human review", processor_version="1.2.0",
    ))


def _reserve(
    state: SQLiteStateStore, *, usage_slot_key: str, generation: int, target_id: str,
    pre_dispatch_snapshot_json: str, provider: str,
) -> tuple[RangeIntentHead, ProviderWriteAttempt] | None:
    head = state.get_range_intent_head(usage_slot_key)
    assert head is not None and head.current_proposal_id is not None
    if state.get_usage_proposal_outbox(head.current_proposal_id) is None:
        state.record_usage_proposal_outbox(
            proposal_id=head.current_proposal_id, usage_slot_key=usage_slot_key,
            request_id=head.current_request_id, intent_generation=head.intent_generation,
            action_json=canonical_action_json(_dispatch_action(head)),
            envelope_json=canonical_usage_proposal_envelope_json(build_usage_proposal_envelope(
                usage_slot_key, head.current_request_id, head.intent_generation,
            )),
        )
        state.mark_usage_proposal_published(proposal_id=head.current_proposal_id, queue_page_id="queue-page")
    return state.reserve_usage_dispatch(
        usage_slot_key=usage_slot_key, generation=generation, target_id=target_id,
        pre_dispatch_snapshot_json=pre_dispatch_snapshot_json, provider=provider,
    )


# -- approval_identity: envelope tri-state parsing --------------------------


def test_derive_usage_slot_key_is_range_agnostic() -> None:
    a = derive_usage_slot_key("COMP319-S05", "COMP319-M03", "Primary")
    b = derive_usage_slot_key("COMP319-S05", "COMP319-M03", "Primary")
    assert a == b
    assert re.fullmatch(r"[0-9a-f]{64}", a)
    assert derive_usage_slot_key("COMP319-S05", "COMP319-M03", "Supporting") != a


def test_envelope_roundtrip_matches_canonical_form() -> None:
    envelope = build_usage_proposal_envelope(_slot(), _REQUEST_ID, 1)
    envelope_json = canonical_usage_proposal_envelope_json(envelope)
    parsed = parse_usage_proposal_envelope({"Proposal Envelope": envelope_json})
    assert parsed == envelope


def test_envelope_absent_is_none() -> None:
    assert parse_usage_proposal_envelope({}) is None


def test_envelope_present_but_malformed_json_raises() -> None:
    with pytest.raises(ValueError):
        parse_usage_proposal_envelope({"Proposal Envelope": "{not json"})


def test_envelope_present_with_unknown_key_raises() -> None:
    envelope = dict(build_usage_proposal_envelope(_slot(), _REQUEST_ID, 1))
    envelope["extra"] = 1
    with pytest.raises(ValueError):
        parse_usage_proposal_envelope({"Proposal Envelope": json.dumps(envelope)})


def test_envelope_generation_below_one_raises() -> None:
    with pytest.raises(ValueError):
        build_usage_proposal_envelope(_slot(), _REQUEST_ID, 0)


# -- approval_identity: v2 proposal ID dispatch ------------------------------


def test_derive_proposal_id_v2_is_64_lowercase_hex() -> None:
    proposal_id = derive_proposal_id_v2(
        "MATERIAL_USAGE", {"a": 1}, [_slot(), _REQUEST_ID, 1]
    )
    assert re.fullmatch(r"[0-9a-f]{64}", proposal_id)


def test_derive_proposal_id_v2_changes_with_generation() -> None:
    semantics = {"a": 1}
    id_gen1 = derive_proposal_id_v2("MATERIAL_USAGE", semantics, [_slot(), _REQUEST_ID, 1])
    id_gen2 = derive_proposal_id_v2("MATERIAL_USAGE", semantics, [_slot(), _REQUEST_ID, 2])
    assert id_gen1 != id_gen2


def test_create_requires_envelope_for_material_usage() -> None:
    with pytest.raises(ValueError):
        derive_proposal_id_for_create("MATERIAL_USAGE", None, {"a": 1})


def test_create_rejects_envelope_for_exam_scope() -> None:
    envelope = build_usage_proposal_envelope(_slot(), _REQUEST_ID, 1)
    with pytest.raises(ValueError):
        derive_proposal_id_for_create("EXAM_SCOPE", envelope, {"a": 1})


def test_read_tolerates_missing_envelope_as_legacy_v1() -> None:
    proposal_id = derive_proposal_id_for_read("MATERIAL_USAGE", {}, {"a": 1})
    assert ":" in proposal_id  # v1 shape retains the type-prefixed form


def test_read_and_create_agree_when_envelope_present() -> None:
    envelope = build_usage_proposal_envelope(_slot(), _REQUEST_ID, 1)
    semantics = {"a": 1}
    created = derive_proposal_id_for_create("MATERIAL_USAGE", envelope, semantics)
    record = {"Proposal Envelope": canonical_usage_proposal_envelope_json(envelope)}
    read = derive_proposal_id_for_read("MATERIAL_USAGE", record, semantics)
    assert created == read


# -- range_intent_heads: generation claim + permanent/transient split -------


def test_claim_generation_is_idempotent_for_the_same_receipt(state: SQLiteStateStore) -> None:
    kwargs: _ClaimArgs = {
        "usage_slot_key": _slot(),
        "session_app_id": "COMP319-S05",
        "material_app_id": "COMP319-M03",
        "usage_role": "Primary",
        "request_id": _REQUEST_ID,
        "receipt_id": "rec-1",
        "receipt_hash": "hash-1",
        "operation": "create_usage",
        "target_entity_id": "MU:abc",
        "adopted_usage_app_id": None,
        "slot_occupant_count": 0,
    }
    first = _claim(state, proposal_id="prop-1", **kwargs)
    second = _claim(state, proposal_id="prop-1", **kwargs)
    assert first.intent_generation == second.intent_generation == 1


def test_claim_generation_bumps_on_a_new_receipt(state: SQLiteStateStore) -> None:
    slot = _slot()
    _claim(state, proposal_id="prop-1",
        usage_slot_key=slot, session_app_id="COMP319-S05", material_app_id="COMP319-M03",
        usage_role="Primary", request_id=_REQUEST_ID, receipt_id="rec-1",
        receipt_hash="hash-1", operation="create_usage", target_entity_id="MU:abc",
        adopted_usage_app_id="MU:abc", slot_occupant_count=1,
        adopted_usage_provider="notion", adopted_usage_provider_row_id="1" * 32,
    )
    second = _claim(state, proposal_id="prop-2",
        usage_slot_key=slot, session_app_id="COMP319-S05", material_app_id="COMP319-M03",
        usage_role="Primary", request_id=_REQUEST_ID_2, receipt_id="rec-2",
        receipt_hash="hash-2", operation="update_range", target_entity_id="MU:abc",
        adopted_usage_app_id="MU:abc", slot_occupant_count=1,
        adopted_usage_provider="notion", adopted_usage_provider_row_id="1" * 32,
    )
    assert second.intent_generation == 2
    assert second.current_usage_app_id == "MU:abc"


def test_a_bound_slot_permanently_blocks_a_different_create_usage_target(
    state: SQLiteStateStore,
) -> None:
    slot = _slot()
    _claim(state, proposal_id="prop-1",
        usage_slot_key=slot, session_app_id="COMP319-S05", material_app_id="COMP319-M03",
        usage_role="Primary", request_id=_REQUEST_ID, receipt_id="rec-1",
        receipt_hash="hash-1", operation="create_usage", target_entity_id="MU:abc",
        adopted_usage_app_id="MU:abc", slot_occupant_count=1,
        adopted_usage_provider="notion", adopted_usage_provider_row_id="1" * 32,
    )
    with pytest.raises(ValueError):
        _claim(state, proposal_id="prop-2",
            usage_slot_key=slot, session_app_id="COMP319-S05", material_app_id="COMP319-M03",
            usage_role="Primary", request_id=_REQUEST_ID_2, receipt_id="rec-2",
            receipt_hash="hash-2", operation="create_usage", target_entity_id="MU:other",
            adopted_usage_app_id=None, slot_occupant_count=0,
        )


def test_slot_occupant_count_above_one_is_rejected(state: SQLiteStateStore) -> None:
    with pytest.raises(ValueError):
        _claim(state, proposal_id="prop-1",
            usage_slot_key=_slot(), session_app_id="COMP319-S05", material_app_id="COMP319-M03",
            usage_role="Primary", request_id=_REQUEST_ID, receipt_id="rec-1",
            receipt_hash="hash-1", operation="create_usage", target_entity_id="MU:abc",
            adopted_usage_app_id=None, slot_occupant_count=2,
        )


# -- range_intent_heads: RESERVED -> DISPATCHED dispatch-ownership CAS ------


def test_only_one_concurrent_caller_wins_the_dispatch_cas(state: SQLiteStateStore) -> None:
    slot = _slot()
    _claim(state, proposal_id="dispatch",
        usage_slot_key=slot, session_app_id="COMP319-S05", material_app_id="COMP319-M03",
        usage_role="Primary", request_id=_REQUEST_ID, receipt_id="rec-1",
        receipt_hash="hash-1", operation="create_usage", target_entity_id="MU:abc",
        adopted_usage_app_id=None, slot_occupant_count=0,
    )
    winner = _reserve(state,
        usage_slot_key=slot, generation=1, target_id="MU:abc",
        pre_dispatch_snapshot_json="[]", provider="notion",
    )
    loser = _reserve(state,
        usage_slot_key=slot, generation=1, target_id="MU:abc",
        pre_dispatch_snapshot_json="[]", provider="notion",
    )
    assert winner is not None
    assert winner[0].reservation_state == "DISPATCHED"
    assert loser is None


def test_legacy_release_without_owner_proof_stays_blocked(state: SQLiteStateStore) -> None:
    slot = _slot()
    _claim(state, proposal_id="dispatch",
        usage_slot_key=slot, session_app_id="COMP319-S05", material_app_id="COMP319-M03",
        usage_role="Primary", request_id=_REQUEST_ID, receipt_id="rec-1",
        receipt_hash="hash-1", operation="create_usage", target_entity_id="MU:abc",
        adopted_usage_app_id=None, slot_occupant_count=0,
    )
    _reserve(state,
        usage_slot_key=slot, generation=1, target_id="MU:abc",
        pre_dispatch_snapshot_json="[]", provider="notion",
    )
    with pytest.raises(ValueError, match="structured evidence"):
        state.release_usage_reservation(
            usage_slot_key=slot, generation=1, target_id="MU:abc", outcome="released",
        )
    current = state.get_range_intent_head(slot)
    assert current is not None and current.reservation_state == "DISPATCHED"


def test_reconcile_outcome_blocks_further_claims_until_resolved(
    state: SQLiteStateStore,
) -> None:
    slot = _slot()
    _claim(state, proposal_id="dispatch",
        usage_slot_key=slot, session_app_id="COMP319-S05", material_app_id="COMP319-M03",
        usage_role="Primary", request_id=_REQUEST_ID, receipt_id="rec-1",
        receipt_hash="hash-1", operation="create_usage", target_entity_id="MU:abc",
        adopted_usage_app_id=None, slot_occupant_count=0,
    )
    _reserve(state,
        usage_slot_key=slot, generation=1, target_id="MU:abc",
        pre_dispatch_snapshot_json="[]", provider="notion",
    )
    reconciling = state.release_usage_reservation(
        usage_slot_key=slot, generation=1, target_id="MU:abc", outcome="reconcile",
    )
    assert reconciling.reservation_state == "RECONCILE_REQUIRED"
    with pytest.raises(ValueError):
        _claim(state, proposal_id="prop-2",
            usage_slot_key=slot, session_app_id="COMP319-S05", material_app_id="COMP319-M03",
            usage_role="Primary", request_id=_REQUEST_ID_2, receipt_id="rec-2",
            receipt_hash="hash-2", operation="create_usage", target_entity_id="MU:other",
            adopted_usage_app_id=None, slot_occupant_count=0,
        )


def test_resolve_reconciliation_requires_a_durable_attempt_readback(
    state: SQLiteStateStore,
) -> None:
    slot = _slot()
    _claim(state, proposal_id="dispatch",
        usage_slot_key=slot, session_app_id="COMP319-S05", material_app_id="COMP319-M03",
        usage_role="Primary", request_id=_REQUEST_ID, receipt_id="rec-1",
        receipt_hash="hash-1", operation="create_usage", target_entity_id="MU:abc",
        adopted_usage_app_id=None, slot_occupant_count=0,
    )
    _reserve(state,
        usage_slot_key=slot, generation=1, target_id="MU:abc",
        pre_dispatch_snapshot_json="[]", provider="notion",
    )
    state.release_usage_reservation(
        usage_slot_key=slot, generation=1, target_id="MU:abc", outcome="reconcile",
    )
    with pytest.raises(ValueError):
        state.resolve_range_intent_reconciliation(
            usage_slot_key=slot, generation=1, target_id="MU:abc",
            bound_usage_app_id="MU:abc",
        )


def test_repeated_claimed_binding_without_proof_never_unlocks(state: SQLiteStateStore) -> None:
    slot = _slot()
    _claim(state, proposal_id="dispatch",
        usage_slot_key=slot, session_app_id="COMP319-S05", material_app_id="COMP319-M03",
        usage_role="Primary", request_id=_REQUEST_ID, receipt_id="rec-1",
        receipt_hash="hash-1", operation="create_usage", target_entity_id="MU:abc",
        adopted_usage_app_id=None, slot_occupant_count=0,
    )
    _reserve(state,
        usage_slot_key=slot, generation=1, target_id="MU:abc",
        pre_dispatch_snapshot_json="[]", provider="notion",
    )
    for _ in range(2):
        with pytest.raises(ValueError, match="structured evidence"):
            state.release_usage_reservation(
                usage_slot_key=slot, generation=1, target_id="MU:abc",
                outcome="bound", bound_usage_app_id="MU:abc",
            )
    current = state.get_range_intent_head(slot)
    assert current is not None and current.current_usage_app_id is None


# -- usage_proposal_outbox: byte-identity conflict detection ----------------


def test_outbox_retry_with_identical_bytes_is_idempotent(state: SQLiteStateStore) -> None:
    slot = _slot()
    _claim(state, proposal_id="prop-1",
        usage_slot_key=slot, session_app_id="COMP319-S05", material_app_id="COMP319-M03",
        usage_role="Primary", request_id=_REQUEST_ID, receipt_id="rec-1",
        receipt_hash="hash-1", operation="create_usage", target_entity_id="MU:abc",
        adopted_usage_app_id=None, slot_occupant_count=0,
    )
    kwargs: _OutboxArgs = {
        "proposal_id": "prop-1", "usage_slot_key": slot, "request_id": _REQUEST_ID,
        "intent_generation": 1, "action_json": '{"a":1}', "envelope_json": '{"e":1}',
    }
    first = state.record_usage_proposal_outbox(**kwargs)
    second = state.record_usage_proposal_outbox(**kwargs)
    assert first.proposal_id == second.proposal_id


def test_outbox_conflicting_bytes_for_the_same_generation_raise(
    state: SQLiteStateStore,
) -> None:
    slot = _slot()
    _claim(state, proposal_id="prop-1",
        usage_slot_key=slot, session_app_id="COMP319-S05", material_app_id="COMP319-M03",
        usage_role="Primary", request_id=_REQUEST_ID, receipt_id="rec-1",
        receipt_hash="hash-1", operation="create_usage", target_entity_id="MU:abc",
        adopted_usage_app_id=None, slot_occupant_count=0,
    )
    state.record_usage_proposal_outbox(
        proposal_id="prop-1", usage_slot_key=slot, request_id=_REQUEST_ID,
        intent_generation=1, action_json='{"a":1}', envelope_json='{"e":1}',
    )
    with pytest.raises(ProposalConflictError):
        state.record_usage_proposal_outbox(
            proposal_id="prop-1-changed", usage_slot_key=slot, request_id=_REQUEST_ID,
            intent_generation=1, action_json='{"a":2}', envelope_json='{"e":1}',
        )


def test_mark_published_updates_publish_state(state: SQLiteStateStore) -> None:
    slot = _slot()
    _claim(state, proposal_id="prop-1",
        usage_slot_key=slot, session_app_id="COMP319-S05", material_app_id="COMP319-M03",
        usage_role="Primary", request_id=_REQUEST_ID, receipt_id="rec-1",
        receipt_hash="hash-1", operation="create_usage", target_entity_id="MU:abc",
        adopted_usage_app_id=None, slot_occupant_count=0,
    )
    state.record_usage_proposal_outbox(
        proposal_id="prop-1", usage_slot_key=slot, request_id=_REQUEST_ID,
        intent_generation=1, action_json='{"a":1}', envelope_json='{"e":1}',
    )
    published = state.mark_usage_proposal_published(
        proposal_id="prop-1", queue_page_id="page-123"
    )
    assert published.publish_state == "PUBLISHED"
    assert published.queue_page_id == "page-123"


# -- Final-review fixes: R1 (envelope tri-state), R2 (create-helper validation),
# -- R3 (dispatch CAS target binding), R4 (reconciliation/outbox identity binding)


def test_envelope_present_but_null_is_invalid_not_absent() -> None:
    with pytest.raises(ValueError):
        parse_usage_proposal_envelope({"Proposal Envelope": None})


def test_envelope_duplicate_json_key_is_rejected() -> None:
    slot = _slot()
    raw = (
        '{"schema":"uls.usage-proposal.v2","usage_slot_key":"' + slot + '",'
        '"request_id":"' + _REQUEST_ID + '","request_id":"' + _REQUEST_ID_2 + '",'
        '"intent_generation":1}'
    )
    with pytest.raises(ValueError):
        parse_usage_proposal_envelope({"Proposal Envelope": raw})


def test_create_dispatch_rejects_a_malformed_envelope_shape() -> None:
    with pytest.raises(ValueError):
        derive_proposal_id_for_create(
            "MATERIAL_USAGE", {"usage_slot_key": "not-hex64"}, {"a": 1}
        )


@pytest.mark.parametrize("defect", ["wrong_schema", "missing_schema", "extra_key"])
def test_create_dispatch_rejects_every_non_exact_envelope_shape(defect: str) -> None:
    envelope = dict(build_usage_proposal_envelope(_slot(), _REQUEST_ID, 1))
    if defect == "wrong_schema":
        envelope["schema"] = "wrong.schema"
    elif defect == "missing_schema":
        del envelope["schema"]
    else:
        envelope["extra"] = "rejected"
    with pytest.raises(ValueError):
        derive_proposal_id_for_create("MATERIAL_USAGE", envelope, {"a": 1})


def test_create_dispatch_rejects_unsupported_proposal_type() -> None:
    envelope = build_usage_proposal_envelope(_slot(), _REQUEST_ID, 1)
    with pytest.raises(ValueError):
        derive_proposal_id_for_create("SOME_OTHER_TYPE", envelope, {"a": 1})


def test_reserve_dispatch_rejects_a_target_id_that_does_not_match_the_claim(
    state: SQLiteStateStore,
) -> None:
    slot = _slot()
    _claim(state, proposal_id="dispatch",
        usage_slot_key=slot, session_app_id="COMP319-S05", material_app_id="COMP319-M03",
        usage_role="Primary", request_id=_REQUEST_ID, receipt_id="rec-1",
        receipt_hash="hash-1", operation="create_usage", target_entity_id="MU:abc",
        adopted_usage_app_id=None, slot_occupant_count=0,
    )
    # A caller cannot substitute an arbitrary target_id to win dispatch
    # ownership -- the CAS is bound to the claim's own current_target_entity_id.
    result = _reserve(state,
        usage_slot_key=slot, generation=1, target_id="MU:different",
        pre_dispatch_snapshot_json="[]", provider="notion",
    )
    assert result is None


def test_dispatch_requires_phase_two_proposal_binding(state: SQLiteStateStore) -> None:
    slot = _slot()
    head = state.claim_range_intent_generation(
        usage_slot_key=slot, session_app_id="COMP319-S05", material_app_id="COMP319-M03",
        usage_role="Primary", request_id=_REQUEST_ID, receipt_id="rec-1",
        receipt_hash="hash-1", operation="create_usage", target_entity_id="MU:abc",
        adopted_usage_app_id=None, slot_occupant_count=0,
    )
    assert head.current_proposal_id is None
    assert state.reserve_usage_dispatch(
        usage_slot_key=slot, generation=head.intent_generation, target_id="MU:abc",
        pre_dispatch_snapshot_json="[]", provider="notion",
    ) is None
    current = state.get_range_intent_head(slot)
    assert current is not None
    assert current.reservation_state == "NONE"


def test_claim_bind_race_only_current_generation_can_bind(tmp_path: Path) -> None:
    db_path = tmp_path / "claim-bind-race.db"
    left = SQLiteStateStore(db_path)
    right = SQLiteStateStore(db_path)
    barrier = threading.Barrier(2)

    def claim(store: SQLiteStateStore, receipt_id: str, request_id: str) -> int:
        barrier.wait()
        return store.claim_range_intent_generation(
            usage_slot_key=_slot(), session_app_id="COMP319-S05", material_app_id="COMP319-M03",
            usage_role="Primary", request_id=request_id, receipt_id=receipt_id,
            receipt_hash=f"hash-{receipt_id}", operation="create_usage",
            target_entity_id=f"MU:{receipt_id}", adopted_usage_app_id=None,
            slot_occupant_count=0,
        ).intent_generation

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            generations = list(pool.map(
                lambda args: claim(*args),
                [(left, "rec-1", _REQUEST_ID), (right, "rec-2", _REQUEST_ID_2)],
            ))
        assert sorted(generations) == [1, 2]

        bind_barrier = threading.Barrier(2)

        def bind(
            store: SQLiteStateStore, generation: int, proposal_id: str
        ) -> tuple[str, str | None]:
            bind_barrier.wait()
            try:
                return ("ok", store.bind_range_intent_proposal(
                    usage_slot_key=_slot(), generation=generation,
                    proposal_id=proposal_id,
                ).current_proposal_id)
            except ValueError as exc:
                return ("stale", str(exc))

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(
                lambda args: bind(*args),
                [(left, generations[0], f"proposal-g{generations[0]}"),
                 (right, generations[1], f"proposal-g{generations[1]}")],
            ))
        assert sorted(outcome[0] for outcome in outcomes) == ["ok", "stale"]
        current = left.get_range_intent_head(_slot())
        assert current is not None
        assert current.intent_generation == 2
        assert current.current_proposal_id == "proposal-g2"
    finally:
        left.close()
        right.close()


def test_reconciliation_rejects_a_mismatched_generation(state: SQLiteStateStore) -> None:
    slot = _slot()
    _claim(state, proposal_id="dispatch",
        usage_slot_key=slot, session_app_id="COMP319-S05", material_app_id="COMP319-M03",
        usage_role="Primary", request_id=_REQUEST_ID, receipt_id="rec-1",
        receipt_hash="hash-1", operation="create_usage", target_entity_id="MU:abc",
        adopted_usage_app_id=None, slot_occupant_count=0,
    )
    _reserve(state,
        usage_slot_key=slot, generation=1, target_id="MU:abc",
        pre_dispatch_snapshot_json="[]", provider="notion",
    )
    state.release_usage_reservation(
        usage_slot_key=slot, generation=1, target_id="MU:abc", outcome="reconcile",
    )
    # Presenting an unrelated attempt's proof (wrong generation) must not
    # resolve this slot's reconciliation.
    with pytest.raises(ValueError):
        state.resolve_range_intent_reconciliation(
            usage_slot_key=slot, generation=2, target_id="MU:abc",
            bound_usage_app_id="MU:abc",
        )


def test_outbox_conflict_also_detects_a_mismatched_request_id(
    state: SQLiteStateStore,
) -> None:
    slot = _slot()
    _claim(state, proposal_id="prop-1",
        usage_slot_key=slot, session_app_id="COMP319-S05", material_app_id="COMP319-M03",
        usage_role="Primary", request_id=_REQUEST_ID, receipt_id="rec-1",
        receipt_hash="hash-1", operation="create_usage", target_entity_id="MU:abc",
        adopted_usage_app_id=None, slot_occupant_count=0,
    )
    state.record_usage_proposal_outbox(
        proposal_id="prop-1", usage_slot_key=slot, request_id=_REQUEST_ID,
        intent_generation=1, action_json='{"a":1}', envelope_json='{"e":1}',
    )
    with pytest.raises(ProposalConflictError):
        state.record_usage_proposal_outbox(
            proposal_id="prop-1", usage_slot_key=slot, request_id=_REQUEST_ID_2,
            intent_generation=1, action_json='{"a":1}', envelope_json='{"e":1}',
        )


def test_mark_published_cannot_repoint_an_already_published_entry(
    state: SQLiteStateStore,
) -> None:
    slot = _slot()
    _claim(state, proposal_id="prop-1",
        usage_slot_key=slot, session_app_id="COMP319-S05", material_app_id="COMP319-M03",
        usage_role="Primary", request_id=_REQUEST_ID, receipt_id="rec-1",
        receipt_hash="hash-1", operation="create_usage", target_entity_id="MU:abc",
        adopted_usage_app_id=None, slot_occupant_count=0,
    )
    state.record_usage_proposal_outbox(
        proposal_id="prop-1", usage_slot_key=slot, request_id=_REQUEST_ID,
        intent_generation=1, action_json='{"a":1}', envelope_json='{"e":1}',
    )
    state.mark_usage_proposal_published(proposal_id="prop-1", queue_page_id="page-A")
    # Idempotent for the SAME page.
    same = state.mark_usage_proposal_published(proposal_id="prop-1", queue_page_id="page-A")
    assert same.queue_page_id == "page-A"
    # Rejects repointing to a DIFFERENT page.
    with pytest.raises(ValueError):
        state.mark_usage_proposal_published(proposal_id="prop-1", queue_page_id="page-B")


def test_bound_but_unpublished_head_cannot_dispatch(state: SQLiteStateStore) -> None:
    head = _claim(
        state, proposal_id="prop-1", usage_slot_key=_slot(), session_app_id="COMP319-S05",
        material_app_id="COMP319-M03", usage_role="Primary", request_id=_REQUEST_ID,
        receipt_id="rec-1", receipt_hash="hash-1", operation="create_usage",
        target_entity_id="MU:abc", adopted_usage_app_id=None, slot_occupant_count=0,
    )
    assert state.reserve_usage_dispatch(
        usage_slot_key=head.usage_slot_key, generation=1, target_id="MU:abc",
        pre_dispatch_snapshot_json="[]", provider="notion",
    ) is None


def test_independent_dispatch_connections_have_one_winner(tmp_path: Path) -> None:
    left = SQLiteStateStore(tmp_path / "dispatch.db")
    right = SQLiteStateStore(tmp_path / "dispatch.db")
    _claim(left, proposal_id="dispatch", usage_slot_key=_slot(), session_app_id="COMP319-S05",
           material_app_id="COMP319-M03", usage_role="Primary", request_id=_REQUEST_ID,
           receipt_id="rec-1", receipt_hash="hash-1", operation="create_usage",
           target_entity_id="MU:abc", adopted_usage_app_id=None, slot_occupant_count=0)
    barrier = threading.Barrier(2)

    def reserve(store: SQLiteStateStore) -> bool:
        barrier.wait(timeout=5)
        return _reserve(store, usage_slot_key=_slot(), generation=1, target_id="MU:abc",
                        pre_dispatch_snapshot_json="[]", provider="notion") is not None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, [left, right]))
    assert sorted(results) == [False, True]
    left.close()
    right.close()


def test_reused_receipt_conflicts_on_adopted_app_identity(state: SQLiteStateStore) -> None:
    args: _ClaimArgs = {
        "usage_slot_key": _slot(), "session_app_id": "COMP319-S05", "material_app_id": "COMP319-M03",
        "usage_role": "Primary", "request_id": _REQUEST_ID, "receipt_id": "rec-1",
        "receipt_hash": "hash-1", "operation": "create_usage", "target_entity_id": "MU:A",
        "adopted_usage_app_id": "MU:A", "slot_occupant_count": 1,
        "adopted_usage_provider": "notion", "adopted_usage_provider_row_id": "1" * 32,
    }
    state.claim_range_intent_generation(**args)
    args["adopted_usage_app_id"] = "MU:B"
    with pytest.raises(ValueError, match="conflicts"):
        state.claim_range_intent_generation(**args)
    head = state.get_range_intent_head(_slot())
    assert head is not None and head.current_usage_app_id == "MU:A" and head.intent_generation == 1
