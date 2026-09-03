import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from uls.adapters.notion.base import find_alias_matches, normalize_alias, parse_aliases


def test_parse_aliases_preserves_case_and_trims_pieces() -> None:
    assert parse_aliases("5강 | 5번째 강의 | CPU Scheduling") == [
        "5강",
        "5번째 강의",
        "CPU Scheduling",
    ]


def test_parse_aliases_ignores_empty_and_whitespace_only_pieces() -> None:
    assert parse_aliases(None) == []
    assert parse_aliases("") == []
    assert parse_aliases(" |  |  CPU Scheduling  | ") == ["CPU Scheduling"]


def test_normalize_alias_is_for_comparison_only() -> None:
    assert normalize_alias("  CPU Scheduling ") == "cpu scheduling"
    assert parse_aliases("CPU Scheduling") == ["CPU Scheduling"]


def test_alias_lookup_splits_rich_text_and_includes_implicit_aliases() -> None:
    records = [
        {"ID": "S-01", "Name": "5강", "Aliases": "5강 | CPU Scheduling"},
        {"ID": "S-02", "Name": "Other", "Aliases": "5강의록"},
    ]
    assert find_alias_matches(records, "cpu scheduling") == [records[0]]
    assert find_alias_matches(records, "S-01") == [records[0]]
