"""Deterministic Session entity resolution with stable multi-turn handles."""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Mapping
from typing import Any

from uls.adapters.notion.base import NotionReader, normalize_alias
from uls.domain.errors import EntityNotFoundError, SourceUnavailableError
from uls.domain.ids import parse_course_key
from uls.ephemeral.models import ResolutionCandidate, ResolvedEntity

from ._compat import (
    exact_alias_match,
    field,
    normal_key,
    record_aliases,
    record_id,
    record_label,
    relation_id,
    text,
)
from .schemas import ResolutionResult


_INTENT_KEYWORDS = frozenset(
    {
        "정리",
        "정리해줘",
        "설명",
        "설명해줘",
        "요약",
        "요약해줘",
        "알려줘",
        "알려",
        "summarize",
        "summary",
        "explain",
        "explanation",
        "please",
    }
)
_SESSION_NO_PATTERNS = (
    re.compile(r"(?<!\S)(?P<number>[0-9]+)\s*강(?![0-9A-Za-z가-힣_])"),
    re.compile(r"(?<!\S)(?P<number>[0-9]+)\s*번째\s*강의(?![0-9A-Za-z가-힣_])"),
    re.compile(r"(?<!\S)(?P<number>[0-9]+)\s*회차(?![0-9A-Za-z가-힣_])"),
)
_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")


def normalize_query(query: str) -> str:
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    return re.sub(r"\s+", " ", query.strip()).casefold()


def query_tokens(query: str) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(normalize_query(query)))


def strip_intent_keywords(query: str) -> str:
    """Remove only whitelisted whole-word intent tokens from the edges."""

    normalized = normalize_query(query)
    pieces = normalized.split(" ") if normalized else []
    while pieces and pieces[0] in _INTENT_KEYWORDS:
        pieces.pop(0)
    while pieces and pieces[-1] in _INTENT_KEYWORDS:
        pieces.pop()
    return " ".join(pieces)


def extract_session_number(query: str) -> int | None:
    normalized = normalize_query(query)
    for pattern in _SESSION_NO_PATTERNS:
        match = pattern.search(normalized)
        if match:
            return int(match.group("number"))
    return None


