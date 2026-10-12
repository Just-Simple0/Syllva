"""S2 pure helpers: minimization, payload, decision thresholds, cache key."""
from __future__ import annotations

import pytest

from uls.intake.classification import s2
from uls.intake.classification.taxonomy import Kind

pytestmark = pytest.mark.unit


def _payload(**over):
    args = {"title": "Week1.pdf", "extension": "pdf", "origin": "PROFESSOR_SOURCE", "course_key": "C1",
                "module_week": 1, "item_type": "module_file", "excerpt": "Binary search halves the range.", "limit": 6144}
    args.update(over)
    return s2.build_payload(**args)


def test_minimize_replaces_identifiers() -> None:
    text = ("Mail a.b@univ.ac.kr or call 010-1234-5678, ID 20231234, see https://x.example/a?b=1 "
            "학번: 20231234 이름: 홍길동")
    out = s2.minimize(text)
    for leaked in ("a.b@", "010-1234", "20231234", "x.example", "홍길동"):
        assert leaked not in out
    assert "<email>" in out and "<url>" in out and "<redacted>" in out and "<id>" in out


def test_minimized_payload_passes_the_final_check() -> None:
    payload = _payload(title="홍길동 20231234 report.pdf", excerpt="contact me: me@x.io https://a.b/c")
    serialized = str(payload.state)
    assert "me@x.io" not in serialized and "https://" not in serialized and "20231234" not in serialized


@pytest.mark.parametrize("excerpt", ["1 2 3 4 5 6 7 8 9 0", "9 8 7 - 6 5 4 - 3 2 1"])
def test_separator_evaded_digit_runs_are_removed(excerpt) -> None:
    assert not any(ch.isdigit() for ch in _payload(excerpt=excerpt).excerpt)


def test_excerpt_is_cut_at_the_limit_and_snapshot_hashes_the_sent_bytes() -> None:
    import hashlib

    payload = _payload(excerpt="가" * 5000, limit=300)
    assert len(payload.excerpt.encode()) <= 300
    assert payload.snapshot_sha256 == hashlib.sha256(payload.excerpt.encode()).hexdigest()
    assert _payload(limit=0).excerpt == ""


def test_excerpt_extraction() -> None:
    code = "\n".join(f"line {i}" for i in range(300)).encode()
    assert s2.extract_excerpt("a.py", "text/x-python", code).count("\n") == s2.CODE_LINES - 1
    assert s2.extract_excerpt("a.md", "text/markdown", b"hello") == "hello"
    assert s2.extract_excerpt("a.bin", "application/octet-stream", b"\x00\x01") == ""
    assert s2.extract_excerpt("a.md", "text/markdown", b"\xff\xfe") == ""
    assert s2.extract_excerpt("a.pdf", "application/pdf", b"%PDF-broken") == ""
    assert s2.extract_excerpt("a.md", None, None) == ""


def test_options_are_the_s2_kinds_plus_no_match() -> None:
    assert s2.OPTIONS[-1] == "no_match" and "UNSUPPORTED" not in s2.OPTIONS
    assert "RECORDING" not in s2.OPTIONS and "ANNOUNCEMENT" not in s2.OPTIONS


@pytest.mark.parametrize("choice,confidence,top,kind,note", [
    ("LECTURE_SLIDES", 0.83, 0.72, Kind.LECTURE_SLIDES, None),
    ("LECTURE_SLIDES", 0.80, 0.70, Kind.LECTURE_SLIDES, None),
    ("LECTURE_SLIDES", 0.79, 0.90, None, "LOW_CONFIDENCE"),
    ("LECTURE_SLIDES", 0.95, 0.69, None, "LOW_CONFIDENCE"),
    ("no_match", 0.99, 0.99, None, "CLASSIFIER_NO_MATCH"),
    ("UNSUPPORTED", 0.99, 0.99, None, "CLASSIFIER_NO_MATCH"),
])
def test_decision_thresholds(choice, confidence, top, kind, note) -> None:
    probabilities = {option: 0.0 for option in s2.OPTIONS}
    probabilities[choice if choice in probabilities else "no_match"] = top
    decision = s2.decide(choice, confidence, probabilities)
    assert (decision.kind, decision.note) == (kind, note)
    assert decision.record()["type"] == "model" and decision.record()["prompt_version"] == s2.PROMPT_VERSION


def test_cache_key_depends_on_content_not_on_the_surrogate_hash() -> None:
    base = {"byte_sha256": "a" * 64, "source_version": 1, "title": "t", "course_key": "C1", "module_week": 1,
                "item_type": "module_file", "origin": "PROFESSOR_SOURCE"}
    one = s2.cache_key(payload=_payload(excerpt="alpha"), **base)
    same = s2.cache_key(payload=_payload(excerpt="alpha"), **base)
    other = s2.cache_key(payload=_payload(excerpt="beta"), **base)
    assert one == same and one != other
    assert one != s2.cache_key(payload=_payload(excerpt="alpha"), **{**base, "byte_sha256": "b" * 64})
    assert one != s2.cache_key(payload=_payload(excerpt="alpha"), **{**base, "source_version": 2})


