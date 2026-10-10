from uls.cli.main import _live_retrieval_probe_course_key
from uls.config.schema import (
    BehaviorContractCfg,
    CourseCfg,
    CourseStaticFolderCfg,
    SemesterRegistryCfg,
    SemesterWorkspaceCfg,
    UlsConfig,
)
from uls.config.validation import validate_config
from uls.runtime import _retrieval_notion_sources

COURSE = "2026-2_COMP322-001"


def _config(tmp_path) -> UlsConfig:
    contract = tmp_path / "study-behavior.md"
    contract.write_text("# contract\n", encoding="utf-8")
    cfg = UlsConfig(
        behavior_contract=BehaviorContractCfg(path=str(contract)),
        courses=[
            CourseCfg(
                course_key=COURSE,
                name="Database Systems",
                code="COMP322",
                section="001",
                semester="2026-2",
            )
        ],
    )
    cfg.google_drive.semester_registries = [
        SemesterRegistryCfg(
            semester="2026-2",
            folder_id="semester-folder",
            upload_folder_id="upload-folder",
            course_folder_ids={COURSE: "course-folder"},
            course_static_folder_ids={
                COURSE: CourseStaticFolderCfg(
                    recordings_folder_id="recordings-folder",
                    materials_folder_id="materials-folder",
                )
            },
        )
    ]
    cfg.notion.semester_workspaces = [
        SemesterWorkspaceCfg(
            semester="2026-2",
            connection_settings_files_parent_id="connection-parent",
            academic_courses_data_source_id="courses-ds",
            sessions_data_source_id="sessions-ds",
            materials_data_source_id="materials-ds",
            file_intake_data_source_id="file-intake-ds",
            input_requests_data_source_id="input-requests-ds",
        )
    ]
    return cfg


def test_semester_retrieval_lane_selects_direct_sources_without_optional_fallback(tmp_path):
    cfg = _config(tmp_path)
    cfg.retrieval.notion_lane = "semester_workspace"
    cfg.retrieval.semester = "2026-2"

    assert validate_config(cfg) == []
    sources, semester = _retrieval_notion_sources(cfg)

    assert semester == "2026-2"
    assert sources == {
        "courses": "courses-ds",
        "sessions": "sessions-ds",
        "materials": "materials-ds",
    }
    assert "material_usage" not in sources
    assert "exams" not in sources
    assert "activities" not in sources


def test_legacy_retrieval_lane_remains_default_and_has_no_semester_selector(tmp_path):
    cfg = _config(tmp_path)

    assert cfg.retrieval.notion_lane == "legacy_global"
    assert cfg.retrieval.semester == ""
    assert validate_config(cfg) == []
    assert _retrieval_notion_sources(cfg) == (None, "")


def test_retrieval_lane_validation_rejects_ambiguous_or_stale_selection(tmp_path):
    cfg = _config(tmp_path)
    cfg.retrieval.notion_lane = "legacy_global"
    cfg.retrieval.semester = "2026-2"
    problems = validate_config(cfg)
    assert any("must be empty" in problem for problem in problems)

    cfg.retrieval.notion_lane = "semester_workspace"
    cfg.retrieval.semester = "2026-1"
    problems = validate_config(cfg)
    assert any("select exactly one" in problem for problem in problems)
    assert any("at least one configured Course" in problem for problem in problems)

    cfg.retrieval.notion_lane = "automatic"
    cfg.retrieval.semester = ""
    problems = validate_config(cfg)
    assert any("retrieval.notion_lane" in problem for problem in problems)


def test_live_probe_uses_the_selected_semester_instead_of_first_course(tmp_path):
    cfg = _config(tmp_path)
    cfg.courses.insert(
        0,
        CourseCfg(
            course_key="2026-1_COMP319-001",
            name="Algorithms",
            code="COMP319",
            section="001",
            semester="2026-1",
        ),
    )

    assert _live_retrieval_probe_course_key(cfg) == "2026-1_COMP319-001"

    cfg.retrieval.notion_lane = "semester_workspace"
    cfg.retrieval.semester = "2026-2"
    assert _live_retrieval_probe_course_key(cfg) == COURSE
