"""P-D: section tags (S4) and the fail-closed assignment gate of the retrieval filter."""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))
from fake_drive import FakeDriveReader
from fake_notion import COURSE_KEY, FakeNotionReader, _material
from no_v2_state import NoV2State

from uls.adapters.drive.binding import ValidatedSourceBindingResolver
from uls.config.schema import RetrievalCfg, UlsConfig
from uls.domain.errors import LocatorNotAllowedError, PolicyDeniedError
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.enrichment.schemas import EnrichmentRecord
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.normalization.transcript import normalize_transcript
from uls.retrieval import tags
from uls.retrieval.engine import RetrievalEngine
from uls.retrieval.schemas import evidence_item_to_dict

pytestmark = pytest.mark.contract

CLEAN = "Binary search halves the range at every step."
ASSIGNMENT = "Upload the .py file to Canvas before Friday."


def _transcript(*lines: str):
    body = "\n".join(f"[00:00:{10 * (i + 1):02d}] {line}" for i, line in enumerate(lines))
    return normalize_transcript(
        body, entity_id="COMP319-S05", course_key=COURSE_KEY, source_ref=SourceRef("google_drive", "transcript-05"),
        source_hash="transcript-hash-v1", source_version=1, processor_version="1.2.0", now="2026-09-04T00:00:00+09:00")


def _material_md(*pages: str) -> str:
    body = "\n".join(f"Page {i + 1}\n{text}" for i, text in enumerate(pages))
    return f"""---
schema: uls.material.v1
entity_id: COMP319-M03
course_key: {COURSE_KEY}
source_ref:
  provider: google_drive
  file_id: material-m03
source_hash: material-hash-v1
source_version: 1
processor_version: 1.2.0
normalized_at: '2026-09-04T00:00:00+09:00'
status: ready
---
{body}
"""


def _engine(*, lines=(CLEAN, "Merge sort splits recursively."), pages=(CLEAN, "Quick sort picks a pivot."),
            material_extra=None, enrichment=None):
    session = {"ID": "COMP319-S05", "Name": "05 · CPU Scheduling", "Aliases": "5강", "Course": COURSE_KEY,
               "Session No": 5, "Normalized Transcript": "transcript-05"}
    usage = {"ID": "USAGE-03", "Session": "COMP319-S05", "Material ID": "COMP319-M03", "Role": "Primary",
             "Verified": True, "Start Page": 1, "End Page": len(pages)}
    material = {**_material(), **(material_extra or {})}
    notion = FakeNotionReader(
        sessions=[session], materials=[material], material_usage={"COMP319-S05": [usage]},
        enrichments={"COMP319-S05": enrichment} if enrichment is not None else None)
    drive = FakeDriveReader(
        derived={"transcript-05": _transcript(*lines), "material-m03": _material_md(*pages)},
        fingerprints={
            "COMP319-S05": SourceFingerprint(1, "transcript-hash-v1"), "transcript-05": SourceFingerprint(1, "transcript-hash-v1"),
            "COMP319-M03": SourceFingerprint(1, "material-hash-v1"), "material-m03": SourceFingerprint(1, "material-hash-v1")})
    engine = RetrievalEngine(
        notion, drive, NoV2State(), MemoryEphemeralStore(), UlsConfig(retrieval=RetrievalCfg()),
        source_binding_resolver=ValidatedSourceBindingResolver(drive))
    return engine, drive


def _contents(package) -> list[str]:
    return [item.content for item in package.sources]


# --- pure tag rules ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Lab Exercise 2\nImplement a binary search tree in Python.\nUpload the .py file to Canvas before Friday.", True),
    ("과제는 다음 주까지 제출하세요", True), ("The deadline is Friday", True), ("Project is due Friday", True),
    ("hw3 handout", True), ("Submit your report", True),
    ("Due to caching the result is stale", False), ("Binary search halves the range.", False),
    ("Dueling banjos", False), ("An exercise in recursion", False),
])
def test_strong_assignment_vocabulary(text, expected) -> None:
    assert tags.has_strong_assignment_signal(text) is expected


