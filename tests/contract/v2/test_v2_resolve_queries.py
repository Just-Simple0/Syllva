"""C-14: natural-language resolution over the v2 catalog (spec §9.3, design §7.2).

The fixture ``tests/fixtures/resolve_queries.yaml`` holds the catalog and the
queries; every query must give the expected resolved entity, the exact
ambiguous candidate set, or ``none``. Ambiguity always goes through a
resolution handle; nothing is picked silently.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import NOW, blob

from syllva.ephemeral.memory import MemoryEphemeralStore
from syllva.retrieval.natural import NaturalResolver
from syllva.state.v2.database import Database

pytestmark = pytest.mark.contract

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "resolve_queries.yaml"
DATA: dict[str, Any] = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


def _seed(conn: sqlite3.Connection) -> None:
    course_ids: dict[str, int] = {}
    for c in DATA["catalog"]["courses"]:
        cid = conn.execute(
            "INSERT INTO courses(course_key, name, code, semester, created_at) VALUES (?, ?, ?, '2026-2', ?)",
            (c["key"], c["name"], c.get("code"), NOW),
        ).lastrowid
        course_ids[c["key"]] = int(cid or 0)
        for alias in c.get("aliases", []):
            conn.execute("INSERT INTO course_aliases(course_id, alias_nfc) VALUES (?, ?)", (cid, alias))
    session_ids: dict[str, int] = {}
    for s in DATA["catalog"]["sessions"]:
        sid = conn.execute(
            "INSERT INTO sessions(session_id, course_id, number, held_on, created_at) VALUES (?, ?, ?, ?, ?)",
            (s["id"], course_ids[s["course"]], s["number"], s["held_on"], NOW),
        ).lastrowid
        session_ids[s["id"]] = int(sid or 0)
    for i, m in enumerate(DATA["catalog"]["materials"]):
        key = blob(conn, f"material {i}".encode())
        fid = conn.execute(
            "INSERT INTO source_files(source_id, provider, display_name, origin, created_at) VALUES (?, 'local', ?, 'USER_DROP', ?)",
            (f"SRC-{i}", m["title"], NOW),
        ).lastrowid
        conn.execute(
            "INSERT INTO source_versions(source_file_id, source_version, source_hash, blob_sha256, created_at) VALUES (?, 1, ?, ?, ?)",
            (fid, "sha256:" + key, key, NOW),
        )
        conn.execute(
            "INSERT INTO materials(material_id, course_id, session_id, source_file_id, kind, role, created_at) VALUES (?, ?, ?, ?, ?, 'PRIMARY', ?)",
            (m["id"], course_ids[m["course"]], session_ids.get(m.get("session", "")), fid, m["kind"], NOW),
        )


@pytest.fixture
def resolver(v2_db: Database) -> NaturalResolver:
    _seed(v2_db.connection)
    starts = {k: date.fromisoformat(v) for k, v in DATA["semester_starts"].items()}
    return NaturalResolver(v2_db.reader(), MemoryEphemeralStore(), today=date(2026, 11, 2), semester_starts=starts)


@pytest.mark.parametrize("case", DATA["queries"], ids=[c["q"] for c in DATA["queries"]])
def test_resolve_query(resolver: NaturalResolver, case: dict[str, Any]) -> None:
    result = resolver.resolve(case["q"])
    assert result.status == case["expect"], (result.status, result.entity, [c.entity_id for c in result.candidates], result.reason)
    if case["expect"] == "resolved":
        assert result.entity is not None
        assert [result.entity.entity_type, result.entity.entity_id] == case["entity"]
    elif case["expect"] == "ambiguous":
        assert result.resolution_id and {c.entity_id for c in result.candidates} == set(case["candidates"])
        assert all(c.candidate_id.startswith("cand_") and c.reason for c in result.candidates)
    else:
        assert result.entity is None and not result.candidates
        if "reason" in case:
            assert (result.reason, result.course_key) == (case["reason"], case["course"])


def test_ambiguity_is_resolved_only_through_the_handle(resolver: NaturalResolver) -> None:
    result = resolver.resolve("알고리즘 2 2주차")
    assert result.status == "ambiguous" and result.resolution_id
    chosen = result.candidates[0]
    resolved = resolver.ephemeral.consume_resolution_choice(result.resolution_id, chosen.candidate_id)
    assert resolved.entity_id == chosen.entity_id
    reasons = {c.entity_id: c.reason for c in result.candidates}
    assert reasons["LMS67535-S02"] == "session number 2" and reasons["LMS67535-S01"] == "calendar week 2"


def test_course_hint_restricts_the_search(resolver: NaturalResolver) -> None:
    result = resolver.resolve("2강", course_hint="LMS67536")
    assert result.status == "resolved" and result.entity is not None and result.entity.entity_id == "LMS67536-S02"
    assert resolver.resolve("2강", course_hint="NOPE").reason == "UNKNOWN_COURSE_HINT"


def test_fixture_meets_the_spec_minimum() -> None:
    """Spec §9.3 lists the minimum query families the fixture must contain."""

    queries = [c["q"] for c in DATA["queries"]]
    for required in ("TCP", "Chapter 04", "09.17", "4주차"):
        assert required in queries
    assert any("주차" in q for q in queries)
    expects = {c["expect"] for c in DATA["queries"]}
    assert expects == {"resolved", "ambiguous", "none"}
