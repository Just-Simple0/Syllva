"""Integrated regressions for producer batch validation and persistence."""

from __future__ import annotations

import pathlib
import sys
from copy import deepcopy

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from phase4 import ready_phase4

from uls.adapters.drive.binding import SourceBindingRecord
from uls.domain.errors import SourcePartialError, SourceUnavailableError
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.proposal.material_usage import MaterialUsageProposalProducer


class Proposer:
    def __init__(self, candidate=None, callback=None):
        self.calls = 0
        self.candidate = candidate or {
            "operation": "create_usage", "material_id": "COMP319-M03", "role": "Supporting",
            "start_page": 2, "end_page": 2, "review_reason": "human review required",
        }
        self.callback = callback

    def propose_material_usage(self, **_kwargs):
        self.calls += 1
        if self.callback:
            self.callback()
        return [self.candidate]


def _producer(fixture, proposer):
    reader, writer, drive, resolver = fixture
    return MaterialUsageProposalProducer(reader, drive, writer, proposer, source_binding_resolver=resolver)


@pytest.mark.parametrize("bounds,valid", [
    ({}, False), ({"start_page": None}, False), ({"end_page": None}, False),
    ({"start_page": None, "end_page": None}, True),
    ({"range": {"start_page": None, "end_page": None}}, True),
])
def test_update_range_requires_explicit_bounds(bounds, valid) -> None:
    fixture = ready_phase4()
    proposer = Proposer({
        "operation": "update_range", "material_id": "COMP319-M03", "usage_id": "MU:existing",
        "role": "Primary", **bounds,
    })
    result = _producer(fixture, proposer).propose("COMP319-S05")
    assert len(result.proposals) == int(valid)
    assert fixture[1].create_calls == int(valid)
    assert not result.created_usage_ids


@pytest.mark.parametrize("by_ids", [False, True])
def test_explicit_batch_with_wrong_course_is_rejected_before_model(by_ids) -> None:
    fixture = ready_phase4()
    reader, writer, drive, _ = fixture
    extra = deepcopy(reader.materials["COMP319-M03"])
    extra["ID"] = "COMP319-M04"
    extra["Course"] = {"relation": [{"id": "other-course"}]}
    reader.materials["COMP319-M04"] = extra
    proposer = Proposer()
    kwargs = {"material_ids": list(reader.materials)} if by_ids else {"materials": list(reader.materials.values())}
    with pytest.raises(SourceUnavailableError):
        _producer(fixture, proposer).propose("COMP319-S05", **kwargs)
    assert proposer.calls == writer.create_calls == 0
    assert not drive.events


def test_explicit_missing_material_rejects_whole_batch() -> None:
    fixture = ready_phase4()
    proposer = Proposer()
    with pytest.raises(SourceUnavailableError):
        _producer(fixture, proposer).propose("COMP319-S05", material_ids=["COMP319-M03", "COMP319-M99"])
    assert proposer.calls == fixture[1].create_calls == 0
    assert not fixture[2].events


@pytest.mark.parametrize("entity", ["session", "material"])
def test_provider_id_cannot_replace_logical_id_before_source_resolution(entity) -> None:
    fixture = ready_phase4()
    reader, writer, drive, resolver = fixture
    record = reader.sessions["COMP319-S05"] if entity == "session" else reader.materials["COMP319-M03"]
    record["id"] = record.pop("ID")
    calls = []

    def trap(*args):
        calls.append(args)
        raise AssertionError("invalid identity reached binding")

    resolver.resolve_derivative_ref = trap
    proposer = Proposer()
    with pytest.raises(SourceUnavailableError):
        _producer(fixture, proposer).propose("COMP319-S05", material_ids=["COMP319-M03"])
    assert not calls and not drive.events
    assert proposer.calls == writer.create_calls == 0