def test_lab_material_is_never_certified_by_rules() -> None:
    class Chunk:
        def __init__(self, content): self.content, self.locator = content, None
    lab = tags.assess_document("M1", [Chunk(CLEAN)], "LAB_MATERIAL")
    assert lab.decisions[0].assignment == tags.UNRESOLVED and not lab.complete
    assert tags.assess_document("M1", [Chunk(CLEAN)], "LECTURE_SLIDES").complete


def test_decisions_are_recomputed_from_the_current_bytes() -> None:
    class Chunk:
        def __init__(self, content): self.content, self.locator = content, None
    assert tags.assess_document("M1", [Chunk(CLEAN)], None).decisions[0].assignment == tags.NO
    assert tags.assess_document("M1", [Chunk(CLEAN + " Submit it.")], None).decisions[0].assignment == tags.YES


# --- Session / transcript -----------------------------------------------------------------------

def test_default_session_context_drops_assignment_chunks_and_carries_policy() -> None:
    engine, _ = _engine(lines=(CLEAN, ASSIGNMENT))
    package = engine.get_session_context("COMP319-S05")
    transcript = [item for item in package.sources if item.source_class == "professor_transcript"]
    assert all(ASSIGNMENT not in item.content for item in package.sources)
    assert transcript and all(item.assignment_decision == "NO" and item.usage_policy == "NORMAL" for item in transcript)
    wire = evidence_item_to_dict(transcript[0])
    assert wire["usage_policy"] == "NORMAL" and wire["assignment_decision"] == "NO"


def test_opt_in_returns_assignment_chunks_with_a_generation_ban() -> None:
    engine, _ = _engine(lines=(CLEAN, ASSIGNMENT))
    package = engine.get_session_context("COMP319-S05", include_assignment=True)
    flagged = [item for item in package.sources if ASSIGNMENT in item.content]
    assert flagged and flagged[0].usage_policy == "NO_ANSWER_GENERATION" and flagged[0].assignment_decision == "YES"


def test_chunks_without_assignment_words_are_unchanged_by_the_filter() -> None:
    engine, _ = _engine()
    default = engine.get_session_context("COMP319-S05")
    opted = engine.get_session_context("COMP319-S05", include_assignment=True)
    assert _contents(default) == _contents(opted)


def test_assignment_pages_of_a_material_never_reach_session_results_or_capabilities() -> None:
    engine, _ = _engine(pages=(CLEAN, ASSIGNMENT))
    package = engine.get_session_context("COMP319-S05")
    assert all(ASSIGNMENT not in item.content for item in package.sources)
    with pytest.raises(LocatorNotAllowedError):
        engine.get_source_chunk(package.context_id, "COMP319-M03:p2")


def test_opt_in_capability_serves_the_page_with_the_ban_attached() -> None:
    engine, _ = _engine(pages=(CLEAN, ASSIGNMENT))
    package = engine.get_session_context("COMP319-S05", include_assignment=True)
    item = engine.get_source_chunk(package.context_id, "COMP319-M03:p2")
    assert ASSIGNMENT in item.content and item.usage_policy == "NO_ANSWER_GENERATION"


def test_a_capability_is_revalidated_against_the_current_bytes() -> None:
    engine, drive = _engine(pages=(CLEAN, "Quick sort picks a pivot."))
    package = engine.get_session_context("COMP319-S05")
    assert engine.get_source_chunk(package.context_id, "COMP319-M03:p2").usage_policy == "NORMAL"
    drive.derived["material-m03"] = _material_md(CLEAN, "Quick sort. Submit the exercise by Monday.")
    with pytest.raises(LocatorNotAllowedError):
        engine.get_source_chunk(package.context_id, "COMP319-M03:p2")


# --- direct Material ------------------------------------------------------------------------------

@pytest.mark.parametrize("extra", [
    {"AI Kind": "ASSIGNMENT_BRIEF"}, {"AI Kind": "ASSIGNMENT_RESOURCE"}, {"AI Kind": "LAB_MATERIAL"},
    {"Type": "Professor Notes"},
])
def test_direct_material_lookup_withholds_assignment_documents_by_default(extra) -> None:
    engine, _ = _engine(material_extra=extra)
    with pytest.raises(PolicyDeniedError):
        engine.get_material_context("COMP319-M03")
    package = engine.get_material_context("COMP319-M03", include_assignment=True)
    assert package.sources


