# KNU LMS connection validation — application evidence

Date: 2026-09-13. This document is sanitized for independent review. All URLs below are synthetic substitutions for actual connector readback; no raw private IDs, PDF bytes, contacts, tokens, or LMS bodies are included. Actual IDs/checks are in the VCS-ignored private receipt.

## Applied and checked

- After web Latest/Very high revision2 GO and Gemini3.8Flash/high revision2 GO, created Drive School/2026-2/종합설계프로젝트1 (002)/강의자료 and uploaded exactly one already downloaded PDF.
- Every destination parent and new folder/file was read back as expected parent/MIME, shared=false, canShare=true, no shared drive, and one user/owner permission only. No permissions changed.
- Uploaded PDF:480043 bytes. Downloaded original raw bytes back through Drive connector. Local original==readback bytes assertion passed; SHA-256 identical. No reliance on filename/size alone.
- Created Notion2026-2 using documented private-root creation contract; private-root complete listing contains exactly one new root, shared-page list empty. Full ACL not exposed and not claimed.
- Created one nested course page with exact semester/section/LMS source binding, then moved its native child block into 내 과목. Fetched exact parent and both content trees after structural update. No session/user progress/verified approval values created.
- Second reception of the same input: fresh Drive file metadata/name+parent search, course folder search, Notion root/course reads. Exactly one file/course folder/native child; ID/parent/source/section/term match; modified timestamp unchanged from byte readback. Decision=no-op. Additional cloud writes=0. This is a bounded manual replay observation, not automatic worker crash/concurrency proof.
- Aside native UI action was interrupted by user control; did not continue fighting the user's interaction. Separate IAB tab for Notion reached an explicit login wall and was closed. Therefore no final visual rendering/click-through claim. Connector readback of hierarchy/content/links is complete. User can open delivered Notion links in their signed-in browser.
- API helper has been implemented separately and targeted final-review fixes are complete. No API token created; no authenticated live API request; no recurring job or full LMS sync configured. Remaining live API gate is at-action user approval for broad possible token access plus local hidden entry.
- Current token modal was opened successfully after one automatic approval-review timeout and a permitted retry. The purpose field is prepared as `ULS 1회 API 연결 검증`; expiration input is `2026-09-14 00:00` and the UI resolved it to `2026년 9월 14일 (월)`. Account timezone observed as Seoul. `토큰 생성` remains unclicked. The dialog exposes purpose/expiration and no read-only/per-course scope selector. If execution is delayed beyond this short expiry, re-propose a future expiry at action time rather than silently extending access.
- Live identity prerequisite: expected numeric course ID, full displayed course name including section, and semester are UI-grounded. At the first final-review submission the exact `course_code` expectation was still unresolved; the later read-only mapping check below supplies an evidence-based expectation. This is not an authenticated API success claim, and matching remains strict.

## Final-review evidence correction and later read-only checks

