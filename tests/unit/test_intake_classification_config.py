"""P-A: config surface for intake classification v2 (plan §2.3, §4, §7)."""
from __future__ import annotations

import pytest

from uls.config.loader import load_config_unvalidated

pytestmark = pytest.mark.unit


def _load(tmp_path, body: str):
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return load_config_unvalidated(path)


def test_defaults_keep_classification_disabled(tmp_path) -> None:
    cfg = _load(tmp_path, "system:\n  timezone: Asia/Seoul\n")
    cls = cfg.intake.classification
    assert cls.enabled is False and cls.schema_profile == ""
    assert (cls.min_confidence, cls.min_top_probability) == (0.80, 0.70)
    assert (cls.max_calls_per_tick, cls.max_terminal_scan) == (50, 50)
    assert cls.max_source_bytes == 50 * 1024 * 1024 and cls.canvas_course_map == {}
    assert cfg.retrieval.v2_exposure_gate is False


def test_full_section_parses_and_normalizes(tmp_path) -> None:
    cfg = _load(tmp_path, """
intake:
  classification:
    enabled: true
    schema_profile: legacy5-cls
    min_confidence: 0.9
    min_top_probability: 1
    max_calls_per_tick: 10
    max_source_bytes: 1024
    max_terminal_scan: 5
    canvas_course_map:
      67535: "2026-2_LMS67535-001"
retrieval:
  v2_exposure_gate: true
courses:
  - course_key: "2026-2_LMS67535-001"
    name: "알고리즘2 (001)"
    code: "LMS67535"
    section: "001"
    semester: "2026-2"
    aliases: ["알고리즘 2", "ALGO2"]
google_drive:
  semester_registries:
    - semester: "2026-2"
      folder_id: f
      upload_folder_id: u
      start_date: 2026-09-01
      end_date: "2026-12-20"
""")
    cls = cfg.intake.classification
    assert cls.enabled is True and cls.schema_profile == "legacy5-cls"
    assert cls.min_top_probability == 1.0 and isinstance(cls.min_top_probability, float)
    assert cls.canvas_course_map == {67535: "2026-2_LMS67535-001"}
    assert cfg.retrieval.v2_exposure_gate is True
    assert cfg.courses[0].aliases == ["알고리즘 2", "ALGO2"]
    registry = cfg.google_drive.semester_registries[0]
    assert (registry.start_date, registry.end_date) == ("2026-09-01", "2026-12-20")


@pytest.mark.parametrize("body", [
    "intake:\n  classification:\n    enabled: yes_please\n",
    "intake:\n  classification:\n    schema_profile: legacy5\n",
    "intake:\n  classification:\n    min_confidence: 1.5\n",
    "intake:\n  classification:\n    min_confidence: true\n",
    "intake:\n  classification:\n    max_calls_per_tick: 0\n",
    "intake:\n  classification:\n    max_source_bytes: -1\n",
    "intake:\n  classification:\n    canvas_course_map:\n      abc: x\n",
    "intake:\n  classification:\n    canvas_course_map:\n      1: ''\n",
    "intake:\n  classification:\n    extra: 1\n",
    "intake:\n  other: 1\n",
    "intake: not-a-mapping\n",
    "courses:\n  - course_key: k\n    aliases: [1]\n",
    "courses:\n  - course_key: k\n    aliases: ['']\n",
    "google_drive:\n  semester_registries:\n    - semester: s\n      start_date: 2026/09/01\n",
    "google_drive:\n  semester_registries:\n    - semester: s\n      start_date: 2026-12-01\n      end_date: 2026-09-01\n",
])
def test_malformed_sections_fail_closed(tmp_path, body: str) -> None:
    with pytest.raises(ValueError):
        _load(tmp_path, body)
