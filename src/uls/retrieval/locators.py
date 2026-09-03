"""Locator grammar/AST/containment — canonical implementation lives in the domain layer.

Per spec §3 the retrieval package exposes a ``locators`` module; per spec §6.3 the
normative Locator types, parser, serialization, and typed containment are domain types.
This module re-exports them so retrieval code imports from a stable location without
duplicating the authorization-critical logic.
"""

from __future__ import annotations

from uls.domain.models import (
    PageLocator,
    TimeLocator,
    is_contained,
    parse_locator,
    serialize_locator,
)

__all__ = [
    "PageLocator",
    "TimeLocator",
    "is_contained",
    "parse_locator",
    "serialize_locator",
]
