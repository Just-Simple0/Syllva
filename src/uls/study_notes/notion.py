"""Dedicated C6 AI block bridge (plan section 10).

Real Notion SDK block operations, never a property-based shortcut, never
SESSION_ENRICHMENT_KINDS/enrichment schema. Verified against the official
Notion API docs (append block children / get block children / update
block): append returns a results array of newly-created first-level blocks
(never a single direct block object); GET children is paginated and a
short page is NOT completion -- has_more/next_cursor must be followed to
exhaustion; update/retrieve use the exact block_id. The repo pins
Notion-Version 2025-09-03 (matches adapters/notion/api.py, intake.py) and
does not rely on any newer position parameter; default append-to-end is
the stable behavior used here.

First-create response loss: an append can succeed remotely while its
response (carrying the new block id) is lost. This module never issues a
second append while that is unresolved -- it persists UNKNOWN via the
store's session-level note_ai_block guard and requires an explicit
reconcile pass (exact full-child-listing content match, not shape/order
guessing) before any further create is permitted for that Session.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from uls.domain.errors import ProviderWriteNotAppliedError

from .store import StudyNoteStore

_CODE_LANGUAGE = "json"


@runtime_checkable
class NotionBlockPort(Protocol):
    """SDK-shaped port -- one method per real Notion API call used here."""

    def append_block_children(
        self, parent_block_id: str, children: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]: ...

    def list_block_children(
        self, block_id: str, *, start_cursor: str | None = None,
    ) -> Mapping[str, Any]: ...

    def retrieve_block(self, block_id: str) -> Mapping[str, Any]: ...

    def update_block(self, block_id: str, **fields: Any) -> Mapping[str, Any]: ...


class UserEditConflictError(RuntimeError):
    """The current block content does not match the last worker-authored
    hash -- a human edited it. The caller must preserve the edit and persist
    the new artifact separately, never overwrite."""


class BlockCreateAmbiguousError(RuntimeError):
    """Reconciliation after a lost append response found more than one
    exact content match, or the create is still genuinely unresolved."""


def _pointer_block(pointer: Mapping[str, Any]) -> dict[str, Any]:
    text = json.dumps(dict(pointer), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "object": "block",
        "type": "code",
        "code": {
            "language": _CODE_LANGUAGE,
            "rich_text": [{"type": "text", "text": {"content": text}}],
        },
    }


def _block_text(block: Mapping[str, Any]) -> str | None:
    code = block.get("code")
    if not isinstance(code, Mapping):
        return None
    rich_text = code.get("rich_text")
    if not isinstance(rich_text, list):
        return None
    parts = [item.get("plain_text") or item.get("text", {}).get("content", "")
             for item in rich_text if isinstance(item, Mapping)]
    return "".join(parts)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class StudyNoteAIBlockBridge:
    def __init__(self, port: NotionBlockPort, store: StudyNoteStore) -> None:
        self._port = port
        self._store = store
        # Public, settable per-dispatch: called immediately before every
        # block create/update mutation (never before a read). Default no-op
        # for component tests. Not used in the submission process.
        self.before_write: Callable[[], None] = lambda: None

    def get_study_note_ai_block(self, session_id: str) -> dict[str, Any] | None:
        return self._store.get_ai_block(session_id)

    def write_study_note_ai_block(
        self, *, session_id: str, note_key: str, attempt_no: int, pointer: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Create-or-update the one AI block for this Session.

        Raises UserEditConflictError on a detected human edit (never
        overwritten) and BlockCreateAmbiguousError when a prior create is
        still UNKNOWN and has not been reconciled."""
        current = self._store.get_ai_block(session_id)
        if current is not None and current["state"] in {"INTENT", "UNKNOWN"}:
            raise BlockCreateAmbiguousError(
                f"session {session_id} has an unresolved prior block-create response loss; "
                "reconcile before writing again"
            )
        if current is not None and current["state"] == "KNOWN" and current["block_id"]:
            return self._update_known(session_id, current, pointer)
        return self._create_first(session_id, note_key, attempt_no, pointer)

    def _update_known(
        self, session_id: str, current: Mapping[str, Any], pointer: Mapping[str, Any],
    ) -> dict[str, Any]:
        block_id = current["block_id"]
        live = self._port.retrieve_block(block_id)
        live_text = _block_text(live) or ""
        live_hash = _content_hash(live_text)
        if live_hash != current["last_worker_hash"]:
            raise UserEditConflictError(
                f"block {block_id} was edited outside the worker; preserving it unchanged"
            )
        new_block = _pointer_block(pointer)
        intended_text = _block_text(new_block) or ""
        intended_hash = _content_hash(intended_text)
        # Real Notion block update accepts only the type-specific field
        # (here "code"), never "object"/"type" -- those are create-only.
        self.before_write()
        self._port.update_block(block_id, code=new_block["code"])
        readback = self._port.retrieve_block(block_id)
        new_text = _block_text(readback) or ""
        new_hash = _content_hash(new_text)
        if new_hash != intended_hash:
            # The readback does not match what we intended to write -- either
            # the update did not apply as expected, or another writer (a
            # human edit) landed between our write and this readback.  Never
            # adopt unknown content as if it were our own; treat this as a
            # conflict rather than silently recording someone else's hash as
            # "worker-authored".
            raise UserEditConflictError(
                f"block {block_id} readback does not match the intended write; "
                "not adopting unverified content as worker-authored"
            )
        return self._store.set_block_state(
            session_id=session_id, state="KNOWN", block_id=block_id, content_hash=new_hash,
        )

    def _create_first(
        self, session_id: str, note_key: str, attempt_no: int, pointer: Mapping[str, Any],
    ) -> dict[str, Any]:
        block = _pointer_block(pointer)
        intended_text = _block_text(block) or ""
        intended_hash = _content_hash(intended_text)
        self._store.begin_block_create_intent_with_payload(
            session_id=session_id, note_key=note_key, attempt_no=attempt_no,
            payload_hash=intended_hash,
        )
        # A rejection from before_write is attributable pre-dispatch
        # no-effect.  Preserve the immutable intent but make that same intent
        # retryable.  A BaseException/process death still leaves INTENT and
        # therefore fails closed as ambiguous.
        try:
            self.before_write()
        except Exception:
            self._store.set_block_state(
                session_id=session_id, state="NONE",
                reason="pre-dispatch validation rejected before provider mutation",
            )
            raise
        try:
            response = self._port.append_block_children(session_id, [block])
        except ProviderWriteNotAppliedError:
            # A trusted, proven-before-dispatch guarantee that no mutation
            # applied -- this is the only typed no-effect evidence that
            # permits leaving the create attempt itself as safely retryable
            # rather than UNKNOWN.
            return self._store.set_block_state(
                session_id=session_id, state="NONE",
                reason="provider confirmed the create did not apply; safe to retry",
            )
        except Exception:  # noqa: BLE001 - post-dispatch provider failure is ambiguous
            # The append call itself may have succeeded remotely even though
            # this exception fired locally (timeout, connection drop after
            # the server committed).  Treat as UNKNOWN, never as "no effect".
            return self._store.set_block_state(
                session_id=session_id, state="UNKNOWN",
                reason="append response lost or errored before an id was read",
            )
        results = response.get("results") if isinstance(response, Mapping) else None
        if not isinstance(results, list) or len(results) != 1:
            return self._store.set_block_state(
                session_id=session_id, state="UNKNOWN",
                reason="append response did not contain exactly one created block",
            )
        new_id = results[0].get("id") if isinstance(results[0], Mapping) else None
        if not isinstance(new_id, str) or not new_id:
            return self._store.set_block_state(
                session_id=session_id, state="UNKNOWN",
                reason="append response result had no block id",
            )
        # Exact readback verification: the append response claims a new id,
        # but only an independent retrieve confirms the block actually holds
        # the intended content -- a response can itself be malformed/stale.
        try:
            readback = self._port.retrieve_block(new_id)
        except Exception:  # noqa: BLE001 - failed exact-ID readback leaves effect unknown
            return self._store.set_block_state(
                session_id=session_id, state="UNKNOWN",
                reason="post-create readback failed; new block id is unverified",
            )
        readback_text = _block_text(readback) or ""
        readback_hash = _content_hash(readback_text)
        if readback_hash != intended_hash:
            return self._store.set_block_state(
                session_id=session_id, state="UNKNOWN",
                reason="post-create readback did not match the intended content",
            )
        return self._store.set_block_state(
            session_id=session_id, state="KNOWN", block_id=new_id, content_hash=intended_hash,
        )

    def reconcile_unknown_block(self, session_id: str) -> dict[str, Any]:
        """Resolve a session stuck in UNKNOWN after a lost append response.

        Lists the Session page's children to full pagination exhaustion and
        looks for a block whose exact content matches the recorded intent
        payload hash.  Exactly one match adopts that block (never chosen by
        shape/order alone). Zero matches after a complete listing remains
        UNKNOWN because it is not attributable proof that the append had no
        effect. More than one match also requires reconciliation."""
        current = self._store.get_ai_block(session_id)
        if current is None or current["state"] not in {"INTENT", "UNKNOWN"}:
            raise ValueError(
                f"session {session_id} is not in an ambiguous create state; nothing to reconcile"
            )
        target_hash = current["intent_payload_hash"]
        matches: list[str] = []
        cursor: str | None = None
        while True:
            page = self._port.list_block_children(session_id, start_cursor=cursor)
            for child in page.get("results", []) if isinstance(page, Mapping) else []:
                if not isinstance(child, Mapping):
                    continue
                text = _block_text(child)
                if text is None:
                    continue
                if _content_hash(text) == target_hash:
                    block_id = child.get("id")
                    if isinstance(block_id, str) and block_id:
                        matches.append(block_id)
            if not isinstance(page, Mapping) or not page.get("has_more"):
                break
            cursor = page.get("next_cursor")
            if not isinstance(cursor, str) or not cursor:
                # has_more=True but no usable cursor is itself ambiguous --
                # do not conclude completeness on a broken pagination signal.
                raise BlockCreateAmbiguousError(
                    f"session {session_id} child listing pagination is inconsistent"
                )
        if len(matches) == 1:
            return self._store.set_block_state(
                session_id=session_id, state="KNOWN", block_id=matches[0], content_hash=target_hash,
            )
        if len(matches) == 0:
            # A complete listing finding no match is still NOT attributable
            # proof of no-effect (Notion documents no strong read-after-write
            # guarantee for a just-created child across a subsequent list
            # call). Stay UNKNOWN; only a typed no-effect signal from the
            # original create call itself (ProviderWriteNotAppliedError,
            # handled in _create_first) may clear this state.
            raise BlockCreateAmbiguousError(
                f"session {session_id} child listing found no exact match; "
                "this is not proof of no-effect and remains UNKNOWN"
            )
        raise BlockCreateAmbiguousError(
            f"session {session_id} child listing has {len(matches)} exact content matches"
        )


__all__ = [
    "BlockCreateAmbiguousError",
    "NotionBlockPort",
    "StudyNoteAIBlockBridge",
    "UserEditConflictError",
]
