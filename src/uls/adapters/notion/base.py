"""Provider-neutral Notion contracts and human-gate write policy.

This module intentionally contains no Notion SDK imports.  It is the policy
boundary used by provider adapters and by the approval application path.  A
real adapter may use any SDK (in a separately isolated module), but writes
must pass through :func:`enforce_write_policy` before they reach that SDK.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from uls.domain.enums import AutomationActor
from uls.domain.errors import PolicyViolation


AUTOMATION_QUEUE = "Automation Queue"
"""The canonical Notion database name for human-review proposals."""

AUTOMATION_QUEUE_DB = AUTOMATION_QUEUE
AUTOMATION_QUEUE_DATABASE = AUTOMATION_QUEUE


class _ValueEnum(str, Enum):
    """A wire-value enum whose string representation is its stored value."""

    def __str__(self) -> str:
        return self.value


class ProposalType(_ValueEnum):
    """The proposal kinds defined by Automation Queue."""

    MATERIAL_REVISION = "MATERIAL_REVISION"
    GOODNOTES_MATCH = "GOODNOTES_MATCH"
    MATERIAL_USAGE = "MATERIAL_USAGE"
    PAGE_RANGE = "PAGE_RANGE"
    EXAM_SCOPE = "EXAM_SCOPE"
    OTHER = "OTHER"


class QueueState(_ValueEnum):
    """The state machine states for an Automation Queue item."""

    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    APPLIED = "APPLIED"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"


class Decision(_ValueEnum):
    """The human-owned decision field, with spec-prescribed spellings."""

    Pending = "Pending"
    Approve = "Approve"
    Reject = "Reject"


def parse_aliases(rich_text: str | None) -> list[str]:
    """Parse the canonical ``alias 1 | alias 2`` Rich-text representation.

    Empty pieces are discarded, surrounding whitespace is removed, and the
    spelling/case of non-empty aliases is retained for display.
    """

    if rich_text is None:
        return []
    if not isinstance(rich_text, str):
        raise TypeError(f"rich_text must be str or None, got {type(rich_text).__name__}")
    return [part.strip() for part in rich_text.split("|") if part.strip()]


def normalize_alias(alias: str) -> str:
    """Return the comparison form of an alias.

    ULS keeps display case in Notion, while matching is whitespace-trimmed and
    Unicode case-insensitive.  Internal whitespace is deliberately preserved:
    it is part of a free-form handle rather than an undocumented rewrite.
    """

    if not isinstance(alias, str):
        raise TypeError(f"alias must be str, got {type(alias).__name__}")
    return alias.strip().casefold()


def aliases_match(rich_text: str | None, alias: str) -> bool:
    """Return whether ``alias`` matches one parsed Rich-text alias."""

    needle = normalize_alias(alias)
    if not needle:
        return False
    return any(normalize_alias(candidate) == needle for candidate in parse_aliases(rich_text))


@runtime_checkable
class NotionAdapter(Protocol):
    """Provider-neutral Notion capability surface.

    Implementations of ``find_by_alias`` must use :func:`parse_aliases` for
    the Rich-text ``Aliases`` property (the reusable
    :func:`find_alias_matches` helper implements that rule).  In particular,
    the whole Rich-text string must not be compared as one opaque alias.
    Implementations should
    call :func:`enforce_write_policy` at their concrete write boundary too;
    the approval helpers below also guard every write they initiate.

    ``actor`` is an explicit enum on write methods.  A provider adapter may
    omit that parameter in its implementation when it is wrapped by a
    policy-enforcing boundary, but it must never expose a free-form actor
    string as an authority selector.
    """

    def find_entity_by_id(self, target_db: str, entity_id: str) -> Any | None:
        ...

    def find_by_alias(self, target_db: str, alias: str) -> Any | None:
        ...

    def create_entity(
        self,
        target_db: str,
        properties: Mapping[str, Any],
        *,
        actor: AutomationActor = AutomationActor.AUTOMATION,
    ) -> Any:
        ...

    def update_properties(
        self,
        target_db: str,
        entity_id: str,
        patch: Mapping[str, Any],
        *,
        actor: AutomationActor = AutomationActor.AUTOMATION,
    ) -> Any:
        ...

    def write_source_metadata_region(
        self,
        target_db: str,
        entity_id: str,
        patch: Mapping[str, Any],
        *,
        actor: AutomationActor = AutomationActor.AUTOMATION,
    ) -> Any:
        ...

    def write_ai_region(
        self,
        target_db: str,
        entity_id: str,
        patch: Mapping[str, Any],
        *,
        actor: AutomationActor = AutomationActor.AUTOMATION,
    ) -> Any:
        ...

    def read_approval(self, proposal_id: str) -> Any | None:
        ...


def _wire_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _is_automation_queue(target_db: str) -> bool:
    if not isinstance(target_db, str):
        return False
    normalized = "".join(character for character in target_db.casefold() if character.isalnum())
    return normalized == "automationqueue"


def _coerce_actor(actor: AutomationActor) -> AutomationActor:
    """Require an actual capability enum, rather than a caller-selected label."""

    if not isinstance(actor, AutomationActor):
        raise PolicyViolation(
            "write actor must be an AutomationActor capability, not a free-form string"
        )
    return actor


def enforce_write_policy(
    actor: AutomationActor,
    target_db: str,
    patch: Mapping[str, Any],
) -> None:
    """Enforce the v1.2 human-gate policy at the write boundary.

    The function is intentionally small and side-effect free so concrete
    adapters can call it immediately before making a provider mutation.
    ``AUTOMATION`` can create/update proposal metadata and system-owned queue
    states, but cannot manufacture a human decision or an approval state.
    """

    actor = _coerce_actor(actor)
    if not isinstance(patch, Mapping):
        raise PolicyViolation("write patch must be a mapping")

    if patch.get("Verified") is True and actor is not AutomationActor.HUMAN_APPROVAL_APPLIER:
        raise PolicyViolation("Material Usage.Verified=true is human-only")

    if patch.get("Scope Confirmed") is True and actor is not AutomationActor.HUMAN_APPROVAL_APPLIER:
        raise PolicyViolation("Exam.Scope Confirmed=true is human-only")

    # Decision is human-owned for every internal capability.  The applier
    # records Decision By/At but never changes Decision itself; ApprovalReader
    # may only read it and derive State.
    if "Decision" in patch and actor is not AutomationActor.AUTOMATION:
        raise PolicyViolation("Decision is human-owned and cannot be changed by this capability")

    if not _is_automation_queue(target_db):
        return

    state = _wire_value(patch.get("State"))
    if actor is AutomationActor.AUTOMATION:
        if "Decision" in patch and _wire_value(patch["Decision"]) != Decision.Pending.value:
            raise PolicyViolation("Automation Queue Decision is human-owned")
        if state in {
            QueueState.APPROVED.value,
            QueueState.REJECTED.value,
            QueueState.APPLIED.value,
        }:
            raise PolicyViolation("Automation cannot promote approval state")

        # These are the only queue states an ordinary automation write may
        # name.  SUPERSEDED and FAILED are system-owned terminal paths; they
        # do not grant human authority and are therefore safe to pass through
        # this guard.  The concrete invalidation/failure helpers below are the
        # intended callers.
        if "State" in patch and state not in {
            QueueState.PENDING_REVIEW.value,
            QueueState.SUPERSEDED.value,
            QueueState.FAILED.value,
        }:
            raise PolicyViolation(f"Automation Queue state is not automation-writable: {state!r}")
    elif actor is AutomationActor.APPROVAL_READER:
        if "State" in patch and state not in {
            QueueState.PENDING_REVIEW.value,
            QueueState.APPROVED.value,
            QueueState.REJECTED.value,
        }:
            raise PolicyViolation("ApprovalReader may only derive review state")
    elif actor is AutomationActor.HUMAN_APPROVAL_APPLIER:
        if state in {QueueState.APPROVED.value, QueueState.REJECTED.value}:
            raise PolicyViolation("HumanApprovalApplier cannot manufacture review state")


def _coerce_enum(value: Any, enum_type: type[_ValueEnum], field_name: str) -> _ValueEnum:
    value = _wire_value(value)
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError as exc:
            raise PolicyViolation(f"Invalid {field_name}: {value!r}") from exc
    raise PolicyViolation(f"Invalid {field_name}: {value!r}")


def coerce_proposal_type(value: Any) -> ProposalType:
    """Coerce a stored proposal type while retaining exact wire values."""

    return _coerce_enum(value, ProposalType, "Proposal Type")  # type: ignore[return-value]


def coerce_queue_state(value: Any) -> QueueState:
    """Coerce a stored queue state while retaining exact wire values."""

    return _coerce_enum(value, QueueState, "State")  # type: ignore[return-value]


def coerce_decision(value: Any) -> Decision:
    """Coerce a stored human decision while retaining exact wire values."""

    return _coerce_enum(value, Decision, "Decision")  # type: ignore[return-value]


_VALID_TRANSITIONS: dict[QueueState, frozenset[QueueState]] = {
    QueueState.PENDING_REVIEW: frozenset(
        {QueueState.PENDING_REVIEW, QueueState.APPROVED, QueueState.REJECTED}
    ),
    QueueState.APPROVED: frozenset(
        {QueueState.APPROVED, QueueState.APPLIED, QueueState.FAILED, QueueState.SUPERSEDED}
    ),
    QueueState.REJECTED: frozenset({QueueState.REJECTED}),
    QueueState.APPLIED: frozenset({QueueState.APPLIED}),
    QueueState.FAILED: frozenset({QueueState.FAILED}),
    QueueState.SUPERSEDED: frozenset({QueueState.SUPERSEDED}),
}


def transition_queue_state(
    current_state: QueueState,
    next_state: QueueState,
    *,
    decision: Decision | None = None,
    actor: AutomationActor | None = None,
) -> QueueState:
    """Validate and return a queue state transition.

    Approval and rejection are derivations of the human Decision and can
    only be requested by the internal ``APPROVAL_READER`` capability.
    ``APPLIED`` is reserved for the internal approval applier.  The optional
    actor is an enum, never a caller-provided string.
    """

    current = coerce_queue_state(current_state)
    next_value = coerce_queue_state(next_state)
    if actor is not None:
        actor = _coerce_actor(actor)

    if next_value not in _VALID_TRANSITIONS[current]:
        raise PolicyViolation(f"Invalid queue transition: {current.value} -> {next_value.value}")

    if next_value is current:
        return next_value

    if next_value in {QueueState.APPROVED, QueueState.REJECTED}:
        if actor is not AutomationActor.APPROVAL_READER:
            raise PolicyViolation("Only ApprovalReader may derive approval state")
        if decision is None:
            raise PolicyViolation("Approval state derivation requires a human Decision")
        actual_decision = coerce_decision(decision)
        expected = (
            QueueState.APPROVED
            if actual_decision is Decision.Approve
            else QueueState.REJECTED
            if actual_decision is Decision.Reject
            else QueueState.PENDING_REVIEW
        )
        if expected is not next_value:
            raise PolicyViolation("Queue State does not match the human Decision")

    if next_value is QueueState.APPLIED and actor is not AutomationActor.HUMAN_APPROVAL_APPLIER:
        raise PolicyViolation("Only HumanApprovalApplier may mark a proposal APPLIED")

    return next_value


def derive_queue_state(decision: Decision) -> QueueState:
    """Pure state derivation used by :class:`ApprovalReader`."""

    actual = coerce_decision(decision)
    return {
        Decision.Pending: QueueState.PENDING_REVIEW,
        Decision.Approve: QueueState.APPROVED,
        Decision.Reject: QueueState.REJECTED,
    }[actual]


derive_state_from_decision = derive_queue_state
transition_state = transition_queue_state


def _normal_key(key: str) -> str:
    return "".join(character for character in key.casefold() if character.isalnum())


def _field_names(name: str) -> tuple[str, ...]:
    snake = []
    for character in name:
        if character.isupper() and snake:
            snake.append("_")
        snake.append(character.casefold())
    snake_name = "".join(snake)
    return (name, snake_name, snake_name.replace("_", ""))


def _unwrap_property(value: Any) -> Any:
    """Unwrap the small subset of Notion property shapes useful to policy."""

    if isinstance(value, Mapping):
        if "value" in value and len(value) == 1:
            return _unwrap_property(value["value"])
        for key in ("title", "rich_text", "select", "status", "number", "checkbox", "date"):
            if key in value and len(value) <= 2:
                inner = value[key]
                if key in {"title", "rich_text"} and isinstance(inner, Sequence) and not isinstance(
                    inner, (str, bytes)
                ):
                    pieces: list[str] = []
                    for item in inner:
                        if isinstance(item, Mapping):
                            text = item.get("plain_text")
                            if text is None and isinstance(item.get("text"), Mapping):
                                text = item["text"].get("content")
                            if text is not None:
                                pieces.append(str(text))
                        elif item is not None:
                            pieces.append(str(item))
                    return "".join(pieces)
                return _unwrap_property(inner)
        if "name" in value and len(value) <= 2:
            return value["name"]
        if "plain_text" in value and len(value) <= 2:
            return value["plain_text"]
        if "content" in value and len(value) <= 2:
            return value["content"]
    return value


def _record_properties(record: Any) -> Any:
    if isinstance(record, Mapping) and "properties" in record:
        properties = record["properties"]
        if isinstance(properties, Mapping):
            return properties
    properties = getattr(record, "properties", None)
    if isinstance(properties, Mapping):
        return properties
    return record


def _get_field(record: Any, *names: str, default: Any = None) -> Any:
    """Read a field from flat fakes, Notion-like properties, or simple objects."""

    sources: list[Any] = []
    properties = _record_properties(record)
    if properties is not record:
        sources.append(properties)
    sources.append(record)

    wanted = {_normal_key(candidate) for name in names for candidate in _field_names(name)}
    for source in sources:
        if isinstance(source, Mapping):
            for key, value in source.items():
                if isinstance(key, str) and _normal_key(key) in wanted:
                    return _unwrap_property(value)
        else:
            for name in names:
                for candidate in _field_names(name):
                    if hasattr(source, candidate):
                        return _unwrap_property(getattr(source, candidate))
    return default


def _raw_field(record: Any, *names: str) -> Any:
    """Read an unwrapped provider property for alias-segment handling."""

    properties = _record_properties(record)
    sources: list[Any] = []
    if properties is not record:
        sources.append(properties)
    sources.append(record)
    wanted = {_normal_key(candidate) for name in names for candidate in _field_names(name)}
    for source in sources:
        if isinstance(source, Mapping):
            for key, value in source.items():
                if isinstance(key, str) and _normal_key(key) in wanted:
                    return value
        else:
            for name in names:
                for candidate in _field_names(name):
                    if hasattr(source, candidate):
                        return getattr(source, candidate)
    return None


def _record_aliases(record: Any) -> list[str]:
    """Return explicit aliases plus the schema's implicit ID and Name aliases."""

    raw_aliases = _raw_field(record, "Aliases", "aliases")
    aliases = parse_aliases(_unwrap_property(raw_aliases))
    if isinstance(raw_aliases, Mapping) and isinstance(raw_aliases.get("rich_text"), Sequence):
        for item in raw_aliases["rich_text"]:
            if isinstance(item, Mapping):
                piece = item.get("plain_text")
                if piece is None and isinstance(item.get("text"), Mapping):
                    piece = item["text"].get("content")
                if piece is not None:
                    aliases.extend(parse_aliases(str(piece)))
    for field_name in ("ID", "Entity ID", "Name"):
        value = _get_field(record, field_name, field_name.casefold(), default=None)
        if isinstance(value, str) and value.strip():
            aliases.append(value.strip())
    return aliases


