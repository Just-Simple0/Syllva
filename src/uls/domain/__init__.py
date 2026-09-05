"""ULS domain layer: model-agnostic types, enums, IDs, refs, provenance, errors.

This layer must not depend on client SDKs, OS schedulers, or MCP transport.
"""

from .enums import (
    AutomationActor,
    DerivativeStatus,
    Explicitness,
    FreshnessStatus,
    JobStatus,
    OwnershipZone,
    ProcessingStatus,
    RetrievalIntent,
    SourceAuthority,
    to_derivative_status,
    to_processing_status,
)
from .contracts import Locator
from .errors import LocatorParseError, PolicyViolation, UlsError
from .ids import CourseKey, EntityId, parse_course_key, parse_entity_id
from .models import (
    ContextPackage,
    EvidenceItem,
    PageLocator,
    TimeLocator,
    is_contained,
    parse_locator,
    serialize_locator,
)
from .provenance import FreshnessInfo, Provenance, check_freshness
from .source_ref import GitHubRef, SourceFingerprint, SourceRef

__all__ = [
    "AutomationActor",
    "ContextPackage",
    "CourseKey",
    "DerivativeStatus",
    "EntityId",
    "EvidenceItem",
    "Explicitness",
    "FreshnessInfo",
    "FreshnessStatus",
    "GitHubRef",
    "JobStatus",
    "Locator",
    "LocatorParseError",
    "OwnershipZone",
    "PageLocator",
    "ProcessingStatus",
    "PolicyViolation",
    "Provenance",
    "RetrievalIntent",
    "SourceAuthority",
    "SourceFingerprint",
    "SourceRef",
    "TimeLocator",
    "UlsError",
    "check_freshness",
    "is_contained",
    "parse_course_key",
    "parse_entity_id",
    "parse_locator",
    "serialize_locator",
    "to_derivative_status",
    "to_processing_status",
]
