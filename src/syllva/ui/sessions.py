"""UI sessions (implementation spec §10.2, §6.4-2, design §5.1(a)).

* bootstrap tokens: 256-bit, single use, 60 s, bound to a Shell window label;
  only the Shell can ask for one (stdio ``session.bootstrap``);
* exchanging a token creates a session (256-bit id for the cookie, 256-bit CSRF
  token returned in the body) bound to that window; a newer session for the
  same window replaces the older one (one session per window);
* sessions expire after 8 h idle and all vanish when the Core restarts;
* every comparison is constant time and every mutation holds one lock, so a
  token can be exchanged at most once even under concurrent requests.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass

BOOTSTRAP_TTL_S = 60.0
IDLE_TTL_S = 8 * 3600.0


def _key(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


@dataclass
class Session:
    session_id: str
    csrf: str
    window_label: str
    last_used: float

    def csrf_ok(self, presented: str | None) -> bool:
        return presented is not None and hmac.compare_digest(self.csrf.encode(), presented.encode())


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bootstrap: dict[bytes, tuple[str, float]] = {}  # sha256(token) -> (window, deadline)
        self._sessions: dict[bytes, Session] = {}  # sha256(sid) -> session
        self._by_window: dict[str, bytes] = {}

    def issue_bootstrap(self, window_label: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._prune()
            for key, (window, _) in list(self._bootstrap.items()):
                if window == window_label:
                    del self._bootstrap[key]  # one outstanding token per window
            self._bootstrap[_key(token)] = (window_label, time.monotonic() + BOOTSTRAP_TTL_S)
        return token

    def exchange(self, token: str) -> Session | None:
        if not isinstance(token, str) or not token:
            return None
        with self._lock:
            self._prune()
            entry = self._bootstrap.pop(_key(token), None)  # consumed whatever happens next
            if entry is None:
                return None
            window, deadline = entry
            if time.monotonic() > deadline:
                return None
            old = self._by_window.pop(window, None)
            if old is not None:
                self._sessions.pop(old, None)
            session = Session(secrets.token_urlsafe(32), secrets.token_urlsafe(32), window, time.monotonic())
            key = _key(session.session_id)
            self._sessions[key] = session
            self._by_window[window] = key
            return session

    def lookup(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        key = _key(session_id)
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                return None
            now = time.monotonic()
            if now - session.last_used > IDLE_TTL_S:
                self._drop(key)
                return None
            session.last_used = now
            return session

    def alive(self, session_id: str) -> bool:
        """Liveness without touching the idle clock (the gate re-checks at confirm, §6.4-2)."""

        with self._lock:
            session = self._sessions.get(_key(session_id))
            return session is not None and time.monotonic() - session.last_used <= IDLE_TTL_S

    def logout(self, session_id: str) -> None:
        with self._lock:
            self._drop(_key(session_id))

    def _prune(self) -> None:
        """Forget expired tokens and idle sessions (caller holds the lock)."""

        now = time.monotonic()
        for key in [k for k, (_, deadline) in self._bootstrap.items() if now > deadline]:
            del self._bootstrap[key]
        for key in [k for k, s in self._sessions.items() if now - s.last_used > IDLE_TTL_S]:
            self._drop(key)

    def counts(self) -> tuple[int, int]:
        with self._lock:
            return len(self._bootstrap), len(self._sessions)

    def _drop(self, key: bytes) -> None:
        session = self._sessions.pop(key, None)
        if session is not None and self._by_window.get(session.window_label) == key:
            del self._by_window[session.window_label]
