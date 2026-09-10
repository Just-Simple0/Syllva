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
from .course_identity import (
    CourseIdentity,
    course_key_of,
    relation_page_ids,
    resolve_course_relation,
    validate_course_record,
)
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
from .page_range import (
    PageRange,
    PageRangeResult,
    PageRangeStatus,
    parse_page_range,
    require_page_range,
)
from .provenance import FreshnessInfo, Provenance, check_freshness
from .source_ref import GitHubRef, SourceFingerprint, SourceRef

__all__ = [
    "AutomationActor",
    "ContextPackage",
    "CourseIdentity",
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
    "PageRange",
    "PageRangeResult",
    "PageRangeStatus",
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
    "course_key_of",
    "is_contained",
    "parse_course_key",
    "parse_entity_id",
    "parse_page_range",
    "parse_locator",
    "serialize_locator",
    "to_derivative_status",
    "to_processing_status",
    "relation_page_ids",
    "require_page_range",
    "resolve_course_relation",
    "validate_course_record",
]
