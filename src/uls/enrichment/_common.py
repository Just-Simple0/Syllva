"""Shared deterministic gates and candidate validation for Phase 3."""

from __future__ import annotations

import inspect
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Iterable, Literal

from uls.adapters.llm.base import LLMAdapter, LLMEnrichmentResult
from uls.domain.enums import Explicitness, ProcessingStatus
from uls.domain.errors import SourcePartialError, SourceUnavailableError, UlsError
from uls.domain.models import PageLocator, TimeLocator
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.normalization.schemas import TimestampMark
from uls.retrieval.chunking import DerivativeChunk, derivative_parts, page_chunks, timestamp_chunks

from .schemas import (
    CompletionState,
    EnrichmentGenerationResult,
    EnrichmentRecord,
    EnrichmentSignal,
    EvidenceLocator,
    Locator,
    MATERIAL_ENRICHMENT_KINDS,
    MaterialEnrichmentPayload,
    SESSION_ENRICHMENT_KINDS,
    SessionEnrichmentPayload,
    is_meaningful_quote,
)


class EnrichmentInputError(SourcePartialError):
    """The candidate derivative is unavailable, non-ready, or not current."""


class EnrichmentOutputError(UlsError):
    """The provider returned a result that cannot be interpreted safely."""

    code = "ENRICHMENT_OUTPUT_INVALID"


class EnrichmentNoEvidenceError(UlsError):
    """All non-empty provider candidates failed grounding."""

    code = "ENRICHMENT_NO_EVIDENCE"


class EnrichmentPublishConflict(UlsError):
    """The source changed between generation and the publish boundary."""

    code = "ENRICHMENT_FINGERPRINT_CHANGED"


class EnrichmentTerminalizationError(UlsError):
    """A durable enrichment job could not be proven terminal."""

    code = "ENRICHMENT_TERMINALIZATION_FAILED"


class EnrichmentSafetyError(UlsError):
    """A consumer-visible enrichment could not be safely compensated."""

    code = "ENRICHMENT_SAFETY_FAILURE"


@dataclass(frozen=True)
class DerivativeContext:
    entity_id: str
    body: str
    front_matter: Mapping[str, Any]
    marks: tuple[TimestampMark, ...]
    chunks: tuple[DerivativeChunk, ...]
    fingerprint: SourceFingerprint
    kind: Literal["session", "material"]


@dataclass(frozen=True)
class ResolvedEvidence:
    evidence: EvidenceLocator
    source_slice: str
    chunks: tuple[DerivativeChunk, ...]


@dataclass(frozen=True)
class _Candidate:
    kind: str
    raw: Any
    ordinal: int
    kind_ordinal: int


@dataclass(frozen=True)
class CandidateCollection:
    candidates: tuple[_Candidate, ...]
    present_kinds: frozenset[str]
    legitimate_empty_kinds: frozenset[str]
    seen_by_kind: Mapping[str, int]


def coerce_fingerprint(value: Any) -> SourceFingerprint:
    if isinstance(value, SourceFingerprint):
        return value
    if isinstance(value, Mapping):
        version = value.get("source_version", value.get("version"))
        source_hash = value.get("source_hash", value.get("hash"))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 2:
        version, source_hash = value
    else:
        version = getattr(value, "source_version", getattr(value, "version", None))
        source_hash = getattr(value, "source_hash", getattr(value, "hash", None))
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version < 1
        or not isinstance(source_hash, str)
        or not source_hash.strip()
    ):
        raise TypeError("source fingerprint must contain a positive version and non-empty hash")
    return SourceFingerprint(version, source_hash.strip())


def coerce_source_ref(value: Any) -> SourceRef | None:
    """Coerce a source reference while preserving its provider/file identity."""

    if isinstance(value, SourceRef):
        provider = value.provider
        file_id = value.file_id
        web_url = value.web_url
    elif isinstance(value, Mapping):
        provider = value.get("provider")
        file_id = value.get("file_id", value.get("id"))
        web_url = value.get("web_url")
    elif value is not None and hasattr(value, "provider") and hasattr(value, "file_id"):
        provider = getattr(value, "provider")
        file_id = getattr(value, "file_id")
        web_url = getattr(value, "web_url", None)
    elif isinstance(value, str) and value.strip():
        provider = "google_drive"
        file_id = value
        web_url = None
    else:
        return None
    if not isinstance(provider, str) or not provider.strip():
        return None
    if not isinstance(file_id, str) or not file_id.strip():
        return None
    return SourceRef(
        provider.strip(),
        file_id.strip(),
        web_url if isinstance(web_url, str) else None,
    )


