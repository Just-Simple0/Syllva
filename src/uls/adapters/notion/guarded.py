"""Provider-neutral guarded Notion writer for the Phase4 schema boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from uls.domain.approval_identity import parse_action_json
from uls.domain.course_identity import resolve_course_relation
from uls.domain.enums import AutomationActor
from uls.domain.errors import PolicyViolation
from uls.domain.page_range import parse_page_range
from uls.domain.source_ref import SourceRef

from .base import (
    Decision,
    QueueState,
    _call_lookup,
    _call_queue_finder,
    _call_with_supported_signature,
    _create_phase4_queue_once,
    _decision_field,
    _get_field,
    _is_human_decision_by,
    _normal_key,
    _physical_or_logical_id,
    _proposal_id,
    _queue_target,
    _unique_approval_row,
    _validate_phase4_queue_identity,
    coerce_decision,
    coerce_queue_state,
    derive_queue_state,
    enforce_write_policy,
    transition_queue_state,
)

_SCHEMAS: dict[str, frozenset[str]] = {
    "courses": frozenset(
        {"Name", "Aliases", "Course Key", "Code", "Section", "Semester", "Professor", "Status"}
    ),
    "sessions": frozenset(
        {
            "Name",
            "ID",
            "Aliases",
            "Course",
            "Session No",
            "Date",
            "Topics",
            "Status",
            "Recording Folder",
            "Normalized Transcript",
            "Recording Status",
            "Material Usage",
            "Activities",
        }
    ),
    "materials": frozenset(
        {
            "Name",
            "ID",
            "Aliases",
            "Course",
            "Type",
            "Source Folder",
            "Original Filename",
            "Normalized Source",
            "Normalized Annotations",
            "Text Status",
            "Text Source",
            "Visual Dependency",
            "AI Priority",
            "Page Count",
            "Annotation Status",
            "Current Source Version",
            "Material Usage",
        }
    ),
    "materialusage": frozenset(
        {
            "Name",
            "ID",
            "Session",
            "Material",
            "Role",
            "Start Page",
            "End Page",
            "Scope Note",
            "Verified",
            "Evidence",
            "Confidence",
            "Notes",
        }
    ),
    "exams": frozenset(
        {
            "Name",
            "ID",
            "Course",
            "Included Sessions",
            "Source Hash",
            "Source Version",
            "Scope Confirmed",
        }
    ),
    "activities": frozenset(
        {
            "Name",
            "ID",
            "Course",
            "Instructions Source",
            "Normalized Instructions",
            "Related Sessions",
            "Related Materials",
            "Result Type",
            "Submission Ref",
            "Repository Ref",
            "Pull Request Ref",
            "Due",
            "Status",
        }
    ),
    "automationqueue": frozenset(
        {
            "Name",
            "Proposal ID",
            "Proposal Type",
            "State",
            "Course",
            "Target Entity ID",
            "Source Ref",
            "Source Hash",
            "Source Version",
            "Proposed Action",
            "Confidence",
            "Evidence",
            "Review Reason",
            "Decision",
            "Decision By",
            "Decision At",
            "Created",
            "Updated",
            "Applied At",
            "Last Error",
        }
    ),
}


class GuardedNotionWriter:
    """Map logical DB names/UUIDs and apply the common write policy.

    The wrapped provider remains responsible for actual persistence.  This
    class deliberately exposes no graph-reader methods; retrieval and the
    approval applier receive separate read-only dependencies.
    """

    def __init__(
        self,
        backend: Any,
        *,
        database_ids: Mapping[str, str] | None = None,
        automation_queue_id: str | None = None,
    ) -> None:
        self.backend = backend
        self.database_ids = {
            _normal_key(str(logical)): str(identifier)
            for logical, identifier in (database_ids or {}).items()
            if isinstance(logical, str) and isinstance(identifier, str) and identifier.strip()
        }
        configured_queue = automation_queue_id or self.database_ids.get("automationqueue")
        self._queue_db_id, self._queue_ids = _queue_target(
            configured_queue,
            {configured_queue} if isinstance(configured_queue, str) and configured_queue else None,
        )

    def _target(self, target_db: str) -> tuple[str, str]:
        if not isinstance(target_db, str) or not target_db.strip():
            raise PolicyViolation("target database is required")
        normalized = _normal_key(target_db)
        if normalized in _SCHEMAS:
            logical = normalized
            provider_id = self.database_ids.get(logical, target_db)
            return logical, provider_id
        for logical, provider_id in self.database_ids.items():
            if target_db == provider_id:
                return logical, provider_id
        raise PolicyViolation("unknown configured Notion database")

    def _validate_properties(
        self,
        logical: str,
        properties: Mapping[str, Any],
        *,
        is_create: bool,
    ) -> None:
        if any(not isinstance(key, str) for key in properties):
            raise PolicyViolation(f"{logical} property names must be strings")
        allowed = _SCHEMAS[logical]
        unknown = {key for key in properties if key not in allowed}
        if unknown:
            raise PolicyViolation(
                f"{logical} contains properties outside the frozen schema: "
                + ", ".join(sorted(unknown))
            )
        required: dict[str, set[str]] = {
            "materialusage": {"Name", "ID", "Session", "Material", "Role", "Verified"},
            "automationqueue": {
                "Name",
                "Proposal ID",
                "Proposal Type",
                "State",
                "Proposed Action",
                "Decision",
            },
        }
        missing = required.get(logical, set()).difference(properties)
        if is_create and missing:
            raise PolicyViolation(
                f"{logical} is missing required properties: " + ", ".join(sorted(missing))
            )
        if is_create:
            for key in required.get(logical, set()) & _TEXT_FIELDS:
                if not _text_value(properties[key]):
                    raise PolicyViolation(f"{logical}.{key} must be a non-empty string")
        _validate_typed_properties(logical, properties, is_create=is_create)
        if is_create and logical == "automationqueue":
            _validate_phase4_queue_identity(properties)

    def create_entity(
        self,
        target_db: str,
        properties: Mapping[str, Any],
        *,
        actor: AutomationActor = AutomationActor.AUTOMATION,
    ) -> Any:
        logical, provider_id = self._target(target_db)
        if not isinstance(properties, Mapping):
            raise PolicyViolation("create properties must be a mapping")
        self._validate_properties(logical, properties, is_create=True)
        enforce_write_policy(
            actor,
            logical,
            properties,
            automation_queue_ids=set(self._queue_ids or ()),
            is_create=True,
        )
        method = getattr(self.backend, "create_entity", None)
        if not callable(method):
            raise PolicyViolation("wrapped Notion backend has no create_entity")
        def create() -> Any:
            return _call_with_supported_signature(
                method,
                (provider_id, dict(properties), actor),
                {"target_db": provider_id, "properties": dict(properties), "actor": actor},
            )

        if logical == "automationqueue":
            return _create_phase4_queue_once(
                self.backend, properties, create, queue_db_id=provider_id,
                queue_ids=set(self._queue_ids or ()),
            )
        return create()

    def update_properties(
        self,
        target_db: str,
        entity_id: str,
        patch: Mapping[str, Any],
        *,
        actor: AutomationActor = AutomationActor.AUTOMATION,
        system_transition: bool = False,
    ) -> Any:
        logical, provider_id = self._target(target_db)
        if not isinstance(patch, Mapping):
            raise PolicyViolation("update patch must be a mapping")
        self._validate_properties(logical, patch, is_create=False)
        enforce_write_policy(
            actor,
            logical,
            patch,
            automation_queue_ids=set(self._queue_ids or ()),
            system_transition=system_transition,
        )
        if logical == "automationqueue":
            current = self._current_queue_for_write(entity_id)
            _validate_queue_transition(current, patch, actor)
            entity_id = _physical_or_logical_id(current, _proposal_id(current))
        method = getattr(self.backend, "update_properties", None)
        if not callable(method):
            raise PolicyViolation("wrapped Notion backend has no update_properties")
        keywords: dict[str, Any] = {
            "target_db": provider_id,
            "entity_id": entity_id,
            "patch": dict(patch),
            "actor": actor,
        }
        if system_transition:
            keywords["system_transition"] = True
        return _call_with_supported_signature(
            method,
            (provider_id, entity_id, dict(patch), actor),
            keywords,
        )

    def _current_queue_for_write(self, entity_id: str) -> Any:
        """Resolve a physical target, then re-establish semantic uniqueness."""
        rows = self.find_approval_rows(entity_id)
        if rows:
            current = _unique_approval_row(rows, entity_id)
        else:
            lookup = getattr(self.backend, "find_entity_by_id", None)
            if not callable(lookup):
                raise PolicyViolation("Queue physical writes require a row lookup")
            addressed = _call_lookup(lookup, self._queue_db_id, entity_id)
            if addressed is None:
                raise PolicyViolation("Queue physical target is missing")
            proposal_id = _proposal_id(addressed)
            current = _unique_approval_row(self.find_approval_rows(proposal_id), proposal_id)
            if current is None or _physical_or_logical_id(current, proposal_id) != entity_id:
                raise PolicyViolation("Queue physical target does not match unique proposal")
        if current is None:
            raise PolicyViolation("Queue target is missing")
        _validate_phase4_queue_identity(current)
        return current

    def find_approval_rows(self, proposal_id: str) -> list[Any]:
        method = getattr(self.backend, "find_approval_rows", None)
        if not callable(method):
            raise PolicyViolation("guarded Queue backend must expose find_approval_rows")
        return _call_queue_finder(method, self._queue_db_id, proposal_id)

    def read_approval(self, proposal_id: str) -> Any | None:
        return _unique_approval_row(self.find_approval_rows(proposal_id), proposal_id)

    def write_source_metadata_region(
        self,
        target_db: str,
        entity_id: str,
        patch: Mapping[str, Any],
        *,
        actor: AutomationActor = AutomationActor.AUTOMATION,
    ) -> Any:
        """Guard the provider's source-metadata region write surface."""

        logical, provider_id = self._target(target_db)
        if logical in {"automationqueue", "materialusage"}:
            raise PolicyViolation(
                "source metadata region cannot write Automation Queue or Material Usage"
            )
        if not isinstance(patch, Mapping):
            raise PolicyViolation("source metadata patch must be a mapping")
        self._validate_properties(logical, patch, is_create=False)
        enforce_write_policy(
            actor,
            logical,
            patch,
            automation_queue_ids=set(self._queue_ids or ()),
        )
        method = getattr(self.backend, "write_source_metadata_region", None)
        if not callable(method):
            raise PolicyViolation("wrapped Notion backend has no source metadata boundary")
        return _call_with_supported_signature(
            method,
            (provider_id, entity_id, dict(patch), actor),
            {
                "target_db": provider_id,
                "entity_id": entity_id,
                "patch": dict(patch),
                "actor": actor,
            },
        )

    def write_ai_region(
        self,
        target_db: str,
        entity_id: str,
        patch: Mapping[str, Any],
        *,
        actor: AutomationActor = AutomationActor.AUTOMATION,
    ) -> Any:
        """Guard AI-region writes without granting Queue semantics access."""

        logical, provider_id = self._target(target_db)
        if logical not in {"sessions", "materials"}:
            raise PolicyViolation("AI region is only available on Sessions and Materials")
        if not isinstance(patch, Mapping) or set(patch) != {"enrichment", "ownership"}:
            raise PolicyViolation("AI-region patch must contain exactly enrichment and ownership")
        if patch.get("ownership") != "AI":
            raise PolicyViolation("AI-region ownership is fixed to AI")
        enforce_write_policy(
            actor,
            logical,
            patch,
            automation_queue_ids=set(self._queue_ids or ()),
        )
        method = getattr(self.backend, "write_ai_region", None)
        if not callable(method):
            raise PolicyViolation("wrapped Notion backend has no AI-region boundary")
        return _call_with_supported_signature(
            method,
            (provider_id, entity_id, dict(patch), actor),
            {
                "target_db": provider_id,
                "entity_id": entity_id,
                "patch": dict(patch),
                "actor": actor,
            },
        )

    def restore_ai_region(
        self,
        target_db: str,
        entity_id: str,
        previous: Any | None,
        *,
        actor: AutomationActor = AutomationActor.AUTOMATION,
    ) -> Any:
        logical, provider_id = self._target(target_db)
        if logical not in {"sessions", "materials"}:
            raise PolicyViolation("AI region is only available on Sessions and Materials")
        enforce_write_policy(
            actor,
            logical,
            {"ownership": "AI"},
            automation_queue_ids=set(self._queue_ids or ()),
        )
        method = getattr(self.backend, "restore_ai_region", None)
        if not callable(method):
            raise PolicyViolation("wrapped Notion backend has no AI-region restore boundary")
        return _call_with_supported_signature(
            method,
            (provider_id, entity_id, previous, actor),
            {
                "target_db": provider_id,
                "entity_id": entity_id,
                "previous": previous,
                "actor": actor,
            },
        )

