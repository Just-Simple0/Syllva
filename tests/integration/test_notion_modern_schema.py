"""Notion 2025-09-03 data-source shapes: database parent and status group arrays (fake-only)."""
from __future__ import annotations

import copy
from typing import Any

import pytest
from tests.integration.test_intake_worker_preview import (
    SEMESTER,
    _NotionClient,
    _provider_schema_payloads,
)

from uls.adapters.notion.intake import (
    STATUS_GROUPS,
    NotionAPIWorker,
    NotionIntakeWriter,
    _data_source_parent_page_id,
    _status_group_names,
)

pytestmark = pytest.mark.integration


def _modern_payloads() -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    ids, payloads = _provider_schema_payloads()
    for logical, payload in payloads.items():
        payload["parent"] = {"type": "database_id", "database_id": f"db-{logical}"}
        payload["database_parent"] = {"type": "page_id", "page_id": "notion-parent"}
        for prop in payload["properties"].values():
            if prop["type"] != "status":
                continue
            options = [{"id": f"opt-{index}", "name": option["name"], "color": "default"}
                       for index, option in enumerate(prop["status"]["options"])]
            by_name = {option["name"]: option["id"] for option in options}
            groups = [{"id": f"grp-{index}", "name": group, "color": "default",
                       "option_ids": [by_name[name] for name in names]}
                      for index, (group, names) in enumerate(STATUS_GROUPS[logical].items())]
            prop["status"] = {"options": options, "groups": groups}
    return ids, payloads


class _CountingClient(_NotionClient):
    def __init__(self, payloads: dict[str, dict[str, Any]]) -> None:
        super().__init__(payloads)
        self.retrieves = 0
        inner = self.data_sources.retrieve

        def counted(**kwargs: Any) -> dict[str, Any]:
            self.retrieves += 1
            return inner(**kwargs)
        self.data_sources.retrieve = counted  # type: ignore[method-assign]

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"unexpected Notion client access: {name}")


def _writer(payloads: dict[str, dict[str, Any]], ids: dict[str, str]) -> tuple[NotionIntakeWriter, _CountingClient]:
    client = _CountingClient(payloads)
    return NotionIntakeWriter(NotionAPIWorker(client), ids, parent_page_id="notion-parent", semester=SEMESTER), client


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(parent="not-a-mapping", _parent_page_id="notion-parent"),
    lambda p: p.update(parent=None, _parent_page_id="notion-parent"),
    lambda p: p["parent"].update(type="workspace"),
    lambda p: (p["parent"].update(type="page_id"), p["parent"].pop("page_id"), p.update(_parent_page_id="notion-parent")),
    lambda p: (p["parent"].update(type="page_id", page_id=""), p.update(_parent_page_id="notion-parent")),
    # An explicit but empty/invalid legacy parent mapping is never completed by other fields.
    lambda p: p.update(parent={}, _parent_page_id="notion-parent"),
    lambda p: p.update(parent={"page_id": ""}, _parent_page_id="notion-parent"),
    lambda p: p.update(parent={"page_id": None}, _parent_page_id="notion-parent"),
    lambda p: p.update(parent={"parent_page_id": 7}, _parent_page_id="notion-parent"),
])
def test_malformed_legacy_parent_never_recovers_through_other_fields(mutate) -> None:
    ids, payloads = _provider_schema_payloads()
    mutate(payloads["sessions"])
    writer, _client = _writer(payloads, ids)
    assert writer.validate_workspace()["status"] == "NOT_VERIFIED"


def test_internal_parent_page_id_alone_and_untyped_legacy_parent_still_verify() -> None:
    ids, payloads = _provider_schema_payloads()
    for payload in payloads.values():
        del payload["parent"]
        payload["_parent_page_id"] = "notion-parent"
    writer, _client = _writer(payloads, ids)
    assert writer.validate_workspace()["status"] == "VERIFIED"
    ids, payloads = _provider_schema_payloads()
    for payload in payloads.values():
        payload["parent"] = {"page_id": "notion-parent"}  # untyped legacy direct parent
    writer, _client = _writer(payloads, ids)
    assert writer.validate_workspace()["status"] == "VERIFIED"


def test_legacy_shape_still_verifies_and_wrong_parent_is_rejected() -> None:
    ids, payloads = _provider_schema_payloads()
    writer, _client = _writer(payloads, ids)
    assert writer.validate_workspace()["status"] == "VERIFIED"
    payloads["materials"]["parent"] = {"type": "page_id", "page_id": "wrong-parent"}
    assert writer.validate_workspace()["status"] == "NOT_VERIFIED"


