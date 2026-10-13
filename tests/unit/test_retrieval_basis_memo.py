from __future__ import annotations

from typing import Any

from uls.domain.enums import FreshnessStatus, SourceAuthority
from uls.domain.models import EvidenceItem, PageLocator
from uls.domain.provenance import Provenance
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.retrieval import engine as engine_module
from uls.retrieval.engine import RetrievalEngine
from uls.retrieval.schemas import CapabilityBinding

FP = SourceFingerprint(1, "sha256:v1")


def _pair(entity: str, file_id: str, page: int) -> tuple[EvidenceItem, CapabilityBinding]:
    ref = SourceRef("google_drive", file_id)
    locator = PageLocator(entity, page, page)
    item = EvidenceItem(
        source_class="professor_material",
        entity_id=entity,
        locator=locator,
        fingerprint=FP,
        authority=SourceAuthority.PROFESSOR_MATERIAL,
        content=f"page {page}",
        provenance=Provenance(
            "uls.test.v1",
            entity,
            "2026-2_LMS67535-001",
            ref,
            "sha256:v1",
            1,
            "1.2.0",
            "2026-10-13T00:00:00+09:00",
            "ready",
        ),
        freshness=FreshnessStatus.FRESH,
        source_ref=ref,
    )
    binding = CapabilityBinding(
        entity,
        locator,
        FP.source_hash,
        FP.source_version,
        "professor_material",
        ref,
        material_id=entity,
    )
    return item, binding


def _engine(revalidate: Any) -> tuple[RetrievalEngine, list[Any]]:
    engine = object.__new__(RetrievalEngine)
    calls: list[Any] = []

    def counted(binding: CapabilityBinding) -> Any:
        calls.append(binding.entity_id)
        return revalidate(binding)

    engine._current_fingerprint_for_binding = counted  # type: ignore[method-assign]
    return engine, calls


def test_chunks_of_one_source_are_revalidated_once_per_assembly() -> None:
    pairs = [_pair("LMS67535-M01", "file-a", page) for page in (1, 2, 3)] + [
        _pair("LMS67535-M02", "file-b", 1)
    ]
    engine, calls = _engine(lambda _binding: FP)
    warnings: list[Any] = []
    kept, bindings = engine._retain_current_evidence(
        [p[0] for p in pairs], [p[1] for p in pairs], warnings
    )
    assert len(kept) == len(bindings) == 4 and warnings == []
    assert sorted(calls) == ["LMS67535-M01", "LMS67535-M02"]
    engine._retain_current_evidence([p[0] for p in pairs], [p[1] for p in pairs], [])
    assert len(calls) == 4  # a new assembly revalidates again


def test_a_failed_basis_excludes_every_chunk_that_shares_it() -> None:
    pairs = [_pair("LMS67535-M01", "file-a", page) for page in (1, 2, 3)]

    def unavailable(_binding: CapabilityBinding) -> Any:
        raise engine_module.SourceUnavailableError("source moved")

    engine, calls = _engine(unavailable)
    warnings: list[Any] = []
    kept, _bindings = engine._retain_current_evidence(
        [p[0] for p in pairs], [p[1] for p in pairs], warnings
    )
    assert kept == [] and len(warnings) == 3 and calls == ["LMS67535-M01"]


def test_a_changed_source_is_excluded_for_every_chunk() -> None:
    pairs = [_pair("LMS67535-M01", "file-a", page) for page in (1, 2)]
    engine, calls = _engine(lambda _binding: SourceFingerprint(2, "sha256:v2"))
    kept, _bindings = engine._retain_current_evidence(
        [p[0] for p in pairs], [p[1] for p in pairs], []
    )
    assert kept == [] and calls == ["LMS67535-M01"]
