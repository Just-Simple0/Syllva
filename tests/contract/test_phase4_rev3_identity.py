"""Optional Queue mirrors and independently registered source identity."""

from __future__ import annotations

import pathlib
import sys
from copy import deepcopy

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from phase4 import phase4_proposal, ready_phase4

from uls.adapters.drive.binding import (
    InMemorySourceBindingBackend,
    SourceBindingRecord,
    ValidatedSourceBindingResolver,
)
from uls.domain.approval_identity import canonical_semantics_from_queue
from uls.domain.errors import ProviderUnavailableError, SourceUnavailableError
from uls.domain.source_ref import SourceRef


def test_optional_absent_mirrors_preserve_canonical_identity() -> None:
    reader, *_ = ready_phase4()
    proposal = phase4_proposal(reader)
    minimal = deepcopy(proposal)
    for key in ("Course", "Source Ref", "Source Hash", "Source Version"):
        minimal.pop(key)
    assert canonical_semantics_from_queue(minimal) == canonical_semantics_from_queue(proposal)
    assert minimal["Proposal ID"] == proposal["Proposal ID"]


@pytest.mark.parametrize("key", ["Course", "Source Ref", "Source Hash", "Source Version"])
@pytest.mark.parametrize("value", [None, "conflicting"])
def test_present_invalid_mirrors_remain_denied(key, value) -> None:
    reader, *_ = ready_phase4()
    proposal = phase4_proposal(reader)
    proposal[key] = value
    with pytest.raises((TypeError, ValueError)):
        canonical_semantics_from_queue(proposal)


def test_source_ref_navigation_is_not_proposal_identity() -> None:
    reader, *_ = ready_phase4()
    proposal = phase4_proposal(reader)
    expected = canonical_semantics_from_queue(proposal)
    proposal["Source Ref"] = {
        "provider": "google_drive", "file_id": "material-m03", "web_url": "https://example.com/navigation",
    }
    assert canonical_semantics_from_queue(proposal) == expected


def _resolver():
    return ValidatedSourceBindingResolver(InMemorySourceBindingBackend([
        SourceBindingRecord(
            "COMP319-M03", "https://drive.google.com/file/d/DERIV/view",
            SourceRef("google_drive", "DERIV", "https://drive.google.com/file/d/DERIV/view"),
            SourceRef("google_drive", "ORIGINAL"),
        ),
    ]))


@pytest.mark.parametrize("pointer", [
    "https://drive.google.com/file/d/DERIV/view?usp=sharing",
    "https://drive.google.com/file/d/DERIV/preview#page=2",
    "DERIV",
])
def test_registered_derivative_navigation_preserves_origin(pointer) -> None:
    assert _resolver().resolve_derivative_ref("COMP319-M03", pointer).identity == ("google_drive", "ORIGINAL")


@pytest.mark.parametrize("pointer", [
    "https://drive.google.com/file/d/OTHER/view?usp=sharing",
    "https://drive.google.com.evil.test/file/d/DERIV/view",
    "https://evil.test/file/d/DERIV/view",
    "https://user@drive.google.com/file/d/DERIV/view",
    "https://drive.google.com/file/d/DERIV/unsupported",
])
def test_unregistered_pointer_never_inherits_origin(pointer) -> None:
    with pytest.raises(SourceUnavailableError):
        _resolver().resolve_derivative_ref("COMP319-M03", pointer)


def test_binding_backend_outage_is_retryable() -> None:
    class Offline:
        def lookup_source_binding(self, *_):
            raise TimeoutError("temporary")

    with pytest.raises(ProviderUnavailableError):
        ValidatedSourceBindingResolver(Offline()).resolve_derivative_ref("COMP319-M03", "DERIV")


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("provider,file_id", [
    ("google_drive", " https://evil.example/source"),
    ("google_drive", "https://evil.example/source "),
    (" ", "valid"),
    ("google_drive", " "),
])
def test_typed_and_mapping_origins_share_canonical_validation(typed, provider, file_id) -> None:
    source = SourceRef(provider, file_id) if typed else {"provider": provider, "file_id": file_id}
    with pytest.raises(ValueError):
        InMemorySourceBindingBackend([{
            "entity_id": "COMP319-M03", "normalized_source_url": "DERIV",
            "derivative_ref": SourceRef("google_drive", "DERIV"), "source_ref": source,
        }])
