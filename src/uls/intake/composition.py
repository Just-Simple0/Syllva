"""Opt-in C5 composition for the existing intake runner.

No credential resolution occurs here. Legacy-only configurations never import or
activate the range writer. Provider mapping/schema failures are explicit readiness
failures, not a fallback to an unrelated registry.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from time import sleep
from typing import Any

from uls.intake.coordinator import RequestBarrier, RequestCoordinator


def merge_request_handler(
    worker: Any, *, workspace: str, name: str, handler: Any,
    sources: dict[str, Any],
    after_publish: Callable[[RequestBarrier], Any] | None = None,
) -> RequestCoordinator:
    """Install an extension before ticking, preserving one workspace barrier.

    Each added source has its own complete-list reader. Existing provider sources
    and the approval callback retain their original bindings.
    """
    matches = [item for item in worker.request_coordinators if item.workspace == workspace]
    if len(matches) > 1:
        raise ValueError("multiple request coordinators for one workspace")
    readers = dict(sources)
    if not matches:
        coordinator = RequestCoordinator(
            workspace=workspace, source_ids=tuple(readers),
            list_records=lambda source: readers[source](source), handlers={name: handler},
            after_publish=after_publish,
        )
        worker.request_coordinators.append(coordinator)
        return coordinator
    existing = matches[0]
    if name in existing.handlers or set(sources).intersection(existing.source_ids):
        raise ValueError("duplicate request handler or source")
    old_listing = existing.list_records

    def listing(source: str) -> Any:
        return readers[source](source) if source in readers else old_listing(source)

    existing_after_publish = existing.after_publish
    if existing_after_publish is None:
        merged_after_publish = after_publish
    elif after_publish is None:
        merged_after_publish = existing_after_publish
    else:
        def merged_after_publish(barrier: RequestBarrier) -> dict[str, Any]:
            return {
                "existing": existing_after_publish(barrier),
                name: after_publish(barrier),
            }

    merged = RequestCoordinator(
        workspace=workspace, source_ids=(*existing.source_ids, *readers),
        list_records=listing, handlers={**existing.handlers, name: handler},
        clock=existing.clock, max_requests=existing.max_requests,
        after_publish=merged_after_publish,
    )
    worker.request_coordinators[worker.request_coordinators.index(existing)] = merged
    return merged


def install_usage_range(
    worker: Any, config: Any, state: Any, notion: Any, source_reader: Any,
) -> None:
    from uls.adapters.notion.intake import NotionIntakeWriter

    if isinstance(notion, NotionIntakeWriter):
        notion = notion.backend
    active_semesters = {item.semester for item in worker.workspaces}
    for workspace in config.notion.semester_workspaces:
        if workspace.semester not in active_semesters:
            continue
        ids = (workspace.material_usage_data_source_id, workspace.automation_queue_data_source_id)
        if not any(ids):
            continue
        name = workspace.semester
        if not all(isinstance(value, str) and value.strip() for value in ids):
            worker.request_extension_readiness[name] = {
                "status": "NOT_VERIFIED", "reason": "C5 mappings are incomplete",
            }
            continue
        if source_reader is None:
            worker.request_extension_readiness[name] = {
                "status": "NOT_VERIFIED", "reason": "C5 source reader is unavailable",
            }
            continue
        _install_workspace(worker, workspace, config, state, notion, source_reader)


def _install_workspace(
    worker: Any, workspace: Any, config: Any, state: Any, notion: Any, source_reader: Any,
) -> None:
    from uls.adapters.drive.binding import ValidatedSourceBindingResolver
    from uls.adapters.notion.base import ApprovalReader, HumanApprovalApplier
    from uls.adapters.notion.guarded import GuardedNotionWriter
    from uls.adapters.notion.usage_range import UsageRangeNotionBridge, UsageRangeSources
    from uls.intake.usage_range import UsageRangeHandler
    from uls.state.reader import ReadOnlyState

    sources = UsageRangeSources(
        workspace_id=workspace.semester,
        parent_page_id=workspace.connection_settings_files_parent_id,
        semester=workspace.semester,
        input_requests=workspace.input_requests_data_source_id,
        courses=workspace.academic_courses_data_source_id,
        sessions=workspace.sessions_data_source_id,
        materials=workspace.materials_data_source_id,
        material_usage=workspace.material_usage_data_source_id,
        automation_queue=workspace.automation_queue_data_source_id,
        file_intake=workspace.file_intake_data_source_id,
    )
    bridge = UsageRangeNotionBridge(notion, sources)
    readiness = bridge.validate_workspace()
    if not readiness.ready:
        worker.request_extension_readiness[workspace.semester] = {
            "status": "NOT_VERIFIED", "reason": "C5 workspace schema is not verified",
        }
        return
    graph = bridge.graph_view()
    writer = GuardedNotionWriter(
        bridge, database_ids={"Automation Queue": sources.automation_queue,
                              "Material Usage": sources.material_usage},
        automation_queue_id=sources.automation_queue,
    )
    binding = ValidatedSourceBindingResolver(ReadOnlyState(state.db_path))
    clock = lambda: datetime.now(UTC)
    handler = UsageRangeHandler(
        state=state, bridge=bridge, graph_reader=graph, writer=writer,
        source_reader=source_reader, source_binding_resolver=binding,
        config=config, clock=clock, sleep=sleep,
    )
    approval_reader = ApprovalReader(writer, automation_queue_id=sources.automation_queue)
    applier = HumanApprovalApplier(
        writer, usage_intent_state=state, request_freshness=handler,
        graph_reader=graph, source_reader=source_reader,
        source_binding_resolver=binding, config=config,
        automation_queue_id=sources.automation_queue, clock=clock,
    )

    def apply_approvals(barrier: RequestBarrier) -> dict[str, int]:
        from uls.domain.approval_identity import parse_usage_proposal_envelope

        applied = 0
        # Full enumeration precedes all approval effects. Per-dispatch request
        # checks inside HAA remain mandatory even with this successful barrier.
        rows = bridge.list_approval_rows()
        if barrier.workspace_blocked:
            return {"applied": 0}
        proposal_ids = [row.get("Proposal ID") for row in rows]
        if any(not isinstance(value, str) or not value for value in proposal_ids):
            raise ValueError("Queue proposal identity is missing")
        if len(set(proposal_ids)) != len(proposal_ids):
            raise ValueError("Queue proposal identity is ambiguous")
        for row in rows:
            if row.get("Proposal Type") not in {"MATERIAL_USAGE", "PAGE_RANGE"}:
                continue
            if row.get("State") not in {"PENDING_REVIEW", "APPROVED"}:
                continue
            envelope = parse_usage_proposal_envelope(row)
            # Unapplied legacy v1 rows require a new v2 request/human decision.
            # A v2 row whose exact slot was blocked by this intake barrier must
            # not even advance ApprovalReader state before the next complete
            # request snapshot.
            if envelope is None or envelope["usage_slot_key"] in barrier.blocked_slots:
                continue
            proposal_id = row.get("Proposal ID")
            if not isinstance(proposal_id, str) or not proposal_id:
                raise ValueError("Queue proposal identity is missing")
            current = approval_reader.sync_state(proposal_id)
            if str(current) == "APPROVED":
                outcome = applier.apply(proposal_id)
                if str(outcome.state) == "APPLIED":
                    applied += 1
        return {"applied": applied}

    merge_request_handler(
        worker, workspace=sources.workspace_id, name="usage_range", handler=handler,
        sources={sources.input_requests: bridge.list_rows}, after_publish=apply_approvals,
    )
    worker.request_extension_readiness[workspace.semester] = {
        "status": "READY", "profile": "c5-range-v1",
    }