def test_modern_sdk_shape_verifies_five_sources_with_only_retrieve_calls() -> None:
    ids, payloads = _modern_payloads()
    writer, client = _writer(payloads, ids)
    verified = writer.validate_workspace()
    assert verified["status"] == "VERIFIED"
    assert verified["data_sources"] == 5 and verified["properties"] == 76
    assert client.retrieves == 5


@pytest.mark.parametrize("mutate", [
    lambda p: p["parent"].pop("database_id"),
    lambda p: p["parent"].update(database_id=""),
    lambda p: p.pop("database_parent"),
    lambda p: p["database_parent"].pop("page_id"),
    lambda p: p["database_parent"].update(type="workspace"),
    lambda p: p["database_parent"].update(page_id="wrong-parent"),
    lambda p: p["parent"].update(page_id="other-parent"),
    lambda p: p.update(_parent_page_id="other-parent"),
    # Incomplete modern shape must not fall back to legacy fields that happen to match.
    lambda p: (p["parent"].pop("database_id"), p["parent"].update(page_id="notion-parent")),
    lambda p: (p["parent"].pop("database_id"), p.update(_parent_page_id="notion-parent")),
    lambda p: (p.pop("database_parent"), p["parent"].update(page_id="notion-parent")),
    # Malformed database_parent values and a contradicting/missing parent.type never fall back to legacy.
    lambda p: (p.update(database_parent="notion-parent"), p["parent"].update(page_id="notion-parent")),
    lambda p: (p.update(database_parent=None), p.update(_parent_page_id="notion-parent")),
    lambda p: p["parent"].update(type="page_id", page_id="notion-parent"),
    lambda p: (p["parent"].pop("type"), p["parent"].update(page_id="notion-parent")),
    lambda p: p.update(parent="not-a-mapping"),
])
def test_incomplete_or_conflicting_modern_parent_fails_closed(mutate) -> None:
    ids, payloads = _modern_payloads()
    mutate(payloads["sessions"])
    writer, _client = _writer(payloads, ids)
    assert writer.validate_workspace()["status"] == "NOT_VERIFIED"


def _status_prop(payloads: dict[str, dict[str, Any]], logical: str = "sessions") -> dict[str, Any]:
    return next(prop for prop in payloads[logical]["properties"].values() if prop["type"] == "status")


@pytest.mark.parametrize("mutate", [
    lambda s: s["groups"][0]["option_ids"].append("opt-unknown"),
    lambda s: s["groups"][0]["option_ids"].append(next(g for g in s["groups"] if g["option_ids"] and g is not s["groups"][0])["option_ids"][0]),
    lambda s: s["groups"].append({"id": "grp-x", "name": "extra", "color": "default", "option_ids": []}),
    lambda s: s["groups"].pop(),
    lambda s: s["groups"][1].update(name=s["groups"][0]["name"]),
    lambda s: s["groups"].append("malformed"),
    lambda s: s["groups"][0].pop("option_ids"),
    lambda s: s["options"].append({"id": "opt-orphan", "name": "Orphan", "color": "default"}),
    lambda s: s["options"].append(dict(s["options"][0])),
    lambda s: s.update(groups="not-a-list"),
    # Duplicate option *names* under distinct IDs and duplicate group IDs collapse under set
    # normalization; they must be rejected before comparison.
    lambda s: s["options"].append({"id": "opt-dup-name", "name": s["options"][0]["name"], "color": "default"}),
    lambda s: s["groups"][1].update(id=s["groups"][0]["id"]),
    lambda s: s["groups"][0].update(id=""),
    lambda s: s["groups"][0].pop("id"),
    lambda s: s["options"][0].update(name=""),
])
def test_malformed_modern_groups_fail_closed(mutate) -> None:
    ids, payloads = _modern_payloads()
    mutate(_status_prop(payloads)["status"])
    writer, _client = _writer(payloads, ids)
    assert writer.validate_workspace()["status"] == "NOT_VERIFIED"


@pytest.mark.parametrize("mutate", [
    lambda s: s["groups"].update({"extra-invalid-group": "not a valid group list"}),
    lambda s: s["groups"].update({"": []}),
    lambda s: s["groups"].__setitem__(next(iter(s["groups"])), ["not-a-mapping"]),
    lambda s: s["groups"].__setitem__(next(iter(s["groups"])), [{"name": ""}]),
    lambda s: s["groups"].__setitem__(next(iter(s["groups"])), [{"id": "no-name"}]),
])
def test_malformed_legacy_groups_fail_closed_instead_of_being_dropped(mutate) -> None:
    ids, payloads = _provider_schema_payloads()
    mutate(_status_prop(payloads)["status"])
    writer, client = _writer(payloads, ids)
    assert writer.validate_workspace()["status"] == "NOT_VERIFIED"
    assert client.retrieves >= 1  # readback only; no create/update calls exist on the client


