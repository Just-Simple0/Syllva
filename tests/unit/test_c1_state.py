from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from uls.intake.identity import derive_study_note_key
from uls.state.sqlite import SQLiteStateStore

pytestmark = pytest.mark.unit


def _seed_intake(state: SQLiteStateStore, intake_id: str, provider_file_id: str) -> None:
    state.connection.execute(
        """
        INSERT INTO intake_items(
            intake_id, provider, provider_file_id, semester,
            original_parent_id, observed_parent_id, original_name, mime_type,
            source_hash, source_version, status, first_seen_at, last_seen_at
        ) VALUES (?, 'google_drive', ?, '2026-2', 'upload', 'upload',
                  'lecture.pdf', 'application/pdf', 'sha256:source', 1,
                  'DISCOVERED', '2026-09-18T00:00:00+00:00',
                  '2026-09-18T00:00:00+00:00')
        """,
        (intake_id, provider_file_id),
    )


def _reservation_kwargs(
    *,
    intake_id: str = "intake-1",
    reservation_id: str = "reserve-1",
    entity_kind: str = "SESSION",
    entity_app_id: str = "session-app-1",
    operation_key: str = "op-1",
) -> dict[str, str]:
    return {
        "reservation_id": reservation_id,
        "intake_id": intake_id,
        "entity_kind": entity_kind,
        "entity_app_id": entity_app_id,
        "parent_folder_id": "course-folder",
        "marker_key": f"marker-{reservation_id}",
        "state": "PENDING",
        "plan_revision": "plan-r1",
        "receipt_id": f"receipt-{reservation_id}",
        "plan_hash": f"plan-hash-{reservation_id}",
        "source_snapshot_hash": f"source-snapshot-{reservation_id}",
        "target_snapshot_hash": f"target-snapshot-{reservation_id}",
        "operation_key": operation_key,
    }


def _claim_and_job(state: SQLiteStateStore, receipt_id: str = "receipt-1") -> str:
    state.claim_study_note_head(
        provider="notion",
        session_provider_page_id="session-page",
        course_key="2026-2_COMP322-002",
        session_id="session-1",
        request_id=f"request-{receipt_id}",
        receipt_id=receipt_id,
        receipt_hash=f"hash-{receipt_id}",
        evidence_mode="SOURCE_ONLY",
        selected_materials=["material-1"],
    )
    job = state.ensure_note_job(
        course_key="2026-2_COMP322-002",
        session_id="session-1",
        evidence_manifest_hash="manifest-1",
        learner_request_hash="learner-1",
        template_version="template-v1",
        generator_config_version="generator-v1",
    )
    return job.note_key


def _attach(
    state: SQLiteStateStore,
    *,
    receipt_id: str,
    generation: int,
    note_key: str,
):
    return state.attach_note_request(
        provider="notion",
        session_provider_page_id="session-page",
        head_generation=generation,
        receipt_id=receipt_id,
        receipt_hash=f"hash-{receipt_id}",
        provider_request_id=f"request-{receipt_id}",
        note_key=note_key,
        course_key="2026-2_COMP322-002",
        session_id="session-1",
        evidence_manifest_hash="manifest-1",
        learner_request_hash="learner-1",
        template_version="template-v1",
        generator_config_version="generator-v1",
    )


