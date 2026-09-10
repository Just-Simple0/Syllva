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
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from uls.domain.enums import AutomationActor
from uls.domain.errors import (
    PolicyViolation,
    ProviderRateLimitedError,
    ProviderUnavailableError,
    ProviderWriteNotAppliedError,
    UlsError,
)
from uls.domain.ids import strict_entity_id
from uls.enrichment.schemas import EnrichmentRecord

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
        system_transition: bool = False,
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


@runtime_checkable
class NotionReader(Protocol):
    """Narrow read-only graph surface used by ``RetrievalEngine``.

    This protocol intentionally does not inherit from ``NotionAdapter``.  The
    latter is the worker/write contract and includes guarded mutation methods;
    retrieval is typed against this smaller surface so write capabilities are
    not part of its dependency boundary.
    """

    def get_session(self, entity_id: str) -> Any | None:
        ...

    def find_sessions_by_alias(self, course: Any, alias_norm: str) -> list[Any]:
        ...

    def list_course_sessions(self, course: Any) -> list[Any]:
        ...

    def get_material_usage(self, session_id: str) -> list[Any]:
        ...

    def get_session_enrichment(self, entity_id: str) -> EnrichmentRecord | None:
        ...

    def get_course_by_alias(self, alias_norm: str) -> Any | None:
        ...

    def get_course_by_relation_id(self, relation_page_id: str) -> Any | None:
        """Dereference exactly one Course relation page ID."""
        ...

    def get_material(self, material_id: str) -> Any | None:
        """Return one normalized Material record by its canonical ID."""
        ...

    if TYPE_CHECKING:
        def get_material_enrichment(self, material_id: str) -> EnrichmentRecord | None:
            """Optional static extension; runtime support uses MaterialEnrichmentReader."""
            ...

    def get_session_user_annotations(self, session_id: str) -> list[Any]:
        """Return metadata for USER annotations related to one Session."""
        ...


@runtime_checkable
class ApprovalGraphReader(Protocol):
    """Read-only graph dependency used by the Phase4 approval applier."""

    def get_material_usage(self, session_id: str) -> list[Any]:
        ...

    def get_session(self, session_id: str) -> Any | None:
        ...

    def get_material(self, material_id: str) -> Any | None:
        ...

    def get_course_by_relation_id(self, relation_page_id: str) -> Any | None:
        ...


@runtime_checkable
class ApprovalSourceReader(Protocol):
    """Read-only canonical derivative/fingerprint dependency for Phase4."""

    def read_derived(self, source_ref: Any) -> Any:
        ...

    def get_current_fingerprint(self, source_ref: Any) -> Any:
        ...


@runtime_checkable
class MaterialEnrichmentReader(Protocol):
    """Optional Phase 3 read capability for fingerprinted Material enrichment.

    ``NotionReader`` remains runtime-compatible with the exact Phase 2 reader
    surface, whose contract tests intentionally use a minimal proxy.  Worker
    and Phase 3 readers that expose Material enrichment additionally satisfy
    this extension protocol; RetrievalEngine discovers the method
    capability-wise just as it does other optional annotation reads.
    """

    def get_material_enrichment(self, material_id: str) -> EnrichmentRecord | None:
        ...


# Optional Material enrichment remains available to static callers above and
# is checked separately through MaterialEnrichmentReader at runtime. Avoid
# typing.Protocol internals, which differ across supported Python versions.


def _wire_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


_PATCH_MISSING = object()

# ``Last Error`` is an existing Queue property.  Phase4 reserves one explicit
# namespace inside it for the durable target-application marker; ordinary
# Queue errors remain plain text and are never interpreted as this marker.
_PHASE4_APPLY_MARKER_PREFIX = "ULS_PHASE4_APPLY_MARKER/v1:"
_PHASE4_APPLY_MARKER_KIND = "phase4_apply"
_PHASE4_APPLY_MARKER_PHASE_PREPARED = "prepared"
_PHASE4_APPLY_MARKER_PHASE_EFFECT_OBSERVED = "effect_observed"
_PHASE4_APPLY_MARKER_PHASES = frozenset(
    {
        _PHASE4_APPLY_MARKER_PHASE_PREPARED,
        _PHASE4_APPLY_MARKER_PHASE_EFFECT_OBSERVED,
    }
)
_PHASE4_APPLY_MARKER_KEYS = frozenset(
    {"kind", "phase", "proposal_id", "proposal_type", "semantics", "target_patch"}
)


def _patch_field(patch: Mapping[str, Any], name: str, default: Any = _PATCH_MISSING) -> Any:
    """Read a policy field while tolerating provider spelling variants."""

    wanted = "".join(character for character in name.casefold() if character.isalnum())
    for key, value in patch.items():
        if isinstance(key, str):
            normalized = "".join(character for character in key.casefold() if character.isalnum())
            if normalized == wanted:
                return value
    return default


def _is_truthy_set(value: Any) -> bool:
    """Return whether a wire value attempts to set a boolean field true.

    Notion/provider serializers sometimes turn checkboxes into integers or
    strings.  Human-only guards must not rely on ``value is True`` because
    that would make ``1`` and ``"true"`` policy bypasses.
    """

    if isinstance(value, str):
        return value.strip().casefold() in {"true", "1", "yes", "y", "on", "t"}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    return value is True or bool(value)


def _normalize_queue_ids(automation_queue_ids: set[str] | None) -> frozenset[str] | None:
    if automation_queue_ids is None:
        return None
    if isinstance(automation_queue_ids, str):
        value = automation_queue_ids.strip()
        values = {value} if value else set()
    else:
        values = {
            value.strip()
            for value in automation_queue_ids
            if isinstance(value, str) and value.strip()
        }
    return frozenset(values)


def _queue_target(
    automation_queue_id: str | None = None,
    automation_queue_ids: set[str] | None = None,
) -> tuple[str, frozenset[str] | None]:
    """Choose the configured queue target and the complete identification set."""

    identifiers = set(_normalize_queue_ids(automation_queue_ids) or ())
    if automation_queue_id is not None:
        if not isinstance(automation_queue_id, str) or not automation_queue_id.strip():
            raise PolicyViolation("automation queue identifier must be a non-empty string")
        automation_queue_id = automation_queue_id.strip()
        identifiers.add(automation_queue_id)
    if automation_queue_id is not None:
        target = automation_queue_id
    elif len(identifiers) == 1:
        target = next(iter(identifiers))
    else:
        target = AUTOMATION_QUEUE
    return target, (frozenset(identifiers) if identifiers else None)


def _is_automation_queue(
    target_db: str,
    automation_queue_ids: set[str] | None = None,
) -> bool:
    """Identify the queue by configured IDs, with a legacy name fallback."""

    if not isinstance(target_db, str):
        return False
    configured_ids = _normalize_queue_ids(automation_queue_ids)
    if configured_ids is not None and target_db in configured_ids:
        return True
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
    *,
    automation_queue_ids: set[str] | None = None,
    is_create: bool = False,
    system_transition: bool = False,
) -> None:
    """Enforce the v1.2 human-gate policy at the write boundary.

    The function is intentionally small and side-effect free so concrete
    adapters can call it immediately before making a provider mutation.
    ``AUTOMATION`` can create/update proposal metadata, and may write
    ``SUPERSEDED``/``FAILED`` only through the explicit internal system
    transition path.  It cannot manufacture a human decision or an approval
    state.
    """

    actor = _coerce_actor(actor)
    if type(system_transition) is not bool:
        raise PolicyViolation("system_transition must be a boolean")
    if system_transition and actor is not AutomationActor.AUTOMATION:
        raise PolicyViolation("system_transition is reserved for the automation system path")
    if not isinstance(patch, Mapping):
        raise PolicyViolation("write patch must be a mapping")

    last_error = _patch_field(patch, "Last Error")
    if last_error is not _PATCH_MISSING:
        if not _is_automation_queue(target_db, automation_queue_ids):
            raise PolicyViolation("Queue Last Error is not valid on this database")
        if actor is AutomationActor.AUTOMATION and not system_transition:
            raise PolicyViolation("Automation cannot write the reserved Queue Last Error")
        if actor is AutomationActor.APPROVAL_READER:
            raise PolicyViolation("ApprovalReader cannot write Queue Last Error")
        if (
            actor is AutomationActor.AUTOMATION
            and isinstance(last_error, str)
            and _PHASE4_APPLY_MARKER_PREFIX in last_error
        ):
            raise PolicyViolation("Automation cannot forge a Phase4 apply marker")
        if (
            actor is AutomationActor.HUMAN_APPROVAL_APPLIER
            and last_error is not None
            and not isinstance(last_error, str)
        ):
            raise PolicyViolation("Queue Last Error must be text or null")

    audit_fields = {
        _normal_key("Decision By"),
        _normal_key("Decision At"),
        _normal_key("Applied At"),
    }
    if any(_normal_key(str(key)) in audit_fields for key in patch):
        if actor is not AutomationActor.HUMAN_APPROVAL_APPLIER:
            raise PolicyViolation("Queue audit fields are applier-only")
        if not _is_automation_queue(target_db, automation_queue_ids):
            raise PolicyViolation("Queue audit fields require the Automation Queue")

    verified = _patch_field(patch, "Verified")
    if verified is not _PATCH_MISSING and _is_truthy_set(verified) and actor is not AutomationActor.HUMAN_APPROVAL_APPLIER:
        raise PolicyViolation("Material Usage.Verified=true is human-only")

    scope_confirmed = _patch_field(patch, "Scope Confirmed")
    if (
        scope_confirmed is not _PATCH_MISSING
        and _is_truthy_set(scope_confirmed)
        and actor is not AutomationActor.HUMAN_APPROVAL_APPLIER
    ):
        raise PolicyViolation("Exam.Scope Confirmed=true is human-only")

    # Decision is human-owned for every internal capability.  The applier
    # records Decision By/At but never changes Decision itself; ApprovalReader
    # may only read it and derive State.
    decision = _patch_field(patch, "Decision")
    state = _patch_field(patch, "State")
    if decision is not _PATCH_MISSING and actor is not AutomationActor.AUTOMATION:
        raise PolicyViolation("Decision is human-owned and cannot be changed by this capability")

    is_queue = _is_automation_queue(target_db, automation_queue_ids)
    if is_queue and any(_normal_key(str(key)) in {"created", "updated"} for key in patch):
        raise PolicyViolation("Queue Created/Updated timestamps are provider-owned")
    if not is_queue:
        # A UUID/name mismatch must fail closed.  Otherwise a queue UUID would
        # look like an ordinary database and permit Decision/State writes.
        if decision is not _PATCH_MISSING or state is not _PATCH_MISSING:
            raise PolicyViolation(
                "Automation Queue identity is required for Decision/State writes"
            )
        if actor is AutomationActor.AUTOMATION and not is_create:
            usage_identity_fields = {
                _normal_key(name)
                for name in (
                    "ID",
                    "Session",
                    "Material",
                    "Material ID",
                    "Role",
                    "Start Page",
                    "End Page",
                    "Verified",
                )
            }
            normalized_target = _normal_key(target_db)
            if normalized_target in {"materialusage", "materialusages"} and any(
                _normal_key(str(key)) in usage_identity_fields for key in patch
            ):
                raise PolicyViolation(
                    "AUTOMATION cannot mutate Material Usage identity or verification fields"
                )
        return

    decision = _wire_value(decision) if decision is not _PATCH_MISSING else _PATCH_MISSING
    state = _wire_value(state) if state is not _PATCH_MISSING else _PATCH_MISSING
    if actor is AutomationActor.AUTOMATION:
        if is_create:
            if system_transition:
                raise PolicyViolation("system transitions are update-only")
            if decision is not _PATCH_MISSING and decision != Decision.Pending.value:
                raise PolicyViolation("Automation Queue Decision is human-owned")
            if state is not _PATCH_MISSING and state != QueueState.PENDING_REVIEW.value:
                raise PolicyViolation(f"Automation Queue state is not creatable: {state!r}")
        else:
            # A retry/upsert must never write even Pending back into an
            # existing row: Decision is immutable to automation on update.
            if decision is not _PATCH_MISSING:
                raise PolicyViolation("Automation Queue Decision cannot be written on update")
            immutable_queue_fields = {
                _normal_key(name)
                for name in (
                    "Proposal ID",
                    "Proposal Type",
                    "Course",
                    "Target Entity ID",
                    "Source Ref",
                    "Source Hash",
                    "Source Version",
                    "Proposed Action",
                    "Evidence",
                    "Review Reason",
                )
            }
            if any(_normal_key(str(key)) in immutable_queue_fields for key in patch):
                raise PolicyViolation(
                    "Automation Queue approval semantics are immutable after creation"
                )
            if system_transition:
                if state not in {
                    QueueState.SUPERSEDED.value,
                    QueueState.FAILED.value,
                }:
                    raise PolicyViolation(
                        "Automation system transitions may only set SUPERSEDED or FAILED"
                    )
                last_error = _patch_field(patch, "Last Error")
                if last_error is _PATCH_MISSING or not isinstance(last_error, str) or not last_error.strip():
                    raise PolicyViolation(
                        "Automation system transitions require a non-empty Last Error"
                    )
            elif state is not _PATCH_MISSING:
                raise PolicyViolation("Automation Queue State can only be set on creation or by an internal path")
    elif actor is AutomationActor.APPROVAL_READER:
        if any(_normal_key(str(key)) != "state" for key in patch):
            raise PolicyViolation("ApprovalReader may only write derived State")
        if any(
            _normal_key(str(key))
            in {
                _normal_key("Decision By"),
                _normal_key("Decision At"),
                _normal_key("Applied At"),
            }
            for key in patch
        ):
            raise PolicyViolation("ApprovalReader cannot write Queue audit fields")
        if state is not _PATCH_MISSING and state not in {
            QueueState.PENDING_REVIEW.value,
            QueueState.APPROVED.value,
            QueueState.REJECTED.value,
        }:
            raise PolicyViolation("ApprovalReader may only derive review state")
    elif actor is AutomationActor.HUMAN_APPROVAL_APPLIER:
        if any(_normal_key(str(key)) not in {
            "state", "decisionby", "decisionat", "appliedat", "lasterror"
        } for key in patch):
            raise PolicyViolation("HumanApprovalApplier may only write Queue outcome and audit")
        if decision is not _PATCH_MISSING:
            raise PolicyViolation("HumanApprovalApplier cannot mutate Decision")
        if state is not _PATCH_MISSING and state not in {
            QueueState.APPLIED.value,
            QueueState.SUPERSEDED.value,
            QueueState.FAILED.value,
        }:
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