def prepare_derivative(
    derivative: Any,
    *,
    expected_entity_id: str | None,
    current_fingerprint: SourceFingerprint,
    kind: Literal["session", "material"],
    expected_source_ref: SourceRef | Mapping[str, Any] | str | None = None,
    max_chunks: int | None = None,
) -> DerivativeContext:
    """Parse and fail-closed validate a normalized derivative before LLM use."""

    if derivative is None:
        raise SourceUnavailableError("normalized derivative is unavailable")
    try:
        derived_entity, body, marks, front = derivative_parts(derivative)
    except Exception as exc:
        raise SourceUnavailableError("normalized derivative could not be parsed") from exc
    if not isinstance(front, Mapping) or not front:
        raise EnrichmentInputError("normalized derivative has no authoritative front matter")
    front = dict(front)
    required = (
        "schema",
        "entity_id",
        "course_key",
        "source_ref",
        "source_hash",
        "source_version",
        "processor_version",
        "normalized_at",
        "status",
    )
    missing = [name for name in required if name not in front]
    if missing:
        raise EnrichmentInputError(
            "normalized derivative front matter is incomplete: " + ", ".join(missing)
        )

    entity_id = front["entity_id"]
    if (
        not isinstance(entity_id, str)
        or not entity_id.strip()
        or entity_id != entity_id.strip()
    ):
        raise EnrichmentInputError("normalized derivative has no valid entity_id")
    if derived_entity is not None and derived_entity != entity_id:
        raise EnrichmentInputError("normalized derivative entity_id is inconsistent")
    if expected_entity_id is not None and entity_id != expected_entity_id:
        raise EnrichmentInputError(
            f"normalized derivative entity_id does not match {expected_entity_id!r}"
        )
    expected_schema = "uls.transcript.v1" if kind == "session" else "uls.material.v1"
    if front.get("schema") != expected_schema:
        raise EnrichmentInputError(f"normalized derivative schema must be {expected_schema}")

    course_key = front["course_key"]
    if not isinstance(course_key, str) or not course_key.strip():
        raise EnrichmentInputError("normalized derivative has an invalid course_key")

    front_source_ref = coerce_source_ref(front["source_ref"])
    if front_source_ref is None:
        raise EnrichmentInputError("normalized derivative has an invalid source_ref")
    if expected_source_ref is not None:
        expected_ref = coerce_source_ref(expected_source_ref)
        if expected_ref is None or front_source_ref.identity != expected_ref.identity:
            raise EnrichmentInputError(
                "normalized derivative source_ref does not match the source"
            )

    derivative_version = front["source_version"]
    derivative_hash = front["source_hash"]
    if (
        isinstance(derivative_version, bool)
        or not isinstance(derivative_version, int)
        or derivative_version < 1
        or not isinstance(derivative_hash, str)
        or not derivative_hash.strip()
    ):
        raise EnrichmentInputError("normalized derivative has an invalid source fingerprint")
    derivative_fingerprint = SourceFingerprint(derivative_version, derivative_hash.strip())
    if derivative_fingerprint != current_fingerprint:
        raise EnrichmentInputError(
            "normalized derivative fingerprint does not match the current source",
            details={"derivative": derivative_fingerprint, "current": current_fingerprint},
        )

    processor_version = front["processor_version"]
    if not isinstance(processor_version, str) or not processor_version.strip():
        raise EnrichmentInputError("normalized derivative has an invalid processor_version")
    normalized_at = front["normalized_at"]
    if not isinstance(normalized_at, str) or not normalized_at.strip():
        raise EnrichmentInputError("normalized derivative has an invalid normalized_at")

    status = front["status"]
    status_value = status.value if hasattr(status, "value") else status
    if not isinstance(status_value, str) or status_value != "ready":
        raise EnrichmentInputError(
            "non-ready derivative cannot produce ordinary READY enrichment",
            details={"status": status_value},
        )
    if not isinstance(body, str):
        raise EnrichmentInputError("normalized derivative body must be text")

    if kind == "session":
        # Do not trust a provider/model-supplied sidecar blindly.  Re-derive
        # timestamp marks from the current normalized body so a forged offset
        # cannot manufacture a resolvable evidence slice.
        from uls.normalization.transcript import extract_timestamp_marks

        verified_marks, extraction_failed = extract_timestamp_marks(body)
        if extraction_failed:
            raise EnrichmentInputError("transcript timestamp extraction is partial")
        marks = verified_marks
        chunks = timestamp_chunks(
            {"body": body, "front_matter": front, "marks": verified_marks},
            entity_id=entity_id,
            max_chunks=max_chunks,
        )
    else:
        page_count = _page_count(front)
        chunks = page_chunks(
            derivative,
            entity_id=entity_id,
            end_page=page_count,
            max_chunks=max_chunks,
        )
    return DerivativeContext(
        entity_id=entity_id,
        body=body,
        front_matter=front,
        marks=tuple(marks),
        chunks=tuple(chunks),
        fingerprint=derivative_fingerprint,
        kind=kind,
    )


