#!/usr/bin/env python3
"""Compute the canonical Behavior Contract version/hash and stamp projections.

Spec §25.1 / §32: the canonical Behavior Contract carries a version and content hash.
Client projections (Claude Skills, ChatGPT Instructions) must declare the version/hash
they were generated or reviewed against. This script recomputes the canonical hash and
writes it into every projection's front matter (`behavior_contract_hash`).

Run this whenever the canonical contract changes and the projections are re-reviewed.
The lint script (`lint_behavior_projection.py`) then verifies projections match.

Usage:
    python scripts/project_behavior_contract.py            # stamp all projections
    python scripts/project_behavior_contract.py --print    # just print version/hash
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = REPO_ROOT / "contracts" / "study-behavior.md"

PROJECTION_PATHS = [
    REPO_ROOT / "clients" / "claude" / "skills" / "study-session" / "SKILL.md",
    REPO_ROOT / "clients" / "claude" / "skills" / "explain-concept" / "SKILL.md",
    REPO_ROOT / "clients" / "claude" / "skills" / "exam-prep" / "SKILL.md",
    REPO_ROOT / "clients" / "claude" / "skills" / "activity-help" / "SKILL.md",
    REPO_ROOT / "clients" / "claude" / "skills" / "verify-source" / "SKILL.md",
    REPO_ROOT / "clients" / "chatgpt" / "instructions" / "study-behavior.md",
]

FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _normalize(text: str) -> str:
    """Normalize line endings so the hash is stable across macOS/Windows."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def canonical_version_and_hash() -> tuple[int, str]:
    """Return (version, 'sha256:<hex>') for the canonical contract.

    The hash covers the full canonical file bytes (LF-normalized). The canonical
    front matter carries only the version, so hashing the whole file is stable.
    """
    import hashlib

    raw = _normalize(CONTRACT_PATH.read_text(encoding="utf-8"))
    version = _read_version(raw)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return version, f"sha256:{digest}"


def _read_version(text: str) -> int:
    m = FRONT_MATTER_RE.match(text)
    if not m:
        raise ValueError(f"{CONTRACT_PATH} has no front matter")
    for line in m.group(1).splitlines():
        if line.strip().startswith("behavior_contract_version:"):
            return int(line.split(":", 1)[1].strip())
    raise ValueError("behavior_contract_version not found in canonical contract")


def _stamp(path: Path, version: int, contract_hash: str) -> bool:
    text = _normalize(path.read_text(encoding="utf-8"))
    m = FRONT_MATTER_RE.match(text)
    if not m:
        raise ValueError(f"{path} has no front matter to stamp")
    fm = m.group(1)
    fm = re.sub(
        r"behavior_contract_version:\s*.*",
        f"behavior_contract_version: {version}",
        fm,
    )
    fm = re.sub(
        r"behavior_contract_hash:\s*.*",
        f"behavior_contract_hash: {contract_hash}",
        fm,
    )
    new_text = f"---\n{fm}\n---\n" + text[m.end():]
    if new_text != text:
        path.write_text(new_text, encoding="utf-8")
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print", action="store_true", dest="print_only")
    args = parser.parse_args()

    version, contract_hash = canonical_version_and_hash()
    print(f"behavior_contract_version: {version}")
    print(f"behavior_contract_hash: {contract_hash}")

    if args.print_only:
        return 0

    for path in PROJECTION_PATHS:
        if not path.exists():
            print(f"  ! missing projection: {path.relative_to(REPO_ROOT)}", file=sys.stderr)
            continue
        changed = _stamp(path, version, contract_hash)
        state = "stamped" if changed else "unchanged"
        print(f"  {state}: {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
