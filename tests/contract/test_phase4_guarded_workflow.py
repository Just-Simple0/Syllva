"""Exercise the real producer and approval services through the guarded writer."""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))
from phase4 import phase4_applier_kwargs, ready_phase4

from uls.adapters.notion.base import (
    ApprovalReader,
    Decision,
    HumanApprovalApplier,
    QueueState,
)
from uls.adapters.notion.guarded import GuardedNotionWriter
from uls.config.schema import UlsConfig
from uls.domain.errors import LocatorNotAllowedError
from uls.domain.models import serialize_locator
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.proposal.material_usage import MaterialUsageProposalProducer
from uls.retrieval.engine import RetrievalEngine


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
@pytest.mark.parametrize("reuse", [False, True])
def test_guarded_producer_approval_and_replay(operation, reuse):
    reader, backend, drive, resolver = ready_phase4()
    if reuse and operation == "create_usage":
        reader.get_material_usage("COMP319-S05")[0]["Start Page"] = 2
    if reuse and operation == "update_range":
        reader.get_material_usage("COMP319-S05")[0]["Verified"] = True

    class Proposer:
        def propose_material_usage(self, **kwargs):
            del kwargs
            return [{
                "operation": operation,
                "material_id": "COMP319-M03",
                "usage_id": "MU:existing" if operation == "update_range" else None,
                "role": "Primary", "start_page": 2, "end_page": 2,
                "confidence": 0.9,
            }]

    writer = GuardedNotionWriter(backend)
    producer = MaterialUsageProposalProducer(
        reader, drive, writer, Proposer(),
        source_binding_resolver=resolver, config=UlsConfig(),
    )
    result = producer.propose("COMP319-S05")
    assert len(result.proposals) == 1
    pid = next(iter(backend.queue))
    target_id = backend.queue[pid]["Target Entity ID"]
    usages = reader.get_material_usage("COMP319-S05")
    target = next(row for row in usages if row["ID"] == target_id)
    assert target["Verified"] is (reuse and operation == "update_range")
    assert len(usages) == (2 if operation == "create_usage" and not reuse else 1)
    retry = producer.propose("COMP319-S05")
    assert len(retry.proposals) == 1
    assert len(backend.queue_rows) == 1
    assert len(reader.get_material_usage("COMP319-S05")) == len(usages)
    backend.queue[pid]["Decision"] = "Approve"
    ApprovalReader(writer).sync_state(pid)
    applier = HumanApprovalApplier(
        writer, decision_by="reviewer", **phase4_applier_kwargs(reader, drive, resolver),
    )
    applied = applier.apply(pid)
    assert applied.state is QueueState.APPLIED
    assert target["Start Page"] == target["End Page"] == 2
    assert target["Verified"] is (operation == "create_usage" or reuse)
    writes = backend.update_calls
    replay = applier.apply(pid)
    assert replay.state is QueueState.APPLIED
    assert replay.mutated is False
    assert backend.update_calls == writes


def test_guarded_approval_refreshes_same_engine_capability() -> None:
    reader, backend, drive, resolver = ready_phase4()
    writer = GuardedNotionWriter(backend)
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=resolver,
    )

    before = engine.get_session_context("COMP319-S05")
    assert not any(item.entity_id == "COMP319-M03" for item in before.sources)

    class Proposer:
        def propose_material_usage(self, **kwargs):
            del kwargs
            return [{
                "operation": "create_usage",
                "material_id": "COMP319-M03",
                "role": "Primary",
                "start_page": 1,
                "end_page": 2,
                "confidence": 0.9,
            }]

    producer = MaterialUsageProposalProducer(
        reader,
        drive,
        writer,
        Proposer(),
        source_binding_resolver=resolver,
        config=UlsConfig(),
    )
    result = producer.propose("COMP319-S05")
    assert len(result.proposals) == 1
    pid = next(iter(backend.queue))
    queue = backend.queue[pid]
    assert queue["State"] == QueueState.PENDING_REVIEW.value
    assert queue["Decision"] == Decision.Pending.value

    target_id = queue["Target Entity ID"]
    target = next(
        row
        for row in reader.get_material_usage("COMP319-S05")
        if row["ID"] == target_id
    )
    assert target["Verified"] is False

    queue["Decision"] = Decision.Approve.value
    ApprovalReader(writer).sync_state(pid)
    applier = HumanApprovalApplier(
        writer,
        decision_by="reviewer",
        **phase4_applier_kwargs(reader, drive, resolver),
    )
    applied = applier.apply(pid)
    assert applied.state is QueueState.APPLIED
    assert target["Verified"] is True

    after = engine.get_session_context("COMP319-S05")
    material_sources = [
        item for item in after.sources if item.entity_id == "COMP319-M03"
    ]
    assert [serialize_locator(item.locator) for item in material_sources] == [
        "COMP319-M03:p1",
        "COMP319-M03:p2",
    ]
    locator = serialize_locator(material_sources[0].locator)
    assert engine.get_source_chunk(after.context_id, locator).entity_id == "COMP319-M03"

    usage_count = len(reader.get_material_usage("COMP319-S05"))
    writes = backend.update_calls
    replay = applier.apply(pid)
    assert replay.state is QueueState.APPLIED
    assert replay.mutated is False
    assert backend.update_calls == writes
    assert len(reader.get_material_usage("COMP319-S05")) == usage_count

    target["Verified"] = False
    with pytest.raises(LocatorNotAllowedError):
        engine.get_source_chunk(after.context_id, locator)
    revoked = engine.get_session_context("COMP319-S05")
    assert not any(item.entity_id == "COMP319-M03" for item in revoked.sources)
