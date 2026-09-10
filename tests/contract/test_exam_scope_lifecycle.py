from __future__ import annotations

import json
import pathlib
import sys
from datetime import UTC, datetime

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from fake_drive import FakeDriveReader
from phase4 import (
    COURSE_KEY,
    COURSE_PAGE_ID,
    FakeNotionAdapter,
    FakeNotionReader,
    exam_record,
)

from uls.adapters.drive.binding import ValidatedSourceBindingResolver
from uls.adapters.notion.base import (
    _PHASE4_APPLY_MARKER_PREFIX,
    HumanApprovalApplier,
    QueueState,
    enforce_write_policy,
    upsert_proposal,
)
from uls.config.schema import UlsConfig
from uls.domain.academic import ExamRecord
from uls.domain.course_identity import validate_course_record
from uls.domain.enums import AutomationActor
from uls.domain.errors import PolicyViolation
from uls.proposal.exam_scope import build_exam_scope_proposal

COURSE = {"relation_page_id": COURSE_PAGE_ID, "course_key": COURSE_KEY}


def _session(session_id: str) -> dict[str, object]:
    return {
        "ID": session_id,
        "Name": session_id,
        "Course": {"relation": [{"id": COURSE_PAGE_ID}]},
        "Session No": int(session_id[-2:]),
        "Normalized Transcript": f"{session_id}-transcript",
    }


def _setup(
    *,
    old_sessions: list[str] | None = None,
    desired_sessions: list[str] | None = None,
    old_confirmed: bool = False,
    writer_type=FakeNotionAdapter,
):
    old = [] if old_sessions is None else old_sessions
    desired = ["COMP319-S01"] if desired_sessions is None else desired_sessions
    sessions = [_session(value) for value in {*(old or ()), *(desired or ())}]
    reader = FakeNotionReader(
        sessions=sessions,
        exams=[
            exam_record(
                included_sessions=old,
                scope_confirmed=old_confirmed,
            )
        ],
    )
    drive = FakeDriveReader()
    writer = writer_type(reader)
    proposal = build_exam_scope_proposal(
        "COMP319-E01",
        COURSE,
        old,
        desired,
        old_scope_confirmed=old_confirmed,
        review_reason="human scope review",
    )
    upsert_proposal(writer, proposal)
    writer.queue[proposal.proposal_id].update(
        {"Decision": "Approve", "State": "APPROVED"}
    )
    return reader, writer, drive, ValidatedSourceBindingResolver(drive), proposal


def _apply(reader, writer, drive, resolver, proposal):
    return HumanApprovalApplier(
        writer,
        decision_by="reviewer@example.edu",
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        graph_reader=reader,
        source_reader=drive,
        source_binding_resolver=resolver,
        config=UlsConfig(),
    ).apply(proposal.proposal_id)


def _typed_exam_from_current(reader, exam_id: str, *, entity_id: str | None = None, course=None) -> ExamRecord:
    raw = reader.exams[exam_id]
    course_relation_id = raw["Course"]["relation"][0]["id"]
    typed_course = course or validate_course_record(
        reader.get_course_by_relation_id(course_relation_id), course_relation_id
    )
    assert typed_course is not None
    included_property = raw.get("Included Sessions")
    included = None if included_property is None else tuple(
        item["id"] for item in included_property["relation"]
    )
    return ExamRecord(
        entity_id or raw["ID"],
        typed_course,
        included,
        raw["Scope Confirmed"],
    )


def _install_dynamic_typed_exam_reader(reader, *, entity_id: str | None = None) -> None:
    def get_exam(exam_id: str) -> ExamRecord:
        raw_id = reader.exams[exam_id]["ID"]
        return _typed_exam_from_current(reader, exam_id, entity_id=entity_id or raw_id)

    reader.get_exam = get_exam


def test_ordinary_automation_cannot_write_exam_scope_even_when_clearing_it() -> None:
    with pytest.raises(PolicyViolation):
        enforce_write_policy(
            AutomationActor.AUTOMATION,
            "Exams",
            {"Scope Confirmed": True},
        )
    with pytest.raises(PolicyViolation):
        enforce_write_policy(
            AutomationActor.AUTOMATION,
            "Exams",
            {"Included Sessions": {"relation": []}},
        )


