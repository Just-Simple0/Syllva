import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from fake_drive import FakeDriveReader
from fake_notion import COURSE_KEY, FakeNotionReader
from uls.adapters.notion.base import NotionReader
from uls.config.schema import RetrievalCfg, UlsConfig
from uls.domain.errors import (
    ContextExpiredError,
    LocatorNotAllowedError,
    LocatorStaleError,
    SourcePartialError,
    SourceUnavailableError,
)
from uls.domain.models import ContextPackage, TimeLocator
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.enrichment.schemas import EnrichmentRecord
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.normalization.transcript import normalize_transcript
from uls.retrieval.chunking import timestamp_chunks
from uls.retrieval.engine import RetrievalEngine
from uls.retrieval.freshness import revalidate_locator
from uls.retrieval.schemas import evidence_item_to_dict


def _transcript(hash_value: str = "transcript-hash-v1", version: int = 1):
    return normalize_transcript(
        "[00:00:01] 첫 주제\n[00:00:10] 둘째 주제",
        entity_id="COMP319-S05",
        course_key=COURSE_KEY,
        source_ref=SourceRef("google_drive", "transcript-05"),
        source_hash=hash_value,
        source_version=version,
        processor_version="1.2.0",
        now="2026-09-04T00:00:00+09:00",
    )


def _material_markdown(hash_value: str = "material-hash-v1", version: int = 1) -> str:
    return f"""---
schema: uls.material.v1
entity_id: COMP319-M03
course_key: {COURSE_KEY}
source_ref:
  provider: google_drive
  file_id: material-m03
source_hash: {hash_value}
source_version: {version}
processor_version: 1.2.0
normalized_at: '2026-09-04T00:00:00+09:00'
status: ready
---
Page 1
Master theorem material
Page 2
More material
"""


def _engine(*, notion=None, drive=None, retrieval=None) -> RetrievalEngine:
    return RetrievalEngine(
        notion or FakeNotionReader(),
        drive or FakeDriveReader(),
        None,
        MemoryEphemeralStore(),
        UlsConfig(retrieval=retrieval or RetrievalCfg()),
    )


def _ready_engine(*, enrichment=None, material_verified=True, drive_hash="transcript-hash-v1", drive_version=1, derived=None, retrieval=None, notion=None, drive=None):
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
        "Verified": material_verified,
        "Start Page": 1,
        "End Page": 2,
    }
    notion = notion or FakeNotionReader(
        sessions=[session],
        material_usage={"COMP319-S05": [usage]},
        enrichments={"COMP319-S05": enrichment} if enrichment is not None else None,
    )
    transcript = derived or _transcript()
    drive = drive or FakeDriveReader(
        derived={"transcript-05": transcript, "material-m03": _material_markdown()},
        fingerprints={
            "COMP319-S05": SourceFingerprint(drive_version, drive_hash),
            "transcript-05": SourceFingerprint(drive_version, drive_hash),
            "COMP319-M03": SourceFingerprint(1, "material-hash-v1"),
            "material-m03": SourceFingerprint(1, "material-hash-v1"),
        },
    )
    return _engine(notion=notion, drive=drive, retrieval=retrieval), notion, drive


def _markdown_without_fingerprint() -> str:
    return (
        _transcript()
        .to_markdown()
        .replace("source_hash: transcript-hash-v1\n", "")
        .replace("source_version: 1\n", "")
    )


def _transcript_front_matter_variant(
    *,
    missing: str | None = None,
    status: str | None = None,
) -> str:
    lines: list[str] = []
    for line in _transcript().to_markdown().splitlines():
        if missing is not None and line.startswith(f"{missing}:"):
            continue
        if status is not None and line.startswith("status:"):
            line = f"status: {status}"
        lines.append(line)
    return "\n".join(lines)


class _NetworkFailureDrive(FakeDriveReader):
    def read_derived(self, source_ref):
        raise RuntimeError("Drive network unavailable")


