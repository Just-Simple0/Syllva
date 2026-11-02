"""C-25: chunk strategies (implementation spec §8.8, errata E12/E14).

* summary windows cover the WHOLE document in locator order, or say they do not
  (``PARTIAL_COVERAGE``) — never a silent cut;
* exceeding the budget is always reported (``PARTIAL_CONTEXT(used/total)``);
* query mode scores any-term (a chunk need not contain every query word) and
  returns the chosen chunks in locator order;
* an unrelated query gets at most ``LOW_RELEVANCE_LEAD`` lead chunks plus
  ``LOW_RELEVANCE`` — never the whole material;
* long pages/segments are split into parts of one locator, and every
  (locator, part) pair names exactly one piece of text.
"""

from __future__ import annotations

import pytest
from conftest import make_pdf_bytes
from test_v2_retrieval_service import SCOPE, Library

from syllva.domain.models import serialize_locator
from syllva.normalization.chunks import split_text, transcript_chunks
from syllva.normalization.schemas import NormalizedTranscript, TimestampMark
from syllva.retrieval.ranking import score_texts
from syllva.retrieval.schemas import RetrievalBudget
from syllva.retrieval.service import LOW_RELEVANCE_LEAD, LocatorNotAuthorized
from syllva.state.v2.database import Database

pytestmark = pytest.mark.contract


@pytest.fixture
def lib(v2_db: Database) -> Library:
    return Library(v2_db)


def _transcript(segments: list[str]) -> str:
    return "".join(f"[{i // 60:02d}:{i % 60:02d}] {text}\n" for i, text in enumerate(segments))


def _times(items: object) -> list[str]:
    return [str(i.locator).split(":t")[1][:8] for i in items]  # type: ignore[attr-defined]


# --- splitting (E14) -------------------------------------------------------------


def test_split_text_covers_everything_within_the_limit() -> None:
    text = ("para one sentence. " * 30 + "\n\n") * 6 + "z" * 900
    spans = split_text(text, 500)
    assert spans[0][0] == 0 and spans[-1][1] == len(text)
    assert all(b - a <= 500 for a, b in spans)
    assert all(spans[i][1] == spans[i + 1][0] for i in range(len(spans) - 1))  # no gap, no overlap
    assert text[spans[0][0] : spans[0][1]].endswith(("\n\n", ". "))  # a boundary, not a mid-word cut


def test_repeated_or_backwards_timestamps_keep_locators_unique() -> None:
    body = "[00:10] first\n[00:10] second\n[00:20] third\n[00:15] late\n[00:30] end\n"
    starts = [(10, "[00:10] first"), (10, "[00:10] second"), (20, "[00:20] third"), (15, "[00:15] late"), (30, "[00:30] end")]
    marks = tuple(TimestampMark(sec, body.index(text)) for sec, text in starts)
    transcript = NormalizedTranscript(front_matter=_front_matter(), body=body, marks=marks)
    chunks = transcript_chunks(transcript, "LMS1-M01")
    keys = [(serialize_locator(c.locator), c.part) for c in chunks]
    assert len(keys) == len(set(keys)), "a (locator, part) pair must name one piece of text"
    assert [serialize_locator(c.locator) for c in chunks] == [
        "LMS1-M01:t00:00:10-00:00:19", "LMS1-M01:t00:00:20-00:00:29", "LMS1-M01:t00:00:30",
    ]
    assert "first" in chunks[0].content and "second" in chunks[0].content
    assert "late" in chunks[1].content  # nothing is dropped
    assert "".join(c.content for c in chunks).count("[") == 5


def _front_matter():  # type: ignore[no-untyped-def]
    from syllva.normalization.transcript import normalize_transcript

    return normalize_transcript(
        b"[00:00] x", entity_id="LMS1-M01", course_key="LMS1", source_ref="local:SRC-1",
        source_hash="sha256:" + "0" * 64, source_version=1, processor_version="p1", now="2026-11-02T00:00:00+00:00",
    ).front_matter


def test_long_pdf_page_is_split_into_parts(lib: Library, v2_db: Database) -> None:
    pytest.importorskip("pypdf")
    import io

    from syllva.ingestion.local import ingest_new
    from syllva.normalization.local import normalize_source_version

    words = " ".join(f"word{i}" for i in range(1400))  # ~11k chars on one page
    ingested = ingest_new(v2_db.writer(), lib.blobs, io.BytesIO(make_pdf_bytes([words, "short page"])), display_name="L.pdf")
    result = normalize_source_version(
        v2_db.writer(), lib.blobs, ingested.source_version_id, kind="pdf", entity_id="LMS1-M09", course_key="LMS1",
        processor_version="p1",
    )
    assert result.status == "READY" and result.chunk_count >= 3


