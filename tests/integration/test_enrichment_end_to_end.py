from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from fake_drive import FakeDriveReader
from fake_llm import FakeLLMAdapter, material_result, session_result
from fake_notion import COURSE_KEY, FakeNotionReader, FakeNotionWriter
from uls.config.schema import UlsConfig
from uls.domain.enums import ProcessingStatus
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.enrichment.writer import EnrichmentWriter
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.normalization.transcript import normalize_transcript
from uls.retrieval.engine import RetrievalEngine
from uls.state.sqlite import SQLiteStateStore


FP1 = SourceFingerprint(1, "transcript-v1")
FP2 = SourceFingerprint(2, "transcript-v2")
MATERIAL_FP = SourceFingerprint(1, "material-v1")


def _transcript(version: int = 1, source_hash: str = "transcript-v1"):
    topic = "첫 주제" if version == 1 else "첫 주제"
    return normalize_transcript(
        f"[00:00:01] {topic}\n[00:00:10] 둘째 주제",
        entity_id="COMP319-S05",
        course_key=COURSE_KEY,
        source_ref=SourceRef("google_drive", "transcript-05"),
        source_hash=source_hash,
        source_version=version,
        processor_version="1.2.0",
        now="2026-09-05T00:00:00+09:00",
    )


def _material() -> str:
    return """---
schema: uls.material.v1
entity_id: COMP319-M03
course_key: 2026-1_COMP319-002
source_ref:
  provider: google_drive
  file_id: material-m03
source_hash: material-v1
source_version: 1
processor_version: 1.2.0
normalized_at: '2026-09-05T00:00:00+09:00'
status: ready
---
Page 1
Master theorem
Page 2
Dynamic programming
"""


def _setup():
    session = {
        "ID": "COMP319-S05",
        "Name": "05 · CPU Scheduling",
        "Aliases": "5강 | CPU Scheduling",
        "Course": COURSE_KEY,
        "Session No": 5,
        "Normalized Transcript": "transcript-05",
    }
    usage = {
        "ID": "USAGE-03",
        "Session": "COMP319-S05",
        "Material ID": "COMP319-M03",
        "Verified": True,
        "Start Page": 1,
        "End Page": 2,
    }
    reader = FakeNotionReader(
        sessions=[session],
        material_usage={"COMP319-S05": [usage]},
    )
    drive = FakeDriveReader(
        derived={"transcript-05": _transcript(), "material-m03": _material()},
        fingerprints={
            "COMP319-S05": FP1,
            "transcript-05": FP1,
            "COMP319-M03": MATERIAL_FP,
            "material-m03": MATERIAL_FP,
        },
    )
    notion = FakeNotionWriter(reader)
    state = SQLiteStateStore(":memory:")
    return reader, drive, notion, state


def _engine(reader, drive):
    return RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
    )


def test_producer_output_is_consumed_as_fresh_ai_signals() -> None:
    reader, drive, notion, state = _setup()
    writer = EnrichmentWriter(
        FakeLLMAdapter(session_result=session_result()),
        notion,
        drive,
        state,
    )

    outcome = writer.process_session("COMP319-S05", _transcript())
    package = _engine(reader, drive).get_session_context("COMP319-S05")

    assert outcome.status is ProcessingStatus.READY
    assert package.professor_signals
    assert any(signal.get("kind") == "professor_emphasis" for signal in package.professor_signals)
    assert all(signal.get("source_class") == "ai_enrichment" for signal in package.professor_signals)


def test_stale_enrichment_is_not_factual_but_top_level_hints_revalidate() -> None:
    reader, drive, notion, state = _setup()
    writer = EnrichmentWriter(FakeLLMAdapter(session_result=session_result()), notion, drive, state)
    writer.process_session("COMP319-S05", _transcript())
    stored = reader.get_session_enrichment("COMP319-S05")
    assert stored is not None
    assert stored.payload.as_dict()["symbolic_hints"]

    current = _transcript(version=2, source_hash="transcript-v2")
    drive.derived["transcript-05"] = current
    drive.fingerprints["COMP319-S05"] = FP2
    drive.fingerprints["transcript-05"] = FP2
    package = _engine(reader, drive).get_session_context("COMP319-S05")

    assert any(warning.get("code") == "STALE_ENRICHMENT" for warning in package.warnings)
    assert package.professor_signals
    assert all(signal.get("kind") == "stale_locator_hint" for signal in package.professor_signals)
    assert all(signal.get("factual") is False for signal in package.professor_signals)
    assert any(signal.get("topic") == "첫 주제" for signal in package.professor_signals)


def test_stale_hint_is_discarded_when_current_derivative_has_no_match() -> None:
    reader, drive, notion, state = _setup()
    EnrichmentWriter(FakeLLMAdapter(session_result=session_result()), notion, drive, state).process_session(
        "COMP319-S05", _transcript()
    )
    current = normalize_transcript(
        "[00:00:01] completely changed content",
        entity_id="COMP319-S05",
        course_key=COURSE_KEY,
        source_ref=SourceRef("google_drive", "transcript-05"),
        source_hash="transcript-v2",
        source_version=2,
        processor_version="1.2.0",
        now="2026-09-05T00:00:00+09:00",
    )
    drive.derived["transcript-05"] = current
    drive.fingerprints["COMP319-S05"] = FP2
    drive.fingerprints["transcript-05"] = FP2

    package = _engine(reader, drive).get_session_context("COMP319-S05")

    assert all(signal.get("topic") not in {"첫 주제", "둘째 주제"} for signal in package.professor_signals)


def test_material_enrichment_round_trips_through_the_canonical_notion_ai_region() -> None:
    reader, drive, notion, state = _setup()
    writer = EnrichmentWriter(FakeLLMAdapter(material_result=material_result()), notion, drive, state)

    outcome = writer.process_material("COMP319-M03", _material())
    stored = reader.get_material_enrichment("COMP319-M03")

    assert outcome.status is ProcessingStatus.READY
    assert stored is not None
    assert stored.source_fingerprint == MATERIAL_FP
    assert stored.payload.as_dict()["content_index"][0]["evidence"][0]["locator"] == "COMP319-M03:p1"