def test_nonempty_apply_uses_boundary_identity_and_replay_is_idempotent() -> None:
    reader, writer, drive, resolver, proposal = _setup()

    first = _apply(reader, writer, drive, resolver, proposal)
    second = _apply(reader, writer, drive, resolver, proposal)

    assert first.state is QueueState.APPLIED
    assert first.mutated is True
    assert second.state is QueueState.APPLIED
    assert second.mutated is False
    assert writer.target_mutations == 1
    assert reader.exams["COMP319-E01"]["Scope Confirmed"] is True
    assert reader.exams["COMP319-E01"]["Included Sessions"] == {
        "relation": [{"id": "COMP319-S01"}]
    }
    row = writer.queue[proposal.proposal_id]
    assert row["Decision By"] == "reviewer@example.edu"
    assert row["State"] == "APPLIED"
    assert row.get("Last Error") is None


def test_session_relation_reordering_does_not_make_an_unchanged_scope_stale() -> None:
    reader, writer, drive, resolver, proposal = _setup(
        old_sessions=["COMP319-S02", "COMP319-S01"],
        desired_sessions=["COMP319-S01", "COMP319-S02"],
    )

    result = _apply(reader, writer, drive, resolver, proposal)

    assert result.state is QueueState.APPLIED
    assert writer.target_mutations == 1


def test_human_approved_empty_scope_is_a_confirmed_empty_context_and_replay_is_safe() -> None:
    reader, writer, drive, resolver, proposal = _setup(
        old_sessions=["COMP319-S01"], desired_sessions=[],
    )

    first = _apply(reader, writer, drive, resolver, proposal)
    second = _apply(reader, writer, drive, resolver, proposal)

    assert first.state is QueueState.APPLIED
    assert second.state is QueueState.APPLIED
    assert writer.target_mutations == 1
    assert reader.exams["COMP319-E01"]["Included Sessions"] == {"relation": []}
    assert reader.exams["COMP319-E01"]["Scope Confirmed"] is True


class _RevokeAfterPrepared(FakeNotionAdapter):
    def __init__(self, reader):
        super().__init__(reader)
        self.done = False

    def find_approval_rows(self, proposal_id):
        rows = super().find_approval_rows(proposal_id)
        if rows and not self.done and "phase4_apply_marker" in str(rows[0].get("Last Error")).casefold():
            rows[0].update({"Decision": "Pending", "State": "PENDING_REVIEW"})
            self.done = True
        return rows


class _DuplicateAfterPrepared(FakeNotionAdapter):
    def __init__(self, reader):
        super().__init__(reader)
        self.done = False

    def find_approval_rows(self, proposal_id):
        rows = super().find_approval_rows(proposal_id)
        if rows and not self.done and "phase4_apply_marker" in str(rows[0].get("Last Error")).casefold():
            duplicate = dict(rows[0])
            duplicate["record_id"] = "queue-duplicate"
            self.queue_rows.append(duplicate)
            self.done = True
        return rows


class _RevokeDuringLastDependencyRead(FakeNotionAdapter):
    """Change approval during the final dependency read before the write."""

    def __init__(self, reader):
        super().__init__(reader)
        self.done = False
        real_get_session = reader.get_session

        def get_session(session_id):
            result = real_get_session(session_id)
            marker_visible = any(
                isinstance(row.get("Last Error"), str)
                and "phase4_apply_marker" in row["Last Error"].casefold()
                for row in self.queue_rows
            )
            if marker_visible and not self.done:
                row = self.queue_rows[0]
                row.update({"Decision": "Pending", "State": "PENDING_REVIEW"})
                self.done = True
            return result

        reader.get_session = get_session