class _NetworkFailureNotion(FakeNotionReader):
    def get_material_usage(self, session_id):
        raise RuntimeError("Notion network unavailable")


class _ProtocolExactReader:
    """Reader with exactly the methods declared by the retrieval protocol."""

    def __init__(self, *, annotations=None) -> None:
        self._reader = FakeNotionReader(
            sessions=[
                {
                    "ID": "COMP319-S05",
                    "Name": "05 · CPU Scheduling",
                    "Aliases": "5강 | CPU Scheduling",
                    "Course": COURSE_KEY,
                    "Session No": 5,
                    "Normalized Transcript": "transcript-05",
                }
            ],
            material_usage={
                "COMP319-S05": [
                    {
                        "ID": "USAGE-03",
                        "Session": "COMP319-S05",
                        "Material ID": "COMP319-M03",
                        "Verified": True,
                        "Start Page": 1,
                        "End Page": 2,
                    }
                ]
            },
            annotations=annotations,
        )

    def get_session(self, entity_id):
        return self._reader.get_session(entity_id)

    def find_sessions_by_alias(self, course, alias_norm):
        return self._reader.find_sessions_by_alias(course, alias_norm)

    def list_course_sessions(self, course):
        return self._reader.list_course_sessions(course)

    def get_material_usage(self, session_id):
        return self._reader.get_material_usage(session_id)

    def get_session_enrichment(self, entity_id):
        return self._reader.get_session_enrichment(entity_id)

    def get_course_by_alias(self, alias_norm):
        return self._reader.get_course_by_alias(alias_norm)

    def get_material(self, material_id):
        return self._reader.get_material(material_id)

    def get_session_user_annotations(self, session_id):
        return self._reader.get_session_user_annotations(session_id)


def test_context_returns_transcript_and_only_verified_usage_by_default() -> None:
    engine, _, _ = _ready_engine()
    package = engine.get_session_context("COMP319-S05", caller_scope="study")

    assert isinstance(package, ContextPackage)
    assert any(item.source_class == "professor_transcript" for item in package.sources)
    assert any(item.source_class == "professor_material" for item in package.sources)
    assert all(not getattr(item, "provisional", False) for item in package.sources)
    capability = engine.ephemeral.get_context_capability(package.context_id)
    assert capability is not None
    assert {str(item.locator) for item in package.sources} == {
        str(allowed.locator) for allowed in capability.allowed_locators
    }


def test_derivative_without_front_matter_fingerprint_is_not_factual() -> None:
    engine, _, _ = _ready_engine(derived=_markdown_without_fingerprint())
    package = engine.get_session_context("COMP319-S05")

    assert all(item.source_class != "professor_transcript" for item in package.sources)
    assert any("SOURCE_PARTIAL" in str(warning) for warning in package.warnings)


def test_plain_text_derivative_without_front_matter_is_not_factual() -> None:
    engine, _, _ = _ready_engine(derived="[00:00:01] plain text is not a normalized derivative")
    package = engine.get_session_context("COMP319-S05")

    assert all(item.source_class != "professor_transcript" for item in package.sources)
    assert any("SOURCE_PARTIAL" in str(warning) for warning in package.warnings)


def test_derivative_with_missing_status_is_not_current_factual_evidence() -> None:
    engine, _, _ = _ready_engine(
        derived=_transcript_front_matter_variant(missing="status")
    )

    package = engine.get_session_context("COMP319-S05")

    assert all(item.source_class != "professor_transcript" for item in package.sources)
    assert any("SOURCE_PARTIAL" in str(warning) for warning in package.warnings)


def test_derivative_with_missing_normalized_at_is_not_current_factual_evidence() -> None:
    engine, _, _ = _ready_engine(
        derived=_transcript_front_matter_variant(missing="normalized_at")
    )

    package = engine.get_session_context("COMP319-S05")

    assert all(item.source_class != "professor_transcript" for item in package.sources)
    assert any("SOURCE_PARTIAL" in str(warning) for warning in package.warnings)