def test_a_clean_legacy_material_is_served_as_before() -> None:
    engine, _ = _engine()
    package = engine.get_material_context("COMP319-M03")
    assert len(package.sources) == 2
    assert all(item.usage_policy == "NORMAL" for item in package.sources)


def test_assignment_kind_materials_always_carry_the_generation_ban_when_opted_in() -> None:
    engine, _ = _engine(material_extra={"AI Kind": "ASSIGNMENT_BRIEF"})
    package = engine.get_material_context("COMP319-M03", include_assignment=True)
    assert package.sources and all(item.usage_policy == "NO_ANSWER_GENERATION" for item in package.sources)


# --- enrichment signals ------------------------------------------------------------------------------

def _enrichment(payload):
    return EnrichmentRecord(payload=payload, based_on_source_version=1,
                            based_on_source_hash="transcript-hash-v1", processor_version="1.2.0")


def test_signals_tied_to_assignment_text_are_withheld() -> None:
    payload = {"professor_signals": [
        {"content": "Binary search is emphasised.", "locator": "COMP319-S05:t00:00:10-00:00:10"},
        {"content": "Implement a stack using an array.", "locator": "COMP319-S05:t00:00:20-00:00:20"},
        {"content": "Hand in the report by Monday."},
        {"content": "Merge sort recurses."}]}
    engine, _ = _engine(lines=(CLEAN, "Implement a stack using an array. Upload it Friday."), enrichment=_enrichment(payload))
    default = engine.get_session_context("COMP319-S05")
    texts = [signal.get("content") for signal in default.professor_signals]
    assert "Implement a stack using an array." not in texts and "Hand in the report by Monday." not in texts
    assert "Binary search is emphasised." in texts and "Merge sort recurses." in texts
    assert any(isinstance(w, dict) and w.get("code") == tags.SIGNAL_WITHHELD_CODE for w in default.warnings)
    opted = engine.get_session_context("COMP319-S05", include_assignment=True)
    assert len(opted.professor_signals) == 4


# --- Exam / concept / verify ---------------------------------------------------------------------------

def test_search_concept_and_verify_follow_the_policy() -> None:
    from uls.retrieval.additional import search_concept

    engine, _ = _engine(lines=("Binary search is covered.", ASSIGNMENT))
    default = search_concept(engine, COURSE_KEY, "binary")
    assert all(ASSIGNMENT not in item.content for item in default.sources)
    opted = search_concept(engine, COURSE_KEY, "Upload", include_assignment=True)
    assert any(ASSIGNMENT in item.content and item.usage_policy == "NO_ANSWER_GENERATION" for item in opted.sources)


# --- Exam and Activity ---------------------------------------------------------------------------------

def _exam_engine(text: str):
    import test_get_exam_context as exam_fixture

    engine, _reader, drive = exam_fixture._engine(included_sessions=["COMP319-S01"])
    drive.derived["transcript-01"] = exam_fixture._transcript("COMP319-S01", "source-01", text)
    return engine


def test_exam_context_excludes_assignment_text_unless_opted_in() -> None:
    engine = _exam_engine("Hand in the lab report by Monday.")
    default = engine.get_exam_context("COMP319-E01")
    assert all("lab report" not in item.content for item in default.sources)
    opted = engine.get_exam_context("COMP319-E01", include_assignment=True)
    flagged = [item for item in opted.sources if "lab report" in item.content]
    assert flagged and flagged[0].usage_policy == "NO_ANSWER_GENERATION"


def test_exam_context_keeps_clean_evidence() -> None:
    engine = _exam_engine("Binary search is the topic.")
    package = engine.get_exam_context("COMP319-E01")
    assert package.sources and all(item.usage_policy == "NORMAL" for item in package.sources)


