"""The network side of the Canvas settings (one object so tests can replace it; R3-1)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from syllva.canvas import http


class CanvasClient(Protocol):
    def verify(self, origin: str, token: str) -> Mapping[str, str]: ...

    def discover(self, origin: str, token: str, profile_id: str) -> Mapping[str, Any]: ...

    def reread(self, origin: str, token: str, course_ids: Iterable[str]) -> list[dict[str, str]]: ...


class LiveCanvasClient:
    """Read-only GETs to the user's Canvas origin (``syllva.canvas.http`` bounds and pinning)."""

    def __init__(self) -> None:
        self._gate = http.CanvasDiscoveryGate()

    def verify(self, origin: str, token: str) -> Mapping[str, str]:
        return http.verify_canvas_user(origin, token)

    def discover(self, origin: str, token: str, profile_id: str) -> Mapping[str, Any]:
        return http.discover_canvas_courses(origin, token, profile_id=profile_id, gate=self._gate)

    def reread(self, origin: str, token: str, course_ids: Iterable[str]) -> list[dict[str, str]]:
        return http.reread_canvas_courses(origin, token, course_ids)


__all__ = ["CanvasClient", "LiveCanvasClient"]
