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

Limit (by design of a ratchet): a pull request that raises a count AND edits
the baseline by hand to match is not caught here. Baseline increases must be
called out in the PR and rejected in review.

Rules (design §11 test ids in brackets):

* ``process-spawn`` [C-09]: ANY process-creating call (``subprocess.*``,
  ``os.system/popen/exec*/spawn*/posix_spawn*/startfile``, asyncio/loop
  ``subprocess_exec``/``subprocess_shell``/``create_subprocess_*``,
  ``pty.spawn``) and any dynamic import of ``subprocess``/``pty``, outside
  ``src/syllva/agents/launcher.py`` — regardless of arguments. Import aliases
  (``import subprocess as sp``, ``from subprocess import run``) are resolved.
  Importing ``subprocess``/``pty`` at all outside the launcher is also a finding,
  which closes bound references (``fn = subprocess.run``), subclassing
  (``class P(subprocess.Popen)``) and ``sys.modules`` lookups at the import site.
  Not detected: names built at runtime (``"sub" + "process"``), ``getattr``/
  ``globals()``, ctypes, ``os.fork``, ``multiprocessing``; reviewed by hand.
* ``write-sql-outside-state`` [C-03]: string literals (including the leading
  piece of an f-string or concatenation) shaped like a SQL write statement —
  INSERT INTO / REPLACE INTO / UPDATE … SET / DELETE FROM, also after leading
  ``--`` comments or an upper-case ``WITH`` clause — outside ``src/syllva/state/``.
  DDL is out of scope (schema lives in migrations). Prose that happens to read
  like a statement (``"update status set to ready"``) can match; such a hit is
  rare and visible in the baseline diff.
* ``ui-raw-html`` [C-18]: raw-HTML sinks in UI code: ``innerHTML``,
  ``outerHTML``, ``insertAdjacentHTML``, ``document.write``, ``setHTML``,
  ``createContextualFragment``, ``DOMParser``, ``srcdoc``, ``javascript:`` URLs,
  and inline ``on*=`` handlers in HTML files.
* ``core-stdout`` [spec §2.3]: ``print(`` and ``sys.stdout``/``sys.__stdout__``
  writes in ``src/syllva/core/``, whose stdout is reserved for the Shell protocol.
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

AGENT_LAUNCHER = "src/syllva/agents/launcher.py"
SPAWN_MODULES = frozenset({"subprocess", "pty"})
OS_SPAWN_FUNCTIONS = frozenset(
    {
        "system", "popen", "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp",
        "execlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "spawnl", "spawnle", "spawnlp",
        "spawnlpe", "posix_spawn", "posix_spawnp", "startfile",
    }
)
SPAWN_ATTRIBUTE_SUFFIXES = (
    "create_subprocess_exec", "create_subprocess_shell", "subprocess_exec", "subprocess_shell",
)
DYNAMIC_IMPORTERS = frozenset({"importlib.import_module", "__import__", "import_module"})

# Statement-shaped only: avoids prose like "Delete one entry" or "update target is missing".
_SQL_HEAD = (
    r"(?:"
    r"INSERT\s+(?:OR\s+\w+\s+)?INTO(?:\s|$)"
    r"|REPLACE\s+INTO(?:\s|$)"
    r"|UPDATE\s+(?:OR\s+\w+\s+)?(?:[\w\"\.]+\s+SET(?:\s|$)|$)"
    r"|DELETE\s+FROM(?:\s|$)"
    r")"
)
SQL_WRITE = re.compile(r"^\s*" + _SQL_HEAD, re.IGNORECASE)
SQL_CTE_WRITE = re.compile(r"^\s*WITH\b.*?\b(?:INSERT|UPDATE|DELETE|REPLACE)\b", re.DOTALL)  # upper-case WITH only
SQL_LEADING_COMMENTS = re.compile(r"^(?:\s*--[^\n]*\n)+")

RAW_HTML = re.compile(
    r"\b(?:innerHTML|outerHTML|insertAdjacentHTML|setHTML|createContextualFragment|DOMParser|srcdoc)\b"
    r"|\bdocument\.write(?:ln)?\s*\("
    r"|javascript:",
    re.IGNORECASE,
)
HTML_INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=\s*[\"']", re.IGNORECASE)
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


def _parse(path: Path, rel: str) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8-sig"), filename=rel)
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise SystemExit(f"check_invariants: cannot parse {rel}: {exc}") from exc


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr}" if inner else None
    return None


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    """Map local names to the fully qualified module/attribute they refer to."""

    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                aliases[local] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _qualify(name: str, aliases: dict[str, str]) -> str:
    head, _, rest = name.partition(".")
    if head in aliases:
        return aliases[head] + (f".{rest}" if rest else "")
    return name


