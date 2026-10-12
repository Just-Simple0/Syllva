"""Composition of the Canvas observer from the Settings Canvas connection."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from uls.intake.canvas_composition import install_canvas_observer
from uls.settings.canvas_service import canvas_profile_id

pytestmark = pytest.mark.unit

ORIGIN = "https://canvas.example.edu"
USER = "4242"


def _config(*, enabled=True, origin=ORIGIN, principal=USER, profile=True, user=USER):
    section = {}
    if profile:
        section = {"profile": {
            "id": canvas_profile_id(ORIGIN, user), "origin": ORIGIN, "user_id": user,
            "display_name": "Student", "credential": "keyring"}}
    classification = SimpleNamespace(
        canvas_observation_enabled=enabled, canvas_origin=origin, canvas_principal=principal,
        canvas_course_map={10: "C1"})
    return SimpleNamespace(
        intake=SimpleNamespace(classification=classification), canvas=section,
        courses=[SimpleNamespace(course_key="C1", semester="2026-2")])


class _Worker:
    canvas_observer = None

    def _semester_range(self, semester):
        return SimpleNamespace(semester=semester, start=date(2026, 9, 1))


def _install(config, token="secret-token"):
    worker = _Worker()
    status = install_canvas_observer(
        worker, config, object(), token_reader=lambda _profile_id: (lambda: token))
    return worker, status


def test_disabled_installs_nothing() -> None:
    worker, status = _install(_config(enabled=False))
    assert (status, worker.canvas_observer) == ("DISABLED", None)


def test_no_settings_profile_installs_nothing() -> None:
    worker, status = _install(_config(profile=False))
    assert (status, worker.canvas_observer) == ("NO_PROFILE", None)


@pytest.mark.parametrize("override", [
    {"origin": "https://other.example.edu"}, {"principal": "9999"}, {"origin": ""}, {"principal": ""}])
def test_profile_that_is_not_the_named_one_installs_nothing(override) -> None:
    worker, status = _install(_config(**override))
    assert (status, worker.canvas_observer) == ("PROFILE_NOT_VERIFIED", None)


def test_matching_profile_installs_an_observer_with_matching_evidence() -> None:
    worker, status = _install(_config())
    observer = worker.canvas_observer
    assert status == "INSTALLED"
    assert observer.profile_matches() and observer.principal_matches()
    assert observer._semester_for("C1").semester == "2026-2"
    assert observer._semester_for("UNKNOWN") is None
    assert observer._course_map == {10: "C1"}


def test_course_map_is_read_live_from_the_config() -> None:
    config = _config()
    worker, _ = _install(config)
    config.intake.classification.canvas_course_map = {11: "C1"}
    assert worker.canvas_observer._course_map == {11: "C1"}


def test_unreadable_credential_yields_no_reader(monkeypatch) -> None:
    import uls.intake.canvas_composition as composition

    class _Boom:
        def __init__(self, *_a, **_k):
            raise RuntimeError("no keychain")

    monkeypatch.setattr("uls.settings.credential_stores.CredentialStores", _Boom)
    assert composition._settings_token_reader("c" + "0" * 32)() is None
