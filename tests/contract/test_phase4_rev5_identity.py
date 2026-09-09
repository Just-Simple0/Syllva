"""Rev5 regressions for Session/Material logical graph ID drift."""

from __future__ import annotations

import pathlib
import sys
from copy import deepcopy
from typing import Any
from collections.abc import Callable

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from phase4 import COURSE_KEY, phase4_applier_kwargs, phase4_proposal, ready_phase4

from uls.adapters.notion.base import HumanApprovalApplier, QueueState, upsert_proposal
from uls.adapters.notion.guarded import GuardedNotionWriter
from uls.config.schema import UlsConfig
from uls.domain.errors import EntityNotFoundError, LocatorNotAllowedError, SourceUnavailableError
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.retrieval.engine import RetrievalEngine


def _engine(reader: Any, drive: Any, resolver: Any) -> RetrievalEngine:
    return RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=resolver,
    )


def _approved() -> tuple[Any, Any, Any, dict[str, Any], HumanApprovalApplier]:
    reader, backend, drive, resolver = ready_phase4()
    proposal = phase4_proposal(reader)
    upsert_proposal(backend, proposal)
    row = backend.queue[proposal["Proposal ID"]]
    row.update({"Decision": "Approve", "State": "APPROVED"})
    applier = HumanApprovalApplier(
        GuardedNotionWriter(backend),
        decision_by="trusted-reviewer",
        **phase4_applier_kwargs(reader, drive, resolver),
    )
    return reader, backend, drive, row, applier


def _install_read_mutation(
    drive: Any,
    derivative_file_id: str,
    mutation: Callable[[], None],
) -> None:
    original = drive.read_derived
    fired = False

    def read(source_ref: Any) -> Any:
        nonlocal fired
        value = original(source_ref)
        if not fired and getattr(source_ref, "file_id", None) == derivative_file_id:
            fired = True
            mutation()
        return value

    drive.read_derived = read


@pytest.mark.parametrize(
    ("entity", "bad_id"),
    [
        ("session", "COMP319-S06"),
        ("session", "COMP319-M03"),
        ("session", None),
        ("session", "physical-page-session"),
        ("material", "COMP319-M04"),
        ("material", "COMP319-S05"),
        ("material", None),
        ("material", "physical-page-material"),
    ],
)
def test_approval_supersedes_when_current_graph_id_is_wrong_missing_or_malformed(
    entity: str,
    bad_id: str | None,
) -> None:
    reader, backend, _, row, applier = _approved()
    if entity == "session":
        reader.sessions["COMP319-S05"]["ID"] = bad_id
    else:
        reader.materials["COMP319-M03"]["ID"] = bad_id

    result = applier.apply(row["Proposal ID"])

    assert result.state is QueueState.SUPERSEDED
    assert result.mutated is False
    assert row["State"] == QueueState.SUPERSEDED.value
    assert backend.target_mutations == 0


@pytest.mark.parametrize("entity", ["session", "material"])
def test_approval_rechecks_graph_id_after_dependency_body_read(entity: str) -> None:
    reader, backend, drive, row, applier = _approved()
    if entity == "session":
        _install_read_mutation(
            drive,
            "transcript-05",
            lambda: reader.sessions["COMP319-S05"].update({"ID": "COMP319-S06"}),
        )
    else:
        _install_read_mutation(
            drive,
            "material-m03",
            lambda: reader.materials["COMP319-M03"].update({"ID": "COMP319-M04"}),
        )

    result = applier.apply(row["Proposal ID"])

    assert result.state is QueueState.APPROVED
    assert result.mutated is False
    assert row["State"] == QueueState.APPROVED.value
    assert backend.target_mutations == 0


def test_approval_postwrite_reconciliation_rejects_graph_id_drift() -> None:
    reader, backend, _, row, applier = _approved()
    original_update = backend.update_properties
    fired = False

    def update(target_db: str, entity_id: str, patch: Any, **kwargs: Any) -> Any:
        nonlocal fired
        result = original_update(target_db, entity_id, patch, **kwargs)
        if not fired and target_db == "Material Usage" and "Verified" in patch:
            fired = True
            reader.materials["COMP319-M03"]["ID"] = "COMP319-M04"
        return result

    backend.update_properties = update
    result = applier.apply(row["Proposal ID"])

    assert result.state is QueueState.APPROVED
    assert result.mutated is True
    assert row["State"] == QueueState.APPROVED.value
    assert backend.target_mutations == 1


