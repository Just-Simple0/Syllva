"""Static invariant gates for Syllva v2 (implementation spec §1.3, §13.5, §14.1).

Covers the static parts of C-03 (write SQL only in ``state/``), C-09 (single
agent spawn path), C-18 (no raw-HTML sinks in UI code) and the import-direction
ratchet. The rule engine is ``scripts/check_invariants.py``; these tests run it
on the real tree and exercise each detector and the ratchet on synthetic trees.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "check_invariants.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("syllva_check_invariants", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return tmp_path


def _rules(findings: dict[str, list]) -> dict[str, list[tuple[str, int]]]:
    return {rule: [(f.path, f.line) for f in items] for rule, items in findings.items() if items}


# --- the real repository -----------------------------------------------------

def test_repository_matches_invariant_baseline(capsys: pytest.CaptureFixture[str]) -> None:
    assert checker.main([]) == 0, capsys.readouterr().err


def test_baseline_only_lists_known_rules_and_positive_counts() -> None:
    raw = json.loads(checker.BASELINE_PATH.read_text(encoding="utf-8"))
    assert set(raw["rules"]) <= set(checker.RULES)
    for per_file in raw["rules"].values():
        assert per_file and all(isinstance(n, int) and n > 0 for n in per_file.values())


def test_no_process_spawn_and_no_raw_html_in_real_tree() -> None:
    findings = checker.collect(ROOT)
    assert findings["process-spawn"] == []
    assert findings["ui-raw-html"] == []
    assert findings["core-stdout"] == []


def test_import_linter_contracts_hold() -> None:
    from importlinter.cli import lint_imports  # dev dependency: must not be skipped

    assert lint_imports(config_filename=str(ROOT / "pyproject.toml")) == 0


# --- detectors on synthetic trees -------------------------------------------

def test_process_spawn_detector_flags_every_spawn_outside_launcher(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "src/syllva/ui/chat.py": "import subprocess\nsubprocess.run(['claude', '-p'])\n",
            "src/syllva/x/shell.py": "import os\nos.system('codex exec hi')\n",
            "src/syllva/y/aio.py": "import asyncio\nasync def f():\n    await asyncio.create_subprocess_exec('/usr/local/bin/claude')\n",
            # aliases and variables must not bypass the rule
            "src/syllva/a/alias.py": "import subprocess as sp\nsp.run([CLI])\n",
            "src/syllva/a/fromimp.py": "from subprocess import run as go\ngo(argv)\n",
            "src/syllva/a/which.py": "import shutil, subprocess\nexe = shutil.which('claude')\nsubprocess.Popen([exe])\n",
            "src/syllva/a/loop.py": "async def f(loop):\n    await loop.subprocess_exec(proto, cmd)\n",
            "src/syllva/a/osfrom.py": "from os import execvp\nexecvp(p, args)\n",
            "src/syllva/a/dyn.py": "import importlib\nsp = importlib.import_module('subprocess')\n",
            "src/syllva/a/pty_.py": "import pty\npty.spawn(cmd)\n",
            # bound references, subclassing and keyword dynamic imports are caught at the import site
            "src/syllva/b/bound.py": "import subprocess\nfn = subprocess.run\n",
            "src/syllva/b/subclass.py": "from subprocess import Popen\nclass P(Popen):\n    pass\n",
            "src/syllva/b/kwdyn.py": "from importlib import import_module\nm = import_module(name='subprocess')\n",
            # an unrelated call is fine, and the launcher is the one allowed place
            "src/syllva/z/ok.py": "import webbrowser\nwebbrowser.open('http://127.0.0.1')\n",
            "src/syllva/agents/launcher.py": "import subprocess\nsubprocess.Popen(['claude'])\n",
        },
    )
    found = _rules(checker.collect(root))
    assert sorted({path for path, _ in found["process-spawn"]}) == [
        "src/syllva/a/alias.py",
        "src/syllva/a/dyn.py",
        "src/syllva/a/fromimp.py",
        "src/syllva/a/loop.py",
        "src/syllva/a/osfrom.py",
        "src/syllva/a/pty_.py",
        "src/syllva/a/which.py",
        "src/syllva/b/bound.py",
        "src/syllva/b/kwdyn.py",
        "src/syllva/b/subclass.py",
        "src/syllva/ui/chat.py",
        "src/syllva/x/shell.py",
        "src/syllva/y/aio.py",
    ]


def test_write_sql_detector_ignores_state_and_prose(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "src/syllva/state/store.py": "Q = 'INSERT INTO t VALUES (1)'\n",
            "src/syllva/ui/bad.py": "Q = '''\n  UPDATE materials SET verified = 1 WHERE id = ?'''\nR = \"delete from t\"\n",
            "src/syllva/ui/prose.py": "def f():\n    \"\"\"Delete one entry.\"\"\"\n    raise ValueError('update target is missing')\n    x = 'with the update below'\n",
            "src/syllva/ui/fstr.py": "q = f'UPDATE {table} SET a = 1'\n",
            "src/syllva/ui/concat.py": "q = 'UPDATE ' + table + ' SET a = 1'\n",
            "src/syllva/ui/cte.py": "q = 'WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x'\n",
            "src/syllva/ui/comment.py": "q = '-- bump\\nDELETE FROM t'\n",
            "src/syllva/ui/select.py": "q = 'SELECT * FROM t WHERE x = ?'\n",
        },
    )
    found = _rules(checker.collect(root))
    assert sorted(found["write-sql-outside-state"]) == [
        ("src/syllva/ui/bad.py", 1),
        ("src/syllva/ui/bad.py", 3),
        ("src/syllva/ui/comment.py", 1),
        ("src/syllva/ui/concat.py", 1),
        ("src/syllva/ui/cte.py", 1),
        ("src/syllva/ui/fstr.py", 1),
    ]


def test_ui_raw_html_detector(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "src/syllva/ui/static/app.js": "el.textContent = x;\nel.innerHTML = x;\ndocument.write(x);\n",
            "src/syllva/settings/static/old.js": "node.insertAdjacentHTML('beforeend', s);\n",
            "src/syllva/ui/static/more.js": "el['innerHTML'] = s;\nel.setHTML(s);\nr.createContextualFragment(s);\nnew DOMParser();\nf.srcdoc = s;\na.href = 'javascript:x()';\nbutton.onclick = handler;\n",
            "src/syllva/ui/static/index.html": "<button onclick=\"go()\">x</button>\n<p>onboarding = fine</p>\n",
        },
    )
    found = _rules(checker.collect(root))
    assert sorted(found["ui-raw-html"]) == [
        ("src/syllva/settings/static/old.js", 1),
        ("src/syllva/ui/static/app.js", 2),
        ("src/syllva/ui/static/app.js", 3),
        ("src/syllva/ui/static/index.html", 1),
        ("src/syllva/ui/static/more.js", 1),
        ("src/syllva/ui/static/more.js", 2),
        ("src/syllva/ui/static/more.js", 3),
        ("src/syllva/ui/static/more.js", 4),
        ("src/syllva/ui/static/more.js", 5),
        ("src/syllva/ui/static/more.js", 6),
    ]


def test_core_stdout_detector(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "src/syllva/core/main.py": "import sys\nprint('ready')\nsys.stdout.write('x')\nfrom sys import stdout as out\nout.write('y')\n",
            "src/syllva/cli/x.py": "print('ok')\n",
        },
    )
    assert _rules(checker.collect(root)) == {
        "core-stdout": [("src/syllva/core/main.py", 2), ("src/syllva/core/main.py", 3), ("src/syllva/core/main.py", 5)]
    }


def test_bom_and_unparseable_files(tmp_path: Path) -> None:
    root = _tree(tmp_path, {})
    target = root / "src/syllva/ui/bom.py"
    target.parent.mkdir(parents=True)
    target.write_bytes("\ufeffQ = 'DELETE FROM t'\n".encode())
    assert _rules(checker.collect(root)) == {"write-sql-outside-state": [("src/syllva/ui/bom.py", 1)]}
    (root / "src/syllva/ui/broken.py").write_text("def (:\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="cannot parse src/syllva/ui/broken.py"):
        checker.collect(root)


# --- ratchet semantics -------------------------------------------------------

def test_ratchet_rejects_regressions_and_stale_entries(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"src/syllva/ui/a.py": "Q = 'DELETE FROM t'\nR = 'DELETE FROM u'\n"})
    current = checker.counts(checker.collect(root))
    assert checker.compare(current, {r: {} for r in checker.RULES})[0].startswith("REGRESSION")
    exact = {r: {} for r in checker.RULES} | {"write-sql-outside-state": {"src/syllva/ui/a.py": 2}}
    assert checker.compare(current, exact) == []
    stale = {r: {} for r in checker.RULES} | {"write-sql-outside-state": {"src/syllva/ui/a.py": 3, "src/syllva/gone.py": 1}}
    problems = checker.compare(current, stale)
    assert len(problems) == 2 and all(p.startswith("STALE BASELINE") for p in problems)


def test_write_baseline_refuses_increase_without_flag(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"src/syllva/ui/a.py": "Q = 'DELETE FROM t'\n"})
    baseline_path = tmp_path / "baseline.json"
    with pytest.raises(SystemExit):
        checker.main(["--write-baseline"], root=root, baseline_path=baseline_path)
    assert not baseline_path.exists()
    assert checker.main(["--write-baseline", "--allow-increase"], root=root, baseline_path=baseline_path) == 0
    assert checker.main([], root=root, baseline_path=baseline_path) == 0
    shutil.rmtree(root / "src")
    (root / "src/syllva").mkdir(parents=True)
    assert checker.main([], root=root, baseline_path=baseline_path) == 1  # stale entry must be lowered
    assert checker.main(["--write-baseline"], root=root, baseline_path=baseline_path) == 0  # lowering is allowed
    assert json.loads(baseline_path.read_text(encoding="utf-8"))["rules"] == {}
