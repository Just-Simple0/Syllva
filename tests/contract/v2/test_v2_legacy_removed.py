"""R1c (errata E19): the v1.2 providers, engine, state and runtime are gone and stay gone.

Import-linter cannot forbid a module that does not exist, so this test keeps the
deleted paths deleted; re-adding one needs a design revision, not a quiet file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

SRC = Path(__file__).resolve().parents[3] / "src" / "syllva"
REMOVED = [
    "adapters/notion", "adapters/drive", "study_notes", "proposal", "cli/commands",
    "domain/approval_identity.py", "retrieval/engine.py", "retrieval/resolver.py", "retrieval/_compat.py",
    "state/sqlite.py", "state/reader.py", "state/usage_range.py", "state/classification_state.py",
    "intake/worker.py", "intake/composition.py", "intake/coordinator.py", "intake/registry.py",
    "intake/classification/s2.py", "adapters/llm/typesafe_choice.py", "enrichment/writer.py",
    "runtime.py", "worker.py", "cli/credential_set.py", "ingestion/discovery.py",
    "retrieval/scope.py", "retrieval/additional.py", "retrieval/provenance.py",
    "enrichment/_common.py", "enrichment/material.py", "enrichment/session.py",
    "intake/attestation.py", "intake/planner.py", "intake/requests.py", "intake/usage_range.py",
    "intake/study_note_composition.py", "intake/canvas_observer.py", "intake/canvas_attachments.py",
    "intake/canvas_composition.py",
]


@pytest.mark.parametrize("relative", REMOVED)
def test_removed_legacy_code_does_not_return(relative: str) -> None:
    assert not (SRC / relative).exists(), f"{relative} was removed in R1c (errata E19)"


def test_no_import_linter_exceptions_remain() -> None:
    pyproject = (SRC.parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert "ignore_imports = [" not in pyproject
