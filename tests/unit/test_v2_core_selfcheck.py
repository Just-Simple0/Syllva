"""R2-5: the packaged-Core self-check and the sidecar entry (implementation spec §13.6)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from syllva.cli.main import main
from syllva.core.selfcheck import PDF_TEXT, _tiny_pdf, self_check

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def test_self_check_reports_every_dependency() -> None:
    report = self_check()
    assert set(report["checks"]) == {"pypdf", "keyring", "sqlite", "uvicorn", "resources"}
    for name in ("pypdf", "sqlite", "uvicorn", "resources"):
        assert report["checks"][name]["ok"], report
    keyring_ok = importlib.util.find_spec("keyring") is not None
    assert report["checks"]["keyring"]["ok"] is keyring_ok
    assert report["ok"] is keyring_ok  # a bundle without keyring must fail the check


def test_tiny_pdf_is_readable() -> None:
    import io

    from pypdf import PdfReader

    assert PDF_TEXT in PdfReader(io.BytesIO(_tiny_pdf(PDF_TEXT))).pages[0].extract_text()


def test_cli_self_check_prints_json(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["core", "--self-check"])
    report = json.loads(capsys.readouterr().out)
    assert code == (0 if report["ok"] else 1)


def _entry():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("core_entry", ROOT / "packaging" / "core_entry.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("argv", [[], ["version"], ["intake", "list"], ["--stdio", "--headless"], ["core", "--headless"], ["setup"]])
def test_the_bundle_entry_runs_only_the_core(argv: list[str]) -> None:
    assert _entry().run(argv) == 2


def test_the_bundle_entry_accepts_the_shell_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    module = _entry()
    monkeypatch.setattr(module, "main", lambda args: seen.append(args) or 0)
    assert module.run(["core", "--stdio"]) == 0 and module.run(["--self-check"]) == 0
    assert seen == [["core", "--stdio"], ["core", "--self-check"]]