@pytest.mark.parametrize("version,source_hash", [
    (True, "material-hash-v1"), (False, "material-hash-v1"), (1.0, "material-hash-v1"),
    (1, ""), (1, " "),
])
def test_malformed_typed_fingerprint_never_reaches_model(version, source_hash) -> None:
    fixture = ready_phase4()
    fixture[2].fingerprints["material-m03"] = SourceFingerprint(version, source_hash)
    proposer = Proposer()
    with pytest.raises(SourceUnavailableError):
        _producer(fixture, proposer).propose("COMP319-S05")
    assert proposer.calls == fixture[1].create_calls == 0


@pytest.mark.parametrize("tampered", [False, True])
def test_distinct_derivative_and_origin_identity_through_producer(tampered) -> None:
    fixture = ready_phase4()
    reader, writer, drive, _ = fixture
    reader.materials["COMP319-M03"]["Normalized Source"] = "normalized-m03"
    drive.derived["normalized-m03"] = drive.derived.pop("material-m03")
    if tampered:
        drive.derived["normalized-m03"] = drive.derived["normalized-m03"].replace("file_id: material-m03", "file_id: unrelated")
    drive.source_bindings.records = [r for r in drive.source_bindings.records if r.entity_id != "COMP319-M03"]
    drive.register_source_binding(SourceBindingRecord(
        "COMP319-M03", "normalized-m03", SourceRef("google_drive", "normalized-m03"),
        SourceRef("google_drive", "material-m03"),
    ))
    proposer = Proposer()
    if tampered:
        with pytest.raises((SourcePartialError, SourceUnavailableError)):
            _producer(fixture, proposer).propose("COMP319-S05")
        assert proposer.calls == writer.create_calls == 0
    else:
        result = _producer(fixture, proposer).propose("COMP319-S05")
        assert proposer.calls == 1
        assert len(result.proposals) == 1


@pytest.mark.parametrize("verified,count", [(False, 1), (True, 1), (False, 2)])
def test_exact_sibling_appearing_during_model_is_reconciled(verified, count) -> None:
    fixture = ready_phase4()
    reader, writer, *_ = fixture

    def insert():
        for index in range(count):
            reader.material_usage["COMP319-S05"].append({
                "ID": f"MU:user-{index}", "Session": "COMP319-S05", "Material": "COMP319-M03",
                "Role": "Supporting", "Start Page": 2, "End Page": 2, "Verified": verified,
            })

    result = _producer(fixture, Proposer(callback=insert)).propose("COMP319-S05")
    assert not result.created_usage_ids
    assert len(reader.material_usage["COMP319-S05"]) == 1 + count
    if count == 1 and not verified:
        assert len(result.proposals) == 1
        assert result.proposals[0]["Target Entity ID"] == "MU:user-0"
    else:
        assert not result.proposals
        assert writer.create_calls == 0


def test_queue_failure_reports_orphan_and_retry_reuses_same_usage(monkeypatch) -> None:
    import uls.proposal.material_usage as module

    fixture = ready_phase4()
    reader, writer, *_ = fixture
    real_upsert = module.upsert_proposal

    def unavailable(*_args, **_kwargs):
        raise TimeoutError("Queue create failed before commit")

    monkeypatch.setattr(module, "upsert_proposal", unavailable)
    first = _producer(fixture, Proposer()).propose("COMP319-S05")
    assert len(first.created_usage_ids) == len(first.retry_pending) == 1
    pending = first.retry_pending[0]
    assert pending["usage_id"] == first.created_usage_ids[0]
    assert pending["unverified_orphan_possible"] is True
    assert not writer.queue_rows
    orphan = reader.material_usage["COMP319-S05"][-1]
    assert orphan["Verified"] is False
    monkeypatch.setattr(module, "upsert_proposal", real_upsert)
    second = _producer(fixture, Proposer()).propose("COMP319-S05")
    assert not second.created_usage_ids and not second.retry_pending
    assert len(reader.material_usage["COMP319-S05"]) == 2
    assert len(writer.queue_rows) == 1
    assert second.proposals[0]["Proposal ID"] == pending["proposal_id"]
