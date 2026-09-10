"""Canonical identity for Material Usage and PAGE_RANGE approvals.

The Queue's human-facing mirrors are not the source of proposal identity.  A
proposal is identified by the complete, typed action payload that the
applier later revalidates.  This module is deliberately pure so producer and
applier cannot drift into different identity rules.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from typing import Any

from .ids import strict_entity_id
from .page_range import PageRange, parse_page_range
from .source_ref import SourceRef

APPROVAL_ACTION_SCHEMA = "uls.material_usage.approval.v1"
MATERIAL_USAGE_TARGET_DB = "Material Usage"
EXAM_SCOPE_APPROVAL_ACTION_SCHEMA = "uls.exam_scope.approval.v1"
EXAM_SCOPE_TARGET_DB = "Exams"
VALID_USAGE_ROLES = frozenset({"Primary", "Supporting", "Reference"})
SUPPORTED_MATERIAL_SOURCE_CLASSES = frozenset(
    {"professor_material", "supplemental_reference"}
)

_MISSING = object()

# These are the only action keys that can participate in Phase4 identity.
# Required keys are still required even when their value is explicitly null.
_ACTION_KEYS = (
    "schema",
    "operation",
    "target_db",
    "target_entity_id",
    "session_id",
    "material_id",
    "course_relation_page_id",
    "course_key",
    "usage_role",
    "material_type",
    "source_class",
    "old_snapshot",
    "desired_range",
    "session_dependency",
    "material_dependency",
    "evidence",
    "review_reason",
    "processor_version",
)
_REQUIRED_ACTION_KEYS = frozenset(_ACTION_KEYS)

_EXAM_ACTION_KEYS = (
    "schema",
    "operation",
    "target_db",
    "target_entity_id",
    "course",
    "old_snapshot",
    "desired_snapshot",
    "session_dependencies",
    "source_dependencies",
    "evidence",
    "review_reason",
    "processor_version",
)
_REQUIRED_EXAM_ACTION_KEYS = frozenset(_EXAM_ACTION_KEYS)


def derive_usage_id(
    session_id: str,
    material_id: str,
    role: str,
    page_range: PageRange,
) -> str:
    """Derive the deterministic app-level ID for a new Usage relation."""

    payload = [session_id, material_id, role, page_range.start_page, page_range.end_page]
    digest = _sha256(payload)
    return f"MU:{digest}"


def build_material_usage_semantics(
    *,
    operation: str,
    target_entity_id: str,
    session_id: str,
    material_id: str,
    course_relation_page_id: str,
    course_key: str,
    usage_role: str,
    material_type: str,
    source_class: str,
    old_snapshot: Mapping[str, Any],
    desired_range: PageRange,
    session_dependency: Mapping[str, Any],
    material_dependency: Mapping[str, Any],
    evidence: Any = None,
    review_reason: str | None = None,
    processor_version: str = "1.2.0",
) -> OrderedDict[str, Any]:
    """Build and validate one canonical Phase4 action payload."""

    action: dict[str, Any] = {
        "schema": APPROVAL_ACTION_SCHEMA,
        "operation": operation,
        "target_db": MATERIAL_USAGE_TARGET_DB,
        "target_entity_id": target_entity_id,
        "session_id": session_id,
        "material_id": material_id,
        "course_relation_page_id": course_relation_page_id,
        "course_key": course_key,
        "usage_role": usage_role,
        "material_type": material_type,
        "source_class": source_class,
        "old_snapshot": dict(old_snapshot),
        "desired_range": desired_range.as_dict(),
        "session_dependency": dict(session_dependency),
        "material_dependency": dict(material_dependency),
        "evidence": evidence,
        "review_reason": review_reason,
        "processor_version": processor_version,
    }
    return _canonical_semantics(action, proposal_type="MATERIAL_USAGE" if operation == "create_usage" else "PAGE_RANGE")


def build_exam_scope_semantics(
    *,
    operation: str,
    target_entity_id: str,
    course: Mapping[str, Any] | Any,
    old_snapshot: Mapping[str, Any],
    desired_snapshot: Mapping[str, Any],
    session_dependencies: Sequence[Mapping[str, Any]] = (),
    source_dependencies: Sequence[Mapping[str, Any]] | None = None,
    evidence: Any = None,
    review_reason: str | None = None,
    processor_version: str = "1.2.0",
) -> OrderedDict[str, Any]:
    """Build the complete, typed identity payload for an EXAM_SCOPE action."""

    action = {
        "schema": EXAM_SCOPE_APPROVAL_ACTION_SCHEMA,
        "operation": operation,
        "target_db": EXAM_SCOPE_TARGET_DB,
        "target_entity_id": target_entity_id,
        "course": dict(course) if isinstance(course, Mapping) else course,
        "old_snapshot": dict(old_snapshot),
        "desired_snapshot": dict(desired_snapshot),
        "session_dependencies": list(session_dependencies),
        "source_dependencies": None if source_dependencies is None else list(source_dependencies),
        "evidence": evidence,
        "review_reason": review_reason,
        "processor_version": processor_version,
    }
    return _canonical_exam_semantics(action)


def canonical_semantics_from_queue(record: Any) -> OrderedDict[str, Any]:
    """Extract the canonical semantics from a Queue row's own action.

    For Phase4 actions this is strict: all required keys must be present,
    unknown fields and conflicting aliases are rejected, and Queue mirrors
    are checked against the action.  Non-Phase4 legacy proposal types retain
    a deterministic generic extraction so the pre-Phase4 approval reader can
    continue to operate without weakening Phase4 identity.
    """

    proposal_type = _wire(_field(record, "Proposal Type", "proposal_type", default=None))
    if hasattr(proposal_type, "value"):
        proposal_type = proposal_type.value
    if not isinstance(proposal_type, str) or not proposal_type.strip():
        raise ValueError("Proposal Type is required")
    proposal_type = proposal_type.strip()
    action = _action_mapping(record)
    if proposal_type in {"MATERIAL_USAGE", "PAGE_RANGE"}:
        semantics = _canonical_semantics(action, proposal_type=proposal_type)
        _validate_mirrors(record, semantics, proposal_type)
        return semantics
    if proposal_type == "EXAM_SCOPE":
        semantics = _canonical_exam_semantics(action)
        _validate_exam_mirrors(record, semantics)
        return semantics
    # The generic path is intentionally not used to authorize Material Usage;
    # it keeps unrelated old approval tools deterministic during migration.
    return _generic_semantics(action)


def canonical_exam_scope_semantics(action: Mapping[str, Any]) -> OrderedDict[str, Any]:
    """Canonicalize an EXAM_SCOPE action independently of Queue mirrors."""

    return _canonical_exam_semantics(action)


def derive_proposal_id(proposal_type: Any, semantics: Mapping[str, Any]) -> str:
    """Return a deterministic namespaced Proposal ID."""

    type_value = _wire(proposal_type)
    if hasattr(type_value, "value"):
        type_value = type_value.value
    if not isinstance(type_value, str) or not type_value.strip():
        raise ValueError("proposal_type must be a non-empty string")
    canonical = _json_value(semantics)
    payload = {"proposal_type": type_value.strip(), "semantics": canonical}
    digest = _sha256(payload)
    return f"{type_value.strip()}:{digest}"


def canonical_action_json(semantics: Mapping[str, Any]) -> str:
    """Serialize canonical action JSON for the Queue Rich text property."""

    return json.dumps(_json_value(semantics), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_action_json(value: Any) -> Mapping[str, Any]:
    """Parse an action mapping and reject duplicate JSON object keys."""

    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Proposed Action must be a structured JSON object")

    def pairs(pairs_value: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs_value:
            if key in result:
                raise ValueError(f"duplicate JSON key in Proposed Action: {key}")
            result[key] = item
        return result

    parsed = json.loads(value, object_pairs_hook=pairs)
    if not isinstance(parsed, Mapping):
        raise TypeError("Proposed Action must be a JSON object")
    return dict(parsed)


def _canonical_semantics(action: Mapping[str, Any], *, proposal_type: str) -> OrderedDict[str, Any]:
    normalized = _normalise_action_aliases(action)
    unknown = set(normalized).difference(_REQUIRED_ACTION_KEYS)
    if unknown:
        raise ValueError("unknown canonical action fields: " + ", ".join(sorted(unknown)))
    missing = _REQUIRED_ACTION_KEYS.difference(normalized)
    if missing:
        raise ValueError(
            "canonical action is missing required fields: " + ", ".join(sorted(missing))
        )

    _require_text(normalized["schema"], "schema", exact=APPROVAL_ACTION_SCHEMA)
    operation = _require_text(normalized["operation"], "operation")
    expected_operations = {
        "MATERIAL_USAGE": {"create_usage"},
        "PAGE_RANGE": {"update_range", "page_range"},
    }
    if operation not in expected_operations[proposal_type]:
        raise ValueError(f"operation {operation!r} is invalid for {proposal_type}")
    target_db = _require_text(normalized["target_db"], "target_db")
    if target_db != MATERIAL_USAGE_TARGET_DB:
        raise ValueError("target_db must be Material Usage")
    for name in (
        "target_entity_id",
        "session_id",
        "material_id",
        "course_relation_page_id",
        "course_key",
        "material_type",
        "processor_version",
    ):
        _require_text(normalized[name], name)
    role = _require_text(normalized["usage_role"], "usage_role")
    if role not in VALID_USAGE_ROLES:
        raise ValueError(f"usage_role is not supported: {role!r}")
    source_class = _require_text(normalized["source_class"], "source_class")
    if source_class not in SUPPORTED_MATERIAL_SOURCE_CLASSES:
        raise ValueError(f"source_class is not supported: {source_class!r}")

    old_snapshot = _mapping_required(normalized["old_snapshot"], "old_snapshot")
    old = _canonical_old_snapshot(old_snapshot)
    desired = _canonical_range(normalized["desired_range"])
    session_dep = _canonical_dependency(normalized["session_dependency"], "session_dependency")
    material_dep = _canonical_dependency(normalized["material_dependency"], "material_dependency")
    evidence = _canonical_json_value(normalized["evidence"], "evidence")
    review_reason = normalized["review_reason"]
    if review_reason is not None and (not isinstance(review_reason, str) or not review_reason.strip()):
        raise ValueError("review_reason must be a non-empty string or null")

    ordered: OrderedDict[str, Any] = OrderedDict()
    for key in _ACTION_KEYS:
        value = normalized[key]
        if key == "old_snapshot":
            value = old
        elif key == "desired_range":
            value = desired
        elif key in {"session_dependency", "material_dependency"}:
            value = session_dep if key == "session_dependency" else material_dep
        elif key == "evidence":
            value = evidence
        elif key in {
            "schema",
            "operation",
            "target_db",
            "target_entity_id",
            "session_id",
            "material_id",
            "course_relation_page_id",
            "course_key",
            "usage_role",
            "material_type",
            "source_class",
            "processor_version",
        }:
            value = _require_text(value, key)
        ordered[key] = value
    return ordered


def _canonical_exam_semantics(action: Mapping[str, Any]) -> OrderedDict[str, Any]:
    normalized = _normalise_exam_action_aliases(action)
    unknown = set(normalized).difference(_REQUIRED_EXAM_ACTION_KEYS)
    if unknown:
        raise ValueError("unknown EXAM_SCOPE action fields: " + ", ".join(sorted(unknown)))
    missing = _REQUIRED_EXAM_ACTION_KEYS.difference(normalized)
    if missing:
        raise ValueError(
            "EXAM_SCOPE action is missing required fields: " + ", ".join(sorted(missing))
        )
    _require_text(normalized["schema"], "schema", exact=EXAM_SCOPE_APPROVAL_ACTION_SCHEMA)
    operation = _require_text(normalized["operation"], "operation")
    if operation not in {"confirm_scope", "confirm_empty_scope"}:
        raise ValueError("EXAM_SCOPE operation is unsupported")
    _require_text(normalized["target_db"], "target_db", exact=EXAM_SCOPE_TARGET_DB)
    target_id = _require_entity_text(normalized["target_entity_id"], "target_entity_id", "E")
    course = _canonical_exam_course(normalized["course"])
    old = _canonical_exam_snapshot(normalized["old_snapshot"], "old_snapshot", desired=False)
    desired = _canonical_exam_snapshot(normalized["desired_snapshot"], "desired_snapshot", desired=True)
    desired_ids = desired["included_session_ids"]
    if operation == "confirm_empty_scope":
        if desired_ids != []:
            raise ValueError("confirm_empty_scope requires an explicit empty desired scope")
    elif not desired_ids:
        raise ValueError("an empty desired scope requires confirm_empty_scope")
    sessions = _canonical_session_dependencies(normalized["session_dependencies"])
    if {item["session_id"] for item in sessions} != set(desired_ids):
        raise ValueError("session_dependencies must exactly cover desired session IDs")
    for dependency in sessions:
        if (
            dependency["course_relation_page_id"] != course["relation_page_id"]
            or dependency["course_key"] != course["course_key"]
        ):
            raise ValueError("session dependency Course must match canonical Exam Course")
    sources = _canonical_source_dependencies(normalized["source_dependencies"])
    evidence = _canonical_json_value(normalized["evidence"], "evidence")
    reason = normalized["review_reason"]
    if reason is not None and (not isinstance(reason, str) or not reason.strip()):
        raise ValueError("review_reason must be a non-empty string or null")
    processor = _require_text(normalized["processor_version"], "processor_version")
    return OrderedDict(
        (
            ("schema", EXAM_SCOPE_APPROVAL_ACTION_SCHEMA),
            ("operation", operation),
            ("target_db", EXAM_SCOPE_TARGET_DB),
            ("target_entity_id", target_id),
            ("course", course),
            ("old_snapshot", old),
            ("desired_snapshot", desired),
            ("session_dependencies", sessions),
            ("source_dependencies", sources),
            ("evidence", evidence),
            ("review_reason", reason),
            ("processor_version", processor),
        )
    )


def _normalise_exam_action_aliases(action: Mapping[str, Any]) -> dict[str, Any]:
    aliases = {
        "schema": {"schema"},
        "operation": {"operation", "op"},
        "target_db": {"target_db", "targetdatabase", "database", "db"},
        "target_entity_id": {"target_entity_id", "targetentityid", "target_id", "exam_id"},
        "course": {"course", "course_identity"},
        "old_snapshot": {"old_snapshot", "oldsnapshot", "old"},
        "desired_snapshot": {"desired_snapshot", "desiredsnapshot", "desired", "scope"},
        "session_dependencies": {"session_dependencies", "sessiondependencies", "dependencies"},
        "source_dependencies": {"source_dependencies", "sourcedependencies", "sources"},
        "evidence": {"evidence"},
        "review_reason": {"review_reason", "reviewreason", "reason"},
        "processor_version": {"processor_version", "processorversion"},
    }
    result: dict[str, Any] = {}
    for raw_key, value in action.items():
        if not isinstance(raw_key, str):
            raise TypeError("EXAM_SCOPE action keys must be strings")
        normalized_key = _normal(raw_key)
        matches = [canonical for canonical, names in aliases.items() if normalized_key in {_normal(name) for name in names}]
        if len(matches) != 1:
            raise ValueError(f"unknown or ambiguous EXAM_SCOPE action field: {raw_key!r}")
        canonical = matches[0]
        if canonical in result:
            raise ValueError(f"conflicting EXAM_SCOPE action field: {canonical}")
        result[canonical] = value
    return result


def _require_entity_text(value: Any, name: str, expected_type: str) -> str:
    result = _require_text(value, name)
    if _entity_type(result) != expected_type:
        raise ValueError(f"{name} must be a canonical {expected_type} entity ID")
    return result


def _entity_type(value: str) -> str | None:
    # Keep identity validation local to this pure module; importing the parser
    # here avoids adding a domain-cycle to older callers.
    from .ids import strict_entity_id

    for expected in ("E", "S", "M", "A"):
        if strict_entity_id(value, expected) is not None:
            return expected
    return None


def _canonical_exam_course(value: Any) -> OrderedDict[str, str]:
    mapping = _mapping_required(value, "course")
    normalized = _alias_mapping(
        mapping,
        {
            "relation_page_id": ("relation_page_id", "relationpageid", "course_page_id", "course_relation_page_id"),
            "course_key": ("course_key", "coursekey"),
        },
        "course",
    )
    if set(normalized) != {"relation_page_id", "course_key"}:
        raise ValueError("course must contain relation_page_id and course_key")
    relation = _require_text(normalized["relation_page_id"], "course.relation_page_id")
    key = _require_text(normalized["course_key"], "course.course_key")
    from .ids import parse_course_key

    try:
        parse_course_key(key)
    except Exception as exc:
        raise ValueError("course.course_key is invalid") from exc
    return OrderedDict((("relation_page_id", relation), ("course_key", key)))


def _canonical_exam_snapshot(value: Any, name: str, *, desired: bool) -> OrderedDict[str, Any]:
    mapping = _mapping_required(value, name)
    normalized = _alias_mapping(
        mapping,
        {
            "included_session_ids": ("included_session_ids", "included_sessions", "session_ids", "sessions"),
            "scope_confirmed": ("scope_confirmed", "scope_confirmed_value", "confirmed"),
        },
        name,
    )
    if set(normalized) != {"included_session_ids", "scope_confirmed"}:
        raise ValueError(f"{name} must contain included_session_ids and scope_confirmed")
    ids = _canonical_id_list(
        normalized["included_session_ids"],
        f"{name}.included_session_ids",
        "S",
        allow_none=not desired,
    )
    confirmed = normalized["scope_confirmed"]
    if type(confirmed) is not bool:
        raise ValueError(f"{name}.scope_confirmed must be a boolean")
    if desired and not confirmed:
        raise ValueError("desired_snapshot.scope_confirmed must be true")
    return OrderedDict((("included_session_ids", ids), ("scope_confirmed", confirmed)))


def _canonical_id_list(value: Any, name: str, expected_type: str, *, allow_none: bool = False) -> list[str] | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{name} must be an explicit list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or strict_entity_id(item, expected_type) is None:
            raise ValueError(f"{name} contains an invalid {expected_type} ID")
        if item in result:
            raise ValueError(f"{name} contains duplicate IDs")
        result.append(item)
    return sorted(result)


def _canonical_session_dependencies(value: Any) -> list[OrderedDict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        raise TypeError("session_dependencies must be an explicit list")
    result: list[OrderedDict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        mapping = _mapping_required(item, "session dependency")
        normalized = _alias_mapping(
            mapping,
            {
                "session_id": ("session_id", "sessionid", "session"),
                "course_relation_page_id": ("course_relation_page_id", "coursepageid", "course_page_id"),
                "course_key": ("course_key", "coursekey"),
                "source_ref": ("source_ref", "sourceref", "transcript_ref"),
                "source_hash": ("source_hash", "sourcehash", "hash"),
                "source_version": ("source_version", "sourceversion", "version"),
            },
            "session dependency",
        )
        required = {"session_id", "course_relation_page_id", "course_key", "source_ref", "source_hash", "source_version"}
        if set(normalized) != required:
            raise ValueError("session dependency must contain its complete identity and fingerprint")
        session_id = _require_entity_text(normalized["session_id"], "session_dependency.session_id", "S")
        if session_id in seen:
            raise ValueError("session_dependencies contains duplicate IDs")
        seen.add(session_id)
        relation = _require_text(normalized["course_relation_page_id"], "session_dependency.course_relation_page_id")
        course_key = _require_text(normalized["course_key"], "session_dependency.course_key")
        ref, source_hash, source_version = _canonical_optional_dependency(
            normalized["source_ref"], normalized["source_hash"], normalized["source_version"], "session_dependency"
        )
        result.append(
            OrderedDict(
                (
                    ("session_id", session_id),
                    ("course_relation_page_id", relation),
                    ("course_key", course_key),
                    ("source_ref", ref),
                    ("source_hash", source_hash),
                    ("source_version", source_version),
                )
            )
        )
    return sorted(result, key=lambda item: item["session_id"])


def _canonical_source_dependencies(value: Any) -> list[OrderedDict[str, Any]] | None:
    if value is None:
        return None
    raise ValueError(
        "EXAM_SCOPE source_dependencies is unsupported; use explicit null"
    )


def _canonical_optional_dependency(ref_value: Any, hash_value: Any, version_value: Any, name: str) -> tuple[dict[str, str] | None, str | None, int | None]:
    if ref_value is None and hash_value is None and version_value is None:
        return None, None, None
    ref = _strict_ref(ref_value, f"{name}.source_ref")
    source_hash = _require_text(hash_value, f"{name}.source_hash")
    if isinstance(version_value, bool) or not isinstance(version_value, int) or version_value < 1:
        raise ValueError(f"{name}.source_version must be a positive integer")
    return {"provider": ref.provider, "file_id": ref.file_id}, source_hash, version_value



def _normalise_action_aliases(action: Mapping[str, Any]) -> dict[str, Any]:
    aliases = {
        "schema": {"schema"},
        "operation": {"operation", "op"},
        "target_db": {"target_db", "targetdatabase", "database", "db"},
        "target_entity_id": {"target_entity_id", "targetentityid", "target_id", "target"},
        "session_id": {"session_id", "sessionid", "session"},
        "material_id": {"material_id", "materialid", "material"},
        "course_relation_page_id": {"course_relation_page_id", "coursepageid", "course_page_id"},
        "course_key": {"course_key", "coursekey"},
        "usage_role": {"usage_role", "usagerole", "role"},
        "material_type": {"material_type", "materialtype", "type"},
        "source_class": {"source_class", "sourceclass"},
        "old_snapshot": {"old_snapshot", "oldsnapshot", "old"},
        "desired_range": {"desired_range", "desiredrange", "range", "new_range"},
        "session_dependency": {"session_dependency", "sessiondependency", "session_source"},
        "material_dependency": {"material_dependency", "materialdependency", "material_source"},
        "evidence": {"evidence"},
        "review_reason": {"review_reason", "reviewreason"},
        "processor_version": {"processor_version", "processorversion"},
    }
    result: dict[str, Any] = {}
    for raw_key, value in action.items():
        if not isinstance(raw_key, str):
            raise ValueError("canonical action keys must be strings")
        normalized_key = _normal(raw_key)
        matches = [canonical for canonical, names in aliases.items() if normalized_key in {_normal(name) for name in names}]
        if len(matches) != 1:
            raise ValueError(f"unknown or ambiguous canonical action field: {raw_key!r}")
        canonical = matches[0]
        if canonical in result:
            raise ValueError(f"conflicting aliases for canonical action field: {canonical}")
        result[canonical] = value
    return result


def _canonical_old_snapshot(value: Mapping[str, Any]) -> OrderedDict[str, Any]:
    aliases = {
        "usage_id": ("usage_id", "usageid", "id"),
        "session_id": ("session_id", "sessionid", "session"),
        "material_id": ("material_id", "materialid", "material"),
        "role": ("role",),
        "start_page": ("start_page", "startpage", "start"),
        "end_page": ("end_page", "endpage", "end"),
        "verified": ("verified",),
    }
    normalized = _alias_mapping(value, aliases, "old_snapshot")
    required = set(aliases)
    if set(normalized) != required:
        raise ValueError("old_snapshot must contain all required keys, including nulls")
    usage_id = normalized["usage_id"]
    if usage_id is not None:
        _require_text(usage_id, "old_snapshot.usage_id")
    for name in ("session_id", "material_id", "role"):
        _require_text(normalized[name], f"old_snapshot.{name}")
    range_result = parse_page_range(normalized["start_page"], normalized["end_page"])
    if not range_result.is_valid or range_result.value is None:
        raise ValueError("old_snapshot page range is invalid")
    if type(normalized["verified"]) is not bool:
        raise ValueError("old_snapshot.verified must be a boolean")
    result: OrderedDict[str, Any] = OrderedDict()
    for name in ("usage_id", "session_id", "material_id", "role"):
        result[name] = normalized[name]
    result["start_page"] = range_result.value.start_page
    result["end_page"] = range_result.value.end_page
    result["verified"] = normalized["verified"]
    return result


def _canonical_range(value: Any) -> dict[str, int | None]:
    if not isinstance(value, Mapping):
        raise ValueError("desired_range must be a mapping")
    normalized = _alias_mapping(
        value,
        {
            "start_page": ("start_page", "startpage", "start"),
            "end_page": ("end_page", "endpage", "end"),
        },
        "desired_range",
    )
    if set(normalized) != {"start_page", "end_page"}:
        raise ValueError("desired_range must explicitly contain start_page and end_page")
    result = parse_page_range(normalized["start_page"], normalized["end_page"])
    if not result.is_valid or result.value is None:
        raise ValueError(result.reason or "desired_range is invalid")
    return result.value.as_dict()


def _canonical_dependency(value: Any, name: str) -> OrderedDict[str, Any]:
    mapping = _mapping_required(value, name)
    normalized = _alias_mapping(
        mapping,
        {
            "source_ref": ("source_ref", "sourceref", "originating_ref"),
            "source_hash": ("source_hash", "sourcehash", "hash"),
            "source_version": ("source_version", "sourceversion", "version"),
        },
        name,
    )
    if set(normalized) != {"source_ref", "source_hash", "source_version"}:
        raise ValueError(f"{name} must contain source_ref/hash/version, including nulls")
    ref = _strict_ref(normalized["source_ref"], f"{name}.source_ref")
    source_hash = _require_text(normalized["source_hash"], f"{name}.source_hash")
    version = normalized["source_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError(f"{name}.source_version must be a positive integer")
    return OrderedDict(
        (
            ("source_ref", {"provider": ref.provider, "file_id": ref.file_id}),
            ("source_hash", source_hash),
            ("source_version", version),
        )
    )


def _validate_mirrors(record: Any, semantics: Mapping[str, Any], proposal_type: str) -> None:
    proposal_value = _field(record, "Proposal Type", "proposal_type", default=None)
    if _wire(proposal_value) != proposal_type:
        raise ValueError("Proposal Type mirror does not match canonical action")
    target_id = _field(record, "Target Entity ID", "target_entity_id", "target_id", default=_MISSING)
    if target_id is _MISSING:
        raise ValueError("Target Entity ID mirror is missing")
    if not isinstance(target_id, str) or target_id.strip() != semantics["target_entity_id"]:
        raise ValueError("Target Entity ID mirror does not match canonical action")
    course = _field(record, "Course", "course", default=_MISSING)
    from .course_identity import resolve_course_relation

    if course is not _MISSING and resolve_course_relation(course) != semantics["course_relation_page_id"]:
        raise ValueError("Course mirror does not match canonical action")
    source_ref = _field(record, "Source Ref", "source_ref", default=_MISSING)
    if source_ref is not _MISSING:
        ref = _strict_ref(_parse_mirror_json(source_ref), "Source Ref mirror")
        if ref.identity != _strict_ref(semantics["material_dependency"]["source_ref"], "material source").identity:
            raise ValueError("Source Ref mirror does not match material dependency")
    source_hash = _field(record, "Source Hash", "source_hash", default=_MISSING)
    if source_hash is not _MISSING and (
        not isinstance(source_hash, str) or source_hash != semantics["material_dependency"]["source_hash"]
    ):
        raise ValueError("Source Hash mirror does not match material dependency")
    source_version = _field(record, "Source Version", "source_version", default=_MISSING)
    if source_version is not _MISSING and (
        type(source_version) is not int or source_version != semantics["material_dependency"]["source_version"]
    ):
        raise ValueError("Source Version mirror does not match material dependency")
    evidence = _field(record, "Evidence", "evidence", default=_MISSING)
    if evidence is not _MISSING and _json_value(_parse_mirror_json(evidence)) != semantics["evidence"]:
        raise ValueError("Evidence mirror does not match canonical action")
    review_reason = _field(record, "Review Reason", "review_reason", default=_MISSING)
    if review_reason is not _MISSING and review_reason != semantics["review_reason"]:
        raise ValueError("Review Reason mirror does not match canonical action")
    # Target DB and Target Fingerprint are deliberately not Queue schema
    # properties.  Their presence is caught by the guarded property validator
    # before this function is used for persisted rows.


def _validate_exam_mirrors(record: Any, semantics: Mapping[str, Any]) -> None:
    from .course_identity import strict_single_relation_page_id

    if _wire(_field(record, "Proposal Type", "proposal_type", default=None)) != "EXAM_SCOPE":
        raise ValueError("Proposal Type mirror does not match EXAM_SCOPE action")
    target_id = _field(record, "Target Entity ID", "target_entity_id", "target_id", default=_MISSING)
    if target_id is _MISSING or not isinstance(target_id, str) or target_id.strip() != semantics["target_entity_id"]:
        raise ValueError("Target Entity ID mirror does not match EXAM_SCOPE action")
    for name, alias in (
        ("Source Ref", "source_ref"),
        ("Source Hash", "source_hash"),
        ("Source Version", "source_version"),
    ):
        if _field(record, name, alias, default=_MISSING) is not _MISSING:
            raise ValueError(f"{name} mirrors are unsupported for EXAM_SCOPE")
    course = _field(record, "Course", "course", default=_MISSING)
    if course is not _MISSING:
        relation = _field(
            course,
            "relation_page_id",
            "course_relation_page_id",
            default=_MISSING,
        )
        if relation is _MISSING:
            relation = strict_single_relation_page_id(course)
        if relation != semantics["course"]["relation_page_id"]:
            raise ValueError("Course mirror does not match EXAM_SCOPE action")
    evidence = _field(record, "Evidence", "evidence", default=_MISSING)
    if evidence is not _MISSING and _json_value(_parse_mirror_json(evidence)) != semantics["evidence"]:
        raise ValueError("Evidence mirror does not match EXAM_SCOPE action")
    review_reason = _field(record, "Review Reason", "review_reason", default=_MISSING)
    if review_reason is not _MISSING and review_reason != semantics["review_reason"]:
        raise ValueError("Review Reason mirror does not match EXAM_SCOPE action")


def _generic_semantics(action: Mapping[str, Any]) -> OrderedDict[str, Any]:
    return OrderedDict((key, _json_value(action[key])) for key in sorted(action, key=str))


def _action_mapping(record: Any) -> Mapping[str, Any]:
    raw = _field(record, "Proposed Action", "proposed_action", "action", default=None)
    if isinstance(raw, Mapping):
        return dict(raw)
    return parse_action_json(raw)


def _mapping_required(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _alias_mapping(
    value: Mapping[str, Any], aliases: Mapping[str, Sequence[str]], name: str
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_key, item in value.items():
        if not isinstance(raw_key, str):
            raise ValueError(f"{name} keys must be strings")
        matches = [canonical for canonical, names in aliases.items() if _normal(raw_key) in {_normal(candidate) for candidate in names}]
        if len(matches) != 1:
            raise ValueError(f"unknown or ambiguous {name} key: {raw_key!r}")
        canonical = matches[0]
        if canonical in result:
            raise ValueError(f"conflicting aliases in {name}: {canonical}")
        result[canonical] = item
    return result


def _strict_ref(value: Any, name: str) -> SourceRef:
    if isinstance(value, SourceRef):
        ref = value
    elif isinstance(value, Mapping):
        provider = value.get("provider")
        file_id = value.get("file_id")
        if not isinstance(provider, str) or not isinstance(file_id, str):
            raise ValueError(f"{name} requires provider and file_id")
        if file_id.casefold().startswith(("http://", "https://")):
            raise ValueError(f"{name} cannot use a URL as file_id")
        ref = SourceRef(provider.strip(), file_id.strip())
    else:
        raise ValueError(f"{name} must be a structured SourceRef")
    if not ref.provider.strip() or not ref.file_id.strip():
        raise ValueError(f"{name} is empty")
    return ref


def _parse_mirror_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return parse_action_json(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return value
    return value


def _field(record: Any, *names: str, default: Any = None) -> Any:
    sources: list[Any] = []
    if isinstance(record, Mapping) and isinstance(record.get("properties"), Mapping):
        sources.append(record["properties"])
    elif isinstance(getattr(record, "properties", None), Mapping):
        sources.append(record.properties)
    sources.append(record)
    wanted = {_normal(name) for name in names}
    matches: list[Any] = []
    for source in sources:
        if isinstance(source, Mapping):
            for key, value in source.items():
                if isinstance(key, str) and _normal(key) in wanted:
                    matches.append(_unwrap(value))
        else:
            for name in names:
                if hasattr(source, name):
                    matches.append(_unwrap(getattr(source, name)))
                snake = name.casefold().replace(" ", "_")
                if hasattr(source, snake):
                    matches.append(_unwrap(getattr(source, snake)))
    if len(matches) > 1:
        raise ValueError(f"duplicate aliases for field: {', '.join(names)}")
    return matches[0] if matches else default


def _unwrap(value: Any) -> Any:
    if isinstance(value, Mapping):
        if "value" in value and len(value) == 1:
            return _unwrap(value["value"])
        for key in ("title", "rich_text", "select", "status", "number", "checkbox"):
            if key in value and len(value) <= 2:
                inner = value[key]
                if key in {"title", "rich_text"} and isinstance(inner, Sequence) and not isinstance(inner, (str, bytes)):
                    parts: list[str] = []
                    for item in inner:
                        if isinstance(item, Mapping):
                            piece = item.get("plain_text")
                            if piece is None and isinstance(item.get("text"), Mapping):
                                piece = item["text"].get("content")
                            if piece is not None:
                                parts.append(str(piece))
                        elif item is not None:
                            parts.append(str(item))
                    return "".join(parts)
                return _unwrap(inner)
        if "name" in value and len(value) <= 2:
            return value["name"]
        if "plain_text" in value and len(value) <= 2:
            return value["plain_text"]
        if "content" in value and len(value) <= 2:
            return value["content"]
    return value


def _require_text(value: Any, name: str, *, exact: str | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    result = value.strip()
    if exact is not None and result != exact:
        raise ValueError(f"{name} must be {exact}")
    return result


def _normal(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _wire(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, SourceRef):
        return {"provider": value.provider, "file_id": value.file_id}
    if isinstance(value, PageRange):
        return value.as_dict()
    return value


def _canonical_json_value(value: Any, name: str) -> Any:
    try:
        result = _json_value(value)
        json.dumps(result, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} is not JSON-compatible") from exc
    return result


def _sha256(value: Any) -> str:
    payload = json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "APPROVAL_ACTION_SCHEMA",
    "EXAM_SCOPE_APPROVAL_ACTION_SCHEMA",
    "EXAM_SCOPE_TARGET_DB",
    "MATERIAL_USAGE_TARGET_DB",
    "SUPPORTED_MATERIAL_SOURCE_CLASSES",
    "VALID_USAGE_ROLES",
    "build_exam_scope_semantics",
    "build_material_usage_semantics",
    "canonical_action_json",
    "canonical_exam_scope_semantics",
    "canonical_semantics_from_queue",
    "derive_proposal_id",
    "derive_usage_id",
    "parse_action_json",
]
