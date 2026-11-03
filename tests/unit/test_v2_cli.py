"""The v2 CLI drives the local pipeline end to end (R1c; the desktop app replaces it in R2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from syllva.cli.main import main


def _run(capsys: pytest.CaptureFixture[str], data: Path, *args: str) -> tuple[int, list[dict[str, object]]]:
    code = main(["--data-dir", str(data), *args])
    out = capsys.readouterr().out
    return code, [json.loads(line) for line in out.splitlines() if line.strip()]


def test_drop_confirm_process_and_read_context(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "data"
    note = tmp_path / "note.txt"
    note.write_text("[00:00] 오늘은 TCP\n[01:00] handshake SYN ACK\n", encoding="utf-8")
    assert _run(capsys, data, "course", "add", "NET", "컴퓨터네트워크", "--semester", "2026-2")[0] == 0
    code, [added] = _run(capsys, data, "intake", "add", str(note), "--course", "NET")
    assert code == 0 and added["state"] == "NEEDS_REVIEW"
    code, [confirmed] = _run(
        capsys, data, "intake", "confirm", str(added["candidate_id"]), "--course", "NET", "--kind", "TRANSCRIPT",
        "--new-session", "2026-09-08",
    )
    assert confirmed["material_id"] == "NET-M01"
    code, [run] = _run(capsys, data, "intake", "run")
    assert [c["state"] for c in run["processed"]] == ["READY"]  # type: ignore[index, union-attr]
    code, [context] = _run(capsys, data, "context", "NET-M01", "--query", "handshake")
    assert [e["locator"] for e in context["evidence"]] == ["NET-M01:t00:01:00"]  # type: ignore[index, union-attr]


def test_errors_are_codes_on_stderr(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--data-dir", str(tmp_path / "d"), "intake", "dismiss", "INT-none"])
    captured = capsys.readouterr()
    assert code == 2 and json.loads(captured.err)["error"] == "INTAKE_INVALID" and not captured.out


def test_ordinary_failures_are_codes_not_tracebacks(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "d"
    assert main(["--data-dir", str(data), "course", "add", "NET", "n", "--semester", "2026-2"]) == 0
    capsys.readouterr()
    assert main(["--data-dir", str(data), "course", "add", "NET", "n", "--semester", "2026-2"]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "CONFLICT"
    assert main(["--data-dir", str(data), "intake", "add", str(tmp_path / "missing.txt")]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "FILE_ERROR"
