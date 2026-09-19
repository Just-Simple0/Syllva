"""C2 regression: direct coverage for ensure_marked_folder()'s recovery branches.

rev10 (docs/ux/intake-execution-contract.md section 3.4/section 9 C2) requires the DriveAdapter
marker-recovery port to distinguish complete-zero / exactly-one / multiple / lookup-indeterminate
search outcomes and never silently re-create, auto-pick among duplicates, or trust a partial/failed
search as an empty result.

A code audit (.review/c2-audit-findings.md) found ensure_marked_folder()
(src/uls/adapters/drive/worker.py) already implemented the complete-zero/exactly-one/multiple/
lookup-indeterminate branching correctly but had zero direct unit test coverage. An independent
insane-review of the first draft of this file found a genuine production gap while reviewing that
coverage: the exactly-one reuse branch only checked owned_by_me, skipping the same
require_private_ownership() boundary (shared-drive, owner-only permission readback, public-sharing,
edit/move capability) that the create path enforces via GoogleDriveWorkerAdapter._validate_private_metadata
and that src/uls/worker.py and src/uls/intake/registry.py already apply for every other Drive
write-destination folder in this codebase. A marker folder that is technically owned_by_me=True but has
since been shared to another user, moved to a shared drive, or made public could otherwise be silently
reused without those checks. Fixed by calling require_private_ownership(item, ...) on the reused match
instead of the narrower owned_by_me-only check.
"""

from __future__ import annotations

import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import pytest

from uls.adapters.drive.worker import (
    DRIVE_FOLDER_MIME,
    DriveMetadata,
    DriveWorkerCapabilities,
    InMemoryDriveWorker,
    ensure_marked_folder,
)
from uls.domain.errors import (
    PolicyDeniedError,
    ProviderUnavailableError,
    SourcePartialError,
    SourceUnavailableError,
)

pytestmark = pytest.mark.unit

_MARKER = {"uls_v": "1", "uls_t": "marker-hash-1", "uls_r": "entity"}


def _owned_folder(
    file_id: str,
    parent_id: str,
    marker: dict[str, str] | None = None,
    *,
    owned_by_me: bool | None = True,
    drive_id: str | None = None,
    owner_only: bool | None = True,
    is_publicly_shared: bool | None = False,
    can_edit: bool | None = True,
    can_move: bool | None = True,
    permission_count: int | None = 1,
    mime_type: str = DRIVE_FOLDER_MIME,
) -> DriveMetadata:
    return DriveMetadata(
        file_id=file_id,
        name=file_id,
        mime_type=mime_type,
        parents=(parent_id,),
        trashed=False,
        owned_by_me=owned_by_me,
        drive_id=drive_id,
        permission_roles=(("user", "owner"),),
        permission_count=permission_count,
        owner_only=owner_only,
        is_publicly_shared=is_publicly_shared,
        can_edit=can_edit,
        can_move=can_move,
        app_properties=dict(marker) if marker is not None else {},
    )


def test_zero_matches_no_prior_attempt_creates_a_new_marked_folder() -> None:
    port = InMemoryDriveWorker()
    result = ensure_marked_folder(
        port, parent_id="parent-1", name="entity", marker=_MARKER, create_attempted=False
    )
    assert result.app_properties == _MARKER
    assert result.parent_id == "parent-1"
    create_events = [event for event in port.events if event[0] == "create"]
    assert len(create_events) == 1


def test_exactly_one_exact_match_is_reused_without_creating() -> None:
    existing = _owned_folder("folder-1", "parent-1", _MARKER)
    port = InMemoryDriveWorker(files=[existing])
    result = ensure_marked_folder(
        port, parent_id="parent-1", name="entity", marker=_MARKER, create_attempted=False
    )
    assert result.file_id == "folder-1"
    assert [event for event in port.events if event[0] == "create"] == []


def test_one_match_with_mismatched_parent_is_rejected_without_creating() -> None:
    existing = _owned_folder("folder-1", "wrong-parent", _MARKER)
    port = InMemoryDriveWorker(files=[existing])
    with pytest.raises(SourceUnavailableError, match="exact parent/MIME readback"):
        ensure_marked_folder(
            port, parent_id="parent-1", name="entity", marker=_MARKER, create_attempted=False
        )
    assert [event for event in port.events if event[0] == "create"] == []


