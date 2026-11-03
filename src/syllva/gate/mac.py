"""Shell MAC over (nonce_id, request_hash) (spec §6.3.2). Same vectors as the Rust shell."""

from __future__ import annotations

import hashlib
import hmac

DOMAIN = b"syllva-gate-v1\x00"


def _len32(data: bytes) -> bytes:
    return len(data).to_bytes(4, "big")


def compute(key: bytes, nonce_id: str, request_hash: str) -> bytes:
    nonce = nonce_id.encode("utf-8")
    digest = request_hash.encode("ascii")
    return hmac.new(key, DOMAIN + _len32(nonce) + nonce + _len32(digest) + digest, hashlib.sha256).digest()


def verify(key: bytes, nonce_id: str, request_hash: str, presented: bytes) -> bool:
    return hmac.compare_digest(compute(key, nonce_id, request_hash), presented)
