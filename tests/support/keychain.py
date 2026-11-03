"""Test-only in-memory keychain (R3-1): the real OS keychain is never touched by tests."""

from __future__ import annotations


class MemoryKeychain:
    def __init__(self, *, fail_set: bool = False, fail_delete: bool = False, wrong_readback: bool = False) -> None:
        self.entries: dict[tuple[str, str], str] = {}
        self.fail_set = fail_set
        self.fail_delete = fail_delete
        self.wrong_readback = wrong_readback

    def get_password(self, service: str, account: str) -> str | None:
        value = self.entries.get((service, account))
        return value + "x" if value is not None and self.wrong_readback else value

    def set_password(self, service: str, account: str, value: str) -> None:
        if self.fail_set:
            raise RuntimeError(f"backend refused {value}")  # carries the value: must never surface
        self.entries[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        if self.fail_delete:
            raise RuntimeError("backend refused delete")
        self.entries.pop((service, account), None)
