from __future__ import annotations

import pathlib
import re
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from project_behavior_contract import PROJECTION_PATHS, canonical_version_and_hash


def _body(path: pathlib.Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    match = re.match(r"^---\n.*?\n---\n", text, re.DOTALL)
    assert match is not None, f"{path} has no front matter"
    return text[match.end() :]


def _metadata(text: str) -> tuple[int, str | None]:
    match = re.match(r"^---\n(.*?)\n---\n", text.replace("\r\n", "\n"), re.DOTALL)
    assert match is not None
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if separator:
            values[key.strip()] = value.strip()
    return int(values["behavior_contract_version"]), values.get("behavior_contract_hash")


def _behavior_contract_semantics_are_valid(body: str) -> bool:
    """Check the bounded Phase 5 clauses with structural relations.

    This is a deterministic contract fixture check, not a general natural
    language evaluator.  The relations require the authority/disclosure
    clauses to be connected, so keyword presence alone cannot pass.
    """

    lowered = re.sub(r"\s+", " ", body.casefold())
    has_provenance = re.search(
        r"source\s*/\s*user\s*/\s*ai\s*/\s*external\s+provenance\s+distinct",
        lowered,
    ) is not None
    has_external_label = (
        re.search(r"general knowledge is external", lowered) is not None
        or re.search(r"general/pretrained knowledge.{0,80}explicitly labeled as external", lowered)
        is not None
    )
    has_official_authority = re.search(
        r"official.{0,220}(?:govern|highest-authority).{0,220}(?:conflict|recommendation)",
        lowered,
    ) is not None
    has_incomplete_disclosure = re.search(
        r"(?:missing|partial|truncated).{0,180}disclos(?:e|ure).{0,100}incomplete",
        lowered,
    ) is not None
    forbids_complete_claim = re.search(
        r"(?:do not|never)\s+claim\s+(?:to have\s+)?(?:the\s+)?complete\s+instruction\s+set",
        lowered,
    ) is not None
    forbids_omitted_inference = re.search(
        r"(?:do not|never)\s+(?:infer\s+omitted\s+requirements|"
        r"claim[^.!?]{0,140}(?:or|and)\s+infer\s+omitted\s+requirements)",
        lowered,
    ) is not None
    presents_external_as_source = re.search(
        r"(?:pretrained|external|general)\s+(?:knowledge|facts?)\s+(?:"
        r"is|are)\s+source\b"
        r"|(?:pretrained|external|general)\s+(?:knowledge|facts?)\s+"
        r"(?:may|can)\s+be\s+(?:reported|presented|treated)\s+as\s+source\b",
        lowered,
    ) is not None
    claims_complete_after_incomplete = re.search(
        r"complete\s+instruction\s+set[^.!?]{0,50}"
        r"(?:is|are|was|were|remains?)\s+(?:available|complete|provided|present)",
        lowered,
    ) is not None
    allows_complete_claim = re.search(
        r"(?:may|can|should)\s+claim\s+(?:to have\s+)?"
        r"(?:the\s+)?complete\s+instruction\s+set\b",
        lowered,
    ) is not None
    return (
        has_provenance
        and has_external_label
        and has_official_authority
        and has_incomplete_disclosure
        and forbids_complete_claim
        and forbids_omitted_inference
        and not presents_external_as_source
        and not claims_complete_after_incomplete
        and not allows_complete_claim
    )


def test_each_projection_body_contains_the_phase5_behavior_semantics() -> None:
    paths = [REPO_ROOT / "contracts" / "study-behavior.md", *PROJECTION_PATHS]

    for path in paths:
        assert _behavior_contract_semantics_are_valid(_body(path)), path


def test_each_projection_has_current_metadata_before_body_semantics_are_used() -> None:
    version, contract_hash = canonical_version_and_hash()

    assert version == 2
    assert contract_hash.startswith("sha256:")
    for path in PROJECTION_PATHS:
        assert _metadata(path.read_text(encoding="utf-8")) == (version, contract_hash)


@pytest.mark.parametrize(
    "body",
    [
        "Official instructions are missing; disclose incomplete coverage. Do not claim the complete instruction set.",
        "Official instructions are partial or truncated; disclose incomplete coverage, do not claim the complete instruction set, and do not infer omitted requirements.",
    ],
)
def test_incomplete_or_truncated_instruction_fixtures_require_disclosure(body: str) -> None:
    assert _behavior_contract_semantics_are_valid(
        "Keep SOURCE/USER/AI/External provenance distinct. General knowledge is External. "
        "Official instructions govern conflicting recommendations and the conflict is disclosed. "
        + body
        + " Do not infer omitted requirements."
    )


@pytest.mark.parametrize(
    "wrong_body",
    [
        """SOURCE/USER/AI/External provenance distinct. General knowledge is External.
Official instructions govern conflicting recommendations. Official instructions are
partial or truncated; disclose incomplete coverage, but the complete instruction set is
available. Do not infer omitted requirements. Pretrained knowledge is SOURCE.""",
        """SOURCE/USER/AI/External provenance distinct. General knowledge is External.
Official instructions govern conflicting recommendations. Missing, partial, or truncated
instructions disclose incomplete coverage. The complete instruction set is available.
Do not infer omitted requirements.""",
    ],
)
def test_wrong_semantic_body_is_rejected_even_with_correct_metadata(wrong_body: str) -> None:
    version, contract_hash = canonical_version_and_hash()
    wrong_body_fixture = (
        f"---\nbehavior_contract_version: {version}\n"
        f"behavior_contract_hash: {contract_hash}\n---\n{wrong_body}\n"
    )

    assert _metadata(wrong_body_fixture) == (version, contract_hash)
    assert not _behavior_contract_semantics_are_valid(_body_from_text(wrong_body_fixture))


def test_correct_external_label_and_incomplete_disclosure_pass_semantic_check() -> None:
    correct_body = """Keep SOURCE/USER/AI/External provenance distinct. General knowledge is External.
Official instructions govern conflicting recommendations and the conflict is disclosed.
If official instructions are missing, partial, or truncated, disclose incomplete coverage;
do not claim to have the complete instruction set and do not infer omitted requirements."""

    assert _behavior_contract_semantics_are_valid(correct_body)


@pytest.mark.parametrize(
    "contradiction",
    [
        "Pretrained knowledge is SOURCE.",
        "Pretrained facts may be reported as SOURCE.",
        "You may claim the complete instruction set.",
    ],
)
def test_isolated_behavior_contradictions_fail_with_actual_metadata(contradiction: str) -> None:
    valid_body = """Keep SOURCE/USER/AI/External provenance distinct. General knowledge is External.
Official instructions govern conflicting recommendations and the conflict is disclosed.
If official instructions are missing, partial, or truncated, disclose incomplete coverage;
do not claim to have the complete instruction set and do not infer omitted requirements."""
    version, contract_hash = canonical_version_and_hash()
    fixture = (
        f"---\nbehavior_contract_version: {version}\n"
        f"behavior_contract_hash: {contract_hash}\n---\n"
        f"{valid_body}\n{contradiction}\n"
    )

    assert _metadata(fixture) == (version, contract_hash)
    assert not _behavior_contract_semantics_are_valid(_body_from_text(fixture))


def _body_from_text(text: str) -> str:
    match = re.match(r"^---\n.*?\n---\n", text, re.DOTALL)
    assert match is not None
    return text[match.end() :]