def find_alias_matches(records: Iterable[Any], alias: str) -> list[Any]:
    """Filter records using parsed Rich-text aliases and implicit aliases.

    Adapter implementations can use this helper for their ``find_by_alias``
    method.  It is deliberately a filter over injected records, keeping the
    provider-independent alias semantics testable without an SDK.
    """

    needle = normalize_alias(alias)
    if not needle:
        return []
    return [
        record
        for record in records
        if any(normalize_alias(candidate) == needle for candidate in _record_aliases(record))
    ]


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "__dict__"):
        values = vars(value)
        if isinstance(values, Mapping):
            return values
    return None


def _merge_records(supplied: Any, current: Any) -> Any:
    """Use the adapter's current record, filling optional fields from input."""

    if current is None:
        return supplied
    current_mapping = _as_mapping(current)
    supplied_mapping = _as_mapping(supplied)
    if current_mapping is None or supplied_mapping is None:
        return current
    merged = dict(supplied_mapping)
    merged.update(current_mapping)
    if "properties" in supplied_mapping or "properties" in current_mapping:
        properties: dict[str, Any] = {}
        supplied_properties = supplied_mapping.get("properties", {})
        current_properties = current_mapping.get("properties", {})
        if isinstance(supplied_properties, Mapping):
            properties.update(supplied_properties)
        if isinstance(current_properties, Mapping):
            properties.update(current_properties)
        merged["properties"] = properties
    return merged


