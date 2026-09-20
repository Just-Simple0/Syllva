"""Local conversational note lifecycle under the shared worker lock.

Provider callbacks recheck the request, physical head and exact evidence immediately
before each write. Cross-store actions have durable intents and recover by their
existing main-store identity. An uncertain effect is held for reconciliation.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from uls.intake.identity import derive_study_note_key

from .drive import StageAmbiguousError
from .evidence import EvidenceWaitingError, InvalidSelectionError, assemble_evidence
from .identity import operation_id, sha256_hex
from .notion import BlockCreateAmbiguousError, UserEditConflictError
from .packaging import bounded_context
from .retry import pipeline_retry_delay_seconds
from .validation import validate_draft_structure

_TERMINAL = frozenset({'READY', 'PARTIAL', 'FAILED', 'CANCELLED', 'STALE'})


class NoteAuthorityChanged(RuntimeError):
    """No further mutation is authorized by the prepared request."""


class NewIngressPending(RuntimeError):
    """A newer committed request must pass the shared barrier first."""


@dataclass(frozen=True)
class _Batch:
    epoch: str
    workspace: str
    blocked_slots: frozenset[str] = frozenset()
    workspace_blocked: bool = False
    claim_items: tuple[Mapping[str, Any], ...] = ()


class StudyNoteHandler:
    def __init__(self, *, state: Any, store: Any, engine: Any, drive_staging: Any,
                 notion_bridge: Any, workspace: str, clock: Callable[[], str], local_source_id: str,
                 session_course_key: Callable[[str], str], session_provider_page_id: Callable[[str], str],
                 session_derived_folder_id: Callable[[str], str], template_version: str,
                 generator_config_version: str, grant_ttl_seconds: int = 1800,
                 max_total_context_chars: int = 24_000, notion_source_id: str | None = None,
                 notion_form_schema_confirmed: bool = False) -> None:
        if notion_source_id is not None or notion_form_schema_confirmed:
            raise ValueError('Notion form submission requires a separately accepted exact schema')
        self.state, self.store, self.engine = state, store, engine
        self.drive_staging, self.notion_bridge = drive_staging, notion_bridge
        self.workspace, self.clock, self._local_source_id = workspace, clock, local_source_id
        self._course = session_course_key
        self._physical = session_provider_page_id
        self._folder = session_derived_folder_id
        self.template_version, self.generator_config_version = template_version, generator_config_version
        self.grant_ttl_seconds, self.max_total_context_chars = grant_ttl_seconds, max_total_context_chars

    def _rows(self) -> list[dict[str, Any]]:
        rows = self.store.list_client_requests(limit=10_001)
        if len(rows) > 10_000:
            raise NewIngressPending('request inventory exceeds the complete-snapshot bound')
        return list(rows)

    def validate_batch(self, snapshot: Any) -> _Batch:
        blocked = (snapshot.workspace != self.workspace or bool(snapshot.failed_sources)
                   or self._local_source_id not in snapshot.complete_sources)
        rows = tuple(row.values for row in snapshot.rows if row.source_id == self._local_source_id)
        return _Batch(snapshot.epoch, snapshot.workspace, workspace_blocked=blocked, claim_items=rows)

    def claim_ordered(self, batch: _Batch) -> _Batch:
        if batch.workspace_blocked:
            return batch
        self._apply_cancellations()
        for item in batch.claim_items:
            row = self.store.get_client_request(item['client_request_id'])
            if row is not None and row['state'] == 'PENDING' and not row['cancel_requested_at']:
                self._claim(row)
        return batch

    def _journal(self, row: Mapping[str, Any], boundary: str, payload: Any,
                 action: Callable[[], Any]) -> Any:
        digest = sha256_hex(payload)
        op = operation_id(client_request_id=row['client_request_id'], note_key=row.get('note_key'),
                          head_generation=row.get('head_generation'), attempt_no=row.get('attempt_no'),
                          boundary=boundary, payload_hash=digest)
        self.store.journal_intent(operation_id=op, client_request_id=row['client_request_id'],
                                  note_key=row.get('note_key'), head_generation=row.get('head_generation'),
                                  attempt_no=row.get('attempt_no'), boundary=boundary, payload_hash=digest)
        result = action()  # Every supplied action recovers the exact existing identity.
        self.store.journal_transition(op, 'DONE')
        return result

    def _claim(self, row: Mapping[str, Any]) -> None:
        physical = self._physical(row['session_id'])
        course = self._course(row['session_id'])
        # Preclaim identity excludes the not-yet-known generation/note key.
        op = operation_id(client_request_id=row['client_request_id'], note_key=None,
                          head_generation=None, attempt_no=None, boundary='claim_head',
                          payload_hash=row['payload_hash'])
        binding_hash = sha256_hex([row['payload_hash'], physical, course])
        prior = self.store.journal_get(op)
        if prior is not None and prior['payload_hash'] != binding_hash:
            self.store.set_client_request_status(row['client_request_id'], 'STALE', reason='Session identity changed')
            return
        self.store.journal_intent(operation_id=op, client_request_id=row['client_request_id'],
                                  note_key=None, head_generation=None, attempt_no=None,
                                  boundary='claim_head', payload_hash=binding_hash)
        head = self.state.get_study_note_head('notion', physical)
        if head is not None and head.current_receipt_id != row['client_request_id']:
            owner = self.store.get_client_request(head.current_receipt_id)
            if (prior is not None and owner is None) or (owner is not None and self._order(owner) > self._order(row)):
                self.store.set_client_request_status(row['client_request_id'], 'SUPERSEDED')
                return
        head = self.state.claim_study_note_head(
            provider='notion', session_provider_page_id=physical, course_key=course,
            session_id=row['session_id'], request_id=row['client_request_id'], receipt_id=row['client_request_id'],
            receipt_hash=row['payload_hash'], evidence_mode=row['evidence_mode'],
            selected_materials=json.loads(row['selected_materials_json'] or '[]'),
        )
        self.store.mark_client_request_head_claimed(row['client_request_id'], head_generation=head.generation,
                                                   session_provider_page_id=physical)
        self.store.journal_transition(op, 'DONE')

    @staticmethod
    def _order(row: Mapping[str, Any]) -> tuple[str, str]:
        return row['created_time'], row['client_request_id']

    def _current(self, row: Mapping[str, Any], *, manifest_hash: str | None = None) -> Any:
        current = self.store.get_client_request(row['client_request_id'])
        if (current is None or current['cancel_requested_at'] or current['payload_hash'] != row['payload_hash']
                or current['state'] in {'CANCELLED', 'SUPERSEDED', 'STALE', 'FAILED'}):
            raise NoteAuthorityChanged('request is no longer active')
        physical = row['session_provider_page_id']
        if self._physical(row['session_id']) != physical:
            raise NoteAuthorityChanged('physical Session identity changed')
        head = self.state.get_study_note_head('notion', physical)
        if (head is None or not head.active or head.current_receipt_id != row['client_request_id']
                or head.generation != row['head_generation'] or head.receipt_hash != row['payload_hash']
                or head.course_key != self._course(row['session_id'])):
            raise NoteAuthorityChanged('request is no longer the exact active head')
        pending_rows = self.store.list_pending_client_requests(limit=10_001)
        if len(pending_rows) > 10_000:
            raise NewIngressPending('pending request inventory is incomplete')
        for pending in pending_rows:
            if (pending['session_id'] == row['session_id'] and not pending['cancel_requested_at']
                    and self._order(pending) > self._order(row)):
                raise NewIngressPending('newer request awaits the shared barrier')
        if row.get('note_key'):
            if (head.current_note_key != row['note_key'] or head.current_attempt_no != row['attempt_no']):
                raise NoteAuthorityChanged('attempt binding changed')
            reference = self.state.connection.execute(
                'SELECT state FROM note_request_references WHERE receipt_id=?', (row['client_request_id'],),
            ).fetchone()
            if reference is None or reference['state'] != 'ACTIVE':
                raise NoteAuthorityChanged('request reference is inactive')
        if manifest_hash is not None:
            manifest = assemble_evidence(self.engine, session_id=row['session_id'],
                evidence_mode=row['evidence_mode'], selected_materials=json.loads(row['selected_materials_json'] or '[]'))
            if manifest.manifest_hash != manifest_hash:
                raise NoteAuthorityChanged('evidence manifest changed')
            return manifest
        return None

    def publish_pending(self, barrier: Any) -> dict[str, Any]:
        if barrier.workspace != self.workspace or barrier.workspace_blocked:
            return {'status': 'blocked'}
        self._apply_cancellations()
        for row in self._rows():
            if row['head_generation'] is None or row['state'] in _TERMINAL | {'SUPERSEDED'}:
                continue
            try:
                self._current(row)
                if not row['note_key']:
                    self._prepare(row)
            except NewIngressPending:
                return {'status': 'blocked', 'reason': 'new_ingress'}
            except NoteAuthorityChanged:
                self._retire_changed(row)
            except EvidenceWaitingError:
                self.store.set_client_request_status(row['client_request_id'], 'WAITING_CONTEXT', reason='evidence unavailable')
            except InvalidSelectionError:
                self._retire(row, 'FAILED')
        for draft in self.store.list_unvalidated_drafts(limit=10_001):
            self._validate(draft)
        for row in self._rows():
            if row.get('note_key') and row['state'] not in _TERMINAL | {'SUPERSEDED'}:
                self._ensure_grant(row)
        for draft in self.store.list_accepted_undispatched_drafts(limit=10_001):
            self._dispatch(draft)
        self._publish_seeded()
        return {'status': 'ok'}

    def _prepare(self, row: Mapping[str, Any]) -> None:
        manifest = assemble_evidence(self.engine, session_id=row['session_id'], evidence_mode=row['evidence_mode'],
                                    selected_materials=json.loads(row['selected_materials_json'] or '[]'))
        learner_hash = sha256_hex(row.get('learner_request') or '')
        key = derive_study_note_key(course_key=self._course(row['session_id']), session_id=row['session_id'],
            evidence_manifest_hash=manifest.manifest_hash, learner_request_hash=learner_hash,
            template_version=self.template_version, generator_config_version=self.generator_config_version)
        previous = self.state.connection.execute(
            'SELECT * FROM note_request_references WHERE receipt_id=?', (row['client_request_id'],),
        ).fetchone()
        if previous is not None and previous['note_key'] != key:
            recovered = dict(row, note_key=previous['note_key'], attempt_no=previous['attempt_no'],
                             request_reference_id=previous['reference_id'])
            self._retire(recovered, 'STALE')
            return
        self._current(row)
        def attach() -> Any:
            return self.state.attach_note_request(provider='notion', session_provider_page_id=row['session_provider_page_id'],
                head_generation=row['head_generation'], receipt_id=row['client_request_id'], receipt_hash=row['payload_hash'],
                provider_request_id=row['client_request_id'], note_key=key, course_key=self._course(row['session_id']),
                session_id=row['session_id'], evidence_manifest_hash=manifest.manifest_hash, learner_request_hash=learner_hash,
                template_version=self.template_version, generator_config_version=self.generator_config_version)
        attempt, reference, _ = self._journal(row, 'attach', [key, manifest.manifest_hash], attach)
        context = bounded_context(manifest, max_total_chars=self.max_total_context_chars)
        context['learner_request'] = row.get('learner_request')
        self.store.save_prepared_context(note_key=key, attempt_no=attempt.attempt_no,
                                        evidence_manifest_hash=manifest.manifest_hash, context=context)
        self.store.mark_client_request_claimed(row['client_request_id'], note_key=key,
            request_reference_id=reference.reference_id, head_generation=row['head_generation'], attempt_no=attempt.attempt_no)

    def _transition(self, row: Mapping[str, Any], target: str, **patch: Any) -> Any:
        def apply() -> Any:
            current = self.state.get_note_attempt(row['note_key'], row['attempt_no'])
            if current.state == target and all(getattr(current, name) == value for name, value in patch.items()
                                               if name != 'require_no_active_references'):
                return current
            return self.state.transition_note_attempt(row['note_key'], row['attempt_no'], target, **patch)
        return self._journal(row, 'attempt:' + target, patch, apply)

    def _ensure_grant(self, row: Mapping[str, Any]) -> None:
        attempt = self.state.get_note_attempt(row['note_key'], row['attempt_no'])
        if attempt is None or attempt.state not in {'REQUESTED', 'WAITING_CONTEXT', 'GENERATING'}:
            return
        try:
            prepared = self.store.get_prepared_context(row['note_key'], row['attempt_no'])
            self._current(row, manifest_hash=prepared['evidence_manifest_hash'])
        except (NoteAuthorityChanged, EvidenceWaitingError, InvalidSelectionError):
            self._retire_changed(row)
            return
        except NewIngressPending:
            return
        if any(draft['note_key'] == row['note_key'] and draft['attempt_no'] == row['attempt_no']
               for draft in self.store.list_accepted_undispatched_drafts(limit=10_001)):
            return
        grant = self.store.get_open_grant_for_attempt(row['note_key'], row['attempt_no'])
        if grant is not None:
            if (grant['client_request_id'] == row['client_request_id']
                    and datetime.fromisoformat(grant['expires_at']) > datetime.fromisoformat(self.clock())):
                return
            self.store.revoke_grant(grant['grant_id'], reason='expired or replaced request')
        self.store.issue_grant(client_request_id=row['client_request_id'],
            session_provider_page_id=row['session_provider_page_id'], note_key=row['note_key'], attempt_no=row['attempt_no'],
            evidence_manifest_hash=prepared['evidence_manifest_hash'], head_generation=row['head_generation'],
            caller_context=row['caller_context'], ttl_seconds=self.grant_ttl_seconds)

    def _validate(self, draft: Mapping[str, Any]) -> None:
        row = self.store.get_request_for_grant(draft['grant_id'])
        if row is None:
            raise NoteAuthorityChanged('draft request binding unavailable')
        prepared = self.store.get_prepared_context(row['note_key'], row['attempt_no'])
        try:
            self._current(row, manifest_hash=prepared['evidence_manifest_hash'])
        except NewIngressPending:
            return
        except (NoteAuthorityChanged, EvidenceWaitingError, InvalidSelectionError):
            self.store.mark_draft_validated(draft['draft_id'], outcome='STALE')
            self._retire_changed(row)
            return
        result = validate_draft_structure(draft['draft_text'], manifest_locators=prepared['context']['locators'])
        if result.accepted:
            self.store.mark_draft_validated(draft['draft_id'], outcome='ACCEPTED')
            return
        count = self.store.reject_draft_once(draft['draft_id'], result.reason or 'invalid draft structure')
        if count >= 3:
            self._transition(row, 'FAILED', error_class='POLICY_DENIED', error_code='draft_rejection_limit')
            self.store.revoke_attempt_grants(
                row['note_key'], row['attempt_no'], reason='draft_rejection_limit',
            )
            self.store.set_client_request_status(row['client_request_id'], 'FAILED', reason='draft_rejection_limit')

    def _apply_cancellations(self) -> None:
        for row in self._rows():
            if row['cancel_requested_at'] and row['state'] not in _TERMINAL | {'SUPERSEDED'}:
                self._retire(row, 'CANCELLED')

    def _retire_changed(self, row: Mapping[str, Any]) -> None:
        current = self.store.get_client_request(row['client_request_id'])
        if current and current['cancel_requested_at']:
            status = 'CANCELLED'
        else:
            head = self.state.get_study_note_head('notion', row['session_provider_page_id'])
            status = (
                'SUPERSEDED'
                if head is not None and head.active
                and head.current_receipt_id != row['client_request_id']
                else 'STALE'
            )
        self._retire(row, status)

    def _retire(self, row: Mapping[str, Any], status: str) -> None:
        if row.get('request_reference_id'):
            target = 'CANCELLED' if status == 'CANCELLED' else 'SUPERSEDED'
            def retire() -> Any:
                current = self.state.connection.execute(
                    'SELECT state FROM note_request_references WHERE receipt_id=?', (row['client_request_id'],),
                ).fetchone()
                if current and current['state'] in {'CANCELLED', 'SUPERSEDED'}:
                    return None
                return self.state.transition_note_request_reference(receipt_id=row['client_request_id'],
                    note_key=row['note_key'], attempt_no=row['attempt_no'], state=target, provider='notion',
                    session_provider_page_id=row['session_provider_page_id'], expected_receipt_hash=row['payload_hash'],
                    inactive_reason=status)
            self._journal(row, 'retire', target, retire)
            self.store.revoke_attempt_grants(
                row['note_key'], row['attempt_no'], reason=status.lower(),
            )
            attempt = self.state.get_note_attempt(row['note_key'], row['attempt_no'])
            if attempt.state not in _TERMINAL and self.state.count_active_note_references(row['note_key'], row['attempt_no']) == 0:
                self._transition(row, 'CANCELLED' if status == 'CANCELLED' else 'STALE', require_no_active_references=True)
        elif row.get('head_generation'):
            self._journal(row, 'deactivate_head', status, lambda: self.state.deactivate_study_note_head(
                provider='notion', session_provider_page_id=row['session_provider_page_id'],
                expected_generation=row['head_generation'], expected_request_id=row['client_request_id'],
                expected_receipt_id=row['client_request_id'], expected_receipt_hash=row['payload_hash'], reason=status))
        self.store.set_client_request_status(row['client_request_id'], status)

    def _dispatch(self, draft: Mapping[str, Any]) -> None:
        row = self.store.get_request_for_grant(draft['grant_id'])
        if row is None:
            raise NoteAuthorityChanged('draft request binding unavailable')
        self._publish(row, draft=draft)

    def _publish_seeded(self) -> None:
        for row in self._rows():
            if not row.get('note_key') or row['state'] in _TERMINAL | {'SUPERSEDED'}:
                continue
            attempt = self.state.get_note_attempt(row['note_key'], row['attempt_no'])
            if attempt is not None and attempt.seed_artifact_id:
                self._publish(row, draft=None)

    def _publish(self, row: Mapping[str, Any], *, draft: Mapping[str, Any] | None) -> None:
        key, number = row['note_key'], row['attempt_no']
        attempt = self.state.get_note_attempt(key, number)
        if attempt is None or attempt.state in _TERMINAL:
            if draft:
                self.store.mark_draft_dispatched(draft['draft_id'])
            if attempt:
                current_request = self.store.get_client_request(row['client_request_id'])
                if current_request is not None and current_request['state'] not in (
                    _TERMINAL | {'SUPERSEDED'}
                ):
                    self.store.set_client_request_status(
                        row['client_request_id'], attempt.state,
                    )
            return
        if attempt.next_retry_at and datetime.fromisoformat(self.clock()) < datetime.fromisoformat(attempt.next_retry_at):
            return
        prepared = self.store.get_prepared_context(key, number)
        def fresh() -> None:
            self._current(row, manifest_hash=prepared['evidence_manifest_hash'])
        self.drive_staging.before_write = fresh
        self.notion_bridge.before_write = fresh
        try:
            fresh()
            if draft is None:
                artifact = self.state.get_reusable_note_artifact(key)
                if artifact is None:
                    raise StageAmbiguousError('seeded artifact is unavailable')
                receipt = self.drive_staging.reuse_verified_artifact(note_key=key,
                    output_identity=artifact.output_identity, output_hash=artifact.output_hash,
                    manifest_hash=artifact.manifest_hash)
            else:
                if attempt.state in {'REQUESTED', 'WAITING_CONTEXT'}:
                    self._transition(row, 'GENERATING')
                folder = self.drive_staging.resolve_note_folder(session_derived_folder_id=self._folder(row['session_id']), note_key=key)
                previous = self.store.get_stage_receipt(key, number)
                if previous is not None and previous['state'] == 'UNKNOWN':
                    self.drive_staging.reconcile_unknown(note_key=key, attempt_no=number)
                receipt = self.drive_staging.stage(note_key=key, attempt_no=number, parent_folder_id=folder,
                    filename=key + '.md', content=draft['draft_text'].encode('utf-8'), manifest_hash=prepared['evidence_manifest_hash'])
            if receipt['state'] != 'VERIFIED':
                raise StageAmbiguousError('artifact write requires reconciliation')
            artifact_id = self._record_artifact(row, receipt)
            fresh()
            attempt = self.state.get_note_attempt(key, number)
            if attempt.state == 'GENERATING':
                self._transition(row, 'STAGED', last_successful_stage='STAGED')
            if self.state.get_note_attempt(key, number).state == 'STAGED':
                self._transition(row, 'PUBLISHING')
            pointer = {'note_key': key, 'coverage': prepared['context']['coverage'],
                       'evidence_mode': row['evidence_mode'], 'drive_file_id': receipt['file_id'],
                       'manifest_hash': prepared['evidence_manifest_hash']}
            physical = row['session_provider_page_id']
            prior_block = self.store.get_ai_block(physical)
            if prior_block is not None and prior_block['state'] in {'INTENT', 'UNKNOWN'}:
                self.notion_bridge.reconcile_unknown_block(physical)
            block = self.notion_bridge.write_study_note_ai_block(session_id=physical,
                note_key=key, attempt_no=number, pointer=pointer)
            if block['state'] != 'KNOWN':
                raise BlockCreateAmbiguousError('Notion publication requires reconciliation')
            fresh()
            self._journal(row, 'artifact_publish', [artifact_id, block['block_id']],
                lambda: self.state.transition_note_artifact(artifact_id, 'PUBLISHED',
                    ai_region_id=block['block_id'], ai_block_ids=[block['block_id']], last_publish_hash=block['last_worker_hash']))
            self._transition(row, 'READY' if prepared['context']['coverage'] == 'FULL' else 'PARTIAL')
            self.store.set_client_request_status(row['client_request_id'],
                'READY' if prepared['context']['coverage'] == 'FULL' else 'PARTIAL',
                artifact_link='https://drive.google.com/file/d/' + receipt['file_id'] + '/view')
            if draft:
                self.store.mark_draft_dispatched(draft['draft_id'])
        except NewIngressPending:
            return
        except (NoteAuthorityChanged, EvidenceWaitingError, InvalidSelectionError):
            # A cancellation can race a dispatched write. Preserve its proven
            # artifact without granting permission for another provider effect.
            staged = self.store.get_stage_receipt(key, number)
            if staged is not None and staged['state'] == 'VERIFIED':
                self._record_artifact(row, staged)
                self.store.set_client_request_status(row['client_request_id'], 'STAGED',
                    artifact_link='https://drive.google.com/file/d/' + staged['file_id'] + '/view')
            self._retire_changed(row)
        except UserEditConflictError:
            self._transition(row, 'PARTIAL', error_class='POLICY_DENIED', error_code='user_edit_preserved')
            self.store.set_client_request_status(row['client_request_id'], 'PARTIAL', reason='user_edit_preserved',
                artifact_link='https://drive.google.com/file/d/' + receipt['file_id'] + '/view')
            if draft:
                self.store.mark_draft_dispatched(draft['draft_id'])
        except (StageAmbiguousError, BlockCreateAmbiguousError):
            staged = self.store.get_stage_receipt(key, number)
            link = None
            if staged is not None and staged['state'] == 'VERIFIED' and staged['file_id']:
                link = 'https://drive.google.com/file/d/' + staged['file_id'] + '/view'
            self.store.set_client_request_status(
                row['client_request_id'], 'RECONCILIATION_REQUIRED',
                reason='provider_effect_unknown', artifact_link=link,
            )
        except Exception:  # noqa: BLE001 - provider failures are persisted as fixed, content-free codes
            self._pipeline_failure(row)
        finally:
            self.drive_staging.before_write = lambda: None
            self.notion_bridge.before_write = lambda: None

    def _record_artifact(self, row: Mapping[str, Any], receipt: Mapping[str, Any]) -> str:
        def record() -> str:
            existing = self.state.connection.execute(
                'SELECT * FROM note_artifacts WHERE note_key=? AND output_hash=? AND manifest_hash=?',
                (row['note_key'], receipt['content_hash'], receipt['manifest_hash']),
            ).fetchone()
            if existing is not None:
                if existing['output_identity'] != receipt['file_id'] or existing['state'] == 'STALE':
                    raise StageAmbiguousError('artifact identity conflict')
                identifier = existing['artifact_id']
            else:
                artifact = self.state.record_note_artifact(note_key=row['note_key'], output_identity=receipt['file_id'],
                    output_hash=receipt['content_hash'], manifest_hash=receipt['manifest_hash'],
                    writer_version=self.generator_config_version)
                identifier = artifact.artifact_id
            if existing is None or existing['state'] == 'STAGED':
                self.state.transition_note_artifact(identifier, 'VERIFIED')
            return str(identifier)
        return str(self._journal(row, 'artifact_verified', [receipt['file_id'], receipt['content_hash']], record))

    def _pipeline_failure(self, row: Mapping[str, Any]) -> None:
        attempt = self.state.get_note_attempt(row['note_key'], row['attempt_no'])
        if attempt.state in _TERMINAL:
            return
        if attempt.retry_count >= 3:
            self._transition(row, 'FAILED', error_class='PROVIDER_UNAVAILABLE', error_code='pipeline_retry_limit')
            self.store.set_client_request_status(row['client_request_id'], 'FAILED', reason='pipeline_retry_limit')
            return
        retry = attempt.retry_count + 1
        after = datetime.fromisoformat(self.clock()) + timedelta(seconds=pipeline_retry_delay_seconds(retry))
        self._transition(row, attempt.state, retry_count=retry, next_retry_at=after.isoformat(),
                         error_class='PROVIDER_UNAVAILABLE', error_code='pipeline_transient_failure')
        self.store.set_client_request_status(row['client_request_id'], 'RETRY_WAIT', reason='pipeline_transient_failure')


__all__ = ['NewIngressPending', 'NoteAuthorityChanged', 'StudyNoteHandler']
