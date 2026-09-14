from __future__ import annotations

import base64
import importlib.util
import io
import json
import subprocess
import sys
import time
import warnings
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

pytestmark = pytest.mark.unit

MODULE_PATH = Path(__file__).parents[2] / "scripts" / "knu_lms_probe.py"
SPEC = importlib.util.spec_from_file_location("knu_lms_probe", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)

TOKEN = "SYNTHETIC_SECRET"
BASE_ARGS = [
    "--course-id",
    "12345",
    "--expected-name",
    "Synthetic Course",
    "--expected-code",
    "SYN-001",
    "--expected-term",
    "Synthetic Term",
    "--start-date",
    "2026-09-01",
    "--end-date",
    "2026-09-13",
]


class FakeStdin:
    def __init__(self, is_tty: bool) -> None:
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


class FakeResponse:
    def __init__(self, payload: Any, *, status: Any = 200, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self._body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            body, self._body = self._body, b""
            return body
        body, self._body = self._body[:size], self._body[size:]
        return body

    def close(self) -> None:
        self.closed = True


class QueueOpener:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[Any, float]] = []

    def open(self, request: Any, timeout: float) -> Any:
        self.calls.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def course_response(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": 12345,
        "name": "Synthetic Course",
        "course_code": "SYN-001",
        "term": {"name": "Synthetic Term"},
        "syllabus_body": "PRIVATE_BODY_SHOULD_NOT_APPEAR",
    }
    result.update(overrides)
    return result


def run_probe(responses: list[Any], args: list[str] | None = None, **kwargs: Any) -> tuple[int, dict[str, Any], str, QueueOpener]:
    output = io.StringIO()
    error = io.StringIO()
    opener = QueueOpener(responses)
    status = probe.main(
        BASE_ARGS if args is None else args,
        opener=opener,
        prompt=lambda _prompt: TOKEN,
        stdin=FakeStdin(True),
        output=output,
        error=error,
        **kwargs,
    )
    parsed = json.loads(output.getvalue())
    return status, parsed, error.getvalue(), opener


def complete_responses() -> list[FakeResponse]:
    return [
        FakeResponse(course_response()),
        FakeResponse(
            [
                {
                    "id": 10,
                    "name": "Assignment without due date",
                    "due_at": None,
                    "description": "BODY_SHOULD_NOT_APPEAR",
                    "submission": {"score": 99},
                }
            ]
        ),
        FakeResponse(
            [
                {
                    "id": 20,
                    "title": "Synthetic announcement",
                    "context_code": "course_12345",
                    "message": "BODY_SHOULD_NOT_APPEAR",
                }
            ]
        ),
    ]


def test_success_is_clean_and_preserves_missing_due_date() -> None:
    status, result, error, opener = run_probe(complete_responses())

    assert status == 0
    assert result["status"] == "complete"
    assert result["assignments"][0]["due_at"] is None
    assert "description" not in result["assignments"][0]
    assert "submission" not in result["assignments"][0]
    assert "message" not in result["announcements"][0]
    assert error == ""
    assert len(opener.calls) == 3
    request, timeout = opener.calls[0]
    assert request.get_method() == "GET"
    assert request.get_header("Authorization") == f"Bearer {TOKEN}"
    assert "SYNTHETIC_SECRET" not in request.full_url
    query = parse_qs(urlsplit(request.full_url).query)
    assert query["include[]"] == ["term"]
    assert timeout <= probe.MAX_SOCKET_TIMEOUT_SECONDS


def test_course_identity_fields_are_required_and_fail_closed() -> None:
    response = course_response(term=None)
    status, result, error, opener = run_probe([FakeResponse(response)])

    assert status == 2
    assert result == {"status": "failed", "error": "course_identity_missing"}
    assert "PRIVATE_BODY" not in result.__repr__()
    assert "course_identity_missing" in error
    assert len(opener.calls) == 1


def test_course_identity_mismatch_stops_resource_calls() -> None:
    status, result, _error, opener = run_probe([FakeResponse(course_response(course_code="SYN-002"))])

    assert status == 2
    assert result["error"] == "course_identity_mismatch"
    assert len(opener.calls) == 1


def test_dates_are_required_calendar_dates_and_bounded() -> None:
    output = io.StringIO()
    error = io.StringIO()
    status = probe.main(
        [*BASE_ARGS[:8], "--start-date", "2026-09-01T00:00:00Z", "--end-date", "2026-09-02"],
        prompt=lambda _prompt: TOKEN,
        stdin=FakeStdin(True),
        output=output,
        error=error,
    )
    assert status == 2
    assert output.getvalue() == ""
    assert "argument_error" in error.getvalue()

    output = io.StringIO()
    error = io.StringIO()
    args = [*BASE_ARGS[:8], "--start-date", "2026-01-01", "--end-date", "2026-03-01"]
    status = probe.main(
        args,
        prompt=lambda _prompt: TOKEN,
        stdin=FakeStdin(True),
        output=output,
        error=error,
    )
    assert status == 2
    assert json.loads(output.getvalue())["error"] == "invalid_date_range"


def test_unknown_token_argument_is_sanitized() -> None:
    output = io.StringIO()
    error = io.StringIO()
    status = probe.main(
        [*BASE_ARGS, "--token", TOKEN],
        prompt=lambda: pytest.fail("credential prompt must not run"),
        stdin=FakeStdin(True),
        output=output,
        error=error,
    )

    assert status == 2
    assert output.getvalue() == ""
    assert "argument_error" in error.getvalue()
    assert TOKEN not in error.getvalue()


def test_help_requires_independently_verified_identity_without_guessing() -> None:
    help_text = probe.build_parser().format_help()

    assert "Independently verified course code" in help_text
    assert "Missing or uncertain identity stops before resource calls" in help_text
    assert "try fake credentials" in help_text