class SessionResolver:
    """Resolver implementation kept independent of MCP and write adapters."""

    def __init__(
        self,
        notion_reader: NotionReader,
        ephemeral: Any,
        *,
        resolution_ttl_seconds: int = 900,
        max_candidate_entities: int = 20,
    ) -> None:
        self.notion_reader = notion_reader
        self.ephemeral = ephemeral
        self.resolution_ttl_seconds = resolution_ttl_seconds
        self.max_candidate_entities = max_candidate_entities

    def resolve_entity(
        self,
        query: str,
        course_hint: Any | None = None,
        entity_type: str = "session",
    ) -> ResolutionResult:
        if entity_type.casefold() != "session":
            raise EntityNotFoundError(
                "Phase 2 resolver supports Session entities only",
                details={"entity_type": entity_type},
            )
        original = normalize_query(query)
        if not original:
            raise EntityNotFoundError("empty entity query")

        # Exact IDs have highest precedence and are never sent through fuzzy
        # matching.  A casefold comparison keeps user input friendly while
        # the returned entity ID remains canonical.
        exact_id = self._exact_id_query(original)
        if exact_id is not None:
            record = self._call_get_session(exact_id)
            if record is not None and self._is_session_record(record):
                return self._resolved(record, reason="exact entity ID")

        course, query_without_course = self._resolve_course_context(original, course_hint)
        base_query = strip_intent_keywords(query_without_course)
        if not base_query:
            base_query = strip_intent_keywords(original)
        session_number = extract_session_number(base_query)

        records = self._candidate_records(course, base_query)
        # Explicit session numbers are parsed only by the normative patterns;
        # this is what prevents 15강 and 제5강의실 from becoming 5강.
        # This is a complete precedence tier: a unique Course+No match is
        # resolved immediately and is never unioned with a later alias tier.
        if session_number is not None and course is not None:
            number_candidates = [
                (record, f"explicit Session No: {session_number}")
                for record in records
                if self._session_number(record) == session_number
                and self._course_matches(record, course)
            ]
            number_candidates = self._dedupe(number_candidates)
            if number_candidates:
                return self._result_for_candidates(number_candidates)

        # Exact alias matching is token/whole-alias based.  Even if a provider
        # implementation returns a broad result for find_sessions_by_alias,
        # filter it again locally before it can affect resolution.
        alias_forms = self._alias_forms(base_query, original)
        alias_candidates: list[tuple[Any, str]] = []
        for alias in alias_forms:
            found = self._call_find_alias(course, alias)
            for record in found:
                if (
                    self._is_session_record(record)
                    and self._course_matches(record, course)
                    and exact_alias_match(record, alias)
                ):
                    alias_candidates.append((record, f"exact alias: {alias}"))

        # A provider may not implement alias lookup over all records, so also
        # compare aliases from the bounded candidate list directly.
        for record in records:
            if not self._is_session_record(record) or not self._course_matches(record, course):
                continue
            for alias in alias_forms:
                if exact_alias_match(record, alias):
                    alias_candidates.append((record, f"exact alias: {alias}"))
                    break

        alias_candidates = self._dedupe(alias_candidates)
        if alias_candidates:
            return self._result_for_candidates(alias_candidates)

        # Deterministic metadata is a separate tier from fuzzy matching.  A
        # whole normalized Session name is safe to use as metadata; it is not
        # merged with fuzzy candidates when it uniquely identifies a record.
        metadata_candidates = self._metadata_candidates(records, base_query, course)
        if metadata_candidates:
            return self._result_for_candidates(metadata_candidates)

        candidates = self._fuzzy_candidates(records, base_query, course)
        if not candidates:
            raise EntityNotFoundError(
                f"No Session matched query {query!r}",
                details={"query": query, "course_hint": course_hint},
            )
        return self._result_for_candidates(candidates)

    def _result_for_candidates(self, candidates: Iterable[tuple[Any, str]]) -> ResolutionResult:
        candidates = self._dedupe(candidates)[: self.max_candidate_entities]
        if not candidates:
            raise EntityNotFoundError(
                "No usable Session candidates were returned",
            )
        if len(candidates) == 1:
            return self._resolved(candidates[0][0], reason=candidates[0][1])

        handles = [
            ResolutionCandidate(
                candidate_id="",
                entity_type="session",
                entity_id=record_id(record) or "",
                label=record_label(record),
                reason=reason,
            )
            for record, reason in candidates
        ]
        handle = self.ephemeral.create_resolution(handles, self.resolution_ttl_seconds)
        return ResolutionResult(
            status="ambiguous",
            resolution_id=handle.resolution_id,
            expires_at=handle.expires_at,
            candidates=handle.candidates,
        )

    def _metadata_candidates(
        self,
        records: Iterable[Any],
        query: str,
        course: Any | None,
    ) -> list[tuple[Any, str]]:
        wanted = normalize_query(query)
        if not wanted:
            return []
        candidates: list[tuple[Any, str]] = []
        for record in records:
            if not self._is_session_record(record) or not self._course_matches(record, course):
                continue
            if normalize_query(record_label(record)) == wanted:
                candidates.append((record, "deterministic metadata: exact name"))
        return self._dedupe(candidates)

    def select_resolution(self, resolution_id: str, candidate_id: str) -> ResolvedEntity:
        return self.ephemeral.consume_resolution_choice(resolution_id, candidate_id)

    def _resolved(self, record: Any, *, reason: str | None = None) -> ResolutionResult:
        entity_id = record_id(record)
        if not entity_id:
            raise EntityNotFoundError("Session record has no canonical entity ID")
        entity = ResolvedEntity(
            entity_type="session",
            entity_id=entity_id,
            label=record_label(record) or entity_id,
            reason=reason,
        )
        return ResolutionResult(status="resolved", entity=entity)

    @staticmethod
    def _is_session_record(record: Any) -> bool:
        entity_id = record_id(record)
        return bool(entity_id and re.fullmatch(r"[A-Z][A-Z0-9]*-S[0-9]{2}", entity_id))

    @staticmethod
    def _session_number(record: Any) -> int | None:
        value = field(record, "Session No", "session_no", "session_number", default=None)
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        entity_id = record_id(record)
        if entity_id and re.search(r"-S([0-9]{2})\Z", entity_id):
            return int(re.search(r"-S([0-9]{2})\Z", entity_id).group(1))  # type: ignore[union-attr]
        return None

    @staticmethod
    def _course_value(record: Any) -> Any:
        return field(record, "Course", "course", "Course Key", "course_key", default=None)

    def _course_matches(self, record: Any, course: Any | None) -> bool:
        if course is None:
            return True
        wanted_id = text(field(course, "ID", "Course Key", "course_key"), default=None)
        wanted_id = wanted_id or record_id(course)
        wanted_code = text(field(course, "Code", "code"), default=None)
        if isinstance(course, str):
            wanted_id = wanted_id or course
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", course.strip()):
                wanted_code = wanted_code or course.strip()
        if wanted_code is None and wanted_id is not None:
            try:
                wanted_code = parse_course_key(wanted_id).code
            except Exception:
                pass
        actual = self._course_value(record)
        if isinstance(actual, list) and actual:
            actual = actual[0]
        actual_text = text(actual, default=None)
        if actual_text and wanted_id and normalize_alias(actual_text) == normalize_alias(wanted_id):
            return True
        # Relation fields often contain only a page ID while a fake may store
        # the full course object; compare stable ID/key values when present.
        actual_id = text(field(actual, "ID", "Course Key", "course_key", "Code", "code"), default=None)
        if actual_id and wanted_id and normalize_alias(actual_id) == normalize_alias(wanted_id):
            return True
        actual_relation_id = relation_id(actual)
        wanted_relation_id = record_id(course)
        if (
            actual_relation_id
            and wanted_relation_id
            and normalize_alias(actual_relation_id) == normalize_alias(wanted_relation_id)
        ):
            return True
        actual_code = text(field(actual, "Code", "code"), default=None)
        actual_key = actual_id or actual_text
        if actual_code and wanted_code and normalize_alias(actual_code) == normalize_alias(wanted_code):
            return True
        if actual_key and wanted_code:
            try:
                parsed = parse_course_key(actual_key)
            except Exception:
                parsed = None
            if parsed is not None and normalize_alias(parsed.code) == normalize_alias(wanted_code):
                return True
        return False

    def _resolve_course_context(self, query: str, hint: Any | None) -> tuple[Any | None, str]:
        if hint is not None:
            course = self._lookup_course(hint)
            return course or hint, query

        tokens = query.split(" ")
        for length in range(len(tokens), 0, -1):
            prefix = " ".join(tokens[:length])
            course = self._lookup_course(prefix)
            if course is not None:
                remainder = " ".join(tokens[length:])
                return course, remainder or query
        return None, query

    def _lookup_course(self, value: Any) -> Any | None:
        if not isinstance(value, str):
            return value
        value = normalize_query(value)
        if not value:
            return None
        getter = getattr(self.notion_reader, "get_course_by_alias", None)
        if getter is not None:
            for candidate in (value, normalize_alias(value)):
                try:
                    result = getter(candidate)
                except _MISSING_PROVIDER_ERRORS:
                    continue
                except Exception as exc:
                    raise SourceUnavailableError("Notion course lookup is unavailable") from exc
                if result is not None and self._course_alias_matches(result, candidate):
                    return result
        # A course key/code can be accepted as an explicit hint even when the
        # fake exposes no alias method.  It is passed through to list_course_sessions.
        upper = value.upper()
        if re.fullmatch(r"[0-9]{4}-[0-9]+_[A-Z][A-Z0-9]*-[A-Z0-9]+", upper):
            return upper
        if re.fullmatch(r"[A-Z][A-Z0-9]*", upper) and any(character.isdigit() for character in upper):
            return upper
        return None

    @staticmethod
    def _course_alias_matches(course: Any, query: str) -> bool:
        """Reject a provider's broad course lookup before it scopes sessions."""

        if exact_alias_match(course, query):
            return True
        wanted = normalize_alias(query)
        for name in ("Course Key", "course_key", "ID", "id", "Code", "code"):
            value = text(field(course, name, default=None), default=None)
            if value is not None and normalize_alias(value) == wanted:
                return True
        return False

    def _candidate_records(self, course: Any | None, query: str) -> list[Any]:
        if course is not None:
            return self._call_list_sessions(course)
        # Alias lookup is the only bounded all-course operation guaranteed by
        # the reader protocol.  Query forms are tried below as well.
        return []

    def _call_list_sessions(self, course: Any) -> list[Any]:
        method = getattr(self.notion_reader, "list_course_sessions", None)
        if method is None:
            return []
        values: list[Any] = []
        arguments = self._course_arguments(course)
        for argument in arguments:
            try:
                result = method(argument)
            except _MISSING_PROVIDER_ERRORS:
                continue
            except Exception as exc:
                raise SourceUnavailableError("Notion Session list lookup is unavailable") from exc
            if result is not None:
                values.extend([result] if isinstance(result, Mapping) else list(result))
                if values:
                    break
        return self._unique_records(values)

    def _call_get_session(self, entity_id: str) -> Any | None:
        method = getattr(self.notion_reader, "get_session", None)
        if method is None:
            return None
        for value in (entity_id, entity_id.upper()):
            try:
                result = method(value)
            except _MISSING_PROVIDER_ERRORS:
                continue
            except Exception as exc:
                raise SourceUnavailableError("Notion Session lookup is unavailable") from exc
            if result is not None:
                return result
        return None

    def _call_find_alias(self, course: Any | None, alias: str) -> list[Any]:
        method = getattr(self.notion_reader, "find_sessions_by_alias", None)
        if method is None:
            return []
        course_arguments = self._course_arguments(course)
        alias_arguments = (normalize_alias(alias), alias)
        for course_arg in course_arguments:
            for alias_arg in alias_arguments:
                try:
                    result = method(course_arg, alias_arg)
                except _MISSING_PROVIDER_ERRORS:
                    continue
                except Exception as exc:
                    raise SourceUnavailableError("Notion Session alias lookup is unavailable") from exc
                if result is not None:
                    return [result] if isinstance(result, Mapping) else list(result)
        return []

    @staticmethod
    def _course_arguments(course: Any | None) -> list[Any]:
        """Return provider-compatible course values without assuming hashability."""

        values: list[Any] = []
        if course is not None:
            values.append(course)
            for name in ("Course Key", "course_key", "ID", "id", "Code", "code"):
                value = field(course, name, default=None)
                if value is not None:
                    values.append(value)
            course_record_id = record_id(course)
            if course_record_id is not None:
                values.append(course_record_id)
            if isinstance(course, str):
                values.append(course.casefold())
        else:
            values.extend((None, ""))
        unique: list[Any] = []
        for value in values:
            if not any(value == existing for existing in unique):
                unique.append(value)
        return unique

    def _alias_forms(self, base_query: str, original: str) -> tuple[str, ...]:
        values = [base_query]
        if original not in values:
            values.append(original)
        # For a phrase containing a session number, also try the compact
        # number handle without changing the query's other tokens.
        number = extract_session_number(base_query)
        if number is not None:
            values.append(f"{number}강")
            values.append(f"{number}번째 강의")
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = normalize_query(value)
            if normalized and normalized not in seen:
                result.append(normalized)
                seen.add(normalized)
        return tuple(result)

    def _fuzzy_candidates(self, records: Iterable[Any], query: str, course: Any | None) -> list[tuple[Any, str]]:
        if not query:
            return []
        wanted = normal_key(query)
        result: list[tuple[Any, str]] = []
        for record in records:
            if not self._is_session_record(record) or not self._course_matches(record, course):
                continue
            scores = [difflib.SequenceMatcher(a=wanted, b=normal_key(alias)).ratio() for alias in record_aliases(record)]
            if scores and max(scores) >= 0.86 and len(wanted) >= 3:
                result.append((record, "constrained fuzzy alias"))
        return result

    @staticmethod
    def _unique_records(records: Iterable[Any]) -> list[Any]:
        result: list[Any] = []
        seen: set[str] = set()
        for record in records:
            key = record_id(record) or repr(record)
            if key not in seen:
                result.append(record)
                seen.add(key)
        return result

    def _dedupe(self, candidates: Iterable[tuple[Any, str]]) -> list[tuple[Any, str]]:
        result: list[tuple[Any, str]] = []
        seen: set[str] = set()
        for record, reason in candidates:
            entity_id = record_id(record)
            if not entity_id or entity_id in seen:
                continue
            seen.add(entity_id)
            result.append((record, reason))
        result.sort(key=lambda pair: (self._session_number(pair[0]) or 10**9, record_label(pair[0]), record_id(pair[0]) or ""))
        return result

    @staticmethod
    def _exact_id_query(query: str) -> str | None:
        if re.fullmatch(r"[A-Z][A-Z0-9]*-S[0-9]{2}", query.upper()):
            return query.upper()
        return None


_MISSING_PROVIDER_ERRORS = (KeyError, LookupError, FileNotFoundError)


def resolve_entity(
    notion_reader: NotionReader,
    ephemeral: Any,
    query: str,
    course_hint: Any | None = None,
    entity_type: str = "session",
    *,
    resolution_ttl_seconds: int = 900,
    max_candidate_entities: int = 20,
) -> ResolutionResult:
    return SessionResolver(
        notion_reader,
        ephemeral,
        resolution_ttl_seconds=resolution_ttl_seconds,
        max_candidate_entities=max_candidate_entities,
    ).resolve_entity(query, course_hint, entity_type)


__all__ = [
    "SessionResolver",
    "extract_session_number",
    "normalize_query",
    "query_tokens",
    "resolve_entity",
    "strip_intent_keywords",
]
