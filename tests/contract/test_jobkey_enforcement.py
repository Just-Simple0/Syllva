import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import pytest

from uls.orchestration.jobs import derive_job_key
from uls.state.sqlite import SQLiteStateStore


def _source_job_kwargs() -> dict[str, str]:
    return {
        "operation": "normalize",
        "stage": "normalization",
        "source_file_id": "source-1",
        "source_hash": "sha256:source-hash",
        "processor_version": "1.2.0",
    }


def test_create_job_derives_and_enforces_canonical_key(tmp_path) -> None:
    store = SQLiteStateStore(tmp_path / "state.sqlite")
    first = store.create_job(**_source_job_kwargs())
    assert first.job_key == derive_job_key(
        "source-1", "sha256:source-hash", "normalize", "1.2.0"
    )

    retry = store.create_job(**{**_source_job_kwargs(), "stage": "retry-stage"})
    assert retry.id == first.id
    assert store.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    store.close()


def test_create_job_rejects_wrong_or_malformed_key(tmp_path) -> None:
    store = SQLiteStateStore(tmp_path / "state.sqlite")
    with pytest.raises(ValueError, match="canonical source identity"):
        store.create_job(
            job_key="sha256:" + "0" * 64,
            **_source_job_kwargs(),
        )
    with pytest.raises(ValueError, match="sha256:<64"):
        store.create_job(job_key="random", **_source_job_kwargs())
    with pytest.raises(ValueError, match="non-source"):
        store.create_job(operation="maintenance", stage="maintenance")
    store.close()
