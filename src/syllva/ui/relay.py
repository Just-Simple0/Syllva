"""Relay authentication (errata E29, R2 plan §7): the Core accepts only Shell-relayed requests.

The Shell attaches, for the window that made the request, ``X-Syllva-Window: <label>``
and ``X-Syllva-Relay: base64url(HMAC-SHA256(shell_secret, "syllva-relay-v1" || 0x00 ||
len32(label) || label))``. The MAC is static per window: it only proves "the Shell
speaks for window <label>" over loopback, which is all the threat model needs (design
§5.6). Expected MACs are cached per label so the secret is read once per window.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import threading

from syllva.core.secret import Secret

DOMAIN = b"syllva-relay-v1\x00"
WINDOW_LABEL = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")


def relay_mac(key: bytes, label: str) -> bytes:
    raw = label.encode("utf-8")
    return hmac.new(key, DOMAIN + len(raw).to_bytes(4, "big") + raw, hashlib.sha256).digest()


class RelayVerifier:
    def __init__(self, secret: Secret) -> None:
        self._secret = secret
        self._expected: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def verify(self, label: str, presented: str) -> bool:
        if not WINDOW_LABEL.fullmatch(label) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", presented):
            return False
        try:
            tag = base64.urlsafe_b64decode(presented + "=")
        except (binascii.Error, ValueError):
            return False
        with self._lock:
            cached = self._expected.get(label)
        expected = cached if cached is not None else relay_mac(self._secret.reveal(), label)
        if not hmac.compare_digest(expected, tag):
            return False
        if cached is None:
            with self._lock:  # cache only labels the Shell actually vouched for (bounded by real windows)
                self._expected[label] = expected
        return True


__all__ = ["RelayVerifier", "relay_mac"]
