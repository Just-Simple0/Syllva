"""Provider-free NotionReader fixture for Phase 2 contract tests.

The reader intentionally has no write methods.  ``FakeNotionWriter`` is a
separate worker-side helper and routes mutations through the existing Phase 1
write policy before touching the in-memory records.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

from uls.adapters.notion.base import (
    AutomationActor,
    _create_phase4_queue_once,
    enforce_write_policy,
    find_alias_matches,
    normalize_alias,
)
from uls.adapters.notion.guarded import _SCHEMAS, GuardedNotionWriter, _validate_queue_transition
from uls.domain.course_identity import relation_page_ids, resolve_course_relation
from uls.domain.errors import PolicyViolation, ProviderWriteNotAppliedError
from uls.enrichment.schemas import coerce_enrichment

COURSE_KEY = "2026-1_COMP319-002"
COURSE_PAGE_ID = "course-page-1"

# Frozen implementation spec §14.2.  Session-only writes are validated here
# so ingestion tests cannot accidentally exercise a Material field on a
# Session record without failing at the fake provider boundary.
SESSION_PROPERTIES = frozenset(
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
)


def _course() -> dict[str, Any]:
    return {
        "id": COURSE_PAGE_ID,
        "Course Key": COURSE_KEY,
        "Name": "알고리즘1",
        "Aliases": "알고리즘 | algorithms | COMP319",
        "Code": "COMP319",
        "Section": "002",
        "Semester": "2026-1",
    }


def _session(
    number: int,
    *,
    aliases: str | None = None,
    transcript_ref: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    entity_id = f"COMP319-S{number:02d}"
    return {
        "ID": entity_id,
        "Name": name or f"{number:02d} · CPU Scheduling",
        "Aliases": aliases or f"{number}강 | {number}번째 강의 | CPU Scheduling",
        "Course": {"relation": [{"id": COURSE_PAGE_ID}]},
        "Session No": number,
        # Enrichment validation treats this graph field as the independent
        # source identity.  The default fixture's transcript is transcript-05.
        "Normalized Transcript": transcript_ref or "transcript-05",
        "Recording Status": "Ready",
    }


def _material(material_id: str = "COMP319-M03", *, source_ref: str = "material-m03") -> dict[str, Any]:
    return {
        "ID": material_id,
        "Name": "Algorithmic Analysis II",
        "Aliases": "Lec3 | Algorithmic Analysis II | 알고리즘 분석 2",
        "Course": {"relation": [{"id": COURSE_PAGE_ID}]},
        "Type": "Lecture Slides",
        "Source Folder": "https://drive.google.com/drive/folders/materials",
        "Original Filename": f"{material_id}.pdf",
        "Normalized Source": source_ref,
        "Current Source Version": 1,
        "Text Source": "PDF Extract",
        "Visual Dependency": "None",
        "AI Priority": "Normal",
        "Page Count": 2,
        "Text Status": "Ready",
    }


class FakeNotionReader:
    """Small in-memory implementation of the Phase 2 ``NotionReader``."""

    def __init__(
        self,
        *,
        courses: Iterable[Mapping[str, Any]] | None = None,
        sessions: Iterable[Mapping[str, Any]] | None = None,
        materials: Iterable[Mapping[str, Any]] | None = None,
        material_usage: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
        enrichments: Mapping[str, Any] | None = None,
        material_enrichments: Mapping[str, Any] | None = None,
        annotations: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    ) -> None:
        course_values = list(courses or [_course()])
        session_values = list(sessions or [_session(5)])
        material_values = list(materials or [_material()])
        self.courses: dict[str, dict[str, Any]] = {}
        course_keys: dict[str, str] = {}
        for index, item in enumerate(course_values, start=1):
            copied = deepcopy(dict(item))
            legacy_key = copied.get("Course Key", copied.get("ID"))
            relation_id = str(copied.get("id", copied.get("page_id", f"course-page-{index}")))
            copied.pop("ID", None)
            copied["id"] = relation_id
            self.courses[relation_id] = copied
            if isinstance(legacy_key, str) and legacy_key.strip():
                course_keys[legacy_key.strip()] = relation_id
            key = copied.get("Course Key")
            if isinstance(key, str) and key.strip():
                course_keys[key.strip()] = relation_id
        self.sessions = {
            str(item["ID"]): _canonical_graph_record(item, course_keys)
            for item in session_values
        }
        self.materials = {
            str(item["ID"]): _canonical_graph_record(item, course_keys)
            for item in material_values
        }
        self.material_usage = {
            key: [_canonical_usage(item, course_keys, self.sessions) for item in values]
            for key, values in (material_usage or {}).items()
        }
        self.enrichments = dict(enrichments or {})
        self.material_enrichments = dict(material_enrichments or {})
        self.annotations = {
            key: [deepcopy(dict(item)) for item in values]
            for key, values in (annotations or {}).items()
        }

    def get_session(self, entity_id: str) -> Mapping[str, Any] | None:
        return self.sessions.get(entity_id)

    def find_sessions_by_alias(self, course: Any, alias_norm: str) -> list[Mapping[str, Any]]:
        values = [item for item in self.sessions.values() if _same_course(item, course)]
        return find_alias_matches(values, alias_norm)

    def list_course_sessions(self, course: Any) -> list[Mapping[str, Any]]:
        return [item for item in self.sessions.values() if _same_course(item, course)]

    def get_material_usage(self, session_id: str) -> list[Mapping[str, Any]]:
        return list(self.material_usage.get(session_id, ()))

    def get_session_enrichment(self, entity_id: str) -> Any | None:
        return self.enrichments.get(entity_id)

    def get_course_by_alias(self, alias_norm: str) -> Mapping[str, Any] | None:
        wanted = normalize_alias(alias_norm)
        matches = find_alias_matches(self.courses.values(), wanted)
        if not matches:
            matches = [
                course
                for course in self.courses.values()
                if any(
                    isinstance(course.get(name), str)
                    and normalize_alias(course[name]) == wanted
                    for name in ("Course Key", "Code", "id")
                )
            ]
        return matches[0] if matches else None

    def get_course_by_relation_id(self, relation_page_id: str) -> Mapping[str, Any] | None:
        return self.courses.get(relation_page_id)

    def get_material(self, material_id: str) -> Mapping[str, Any] | None:
        return self.materials.get(material_id)

    def get_material_enrichment(self, material_id: str) -> Any | None:
        return self.material_enrichments.get(material_id)

    def get_session_user_annotations(self, session_id: str) -> list[Mapping[str, Any]]:
        return list(self.annotations.get(session_id, ()))


class FakeNotionWriter:
    """Strict worker-side fake for every Phase4 Notion write boundary.

    The object deliberately does not expose the graph reader methods.  Queue
    rows are stored by physical record ID, while ``queue`` remains as a small
    compatibility view for older tests and local debugging.
    """

    def __init__(
        self,
        reader: FakeNotionReader | None = None,
        *,
        events: list[Any] | None = None,
        database_ids: Mapping[str, str] | None = None,
        automation_queue_id: str | None = None,
    ) -> None:
        self.reader = reader or FakeNotionReader()
        self.events = events if events is not None else []
        self.ai_region_writes: list[dict[str, Any]] = []
        self.source_metadata_writes: list[dict[str, Any]] = []
        self.writes: list[tuple[str, str, dict[str, Any], AutomationActor]] = []
        self.queue_rows: list[dict[str, Any]] = []
        self.queue: dict[str, dict[str, Any]] = {}
        self.entities: dict[tuple[str, str], dict[str, Any]] = {}
        self.create_calls = 0
        self.update_calls = 0
        self.target_mutations = 0
        self.database_ids = {
            _fake_key(logical): value.strip()
            for logical, value in (database_ids or {}).items()
            if isinstance(logical, str) and isinstance(value, str) and value.strip()
        }
        configured_queue = automation_queue_id or self.database_ids.get("automationqueue")
        self.automation_queue_id = configured_queue.strip() if isinstance(configured_queue, str) else None

        # Failure switches model provider outcomes used by the Phase4 retry
        # tests.  Aliases are intentionally public so a fixture can express
        # the failure mode without reaching into implementation internals.
        self.fail_create_after_commit = False
        self.create_then_raise = False
        self.mutate_then_raise_target = False
        self.target_mutate_then_raise = False
        self.raise_before_target = False
        self.target_raise_before_mutation = False
        self.fail_audit_once = False

    def _target(self, target_db: str) -> tuple[str, str]:
        if not isinstance(target_db, str) or not target_db.strip():
            raise PolicyViolation("target database is required")
        normalized = _fake_key(target_db)
        if normalized in _SCHEMAS:
            provider_id = self.database_ids.get(normalized, target_db)
            return normalized, provider_id
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
        GuardedNotionWriter._validate_properties(self, logical, properties, is_create=is_create)

    def _queue_values(self) -> list[dict[str, Any]]:
        values = list(self.queue_rows)
        seen = {id(value) for value in values}
        for value in self.queue.values():
            if id(value) not in seen:
                if "record_id" not in value:
                    value["record_id"] = f"queue-external-{len(values) + 1}"
                values.append(value)
                seen.add(id(value))
        return values

    def find_approval_rows(self, proposal_id: str) -> list[dict[str, Any]]:
        return [
            row
            for row in self._queue_values()
            if row.get("Proposal ID") == proposal_id
        ]

    def read_approval(self, proposal_id: str) -> dict[str, Any] | None:
        from uls.adapters.notion.base import _unique_approval_row

        return _unique_approval_row(self.find_approval_rows(proposal_id), proposal_id)

    def find_entity_by_id(self, target_db: str, entity_id: str) -> Mapping[str, Any] | None:
        logical, _ = self._target(target_db)
        if logical == "materialusage":
            matches = self._usage_rows(entity_id)
            return matches[0] if len(matches) == 1 else None
        if logical == "automationqueue":
            rows = [row for row in self._queue_values() if row.get("record_id") == entity_id]
            return rows[0] if len(rows) == 1 else None
        return self.entities.get((target_db, entity_id)) or self.entities.get((logical, entity_id))

    def create_entity(
        self,
        target_db: str,
        properties: Mapping[str, Any],
        *,
        actor: AutomationActor = AutomationActor.AUTOMATION,
    ) -> Mapping[str, Any]:
        logical, provider_id = self._target(target_db)
        if not isinstance(properties, Mapping):
            raise PolicyViolation("create properties must be a mapping")
        self._validate_properties(logical, properties, is_create=True)
        self._enforce(actor, provider_id, properties, is_create=True)
        if logical == "automationqueue":
            return _create_phase4_queue_once(
                self, properties,
                lambda: self._create_entity_row(logical, target_db, properties),
                queue_db_id=provider_id,
            )
        return self._create_entity_row(logical, target_db, properties)

    def _create_entity_row(
        self, logical: str, target_db: str, properties: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        item = deepcopy(dict(properties))
        self.create_calls += 1
        if logical == "automationqueue":
            proposal_id = str(item["Proposal ID"])
            row = {"record_id": f"queue-{len(self.queue_rows) + 1}", **item}
            self.queue_rows.append(row)
            self.queue[proposal_id] = row
            self.events.append(("queue_create", row["record_id"], deepcopy(item)))
            if self.fail_create_after_commit or self.create_then_raise:
                self.fail_create_after_commit = False
                self.create_then_raise = False
                raise RuntimeError("queue create committed before provider timeout")
            return row
        if logical == "materialusage":
            session_id = _single_relation_id(item.get("Session"))
            if session_id is None:
                raise PolicyViolation("Material Usage Session relation is required")
            self.reader.material_usage.setdefault(session_id, []).append(item)
            self.entities[("Material Usage", str(item["ID"]))] = item
        else:
            entity_id = str(item.get("ID", item.get("id", f"fake-{self.create_calls}")))
            self.entities[(target_db, entity_id)] = item
            self.entities[(logical, entity_id)] = item
        self.events.append(("notion_create", target_db, deepcopy(item)))
        return item

    def update_properties(
        self,
        target_db: str,
        entity_id: str,
        patch: Mapping[str, Any],
        *,
        actor: AutomationActor = AutomationActor.AUTOMATION,
        system_transition: bool = False,
    ) -> Mapping[str, Any]:
        logical, provider_id = self._target(target_db)
        if not isinstance(patch, Mapping):
            raise PolicyViolation("update patch must be a mapping")
        self._validate_properties(logical, patch, is_create=False)
        self._enforce(
            actor,
            provider_id,
            patch,
            system_transition=system_transition,
        )
        if logical == "automationqueue":
            row = self._queue_row(entity_id)
            from uls.adapters.notion.base import (
                _unique_approval_row,
                _validate_phase4_queue_identity,
            )
            _unique_approval_row(self.find_approval_rows(row["Proposal ID"]), row["Proposal ID"])
            _validate_phase4_queue_identity(row)
            _validate_queue_transition(row, patch, actor)
        self.update_calls += 1
        if logical == "automationqueue":
            row = self._queue_row(entity_id)
            if self.fail_audit_once and any(
                key in patch for key in ("Decision By", "Decision At", "Applied At", "State")
            ):
                self.fail_audit_once = False
                raise RuntimeError("simulated queue audit outage")
            row.update(deepcopy(dict(patch)))
            self.writes.append((target_db, entity_id, dict(patch), actor))
            self.events.append(("queue_update", row.get("record_id"), deepcopy(dict(patch))))
            return row
        if logical == "materialusage":
            if self.raise_before_target or self.target_raise_before_mutation:
                self.raise_before_target = False
                self.target_raise_before_mutation = False
                raise ProviderWriteNotAppliedError("target write failed before mutation")
            row = self._usage_row(entity_id)
            row.update(deepcopy(dict(patch)))
            self.target_mutations += 1
            self.writes.append((target_db, entity_id, dict(patch), actor))
            self.events.append(("usage_update", entity_id, deepcopy(dict(patch))))
            if self.mutate_then_raise_target or self.target_mutate_then_raise:
                self.mutate_then_raise_target = False
                self.target_mutate_then_raise = False
                raise RuntimeError("target write committed before provider timeout")
            return row
        row = self.entities.get((target_db, entity_id)) or self.entities.get((logical, entity_id))
        if row is None:
            raise KeyError(entity_id)
        row.update(deepcopy(dict(patch)))
        self.writes.append((target_db, entity_id, dict(patch), actor))
        return row

    def update_session_metadata(self, entity_id: str, patch: Mapping[str, Any]) -> Mapping[str, Any]:
        unknown = set(patch).difference(SESSION_PROPERTIES)
        if unknown:
            raise ValueError(
                "Sessions patch contains properties outside §14.2: "
                + ", ".join(sorted(str(key) for key in unknown))
            )
        enforce_write_policy(AutomationActor.AUTOMATION, "Sessions", patch)
        if entity_id not in self.reader.sessions:
            self.reader.sessions[entity_id] = {"ID": entity_id}
        self.reader.sessions[entity_id].update(dict(patch))
        self.events.append(("notion", entity_id, dict(patch)))
        return self.reader.sessions[entity_id]

    def write_source_metadata_region(
        self,
        target_db: str,
        entity_id: str,
        patch: Mapping[str, Any],
        *,
        actor: AutomationActor = AutomationActor.AUTOMATION,
    ) -> Mapping[str, Any]:
        logical, _ = self._target(target_db)
        self._enforce(actor, target_db, patch)
        if logical in {"automationqueue", "materialusage"}:
            raise PolicyViolation("source metadata region cannot write Queue or Material Usage")
        captured = {
            "target_db": target_db,
            "entity_id": entity_id,
            "patch": deepcopy(dict(patch)),
            "actor": actor,
        }
        self.source_metadata_writes.append(captured)
        self.events.append(("source_region", target_db, entity_id, deepcopy(dict(patch))))
        return captured

    def write_ai_region(
        self,
        target_db: str,
        entity_id: str,
        patch: Mapping[str, Any],
        *,
        actor: AutomationActor = AutomationActor.AUTOMATION,
    ) -> Mapping[str, Any]:
        logical, _ = self._target(target_db)
        if logical == "automationqueue":
            raise PolicyViolation("AI region cannot write Automation Queue")
        if not isinstance(actor, AutomationActor):
            raise ValueError("AI-region actor must be an AutomationActor")
        enforce_write_policy(actor, target_db, patch)
        if set(patch).difference({"enrichment", "ownership"}):
            raise ValueError("AI-region patch contains non-AI fields")
        if patch.get("ownership") != "AI":
            raise ValueError("AI-region ownership is fixed to AI")
        raw_record = patch.get("enrichment")
        if raw_record is None:
            raise ValueError("AI-region patch requires an enrichment record")
        record = coerce_enrichment(raw_record)
        normalized_db = logical
        if normalized_db == "sessions":
            if entity_id not in self.reader.sessions:
                raise KeyError(entity_id)
            self.reader.enrichments[entity_id] = deepcopy(record)
        elif normalized_db == "materials":
            if entity_id not in self.reader.materials:
                raise KeyError(entity_id)
            self.reader.material_enrichments[entity_id] = deepcopy(record)
        else:
            raise ValueError(f"unsupported AI-region database: {target_db!r}")
        captured = {
            "target_db": target_db,
            "entity_id": entity_id,
            "patch": deepcopy(dict(patch)),
            "actor": actor,
        }
        self.ai_region_writes.append(captured)
        self.events.append(("ai_region", target_db, entity_id, deepcopy(dict(patch))))
        return captured

    def restore_ai_region(
        self,
        target_db: str,
        entity_id: str,
        previous: Any | None,
        *,
        actor: AutomationActor = AutomationActor.AUTOMATION,
    ) -> None:
        logical, _ = self._target(target_db)
        if logical == "automationqueue":
            raise PolicyViolation("AI region cannot write Automation Queue")
        if not isinstance(actor, AutomationActor):
            raise ValueError("AI-region actor must be an AutomationActor")
        enforce_write_policy(actor, target_db, {"ownership": "AI"})
        store = (
            self.reader.enrichments
            if logical == "sessions"
            else self.reader.material_enrichments
            if logical == "materials"
            else None
        )
        if store is None:
            raise ValueError(f"unsupported AI-region database: {target_db!r}")
        if previous is None:
            store.pop(entity_id, None)
        else:
            store[entity_id] = deepcopy(coerce_enrichment(previous))
        self.events.append(("ai_region_rollback", target_db, entity_id))

    def _enforce(
        self,
        actor: AutomationActor,
        target_db: str,
        patch: Mapping[str, Any],
        *,
        is_create: bool = False,
        system_transition: bool = False,
    ) -> None:
        ids = {self.automation_queue_id} if self.automation_queue_id else set()
        logical, _ = self._target(target_db)
        enforce_write_policy(
            actor,
            logical,
            patch,
            automation_queue_ids=ids,
            is_create=is_create,
            system_transition=system_transition,
        )

    def _queue_row(self, entity_id: str) -> dict[str, Any]:
        rows = [
            row
            for row in self._queue_values()
            if row.get("record_id") == entity_id or row.get("Proposal ID") == entity_id
        ]
        if len(rows) != 1:
            raise PolicyViolation("Queue physical identity is missing or ambiguous")
        return rows[0]

    def _usage_rows(self, usage_id: str) -> list[dict[str, Any]]:
        return [
            row
            for rows in self.reader.material_usage.values()
            for row in rows
            if row.get("ID") == usage_id
        ]

    def _usage_row(self, usage_id: str) -> dict[str, Any]:
        rows = self._usage_rows(usage_id)
        if len(rows) != 1:
            raise PolicyViolation("Material Usage identity is missing or ambiguous")
        return rows[0]


class FakeNotionAdapter(FakeNotionWriter):
    """Backward-compatible name for the strict worker-side fake."""

    def __init__(self, reader: FakeNotionReader | None = None, **kwargs: Any) -> None:
        super().__init__(reader or FakeNotionReader(), **kwargs)


FakeNotion = FakeNotionReader


def _fake_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _single_relation_id(value: Any) -> str | None:
    values = relation_page_ids(value)
    return values[0] if len(values) == 1 else None


def _same_course(record: Mapping[str, Any], course: Any) -> bool:
    if course is None:
        return True
    wanted = course
    if isinstance(course, Mapping):
        wanted = course.get("id", course.get("page_id"))
        if wanted is None:
            wanted = course.get("Course Key", course.get("ID", course.get("Code")))
    actual = record.get("Course", record.get("Course Key"))
    if isinstance(actual, Mapping):
        relation = resolve_course_relation(actual)
        actual = relation or actual.get("Course Key", actual.get("ID", actual.get("Code")))
    if actual is None or wanted is None:
        return False
    return normalize_alias(str(actual)) == normalize_alias(str(wanted)) or (
        str(wanted).casefold() == "comp319" and "comp319" in str(actual).casefold()
    )


def _canonical_graph_record(item: Mapping[str, Any], course_keys: Mapping[str, str]) -> dict[str, Any]:
    copied = deepcopy(dict(item))
    relation = copied.get("Course")
    if isinstance(relation, str):
        relation_id = course_keys.get(relation.strip())
        if relation_id is not None:
            copied["Course"] = {"relation": [{"id": relation_id}]}
    return copied


def _canonical_usage(
    item: Mapping[str, Any],
    course_keys: Mapping[str, str],
    sessions: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    copied = deepcopy(dict(item))
    session_value = copied.get("Session")
    if isinstance(session_value, str):
        copied["Session"] = {"relation": [{"id": session_value}]}
    if "Material ID" in copied and "Material" not in copied:
        copied["Material"] = {"relation": [{"id": str(copied.pop("Material ID"))}]}
    return copied


__all__ = [
    "COURSE_KEY",
    "COURSE_PAGE_ID",
    "SESSION_PROPERTIES",
    "FakeNotion",
    "FakeNotionAdapter",
    "FakeNotionReader",
    "FakeNotionWriter",
]
