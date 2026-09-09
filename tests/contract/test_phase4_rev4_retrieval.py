"""Read-time Phase 4 retrieval regressions.

Each mutation is applied after the fake provider has captured the old
derivative body and before it returns that body.  The tests therefore fail
against a pre-recheck implementation that treats the provider read as the
last freshness or authorization check.
"""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from phase4 import ready_phase4

from uls.config.schema import RetrievalCfg, UlsConfig
from uls.domain.errors import (
    LocatorNotAllowedError,
    LocatorStaleError,
    SourcePartialError,
    SourceUnavailableError,
)
from uls.domain.source_ref import SourceFingerprint
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.retrieval.engine import RetrievalEngine


def _engine(reader: Any, drive: Any, *, config: UlsConfig | None = None) -> RetrievalEngine:
    _, _, _, resolver = ready_phase4(drive=drive)
    return RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        config or UlsConfig(),
        source_binding_resolver=resolver,
    )


def _install_read_mutation(
    drive: Any,
    derivative_file_id: str,
    mutation: Callable[[], None],
) -> None:
    """Mutate state after the old body is captured, once for one derivative."""

    original = drive.read_derived
    fired = False

    def read(source_ref: Any) -> Any:
        nonlocal fired
        value = original(source_ref)
        file_id = getattr(source_ref, "file_id", None)
        if not fired and file_id == derivative_file_id:
            fired = True
            mutation()
        return value

    drive.read_derived = read


def _usage(reader: Any, usage_id: str = "MU:existing") -> dict[str, Any]:
    return next(
        row
        for row in reader.material_usage["COMP319-S05"]
        if row.get("ID") == usage_id
    )


def _material(reader: Any) -> dict[str, Any]:
    return reader.materials["COMP319-M03"]


def _session(reader: Any) -> dict[str, Any]:
    return reader.sessions["COMP319-S05"]


def _change_fingerprint(drive: Any, *, derivative_file_id: str) -> None:
    if derivative_file_id == "transcript-05":
        fingerprint = SourceFingerprint(2, "transcript-hash-v2")
        drive.fingerprints["transcript-05"] = fingerprint
        drive.fingerprints["COMP319-S05"] = fingerprint
    else:
        fingerprint = SourceFingerprint(2, "material-hash-v2")
        drive.fingerprints["material-m03"] = fingerprint
        drive.fingerprints["COMP319-M03"] = fingerprint


def _mutate_graph(
    reader: Any,
    drive: Any,
    mutation: str,
    *,
    entity: str,
    usage: dict[str, Any] | None = None,
) -> None:
    record = _session(reader) if entity == "COMP319-S05" else _material(reader)
    if mutation == "verified":
        assert usage is not None
        usage["Verified"] = False
    elif mutation == "role":
        assert usage is not None
        usage["Role"] = "Supporting"
    elif mutation == "range":
        assert usage is not None
        usage.update({"Start Page": 2, "End Page": 2})
    elif mutation == "type":
        assert entity == "COMP319-M03"
        record["Type"] = "Professor Notes"
    elif mutation == "course_key":
        reader.courses["course-page-1"]["Course Key"] = "2026-2_COMP319-002"
    elif mutation == "course_relation":
        record["Course"] = {"relation": [{"id": "missing-course"}]}
    elif mutation == "source_rebind":
        if entity == "COMP319-S05":
            record["Normalized Transcript"] = "unregistered-transcript-v2"
        else:
            record["Normalized Source"] = "unregistered-material-v2"
    elif mutation == "fingerprint":
        _change_fingerprint(
            drive,
            derivative_file_id=(
                "transcript-05" if entity == "COMP319-S05" else "material-m03"
            ),
        )
    else:
        raise AssertionError(f"unknown mutation: {mutation}")


@pytest.mark.parametrize(
    "mutation",
    ["course_key", "course_relation", "source_rebind", "fingerprint"],
)
def test_initial_session_transcript_read_rechecks_the_mutated_basis(mutation: str) -> None:
    reader, _, drive, resolver = ready_phase4()
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=resolver,
    )
    _install_read_mutation(
        drive,
        "transcript-05",
        lambda: _mutate_graph(
            reader,
            drive,
            mutation,
            entity="COMP319-S05",
        ),
    )

    package = engine.get_session_context("COMP319-S05")

    assert package.sources == ()
    assert all(
        binding.entity_id != "COMP319-S05"
        for binding in engine.capabilities.bindings_for(package.context_id) or ()
    )


