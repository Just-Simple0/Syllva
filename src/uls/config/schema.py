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


@dataclass
class NotionCfg:
    courses_db_id: str = ""
    sessions_db_id: str = ""
    materials_db_id: str = ""
    material_usage_db_id: str = ""
    activities_db_id: str = ""
    exams_db_id: str = ""
    automation_queue_db_id: str = ""


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


@dataclass
class BehaviorContractCfg:
    version: int = 1
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

    @property
    def drive(self) -> DriveCfg:
        """Convenient alias for callers that call the section ``drive``."""

        return self.google_drive


__all__ = [
    "BehaviorContractCfg",
    "CourseCfg",
    "DriveCfg",
    "McpCfg",
    "NormalizationCfg",
    "NotionCfg",
    "RemoteMcpCfg",
    "RetrievalCfg",
    "StorageCfg",
    "SystemCfg",
    "UlsConfig",
    "WorkerCfg",
]