@pytest.mark.parametrize("text,leaked", [
    ("Name: John Smith", "Smith"), ("이름: 홍 길동", "길동"), ("성 명 : 김 철 수\nnext line", "철"),
    ("Student ID: 2023-1234", "2023"), ("ID: 2023-1234", "2023-1234"), ("학번 2023 1234", "1234"),
    ("no. 2023.1234.56", "1234"),
])
def test_labelled_and_separated_identifiers_are_removed_whole(text, leaked) -> None:
    assert leaked not in s2.minimize(text)
    payload = _payload(excerpt=text, title="Week1.pdf")
    assert leaked not in str(payload.state["excerpt"])


def test_dates_survive_minimization() -> None:
    assert s2.minimize("Lecture 2026-10-12 and 2026.09.17") == "Lecture 2026-10-12 and 2026.09.17"


def test_a_leftover_identifier_after_minimization_is_pii_uncertain(monkeypatch) -> None:
    monkeypatch.setattr(s2, "minimize", lambda text: text)
    for leaked in ("ID 2023-1234", "Name: Jane Roe", "see https://x.example/a", "lms.example.ac.kr/a?t=1", "a@b.co"):
        with pytest.raises(s2.PiiUncertain):
            _payload(excerpt=leaked)


@pytest.mark.parametrize("text,leaked", [
    ("Name:<redacted> Jane Doe", "Jane"), ("이름:<redacted> 홍길동", "홍길동"),
    ("2023\u20131234", "2023"), ("2023_1234", "1234"), ("010\u2013" + "1234\u20135678", "5678"),
    ("0 1 0\u200b-1234-5678", "5678"), ("\uff12\uff10\uff12\uff13-\uff11\uff12\uff13\uff14", "1234"),
    ("lms.example.ac.kr/courses/1234?access_token=MySecret", "MySecret"),
    ("//cdn.example.org/a?token=XYZ", "XYZ"), ("example.com:8080/x", "8080"),
])
def test_bypass_forms_never_reach_the_payload(text, leaked) -> None:
    payload = _payload(excerpt=text, title=text)
    assert leaked not in str(payload.state)


def test_source_text_cannot_forge_a_placeholder() -> None:
    out = s2.minimize("Name:<redacted> Jane")
    assert "Jane" not in out and "<" not in s2.minimize("<url> <id> x").replace("<url>", "").replace("<id>", "")


def test_ordinary_titles_survive() -> None:
    assert s2.minimize("Week1.pdf lec.v2.md Chapter 3") == "Week1.pdf lec.v2.md Chapter 3"


@pytest.mark.parametrize("text,leaked", [
    ("jane.doe@univ.ac.kr/assignments", "jane"), ("mail (jane.doe@univ.ac.kr), thanks", "jane"),
    ("학번 2023/13/40", "2023"), ("ID 2026-10-12", "2026"), ("Student ID: 2023.10.12", "2023"),
    ("010    1234    5678", "5678"), ("학번 2023    1234", "1234"), ("010 \u00a0 \u3000 1234 \u2014 5678", "5678"),
])
def test_round_three_bypasses_are_closed(text, leaked) -> None:
    payload = _payload(excerpt=text, title=text)
    assert leaked not in str(payload.state)


def test_valid_dates_survive_outside_an_id_context() -> None:
    assert s2.minimize("Lecture 2026-10-12 and 2026.09.17") == "Lecture 2026-10-12 and 2026.09.17"
    assert "2026" not in s2.minimize("학번 2026-10-12")


@pytest.mark.parametrize("text,leaked", [
    ("010\n1234\n5678", "5678"), ("2023\n1234", "1234"), ("학번\n2023\n\n1234", "1234"),
    ("10.0.0.1/private?access_token=MySecret", "MySecret"), ("localhost:8080/user?api_key=TopSecret", "TopSecret"),
    ("see 192.168.0.5:9000/x", "9000"), ("한글.대학교.kr/경로?토큰=비밀", "비밀"), ("page.html?token=abc", "abc"),
    ("Name = John Smith", "Smith"), ("이름 홍 길동", "길동"), ("Name - Jane Roe", "Roe"), ("성명 = 김철수", "철수"),
])
def test_round_four_bypasses_are_closed(text, leaked) -> None:
    payload = _payload(excerpt=text, title=text)
    assert leaked not in str(payload.state)


def test_plain_prose_and_filenames_survive_the_new_rules() -> None:
    text = "Name of the course is Operating Systems. See Week1.pdf and lec.v2.md, section 3.2."
    assert s2.minimize(text) == text


@pytest.mark.parametrize("text,leaked", [
    ("Student ID 654321", "654321"), ("Student ID A12345", "A12345"), ("student id  B-77", "B-77"),
    ("API_KEY=sk-example-AbCDEfgHijkLMNOP", "AbCDE"), ("Authorization: Bearer ghp_ExampleTokenABCDxyz", "ExampleToken"),
    ("access_token: abcdef123456", "abcdef123456"), ("password = hunter2", "hunter2"),
    ("use ghp_ExampleTokenABCDxyz now", "ExampleToken"), ("key AKIAIOSFODNN7EXAMPLE x", "AKIAIOSFODNN7EXAMPLE"),
    ("blob 0123456789abcdef0123456789abcdefXYZ end", "0123456789abcdef"),
    ("api key: secretvalue", "secretvalue"), ("Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
])
def test_round_five_identifiers_and_secrets_never_reach_the_payload(text, leaked) -> None:
    payload = _payload(excerpt=text, title=text)
    assert leaked not in str(payload.state)


def test_prose_about_ids_and_tokens_survives() -> None:
    text = "The student ID card is issued in week 2. A token ring network, and the secret of recursion."
    assert s2.minimize(text) == text