@pytest.mark.parametrize(
    "mutation",
    ["type", "course_key", "course_relation", "source_rebind", "fingerprint"],
)
def test_initial_direct_material_read_does_not_issue_mutated_evidence(mutation: str) -> None:
    reader, _, drive, resolver = ready_phase4()
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=resolver,
    )
    _install_read_mutation(
        drive,
        "material-m03",
        lambda: _mutate_graph(
            reader,
            drive,
            mutation,
            entity="COMP319-M03",
        ),
    )

    try:
        package = engine.get_material_context("COMP319-M03")
    except SourcePartialError:
        # A fingerprint advance is detected by the first post-body freshness
        # check.  It is still a successful regression: no package or
        # capability containing the old body was produced.
        assert mutation == "fingerprint"
        return

    assert package.sources == ()
    assert engine.capabilities.bindings_for(package.context_id) == ()


@pytest.mark.parametrize(
    "mutation",
    ["verified", "role", "range", "type", "course_key", "course_relation", "source_rebind", "fingerprint"],
)
def test_initial_usage_material_read_rechecks_every_authorization_basis(mutation: str) -> None:
    reader, _, drive, resolver = ready_phase4()
    usage = _usage(reader)
    usage["Verified"] = True
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=resolver,
    )
    _install_read_mutation(
        drive,
        "material-m03",
        lambda: _mutate_graph(
            reader,
            drive,
            mutation,
            entity="COMP319-M03",
            usage=usage,
        ),
    )

    package = engine.get_session_context("COMP319-S05")

    assert not any(item.entity_id == "COMP319-M03" for item in package.sources)
    assert all(
        binding.material_id != "COMP319-M03"
        for binding in engine.capabilities.bindings_for(package.context_id) or ()
    )
    assert package.scope["provisional"] is False


@pytest.mark.parametrize("mutation", ["role", "range"])
def test_initial_provisional_usage_revocation_does_not_leave_provisional_flags(
    mutation: str,
) -> None:
    reader, _, drive, resolver = ready_phase4()
    usage = _usage(reader)
    usage["Verified"] = False
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=resolver,
    )
    _install_read_mutation(
        drive,
        "material-m03",
        lambda: _mutate_graph(
            reader,
            drive,
            mutation,
            entity="COMP319-M03",
            usage=usage,
        ),
    )

    package = engine.get_session_context("COMP319-S05", include_provisional=True)

    assert not any(item.entity_id == "COMP319-M03" for item in package.sources)
    assert all(
        binding.material_id != "COMP319-M03"
        for binding in engine.capabilities.bindings_for(package.context_id) or ()
    )
    assert package.scope["include_provisional"] is True
    assert package.scope["provisional"] is False
    assert not any(warning["code"] == "PROVISIONAL_SOURCE" for warning in package.warnings)


@pytest.mark.parametrize("sibling_first", [False, True])
@pytest.mark.parametrize("mutation", ["verified", "role", "range"])
def test_mutated_usage_drops_only_that_overlapping_sibling(
    sibling_first: bool,
    mutation: str,
) -> None:
    reader, _, drive, resolver = ready_phase4()
    target = _usage(reader)
    target["Verified"] = True
    sibling = deepcopy(target)
    sibling.update({"ID": "MU:sibling", "Start Page": 1, "End Page": 1, "Verified": True})
    reader.material_usage["COMP319-S05"] = (
        [sibling, target] if sibling_first else [target, sibling]
    )
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=resolver,
    )
    _install_read_mutation(
        drive,
        "material-m03",
        lambda: _mutate_graph(
            reader,
            drive,
            mutation,
            entity="COMP319-M03",
            usage=target,
        ),
    )

    package = engine.get_session_context("COMP319-S05")
    material_items = [item for item in package.sources if item.entity_id == "COMP319-M03"]
    material_bindings = [
        binding
        for binding in engine.capabilities.bindings_for(package.context_id) or ()
        if binding.material_id == "COMP319-M03"
    ]

    assert material_items
    assert {binding.usage_id for binding in material_bindings} == {"MU:sibling"}
    assert all(getattr(item, "provisional", False) is False for item in material_items)
    assert package.scope["provisional"] is False


