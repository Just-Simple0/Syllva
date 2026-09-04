import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import pytest

from uls.domain.errors import LocatorParseError
from uls.domain.models import is_contained, parse_locator


@pytest.mark.parametrize(
    ("allowed", "requested", "expected"),
    [
        ("COMP319-M03:p13-p27", "COMP319-M03:p18", True),
        ("COMP319-M03:p13-p27", "COMP319-M03:p13", True),
        ("COMP319-M03:p13-p27", "COMP319-M03:p13:user", False),
        ("COMP319-M03:p13-p27:user", "COMP319-M03:p13", False),
        ("COMP319-M03:p13-p27:user", "COMP319-M03:p13:user", True),
        ("COMP319-M03:p13-p27", "COMP319-S05:t00:18:00", False),
        ("COMP319-S05:t00:10:00-00:40:00", "COMP319-S05:t00:10:00", True),
        ("COMP319-S05:t00:10:00-00:40:00", "COMP319-S05:t00:40:00", True),
        ("COMP319-S05:t00:10:00-00:40:00", "COMP319-S05:t00:09:59", False),
        ("COMP319-S05:t00:10:00-00:40:00", "COMP319-S05:t00:40:01", False),
        ("COMP319-M03:p13-p27", "COMP319-M04:p18", False),
    ],
)
def test_adversarial_typed_containment_matrix(allowed, requested, expected) -> None:
    assert is_contained(parse_locator(requested), parse_locator(allowed)) is expected


@pytest.mark.parametrize(
    "allowed,requested",
    [
        ("COMP319-M03:p13-p27", "COMP319-M03:p13-p27:user"),
        ("COMP319-M03:p13-p27:source", "COMP319-M03:p13-p27"),
        ("COMP319-M03:p13-p27:user", "COMP319-M03:p13-p27:source"),
        ("COMP319-S05:t00:10:00-00:40:00", "COMP319-M03:p13"),
    ],
)
def test_subtype_and_cross_kind_misses_are_denied(allowed, requested) -> None:
    assert is_contained(parse_locator(requested), parse_locator(allowed)) is False


@pytest.mark.parametrize(
    "value",
    [
        "COMP319-M03:p0",
        "COMP319-M03:p27-p13",
        "COMP319-S05:t00:40:00-00:10:00",
        "COMP319-S05:t00:60:00",
        "COMP319-M03:p13:unknown",
        "https://example.invalid/COMP319-M03:p13",
    ],
)
def test_parse_locator_rejects_adversarial_strings(value) -> None:
    with pytest.raises(LocatorParseError):
        parse_locator(value)