def _valid_proposal_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PolicyViolation("Proposal ID must be a non-empty string")
    return value.strip()


def _proposal_id(record: Any) -> str:
    return _valid_proposal_id(
        _get_field(record, "Proposal ID", "proposal_id", "proposalId", default=None)
    )


def _requested_proposal_id(value: Any) -> str:
    if isinstance(value, str):
        return _valid_proposal_id(value)
    return _proposal_id(value)


def _decision_field(record: Any) -> Any:
    value = _get_field(record, "Decision", "decision", default=None)
    if value is not None:
        return value
    if isinstance(record, Decision):
        return record
    if isinstance(record, str) and record in {member.value for member in Decision}:
        return record
    return None


_MISSING = object()


class _StaleApproval(PolicyViolation):
    """Internal signal for an approved action that no longer matches target state."""


def _same_stored_value(left: Any, right: Any) -> bool:
    if isinstance(left, Enum):
        left = left.value
    if isinstance(right, Enum):
        right = right.value
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        if set(left) != set(right):
            return False
        return all(_same_stored_value(left[key], right[key]) for key in left)
    if isinstance(left, Sequence) and isinstance(right, Sequence) and not isinstance(
        left, (str, bytes)
    ) and not isinstance(right, (str, bytes)):
        return len(left) == len(right) and all(
            _same_stored_value(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


def _assert_supplied_matches_current(supplied: Any, current: Any) -> None:
    """Reject a caller's different target/action under the same Proposal ID."""

    if isinstance(supplied, str):
        return
    for names in (
        ("Proposal Type", "proposal_type"),
        ("Target DB", "Target Database", "target_db", "target_database"),
        ("Target Entity ID", "Target Entity", "target_entity_id", "target_id"),
        ("Source Hash", "source_hash"),
        ("Source Version", "source_version"),
    ):
        supplied_value = _get_field(supplied, *names, default=_MISSING)
        if supplied_value is _MISSING or supplied_value is None:
            continue
        current_value = _get_field(current, *names, default=_MISSING)
        if current_value is _MISSING or not _same_stored_value(supplied_value, current_value):
            raise PolicyViolation("Requested proposal does not match the current queue item")

    supplied_action = _get_field(
        supplied,
        "Proposed Action",
        "proposed_action",
        "action",
        default=_MISSING,
    )
    if supplied_action is not _MISSING:
        try:
            supplied_action = _action_mapping(supplied)
            current_action = _action_mapping(current)
        except PolicyViolation:
            raise
        if not _same_stored_value(dict(supplied_action), dict(current_action)):
            raise PolicyViolation("Requested proposal action does not match the current queue item")


def _call_with_supported_signature(
    method: Callable[..., Any],
    positional: tuple[Any, ...],
    keyword_values: Mapping[str, Any],
) -> Any:
    """Call a fake/provider method without weakening the policy boundary.

    Test fakes in this repository intentionally use both three-argument and
    actor-aware four-argument write signatures.  Signature inspection lets us
    support both without catching a TypeError raised by a mutation body and
    accidentally retrying that mutation.
    """

    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return method(*positional, **dict(keyword_values))

    parameters = list(signature.parameters.values())
    accepts_kwargs = any(parameter.kind is parameter.VAR_KEYWORD for parameter in parameters)
    parameter_names = {parameter.name for parameter in parameters}
    if accepts_kwargs:
        return method(**dict(keyword_values))

    if all(name in parameter_names for name in keyword_values):
        return method(**dict(keyword_values))

    # A fake often calls the database parameter ``db`` and makes ``actor``
    # keyword-only.  Keep the canonical positional order for the provider
    # arguments, then pass the capability by its explicit keyword.
    actor_parameter = signature.parameters.get("actor")
    positional_parameters = [
        parameter
        for parameter in parameters
        if parameter.kind
        in {parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD}
    ]
    if actor_parameter is not None and actor_parameter.kind is actor_parameter.KEYWORD_ONLY:
        return method(*positional[: len(positional_parameters)], actor=keyword_values["actor"])

    # Keyword-only provider arguments with canonical names can be filled from
    # the values we do know, even if a non-canonical optional name is present.
    named_values = {
        name: value for name, value in keyword_values.items() if name in parameter_names
    }
    if named_values and len(positional_parameters) == 0:
        return method(**named_values)

    if len(positional_parameters) >= len(positional):
        return method(*positional)
    return method(*positional[: len(positional_parameters)])


def _guarded_update(
    adapter: NotionAdapter,
    actor: AutomationActor,
    target_db: str,
    entity_id: str,
    patch: Mapping[str, Any],
) -> Any:
    enforce_write_policy(actor, target_db, patch)
    method = getattr(adapter, "update_properties", None)
    if method is None:
        raise PolicyViolation("Notion adapter has no update_properties write boundary")
    return _call_with_supported_signature(
        method,
        (target_db, entity_id, dict(patch), actor),
        {
            "target_db": target_db,
            "entity_id": entity_id,
            "patch": dict(patch),
            "actor": actor,
        },
    )


def _guarded_create(
    adapter: NotionAdapter,
    actor: AutomationActor,
    target_db: str,
    properties: Mapping[str, Any],
) -> Any:
    enforce_write_policy(actor, target_db, properties)
    method = getattr(adapter, "create_entity", None)
    if method is None:
        raise PolicyViolation("Notion adapter has no create_entity write boundary")
    return _call_with_supported_signature(
        method,
        (target_db, dict(properties), actor),
        {
            "target_db": target_db,
            "properties": dict(properties),
            "actor": actor,
        },
    )


def _call_lookup(method: Callable[..., Any], target_db: str, entity_id: str) -> Any | None:
    """Call common two-argument or one-argument fake lookup signatures."""

    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return method(target_db, entity_id)

    parameters = list(signature.parameters.values())
    positional = [
        parameter
        for parameter in parameters
        if parameter.kind
        in {parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD}
    ]
    if len(positional) >= 2:
        first = _normal_key(positional[0].name)
        second = _normal_key(positional[1].name)
        db_names = {"db", "database", "targetdb", "targetdatabase"}
        id_names = {"id", "entityid", "proposalid", "targetid"}
        if first in id_names and second in db_names:
            return method(entity_id, target_db)
        return method(target_db, entity_id)
    if "target_db" in signature.parameters and "entity_id" in signature.parameters:
        return method(target_db=target_db, entity_id=entity_id)
    if "proposal_id" in signature.parameters:
        return method(proposal_id=entity_id)
    if "entity_id" in signature.parameters:
        return method(entity_id=entity_id)
    return method(entity_id)


def _read_approval(adapter: NotionAdapter | None, proposal_id: str) -> Any | None:
    method = getattr(adapter, "read_approval", None)
    if method is not None:
        result = _call_lookup(method, AUTOMATION_QUEUE, proposal_id)
        if result is not None:
            return result
    finder = getattr(adapter, "find_entity_by_id", None)
    if finder is not None:
        return _call_lookup(finder, AUTOMATION_QUEUE, proposal_id)
    return None


def _find_target(adapter: NotionAdapter, target_db: str, entity_id: str) -> Any | None:
    method = getattr(adapter, "find_entity_by_id", None)
    if method is None:
        raise PolicyViolation("Notion adapter has no find_entity_by_id read boundary")
    return _call_lookup(method, target_db, entity_id)


def _target_database(proposal: Any, proposal_type: ProposalType) -> str:
    explicit = _get_field(
        proposal,
        "Target DB",
        "Target Database",
        "target_db",
        "target_database",
        default=None,
    )
    if explicit is None:
        target = _get_field(proposal, "Target", "target", default=None)
        explicit = _get_field(
            target,
            "Target DB",
            "Target Database",
            "Database",
            "DB",
            "target_db",
            "database",
            default=None,
        )
    expected = {
        ProposalType.MATERIAL_USAGE: "Material Usage",
        ProposalType.EXAM_SCOPE: "Exams",
        ProposalType.MATERIAL_REVISION: "Materials",
    }.get(proposal_type)
    if expected is None:
        raise PolicyViolation(
            f"Proposal type has no supported approval mutation: {proposal_type.value}"
        )
    if explicit is None:
        return expected
    if not isinstance(explicit, str) or not explicit.strip():
        raise PolicyViolation("Proposal target database is invalid")
    if _normal_key(explicit) != _normal_key(expected):
        raise PolicyViolation(
            f"Proposal target database does not match {proposal_type.value}: {explicit!r}"
        )
    return explicit


def _target_entity_id(proposal: Any, action: Mapping[str, Any]) -> str:
    proposal_target = _get_field(
        proposal,
        "Target Entity ID",
        "Target Entity",
        "target_entity_id",
        "target_id",
        default=None,
    )
    if proposal_target is None:
        target = _get_field(proposal, "Target", "target", default=None)
        proposal_target = _get_field(
            target,
            "Target Entity ID",
            "Entity ID",
            "ID",
            "entity_id",
            "id",
            default=None,
        )
    action_target = _get_field(
        action,
        "Target Entity ID",
        "Target Entity",
        "target_entity_id",
        "target_id",
        default=None,
    )
    if action_target is not None and proposal_target is not None:
        if str(action_target).strip() != str(proposal_target).strip():
            raise PolicyViolation("Proposed action targets a different entity")
    return _valid_proposal_id(action_target if action_target is not None else proposal_target)


_ACTION_CONTAINER_NAMES = (
    "patch",
    "properties",
    "mutation",
    "action",
    "set",
    "changes",
)


def _action_mapping(proposal: Any) -> Mapping[str, Any]:
    raw_action = _get_field(
        proposal,
        "Proposed Action",
        "proposed_action",
        "action",
        default=None,
    )
    if raw_action is None:
        raise PolicyViolation("Proposal has no Proposed Action")
    if isinstance(raw_action, str):
        try:
            raw_action = json.loads(raw_action)
        except (TypeError, ValueError):
            # The schema stores Proposed Action as Rich text.  Permit only
            # the narrow, deterministic scalar forms needed by the three
            # supported approval mutations; free-form prose remains denied.
            scalar_match = re.fullmatch(
                r"\s*(Verified|Scope\s+Confirmed|Current\s+Source\s+Version|"
                r"New\s+Source\s+Version)\s*(?:=|:)\s*(true|false|[0-9]+)\s*",
                raw_action,
                flags=re.IGNORECASE,
            )
            if scalar_match is None:
                raise PolicyViolation("Proposed Action must be a structured mapping")
            field_name, scalar = scalar_match.groups()
            if scalar.casefold() in {"true", "false"}:
                raw_value: Any = scalar.casefold() == "true"
            else:
                raw_value = int(scalar)
            raw_action = {field_name: raw_value}
    action = _as_mapping(raw_action)
    if action is None:
        raise PolicyViolation("Proposed Action must be a structured mapping")

    # Accept a single explicit container used by common queue serializers.
    for name in _ACTION_CONTAINER_NAMES:
        nested = _get_field(action, name, default=None)
        if nested is not None and isinstance(_as_mapping(nested), Mapping):
            nested_mapping = _as_mapping(nested)
            assert nested_mapping is not None
            if len(action) == 1 or name in {"patch", "properties", "mutation", "set", "changes"}:
                merged = dict(nested_mapping)
                for outer_key, outer_value in action.items():
                    if outer_key != name:
                        merged.setdefault(outer_key, outer_value)
                return merged
    return action


def _action_value(action: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    return _get_field(action, *names, default=default)


def _action_relation(action: Mapping[str, Any], relation: str) -> Any:
    return _action_value(
        action,
        f"{relation} ID",
        f"{relation} Id",
        relation,
        relation.casefold(),
        f"{relation.casefold()}_id",
        default=None,
    )


def _relation_ids(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value.strip()} if value.strip() else set()
    if isinstance(value, Mapping):
        for key in ("id", "ID", "entity_id", "page_id"):
            if key in value:
                return _relation_ids(value[key])
        for key in ("relation", "relations"):
            if key in value:
                return _relation_ids(value[key])
        return set()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        values: set[str] = set()
        for item in value:
            values.update(_relation_ids(item))
        return values
    return {str(value).strip()} if str(value).strip() else set()


def _validate_relation_action(action: Mapping[str, Any], target: Any) -> None:
    for relation in ("Session", "Material"):
        requested = _action_relation(action, relation)
        if requested is None:
            continue
        current = _get_field(
            target,
            relation,
            f"{relation} ID",
            f"{relation.casefold()}_id",
            default=None,
        )
        if current is None:
            raise _StaleApproval(f"Approved {relation} relation is not present on the target")
        requested_ids = _relation_ids(requested)
        current_ids = _relation_ids(current)
        if current is not None and requested_ids != current_ids:
            raise _StaleApproval(f"Approved {relation} relation no longer matches the target")


def _validate_scope_action(action: Mapping[str, Any], target: Any) -> None:
    for name in ("Included Sessions", "Session IDs", "Scope", "included_sessions"):
        requested = _action_value(action, name, default=None)
        if requested is None:
            continue
        current = _get_field(target, name, default=None)
        if current is None:
            raise _StaleApproval("Approved exam scope is not present on the target")
        requested_ids = _relation_ids(requested)
        current_ids = _relation_ids(current)
        if current is not None and requested_ids != current_ids:
            raise _StaleApproval("Approved exam scope no longer matches the target")


def _fingerprint_part(record: Any, part: str) -> Any:
    if part == "hash":
        preferred_names = ("Current Source Hash", "current_source_hash")
        fallback_names = (
            "Source Hash",
            "source_hash",
            "Fingerprint Hash",
        )
    else:
        preferred_names = ("Current Source Version", "current_source_version")
        fallback_names = (
            "Source Version",
            "source_version",
            "Fingerprint Version",
        )
    value = _get_field(record, *preferred_names, default=None)
    if value is None:
        value = _get_field(record, *fallback_names, default=None)
    if value is not None:
        return value
    fingerprint = _get_field(record, "Source Fingerprint", "source_fingerprint", default=None)
    if fingerprint is not None:
        if part == "hash":
            return _get_field(fingerprint, "Source Hash", "source_hash", "hash", default=None)
        return _get_field(
            fingerprint,
            "Source Version",
            "source_version",
            "version",
            default=None,
        )
    return None


def _proposal_fingerprint(proposal: Any) -> tuple[Any, Any]:
    fingerprint = _get_field(proposal, "Source Fingerprint", "source_fingerprint", default=None)
    source_hash = _get_field(proposal, "Source Hash", "source_hash", default=None)
    source_version = _get_field(proposal, "Source Version", "source_version", default=None)
    if fingerprint is not None:
        if source_hash is None:
            source_hash = _get_field(
                fingerprint,
                "Source Hash",
                "source_hash",
                "hash",
                default=None,
            )
        if source_version is None:
            source_version = _get_field(
                fingerprint,
                "Source Version",
                "source_version",
                "version",
                default=None,
            )
    return source_hash, source_version


def _optional_current_fingerprint(
    adapter: NotionAdapter,
    target_db: str,
    target_id: str,
    target: Any,
) -> tuple[Any, Any]:
    source_hash = _fingerprint_part(target, "hash")
    source_version = _fingerprint_part(target, "version")
    if source_hash is not None or source_version is not None:
        return source_hash, source_version

    for method_name in (
        "read_source_fingerprint",
        "get_source_fingerprint",
        "source_fingerprint",
        "read_current_source_fingerprint",
        "get_current_source_fingerprint",
    ):
        method = getattr(adapter, method_name, None)
        if method is None:
            continue
        try:
            result = method(target_db, target_id)
        except TypeError:
            result = method(target_id)
        return _fingerprint_part(result, "hash"), _fingerprint_part(result, "version")
    return None, None


def _revalidate_fingerprint(
    adapter: NotionAdapter,
    proposal: Any,
    target_db: str,
    target_id: str,
    target: Any,
) -> tuple[bool, str | None]:
    expected_hash, expected_version = _proposal_fingerprint(proposal)
    if expected_hash is None and expected_version is None:
        return True, None

    current_hash, current_version = _optional_current_fingerprint(
        adapter, target_db, target_id, target
    )
    if expected_hash is not None:
        if current_hash is None or str(current_hash) != str(expected_hash):
            return False, "source hash changed or could not be revalidated"
    if expected_version is not None:
        if current_version is None:
            return False, "source version changed or could not be revalidated"
        try:
            if int(current_version) != int(expected_version):
                return False, "source version changed"
        except (TypeError, ValueError):
            if current_version != expected_version:
                return False, "source version changed"
    return True, None


def _target_snapshot_matches(proposal: Any, target: Any) -> tuple[bool, str | None]:
    expected = _get_field(
        proposal,
        "Target Fingerprint",
        "Target Snapshot",
        "target_fingerprint",
        "target_snapshot",
        default=None,
    )
    if expected is None:
        expected_version = _get_field(
            proposal,
            "Target Version",
            "target_version",
            "Target Entity Version",
            "target_entity_version",
            default=None,
        )
        if expected_version is None:
            return True, None
        current_version = _get_field(
            target,
            "Version",
            "Target Version",
            "Entity Version",
            "version",
            default=None,
        )
        if current_version != expected_version:
            return False, "target version changed"
        return True, None
    expected_mapping = _as_mapping(expected)
    if expected_mapping is None:
        raise PolicyViolation("Target fingerprint must be a structured mapping")
    for field_name in ("Version", "Last Edited", "Last Edited Time", "Updated"):
        wanted = _get_field(expected_mapping, field_name, default=None)
        if wanted is None:
            continue
        current = _get_field(target, field_name, default=None)
        if current != wanted:
            return False, f"target {field_name} changed"
    return True, None


def _stale_error_patch(reason: str, state: QueueState = QueueState.SUPERSEDED) -> dict[str, Any]:
    return {"State": state.value, "Last Error": reason}


@dataclass(frozen=True)
class ApprovalApplyResult:
    """Small, provider-neutral result returned by ``HumanApprovalApplier``."""

    proposal_id: str
    state: QueueState
    mutated: bool
    reason: str | None = None


@dataclass(frozen=True)
class Proposal:
    """Convenience value object for callers that do not use raw Notion maps.

    The applier also accepts flat dictionaries and Notion-like records, so
    this class is optional.  It exists to make the lifecycle contract easy to
    exercise without a provider SDK.
    """

    proposal_id: str
    proposal_type: ProposalType
    target_entity_id: str
    proposed_action: Mapping[str, Any]
    state: QueueState = QueueState.PENDING_REVIEW
    decision: Decision = Decision.Pending
    target_db: str | None = None
    source_hash: str | None = None
    source_version: int | None = None
    decision_by: str | None = None
    decision_at: str | None = None
    applied_at: str | None = None
    target_fingerprint: Mapping[str, Any] | None = None

    def as_properties(self) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "Proposal ID": self.proposal_id,
            "Proposal Type": self.proposal_type.value,
            "State": self.state.value,
            "Decision": self.decision.value,
            "Target Entity ID": self.target_entity_id,
            "Proposed Action": dict(self.proposed_action),
        }
        if self.target_db is not None:
            properties["Target DB"] = self.target_db
        if self.source_hash is not None:
            properties["Source Hash"] = self.source_hash
        if self.source_version is not None:
            properties["Source Version"] = self.source_version
        if self.decision_by is not None:
            properties["Decision By"] = self.decision_by
        if self.decision_at is not None:
            properties["Decision At"] = self.decision_at
        if self.applied_at is not None:
            properties["Applied At"] = self.applied_at
        if self.target_fingerprint is not None:
            properties["Target Fingerprint"] = dict(self.target_fingerprint)
        return properties


AutomationQueueProposal = Proposal
ApprovalProposal = Proposal


class ApprovalReader:
    """Internal capability that derives queue state from human Decision only."""

    _actor = AutomationActor.APPROVAL_READER

    def __init__(self, adapter: NotionAdapter | None = None) -> None:
        self._adapter = adapter

    @staticmethod
    def derive_state(decision: Decision) -> QueueState:
        return derive_queue_state(decision)

    def read(self, proposal_id: Any) -> QueueState:
        """Read a current human decision and return its derived state."""

        proposal_id = _requested_proposal_id(proposal_id)
        record = _read_approval(self._adapter, proposal_id)
        if record is None:
            raise PolicyViolation(f"Approval proposal not found: {proposal_id}")
        decision_value = _decision_field(record)
        if decision_value is None:
            raise PolicyViolation("Approval record has no human Decision")
        return self.derive_state(coerce_decision(decision_value))

    def sync_state(self, proposal_id: Any) -> QueueState:
        """Derive and persist State without exposing any Decision mutation."""

        if self._adapter is None:
            raise PolicyViolation("ApprovalReader requires a Notion adapter to sync State")
        proposal_id = _requested_proposal_id(proposal_id)
        record = _read_approval(self._adapter, proposal_id)
        if record is None:
            raise PolicyViolation(f"Approval proposal not found: {proposal_id}")
        decision_value = _decision_field(record)
        if decision_value is None:
            raise PolicyViolation("Approval record has no human Decision")
        state = self.derive_state(coerce_decision(decision_value))
        current_value = _get_field(record, "State", "state", default=None)
        if current_value is not None:
            current_state = coerce_queue_state(current_value)
            if current_state is state:
                return state
            # Reader derivation may not regress a later terminal outcome or
            # promote a rejected item after its human decision was changed.
            if current_state in {
                QueueState.REJECTED,
                QueueState.APPLIED,
                QueueState.FAILED,
                QueueState.SUPERSEDED,
            }:
                raise PolicyViolation(
                    f"ApprovalReader cannot regress terminal state {current_state.value}"
                )
            transition_queue_state(
                current_state,
                state,
                decision=coerce_decision(decision_value),
                actor=self._actor,
            )
        _guarded_update(
            self._adapter,
            self._actor,
            AUTOMATION_QUEUE,
            proposal_id,
            {"State": state.value},
        )
        return state

    # Descriptive aliases make the internal lifecycle operation discoverable
    # without adding a method that can write the human-owned Decision field.
    derive_and_sync = sync_state
    update_state = sync_state
    sync = sync_state
    read_approval = read


class HumanApprovalApplier:
    """Apply an exact, currently approved proposal through human authority.

    The actor is fixed internally to ``HUMAN_APPROVAL_APPLIER``.  Callers can
    provide reviewer metadata, but cannot select this capability by passing a
    string such as ``"HUMAN_APPROVAL_APPLIER"``.
    """

    _actor = AutomationActor.HUMAN_APPROVAL_APPLIER

    def __init__(
        self,
        adapter: NotionAdapter,
        decision_by: str | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if callable(decision_by) and clock is None:
            # A small convenience for tests that pass a clock as the second
            # positional argument; it does not introduce an actor selector.
            clock = decision_by  # type: ignore[assignment]
            decision_by = None
        self._adapter = adapter
        self._decision_by = decision_by.strip() if isinstance(decision_by, str) else decision_by
        if self._decision_by == "":
            raise PolicyViolation("decision_by must not be empty")
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _timestamp(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime):
            raise PolicyViolation("approval clock must return datetime")
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    def _read_current(self, supplied: Any) -> tuple[str, Any]:
        if isinstance(supplied, str):
            proposal_id = _valid_proposal_id(supplied)
        else:
            proposal_id = _proposal_id(supplied)
        current = _read_approval(self._adapter, proposal_id)
        if current is None:
            raise PolicyViolation(f"Current approval proposal not found: {proposal_id}")
        current_id = _proposal_id(current)
        if current_id != proposal_id:
            raise PolicyViolation("Approval record Proposal ID does not match the requested ID")
        _assert_supplied_matches_current(supplied, current)
        return proposal_id, _merge_records(supplied, current)

    def _mark_terminal(
        self,
        proposal_id: str,
        state: QueueState,
        reason: str,
    ) -> ApprovalApplyResult:
        patch = _stale_error_patch(reason, state)
        _guarded_update(self._adapter, self._actor, AUTOMATION_QUEUE, proposal_id, patch)
        return ApprovalApplyResult(proposal_id, state, False, reason)

    def _validate_material_usage_action(
        self,
        action: Mapping[str, Any],
        target: Any,
    ) -> dict[str, Any]:
        _validate_relation_action(action, target)
        verified = _action_value(action, "Verified", "verified", default=None)
        if verified is None:
            field = _action_value(action, "field", "Field", default=None)
            value = _action_value(action, "value", "Value", default=None)
            if _normal_key(str(field)) == _normal_key("Verified"):
                verified = value
            operation = _normal_key(
                str(_action_value(action, "operation", "Operation", default=""))
            )
            if operation in {"verify", "setverified", "markverified"}:
                verified = value if value is not None else True
        if verified is not True:
            raise PolicyViolation("MATERIAL_USAGE approval must explicitly set Verified=true")
        return {"Verified": True}

    def _validate_exam_scope_action(self, action: Mapping[str, Any], target: Any) -> dict[str, Any]:
        _validate_scope_action(action, target)
        confirmed = _action_value(action, "Scope Confirmed", "scope_confirmed", default=None)
        if confirmed is None:
            field = _action_value(action, "field", "Field", default=None)
            value = _action_value(action, "value", "Value", default=None)
            if _normal_key(str(field)) == _normal_key("Scope Confirmed"):
                confirmed = value
            operation = _normal_key(
                str(_action_value(action, "operation", "Operation", default=""))
            )
            if operation in {"confirmscope", "setscopeconfirmed", "markscopeconfirmed"}:
                confirmed = value if value is not None else True
        if confirmed is not True:
            raise PolicyViolation("EXAM_SCOPE approval must explicitly set Scope Confirmed=true")
        return {"Scope Confirmed": True}

    def _validate_material_revision_action(
        self,
        proposal: Any,
        action: Mapping[str, Any],
        target: Any,
    ) -> dict[str, Any]:
        del target  # The target existence/fingerprint checks happen before this call.
        new_version = _action_value(
            action,
            "New Source Version",
            "new_source_version",
            "Bind Source Version",
            "bind_source_version",
            "Current Source Version",
            "current_source_version",
            "Source Version",
            "source_version",
            default=None,
        )
        if new_version is None:
            operation = _normal_key(
                str(_action_value(action, "operation", "Operation", default=""))
            )
            if operation in {"bindsourceversion", "setcurrentsourceversion"}:
                new_version = _action_value(action, "value", "Value", default=None)
        if new_version is None:
            new_version = _get_field(
                proposal,
                "New Source Version",
                "new_source_version",
                "bind_source_version",
                default=None,
            )
        if isinstance(new_version, bool) or not isinstance(new_version, int) or new_version < 1:
            raise PolicyViolation("MATERIAL_REVISION approval needs a positive new source version")
        return {"Current Source Version": new_version}

    def _validate_action(
        self,
        proposal: Any,
        proposal_type: ProposalType,
        action: Mapping[str, Any],
        target: Any,
    ) -> dict[str, Any]:
        target_id = _target_entity_id(proposal, action)
        target_record_id = _get_field(target, "ID", "Entity ID", "entity_id", default=None)
        if target_record_id is not None and str(target_record_id).strip() != target_id:
            raise PolicyViolation("Approval target record does not match Proposal target entity")

        if proposal_type is ProposalType.MATERIAL_USAGE:
            return self._validate_material_usage_action(action, target)
        if proposal_type is ProposalType.EXAM_SCOPE:
            return self._validate_exam_scope_action(action, target)
        if proposal_type is ProposalType.MATERIAL_REVISION:
            return self._validate_material_revision_action(proposal, action, target)
        raise PolicyViolation(
            f"No human-authoritative mutation is defined for {proposal_type.value}"
        )

    def apply(self, proposal: Any) -> ApprovalApplyResult:
        """Revalidate and apply a current approved proposal exactly once."""

        proposal_id, current = self._read_current(proposal)
        state_value = _get_field(current, "State", "state", default=None)
        decision_value = _decision_field(current)
        if state_value is None or decision_value is None:
            raise PolicyViolation("Approval proposal must contain State and Decision")
        state = coerce_queue_state(state_value)
        decision = coerce_decision(decision_value)

        # APPLIED is a terminal idempotent replay.  Do this before target
        # lookup so replay cannot duplicate a provider mutation.
        if state is QueueState.APPLIED:
            return ApprovalApplyResult(proposal_id, QueueState.APPLIED, False, "already applied")

        if state in {QueueState.REJECTED, QueueState.SUPERSEDED, QueueState.FAILED}:
            raise PolicyViolation(f"Proposal {proposal_id} is terminal: {state.value}")
        if state is not QueueState.APPROVED or decision is not Decision.Approve:
            raise PolicyViolation(
                "HumanApprovalApplier requires State=APPROVED and Decision=Approve"
            )

        proposal_type_value = _get_field(current, "Proposal Type", "proposal_type", default=None)
        proposal_type = coerce_proposal_type(proposal_type_value)
        target_db = _target_database(current, proposal_type)
        target_id = _target_entity_id(current, _action_mapping(current))
        target = _find_target(self._adapter, target_db, target_id)
        if target is None:
            return self._mark_terminal(
                proposal_id,
                QueueState.SUPERSEDED,
                "approval target was deleted",
            )

        fresh, reason = _revalidate_fingerprint(
            self._adapter,
            current,
            target_db,
            target_id,
            target,
        )
        if not fresh:
            assert reason is not None
            return self._mark_terminal(proposal_id, QueueState.SUPERSEDED, reason)

        target_fresh, target_reason = _target_snapshot_matches(current, target)
        if not target_fresh:
            assert target_reason is not None
            return self._mark_terminal(proposal_id, QueueState.SUPERSEDED, target_reason)

        action = _action_mapping(current)
        try:
            patch = self._validate_action(current, proposal_type, action, target)
        except _StaleApproval as exc:
            return self._mark_terminal(proposal_id, QueueState.SUPERSEDED, str(exc))

        # The only target mutation is the exact, type-specific patch returned
        # by _validate_action.  No caller-supplied actor or arbitrary patch is
        # accepted here.
        try:
            _guarded_update(self._adapter, self._actor, target_db, target_id, patch)
        except Exception as exc:
            return self._mark_terminal(
                proposal_id,
                QueueState.FAILED,
                f"approval application failed: {type(exc).__name__}: {exc}",
            )

        now = self._timestamp()
        decision_by = _get_field(current, "Decision By", "decision_by", default=None)
        if decision_by is None:
            decision_by = self._decision_by or "HUMAN_APPROVAL_APPLIER"
        decision_at = _get_field(current, "Decision At", "decision_at", default=None) or now
        queue_patch = {
            "Decision By": decision_by,
            "Decision At": decision_at,
            "Applied At": now,
            "State": QueueState.APPLIED.value,
        }
        try:
            _guarded_update(self._adapter, self._actor, AUTOMATION_QUEUE, proposal_id, queue_patch)
        except Exception as exc:
            return self._mark_terminal(
                proposal_id,
                QueueState.FAILED,
                f"approval audit update failed: {type(exc).__name__}: {exc}",
            )
        return ApprovalApplyResult(proposal_id, QueueState.APPLIED, True, None)


def upsert_proposal(
    adapter: NotionAdapter,
    properties: Mapping[str, Any] | Proposal,
) -> Any:
    """Create or update one automation proposal without touching human fields.

    Proposal identity is supplied by the producer.  A retry reads the current
    queue item by that identity and updates only metadata, so a human Decision
    cannot be overwritten by an automation retry.  This is the ordinary
    automation path and therefore has no actor parameter.
    """

    proposal_properties: Mapping[str, Any]
    if isinstance(properties, Proposal):
        proposal_properties = properties.as_properties()
    elif isinstance(properties, Mapping):
        proposal_properties = properties
    else:
        raise PolicyViolation("proposal properties must be a mapping")
    proposal_id = _proposal_id(proposal_properties)
    patch = dict(proposal_properties)
    patch.setdefault("Decision", Decision.Pending.value)
    patch.setdefault("State", QueueState.PENDING_REVIEW.value)
    # This guard rejects Approve/Reject and approval states before any lookup
    # or provider call, which is the self-approval invariant at the boundary.
    enforce_write_policy(AutomationActor.AUTOMATION, AUTOMATION_QUEUE, patch)
    if _wire_value(patch["Decision"]) != Decision.Pending.value:
        raise PolicyViolation("A new/upserted automation proposal must have Decision=Pending")
    if _wire_value(patch["State"]) != QueueState.PENDING_REVIEW.value:
        raise PolicyViolation("A new/upserted automation proposal must have State=PENDING_REVIEW")

    existing = _read_approval(adapter, proposal_id)
    if existing is not None:
        # Never write human-owned decision or decision audit fields during a
        # retry.  State=Pending is harmless only when the item is still in its
        # initial state; otherwise it is omitted rather than regressed.
        current_state_value = _get_field(existing, "State", "state", default=None)
        metadata = {
            key: value
            for key, value in patch.items()
            if _normal_key(str(key))
            not in {
                _normal_key("Decision"),
                _normal_key("State"),
                _normal_key("Decision By"),
                _normal_key("Decision At"),
                _normal_key("Applied At"),
            }
        }
        is_pending = (
            current_state_value is None
            or coerce_queue_state(current_state_value) is QueueState.PENDING_REVIEW
        )
        if is_pending:
            metadata["State"] = QueueState.PENDING_REVIEW.value
        if _decision_field(existing) is None:
            metadata["Decision"] = Decision.Pending.value
        if metadata:
            _guarded_update(
                adapter,
                AutomationActor.AUTOMATION,
                AUTOMATION_QUEUE,
                proposal_id,
                metadata,
            )
        return existing

    return _guarded_create(adapter, AutomationActor.AUTOMATION, AUTOMATION_QUEUE, patch)


create_or_update_proposal = upsert_proposal
upsert_automation_proposal = upsert_proposal


__all__ = [
    "AUTOMATION_QUEUE",
    "AUTOMATION_QUEUE_DATABASE",
    "AUTOMATION_QUEUE_DB",
    "ApprovalApplyResult",
    "ApprovalProposal",
    "ApprovalReader",
    "AutomationActor",
    "AutomationQueueProposal",
    "Decision",
    "HumanApprovalApplier",
    "NotionAdapter",
    "PolicyViolation",
    "Proposal",
    "ProposalType",
    "QueueState",
    "aliases_match",
    "coerce_decision",
    "coerce_proposal_type",
    "coerce_queue_state",
    "derive_queue_state",
    "derive_state_from_decision",
    "enforce_write_policy",
    "find_alias_matches",
    "normalize_alias",
    "parse_aliases",
    "transition_queue_state",
    "transition_state",
    "create_or_update_proposal",
    "upsert_proposal",
    "upsert_automation_proposal",
]
