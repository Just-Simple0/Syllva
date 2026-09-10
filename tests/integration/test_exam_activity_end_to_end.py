from __future__ import annotations

import pathlib
import sys
from datetime import UTC, datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from fake_drive import FakeDriveReader
from fake_notion import FakeNotionAdapter, FakeNotionReader
from phase4 import (
    COURSE_KEY,
    COURSE_PAGE_ID,
    activity_binding,
    activity_derivative,
    activity_record,
    exam_record,
)

from uls.adapters.drive.binding import ValidatedSourceBindingResolver
from uls.adapters.notion.base import HumanApprovalApplier, QueueState, upsert_proposal
from uls.config.schema import UlsConfig
from uls.domain.source_ref import SourceFingerprint
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.proposal.exam_scope import build_exam_scope_proposal
from uls.retrieval.engine import RetrievalEngine


def test_activity_derivative_provenance_and_exam_approval_flow_use_provider_neutral_boundaries() -> None:
    reader = FakeNotionReader(
        activities=[activity_record()],
        exams=[exam_record(included_sessions=[])],
    )
    drive = FakeDriveReader(
        derived={"activity-normalized-01": activity_derivative()},
        fingerprints={
            "activity-source-01": SourceFingerprint(1, "activity-source-v1"),
        },
        bindings=[activity_binding()],
    )
    resolver = ValidatedSourceBindingResolver(drive)
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=resolver,
    )

    activity_context = engine.get_activity_context("COMP319-A01")
    assert activity_context.sources[0].source_class == "official_activity"
    assert activity_context.sources[0].provenance.source_ref.identity == (
        "google_drive",
        "activity-source-01",
    )

    writer = FakeNotionAdapter(reader)
    proposal = build_exam_scope_proposal(
        "COMP319-E01",
        {"relation_page_id": COURSE_PAGE_ID, "course_key": COURSE_KEY},
        [],
        ["COMP319-S05"],
        review_reason="integration scope review",
    )
    upsert_proposal(writer, proposal)
    writer.queue[proposal.proposal_id].update(
        {"Decision": "Approve", "State": "APPROVED"}
    )

    result = HumanApprovalApplier(
        writer,
        decision_by="reviewer@example.edu",
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        graph_reader=reader,
        source_reader=drive,
        source_binding_resolver=resolver,
        config=UlsConfig(),
    ).apply(proposal.proposal_id)

    assert result.state is QueueState.APPLIED
    assert result.mutated is True
    assert reader.exams["COMP319-E01"]["Scope Confirmed"] is True
    assert reader.exams["COMP319-E01"]["Included Sessions"] == {
        "relation": [{"id": "COMP319-S05"}]
    }
    assert writer.target_mutations == 1