# --- summary windows (§8.8.3) ----------------------------------------------------


def test_windows_cover_the_whole_document_in_order(lib: Library) -> None:
    segments = [f"topic {n} " + "y" * 900 for n in range(20)]
    lib.material("LMS1-M01", _transcript(segments))
    service = lib.service()
    result = service.get_material_windows("LMS1-M01", caller_scope=SCOPE, window_chars=4000)
    assert result.complete and len(result.windows) > 1
    flattened = [item for window in result.windows for item in window.sources]
    assert _times(flattened) == [f"00:00:{n:02d}" for n in range(20)]  # all chunks, once, in order
    assert all(sum(len(i.content) for i in w.sources) <= 4000 for w in result.windows)
    assert not [w for w in result.warnings if w["code"] in ("PARTIAL_CONTEXT", "PARTIAL_COVERAGE")]
    # each window is its own capability: a locator of window 2 is not served through window 1
    first, second = result.windows[0], result.windows[1]
    with pytest.raises(LocatorNotAuthorized):
        service.get_source_chunk(first.context_id or "", str(second.sources[0].locator), caller_scope=SCOPE)
    served = service.get_source_chunk(second.context_id or "", str(second.sources[0].locator), caller_scope=SCOPE)
    assert served.content == second.sources[0].content


def test_too_many_windows_is_reported_not_cut_silently(lib: Library) -> None:
    lib.material("LMS1-M01", _transcript([f"part {n} " + "y" * 3000 for n in range(6)]))
    result = lib.service().get_material_windows("LMS1-M01", caller_scope=SCOPE, window_chars=4000, max_windows=2)
    assert not result.complete and len(result.windows) == 2
    coverage = next(w for w in result.warnings if w["code"] == "PARTIAL_COVERAGE")
    assert (coverage["windows_used"], coverage["windows_needed"]) == (2, 6)
    assert coverage["covered_through"]["locator"].startswith("LMS1-M01:t00:00:01")
    assert {"code": "PARTIAL_CONTEXT", "used": 2, "total": 6} in result.warnings


def test_partial_source_windows_are_not_complete(lib: Library) -> None:
    lib.material("LMS1-M01", "[00:00] a\n[99:99:99:99] broken\n")
    result = lib.service().get_material_windows("LMS1-M01", caller_scope=SCOPE)
    assert result.windows and not result.complete
    assert {"code": "PARTIAL_SOURCE", "material_id": "LMS1-M01"} in result.windows[0].warnings


def test_window_must_hold_one_chunk(lib: Library) -> None:
    lib.material("LMS1-M01", "[00:00] a\n")
    with pytest.raises(ValueError):
        lib.service().get_material_windows("LMS1-M01", caller_scope=SCOPE, window_chars=100)


# --- query mode (§8.8.2) -----------------------------------------------------------


def test_query_is_any_term_and_results_are_in_locator_order(lib: Library) -> None:
    lib.material(
        "LMS1-M01",
        _transcript(["welcome and logistics", "TCP congestion window grows", "grading policy", "TCP handshake: SYN, ACK"]),
    )
    package = lib.service().get_material_context("LMS1-M01", caller_scope=SCOPE, query="TCP는 handshake가 뭐야")
    # chunk 1 has only "tcp" (any-term), chunk 3 has both; both are used, in locator order
    assert _times(package.sources) == ["00:00:01", "00:00:03"]
    assert {"code": "PARTIAL_CONTEXT", "used": 2, "total": 4} in package.warnings
    assert not [w for w in package.warnings if w["code"] == "LOW_RELEVANCE"]


def test_query_budget_takes_the_best_scoring_chunk(lib: Library) -> None:
    lib.material("LMS1-M01", _transcript(["TCP congestion window grows", "TCP handshake: SYN, ACK", "TCP header fields"]))
    service = lib.service(budget=RetrievalBudget(max_evidence_items=1))
    package = service.get_material_context("LMS1-M01", caller_scope=SCOPE, query="TCP handshake")
    assert _times(package.sources) == ["00:00:01"]
    assert {"code": "PARTIAL_CONTEXT", "used": 1, "total": 3} in package.warnings


