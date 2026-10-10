"""Drive staging for AI_STUDY_NOTE artifacts (plan section 7).

Reuses the existing DriveWorkerPort marker primitives
(search_marker/create_file_with_marker/read_metadata/download) exactly as
intake._stage_derivative does, with its own idempotency bookkeeping in this
package's own store rather than the main state store. Every file this
module creates uses the literal artifact role string AI_STUDY_NOTE so the
SOURCE-discovery exclusion filtering on app_properties['uls_r'] matches
exactly.

First-create response loss: persist the deterministic marker BEFORE the
create call. On any exception from create_file_with_marker other than a
trusted ProviderWriteNotAppliedError (proven non-application), mark the
receipt UNKNOWN and stop -- never assume no-effect and never immediately
retry. Drive marker search is an eventually-consistent search index, not
an authoritative listing: a search returning zero matches during
reconciliation is NOT attributable no-effect evidence either, and does not
clear an UNKNOWN receipt -- only a ProviderWriteNotAppliedError from the
original create call itself may do that.

Once a receipt exists, every later search/adopt/reconcile call uses the
PERSISTED marker (recorded at first-intent time), never a freshly
recomputed one, so no later drift in how a marker is built can desync a
receipt from what was actually searched for on Drive.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from uls.adapters.drive.worker import (
    DRIVE_FOLDER_MIME,
    DriveMetadata,
    DriveWorkerPort,
    require_private_ownership,
)
from uls.domain.errors import ProviderWriteNotAppliedError
from uls.intake.identity import sha256_hex

from .store import StudyNoteStore

ARTIFACT_ROLE = "AI_STUDY_NOTE"


class StageAmbiguousError(RuntimeError):
    """
    Staging is UNKNOWN/BLOCKED and requires reconciliation, or a stored
    VERIFIED receipt no longer matches live Drive state. Never resolved by
    guessing.
    """
class FolderCreateNotAppliedError(RuntimeError):
    """Trusted, proven-before-dispatch guarantee the folder create did NOT
    apply. The receipt stays MARKER_PERSISTED; this is a clean,
    immediately-retryable failure via the caller's OWN pipeline retry
    schedule -- this method never retries itself (no recursion/loop)."""


def _verify_folder_metadata(
    found: DriveMetadata, *, parent_id: str, marker: dict[str, str],
) -> bool:
    """True only if found is an exact, live, non-trashed, private,
    correctly-typed, correctly-parented, correctly-marked folder -- never
    adopted on parent match alone."""
    if found.trashed:
        return False
    if found.mime_type != DRIVE_FOLDER_MIME:
        return False
    if found.parents != (parent_id,):
        return False
    if found.app_properties != marker:
        return False
    try:
        require_private_ownership(found, context="AI_STUDY_NOTE folder")
    except Exception:  # noqa: BLE001 - any provider/privacy read failure rejects adoption
        return False
    return True


def _content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _marker(
    *, note_key: str, attempt_no: int, manifest_hash: str,
    content_hash: str, parent_folder_id: str,
) -> dict[str, str]:
    tuple_hash = sha256_hex(
        [
            "study-note-drive.v1", note_key, attempt_no, parent_folder_id,
            manifest_hash, content_hash, ARTIFACT_ROLE,
        ]
    )
    return {
        "uls_v": "1",
        "uls_t": tuple_hash,
        "uls_r": ARTIFACT_ROLE,
        "uls_p": parent_folder_id,
    }


class StudyNoteDriveStaging:
    def __init__(self, drive: DriveWorkerPort, store: StudyNoteStore) -> None:
        self._drive = drive
        self._store = store
        # Public, settable per-dispatch: called immediately before every
        # folder/file create or update mutation, and again before any
        # subsequent mutation within the same call (e.g. two nested folder
        # creates) -- never before a read. Default no-op for component
        # tests; the worker-side dispatcher assigns a closure per authorized
        # dispatch. Not used anywhere in the submission process.
        self.before_write: Callable[[], None] = lambda: None

    def resolve_note_folder(self, *, session_derived_folder_id: str, note_key: str) -> str:
        """Idempotently resolve derived/ai-study-notes/<note_key>/ under the
        Session's existing derived folder.

        Callers supply session_derived_folder_id -- the Session's own
        `derived` folder, already created by the existing intake folder
        machinery. This method owns creating exactly two nested levels under
        it (a shared ai-study-notes folder, then one <note_key> subfolder),
        reusing the same marker create/search idempotency as file staging so
        a lost create response here is recoverable the same way. No other
        module needs to pre-create these folders.
        """
        shared = self._resolve_or_create_folder(
            parent_id=session_derived_folder_id, name="ai-study-notes",
            marker_scope="ai-study-notes-root",
        )
        return self._resolve_or_create_folder(
            parent_id=shared, name=note_key, marker_scope=f"ai-study-notes-note:{note_key}",
        )

    def _resolve_or_create_folder(self, *, parent_id: str, name: str, marker_scope: str) -> str:
        marker = {
            "uls_v": "1",
            "uls_r": ARTIFACT_ROLE,
            "uls_t": sha256_hex(["study-note-folder.v1", parent_id, name, marker_scope]),
        }
        folder_key = marker["uls_t"]
        receipt = self._store.get_folder_receipt(folder_key)
        if receipt is None:
            receipt = self._store.persist_folder_marker(folder_key=folder_key, marker=marker)
        if receipt["state"] == "VERIFIED":
            try:
                readback = self._drive.read_metadata(receipt["folder_id"])
            except Exception:  # noqa: BLE001 - provider read failure makes identity unverified
                raise StageAmbiguousError(
                    f"folder {receipt['folder_id']!r} for {name!r} could not be re-verified"
                ) from None
            if not _verify_folder_metadata(readback, parent_id=parent_id, marker=marker):
                self._store.mark_folder_state(folder_key=folder_key, state="BLOCKED")
                raise StageAmbiguousError(f"folder {name!r} no longer matches its recorded state")
            folder_id = receipt["folder_id"]
            if not isinstance(folder_id, str) or not folder_id:
                raise StageAmbiguousError(f"folder {name!r} verified receipt has no identity")
            return folder_id
        if receipt["state"] == "UNKNOWN":
            return self._reconcile_folder(folder_key, marker, parent_id, name)
        if receipt["state"] == "BLOCKED":
            raise StageAmbiguousError(f"folder {name!r} under {parent_id!r} is BLOCKED; needs manual reconciliation")
        matches = self._drive.search_marker(marker)
        exact = [m for m in matches if _verify_folder_metadata(m, parent_id=parent_id, marker=marker)]
        if len(exact) > 1:
            raise StageAmbiguousError(f"multiple folders match marker for {name!r} under {parent_id!r}")
        if len(exact) == 1:
            self._store.mark_folder_state(folder_key=folder_key, state="VERIFIED", folder_id=exact[0].file_id)
            return exact[0].file_id
        # before_write is OUTSIDE the mutation try/except: a rejection from
        # it (e.g. the caller's own freshness/cancellation check) must
        # propagate as a clean rejection, never be caught and reclassified
        # as an ambiguous/UNKNOWN provider failure.
        self.before_write()
        # Crossing this durable boundary means a crash can no longer prove
        # whether the provider call happened.  Stay UNKNOWN unless the
        # provider itself later supplies attributable no-effect evidence.
        self._store.mark_folder_state(folder_key=folder_key, state="UNKNOWN")
        try:
            created = self._drive.create_folder_with_marker(parent_id, name, marker)
        except ProviderWriteNotAppliedError:
            # Trusted proof of non-application. The receipt stays
            # retryable only after restoring MARKER_PERSISTED; this is a
            # clean failure handed
            # to the CALLER's own pipeline retry schedule -- never retried
            # here (no recursion/loop).
            self._store.mark_folder_state(folder_key=folder_key, state="MARKER_PERSISTED")
            raise FolderCreateNotAppliedError(
                f"folder create for {name!r} under {parent_id!r} did not apply"
            ) from None
        except Exception:  # noqa: BLE001 - post-dispatch provider failure is ambiguous
            raise StageAmbiguousError(
                f"folder create for {name!r} under {parent_id!r} lost its response; now UNKNOWN"
            ) from None
        try:
            readback = self._drive.read_metadata(created.file_id)
        except Exception:  # noqa: BLE001 - post-create readback failure leaves UNKNOWN
            raise StageAmbiguousError(
                f"folder create readback for {name!r} failed; remains UNKNOWN"
            ) from None
        if not _verify_folder_metadata(readback, parent_id=parent_id, marker=marker):
            raise StageAmbiguousError(
                f"folder create readback for {name!r} did not match the intended state"
            )
        self._store.mark_folder_state(folder_key=folder_key, state="VERIFIED", folder_id=readback.file_id)
        return readback.file_id

    def _reconcile_folder(self, folder_key: str, marker: dict[str, str], parent_id: str, name: str) -> str:
        matches = self._drive.search_marker(marker)
        exact = [m for m in matches if _verify_folder_metadata(m, parent_id=parent_id, marker=marker)]
        if len(exact) > 1:
            self._store.mark_folder_state(folder_key=folder_key, state="BLOCKED")
            raise StageAmbiguousError(f"folder {name!r} reconciliation found multiple matches")
        if len(exact) == 1:
            self._store.mark_folder_state(folder_key=folder_key, state="VERIFIED", folder_id=exact[0].file_id)
            return exact[0].file_id
        # Zero matches on Drive's eventually-consistent search index is not
        # attributable no-effect evidence, matching file staging exactly.
        raise StageAmbiguousError(
            f"folder {name!r} under {parent_id!r} marker search found no match; remains UNKNOWN"
        )

    def stage(
        self, *, note_key: str, attempt_no: int, parent_folder_id: str, filename: str,
        content: bytes, manifest_hash: str,
    ) -> dict[str, Any]:
        return self._stage(
            note_key=note_key, attempt_no=attempt_no, parent_folder_id=parent_folder_id,
            filename=filename, content=content, manifest_hash=manifest_hash,
        )

    def reuse_verified_artifact(
        self, *, note_key: str, output_identity: str, output_hash: str, manifest_hash: str,
    ) -> dict[str, Any]:
        """Locate and re-verify an existing VERIFIED artifact for reuse by a
        seeded (generator-free) attempt -- never creates a new file.

        Re-checks the immutable marker plus parent/privacy/content against
        the SAVED hash by exact readback; never trusts the durable row alone.
        """
        receipt = self._store.get_verified_stage_receipt_by_output(note_key, output_identity)
        if receipt is None:
            raise StageAmbiguousError(
                f"no VERIFIED stage receipt for note {note_key} output {output_identity!r}"
            )
        marker = json.loads(receipt["marker_json"])
        if receipt["content_hash"] != output_hash or receipt["manifest_hash"] != manifest_hash:
            raise StageAmbiguousError(
                f"stored receipt for note {note_key} does not match the requested "
                "output_hash/manifest_hash exactly"
            )
        try:
            metadata = self._drive.read_metadata(output_identity)
            require_private_ownership(metadata, context="AI_STUDY_NOTE staged file")
            live_content = self._drive.download(output_identity)
        except Exception:  # noqa: BLE001 - any provider/privacy read failure blocks reuse
            raise StageAmbiguousError(
                f"artifact {output_identity!r} for note {note_key} could not be re-verified"
            ) from None
        parent_id = marker.get("uls_p")
        if (
            not isinstance(parent_id, str)
            or not parent_id
            or metadata.app_properties != marker
            or metadata.parents != (parent_id,)
            or metadata.trashed
            or _content_hash(live_content) != output_hash
        ):
            raise StageAmbiguousError(
                f"artifact {output_identity!r} for note {note_key} no longer matches "
                "its persisted parent/marker/content"
            )
        return receipt

    def _stage(
        self, *, note_key: str, attempt_no: int, parent_folder_id: str, filename: str,
        content: bytes, manifest_hash: str,
    ) -> dict[str, Any]:
        content_hash = _content_hash(content)
        receipt = self._store.get_stage_receipt(note_key, attempt_no)
        if receipt is None:
            marker = _marker(
                note_key=note_key, attempt_no=attempt_no, manifest_hash=manifest_hash,
                content_hash=content_hash, parent_folder_id=parent_folder_id,
            )
            receipt = self._store.persist_stage_marker(
                note_key=note_key, attempt_no=attempt_no, marker=marker,
                manifest_hash=manifest_hash, content_hash=content_hash,
            )
        else:
            marker = json.loads(receipt["marker_json"])  # persisted intent, never recomputed
            if (
                receipt["manifest_hash"] != manifest_hash
                or receipt["content_hash"] != content_hash
                or marker.get("uls_p") != parent_folder_id
            ):
                self._store.mark_stage_state(
                    note_key=note_key, attempt_no=attempt_no, state="BLOCKED",
                )
                raise StageAmbiguousError(
                    f"note {note_key} attempt {attempt_no} stage intent changed"
                )
        state = receipt["state"]
        if state == "VERIFIED":
            return self._verify_existing(note_key, attempt_no, receipt, marker, content)
        if state == "UNKNOWN":
            raise StageAmbiguousError(
                f"note {note_key} attempt {attempt_no} staging is UNKNOWN; reconcile before staging again"
            )
        if state == "BLOCKED":
            raise StageAmbiguousError(
                f"note {note_key} attempt {attempt_no} staging is BLOCKED; needs manual reconciliation"
            )
        matches = self._drive.search_marker(marker)
        if len(matches) > 1:
            return self._store.mark_stage_state(note_key=note_key, attempt_no=attempt_no, state="BLOCKED")
        if len(matches) == 1:
            return self._adopt_exact(
                note_key, attempt_no, matches[0], marker, content_hash, parent_folder_id,
            )
        return self._create(note_key, attempt_no, parent_folder_id, filename, content, marker, content_hash)

    def _verify_existing(
        self, note_key: str, attempt_no: int, receipt: dict[str, Any],
        marker: dict[str, str], content: bytes,
    ) -> dict[str, Any]:
        """
        A previously VERIFIED receipt is re-confirmed by exact readback,
        never trusted blindly -- the referenced file could have been moved,
        edited, or deleted outside this pipeline since it was last verified.
        """
        file_id = receipt["file_id"]
        try:
            metadata = self._drive.read_metadata(file_id)
            live_content = self._drive.download(file_id)
            require_private_ownership(metadata, context="AI_STUDY_NOTE staged file")
        except Exception:  # noqa: BLE001 - any provider/privacy read failure blocks reuse
            return self._store.mark_stage_state(
                note_key=note_key, attempt_no=attempt_no, state="BLOCKED",
            )
        expected_parent = marker.get("uls_p")
        if (
            not isinstance(expected_parent, str)
            or metadata.parents != (expected_parent,)
            or metadata.app_properties != marker
            or live_content != content
            or _content_hash(live_content) != receipt["content_hash"]
        ):
            return self._store.mark_stage_state(
                note_key=note_key, attempt_no=attempt_no, state="BLOCKED",
            )
        return receipt

    def reconcile_unknown(self, *, note_key: str, attempt_no: int) -> dict[str, Any]:
        """
        Attempt to resolve an UNKNOWN receipt with a marker search.

        A single exact match adopts it (this can legitimately happen once
        Drive eventually-consistent search index catches up). Multiple
        matches block. Zero matches is explicitly NOT proof of no-effect for
        Drive search-index-backed marker lookup and leaves the receipt
        UNKNOWN -- callers must not treat this as license to retry create.
        """
        receipt = self._store.get_stage_receipt(note_key, attempt_no)
        if receipt is None or receipt["state"] != "UNKNOWN":
            raise ValueError(
                f"note {note_key} attempt {attempt_no} is not UNKNOWN; nothing to reconcile"
            )
        marker = json.loads(receipt["marker_json"])
        matches = self._drive.search_marker(marker)
        if len(matches) > 1:
            return self._store.mark_stage_state(note_key=note_key, attempt_no=attempt_no, state="BLOCKED")
        if len(matches) == 1:
            found = matches[0]
            parent_id = marker.get("uls_p")
            if not isinstance(parent_id, str) or not parent_id:
                return self._store.mark_stage_state(
                    note_key=note_key, attempt_no=attempt_no, state="BLOCKED",
                )
            return self._adopt_exact(
                note_key, attempt_no, found, marker, receipt["content_hash"], parent_id,
            )
        raise StageAmbiguousError(
            f"note {note_key} attempt {attempt_no} marker search found no match; "
            "this is not attributable no-effect evidence for Drive and remains UNKNOWN"
        )

    def _adopt_exact(
        self, note_key: str, attempt_no: int, found: DriveMetadata,
        marker: dict[str, str], content_hash: str, parent_folder_id: str,
    ) -> dict[str, Any]:
        if found.app_properties != marker:
            return self._store.mark_stage_state(note_key=note_key, attempt_no=attempt_no, state="BLOCKED")
        if marker.get("uls_p") != parent_folder_id or found.parents != (parent_folder_id,):
            return self._store.mark_stage_state(note_key=note_key, attempt_no=attempt_no, state="BLOCKED")
        try:
            require_private_ownership(found, context="AI_STUDY_NOTE staged file")
            live_content = self._drive.download(found.file_id)
        except Exception:  # noqa: BLE001 - any provider/privacy read failure blocks adoption
            return self._store.mark_stage_state(note_key=note_key, attempt_no=attempt_no, state="BLOCKED")
        if _content_hash(live_content) != content_hash:
            return self._store.mark_stage_state(note_key=note_key, attempt_no=attempt_no, state="BLOCKED")
        return self._store.mark_stage_state(
            note_key=note_key, attempt_no=attempt_no, state="VERIFIED",
            file_id=found.file_id, content_hash=content_hash,
        )

    def _create(
        self, note_key: str, attempt_no: int, parent_folder_id: str, filename: str,
        content: bytes, marker: dict[str, str], content_hash: str,
    ) -> dict[str, Any]:
        # before_write is OUTSIDE the mutation try/except -- a rejection
        # from it must propagate cleanly, never be caught as an ambiguous
        # provider failure.
        self.before_write()
        # Persist the ambiguous/in-flight boundary before dispatch.  A
        # process death after this point can never authorize another create
        # merely because marker search temporarily returns zero results.
        self._store.mark_stage_state(
            note_key=note_key, attempt_no=attempt_no, state="UNKNOWN",
        )
        try:
            result = self._drive.create_file_with_marker(
                parent_folder_id, filename, "text/markdown", content, marker,
            )
        except ProviderWriteNotAppliedError:
            # Trusted, proven-before-dispatch guarantee of non-application --
            # the only typed no-effect evidence for Drive. Restore the exact
            # immutable intent to its retryable state and let the caller's
            # pipeline retry schedule decide when to try again.
            self._store.mark_stage_state(
                note_key=note_key, attempt_no=attempt_no, state="MARKER_PERSISTED",
            )
            raise
        except Exception:  # noqa: BLE001 - post-dispatch provider failure is ambiguous
            receipt = self._store.get_stage_receipt(note_key, attempt_no)
            if receipt is None:
                raise StageAmbiguousError("persisted Drive stage intent disappeared")
            return receipt
        try:
            readback = self._drive.read_metadata(result.file_id)
            require_private_ownership(readback, context="AI_STUDY_NOTE staged file")
            live_content = self._drive.download(readback.file_id)
        except Exception:  # noqa: BLE001 - failed post-create readback/download leaves effect unknown
            receipt = self._store.get_stage_receipt(note_key, attempt_no)
            if receipt is None:
                raise StageAmbiguousError("persisted Drive stage intent disappeared")
            return receipt
        if (
            readback.file_id != result.file_id
            or readback.parents != (parent_folder_id,)
            or readback.app_properties != marker
            or live_content != content
        ):
            receipt = self._store.get_stage_receipt(note_key, attempt_no)
            if receipt is None:
                raise StageAmbiguousError("persisted Drive stage intent disappeared")
            return receipt
        return self._store.mark_stage_state(
            note_key=note_key, attempt_no=attempt_no, state="VERIFIED",
            file_id=readback.file_id, content_hash=content_hash,
        )


__all__ = ["ARTIFACT_ROLE", "StageAmbiguousError", "StudyNoteDriveStaging"]