def test_activity_context_marks_official_instructions_as_no_answer_generation() -> None:
    import test_get_activity_context as activity_fixture

    engine = activity_fixture._engine()[0]
    package = engine.get_activity_context("COMP319-A01")
    official = [item for item in package.sources if item.source_class == "official_activity"]
    assert official and all(
        item.usage_policy == "NO_ANSWER_GENERATION" and item.assignment_decision == "YES" for item in official)
    wire = evidence_item_to_dict(official[0])
    assert wire["usage_policy"] == "NO_ANSWER_GENERATION"
    chunk = engine.get_source_chunk(package.context_id, str(official[0].locator))
    assert chunk.usage_policy == "NO_ANSWER_GENERATION"


def test_every_projection_carries_the_assignment_region_rule() -> None:
    root = pathlib.Path(__file__).resolve().parents[2]
    paths = [root / "contracts" / "study-behavior.md",
             *(root / "clients" / "claude" / "skills" / name / "SKILL.md"
               for name in ("study-session", "explain-concept", "exam-prep", "activity-help", "verify-source")),
             root / "clients" / "chatgpt" / "instructions" / "study-behavior.md"]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "NO_ANSWER_GENERATION" in text and "assignment_decision" in text, path


# --- review round 1 -------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["ASSIGNMENT_BRIEF", "LAB_MATERIAL"])
def test_a_kind_changed_after_issuance_applies_to_existing_capabilities(kind) -> None:
    engine, _ = _engine(material_extra={"AI Kind": "LECTURE_SLIDES"}, pages=(CLEAN, "Implement a stack using an array."))
    package = engine.get_session_context("COMP319-S05")
    assert engine.get_source_chunk(package.context_id, "COMP319-M03:p2").usage_policy == "NORMAL"
    opt_in = engine.get_session_context("COMP319-S05", include_assignment=True)
    engine.notion_reader.materials["COMP319-M03"]["AI Kind"] = kind
    with pytest.raises(LocatorNotAllowedError):
        engine.get_source_chunk(package.context_id, "COMP319-M03:p2")
    with pytest.raises(PolicyDeniedError):
        engine.get_material_context("COMP319-M03")
    item = engine.get_source_chunk(opt_in.context_id, "COMP319-M03:p2")
    assert item.usage_policy == "NO_ANSWER_GENERATION"


@pytest.mark.parametrize("value", ["false", "0", 1, 0, None, "True"])
def test_assignment_opt_in_must_be_a_real_boolean(value) -> None:
    engine, _ = _engine(lines=(CLEAN, ASSIGNMENT))
    for call in (
        lambda: engine.get_session_context("COMP319-S05", include_assignment=value),
        lambda: engine.get_material_context("COMP319-M03", include_assignment=value),
    ):
        with pytest.raises(PolicyDeniedError):
            call()


def test_a_binding_rejects_a_non_boolean_opt_in() -> None:
    from uls.retrieval.schemas import CapabilityBinding

    with pytest.raises(ValueError):
        CapabilityBinding("S1", None, "h", 1, "professor_transcript", assignment_opt_in="false")  # type: ignore[arg-type]


def test_official_activity_keeps_its_decision_on_refetch() -> None:
    import test_get_activity_context as activity_fixture

    engine = activity_fixture._engine()[0]
    package = engine.get_activity_context("COMP319-A01")
    official = next(item for item in package.sources if item.source_class == "official_activity")
    chunk = engine.get_source_chunk(package.context_id, str(official.locator))
    assert chunk.assignment_decision == "YES" and chunk.usage_policy == "NO_ANSWER_GENERATION"