def test_fresh_c1_schema_and_identity_are_stable(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    with SQLiteStateStore(path) as state:
        tables = {
            row[0]
            for row in state.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {
            "study_note_heads",
            "note_jobs",
            "note_attempts",
            "note_request_references",
            "note_artifacts",
        }.issubset(tables)
        reservation_columns = {
            row[1]
            for row in state.connection.execute(
                "PRAGMA table_info(entity_reservations)"
            ).fetchall()
        }
        assert {
            "receipt_id",
            "plan_hash",
            "source_snapshot_hash",
            "target_snapshot_hash",
            "operation_key",
            "updated_at",
            "released_at",
        }.issubset(reservation_columns)
        state.apply_migrations()

    expected = derive_study_note_key(
        course_key="2026-2_COMP322-002",
        session_id="session-1",
        evidence_manifest_hash="manifest-1",
        learner_request_hash="learner-1",
        template_version="template-v1",
        generator_config_version="generator-v1",
    )
    with SQLiteStateStore(path) as reopened:
        actual = reopened.ensure_note_job(
            course_key="2026-2_COMP322-002",
            session_id="session-1",
            evidence_manifest_hash="manifest-1",
            learner_request_hash="learner-1",
            template_version="template-v1",
            generator_config_version="generator-v1",
        )
        assert actual.note_key == expected


def test_legacy_reservation_migration_preserves_rows_and_child_fk(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with SQLiteStateStore(path) as state:
        _seed_intake(state, "intake-1", "file-1")
        state.reserve_entity(**_reservation_kwargs())
        state.record_session_source_binding(
            binding_id="binding-1",
            course_key="2026-2_COMP322-002",
            session_id="session-1",
            provider="google_drive",
            provider_file_id="file-1",
            reservation_id="reserve-1",
            state="PENDING",
        )

    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.executescript(
        """
        CREATE TABLE entity_reservations_legacy_fixture (
            reservation_id TEXT PRIMARY KEY,
            intake_id TEXT NOT NULL REFERENCES intake_items(intake_id),
            entity_kind TEXT NOT NULL,
            entity_app_id TEXT NOT NULL,
            parent_folder_id TEXT NOT NULL,
            marker_key TEXT NOT NULL,
            state TEXT NOT NULL,
            plan_revision TEXT NOT NULL,
            source_file_id TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(intake_id, entity_kind),
            UNIQUE(entity_kind, entity_app_id)
        );
        INSERT INTO entity_reservations_legacy_fixture(
            reservation_id, intake_id, entity_kind, entity_app_id,
            parent_folder_id, marker_key, state, plan_revision,
            source_file_id, created_at
        )
        SELECT reservation_id, intake_id, entity_kind, entity_app_id,
               parent_folder_id, marker_key, state, plan_revision,
               source_file_id, created_at
        FROM entity_reservations;
        DROP TABLE entity_reservations;
        ALTER TABLE entity_reservations_legacy_fixture RENAME TO entity_reservations;
        """
    )
    connection.commit()
    connection.close()

    with SQLiteStateStore(path) as migrated:
        row = migrated.get_entity_reservation("reserve-1")
        assert row is not None
        assert row.entity_app_id == "session-app-1"
        assert row.updated_at == row.created_at
        fk_targets = {
            item[2]
            for item in migrated.connection.execute(
                "PRAGMA foreign_key_list(entity_reservations)"
            ).fetchall()
        }
        assert "intake_items" in fk_targets
        assert migrated.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        binding = migrated.connection.execute(
            "SELECT reservation_id FROM session_source_bindings WHERE binding_id='binding-1'"
        ).fetchone()
        assert binding is not None and binding["reservation_id"] == "reserve-1"
        migrated.apply_migrations()
        assert migrated.get_entity_reservation("reserve-1") is not None

        with SQLiteStateStore(tmp_path / "fresh.sqlite3") as fresh:
            migrated_columns = [
                tuple(row)
                for row in migrated.connection.execute(
                    "PRAGMA table_info(entity_reservations)"
                ).fetchall()
            ]
            fresh_columns = [
                tuple(row)
                for row in fresh.connection.execute(
                    "PRAGMA table_info(entity_reservations)"
                ).fetchall()
            ]
            assert migrated_columns == fresh_columns

            migrated_indexes = sorted(
                tuple(row[1:5])
                for row in migrated.connection.execute(
                    "PRAGMA index_list(entity_reservations)"
                ).fetchall()
            )
            fresh_indexes = sorted(
                tuple(row[1:5])
                for row in fresh.connection.execute(
                    "PRAGMA index_list(entity_reservations)"
                ).fetchall()
            )
            assert migrated_indexes == fresh_indexes


def test_reservation_history_safe_replacement_and_consumed_id_guard(tmp_path: Path) -> None:
    with SQLiteStateStore(tmp_path / "state.sqlite3") as state:
        _seed_intake(state, "intake-1", "file-1")
        _seed_intake(state, "intake-2", "file-2")
        first = state.reserve_entity(**_reservation_kwargs())
        replacement_values = _reservation_kwargs(
            entity_app_id="session-app-2",
            operation_key="op-2",
        )
        replacement_values.pop("reservation_id")
        replacement = state.release_pending_entity_reservation(
            first.reservation_id, **replacement_values
        )
        historical = state.get_entity_reservation("reserve-1")
        assert historical is not None and historical.state == "RELEASED"
        assert historical.released_at is not None
        assert replacement.state == "PENDING"
        assert state.get_entity_reservation(
            intake_id="intake-1", entity_kind="SESSION"
        ) == replacement

        with pytest.raises(ValueError, match="already reserved"):
            state.reserve_entity(
                **_reservation_kwargs(
                    intake_id="intake-2",
                    reservation_id="reserve-3",
                    entity_app_id="session-app-1",
                    operation_key="op-3",
                )
            )

        applied = state.update_entity_reservation(replacement.reservation_id, state="APPLIED")
        assert applied.state == "APPLIED"
        with pytest.raises(ValueError, match="invalid entity reservation transition"):
            state.update_entity_reservation(
                replacement.reservation_id, state="RECONCILE_REQUIRED"
            )
        rejected_values = _reservation_kwargs(
            entity_app_id="session-app-4",
            operation_key="op-4",
        )
        rejected_values.pop("reservation_id")
        with pytest.raises(ValueError, match="only a PENDING or RECONCILE_REQUIRED reservation"):
            state.release_pending_entity_reservation(
                replacement.reservation_id,
                **rejected_values,
            )


def test_provider_write_attempt_blocks_unsafe_reservation_release(tmp_path: Path) -> None:
    with SQLiteStateStore(tmp_path / "state.sqlite3") as state:
        _seed_intake(state, "intake-1", "file-1")
        state.reserve_entity(**_reservation_kwargs())
        state.record_provider_write_attempt(
            attempt_id="write-1",
            operation="CREATE_SESSION",
            operation_key="op-1",
            provider="notion",
            reservation_id="reserve-1",
            stage="PREWRITE",
        )
        replacement_values = _reservation_kwargs(
            entity_app_id="session-app-2",
            operation_key="op-2",
        )
        replacement_values.pop("reservation_id")
        with pytest.raises(ValueError, match="provider mutation attempt exists"):
            state.release_pending_entity_reservation(
                "reserve-1",
                **replacement_values,
            )
        current = state.get_entity_reservation("reserve-1")
        assert current is not None and current.state == "PENDING"


def test_reconcile_required_release_needs_durable_no_mutation_proof(tmp_path: Path) -> None:
    with SQLiteStateStore(tmp_path / "state.sqlite3") as state:
        _seed_intake(state, "intake-1", "file-1")
        state.reserve_entity(**_reservation_kwargs())
        state.record_provider_write_attempt(
            attempt_id="write-1",
            operation="CREATE_SESSION",
            operation_key="op-1",
            provider="notion",
            reservation_id="reserve-1",
            stage="DISPATCHED",
            response_state="UNKNOWN",
        )
        state.update_entity_reservation("reserve-1", state="RECONCILE_REQUIRED")

        replacement_values = _reservation_kwargs(
            entity_app_id="session-app-2",
            operation_key="op-2",
        )
        replacement_values.pop("reservation_id")
        with pytest.raises(ValueError, match="verified no-mutation reconciliation"):
            state.release_pending_entity_reservation("reserve-1", **replacement_values)

        current = state.get_entity_reservation("reserve-1")
        assert current is not None and current.state == "RECONCILE_REQUIRED"
        assert state.get_entity_reservation(
            intake_id="intake-1", entity_kind="SESSION"
        ) == current


def test_reservation_reconciliation_markers_require_dedicated_api(tmp_path: Path) -> None:
    with SQLiteStateStore(tmp_path / "state.sqlite3") as state:
        _seed_intake(state, "intake-1", "file-1")
        state.reserve_entity(**_reservation_kwargs())

        with pytest.raises(ValueError, match="dedicated reservation reconciliation API"):
            state.record_provider_write_attempt(
                attempt_id="forged-write",
                operation="CREATE_SESSION",
                operation_key="forged-op",
                provider="notion",
                reservation_id="reserve-1",
                stage="RECONCILED",
            )

        state.record_provider_write_attempt(
            attempt_id="write-1",
            operation="CREATE_SESSION",
            operation_key="op-1",
            provider="notion",
            reservation_id="reserve-1",
            stage="DISPATCHED",
            response_state="UNKNOWN",
        )
        with pytest.raises(ValueError, match="dedicated reservation reconciliation API"):
            state.update_provider_write_attempt(
                "op-1",
                response_state="RECONCILED_NO_MUTATION",
            )
        with pytest.raises(ValueError, match="RECONCILE_REQUIRED state"):
            state.record_reservation_no_mutation_reconciliation(
                "reserve-1",
                operation_key="op-1",
                verified_readback={"provider_mutation": False},
            )

        state.update_entity_reservation("reserve-1", state="RECONCILE_REQUIRED")
        with pytest.raises(ValueError, match="verified reconciliation readback is required"):
            state.record_reservation_no_mutation_reconciliation(
                "reserve-1",
                operation_key="op-1",
                verified_readback=None,
            )
        with pytest.raises(ValueError, match="not linked to reservation"):
            state.record_reservation_no_mutation_reconciliation(
                "reserve-1",
                operation_key="missing-op",
                verified_readback={"provider_mutation": False},
            )


def test_reconcile_required_apply_needs_verified_linked_readback(tmp_path: Path) -> None:
    with SQLiteStateStore(tmp_path / "state.sqlite3") as state:
        _seed_intake(state, "intake-1", "file-1")
        state.reserve_entity(**_reservation_kwargs())
        state.record_provider_write_attempt(
            attempt_id="write-1",
            operation="CREATE_SESSION",
            operation_key="op-1",
            provider="notion",
            reservation_id="reserve-1",
            stage="DISPATCHED",
            response_state="UNKNOWN",
        )
        state.update_entity_reservation("reserve-1", state="RECONCILE_REQUIRED")

        with pytest.raises(ValueError, match="verified linked readback evidence"):
            state.update_entity_reservation("reserve-1", state="APPLIED")

        state.update_provider_write_attempt(
            "op-1",
            response_state="READBACK_OK",
            readback_json={"entity_app_id": "session-app-1", "provider_mutation": True},
        )
        applied = state.update_entity_reservation("reserve-1", state="APPLIED")
        assert applied.state == "APPLIED"


def test_reconciled_no_mutation_release_is_atomic_and_checks_all_attempts(tmp_path: Path) -> None:
    with SQLiteStateStore(tmp_path / "state.sqlite3") as state:
        _seed_intake(state, "intake-1", "file-1")
        state.reserve_entity(**_reservation_kwargs())
        for attempt_id, operation_key in (("write-1", "op-1"), ("write-2", "op-extra")):
            state.record_provider_write_attempt(
                attempt_id=attempt_id,
                operation="CREATE_SESSION",
                operation_key=operation_key,
                provider="notion",
                reservation_id="reserve-1",
                stage="DISPATCHED",
                response_state="UNKNOWN",
            )
        state.update_entity_reservation("reserve-1", state="RECONCILE_REQUIRED")
        state.record_reservation_no_mutation_reconciliation(
            "reserve-1",
            operation_key="op-1",
            verified_readback={"provider_mutation": False, "operation_key": "op-1"},
        )

        replacement_values = _reservation_kwargs(
            entity_app_id="session-app-2",
            operation_key="op-2",
        )
        replacement_values.pop("reservation_id")
        with pytest.raises(ValueError, match="all reservation write attempts"):
            state.release_pending_entity_reservation("reserve-1", **replacement_values)
        assert state.get_entity_reservation("reserve-1").state == "RECONCILE_REQUIRED"
        assert state.connection.execute(
            "SELECT COUNT(*) FROM entity_reservations"
        ).fetchone()[0] == 1

        state.record_reservation_no_mutation_reconciliation(
            "reserve-1",
            operation_key="op-extra",
            verified_readback={"provider_mutation": False, "operation_key": "op-extra"},
        )
        replacement = state.release_pending_entity_reservation(
            "reserve-1",
            **replacement_values,
        )
        released = state.get_entity_reservation("reserve-1")
        assert released is not None and released.state == "RELEASED"
        assert released.released_at is not None
        assert replacement.state == "PENDING"
        assert replacement.entity_app_id == "session-app-2"


def test_deactivate_study_note_head_requires_exact_current_identity(tmp_path: Path) -> None:
    with SQLiteStateStore(tmp_path / "state.sqlite3") as state:
        _claim_and_job(state, "receipt-1")

        stale_request = state.deactivate_study_note_head(
            provider="notion",
            session_provider_page_id="session-page",
            expected_generation=1,
            expected_request_id="request-other",
            expected_receipt_id="receipt-1",
            expected_receipt_hash="hash-receipt-1",
            reason="stale request",
        )
        assert stale_request.active == 1

        stale_hash = state.deactivate_study_note_head(
            provider="notion",
            session_provider_page_id="session-page",
            expected_generation=1,
            expected_request_id="request-receipt-1",
            expected_receipt_id="receipt-1",
            expected_receipt_hash="hash-other",
            reason="stale receipt",
        )
        assert stale_hash.active == 1

        deactivated = state.deactivate_study_note_head(
            provider="notion",
            session_provider_page_id="session-page",
            expected_generation=1,
            expected_request_id="request-receipt-1",
            expected_receipt_id="receipt-1",
            expected_receipt_hash="hash-receipt-1",
            reason="cancelled",
        )
        assert deactivated.active == 0
        assert deactivated.inactive_reason == "cancelled"


def test_deactivate_study_note_head_rejects_stale_note_key_and_attempt_guard(
    tmp_path: Path,
) -> None:
    """A caller holding a pre-attach head snapshot must not deactivate past an
    attempt attachment that happened after that snapshot was taken, even though
    generation/request/receipt identity are unchanged (attach does not bump
    generation). Passing expected_note_key/expected_attempt_no closes this gap."""

    with SQLiteStateStore(tmp_path / "state.sqlite3") as state:
        note_key = _claim_and_job(state, "receipt-1")

        # Caller A captures the pre-attach snapshot: no note_key/attempt yet.
        stale_snapshot = state.get_study_note_head("notion", "session-page")
        assert stale_snapshot is not None
        assert stale_snapshot.current_note_key is None

        # Caller B attaches the same active receipt to an attempt without
        # changing generation/request_id/receipt_id/receipt_hash.
        _attach(state, receipt_id="receipt-1", generation=1, note_key=note_key)

        # Caller A's stale expectation (still expects no note_key attached) must
        # not be sufficient to deactivate a head that has since progressed, even
        # though generation/request_id/receipt_id/receipt_hash are unchanged.
        guarded = state.deactivate_study_note_head(
            provider="notion",
            session_provider_page_id="session-page",
            expected_generation=1,
            expected_request_id="request-receipt-1",
            expected_receipt_id="receipt-1",
            expected_receipt_hash="hash-receipt-1",
            reason="stale-deactivate",
            expected_note_key=None,
            expected_attempt_no=None,
        )
        assert guarded.active == 1

        wrong_attempt = state.deactivate_study_note_head(
            provider="notion",
            session_provider_page_id="session-page",
            expected_generation=1,
            expected_request_id="request-receipt-1",
            expected_receipt_id="receipt-1",
            expected_receipt_hash="hash-receipt-1",
            reason="stale-deactivate",
            expected_note_key=note_key,
            expected_attempt_no=99,
        )
        assert wrong_attempt.active == 1

        deactivated = state.deactivate_study_note_head(
            provider="notion",
            session_provider_page_id="session-page",
            expected_generation=1,
            expected_request_id="request-receipt-1",
            expected_receipt_id="receipt-1",
            expected_receipt_hash="hash-receipt-1",
            reason="matched-deactivate",
            expected_note_key=note_key,
            expected_attempt_no=1,
        )
        assert deactivated.active == 0
        assert deactivated.inactive_reason == "matched-deactivate"

        # Confirms the default (sentinel) behavior preserves the prior contract:
        # omitting the two new params skips the note_key/attempt_no check.


def test_deactivate_study_note_head_default_guard_skips_note_key_check(
    tmp_path: Path,
) -> None:
    with SQLiteStateStore(tmp_path / "state.sqlite3") as state:
        note_key = _claim_and_job(state, "receipt-1")
        _attach(state, receipt_id="receipt-1", generation=1, note_key=note_key)

        deactivated = state.deactivate_study_note_head(
            provider="notion",
            session_provider_page_id="session-page",
            expected_generation=1,
            expected_request_id="request-receipt-1",
            expected_receipt_id="receipt-1",
            expected_receipt_hash="hash-receipt-1",
            reason="legacy-caller-without-note-key-guard",
        )
        assert deactivated.active == 0


def test_attach_note_request_requires_exact_receipt_hash(tmp_path: Path) -> None:
    with SQLiteStateStore(tmp_path / "state.sqlite3") as state:
        note_key = _claim_and_job(state, "receipt-1")

        with pytest.raises(ValueError, match="exact active head"):
            state.attach_note_request(
                provider="notion",
                session_provider_page_id="session-page",
                head_generation=1,
                receipt_id="receipt-1",
                receipt_hash="hash-other",
                provider_request_id="request-receipt-1",
                note_key=note_key,
                course_key="2026-2_COMP322-002",
                session_id="session-1",
                evidence_manifest_hash="manifest-1",
                learner_request_hash="learner-1",
                template_version="template-v1",
                generator_config_version="generator-v1",
            )

        assert state.connection.execute(
            "SELECT COUNT(*) FROM note_attempts WHERE note_key=?", (note_key,)
        ).fetchone()[0] == 0
        assert state.connection.execute(
            "SELECT COUNT(*) FROM note_request_references WHERE receipt_id='receipt-1'"
        ).fetchone()[0] == 0


def test_existing_receipt_rediscovery_is_state_free(tmp_path: Path) -> None:
    with SQLiteStateStore(tmp_path / "state.sqlite3") as state:
        note_key = _claim_and_job(state, "receipt-1")
        attempt, reference, generation_required = _attach(
            state, receipt_id="receipt-1", generation=1, note_key=note_key
        )
        assert generation_required is True

        before = {
            table: [dict(row) for row in state.connection.execute(f"SELECT * FROM {table}")]
            for table in (
                "study_note_heads",
                "note_jobs",
                "note_attempts",
                "note_request_references",
            )
        }
        rediscovered = state.attach_note_request(
            provider="notion",
            session_provider_page_id="session-page",
            head_generation=1,
            receipt_id="receipt-1",
            receipt_hash="hash-does-not-match-current-head",
            provider_request_id="request-receipt-1",
            note_key=note_key,
            course_key="2026-2_COMP322-002",
            session_id="session-1",
            evidence_manifest_hash="manifest-1",
            learner_request_hash="learner-1",
            template_version="template-v1",
            generator_config_version="generator-v1",
        )
        after = {
            table: [dict(row) for row in state.connection.execute(f"SELECT * FROM {table}")]
            for table in before
        }

        assert rediscovered[0].attempt_no == attempt.attempt_no
        assert rediscovered[1].reference_id == reference.reference_id
        assert rediscovered[2] is False
        assert after == before


def test_attach_note_request_atomically_creates_missing_note_job(tmp_path: Path) -> None:
    with SQLiteStateStore(tmp_path / "state.sqlite3") as state:
        state.claim_study_note_head(
            provider="notion",
            session_provider_page_id="session-page",
            course_key="2026-2_COMP322-002",
            session_id="session-1",
            request_id="request-receipt-1",
            receipt_id="receipt-1",
            receipt_hash="hash-receipt-1",
        )
        note_key = derive_study_note_key(
            course_key="2026-2_COMP322-002",
            session_id="session-1",
            evidence_manifest_hash="manifest-1",
            learner_request_hash="learner-1",
            template_version="template-v1",
            generator_config_version="generator-v1",
        )
        assert state.get_note_job(note_key) is None

        attempt, reference, generation_required = _attach(
            state,
            receipt_id="receipt-1",
            generation=1,
            note_key=note_key,
        )

        job = state.get_note_job(note_key)
        assert job is not None and job.current_attempt_no == 1
        assert attempt.attempt_no == 1 and reference.attempt_no == 1
        assert generation_required is True


def test_reference_transition_requires_exact_attempt_identity(tmp_path: Path) -> None:
    with SQLiteStateStore(tmp_path / "state.sqlite3") as state:
        note_key = _claim_and_job(state, "receipt-1")
        _attach(state, receipt_id="receipt-1", generation=1, note_key=note_key)

        with pytest.raises(ValueError, match="expected attempt identity"):
            state.transition_note_request_reference(
                receipt_id="receipt-1",
                note_key=note_key,
                attempt_no=2,
                state="CANCELLED",
                provider="notion",
                session_provider_page_id="session-page",
                expected_receipt_hash="hash-receipt-1",
                inactive_reason="wrong attempt",
            )

        reference = state.connection.execute(
            "SELECT state FROM note_request_references WHERE receipt_id='receipt-1'"
        ).fetchone()
        head = state.get_study_note_head("notion", "session-page")
        assert reference is not None and reference["state"] == "ACTIVE"
        assert head is not None and head.active == 1


def test_transition_note_attempt_blocks_cancellation_with_active_references(
    tmp_path: Path,
) -> None:
    """require_no_active_references closes the TOCTOU where a caller counts zero
    active references, then another attach_note_request shares the same
    non-terminal attempt before the caller's cancellation transition commits.
    The guard re-checks the count atomically inside the same transaction."""

    with SQLiteStateStore(tmp_path / "state.sqlite3") as state:
        note_key = _claim_and_job(state, "receipt-1")
        attempt, _reference, _generation_required = _attach(
            state, receipt_id="receipt-1", generation=1, note_key=note_key
        )
        assert state.count_active_note_references(note_key, attempt.attempt_no) == 1

        with pytest.raises(ValueError, match="active note request references remain"):
            state.transition_note_attempt(
                note_key,
                attempt.attempt_no,
                "CANCELLED",
                require_no_active_references=True,
            )
        unchanged = state.get_note_attempt(note_key, attempt.attempt_no)
        assert unchanged is not None and unchanged.state == "REQUESTED"

        state.transition_note_request_reference(
            receipt_id="receipt-1",
            note_key=note_key,
            attempt_no=attempt.attempt_no,
            state="CANCELLED",
            provider="notion",
            session_provider_page_id="session-page",
            expected_receipt_hash="hash-receipt-1",
            inactive_reason="request withdrawn",
        )
        assert state.count_active_note_references(note_key, attempt.attempt_no) == 0

        cancelled = state.transition_note_attempt(
            note_key,
            attempt.attempt_no,
            "CANCELLED",
            require_no_active_references=True,
        )
        assert cancelled.state == "CANCELLED"


def test_study_note_generation_idempotency_sharing_and_stale_cancel(tmp_path: Path) -> None:
    with SQLiteStateStore(tmp_path / "state.sqlite3") as state:
        note_key = _claim_and_job(state, "receipt-1")
        head1 = state.get_study_note_head("notion", "session-page")
        assert head1 is not None and head1.generation == 1

        same = state.claim_study_note_head(
            provider="notion",
            session_provider_page_id="session-page",
            course_key="2026-2_COMP322-002",
            session_id="session-1",
            request_id="request-receipt-1",
            receipt_id="receipt-1",
            receipt_hash="hash-receipt-1",
            evidence_mode="SOURCE_ONLY",
            selected_materials=["material-1"],
        )
        assert same.generation == 1
        attempt1, reference1, generation_required = _attach(
            state, receipt_id="receipt-1", generation=1, note_key=note_key
        )
        assert attempt1.attempt_no == 1 and attempt1.state == "REQUESTED"
        assert generation_required is True

        rediscovered = _attach(
            state, receipt_id="receipt-1", generation=1, note_key=note_key
        )
        assert rediscovered[0].attempt_no == 1
        assert rediscovered[1].reference_id == reference1.reference_id
        assert rediscovered[2] is False

        head2 = state.claim_study_note_head(
            provider="notion",
            session_provider_page_id="session-page",
            course_key="2026-2_COMP322-002",
            session_id="session-1",
            request_id="request-receipt-2",
            receipt_id="receipt-2",
            receipt_hash="hash-receipt-2",
            evidence_mode="SOURCE_ONLY",
            selected_materials=["material-1"],
        )
        assert head2.generation == 2

        stale_rediscovery = _attach(
            state, receipt_id="receipt-1", generation=1, note_key=note_key
        )
        assert stale_rediscovery[0].attempt_no == 1
        assert stale_rediscovery[1].reference_id == reference1.reference_id
        assert stale_rediscovery[2] is False
        unchanged_head2 = state.get_study_note_head("notion", "session-page")
        assert unchanged_head2 is not None
        assert unchanged_head2.generation == 2
        assert unchanged_head2.current_receipt_id == "receipt-2"
        assert unchanged_head2.current_attempt_no is None

        attempt2, _, second_generation_required = _attach(
            state, receipt_id="receipt-2", generation=2, note_key=note_key
        )
        assert attempt2.attempt_no == 1
        assert second_generation_required is False
        assert state.count_active_note_references(note_key, 1) == 2

        state.transition_note_request_reference(
            receipt_id="receipt-1",
            note_key=note_key,
            attempt_no=1,
            state="CANCELLED",
            provider="notion",
            session_provider_page_id="session-page",
            expected_receipt_hash="hash-receipt-1",
            inactive_reason="superseded request",
        )
        current_head = state.get_study_note_head("notion", "session-page")
        assert current_head is not None
        assert current_head.generation == 2 and current_head.active == 1
        assert current_head.current_attempt_no == 1
        assert state.count_active_note_references(note_key, 1) == 1


def test_terminal_attempt_artifact_reuse_and_old_attempt_cannot_rewind_head(tmp_path: Path) -> None:
    with SQLiteStateStore(tmp_path / "state.sqlite3") as state:
        note_key = _claim_and_job(state, "receipt-1")
        first, _, _ = _attach(
            state, receipt_id="receipt-1", generation=1, note_key=note_key
        )
        failed = state.transition_note_attempt(
            note_key,
            first.attempt_no,
            "FAILED",
            retry_count=3,
            error_class="PROVIDER",
            error_code="EXHAUSTED",
        )
        assert failed.state == "FAILED" and failed.retry_count == 3
        with pytest.raises(ValueError, match="terminal note attempts are immutable"):
            state.transition_note_attempt(note_key, 1, "REQUESTED")

        artifact = state.record_note_artifact(
            artifact_id="artifact-1",
            note_key=note_key,
            output_identity="notion:session-page:ai-region",
            output_hash="output-hash-1",
            manifest_hash="manifest-hash-1",
            writer_version="writer-v1",
            ai_region_id="ai-region",
            ai_block_ids=["block-1"],
            state="STAGED",
        )
        state.transition_note_artifact(artifact.artifact_id, "VERIFIED")

        head2 = state.claim_study_note_head(
            provider="notion",
            session_provider_page_id="session-page",
            course_key="2026-2_COMP322-002",
            session_id="session-1",
            request_id="request-receipt-2",
            receipt_id="receipt-2",
            receipt_hash="hash-receipt-2",
        )
        second, _, generation_required = _attach(
            state,
            receipt_id="receipt-2",
            generation=head2.generation,
            note_key=note_key,
        )
        assert second.attempt_no == 2
        assert second.state == "STAGED"
        assert second.seed_artifact_id == "artifact-1"
        assert generation_required is False

        current = state.get_study_note_head("notion", "session-page")
        assert current is not None and current.current_attempt_no == 2
        with pytest.raises(ValueError, match="terminal note attempts are immutable"):
            state.transition_note_attempt(note_key, 1, "FAILED", error_code="late")
        unchanged = state.get_study_note_head("notion", "session-page")
        assert unchanged is not None and unchanged.current_attempt_no == 2


def test_reopen_preserves_note_retry_reference_artifact_and_pointer(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    with SQLiteStateStore(path) as state:
        note_key = _claim_and_job(state, "receipt-1")
        attempt, reference, _ = _attach(
            state, receipt_id="receipt-1", generation=1, note_key=note_key
        )
        state.transition_note_attempt(
            note_key,
            attempt.attempt_no,
            "WAITING_CONTEXT",
            retry_count=2,
            next_retry_at="2026-09-18T12:00:00+00:00",
            last_successful_stage="REQUESTED",
        )
        artifact = state.record_note_artifact(
            artifact_id="artifact-staged",
            note_key=note_key,
            output_identity="notion:session-page:staged",
            output_hash="output-hash-staged",
            manifest_hash="manifest-hash-staged",
            writer_version="writer-v1",
        )
        assert reference.reference_id
        assert artifact.state == "STAGED"

    with SQLiteStateStore(path) as reopened:
        head = reopened.get_study_note_head("notion", "session-page")
        persisted = reopened.get_note_attempt(note_key, 1)
        assert head is not None and head.current_attempt_no == 1
        assert persisted is not None
        assert persisted.state == "WAITING_CONTEXT"
        assert persisted.retry_count == 2
        assert persisted.next_retry_at == "2026-09-18T12:00:00+00:00"
        row = reopened.connection.execute(
            "SELECT * FROM note_request_references WHERE receipt_id='receipt-1'"
        ).fetchone()
        assert row is not None and row["attempt_no"] == 1
        artifact_row = reopened.connection.execute(
            "SELECT * FROM note_artifacts WHERE artifact_id='artifact-staged'"
        ).fetchone()
        assert artifact_row is not None and artifact_row["state"] == "STAGED"


def test_concurrent_same_receipt_allocates_one_attempt_and_reference(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    with SQLiteStateStore(path) as state:
        note_key = _claim_and_job(state, "receipt-1")

    def attach_once() -> tuple[int, str, bool]:
        with SQLiteStateStore(path) as worker:
            attempt, reference, required = _attach(
                worker,
                receipt_id="receipt-1",
                generation=1,
                note_key=note_key,
            )
            return attempt.attempt_no, reference.reference_id, required

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attach_once(), range(2)))

    assert {item[0] for item in results} == {1}
    assert len({item[1] for item in results}) == 1
    assert sorted(item[2] for item in results) == [False, True]
    with SQLiteStateStore(path) as state:
        assert state.connection.execute(
            "SELECT COUNT(*) FROM note_attempts WHERE note_key=?", (note_key,)
        ).fetchone()[0] == 1
        assert state.connection.execute(
            "SELECT COUNT(*) FROM note_request_references WHERE receipt_id='receipt-1'"
        ).fetchone()[0] == 1


def test_artifact_metadata_is_body_free_and_only_one_reusable_artifact_exists(tmp_path: Path) -> None:
    with SQLiteStateStore(tmp_path / "state.sqlite3") as state:
        note_key = _claim_and_job(state, "receipt-1")
        columns = {
            row[1]
            for row in state.connection.execute("PRAGMA table_info(note_artifacts)").fetchall()
        }
        assert "body" not in columns
        assert "source_body" not in columns

        first = state.record_note_artifact(
            artifact_id="artifact-1",
            note_key=note_key,
            output_identity="notion:session-page:ai-1",
            output_hash="output-1",
            manifest_hash="manifest-1",
            writer_version="writer-v1",
        )
        duplicate = state.record_note_artifact(
            artifact_id="artifact-different-id",
            note_key=note_key,
            output_identity="notion:session-page:ai-1",
            output_hash="output-1",
            manifest_hash="manifest-1",
            writer_version="writer-v1",
        )
        assert duplicate.artifact_id == first.artifact_id
        state.transition_note_artifact(first.artifact_id, "VERIFIED")

        second = state.record_note_artifact(
            artifact_id="artifact-2",
            note_key=note_key,
            output_identity="notion:session-page:ai-2",
            output_hash="output-2",
            manifest_hash="manifest-2",
            writer_version="writer-v1",
        )
        with pytest.raises(sqlite3.IntegrityError):
            state.transition_note_artifact(second.artifact_id, "VERIFIED")
        reusable = state.get_reusable_note_artifact(note_key)
        assert reusable is not None and reusable.artifact_id == first.artifact_id