def test_retain_current_evidence_preserves_a_sibling_when_one_check_raises() -> None:
    reader, _, drive, resolver = ready_phase4()
    target = _usage(reader)
    target["Verified"] = True
    sibling = deepcopy(target)
    sibling.update({"ID": "MU:sibling", "Start Page": 1, "End Page": 1, "Verified": True})
    reader.material_usage["COMP319-S05"] = [target, sibling]
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=resolver,
    )
    package = engine.get_session_context("COMP319-S05")
    bindings = list(engine.capabilities.bindings_for(package.context_id) or ())
    material_items = [item for item in package.sources if item.entity_id == "COMP319-M03"]
    material_bindings = [binding for binding in bindings if binding.material_id == "COMP319-M03"]
    assert material_items
    assert {binding.usage_id for binding in material_bindings} == {"MU:existing", "MU:sibling"}

    original = engine._current_fingerprint_for_binding

    def check(binding: Any) -> Any:
        if binding.usage_id == "MU:existing":
            raise RuntimeError("candidate lookup failed")
        return original(binding)

    engine._current_fingerprint_for_binding = check
    warnings: list[Any] = []
    retained_evidence, retained_bindings = engine._retain_current_evidence(
        list(package.sources),
        bindings,
        warnings,
    )

    assert {binding.usage_id for binding in retained_bindings if binding.material_id} == {
        "MU:sibling"
    }
    assert len(retained_evidence) == len(retained_bindings)
    assert any(warning["code"] == "SOURCE_UNAVAILABLE" for warning in warnings)


def test_retain_current_evidence_rejects_count_mismatch_as_structured_failure() -> None:
    reader, _, drive, resolver = ready_phase4()
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=resolver,
    )
    package = engine.get_session_context("COMP319-S05")
    bindings = list(engine.capabilities.bindings_for(package.context_id) or ())

    with pytest.raises(SourceUnavailableError) as error:
        engine._retain_current_evidence(list(package.sources), bindings[:-1], [])

    assert error.value.details == {
        "evidence_count": len(package.sources),
        "binding_count": len(bindings) - 1,
    }


def test_retain_current_evidence_rejects_same_length_provisional_misalignment() -> None:
    reader, _, drive, resolver = ready_phase4()
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=resolver,
    )
    package = engine.get_session_context("COMP319-S05")
    bindings = list(engine.capabilities.bindings_for(package.context_id) or ())
    bad_binding = replace(bindings[0], provisional=not bindings[0].provisional)
    warnings: list[Any] = []

    retained_evidence, retained_bindings = engine._retain_current_evidence(
        [package.sources[0]],
        [bad_binding],
        warnings,
    )

    assert retained_evidence == []
    assert retained_bindings == []
    assert warnings == [
        {
            "code": "SOURCE_UNAVAILABLE",
            "message": "evidence and capability binding identity is misaligned",
        }
    ]