@pytest.mark.parametrize(
    ("entity", "bad_id"),
    [
        ("session", "COMP319-S06"),
        ("session", "COMP319-M03"),
        ("session", None),
        ("session", "physical-page-session"),
        ("material", "COMP319-M04"),
        ("material", "COMP319-S05"),
        ("material", None),
        ("material", "physical-page-material"),
    ],
)
def test_initial_context_rejects_wrong_missing_or_malformed_graph_id(
    entity: str,
    bad_id: str | None,
) -> None:
    reader, _, drive, resolver = ready_phase4()
    if entity == "session":
        reader.sessions["COMP319-S05"]["ID"] = bad_id
    else:
        reader.materials["COMP319-M03"]["ID"] = bad_id
    engine = _engine(reader, drive, resolver)

    with pytest.raises(SourceUnavailableError):
        if entity == "session":
            engine.get_session_context("COMP319-S05")
        else:
            engine.get_material_context("COMP319-M03")


@pytest.mark.parametrize("entity", ["session", "material"])
def test_physical_provider_id_does_not_replace_logical_graph_id(entity: str) -> None:
    reader, _, drive, resolver = ready_phase4()
    if entity == "session":
        record = deepcopy(reader.sessions["COMP319-S05"])
        record.pop("ID")
        record["id"] = "physical-session-page"
        reader.sessions["COMP319-S05"] = record
    else:
        record = deepcopy(reader.materials["COMP319-M03"])
        record.pop("ID")
        record["id"] = "physical-material-page"
        reader.materials["COMP319-M03"] = record
    engine = _engine(reader, drive, resolver)

    with pytest.raises(SourceUnavailableError):
        if entity == "session":
            engine.get_session_context("COMP319-S05")
        else:
            engine.get_material_context("COMP319-M03")


@pytest.mark.parametrize("entity", ["session", "material"])
def test_physical_provider_id_cannot_be_stored_as_logical_graph_id(entity: str) -> None:
    reader, _, drive, resolver = ready_phase4()
    if entity == "session":
        record = deepcopy(reader.sessions["COMP319-S05"])
        record["id"] = "physical-session-page"
        record["ID"] = record["id"]
        reader.sessions["COMP319-S05"] = record
    else:
        record = deepcopy(reader.materials["COMP319-M03"])
        record["id"] = "physical-material-page"
        record["ID"] = record["id"]
        reader.materials["COMP319-M03"] = record
    engine = _engine(reader, drive, resolver)

    with pytest.raises(SourceUnavailableError):
        if entity == "session":
            engine.get_session_context("COMP319-S05")
        else:
            engine.get_material_context("COMP319-M03")


@pytest.mark.parametrize("entity", ["session", "material"])
def test_correct_nested_logical_id_with_distinct_physical_page_id_is_accepted(
    entity: str,
) -> None:
    reader, _, drive, resolver = ready_phase4()
    if entity == "session":
        reader.sessions["COMP319-S05"] = {
            "id": "physical-session-page",
            "properties": deepcopy(reader.sessions["COMP319-S05"]),
        }
    else:
        reader.materials["COMP319-M03"] = {
            "id": "physical-material-page",
            "properties": deepcopy(reader.materials["COMP319-M03"]),
        }
    engine = _engine(reader, drive, resolver)

    package = (
        engine.get_session_context("COMP319-S05")
        if entity == "session"
        else engine.get_material_context("COMP319-M03")
    )

    assert package.sources


@pytest.mark.parametrize("bad_id", ["COMP319-M03", None, "physical-session-page"])
def test_session_resolver_does_not_create_identity_from_wrong_or_physical_id(
    bad_id: str | None,
) -> None:
    reader, _, drive, resolver = ready_phase4()
    record = deepcopy(reader.sessions["COMP319-S05"])
    record.pop("ID")
    record["id"] = bad_id or "physical-session-page"
    if bad_id is not None and bad_id != "physical-session-page":
        record["ID"] = bad_id
    reader.sessions["COMP319-S05"] = record
    engine = _engine(reader, drive, resolver)

    with pytest.raises(EntityNotFoundError):
        engine.resolve_entity("COMP319-S05", COURSE_KEY)


