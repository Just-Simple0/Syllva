"""Plan §9 P-B: a Material created by an AUTO run stays unexposed while retrieval.v2_exposure_gate is off.

All Material reads of the retrieval engine pass one lookup, so the same hidden Material is absent from
Material and Session contexts, from newly issued capabilities and from the revalidation of capabilities
that were issued before the Material was hidden (plan: context, new capability, re-lookup of old ones).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from uls.config.schema import UlsConfig
from uls.domain.errors import EntityNotFoundError
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.retrieval.engine import RetrievalEngine

pytestmark = pytest.mark.contract


class _State:
    """The read-only state marker: which Materials are v2 products (AUTO run or HUMAN v2 Kind)."""

    def __init__(self, auto_ids: set[str]) -> None:
        self.auto_ids = auto_ids

    def is_v2_material(self, material_id: str) -> bool:
        return material_id in self.auto_ids


def _engine(auto_ids: set[str], *, gate: bool = False):
    fixtures = str(Path(__file__).resolve().parents[1] / "fixtures")
    if fixtures not in sys.path:
        sys.path.insert(0, fixtures)
    from phase4 import ready_phase4

    reader, _, drive, resolver = ready_phase4()
    reader.material_usage["COMP319-S05"][0]["Verified"] = True  # a verified Usage exposes the Material
    config = UlsConfig()
    config.retrieval.v2_exposure_gate = gate
    state = _State(auto_ids)
    engine = RetrievalEngine(reader, drive, state, MemoryEphemeralStore(), config, source_binding_resolver=resolver)
    return engine, state


def _material_locators(package) -> list[str]:
    return [str(item.locator) for item in package.sources if "COMP319-M03" in str(item.locator)]


def test_an_auto_material_is_unreachable_while_the_gate_is_off() -> None:
    engine, _ = _engine({"COMP319-M03"})
    with pytest.raises(EntityNotFoundError):
        engine.get_material_context("COMP319-M03")
    for include_provisional in (False, True):
        package = engine.get_session_context("COMP319-S05", include_provisional=include_provisional)
        assert _material_locators(package) == []  # neither verified nor provisional usage exposes it
        assert package.sources  # the Session's own transcript is unaffected


def test_a_gate_on_or_a_non_auto_material_is_exposed_as_before() -> None:
    for auto_ids, gate in ((set(), False), ({"COMP319-M03"}, True)):
        engine, _ = _engine(auto_ids, gate=gate)
        assert engine.get_material_context("COMP319-M03").sources
        assert _material_locators(engine.get_session_context("COMP319-S05"))


def test_a_capability_issued_before_the_material_was_hidden_is_revoked() -> None:
    engine, state = _engine(set())
    package = engine.get_session_context("COMP319-S05")
    assert _material_locators(package)
    state.auto_ids.add("COMP319-M03")  # the Material is (re)classified as an AUTO product afterwards
    with pytest.raises(Exception):  # noqa: B017 - any refusal: the capability no longer authorizes it
        engine.get_source_chunk(package.context_id, _material_locators(package)[0])


class _StateWithoutMarker:
    """A state object that cannot say whether a Material is a v2 product."""


def test_a_state_that_cannot_classify_materials_fails_closed_while_the_gate_is_off() -> None:
    # r2 H2: no marker interface -> refuse rather than expose; no state at all -> nothing can be v2.
    engine, _ = _engine(set())
    engine.state_store = _StateWithoutMarker()
    from uls.domain.errors import SourceUnavailableError

    with pytest.raises(SourceUnavailableError):
        engine.get_material_context("COMP319-M03")
    engine.state_store = None
    assert engine.get_material_context("COMP319-M03").sources
    gate_on, _ = _engine(set(), gate=True)
    gate_on.state_store = _StateWithoutMarker()
    assert gate_on.get_material_context("COMP319-M03").sources  # the gate on needs no marker


def test_a_human_v2_kind_material_is_hidden_like_an_auto_one() -> None:
    # r2 R3: the marker is not AUTO-specific — the state reports HUMAN v2 Materials as v2 products too.
    engine, state = _engine({"COMP319-M03"})  # any v2 product, whatever its authority
    with pytest.raises(EntityNotFoundError):
        engine.get_material_context("COMP319-M03")
    state.auto_ids.clear()
    assert engine.get_material_context("COMP319-M03").sources
