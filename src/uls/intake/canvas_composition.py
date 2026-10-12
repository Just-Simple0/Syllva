"""Compose the Canvas observer from the Settings Canvas connection (plan §6.3, P-B3 follow-up).

The observer needs a pinned reader, the active profile origin / principal and the course
map.  The profile comes from the loaded config (``canvas.profile``, written by Settings);
the personal access token is read from its credential store on every use and never kept.
Nothing here is installed unless observation is enabled and the Settings profile is exactly
the origin and principal the operator named in ``intake.classification``; a profile that
differs leaves the worker without an observer, so no calendar is used."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from uls.intake.canvas_observer import CanvasObserver, connection_reader_factory


def _settings_token_reader(profile_id: str) -> Callable[[], str | None]:
    def read() -> str | None:
        try:
            from uls.config._secure_file import secrets_directory
            from uls.settings.credential_roles import canvas_role
            from uls.settings.credential_stores import CredentialStores

            value = CredentialStores(secrets_directory()).read(canvas_role(profile_id))
            return value.decode("ascii") if value else None
        except Exception:  # noqa: BLE001 - an unreadable credential store is "no credential"
            return None

    return read


def install_canvas_observer(
    worker: Any, config: Any, state: Any, *, token_reader: Callable[[str], Callable[[], str | None]] | None = None,
) -> str:
    """Attach a CanvasObserver to the worker; returns a status word for diagnostics."""

    classification = getattr(config.intake, "classification", None)
    if classification is None or not classification.canvas_observation_enabled:
        return "DISABLED"
    try:
        from uls.settings.canvas_service import validated_canvas_profile

        profile = validated_canvas_profile({"canvas": config.canvas})
    except Exception:  # noqa: BLE001 - no valid Settings Canvas profile: no observer
        return "NO_PROFILE"
    origin, principal = profile["origin"], profile["user_id"]
    if origin != classification.canvas_origin or principal != classification.canvas_principal:
        return "PROFILE_NOT_VERIFIED"
    semesters = {course.course_key: course.semester for course in config.courses}

    def semester_for(course_key: str) -> Any:
        semester = semesters.get(course_key)
        return worker._semester_range(semester) if semester else None  # noqa: SLF001

    worker.canvas_observer = CanvasObserver(
        state,
        origin=origin,
        verified_origin=classification.canvas_origin,
        course_map=lambda: classification.canvas_course_map,
        reader_factory=connection_reader_factory(origin, (token_reader or _settings_token_reader)(profile["id"])),
        semester_for=semester_for,
        profile_provider=lambda: origin,
        principal_provider=lambda: principal,
        verified_principal=classification.canvas_principal,
    )
    return "INSTALLED"