- The final web reviewer requested explicit second-input identity/hash proof. Root performed this missing check without any cloud write: recomputed SHA-256 directly from the second local input; independently constructed the observed LMS origin/course ID/file ID tuple; asserted every tuple field plus hash equals the private receipt, and asserted the exact recorded Drive file ID and Notion course ID. All assertions passed. The hash matched the original uploaded/read-back PDF.
- Immediately following the rehash, root freshly fetched Drive file metadata and exact name+parent candidates, and Notion semester/course content. One file and one native course child remained; exact remote IDs/parents, semester/section/LMS source binding, MIME/size and unchanged file modified timestamp matched the receipt. Repeated decision=no-op, additional cloud writes=0. Name search was only candidate discovery; canonical identity plus local hash plus receipt remote ID was required for acceptance.
- Later API identity expectation preparation: observed the course breadcrumb, full heading, card, and semester. Opened that course's nickname dialog, read the nickname textbox as exactly empty, then canceled with no setting change. Official Canvas code maps the breadcrumb to nickname_for(user, :short_name), and Course.short_name aliases course_code. This provides a UI-grounded inference for the expected code; it is still not an API-verified value, and a customized deployment mismatch must fail before resource calls. A launch file with only these non-secret expected values was prepared locally but not executed.
- Sources for the field mapping (primary implementation, fetched through official-doc/GitHub fallback): [Canvas breadcrumb implementation](https://raw.githubusercontent.com/instructure/canvas-lms/master/app/controllers/application_controller.rb), [Canvas Course short_name alias](https://raw.githubusercontent.com/instructure/canvas-lms/master/app/models/course.rb), [Canvas Courses API](https://canvas.instructure.com/doc/api/courses.html). The school deployment's actual API response remains untested.

## Actual Notion root readback (URLs replaced)

```text
<callout icon="🔎">
	강의자료 1개를 수동으로 연결했습니다. 다른 과목·과제·일정 수집과 자동 갱신은 아직 연결되지 않았습니다.
</callout>
## 내 과목
<page url="https://notion.example/course">종합설계프로젝트1 (002)</page>
## 이어서 공부
아직 학습 세션을 연결하지 않았습니다. 연결 후 최근 학습을 최대 3개까지 표시합니다.
## To DO
과제 수집 전입니다. 현재 해야 할 일이 없다는 뜻은 아닙니다.
## 캘린더
학사일정·과제 마감일·시험일정 수집 전입니다.
## 파일 확인
<mention-page url="https://notion.example/course">종합설계프로젝트1 (002)</mention-page> · 강의자료 1개 연결
[2026F_CDP1(OT).pdf — Drive에서 열기](https://drive.example/file)
2026-09-13 수집 확인 · 원본 파일과 바이트 일치
```

## Actual Notion course readback (URLs replaced)

```text
2026년 2학기 · 002분반
[학교 LMS 과목 열기](https://canvas.knu.ac.kr/courses/123)
## 강의자료
[2026F_CDP1(OT).pdf](https://drive.example/file)
- 분류: 강의자료 · OT
- 출처: [LMS 원본 공지](https://canvas.knu.ac.kr/courses/123/discussion_topics/456)
- 수집 확인일: 2026-09-13
- 원본과 Drive 저장 파일의 바이트 일치를 확인했습니다.
## 학습 세션
아직 학습 세션을 연결하지 않았습니다. 실제 강의 날짜와 자료 사용 범위는 확인 전입니다.
<callout icon="ℹ️">
	이 자료는 LMS 연결 검증으로 수동 등록했습니다. 자동 갱신은 아직 연결되지 않았습니다.
</callout>
```

## API candidate checks

- Continuous Luna/high implementer completed helper and unit tests. Reported focused results:30 unit tests passed, Ruff passed, strict mypy passed. Tests include query/announcement context drift, nested module incomplete cases, noecho failure and malformed token/transport secrecy, JSON content type, caps/redirects and course mismatch. No live requests or actual token were used.
- After the two final-review code fixes, the same implementer ran `.review/venv311/bin/pytest -q -m unit tests/unit/test_knu_lms_probe.py` (37 passed), `.review/venv311/bin/ruff check scripts/knu_lms_probe.py tests/unit/test_knu_lms_probe.py` (all checks passed), and `.review/venv311/bin/mypy --strict scripts/knu_lms_probe.py tests/unit/test_knu_lms_probe.py` (no issues in2sourcefiles). Missing module counts now mark incomplete; NaN/Infinity/-Infinity/1e400 input, nonfinite scalar values and nonfinite output are rejected; output serialization happens before any stdout write.
- Root added actual-process smoke checks with non-TTY stdin: CLI exits2 with credential_tty_required before prompting/network; unknown `--token` plus synthetic secret exits2 without echo. Both passed. These supplement implementer unit evidence rather than rerunning the same suite.
- This helper is stdlib-only and imported directly by its test; pyproject.toml supplies unit marker/test/style/type configuration. There is no tests/conftest.py or tests/unit/conftest.py dependency. The final review packet includes full helper, full test, pyproject, complete plan, and this sanitized evidence.

## Review/acceptance boundary

External application can be accepted for connector hierarchy/link/privacy checks and exact PDF bytes. Visual polish validation remains unverified because of browser login/control limitations, and must not be reported as complete. API preparation candidate acceptance will require code/tests/final reviews; actual API success requires a later authenticated run. No commit/push is requested or performed.

## HTTP diagnostic implementation evidence — 2026-09-13

Root accepted the bounded diagnostic plan after the independent web Latest/Very high GO in `.insane-review/response_prompt_20260913_154105_23101_b086af.md` and explicitly assigned Gemini 3.8 Flash/high GO in `.review/knu-lms-http-diagnostics-gemini-plan-verified.md`. The continuous Luna/high implementer then changed only `scripts/knu_lms_probe.py`, `tests/unit/test_knu_lms_probe.py`, and narrow addenda in this evidence file and `docs/plans/knu-lms-connection-validation.md` for this bundle. This is implementation/self-test evidence, pending independent final reviews and root acceptance.

Actual focused commands, run from the repository with the existing Python 3.11 environment:

```text
.review/venv311/bin/python -m pytest -q -m unit tests/unit/test_knu_lms_probe.py
90 passed in 0.09s
.review/venv311/bin/python -m ruff check scripts/knu_lms_probe.py tests/unit/test_knu_lms_probe.py
All checks passed!
.review/venv311/bin/python -m mypy --strict scripts/knu_lms_probe.py tests/unit/test_knu_lms_probe.py
Success: no issues found in 2 source files
```

The 90 total cases include the prior 37 regressions. New synthetic cases cover both returned non-2xx and raised `HTTPError` paths for 100, 401, 403, 404, 429, 500, 503, and 599; rejection of booleans, strings, null, floats, and out-of-range status codes; absent, mixed-case, empty, and secret-bearing challenge headers; and fixed failures for nonlocal resource names. Error-body readers fail the test if invoked. Malicious synthetic URL/reason/body/header text stays out of JSON and stderr. Course failure stops before child calls; failures at each later local resource preserve only verified course/completed preceding lists; a failure on page 2 discards the failed resource's first page and stops all further requests. Exact JSON/stderr assertions verify consistent diagnostics and nonzero exits, with HTTP-induced incompleteness reported as `http_error`.

No live requests, actual credentials, browser data, secret files, new dependencies, launcher/system trust edits, external writes, or commit/push were used for this correction. Live API success remains unverified; this synthetic evidence does not authorize a retry or establish the cause of the user's earlier HTTP failure.