@pytest.mark.parametrize("signal", [
    {"content": "Implement a stack using an array.", "locator": "COMP319-S05:t00:00:20-00:00:20"},
    {"content": "Implement a stack using an array.", "source_locators": [{"locator": "COMP319-S05:t00:00:20-00:00:20"}]},
    {"content": "Implement a stack using an array.", "locators": ["COMP319-S05:t00:00:10-00:00:10", "COMP319-S05:t00:00:20-00:00:20"]},
    {"content": "Implement a stack using an array.", "evidence": {"source_locator": {"start": "COMP319-S05:t00:00:20-00:00:20"}}},
    {"content": "Implement a stack using an array.", "locator": "not-a-locator"},
    {"content": "Implement a stack using an array.", "source_locators": {"COMP319-S05:t00:00:20-00:00:20": True}},
    {"content": "Implement a stack using an array.", "source_locators": {"COMP319-S05:t00:00:20-00:00:20": 3}},
    {"content": "Implement a stack using an array.", "refs": [{"COMP319-S05:t00:00:10-00:00:10": 1, "COMP319-S05:t00:00:20-00:00:20": 2}]},
    {"content": "Implement a stack using an array.", "note": "see COMP319-S05:t00:00:20-00:00:20 for the source"},
    {"content": "Implement a stack using an array.", "weird": {"\uff23OMP319-S05:t00:00:20-00:00:20": None}},
])
def test_signals_tied_to_a_yes_chunk_are_withheld_whatever_the_locator_shape(signal) -> None:
    engine, _ = _engine(lines=(CLEAN, "Implement a stack using an array. Upload it Friday."),
                        enrichment=_enrichment({"professor_signals": [signal]}))
    default = engine.get_session_context("COMP319-S05")
    assert default.professor_signals == ()
    assert any(isinstance(w, dict) and w.get("code") == tags.SIGNAL_WITHHELD_CODE for w in default.warnings)
    assert len(engine.get_session_context("COMP319-S05", include_assignment=True).professor_signals) == 1


def test_a_signal_whose_every_locator_is_clean_is_kept() -> None:
    signal = {"content": "Binary search is emphasised.", "source_locators": [{"locator": "COMP319-S05:t00:00:10-00:00:10"}]}
    engine, _ = _engine(lines=(CLEAN, "Implement a stack using an array. Upload it Friday."),
                        enrichment=_enrichment({"professor_signals": [signal]}))
    assert len(engine.get_session_context("COMP319-S05").professor_signals) == 1


def test_descriptive_metadata_under_a_locator_named_mapping_is_not_a_claim() -> None:
    signal = {"content": "Binary search is emphasised.",
              "source_locators": {"type": "timestamp", "note": "first minute", "range": [10, 20]}}
    engine, _ = _engine(lines=(CLEAN, "Implement a stack using an array. Upload it Friday."),
                        enrichment=_enrichment({"professor_signals": [signal]}))
    assert len(engine.get_session_context("COMP319-S05").professor_signals) == 1


def test_signal_locator_extraction_shapes() -> None:
    cases = [
        ({"a": {"X-1:t00:00:01": True}}, ["X-1:t00:00:01"]),
        ({"locator": "bogus"}, ["bogus"]),
        ({"source_locators": {"type": "page"}}, []),
        ({"x": ["see M1-3:p2-p4 and S1:t00:00:01-00:00:09:source"]}, ["M1-3:p2-p4", "S1:t00:00:01-00:00:09:source"]),
    ]
    for value, expected in cases:
        assert tags.signal_locators(value) == expected


@pytest.mark.parametrize("signal", [
    {"Upload the .py file to Canvas before Friday.": "instruction metadata", "content": "Binary search is emphasised."},
    {"content": "Binary search is emphasised.", "meta": {"homework": {"x": 1}}},
    {"content": "Binary search is emphasised.", "items": [{"\uff35pload it": "y"}],
     "source_locators": [{"locator": "COMP319-S05:t00:00:10-00:00:10"}]},
    {"content": "Binary search is emphasised.", "과제 안내": True},
])
def test_assignment_words_in_signal_keys_are_withheld(signal) -> None:
    engine, _ = _engine(enrichment=_enrichment({"professor_signals": [signal]}))
    default = engine.get_session_context("COMP319-S05")
    assert default.professor_signals == ()
    assert any(isinstance(w, dict) and w.get("code") == tags.SIGNAL_WITHHELD_CODE for w in default.warnings)
    assert len(engine.get_session_context("COMP319-S05", include_assignment=True).professor_signals) == 1


def test_a_normalised_locator_is_not_also_claimed_in_its_raw_form() -> None:
    assert tags.signal_locators({"locator": "\uff23OMP319-S05:t00:00:10-00:00:10"}) == ["COMP319-S05:t00:00:10-00:00:10"]
