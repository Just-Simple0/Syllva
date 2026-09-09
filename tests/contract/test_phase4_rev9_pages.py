"""Permanent Phase4 rev9 full-page-index regressions."""

from __future__ import annotations

import json

import pytest
from tests.contract.test_phase4_rev6_recovery import _apply
from tests.fixtures.phase4 import material_derivative, phase4_proposal, ready_phase4

from uls.adapters.notion.base import (
    _PHASE4_APPLY_MARKER_PREFIX,
    QueueState,
    upsert_proposal,
)
from uls.domain.page_range import PageRange


def _pages_body(pages: list[int], *, page_count: int | None = None) -> str:
    header = material_derivative().split("Page 1\n", 1)[0]
    if page_count is not None:
        header = header.replace(
            "processor_version: 1.2.0\n",
            f"processor_version: 1.2.0\npage_count: {page_count}\n",
        )
    return header + "".join(
        f"Page {page}\nMaterial evidence for page {page}.\n" for page in pages
    )


def _approved_long_page(
    operation: str,
    desired_page: int,
    *,
    body: str | None = None,
):
    reader, writer, drive, resolver = ready_phase4()
    target = reader.material_usage["COMP319-S05"][0]
    if operation == "create_usage":
        target.update({"Start Page": desired_page, "End Page": desired_page})
    drive.derived["material-m03"] = body or _pages_body(list(range(1, 41)))
    reader.materials["COMP319-M03"]["Page Count"] = max(desired_page, 40)
    proposal = phase4_proposal(
        reader,
        operation=operation,
        desired_range=PageRange(desired_page, desired_page),
    )
    upsert_proposal(writer, proposal)
    writer.queue[proposal["Proposal ID"]].update(
        {"Decision": "Approve", "State": QueueState.APPROVED.value}
    )
    return reader, writer, drive, resolver, proposal


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_page40_applies_once_and_replays_without_a_second_target_write(operation: str) -> None:
    reader, writer, drive, resolver, proposal = _approved_long_page(operation, 40)

    first = _apply(reader, writer, drive, resolver, proposal)
    second = _apply(reader, writer, drive, resolver, proposal)

    assert first.state is QueueState.APPLIED
    assert first.mutated is True
    assert second.state is QueueState.APPLIED
    assert second.mutated is False
    assert writer.target_mutations == 1
    target = reader.material_usage["COMP319-S05"][0]
    if operation == "create_usage":
        assert target["Verified"] is True
    else:
        assert target["Start Page"] == 40
        assert target["End Page"] == 40


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_page41_is_denied_without_target_mutation(operation: str) -> None:
    reader, writer, drive, resolver, proposal = _approved_long_page(operation, 41)

    result = _apply(reader, writer, drive, resolver, proposal)

    assert result.state is QueueState.SUPERSEDED
    assert result.mutated is False
    assert writer.target_mutations == 0
    assert writer.queue[proposal["Proposal ID"]]["State"] == QueueState.SUPERSEDED.value


@pytest.mark.parametrize(
    "defect, body, requested_page",
    [
        ("invalid", _pages_body([1, 2, 3]).replace("Page 2\n", "Page 2.5\n"), 2),
        ("gapped", _pages_body([1, 3]), 2),
        ("duplicate", _pages_body([1, 2, 2, 3]), 2),
        (
            "contradictory",
            _pages_body([1, 2]) + "[[page:2]]\nContradictory second declaration.\n",
            2,
        ),
        ("unmarked", material_derivative().split("Page 1\n", 1)[0] + "No page marker here.\n", 1),
        ("page_count_mismatch", _pages_body([1, 2, 3], page_count=2), 3),
    ],
)
@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_invalid_or_contradictory_page_index_is_denied(
    operation: str,
    defect: str,
    body: str,
    requested_page: int,
) -> None:
    _reader, writer, drive, resolver, proposal = _approved_long_page(
        operation,
        requested_page,
        body=body,
    )

    result = _apply(_reader, writer, drive, resolver, proposal)

    assert result.state is QueueState.SUPERSEDED
    assert result.mutated is False
    assert writer.target_mutations == 0


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_full_page_index_survives_mutate_then_raise_and_audit_recovery(operation: str) -> None:
    reader, writer, drive, resolver, proposal = _approved_long_page(operation, 40)
    writer.mutate_then_raise_target = True
    writer.fail_audit_once = True

    first = _apply(reader, writer, drive, resolver, proposal)

    assert first.state is QueueState.APPROVED
    assert first.mutated is True
    assert writer.target_mutations == 1
    marker = writer.queue[proposal["Proposal ID"]]["Last Error"]
    assert isinstance(marker, str)
    assert marker.startswith(_PHASE4_APPLY_MARKER_PREFIX)
    assert json.loads(marker[len(_PHASE4_APPLY_MARKER_PREFIX) :])["phase"] == "effect_observed"

    second = _apply(reader, writer, drive, resolver, proposal)

    assert second.state is QueueState.APPLIED
    assert second.mutated is False
    assert writer.target_mutations == 1