def resolve_evidence(locator: EvidenceLocator, context: DerivativeContext) -> ResolvedEvidence | None:
    """Resolve one typed locator into a contiguous current derivative slice."""

    value = locator.locator
    if value.entity_id != context.entity_id:
        return None
    if context.kind == "session" and not isinstance(value, TimeLocator):
        return None
    if context.kind == "material" and not isinstance(value, PageLocator):
        return None

    selected: list[DerivativeChunk] = []
    requested_start, requested_end = _locator_range(value)
    for chunk in context.chunks:
        chunk_value = chunk.locator
        if type(chunk_value) is not type(value):
            continue
        if chunk_value.entity_id != value.entity_id or chunk_value.subtype != value.subtype:
            continue
        chunk_start, chunk_end = _locator_range(chunk_value)
        if chunk_end < requested_start or chunk_start > requested_end:
            continue
        selected.append(chunk)
    if not selected or not _covers_range(selected, requested_start, requested_end):
        return None

    selected.sort(key=lambda item: (item.start_offset, item.end_offset))
    start = min(item.start_offset for item in selected)
    end = max(item.end_offset for item in selected)
    if start < 0 or end < start or end > len(context.body):
        return None
    return ResolvedEvidence(locator, context.body[start:end], tuple(selected))


def _locator_range(locator: Locator) -> tuple[int, int]:
    if isinstance(locator, PageLocator):
        return locator.start_page, locator.end_page
    return locator.start_seconds, locator.end_seconds


def _page_count(front_matter: Mapping[str, Any]) -> int | None:
    value = front_matter.get("page_count", front_matter.get("Page Count"))
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EnrichmentInputError("normalized material has an invalid page_count")
    return value


def _covers_range(chunks: Iterable[DerivativeChunk], start: int, end: int) -> bool:
    intervals = sorted((_locator_range(chunk.locator) for chunk in chunks), key=lambda item: item[0])
    cursor = start
    for interval_start, interval_end in intervals:
        if interval_end < cursor:
            continue
        if interval_start > cursor:
            return False
        cursor = max(cursor, interval_end + 1)
        if cursor > end:
            return True
    return False


def coerce_llm_result(value: Any) -> LLMEnrichmentResult:
    if isinstance(value, LLMEnrichmentResult):
        return value
    if isinstance(value, Mapping):
        return LLMEnrichmentResult.from_mapping(value)
    if value is not None and hasattr(value, "output"):
        return LLMEnrichmentResult(
            output=getattr(value, "output"),
            evidence=getattr(value, "evidence", ()),
            confidence=getattr(value, "confidence", 0.0),
            classification=getattr(value, "classification", "proposal"),
            provider_provenance=getattr(value, "provider_provenance", {}),
            based_on=getattr(value, "based_on", None),
            explicitness=getattr(value, "explicitness", None),
        )
    raise EnrichmentOutputError("LLM adapter did not return a structured enrichment result")


