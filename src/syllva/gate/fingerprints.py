"""Target fingerprints (spec §6.2) and the VERIFY_MATERIAL target lookup.

The four formats are fixed now (errata E24); only VERIFY_MATERIAL has a lookup in
R2. Lookups take a ``query_one`` callable so the same code runs at begin and,
inside the commit transaction, at confirm (recompute-at-commit, §6.4-4).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

QueryOne = Callable[[str, Sequence[Any]], Any]


def verify_material(source_hash: str, source_version: int, completeness: str) -> str:
    return f"{source_hash}|{source_version}|{completeness}"


def exam_scope(materials: Sequence[tuple[str, str]]) -> str:
    joined = "\n".join(f"{material_id}:{source_hash}" for material_id, source_hash in sorted(materials))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def submission(activity_id: str, current_status: str, updated_at: str) -> str:
    return f"{activity_id}|{current_status}|{updated_at}"


def adoption(artifact_id: str, body_blob_sha256: str) -> str:
    return f"{artifact_id}|{body_blob_sha256}"


@dataclass(frozen=True)
class MaterialTarget:
    row_id: int
    title: str
    course: str
    source_id: str
    source_hash: str
    source_version: int
    completeness: str
    verified: bool

    @property
    def fingerprint(self) -> str:
        return verify_material(self.source_hash, self.source_version, self.completeness)


def material_target(query_one: QueryOne, material_id: str) -> MaterialTarget | None:
    row = query_one(
        "SELECT m.id, f.display_name, c.name AS course, f.source_id, v.id AS version_row, v.source_hash, v.source_version, "
        "COALESCE(u.verified, 0) AS verified FROM materials m "
        "JOIN courses c ON c.id = m.course_id JOIN source_files f ON f.id = m.source_file_id "
        "JOIN source_versions v ON v.source_file_id = f.id LEFT JOIN material_usage u ON u.material_id = m.id "
        "WHERE m.material_id = ? AND f.deleted_at IS NULL ORDER BY v.source_version DESC LIMIT 1",
        (material_id,),
    )
    if row is None:
        return None
    derivative = query_one(
        "SELECT completeness FROM derivatives WHERE source_version_id = ? AND kind = 'chunks' ORDER BY id DESC LIMIT 1",
        (int(row["version_row"]),),
    )
    return MaterialTarget(
        int(row["id"]), str(row["display_name"]), str(row["course"]), str(row["source_id"]), str(row["source_hash"]),
        int(row["source_version"]), str(derivative["completeness"]) if derivative else "NONE", bool(row["verified"]),
    )