def test_unrelated_query_gets_a_small_lead_not_everything(lib: Library) -> None:
    lib.material("LMS1-M01", _transcript([f"networking topic {n}" for n in range(10)]))
    package = lib.service().get_material_context("LMS1-M01", caller_scope=SCOPE, query="양자역학 슈뢰딩거")
    assert len(package.sources) == LOW_RELEVANCE_LEAD
    assert _times(package.sources) == ["00:00:00", "00:00:01", "00:00:02", "00:00:03"]
    codes = [w["code"] for w in package.warnings]
    assert "LOW_RELEVANCE" in codes
    assert {"code": "PARTIAL_CONTEXT", "used": LOW_RELEVANCE_LEAD, "total": 10} in package.warnings


def test_without_query_everything_that_fits_is_used(lib: Library) -> None:
    lib.material("LMS1-M01", _transcript(["a1", "b2", "c3"]))
    package = lib.service().get_material_context("LMS1-M01", caller_scope=SCOPE)
    assert _times(package.sources) == ["00:00:00", "00:00:01", "00:00:02"]
    assert not [w for w in package.warnings if w["code"] in ("PARTIAL_CONTEXT", "LOW_RELEVANCE")]


def test_part_not_packaged_is_refused(lib: Library) -> None:
    lib.material("LMS1-M01", _transcript(["x" * 5000]))
    service = lib.service(budget=RetrievalBudget(max_evidence_items=1))
    package = service.get_material_context("LMS1-M01", caller_scope=SCOPE)
    only = package.sources[0]
    assert (only.part, only.parts) == (1, 2)
    with pytest.raises(LocatorNotAuthorized):
        service.get_source_chunk(package.context_id or "", str(only.locator), caller_scope=SCOPE, part=2)


def test_scoring_is_deterministic_and_zero_without_overlap() -> None:
    texts = ["TCP handshake SYN ACK", "UDP datagram", "TCP congestion"]
    assert score_texts("tcp handshake", texts) == score_texts("tcp handshake", texts)
    scores = score_texts("tcp handshake", texts)
    assert scores[1] == 0.0 and scores[0] > scores[2] > 0
    assert score_texts("", texts) == [0.0, 0.0, 0.0]


# --- R1b-5 review follow-ups -------------------------------------------------------


def test_short_korean_terms_and_particles_match() -> None:
    texts = ["힙 정렬은 완전 이진 트리를 쓴다", "큐는 선입선출", "스택은 후입선출"]
    scores = score_texts("힙 정렬", texts)
    assert scores[0] > 0 and scores[1] == scores[2] == 0
    scores = score_texts("큐의 특징", texts)
    assert scores[1] > 0 and scores[0] == scores[2] == 0


def test_every_window_says_whether_its_set_is_complete(lib: Library) -> None:
    lib.material("LMS1-M01", _transcript([f"part {n} " + "y" * 3000 for n in range(6)]))
    partial = lib.service().get_material_windows("LMS1-M01", caller_scope=SCOPE, window_chars=4000, max_windows=2)
    markers = [next(w for w in window.warnings if w["code"] == "WINDOW") for window in partial.windows]
    assert all(m["set_complete"] is False and m["windows_needed"] == 6 for m in markers)
    whole = lib.service().get_material_windows("LMS1-M01", caller_scope=SCOPE)
    assert whole.complete
    assert all(next(w for w in win.warnings if w["code"] == "WINDOW")["set_complete"] for win in whole.windows)


def test_no_evidence_is_said_not_implied(lib: Library) -> None:
    lib.material("LMS1-M01", "[00:00] a\n", normalize=False)
    result = lib.service().get_material_windows("LMS1-M01", caller_scope=SCOPE)
    assert not result.windows and not result.complete
    assert {"code": "NO_EVIDENCE"} in result.warnings


def test_window_is_capped_at_the_prompt_budget(lib: Library) -> None:
    lib.material("LMS1-M01", "[00:00] a\n")
    with pytest.raises(ValueError):
        lib.service().get_material_windows("LMS1-M01", caller_scope=SCOPE, window_chars=10**6)


def test_serialized_evidence_keeps_parts(lib: Library) -> None:
    from syllva.retrieval.schemas import evidence_item_to_dict

    lib.material("LMS1-M01", _transcript(["x" * 5000, "short"]))
    package = lib.service().get_material_context("LMS1-M01", caller_scope=SCOPE)
    rows = [evidence_item_to_dict(item) for item in package.sources]
    assert [(r.get("part"), r.get("parts")) for r in rows] == [(1, 2), (2, 2), (None, None)]
