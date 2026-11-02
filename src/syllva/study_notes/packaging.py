"""Bounded prepared-context packaging.

Applies a TOTAL budget across the whole prepared package (plus a
membership-count cap independent of character budget) while never
silently pretending the result is complete: any budget/membership trim
forces effective coverage to PARTIAL, and the citable locator list only
ever contains fully-delivered chunks -- a truncated chunk is omitted
WHOLE, never sliced and still labeled with its full locator (a partial
slice under a full locator would misrepresent what was actually seen).
The complete dependency membership (every locator that exists in the
manifest, delivered or not) is kept separately so freshness/completeness
reasoning never loses track of what was authorized even when not sent.
Per-chunk metadata beyond locator/content (source_class, verified,
partial, etc., as the graph adapter supplies) is preserved verbatim on
delivered chunks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

DEFAULT_MAX_TOTAL_CONTEXT_CHARS = 24_000
DEFAULT_MAX_USAGE_COUNT = 50


class _Budget:
    def __init__(self, max_total_chars: int) -> None:
        self.remaining = max_total_chars
        self.truncated = False
        self.all_locators: list[str] = []
        self.delivered_locators: list[str] = []

    def bind(self, chunks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        delivered: list[dict[str, Any]] = []
        for chunk in chunks:
            locator = chunk.get("locator")
            if locator:
                self.all_locators.append(locator)
            content = chunk.get("content") or ""
            if self.remaining <= 0 or len(content) > self.remaining:
                # Whole-chunk omission only -- never a partial slice kept
                # under its full locator label.
                self.truncated = True
                continue
            self.remaining -= len(content)
            if locator:
                self.delivered_locators.append(locator)
            extra = {k: v for k, v in chunk.items() if k not in ("locator", "content")}
            delivered.append({"locator": locator, "content": content, **extra})
        return delivered


def bounded_context(
    manifest: Any, *,
    max_total_chars: int = DEFAULT_MAX_TOTAL_CONTEXT_CHARS,
    max_usage_count: int = DEFAULT_MAX_USAGE_COUNT,
) -> dict[str, Any]:
    budget = _Budget(max_total_chars)
    transcript_ctx: dict[str, Any] | None = None
    if manifest.transcript is not None:
        transcript_ctx = {
            "source_hash": manifest.transcript.source_hash,
            "source_version": manifest.transcript.source_version,
            "partial": manifest.transcript.partial,
            "chunks": budget.bind(manifest.transcript.chunks),
        }

    usages_ctx: list[dict[str, Any]] = []
    membership_truncated = False
    for index, u in enumerate(manifest.usages):
        base = {
            "material_app_id": u.material_app_id,
            "usage_app_id": u.usage_app_id,
            "provider_page_id": u.provider_page_id,
            "usage_role": u.usage_role,
            "verified": u.verified,
            "material_type": u.material_type,
            "read": u.read,
            "partial": u.partial,
            "start_page": u.start_page,
            "end_page": u.end_page,
        }
        if index >= max_usage_count:
            membership_truncated = True
            for chunk in u.chunks:
                locator = chunk.get("locator")
                if locator:
                    budget.all_locators.append(locator)
            usages_ctx.append({**base, "chunks": [], "omitted_for_membership_cap": True})
            continue
        usages_ctx.append({**base, "chunks": budget.bind(u.chunks), "omitted_for_membership_cap": False})

    any_trim = budget.truncated or membership_truncated
    effective_coverage = "PARTIAL" if (manifest.coverage == "PARTIAL" or any_trim) else "FULL"
    return {
        "evidence_mode": manifest.evidence_mode,
        "coverage": effective_coverage,
        "missing_dependencies": list(manifest.missing_dependencies),
        "transcript": transcript_ctx,
        "usages": usages_ctx,
        "locators": budget.delivered_locators,
        "all_locators": budget.all_locators,
        "budget_truncated": any_trim,
    }


__all__ = ["DEFAULT_MAX_TOTAL_CONTEXT_CHARS", "DEFAULT_MAX_USAGE_COUNT", "bounded_context"]
