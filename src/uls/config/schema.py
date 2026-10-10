"""Typed configuration schema for the frozen v1.2 YAML contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .google_oauth import GoogleOAuthClient


@dataclass
class SystemCfg:
    timezone: str = "Asia/Seoul"
    workspace_dir: str = "~/.uls"
    state_backend: str = "sqlite"
    ephemeral_backend: str = "memory"


@dataclass
class WorkerCfg:
    enabled: bool = True
    poll_interval_minutes: int = 1


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
    semester_registries: list[SemesterRegistryCfg] = field(default_factory=list)
    worker_credentials_path: str = ""
    mcp_credentials_path: str = ""


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
    semester_workspaces: list[SemesterWorkspaceCfg] = field(default_factory=list)


@dataclass
class CourseStaticFolderCfg:
    recordings_folder_id: str = ""
    materials_folder_id: str = ""


@dataclass
class SemesterRegistryCfg:
    semester: str = ""
    folder_id: str = ""
    upload_folder_id: str = ""
    # Effective semester date range (ISO ``YYYY-MM-DD``) used by the intake
    # classification v2 recording calendar; empty means unknown (AMBIGUOUS).
    start_date: str = ""
    end_date: str = ""
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
    # Explicit v1.3 extensions. Missing mappings never fall back to legacy IDs.
    material_usage_data_source_id: str = ""
    automation_queue_data_source_id: str = ""
    study_requests_data_source_id: str = ""
    portal_page_ids: dict[str, str] = field(default_factory=dict)


@dataclass
class NormalizationCfg:
    schema_version: str = "v1"
    processor_version: str = "1.2.0"
    goodnotes_visual_fallback: bool = True


@dataclass
class RetrievalCfg:
    # v1.3 additive selector.  The frozen v1.2 global registry remains the
    # default; semester workspaces are used only after an explicit opt-in.
    notion_lane: str = "legacy_global"
    semester: str = ""
    concept_mode: str = "bounded_lexical"
    max_candidate_entities: int = 20
    max_candidate_chunks: int = 12
    context_ttl_seconds: int = 900
    resolution_ttl_seconds: int = 900
    allow_bounded_llm_rerank: bool = False
    # Enabled preserves the frozen Session contract: callers still need the
    # explicit include_provisional=True request opt-in.
    allow_provisional_material_usage: bool = True
    # Intake classification v2 (plan §7): new v2 content stays out of every
    # retrieval path until the P-D fail-closed filters are active.
    v2_exposure_gate: bool = False
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
class StudyNotesCfg:
    """Separate opt-in for AI draft submission; never enables search writes."""

    enabled: bool = False
    local_caller_id: str = "uls-local-study-notes"
    template_version: str = "study-note.v1"
    generator_config_version: str = "mcp-client-draft.v1"
    grant_ttl_seconds: int = 1800
    max_draft_chars: int = 100_000


@dataclass
class OidcCfg:
    issuer: str = ""
    audience: str = ""
    authorized_subject: str = ""
    authorized_email: str = ""
    jwks_uri: str = ""
    leeway_seconds: int = 60


@dataclass
class RemoteOAuthCfg:
    google_client_id: str = ""
    authorized_email: str = ""
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 2_592_000
    authorization_ttl_seconds: int = 600


@dataclass
class RemoteMcpCfg:
    enabled: bool = False
    auth_mode: str = "oauth_or_bearer"
    edge_mode: str = "direct_tls"
    public_unauthenticated: bool = False
    public_url: str = ""
    host: str = "127.0.0.1"
    port: int = 8765
    tls_certfile: str = ""
    tls_keyfile: str = ""
    oidc: OidcCfg = field(default_factory=OidcCfg)
    oauth: RemoteOAuthCfg = field(default_factory=RemoteOAuthCfg)


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
    # Extra deterministic aliases for file-name course resolution (plan §2.3).
    # Aliases shared by two courses in one semester are disabled at startup.
    aliases: list[str] = field(default_factory=list)


@dataclass
class ClassificationCfg:
    """Intake classification v2 (docs/plans/intake-classification-v2.md).

    ``enabled`` false keeps the current HUMAN-only intake behaviour.  The
    thresholds are the user-decided S2 confirmation gates; lowering them is
    a human decision recorded in config, never a runtime adjustment.
    """

    enabled: bool = False
    # Notion intake schema profile: "" keeps the current selection
    # (legacy5 / c5-range-v1); "legacy5-cls" or "c5-range-v2" enable the
    # classification properties after a human added them and readback verified.
    schema_profile: str = ""
    min_confidence: float = 0.80
    min_top_probability: float = 0.70
    max_calls_per_tick: int = 50
    max_source_bytes: int = 50 * 1024 * 1024
    max_terminal_scan: int = 50
    # Verified Canvas course ID -> canonical course_key (plan §2.3). Only
    # registry rows with verification_state=api_code_verified belong here.
    canvas_course_map: dict[int, str] = field(default_factory=dict)


@dataclass
class IntakeCfg:
    classification: ClassificationCfg = field(default_factory=ClassificationCfg)


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
    study_notes: StudyNotesCfg = field(default_factory=StudyNotesCfg)
    remote_mcp: RemoteMcpCfg = field(default_factory=RemoteMcpCfg)
    behavior_contract: BehaviorContractCfg = field(default_factory=BehaviorContractCfg)
    courses: list[CourseCfg] = field(default_factory=list)
    intake: IntakeCfg = field(default_factory=IntakeCfg)
    # Credential name -> declared source ("environment" | "keyring" | "file").
    # Parsed by config/loader.py's _credentials_section(); missing entries
    # default to "environment" at CredentialResolver construction time, not
    # here. See docs/plans/credential-resolver.md.
    credentials: dict[str, str] = field(default_factory=dict)
    # Local Settings role slug -> random non-secret revision written by every
    # credential operation (GUI-2 R2); never derived from a credential value.
    credential_revisions: dict[str, str] = field(default_factory=dict)
    canvas: dict[str, Any] = field(default_factory=dict)
    google_worker_credentials_path: str = ""
    google_mcp_credentials_path: str = ""
    # Personal Google Desktop OAuth client (docs/plans/drive-oauth-p2-r2.md).
    # All-or-none: both strings or None. Never contains tokens.
    google_oauth: GoogleOAuthClient | None = None

    @property
    def drive(self) -> DriveCfg:
        """Convenient alias for callers that call the section ``drive``."""

        return self.google_drive

    @property
    def google_path_overrides(self) -> dict[str, str]:
        """Extract composition-root non-secret Google credential path overrides."""
        overrides: dict[str, str] = {}
        worker_path = self.google_worker_credentials_path or self.google_drive.worker_credentials_path
        mcp_path = self.google_mcp_credentials_path or self.google_drive.mcp_credentials_path
        if worker_path:
            overrides["GOOGLE_WORKER_CREDENTIALS_FILE"] = worker_path
        if mcp_path:
            overrides["GOOGLE_MCP_CREDENTIALS_FILE"] = mcp_path
        return overrides

    def resolve_semester_workspace(self, course_key: str):
        """Resolve an intake workspace without falling back to legacy IDs."""

        from .intake import resolve_semester_workspace

        return resolve_semester_workspace(self, course_key)


__all__ = [
    "BehaviorContractCfg",
    "CourseCfg",
    "CourseStaticFolderCfg",
    "DriveCfg",
    "GoogleOAuthClient",
    "McpCfg",
    "NormalizationCfg",
    "NotionCfg",
    "RemoteMcpCfg",
    "RemoteOAuthCfg",
    "RetrievalCfg",
    "SemesterRegistryCfg",
    "SemesterWorkspaceCfg",
    "StorageCfg",
    "StudyNotesCfg",
    "SystemCfg",
    "UlsConfig",
    "WorkerCfg",
]
