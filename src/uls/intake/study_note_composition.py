"""Worker-only SDK bindings for the separate study-note publication boundary."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from uls.domain.errors import SourceUnavailableError
from uls.study_notes.handler import NoteAuthorityChanged


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SourceUnavailableError("Notion block response is malformed")
    return dict(value)


class NotionSDKBlockPort:
    """Adapt the already authenticated worker client; never resolve credentials."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def append_block_children(
        self, parent_block_id: str, children: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        return _mapping(self.client.blocks.children.append(block_id=parent_block_id, children=list(children)))

    def list_block_children(
        self, block_id: str, *, start_cursor: str | None = None,
    ) -> Mapping[str, Any]:
        arguments: dict[str, Any] = {"block_id": block_id, "page_size": 100}
        if start_cursor is not None:
            arguments["start_cursor"] = start_cursor
        return _mapping(self.client.blocks.children.list(**arguments))

    def retrieve_block(self, block_id: str) -> Mapping[str, Any]:
        return _mapping(self.client.blocks.retrieve(block_id=block_id))

    def update_block(self, block_id: str, **fields: Any) -> Mapping[str, Any]:
        if set(fields) != {"code"}:
            raise ValueError("study-note block updates accept only code content")
        return _mapping(self.client.blocks.update(block_id=block_id, **fields))


class StudyNoteSessionBindings:
    """Resolve physical Session identity and the registered private derived folder."""

    def __init__(self, *, graph: Any, engine: Any, provenance: Any, drive: Any,
                 workspaces: Sequence[Any]) -> None:
        self.graph, self.engine, self.provenance, self.drive = graph, engine, provenance, drive
        self.workspaces = tuple(workspaces)

    def course_key(self, session_id: str) -> str:
        session = self.graph.get_session(session_id)
        if session is None:
            raise NoteAuthorityChanged("Session is unavailable")
        key = self.engine._course_for_record(session, session_id).course_key
        if not isinstance(key, str) or sum(workspace.course_key == key for workspace in self.workspaces) != 1:
            raise NoteAuthorityChanged("Session Course is outside the active workspace")
        return key

    def physical_id(self, session_id: str) -> str:
        from uuid import UUID

        self.course_key(session_id)
        session = self.graph.get_session(session_id)
        try:
            return str(UUID(session["page_id"]))
        except (TypeError, ValueError, KeyError):
            raise NoteAuthorityChanged("Session physical identity is unavailable") from None

    def derived_folder_id(self, session_id: str) -> str:
        from uls.adapters.drive.binding import ValidatedSourceBindingResolver
        from uls.adapters.drive.worker import DRIVE_FOLDER_MIME, require_private_ownership

        key = self.course_key(session_id)
        workspace = next(item for item in self.workspaces if item.course_key == key)
        session = self.graph.get_session(session_id)
        source = ValidatedSourceBindingResolver(self.provenance).resolve_derivative_ref(
            session_id, session.get("Normalized Transcript"),
        )
        binding = self.provenance.binding(session_id)
        if source.identity != binding.source_ref.identity:
            raise NoteAuthorityChanged("Session source binding changed")
        derivative = self.drive.read_metadata(binding.derivative_ref.file_id)
        if (derivative.file_id != binding.derivative_ref.file_id
                or derivative.trashed or len(derivative.parents) != 1):
            raise NoteAuthorityChanged("Session derivative parent is unavailable")
        require_private_ownership(derivative, context="Session derivative")
        folder = self.drive.read_metadata(derivative.parents[0])
        if (folder.file_id != derivative.parents[0] or not isinstance(folder.file_id, str)
                or folder.trashed or folder.mime_type != DRIVE_FOLDER_MIME or len(folder.parents) != 1):
            raise NoteAuthorityChanged("Session derived folder is unavailable")
        require_private_ownership(folder, context="Session derived folder")
        entity = self.drive.read_metadata(folder.parents[0])
        if (entity.file_id != folder.parents[0] or entity.trashed or entity.mime_type != DRIVE_FOLDER_MIME
                or entity.parents != (workspace.recordings_folder_id,)):
            raise NoteAuthorityChanged("Session folder is outside configured recordings")
        require_private_ownership(entity, context="Session folder")
        return folder.file_id