def test_derivative_with_invalid_status_is_not_current_factual_evidence() -> None:
    engine, _, _ = _ready_engine(
        derived=_transcript_front_matter_variant(status="not-a-status")
    )

    package = engine.get_session_context("COMP319-S05")

    assert all(item.source_class != "professor_transcript" for item in package.sources)
    assert any("SOURCE_PARTIAL" in str(warning) for warning in package.warnings)


@pytest.mark.parametrize(
    ("missing", "status"),
    [("status", None), ("normalized_at", None), (None, "not-a-status")],
)
def test_source_chunk_rejects_incomplete_or_invalid_front_matter(
    missing: str | None,
    status: str | None,
) -> None:
    engine, _, drive = _ready_engine()
    package = engine.get_session_context("COMP319-S05")
    transcript_item = next(
        item for item in package.sources if item.source_class == "professor_transcript"
    )
    drive.derived["transcript-05"] = _transcript_front_matter_variant(
        missing=missing,
        status=status,
    )

    with pytest.raises(SourcePartialError):
        engine.get_source_chunk(package.context_id, str(transcript_item.locator))


def test_source_chunk_rejects_derivative_when_front_matter_fingerprint_is_missing() -> None:
    engine, _, drive = _ready_engine()
    package = engine.get_session_context("COMP319-S05")
    transcript_item = next(item for item in package.sources if item.source_class == "professor_transcript")
    drive.derived["transcript-05"] = _markdown_without_fingerprint()

    with pytest.raises(LocatorStaleError):
        engine.get_source_chunk(package.context_id, str(transcript_item.locator))


def test_unverified_usage_is_excluded_or_explicitly_provisional() -> None:
    engine, _, _ = _ready_engine(material_verified=False)
    default = engine.get_session_context("COMP319-S05")
    assert all(item.source_class != "professor_material" for item in default.sources)

    provisional = engine.get_session_context("COMP319-S05", include_provisional=True)
    material_items = [item for item in provisional.sources if item.source_class == "professor_material"]
    assert material_items
    assert all(getattr(item, "provisional", False) for item in material_items)
    assert any("PROVISIONAL" in str(warning) for warning in provisional.warnings)


@pytest.mark.parametrize("verified_value", ["approve", "true", 1])
def test_non_boolean_verified_values_are_not_confirmed(verified_value) -> None:
    engine, _, _ = _ready_engine(material_verified=verified_value)

    package = engine.get_session_context("COMP319-S05")
    assert all(item.source_class != "professor_material" for item in package.sources)


def test_capability_allows_returned_ranges_and_denies_unreturned_or_other_entity() -> None:
    engine, _, _ = _ready_engine()
    package = engine.get_session_context("COMP319-S05", caller_scope="study")
    transcript_item = next(item for item in package.sources if item.source_class == "professor_transcript")

    fetched = engine.get_source_chunk(package.context_id, str(transcript_item.locator), caller_scope="study")
    assert fetched.content == transcript_item.content
    with pytest.raises(LocatorNotAllowedError):
        engine.get_source_chunk(package.context_id, "COMP319-S05:t00:00:00", caller_scope="study")
    with pytest.raises(LocatorNotAllowedError):
        engine.get_source_chunk(package.context_id, "COMP319-M99:p1", caller_scope="study")
    with pytest.raises(LocatorNotAllowedError):
        engine.get_source_chunk(package.context_id, str(transcript_item.locator), caller_scope="other")


def test_capability_rejects_a_changed_current_fingerprint() -> None:
    engine, _, drive = _ready_engine()
    package = engine.get_session_context("COMP319-S05")
    transcript_item = next(item for item in package.sources if item.source_class == "professor_transcript")
    drive.fingerprints["COMP319-S05"] = SourceFingerprint(2, "transcript-hash-v2")
    drive.fingerprints["transcript-05"] = SourceFingerprint(2, "transcript-hash-v2")

    with pytest.raises(LocatorStaleError):
        engine.get_source_chunk(package.context_id, str(transcript_item.locator))


