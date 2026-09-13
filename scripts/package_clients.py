"""Build an explicit client distribution after contract drift validation."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]


def package(output: Path) -> None:
    subprocess.run([sys.executable, str(ROOT / 'scripts/lint_behavior_projection.py')], check=True)
    files = [ROOT / 'contracts/study-behavior.md',
             ROOT / 'clients/README.md', ROOT / 'clients/support-matrix.json',
             ROOT / 'clients/e2e-checklist.md', ROOT / 'clients/claude/mcp-config.example.json',
             ROOT / 'clients/chatgpt/instructions/study-behavior.md']
    files.extend(sorted((ROOT / 'clients/claude/skills').glob('*/SKILL.md')))
    if any(path.is_symlink() for path in files):
        raise ValueError('package inputs must not be symlinks')
    with ZipFile(output, 'x', compression=ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    package(parser.parse_args().output)
