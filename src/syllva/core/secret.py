"""``shell_secret`` holder (spec §2.2, §6.1, I9): in memory only, never printable or serializable."""

from __future__ import annotations

import hmac
from typing import NoReturn


class Secret:
    __slots__ = ("_value",)

    def __init__(self, value: bytes) -> None:
        if not isinstance(value, bytes) or len(value) != 32:
            raise ValueError("shell_secret must be exactly 32 bytes")
        self._value = value

    def reveal(self) -> bytes:
        """Only the gate's MAC check calls this."""

        return self._value

    def matches(self, other: bytes) -> bool:
        return hmac.compare_digest(self._value, other)

    def __repr__(self) -> str:
        return "<secret>"

    __str__ = __repr__

    def __reduce__(self) -> NoReturn:
        raise TypeError("Secret cannot be pickled")

    def __getstate__(self) -> NoReturn:
        raise TypeError("Secret cannot be serialized")