def _select_prop(payloads: dict[str, dict[str, Any]], logical: str = "sessions") -> dict[str, Any]:
    return next(prop for prop in payloads[logical]["properties"].values() if prop["type"] == "select")


@pytest.mark.parametrize("kind", ["status", "select"])
@pytest.mark.parametrize("mutate", [
    lambda cfg: cfg["options"].append({"name": None}),
    lambda cfg: cfg["options"].append({"name": ""}),
    lambda cfg: cfg["options"].append("not-a-mapping"),
    lambda cfg: cfg["options"].append({"id": "no-name"}),
    lambda cfg: cfg["options"].append(dict(cfg["options"][0])),
    lambda cfg: cfg.update(options="not-a-list"),
])
def test_malformed_or_duplicate_legacy_options_fail_closed(kind, mutate) -> None:
    ids, payloads = _provider_schema_payloads()
    prop = _status_prop(payloads) if kind == "status" else _select_prop(payloads)
    mutate(prop[kind])
    writer, _client = _writer(payloads, ids)
    assert writer.validate_workspace()["status"] == "NOT_VERIFIED"


def test_duplicate_name_inside_a_legacy_group_list_fails_closed() -> None:
    ids, payloads = _provider_schema_payloads()
    groups = _status_prop(payloads)["status"]["groups"]
    group = next(name for name, options in groups.items() if options)
    groups[group].append(dict(groups[group][0]))
    writer, _client = _writer(payloads, ids)
    assert writer.validate_workspace()["status"] == "NOT_VERIFIED"


def test_parsers_normalize_both_shapes_to_the_same_contract() -> None:
    _ids, legacy = _provider_schema_payloads()
    _ids, modern = _modern_payloads()
    for logical in legacy:
        if logical == "automation_queue":
            continue
        assert _status_group_names(_status_prop(legacy, logical)) == _status_group_names(_status_prop(modern, logical))
        assert _status_group_names(_status_prop(modern, logical)) == {
            group: set(values) for group, values in STATUS_GROUPS[logical].items()}
    assert _data_source_parent_page_id(copy.deepcopy(modern["sessions"])) == "notion-parent"
    assert _data_source_parent_page_id({"parent": {"type": "database_id", "database_id": "db"}}) is None
    assert _data_source_parent_page_id({"parent": {"type": "page_id", "page_id": "p"},
                                        "database_parent": {"type": "page_id", "page_id": "q"}}) is None
    assert _data_source_parent_page_id({"parent": {"type": "page_id", "page_id": "abc-def"},
                                        "_parent_page_id": "abcdef"}) == "abcdef"


def _live_shaped_payloads() -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """Mirror the real 2025-09-03 readback: only the three built-in groups, display-named."""
    display = {"to_do": "To-do", "in_progress": "In progress", "complete": "Complete"}
    ids, payloads = _modern_payloads()
    for payload in payloads.values():
        for prop in payload["properties"].values():
            if prop["type"] != "status":
                continue
            prop["status"]["groups"] = [
                {**group, "name": display[group["name"]]}
                for group in prop["status"]["groups"] if group["name"] in display
            ]
    return ids, payloads


def test_live_shaped_built_in_groups_verify_and_misplaced_option_still_fails() -> None:
    ids, payloads = _live_shaped_payloads()
    for prop in payloads["academic_courses"]["properties"].values():
        if prop["type"] == "status":
            assert [g["name"] for g in prop["status"]["groups"]] == ["Complete", "In progress", "To-do"]
    writer, _client = _writer(payloads, ids)
    assert writer.validate_workspace()["status"] == "VERIFIED"

    ids, payloads = _live_shaped_payloads()
    status = _status_prop(payloads, "academic_courses")["status"]
    complete = next(g for g in status["groups"] if g["name"] == "Complete")
    in_progress = next(g for g in status["groups"] if g["name"] == "In progress")
    in_progress["option_ids"].extend(complete["option_ids"]); complete["option_ids"].clear()
    writer, _client = _writer(payloads, ids)
    assert writer.validate_workspace() == {"status": "NOT_VERIFIED",
                                           "reason": "data-source status groups mismatch: academic_courses.Status"}

    ids, payloads = _live_shaped_payloads()
    _status_prop(payloads, "academic_courses")["status"]["groups"].append(
        {"id": "grp-x", "name": "Someday", "color": "default", "option_ids": []})
    writer, _client = _writer(payloads, ids)
    assert writer.validate_workspace()["status"] == "NOT_VERIFIED"
