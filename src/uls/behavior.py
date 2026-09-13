"""Installed-package and checkout Behavior Contract validation."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path


def asset_root() -> Path:
    packaged = Path(__file__).resolve().parent / 'data'
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[2]


def lint_behavior(contract: Path | None = None) -> list[str]:
    root = asset_root()
    contract = contract or root / 'contracts/study-behavior.md'
    content = contract.read_text(encoding='utf-8').replace('\r\n', '\n').replace('\r', '\n')
    match = re.search(r'^behavior_contract_version: (\d+)\s*$', content, re.MULTILINE)
    if not match:
        return ['Canonical contract version is missing']
    version = match.group(1)
    digest = 'sha256:' + hashlib.sha256(content.encode()).hexdigest()
    paths = list((root / 'clients/claude/skills').glob('*/SKILL.md'))
    paths.append(root / 'clients/chatgpt/instructions/study-behavior.md')
    problems = []
    if len(paths) != 6:
        problems.append('Expected six client projections')
    for path in paths:
        text = path.read_text(encoding='utf-8')
        front = text.split('---', 2)
        if (not text.startswith('---\n') or len(front) != 3
                or re.findall(r'^behavior_contract_version: (\d+)\s*$', front[1], re.MULTILINE) != [version]
                or re.findall(r'^behavior_contract_hash: (sha256:[a-f0-9]{64})\s*$', front[1], re.MULTILINE) != [digest]):
            problems.append('Projection drift: ' + path.name)
    return problems
