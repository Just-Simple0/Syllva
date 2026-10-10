"""Composition acceptance for the opt-in range extension."""
from types import SimpleNamespace
from uuid import UUID

from uls.adapters.notion.intake import InMemoryNotionWorker
from uls.config.schema import SemesterWorkspaceCfg, UlsConfig
from uls.intake.composition import install_usage_range
from uls.intake.coordinator import RequestCoordinator
from uls.state.sqlite import SQLiteStateStore


def test_ready_c5_composition_constructs_real_handler_and_runs_empty_barrier(tmp_path):
    sources = [str(UUID(int=number)) for number in range(1, 9)]
    workspace = SemesterWorkspaceCfg(
        semester='2026-2', connection_settings_files_parent_id=sources[0],
        academic_courses_data_source_id=sources[1], sessions_data_source_id=sources[2],
        materials_data_source_id=sources[3], file_intake_data_source_id=sources[4],
        input_requests_data_source_id=sources[5], material_usage_data_source_id=sources[6],
        automation_queue_data_source_id=sources[7],
    )
    config = UlsConfig()
    config.notion.semester_workspaces = [workspace]
    backend = InMemoryNotionWorker({source: [] for source in sources[1:]}, parent_page_id=sources[0])
    worker = SimpleNamespace(workspaces=[workspace], request_coordinators=[], request_extension_readiness={})
    with SQLiteStateStore(tmp_path / 'state.db') as state:
        install_usage_range(worker, config, state, backend, object())
        assert worker.request_extension_readiness['2026-2']['status'] == 'READY'
        assert len(worker.request_coordinators) == 1
        coordinator = worker.request_coordinators[0]
        result = coordinator.publish(coordinator.receive(coordinator.snapshot()))
        assert result['status'] != 'blocked'
        assert not any(event[0] in {'create', 'update'} for event in backend.events)


def test_ready_c5_composition_merges_with_preinstalled_workspace_coordinator(tmp_path):
    sources = [str(UUID(int=number)) for number in range(1, 9)]
    workspace = SemesterWorkspaceCfg(
        semester='2026-2', connection_settings_files_parent_id=sources[0],
        academic_courses_data_source_id=sources[1], sessions_data_source_id=sources[2],
        materials_data_source_id=sources[3], file_intake_data_source_id=sources[4],
        input_requests_data_source_id=sources[5], material_usage_data_source_id=sources[6],
        automation_queue_data_source_id=sources[7],
    )
    config = UlsConfig()
    config.notion.semester_workspaces = [workspace]
    backend = InMemoryNotionWorker({source: [] for source in sources[1:]}, parent_page_id=sources[0])
    local_source = 'uls-submit:2026-2'
    existing = RequestCoordinator(
        workspace='2026-2', source_ids=(local_source,),
        list_records=lambda source: [],
        handlers={},
    )
    worker = SimpleNamespace(
        workspaces=[workspace], request_coordinators=[existing], request_extension_readiness={},
    )
    with SQLiteStateStore(tmp_path / 'state.db') as state:
        install_usage_range(worker, config, state, backend, object())
        assert len(worker.request_coordinators) == 1
        coordinator = worker.request_coordinators[0]
        assert coordinator.source_ids == (local_source, sources[5])
        assert set(coordinator.handlers) == {'usage_range'}
        assert coordinator.after_publish is not None
        result = coordinator.publish(coordinator.receive(coordinator.snapshot()))
        assert result['status'] != 'blocked'
        assert not any(event[0] in {'create', 'update'} for event in backend.events)