def test_malformed_locator_is_not_exposed_as_locator_parse_error() -> None:
    engine, _, _ = _ready_engine()
    package = engine.get_session_context("COMP319-S05")

    with pytest.raises(LocatorNotAllowedError):
        engine.get_source_chunk(package.context_id, "not-a-locator")


def test_expired_context_is_reported_by_source_chunk() -> None:
    engine, _, _ = _ready_engine()
    engine.capabilities.ttl_seconds = 0
    package = engine.get_session_context("COMP319-S05")
    transcript_item = next(item for item in package.sources if item.source_class == "professor_transcript")

    with pytest.raises(ContextExpiredError):
        engine.get_source_chunk(package.context_id, str(transcript_item.locator))


def test_reader_provider_failure_is_structured_source_unavailable() -> None:
    engine, _, _ = _ready_engine(drive=_NetworkFailureDrive())

    with pytest.raises(SourceUnavailableError) as error:
        engine.get_session_context("COMP319-S05")
    assert error.value.code == "SOURCE_UNAVAILABLE"


def test_notion_provider_failure_is_structured_source_unavailable() -> None:
    engine, _, _ = _ready_engine(notion=_NetworkFailureNotion())

    with pytest.raises(SourceUnavailableError) as error:
        engine.get_session_context("COMP319-S05")
    assert error.value.code == "SOURCE_UNAVAILABLE"


def test_protocol_exact_reader_keeps_verified_material_usage() -> None:
    notion = _ProtocolExactReader()
    assert isinstance(notion, NotionReader)
    engine, _, _ = _ready_engine(notion=notion)

    package = engine.get_session_context("COMP319-S05")
    assert any(item.source_class == "professor_material" for item in package.sources)


def test_protocol_exact_reader_keeps_material_and_user_annotation_reference() -> None:
    notion = _ProtocolExactReader(
        annotations={
            "COMP319-S05": [
                {
                    "ID": "ANN-01",
                    "Topic": "CPU scheduling confusion",
                    "Locator": "COMP319-S05:t00:00:01",
                    "Content": "private annotation body must not be returned",
                }
            ]
        }
    )
    assert isinstance(notion, NotionReader)
    engine, _, _ = _ready_engine(notion=notion)

    package = engine.get_session_context("COMP319-S05")

    assert any(item.source_class == "professor_material" for item in package.sources)
    assert package.user_context == (
        {
            "kind": "user_annotation",
            "source_class": "user_source",
            "entity_id": "ANN-01",
            "locator": "COMP319-S05:t00:00:01",
            "topic": "CPU scheduling confusion",
        },
    )
    assert "private annotation body" not in repr(package.user_context)


def test_role_is_rechecked_after_capability_issue() -> None:
    engine, notion, _ = _ready_engine()
    package = engine.get_session_context("COMP319-S05")
    material_item = next(item for item in package.sources if item.source_class == "professor_material")
    notion.material_usage["COMP319-S05"][0]["Verified"] = False

    with pytest.raises(LocatorNotAllowedError):
        engine.get_source_chunk(package.context_id, str(material_item.locator))


def test_stale_enrichment_is_not_factual_and_symbolic_locator_is_revalidated() -> None:
    enrichment = EnrichmentRecord(
        payload={
            "summary": "old factual summary must not be returned",
            "hints": [{"topic": "둘째 주제", "locator": "COMP319-S05:t00:00:01"}],
        },
        based_on_source_version=1,
        based_on_source_hash="old-hash",
        processor_version="1.1.0",
    )
    engine, _, _ = _ready_engine(enrichment=enrichment)
    package = engine.get_session_context("COMP319-S05")

    assert "old factual summary" not in repr(package)
    assert any(signal.get("freshness") == "STALE" for signal in package.professor_signals)
    assert any(signal.get("locator") == "COMP319-S05:t00:00:10" for signal in package.professor_signals)