def _phase4_apply_marker_text(
    proposal_id: str,
    proposal_type: str,
    semantics: Mapping[str, Any],
    target_patch: Mapping[str, Any],
    *,
    phase: str = _PHASE4_APPLY_MARKER_PHASE_PREPARED,
) -> str:
    """Serialize one durable Phase4 target-application marker phase.

    The marker intentionally stores the complete canonical approval semantics
    as well as the exact target patch.  ``prepared`` records only that the
    target write was armed.  ``effect_observed`` is reserved for a marker
    written after target invocation and exact desired read-back.
    """

    from uls.domain.approval_identity import canonical_action_json

    if not isinstance(phase, str) or phase not in _PHASE4_APPLY_MARKER_PHASES:
        raise PolicyViolation("Phase4 apply marker phase is invalid")
    payload = {
        "kind": _PHASE4_APPLY_MARKER_KIND,
        "phase": phase,
        "proposal_id": _valid_proposal_id(proposal_id),
        "proposal_type": proposal_type,
        "semantics": json.loads(canonical_action_json(semantics)),
        "target_patch": dict(target_patch),
    }
    return _PHASE4_APPLY_MARKER_PREFIX + json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _phase4_apply_marker_phase(
    record: Any,
    *,
    proposal_id: str,
    proposal_type: str,
    semantics: Mapping[str, Any],
    target_patch: Mapping[str, Any],
) -> str | None:
    """Validate the reserved Queue marker and compare its full binding.

    ``None`` means that the Queue has no marker.  Any non-empty ``Last Error``
    value in the reserved namespace that is malformed, phase-less, unknown,
    or bound to a different proposal is an integrity failure and raises
    instead of being treated as a virgin proposal.
    """

    raw = _get_field(record, "Last Error", "last_error", default=None)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if not isinstance(raw, str):
        raise PolicyViolation("Phase4 Queue Last Error is malformed")
    if not raw.startswith(_PHASE4_APPLY_MARKER_PREFIX):
        # An unresolved error from an earlier Phase4 attempt is not proof that
        # the target was never touched.  Keep the applier fail-closed.
        raise PolicyViolation("Phase4 Queue has an unrecognized Last Error state")

    from uls.domain.approval_identity import parse_action_json

    encoded = raw[len(_PHASE4_APPLY_MARKER_PREFIX) :]
    try:
        payload = parse_action_json(encoded)
    except (TypeError, ValueError) as exc:
        raise PolicyViolation("Phase4 apply marker is malformed") from exc
    if set(payload) != _PHASE4_APPLY_MARKER_KEYS:
        raise PolicyViolation("Phase4 apply marker has an invalid shape")
    if payload.get("kind") != _PHASE4_APPLY_MARKER_KIND:
        raise PolicyViolation("Phase4 apply marker kind is invalid")
    phase = payload.get("phase")
    if not isinstance(phase, str) or phase not in _PHASE4_APPLY_MARKER_PHASES:
        raise PolicyViolation("Phase4 apply marker phase is invalid")
    marker_proposal_id = payload.get("proposal_id")
    marker_type = payload.get("proposal_type")
    marker_semantics = payload.get("semantics")
    marker_patch = payload.get("target_patch")
    if (
        not isinstance(marker_proposal_id, str)
        or not marker_proposal_id.strip()
        or not isinstance(marker_type, str)
        or not marker_type.strip()
        or not isinstance(marker_semantics, Mapping)
        or not isinstance(marker_patch, Mapping)
    ):
        raise PolicyViolation("Phase4 apply marker fields are malformed")
    if (
        marker_proposal_id != proposal_id
        or marker_type != proposal_type
        or not _same_stored_value(dict(marker_semantics), dict(semantics))
        or not _same_stored_value(dict(marker_patch), dict(target_patch))
    ):
        raise PolicyViolation("Phase4 apply marker conflicts with the approved proposal")
    return phase


def _phase4_apply_marker_matches(
    record: Any,
    *,
    proposal_id: str,
    proposal_type: str,
    semantics: Mapping[str, Any],
    target_patch: Mapping[str, Any],
) -> bool:
    """Return whether a valid, proposal-bound Phase4 marker is present."""

    return (
        _phase4_apply_marker_phase(
            record,
            proposal_id=proposal_id,
            proposal_type=proposal_type,
            semantics=semantics,
            target_patch=target_patch,
        )
        is not None
    )


def _assert_supplied_matches_current(supplied: Any, current: Any) -> None:
    """Reject a caller's different target/action under the same Proposal ID."""

    if isinstance(supplied, str):
        return
    proposal_type = _wire_value(_get_field(current, "Proposal Type", "proposal_type"))
    if proposal_type in {ProposalType.MATERIAL_USAGE.value, ProposalType.PAGE_RANGE.value}:
        from uls.domain.approval_identity import _field, canonical_semantics_from_queue

        try:
            expected = canonical_semantics_from_queue(current)
            candidate = {
                "Proposal Type": proposal_type,
                "Target Entity ID": expected["target_entity_id"],
                "Proposed Action": _action_mapping(current),
            }
            for names in (
                ("Proposal Type", "proposal_type"),
                ("Target Entity ID", "target_entity_id", "target_id", "Target Entity"),
                ("Proposed Action", "proposed_action", "action"),
                ("Course", "course"), ("Source Ref", "source_ref"),
                ("Source Hash", "source_hash"), ("Source Version", "source_version"),
                ("Evidence", "evidence"), ("Review Reason", "review_reason"),
            ):
                value = _field(supplied, *names, default=_MISSING)
                if value is not _MISSING:
                    candidate[names[0]] = value
            supplied_id = _field(supplied, "Proposal ID", "proposal_id", default=_MISSING)
            if supplied_id is not _MISSING and supplied_id != _proposal_id(current):
                raise ValueError("caller Proposal ID mismatch")
            if canonical_semantics_from_queue(candidate) != expected:
                raise ValueError("caller approval semantics mismatch")
            # Phase4 intentionally does not persist a Target DB property;
            # the logical target is part of the canonical action.  Continue
            # accepting the legacy caller mirror only when it agrees with
            # that action, while treating an explicit null or wrong alias as
            # a semantic mismatch.
            target_db = _field(
                supplied,
                "Target DB",
                "Target Database",
                "target_db",
                "target_database",
                default=_MISSING,
            )
            if target_db is _MISSING:
                target = _field(supplied, "Target", "target", default=_MISSING)
                if target is not _MISSING:
                    target_db = _field(
                        target,
                        "Target DB",
                        "Target Database",
                        "Database",
                        "DB",
                        "target_db",
                        "database",
                        default=_MISSING,
                    )
            if target_db is not _MISSING and (
                not isinstance(target_db, str)
                or _normal_key(target_db) != _normal_key(expected["target_db"])
            ):
                raise ValueError("caller target database mismatch")
        except (ValueError, TypeError) as exc:
            raise PolicyViolation("Requested proposal does not match the current queue item") from exc
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
        supplied_action = _action_mapping(supplied)
        current_action = _action_mapping(current)
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
    used_positional_names = {
        parameter.name for parameter in positional_parameters[: len(positional)]
    }
    supported_keyword_values = {
        name: value
        for name, value in keyword_values.items()
        if name in parameter_names and name not in used_positional_names
    }
    if actor_parameter is not None and actor_parameter.kind is actor_parameter.KEYWORD_ONLY:
        return method(
            *positional[: len(positional_parameters)],
            **supported_keyword_values,
        )

    # Keyword-only provider arguments with canonical names can be filled from
    # the values we do know, even if a non-canonical optional name is present.
    named_values = supported_keyword_values
    if named_values and len(positional_parameters) == 0:
        return method(**named_values)

    if len(positional_parameters) >= len(positional):
        if supported_keyword_values:
            return method(*positional, **supported_keyword_values)
        return method(*positional)
    if supported_keyword_values:
        return method(
            *positional[: len(positional_parameters)],
            **supported_keyword_values,
        )
    return method(*positional[: len(positional_parameters)])


def _guarded_update(
    adapter: NotionAdapter,
    actor: AutomationActor,
    target_db: str,
    entity_id: str,
    patch: Mapping[str, Any],
    *,
    automation_queue_ids: set[str] | None = None,
    is_create: bool = False,
    system_transition: bool = False,
) -> Any:
    enforce_write_policy(
        actor,
        target_db,
        patch,
        automation_queue_ids=automation_queue_ids,
        is_create=is_create,
        system_transition=system_transition,
    )
    method = getattr(adapter, "update_properties", None)
    if method is None:
        raise PolicyViolation("Notion adapter has no update_properties write boundary")
    keyword_values: dict[str, Any] = {
        "target_db": target_db,
        "entity_id": entity_id,
        "patch": dict(patch),
        "actor": actor,
    }
    if system_transition:
        # This keyword is deliberately added only by the internal helper that
        # requested the system transition.  It is not part of ordinary
        # caller/provider request data.
        keyword_values["system_transition"] = True
    return _call_with_supported_signature(
        method,
        (target_db, entity_id, dict(patch), actor),
        keyword_values,
    )


def _guarded_create(
    adapter: NotionAdapter,
    actor: AutomationActor,
    target_db: str,
    properties: Mapping[str, Any],
    *,
    automation_queue_ids: set[str] | None = None,
) -> Any:
    enforce_write_policy(
        actor,
        target_db,
        properties,
        automation_queue_ids=automation_queue_ids,
        is_create=True,
    )
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


def _read_approval(
    adapter: NotionAdapter | None,
    proposal_id: str,
    *,
    automation_queue_id: str | None = None,
    automation_queue_ids: set[str] | None = None,
    require_unique_physical: bool = False,
) -> Any | None:
    queue_target, _ = _queue_target(automation_queue_id, automation_queue_ids)
    finder = getattr(adapter, "find_approval_rows", None)
    if callable(finder):
        rows = _call_queue_finder(finder, queue_target, proposal_id)
        return _unique_approval_row(rows, proposal_id)
    if require_unique_physical:
        raise PolicyViolation("guarded Queue backend must expose find_approval_rows")
    method = getattr(adapter, "read_approval", None)
    if method is not None:
        result = _call_lookup(method, queue_target, proposal_id)
        if result is not None:
            return result
    finder = getattr(adapter, "find_entity_by_id", None)
    if finder is not None:
        return _call_lookup(finder, queue_target, proposal_id)
    return None


def _call_queue_finder(
    method: Callable[..., Any],
    target_db: str,
    proposal_id: str,
) -> list[Any]:
    """Call a physical Queue lookup without hiding pagination/duplicates."""

    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        result = method(proposal_id)
    else:
        names = set(signature.parameters)
        if "proposal_id" in names:
            result = method(proposal_id=proposal_id)
        elif "target_db" in names and "entity_id" in names:
            result = method(target_db=target_db, entity_id=proposal_id)
        elif "database" in names and "proposal_id" in names:
            result = method(database=target_db, proposal_id=proposal_id)
        else:
            positional = [
                parameter
                for parameter in signature.parameters.values()
                if parameter.kind
                in {parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD}
            ]
            result = method(proposal_id) if len(positional) <= 1 else method(target_db, proposal_id)
    if result is None:
        return []
    if isinstance(result, Mapping):
        return [result]
    if isinstance(result, (str, bytes)):
        raise PolicyViolation("find_approval_rows must return physical Queue rows")
    try:
        return list(result)
    except TypeError as exc:
        raise PolicyViolation("find_approval_rows must return physical Queue rows") from exc


def _unique_approval_row(rows: Sequence[Any], proposal_id: str) -> Any | None:
    """Shared physical 0/exactly-1/>1 Queue cardinality check."""

    values = list(rows)
    if len(values) > 1:
        raise PolicyViolation(
            f"Queue Proposal ID is physically ambiguous: {proposal_id}",
            details={"proposal_id": proposal_id, "physical_matches": len(values)},
        )
    if not values:
        return None
    current = values[0]
    if _proposal_id(current) != proposal_id:
        raise PolicyViolation("Queue lookup returned a mismatched Proposal ID")
    return current


def _require_phase4_queue_backend(record: Any, adapter: Any) -> None:
    proposal_type = _get_field(record, "Proposal Type", "proposal_type", default=None)
    proposal_type = _wire_value(proposal_type)
    if proposal_type in {ProposalType.MATERIAL_USAGE.value, ProposalType.PAGE_RANGE.value} and not callable(
        getattr(adapter, "find_approval_rows", None)
    ):
        raise PolicyViolation("Phase4 guarded Queue path requires physical uniqueness lookup")


