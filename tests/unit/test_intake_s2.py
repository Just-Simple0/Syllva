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
    assert "<email>" in out and "<phone>" in out and "<url>" in out and "<redacted>" in out


def test_minimized_payload_passes_the_final_check() -> None:
    payload = _payload(title="홍길동 20231234 report.pdf", excerpt="contact me: me@x.io https://a.b/c")
    serialized = str(payload.state)
    assert "me@x.io" not in serialized and "https://" not in serialized and "20231234" not in serialized


@pytest.mark.parametrize("excerpt", ["1 2 3 4 5 6 7 8 9 0", "9 8 7 - 6 5 4 - 3 2 1"])
def test_separator_evaded_digit_runs_are_pii_uncertain(excerpt) -> None:
    with pytest.raises(s2.PiiUncertain):
        _payload(excerpt=excerpt)


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
