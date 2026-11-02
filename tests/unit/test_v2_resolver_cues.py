"""Unit tests for the resolver's cue extraction and title chapter parsing (spec §9.3)."""

from __future__ import annotations

import pytest

from syllva.retrieval.natural import extract_cues, norm, title_chapters

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("query", "weeks", "sessions", "chapters", "dates"),
    [
        ("4주차", (4,), (), (), ()),
        ("week 3", (3,), (), (), ()),
        ("2강", (), (2,), (), ()),
        ("2강의 내용", (), (2,), (), ()),
        ("3번째 강의", (), (3,), (), ()),
        ("5회차", (), (5,), (), ()),
        ("s02 전사문", (), (2,), (), ()),
        ("102강", (), (), (), ()),
        ("09.17", (), (), (), ((None, 9, 17),)),
        ("9/17", (), (), (), ((None, 9, 17),)),
        ("2026-09-17", (), (), (), ((2026, 9, 17),)),
        ("9월 17일", (), (), (), ((None, 9, 17),)),
        ("ch4", (), (), (4,), ()),
        ("chapter 04", (), (), (4,), ()),
        ("4장", (), (), (4,), ()),
        ("mech201", (), (), (), ()),
        ("phys101", (), (), (), ()),
        ("which 4", (), (), (), ()),
        ("cs101 1강", (), (1,), (), ()),
    ],
)
def test_extract_cues(query: str, weeks: tuple[int, ...], sessions: tuple[int, ...], chapters: tuple[int, ...], dates: tuple[object, ...]) -> None:
    cues = extract_cues(norm(query))
    assert (cues.weeks, cues.sessions, cues.chapters, cues.dates) == (weeks, sessions, chapters, dates)


def test_cue_spans_are_masked_for_course_matching() -> None:
    assert extract_cues(norm("알고리즘 2강")).masked.strip() == "알고리즘"
    assert "comp0217" in extract_cues(norm("COMP0217 3강")).masked


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Chapter 04_05 TCP 소켓 프로그래밍.pdf", {4, 5}),
        ("Chapter 03 UDP.pdf", {3}),
        ("04_05 소켓.pdf", {4, 5}),
        ("09_17 TCP.pdf", set()),  # a date, not chapters
        ("알고리즘2_중간고사_기출문제지.pdf", set()),
        ("S02 전사문.txt", set()),
        ("2026_09_17 녹음.m4a", set()),
        ("3장 정렬.pdf", {3}),
    ],
)
def test_title_chapters(title: str, expected: set[int]) -> None:
    assert title_chapters(title) == expected