def test_session_resolver_accepts_nested_logical_id_with_distinct_physical_id() -> None:
    reader, _, drive, resolver = ready_phase4()
    reader.sessions["COMP319-S05"] = {
        "id": "physical-session-page",
        "properties": deepcopy(reader.sessions["COMP319-S05"]),
    }
    engine = _engine(reader, drive, resolver)

    result = engine.resolve_entity("COMP319-S05", COURSE_KEY)

    assert result.status == "resolved"
    assert result.entity_id == "COMP319-S05"


@pytest.mark.parametrize("entity", ["session", "material", "usage"])
def test_initial_body_read_id_drift_does_not_issue_evidence(entity: str) -> None:
    reader, _, drive, resolver = ready_phase4()
    if entity == "session":
        _install_read_mutation(
            drive,
            "transcript-05",
            lambda: reader.sessions["COMP319-S05"].update({"ID": "COMP319-S06"}),
        )
    elif entity == "material":
        _install_read_mutation(
            drive,
            "material-m03",
            lambda: reader.materials["COMP319-M03"].update({"ID": "COMP319-M04"}),
        )
    else:
        reader.material_usage["COMP319-S05"][0]["Verified"] = True
        _install_read_mutation(
            drive,
            "material-m03",
            lambda: reader.materials["COMP319-M03"].update({"ID": "COMP319-M04"}),
        )
    engine = _engine(reader, drive, resolver)

    package = (
        engine.get_session_context("COMP319-S05")
        if entity in {"session", "usage"}
        else engine.get_material_context("COMP319-M03")
    )

    if entity == "usage":
        assert not any(item.entity_id == "COMP319-M03" for item in package.sources)
        assert all(
            binding.material_id != "COMP319-M03"
            for binding in engine.capabilities.bindings_for(package.context_id) or ()
        )
    else:
        assert package.sources == ()
        assert not (engine.capabilities.bindings_for(package.context_id) or ())


@pytest.mark.parametrize("kind", ["session", "material", "usage"])
def test_candidate_followup_rejects_current_graph_id_drift(kind: str) -> None:
    reader, _, drive, resolver = ready_phase4()
    if kind == "session":
        engine = _engine(reader, drive, resolver)
        package = engine.get_session_context("COMP319-S05")
        item = next(item for item in package.sources if item.source_class == "professor_transcript")
        reader.sessions["COMP319-S05"]["ID"] = "COMP319-S06"
    elif kind == "material":
        engine = _engine(reader, drive, resolver)
        package = engine.get_material_context("COMP319-M03")
        item = package.sources[0]
        reader.materials["COMP319-M03"]["ID"] = "COMP319-M04"
    else:
        usage = reader.material_usage["COMP319-S05"][0]
        usage["Verified"] = True
        engine = _engine(reader, drive, resolver)
        package = engine.get_session_context("COMP319-S05")
        item = next(item for item in package.sources if item.entity_id == "COMP319-M03")
        reader.materials["COMP319-M03"]["ID"] = "COMP319-M04"

    with pytest.raises(LocatorNotAllowedError):
        engine.get_source_chunk(package.context_id, str(item.locator))


@pytest.mark.parametrize("kind", ["session", "material", "usage"])
def test_final_body_read_rejects_graph_id_drift(kind: str) -> None:
    reader, _, drive, resolver = ready_phase4()
    if kind == "session":
        engine = _engine(reader, drive, resolver)
        package = engine.get_session_context("COMP319-S05")
        item = next(item for item in package.sources if item.source_class == "professor_transcript")
        mutation = lambda: reader.sessions["COMP319-S05"].update({"ID": "COMP319-S06"})
    elif kind == "material":
        engine = _engine(reader, drive, resolver)
        package = engine.get_material_context("COMP319-M03")
        item = package.sources[0]
        mutation = lambda: reader.materials["COMP319-M03"].update({"ID": "COMP319-M04"})
    else:
        usage = reader.material_usage["COMP319-S05"][0]
        usage["Verified"] = True
        engine = _engine(reader, drive, resolver)
        package = engine.get_session_context("COMP319-S05")
        item = next(item for item in package.sources if item.entity_id == "COMP319-M03")
        mutation = lambda: reader.materials["COMP319-M03"].update({"ID": "COMP319-M04"})
    source_ref = item.source_ref
    assert source_ref is not None
    _install_read_mutation(drive, source_ref.file_id, mutation)

    with pytest.raises(LocatorNotAllowedError):
        engine.get_source_chunk(package.context_id, str(item.locator))
