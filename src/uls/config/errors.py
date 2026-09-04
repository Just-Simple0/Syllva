"""Structured configuration failures."""

from __future__ import annotations

from collections.abc import Iterable

from uls.domain.errors import UlsError


class ConfigurationError(UlsError):
    """Raised when a parsed configuration fails the v1.2 safety contract."""

    code = "CONFIGURATION_INVALID"

    def __init__(self, problems: Iterable[str] | str) -> None:
        if isinstance(problems, str):
            normalized = [problems]
        else:
            normalized = [str(problem) for problem in problems]
        if not normalized:
            normalized = ["configuration is invalid"]
        super().__init__(
            "Invalid configuration: " + "; ".join(normalized),
            details={"problems": normalized},
        )


__all__ = ["ConfigurationError"]