def _is_spawn(qualified: str) -> bool:
    module, _, attr = qualified.rpartition(".")
    if module in SPAWN_MODULES or qualified.split(".")[0] in SPAWN_MODULES:
        return True
    if module == "os" and attr in OS_SPAWN_FUNCTIONS:
        return True
    return qualified.endswith(SPAWN_ATTRIBUTE_SUFFIXES)


def check_process_spawn(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in _python_files(root):
        rel = _rel(path, root)
        if rel == AGENT_LAUNCHER:
            continue
        tree = _parse(path, rel)
        aliases = _import_aliases(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in SPAWN_MODULES:
                        findings.append(Finding("process-spawn", rel, node.lineno, f"import {alias.name}"))
                continue
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.split(".")[0] in SPAWN_MODULES:
                    findings.append(Finding("process-spawn", rel, node.lineno, f"from {node.module} import"))
                continue
            if not isinstance(node, ast.Call):
                continue
            name = _dotted(node.func)
            if name is None:
                continue
            qualified = _qualify(name, aliases)
            if _is_spawn(qualified):
                findings.append(Finding("process-spawn", rel, node.lineno, qualified))
            elif qualified in DYNAMIC_IMPORTERS and any(
                isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.split(".")[0] in SPAWN_MODULES
                for arg in [*node.args, *(kw.value for kw in node.keywords)]
            ):
                findings.append(Finding("process-spawn", rel, node.lineno, f"{qualified}(dynamic)"))
    return findings


def _sql_write(text: str) -> bool:
    body = SQL_LEADING_COMMENTS.sub("", text, count=1)
    return bool(SQL_WRITE.match(body) or SQL_CTE_WRITE.match(body))


def check_write_sql(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in _python_files(root):
        rel = _rel(path, root)
        if rel.startswith("src/syllva/state/"):
            continue
        tree = _parse(path, rel)
        seen: set[int] = set()
        for node in ast.walk(tree):
            text: str | None = None
            if isinstance(node, ast.JoinedStr) and node.values:
                first = node.values[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    text = first.value
                    seen.add(id(first))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in seen:
                text = node.value
            if text is not None and _sql_write(text):
                findings.append(Finding("write-sql-outside-state", rel, node.lineno, text.strip().split()[0].upper()))
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
            rel = _rel(path, root)
            for lineno, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
                for match in RAW_HTML.finditer(line):
                    findings.append(Finding("ui-raw-html", rel, lineno, match.group(0)))
                if path.suffix == ".html":
                    for match in HTML_INLINE_HANDLER.finditer(line):
                        findings.append(Finding("ui-raw-html", rel, lineno, match.group(0).strip()))
    return findings


def check_core_stdout(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    base = root / "src" / "syllva" / "core"
    if not base.is_dir():
        return findings
    for path in sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts):
        rel = _rel(path, root)
        tree = _parse(path, rel)
        aliases = _import_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _dotted(node.func)
            qualified = _qualify(name, aliases) if name else None
            if qualified in {"print", "builtins.print"} or (
                qualified and qualified.startswith(("sys.stdout.", "sys.__stdout__."))
            ):
                findings.append(Finding("core-stdout", rel, node.lineno, qualified))
    return findings


RULES = {
    "process-spawn": check_process_spawn,
    "write-sql-outside-state": check_write_sql,
    "ui-raw-html": check_ui_raw_html,
    "core-stdout": check_core_stdout,
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
