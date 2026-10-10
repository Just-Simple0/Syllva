"""Deterministic human range requests and explicit immutable intake evidence.

The parent coordinator owns polling/ordering. This module never treats a cached
poll barrier as authority to perform a later provider mutation.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from uls.domain.approval_identity import UsageSlotIdentity, canonical_notion_page_id
from uls.domain.page_range import PageRange
from uls.intake.coordinator import RequestBarrier, RequestSnapshot


class UsageFreshnessError(ValueError):
    """A known stale request or an unknown scope cannot authorize dispatch."""

    def __init__(self, reason: str, scope: str) -> None:
        self.reason = reason
        self.scope = scope
        super().__init__(f"{scope}: {reason}")


class UsageRequestFreshness(Protocol):
    def assert_current(self, receipt_id: str, slot: str, generation: int) -> None: ...


_FIELDS = frozenset({
    "Request Type", "Usage Operation", "Session", "Material", "Usage Role",
    "Target Usage", "Range Mode", "Start Page", "End Page", "Submitted", "Cancelled",
})
_FORBIDDEN = frozenset({
    "Intake Items", "Course", "Kind", "Material Role", "Actual Date", "Session Mode",
    "Session No", "Evidence Mode", "Additional Instructions", "Study Materials",
})
_CONTROL = frozenset({
    "Name", "Request Key", "Request Revision Hash", "Input Hash", "Plan Revision",
    "Request Status", "Result Status", "Result Reference", "Error", "Workspace Fingerprint",
})


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == ()


def _relation(value: Any, field: str, *, required: bool) -> str | None:
    if isinstance(value, Mapping) and set(value) == {"relation"}:
        value = value["relation"]
    if _empty(value) and not required:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 1 or not isinstance(value[0], str):
        raise ValueError(f"{field} requires exactly one physical page relation")
    return canonical_notion_page_id(value[0])


@dataclass(frozen=True)
class UsageRangeInput:
    operation: str
    session_page_id: str
    material_page_id: str
    usage_page_id: str | None
    role: str | None
    mode: str
    start_page: int | None
    end_page: int | None

    @property
    def user_json(self) -> str:
        return json.dumps({
            "Request Type": "USAGE_RANGE", "Usage Operation": self.operation,
            "Session": [self.session_page_id], "Material": [self.material_page_id],
            "Target Usage": [] if self.usage_page_id is None else [self.usage_page_id],
            "Usage Role": self.role, "Range Mode": self.mode,
            "Start Page": self.start_page, "End Page": self.end_page,
            "Submitted": True, "Cancelled": False,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)

    @property
    def user_hash(self) -> str:
        return hashlib.sha256(self.user_json.encode("utf-8")).hexdigest()

    def page_range(self, page_count: int | None) -> PageRange | None:
        if self.mode == "UNKNOWN":
            return None
        if self.mode == "WHOLE":
            return PageRange(None, None)
        if type(page_count) is not int or page_count < 1 or self.end_page is None or self.end_page > page_count:
            raise ValueError("bounded range exceeds verified PDF page coverage")
        return PageRange(self.start_page, self.end_page)


def parse_usage_range_input(fields: Mapping[str, Any], *, submitted: bool, cancelled: bool) -> UsageRangeInput:
    if submitted is not True or cancelled is not False:
        raise ValueError("range request must remain submitted and not cancelled")
    if fields.get("Request Type") != "USAGE_RANGE":
        raise ValueError("Request Type must be USAGE_RANGE")
    if any(not _empty(value) for name, value in fields.items() if name in _FORBIDDEN):
        raise ValueError("range request contains forbidden residual fields")
    if any(name not in _FIELDS | _FORBIDDEN | _CONTROL for name in fields):
        raise ValueError("range request contains unknown fields")
    operation, mode = fields.get("Usage Operation"), fields.get("Range Mode")
    if operation not in {"CREATE", "UPDATE"} or mode not in {"UNKNOWN", "BOUNDED", "WHOLE"}:
        raise ValueError("explicit Usage Operation and Range Mode are required")
    session = _relation(fields.get("Session"), "Session", required=True)
    material = _relation(fields.get("Material"), "Material", required=True)
    usage = _relation(fields.get("Target Usage"), "Target Usage", required=operation == "UPDATE")
    role = fields.get("Usage Role")
    if operation == "CREATE":
        if usage is not None or role not in {"Primary", "Supporting", "Reference"}:
            raise ValueError("CREATE requires explicit role and no target Usage")
    elif not _empty(role):
        raise ValueError("UPDATE preserves current role; role input must be empty")
    start, end = fields.get("Start Page"), fields.get("End Page")
    if mode == "BOUNDED":
        if type(start) is not int or type(end) is not int or not 1 <= start <= end:
            raise ValueError("BOUNDED requires positive ordered integer pages")
    elif not _empty(start) or not _empty(end):
        raise ValueError("UNKNOWN/WHOLE forbids residual page values")
    assert session is not None and material is not None
    return UsageRangeInput(operation, session, material, usage, role or None, mode,
                           None if _empty(start) else start, None if _empty(end) else end)


@dataclass(frozen=True)
class ValidatedRangeRequest:
    request_id: str
    source_id: str
    created_time: datetime
    input: UsageRangeInput
    slot: UsageSlotIdentity
    session_app_id: str
    material_app_id: str
    first_read_at: datetime
    second_read_at: datetime
    first_hash: str
    second_hash: str
    identity_json: str
    created_by: str | None
    last_edited_by: str | None

    def __post_init__(self) -> None:
        for name in ("request_id", "source_id"):
            if canonical_notion_page_id(getattr(self, name)) != getattr(self, name):
                raise ValueError("request/source UUID must be canonical")
        for value in (self.created_time, self.first_read_at, self.second_read_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("request observations require timezone-aware timestamps")
        if (self.second_read_at - self.first_read_at).total_seconds() < 1:
            raise ValueError("stable request reads must be at least one second apart")
        if self.first_hash != self.second_hash or self.second_hash != self.input.user_hash:
            raise ValueError("request USER input changed between stable reads")
        if self.input.session_page_id != self.slot.session_page_id or self.input.material_page_id != self.slot.material_page_id:
            raise ValueError("request relations differ from validated physical slot")


@dataclass(frozen=True)
class RangeInvalidation:
    receipt_id: str
    request_id: str
    generation: int
    slot: str
    reason: str


@dataclass(frozen=True)
class RangeValidationBatch:
    epoch: str
    workspace: str
    claims: tuple[ValidatedRangeRequest, ...]
    invalidations: tuple[RangeInvalidation, ...]
    complete_sources: frozenset[str]
    failed_sources: tuple[tuple[str, str], ...]
    field_errors: tuple[tuple[str, str], ...]
    blocked_slots: frozenset[str]
    workspace_blocked: bool


@dataclass(frozen=True)
class RangeClaimResult:
    epoch: str
    workspace: str
    claims: tuple[tuple[str, str, int], ...]  # receipt, slot, generation
    pending_invalidations: tuple[RangeInvalidation, ...]
    blocked_slots: frozenset[str]
    workspace_blocked: bool


@dataclass(frozen=True)
class RangePublishResult:
    published: tuple[tuple[str, str], ...]  # proposal, physical Queue UUID
    outcomes: tuple[tuple[str, str], ...]  # receipt, retry/partial/error reason


class UsageRangeHandler:
    """Consume the coordinator's immutable observation and persist ordered claims."""

    def __init__(self, *, state: Any, bridge: Any, graph_reader: Any, writer: Any,
                 source_reader: Any, source_binding_resolver: Any, config: Any,
                 clock: Callable[[], datetime], sleep: Callable[[float], None]) -> None:
        from uls.proposal.material_usage import MaterialUsageProposalProducer

        self.state, self.bridge, self.graph_reader, self.writer = state, bridge, graph_reader, writer
        self.clock, self.sleep = clock, sleep
        self.producer = MaterialUsageProposalProducer(
            graph_reader=graph_reader, writer=writer, source_reader=source_reader,
            source_binding_resolver=source_binding_resolver, config=config,
        )

    @staticmethod
    def _input(row: Mapping[str, Any]) -> UsageRangeInput:
        metadata = {"id", "page_id", "record_id", "_parent_data_source_id", "_parent_page_id", "created_time",
                    "last_edited_time", "created_by", "last_edited_by", "archived", "in_trash"}
        if row.get("archived") is not False or row.get("in_trash") is not False:
            raise ValueError("request is archived or archive evidence is unavailable")
        if row.get("Submitted") is not True or row.get("Cancelled") is not False:
            raise ValueError("request must remain submitted and not cancelled")
        return parse_usage_range_input({k: v for k, v in row.items() if k not in metadata},
                                       submitted=True, cancelled=False)

    def _identity(self, value: UsageRangeInput) -> tuple[UsageSlotIdentity, str, str]:
        from uls.adapters.notion.usage_range import physical_relations
        from uls.domain.ids import strict_entity_id

        sources = self.bridge.sources
        session = self.bridge.read_row(sources.sessions, value.session_page_id)
        material = self.bridge.read_row(sources.materials, value.material_page_id)
        session_id = strict_entity_id(session.get("ID"), expected_type="S")
        material_id = strict_entity_id(material.get("ID"), expected_type="M")
        if session_id is None or material_id is None:
            raise ValueError("request graph application identity is invalid")
        courses = physical_relations(session.get("Course"))
        if len(courses) != 1 or physical_relations(material.get("Course")) != courses:
            raise ValueError("request Session and Material require the same physical Course")
        role = value.role
        if value.operation == "UPDATE":
            usage = self.bridge.read_row(sources.material_usage, value.usage_page_id)
            if (physical_relations(usage.get("Session")) != (value.session_page_id,)
                or physical_relations(usage.get("Material")) != (value.material_page_id,)):
                raise ValueError("selected Usage differs from requested slot")
            role = usage.get("Role")
        if not isinstance(role, str):
            raise TypeError("request Usage role is missing")
        slot = UsageSlotIdentity(courses[0], value.session_page_id, value.material_page_id, role)
        # Resolve aliases/duplicates through the graph view as well as the exact physical read.
        for actual, expected in ((self.graph_reader.get_session(session_id), value.session_page_id),
                                 (self.graph_reader.get_material(material_id), value.material_page_id)):
            if actual is None or actual.get("page_id") != expected:
                raise ValueError("application identity does not resolve to the selected physical row")
        self.producer._course(self.graph_reader.get_session(session_id), session_id)
        return slot, session_id, material_id

    @staticmethod
    def _author(row: Mapping[str, Any], key: str) -> str:
        value = row.get(key)
        if not isinstance(value, Mapping) or not isinstance(value.get("id"), str):
            raise TypeError("request author evidence is missing")
        return canonical_notion_page_id(value["id"])

    def validate_batch(self, snapshot: RequestSnapshot) -> RangeValidationBatch:
        source = self.bridge.sources.input_requests
        complete = snapshot.complete_sources
        blocked = snapshot.workspace != self.bridge.sources.workspace_id or source not in complete or bool(snapshot.failed_sources)
        known = {row["request_id"]: row for row in self.state.list_usage_request_receipts(workspace=snapshot.workspace)}
        claims: list[ValidatedRangeRequest] = []
        invalidations: list[RangeInvalidation] = []
        errors: list[tuple[str, str]] = []
        blocked_slots: set[str] = set()
        seen: set[str] = set()
        for row in sorted(snapshot.rows, key=lambda row: (row.created_time, row.page_id)):
            if row.source_id != source:
                continue
            seen.add(row.page_id)
            previous = known.get(row.page_id)
            if previous is None and (row.values.get("Request Type") != "USAGE_RANGE"
                                     or row.values.get("Submitted") is not True or row.values.get("Cancelled") is True):
                continue
            slot: UsageSlotIdentity | None = None
            try:
                value = self._input(row.values)
                slot, session_id, material_id = self._identity(value)
                identity = json.dumps({"slot": slot.canonical_json, "session": session_id, "material": material_id}, sort_keys=True)
                if previous is not None:
                    if previous["user_hash"] != value.user_hash or previous["identity_json"] != identity:
                        raise ValueError("claimed request input or graph identity changed")
                    continue
                first_at = snapshot.observed_at
                elapsed = (self.clock() - first_at).total_seconds()
                self.sleep(max(0.0, 1.0 - elapsed))
                second = self.bridge.read_row(source, row.page_id)
                current = self._input(second)
                if self._identity(current) != (slot, session_id, material_id):
                    raise ValueError("physical request identity changed during stable read")
                claims.append(ValidatedRangeRequest(
                    row.page_id, source, row.created_time, current, slot, session_id, material_id,
                    first_at, self.clock(), value.user_hash, current.user_hash, identity,
                    self._author(second, "created_by"), self._author(second, "last_edited_by"),
                ))
            except Exception as exc:  # noqa: BLE001 - one unsafe request blocks its known scope
                errors.append((row.page_id, type(exc).__name__))
                if previous is not None:
                    invalidations.append(RangeInvalidation(previous["receipt_id"], row.page_id,
                                                           previous["generation"], previous["usage_slot_key"], "request changed or unavailable"))
                elif slot is not None:
                    blocked_slots.add(slot.key)
                else:
                    blocked = True
        if source in complete:
            for request_id, previous in known.items():
                if request_id not in seen:
                    invalidations.append(RangeInvalidation(previous["receipt_id"], request_id,
                                                           previous["generation"], previous["usage_slot_key"], "request missing from complete source"))
        return RangeValidationBatch(snapshot.epoch, snapshot.workspace, tuple(claims), tuple(invalidations),
                                    snapshot.complete_sources, tuple(sorted(snapshot.failed_sources.items())),
                                    tuple(errors), frozenset(blocked_slots), blocked)

    def assert_current(self, receipt_id: str, slot: str, generation: int) -> None:
        head = self.state.get_range_intent_head(slot)
        receipt = self.state.get_usage_request_receipt(receipt_id)
        if (head is None or receipt is None or not head.active or head.intent_generation != generation
            or head.current_receipt_id != receipt_id or receipt["generation"] != generation):
            raise UsageFreshnessError("inactive or stale range intent", slot)
        try:
            rows = self.bridge.list_rows(self.bridge.sources.input_requests)
            known = {row["request_id"]: row for row in self.state.list_usage_request_receipts(workspace=receipt["workspace"])}
            original = None
            for row in rows:
                request_id = canonical_notion_page_id(row["id"])
                if request_id == receipt["request_id"]:
                    original = row
                if row.get("Request Type") != "USAGE_RANGE" or row.get("Submitted") is not True or row.get("Cancelled") is True:
                    continue
                value = self._input(row)
                identity, session_id, material_id = self._identity(value)
                previous = known.get(request_id)
                if identity.key == slot and (previous is None or previous["user_hash"] != value.user_hash):
                    raise UsageFreshnessError("unprocessed competing range request", slot)
                if request_id == receipt["request_id"]:
                    current_identity = json.dumps({"slot": identity.canonical_json, "session": session_id, "material": material_id}, sort_keys=True)
                    if current_identity != receipt["identity_json"]:
                        raise UsageFreshnessError("request graph identity changed", slot)
            if original is None or self._input(original).user_hash != receipt["user_hash"]:
                raise UsageFreshnessError("original request missing or changed", slot)
        except UsageFreshnessError:
            raise
        except Exception as exc:
            raise UsageFreshnessError("request enumeration or identity is unavailable", "workspace") from exc

    def _snapshot(self, session_id: str) -> str:
        from uls.adapters.notion.usage_range import physical_relations
        from uls.domain.approval_identity import c5_usage_snapshot_json

        rows = []
        for row in self.graph_reader.get_material_usage(session_id):
            if row.get("archived") is not False or row.get("in_trash") is not False:
                raise ValueError("slot contains archived or unreadable row")
            session = row.get("Session", {}).get("relation", ())
            material = row.get("Material", {}).get("relation", ())
            if len(session) != 1 or len(material) != 1:
                raise ValueError("slot row relations are malformed")
            physical_relations(row.get("_physical_relations", {}).get("Session"))
            rows.append({"provider": "notion", "provider_row_id": row["page_id"], "usage_app_id": row["ID"],
                         "session_app_id": session[0]["id"], "material_app_id": material[0]["id"],
                         "usage_role": row.get("Role"), "start_page": row.get("Start Page"),
                         "end_page": row.get("End Page"), "verified": row.get("Verified")})
        return c5_usage_snapshot_json(rows)

    def claim_ordered(self, batch: RangeValidationBatch) -> RangeClaimResult:
        from uls.domain.approval_identity import (
            canonical_action_json,
            derive_usage_id,
            parse_c5_usage_snapshot,
        )

        if batch.workspace != self.bridge.sources.workspace_id:
            raise ValueError("range batch belongs to another workspace")
        blocked = set(batch.blocked_slots)
        pending: list[RangeInvalidation] = []
        claimed: list[tuple[str, str, int]] = []
        for invalidation in batch.invalidations:
            try:
                invalidated = self.state.invalidate_usage_request(
                    receipt_id=invalidation.receipt_id, slot=invalidation.slot,
                    generation=invalidation.generation, reason=invalidation.reason,
                )
                if invalidated:
                    blocked.add(invalidation.slot)
            except Exception:  # noqa: BLE001 - do not publish through failed durable invalidation
                pending.append(invalidation)
                blocked.add(invalidation.slot)
        self.state.finalize_usage_producer_proofs(batch.workspace)
        if not batch.workspace_blocked:
            for request in sorted(batch.claims, key=lambda value: (value.created_time, value.request_id)):
                if request.slot.key in blocked:
                    continue
                try:
                    baseline = self._snapshot(request.session_app_id)
                    candidate = None
                    expected = None
                    action = None
                    if request.input.mode != "UNKNOWN":
                        material = self.graph_reader.get_material(request.material_app_id)
                        desired = request.input.page_range(material.get("Page Count"))
                        assert desired is not None
                        old = None
                        if request.input.operation == "UPDATE":
                            assert request.input.usage_page_id is not None
                            physical_usage_id = request.input.usage_page_id.replace("-", "")
                            old = next(row for row in parse_c5_usage_snapshot(baseline)
                                       if row["provider_row_id"] == physical_usage_id)
                            candidate = old["usage_app_id"]
                        else:
                            candidate = derive_usage_id(request.session_app_id, request.material_app_id, request.slot.role, desired)
                        expected = {"usage_app_id": candidate, "session_app_id": request.session_app_id,
                                    "material_app_id": request.material_app_id, "usage_role": request.slot.role,
                                    "start_page": desired.start_page, "end_page": desired.end_page,
                                    "verified": False if old is None else old["verified"]}
                        previous = expected if old is None else old
                        old_snapshot = {"usage_id": candidate, "session_id": request.session_app_id,
                                        "material_id": request.material_app_id, "role": request.slot.role,
                                        "start_page": previous["start_page"], "end_page": previous["end_page"],
                                        "verified": previous["verified"]}
                        semantics = self.producer.prepare_human_range(
                            session_id=request.session_app_id, material_id=request.material_app_id, role=request.slot.role,
                            operation="create_usage" if request.input.operation == "CREATE" else "update_range",
                            target_id=candidate, desired_range=desired, old_snapshot=old_snapshot,
                        )
                        action = canonical_action_json(semantics)
                    receipt = self.state.claim_usage_request(request, workspace=batch.workspace, candidate_id=candidate,
                                                             baseline_json=baseline, expected_json=None if expected is None else json.dumps(expected),
                                                             action_json=action)
                    claimed.append((receipt["receipt_id"], request.slot.key, receipt["generation"]))
                except Exception:  # noqa: BLE001 - failed claim is a publication barrier for its slot
                    blocked.add(request.slot.key)
        return RangeClaimResult(batch.epoch, batch.workspace, tuple(claimed), tuple(pending), frozenset(blocked), batch.workspace_blocked)

    def _assert_action(self, action_json: str) -> None:
        from uls.domain.approval_identity import canonical_action_json

        action = json.loads(action_json)
        current = self.producer.prepare_human_range(
            session_id=action["session_id"], material_id=action["material_id"], role=action["usage_role"],
            operation=action["operation"], target_id=action["target_entity_id"],
            desired_range=PageRange(**action["desired_range"]), old_snapshot=action["old_snapshot"],
        )
        if canonical_action_json(current) != action_json:
            raise UsageFreshnessError("proposal source semantics changed", "slot")

    def publish_pending(self, barrier: RequestBarrier) -> RangePublishResult:
        """Publish receipt-owned outboxes only; parent owns ApprovalReader and HAA."""
        from uls.domain.approval_identity import (
            canonical_action_json,
            canonical_semantics_from_queue,
            canonical_usage_proposal_envelope_json,
            parse_usage_proposal_envelope,
        )
        from uls.domain.errors import ProviderWriteNotAppliedError

        if barrier.workspace != self.bridge.sources.workspace_id or "usage_range" not in barrier.enabled_handlers:
            raise ValueError("range publication barrier does not authorize this handler")
        if barrier.workspace_blocked:
            return RangePublishResult((), ())
        published: list[tuple[str, str]] = []
        outcomes: list[tuple[str, str]] = []
        for invalidation in self.state.pending_usage_invalidations(barrier.workspace):
            try:
                proposal_id = invalidation["proposal_id"]
                matches = self.bridge.find_approval_rows(proposal_id)
                if len(matches) > 1:
                    raise ValueError("invalidated Queue identity is ambiguous")
                if matches:
                    self.writer._supersede_range_proposal(proposal_id, invalidation["reason"])
                    current = self.bridge.find_approval_rows(proposal_id)
                    if len(current) != 1 or current[0].get("State") not in {"SUPERSEDED", "FAILED", "APPLIED", "REJECTED"}:
                        raise ValueError("range invalidation projection is not verified")
                self.state.acknowledge_usage_invalidation(proposal_id)
            except Exception as exc:  # noqa: BLE001 - a projection failure never authorizes target mutation
                outcomes.append((invalidation["proposal_id"], type(exc).__name__))
        for receipt in self.state.list_usage_request_receipts(workspace=barrier.workspace):
            slot, generation, receipt_id = receipt["usage_slot_key"], receipt["generation"], receipt["receipt_id"]
            head = self.state.get_range_intent_head(slot)
            if (head is None or head.intent_generation != generation or not head.active
                or slot in barrier.blocked_slots or head.input_mode == "UNKNOWN"):
                continue
            token = None
            try:
                self.assert_current(receipt_id, slot, generation)
                action_json = self.state.get_usage_producer_action(slot, generation)
                if action_json is None:
                    raise ValueError("immutable producer action is missing")
                self._assert_action(action_json)
                action = json.loads(action_json)
                intent = self.state.get_usage_producer_intent(slot, generation)
                if action["operation"] == "create_usage" and intent["status"] == "PREPARED":
                    baseline = self._snapshot(head.session_app_id)
                    token = self.state.acquire_usage_creation(slot=slot, generation=generation,
                                                               receipt_hash=receipt["user_hash"], baseline_json=baseline)
                    identity = UsageSlotIdentity.from_json(head.slot_identity_json)
                    props = {"Name": f"Requested range: {head.material_app_id}", "ID": intent["candidate_id"],
                             "Session": {"relation": [{"id": identity.session_page_id}]},
                             "Material": {"relation": [{"id": identity.material_page_id}]},
                             "Role": head.usage_role, "Start Page": action["desired_range"]["start_page"],
                             "End Page": action["desired_range"]["end_page"], "Verified": False}
                    attempted = False

                    def before_create(receipt_id: str = receipt_id, slot: str = slot, generation: int = generation,
                                      action_json: str = action_json, token: str = token,
                                      session_id: str = head.session_app_id, candidate_id: str = intent["candidate_id"]) -> None:
                        nonlocal attempted
                        self.assert_current(receipt_id, slot, generation)
                        self._assert_action(action_json)
                        if self.bridge.find_entity_by_id(self.bridge.sources.material_usage, candidate_id) is not None:
                            raise UsageFreshnessError("candidate Usage identity already exists", slot)
                        self.state.mark_usage_creation_mutating(token, baseline_json=self._snapshot(session_id))
                        attempted = True

                    try:
                        self.writer._create_range_entity("Material Usage", props, before_provider_write=before_create)
                    except ProviderWriteNotAppliedError as exc:
                        if attempted:
                            self.state.record_usage_creation_outcome(token, readback_json=self._snapshot(head.session_app_id),
                                                                     not_applied_error=exc)
                        raise
                    self.state.record_usage_creation_outcome(token, readback_json=self._snapshot(head.session_app_id))
                    # Finalize the proven local outcome before a subsequent freshness
                    # refusal; the outbox remains unpublished and cannot authorize HAA.
                    self.state.seal_usage_proposal(slot=slot, generation=generation,
                                                   receipt_hash=receipt["user_hash"], action_json=action_json)
                    self.assert_current(receipt_id, slot, generation)
                proposal_id = self.state.seal_usage_proposal(slot=slot, generation=generation,
                                                             receipt_hash=receipt["user_hash"], action_json=action_json)
                outbox = self.state.get_usage_proposal_outbox(proposal_id)
                dependency = action["material_dependency"]
                props = {"Name": f"Range review: {head.material_app_id}", "Proposal ID": proposal_id,
                         "Proposal Type": "MATERIAL_USAGE" if action["operation"] == "create_usage" else "PAGE_RANGE",
                         "State": "PENDING_REVIEW", "Decision": "Pending",
                         "Course": {"relation": [{"id": action["course_relation_page_id"]}]},
                         "Target Entity ID": action["target_entity_id"], "Source Ref": dependency["source_ref"],
                         "Source Hash": dependency["source_hash"], "Source Version": dependency["source_version"],
                         "Proposed Action": action_json, "Proposal Envelope": outbox.envelope_json,
                         "Review Reason": action["review_reason"]}
                matches = self.bridge.find_approval_rows(proposal_id)
                if not matches:
                    def before_queue(receipt_id: str = receipt_id, slot: str = slot, generation: int = generation,
                                     action_json: str = action_json) -> None:
                        self.assert_current(receipt_id, slot, generation)
                        self._assert_action(action_json)
                        self._assert_bound_usage(slot, action_json)

                    try:
                        self.writer._create_range_entity("Automation Queue", props, before_provider_write=before_queue)
                    except Exception:
                        matches = self.bridge.find_approval_rows(proposal_id)
                        if not matches:
                            raise
                    matches = self.bridge.find_approval_rows(proposal_id)
                if len(matches) != 1:
                    raise ValueError("published Queue identity is ambiguous")
                row = matches[0]
                envelope = parse_usage_proposal_envelope(row)
                if (envelope is None or canonical_action_json(canonical_semantics_from_queue(row)) != action_json
                    or canonical_usage_proposal_envelope_json(envelope) != outbox.envelope_json
                    or row.get("Target Entity ID") != action["target_entity_id"]):
                    raise ValueError("published Queue bytes differ from immutable outbox")
                self.assert_current(receipt_id, slot, generation)
                self.state.mark_usage_proposal_published(proposal_id=proposal_id, queue_page_id=row["id"])
                published.append((proposal_id, row["id"]))
            except Exception as exc:  # noqa: BLE001 - preserve partial effects/ownership and continue independent slots
                reason = f"Range publication deferred ({type(exc).__name__})"
                if token is not None:
                    durable = self.state.get_usage_producer_intent(slot, generation)
                    if durable is not None and durable["status"] in {"CREATED", "SEALED"}:
                        reason = "Unverified Usage was created and retained; Queue publication is deferred"
                    elif isinstance(exc, ProviderWriteNotAppliedError):
                        reason = "Provider confirmed no creation; retry remains subject to request validation"
                    else:
                        reason = "Creation was not confirmed; unresolved ownership requires reconciliation"
                outcomes.append((receipt_id, reason))
            finally:
                if token is not None:
                    self.state.release_usage_creation(token)
        return RangePublishResult(tuple(published), tuple(outcomes))

    def _assert_bound_usage(self, slot: str, action_json: str) -> None:
        from uls.domain.approval_identity import parse_c5_usage_snapshot

        head = self.state.get_range_intent_head(slot)
        action = json.loads(action_json)
        if head is None:
            raise UsageFreshnessError("bound usage head is missing", slot)
        rows = [row for row in parse_c5_usage_snapshot(self._snapshot(head.session_app_id))
                if row["material_app_id"] == head.material_app_id and row["usage_role"] == head.usage_role]
        old = action["old_snapshot"]
        if (len(rows) != 1 or rows[0]["provider_row_id"] != head.current_usage_provider_row_id
            or rows[0]["provider"] != head.current_usage_provider or rows[0]["usage_app_id"] != head.current_usage_app_id
            or rows[0]["start_page"] != old["start_page"] or rows[0]["end_page"] != old["end_page"]
            or rows[0]["verified"] != old["verified"]):
            raise UsageFreshnessError("bound physical usage changed before Queue publication", slot)
