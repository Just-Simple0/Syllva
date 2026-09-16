"""Typed configuration schema for the frozen v1.2 YAML contract."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SystemCfg:
    timezone: str = "Asia/Seoul"
    workspace_dir: str = "~/.uls"
    state_backend: str = "sqlite"
    ephemeral_backend: str = "memory"


@dataclass
class WorkerCfg:
    enabled: bool = True
    poll_interval_minutes: int = 10


@dataclass
class StorageCfg:
    """Storage subsection present in ``config.example.yaml``."""

    normalized_derivatives: str = "google_drive"


@dataclass
class DriveCfg:
    university_root_id: str = ""
    inbox_root_id: str = ""
    # The v1.3 intake preview is explicitly semester scoped.  These values
    # are provider IDs returned by reviewed static provisioning; they are not
    # discovered by display name and are never used as a retrieval fallback.
    semester_registries: list["SemesterRegistryCfg"] = field(default_factory=list)


@dataclass
class NotionCfg:
    courses_db_id: str = ""
    sessions_db_id: str = ""
    materials_db_id: str = ""
    material_usage_db_id: str = ""
    activities_db_id: str = ""
    exams_db_id: str = ""
    automation_queue_db_id: str = ""
    # Current-semester canonical/operational data sources.  The existing
    # *_db_id fields above remain the v1.2 legacy retrieval lane.
    semester_workspaces: list["SemesterWorkspaceCfg"] = field(default_factory=list)


@dataclass
class CourseStaticFolderCfg:
    recordings_folder_id: str = ""
    materials_folder_id: str = ""


@dataclass
class SemesterRegistryCfg:
    semester: str = ""
    folder_id: str = ""
    upload_folder_id: str = ""
    course_folder_ids: dict[str, str] = field(default_factory=dict)
    course_static_folder_ids: dict[str, CourseStaticFolderCfg] = field(default_factory=dict)
    optional_course_upload_folder_ids: dict[str, str] = field(default_factory=dict)


@dataclass
class SemesterWorkspaceCfg:
    semester: str = ""
    connection_settings_files_parent_id: str = ""
    academic_courses_data_source_id: str = ""
    sessions_data_source_id: str = ""
    materials_data_source_id: str = ""
    file_intake_data_source_id: str = ""
    input_requests_data_source_id: str = ""
    portal_page_ids: dict[str, str] = field(default_factory=dict)


@dataclass
class NormalizationCfg:
    schema_version: str = "v1"
    processor_version: str = "1.2.0"
    goodnotes_visual_fallback: bool = True


@dataclass
class RetrievalCfg:
    concept_mode: str = "bounded_lexical"
    max_candidate_entities: int = 20
    max_candidate_chunks: int = 12
    context_ttl_seconds: int = 900
    resolution_ttl_seconds: int = 900
    allow_bounded_llm_rerank: bool = False
    # Enabled preserves the frozen Session contract: callers still need the
    # explicit include_provisional=True request opt-in.
    allow_provisional_material_usage: bool = True
    # Deployment-configured Material Select -> source authority mapping.  It
    # is never inferred from Usage.Role or derivative front matter.
    material_type_source_class: dict[str, str] = field(
        default_factory=lambda: {
            "Lecture Slides": "professor_material",
            "Professor Notes": "professor_material",
            "Syllabus": "professor_material",
            "Textbook": "supplemental_reference",
            "Reference": "supplemental_reference",
            "Supplementary": "supplemental_reference",
        }
    )
    # Phase 2 context budgets.  A capability describes exactly the evidence
    # returned by a context call, so these limits are applied before issuance.
    max_evidence_items: int = 12
    max_chars_per_item: int = 4000
    max_total_chars: int = 24000
    max_followup_chunks: int = 8


@dataclass
class McpCfg:
    mode: str = "local"
    read_only: bool = True


@dataclass
class RemoteMcpCfg:
    enabled: bool = False
    auth_mode: str = "oauth_or_bearer"
    public_unauthenticated: bool = False
    public_url: str = ""
    host: str = "127.0.0.1"
    port: int = 8765
    tls_certfile: str = ""
    tls_keyfile: str = ""


@dataclass
class BehaviorContractCfg:
    version: int = 2
    path: str = ""


@dataclass
class CourseCfg:
    course_key: str = ""
    name: str = ""
    code: str = ""
    section: str = ""
    semester: str = ""


@dataclass
class UlsConfig:
    system: SystemCfg = field(default_factory=SystemCfg)
    worker: WorkerCfg = field(default_factory=WorkerCfg)
    storage: StorageCfg = field(default_factory=StorageCfg)
    google_drive: DriveCfg = field(default_factory=DriveCfg)
    notion: NotionCfg = field(default_factory=NotionCfg)
    normalization: NormalizationCfg = field(default_factory=NormalizationCfg)
    retrieval: RetrievalCfg = field(default_factory=RetrievalCfg)
    mcp: McpCfg = field(default_factory=McpCfg)
    remote_mcp: RemoteMcpCfg = field(default_factory=RemoteMcpCfg)
    behavior_contract: BehaviorContractCfg = field(default_factory=BehaviorContractCfg)
    courses: list[CourseCfg] = field(default_factory=list)
    # Credential name -> declared source ("environment" | "keyring").
    # Parsed by config/loader.py's _credentials_section(); missing entries
    # default to "environment" at CredentialResolver construction time, not
    # here. See docs/plans/credential-resolver.md.
    credentials: dict[str, str] = field(default_factory=dict)

    @property
    def drive(self) -> DriveCfg:
        """Convenient alias for callers that call the section ``drive``."""

        return self.google_drive

    def resolve_semester_workspace(self, course_key: str):
        """Resolve an intake workspace without falling back to legacy IDs."""

        from .intake import resolve_semester_workspace

        return resolve_semester_workspace(self, course_key)


__all__ = [
    "BehaviorContractCfg",
    "CourseCfg",
    "CourseStaticFolderCfg",
    "DriveCfg",
    "McpCfg",
    "NormalizationCfg",
    "NotionCfg",
    "RemoteMcpCfg",
    "RetrievalCfg",
    "StorageCfg",
    "SemesterRegistryCfg",
    "SemesterWorkspaceCfg",
    "SystemCfg",
    "UlsConfig",
    "WorkerCfg",
]
