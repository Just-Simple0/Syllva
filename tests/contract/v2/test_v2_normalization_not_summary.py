"""C-12: Partial never silently becomes Ready; a summary is never a normalization output.

Design §2 I3, implementation spec §3.7, §11.2.
"""

from __future__ import annotations

import sqlite3

import pytest
from conftest import NOW, blob, seed_catalog

from syllva.domain.enums import ProcessingStatus
from syllva.domain.errors import PolicyViolation
from syllva.state.v2.database import Database
from syllva.state.v2.ownership import STATUS_TRANSITIONS, Actor, can_transition

pytestmark = pytest.mark.contract


def test_transition_table_matches_domain_enum_and_forbids_partial_to_ready() -> None:
    assert set(STATUS_TRANSITIONS) == {status.value for status in ProcessingStatus}
    assert not can_transition("PARTIAL", "READY")
    assert can_transition("PARTIAL", "PROCESSING")  # reprocessing is the way out


def test_writer_refuses_partial_to_ready(v2_db: Database) -> None:
    seed_catalog(v2_db.connection)
    writer = v2_db.writer()
    writer.transition_material_status(Actor.INGESTER, "M-0001", "PROCESSING")
    writer.transition_material_status(Actor.INGESTER, "M-0001", "PARTIAL")
    with pytest.raises(PolicyViolation, match="transition not allowed"):
        writer.transition_material_status(Actor.INGESTER, "M-0001", "READY")
    writer.transition_material_status(Actor.INGESTER, "M-0001", "PROCESSING")
    writer.transition_material_status(Actor.INGESTER, "M-0001", "READY")


def test_status_changes_only_through_the_state_machine(v2_db: Database) -> None:
    seed_catalog(v2_db.connection)
    writer = v2_db.writer()
    from syllva.state.v2.ownership import Owner

    with pytest.raises(PolicyViolation, match="transition_material_status"):
        writer.update(Owner.CATALOG, Actor.INGESTER, "materials", {"material_id": "M-0001"}, {"processing_status": "READY"})
    for actor in (Actor.UI_USER, Actor.CORE_AGENT_OUTPUT):
        with pytest.raises(PolicyViolation):
            writer.transition_material_status(actor, "M-0001", "PROCESSING")


def test_schema_refuses_partial_to_ready_even_with_raw_sql(v2_db: Database) -> None:
    conn = v2_db.connection
    seed_catalog(conn)
    conn.execute("UPDATE materials SET processing_status = 'PARTIAL' WHERE material_id = 'M-0001'")
    with pytest.raises(sqlite3.IntegrityError, match="PARTIAL_TO_READY"):
        conn.execute("UPDATE materials SET processing_status = 'READY' WHERE material_id = 'M-0001'")


def test_derivatives_cannot_hold_summaries_or_turn_partial_into_complete(v2_db: Database) -> None:
    conn = v2_db.connection
    ids = seed_catalog(conn)
    key = blob(conn, b"normalized")
    insert = (
        "INSERT INTO derivatives(source_version_id, kind, schema_version, processor_version, blob_sha256, completeness, created_at) "
        "VALUES (?, ?, 's1', 'p1', ?, ?, ?)"
    )
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute(insert, (ids["source_version"], "summary", key, "COMPLETE", NOW))
    conn.execute(insert, (ids["source_version"], "normalized", key, "PARTIAL", NOW))
    with pytest.raises(sqlite3.IntegrityError, match="PARTIAL_TO_COMPLETE"):
        conn.execute("UPDATE derivatives SET completeness = 'COMPLETE'")
