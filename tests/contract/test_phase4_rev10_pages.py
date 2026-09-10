"""Permanent Phase 4 rev10 page-range coverage regressions."""

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


def _pages_body(pages: list[int]) -> str:
    header = material_derivative().split("Page 1\n", 1)[0]
    return header + "".join(
        f"Page {page}\nMaterial evidence for page {page}.\n" for page in pages
    )


def _approved_page_range(
    operation: str,
    desired_range: PageRange,
    *,
    body_pages: list[int],
    graph_page_count: int = 41,
):
    reader, writer, drive, resolver = ready_phase4()
    target = reader.material_usage["COMP319-S05"][0]
    if operation == "create_usage":
        target.update(
            {
                "Start Page": desired_range.start_page,
                "End Page": desired_range.end_page,
            }
        )
    drive.derived["material-m03"] = _pages_body(body_pages)
    reader.materials["COMP319-M03"]["Page Count"] = graph_page_count
    proposal = phase4_proposal(
        reader,
        operation=operation,
        desired_range=desired_range,
    )
    upsert_proposal(writer, proposal)
    writer.queue[proposal["Proposal ID"]].update(
        {"Decision": "Approve", "State": QueueState.APPROVED.value}
    )
    return reader, writer, drive, resolver, proposal


def _marker_phase(writer, proposal) -> str:
    value = writer.queue[proposal["Proposal ID"]].get("Last Error")
    assert isinstance(value, str)
    assert value.startswith(_PHASE4_APPLY_MARKER_PREFIX)
    return json.loads(value[len(_PHASE4_APPLY_MARKER_PREFIX) :])["phase"]


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
@pytest.mark.parametrize(
    "body_pages",
    [
        list(range(1, 41)),
        [*range(1, 39), 40, 41],
        [*range(1, 40), 41],
    ],
    ids=["missing-tail", "missing-head", "gap"],
)
def test_multi_page_range_requires_every_current_page(
    operation: str,
    body_pages: list[int],
) -> None:
    reader, writer, drive, resolver, proposal = _approved_page_range(
        operation,
        PageRange(39, 41),
        body_pages=body_pages,
    )

    result = _apply(reader, writer, drive, resolver, proposal)

    assert result.state is QueueState.SUPERSEDED
    assert result.mutated is False
    assert writer.target_mutations == 0
    row = writer.queue[proposal["Proposal ID"]]
    assert row["State"] == QueueState.SUPERSEDED.value
    assert "Applied At" not in row
    target = reader.material_usage["COMP319-S05"][0]
    if operation == "create_usage":
        assert target["Start Page"] == 39
        assert target["End Page"] == 41
        assert target["Verified"] is False
    else:
        assert target["Start Page"] == 1
        assert target["End Page"] == 2
        assert target["Verified"] is False


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_page41_remains_denied_when_graph_page_count_overstates_body(
    operation: str,
) -> None:
    reader, writer, drive, resolver, proposal = _approved_page_range(
        operation,
        PageRange(41, 41),
        body_pages=list(range(1, 41)),
    )

    result = _apply(reader, writer, drive, resolver, proposal)

    assert result.state is QueueState.SUPERSEDED
    assert result.mutated is False
    assert writer.target_mutations == 0
    assert writer.queue[proposal["Proposal ID"]]["State"] == QueueState.SUPERSEDED.value


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
@pytest.mark.parametrize(
    "desired_range",
    [PageRange(39, 40), PageRange(40, 40)],
    ids=["pages-39-40", "page-40"],
)
def test_valid_long_page_range_applies_once(
    operation: str,
    desired_range: PageRange,
) -> None:
    reader, writer, drive, resolver, proposal = _approved_page_range(
        operation,
        desired_range,
        body_pages=list(range(1, 41)),
    )

    first = _apply(reader, writer, drive, resolver, proposal)
    second = _apply(reader, writer, drive, resolver, proposal)

    assert first.state is QueueState.APPLIED
    assert first.mutated is True
    assert second.state is QueueState.APPLIED
    assert second.mutated is False
    assert writer.target_mutations == 1
    row = writer.queue[proposal["Proposal ID"]]
    assert row["State"] == QueueState.APPLIED.value
    assert row.get("Last Error") is None
    target = reader.material_usage["COMP319-S05"][0]
    if operation == "create_usage":
        assert target["Verified"] is True
    else:
        assert target["Start Page"] == desired_range.start_page
        assert target["End Page"] == desired_range.end_page
        assert target["Verified"] is False


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_stale_multi_page_audit_recovery_keeps_effect_marker(
    operation: str,
) -> None:
    reader, writer, drive, resolver, proposal = _approved_page_range(
        operation,
        PageRange(39, 40),
        body_pages=list(range(1, 41)),
    )
    writer.fail_audit_once = True

    first = _apply(reader, writer, drive, resolver, proposal)

    assert first.state is QueueState.APPROVED
    assert first.mutated is True
    assert writer.target_mutations == 1
    assert _marker_phase(writer, proposal) == "effect_observed"

    original_fingerprint = drive.fingerprints["material-m03"]
    drive.derived["material-m03"] = _pages_body(list(range(1, 40)))
    assert drive.fingerprints["material-m03"] == original_fingerprint

    second = _apply(reader, writer, drive, resolver, proposal)

    assert second.state is QueueState.APPROVED
    assert second.mutated is False
    assert writer.target_mutations == 1
    row = writer.queue[proposal["Proposal ID"]]
    assert row["State"] == QueueState.APPROVED.value
    assert "Applied At" not in row
    assert _marker_phase(writer, proposal) == "effect_observed"
