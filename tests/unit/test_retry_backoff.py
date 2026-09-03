import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from uls.orchestration.retry import (
    DEFAULT_MAX_ATTEMPTS,
    ErrorClass,
    is_retryable,
    next_backoff,
    should_retry,
)


def test_backoff_is_bounded_and_retry_after_wins(monkeypatch) -> None:
    monkeypatch.setattr("uls.orchestration.retry.random.uniform", lambda _low, _high: 0.0)
    assert next_backoff(1, base=1.0, cap=5.0) == 1.0
    assert next_backoff(2, base=1.0, cap=5.0) == 2.0
    assert next_backoff(10, base=1.0, cap=5.0) == 5.0
    assert next_backoff(1, base=1.0, cap=5.0, retry_after=3.5) == 3.5
    assert next_backoff(1, base=1.0, cap=5.0, retry_after=50.0) == 5.0


def test_retry_classes_are_finite_and_non_retryable_classes_stop() -> None:
    assert is_retryable(ErrorClass.TRANSIENT)
    assert is_retryable("rate_limited")
    assert not is_retryable(ErrorClass.PERMANENT)
    assert not is_retryable(ErrorClass.AMBIGUOUS)
    assert not is_retryable(ErrorClass.POLICY_DENIED)
    assert should_retry(ErrorClass.TRANSIENT, 1)
    assert not should_retry(ErrorClass.TRANSIENT, DEFAULT_MAX_ATTEMPTS)
    assert not should_retry(ErrorClass.PERMANENT, 1)