class _DuplicateDuringLastDependencyRead(FakeNotionAdapter):
    """Insert a second physical Queue row during the final dependency read."""

    def __init__(self, reader):
        super().__init__(reader)
        self.done = False
        real_get_session = reader.get_session

        def get_session(session_id):
            result = real_get_session(session_id)
            marker_visible = any(
                isinstance(row.get("Last Error"), str)
                and "phase4_apply_marker" in row["Last Error"].casefold()
                for row in self.queue_rows
            )
            if marker_visible and not self.done:
                duplicate = dict(self.queue_rows[0])
                duplicate["record_id"] = "queue-late-duplicate"
                self.queue_rows.append(duplicate)
                self.done = True
            return result

        reader.get_session = get_session


@pytest.mark.parametrize("writer_type", [_RevokeAfterPrepared, _DuplicateAfterPrepared])
def test_queue_revocation_or_duplicate_after_prepared_causes_zero_target_mutation(writer_type) -> None:
    reader, writer, drive, resolver, proposal = _setup(writer_type=writer_type)

    if writer_type is _DuplicateAfterPrepared:
        with pytest.raises(PolicyViolation):
            _apply(reader, writer, drive, resolver, proposal)
    else:
        result = _apply(reader, writer, drive, resolver, proposal)
        assert result.state is QueueState.APPROVED
    assert writer.target_mutations == 0
    assert reader.exams["COMP319-E01"]["Scope Confirmed"] is False


@pytest.mark.parametrize(
    "writer_type",
    [_RevokeDuringLastDependencyRead, _DuplicateDuringLastDependencyRead],
)
def test_last_dependency_read_revocation_or_duplicate_is_seen_before_target_write(writer_type) -> None:
    reader, writer, drive, resolver, proposal = _setup(writer_type=writer_type)

    with pytest.raises(PolicyViolation):
        _apply(reader, writer, drive, resolver, proposal)

    assert writer.target_mutations == 0
    assert reader.exams["COMP319-E01"]["Scope Confirmed"] is False


@pytest.mark.parametrize("record_kind", ["exam", "session"])
@pytest.mark.parametrize(
    "course_relation",
    [
        {
            "id": "course-property",
            "type": "relation",
            "relation": [{"id": COURSE_PAGE_ID}],
            "has_more": True,
        },
        {
            "id": "course-property",
            "type": "relation",
            "relation": [{"id": COURSE_PAGE_ID}, {"id": "course-page-2"}],
            "has_more": False,
        },
    ],
)
def test_malformed_or_truncated_course_relation_stops_exam_apply_before_mutation(
    record_kind, course_relation
) -> None:
    reader, writer, drive, resolver, proposal = _setup()
    if record_kind == "exam":
        reader.exams["COMP319-E01"]["Course"] = course_relation
    else:
        reader.sessions["COMP319-S01"]["Course"] = course_relation

    result = _apply(reader, writer, drive, resolver, proposal)

    assert result.state is QueueState.APPROVED
    assert writer.target_mutations == 0
    assert reader.exams["COMP319-E01"]["Scope Confirmed"] is False


def test_typed_exam_graph_reader_reconciles_current_scope_and_applies_once() -> None:
    reader, writer, drive, resolver, proposal = _setup()
    _install_dynamic_typed_exam_reader(reader)

    first = _apply(reader, writer, drive, resolver, proposal)
    second = _apply(reader, writer, drive, resolver, proposal)

    assert first.state is QueueState.APPLIED
    assert second.state is QueueState.APPLIED
    assert second.mutated is False
    assert writer.target_mutations == 1


def test_typed_exam_id_mismatch_is_rejected_before_target_mutation() -> None:
    reader, writer, drive, resolver, proposal = _setup()
    _install_dynamic_typed_exam_reader(reader, entity_id="COMP319-E99")

    result = _apply(reader, writer, drive, resolver, proposal)

    assert result.state is QueueState.APPROVED
    assert writer.target_mutations == 0
    assert reader.exams["COMP319-E01"]["Scope Confirmed"] is False


