"""Durable-state stand-in for legacy retrieval tests: no Material is a v2 product."""
from __future__ import annotations


class NoV2State:
    def is_v2_material(self, material_id: str) -> bool:
        return False