def call_enrichment_adapter(
    adapter: LLMAdapter,
    operation: Literal["session", "material"],
    derivative: Any,
    chunks: Sequence[DerivativeChunk],
) -> LLMEnrichmentResult:
    """Call only the Phase 3 pure adapter operation.

    The small signature fallback keeps simple fakes that omit the optional
    keyword compatible without adding a second adapter capability.
    """

    name = "enrich_session" if operation == "session" else "enrich_material"
    method = getattr(adapter, name, None)
    if not callable(method):
        raise EnrichmentOutputError(f"LLM adapter has no {name} operation")
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        signature = None
    if signature is not None and "chunks" not in signature.parameters:
        return coerce_llm_result(method(derivative))
    return coerce_llm_result(method(derivative, chunks=tuple(chunks)))


def collect_candidates(output: Any, required_kinds: Sequence[str]) -> CandidateCollection:
    """Normalize common structured-output shapes without trusting controls."""

    canonical = {normalize_kind(kind): kind for kind in required_kinds}
    candidates: list[_Candidate] = []
    present: set[str] = set()
    seen: dict[str, int] = {kind: 0 for kind in required_kinds}
    kind_counts: dict[str, int] = {kind: 0 for kind in required_kinds}

    def add(kind: str, value: Any) -> None:
        actual = canonical.get(normalize_kind(kind))
        if actual is None:
            return
        present.add(actual)
        values = _candidate_values(value)
        if not values:
            return
        for item in values:
            kind_counts[actual] += 1
            seen[actual] += 1
            candidates.append(_Candidate(actual, item, len(candidates), kind_counts[actual] - 1))

    if isinstance(output, Mapping):
        # The explicit kind buckets are the normative shape.
        found_bucket = False
        for key, value in output.items():
            if not isinstance(key, str):
                continue
            actual = canonical.get(normalize_kind(key))
            if actual is not None:
                found_bucket = True
                add(actual, value)
        # A flat {kind, content, ...} item or an {items: [...]} envelope is
        # also accepted for provider-neutral test fakes.
        if not found_bucket:
            if _mapping_kind(output) is not None:
                add(str(_mapping_kind(output)), output)
            else:
                envelope = output.get("items", output.get("signals"))
                if isinstance(envelope, Sequence) and not isinstance(envelope, (str, bytes)):
                    for item in envelope:
                        if isinstance(item, Mapping):
                            kind = _mapping_kind(item)
                            if kind is not None:
                                add(str(kind), item)
    elif isinstance(output, Sequence) and not isinstance(output, (str, bytes)):
        for item in output:
            if isinstance(item, Mapping):
                kind = _mapping_kind(item)
                if kind is not None:
                    add(str(kind), item)

    # Empty/omitted buckets are deliberately not marked as legitimate here.
    # That completion state is system-derived only; provider output cannot
    # certify that it had no work to do.
    return CandidateCollection(tuple(candidates), frozenset(present), frozenset(), seen)