def test_one_match_with_wrong_mime_is_rejected_without_creating() -> None:
    """A non-folder MIME type must be rejected on its own, independent of the
    parent check -- both conditions are checked with 'or', but only testing
    wrong-parent could hide a regression that drops the MIME comparison."""

    existing = _owned_folder("file-1", "parent-1", _MARKER, mime_type="application/pdf")
    port = InMemoryDriveWorker(files=[existing])
    with pytest.raises(SourceUnavailableError, match="exact parent/MIME readback"):
        ensure_marked_folder(
            port, parent_id="parent-1", name="entity", marker=_MARKER, create_attempted=False
        )
    assert [event for event in port.events if event[0] == "create"] == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"owned_by_me": False},
        {"drive_id": "shared-drive-1"},
        {"owner_only": False},
        {"is_publicly_shared": True},
        {"can_edit": False},
        {"can_move": False},
        {"permission_count": None},
        {"is_publicly_shared": None},
    ],
)
def test_one_match_failing_any_private_ownership_check_is_rejected(overrides: dict[str, Any]) -> None:
    """The reuse path must apply the exact same require_private_ownership()
    boundary as every other Drive write-destination folder in this codebase
    (src/uls/worker.py, src/uls/intake/registry.py) -- not just owned_by_me.
    A marker folder that is technically owned_by_me=True but has since been
    shared to a shared drive, lost owner-only status, been made public, or
    lost edit/move capability must never be silently reused."""

    existing = _owned_folder("folder-1", "parent-1", _MARKER, **overrides)
    port = InMemoryDriveWorker(files=[existing])
    with pytest.raises((PolicyDeniedError, SourceUnavailableError)):
        ensure_marked_folder(
            port, parent_id="parent-1", name="entity", marker=_MARKER, create_attempted=False
        )
    assert [event for event in port.events if event[0] == "create"] == []


def test_two_or_more_matches_are_never_auto_selected() -> None:
    first = _owned_folder("folder-1", "parent-1", _MARKER)
    second = _owned_folder("folder-2", "parent-1", _MARKER)
    port = InMemoryDriveWorker(files=[first, second])
    with pytest.raises(SourceUnavailableError, match="reconciliation"):
        ensure_marked_folder(
            port, parent_id="parent-1", name="entity", marker=_MARKER, create_attempted=False
        )
    assert [event for event in port.events if event[0] == "create"] == []


def test_zero_matches_after_a_prior_create_attempt_is_indeterminate_not_a_blind_recreate() -> None:
    """No folder exists in the backing store at all -- as if the create call was
    never actually dispatched, or a since-changed marker means the earlier
    folder can no longer be found. A recovery pass must not create a second
    folder; it must fail closed for a human/reconcile path."""

    port = InMemoryDriveWorker()
    with pytest.raises(SourceUnavailableError, match="indeterminate"):
        ensure_marked_folder(
            port, parent_id="parent-1", name="entity", marker=_MARKER, create_attempted=True
        )
    assert [event for event in port.events if event[0] == "create"] == []


def test_response_loss_then_recovery_finds_and_reuses_the_durably_created_folder() -> None:
    """The realistic case: the first ensure_marked_folder() call's underlying
    create durably persists in the provider before the ack is lost. A second
    recovery pass with create_attempted=True must find that same folder via
    search_marker() and reuse it, never creating a duplicate."""

    port = InMemoryDriveWorker()
    port.drop_next_create_response = True
    with pytest.raises(ProviderUnavailableError):
        ensure_marked_folder(
            port, parent_id="parent-1", name="entity", marker=_MARKER, create_attempted=False
        )
    create_events_before = [event for event in port.events if event[0] == "create"]
    assert len(create_events_before) == 1

    recovered = ensure_marked_folder(
        port, parent_id="parent-1", name="entity", marker=_MARKER, create_attempted=True
    )
    assert recovered.file_id == create_events_before[0][1]
    assert len([event for event in port.events if event[0] == "create"]) == 1