def test_typed_exam_course_snapshot_must_match_the_current_course_row() -> None:
    reader, writer, drive, resolver, proposal = _setup()
    typed = _typed_exam_from_current(reader, "COMP319-E01")
    reader.get_exam = lambda _exam_id: typed
    reader.courses[COURSE_PAGE_ID]["Course Key"] = "2026-1_COMP319-003"
    reader.courses[COURSE_PAGE_ID]["Section"] = "003"

    result = _apply(reader, writer, drive, resolver, proposal)

    assert result.state is QueueState.APPROVED
    assert writer.target_mutations == 0
    assert reader.exams["COMP319-E01"]["Scope Confirmed"] is False


def test_typed_exam_scope_confirmed_must_match_the_current_target_snapshot() -> None:
    reader, writer, drive, resolver, proposal = _setup()
    typed = _typed_exam_from_current(reader, "COMP319-E01")
    reader.get_exam = lambda _exam_id: ExamRecord(
        typed.entity_id,
        typed.course,
        typed.included_session_ids,
        True,
    )

    result = _apply(reader, writer, drive, resolver, proposal)

    assert result.state is QueueState.APPROVED
    assert writer.target_mutations == 0
    assert reader.exams["COMP319-E01"]["Scope Confirmed"] is False


def test_frozen_old_typed_exam_snapshot_cannot_become_applied_after_target_write() -> None:
    reader, writer, drive, resolver, proposal = _setup()
    typed = _typed_exam_from_current(reader, "COMP319-E01")
    reader.get_exam = lambda _exam_id: typed

    result = _apply(reader, writer, drive, resolver, proposal)

    assert result.state is QueueState.APPROVED
    assert writer.target_mutations == 1
    assert reader.exams["COMP319-E01"]["Scope Confirmed"] is True
    assert writer.queue[proposal.proposal_id]["State"] == QueueState.APPROVED.value


def test_typed_exam_audit_recovery_reuses_dynamic_post_write_snapshot() -> None:
    reader, writer, drive, resolver, proposal = _setup()
    _install_dynamic_typed_exam_reader(reader)
    real_update = writer.update_properties
    partial = True

    def update(target_db, entity_id, patch, **kwargs):
        nonlocal partial
        logical, _ = writer._target(target_db)
        if partial and logical == "automationqueue" and patch.get("State") == "APPLIED":
            partial = False
            row = writer.queue[proposal.proposal_id]
            row["State"] = "APPLIED"
            raise TimeoutError("audit committed State without audit fields")
        return real_update(target_db, entity_id, patch, **kwargs)

    writer.update_properties = update
    first = _apply(reader, writer, drive, resolver, proposal)
    second = _apply(reader, writer, drive, resolver, proposal)

    assert first.state is QueueState.APPROVED
    assert second.state is QueueState.APPLIED
    assert second.mutated is False
    assert writer.target_mutations == 1


def test_live_session_course_must_match_the_exam_parent_course() -> None:
    reader, writer, drive, resolver, proposal = _setup()
    reader.courses["course-page-2"] = {
        "id": "course-page-2",
        "Course Key": "2026-1_COMP319-003",
        "Code": "COMP319",
        "Section": "003",
        "Semester": "2026-1",
    }
    reader.sessions["COMP319-S01"]["Course"] = {
        "relation": [{"id": "course-page-2"}]
    }

    result = _apply(reader, writer, drive, resolver, proposal)

    assert result.state is QueueState.APPROVED
    assert writer.target_mutations == 0
    assert reader.exams["COMP319-E01"]["Scope Confirmed"] is False


def test_stored_cross_course_dependency_is_rejected_before_exam_mutation() -> None:
    reader, writer, drive, resolver, proposal = _setup()
    row = writer.queue[proposal.proposal_id]
    action = json.loads(row["Proposed Action"])
    action["session_dependencies"][0]["course_relation_page_id"] = "course-page-2"
    action["session_dependencies"][0]["course_key"] = "2026-1_COMP319-003"
    row["Proposed Action"] = json.dumps(action)

    with pytest.raises(PolicyViolation):
        _apply(reader, writer, drive, resolver, proposal)

    assert writer.target_mutations == 0
    assert reader.exams["COMP319-E01"]["Scope Confirmed"] is False