def test_non_tty_is_a_safe_fatal_error_without_prompt() -> None:
    output = io.StringIO()
    error = io.StringIO()
    status = probe.main(
        BASE_ARGS,
        prompt=lambda _prompt: pytest.fail("non-TTY must not prompt"),
        stdin=FakeStdin(False),
        output=output,
        error=error,
    )

    assert status == 2
    assert json.loads(output.getvalue()) == {"status": "failed", "error": "credential_tty_required"}
    assert TOKEN not in output.getvalue() + error.getvalue()


@pytest.mark.parametrize("prompt_exception", [EOFError(), KeyboardInterrupt()])
def test_credential_abort_is_sanitized(prompt_exception: BaseException) -> None:
    output = io.StringIO()
    error = io.StringIO()

    def abort(_prompt: str) -> str:
        raise prompt_exception

    status = probe.main(
        BASE_ARGS,
        prompt=abort,
        stdin=FakeStdin(True),
        output=output,
        error=error,
    )
    assert status == 2
    assert json.loads(output.getvalue()) == {"status": "failed", "error": "credential_input_aborted"}
    assert TOKEN not in output.getvalue() + error.getvalue()


def test_getpass_warning_fallback_is_an_error() -> None:
    output = io.StringIO()
    error = io.StringIO()

    def warning_prompt(_prompt: str) -> str:
        warnings.warn("echo fallback", probe.getpass.GetPassWarning)
        return TOKEN

    status = probe.main(
        BASE_ARGS,
        prompt=warning_prompt,
        stdin=FakeStdin(True),
        output=output,
        error=error,
    )
    assert status == 2
    assert json.loads(output.getvalue()) == {"status": "failed", "error": "credential_noecho_unavailable"}
    assert "echo fallback" not in output.getvalue() + error.getvalue()


def test_cross_origin_redirect_location_is_refused_without_leaking_location() -> None:
    synthetic_location = "https://evil.invalid/next?access_token=SYNTHETIC_SECRET"
    responses = [
        FakeResponse(
            b"",
            status=302,
            headers={"Location": synthetic_location, "Content-Type": "text/html"},
        )
    ]
    status, result, error, _opener = run_probe(responses)

    assert status == 2
    assert result == {"status": "failed", "error": "redirect_rejected"}
    assert synthetic_location not in json.dumps(result) + error


def test_unsafe_pagination_link_is_refused_without_leaking_link() -> None:
    synthetic_link = "https://evil.invalid/next?token=SYNTHETIC_SECRET"
    responses = [
        FakeResponse(course_response()),
        FakeResponse([], headers={"Link": f'<{synthetic_link}>; rel="next"'}),
    ]
    status, result, error, _opener = run_probe(responses)

    assert status == 2
    assert result == {"status": "failed", "error": "unsafe_pagination"}
    assert synthetic_link not in json.dumps(result) + error


@pytest.mark.parametrize(
    "query",
    [
        "context_codes%5B%5D=course_54321&start_date=2026-09-01&end_date=2026-09-13&per_page=50&page=2",
        "context_codes%5B%5D=course_12345&start_date=2026-09-02&end_date=2026-09-13&per_page=50&page=2",
        "context_codes%5B%5D=course_12345&start_date=2026-09-01&end_date=2026-09-13&include%5B%5D=items&per_page=50&page=2",
        "context_codes%5B%5D=course_12345&start_date=2026-09-01&end_date=2026-09-13&per_page=50&per_page=50&page=2",
    ],
)
def test_pagination_query_drift_or_duplicate_is_refused(query: str) -> None:
    responses = [
        FakeResponse(course_response()),
        FakeResponse([]),
        FakeResponse(
            [],
            headers={
                "Link": f'<https://canvas.knu.ac.kr/api/v1/announcements?{query}>; rel="next"'
            },
        ),
    ]
    status, result, error, _opener = run_probe(responses)

    assert status == 2
    assert result == {"status": "failed", "error": "unsafe_pagination"}
    assert "SYNTHETIC_SECRET" not in json.dumps(result) + error


def test_announcement_context_mismatch_is_refused() -> None:
    responses = complete_responses()
    responses[2] = FakeResponse(
        [{"id": 20, "title": "Synthetic announcement", "context_code": "course_54321"}]
    )
    status, result, _error, _opener = run_probe(responses)

    assert status == 2
    assert result == {"status": "failed", "error": "announcement_context_mismatch"}


def test_page_cap_is_incomplete_and_nonzero() -> None:
    responses: list[FakeResponse] = [FakeResponse(course_response())]
    for page in range(3):
        responses.append(
            FakeResponse(
                [{"id": page + 1, "name": f"A{page}", "due_at": None}],
                headers={
                    "Link": '<https://canvas.knu.ac.kr/api/v1/courses/12345/assignments?'
                    f"order_by=name&per_page=50&page={page + 2}>; rel=\"next\""
                },
            )
        )
    status, result, error, _opener = run_probe(responses)

    assert status == 3
    assert result["status"] == "incomplete"
    assert result["incomplete_resources"] == ["assignments"]
    assert result["incomplete_reasons"] == {"assignments": "page_cap"}
    assert "SYNTHETIC_SECRET" not in json.dumps(result) + error


def test_deadline_during_read_is_incomplete_and_nonzero() -> None:
    class DeadlineClock:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self) -> float:
            self.calls += 1
            return 0.0 if self.calls < 8 else 46.0

    status, result, error, _opener = run_probe(
        [FakeResponse(course_response()), FakeResponse([])],
        clock=DeadlineClock(),
    )

    assert status == 3
    assert result["status"] == "incomplete"
    assert result["incomplete_reasons"] == {"assignments": "deadline"}
    assert "SYNTHETIC_SECRET" not in json.dumps(result) + error


def test_optional_resources_are_opt_in_and_urls_are_omitted() -> None:
    status, result, _error, opener = run_probe(complete_responses())
    assert status == 0
    assert "files" not in result
    assert "modules" not in result
    assert all("download_url" not in request.full_url for request, _timeout in opener.calls)