GuardedNotionAdapter = GuardedNotionWriter


__all__ = ["GuardedNotionAdapter", "GuardedNotionWriter"]


_USAGE_ROLES = frozenset({"Primary", "Supporting", "Reference"})
_QUEUE_TYPES = frozenset({"MATERIAL_USAGE", "PAGE_RANGE", "EXAM_SCOPE"})
_QUEUE_STATES = frozenset({"PENDING_REVIEW", "APPROVED", "REJECTED", "APPLIED", "FAILED", "SUPERSEDED"})
_QUEUE_DECISIONS = frozenset({"Pending", "Approve", "Reject"})
_TEXT_FIELDS = {
    "Name",
    "ID",
    "Proposal ID",
    "Target Entity ID",
    "Source Hash",
    "Review Reason",
    "Decision By",
    "Decision At",
    "Created",
    "Updated",
    "Applied At",
    "Last Error",
    "Code",
    "Section",
    "Semester",
    "Type",
    "Role",
    "Scope Note",
    "Notes",
    "Status",
}


def _validate_queue_transition(current: Any, patch: Mapping[str, Any], actor: AutomationActor) -> None:
    """A write capability constrains fields; current human state constrains use."""
    current_state = coerce_queue_state(_get_field(current, "State"))
    decision = coerce_decision(_decision_field(current))
    if "State" in patch:
        next_state = coerce_queue_state(patch["State"])
        transition_queue_state(current_state, next_state, decision=decision, actor=actor)
        if actor is AutomationActor.APPROVAL_READER and next_state is not derive_queue_state(decision):
            raise PolicyViolation("reader State must reflect the current human Decision")
    if actor is AutomationActor.HUMAN_APPROVAL_APPLIER:
        if decision is not Decision.Approve:
            raise PolicyViolation("applier Queue write requires current human approval")
        if any(key in patch for key in ("Decision By", "Decision At", "Applied At")):
            if current_state not in {QueueState.APPROVED, QueueState.APPLIED}:
                raise PolicyViolation("audit requires an approved or already applied proposal")
            attribution = patch.get("Decision By", _get_field(current, "Decision By", default=None))
            if not _is_human_decision_by(attribution):
                raise PolicyViolation("audit attribution must be a human identity")