def _validate_phase4_queue_identity(record: Any) -> None:
    proposal_type = _wire_value(_get_field(record, "Proposal Type", "proposal_type", default=None))
    if proposal_type not in {ProposalType.MATERIAL_USAGE.value, ProposalType.PAGE_RANGE.value}:
        return
    from uls.domain.approval_identity import canonical_semantics_from_queue, derive_proposal_id

    try:
        semantics = canonical_semantics_from_queue(record)
    except (TypeError, ValueError) as exc:
        raise PolicyViolation("stored Phase4 Queue action is malformed") from exc
    stored_id = _proposal_id(record)
    expected_id = derive_proposal_id(str(proposal_type), semantics)
    if stored_id != expected_id:
        raise PolicyViolation("stored Queue Proposal ID fails semantic self-validation")


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
    direct = _action_value(
        action,
        f"{relation} ID",
        f"{relation} Id",
        relation,
        relation.casefold(),
        f"{relation.casefold()}_id",
        default=_PATCH_MISSING,
    )
    if direct is not _PATCH_MISSING:
        return direct
    snapshot = _action_value(
        action,
        "Relation Snapshot",
        "relation_snapshot",
        "Relations",
        "relations",
        default=None,
    )
    if snapshot is not None:
        return _action_value(
            _as_mapping(snapshot) or {},
            f"{relation} ID",
            f"{relation} Id",
            relation,
            relation.casefold(),
            f"{relation.casefold()}_id",
            default=None,
        )
    return None


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


def _scope_ids(value: Any) -> set[str]:
    """Extract session IDs from a flat or structured scope snapshot."""

    if isinstance(value, Mapping):
        nested = _action_value(
            value,
            "Included Sessions",
            "Session IDs",
            "Scope",
            "included_sessions",
            "scope",
            default=_PATCH_MISSING,
        )
        if nested is not _PATCH_MISSING and nested is not value:
            return _scope_ids(nested)
    return _relation_ids(value)


def _validate_relation_action(action: Mapping[str, Any], target: Any) -> None:
    for relation in ("Session", "Material"):
        requested = _action_relation(action, relation)
        if requested is None:
            raise PolicyViolation(
                f"Approved MATERIAL_USAGE action must include the {relation} relation snapshot"
            )
        requested_ids = _relation_ids(requested)
        if not requested_ids:
            raise PolicyViolation(
                f"Approved MATERIAL_USAGE action must include a non-empty {relation} relation snapshot"
            )
        current = _get_field(
            target,
            relation,
            f"{relation} ID",
            f"{relation.casefold()}_id",
            default=None,
        )
        if current is None:
            raise _StaleApproval(f"Approved {relation} relation is not present on the target")
        current_ids = _relation_ids(current)
        if requested_ids != current_ids:
            raise _StaleApproval(f"Approved {relation} relation no longer matches the target")


def _validate_scope_action(action: Mapping[str, Any], target: Any) -> None:
    snapshot_names = (
        "Included Sessions",
        "Session IDs",
        "Scope",
        "included_sessions",
        "Scope Snapshot",
        "scope_snapshot",
    )
    requested = _PATCH_MISSING
    requested_name: str | None = None
    for name in snapshot_names:
        value = _action_value(action, name, default=_PATCH_MISSING)
        if value is not _PATCH_MISSING:
            requested = value
            requested_name = name
            break
    if requested is _PATCH_MISSING or requested is None:
        raise PolicyViolation("Approved EXAM_SCOPE action must include a scope snapshot")

    if requested_name in {"Scope Snapshot", "scope_snapshot"}:
        nested = _as_mapping(requested)
        if nested is None:
            raise PolicyViolation("Approved EXAM_SCOPE scope snapshot must be structured")
        requested = _action_value(
            nested,
            "Included Sessions",
            "Session IDs",
            "Scope",
            "included_sessions",
            default=_PATCH_MISSING,
        )
        if requested is _PATCH_MISSING:
            raise PolicyViolation("Approved EXAM_SCOPE action must include a scope snapshot")

    current = _PATCH_MISSING
    for name in ("Included Sessions", "Session IDs", "Scope", "included_sessions"):
        value = _get_field(target, name, default=_PATCH_MISSING)
        if value is not _PATCH_MISSING:
            current = value
            break
    if current is _PATCH_MISSING or current is None:
        raise _StaleApproval("Approved exam scope is not present on the target")
    requested_ids = _scope_ids(requested)
    if not requested_ids:
        raise PolicyViolation("Approved EXAM_SCOPE action must include a non-empty scope snapshot")
    current_ids = _scope_ids(current)
    if requested_ids != current_ids:
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
    *,
    required: bool = False,
) -> tuple[bool, str | None]:
    expected_hash, expected_version = _proposal_fingerprint(proposal)
    if required and (expected_hash is None or expected_version is None):
        return False, "source fingerprint is required for this proposal"
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


def _mark_proposal_terminal(
    adapter: NotionAdapter,
    proposal_id: str,
    state: QueueState,
    reason: str,
    *,
    automation_queue_id: str | None = None,
    automation_queue_db_id: str | None = None,
    automation_queue_ids: set[str] | None = None,
) -> Any:
    """Write one system-owned terminal state through the internal path."""

    if state not in {QueueState.SUPERSEDED, QueueState.FAILED}:
        raise PolicyViolation("only SUPERSEDED or FAILED are system terminal states")
    proposal_id = _valid_proposal_id(proposal_id)
    if not isinstance(reason, str) or not reason.strip():
        raise PolicyViolation("system terminal transitions require a non-empty reason")
    if automation_queue_id is not None and automation_queue_db_id is not None:
        raise TypeError("pass either automation_queue_id or automation_queue_db_id, not both")
    if automation_queue_id is None:
        automation_queue_id = automation_queue_db_id
    queue_db_id, queue_ids = _queue_target(automation_queue_id, automation_queue_ids)
    current = _read_approval(adapter, proposal_id, automation_queue_id=queue_db_id,
                             automation_queue_ids=set(queue_ids or ()))
    if current is None:
        raise PolicyViolation("Queue terminal transition target is missing")
    _require_phase4_queue_backend(current, adapter)
    _validate_phase4_queue_identity(current)
    transition_queue_state(_get_field(current, "State"), state, actor=AutomationActor.AUTOMATION)
    return _guarded_update(
        adapter,
        AutomationActor.AUTOMATION,
        queue_db_id,
        _physical_or_logical_id(current, proposal_id),
        _stale_error_patch(reason.strip(), state),
        automation_queue_ids=set(queue_ids or ()),
        system_transition=True,
    )


def mark_proposal_superseded(
    adapter: NotionAdapter,
    proposal_id: str,
    reason: str,
    *,
    automation_queue_id: str | None = None,
    automation_queue_db_id: str | None = None,
    automation_queue_ids: set[str] | None = None,
) -> Any:
    """Record source invalidation for a proposal via the internal path."""

    return _mark_proposal_terminal(
        adapter,
        proposal_id,
        QueueState.SUPERSEDED,
        reason,
        automation_queue_id=automation_queue_id,
        automation_queue_db_id=automation_queue_db_id,
        automation_queue_ids=automation_queue_ids,
    )


def mark_proposal_failed(
    adapter: NotionAdapter,
    proposal_id: str,
    reason: str,
    *,
    automation_queue_id: str | None = None,
    automation_queue_db_id: str | None = None,
    automation_queue_ids: set[str] | None = None,
) -> Any:
    """Record a defined system/application failure for a proposal."""

    return _mark_proposal_terminal(
        adapter,
        proposal_id,
        QueueState.FAILED,
        reason,
        automation_queue_id=automation_queue_id,
        automation_queue_db_id=automation_queue_db_id,
        automation_queue_ids=automation_queue_ids,
    )


_NON_HUMAN_DECISION_IDENTIFIERS = frozenset(
    {
        "automation",
        "approvalreader",
        "humanapprovalapplier",
        "system",
        "systemautomation",
    }
)