def test_budget_keeps_only_the_binding_for_the_returned_duplicate_locator() -> None:
    reader, _, drive, resolver = ready_phase4()
    target = _usage(reader)
    target["Verified"] = True
    sibling = deepcopy(target)
    sibling.update({"ID": "MU:sibling", "Start Page": 1, "End Page": 1, "Verified": True})
    reader.material_usage["COMP319-S05"] = [target, sibling]
    config = UlsConfig(
        retrieval=RetrievalCfg(
            max_evidence_items=3,
            max_chars_per_item=4000,
            max_total_chars=24_000,
            max_followup_chunks=8,
        )
    )
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        config,
        source_binding_resolver=resolver,
    )

    package = engine.get_session_context("COMP319-S05")
    material_items = [item for item in package.sources if item.entity_id == "COMP319-M03"]
    material_bindings = [
        binding
        for binding in engine.capabilities.bindings_for(package.context_id) or ()
        if binding.material_id == "COMP319-M03"
    ]

    assert len(material_items) == 1
    assert len(material_bindings) == 1
    assert material_bindings[0].usage_id == "MU:existing"


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("verified", LocatorNotAllowedError),
        ("role", LocatorNotAllowedError),
        ("range", LocatorNotAllowedError),
        ("type", LocatorNotAllowedError),
        ("course_key", LocatorNotAllowedError),
        ("course_relation", LocatorNotAllowedError),
        ("source_rebind", LocatorNotAllowedError),
        ("fingerprint", LocatorStaleError),
    ],
)
def test_final_usage_body_read_rechecks_full_authorization_basis(
    mutation: str,
    expected_error: type[Exception],
) -> None:
    reader, _, drive, resolver = ready_phase4()
    usage = _usage(reader)
    usage["Verified"] = True
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=resolver,
    )
    package = engine.get_session_context("COMP319-S05")
    material_item = next(item for item in package.sources if item.entity_id == "COMP319-M03")
    _install_read_mutation(
        drive,
        "material-m03",
        lambda: _mutate_graph(
            reader,
            drive,
            mutation,
            entity="COMP319-M03",
            usage=usage,
        ),
    )

    with pytest.raises(expected_error) as error:
        engine.get_source_chunk(package.context_id, str(material_item.locator))

    assert error.value.code == (
        "LOCATOR_STALE" if expected_error is LocatorStaleError else "LOCATOR_NOT_ALLOWED"
    )


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("type", LocatorNotAllowedError),
        ("course_key", LocatorNotAllowedError),
        ("course_relation", LocatorNotAllowedError),
        ("source_rebind", LocatorNotAllowedError),
        ("fingerprint", LocatorStaleError),
    ],
)
def test_final_direct_material_body_read_rechecks_graph_and_fingerprint(
    mutation: str,
    expected_error: type[Exception],
) -> None:
    reader, _, drive, resolver = ready_phase4()
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=resolver,
    )
    package = engine.get_material_context("COMP319-M03")
    material_item = package.sources[0]
    _install_read_mutation(
        drive,
        "material-m03",
        lambda: _mutate_graph(
            reader,
            drive,
            mutation,
            entity="COMP319-M03",
        ),
    )

    with pytest.raises(expected_error) as error:
        engine.get_source_chunk(package.context_id, str(material_item.locator))

    assert error.value.code == (
        "LOCATOR_STALE" if expected_error is LocatorStaleError else "LOCATOR_NOT_ALLOWED"
    )


@pytest.mark.parametrize(
    "mutation",
    ["course_key", "course_relation", "source_rebind", "fingerprint"],
)
def test_final_transcript_body_read_rechecks_graph_and_fingerprint(mutation: str) -> None:
    reader, _, drive, resolver = ready_phase4()
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=resolver,
    )
    package = engine.get_session_context("COMP319-S05")
    transcript_item = next(
        item for item in package.sources if item.source_class == "professor_transcript"
    )
    _install_read_mutation(
        drive,
        "transcript-05",
        lambda: _mutate_graph(
            reader,
            drive,
            mutation,
            entity="COMP319-S05",
        ),
    )

    expected_error = LocatorStaleError if mutation == "fingerprint" else LocatorNotAllowedError
    with pytest.raises(expected_error) as error:
        engine.get_source_chunk(package.context_id, str(transcript_item.locator))

    assert error.value.code == (
        "LOCATOR_STALE" if mutation == "fingerprint" else "LOCATOR_NOT_ALLOWED"
    )


def test_navigation_only_pointer_change_during_initial_material_read_preserves_identity() -> None:
    reader, _, drive, resolver = ready_phase4()
    usage = _usage(reader)
    usage["Verified"] = True
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=resolver,
    )

    def navigate() -> None:
        _material(reader)["Normalized Source"] = (
            "https://drive.google.com/file/d/material-m03/view"
        )

    _install_read_mutation(drive, "material-m03", navigate)
    package = engine.get_session_context("COMP319-S05")

    material_items = [item for item in package.sources if item.entity_id == "COMP319-M03"]
    material_bindings = [
        binding
        for binding in engine.capabilities.bindings_for(package.context_id) or ()
        if binding.material_id == "COMP319-M03"
    ]
    assert material_items
    assert material_bindings
    assert all(binding.source_ref.identity == ("google_drive", "material-m03") for binding in material_bindings)


def test_navigation_only_pointer_change_during_final_material_read_preserves_identity() -> None:
    reader, _, drive, resolver = ready_phase4()
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=resolver,
    )
    package = engine.get_material_context("COMP319-M03")

    def navigate() -> None:
        _material(reader)["Normalized Source"] = (
            "https://drive.google.com/file/d/material-m03/view"
        )

    _install_read_mutation(drive, "material-m03", navigate)
    item = engine.get_source_chunk(package.context_id, "COMP319-M03:p1")

    assert item.content == "Page 1\nMaster theorem material"
    assert item.fingerprint == SourceFingerprint(1, "material-hash-v1")
