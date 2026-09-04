import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from fake_notion import COURSE_KEY, FakeNotionReader
from uls.config.schema import UlsConfig
from uls.domain.errors import EntityNotFoundError, InvalidCandidateError, ResolutionExpiredError
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.retrieval.engine import RetrievalEngine


def _engine(notion: FakeNotionReader | None = None, *, ttl: int = 900) -> RetrievalEngine:
    config = UlsConfig()
    config.retrieval.resolution_ttl_seconds = ttl
    return RetrievalEngine(notion or FakeNotionReader(), object(), None, MemoryEphemeralStore(), config)


def test_exact_id_alias_session_number_and_intent_suffix_resolve_same_session() -> None:
    engine = _engine()

    assert engine.resolve_entity("COMP319-S05").entity_id == "COMP319-S05"
    assert engine.resolve_entity("5강").entity_id == "COMP319-S05"
    assert engine.resolve_entity("5강 정리").entity_id == "COMP319-S05"
    assert engine.resolve_entity("5강", course_hint="COMP319").entity_id == "COMP319-S05"
    assert engine.resolve_entity("알고리즘 5강").entity_id == "COMP319-S05"


def test_session_number_matching_is_token_exact_not_substring() -> None:
    engine = _engine()

    for query in ("15강", "제5강의실"):
        with pytest.raises(EntityNotFoundError) as error:
            engine.resolve_entity(query, course_hint=COURSE_KEY)
        assert error.value.code == "ENTITY_NOT_FOUND"


def test_course_number_tier_wins_over_a_later_alias_tier() -> None:
    notion = FakeNotionReader(
        sessions=[
            {
                "ID": "COMP319-S05",
                "Name": "05 · Number match",
                "Aliases": "다른 별칭",
                "Course": COURSE_KEY,
                "Session No": 5,
            },
            {
                "ID": "COMP319-S06",
                "Name": "06 · Alias match",
                "Aliases": "5강",
                "Course": COURSE_KEY,
                "Session No": 6,
            },
        ]
    )
    engine = _engine(notion)

    result = engine.resolve_entity("5강", course_hint=COURSE_KEY)

    assert result.status == "resolved"
    assert result.entity_id == "COMP319-S05"


def test_ambiguous_resolution_uses_opaque_handle_and_selection() -> None:
    notion = FakeNotionReader(
        sessions=[
            {"ID": "COMP319-S05", "Name": "05 A", "Aliases": "공통", "Course": COURSE_KEY, "Session No": 5},
            {"ID": "COMP319-S06", "Name": "06 B", "Aliases": "공통", "Course": COURSE_KEY, "Session No": 6},
        ]
    )
    engine = _engine(notion)
    result = engine.resolve_entity("공통", course_hint=COURSE_KEY)

    assert result.status == "ambiguous"
    assert result.resolution_id
    assert len(result.candidates) == 2
    with pytest.raises(InvalidCandidateError):
        engine.select_resolution(result.resolution_id, "cand_from_other_handle")
    selected = engine.select_resolution(result.resolution_id, result.candidates[1].candidate_id)
    assert selected.entity_id == "COMP319-S06"


def test_resolution_expiry_is_structured() -> None:
    notion = FakeNotionReader(
        sessions=[
            {"ID": "COMP319-S05", "Name": "05 A", "Aliases": "공통", "Course": COURSE_KEY, "Session No": 5},
            {"ID": "COMP319-S06", "Name": "06 B", "Aliases": "공통", "Course": COURSE_KEY, "Session No": 6},
        ]
    )
    engine = _engine(notion, ttl=0)
    engine.resolver.resolution_ttl_seconds = 0
    result = engine.resolve_entity("공통", course_hint=COURSE_KEY)
    with pytest.raises(ResolutionExpiredError) as error:
        engine.select_resolution(result.resolution_id, result.candidates[0].candidate_id)
    assert error.value.code == "RESOLUTION_EXPIRED"