class _FailingSearchPort:
    """A minimal DriveWorkerPort whose search_marker() raises, modeling a
    partial/failed provider listing. ensure_marked_folder() must propagate
    this, never treating a failed search as a trustworthy empty result. The
    other Protocol methods are unused by ensure_marked_folder() and only
    exist to satisfy the DriveWorkerPort structural type."""

    capabilities = DriveWorkerCapabilities(
        marker_create=True,
        marker_search=True,
        metadata_readback=True,
        file_id_preserving_move=True,
        private_owner_readback=True,
    )

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.create_calls = 0

    def list_folder(self, folder_id: str) -> list[DriveMetadata]:
        raise NotImplementedError

    def read_metadata(self, file_id: str) -> DriveMetadata:
        raise NotImplementedError

    def download(self, file_id: str) -> bytes:
        raise NotImplementedError

    def search_marker(self, marker: dict[str, str]) -> list[DriveMetadata]:
        raise self._error

    def create_folder_with_marker(self, parent_id: str, name: str, marker: dict[str, str]) -> DriveMetadata:
        self.create_calls += 1
        raise AssertionError("must not create after a failed/partial search")

    def create_file_with_marker(
        self, parent_id: str, name: str, mime_type: str, content: bytes, marker: dict[str, str]
    ) -> DriveMetadata:
        raise NotImplementedError

    def move_file(self, file_id: str, original_parent_id: str, target_parent_id: str) -> DriveMetadata:
        raise NotImplementedError


@pytest.mark.parametrize(
    "error",
    [
        SourceUnavailableError("Drive marker search is malformed"),
        SourcePartialError("Drive marker search exceeds bounded limit"),
        ProviderUnavailableError("Drive marker search failed"),
    ],
)
def test_failed_or_partial_search_propagates_and_never_triggers_a_create(error: Exception) -> None:
    port = _FailingSearchPort(error)
    with pytest.raises(type(error)):
        ensure_marked_folder(
            port, parent_id="parent-1", name="entity", marker=_MARKER, create_attempted=False
        )
    assert port.create_calls == 0



class _PermissiveCreatePort:
    """A minimal DriveWorkerPort whose create_folder_with_marker() returns a
    folder that fails require_private_ownership() (can_move=False), modeling
    a provider or adapter whose create-side readback validation is weaker
    than the reuse-side boundary. ensure_marked_folder() must apply the same
    postcondition to its own create result and reject this, never returning
    a freshly created folder that would fail its own reuse check on the very
    next recovery pass."""

    capabilities = DriveWorkerCapabilities(
        marker_create=True,
        marker_search=True,
        metadata_readback=True,
        file_id_preserving_move=True,
        private_owner_readback=True,
    )

    def __init__(self) -> None:
        self.create_calls = 0

    def list_folder(self, folder_id: str) -> list[DriveMetadata]:
        raise NotImplementedError

    def read_metadata(self, file_id: str) -> DriveMetadata:
        raise NotImplementedError

    def download(self, file_id: str) -> bytes:
        raise NotImplementedError

    def search_marker(self, marker: dict[str, str]) -> list[DriveMetadata]:
        return []

    def create_folder_with_marker(self, parent_id: str, name: str, marker: dict[str, str]) -> DriveMetadata:
        self.create_calls += 1
        return _owned_folder("weak-folder-1", parent_id, marker, can_move=False)

    def create_file_with_marker(
        self, parent_id: str, name: str, mime_type: str, content: bytes, marker: dict[str, str]
    ) -> DriveMetadata:
        raise NotImplementedError

    def move_file(self, file_id: str, original_parent_id: str, target_parent_id: str) -> DriveMetadata:
        raise NotImplementedError


def test_create_result_failing_private_ownership_is_rejected_not_returned() -> None:
    """The create path must be held to the exact same postcondition as the
    reuse path -- a newly created folder that cannot be moved (or otherwise
    fails require_private_ownership()) must never be silently returned as
    success, even though the port's own create call succeeded."""

    port = _PermissiveCreatePort()
    with pytest.raises(SourceUnavailableError, match="lacks worker capabilities"):
        ensure_marked_folder(
            port, parent_id="parent-1", name="entity", marker=_MARKER, create_attempted=False
        )
    assert port.create_calls == 1
