import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from concurrent.futures import ThreadPoolExecutor

from uls.state.sqlite import SQLiteStateStore


COURSE_KEY = "2026-1_COMP319-002"


def test_entity_allocation_is_source_retry_idempotent(tmp_path) -> None:
    store = SQLiteStateStore(tmp_path / "state.sqlite")
    store.register_source_file("source-1", "drive", "file-1", COURSE_KEY, "material")

    first = store.allocate_entity(COURSE_KEY, "M", "source-1")
    retry = store.allocate_entity(COURSE_KEY, "M", "source-1")

    assert first == "COMP319-M01"
    assert retry == first
    assert store.get_source_file("source-1").canonical_entity_id == first
    assert store.connection.execute(
        "SELECT next_sequence FROM entity_allocations WHERE course_key = ? AND entity_type = ?",
        (COURSE_KEY, "M"),
    ).fetchone()[0] == 2
    store.close()


def test_concurrent_allocation_attempts_converge_on_one_id(tmp_path) -> None:
    db_path = tmp_path / "concurrent.sqlite"
    setup = SQLiteStateStore(db_path)
    setup.register_source_file("source-1", "drive", "file-1", COURSE_KEY, "material")
    setup.close()

    def allocate() -> str:
        store = SQLiteStateStore(db_path)
        try:
            return store.allocate_entity(COURSE_KEY, "M", "source-1")
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=8) as executor:
        allocated = list(executor.map(lambda _index: allocate(), range(8)))

    assert set(allocated) == {"COMP319-M01"}
    check = SQLiteStateStore(db_path)
    assert check.get_source_file("source-1").canonical_entity_id == "COMP319-M01"
    assert check.connection.execute(
        "SELECT COUNT(*) FROM entity_allocations WHERE course_key = ? AND entity_type = ?",
        (COURSE_KEY, "M"),
    ).fetchone()[0] == 1
    check.close()
