"""C4 regression: classifier timestamp detection shares the normalizer grammar.

rev10 (docs/ux/intake-execution-contract.md section 3.1/section 9 C4) requires the
pre-canonical content-based transcript-candidate signal to share the same
two-/three-part timestamp and bracket grammar as the normalizer, so a
transcript whose only evidence is a two-part timestamp (or a non-square
bracket pair) is still classified as a transcript candidate (acceptance
scenario A10), never silently downgraded to the generic MATERIAL default.

An independent insane-review of an earlier draft found the positive matrix
was missing the three-part fullwidth-bracket cell, and that
classify_source_detailed() itself was only exercised directly for two of the
six two-/three-part x [ ]/( )/[fullwidth] combinations. Both are covered
directly below as the full 2x3 matrix, on both contains_timestamp_marker()
and classify_source_detailed(), plus negative regressions for a mismatched
bracket pair and a truncated/unbalanced marker.
"""

from __future__ import annotations

import pytest

from uls.ingestion.classifier import SourceKind, classify_source_detailed
from uls.normalization.transcript import contains_timestamp_marker

pytestmark = pytest.mark.unit

# The full 2-part/3-part x [ ] / ( ) / fullwidth-bracket matrix required by
# rev10 section 3.1's "shares the normalizer's two-/three-part grammar" rule.
_TWO_PART_MATRIX = [
    "[0:01]",
    "(0:01)",
    "\u30100:01\u3011",
]
_THREE_PART_MATRIX = [
    "[00:01:02]",
    "(00:01:02)",
    "\u301000:01:02\u3011",
]
_ALL_VALID_MARKERS = _TWO_PART_MATRIX + _THREE_PART_MATRIX


@pytest.mark.parametrize("marker", _ALL_VALID_MARKERS)
def test_contains_timestamp_marker_accepts_full_two_and_three_part_bracket_matrix(
    marker: str,
) -> None:
    assert contains_timestamp_marker(f"intro\n{marker} first line") is True


@pytest.mark.parametrize("marker", _ALL_VALID_MARKERS)
def test_classify_source_detailed_accepts_full_two_and_three_part_bracket_matrix(
    marker: str,
) -> None:
    """Direct classify_source_detailed() coverage for every one of the six
    two-/three-part x bracket-kind cells, not just the shared helper."""

    result = classify_source_detailed(
        "week1-notes.md",
        mime_type="text/markdown",
        content=f"intro\n{marker} the lecture begins",
    )
    assert result.kind is SourceKind.TRANSCRIPT
    assert result.reason == "timestamp marker"


@pytest.mark.parametrize(
    "content",
    [
        "",
        "plain prose with no markers at all",
        "a (parenthetical remark) with no colon",
        "an [unrelated bracket] with no timestamp",
        "a malformed [0:1] single-digit-second stamp",
        "an out-of-range (12:99) stamp",
        "a mismatched bracket pair [0:01)",
        "a truncated unbalanced marker [0:01 with no closing bracket\nnext line",
    ],
)
def test_contains_timestamp_marker_rejects_non_timestamp_and_malformed_content(
    content: str,
) -> None:
    assert contains_timestamp_marker(content) is False


def test_contains_timestamp_marker_rejects_non_string() -> None:
    with pytest.raises(TypeError):
        contains_timestamp_marker(None)  # type: ignore[arg-type]


def test_classify_source_detailed_still_falls_back_to_material_without_any_timestamp() -> None:
    """No regression: content with no timestamp-shaped marker at all keeps the
    existing generic MATERIAL confidence=0.5 default candidate. This default
    is never used for automatic registration on its own -- the production
    fail-closed gate is validate_request_input() (see
    tests/unit/test_c4_route_intake_gate.py), and RequestInput (the type that
    gate validates) has no observed_kind/confidence field at all."""

    result = classify_source_detailed(
        "week1-notes.md",
        mime_type="text/markdown",
        content="plain lecture notes with no timestamps at all",
    )
    assert result.kind is SourceKind.MATERIAL
    assert result.confidence == 0.5
    assert result.reason == "default academic file"