def _validate_typed_properties(
    logical: str,
    properties: Mapping[str, Any],
    *,
    is_create: bool,
) -> None:
    """Validate the provider-neutral property shapes before any write.

    The backend may use richer provider objects, but the Phase4 writer only
    accepts the canonical scalar/relation/select representation.  Rejecting
    malformed values here prevents a UUID alias or a provider coercion from
    bypassing the human-gate invariants.
    """

    for key, value in properties.items():
        if key in _TEXT_FIELDS and value is not None and not _text_value(value):
            raise PolicyViolation(f"{logical}.{key} must be a non-empty string")

    if logical == "materialusage":
        if "Role" in properties and properties["Role"] not in _USAGE_ROLES:
            raise PolicyViolation("Material Usage.Role is not a valid Usage Role")
        for key in ("Session", "Material"):
            if key in properties and _one_relation(properties[key]) is None:
                raise PolicyViolation(f"Material Usage.{key} must contain exactly one relation")
        if "Verified" in properties and type(properties["Verified"]) is not bool:
            raise PolicyViolation("Material Usage.Verified must be a boolean")
        if "Confidence" in properties:
            _select_value(properties["Confidence"], "Material Usage.Confidence")
        if "Evidence" in properties:
            _json_value(properties["Evidence"], "Material Usage.Evidence")
        if "Start Page" in properties or "End Page" in properties:
            if "Start Page" not in properties or "End Page" not in properties:
                raise PolicyViolation("Material Usage page bounds must be written together")
            result = parse_page_range(properties["Start Page"], properties["End Page"])
            if not result.is_valid or result.value is None:
                raise PolicyViolation("Material Usage page bounds are invalid")
        return

    if logical == "automationqueue":
        if "Proposal Type" in properties and properties["Proposal Type"] not in _QUEUE_TYPES:
            raise PolicyViolation("Automation Queue Proposal Type is invalid")
        if "State" in properties and properties["State"] not in _QUEUE_STATES:
            raise PolicyViolation("Automation Queue State is invalid")
        if "Decision" in properties and properties["Decision"] not in _QUEUE_DECISIONS:
            raise PolicyViolation("Automation Queue Decision is invalid")
        if "Course" in properties and _one_relation(properties["Course"]) is None:
            raise PolicyViolation("Automation Queue.Course must contain exactly one relation")
        if "Source Ref" in properties:
            _source_ref(properties["Source Ref"])
        if "Source Version" in properties:
            value = properties["Source Version"]
            if type(value) is not int or value < 1:
                raise PolicyViolation("Automation Queue.Source Version must be a positive integer")
        if "Proposed Action" in properties:
            action = properties["Proposed Action"]
            if isinstance(action, Mapping):
                _json_value(action, "Automation Queue.Proposed Action")
            elif isinstance(action, str):
                try:
                    parsed = parse_action_json(action)
                except Exception as exc:
                    raise PolicyViolation("Automation Queue.Proposed Action must be valid JSON") from exc
                if not isinstance(parsed, Mapping):
                    raise PolicyViolation("Automation Queue.Proposed Action must be a JSON object")
            else:
                raise PolicyViolation("Automation Queue.Proposed Action must be a JSON object")
        if "Confidence" in properties:
            _select_value(properties["Confidence"], "Automation Queue.Confidence")
        if "Evidence" in properties:
            _json_value(properties["Evidence"], "Automation Queue.Evidence")
        return

    # The non-Phase4 graph tables still get scalar/cardinality checks at this
    # boundary so the wrapper cannot be used as an untyped provider tunnel.
    if logical in {"courses", "sessions", "materials", "exams", "activities"}:
        if "Course" in properties and _one_relation(properties["Course"]) is None:
            raise PolicyViolation(f"{logical}.Course must contain exactly one relation")
        if "Included Sessions" in properties and not _valid_relation_list(
            properties["Included Sessions"], expected_type="S", allow_empty=True
        ):
            raise PolicyViolation(f"{logical}.Included Sessions must be a valid Session relation")
        if "Related Sessions" in properties and not _valid_relation_list(
            properties["Related Sessions"], expected_type="S", allow_empty=True
        ):
            raise PolicyViolation(f"{logical}.Related Sessions must be a valid Session relation")
        if "Related Materials" in properties and not _valid_relation_list(
            properties["Related Materials"], expected_type="M", allow_empty=True
        ):
            raise PolicyViolation(f"{logical}.Related Materials must be a valid Material relation")
        if "Scope Confirmed" in properties and type(properties["Scope Confirmed"]) is not bool:
            raise PolicyViolation(f"{logical}.Scope Confirmed must be a boolean")
        if "ID" in properties:
            expected_type = "E" if logical == "exams" else "A" if logical == "activities" else None
            if expected_type is not None:
                from uls.domain.ids import strict_entity_id

                if strict_entity_id(properties["ID"], expected_type) is None:
                    raise PolicyViolation(f"{logical}.ID is not a canonical {expected_type} ID")
        for key in ("Current Source Version", "Page Count", "Session No"):
            if key in properties:
                value = properties[key]
                if type(value) is not int or value < 1:
                    raise PolicyViolation(f"{logical}.{key} must be a positive integer")


