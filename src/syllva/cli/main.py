"""``syllva`` command line (v2).

The v1.2 commands (Notion/Drive worker, sync, MCP server launcher, settings
launcher) were removed with the legacy runtime in R1c (spec errata E19). Until
the desktop app (R2) exists, this CLI drives the v2 Core directly so the local
pipeline can be used and inspected:

    syllva course add NET "컴퓨터네트워크" --semester 2026-2 --alias 네트워크
    syllva intake add lecture.pdf --course NET
    syllva intake list
    syllva intake confirm INT-… --course NET --kind TRANSCRIPT --new-session 2026-09-08
    syllva intake run
    syllva context NET-M01 --query "handshake"

Output is JSON (one object per line) so it can be piped. The data directory
follows ``SYLLVA_DATA_DIR`` or the OS default (spec §3.1).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from syllva import __version__
from syllva.domain.errors import SyllvaError
from syllva.ephemeral.memory import MemoryEphemeralStore
from syllva.intake.v2 import LocalIntake
from syllva.library.blobs import BlobStore
from syllva.library.dblock import DatabaseLocked
from syllva.library.paths import layout
from syllva.retrieval.service import RetrievalService
from syllva.state.v2.database import Database, DatabaseCorrupt, open_database
from syllva.state.v2.ownership import Actor, Owner

CLI_SCOPE = "cli"  # caller scope for capabilities issued to this process


def _emit(value: Any) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _intake(db: Database) -> LocalIntake:
    return LocalIntake(db.writer(), BlobStore(db.layout.blobs_dir, db.layout.tmp_dir))


def _candidate(c: Any) -> dict[str, Any]:
    return {
        "candidate_id": c.candidate_id, "state": c.state, "name": c.display_name, "reason": c.reason_code,
        "material_id": c.material_id, "duplicate_of": c.duplicate_of,
        "blockers": (c.suggestion or {}).get("blockers"), "suggested_kind": (c.suggestion or {}).get("suggested_kind"),
    }


def _course_add(db: Database, args: argparse.Namespace) -> int:
    writer = db.writer()
    with writer.transaction():
        course_id = writer.insert(
            Owner.CATALOG, Actor.UI_USER, "courses",
            {"course_key": args.key, "name": args.name, "code": args.code or args.key, "semester": args.semester, "created_at": _now()},
        )
        for alias in args.alias or ():
            writer.insert(Owner.CATALOG, Actor.UI_USER, "course_aliases", {"course_id": course_id, "alias_nfc": alias})
    _emit({"course_key": args.key})
    return 0


def _intake_add(db: Database, args: argparse.Namespace) -> int:
    intake = _intake(db)
    path = Path(args.file)
    with path.open("rb") as handle:
        candidate = intake.receive(handle, display_name=path.name, mime_type=args.mime, course_hint=args.course)
    if candidate.state == "RECEIVED":
        candidate = intake.classify(candidate.candidate_id)
    _emit(_candidate(candidate))
    return 0


def _intake_list(db: Database, args: argparse.Namespace) -> int:
    intake = _intake(db)
    sql = "SELECT candidate_id FROM intake_candidates" + (" WHERE state = ?" if args.state else "") + " ORDER BY id"
    for row in db.reader().fetch_all(sql, (args.state,) if args.state else ()):
        _emit(_candidate(intake.get(str(row["candidate_id"]))))
    return 0


def _intake_confirm(db: Database, args: argparse.Namespace) -> int:
    candidate = _intake(db).confirm(
        args.candidate, course_key=args.course, kind=args.kind, session_id=args.session, new_session_held_on=args.new_session
    )
    _emit(_candidate(candidate))
    return 0


def _intake_simple(action: str):  # type: ignore[no-untyped-def]
    def run(db: Database, args: argparse.Namespace) -> int:
        _emit(_candidate(getattr(_intake(db), action)(args.candidate)))
        return 0

    return run


def _intake_run(db: Database, args: argparse.Namespace) -> int:
    intake = _intake(db)
    recovered = intake.recover()
    done = intake.run_pending()
    _emit({"recovered": recovered, "processed": [_candidate(c) for c in done]})
    return 0


def _context(db: Database, args: argparse.Namespace) -> int:
    service = RetrievalService(db.reader(), BlobStore(db.layout.blobs_dir, db.layout.tmp_dir), MemoryEphemeralStore())
    package = service.get_material_context(args.material, caller_scope=CLI_SCOPE, query=args.query)
    _emit({
        "material_id": args.material,
        "warnings": list(package.warnings),
        "evidence": [{"locator": str(i.locator), "part": i.part, "parts": i.parts, "text": i.content} for i in package.sources],
    })
    return 0


def _setup(args: argparse.Namespace) -> int:
    from syllva.settings.launcher import preflight, run_setup

    refusal = preflight()
    if refusal is not None:
        sys.stderr.write(refusal.message + "\n")
        _emit({"status": "failed", "code": refusal.code})
        return 1
    if not args.config.is_file():
        sys.stderr.write(json.dumps({"error": "CONFIGURATION_INVALID", "message": "config file not found"}) + "\n")
        return 2
    result = run_setup(args.config, open_browser=not args.no_browser)
    if result is not None:
        _emit(result)
    return 1 if isinstance(result, dict) and result.get("status") in {"failed", "unhealthy"} else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="syllva", description="Syllva v2 Core (local library and intake).")
    parser.add_argument("--data-dir", type=Path, help="data directory (default: SYLLVA_DATA_DIR or the OS location)")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="Local Settings config file (setup)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version", help="print the version")
    setup = sub.add_parser("setup", help="open the Local Settings web interface (the R2 UI server's starting point)")
    setup.add_argument("--no-browser", action="store_true", help="print the one-time local URL instead of opening a browser")

    course = sub.add_parser("course", help="courses").add_subparsers(dest="action", required=True)
    add = course.add_parser("add", help="add a course")
    add.add_argument("key")
    add.add_argument("name")
    add.add_argument("--semester", required=True)
    add.add_argument("--code", help="entity id prefix (default: the key)")
    add.add_argument("--alias", action="append")
    add.set_defaults(handler=_course_add)

    intake = sub.add_parser("intake", help="add files and review them").add_subparsers(dest="action", required=True)
    i_add = intake.add_parser("add", help="add a file (it is stored and classified)")
    i_add.add_argument("file")
    i_add.add_argument("--course", help="course this file belongs to")
    i_add.add_argument("--mime")
    i_add.set_defaults(handler=_intake_add)
    i_list = intake.add_parser("list", help="list candidates")
    i_list.add_argument("--state")
    i_list.set_defaults(handler=_intake_list)
    confirm = intake.add_parser("confirm", help="confirm a candidate waiting for review")
    confirm.add_argument("candidate")
    confirm.add_argument("--course", required=True)
    confirm.add_argument("--kind", required=True)
    group = confirm.add_mutually_exclusive_group()
    group.add_argument("--session", help="existing session id")
    group.add_argument("--new-session", metavar="YYYY-MM-DD", help="create a new session held on this date")
    confirm.set_defaults(handler=_intake_confirm)
    for name in ("dismiss", "retry"):
        p = intake.add_parser(name)
        p.add_argument("candidate")
        p.set_defaults(handler=_intake_simple(name))
    intake.add_parser("run", help="recover, classify and process what is pending").set_defaults(handler=_intake_run)

    context = sub.add_parser("context", help="show the evidence package of a material")
    context.add_argument("material")
    context.add_argument("--query")
    context.set_defaults(handler=_context)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        _emit({"version": __version__})
        return 0
    if args.command == "setup":
        return _setup(args)
    try:
        with open_database(layout(args.data_dir)) as db:
            return int(args.handler(db, args))
    except SyllvaError as exc:
        return _fail(getattr(exc, "code", "SYLLVA_ERROR"), str(exc))
    except (DatabaseLocked, DatabaseCorrupt) as exc:
        return _fail(type(exc).__name__.upper(), "the library is in use by another process or damaged")
    except sqlite3.IntegrityError as exc:
        return _fail("CONFLICT", str(exc).split(":")[0])
    except OSError as exc:
        return _fail("FILE_ERROR", exc.strerror or type(exc).__name__)


def _fail(code: str, message: str) -> int:
    sys.stderr.write(json.dumps({"error": code, "message": message}, ensure_ascii=False) + "\n")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
