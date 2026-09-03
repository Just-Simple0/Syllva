import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import hashlib

from uls.orchestration.jobs import derive_job_key


def test_job_key_matches_frozen_byte_layout() -> None:
    value = derive_job_key("src-1", "sha256:abc", "normalize", "1.2.0")
    assert value == "sha256:969e83690bc140a7e7392e0a4edfc963cafe387e94b0c96c1fe13d46081f7aa4"


def test_job_key_is_deterministic_and_changes_for_each_canonical_field() -> None:
    fields = ("source-1", "hash-1", "normalize", "processor-1")
    original = derive_job_key(*fields)
    assert original == derive_job_key(*fields)
    for index in range(4):
        changed = list(fields)
        changed[index] += "-changed"
        assert derive_job_key(*changed) != original


def test_job_key_is_not_a_plain_hash_without_the_unit_separator() -> None:
    fields = ("source-1", "hash-1", "normalize", "processor-1")
    expected = "sha256:" + hashlib.sha256(b"\x1f".join(item.encode() for item in fields)).hexdigest()
    assert derive_job_key(*fields) == expected