def _is_human_decision_by(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = "".join(character for character in value.casefold() if character.isalnum())
    if not normalized or normalized in _NON_HUMAN_DECISION_IDENTIFIERS:
        return False
    if normalized.startswith(("automation", "approvalreader", "humanapprovalapplier")):
        return False
    if normalized.endswith(("bot", "worker", "service")):
        return False
    return True


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
        phase4_type = self.proposal_type in {
            ProposalType.MATERIAL_USAGE,
            ProposalType.PAGE_RANGE,
        }
        # Phase4 keeps logical target/database and target snapshot inside the
        # canonical Proposed Action JSON.  They are deliberately not emitted
        # as legacy Queue properties, which would create a second mutable
        # source of approval identity.
        if self.target_db is not None and not phase4_type:
            properties["Target DB"] = self.target_db
        if self.source_hash is not None:
            properties["Source Hash"] = self.source_hash
        if self.source_version is not None:
            properties["Source Version"] = self.source_version
        if self.decision_by is not None and not phase4_type:
            properties["Decision By"] = self.decision_by
        if self.decision_at is not None and not phase4_type:
            properties["Decision At"] = self.decision_at
        if self.applied_at is not None and not phase4_type:
            properties["Applied At"] = self.applied_at
        if self.target_fingerprint is not None and not phase4_type:
            properties["Target Fingerprint"] = dict(self.target_fingerprint)
        return properties


AutomationQueueProposal = Proposal
ApprovalProposal = Proposal


class ApprovalReader:
    """Internal capability that derives queue state from human Decision only."""

    _actor = AutomationActor.APPROVAL_READER

    def __init__(
        self,
        adapter: NotionAdapter | None = None,
        *,
        automation_queue_id: str | None = None,
        automation_queue_db_id: str | None = None,
        automation_queue_ids: set[str] | None = None,
    ) -> None:
        if automation_queue_id is not None and automation_queue_db_id is not None:
            raise TypeError("pass either automation_queue_id or automation_queue_db_id, not both")
        if automation_queue_id is None:
            automation_queue_id = automation_queue_db_id
        self._queue_db_id, self._queue_ids = _queue_target(
            automation_queue_id, automation_queue_ids
        )
        self._adapter = adapter

    @staticmethod
    def derive_state(decision: Decision) -> QueueState:
        return derive_queue_state(decision)

    def read(self, proposal_id: Any) -> QueueState:
        """Read a current human decision and return its derived state."""

        proposal_id = _requested_proposal_id(proposal_id)
        record = _read_approval(
            self._adapter,
            proposal_id,
            automation_queue_id=self._queue_db_id,
            automation_queue_ids=set(self._queue_ids or ()),
        )
        if record is None:
            raise PolicyViolation(f"Approval proposal not found: {proposal_id}")
        _require_phase4_queue_backend(record, self._adapter)
        _validate_phase4_queue_identity(record)
        decision_value = _decision_field(record)
        if decision_value is None:
            raise PolicyViolation("Approval record has no human Decision")
        return self.derive_state(coerce_decision(decision_value))

    def sync_state(self, proposal_id: Any) -> QueueState:
        """Derive and persist State without exposing any Decision mutation."""

        if self._adapter is None:
            raise PolicyViolation("ApprovalReader requires a Notion adapter to sync State")
        proposal_id = _requested_proposal_id(proposal_id)
        record = _read_approval(
            self._adapter,
            proposal_id,
            automation_queue_id=self._queue_db_id,
            automation_queue_ids=set(self._queue_ids or ()),
        )
        if record is None:
            raise PolicyViolation(f"Approval proposal not found: {proposal_id}")
        _require_phase4_queue_backend(record, self._adapter)
        _validate_phase4_queue_identity(record)
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
            self._queue_db_id,
            _physical_or_logical_id(record, proposal_id),
            {"State": state.value},
            automation_queue_ids=set(self._queue_ids or ()),
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
        graph_reader: Any | None = None,
        source_reader: Any | None = None,
        source_binding_resolver: Any | None = None,
        config: Any | None = None,
        automation_queue_id: str | None = None,
        automation_queue_db_id: str | None = None,
        automation_queue_ids: set[str] | None = None,
    ) -> None:
        if callable(decision_by) and clock is None:
            # A small convenience for tests that pass a clock as the second
            # positional argument; it does not introduce an actor selector.
            clock = decision_by  # type: ignore[assignment]
            decision_by = None
        self._adapter = adapter
        self._decision_by = decision_by.strip() if isinstance(decision_by, str) else decision_by
        if self._decision_by is not None and not _is_human_decision_by(self._decision_by):
            raise PolicyViolation("human decision_by required")
        if automation_queue_id is not None and automation_queue_db_id is not None:
            raise TypeError("pass either automation_queue_id or automation_queue_db_id, not both")
        if automation_queue_id is None:
            automation_queue_id = automation_queue_db_id
        self._queue_db_id, self._queue_ids = _queue_target(
            automation_queue_id, automation_queue_ids
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._graph_reader = graph_reader
        self._source_reader = source_reader
        self._source_binding_resolver = source_binding_resolver
        self._config = config

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
        current = _read_approval(
            self._adapter,
            proposal_id,
            automation_queue_id=self._queue_db_id,
            automation_queue_ids=set(self._queue_ids or ()),
        )
        if current is None:
            raise PolicyViolation(f"Current approval proposal not found: {proposal_id}")
        _require_phase4_queue_backend(current, self._adapter)
        _validate_phase4_queue_identity(current)
        current_id = _proposal_id(current)
        if current_id != proposal_id:
            raise PolicyViolation("Approval record Proposal ID does not match the requested ID")
        _assert_supplied_matches_current(supplied, current)
        if _wire_value(_get_field(current, "Proposal Type", "proposal_type")) in {
            ProposalType.MATERIAL_USAGE.value,
            ProposalType.PAGE_RANGE.value,
        }:
            # The caller's Phase4 mirrors have been compared above, but they
            # must not be merged into the provider's current row.  Merging an
            # alias such as ``source_ref`` beside the stored ``Source Ref``
            # would create duplicate semantic fields on the next canonical
            # read.  The current row owns lifecycle, audit, and all semantics.
            return proposal_id, current
        return proposal_id, _merge_records(supplied, current)

    def _mark_terminal(
        self,
        proposal_id: str,
        state: QueueState,
        reason: str,
    ) -> ApprovalApplyResult:
        patch = _stale_error_patch(reason, state)
        current = _read_approval(self._adapter, proposal_id,
                                 automation_queue_id=self._queue_db_id,
                                 automation_queue_ids=set(self._queue_ids or ()))
        if current is None:
            raise PolicyViolation("Queue terminal transition target is missing")
        _require_phase4_queue_backend(current, self._adapter)
        _validate_phase4_queue_identity(current)
        transition_queue_state(_get_field(current, "State"), state, actor=self._actor)
        _guarded_update(
            self._adapter,
            self._actor,
            self._queue_db_id,
            _physical_or_logical_id(current, proposal_id),
            patch,
            automation_queue_ids=set(self._queue_ids or ()),
        )
        return ApprovalApplyResult(proposal_id, state, False, reason)

    def _phase4_recoverable(
        self,
        proposal_id: str,
        reason: str,
    ) -> ApprovalApplyResult:
        return ApprovalApplyResult(
            proposal_id,
            QueueState.APPROVED,
            False,
            f"Phase4 target application requires reconciliation: {reason}",
        )

    def _phase4_approved_queue(
        self,
        proposal_id: str,
        proposal_type: str,
        semantics: Mapping[str, Any],
        target_patch: Mapping[str, Any],
    ) -> tuple[Any, str | None] | None:
        """Read and validate the unique approved Queue row and its marker."""

        from uls.domain.approval_identity import canonical_semantics_from_queue

        current = _read_approval(
            self._adapter,
            proposal_id,
            automation_queue_id=self._queue_db_id,
            automation_queue_ids=set(self._queue_ids or ()),
            require_unique_physical=True,
        )
        if current is None:
            return None
        _require_phase4_queue_backend(current, self._adapter)
        _validate_phase4_queue_identity(current)
        if _wire_value(_get_field(current, "Proposal Type", "proposal_type")) != proposal_type:
            raise PolicyViolation("Phase4 approval type changed before target application")
        if (
            coerce_queue_state(_get_field(current, "State", "state", default=None))
            is not QueueState.APPROVED
            or coerce_decision(_decision_field(current)) is not Decision.Approve
        ):
            raise PolicyViolation("Phase4 human approval changed before target application")
        try:
            current_semantics = canonical_semantics_from_queue(current)
        except (TypeError, ValueError) as exc:
            raise PolicyViolation("stored Phase4 Queue action is malformed") from exc
        if current_semantics != semantics:
            raise PolicyViolation("Phase4 approval content changed before target application")
        marker_phase = _phase4_apply_marker_phase(
            current,
            proposal_id=proposal_id,
            proposal_type=proposal_type,
            semantics=semantics,
            target_patch=target_patch,
        )
        return current, marker_phase

    def _phase4_marker_is_verified(
        self,
        proposal_id: str,
        proposal_type: str,
        semantics: Mapping[str, Any],
        target_patch: Mapping[str, Any],
        *,
        expected_phase: str | None = None,
    ) -> bool:
        """Verify marker persistence after a possibly ambiguous Queue write."""

        from uls.domain.approval_identity import canonical_semantics_from_queue

        try:
            current = _read_approval(
                self._adapter,
                proposal_id,
                automation_queue_id=self._queue_db_id,
                automation_queue_ids=set(self._queue_ids or ()),
                require_unique_physical=True,
            )
            if current is None:
                return False
            _require_phase4_queue_backend(current, self._adapter)
            _validate_phase4_queue_identity(current)
            if _wire_value(
                _get_field(current, "Proposal Type", "proposal_type", default=None)
            ) != proposal_type:
                return False
            if (
                coerce_queue_state(_get_field(current, "State", "state", default=None))
                is not QueueState.APPROVED
                or coerce_decision(_decision_field(current)) is not Decision.Approve
            ):
                return False
            if canonical_semantics_from_queue(current) != semantics:
                return False
            marker_phase = _phase4_apply_marker_phase(
                current,
                proposal_id=proposal_id,
                proposal_type=proposal_type,
                semantics=semantics,
                target_patch=target_patch,
            )
            return marker_phase is not None and (
                expected_phase is None or marker_phase == expected_phase
            )
        except PolicyViolation:
            raise
        except Exception:
            # A marker write can commit and raise, and its read-back can also
            # be unavailable.  Neither case authorizes a target write.
            return False

    def _phase4_arm_marker(
        self,
        proposal_id: str,
        proposal_type: str,
        semantics: Mapping[str, Any],
        target_patch: Mapping[str, Any],
    ) -> bool:
        """Persist and independently verify the write-ahead marker."""

        from uls.domain.approval_identity import canonical_semantics_from_queue

        marker = _phase4_apply_marker_text(
            proposal_id,
            proposal_type,
            semantics,
            target_patch,
        )
        try:
            current = _read_approval(
                self._adapter,
                proposal_id,
                automation_queue_id=self._queue_db_id,
                automation_queue_ids=set(self._queue_ids or ()),
                require_unique_physical=True,
            )
            if current is None:
                raise PolicyViolation("Phase4 approval disappeared before marker arm")
            _require_phase4_queue_backend(current, self._adapter)
            _validate_phase4_queue_identity(current)
            if _wire_value(
                _get_field(current, "Proposal Type", "proposal_type", default=None)
            ) != proposal_type:
                raise PolicyViolation("Phase4 approval type changed before marker arm")
            if (
                coerce_queue_state(_get_field(current, "State", "state", default=None))
                is not QueueState.APPROVED
                or coerce_decision(_decision_field(current)) is not Decision.Approve
            ):
                raise PolicyViolation("Phase4 human approval changed before marker arm")
            if canonical_semantics_from_queue(current) != semantics:
                raise PolicyViolation("Phase4 approval content changed before marker arm")
            if _phase4_apply_marker_phase(
                current,
                proposal_id=proposal_id,
                proposal_type=proposal_type,
                semantics=semantics,
                target_patch=target_patch,
            ):
                # The caller saw no marker on its initial Queue read.  A
                # marker found here belongs to an earlier or concurrent
                # attempt, so this arm call must not claim it as its own.
                return False
            physical_id = _physical_or_logical_id(current, proposal_id)
        except PolicyViolation:
            raise
        except Exception:
            return False

        try:
            _guarded_update(
                self._adapter,
                self._actor,
                self._queue_db_id,
                physical_id,
                {"Last Error": marker},
                automation_queue_ids=set(self._queue_ids or ()),
            )
        except Exception:
            return self._phase4_marker_is_verified(
                proposal_id,
                proposal_type,
                semantics,
                target_patch,
                expected_phase=_PHASE4_APPLY_MARKER_PHASE_PREPARED,
            )
        return self._phase4_marker_is_verified(
            proposal_id,
            proposal_type,
            semantics,
            target_patch,
            expected_phase=_PHASE4_APPLY_MARKER_PHASE_PREPARED,
        )

    def _phase4_mark_effect_observed(
        self,
        proposal_id: str,
        proposal_type: str,
        semantics: Mapping[str, Any],
        target_patch: Mapping[str, Any],
    ) -> bool:
        """Durably record a target effect only after desired read-back.

        The prepared marker remains the recovery evidence until this separate
        Queue write is independently verified.  An ambiguous marker write is
        therefore never treated as permission to audit the application.
        """

        from uls.domain.approval_identity import canonical_semantics_from_queue

        marker = _phase4_apply_marker_text(
            proposal_id,
            proposal_type,
            semantics,
            target_patch,
            phase=_PHASE4_APPLY_MARKER_PHASE_EFFECT_OBSERVED,
        )
        try:
            current = _read_approval(
                self._adapter,
                proposal_id,
                automation_queue_id=self._queue_db_id,
                automation_queue_ids=set(self._queue_ids or ()),
                require_unique_physical=True,
            )
            if current is None:
                return False
            _require_phase4_queue_backend(current, self._adapter)
            _validate_phase4_queue_identity(current)
            if _wire_value(
                _get_field(current, "Proposal Type", "proposal_type", default=None)
            ) != proposal_type:
                return False
            if (
                coerce_queue_state(_get_field(current, "State", "state", default=None))
                is not QueueState.APPROVED
                or coerce_decision(_decision_field(current)) is not Decision.Approve
            ):
                return False
            if canonical_semantics_from_queue(current) != semantics:
                return False
            current_phase = _phase4_apply_marker_phase(
                current,
                proposal_id=proposal_id,
                proposal_type=proposal_type,
                semantics=semantics,
                target_patch=target_patch,
            )
            if current_phase == _PHASE4_APPLY_MARKER_PHASE_EFFECT_OBSERVED:
                return self._phase4_marker_is_verified(
                    proposal_id,
                    proposal_type,
                    semantics,
                    target_patch,
                    expected_phase=_PHASE4_APPLY_MARKER_PHASE_EFFECT_OBSERVED,
                )
            if current_phase != _PHASE4_APPLY_MARKER_PHASE_PREPARED:
                return False
            physical_id = _physical_or_logical_id(current, proposal_id)
        except PolicyViolation:
            raise
        except Exception:
            return False

        try:
            _guarded_update(
                self._adapter,
                self._actor,
                self._queue_db_id,
                physical_id,
                {"Last Error": marker},
                automation_queue_ids=set(self._queue_ids or ()),
            )
        except Exception:
            return self._phase4_marker_is_verified(
                proposal_id,
                proposal_type,
                semantics,
                target_patch,
                expected_phase=_PHASE4_APPLY_MARKER_PHASE_EFFECT_OBSERVED,
            )
        return self._phase4_marker_is_verified(
            proposal_id,
            proposal_type,
            semantics,
            target_patch,
            expected_phase=_PHASE4_APPLY_MARKER_PHASE_EFFECT_OBSERVED,
        )

    def _phase4_clear_marker(
        self,
        proposal_id: str,
        proposal_type: str,
        semantics: Mapping[str, Any],
        target_patch: Mapping[str, Any],
    ) -> bool:
        """Clear a marker only after a same-attempt exact-old confirmation."""

        from uls.domain.approval_identity import canonical_semantics_from_queue

        try:
            current = _read_approval(
                self._adapter,
                proposal_id,
                automation_queue_id=self._queue_db_id,
                automation_queue_ids=set(self._queue_ids or ()),
                require_unique_physical=True,
            )
            if current is None:
                return False
            _require_phase4_queue_backend(current, self._adapter)
            _validate_phase4_queue_identity(current)
            if _wire_value(
                _get_field(current, "Proposal Type", "proposal_type", default=None)
            ) != proposal_type:
                return False
            if (
                coerce_queue_state(_get_field(current, "State", "state", default=None))
                is not QueueState.APPROVED
                or coerce_decision(_decision_field(current)) is not Decision.Approve
            ):
                return False
            if canonical_semantics_from_queue(current) != semantics:
                return False
            marker_phase = _phase4_apply_marker_phase(
                current,
                proposal_id=proposal_id,
                proposal_type=proposal_type,
                semantics=semantics,
                target_patch=target_patch,
            )
            if marker_phase is None:
                return True
            if marker_phase != _PHASE4_APPLY_MARKER_PHASE_PREPARED:
                return False
            physical_id = _physical_or_logical_id(current, proposal_id)
        except PolicyViolation:
            raise
        except Exception:
            return False

        try:
            _guarded_update(
                self._adapter,
                self._actor,
                self._queue_db_id,
                physical_id,
                {"Last Error": None},
                automation_queue_ids=set(self._queue_ids or ()),
            )
        except Exception:
            pass

        try:
            current = _read_approval(
                self._adapter,
                proposal_id,
                automation_queue_id=self._queue_db_id,
                automation_queue_ids=set(self._queue_ids or ()),
                require_unique_physical=True,
            )
            if current is None:
                return False
            _require_phase4_queue_backend(current, self._adapter)
            _validate_phase4_queue_identity(current)
            if _wire_value(
                _get_field(current, "Proposal Type", "proposal_type", default=None)
            ) != proposal_type:
                return False
            return _phase4_apply_marker_phase(
                current,
                proposal_id=proposal_id,
                proposal_type=proposal_type,
                semantics=semantics,
                target_patch=target_patch,
            ) is None
        except PolicyViolation:
            raise
        except Exception:
            return False

    def _phase4_confirm_applied_queue(
        self,
        proposal_id: str,
        proposal_type: str,
        semantics: Mapping[str, Any],
        target_patch: Mapping[str, Any],
        *,
        marker_expected: bool,
    ) -> bool:
        """Confirm an APPLIED audit while preserving marker identity."""

        from uls.domain.approval_identity import canonical_semantics_from_queue

        try:
            current = _read_approval(
                self._adapter,
                proposal_id,
                automation_queue_id=self._queue_db_id,
                automation_queue_ids=set(self._queue_ids or ()),
                require_unique_physical=True,
            )
            if current is None:
                return False
            _require_phase4_queue_backend(current, self._adapter)
            _validate_phase4_queue_identity(current)
            if _wire_value(
                _get_field(current, "Proposal Type", "proposal_type", default=None)
            ) != proposal_type:
                return False
            if coerce_queue_state(_get_field(current, "State", "state", default=None)) is not QueueState.APPLIED:
                return False
            if coerce_decision(_decision_field(current)) is not Decision.Approve:
                return False
            if not _phase4_audit_fields_complete(current):
                return False
            if canonical_semantics_from_queue(current) != semantics:
                return False
            marker_phase = _phase4_apply_marker_phase(
                current,
                proposal_id=proposal_id,
                proposal_type=proposal_type,
                semantics=semantics,
                target_patch=target_patch,
            )
            return marker_phase == (
                _PHASE4_APPLY_MARKER_PHASE_EFFECT_OBSERVED
                if marker_expected
                else None
            )
        except PolicyViolation:
            raise
        except Exception:
            return False

    def _phase4_clear_applied_marker(
        self,
        proposal_id: str,
        proposal_type: str,
        semantics: Mapping[str, Any],
        target_patch: Mapping[str, Any],
    ) -> bool:
        """Best-effort cleanup after APPLIED has been independently verified."""

        from uls.domain.approval_identity import canonical_semantics_from_queue

        try:
            current = _read_approval(
                self._adapter,
                proposal_id,
                automation_queue_id=self._queue_db_id,
                automation_queue_ids=set(self._queue_ids or ()),
                require_unique_physical=True,
            )
            if current is None:
                return False
            _require_phase4_queue_backend(current, self._adapter)
            _validate_phase4_queue_identity(current)
            if _wire_value(
                _get_field(current, "Proposal Type", "proposal_type", default=None)
            ) != proposal_type:
                return False
            if coerce_queue_state(_get_field(current, "State", "state", default=None)) is not QueueState.APPLIED:
                return False
            if canonical_semantics_from_queue(current) != semantics:
                return False
            marker_phase = _phase4_apply_marker_phase(
                current,
                proposal_id=proposal_id,
                proposal_type=proposal_type,
                semantics=semantics,
                target_patch=target_patch,
            )
            if marker_phase is None:
                return True
            if marker_phase != _PHASE4_APPLY_MARKER_PHASE_EFFECT_OBSERVED:
                # An APPLIED row may be terminal, but a prepared marker is
                # still unresolved evidence and must not be silently erased.
                return False
            physical_id = _physical_or_logical_id(current, proposal_id)
        except PolicyViolation:
            raise
        except Exception:
            return False

        try:
            _guarded_update(
                self._adapter,
                self._actor,
                self._queue_db_id,
                physical_id,
                {"Last Error": None},
                automation_queue_ids=set(self._queue_ids or ()),
            )
        except Exception:
            pass

        return self._phase4_confirm_applied_queue(
            proposal_id,
            proposal_type,
            semantics,
            target_patch,
            marker_expected=False,
        )

    def _phase4_repair_applied_audit(
        self,
        proposal_id: str,
        proposal_type: str,
        semantics: Mapping[str, Any],
        target_patch: Mapping[str, Any],
        current: Any,
    ) -> ApprovalApplyResult:
        """Repair an incomplete APPLIED audit after desired-target proof."""

        stored_decision_by = _get_field(current, "Decision By", "decision_by", default=None)
        decision_by = _phase4_decision_by(current, self._decision_by)
        audit_patch: dict[str, Any] = {}
        if not _is_human_decision_by(stored_decision_by):
            audit_patch["Decision By"] = decision_by
        now = self._timestamp()
        decision_at = _get_field(current, "Decision At", "decision_at", default=None)
        if not _phase4_audit_value_present(decision_at):
            audit_patch["Decision At"] = now
        applied_at = _get_field(current, "Applied At", "applied_at", default=None)
        if not _phase4_audit_value_present(applied_at):
            audit_patch["Applied At"] = now
        if not audit_patch:
            return ApprovalApplyResult(proposal_id, QueueState.APPLIED, False, "already applied")

        try:
            _guarded_update(
                self._adapter,
                self._actor,
                self._queue_db_id,
                _physical_or_logical_id(current, proposal_id),
                audit_patch,
                automation_queue_ids=set(self._queue_ids or ()),
            )
        except Exception as exc:
            if self._phase4_confirm_applied_queue(
                proposal_id,
                proposal_type,
                semantics,
                target_patch,
                marker_expected=True,
            ):
                self._phase4_clear_applied_marker(
                    proposal_id,
                    proposal_type,
                    semantics,
                    target_patch,
                )
                return ApprovalApplyResult(proposal_id, QueueState.APPLIED, False, None)
            return ApprovalApplyResult(
                proposal_id,
                QueueState.APPROVED,
                False,
                f"APPLIED audit recovery pending: {type(exc).__name__}: {exc}",
            )
        if not self._phase4_confirm_applied_queue(
            proposal_id,
            proposal_type,
            semantics,
            target_patch,
            marker_expected=True,
        ):
            return ApprovalApplyResult(
                proposal_id,
                QueueState.APPROVED,
                False,
                "APPLIED audit recovery read-back requires reconciliation",
            )
        self._phase4_clear_applied_marker(
            proposal_id,
            proposal_type,
            semantics,
            target_patch,
        )
        return ApprovalApplyResult(proposal_id, QueueState.APPLIED, False, None)

    def _apply_phase4(self, proposal_id: str, current: Any) -> ApprovalApplyResult:
        """Apply a canonical Material Usage/PAGE_RANGE action with recovery."""

        from uls.adapters.drive.binding import SourceBindingResolver
        from uls.domain.approval_identity import canonical_semantics_from_queue
        from uls.enrichment._common import prepare_derivative
        from uls.retrieval._compat import field
        from uls.retrieval.authority import material_source_class
        from uls.retrieval.scope import material_usage_scopes

        if self._graph_reader is None or self._source_reader is None or self._source_binding_resolver is None:
            raise PolicyViolation(
                "Phase4 approval application requires separate graph, source and binding readers"
            )
        if not isinstance(self._source_binding_resolver, SourceBindingResolver):
            # runtime_checkable structural validation is useful for adapters
            # without importing a provider SDK; it still fails closed when a
            # caller passes a random string/callable.
            raise PolicyViolation("Phase4 source binding resolver is invalid")

        state = coerce_queue_state(_get_field(current, "State", "state", default=None))
        decision = coerce_decision(_decision_field(current))
        if state in {QueueState.REJECTED, QueueState.SUPERSEDED, QueueState.FAILED}:
            raise PolicyViolation(f"Proposal {proposal_id} is terminal: {state.value}")
        if state not in {QueueState.APPROVED, QueueState.APPLIED} or decision is not Decision.Approve:
            raise PolicyViolation(
                "HumanApprovalApplier requires State=APPROVED and Decision=Approve"
            )

        # Attribution is determined before any target mutation.  A pre-existing
        # Decision By is optional, but the value eventually persisted may never
        # be blank or an internal actor label.
        stored_decision_by = _get_field(current, "Decision By", "decision_by", default=None)
        decision_by = (
            stored_decision_by
            if _is_human_decision_by(stored_decision_by)
            else self._decision_by
        )
        if not _is_human_decision_by(decision_by):
            raise PolicyViolation("no attributable human decision_by is available")

        try:
            semantics = canonical_semantics_from_queue(current)
        except (TypeError, ValueError) as exc:
            raise PolicyViolation("stored Phase4 Queue action is malformed") from exc
        operation = semantics["operation"]
        session_id = semantics["session_id"]
        material_id = semantics["material_id"]
        target_id = semantics["target_entity_id"]
        role = semantics["usage_role"]
        material_type = semantics["material_type"]
        source_class = semantics["source_class"]
        desired = _phase4_range(semantics["desired_range"])
        old_snapshot = semantics["old_snapshot"]
        proposal_type = (
            ProposalType.MATERIAL_USAGE.value
            if operation == "create_usage"
            else ProposalType.PAGE_RANGE.value
        )
        target_patch: dict[str, Any]
        desired_verified = old_snapshot.get("verified")
        if operation == "create_usage":
            target_patch = {"Verified": True}
            desired_verified = True
        elif operation in {"update_range", "page_range"}:
            target_patch = {
                "Start Page": desired.start_page,
                "End Page": desired.end_page,
            }
        else:
            raise PolicyViolation("unsupported Phase4 approval operation")
        marker_phase = _phase4_apply_marker_phase(
            current,
            proposal_id=proposal_id,
            proposal_type=proposal_type,
            semantics=semantics,
            target_patch=target_patch,
        )
        marker_present = marker_phase is not None

        applied_audit_incomplete = (
            state is QueueState.APPLIED and not _phase4_audit_fields_complete(current)
        )
        if state is QueueState.APPLIED:
            if not applied_audit_incomplete:
                if marker_phase == _PHASE4_APPLY_MARKER_PHASE_EFFECT_OBSERVED:
                    self._phase4_clear_applied_marker(
                        proposal_id,
                        proposal_type,
                        semantics,
                        target_patch,
                    )
                return ApprovalApplyResult(proposal_id, QueueState.APPLIED, False, "already applied")
            if marker_phase != _PHASE4_APPLY_MARKER_PHASE_EFFECT_OBSERVED:
                return ApprovalApplyResult(
                    proposal_id,
                    QueueState.APPROVED,
                    False,
                    "APPLIED Phase4 audit is incomplete and has no recovery marker",
                )
            # An incomplete APPLIED audit is recoverable only after the same
            # trusted dependency and exact desired-target checks used before a
            # target write.  The repair below is audit-only.

        def terminal_or_recover(
            reason: str,
            *,
            terminal_on_marker: bool = False,
        ) -> ApprovalApplyResult:
            if marker_present and terminal_on_marker:
                # A freshly observed divergent target is an explicit
                # reconciliation result.  It is safe to record the terminal
                # outcome on Queue because this path never writes the target;
                # an exact old snapshot remains recoverable below.
                return self._mark_terminal(proposal_id, QueueState.SUPERSEDED, reason)
            if marker_present:
                return self._phase4_recoverable(proposal_id, reason)
            return self._mark_terminal(proposal_id, QueueState.SUPERSEDED, reason)

        graph = self._graph_reader
        source = self._source_reader
        session = _phase4_call(graph, "get_session", session_id)
        material = _phase4_call(graph, "get_material", material_id)
        if session is None or material is None:
            return terminal_or_recover("approved Material Usage dependency was deleted")
        if not _phase4_graph_id_matches(session, session_id, "S") or not _phase4_graph_id_matches(
            material, material_id, "M"
        ):
            return terminal_or_recover("approved Session or Material logical ID changed")
        session_course = _phase4_course(graph, session)
        material_course = _phase4_course(graph, material)
        if session_course is None or material_course is None or session_course != material_course:
            return terminal_or_recover("approved Material Usage Course identity changed")
        if (
            session_course.relation_page_id != semantics["course_relation_page_id"]
            or session_course.course_key != semantics["course_key"]
        ):
            return terminal_or_recover("approved Material Usage Course binding changed")

        current_type = field(material, "Type", "material_type", default=None)
        if current_type != material_type:
            return terminal_or_recover("approved Material Type changed")
        mapping = _phase4_config_mapping(self._config, "material_type_source_class")
        if material_source_class(current_type, mapping) != source_class:
            return terminal_or_recover("approved Material Type authority mapping changed")

        usage_rows = _phase4_call(graph, "get_material_usage", session_id) or []
        try:
            _phase4_assert_unique_usage_rows(usage_rows, target_id)
        except PolicyViolation:
            return terminal_or_recover("approved Material Usage lookup is physically ambiguous")
        scopes = material_usage_scopes(usage_rows, session_id=session_id)
        targets = [scope for scope in scopes if scope.usage_id == target_id]
        if len(targets) != 1:
            return terminal_or_recover("approved Material Usage target is missing or ambiguous")
        target_scope = targets[0]
        if (
            target_scope.material_id != material_id
            or target_scope.role != role
            or target_scope.range != desired
        ) and operation == "create_usage":
            # A create/verification action is only allowed to verify the exact
            # approved target snapshot; it never rewrites scope.
            return terminal_or_recover(
                "approved Material Usage target scope changed",
                terminal_on_marker=True,
            )
        if not _phase4_old_snapshot_matches(target_scope, old_snapshot) and not _phase4_target_scope_is_desired(
            target_scope,
            operation=operation,
            material_id=material_id,
            role=role,
            desired_range=desired,
            old_snapshot=old_snapshot,
        ):
            return terminal_or_recover(
                "approved Material Usage target snapshot changed",
                terminal_on_marker=True,
            )

        session_ref = _phase4_resolve_graph_source(
            self._source_binding_resolver,
            graph,
            session,
            session_id,
            ("Normalized Transcript", "normalized_transcript"),
        )
        material_ref = _phase4_resolve_graph_source(
            self._source_binding_resolver,
            graph,
            material,
            material_id,
            ("Normalized Source", "normalized_source"),
        )
        expected_session_ref = _phase4_dependency_ref(semantics["session_dependency"])
        expected_material_ref = _phase4_dependency_ref(semantics["material_dependency"])
        if session_ref.identity != expected_session_ref.identity or material_ref.identity != expected_material_ref.identity:
            return terminal_or_recover("approved source binding changed")
        session_fp = _phase4_source_fingerprint(source, session_ref)
        material_fp = _phase4_source_fingerprint(source, material_ref)
        if (
            session_fp != _phase4_dependency_fp(semantics["session_dependency"])
            or material_fp != _phase4_dependency_fp(semantics["material_dependency"])
        ):
            return terminal_or_recover("approved source fingerprint changed")
        session_derivative = _phase4_source_read(source, session_ref)
        material_derivative = _phase4_source_read(source, material_ref)
        try:
            session_context = prepare_derivative(
                session_derivative,
                expected_entity_id=session_id,
                current_fingerprint=session_fp,
                kind="session",
                expected_source_ref=session_ref,
                max_chunks=8,
            )
            material_context = prepare_derivative(
                material_derivative,
                expected_entity_id=material_id,
                current_fingerprint=material_fp,
                kind="material",
                expected_source_ref=material_ref,
                max_chunks=None,
            )
        except Exception as exc:
            if isinstance(exc, PolicyViolation):
                raise
            return terminal_or_recover(f"approved source derivative is no longer current: {exc}")
        if (
            session_context.front_matter.get("course_key") != session_course.course_key
            or material_context.front_matter.get("course_key") != session_course.course_key
        ):
            return terminal_or_recover("approved derivative Course identity changed")
        material_chunks = _phase4_page_chunks(material_context, desired)
        if not material_chunks:
            return terminal_or_recover("approved page range is not present in the current material")

        resulting_range = (
            desired
            if operation in {"update_range", "page_range"}
            else target_scope.range
        )
        duplicate = _phase4_has_sibling_duplicate(
            usage_rows,
            target_id,
            material_id,
            role,
            resulting_range,
            session_id,
        )
        if duplicate:
            return terminal_or_recover("would create a duplicate sibling Material Usage")

        reconcile_kwargs = {
            "material_id": material_id,
            "role": role,
            "operation": operation,
            "desired_range": desired,
            "expected_course": (session_course.relation_page_id, session_course.course_key),
            "expected_material_type": material_type,
            "expected_source_class": source_class,
            "session_ref": session_ref,
            "material_ref": material_ref,
            "session_fp": session_fp,
            "material_fp": material_fp,
            "source_binding_resolver": self._source_binding_resolver,
            "source_reader": source,
        }
        preflight = self._phase4_reconcile_target(
            graph, session_id, target_id, target_patch, desired_verified,
            old_snapshot, strict_availability=True, **reconcile_kwargs,
        )
        if preflight not in {"old", "desired"}:
            return ApprovalApplyResult(
                proposal_id, QueueState.APPROVED, False,
                "target/source basis changed or is unavailable before mutation",
            )
        already_desired = preflight == "desired"
        if already_desired:
            if marker_phase == _PHASE4_APPLY_MARKER_PHASE_EFFECT_OBSERVED:
                # A previously observed effect is attributable evidence for
                # an audit-only replay.  No target write is needed.
                pass
            elif marker_phase == _PHASE4_APPLY_MARKER_PHASE_PREPARED:
                # A prepared marker may survive a crash before dispatch.  A
                # current desired target can therefore be a human/external
                # edit or an ambiguous provider outcome, never proof by
                # itself.
                return self._phase4_recoverable(
                    proposal_id,
                    "prepared marker has no attributable target effect",
                )
            else:
                return self._mark_terminal(
                    proposal_id,
                    QueueState.SUPERSEDED,
                    "target changed to desired state without a prior application attempt",
                )
        mutated = False
        if not already_desired:
            # A marker from an earlier target attempt makes an old or
            # otherwise non-desired target state unrecoverable by blind retry.
            # Only an exact desired read-back may proceed to audit-only replay.
            if marker_present:
                return self._phase4_recoverable(
                    proposal_id,
                    "previous target attempt is not at the exact desired state",
                )

            # Graph/source reads may take time.  Arm the durable marker, then
            # repeat the trusted dependency and target checks before writing.
            if not self._phase4_arm_marker(
                proposal_id,
                proposal_type,
                semantics,
                target_patch,
            ):
                return self._phase4_recoverable(
                    proposal_id,
                    "write-ahead marker persistence is not verified",
                )
            marker_present = True
            marker_phase = _PHASE4_APPLY_MARKER_PHASE_PREPARED
            post_arm = self._phase4_reconcile_target(
                graph, session_id, target_id, target_patch, desired_verified,
                old_snapshot, strict_availability=True, **reconcile_kwargs,
            )
            if post_arm == "desired":
                # This invocation armed the marker but has not called the
                # target writer. Equality here cannot attribute an external
                # edit to this proposal. A marker from a prior invocation is
                # deliberately handled separately by preflight recovery.
                return self._mark_terminal(
                    proposal_id,
                    QueueState.SUPERSEDED,
                    "target changed to desired state before target application",
                )
            elif post_arm != "old":
                return self._phase4_recoverable(
                    proposal_id,
                    "target/source basis changed or is unavailable after marker arm",
                )

        if not already_desired:
            # The marker write itself is a provider operation.  Re-establish
            # the current human approval and exact physical Queue row
            # immediately before the target mutation.
            checked_queue = self._phase4_approved_queue(
                proposal_id,
                proposal_type,
                semantics,
                target_patch,
            )
            if checked_queue is None:
                return self._phase4_recoverable(
                    proposal_id,
                    "approved Queue row is not authoritatively visible",
                )
            pre_write, pre_write_marker = checked_queue
            if pre_write_marker != _PHASE4_APPLY_MARKER_PHASE_PREPARED:
                return self._phase4_recoverable(
                    proposal_id,
                    "write-ahead marker is not in prepared phase before target write",
                )
            decision_by = _phase4_decision_by(pre_write, self._decision_by)
            try:
                _guarded_update(self._adapter, self._actor, "Material Usage", target_id, target_patch)
                mutated = True
            except Exception as exc:
                outcome = self._phase4_reconcile_target(
                    graph,
                    session_id,
                    target_id,
                    target_patch,
                    desired_verified,
                    old_snapshot,
                    **reconcile_kwargs,
                )
                if outcome == "desired":
                    if isinstance(exc, ProviderWriteNotAppliedError):
                        # The provider explicitly guarantees that this target
                        # invocation did not apply.  A desired read-back in
                        # this branch cannot be attributed to this proposal.
                        return self._phase4_recoverable(
                            proposal_id,
                            "trusted no-effect outcome has no attributable target effect",
                        )
                    mutated = True
                elif outcome == "old":
                    if not isinstance(exc, ProviderWriteNotAppliedError):
                        # Old can be a human restoration after a committed
                        # write. Only a trusted adapter's explicit guarantee
                        # permits disarming this attempt's durable marker.
                        return self._phase4_recoverable(
                            proposal_id,
                            "old snapshot does not prove the target write was not applied",
                        )
                    cleared = self._phase4_clear_marker(
                        proposal_id,
                        proposal_type,
                        semantics,
                        target_patch,
                    )
                    if not cleared:
                        return self._phase4_recoverable(
                            proposal_id,
                            "target write may not have occurred and marker disarm is unverified",
                        )
                    return ApprovalApplyResult(
                        proposal_id,
                        QueueState.APPROVED,
                        False,
                        f"target write outcome is retryable: {type(exc).__name__}: {exc}",
                    )
                else:
                    return ApprovalApplyResult(
                        proposal_id,
                        QueueState.APPROVED,
                        False,
                        "target write outcome requires reconciliation",
                    )
            else:
                outcome = self._phase4_reconcile_target(
                    graph,
                    session_id,
                    target_id,
                    target_patch,
                    desired_verified,
                    old_snapshot,
                    **reconcile_kwargs,
                )
            if outcome != "desired":
                return ApprovalApplyResult(
                    proposal_id,
                    QueueState.APPROVED,
                    mutated,
                    "target read-back requires reconciliation",
                )
            if not self._phase4_mark_effect_observed(
                proposal_id,
                proposal_type,
                semantics,
                target_patch,
            ):
                return ApprovalApplyResult(
                    proposal_id,
                    QueueState.APPROVED,
                    mutated,
                    "effect-observed marker persistence is not verified",
                )
            marker_phase = _PHASE4_APPLY_MARKER_PHASE_EFFECT_OBSERVED

        # Persisting the effect marker is an external operation. Revalidate
        # the complete target/source basis after it, including audit-only
        # retries, before the final unique Queue check and completion audit.
        # A later human edit must never be overwritten or audited as current.
        audit_outcome = self._phase4_reconcile_target(
            graph,
            session_id,
            target_id,
            target_patch,
            desired_verified,
            old_snapshot,
            strict_availability=True,
            **reconcile_kwargs,
        )
        if audit_outcome != "desired":
            return ApprovalApplyResult(
                proposal_id,
                QueueState.APPROVED,
                mutated,
                "target/source basis requires reconciliation before audit",
            )

        latest = _read_approval(
            self._adapter,
            proposal_id,
            automation_queue_id=self._queue_db_id,
            automation_queue_ids=set(self._queue_ids or ()),
            require_unique_physical=True,
        )
        if latest is None:
            raise PolicyViolation("approval Queue row disappeared before audit")
        expected_type = (
            ProposalType.MATERIAL_USAGE
            if operation == "create_usage"
            else ProposalType.PAGE_RANGE
        )
        if coerce_proposal_type(
            _get_field(latest, "Proposal Type", "proposal_type", default=None)
        ) is not expected_type:
            raise PolicyViolation("Phase4 approval type changed before audit")
        _validate_phase4_queue_identity(latest)
        if canonical_semantics_from_queue(latest) != semantics:
            raise PolicyViolation("Phase4 approval content changed before audit")
        latest_marker_phase = _phase4_apply_marker_phase(
            latest,
            proposal_id=proposal_id,
            proposal_type=proposal_type,
            semantics=semantics,
            target_patch=target_patch,
        )
        if latest_marker_phase != marker_phase:
            marker_reason = (
                "write-ahead marker changed before audit"
                if latest_marker_phase is not None
                else "write-ahead marker is not present for audit recovery"
            )
            return self._phase4_recoverable(
                proposal_id,
                marker_reason,
            )
        if marker_phase != _PHASE4_APPLY_MARKER_PHASE_EFFECT_OBSERVED:
            return self._phase4_recoverable(
                proposal_id,
                "durable effect-observed marker is not verified before audit",
            )
        latest_state = coerce_queue_state(_get_field(latest, "State", default=None))
        latest_decision = coerce_decision(_decision_field(latest))
        if applied_audit_incomplete:
            if latest_state is not QueueState.APPLIED or latest_decision is not Decision.Approve:
                return ApprovalApplyResult(
                    proposal_id,
                    QueueState.APPROVED,
                    False,
                    "incomplete APPLIED audit changed before recovery",
                )
            if _phase4_audit_fields_complete(latest):
                self._phase4_clear_applied_marker(
                    proposal_id,
                    proposal_type,
                    semantics,
                    target_patch,
                )
                return ApprovalApplyResult(proposal_id, QueueState.APPLIED, False, "already applied")
            return self._phase4_repair_applied_audit(
                proposal_id,
                proposal_type,
                semantics,
                target_patch,
                latest,
            )
        if latest_state is not QueueState.APPROVED or latest_decision is not Decision.Approve:
            return ApprovalApplyResult(
                proposal_id,
                QueueState.APPROVED,
                mutated,
                "human approval changed before audit",
            )
        decision_by = _phase4_decision_by(latest, self._decision_by)
        now = self._timestamp()
        decision_at = _get_field(latest, "Decision At", "decision_at", default=None) or now
        queue_patch = {
            "Decision By": decision_by,
            "Decision At": decision_at,
            "Applied At": now,
            "State": QueueState.APPLIED.value,
        }
        try:
            _guarded_update(
                self._adapter,
                self._actor,
                self._queue_db_id,
                _physical_or_logical_id(latest, proposal_id),
                queue_patch,
                automation_queue_ids=set(self._queue_ids or ()),
            )
        except Exception as exc:
            if self._phase4_confirm_applied_queue(
                proposal_id,
                proposal_type,
                semantics,
                target_patch,
                marker_expected=marker_present,
            ):
                # APPLIED is durable enough to replay idempotently.  Leave
                # the marker in place when the provider raised ambiguously;
                # cleanup is optional once terminal state is confirmed.
                return ApprovalApplyResult(proposal_id, QueueState.APPLIED, mutated, None)
            return ApprovalApplyResult(
                proposal_id,
                QueueState.APPROVED,
                mutated,
                f"approval audit update pending: {type(exc).__name__}: {exc}",
            )
        if not self._phase4_confirm_applied_queue(
            proposal_id,
            proposal_type,
            semantics,
            target_patch,
            marker_expected=marker_present,
        ):
            return ApprovalApplyResult(
                proposal_id,
                QueueState.APPROVED,
                mutated,
                "approval audit read-back requires reconciliation",
            )
        if marker_present:
            # This write is intentionally separate from the APPLIED audit.
            # If it is ambiguous, the already-terminal row safely retains the
            # marker and a later idempotent replay will not touch the target.
            self._phase4_clear_applied_marker(
                proposal_id,
                proposal_type,
                semantics,
                target_patch,
            )
        return ApprovalApplyResult(proposal_id, QueueState.APPLIED, mutated, None)

    def _phase4_reconcile_target(
        self,
        graph: Any,
        session_id: str,
        target_id: str,
        patch: Mapping[str, Any],
        desired_verified: bool,
        old_snapshot: Mapping[str, Any],
        *,
        material_id: str,
        role: str,
        operation: str,
        desired_range: Any,
        expected_course: tuple[str, str],
        expected_material_type: str,
        expected_source_class: str,
        session_ref: Any,
        material_ref: Any,
        session_fp: Any,
        material_fp: Any,
        source_binding_resolver: Any,
        source_reader: Any,
        strict_availability: bool = False,
    ) -> str:
        try:
            from uls.enrichment._common import prepare_derivative
            from uls.retrieval._compat import field
            from uls.retrieval.authority import material_source_class
            from uls.retrieval.scope import material_usage_scopes

            # Reconcile only against the same trusted graph/source identity
            # that was approved.  A provider timeout must never be resolved by
            # accepting a target row whose Course, Material Type, source ref,
            # fingerprint, or Usage scope changed in the meantime.
            session = _phase4_call(graph, "get_session", session_id)
            material = _phase4_call(graph, "get_material", material_id)
            if session is None or material is None:
                return "unknown"
            if not _phase4_graph_id_matches(session, session_id, "S") or not _phase4_graph_id_matches(
                material, material_id, "M"
            ):
                return "unknown"
            course = _phase4_course(graph, session)
            material_course = _phase4_course(graph, material)
            if (
                course is None
                or material_course is None
                or course != material_course
                or (course.relation_page_id, course.course_key) != expected_course
            ):
                return "unknown"
            current_type = field(material, "Type", "material_type", default=None)
            if current_type != expected_material_type:
                return "unknown"
            mapping = _phase4_config_mapping(self._config, "material_type_source_class")
            if material_source_class(current_type, mapping) != expected_source_class:
                return "unknown"
            current_session_ref = _phase4_resolve_graph_source(
                source_binding_resolver,
                graph,
                session,
                session_id,
                ("Normalized Transcript", "normalized_transcript"),
            )
            current_material_ref = _phase4_resolve_graph_source(
                source_binding_resolver,
                graph,
                material,
                material_id,
                ("Normalized Source", "normalized_source"),
            )
            if (
                current_session_ref.identity != session_ref.identity
                or current_material_ref.identity != material_ref.identity
                or _phase4_source_fingerprint(source_reader, current_session_ref) != session_fp
                or _phase4_source_fingerprint(source_reader, current_material_ref) != material_fp
            ):
                return "unknown"
            session_context = prepare_derivative(
                _phase4_source_read(source_reader, current_session_ref),
                expected_entity_id=session_id,
                current_fingerprint=session_fp,
                kind="session",
                expected_source_ref=current_session_ref,
                max_chunks=8,
            )
            material_context = prepare_derivative(
                _phase4_source_read(source_reader, current_material_ref),
                expected_entity_id=material_id,
                current_fingerprint=material_fp,
                kind="material",
                expected_source_ref=current_material_ref,
                max_chunks=None,
            )
            if (
                session_context.front_matter.get("course_key") != expected_course[1]
                or material_context.front_matter.get("course_key") != expected_course[1]
            ):
                return "unknown"
            if not _phase4_page_chunks(material_context, desired_range):
                return "unknown"
            # Body reads are external calls too; they must not outlive the
            # fingerprint or graph basis used for the final write/readback.
            if (
                _phase4_source_fingerprint(source_reader, current_session_ref) != session_fp
                or _phase4_source_fingerprint(source_reader, current_material_ref) != material_fp
            ):
                return "unknown"
            final_session = _phase4_call(graph, "get_session", session_id)
            final_material = _phase4_call(graph, "get_material", material_id)
            if not _phase4_graph_id_matches(final_session, session_id, "S") or not _phase4_graph_id_matches(
                final_material, material_id, "M"
            ):
                return "unknown"
            final_course = _phase4_course(graph, final_session)
            final_material_course = _phase4_course(graph, final_material)
            if (
                final_course != course or final_material_course != material_course
                or field(final_material, "Type", "material_type", default=None) != expected_material_type
                or _phase4_resolve_graph_source(source_binding_resolver, graph, final_session,
                     session_id, ("Normalized Transcript", "normalized_transcript")).identity != session_ref.identity
                or _phase4_resolve_graph_source(source_binding_resolver, graph, final_material,
                     material_id, ("Normalized Source", "normalized_source")).identity != material_ref.identity
            ):
                return "unknown"
            rows = _phase4_call(graph, "get_material_usage", session_id)
            _phase4_assert_unique_usage_rows(rows or [], target_id)
            if _phase4_has_sibling_duplicate(rows or [], target_id, material_id, role,
                                             desired_range, session_id):
                return "unknown"
            target = _phase4_find_usage(rows or [], target_id)
            if target is None:
                return "unknown"
            scopes = material_usage_scopes(rows or [], session_id=session_id)
            targets = [scope for scope in scopes if scope.usage_id == target_id]
            if len(targets) != 1:
                return "unknown"
            scope = targets[0]
            if (
                scope.session_id != session_id
                or scope.material_id != material_id
                or scope.role != role
            ):
                return "unknown"
            if _phase4_target_matches(target, patch, desired_verified) and _phase4_target_scope_is_desired(
                scope,
                operation=operation,
                material_id=material_id,
                role=role,
                desired_range=desired_range,
                old_snapshot=old_snapshot,
            ):
                return "desired"
            old_patch = {
                "Start Page": old_snapshot.get("start_page"),
                "End Page": old_snapshot.get("end_page"),
            }
            if old_snapshot.get("verified") is not None:
                old_patch["Verified"] = old_snapshot.get("verified")
            if _phase4_old_snapshot_matches(scope, old_snapshot) and _phase4_target_matches(
                target, old_patch, old_snapshot.get("verified")
            ):
                return "old"
        except (ProviderUnavailableError, ProviderRateLimitedError):
            if strict_availability:
                raise
            return "unknown"
        except Exception:
            return "unknown"
        return "unknown"

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
        proposal_type_value = _wire_value(
            _get_field(current, "Proposal Type", "proposal_type", default=None)
        )
        if proposal_type_value in {
            ProposalType.MATERIAL_USAGE.value,
            ProposalType.PAGE_RANGE.value,
        }:
            return self._apply_phase4(proposal_id, current)
        state_value = _get_field(current, "State", "state", default=None)
        decision_value = _decision_field(current)
        if state_value is None or decision_value is None:
            raise PolicyViolation("Approval proposal must contain State and Decision")
        state = coerce_queue_state(state_value)
        decision = coerce_decision(decision_value)

        decision_by = _get_field(current, "Decision By", "decision_by", default=None)
        if not _is_human_decision_by(decision_by):
            raise PolicyViolation("human decision_by required")
        if self._decision_by is not None and not _is_human_decision_by(self._decision_by):
            raise PolicyViolation("human decision_by required")

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
            required=proposal_type
            in {
                ProposalType.MATERIAL_USAGE,
                ProposalType.EXAM_SCOPE,
                ProposalType.MATERIAL_REVISION,
            },
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
        target_current = {
            key: _get_field(target, key, default=_PATCH_MISSING) for key in patch
        }
        target_already_matches = all(
            value is not _PATCH_MISSING and _same_stored_value(value, patch[key])
            for key, value in target_current.items()
        )
        mutated = False
        try:
            if not target_already_matches:
                _guarded_update(self._adapter, self._actor, target_db, target_id, patch)
                mutated = True
        except Exception as exc:
            return self._mark_terminal(
                proposal_id,
                QueueState.FAILED,
                f"approval application failed: {type(exc).__name__}: {exc}",
            )

        now = self._timestamp()
        decision_at = _get_field(current, "Decision At", "decision_at", default=None) or now
        queue_patch = {
            "Decision By": decision_by,
            "Decision At": decision_at,
            "Applied At": now,
            "State": QueueState.APPLIED.value,
        }
        try:
            _guarded_update(
                self._adapter,
                self._actor,
                self._queue_db_id,
                proposal_id,
                queue_patch,
                automation_queue_ids=set(self._queue_ids or ()),
            )
        except Exception as exc:
            # The target mutation may already have committed.  Keep the
            # proposal APPROVED so a later reconciliation/replay can write the
            # missing APPLIED audit without applying the target twice.
            return ApprovalApplyResult(
                proposal_id,
                QueueState.APPROVED,
                mutated,
                f"approval audit update pending: {type(exc).__name__}: {exc}",
            )
        return ApprovalApplyResult(proposal_id, QueueState.APPLIED, mutated, None)


def _phase4_audit_value_present(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _phase4_audit_fields_complete(record: Any) -> bool:
    return (
        _is_human_decision_by(_get_field(record, "Decision By", "decision_by", default=None))
        and _phase4_audit_value_present(
            _get_field(record, "Decision At", "decision_at", default=None)
        )
        and _phase4_audit_value_present(
            _get_field(record, "Applied At", "applied_at", default=None)
        )
    )


def _phase4_decision_by(record: Any, trusted_reviewer: Any) -> str:
    stored = _get_field(record, "Decision By", "decision_by", default=None)
    selected = stored if _is_human_decision_by(stored) else trusted_reviewer
    if not _is_human_decision_by(selected):
        raise PolicyViolation("no attributable human decision_by is available")
    assert isinstance(selected, str)
    return selected


def _phase4_call(reader: Any, method_name: str, *args: Any) -> Any:
    method = getattr(reader, method_name, None)
    if not callable(method):
        raise PolicyViolation(f"Phase4 reader has no {method_name} method")
    try:
        return method(*args)
    except UlsError:
        raise
    except Exception as exc:
        raise ProviderUnavailableError(f"Phase4 {method_name} lookup is unavailable") from exc


def _phase4_graph_id_matches(record: Any, expected_id: str, expected_type: str) -> bool:
    """Bind a current graph record's exact logical ``ID`` property."""

    from uls.retrieval.scope import usage_app_id

    actual = strict_entity_id(usage_app_id(record), expected_type)
    expected = strict_entity_id(expected_id, expected_type)
    return actual is not None and actual == expected


def _phase4_course(reader: Any, record: Any) -> Any | None:
    from uls.domain.course_identity import resolve_course_relation, validate_course_record
    from uls.retrieval._compat import raw_field

    relation_id = resolve_course_relation(raw_field(record, "Course", "course", default=None))
    if relation_id is None:
        return None
    course = _phase4_call(reader, "get_course_by_relation_id", relation_id)
    return validate_course_record(course, relation_id)


def _phase4_config_mapping(config: Any, name: str) -> Mapping[str, str]:
    if config is None:
        from uls.config.schema import RetrievalCfg

        return RetrievalCfg().material_type_source_class
    section = config.get("retrieval", config) if isinstance(config, Mapping) else getattr(config, "retrieval", config)
    value = section.get(name, {}) if isinstance(section, Mapping) else getattr(section, name, {})
    if not isinstance(value, Mapping):
        raise PolicyViolation("Phase4 material type source mapping is invalid")
    return value


def _phase4_dependency_ref(value: Mapping[str, Any]) -> Any:
    from uls.domain.source_ref import SourceRef

    ref = value.get("source_ref")
    if not isinstance(ref, Mapping):
        raise PolicyViolation("Phase4 source dependency has no structured SourceRef")
    provider = ref.get("provider")
    file_id = ref.get("file_id")
    if not isinstance(provider, str) or not isinstance(file_id, str) or not provider.strip() or not file_id.strip():
        raise PolicyViolation("Phase4 source dependency has an invalid SourceRef")
    if file_id.casefold().startswith(("http://", "https://")):
        raise PolicyViolation("Phase4 source dependency cannot use a URL as file_id")
    return SourceRef(provider.strip(), file_id.strip())


def _phase4_dependency_fp(value: Mapping[str, Any]) -> Any:
    from uls.domain.source_ref import SourceFingerprint

    version = value.get("source_version")
    source_hash = value.get("source_hash")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version < 1
        or not isinstance(source_hash, str)
        or not source_hash.strip()
    ):
        raise PolicyViolation("Phase4 source dependency has an invalid fingerprint")
    return SourceFingerprint(version, source_hash.strip())


def _phase4_source_fingerprint(reader: Any, ref: Any) -> Any:
    from uls.enrichment._common import coerce_fingerprint

    try:
        value = reader.get_current_fingerprint(ref)
    except UlsError:
        raise
    except Exception as exc:
        raise ProviderUnavailableError("Phase4 source fingerprint lookup is unavailable") from exc
    if value is None:
        return None
    try:
        fingerprint = coerce_fingerprint(value)
        if (
            type(fingerprint.source_version) is not int
            or fingerprint.source_version < 1
            or not isinstance(fingerprint.source_hash, str)
            or not fingerprint.source_hash.strip()
            or fingerprint.source_hash != fingerprint.source_hash.strip()
        ):
            return None
        return fingerprint
    except (TypeError, ValueError):
        # A missing/malformed current fingerprint is a stale dependency, not
        # an invitation to use a graph hash or to retry an unbound source.
        return None


def _phase4_source_read(reader: Any, ref: Any) -> Any:
    try:
        return reader.read_derived(ref)
    except UlsError:
        raise
    except Exception as exc:
        raise ProviderUnavailableError("Phase4 normalized source read is unavailable") from exc


def _phase4_resolve_graph_source(
    resolver: Any,
    reader: Any,
    record: Any,
    entity_id: str,
    names: Sequence[str],
) -> Any:
    del reader
    from uls.domain.source_ref import SourceRef
    from uls.retrieval._compat import field

    pointer = field(record, *names, default=None)
    if not isinstance(pointer, str) or not pointer.strip():
        raise PolicyViolation("Phase4 normalized source pointer is missing")
    try:
        resolved = resolver.resolve_derivative_ref(entity_id, pointer.strip())
    except UlsError:
        raise
    except Exception as exc:
        raise ProviderUnavailableError("Phase4 trusted source binding is unavailable") from exc
    if not isinstance(resolved, SourceRef):
        raise PolicyViolation("Phase4 trusted source binding returned no SourceRef")
    return resolved


def _phase4_range(value: Mapping[str, Any]) -> Any:
    from uls.domain.page_range import parse_page_range

    result = parse_page_range(value.get("start_page"), value.get("end_page"))
    if not result.is_valid or result.value is None:
        raise PolicyViolation("Phase4 action contains an invalid page range")
    return result.value


def _phase4_page_chunks(context: Any, page_range: Any) -> list[Any]:
    """Return evidence only when the complete requested range is indexed."""

    chunks = list(getattr(context, "chunks", ()))
    if page_range.is_whole_source:
        return chunks
    selected = [
        chunk
        for chunk in chunks
        if page_range.contains_page(getattr(chunk.locator, "start_page", -1))
        and page_range.contains_page(getattr(chunk.locator, "end_page", -1))
    ]
    # Strict material contexts contain one indexed page per locator. Count
    # distinct pages in the bounded subset instead of iterating a potentially
    # huge claimed range: any missing head, middle or tail page rejects it.
    pages = {
        chunk.locator.start_page
        for chunk in selected
        if chunk.locator.start_page == chunk.locator.end_page
    }
    if len(pages) != page_range.end_page - page_range.start_page + 1:
        return []
    return selected


def _phase4_old_snapshot_matches(scope: Any, snapshot: Mapping[str, Any]) -> bool:
    return (
        snapshot.get("usage_id") == scope.usage_id
        and snapshot.get("session_id") == scope.session_id
        and snapshot.get("material_id") == scope.material_id
        and snapshot.get("role") == scope.role
        and snapshot.get("start_page") == scope.start_page
        and snapshot.get("end_page") == scope.end_page
        and snapshot.get("verified") is scope.verified
    )


def _phase4_target_scope_is_desired(
    scope: Any,
    *,
    operation: str,
    material_id: str,
    role: str,
    desired_range: Any,
    old_snapshot: Mapping[str, Any],
) -> bool:
    """Recognize an already-applied target during audit recovery."""

    if scope.material_id != material_id or scope.role != role:
        return False
    if scope.range != desired_range:
        return False
    if operation == "create_usage":
        return scope.verified is True
    if operation in {"update_range", "page_range"}:
        return scope.verified is old_snapshot.get("verified")
    return False


def _phase4_find_usage(rows: Sequence[Any], usage_id: str) -> Any | None:
    from uls.retrieval.scope import usage_app_id

    matches = [row for row in rows if usage_app_id(row) == usage_id]
    return matches[0] if len(matches) == 1 else None


def _phase4_assert_unique_usage_rows(rows: Sequence[Any], target_id: str) -> None:
    """Require one physical target; unrelated ID defects do not veto it.

    Raw exact-tuple siblings are checked separately, independently of ID and
    Verified validity. Never deduplicate the target's physical matches.
    """

    from uls.retrieval.scope import usage_app_id

    if sum(usage_app_id(row) == target_id for row in rows) != 1:
        raise PolicyViolation("Material Usage target is missing or physically ambiguous")


def _phase4_has_sibling_duplicate(
    rows: Sequence[Any],
    target_id: str,
    material_id: str,
    role: str,
    page_range: Any,
    session_id: str,
) -> bool:
    from uls.retrieval.scope import (
        material_usage_identity,
        material_usage_identity_counts,
        usage_app_id,
    )

    identity = (session_id, material_id, role, page_range)
    counts = material_usage_identity_counts(rows, session_id=session_id)
    if counts.get(identity, 0) == 0:
        return False
    return any(
        usage_app_id(row) != target_id and material_usage_identity(row) == identity
        for row in rows
    )


def _phase4_target_matches(
    target: Any,
    patch: Mapping[str, Any],
    desired_verified: bool | None,
) -> bool:
    for name, expected in patch.items():
        actual = _get_field(
            target,
            name,
            name.casefold().replace(" ", "_"),
            default=_PATCH_MISSING,
        )
        if actual is _PATCH_MISSING or not _same_stored_value(actual, expected):
            return False
    if desired_verified is not None:
        actual_verified = _get_field(target, "Verified", "verified", default=_PATCH_MISSING)
        return actual_verified is desired_verified
    return True


def upsert_proposal(
    adapter: NotionAdapter,
    properties: Mapping[str, Any] | Proposal,
    *,
    automation_queue_id: str | None = None,
    automation_queue_db_id: str | None = None,
    automation_queue_ids: set[str] | None = None,
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
    if automation_queue_id is not None and automation_queue_db_id is not None:
        raise TypeError("pass either automation_queue_id or automation_queue_db_id, not both")
    if automation_queue_id is None:
        automation_queue_id = automation_queue_db_id
    queue_db_id, queue_ids = _queue_target(automation_queue_id, automation_queue_ids)

    proposal_id = _proposal_id(proposal_properties)
    proposal_type_value = _get_field(
        proposal_properties, "Proposal Type", "proposal_type", default=None
    )
    if isinstance(proposal_type_value, Enum):
        proposal_type_value = proposal_type_value.value
    if proposal_type_value in {ProposalType.MATERIAL_USAGE.value, ProposalType.PAGE_RANGE.value}:
        return _upsert_phase4_proposal(
            adapter,
            proposal_properties,
            proposal_id=proposal_id,
            proposal_type=str(proposal_type_value),
            queue_db_id=queue_db_id,
            queue_ids=set(queue_ids or ()),
        )

    patch = dict(proposal_properties)
    patch.setdefault("Decision", Decision.Pending.value)
    patch.setdefault("State", QueueState.PENDING_REVIEW.value)
    # Validate the creation form before lookup.  In particular, a malicious
    # retry cannot turn an existing item into an approval by supplying
    # Decision=Approve or an approval State.
    enforce_write_policy(
        AutomationActor.AUTOMATION,
        queue_db_id,
        patch,
        automation_queue_ids=set(queue_ids or ()),
        is_create=True,
    )
    if _wire_value(patch["Decision"]) != Decision.Pending.value:
        raise PolicyViolation("A new/upserted automation proposal must have Decision=Pending")
    if _wire_value(patch["State"]) != QueueState.PENDING_REVIEW.value:
        raise PolicyViolation("A new/upserted automation proposal must have State=PENDING_REVIEW")

    existing = _read_approval(
        adapter,
        proposal_id,
        automation_queue_id=queue_db_id,
        automation_queue_ids=set(queue_ids or ()),
    )
    if existing is not None:
        # Never write human-owned decision/state or decision audit fields
        # during a retry.  The existing state is intentionally left untouched.
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
        if metadata:
            _guarded_update(
                adapter,
                AutomationActor.AUTOMATION,
                queue_db_id,
                proposal_id,
                metadata,
                automation_queue_ids=set(queue_ids or ()),
            )
        return existing

    return _guarded_create(
        adapter,
        AutomationActor.AUTOMATION,
        queue_db_id,
        patch,
        automation_queue_ids=set(queue_ids or ()),
    )


def _create_phase4_queue_once(
    adapter: Any,
    properties: Mapping[str, Any],
    create: Callable[[], Any],
    *,
    queue_db_id: str = AUTOMATION_QUEUE,
    queue_ids: set[str] | None = None,
) -> Any:
    """Guard every public Phase4 Queue create, including unknown outcomes.

    The callback is a single backend primitive, not an upsert or recursive
    call to this adapter. Provider return values are never visibility proof.
    """
    from uls.domain.approval_identity import canonical_semantics_from_queue

    proposal_type = _wire_value(_get_field(properties, "Proposal Type", default=None))
    if proposal_type not in {"MATERIAL_USAGE", "PAGE_RANGE"}:
        return create()
    _validate_phase4_queue_identity(properties)
    semantics = canonical_semantics_from_queue(properties)
    proposal_id = _proposal_id(properties)

    def read_matching() -> Any:
        row = _read_approval(
            adapter, proposal_id, automation_queue_id=queue_db_id,
            automation_queue_ids=queue_ids, require_unique_physical=True,
        )
        if row is not None:
            _validate_phase4_queue_identity(row)
            if (
                _wire_value(_get_field(row, "Proposal Type", default=None)) != proposal_type
                or canonical_semantics_from_queue(row) != semantics
            ):
                raise PolicyViolation("existing Queue row conflicts with proposed identity")
        return row

    existing = read_matching()
    if existing is not None:
        return existing
    try:
        create()
    except Exception:
        recovered = read_matching()
        if recovered is not None:
            return recovered
        raise
    created = read_matching()
    if created is None:
        raise ProviderUnavailableError(
            "Queue create outcome is awaiting authoritative visibility",
            details={"proposal_id": proposal_id, "reconciliation_required": True},
        )
    return created


def _upsert_phase4_proposal(
    adapter: NotionAdapter,
    supplied: Mapping[str, Any],
    *,
    proposal_id: str,
    proposal_type: str,
    queue_db_id: str,
    queue_ids: set[str],
) -> Any:
    """Strict immutable Queue path for Material Usage and PAGE_RANGE."""

    from uls.domain.approval_identity import (
        canonical_action_json,
        canonical_semantics_from_queue,
        derive_proposal_id,
        parse_action_json,
    )

    forbidden = {
        _normal_key("Target DB"),
        _normal_key("Target Database"),
        _normal_key("Target Fingerprint"),
        _normal_key("Target Snapshot"),
    }
    if any(_normal_key(str(key)) in forbidden for key in supplied):
        raise PolicyViolation(
            "Phase4 Queue schema does not persist Target DB or Target Fingerprint"
        )
    for name in ("Decision By", "Decision At", "Applied At"):
        if _get_field(supplied, name, name.casefold().replace(" ", "_"), default=_MISSING) is not _MISSING:
            raise PolicyViolation("Queue audit fields are applier-only")

    action_value = _get_field(
        supplied, "Proposed Action", "proposed_action", "action", default=_MISSING
    )
    if action_value is _MISSING:
        raise PolicyViolation("Phase4 proposal requires Proposed Action")
    action_mapping = parse_action_json(action_value)
    # Validate all action keys, explicit nulls, and mirrors before any lookup.
    action_record = dict(supplied)
    action_record["Proposed Action"] = action_mapping
    try:
        semantics = canonical_semantics_from_queue(action_record)
    except (TypeError, ValueError) as exc:
        raise PolicyViolation("Phase4 proposal action is malformed") from exc
    expected_id = derive_proposal_id(proposal_type, semantics)
    if proposal_id != expected_id:
        raise PolicyViolation("Proposal ID does not match canonical Phase4 semantics")

    patch = dict(supplied)
    patch["Proposal ID"] = proposal_id
    patch["Proposal Type"] = proposal_type
    patch["Proposed Action"] = canonical_action_json(semantics)
    patch.setdefault("Decision", Decision.Pending.value)
    patch.setdefault("State", QueueState.PENDING_REVIEW.value)
    if _wire_value(patch["Decision"]) != Decision.Pending.value:
        raise PolicyViolation("Phase4 proposal creation requires Decision=Pending")
    if _wire_value(patch["State"]) != QueueState.PENDING_REVIEW.value:
        raise PolicyViolation("Phase4 proposal creation requires State=PENDING_REVIEW")
    _require_queue_display_fields(patch)
    enforce_write_policy(
        AutomationActor.AUTOMATION,
        queue_db_id,
        patch,
        automation_queue_ids=queue_ids,
        is_create=True,
    )

    existing = _read_approval(
        adapter,
        proposal_id,
        automation_queue_id=queue_db_id,
        automation_queue_ids=queue_ids,
        require_unique_physical=True,
    )
    if existing is not None:
        try:
            existing_semantics = canonical_semantics_from_queue(existing)
        except (TypeError, ValueError) as exc:
            raise PolicyViolation("stored Queue row action is malformed") from exc
        if derive_proposal_id(proposal_type, existing_semantics) != proposal_id:
            raise PolicyViolation("stored Queue Proposal ID fails semantic self-validation")
        if existing_semantics != semantics:
            raise PolicyViolation("existing Queue semantics do not match the retry")
        if _wire_value(_get_field(existing, "Proposal Type", default=None)) != proposal_type:
            raise PolicyViolation("existing Queue Proposal Type does not match the retry")
        # Only display metadata is mutable on an idempotent retry.  This never
        # forwards semantic, decision, or audit fields to an existing row.
        display = {
            key: value
            for key, value in patch.items()
            if _normal_key(str(key)) in {_normal_key("Name"), _normal_key("Confidence")}
        }
        if display:
            _guarded_update(
                adapter,
                AutomationActor.AUTOMATION,
                queue_db_id,
                _physical_or_logical_id(existing, proposal_id),
                display,
                automation_queue_ids=queue_ids,
            )
        return existing

    try:
        _guarded_create(
            adapter,
            AutomationActor.AUTOMATION,
            queue_db_id,
            patch,
            automation_queue_ids=queue_ids,
        )
    except Exception:
        # A provider timeout after commit is an unknown outcome.  Re-read the
        # physical Queue set before allowing a retry to create another row.
        recovered = _read_approval(
            adapter,
            proposal_id,
            automation_queue_id=queue_db_id,
            automation_queue_ids=queue_ids,
            require_unique_physical=True,
        )
        if recovered is not None:
            try:
                stored = canonical_semantics_from_queue(recovered)
            except (TypeError, ValueError) as exc:
                raise PolicyViolation("committed Queue row action is malformed") from exc
            if derive_proposal_id(proposal_type, stored) != proposal_id or stored != semantics:
                raise PolicyViolation("committed Queue row does not match the retry")
            return recovered
        raise
    # Every success response, including None, must become an exactly-one
    # authoritative physical row. A returned object is not visibility proof.
    reread = _read_approval(
        adapter, proposal_id, automation_queue_id=queue_db_id,
        automation_queue_ids=queue_ids, require_unique_physical=True,
    )
    if reread is None:
        raise ProviderUnavailableError(
            "Queue create outcome is awaiting authoritative visibility",
            details={"proposal_id": proposal_id, "reconciliation_required": True},
        )
    _validate_phase4_queue_identity(reread)
    if canonical_semantics_from_queue(reread) != semantics:
        raise PolicyViolation("created Queue row does not match proposed semantics")
    return reread


def _require_queue_display_fields(properties: Mapping[str, Any]) -> None:
    name = _get_field(properties, "Name", "name", default=None)
    if not isinstance(name, str) or not name.strip():
        raise PolicyViolation("Phase4 Queue proposal requires a non-empty Name")


def _physical_or_logical_id(record: Any, fallback: str) -> str:
    if isinstance(record, Mapping):
        value = record.get("record_id", record.get("provider_id"))
        if value is None and isinstance(record.get("properties"), Mapping):
            value = record.get("id")
        if isinstance(value, str) and value.strip():
            return value
    value = getattr(record, "record_id", getattr(record, "provider_id", None))
    return value.strip() if isinstance(value, str) and value.strip() else fallback


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
    "MaterialEnrichmentReader",
    "NotionAdapter",
    "NotionReader",
    "PolicyViolation",
    "Proposal",
    "ProposalType",
    "QueueState",
    "aliases_match",
    "coerce_decision",
    "coerce_proposal_type",
    "coerce_queue_state",
    "create_or_update_proposal",
    "derive_queue_state",
    "derive_state_from_decision",
    "enforce_write_policy",
    "find_alias_matches",
    "mark_proposal_failed",
    "mark_proposal_superseded",
    "normalize_alias",
    "parse_aliases",
    "transition_queue_state",
    "transition_state",
    "upsert_automation_proposal",
    "upsert_proposal",
]
