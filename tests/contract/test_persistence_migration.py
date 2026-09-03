import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from uls.state.sqlite import SQLiteStateStore


def test_migrations_are_safe_to_apply_repeatedly(tmp_path) -> None:
    store = SQLiteStateStore(tmp_path / "state.sqlite")
    store.apply_migrations()
    store.apply_migrations()

    tables = {
        row[0]
        for row in store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {
        "jobs",
        "source_files",
        "source_versions",
        "processing_records",
        "checkpoints",
        "entity_allocations",
        "schema_migrations",
    } <= tables
    assert store.connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1
    store.close()