def _text_value(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _one_relation(value: Any) -> str | None:
    relation = resolve_course_relation(value)
    return relation if isinstance(relation, str) and relation else None


def _relation_ids(value: Any) -> tuple[str, ...]:
    from uls.domain.course_identity import relation_page_ids

    return relation_page_ids(value)


def _valid_relation_list(value: Any, *, expected_type: str, allow_empty: bool) -> bool:
    """Validate relation shape while preserving an explicit empty list."""

    if isinstance(value, Mapping):
        for key in ("relation", "relations", "results"):
            if key in value:
                return _valid_relation_list(
                    value[key], expected_type=expected_type, allow_empty=allow_empty
                )
        return False
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return False
    if not value:
        return allow_empty
    ids = _relation_ids(value)
    if len(ids) != len(value):
        return False
    from uls.domain.ids import strict_entity_id

    return all(strict_entity_id(item, expected_type) is not None for item in ids)


def _select_value(value: Any, name: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise PolicyViolation(f"{name} must be a non-empty Select option")


def _source_ref(value: Any) -> SourceRef:
    if not isinstance(value, Mapping):
        raise PolicyViolation("Automation Queue.Source Ref must be a structured object")
    provider = value.get("provider")
    file_id = value.get("file_id")
    if (
        not isinstance(provider, str)
        or not isinstance(file_id, str)
        or not provider.strip()
        or not file_id.strip()
    ):
        raise PolicyViolation("Automation Queue.Source Ref requires provider and file_id")
    if file_id.casefold().startswith(("http://", "https://")):
        raise PolicyViolation("Automation Queue.Source Ref.file_id cannot be a URL")
    return SourceRef(provider.strip(), file_id.strip())


def _json_value(value: Any, name: str) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item, name) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item, name) for item in value]
    raise PolicyViolation(f"{name} must be JSON-compatible")