def test_optional_files_and_modules_are_metadata_only() -> None:
    responses = complete_responses()
    responses.extend(
        [
            FakeResponse(
                [
                    {
                        "id": 30,
                        "display_name": "Synthetic.pdf",
                        "url": "https://evil.invalid/download/SYNTHETIC_SECRET",
                        "download_url": "https://evil.invalid/download/SYNTHETIC_SECRET",
                        "size": 10,
                    }
                ]
            ),
            FakeResponse(
                [
                    {
                        "id": 40,
                        "name": "Week 1",
                        "items_count": 1,
                        "items": [
                            {
                                "id": 41,
                                "title": "Synthetic item",
                                "html_url": "https://evil.invalid/body/SYNTHETIC_SECRET",
                            }
                        ],
                    }
                ]
            ),
        ]
    )
    args = [*BASE_ARGS, "--include-files", "--include-modules"]
    status, result, error, opener = run_probe(responses, args)

    assert status == 0
    assert "url" not in result["files"][0]
    assert "download_url" not in result["files"][0]
    assert "html_url" not in result["modules"][0]["items"][0]
    assert len(opener.calls) == 5
    assert "SYNTHETIC_SECRET" not in json.dumps(result) + error


@pytest.mark.parametrize(
    ("module_record", "reason"),
    [
        ({"id": 40, "name": "Week 1"}, "module_items_unavailable"),
        ({"id": 40, "name": "Week 1", "items": [{"id": 41}]}, "module_item_invalid"),
        (
            {"id": 40, "name": "Week 1", "items": [{"id": 41, "title": "Synthetic item"}]},
            "module_items_count_missing",
        ),
        (
            {
                "id": 40,
                "name": "Week 1",
                "items": [{"id": 41, "title": "Synthetic item"}],
                "items_count": 2,
            },
            "module_items_count_mismatch",
        ),
    ],
)
def test_module_item_unavailable_or_invalid_is_explicitly_incomplete(
    module_record: dict[str, Any], reason: str
) -> None:
    responses = [*complete_responses(), FakeResponse([module_record])]
    status, result, error, _opener = run_probe(responses, [*BASE_ARGS, "--include-modules"])

    assert status == 3
    assert result["status"] == "incomplete"
    assert result["incomplete_reasons"] == {"modules": reason}
    module = result["modules"][0]
    assert module["items_complete"] is False
    assert "items" not in module or isinstance(module["items"], list)
    if reason == "module_items_unavailable":
        assert module["items_unavailable"] is True
    elif reason == "module_items_count_missing":
        assert module["items_count_available"] is False
        assert module["items_error"] == "count_unavailable"
    else:
        assert module["items_error"] in {"invalid_item", "count_mismatch"}
    assert "SYNTHETIC_SECRET" not in json.dumps(result) + error


def test_sanitized_module_shape_is_revalidated_without_raw_items_count() -> None:
    record = {
        "id": 40,
        "name": "1주차",
        "position": 1,
        "items": [{"id": 41, "title": "Synthetic item"}],
        "items_available": True,
        "items_unavailable": False,
        "items_count_available": True,
        "items_returned_count": 1,
        "items_complete": True,
    }
    sanitized = probe.sanitize_module(record)
    assert "items_count" not in sanitized
    assert sanitized["items_count_available"] is True
    assert sanitized["items_returned_count"] == 1
    assert sanitized["items_complete"] is True


def test_aside_script_uses_bounded_reader_and_does_not_emit_raw_fields() -> None:
    script = probe._aside_request_script({"tabId": "tab", "origin": probe.CANVAS_ORIGIN, "courses": []})
    assert "getReader" in script
    assert "arrayBuffer" not in script
    assert "JSON.parse" in script
    assert "await (async()=>" in script
    assert "download_url" not in script


def test_aside_emitted_script_executes_against_actual_tab_and_pagination_shapes() -> None:
    request = {
        "tabId": "target-1", "origin": probe.CANVAS_ORIGIN, "frameUrl": probe.CANVAS_ORIGIN,
        "startDate": "2026-09-01", "endDate": "2026-09-13",
        "courses": [{"id": 101, "name": "Synthetic Course", "code": "SYN-101", "term": "2026년 2학기"}],
    }
    script = probe._aside_request_script(request)
    harness = f'''globalThis.listBrowserTabs=async()=>[{{targetId:"target-1",title:"Canvas",url:"{probe.CANVAS_ORIGIN}/courses/101"}}];
let attached=""; globalThis.attachBrowserTab=async id=>{{attached=id;}};
const mk=(url,data,link="")=>{{let text=Buffer.from(JSON.stringify(data)),used=false;return {{status:200,url,headers:new Map([["content-type","application/json"],["link",link]]),body:{{getReader(){{return {{read:async()=>used?{{done:true}}:(used=true,{{done:false,value:text}})}};}}}}}};}};
globalThis.fetch=async(url,opts)=>{{let u=new URL(url),data=[],link="",p=u.pathname;
if(p==="/api/v1/courses/101")data={{id:101,name:"Synthetic Course",course_code:"SYN-101",term:{{name:"2026년 2학기"}}}};
else if(p.endsWith("/assignments")&&u.searchParams.get("page")==="2")data=[{{id:12,name:"B",due_at:null}}];
else if(p.endsWith("/assignments")){{data=[{{id:11,name:"A",due_at:null}}];link="<{probe.CANVAS_ORIGIN}/api/v1/courses/101/assignments?order_by=name&per_page=50&page=2>; rel=\\"next\\"";}}
else if(p==="/api/v1/announcements")data=[];
else if(p==="/api/v1/courses/101/modules")data=[{{id:30,name:"1주차",position:1,items:[{{id:301,title:"A"}}],items_count:2}}];
else if(p==="/api/v1/courses/101/modules/30/items")data=[{{id:301,title:"A"}},{{id:302,title:"B"}}];
else throw Error("unexpected"); return mk(url,data,link);}};
{script}
setTimeout(()=>console.log("ATTACHED:"+attached),50);'''
    completed = subprocess.run(["node", "--input-type=module", "-e", harness], capture_output=True, text=True, check=False)
    assert completed.returncode == 0
    assert "ATTACHED:target-1" in completed.stdout
    lines = [line for line in completed.stdout.splitlines() if line.startswith("ULS_ASIDE_FRAME:")]
    assert len(lines) == 1
    frame = json.loads(base64.b64decode(lines[0].split(":", 1)[1]))
    assert len(frame["courses"]["101"]["assignments"]) == 2
    assert len(frame["courses"]["101"]["modules"][0]["items"]) == 2


