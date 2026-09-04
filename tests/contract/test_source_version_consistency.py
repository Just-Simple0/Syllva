import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import pytest

from uls.state.sqlite import SQLiteStateStore


COURSE_KEY = "2026-1_COMP319-002"


def test_source_version_canonical_entity_must_match_source_file(tmp_path) -> None:
    store = SQLiteStateStore(tmp_path / "state.sqlite")
    store.register_source_file(
        "source-1",
        "drive",
        "file-1",
        COURSE_KEY,
        "material",
        canonical_entity_id="COMP319-M01",
    )
    with pytest.raises(ValueError, match="canonical entity"):
        store.register_source_version(
            "source-1",
            "hash-v1",
            "COMP319-M02",
            {"provider": "drive", "file_id": "file-1"},
            "1.2.0",
        )
    version = store.register_source_version(
        "source-1",
        "hash-v1",
        "COMP319-M01",
        {"provider": "drive", "file_id": "file-1"},
        "1.2.0",
    )
    assert version.canonical_entity_id == "COMP319-M01"
    store.close()
