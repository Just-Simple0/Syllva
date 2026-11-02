"""R1b-6: every legacy test file has exactly one disposition (spec §15, docs/plans/v2-test-disposition.md).

The table must match the file system: a legacy test that is deleted, or a new
test outside ``tests/contract/v2`` that is not listed as v2-native, fails here,
so the disposition document cannot drift from the code it describes.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[3]
DOC = ROOT / "docs" / "plans" / "v2-test-disposition.md"
TESTS = ROOT / "tests"
VERDICTS = {"유지", "재작성", "대체", "폐기", "보류"}
ROW = re.compile(r"^\| `tests/([^`]+)` \| (\S+) \| ([^|]*) \| ([^|]*) \|$", re.MULTILINE)
V2_NATIVE = re.compile(r"^- `tests/(unit/test_v2_[a-z_]+\.py)`$", re.MULTILINE)
SPEC_IDS = set(
    re.findall(r"^\| (C-\d{2}) \|", (ROOT / "syllva-v2-implementation-spec-frozen.md").read_text(encoding="utf-8"), re.MULTILINE)
)


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _rows() -> list[tuple[str, str, str, str]]:
    return ROW.findall(_doc())


def test_table_matches_the_file_system() -> None:
    listed = [row[0] for row in _rows()]
    assert len(listed) == len(set(listed)), "a file is listed twice"
    native = set(V2_NATIVE.findall(_doc()))
    on_disk = {
        path.relative_to(TESTS).as_posix()
        for path in TESTS.rglob("test_*.py")
        if not path.relative_to(TESTS).as_posix().startswith("contract/v2/")
    }
    assert native <= on_disk, f"v2-native entries without a file: {sorted(native - on_disk)}"
    assert set(listed) == on_disk - native, {
        "unlisted": sorted(on_disk - native - set(listed)),
        "listed but gone": sorted(set(listed) - on_disk),
    }


def test_every_row_has_a_known_verdict_and_a_target() -> None:
    for path, verdict, target, reason in _rows():
        assert verdict in VERDICTS, path
        assert reason.strip(), path
        if verdict in ("재작성", "대체", "보류"):
            assert target.strip() not in ("", "—"), f"{path}: {verdict} needs a target stage or replacement"
        if verdict == "재작성":
            assert re.match(r"R\d", target.strip()), f"{path}: rewrite target must name a roadmap stage"
        if verdict == "대체":
            ids = set(re.findall(r"C-\d{2}", target))
            files = re.findall(r"`(test_v2_[a-z_]+\.py)`", target)
            assert ids or files, f"{path}: a replacement must name a contract id or a v2 test file"
            assert ids <= SPEC_IDS, f"{path}: unknown contract id {sorted(ids - SPEC_IDS)}"
            for name in files:
                assert list(TESTS.rglob(name)), f"{path}: replacement {name} does not exist"


def test_summary_counts_match_the_table() -> None:
    counts = Counter(row[1] for row in _rows())
    summary = dict(re.findall(r"^\| (유지|재작성|대체|폐기|보류) \| (\d+) \|$", _doc(), re.MULTILINE))
    assert {k: int(v) for k, v in summary.items()} == dict(counts)
    total = re.search(r"\*\*합계\*\* \| \*\*(\d+)\*\*", _doc())
    assert total and int(total.group(1)) == sum(counts.values())