def test_exam_current_queue_row_owns_lifecycle_and_audit_aliases() -> None:
    reader, writer, drive, resolver, proposal = _setup()
    applier = HumanApprovalApplier(
        writer,
        decision_by="reviewer@example.edu",
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        graph_reader=reader,
        source_reader=drive,
        source_binding_resolver=resolver,
        config=UlsConfig(),
    )

    _proposal_id, current = applier._read_current(
        {
            "Proposal ID": proposal.proposal_id,
            "state": "PENDING_REVIEW",
            "decision": "Pending",
            "last_error": "caller spoof",
            "decision_by": "caller spoof",
            "decision_at": "caller spoof",
            "applied_at": "caller spoof",
        }
    )

    assert current is writer.queue[proposal.proposal_id]
    assert current["State"] == QueueState.APPROVED.value
    assert current["Decision"] == "Approve"
    assert "caller spoof" not in str(current)


def test_incomplete_applied_exam_audit_is_repaired_without_target_replay() -> None:
    reader, writer, drive, resolver, proposal = _setup()
    real_update = writer.update_properties
    partial = True

    def update(target_db, entity_id, patch, **kwargs):
        nonlocal partial
        logical, _ = writer._target(target_db)
        if partial and logical == "automationqueue" and patch.get("State") == "APPLIED":
            partial = False
            row = writer.queue[proposal.proposal_id]
            row["State"] = "APPLIED"
            raise TimeoutError("audit committed State without audit fields")
        return real_update(target_db, entity_id, patch, **kwargs)

    writer.update_properties = update
    first = _apply(reader, writer, drive, resolver, proposal)

    assert first.state is QueueState.APPROVED
    assert writer.target_mutations == 1
    row = writer.queue[proposal.proposal_id]
    assert row["State"] == QueueState.APPLIED.value
    assert "Decision At" not in row
    assert "Applied At" not in row

    second = _apply(reader, writer, drive, resolver, proposal)

    assert second.state is QueueState.APPLIED
    assert second.mutated is False
    assert writer.target_mutations == 1
    assert isinstance(row["Decision By"], str)
    assert isinstance(row["Decision At"], str)
    assert isinstance(row["Applied At"], str)


@pytest.mark.parametrize(
    "relation",
    [
        {"not_a_relation": True},
        {"relation": [{"id": "COMP319-S01"}, {"id": "COMP319-S01"}]},
        {"relation": [{"id": "COMP319-M03"}]},
        {
            "id": "included-sessions-property",
            "type": "relation",
            "relation": [{"id": "COMP319-S01"}],
            "has_more": True,
        },
    ],
)
def test_malformed_exam_target_relation_is_rejected_before_any_target_write(relation) -> None:
    reader, writer, drive, resolver, proposal = _setup(
        old_sessions=[], desired_sessions=["COMP319-S01"]
    )
    reader.exams["COMP319-E01"]["Included Sessions"] = relation

    result = _apply(reader, writer, drive, resolver, proposal)

    assert result.state is QueueState.SUPERSEDED
    assert writer.target_mutations == 0
    assert reader.exams["COMP319-E01"]["Scope Confirmed"] is False
    assert reader.exams["COMP319-E01"]["Included Sessions"] == relation


def test_absent_exam_target_relation_does_not_match_an_approved_explicit_empty_scope() -> None:
    reader, writer, drive, resolver, proposal = _setup(
        old_sessions=[], desired_sessions=["COMP319-S01"]
    )
    reader.exams["COMP319-E01"].pop("Included Sessions")

    result = _apply(reader, writer, drive, resolver, proposal)

    assert result.state is QueueState.SUPERSEDED
    assert writer.target_mutations == 0
    assert "Included Sessions" not in reader.exams["COMP319-E01"]


