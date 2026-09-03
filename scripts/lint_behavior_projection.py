#!/usr/bin/env python3
"""Fail CI when a Behavior Contract projection drifts from the canonical contract.

Spec §32 / §50.14: CI must fail when a projection's declared
`behavior_contract_version` / `behavior_contract_hash` does not match the canonical
contract. This prevents long-lived client Skills/Instructions from silently drifting.

Exit code 0 = all projections match. Non-zero = drift detected.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Reuse the canonical computation and projection list.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from project_behavior_contract import (  # noqa: E402
    PROJECTION_PATHS,
    REPO_ROOT,
    canonical_version_and_hash,
)

FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _read_declared(path: Path) -> tuple[int | None, str | None]:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    m = FRONT_MATTER_RE.match(text)
    if not m:
        return None, None
    version: int | None = None
    contract_hash: str | None = None
    for line in m.group(1).splitlines():
        s = line.strip()
        if s.startswith("behavior_contract_version:"):
            try:
                version = int(s.split(":", 1)[1].strip())
            except ValueError:
                version = None
        elif s.startswith("behavior_contract_hash:"):
            contract_hash = s.split(":", 1)[1].strip()
            # value itself is "sha256:...", so re-join the tail
            contract_hash = s.split("behavior_contract_hash:", 1)[1].strip()
    return version, contract_hash


def main() -> int:
    version, contract_hash = canonical_version_and_hash()
    print(f"canonical version={version} hash={contract_hash}")

    failures = 0
    for path in PROJECTION_PATHS:
        rel = path.relative_to(REPO_ROOT)
        if not path.exists():
            print(f"FAIL  {rel}: missing projection")
            failures += 1
            continue
        dv, dh = _read_declared(path)
        if dv != version or dh != contract_hash:
            print(f"FAIL  {rel}: declared version={dv} hash={dh}")
            failures += 1
        else:
            print(f"ok    {rel}")

    if failures:
        print(
            f"\n{failures} projection(s) drifted. "
            "Re-review and run: python scripts/project_behavior_contract.py",
            file=sys.stderr,
        )
        return 1
    print("\nAll behavior projections match the canonical contract.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
