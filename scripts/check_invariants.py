#!/usr/bin/env python3
"""Static invariant checks for Syllva v2 (implementation spec §13.5, §14.1).

Each rule scans the source tree and counts violations per file. The counts are
compared with ``scripts/invariants_baseline.json``, which is a RATCHET:

* a file that is not in the baseline must have zero violations;
* a file in the baseline must have exactly the recorded count — fewer means the
  baseline is stale and must be lowered (``--write-baseline``), more is a
  regression.

``--write-baseline`` refuses to raise any count unless ``--allow-increase`` is
given (used once, when the rule set is introduced).

Rules (design §11 test ids in brackets):

* ``agent-spawn`` [C-09]: process-spawning calls whose literal arguments name an
  agent CLI (``claude``, ``codex``) outside ``src/syllva/agents/launcher.py``.
* ``write-sql-outside-state`` [C-03]: string literals that start with a SQL
  write statement (INSERT/UPDATE/DELETE/REPLACE) outside ``src/syllva/state/``.
* ``ui-raw-html`` [C-18]: ``innerHTML``/``outerHTML``/``insertAdjacentHTML``/
  ``document.write`` in UI JavaScript.
* ``core-stdout-print`` [spec §2.3]: ``print(`` in ``src/syllva/core/``, whose
  stdout is reserved for the Shell protocol.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "syllva"
BASELINE_PATH = ROOT / "scripts" / "invariants_baseline.json"

AGENT_EXECUTABLES = frozenset({"claude", "codex", "claude.exe", "codex.exe", "claude.cmd", "codex.cmd"})
AGENT_LAUNCHER = "src/syllva/agents/launcher.py"
SPAWN_CALLS = frozenset(
    {
        "subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_call",
        "subprocess.check_output", "subprocess.getoutput", "subprocess.getstatusoutput",
        "os.system", "os.popen", "os.execv", "os.execve", "os.execvp", "os.execvpe",
        "os.execl", "os.execle", "os.execlp", "os.execlpe", "os.spawnv", "os.spawnve",
        "os.spawnvp", "os.spawnvpe", "os.spawnl", "os.spawnle", "os.spawnlp", "os.spawnlpe",
        "os.posix_spawn", "os.posix_spawnp", "os.startfile",
        "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell", "pty.spawn",
    }
)
# Statement-shaped only: avoids prose like "Delete one entry" or "update target is missing".
SQL_WRITE = re.compile(
    r"^\s*(?:"
    r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s"
    r"|REPLACE\s+INTO\s"
    r"|UPDATE\s+(?:OR\s+\w+\s+)?[\w\"\.]+\s+SET\s"
    r"|DELETE\s+FROM\s"
    r")",
    re.IGNORECASE,
)
RAW_HTML = re.compile(r"\b(innerHTML|outerHTML|insertAdjacentHTML)\b|\bdocument\.write\s*\(")
UI_JS_DIRS = ("src/syllva/settings/static", "src/syllva/ui/static", "shell/src")


@dataclass(frozen=True)
class Finding:
    rule: str
    path: str  # repository-relative, POSIX separators
    line: int
    detail: str


def _rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _python_files(root: Path) -> Iterator[Path]:
    base = root / "src" / "syllva"
    if base.is_dir():
        yield from sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr}" if inner else None
    return None


def _string_literals(node: ast.AST) -> Iterator[str]:
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            yield child.value


def _names_agent(value: str) -> bool:
    for token in re.split(r"[\s/\\]+", value.strip()):
        if token.lower() in AGENT_EXECUTABLES:
            return True
    return False


def check_agent_spawn(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in _python_files(root):
        rel = _rel(path, root)
        if rel == AGENT_LAUNCHER:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _dotted(node.func)
            if name is None or (name not in SPAWN_CALLS and not name.endswith(("create_subprocess_exec", "create_subprocess_shell"))):
                continue
            arguments = [*node.args, *(kw.value for kw in node.keywords)]
            if any(_names_agent(text) for arg in arguments for text in _string_literals(arg)):
                findings.append(Finding("agent-spawn", rel, node.lineno, name))
    return findings


def check_write_sql(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in _python_files(root):
        rel = _rel(path, root)
        if rel.startswith("src/syllva/state/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and SQL_WRITE.match(node.value):
                findings.append(Finding("write-sql-outside-state", rel, node.lineno, node.value.strip().split()[0].upper()))
    return findings


def check_ui_raw_html(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for directory in UI_JS_DIRS:
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in {".js", ".mjs", ".ts", ".html"} or "node_modules" in path.parts:
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                for match in RAW_HTML.finditer(line):
                    findings.append(Finding("ui-raw-html", _rel(path, root), lineno, match.group(0)))
    return findings


def check_core_stdout(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    base = root / "src" / "syllva" / "core"
    if not base.is_dir():
        return findings
    for path in sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts):
        rel = _rel(path, root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _dotted(node.func) == "print":
                findings.append(Finding("core-stdout-print", rel, node.lineno, "print"))
    return findings


RULES = {
    "agent-spawn": check_agent_spawn,
    "write-sql-outside-state": check_write_sql,
    "ui-raw-html": check_ui_raw_html,
    "core-stdout-print": check_core_stdout,
}


def collect(root: Path = ROOT) -> dict[str, list[Finding]]:
    return {rule: check(root) for rule, check in RULES.items()}


def counts(findings: dict[str, list[Finding]]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {rule: {} for rule in RULES}
    for rule, items in findings.items():
        for item in items:
            result[rule][item.path] = result[rule].get(item.path, 0) + 1
    return {rule: dict(sorted(per_file.items())) for rule, per_file in result.items()}


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, dict[str, int]]:
    if not path.exists():
        return {rule: {} for rule in RULES}
    raw = json.loads(path.read_text(encoding="utf-8"))
    rules = raw.get("rules", {})
    unknown = set(rules) - set(RULES)
    if unknown:
        raise SystemExit(f"baseline has unknown rules: {sorted(unknown)}")
    return {rule: {str(k): int(v) for k, v in rules.get(rule, {}).items()} for rule in RULES}


def compare(current: dict[str, dict[str, int]], baseline: dict[str, dict[str, int]]) -> list[str]:
    problems: list[str] = []
    for rule in RULES:
        now, allowed = current.get(rule, {}), baseline.get(rule, {})
        for path in sorted(set(now) | set(allowed)):
            have, may = now.get(path, 0), allowed.get(path, 0)
            if have > may:
                problems.append(f"REGRESSION {rule}: {path} has {have} (baseline {may})")
            elif have < may:
                problems.append(f"STALE BASELINE {rule}: {path} has {have} (baseline {may}); lower it with --write-baseline")
    return problems


def write_baseline(current: dict[str, dict[str, int]], baseline: dict[str, dict[str, int]], *, allow_increase: bool, path: Path = BASELINE_PATH) -> None:
    if not allow_increase:
        increases = [
            f"{rule}: {file} {baseline.get(rule, {}).get(file, 0)} -> {n}"
            for rule, per_file in current.items()
            for file, n in per_file.items()
            if n > baseline.get(rule, {}).get(file, 0)
        ]
        if increases:
            raise SystemExit("refusing to raise baseline counts:\n  " + "\n  ".join(increases))
    payload = {
        "_comment": "Ratchet for scripts/check_invariants.py. Counts may only decrease. Empty by end of R1b where the spec requires.",
        "rules": {rule: per_file for rule, per_file in current.items() if per_file},
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None, *, root: Path = ROOT, baseline_path: Path = BASELINE_PATH) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--allow-increase", action="store_true")
    parser.add_argument("--verbose", action="store_true", help="list every finding")
    args = parser.parse_args(argv)

    findings = collect(root)
    current = counts(findings)
    baseline = load_baseline(baseline_path)
    if args.write_baseline:
        write_baseline(current, baseline, allow_increase=args.allow_increase, path=baseline_path)
        print(f"baseline written: {baseline_path}")
        return 0
    if args.verbose:
        for items in findings.values():
            for item in items:
                print(f"{item.rule}\t{item.path}:{item.line}\t{item.detail}")
    problems = compare(current, baseline)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    total = sum(sum(per_file.values()) for per_file in current.values())
    print(f"invariants OK ({total} baselined findings across {len(RULES)} rules)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
