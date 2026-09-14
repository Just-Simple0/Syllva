from __future__ import annotations

import argparse
import builtins
import datetime as dt
import getpass
import importlib.util
import io
import json
import subprocess
import sys
import warnings
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

MODULE_PATH = Path(__file__).parents[2] / "scripts" / "knu_lms_sync.py"
SPEC = importlib.util.spec_from_file_location("knu_lms_sync", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
sync = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sync
SPEC.loader.exec_module(sync)

CONFIG = sync.SyncConfig(
    course_id=12345,
    expected_name="알고리즘 (002)",
    expected_code="CS-002",
    expected_term="2026-2",
    module_positions=sync.MODULE_POSITIONS,
)


def module_record(position: int, *, items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    actual_items = [] if items is None else items
    return {
        "id": 1000 + position,
        "name": f"{position}주차",
        "position": position,
        "items_available": True,
        "items_unavailable": False,
        "items_count_available": True,
        "items_returned_count": len(actual_items),
        "items_complete": True,
        "state": "completed",
        "items": actual_items,
    }


def raw_snapshot(*, assignment_name: str = "과제 <1>") -> dict[str, Any]:
    return {
        "status": "complete",
        "course": {
            "id": 12345,
            "name": "알고리즘 (002)",
            "course_code": "CS-002",
            "term": "2026-2",
        },
        "assignments": [
            {"id": 11, "name": assignment_name, "due_at": None},
            {"id": 12, "name": "과제 2", "due_at": "2026-09-20T23:59:00+09:00"},
        ],
        "announcements": [
            {
                "id": 21,
                "title": "공지 [1]",
                "context_code": "course_12345",
                "posted_at": "2026-09-12T15:00:00+00:00",
            }
        ],
        "modules": [
            module_record(1, items=[{"id": 2001, "title": "자료 *1*", "content_id": 7001}]),
            *[module_record(position) for position in range(2, 16)],
        ],
    }


def snapshot() -> sync.CanonicalSnapshot:
    return sync.validate_snapshot(raw_snapshot(), CONFIG)


def test_validates_sanitized_probe_term_and_stable_routes() -> None:
    result = snapshot()
    assert result.payload["course"]["term"] == "2026-2"
    assert result.payload["assignments"][0]["source_key"] == (
        "https://canvas.knu.ac.kr/courses/12345/assignments/11"
    )
    assert result.payload["announcements"][0]["lms_url"] == (
        "https://canvas.knu.ac.kr/courses/12345/discussion_topics/21"
    )
    assert result.payload["modules"][0]["items"][0]["lms_url"] == (
        "https://canvas.knu.ac.kr/courses/12345/modules/items/2001"
    )


def test_rejects_raw_term_object_and_invalid_module_positions() -> None:
    raw = raw_snapshot()
    raw["course"]["term"] = {"name": "2026-2"}
    with pytest.raises(sync.SyncError, match="invalid_course_term"):
        sync.validate_snapshot(raw, CONFIG)
    raw = raw_snapshot()
    raw["modules"][0]["position"] = True
    with pytest.raises(sync.SyncError, match="invalid_module_position"):
        sync.validate_snapshot(raw, CONFIG)


def test_actual_empty_modules_render_without_module_fabrication() -> None:
    rendered = sync.render_markdown(snapshot())
    assert rendered.count("<details>\n<summary>") == 15
    assert rendered.count("\t- 등록된 자료 없음 · 수집 시점 기준") == 14
    assert "state" not in rendered
    assert "<details>\n<summary>1주차</summary>\n\t- [자료 \\*1\\*]" in rendered


def test_all_lms_labels_are_escaped_and_source_region_excludes_user_sections() -> None:
    result = snapshot()
    source = sync.render_source_region(result)
    assert "공지 \\[1\\]" in source
    assert "내 메모" not in source
    assert "학습 세션" not in source
    assert "과제" not in source
    assert "<details>\n<summary>" in source
    assert "\n\t-" in source


def test_projection_has_one_datasource_and_three_views_with_exact_prefix() -> None:
    plan = sync.build_projection(
        snapshot(),
        notion_readback={
            "course_parent_verified": True,
            "private_root_verified": True,
            "rows": [],
            "course_pages": [],
        },
    )
    assert plan["datasource_count"] == 1
    assert len(plan["datasource"]["views"]) == 3
    course_view = plan["datasource"]["views"][2]
    assert course_view["filter"] == (
        "LMS 키 STARTS WITH https://canvas.knu.ac.kr/courses/12345/assignments/"
    )
    assert all(row["operation"] == "create" for row in plan["rows"])


def test_row_noop_update_and_user_source_conflict() -> None:
    current = snapshot()
    initial = sync.build_projection(
        current,
        notion_readback={
            "datasource": {"id": "ds-1"},
            "rows": [],
            "course_parent_verified": True,
            "private_root_verified": True,
            "course_pages": [],
        },
    )
    row_plan = initial["rows"][0]
    source_values = row_plan["source_fields"]
    base_row = {
        "id": "row-1",
        "parent_datasource_id": "ds-1",
        "source_values": source_values,
        "last_applied_source_values": source_values,
        "last_applied_source_hash": row_plan["source_hash"],
        "properties": {"내 상태": "완료", "내 메모": "USER"},
        "LMS 키": row_plan["source_key"],
    }
    readback = {
        "datasource": {"id": "ds-1"},
        "rows": [base_row],
        "course_pages": [],
        "course_parent_verified": True,
        "private_root_verified": True,
    }
    assert sync.build_projection(current, notion_readback=readback)["rows"][0]["operation"] == "noop"
    changed_raw = raw_snapshot(assignment_name="과제 바뀜")
    changed = sync.validate_snapshot(changed_raw, CONFIG)
    changed_plan = sync.build_projection(changed, notion_readback=readback)
    assert changed_plan["rows"][0]["operation"] == "update_source_preserve_user"
    edited = dict(base_row)
    edited["source_values"] = {**source_values, "이름": "USER 변경"}
    conflict_plan = sync.build_projection(
        current,
        notion_readback={**readback, "rows": [edited]},
    )
    assert conflict_plan["rows"][0]["operation"] == "conflict"
    assert conflict_plan["rows"][0]["reason"] == "source_user_edit_conflict"


def test_notion_rich_text_key_and_omitted_null_date_are_normalized_exactly() -> None:
    current = snapshot()
    initial = sync.build_projection(
        current,
        notion_readback={
            "datasource": {"id": "ds-1"},
            "rows": [],
            "course_parent_verified": True,
            "private_root_verified": True,
            "course_pages": [],
        },
    )
    row_plan = initial["rows"][0]
    key = row_plan["source_key"]
    source_values = dict(row_plan["source_fields"])
    source_values.pop("날짜")
    row = {
        "id": "row-1",
        "parent_datasource_id": "ds-1",
        "properties": {
            **source_values,
            "LMS 키": f"[{key}]({key})",
            "내 상태": "확인 전",
            "내 메모": "USER",
        },
        "last_applied_source_values": row_plan["source_fields"],
        "last_applied_source_hash": row_plan["source_hash"],
    }
    plan = sync.build_projection(
        current,
        notion_readback={
            "datasource": {"id": "ds-1"},
            "rows": [row],
            "course_parent_verified": True,
            "private_root_verified": True,
            "course_pages": [],
        },
    )
    assert plan["rows"][0]["operation"] == "noop"


def test_mismatched_notion_rich_text_wrapper_is_a_conflict() -> None:
    current = snapshot()
    key = current.payload["assignments"][0]["source_key"]
    row = {
        "id": "row-1",
        "이름": current.payload["assignments"][0]["name"],
        "properties": {"LMS 키": f"[{key}]({key}/wrong)"},
    }
    plan = sync.build_projection(
        current,
        notion_readback={
            "rows": [row],
            "course_pages": [],
            "course_parent_verified": True,
            "private_root_verified": True,
        },
    )
    assert plan["rows"][0]["operation"] == "conflict"
    assert plan["rows"][0]["reason"] == "source_key_readback_mismatch"


def test_verified_course_title_drives_subject_and_missing_tombstone_never_recreates() -> None:
    current = snapshot()
    missing_key = current.payload["assignments"][0]["source_key"]
    plan = sync.build_projection(
        current,
        notion_readback={
            "verified_course_title": "알고리즘2 (001)",
            "missing_bindings": [missing_key],
            "rows": [],
            "course_pages": [],
            "course_parent_verified": True,
            "private_root_verified": True,
        },
    )
    assert plan["rows"][0]["operation"] == "conflict"
    assert plan["rows"][0]["reason"] == "known_binding_missing"
    assert plan["rows"][0]["source_fields"]["과목"] == "알고리즘2 (001)"
    assert plan["course_page"]["title"] == "알고리즘2 (001)"


def test_known_missing_or_title_only_binding_conflicts() -> None:
    current = snapshot()
    readback = {
        "rows": [{"id": "row-1", "이름": "과제 <1>", "source_identity": current.payload["assignments"][0]["source_identity"]}],
        "course_pages": [],
        "course_parent_verified": True,
        "private_root_verified": True,
    }
    plan = sync.build_projection(current, notion_readback=readback)
    assert plan["rows"][0]["operation"] == "conflict"
    assert plan["rows"][0]["reason"] == "title_only_candidate"
    readback["rows"][0].pop("이름")
    plan = sync.build_projection(current, notion_readback=readback)
    assert plan["rows"][0]["reason"] == "missing_source_binding"


def test_same_title_row_bound_to_other_course_is_not_a_candidate() -> None:
    current = snapshot()
    assignment = current.payload["assignments"][0]
    other_course_key = "https://canvas.knu.ac.kr/courses/54321/assignments/11"
    other_bound = {
        "id": "row-other",
        "이름": assignment["name"],
        "properties": {"LMS 키": f"[{other_course_key}]({other_course_key})"},
    }
    readback = {
        "rows": [other_bound],
        "course_pages": [],
        "course_parent_verified": True,
        "private_root_verified": True,
    }
    plan = sync.build_projection(current, notion_readback=readback)
    assert plan["rows"][0]["operation"] == "create"
    unbound = {"id": "row-unbound", "이름": assignment["name"], "properties": {}}
    conflict = sync.build_projection(
        current,
        notion_readback={**readback, "rows": [unbound]},
    )
    assert conflict["rows"][0]["operation"] == "conflict"
    assert conflict["rows"][0]["reason"] == "title_only_candidate"


def test_existing_course_requires_binding_parent_privacy_comments_and_prior_hash() -> None:
    current = snapshot()
    page = {"id": "page-1", "source_key": current.payload["course"]["source_key"], "title": "알고리즘 (002)"}
    readback = {"course_pages": [page], "course_parent_id": "root-1", "rows": []}
    plan = sync.build_projection(current, notion_readback=readback)
    assert plan["course_page"]["reason"] == "course_parent_mismatch"
    page.update({"parent_id": "root-1", "privacy": "private", "comments": []})
    plan = sync.build_projection(current, notion_readback=readback)
    assert plan["course_page"]["reason"] == "prior_source_hash_missing"
    title_only = sync.build_projection(
        current,
        notion_readback={
            "course_pages": [{"id": "other", "title": "알고리즘 (002)"}],
            "course_parent_verified": True,
            "private_root_verified": True,
            "rows": [],
        },
    )
    assert title_only["course_page"]["reason"] == "title_only_candidate"


def test_course_source_dual_hash_handles_connector_normalization_and_user_edit() -> None:
    current = snapshot()
    initial = sync.build_projection(
        current,
        notion_readback={
            "rows": [],
            "course_pages": [],
            "course_parent_verified": True,
            "private_root_verified": True,
        },
    )
    source_region = initial["course_page_source_region"]
    desired_hash = sha256(
        sync._normalize_notion_readback(source_region).encode("utf-8")
    ).hexdigest()
    actual_readback = source_region.replace("\\", "").replace("\n\n", "\n")
    page = {
        "id": "page-1",
        "source_key": current.payload["course"]["source_key"],
        "title": "알고리즘2 (001)",
        "parent_id": "root-1",
        "privacy": "private",
        "comments": [],
        "source_region": actual_readback,
        "last_applied_source_hash": sha256(
            sync._normalize_notion_readback(actual_readback).encode("utf-8")
        ).hexdigest(),
        "last_applied_desired_region_hash": desired_hash,
    }
    readback = {
        "rows": [],
        "course_pages": [page],
        "course_parent_id": "root-1",
        "course_parent_verified": True,
        "private_root_verified": True,
    }
    assert sync.build_projection(current, notion_readback=readback)["course_page"]["operation"] == "noop"
    edited = {**page, "source_region": actual_readback + "\nUSER edit"}
    conflict = sync.build_projection(
        current,
        notion_readback={**readback, "course_pages": [edited]},
    )
    assert conflict["course_page"]["operation"] == "conflict"
    assert conflict["course_page"]["reason"] == "source_region_changed"


def test_retained_announcements_feed_source_region() -> None:
    current = snapshot()
    old = {
        "source_key": "https://canvas.knu.ac.kr/courses/12345/discussion_topics/99",
        "record": {
            "id": 99,
            "title": "이전 공지",
            "context_code": "course_12345",
            "source_identity": {"origin": sync.ORIGIN, "course_id": 12345, "resource_kind": "announcement", "resource_id": 99},
            "source_key": "https://canvas.knu.ac.kr/courses/12345/discussion_topics/99",
            "lms_url": "https://canvas.knu.ac.kr/courses/12345/discussion_topics/99",
        },
        "last_seen": "2026-08-31",
    }
    plan = sync.build_projection(
        current,
        prior={"announcements": [old]},
        notion_readback={"rows": [], "course_pages": [], "course_parent_verified": True, "private_root_verified": True},
        observed_on="2026-09-13",
    )
    assert any(item["source_key"].endswith("/99") and not item["seen_in_window"] for item in plan["announcements"])
    assert "이전 공지" in plan["course_page_template"]
    assert "현재까지 수집한 공지입니다. 게시판 전체 기록 수집은 연결 전입니다." in plan[
        "course_page_source_region"
    ]


def test_semester_projection_requires_each_academic_prior_and_carries_observed_on() -> None:
    registry, semester = _two_course_semester()
    common = {"rows": [], "course_pages": [], "course_parent_verified": True, "private_root_verified": True}
    with pytest.raises(sync.SyncError, match="semester_prior_missing"):
        sync.build_semester_projection(
            registry, semester, notion_readback=common, prior={"courses": {"12345": {}}}
        )
    plan = sync.build_semester_projection(
        registry,
        semester,
        notion_readback=common,
        prior={"courses": {"12345": {"announcements": []}, "54321": {"announcements": []}}},
        observed_on="2026-09-13",
    )
    assert plan["observed_on"] == "2026-09-13"
    assert plan["owner_id"] is None
    assert all(item["last_seen"] == "2026-09-13" for item in plan["courses"]["12345"]["announcements"])
    assert "course_page:https://canvas.knu.ac.kr/courses/12345" in plan["recovery_components"]


def test_notification_decision_suppresses_only_same_durable_signature() -> None:
    changed = {"status": "complete", "snapshot_hash": "a" * 64, "rows": [{"operation": "create"}]}
    first = sync.decide_notification(changed)
    assert first["notify"] is True
    second = sync.decide_notification(changed, first["state"])
    assert second["notify"] is False
    assert second["reason"] == "unchanged"
    changed_again = {"status": "complete", "snapshot_hash": "a" * 64, "rows": [{"operation": "update"}]}
    assert sync.decide_notification(changed_again, first["state"])["notify"] is True
    failed = {"status": "conflict", "conflicts": [{"reason": "source_user_edit_conflict"}]}
    failure = sync.decide_notification(failed)
    assert failure["kind"] == "failure"
    assert sync.decide_notification(failed, failure["state"])["notify"] is False
    assert sync.decide_notification({"status": "complete", "rows": [{"operation": "noop"}]})["reason"] == "no_action"
    with pytest.raises(sync.SyncError, match="notification_state_invalid"):
        sync.decide_notification(changed, {"version": 1, "last_signature": "raw", "last_kind": "change"})


def test_course_page_bootstrap_is_additive_and_publishes_hashes_after_readback() -> None:
    current = snapshot()
    old_body = "# 알고리즘2 (001)\n\nPDF 링크: 보존\n\n내 메모: USER\n\n## 학습 세션\n기존 세션"
    common = {
        "course_parent_id": "root-1",
        "course_parent_verified": True,
        "private_root_verified": True,
        "rows": [],
        "course_pages": [{
            "id": "page-1",
            "source_key": current.payload["course"]["source_key"],
            "parent_id": "root-1",
            "privacy": "private",
            "full_body_verified": True,
            "comments": [],
            "learning_sessions": [],
            "body": old_body,
        }],
    }
    source = sync.render_source_region(current)
    plan = sync.build_course_page_bootstrap_plan(current, common, source, original_body=old_body)
    assert plan["operation"] == "bootstrap_append_source_region"
    assert "last_applied_source_hash" not in plan
    returned = dict(common)
    returned["course_pages"] = [{**common["course_pages"][0], "body": old_body.replace("## 학습 세션", source + "\n## 학습 세션")}]
    verified = sync.verify_course_page_bootstrap(plan, returned, original_body=old_body)
    assert verified["operation"] == "bootstrap_verified"
    assert len(verified["last_applied_source_hash"]) == 64
    managed_page = {
        **returned["course_pages"][0],
        "source_region": source,
        "last_applied_source_hash": verified["last_applied_source_hash"],
        "last_applied_desired_region_hash": verified["last_applied_desired_region_hash"],
    }
    normal = sync.build_projection(
        current,
        notion_readback={**returned, "course_pages": [managed_page]},
    )
    assert normal["course_page"]["operation"] == "noop"
    with pytest.raises(sync.SyncError, match="bootstrap_comments_present"):
        sync.build_course_page_bootstrap_plan(
            current,
            {**common, "course_pages": [{**common["course_pages"][0], "comments": [{"id": "c"}]}]},
            source,
            original_body=old_body,
        )


def test_canonical_loader_revalidates_shape(tmp_path: Path) -> None:
    canonical = snapshot().as_dict()
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(canonical, ensure_ascii=False), encoding="utf-8")
    assert sync._load_canonical(str(path)).snapshot_hash == canonical["snapshot_hash"]
    bad_payload = dict(canonical["snapshot"])
    bad_modules = list(bad_payload["modules"])
    bad_modules[0] = {**bad_modules[0], "name": "wrong"}
    bad_payload["modules"] = bad_modules
    bad = {"status": "complete", "snapshot": bad_payload, "snapshot_hash": sha256(sync._canonical_bytes(bad_payload)).hexdigest()}
    path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(sync.SyncError, match="invalid_module_name"):
        sync._load_canonical(str(path))


def _auth_document(*, issued: dt.datetime, expires: dt.datetime) -> dict[str, Any]:
    return {
        "version": 1,
        "course": {
            "id": 12345,
            "name": "알고리즘 (002)",
            "code": "CS-002",
            "term": "2026-2",
        },
        "origin": sync.ORIGIN,
        "service": sync.KEYCHAIN_SERVICE,
        "account": "canvas.knu.ac.kr/course/12345",
        "backend": sync._expected_backend_module(),
        "resources": ["course", "assignments", "announcements", "modules"],
        "issued_at": issued.isoformat(),
        "expires_at": expires.isoformat(),
    }


def _install_auth_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, document: dict[str, Any]) -> str:
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    config_path = runtime / "config.json"
    manifest_path = runtime / "auth-manifest.json"
    config_path.write_text(json.dumps(document), encoding="utf-8")
    config_path.chmod(0o600)
    monkeypatch.setattr(sync, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(sync, "CONFIG_PATH", config_path)
    monkeypatch.setattr(sync, "AUTH_MANIFEST_PATH", manifest_path)
    return sync.config_scope_hash(document)


def test_failed_atomic_state_write_keeps_private_temporary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(sync, "RUNTIME_DIR", runtime)
    target = runtime / "auth-manifest.json"

    def fail_replace(_temporary: Path, _target: Path) -> None:
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(sync.os, "replace", fail_replace)
    with pytest.raises(sync.SyncError, match="state_write_failed"):
        sync._atomic_json_write(target, {"state": "pending"}, 0o600)
    temporary_files = list(runtime.glob(".auth-manifest.json.*"))
    assert len(temporary_files) == 1
    assert temporary_files[0].stat().st_mode & 0o777 == 0o600


def test_auth_manifest_preflight_blocks_pending_and_expired_before_keychain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    issued = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    now = dt.datetime(2026, 9, 13, tzinfo=dt.UTC)
    document = _auth_document(issued=issued, expires=dt.datetime(2026, 9, 30, tzinfo=dt.UTC))
    scope_hash = _install_auth_files(monkeypatch, tmp_path, document)
    pending = sync._fixed_auth_document(document, "pending", scope_hash)
    sync.AUTH_MANIFEST_PATH.write_text(json.dumps(pending), encoding="utf-8")
    sync.AUTH_MANIFEST_PATH.chmod(0o600)
    called = False

    def unexpected_keychain() -> Any:
        nonlocal called
        called = True
        raise AssertionError("Keychain must not be reached")

    monkeypatch.setattr(sync, "_explicit_os_keyring", unexpected_keychain)
    with pytest.raises(sync.SyncError, match="auth_manifest_not_enrolled"):
        sync.read_enrolled_token(now=now)
    assert called is False
    enrolled = sync._fixed_auth_document(document, "enrolled", scope_hash)
    sync.AUTH_MANIFEST_PATH.write_text(json.dumps(enrolled), encoding="utf-8")
    sync.AUTH_MANIFEST_PATH.chmod(0o600)
    with pytest.raises(sync.SyncError, match="auth_manifest_expired"):
        sync.read_enrolled_token(now=dt.datetime(2026, 10, 1, tzinfo=dt.UTC))
    assert called is False


def test_future_issued_auth_blocks_getter_prompt_setter_and_probe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    issued = dt.datetime(2026, 9, 20, tzinfo=dt.UTC)
    document = _auth_document(issued=issued, expires=dt.datetime(2026, 10, 10, tzinfo=dt.UTC))
    scope_hash = _install_auth_files(monkeypatch, tmp_path, document)
    now = dt.datetime(2026, 9, 13, tzinfo=dt.UTC)
    calls = {"getter": 0, "prompt": 0, "setter": 0, "probe": 0, "reservation": 0}

    class Backend:
        keychain: Any = None

        def get_password(self, _service: str, _account: str) -> None:
            calls["getter"] += 1

        def set_password(self, _service: str, _account: str, _token: str) -> None:
            calls["setter"] += 1

    monkeypatch.setattr(sync, "_explicit_os_keyring", lambda: Backend())
    with pytest.raises(sync.SyncError, match="auth_not_yet_valid"):
        sync.read_enrolled_token(now=now)
    with pytest.raises(sync.SyncError, match="auth_not_yet_valid"):
        sync.enroll_keychain(
            prompt=lambda _label: calls.__setitem__("prompt", calls["prompt"] + 1) or "token",
            stdin=type("TTY", (), {"isatty": lambda self: True})(),
            now=now,
            backend=Backend(),
        )
    monkeypatch.setattr(sync, "_ensure_participant", lambda _owner, _scope: None)
    monkeypatch.setattr(sync, "_probe_module", lambda: calls.__setitem__("probe", calls["probe"] + 1))
    monkeypatch.setattr(sync, "_load_auth_config", lambda: (CONFIG, document, scope_hash))
    with pytest.raises(sync.SyncError, match="auth_not_yet_valid"):
        sync.collect(owner_id="owner", scope_hash=scope_hash, now=now)
    assert calls == {"getter": 0, "prompt": 0, "setter": 0, "probe": 0, "reservation": 0}


def test_collect_rejects_config_scope_drift_before_keychain_or_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    issued = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    document_a = _auth_document(issued=issued, expires=dt.datetime(2026, 9, 30, tzinfo=dt.UTC))
    document_b = {**document_a, "course": {**document_a["course"], "id": 54321}, "account": "canvas.knu.ac.kr/course/54321"}
    config_b = sync.SyncConfig(
        course_id=54321,
        expected_name="알고리즘 (002)",
        expected_code="CS-002",
        expected_term="2026-2",
    )
    scope_a = sync.config_scope_hash(document_a)
    scope_b = sync.config_scope_hash(document_b)
    loads = iter([(CONFIG, document_a, scope_a), (config_b, document_b, scope_b)])
    calls = {"keychain": 0, "probe": 0}
    monkeypatch.setattr(sync, "_load_auth_config", lambda: next(loads))
    monkeypatch.setattr(sync, "_ensure_participant", lambda _owner, _scope: None)
    monkeypatch.setattr(sync, "_explicit_os_keyring", lambda: calls.__setitem__("keychain", 1))
    monkeypatch.setattr(sync, "_probe_module", lambda: calls.__setitem__("probe", 1))
    with pytest.raises(sync.SyncError, match="scope_hash_mismatch"):
        sync.collect(
            owner_id="owner",
            scope_hash=scope_a,
            now=dt.datetime(2026, 9, 13, tzinfo=dt.UTC),
        )
    assert calls == {"keychain": 0, "probe": 0}


def test_write_json_serializes_before_touching_stdout() -> None:
    output = io.StringIO()
    with pytest.raises(sync.SyncError, match="output_serialization"):
        sync._write_json(output, {"bad": float("nan")})
    assert output.getvalue() == ""


def test_enrollment_publishes_pending_before_fake_keychain_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    issued = dt.datetime(2026, 9, 13, tzinfo=dt.UTC)
    now = dt.datetime(2026, 9, 13, 1, tzinfo=dt.UTC)
    document = _auth_document(issued=issued, expires=dt.datetime(2026, 10, 1, tzinfo=dt.UTC))
    _install_auth_files(monkeypatch, tmp_path, document)

    class FakeReservation:
        completed = False
        closed = False

        def complete(self) -> None:
            self.completed = True

        def close(self) -> None:
            self.closed = True

    reservation = FakeReservation()
    monkeypatch.setattr(sync, "_begin_reservation", lambda value: reservation)
    writes: list[str] = []
    original_write = sync._atomic_json_write

    def record_write(path: Path, value: dict[str, Any], mode: int) -> None:
        writes.append(value["state"])
        original_write(path, value, mode)

    monkeypatch.setattr(sync, "_atomic_json_write", record_write)

    class FakeBackend:
        def __init__(self) -> None:
            self.keychain: Any = object()
            self.calls: list[tuple[str, str, str]] = []

        def set_password(self, service: str, account: str, token: str) -> None:
            assert self.keychain is None
            self.calls.append((service, account, token))

    fake_backend = FakeBackend()
    result = sync.enroll_keychain(
        prompt=lambda _label: "test-token",
        stdin=type("TTY", (), {"isatty": lambda self: True})(),
        now=now,
        backend=fake_backend,
    )
    assert result["status"] == "enrolled"
    assert writes == ["pending", "enrolled"]
    assert fake_backend.calls == [(sync.KEYCHAIN_SERVICE, "canvas.knu.ac.kr/course/12345", "test-token")]
    assert reservation.completed is True
    assert reservation.closed is True


def test_enrollment_getpass_warning_is_fixed_noecho_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    issued = dt.datetime(2026, 9, 13, tzinfo=dt.UTC)
    document = _auth_document(issued=issued, expires=dt.datetime(2026, 10, 1, tzinfo=dt.UTC))
    _install_auth_files(monkeypatch, tmp_path, document)
    reservation = type(
        "Reservation",
        (),
        {"complete": lambda self: None, "close": lambda self: None},
    )()
    monkeypatch.setattr(sync, "_begin_reservation", lambda _scope: reservation)

    def warning_prompt(_label: str) -> str:
        warnings.warn("no controlling terminal", getpass.GetPassWarning)
        return "never-used"

    with pytest.raises(sync.SyncError, match="credential_noecho_unavailable"):
        sync.enroll_keychain(
            prompt=warning_prompt,
            stdin=type("TTY", (), {"isatty": lambda self: True})(),
            now=dt.datetime(2026, 9, 13, 1, tzinfo=dt.UTC),
            backend=object(),
        )


def test_enrollment_manifest_failure_leaves_pending_and_does_not_complete(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    issued = dt.datetime(2026, 9, 13, tzinfo=dt.UTC)
    document = _auth_document(issued=issued, expires=dt.datetime(2026, 10, 1, tzinfo=dt.UTC))
    _install_auth_files(monkeypatch, tmp_path, document)

    class FakeReservation:
        def __init__(self) -> None:
            self.completed = False

        def complete(self) -> None:
            self.completed = True

        def close(self) -> None:
            pass

    reservation = FakeReservation()
    monkeypatch.setattr(sync, "_begin_reservation", lambda _scope: reservation)
    write_states: list[str] = []
    original_write = sync._atomic_json_write

    def fail_final_manifest(path: Path, value: dict[str, Any], mode: int) -> None:
        write_states.append(value["state"])
        if value["state"] == "enrolled":
            raise sync.SyncError("state_write_failed")
        original_write(path, value, mode)

    monkeypatch.setattr(sync, "_atomic_json_write", fail_final_manifest)

    class FakeBackend:
        keychain: Any = object()

        def set_password(self, _service: str, _account: str, _token: str) -> None:
            assert self.keychain is None

    with pytest.raises(sync.SyncError, match="enrollment_incomplete"):
        sync.enroll_keychain(
            prompt=lambda _label: "test-token",
            stdin=type("TTY", (), {"isatty": lambda self: True})(),
            now=dt.datetime(2026, 9, 13, 1, tzinfo=dt.UTC),
            backend=FakeBackend(),
        )
    assert write_states == ["pending", "enrolled"]
    assert reservation.completed is False


def test_explicit_backend_clears_keychain_path_without_global_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeKeyring:
        __module__ = "keyring.backends.macOS"

        def __init__(self) -> None:
            self.keychain = "sentinel-from-environment"

    original_import = builtins.__import__

    def import_only_mac_backend(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "keyring.backends.macOS":
            return type("MacOSModule", (), {"Keyring": FakeKeyring})
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(sync.sys, "platform", "darwin")
    monkeypatch.setenv("KEYCHAIN_PATH", "sentinel-no-dump")
    monkeypatch.setattr(builtins, "__import__", import_only_mac_backend)
    backend = sync._explicit_os_keyring()
    assert backend.keychain is None


def test_explicit_backend_selects_windows_vault_on_win32(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeWinVaultKeyring:
        __module__ = "keyring.backends.Windows"

    original_import = builtins.__import__

    def import_only_windows_backend(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "keyring.backends.Windows":
            return type("WindowsModule", (), {"WinVaultKeyring": FakeWinVaultKeyring})
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(sync.sys, "platform", "win32")
    monkeypatch.setattr(builtins, "__import__", import_only_windows_backend)
    backend = sync._explicit_os_keyring()
    assert isinstance(backend, FakeWinVaultKeyring)
    assert not hasattr(backend, "keychain")


def test_explicit_backend_rejects_monkeypatched_windows_module_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    class SpoofedKeyring:
        __module__ = "attacker.module"

    original_import = builtins.__import__

    def import_spoofed_backend(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "keyring.backends.Windows":
            return type("WindowsModule", (), {"WinVaultKeyring": SpoofedKeyring})
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(sync.sys, "platform", "win32")
    monkeypatch.setattr(builtins, "__import__", import_spoofed_backend)
    with pytest.raises(sync.SyncError, match="keychain_backend_invalid"):
        sync._explicit_os_keyring()


def test_explicit_backend_rejects_unsupported_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync.sys, "platform", "linux")
    with pytest.raises(sync.SyncError, match="keychain_platform_unsupported"):
        sync._explicit_os_keyring()
    with pytest.raises(sync.SyncError, match="keychain_platform_unsupported"):
        sync._expected_backend_module()


def test_expected_backend_module_matches_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync.sys, "platform", "darwin")
    assert sync._expected_backend_module() == "keyring.backends.macOS.Keyring"
    monkeypatch.setattr(sync.sys, "platform", "win32")
    assert sync._expected_backend_module() == "keyring.backends.Windows.WinVaultKeyring"


def test_read_enrolled_token_skips_keychain_property_reset_on_windows_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sync.sys, "platform", "win32")
    issued = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    now = dt.datetime(2026, 9, 13, tzinfo=dt.UTC)
    document = _auth_document(issued=issued, expires=dt.datetime(2026, 9, 30, tzinfo=dt.UTC))
    scope_hash = _install_auth_files(monkeypatch, tmp_path, document)
    enrolled = sync._fixed_auth_document(document, "enrolled", scope_hash)
    sync.AUTH_MANIFEST_PATH.write_text(json.dumps(enrolled), encoding="utf-8")
    sync.AUTH_MANIFEST_PATH.chmod(0o600)

    class WindowsBackend:
        def get_password(self, _service: str, _account: str) -> str:
            return "windows-token"

    monkeypatch.setattr(sync, "_explicit_os_keyring", lambda: WindowsBackend())
    token, config, returned_scope_hash = sync.read_enrolled_token(now=now)
    assert token == "windows-token"
    assert config.course_id == 12345
    assert returned_scope_hash == scope_hash


def test_collect_uses_kst_current_day_and_reviewed_probe_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    scope_hash = "a" * 64
    calls: list[argparse.Namespace] = []

    class FakeProbe:
        class NoRedirectHandler:
            pass

        @staticmethod
        def build_opener(_handler: Any) -> object:
            return object()

        @staticmethod
        def run_probe(args: argparse.Namespace, _token: str, **_kwargs: Any) -> dict[str, Any]:
            calls.append(args)
            return raw_snapshot()

    monkeypatch.setattr(sync, "_load_auth_config", lambda: (CONFIG, {}, scope_hash))
    monkeypatch.setattr(sync, "_ensure_participant", lambda _owner, _scope: None)
    monkeypatch.setattr(sync, "read_enrolled_token", lambda **_kwargs: ("memory-token", CONFIG, scope_hash))
    monkeypatch.setattr(sync, "_probe_module", lambda: FakeProbe)
    result = sync.collect(
        owner_id="owner",
        scope_hash=scope_hash,
        now=dt.datetime(2026, 9, 13, 14, 30, tzinfo=dt.UTC),
    )
    assert result.snapshot_hash
    assert calls[0].start_date == "2026-08-15"
    assert calls[0].end_date == "2026-09-14"
    assert calls[0].include_files is False
    assert calls[0].include_modules is True


def test_cli_snapshot_and_project_are_executable(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.json"
    canonical_path = tmp_path / "canonical.json"
    readback_path = tmp_path / "readback.json"
    raw_path.write_text(json.dumps(raw_snapshot(), ensure_ascii=False), encoding="utf-8")
    command = [
        sys.executable,
        str(MODULE_PATH),
        "snapshot",
        "--course-id",
        "12345",
        "--expected-name",
        "알고리즘 (002)",
        "--expected-code",
        "CS-002",
        "--expected-term",
        "2026-2",
        "--input",
        str(raw_path),
    ]
    captured = subprocess.run(command, check=True, capture_output=True, text=True)
    canonical_path.write_text(captured.stdout, encoding="utf-8")
    readback_path.write_text(
        json.dumps({"rows": [], "course_pages": [], "course_parent_verified": True, "private_root_verified": True}),
        encoding="utf-8",
    )
    projected = subprocess.run(
        [sys.executable, str(MODULE_PATH), "project", "--snapshot", str(canonical_path), "--readback", str(readback_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(projected.stdout)
    assert result["datasource_count"] == 1
    assert result["status"] == "complete"


@pytest.mark.parametrize("positions", [[], [2, 4, 9, 14], [3, 7, 11, 16]])
def test_nonpilot_course_accepts_actual_module_positions(positions: list[int]) -> None:
    config = sync.SyncConfig(54321, "다른 과목", "OTHER-001", "2026-2")
    raw = raw_snapshot()
    raw["course"] = {"id": 54321, "name": "다른 과목", "course_code": "OTHER-001", "term": "2026-2"}
    raw["announcements"][0]["context_code"] = "course_54321"
    raw["modules"] = [
        {**module_record(position), "id": 8000 + position, "name": f"자료 묶음 {position}"}
        for position in positions
    ]
    result = sync.validate_snapshot(raw, config)
    assert [module["position"] for module in result.payload["modules"]] == positions


def test_semester_registry_preserves_per_course_partial_and_excluded_candidate() -> None:
    registry = sync.registry_from_document({
        "version": "knu-lms-semester-registry.v1",
        "semester": "2026-2",
        "courses": [
            {"id": 12345, "name": "알고리즘 (002)", "code": "CS-002", "term": "2026-2",
             "verification_state": "api_code_verified", "academic_import": True,
             "module_positions": list(range(1, 16))},
            {"id": 99999, "name": "비교과 후보", "code": "EXTRA", "term": "other",
             "verification_state": "observed_candidate", "academic_import": False},
        ],
    })
    result = sync.validate_semester_snapshot(registry, {
        "status": "complete", "provenance": "aside-readonly",
        "scope_hash": registry.scope_hash(transport="aside-readonly"),
        "courses": {"12345": raw_snapshot()},
    })
    assert result["status"] == "complete"
    assert result["academic_expected"] == 1
    assert result["academic_complete"] == 1
    assert result["courses"]["99999"]["status"] == "excluded"
    assert result["excluded_candidates"] == ["99999"]

    partial = sync.validate_semester_snapshot(registry, {
        "status": "complete", "provenance": "aside-readonly",
        "scope_hash": registry.scope_hash(transport="aside-readonly"),
        "courses": {"12345": {"status": "partial", "error": "page_cap"}},
    })
    assert partial["status"] == "incomplete"
    assert partial["courses"]["12345"]["status"] == "partial"


def test_semester_projection_emits_one_datasource_and_course_qualified_views() -> None:
    registry = sync.registry_from_document({
        "version": "knu-lms-semester-registry.v1", "semester": "2026-2", "courses": [
            {"id": 12345, "name": "알고리즘 (002)", "code": "CS-002", "term": "2026-2",
             "verification_state": "api_code_verified", "academic_import": True,
             "module_positions": list(range(1, 16))},
        ],
    })
    semester = sync.validate_semester_snapshot(registry, {
        "status": "complete", "provenance": "aside-readonly",
        "scope_hash": registry.scope_hash(transport="aside-readonly"),
        "courses": {"12345": raw_snapshot()},
    })
    plan = sync.build_semester_projection(registry, semester, notion_readback={
        "rows": [], "course_pages": [], "course_parent_verified": True, "private_root_verified": True,
    })
    assert plan["datasource_count"] == 1
    assert [view["name"] for view in plan["datasource"]["views"][:2]] == ["To DO", "캘린더"]
    assert len(plan["datasource"]["views"]) == 3
    assert plan["datasource"]["views"][2]["filter"].endswith("/courses/12345/assignments/")


def _two_course_semester() -> tuple[sync.SemesterCourseRegistry, dict[str, Any]]:
    registry = sync.registry_from_document({
        "version": "knu-lms-semester-registry.v1", "semester": "2026-2", "courses": [
            {"id": 12345, "name": "알고리즘 (002)", "code": "CS-002", "term": "2026-2",
             "verification_state": "api_code_verified", "academic_import": True,
             "module_positions": list(range(1, 16))},
            {"id": 54321, "name": "데이터베이스 (001)", "code": "DB-001", "term": "2026-2",
             "verification_state": "api_code_verified", "academic_import": True},
        ],
    })
    second = raw_snapshot()
    second["course"] = {
        "id": 54321, "name": "데이터베이스 (001)", "course_code": "DB-001", "term": "2026-2"
    }
    second["announcements"][0]["context_code"] = "course_54321"
    semester = sync.validate_semester_snapshot(registry, {
        "status": "complete", "provenance": "aside-readonly",
        "scope_hash": registry.scope_hash(transport="aside-readonly"),
        "courses": {"12345": raw_snapshot(), "54321": second},
    })
    return registry, semester


def test_semester_projection_scopes_verified_titles_per_course_and_local_readback() -> None:
    registry, semester = _two_course_semester()
    key_a = "https://canvas.knu.ac.kr/courses/12345"
    key_b = "https://canvas.knu.ac.kr/courses/54321"
    readback = {
        "rows": [], "course_pages": [], "course_parent_verified": True, "private_root_verified": True,
        "verified_course_titles": {key_a: "알고리즘2 (001)"},
        "course_readbacks": {key_b: {"verified_course_title": "데이터베이스 (001)"}},
    }
    plan = sync.build_semester_projection(registry, semester, notion_readback=readback)
    rows_a = plan["courses"]["12345"]["rows"]
    rows_b = plan["courses"]["54321"]["rows"]
    assert rows_a[0]["source_fields"]["과목"] == "알고리즘2 (001)"
    assert rows_b[0]["source_fields"]["과목"] == "데이터베이스 (001)"
    assert plan["courses"]["12345"]["course_page"]["title"] == "알고리즘2 (001)"
    assert plan["courses"]["54321"]["course_page"]["title"] == "데이터베이스 (001)"


def test_semester_projection_missing_title_map_falls_back_to_own_course() -> None:
    registry, semester = _two_course_semester()
    plan = sync.build_semester_projection(
        registry, semester,
        notion_readback={"rows": [], "course_pages": [], "course_parent_verified": True, "private_root_verified": True},
    )
    assert plan["courses"]["12345"]["rows"][0]["source_fields"]["과목"] == "알고리즘 (002)"
    assert plan["courses"]["54321"]["rows"][0]["source_fields"]["과목"] == "데이터베이스 (001)"


def test_semester_projection_rejects_global_title_and_stale_or_mismatched_registry() -> None:
    registry, semester = _two_course_semester()
    common = {"rows": [], "course_pages": [], "course_parent_verified": True, "private_root_verified": True}
    with pytest.raises(sync.SyncError, match="semester_title_scope_required"):
        sync.build_semester_projection(
            registry, semester, notion_readback={**common, "verified_course_title": "알고리즘2 (001)"}
        )
    stale = {**semester, "registry_hash": "0" * 64}
    with pytest.raises(sync.SyncError, match="registry_hash_mismatch"):
        sync.build_semester_projection(registry, stale, notion_readback=common)
    mismatched = json.loads(json.dumps(semester, ensure_ascii=False))
    payload = mismatched["snapshots"]["12345"]["snapshot"]
    payload["scope"]["expected_name"] = "다른 이름"
    payload["course"]["name"] = "다른 이름"
    mismatched["snapshots"]["12345"]["snapshot_hash"] = sha256(
        sync._canonical_bytes(payload)
    ).hexdigest()
    with pytest.raises(sync.SyncError, match="semester_course_identity_mismatch"):
        sync.build_semester_projection(registry, mismatched, notion_readback=common)


def test_bootstrap_binds_identity_candidate_with_private_atomic_registry(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({
        "version": "knu-lms-semester-registry.v1", "semester": "2026-2", "courses": [
            {"id": 54321, "name": "새 과목", "code": None, "term": "2026-2",
             "verification_state": "identity_observed", "academic_import": True},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    registry_path.chmod(0o600)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps({"status": "complete", "candidates": {
        "54321": {"status": "needs_verification", "course": {
            "id": 54321, "name": "새 과목", "course_code": "NEW-001", "term": "2026-2"
        }}
    }}), encoding="utf-8")
    result = sync.bind_bootstrap(str(registry_path), str(candidate_path))
    assert result["status"] == "bound"
    assert sync.registry_from_document(json.loads(registry_path.read_text())).courses[0].expected_code == "NEW-001"


def test_registry_project_cli_requires_prior_and_observed_on(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({
        "version": "knu-lms-semester-registry.v1", "semester": "2026-2", "courses": [
            {"id": 12345, "name": "알고리즘 (002)", "code": "CS-002", "term": "2026-2",
             "verification_state": "api_code_verified", "academic_import": True},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "project",
            "--registry", str(registry_path),
            "--snapshot", str(tmp_path / "snapshot.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert json.loads(completed.stdout) == {
        "status": "failed", "error": "semester_runtime_binding_required"
    }


def test_read_enrolled_token_full_path_on_simulated_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exercise the entire config/manifest read plus ownership-boundary path
    (not just keyring backend selection) with the process's own os.name
    branch simulated as Windows, so a Unix-only primitive anywhere in this
    call chain (os.getuid/O_NOFOLLOW/O_DIRECTORY/fchmod) fails this test
    instead of only failing on a real Windows runner much later."""
    monkeypatch.setattr(sync.sys, "platform", "win32")
    monkeypatch.setattr(sync.fsplat, "IS_WINDOWS", True)
    monkeypatch.setattr(sync.fsplat, "_windows_owner_sid", lambda path: "S-1-5-21-SAME")
    monkeypatch.setattr(sync.fsplat, "_windows_current_user_sid", lambda: "S-1-5-21-SAME")
    issued = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    now = dt.datetime(2026, 9, 13, tzinfo=dt.UTC)
    document = _auth_document(issued=issued, expires=dt.datetime(2026, 9, 30, tzinfo=dt.UTC))
    scope_hash = _install_auth_files(monkeypatch, tmp_path, document)
    enrolled = sync._fixed_auth_document(document, "enrolled", scope_hash)
    sync.AUTH_MANIFEST_PATH.write_text(json.dumps(enrolled), encoding="utf-8")
    sync.AUTH_MANIFEST_PATH.chmod(0o600)

    class WindowsBackend:
        def get_password(self, _service: str, _account: str) -> str:
            return "windows-token"

    monkeypatch.setattr(sync, "_explicit_os_keyring", lambda: WindowsBackend())
    token, config, returned_scope_hash = sync.read_enrolled_token(now=now)
    assert token == "windows-token"
    assert config.course_id == 12345
    assert returned_scope_hash == scope_hash