def normalize_kind(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _mapping_kind(value: Mapping[str, Any]) -> str | None:
    for key in ("kind", "type", "category", "signal_kind"):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return None


def _candidate_values(value: Any) -> list[Any]:
    if isinstance(value, Mapping):
        if any(key in value for key in ("content", "text", "summary", "topic", "heading", "term", "evidence", "locator")):
            return [value]
        for key in ("items", "signals", "values", "candidates"):
            nested = value.get(key)
            if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
                return list(nested)
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    if value is None:
        return []
    return [value]


def evidence_for_candidate(
    candidate: _Candidate,
    external: Any,
) -> list[Any]:
    raw = candidate.raw
    if isinstance(raw, Mapping):
        for key in ("evidence", "evidence_locators", "locators"):
            if key in raw:
                return _as_evidence_list(raw[key])
        if "locator" in raw or "location" in raw or "source_locator" in raw:
            quote = raw.get("quote", raw.get("verbatim_quote"))
            locator_value = raw.get("locator", raw.get("location", raw.get("source_locator")))
            return [{"locator": locator_value, "quote": quote}]
    if external is None:
        return []
    if isinstance(external, Mapping):
        # A kind bucket takes precedence over an index/global bucket.  Once a
        # bucket is present, a missing candidate slot is a hard miss rather
        # than permission to reuse a sibling's evidence.
        for key, value in external.items():
            if isinstance(key, str) and normalize_kind(key) == normalize_kind(candidate.kind):
                return _evidence_slot(value, candidate.kind_ordinal)
        for key in (candidate.ordinal, str(candidate.ordinal)):
            if key in external:
                return _as_evidence_list(external[key])
        if any(key in external for key in ("locator", "location", "source_locator")):
            return [external] if candidate.ordinal == 0 else []
        return []
    if isinstance(external, Sequence) and not isinstance(external, (str, bytes)):
        values = list(external)
        with_kind = [
            value
            for value in values
            if isinstance(value, Mapping)
            and isinstance(value.get("kind"), str)
            and normalize_kind(value["kind"]) == normalize_kind(candidate.kind)
        ]
        if with_kind:
            return _evidence_slot(with_kind, candidate.kind_ordinal)
        # A tagged list is a set of kind-addressed slots.  Do not reinterpret
        # another kind's entry as this candidate's global evidence.
        if any(
            isinstance(value, Mapping) and isinstance(value.get("kind"), str)
            for value in values
        ):
            return []
        if len(values) > candidate.ordinal:
            return _as_evidence_list(values[candidate.ordinal])
    return [external] if candidate.ordinal == 0 else []


def _evidence_slot(value: Any, index: int) -> list[Any]:
    """Return exactly one candidate-addressed evidence slot, if present."""

    if isinstance(value, Mapping) and not any(
        key in value for key in ("locator", "location", "source_locator")
    ):
        for key in (index, str(index)):
            if key in value:
                return _as_evidence_list(value[key])
        nested = value.get("evidence", value.get("locators"))
        if nested is not None:
            return _evidence_slot(nested, index)
        return []
    values = _as_evidence_list(value)
    if index >= len(values):
        return []
    return _as_evidence_list(values[index])


def _as_evidence_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        if any(key in value for key in ("locator", "location", "source_locator")):
            return [value]
        nested = value.get("evidence", value.get("locators"))
        if nested is not None:
            return _as_evidence_list(nested)
        return []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return [value]


def signal_from_candidate(
    candidate: _Candidate,
    *,
    external_evidence: Any,
    context: DerivativeContext,
    default_confidence: Any,
    kind: str,
) -> tuple[EnrichmentSignal | None, str | None]:
    raw = candidate.raw
    if isinstance(raw, Mapping):
        candidate_mapping = raw
    else:
        candidate_mapping = {"content": raw}

    content = _first_value(
        candidate_mapping,
        "content",
        "text",
        "summary",
        "description",
        "value",
        "title",
        "topic" if kind == "topics" else "heading" if kind == "content_index" else "",
    )
    if content is None:
        return None, "INVALID_CONTENT"
    if not isinstance(content, str) or not content.strip():
        return None, "INVALID_CONTENT"
    hint = _first_value(
        candidate_mapping,
        "symbolic_hint",
        "topic",
        "heading",
        "term",
        "keyword",
        "section",
        "title",
    )
    if hint is None and kind in {"topics", "content_index"}:
        hint = content
    if not isinstance(hint, str) or not hint.strip():
        return None, "MISSING_SYMBOLIC_HINT"

    confidence = _first_value(candidate_mapping, "confidence", "score")
    if confidence is None:
        confidence = _confidence_for(default_confidence, candidate)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return None, "INVALID_CONFIDENCE"
    confidence_float = float(confidence)
    if not math.isfinite(confidence_float) or not 0 <= confidence_float <= 1:
        return None, "INVALID_CONFIDENCE"

    raw_evidence = evidence_for_candidate(candidate, external_evidence)
    if not raw_evidence:
        return None, "MISSING_EVIDENCE"
    resolved: list[EvidenceLocator] = []
    explicit = False
    for value in raw_evidence:
        try:
            evidence = value if isinstance(value, EvidenceLocator) else EvidenceLocator.from_mapping(value)
        except (TypeError, ValueError, KeyError):
            continue
        located = resolve_evidence(evidence, context)
        if located is None:
            continue
        quote = evidence.quote
        # "Attributable" is deliberately a deterministic, conservative
        # producer rule: a meaningful verbatim quote must be inside this
        # exact locator slice and substantially cover the claim.  Deterministic
        # code cannot prove full semantic entailment or contradiction.  It does
        # reject recognized negation-polarity mismatches; this remains a
        # precision-biased, fail-closed approximation that can only downgrade
        # to INFERRED.  An EXPLICIT signal still never fabricates evidence:
        # its quote is always a real verbatim span at the cited locator.
        valid_quote = (
            quote
            if is_meaningful_quote(quote)
            and quote in located.source_slice
            and _quote_attributable(content, quote)
            else None
        )
        resolved.append(EvidenceLocator(evidence.locator, valid_quote))
        if valid_quote is not None:
            explicit = True
    if not resolved:
        return None, "UNRESOLVABLE_LOCATOR"

    if kind in {"summary", "likely_confusions"}:
        # These categories are always interpretive, even when a valid quote
        # exists; their explicitness contract is intentionally fixed.
        explicitness = Explicitness.INFERRED
    else:
        explicitness = Explicitness.EXPLICIT if explicit else Explicitness.INFERRED
    try:
        return (
            EnrichmentSignal(
                kind=kind,
                content=content,
                explicitness=explicitness,
                evidence=tuple(resolved),
                confidence=confidence_float,
                symbolic_hint=hint,
            ),
            None,
        )
    except (TypeError, ValueError):
        return None, "INVALID_SIGNAL"


def _quote_attributable(content: str, quote: str) -> bool:
    """Require conservative content coverage before labeling a quote explicit.

    At least two meaningful claim tokens must be shared, and the quote must
    cover at least 80% of the claim's distinct meaningful tokens.  This keeps
    a one-word coincidence, including a contradiction that shares a topic
    word, in the safer INFERRED state.  Recognized Korean and English
    negation markers must also have matching polarity, so an inserted or
    removed negation is rejected.  Attached Korean ``안``/``못``
    short-negation is handled only as a paired form when its unprefixed
    predicate token appears on the other side.  The rule intentionally
    favors precision over recall: it is downgrade-only and cannot establish
    full semantic entailment or contradiction beyond this deterministic
    negation guard.
    """

    content_text = content.strip()
    if ":" in content_text:
        prefix, suffix = content_text.split(":", 1)
        # Provider fixtures and common structured outputs often prefix the
        # claim with its bucket name.  Do not make that label part of the
        # substantive coverage denominator.
        if normalize_kind(prefix.strip()) in _ATTRIBUTION_BUCKETS:
            content_text = suffix.strip()

    content_words = _meaningful_tokens(content_text)
    quote_words = _meaningful_tokens(quote)
    shared = content_words.intersection(quote_words)
    if len(shared) < 2 or not content_words:
        return False
    if len(shared) / len(content_words) < 0.8:
        return False

    content_polarity = _negation_polarity(content_text, paired_value=quote)
    quote_polarity = _negation_polarity(quote, paired_value=content_text)
    # Unknown polarity is fail-closed.  A recognized marker present in only
    # one side is a deterministic contradiction for attribution purposes.
    if (
        content_polarity is None
        or quote_polarity is None
        or content_polarity != quote_polarity
    ):
        return False
    return True


_ATTRIBUTION_BUCKETS = frozenset(
    normalize_kind(value)
    for value in SESSION_ENRICHMENT_KINDS + MATERIAL_ENRICHMENT_KINDS
)


def _meaningful_tokens(value: str) -> set[str]:
    """Return conservative, case-folded content tokens for attribution."""

    result: set[str] = set()
    for token in re.findall(r"\w+", value, flags=re.UNICODE):
        # One-character Hangul/CJK tokens can carry meaning; one-character
        # Latin tokens are usually articles or accidental lexical noise.
        if len(token) > 1 or any("\u2e80" <= character <= "\u9fff" or "\uac00" <= character <= "\ud7af" for character in token):
            result.add(token.casefold())
    return result


_NEGATION_WORDS = frozenset({"안", "못", "않", "not", "no", "never"})
_KOREAN_SHORT_NEGATION_PREFIXES = ("안", "못")
_ENGLISH_NEGATION_CONTRACTION = re.compile(
    r"(?:^|[^\w])\w+n['’]t(?:$|[^\w])",
    flags=re.IGNORECASE,
)


def _negation_polarity(
    value: str,
    *,
    paired_value: str | None = None,
) -> bool | None:
    """Return clear negation-marker presence, or ``None`` when unclassifiable.

    Korean ``않`` is recognized both as a common ending (``않는다``,
    ``않다``, ``않았다``) and in an unspaced ``-지않-`` form.  English
    contractions are checked on the raw text because tokenization separates
    the apostrophe from ``n't``.  Attached Korean ``안``/``못`` short-negation
    is recognized only in a paired comparison when removing the prefix gives
    an exact predicate-shaped token on the paired side.  This is marker
    detection only; it does not attempt general semantic scope or entailment
    analysis.  Korean morphology beyond this deterministic paired check
    remains a documented limitation.
    """

    text = value.strip()
    if not text:
        return None

    tokens = [token.casefold() for token in re.findall(r"\w+", text, flags=re.UNICODE)]
    if not tokens:
        return None
    if any(token in _NEGATION_WORDS for token in tokens):
        return True
    if any(token.startswith("않") or "지않" in token for token in tokens):
        return True
    if paired_value is not None:
        paired_tokens = {
            token.casefold()
            for token in re.findall(r"\w+", paired_value, flags=re.UNICODE)
        }
        if _has_paired_attached_short_negation(tokens, paired_tokens):
            return True
    if _ENGLISH_NEGATION_CONTRACTION.search(text) is not None:
        return True
    return False


def _has_paired_attached_short_negation(
    tokens: Sequence[str],
    paired_tokens: set[str],
) -> bool:
    """Recognize only an attached 안/못 prefix paired with a positive predicate.

    Detection is conjugation-agnostic: an attached short-negation is only
    recognized when stripping the 안/못 prefix leaves a predicate (len>=2)
    that appears, un-negated, on the other side.  That paired-membership
    condition — not a fixed verb ending — is what makes this precise: it
    catches every conjugation (``안나온다``/``안해요``/``못나와요``) while the
    len>=2 guard keeps 안-morpheme nouns (안전/안내/안녕, whose remainder is a
    single syllable) from being mistaken for negation.
    """

    for token in tokens:
        for prefix in _KOREAN_SHORT_NEGATION_PREFIXES:
            if not token.startswith(prefix):
                continue
            predicate = token[len(prefix) :]
            if len(predicate) >= 2 and predicate in paired_tokens:
                return True
    return False


def _first_value(value: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name and name in value:
            return value[name]
    normalized = {normalize_kind(name): item for name, item in value.items() if isinstance(name, str)}
    for name in names:
        if name and normalize_kind(name) in normalized:
            return normalized[normalize_kind(name)]
    return None


def _confidence_for(value: Any, candidate: _Candidate) -> Any:
    if isinstance(value, Mapping):
        for key in (candidate.kind, candidate.kind_ordinal, str(candidate.kind_ordinal), candidate.ordinal, str(candidate.ordinal)):
            if key in value:
                return value[key]
        return 0.0
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) > candidate.ordinal:
            return value[candidate.ordinal]
        if len(value) > candidate.kind_ordinal:
            return value[candidate.kind_ordinal]
        return 0.0
    return value


def build_generation_result(
    *,
    context: DerivativeContext,
    llm_result: LLMEnrichmentResult,
    required_kinds: Sequence[str],
    payload_kind: Literal["session", "material"],
    processor_version: str,
) -> EnrichmentGenerationResult:
    collection = collect_candidates(llm_result.output, required_kinds)
    buckets: dict[str, list[EnrichmentSignal]] = {kind: [] for kind in required_kinds}
    drops: list[str] = []
    for candidate in collection.candidates:
        signal, reason = signal_from_candidate(
            candidate,
            external_evidence=llm_result.evidence,
            context=context,
            default_confidence=llm_result.confidence,
            kind=candidate.kind,
        )
        if signal is None:
            drops.append(f"{candidate.kind}:{reason or 'DROPPED'}")
            continue
        buckets[candidate.kind].append(signal)

    completeness: dict[str, str] = {}
    for kind in required_kinds:
        if buckets[kind]:
            completeness[kind] = CompletionState.PRODUCED.value
        elif collection.seen_by_kind.get(kind, 0) > 0:
            completeness[kind] = CompletionState.OMITTED_OR_FAILED.value
        elif kind in collection.legitimate_empty_kinds:
            completeness[kind] = CompletionState.LEGITIMATELY_EMPTY.value
        else:
            completeness[kind] = CompletionState.OMITTED_OR_FAILED.value

    if payload_kind == "session":
        payload = SessionEnrichmentPayload(**{kind: tuple(buckets[kind]) for kind in required_kinds})
    else:
        payload = MaterialEnrichmentPayload(**{kind: tuple(buckets[kind]) for kind in required_kinds})
    provenance = sanitize_provider_provenance(llm_result.provider_provenance)
    record = EnrichmentRecord(
        payload=payload,
        based_on_source_version=context.fingerprint.source_version,
        based_on_source_hash=context.fingerprint.source_hash,
        processor_version=processor_version,
        provider_provenance=provenance,
    )
    has_omission = any(
        state == CompletionState.OMITTED_OR_FAILED.value for state in completeness.values()
    )
    produced_count = sum(len(value) for value in buckets.values())
    if produced_count == 0:
        if "ENRICHMENT_NO_EVIDENCE" not in drops:
            drops.append("ENRICHMENT_NO_EVIDENCE")
        status = ProcessingStatus.NEEDS_REVIEW
    elif has_omission:
        status = ProcessingStatus.NEEDS_REVIEW
    else:
        status = ProcessingStatus.READY
    return EnrichmentGenerationResult(
        payload=payload,
        record=record,
        input_fingerprint=context.fingerprint,
        processor_version=processor_version,
        completeness=completeness,
        produced_count=produced_count,
        dropped_count=len(collection.candidates) - produced_count,
        drop_reasons=tuple(drops),
        status=status,
        classification=_classification(llm_result.classification),
        provider_provenance=provenance,
    )


def _classification(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        normalized = value.strip().casefold()
        if normalized in {"proposal", "fact"}:
            return normalized
    return "proposal"


def sanitize_provider_provenance(value: Any) -> dict[str, Any]:
    """Keep model/provider attribution while dropping control metadata."""

    if not isinstance(value, Mapping):
        return {}
    forbidden = {
        "basedon",
        "sourceclass",
        "ownership",
        "freshness",
        "factual",
        "verified",
        "scopeconfirmed",
        "decision",
        "state",
    }
    def clean(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {
                key: clean(child)
                for key, child in item.items()
                if isinstance(key, str) and normalize_kind(key) not in forbidden
            }
        if isinstance(item, (list, tuple)):
            return [clean(child) for child in item]
        if isinstance(item, (str, int, float, bool)) or item is None:
            return item
        return None

    return clean(value)


def make_adapter_call(
    adapter: LLMAdapter,
    operation: Literal["session", "material"],
    derivative: Any,
    chunks: Sequence[DerivativeChunk],
) -> LLMEnrichmentResult:
    return call_enrichment_adapter(adapter, operation, derivative, chunks)


__all__ = [
    "CandidateCollection",
    "DerivativeContext",
    "EnrichmentInputError",
    "EnrichmentNoEvidenceError",
    "EnrichmentOutputError",
    "EnrichmentPublishConflict",
    "EnrichmentSafetyError",
    "EnrichmentTerminalizationError",
    "ResolvedEvidence",
    "build_generation_result",
    "call_enrichment_adapter",
    "coerce_fingerprint",
    "coerce_source_ref",
    "collect_candidates",
    "make_adapter_call",
    "prepare_derivative",
    "resolve_evidence",
    "sanitize_provider_provenance",
]