def test_malformed_relation_after_target_write_stays_prepared_for_reconciliation() -> None:
    reader, writer, drive, resolver, proposal = _setup()
    real_update = writer.update_properties

    def update(target_db, entity_id, patch, **kwargs):
        result = real_update(target_db, entity_id, patch, **kwargs)
        if writer._target(target_db)[0] == "exams":
            reader.exams["COMP319-E01"]["Included Sessions"] = {"not_a_relation": True}
        return result

    writer.update_properties = update
    first = _apply(reader, writer, drive, resolver, proposal)

    assert first.state is QueueState.APPROVED
    assert writer.target_mutations == 1
    assert writer.queue[proposal.proposal_id]["State"] == QueueState.APPROVED.value
    assert str(writer.queue[proposal.proposal_id]["Last Error"]).startswith(
        _PHASE4_APPLY_MARKER_PREFIX
    )

    second = _apply(reader, writer, drive, resolver, proposal)
    assert second.state is QueueState.SUPERSEDED
    assert writer.target_mutations == 1


def test_malformed_relation_during_effect_observed_recovery_never_becomes_applied() -> None:
    reader, writer, drive, resolver, proposal = _setup()
    applier = HumanApprovalApplier(
        writer,
        decision_by="reviewer@example.edu",
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        graph_reader=reader,
        source_reader=drive,
        source_binding_resolver=resolver,
        config=UlsConfig(),
    )
    real_mark = applier._phase4_mark_effect_observed

    def mark_effect(*args, **kwargs):
        result = real_mark(*args, **kwargs)
        reader.exams["COMP319-E01"]["Included Sessions"] = {"not_a_relation": True}
        return result

    applier._phase4_mark_effect_observed = mark_effect
    first = applier.apply(proposal.proposal_id)

    assert first.state is QueueState.APPROVED
    assert writer.target_mutations == 1
    assert writer.queue[proposal.proposal_id]["State"] == QueueState.APPROVED.value

    second = _apply(reader, writer, drive, resolver, proposal)
    assert second.state is QueueState.SUPERSEDED
    assert writer.target_mutations == 1
    assert writer.queue[proposal.proposal_id]["State"] == QueueState.SUPERSEDED.value
    assert "Applied At" not in writer.queue[proposal.proposal_id]


def test_course_dependency_change_after_marker_stops_before_exam_mutation() -> None:
    reader, writer, drive, resolver, proposal = _setup()
    real_find = writer.find_approval_rows
    changed = False

    def find(proposal_id):
        nonlocal changed
        rows = real_find(proposal_id)
        if rows and not changed and "phase4_apply_marker" in str(rows[0].get("Last Error")).casefold():
            reader.courses[COURSE_PAGE_ID]["Course Key"] = "2026-1_COMP319-003"
            reader.courses[COURSE_PAGE_ID]["Section"] = "003"
            changed = True
        return rows

    writer.find_approval_rows = find
    result = _apply(reader, writer, drive, resolver, proposal)

    assert result.state is QueueState.APPROVED
    assert writer.target_mutations == 0
    assert reader.exams["COMP319-E01"]["Scope Confirmed"] is False


def test_committed_write_then_raise_keeps_prepared_and_later_desired_is_reconciliation_only() -> None:
    reader, writer, drive, resolver, proposal = _setup()
    writer.mutate_then_raise_target = True

    first = _apply(reader, writer, drive, resolver, proposal)
    second = _apply(reader, writer, drive, resolver, proposal)

    assert first.state is QueueState.APPROVED
    assert first.mutated is False
    assert second.state is QueueState.APPROVED
    assert second.mutated is False
    assert writer.target_mutations == 1
    assert writer.queue[proposal.proposal_id]["State"] == "APPROVED"
    assert "prepared" in str(writer.queue[proposal.proposal_id]["Last Error"])
    assert "Applied At" not in writer.queue[proposal.proposal_id]


def test_explicit_no_effect_clears_prepared_and_allows_one_retry() -> None:
    reader, writer, drive, resolver, proposal = _setup()
    writer.raise_before_target = True

    first = _apply(reader, writer, drive, resolver, proposal)
    second = _apply(reader, writer, drive, resolver, proposal)

    assert first.state is QueueState.APPROVED
    assert first.mutated is False
    assert second.state is QueueState.APPLIED
    assert second.mutated is True
    assert writer.target_mutations == 1
