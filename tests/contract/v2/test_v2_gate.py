"""R2-2: Human-Proof gate (implementation spec §6, design §5.1; C-04, C-05, C-17, C-19 Python half).

In R2 only VERIFY_MATERIAL is wired (errata E24); the other three actions are
refused at begin, while their summaries and fingerprint formats are fixed here
by vectors. Commit = recompute + writes inside ONE ``BEGIN IMMEDIATE`` with the
gate lock held throughout (plan rev 2, condition D).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import threading
from pathlib import Path

import pytest
from conftest import NOW

from syllva.core.secret import Secret
from syllva.gate import canonical, fingerprints, mac, summaries
from syllva.gate.service import GateError, GateService
from syllva.intake.v2 import LocalIntake
from syllva.library.blobs import BlobStore
from syllva.state.v2.database import Database

pytestmark = pytest.mark.contract

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SECRET = bytes(range(32))
SESSION, WINDOW = "sid-1", "main"


class Sessions:
    def __init__(self) -> None:
        self.live = {SESSION, "sid-2"}

    def alive(self, session_id: str) -> bool:
        return session_id in self.live


@pytest.fixture
def material(v2_db: Database) -> str:
    conn = v2_db.connection
    conn.execute("INSERT INTO courses(course_key, name, code, semester, created_at) VALUES ('NET', '컴퓨터네트워크', 'NET', '2026-2', ?)", (NOW,))
    intake = LocalIntake(v2_db.writer(), BlobStore(v2_db.layout.blobs_dir, v2_db.layout.tmp_dir))
    c = intake.classify(intake.receive(io.BytesIO(b"[00:00] TCP\n"), display_name="Lec 1.txt", now=NOW).candidate_id, now=NOW)
    intake.confirm(c.candidate_id, course_key="NET", kind="TRANSCRIPT", new_session_held_on="2026-09-01", now=NOW)
    intake.run_pending(now=NOW)
    return "NET-M01"


@pytest.fixture
def clock() -> list[float]:
    return [1000.0]


@pytest.fixture
def gate(v2_db: Database, clock: list[float]) -> tuple[GateService, Sessions]:
    sessions = Sessions()
    service = GateService(v2_db.writer(), sessions.alive, secret=Secret(SECRET), shell_version="0.0.0", clock=lambda: clock[0])
    return service, sessions


def _mac(nonce_id: str, request_hash: str) -> str:
    return base64.urlsafe_b64encode(mac.compute(SECRET, nonce_id, request_hash)).decode().rstrip("=")


def _begin(service: GateService, material: str, session: str = SESSION, window: str = WINDOW) -> str:
    return service.begin(session, window, "VERIFY_MATERIAL", material, {"material_id": material})


def _confirm(service: GateService, nonce: str, window: str = WINDOW) -> dict[str, object]:
    view = service.view(nonce, window)
    assert view["t"] == "gate.view", view
    return service.confirm(nonce, window, _mac(nonce, str(view["request_hash"])))


def _verified(v2_db: Database) -> list[tuple[int, int | None]]:
    return [tuple(r) for r in v2_db.connection.execute("SELECT verified, verified_gate_id FROM material_usage").fetchall()]


# --- pure parts: canonical JSON, MAC, summaries, fingerprints ------------------------------


def test_canonical_json_vectors() -> None:
    cases = json.loads((FIXTURES / "gate_canonical_vectors.json").read_text(encoding="utf-8"))
    for case in cases:
        assert canonical.dumps(case["input"]) == case["canonical"], case
    with pytest.raises(ValueError):
        canonical.dumps({"x": 1.5})
    assert canonical.dumps({"s": "é"}) == canonical.dumps({"s": "é"})  # NFC


def test_mac_vectors_shared_with_the_shell() -> None:
    for case in json.loads((FIXTURES / "gate_mac_vectors.json").read_text(encoding="utf-8")):
        key = bytes.fromhex(case["key_hex"])
        assert mac.compute(key, case["nonce_id"], case["request_hash"]).hex() == case["mac_hex"]
    data = b"syllva-gate-v1\x00" + (4).to_bytes(4, "big") + b"abcd" + (64).to_bytes(4, "big") + b"0" * 64
    assert mac.compute(SECRET, "abcd", "0" * 64) == hmac.new(SECRET, data, hashlib.sha256).digest()


def test_summaries_for_all_four_actions_are_pure_and_sanitised() -> None:
    assert summaries.summarize("VERIFY_MATERIAL", {"material_id": "NET-M01"}, {"title": "Lec\n1\x07", "course": "네트워크"}) == (
        "자료 NET-M01 \"Lec 1\"(네트워크)를 '검증됨'으로 표시합니다."
    )
    assert summaries.summarize("CONFIRM_EXAM_SCOPE", {"exam_id": "NET-E01", "material_ids": ["NET-M01", "NET-M02"]},
                               {"course": "네트워크", "exam_title": "중간고사"}) == "네트워크 중간고사(NET-E01)의 범위를 자료 2개로 확정합니다."
    assert summaries.summarize("SET_SUBMISSION_STATUS", {"activity_id": "NET-A02", "status": "SUBMITTED"},
                               {"activity_title": "HW2"}) == "과제 NET-A02 \"HW2\"의 제출 상태를 '제출함'으로 바꿉니다."
    assert summaries.summarize("ADOPT_AI_ARTIFACT", {"artifact_id": "A-0031"}, {"kind": "summary"}) == (
        "AI 요약 A-0031을(를) 내 노트로 채택합니다."
    )
    assert len(summaries.summarize("VERIFY_MATERIAL", {"material_id": "M"}, {"title": "x" * 500, "course": "c"})) < 140


def test_fingerprint_formats_for_all_four_actions() -> None:
    assert fingerprints.verify_material("sha256:ab", 3, "COMPLETE") == "sha256:ab|3|COMPLETE"
    scope = fingerprints.exam_scope([("NET-M02", "sha256:b"), ("NET-M01", "sha256:a")])
    assert scope == hashlib.sha256(b"NET-M01:sha256:a\nNET-M02:sha256:b").hexdigest()
    assert fingerprints.submission("NET-A02", "NOT_SUBMITTED", NOW) == f"NET-A02|NOT_SUBMITTED|{NOW}"
    assert fingerprints.adoption("A-0031", "c" * 64) == "A-0031|" + "c" * 64


# --- C-19: begin takes ids/enums only and returns only a nonce ------------------------------


def test_begin_returns_only_a_nonce(gate: tuple[GateService, Sessions], material: str) -> None:
    service, _ = gate
    nonce = _begin(service, material)
    assert isinstance(nonce, str) and len(bytes.fromhex(nonce)) == 16


@pytest.mark.parametrize("params", [{"material_id": "NET-M01", "summary": "x"}, {}, {"material_id": 5}, {"material_id": "a b"}])
def test_begin_refuses_anything_but_ids(gate: tuple[GateService, Sessions], material: str, params: dict[str, object]) -> None:
    with pytest.raises(GateError) as err:
        gate[0].begin(SESSION, WINDOW, "VERIFY_MATERIAL", material, params)
    assert err.value.code == "BAD_REQUEST"


@pytest.mark.parametrize("action", ["CONFIRM_EXAM_SCOPE", "SET_SUBMISSION_STATUS", "ADOPT_AI_ARTIFACT", "DROP_TABLES"])
def test_other_actions_are_not_available_yet(gate: tuple[GateService, Sessions], material: str, action: str) -> None:
    with pytest.raises(GateError) as err:
        gate[0].begin(SESSION, WINDOW, action, material, {"material_id": material})
    assert err.value.code in ("ACTION_NOT_AVAILABLE", "BAD_REQUEST")


def test_view_shows_the_core_summary_and_source_identity(gate: tuple[GateService, Sessions], material: str) -> None:
    service, _ = gate
    view = service.view(_begin(service, material), WINDOW)
    assert view["summary"] == "자료 NET-M01 \"Lec 1.txt\"(컴퓨터네트워크)를 '검증됨'으로 표시합니다."
    assert view["target_id"] == material and len(view["source_hash_prefixes"][0]) == 12  # type: ignore[index]
    assert view["source_ids"] and 0 < int(view["expires_in_ms"]) <= 60000  # type: ignore[arg-type]
    assert len(str(view["request_hash"])) == 64


# --- happy path and C-17 replay ---------------------------------------------------------------


def test_confirm_commits_once_and_replay_is_idempotent(gate: tuple[GateService, Sessions], material: str, v2_db: Database) -> None:
    service, _ = gate
    nonce = _begin(service, material)
    view = service.view(nonce, WINDOW)
    tag = _mac(nonce, str(view["request_hash"]))
    assert service.confirm(nonce, WINDOW, tag) == {"t": "gate.result", "nonce_id": nonce, "status": "COMMITTED"}
    assert service.confirm(nonce, WINDOW, tag)["status"] == "COMMITTED"  # idempotent
    assert len(_verified(v2_db)) == 1 and _verified(v2_db)[0][0] == 1
    log = v2_db.connection.execute("SELECT action, target_id, nonce_id, request_hash FROM human_gate_log").fetchall()
    assert [tuple(r) for r in log] == [("VERIFY_MATERIAL", material, nonce, view["request_hash"])]


def test_already_verified_material_cannot_be_begun_again(gate: tuple[GateService, Sessions], material: str) -> None:
    service, _ = gate
    _confirm(service, _begin(service, material))
    with pytest.raises(GateError) as err:
        _begin(service, material)
    assert err.value.code == "ALREADY_DONE"


def test_denied_and_rejected_cannot_be_replayed(gate: tuple[GateService, Sessions], material: str, clock: list[float]) -> None:
    service, _ = gate
    nonce = _begin(service, material)
    view = service.view(nonce, WINDOW)
    assert service.deny(nonce)["status"] == "DENIED"
    assert service.confirm(nonce, WINDOW, _mac(nonce, str(view["request_hash"])))["status"] == "DENIED"


# --- C-05: nonce rules -------------------------------------------------------------------------


def test_wrong_mac_is_rejected_and_ends_the_nonce(gate: tuple[GateService, Sessions], material: str, v2_db: Database) -> None:
    service, _ = gate
    nonce = _begin(service, material)
    service.view(nonce, WINDOW)
    result = service.confirm(nonce, WINDOW, _mac(nonce, "f" * 64))
    assert (result["status"], result["reason"]) == ("REJECTED", "BAD_MAC")
    assert _verified(v2_db) == []


def test_expired_nonce_is_refused(gate: tuple[GateService, Sessions], material: str, clock: list[float]) -> None:
    service, _ = gate
    nonce = _begin(service, material)
    view = service.view(nonce, WINDOW)
    clock[0] += 61
    assert service.confirm(nonce, WINDOW, _mac(nonce, str(view["request_hash"])))["status"] == "EXPIRED"
    assert service.view(nonce, WINDOW)["t"] == "gate.none"


def test_one_pending_per_session_and_cooldown(gate: tuple[GateService, Sessions], material: str, clock: list[float]) -> None:
    service, _ = gate
    nonce = _begin(service, material)
    with pytest.raises(GateError) as err:
        _begin(service, material)
    assert err.value.code == "GATE_BUSY"
    service.deny(nonce)
    with pytest.raises(GateError) as err:
        _begin(service, material)
    assert err.value.code == "GATE_COOLDOWN"
    clock[0] += 2.1
    assert _begin(service, material)


def test_concurrent_confirms_commit_exactly_once(gate: tuple[GateService, Sessions], material: str, v2_db: Database) -> None:
    service, _ = gate
    nonce = _begin(service, material)
    tag = _mac(nonce, str(service.view(nonce, WINDOW)["request_hash"]))
    results: list[dict[str, object]] = []
    barrier = threading.Barrier(6)

    def go() -> None:
        barrier.wait()
        results.append(service.confirm(nonce, WINDOW, tag))

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert {r["status"] for r in results} == {"COMMITTED"}
    assert v2_db.connection.execute("SELECT count(*) FROM human_gate_log").fetchone()[0] == 1


def test_target_change_after_begin_rejects_and_supersedes(gate: tuple[GateService, Sessions], material: str, v2_db: Database) -> None:
    service, _ = gate
    nonce = _begin(service, material)
    tag = _mac(nonce, str(service.view(nonce, WINDOW)["request_hash"]))
    intake = LocalIntake(v2_db.writer(), BlobStore(v2_db.layout.blobs_dir, v2_db.layout.tmp_dir))
    intake.replace(material, io.BytesIO(b"[00:00] UDP\n"), display_name="Lec 1 v2.txt", now=NOW)  # new source version
    result = service.confirm(nonce, WINDOW, tag)
    assert (result["status"], result["reason"], result.get("superseded")) == ("REJECTED", "TARGET_CHANGED", True)
    assert _verified(v2_db) == [] and v2_db.connection.execute("SELECT count(*) FROM human_gate_log").fetchone()[0] == 0


# --- C-04: binding to session and window; headless refusal ---------------------------------------


def test_another_window_cannot_view_or_confirm(gate: tuple[GateService, Sessions], material: str, v2_db: Database) -> None:
    service, _ = gate
    nonce = _begin(service, material)
    assert service.view(nonce, "notes") == {"t": "gate.none", "nonce_id": nonce, "reason": "WRONG_WINDOW"}
    real = service.view(nonce, WINDOW)
    result = service.confirm(nonce, "notes", _mac(nonce, str(real["request_hash"])))
    assert (result["status"], result["reason"]) == ("REJECTED", "WRONG_WINDOW")
    assert _verified(v2_db) == []
    assert _confirm(service, nonce)["status"] == "COMMITTED"  # the right window still can


def test_expired_session_cannot_confirm(gate: tuple[GateService, Sessions], material: str, v2_db: Database) -> None:
    service, sessions = gate
    nonce = _begin(service, material)
    tag = _mac(nonce, str(service.view(nonce, WINDOW)["request_hash"]))
    sessions.live.discard(SESSION)
    result = service.confirm(nonce, WINDOW, tag)
    assert (result["status"], result["reason"]) == ("REJECTED", "SESSION_GONE")
    assert _verified(v2_db) == []


def test_unknown_nonce_after_restart(v2_db: Database, material: str) -> None:
    fresh = GateService(v2_db.writer(), Sessions().alive, secret=Secret(SECRET), shell_version="0", clock=lambda: 0.0)
    assert fresh.view("00" * 16, WINDOW)["reason"] == "UNKNOWN"
    assert fresh.confirm("00" * 16, WINDOW, _mac("00" * 16, "0" * 64))["reason"] == "UNKNOWN"


def test_headless_core_has_no_gate(v2_db: Database, material: str) -> None:
    service = GateService(v2_db.writer(), Sessions().alive, secret=None, shell_version="0")
    with pytest.raises(GateError) as err:
        _begin(service, material)
    assert err.value.code == "GATE_UNAVAILABLE"


def test_human_only_columns_have_no_other_writer(v2_db: Database, material: str) -> None:
    """C-04: generic StateWriter paths still refuse human-only columns; commit_gate is the one path."""

    from syllva.domain.errors import PolicyViolation
    from syllva.state.v2.ownership import Actor, Owner

    for actor in (Actor.UI_USER, Actor.INGESTER, Actor.CORE_AGENT_OUTPUT):
        with pytest.raises(PolicyViolation):
            v2_db.writer().insert(Owner.CATALOG, actor, "material_usage", {"material_id": 1, "verified": 1})


def test_no_http_route_commits_a_gate() -> None:
    """C-04: commits happen only over stdio (gate.confirm); the HTTP app has begin and nothing else."""

    from syllva.ui import http

    source = Path(http.__file__).read_text(encoding="utf-8")
    assert "/api/gate/begin" in source and "confirm" not in source.replace("confirm_gate", "")


# --- end to end through the real Core process (HTTP begin, stdio confirm) ----------------------


def test_end_to_end_begin_over_http_confirm_over_stdio(tmp_path: Path) -> None:
    import sys

    import httpx

    from syllva.library.paths import layout
    from syllva.state.v2.database import open_database

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from support.shell_sim import ShellSim

    data = tmp_path / "data"
    with open_database(layout(data)) as db:  # prepare a library, then hand it to the Core
        db.connection.execute(
            "INSERT INTO courses(course_key, name, code, semester, created_at) VALUES ('NET', '네트워크', 'NET', '2026-2', ?)", (NOW,))
        intake = LocalIntake(db.writer(), BlobStore(db.layout.blobs_dir, db.layout.tmp_dir))
        c = intake.classify(intake.receive(io.BytesIO(b"[00:00] TCP\n"), display_name="a.txt", now=NOW).candidate_id, now=NOW)
        intake.confirm(c.candidate_id, course_key="NET", kind="TRANSCRIPT", new_session_held_on="2026-09-01", now=NOW)
        intake.run_pending(now=NOW)

    shell = ShellSim(data, secret=SECRET)
    try:
        ready = shell.hello()
        base, host = f"http://127.0.0.1:{ready['port']}", {"Host": f"127.0.0.1:{ready['port']}", "Origin": "syllva://localhost"}
        with httpx.Client(base_url=base, headers=host) as http:
            csrf = http.post("/api/session", json={"bootstrap": ready["bootstrap"]}).json()["csrf"]
            assert http.post("/api/gate/begin", json={"action": "VERIFY_MATERIAL", "target_id": "NET-M01",
                             "params": {"material_id": "NET-M01"}, "summary": "x"}, headers={"X-Syllva-CSRF": csrf}).status_code == 400
            begun = http.post("/api/gate/begin", json={"action": "VERIFY_MATERIAL", "target_id": "NET-M01",
                              "params": {"material_id": "NET-M01"}}, headers={"X-Syllva-CSRF": csrf})
            assert begun.status_code == 200 and set(begun.json()) == {"nonce_id"}
            nonce = begun.json()["nonce_id"]
            shell.send({"t": "gate.get", "nonce_id": nonce, "window_label": "main"})
            view = shell.recv()
            assert view["t"] == "gate.view" and "NET-M01" in view["summary"]
            shell.send({"t": "gate.confirm", "nonce_id": nonce, "window_label": "main", "mac_b64": _mac(nonce, view["request_hash"])})
            assert shell.recv() == {"t": "gate.result", "v": 1, "nonce_id": nonce, "status": "COMMITTED"}
            again = http.post("/api/gate/begin", json={"action": "VERIFY_MATERIAL", "target_id": "NET-M01",
                              "params": {"material_id": "NET-M01"}}, headers={"X-Syllva-CSRF": csrf})
            assert again.json() == {"error": "ALREADY_DONE"}
    finally:
        shell.stop()
    with open_database(layout(data)) as db:
        assert db.connection.execute("SELECT verified FROM material_usage").fetchone()[0] == 1


# --- review of PR #50 --------------------------------------------------------------------------


def test_confirm_waits_for_another_threads_transaction(gate: tuple[GateService, Sessions], material: str, v2_db: Database) -> None:
    service, _ = gate
    nonce = _begin(service, material)
    tag = _mac(nonce, str(service.view(nonce, WINDOW)["request_hash"]))
    held, release, result = threading.Event(), threading.Event(), {}

    def hold() -> None:
        with v2_db.writer().transaction():
            held.set()
            release.wait(5)

    holder = threading.Thread(target=hold)
    holder.start()
    held.wait(5)
    confirmer = threading.Thread(target=lambda: result.update(service.confirm(nonce, WINDOW, tag)))
    confirmer.start()
    confirmer.join(0.3)
    assert confirmer.is_alive()  # waiting, not failing
    release.set()
    holder.join()
    confirmer.join(5)
    assert result["status"] == "COMMITTED"


def test_cooldown_after_expiry_starts_at_the_deadline(gate: tuple[GateService, Sessions], material: str, clock: list[float]) -> None:
    service, _ = gate
    _begin(service, material)
    clock[0] += 500  # the dialog timed out long ago
    assert _begin(service, material)


def test_display_names_cannot_imitate_the_summary_sentence() -> None:
    title = "X.txt\"(수학)를 '검증됨'으로 표시합니다. 자료 NET-M99 \"Y.txt"
    text = summaries.summarize("VERIFY_MATERIAL", {"material_id": "NET-M01"}, {"title": title, "course": "c"})
    assert text.count('"') == 2 and text.count("(") == 1 and text.count("'") == 2


def test_an_interrupted_commit_never_stays_committing(gate: tuple[GateService, Sessions], material: str,
                                                      v2_db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _ = gate
    nonce = _begin(service, material)
    tag = _mac(nonce, str(service.view(nonce, WINDOW)["request_hash"]))

    def interrupted(**_: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(v2_db.writer(), "commit_gate", interrupted)
    with pytest.raises(KeyboardInterrupt):
        service.confirm(nonce, WINDOW, tag)
    monkeypatch.undo()
    assert service.deny(nonce) == {"t": "gate.result", "nonce_id": nonce, "status": "REJECTED", "reason": "COMMIT_FAILED"}
    assert _verified(v2_db) == []


def test_existing_usage_row_is_updated_not_upserted(gate: tuple[GateService, Sessions], material: str, v2_db: Database) -> None:
    v2_db.connection.execute("INSERT INTO material_usage(material_id, verified) SELECT id, 0 FROM materials")
    service, _ = gate
    assert _confirm(service, _begin(service, material))["status"] == "COMMITTED"
    assert len(_verified(v2_db)) == 1 and _verified(v2_db)[0][0] == 1


def test_commit_gate_has_one_caller() -> None:
    """C-04 defence in depth: only the gate service calls StateWriter.commit_gate."""

    src = Path(__file__).resolve().parents[3] / "src"
    callers = sorted(p.relative_to(src).as_posix() for p in src.rglob("*.py") if ".commit_gate(" in p.read_text(encoding="utf-8"))
    assert callers == ["syllva/gate/service.py"]