@pytest.mark.parametrize(
    "link",
    [
        f"<{probe.CANVAS_ORIGIN}/api/v1/courses/101/assignments?order_by=name&per_page=49&page=2>; rel=\"next\"",
        f"<{probe.CANVAS_ORIGIN}/api/v1/courses/101/assignments?order_by=name&per_page=50&page=2&extra=x>; rel=\"next\"",
        f"<{probe.CANVAS_ORIGIN}/api/v1/courses/101/assignments?order_by=name&per_page=50&page=3>; rel=\"next\"",
        f"<{probe.CANVAS_ORIGIN}/api/v1/courses/101/assignments?order_by=name&per_page=50&per_page=50&page=2>; rel=\"next\"",
        f"<{probe.CANVAS_ORIGIN}/api/v1/courses/101/assignments?order_by=name&per_page=50&page=2>; rel=\"next\", <{probe.CANVAS_ORIGIN}/api/v1/courses/101/assignments?order_by=name&per_page=50&page=2>; rel=\"next\"",
    ],
)
def test_aside_rejects_pagination_drift_without_losing_other_courses(link: str) -> None:
    request = {
        "tabId": "target-1", "origin": probe.CANVAS_ORIGIN, "frameUrl": probe.CANVAS_ORIGIN,
        "startDate": "2026-09-01", "endDate": "2026-09-13",
        "courses": [
            {"id": 101, "name": "Course A", "code": "A-1", "term": "2026년 2학기"},
            {"id": 102, "name": "Course B", "code": "B-1", "term": "2026년 2학기"},
        ],
    }
    script = probe._aside_request_script(request)
    link_literal = json.dumps(link)
    harness = f'''globalThis.listBrowserTabs=async()=>[{{targetId:"target-1",title:"Canvas",url:"{probe.CANVAS_ORIGIN}/courses/101"}}];
globalThis.attachBrowserTab=async()=>{{}};
const badLink={link_literal};
const mk=(url,data,link="")=>{{let text=Buffer.from(JSON.stringify(data)),used=false;return {{status:200,url,headers:new Map([["content-type","application/json"],["link",link]]),body:{{getReader(){{return {{read:async()=>used?{{done:true}}:(used=true,{{done:false,value:text}})}};}}}}}};}};
globalThis.fetch=async(url)=>{{let u=new URL(url),data=[],header="";
if(u.pathname==="/api/v1/courses/101"||u.pathname==="/api/v1/courses/102")data={{id:Number(u.pathname.split("/").pop()),name:u.pathname.endsWith("101")?"Course A":"Course B",course_code:u.pathname.endsWith("101")?"A-1":"B-1",term:{{name:"2026년 2학기"}}}};
else if(u.pathname.endsWith("/assignments")){{data=[{{id:11,name:"A",due_at:null}}];header=u.pathname.endsWith("/101/assignments")?badLink:"";}}
else if(u.pathname==="/api/v1/announcements")data=[];
else if(u.pathname.endsWith("/modules"))data=[];
else throw Error("unexpected"); return mk(url,data,header);}};
{script}'''
    completed = subprocess.run(["node", "--input-type=module", "-e", harness], capture_output=True, text=True, check=False)
    assert completed.returncode == 0
    lines = [line for line in completed.stdout.splitlines() if line.startswith("ULS_ASIDE_FRAME:")]
    assert len(lines) == 1
    frame = json.loads(base64.b64decode(lines[0].split(":", 1)[1]))
    assert frame["courses"]["101"] == {"status": "partial", "error": "course_collection_incomplete"}
    assert frame["courses"]["102"]["status"] == "complete"


def test_aside_frame_parser_preserves_per_course_partial_and_failed_results() -> None:
    frame = {
        "version": 1, "provenance": "aside-readonly", "origin": probe.CANVAS_ORIGIN,
        "request_url": probe.CANVAS_ORIGIN, "final_url": probe.CANVAS_ORIGIN,
        "headers": {"content_type": "application/json"}, "courses": {
            "1": {"status": "partial", "error": "course_collection_incomplete"},
            "2": {"status": "failed", "error": "course_identity_mismatch"},
        },
    }
    parsed = probe.parse_canonical_frame(frame)
    assert parsed["courses"]["1"]["status"] == "partial"
    assert parsed["courses"]["2"]["status"] == "failed"
    bad = {**frame, "courses": {"1": {"status": "partial", "error": "raw_backend_error"}}}
    with pytest.raises(probe.ProbeError, match="browser_frame_invalid"):
        probe.parse_canonical_frame(bad)