def test_derivative_behind_current_source_is_not_returned_as_current() -> None:
    engine, _, _ = _ready_engine(
        drive_hash="transcript-hash-v2",
        drive_version=2,
        derived=_transcript("transcript-hash-v1", 1),
    )
    package = engine.get_session_context("COMP319-S05")

    assert all(item.source_class != "professor_transcript" for item in package.sources)
    assert any("SOURCE_PARTIAL" in str(warning) for warning in package.warnings)


def test_stale_locator_is_dropped_when_current_body_has_no_symbolic_match() -> None:
    enrichment = EnrichmentRecord(
        payload={"hints": [{"topic": "removed topic", "locator": "COMP319-S05:t00:00:01"}]},
        based_on_source_version=1,
        based_on_source_hash="old-hash",
        processor_version="1.1.0",
    )
    engine, _, _ = _ready_engine(enrichment=enrichment)
    package = engine.get_session_context("COMP319-S05")

    assert all(signal.get("topic") != "removed topic" for signal in package.professor_signals)


def test_stale_locator_range_matches_intermediate_current_chunk() -> None:
    current = normalize_transcript(
        "[00:00:01] first\n[00:00:10] middle topic\n[00:00:20] last",
        entity_id="COMP319-S05",
        course_key=COURSE_KEY,
        source_ref=SourceRef("google_drive", "transcript-05"),
        source_hash="transcript-hash-v1",
        source_version=1,
        processor_version="1.2.0",
        now="2026-09-04T00:00:00+09:00",
    )
    locator = revalidate_locator({"topic": "middle topic"}, current)
    middle_chunk = next(
        chunk
        for chunk in timestamp_chunks(current, entity_id="COMP319-S05")
        if chunk.locator.start_seconds == 10
    )

    assert locator == middle_chunk.locator


def test_zero_timestamp_transcript_stale_locator_matches_zero_mark_chunk() -> None:
    current = normalize_transcript(
        "이 transcript에는 timestamp mark가 없다. zero mark topic",
        entity_id="COMP319-S05",
        course_key=COURSE_KEY,
        source_ref=SourceRef("google_drive", "transcript-05"),
        source_hash="transcript-hash-v1",
        source_version=1,
        processor_version="1.2.0",
        now="2026-09-04T00:00:00+09:00",
    )
    assert current.marks == ()

    locator = revalidate_locator({"topic": "zero mark topic"}, current)
    chunks = timestamp_chunks(current, entity_id="COMP319-S05")

    assert len(chunks) == 1
    assert isinstance(chunks[0].locator, TimeLocator)
    assert chunks[0].locator == TimeLocator("COMP319-S05", 0, 0)
    assert locator == chunks[0].locator


def test_provisional_marker_survives_budget_truncation() -> None:
    engine, _, _ = _ready_engine(
        material_verified=False,
        retrieval=RetrievalCfg(
            max_evidence_items=12,
            max_chars_per_item=4,
            max_total_chars=24000,
            max_followup_chunks=8,
        ),
    )
    package = engine.get_session_context("COMP319-S05", include_provisional=True)
    material_items = [item for item in package.sources if item.source_class == "professor_material"]

    assert material_items
    assert all(getattr(item, "provisional", False) for item in material_items)


def test_budget_truncation_preserves_provenance() -> None:
    engine, _, _ = _ready_engine(
        retrieval=RetrievalCfg(max_evidence_items=1, max_chars_per_item=8, max_total_chars=8, max_followup_chunks=8)
    )
    package = engine.get_session_context("COMP319-S05")
    assert len(package.sources) == 1
    assert len(package.sources[0].content) <= 8
    assert package.sources[0].provenance.entity_id == package.sources[0].entity_id
    assert evidence_item_to_dict(package.sources[0])["provenance"]