@dataclass(frozen=True)
class _StudyNoteReadSources:
    """Only the graph data sources C6 reads; independent from C5 readiness."""

    workspace_id: str
    semester: str
    courses: str
    sessions: str
    materials: str
    material_usage: str | None

    def __post_init__(self) -> None:
        for name in ("courses", "sessions", "materials", "material_usage"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a non-empty source id when configured")

    def mapping(self) -> dict[str, str]:
        result = {
            "academic_courses": self.courses,
            "sessions": self.sessions,
            "materials": self.materials,
        }
        if self.material_usage:
            result["material_usage"] = self.material_usage
        return result


def install_study_notes(worker: Any, config: Any, state: Any, notion: Any,
                        source_reader: Any, *, block_port: Any = None) -> None:
    """Compose the opted-in local inbox into the current semester's barrier."""
    if config.study_notes.enabled is not True:
        return
    from datetime import UTC, datetime
    from pathlib import Path

    from uls.adapters.drive.binding import ValidatedSourceBindingResolver
    from uls.adapters.notion.intake import NotionIntakeWriter
    from uls.adapters.notion.usage_range import UsageRangeNotionBridge
    from uls.ephemeral.memory import MemoryEphemeralStore
    from uls.intake.composition import merge_request_handler
    from uls.retrieval.engine import RetrievalEngine
    from uls.state.reader import ReadOnlyState
    from uls.study_notes.drive import StudyNoteDriveStaging
    from uls.study_notes.handler import StudyNoteHandler
    from uls.study_notes.notion import StudyNoteAIBlockBridge
    from uls.study_notes.retrieval_adapter import RetrievalEngineEvidenceAdapter
    from uls.study_notes.store import StudyNoteStore

    active = {item.semester for item in worker.workspaces}
    candidates = [item for item in config.notion.semester_workspaces if item.semester in active]
    if len(candidates) != 1:
        raise ValueError("study-note inbox requires one unambiguous active semester")
    workspace = candidates[0]
    readiness = worker.request_extension_readiness.setdefault(workspace.semester, {})
    if isinstance(notion, NotionIntakeWriter):
        notion = notion.backend
    if block_port is None and hasattr(notion, "client"):
        block_port = NotionSDKBlockPort(notion.client)
    if source_reader is None or block_port is None or not worker.intake_ready:
        readiness["study_notes"] = {"status": "NOT_VERIFIED", "reason": "worker provider bindings unavailable"}
        return
    if workspace.study_requests_data_source_id:
        # A schema guess must never activate form writes or override cancellations.
        readiness["study_notes"] = {"status": "NOT_VERIFIED", "reason": "Notion request schema not accepted"}
        return
    sources = _StudyNoteReadSources(
        workspace_id=workspace.semester, semester=workspace.semester,
        courses=workspace.academic_courses_data_source_id,
        sessions=workspace.sessions_data_source_id,
        materials=workspace.materials_data_source_id,
        material_usage=workspace.material_usage_data_source_id or None,
    )
    # This read-only graph does not require C5 Queue activation for transcript mode.
    graph: Any = UsageRangeNotionBridge(notion, cast(Any, sources)).graph_view()
    provenance = ReadOnlyState(state.db_path)
    engine = RetrievalEngine(graph, source_reader, provenance, MemoryEphemeralStore(), config,
                             source_binding_resolver=ValidatedSourceBindingResolver(provenance))
    bindings = StudyNoteSessionBindings(graph=graph, engine=engine, provenance=provenance,
                                       drive=worker.drive, workspaces=worker.workspaces)
    store = StudyNoteStore(Path(state.db_path).parent / 'study_notes.sqlite3')
    local_source_id = 'uls-submit:' + workspace.semester

    def list_local_requests(source: str) -> list[dict[str, Any]]:
        if source != local_source_id:
            raise ValueError("unknown study-note inbox source")
        return [{**row, "id": row["client_request_id"]}
                for row in store.list_pending_client_requests(limit=10_001)]

    try:
        handler = StudyNoteHandler(
            state=state, store=store,
            engine=RetrievalEngineEvidenceAdapter(engine, usage_mapping_available=bool(sources.material_usage)),
            drive_staging=StudyNoteDriveStaging(worker.drive, store),
            notion_bridge=StudyNoteAIBlockBridge(block_port, store),
            workspace=workspace.semester, clock=lambda: datetime.now(UTC).isoformat(), local_source_id=local_source_id,
            session_course_key=bindings.course_key, session_provider_page_id=bindings.physical_id,
            session_derived_folder_id=bindings.derived_folder_id,
            template_version=config.study_notes.template_version,
            generator_config_version=config.study_notes.generator_config_version,
            grant_ttl_seconds=config.study_notes.grant_ttl_seconds,
        )
        merge_request_handler(worker, workspace=workspace.semester, name='study_notes',
                              handler=handler, sources={local_source_id: list_local_requests})
    except BaseException:
        store.close()
        raise
    worker.extension_resources.append(store)
    readiness["study_notes"] = {"status": "READY", "transport": "local_inbox"}