def test_bootstrap_keeps_full_registry_scope_but_targets_unknown_identity_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = [
        {
            "id": 100 + index,
            "name": f"Course {index}",
            "code": f"C-{index}",
            "term": "2026-2",
            "origin": probe.CANVAS_ORIGIN,
            "academic_import": True,
            "verification_state": "api_code_verified",
        }
        for index in range(5)
    ]
    registry.append({
        "id": 999,
        "name": "Unknown Course",
        "code": None,
        "term": "2026-2",
        "origin": probe.CANVAS_ORIGIN,
        "academic_import": True,
        "verification_state": "identity_observed",
    })
    seen: list[list[dict[str, Any]]] = []

    class Transport:
        def run(self, courses: list[dict[str, Any]], **_kwargs: Any) -> dict[str, Any]:
            seen.append(courses)
            return {"status": "complete", "provenance": "aside-readonly", "courses": {}}

    monkeypatch.setattr(probe, "_aside_participant", lambda *_args: None)
    full_scope = probe._expected_registry_scope_hash(registry)
    result = probe.run_registry_aside(
        registry,
        tab_id="tab",
        owner_id="owner",
        scope_hash=full_scope,
        start_date="2026-09-01",
        end_date="2026-09-14",
        transport=Transport(),
        bootstrap_targets=[registry[-1]],
    )
    assert [course["id"] for course in seen[0]] == [999]
    assert result["registry_course_count"] == 6
    changed = [*registry]
    changed[-1] = {**changed[-1], "code": "NEW-999", "verification_state": "api_code_verified"}
    with pytest.raises(probe.ProbeError, match="scope_hash_mismatch"):
        probe.run_registry_aside(
            changed,
            tab_id="tab",
            owner_id="owner",
            scope_hash=full_scope,
            start_date="2026-09-01",
            end_date="2026-09-14",
            transport=Transport(),
            bootstrap_targets=[changed[-1]],
        )


def test_aside_frame_parser_accepts_one_bounded_sanitized_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = {"version": 1, "provenance": "aside-readonly", "origin": probe.CANVAS_ORIGIN,
             "request_url": probe.CANVAS_ORIGIN, "final_url": probe.CANVAS_ORIGIN,
             "headers": {"content_type": "application/json"}, "courses": {}}
    encoded = base64.b64encode(json.dumps(frame).encode()).decode()
    monkeypatch.setattr(probe, "_run_bounded_aside_repl", lambda *_args: (f"ULS_ASIDE_FRAME:{encoded}\n".encode(), b""))
    transport = probe.AsideReplTransport(tab_id="tab", origin=probe.CANVAS_ORIGIN)
    assert transport.run([], start_date="2026-09-01", end_date="2026-09-13")["courses"] == {}


