"""Registry of v2 contract tests that are not active yet (implementation spec §14, §15).

Every design §11 test id appears here or in an active ``test_v2_*`` file. A
pending entry is reported as SKIPPED with the stage that activates it, so the
gap stays visible in every test run. When a stage implements a contract, it
moves the id out of ``PENDING`` and adds the real test file (§14 table).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

DESIGN = Path(__file__).resolve().parents[3] / "syllva-v2-design-frozen.md"

# id -> (spec §14 file, activating stage, what is still missing)
PENDING: dict[str, tuple[str, str, str]] = {
    "C-01": ("test_agent_no_tools.py", "R4", "agents/ and build_agent_launch do not exist yet"),
    "C-04": ("test_gate_auth.py", "R3/R5", "SET_SUBMISSION_STATUS (R3), CONFIRM_EXAM_SCOPE/ADOPT_AI_ARTIFACT (R5) — errata E24; VERIFY_MATERIAL active in test_v2_gate"),
    "C-05": ("test_gate_nonce.py", "R3/R5", "remaining gate actions (E24); nonce rules for VERIFY_MATERIAL active in test_v2_gate"),
    "C-06": ("test_ai_artifact_fingerprint.py", "R4", "ai_artifacts pipeline (§8.7)"),
    "C-07": ("test_launch_env.py", "R4", "build_agent_launch env allowlist (§7.2)"),
    "C-08": ("test_agent_cwd.py", "R4", "agent cwd creation (§3.1, §7.1)"),
    "C-09": ("test_single_spawn_path.py", "R4", "launcher and Rust-side check; Python static part (any spawn outside agents/launcher.py) active in test_v2_static_invariants"),
    "C-11": ("test_run_context_lifetime.py", "R4", "Run Context lifetime (§8.6)"),
    "C-15": ("test_template_manifest.py", "R4", "prompt template manifest (§8.2)"),
    "C-16": ("CI workflow", "R2", "v2 subset runs on Linux/macOS/Windows (ci.yml v2 job); full when all C-xx are active"),
    "C-17": ("test_gate_replay.py", "R3/R5", "remaining gate actions (E24); replay/idempotency for VERIFY_MATERIAL active in test_v2_gate, DB trigger half in test_v2_gate_db_triggers"),
    "C-20": ("test_secret_and_lifecycle.py", "R4", "agent-child half (pipes/env of agent processes, needs the R4 spawn path); Core stdio + secret half active in test_v2_core_stdio, DB lock half in test_v2_reader_and_lock"),
    "C-21": ("test_adapter_conformance.py", "R4", "fake_agent_cli harness and per-run assertion (§7.4, §14.2)"),
    "C-22": ("test_citation_validation.py", "R4", "citation parser and escaping (§8.4-§8.5)"),
    "C-23": ("test_prompt_assembly.py", "R4", "prompt assembly (§8.2-§8.4)"),
    "C-24": ("test_multiturn_independence.py", "R4", "multi-turn rule (§8.9)"),
    "C-26": ("test_disclosure_and_logging.py", "R4", "disclosure assertion and log filter (§13.1, §13.4)"),
}


# Contracts that are FULLY active. Moving an id here requires removing it from
# PENDING and naming the test file that implements it (checked below).
ACTIVE: dict[str, str] = {
    "C-03": "test_v2_ownership_matrix.py",
    "C-12": "test_v2_normalization_not_summary.py",
    "C-14": "test_v2_resolve_queries.py",
    "C-02": "test_v2_retrieval_service.py",
    "C-10": "test_v2_retrieval_service.py",
    "C-25": "test_v2_chunk_strategy.py",
    "C-13": "test_v2_core_stdio.py",
    "C-19": "test_v2_shell_config.py",  # summary binding half in test_v2_gate.py
    "C-18": "test_v2_ui_xss.py",  # DOM half; CSP headers in test_v2_ui_boundary.py
}


def test_registry_covers_every_design_contract_id() -> None:
    design_ids = set(re.findall(r"^\| (C-\d{2}) \|", DESIGN.read_text(encoding="utf-8"), flags=re.MULTILINE))
    assert design_ids == {f"C-{n:02d}" for n in range(1, 27)}
    assert not set(PENDING) & set(ACTIVE), "an id cannot be both pending and active"
    assert set(PENDING) | set(ACTIVE) == design_ids, "every design contract id must be pending or active"
    here = Path(__file__).resolve().parent
    for contract_id, filename in ACTIVE.items():
        test_file = here / filename
        assert test_file.is_file(), f"{contract_id}: {filename} missing"
        assert contract_id in test_file.read_text(encoding="utf-8"), f"{contract_id} not referenced in {filename}"


@pytest.mark.parametrize("contract_id", sorted(PENDING))
def test_pending_contract(contract_id: str) -> None:
    target, stage, missing = PENDING[contract_id]
    pytest.skip(f"{contract_id} pending until {stage}: {missing} (spec §14 file: {target})")