def test_aside_frame_rejects_unknown_nested_fields_and_bounded_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    frame = {"version": 1, "provenance": "aside-readonly", "origin": probe.CANVAS_ORIGIN,
             "request_url": probe.CANVAS_ORIGIN, "final_url": probe.CANVAS_ORIGIN,
             "headers": {"content_type": "application/json"}, "courses": {}}
    bad = {**frame, "courses": {"101": {"status": "needs_verification", "course": {
        "id": 101, "name": "Synthetic", "course_code": "SYN", "term": "2026-2", "raw": "blocked"
    }}}}
    with pytest.raises(probe.ProbeError, match="browser_frame_invalid"):
        probe.parse_canonical_frame(bad)
    stdout_path = tmp_path / "bounded.stdout"
    stderr_path = tmp_path / "bounded.stderr"
    stdout_path.write_bytes(b"x" * (probe.MAX_ASIDE_OUTPUT_BYTES + 1))
    stderr_path.write_bytes(b"")

    class Process:
        def __init__(self) -> None:
            self.stdout = stdout_path.open("rb")
            self.stderr = stderr_path.open("rb")

        def kill(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            return 0

    monkeypatch.setattr(probe.subprocess, "Popen", lambda *args, **kwargs: Process())
    with pytest.raises(probe.ProbeError, match="browser_output_too_large"):
        probe.AsideReplTransport(tab_id="tab", origin=probe.CANVAS_ORIGIN).run([], start_date="2026-09-01", end_date="2026-09-13")


def test_aside_process_with_closed_pipes_is_killed_at_remaining_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    real_popen = subprocess.Popen
    child: subprocess.Popen[bytes] | None = None

    def fake_popen(_args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        nonlocal child
        child = real_popen(
            [sys.executable, "-c", "import os, time; os.close(1); os.close(2); time.sleep(60)"],
            **kwargs,
        )
        return child

    monkeypatch.setattr(probe.subprocess, "Popen", fake_popen)
    started = time.monotonic()
    with pytest.raises(probe.ProbeError, match="browser_timeout"):
        probe._run_bounded_aside_repl("synthetic", 0.2)
    elapsed = time.monotonic() - started
    assert elapsed < 1.5
    assert child is not None
    assert child.poll() is not None


def test_aside_nonzero_exit_rejected_even_with_valid_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    real_popen = subprocess.Popen
    frame = {"version": 1, "provenance": "aside-readonly", "origin": probe.CANVAS_ORIGIN,
             "request_url": probe.CANVAS_ORIGIN, "final_url": probe.CANVAS_ORIGIN,
             "headers": {"content_type": "application/json"}, "courses": {}}
    encoded = base64.b64encode(json.dumps(frame, separators=(",", ":")).encode()).decode()

    def fake_popen(_args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        code = f"import sys; sys.stdout.write('ULS_ASIDE_FRAME:{encoded}\\n'); sys.exit(7)"
        return real_popen([sys.executable, "-c", code], **kwargs)

    monkeypatch.setattr(probe.subprocess, "Popen", fake_popen)
    with pytest.raises(probe.ProbeError, match="browser_transport_failed"):
        probe.AsideReplTransport(tab_id="tab", origin=probe.CANVAS_ORIGIN).run(
            [], start_date="2026-09-01", end_date="2026-09-13"
        )


def test_aside_reader_bounds_queued_bytes_with_fast_producer_slow_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: an earlier thread-based reader queued every chunk
    unconditionally and only checked the MAX_ASIDE_OUTPUT_BYTES budget
    after the consumer dequeued it, so a fast producer paired with a
    slow-to-start consumer could pile arbitrarily more than the budget
    into memory before the overflow was ever detected. The reader must
    enforce the per-stream byte budget itself, before queuing, so the
    queue can never hold much more than one budget's worth of chunks
    regardless of how slowly the consumer drains it."""
    payload_size = probe.MAX_ASIDE_OUTPUT_BYTES * 20
    code = f"import sys; sys.stdout.buffer.write(b'x' * {payload_size}); sys.stdout.flush()"
    real_popen = subprocess.Popen

    def fake_popen(_args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        return real_popen([sys.executable, "-c", code], **kwargs)

    monkeypatch.setattr(probe.subprocess, "Popen", fake_popen)

    class _TrackingQueue(probe.queue.Queue):
        max_size_seen = 0
        _delayed_once = False

        def put(self, *args: Any, **kwargs: Any) -> None:
            super().put(*args, **kwargs)
            type(self).max_size_seen = max(type(self).max_size_seen, self.qsize())

        def get(self, *args: Any, **kwargs: Any) -> Any:
            if not type(self)._delayed_once:
                type(self)._delayed_once = True
                # Simulate a consumer that has not started draining yet,
                # matching the review's repro (delay the first dequeue).
                time.sleep(0.3)
            return super().get(*args, **kwargs)

    monkeypatch.setattr(probe.queue, "Queue", _TrackingQueue)
    with pytest.raises(probe.ProbeError, match="browser_output_too_large"):
        probe._run_bounded_aside_repl("synthetic", 5.0)
    chunk_budget = probe.MAX_ASIDE_OUTPUT_BYTES // probe.READ_CHUNK_BYTES + 2
    assert _TrackingQueue.max_size_seen <= chunk_budget, (
        f"queue held {_TrackingQueue.max_size_seen} chunks, more than the "
        f"{chunk_budget}-chunk budget the byte limit should enforce"
    )


def test_aside_reader_error_on_stderr_fails_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: an earlier thread-based reader converted any os.read()
    OSError into the same b"" event as a clean end of stream, so a read
    failure on one stream could be reported as a successful transport
    result whenever the other stream and exit code looked fine."""
    real_popen = subprocess.Popen
    child_holder: dict[str, subprocess.Popen[bytes]] = {}

    def fake_popen(_args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        proc = real_popen([sys.executable, "-c", "import time; time.sleep(2)"], **kwargs)
        child_holder["proc"] = proc
        return proc

    monkeypatch.setattr(probe.subprocess, "Popen", fake_popen)
    real_read = probe.os.read

    def fake_read(fd: int, count: int) -> bytes:
        proc = child_holder.get("proc")
        if proc is not None and proc.stderr is not None and fd == proc.stderr.fileno():
            raise OSError("synthetic stderr read failure")
        return real_read(fd, count)

    monkeypatch.setattr(probe.os, "read", fake_read)
    with pytest.raises(probe.ProbeError, match="browser_transport_unavailable"):
        probe._run_bounded_aside_repl("synthetic", 5.0)
    proc = child_holder["proc"]
    proc.wait(timeout=5)
    assert proc.poll() is not None


def test_aside_reader_error_on_stdout_after_valid_frame_fails_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a read failure after a valid frame was already
    buffered must still fail the whole transport, not return the partial
    result as if the run had completed cleanly."""
    real_popen = subprocess.Popen
    frame = {"version": 1, "provenance": "aside-readonly", "origin": probe.CANVAS_ORIGIN,
             "request_url": probe.CANVAS_ORIGIN, "final_url": probe.CANVAS_ORIGIN,
             "headers": {"content_type": "application/json"}, "courses": {}}
    encoded = base64.b64encode(json.dumps(frame, separators=(",", ":")).encode()).decode()
    child_holder: dict[str, subprocess.Popen[bytes]] = {}

    def fake_popen(_args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        code = (
            "import sys, time; "
            f"sys.stdout.write('ULS_ASIDE_FRAME:{encoded}\\n'); sys.stdout.flush(); "
            "time.sleep(2)"
        )
        proc = real_popen([sys.executable, "-c", code], **kwargs)
        child_holder["proc"] = proc
        return proc

    monkeypatch.setattr(probe.subprocess, "Popen", fake_popen)
    real_read = probe.os.read
    stdout_reads = {"count": 0}

    def fake_read(fd: int, count: int) -> bytes:
        proc = child_holder.get("proc")
        if proc is not None and proc.stdout is not None and fd == proc.stdout.fileno():
            stdout_reads["count"] += 1
            if stdout_reads["count"] >= 2:
                raise OSError("synthetic stdout read failure")
        return real_read(fd, count)

    monkeypatch.setattr(probe.os, "read", fake_read)
    with pytest.raises(probe.ProbeError, match="browser_transport_unavailable"):
        probe._run_bounded_aside_repl("synthetic", 5.0)
    proc = child_holder["proc"]
    proc.wait(timeout=5)
    assert proc.poll() is not None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("due_at", {"raw": "must_not_survive"}),
        ("lock_at", ["must_not_survive"]),
        ("position", True),
        ("published", "true"),
        ("posted_at", {"timestamp": "must_not_survive"}),
        ("module_type", {"raw": "must_not_survive"}),
        ("module_position", "1"),
        ("module_published", 1),
        ("content_id", {"raw": "must_not_survive"}),
    ],
)
def test_aside_frame_rejects_nested_or_wrong_scalar_values(field: str, value: Any) -> None:
    frame: dict[str, Any] = {
        "version": 1, "provenance": "aside-readonly", "origin": probe.CANVAS_ORIGIN,
        "request_url": probe.CANVAS_ORIGIN, "final_url": probe.CANVAS_ORIGIN,
        "headers": {"content_type": "application/json"}, "courses": {"1": {
            "status": "complete", "course": {"id": 1, "name": "Course", "course_code": "C-1", "term": "2026년 2학기"},
            "assignments": [{"id": 1, "name": "a", "due_at": None}],
            "announcements": [{"id": 2, "title": "n", "context_code": "course_1"}],
            "modules": [{"id": 3, "name": "1주차", "position": 1, "items_available": True,
                         "items_unavailable": False, "items_count_available": True,
                         "items_returned_count": 1, "items_complete": True,
                         "items": [{"id": 4, "title": "i"}]}],
        }},
    }
    course = frame["courses"]["1"]
    if field in {"due_at", "lock_at", "position", "published"}:
        course["assignments"][0][field] = value
    elif field == "posted_at":
        course["announcements"][0][field] = value
    elif field == "module_type":
        course["modules"][0]["items"][0]["type"] = value
    elif field == "module_position":
        course["modules"][0]["items"][0]["position"] = value
    elif field == "module_published":
        course["modules"][0]["items"][0]["published"] = value
    else:
        course["modules"][0]["items"][0][field] = value
    with pytest.raises(probe.ProbeError, match="browser_frame_invalid"):
        probe.parse_canonical_frame(frame)


def test_aside_frame_rejects_nonlist_module_items_before_length_check() -> None:
    frame = {"version": 1, "provenance": "aside-readonly", "origin": probe.CANVAS_ORIGIN,
             "request_url": probe.CANVAS_ORIGIN, "final_url": probe.CANVAS_ORIGIN,
             "headers": {"content_type": "application/json"}, "courses": {"1": {
                 "status": "complete", "course": {"id": 1, "name": "Course", "course_code": "C-1", "term": "2026-2"},
                 "assignments": [], "announcements": [], "modules": [{
                     "id": 3, "name": "1주차", "position": 1, "items_available": True,
                     "items_unavailable": False, "items_count_available": True,
                     "items_returned_count": 0, "items_complete": True, "items": None,
                 }],
             }}}
    with pytest.raises(probe.ProbeError, match="browser_frame_invalid"):
        probe.parse_canonical_frame(frame)


def test_module_item_cap_is_explicitly_incomplete() -> None:
    module_record = {
        "id": 40,
        "name": "Large synthetic module",
        "items": [{"id": index, "title": f"Item {index}"} for index in range(probe.MAX_ITEMS + 1)],
    }
    responses = [*complete_responses(), FakeResponse([module_record])]
    status, result, _error, _opener = run_probe(
        responses, [*BASE_ARGS, "--include-modules"]
    )

    assert status == 3
    assert result["incomplete_reasons"] == {"modules": "module_items_item_cap"}
    assert result["modules"][0]["items_complete"] is False
    assert result["modules"][0]["items_error"] == "item_cap"
    assert result["modules"][0]["items_returned_count"] == probe.MAX_ITEMS


def test_json_body_with_html_content_type_is_refused() -> None:
    status, result, error, _opener = run_probe(
        [FakeResponse(course_response(), headers={"Content-Type": "text/html"})]
    )

    assert status == 2
    assert result == {"status": "failed", "error": "non_json_content_type"}
    assert "SYNTHETIC_SECRET" not in json.dumps(result) + error


@pytest.mark.parametrize("bad_token", ["SYNTHETIC_SECRET\r\nX-Leak: 1", "SYNTHETIC_SECRET-é", " SYNTHETIC_SECRET"])
def test_malformed_token_is_rejected_before_header_creation(bad_token: str) -> None:
    output = io.StringIO()
    error = io.StringIO()
    opener = QueueOpener([])
    status = probe.main(
        BASE_ARGS,
        opener=opener,
        prompt=lambda _prompt: bad_token,
        stdin=FakeStdin(True),
        output=output,
        error=error,
    )

    assert status == 2
    assert json.loads(output.getvalue()) == {"status": "failed", "error": "credential_invalid"}
    assert opener.calls == []
    assert bad_token not in output.getvalue() + error.getvalue()


def test_unexpected_transport_error_is_sanitized() -> None:
    class ExplodingOpener:
        def open(self, _request: Any, timeout: float) -> Any:
            raise ValueError(f"raw transport detail {TOKEN}")

    output = io.StringIO()
    error = io.StringIO()
    status = probe.main(
        BASE_ARGS,
        opener=ExplodingOpener(),
        prompt=lambda _prompt: TOKEN,
        stdin=FakeStdin(True),
        output=output,
        error=error,
    )

    assert status == 2
    assert json.loads(output.getvalue()) == {"status": "failed", "error": "unexpected_error"}
    assert TOKEN not in output.getvalue() + error.getvalue()


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_json_constants_are_rejected_without_raw_output(constant: str) -> None:
    body = (
        '{"id":12345,"name":"Synthetic Course","course_code":"SYN-001",'
        f'"term":{{"name":"Synthetic Term"}},"bad":{constant}}}'
    ).encode()
    status, result, error, _opener = run_probe([FakeResponse(body)])

    assert status == 2
    assert result == {"status": "failed", "error": "nonfinite_json"}
    assert constant not in json.dumps(result) + error


def test_nonfinite_float_metadata_raises_fixed_error() -> None:
    with pytest.raises(probe.ProbeError) as file_error:
        probe.sanitize_file({"id": 30, "display_name": "Synthetic.pdf", "size": float("inf")})
    assert file_error.value.code == "nonfinite_scalar"

    with pytest.raises(probe.ProbeError) as assignment_error:
        probe.sanitize_assignment({"id": 31, "name": "Synthetic", "position": float("-inf")})
    assert assignment_error.value.code == "nonfinite_scalar"


def test_json_output_rejects_nonfinite_without_partial_output() -> None:
    output = io.StringIO()

    with pytest.raises(ValueError):
        probe._write_json(output, {"value": float("nan")})

    assert output.getvalue() == ""


def test_raw_nonfinite_assignment_number_is_rejected_before_sanitization() -> None:
    assignment_body = b'[{"id":10,"name":"Synthetic","position":1e400}]'
    status, result, error, opener = run_probe(
        [FakeResponse(course_response()), FakeResponse(assignment_body)]
    )

    assert status == 2
    assert result == {"status": "failed", "error": "nonfinite_json"}
    assert len(opener.calls) == 2
    assert "1e400" not in json.dumps(result) + error


def http_failure(
    status: Any, *, raised: bool, headers: dict[str, str] | None = None
) -> FakeResponse | HTTPError:
    """Synthetic error sources whose bodies must never be consumed."""
    class UnreadableBody(io.BytesIO):
        def read(self, size: int | None = -1, /) -> bytes:
            pytest.fail("HTTP error body must not be read")

    class UnreadableResponse(FakeResponse):
        def read(self, size: int = -1) -> bytes:
            pytest.fail("HTTP error body must not be read")

    secret = f"REMOTE_DETAIL_{TOKEN}"
    if raised:
        message = Message()
        for name, value in (headers or {}).items():
            message[name] = value
        return HTTPError(
            f"https://untrusted.invalid/{secret}",
            status,
            secret,
            message,
            UnreadableBody(secret.encode()),
        )
    return UnreadableResponse(secret.encode(), status=status, headers=headers)


@pytest.mark.parametrize("raised", [False, True], ids=["returned", "raised"])
@pytest.mark.parametrize("http_status", [100, 401, 403, 404, 429, 500, 503, 599])
def test_http_course_failure_has_only_safe_diagnostics(http_status: int, raised: bool) -> None:
    failure = http_failure(http_status, raised=raised)
    status, result, error, opener = run_probe([failure, *complete_responses()])

    assert status == 2
    assert result == {
        "status": "failed",
        "error": "http_error",
        "http_status": http_status,
        "resource": "course",
        "auth_challenge_present": False,
    }
    assert error == (
        f"probe_failed=http_error http_status={http_status} "
        "resource=course auth_challenge_present=false\n"
    )
    assert len(opener.calls) == 1
    assert TOKEN not in json.dumps(result) + error
    assert "untrusted.invalid" not in json.dumps(result) + error
    if isinstance(failure, FakeResponse):
        assert failure.closed
    else:
        assert failure.fp is not None and failure.fp.closed


@pytest.mark.parametrize("raised", [False, True], ids=["returned", "raised"])
@pytest.mark.parametrize("bad_status", [True, False, "401", TOKEN, 99, 600, None, 401.0])
def test_http_status_validation_fails_without_echo(bad_status: Any, raised: bool) -> None:
    status, result, error, opener = run_probe(
        [http_failure(bad_status, raised=raised), *complete_responses()]
    )

    assert status == 2
    assert result == {"status": "failed", "error": "invalid_http_status"}
    assert error == "probe_failed=invalid_http_status\n"
    assert len(opener.calls) == 1


@pytest.mark.parametrize("raised", [False, True], ids=["returned", "raised"])
@pytest.mark.parametrize(
    ("headers", "present"),
    [
        ({}, False),
        ({"X-WWW-Authenticate": TOKEN}, False),
        ({"WWW-Authenticate": ""}, True),
        ({"www-authenticate": TOKEN}, True),
        ({"wWw-AuThEnTiCaTe": f'Bearer realm="{TOKEN}"'}, True),
    ],
)
def test_auth_challenge_is_only_case_insensitive_header_presence(
    headers: dict[str, str], present: bool, raised: bool
) -> None:
    status, result, error, _opener = run_probe(
        [http_failure(401, raised=raised, headers=headers)]
    )

    assert status == 2
    assert result["auth_challenge_present"] is present
    assert error == (
        "probe_failed=http_error http_status=401 resource=course "
        f"auth_challenge_present={str(present).lower()}\n"
    )
    assert TOKEN not in json.dumps(result) + error
    assert "Bearer" not in json.dumps(result) + error


@pytest.mark.parametrize("raised", [False, True], ids=["returned", "raised"])
@pytest.mark.parametrize("failed_resource", ["assignments", "announcements", "files", "modules"])
def test_later_http_failure_preserves_only_completed_verified_resources(
    failed_resource: str, raised: bool
) -> None:
    resource_order = ["course", "assignments", "announcements", "files", "modules"]
    failure_index = resource_order.index(failed_resource)
    args = [*BASE_ARGS, "--include-files", "--include-modules"]
    _, completed, _, _ = run_probe(
        [*complete_responses(), FakeResponse([]), FakeResponse([])], args
    )
    success = [*complete_responses(), FakeResponse([]), FakeResponse([])]
    responses = [
        *success[:failure_index],
        http_failure(403, raised=raised, headers={"WWW-Authenticate": TOKEN}),
        *success[failure_index + 1:],
    ]
    status, result, error, opener = run_probe(responses, args)

    assert status == 3
    assert result == {
        **{name: completed[name] for name in resource_order[:failure_index]},
        "status": "incomplete",
        "error": "http_error",
        "http_status": 403,
        "resource": failed_resource,
        "auth_challenge_present": True,
        "incomplete_resources": [failed_resource],
        "incomplete_reasons": {failed_resource: "http_error"},
    }
    assert len(opener.calls) == failure_index + 1
    assert error == (
        f"probe_incomplete=http_error http_status=403 resource={failed_resource} "
        "auth_challenge_present=true\n"
    )
    assert TOKEN not in json.dumps(result) + error


@pytest.mark.parametrize("raised", [False, True], ids=["returned", "raised"])
def test_http_failure_discards_partial_pages_and_stops(raised: bool) -> None:
    next_url = (
        f"{probe.CANVAS_ORIGIN}/api/v1/courses/12345/assignments"
        "?order_by=name&per_page=50&page=2"
    )
    first_page = FakeResponse(
        [{"id": 10, "name": "Partial synthetic assignment"}],
        headers={"Link": f'<{next_url}>; rel="next"'},
    )
    status, result, error, opener = run_probe([
        FakeResponse(course_response()), first_page,
        http_failure(429, raised=raised), FakeResponse([]),
    ])

    assert status == 3
    assert result["course"]["course_code"] == "SYN-001"
    assert result["resource"] == "assignments"
    assert result["incomplete_reasons"] == {"assignments": "http_error"}
    assert "assignments" not in result and "announcements" not in result
    assert "Partial synthetic assignment" not in json.dumps(result) + error
    assert "http_status=429" in error
    assert len(opener.calls) == 3


def test_http_resource_diagnostic_rejects_nonlocal_stage() -> None:
    with pytest.raises(probe.ProbeError) as failure:
        probe.HttpProbeError(401, {}).diagnostics(f"https://untrusted.invalid/{TOKEN}")

    assert failure.value.code == "invalid_resource"
    assert TOKEN not in str(failure.value)
