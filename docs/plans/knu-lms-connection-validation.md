# KNU LMS connection validation plan

Plan accepted after web/Gemini revision2 GO. Targeted final-review fixes complete;37unit tests and strict checks passed. Authenticated live API and final visual verification remain pending.

# KNU LMS 다음 검증 — 외부 적용 계획

## 범위와 담당

사용자가 실제 LMS 로그인/파일 다운로드 확인에 이어 다음 검증을 요청했다. 이번 묶음은 별도 API 조회 도구 준비와, 이미 정상 다운로드한 강의자료 1개를 실제 Drive/Notion에 연결하고 재처리 중복 여부를 확인하는 것이다. 주기적 전체 동기화나 ULS 제품 어댑터 구현 완료를 뜻하지 않는다.

- 루트 담당: 외부 데이터 대상 결정, live connector 실행/검증, 독립 리뷰 통합/최종 수용.
- API 도구 담당: Luna high 전용 구현자. GET 전용 제한된 검증 도구에 적합하여 배정. 계획 검토 후 같은 구현자가 코드와 자체 검증을 수행한다.
- 위험: 신규 인증 접근, 개인정보/원문 보관, 잘못된 학기 연결. 새 토큰 발급은 별도 명시적 승인 전 수행하지 않는다. 기존 토큰/쿠키/비밀번호 추출 금지.
- 웹 ChatGPT 매우 높음(사용자가 승인한 Pro 쿼터 소진 대체) 및 독립 Gemini high의 계획/최종 리뷰를 수행. 웹 리뷰 입력에는 개인 LMS 원문·이름·명단·연락처·비밀값·실제 리소스 ID를 넣지 않는다.

## 실제 발견한 저장 위치

- Notion private root 목록과 학기/과목 검색에서 이번 2학기 대상이 확인되지 않았다. 기존 1학기 페이지는 top-level private page이며 기존 내용은 보존한다.
- Drive의 기존 School 폴더는 소유자만 접근하는 비공개 폴더다. 직접 하위에는 이번 2학기 폴더가 없고 기존 1학기와 여름 폴더만 있다.
- 따라서 Drive `School/2026-2/종합설계프로젝트1 (002)/강의자료`와 Notion private `2026-2/종합설계프로젝트1 (002)`에 실제 자료 1개를 연결한다. 이름과 parent 조회는 후보 탐색일 뿐 신원 검증이 아니다. 기존 과목 객체는 현재 receipt의 원격 ID, parent, 학교 origin/course ID, 학기, 분반 binding과 readback이 일치할 때만 재사용한다. Notion에는 학기/분반과 검증된 LMS 과목 링크를 표시한다. Drive는 신규 생성 시점의 exact parent/name 및 private readback을 receipt의 course binding과 함께 기록한다. binding 없는 기존 동명 객체, 둘 이상의 후보, 신원 불일치는 쓰기 중단한다. School의 ID는 이번 직접 조회로 확인한 기존 소유자 전용 School에 고정한다. 추후 폴더/페이지 이름 변경에도 이번에 발급받은 ID 매핑을 유지한다.

## 이번 실제 데이터

- 2학기 종합설계프로젝트1 002분반의 OT PDF 1개. LMS 공지 첨부의 정상 다운로드 UI로 받은 원본이며 로컬에서 PDF v1.7 / 15 pages / SHA-256을 확인했다.
- course ID와 file ID는 추정값이 아니다. 실제로 열람한 공지 URL의 `/courses/{course_id}/discussion_topics/{topic_id}` 및 클릭한 첨부 링크 `/courses/{course_id}/files/{file_id}/download?wrap=1`에서 확인했고, 같은 과목 제목/002분반 화면과 대조했다. 실제 ID와 URL은 로컬 비공개 receipt에만 기록하며 리뷰 입력에서는 치환한다. 이는 UI 링크 기반 식별 근거이며 API 응답으로 재검증했다는 뜻이 아니다.
- 외부 표시에 파일명, 과목/학기/분반, 원본 공지 링크, Drive 원본 링크, 수집 확인일을 보관한다. 실제 강의 날짜·자료 사용 범위는 알 수 없으므로 추정하지 않는다.
- 파일명이나 공지 제목에 포함된 OT를 특정 Session으로 해석하지 않는다. Material Usage verified, 시험 범위 확인, USER 공부 상태를 설정하지 않는다.
- PDF 전체는 사용자의 기존 비공개 Drive에만 업로드. 공지의 팀 명단·연락처·댓글이나 다른 과목의 성적·제출물은 보관/전송하지 않는다. 독립 웹/Gemini 리뷰에는 PDF 원문을 제공하지 않는다.

## 최소 Notion 화면

새 학기 페이지는 기존 사용자 순서인 내 과목 → 이어서 공부(최대3) → To DO → 캘린더 → 파일 확인을 유지한다. 이번 파일의 대상 과목 1개만 native 하위 페이지로 만든다. 다른 과목을 동기화 완료로 표시하지 않는다.

- 내 과목: 이번 검증으로 연결한 과목 하위 페이지.
- 이어서 공부: 실제 학습 세션이 연결되지 않았으므로 빈 상태 설명. 다운로드를 최근 학습으로 간주하지 않는다.
- To DO / 캘린더: 이번 검증에는 과제/확정 일정 입력이 없음을 짧게 알린다. 가짜 일정/DB/과제는 만들지 않는다. 기존 1학기 DB를 재사용하지 않는다.
- 파일 확인: 연결한 원본 자료의 과목 페이지/파일 기록으로 접근. 이번 등록은 수동 실행한 연결 검증이며 자동 갱신 연결 전이라고 명확히 표시.
- 과목 페이지: LMS 과목 링크, 강의자료 목록(파일명·Drive·LMS 원문), 실제 차시/활용 범위는 확인 전. 사용자 개인 메모를 자동으로 쓰거나 덮어쓰지 않는다.

## 적용과 재처리 검증

1. fresh private parent/동일 이름 후보 조회. 신규 파일은 등록 직전 해시를 다시 계산한다.
2. 단계별 remote 생성 응답의 실제 ID와 parent를 `.review/knu-lms-live-receipts.json`에 즉시 기록한다. receipt는 VCS/review pack 비포함이며 token/원문 body를 기록하지 않는다. 각 객체를 `absent` / `exact verified one` / `ambiguous`로 독립 판정한다. 검증된 객체는 재사용하고 absent인 단계만 실행하여 Drive 성공/Notion 실패 같은 부분 성공을 이어간다. 응답 유실은 무조건 재시도하지 않고 원격 재조회하여 찾으며, 재조회는 추가 생성의 근거가 될 정확한 identity/receipt가 없으면 ambiguous로 중단한다. 기존 내용이 사용자에 의해 변경되면 자동 덮어쓰기하지 않는다.
3. PDF 업로드 후 MIME/크기/parent/비공개 상태를 원격 metadata로 확인하고, 가능한 파일 readback을 통해 로컬 바이트 해시를 대조한다. 지원 도구가 바이트를 돌려주지 못하면 검사한 항목만 보고하며 바이트 동일성 검증 완료라고 주장하지 않는다.
4. Notion에 기록한 원본 링크와 Drive ID, 과목/학기/분반을 재조회해 정확히 일치하는지 확인한다. 새 page/schema와 기존 USER 영역을 혼합하지 않는다.
5. 두 번째 접수 판정은 canonical source identity(학교 origin, course ID, file ID, 로컬 원본 hash)와 receipt의 remote ID를 기준으로 조회한다. 동일 원격 파일/페이지가 정확히 한 개이고 binding/readback이 일치하면 no-op으로 종료하며 새 파일/페이지를 만들지 않는다. filename 단독 중복 판정 금지. 원본 변경/매핑 불일치/후보 중복/조회 실패면 conflict로 중단한다.
6. 이 판정은 이번 단발 connector 실행에 대한 실증으로 보고한다. 재시작/경합/응답 유실 자동 복구나 상시 sync worker의 정확성을 증명한 것으로 확대하지 않는다.

### 링크 및 비공개 확인의 구체 조건

- Notion에 저장할 LMS 링크는 쓰기 전에 따로 검증한다: 정확히 `https://canvas.knu.ac.kr`, user-info/port/fragment 없음, 저장 query 없음, receipt와 일치하는 course path 및 course/announcement/file resource ID. SSO·리다이렉트·세션/토큰 query·LearningX iframe 주소는 저장하지 않는다. 첨부의 `wrap=1`은 링크 관찰 근거에만 남기며 표시용 파일 링크는 query 없는 동일 원본 경로를 쓴다. Drive/Notion 링크도 실제 생성 응답의 canonical URL/ID에 고정한다.
- Drive: School 및 새 부모 폴더의 metadata에서 예상 ID/parent/MIME, `shared=false`, `current_user_can_share=true`, `permissions`가 정확히 user/owner 한 개임을 확인한다. 공유 드라이브·anyone/domain/group·추가 사용자 권한 또는 metadata 불명확이면 해당 쓰기를 중단한다. 폴더와 업로드 파일 모두 생성 후 같은 조건을 readback한다. 소유권/공유 변경은 하지 않는다.
- Notion: parent 생략 생성은 connector 명세상 workspace-level private page를 만든다. 생성 직전 complete private-root 목록에서 해당 새 학기가 없음을 확인하고, 생성 뒤 root가 private-page 목록에 있고 shared-page 목록에는 없음을 확인한다. course는 그 root의 exact parent로 생성하며 fetched path/ancestor 및 contents binding을 대조한다. 이 connector가 완전한 ACL 목록을 노출하지 않으므로 `ACL 전체 검증`이라고 주장하지 않는다. 생성 계약·private 목록 분류·부모 연결까지만 검증 사실로 기록하며 공유 확대 작업은 하지 않는다. private/shared 분류나 부모가 모호하거나 예상과 다르면 추가 적용을 중단한다.

## 승인 경계와 남는 작업

- 기존 승인은 실제 LMS 수집 테스트와 비공개 Notion/Drive 연결 범위에 적용한다. 외부 게시, 공유 확대, LMS 게시/과제 제출/출석 조작, 전체 계정 대량 동기화, scheduler 등록은 이번 실행에 포함하지 않는다.
- API token 생성은 새 지속적 접근 권한이며 현재 승인되지 않았다. 검증 도구/리뷰/테스트를 먼저 완료한 후 정확한 유효기간·실제 권한범위·로컬 입력 방식을 보여주고 발급 직전 사용자 승인을 받는다. 권한이 읽기 전용으로 제한된다고 허위 설명하지 않는다.
- Aside CLI 오류 및 IAB의 API 직접 열기 차단은 계속 미해결이다. 별도로 사용자가 승인한 API 자격증명으로 공식 endpoint를 읽는 검증을 준비하며 브라우저 저장 인증정보를 우회 추출하지 않는다.


---

# KNU LMS API probe plan

Date: 2026-09-13  
Stage: implementation complete; focused checks pass; awaiting independent final review  
Owner: continuous worker for design, implementation, self-tests, and related fixes  
Model/effort: gpt-5.6-luna / high — directly assigned for a small but security-sensitive GET-only HTTP helper with strict credential and redirect boundaries.

## Scope and acceptance

Add one standalone, one-shot helper at `scripts/knu_lms_probe.py` and bounded unit tests at `tests/unit/test_knu_lms_probe.py`. A scoped README addition is allowed only if local usage needs documentation. This is a verification helper, not a ULS adapter, frozen-contract change, scheduler, importer, Notion/Drive writer, or downloader.

The accepted live result will establish whether a separately user-approved Canvas token can read one explicitly selected target course and return selected metadata for:

- the matched course;
- assignments, retaining `due_at: null` when Canvas has no due date;
- announcements; and
- optionally, bounded file metadata and module/module-item metadata.

The command emits sanitized JSON to stdout and a short status/error to stderr. It never emits grades, submissions, user data, assignment or announcement bodies, file/module content, download bytes, raw exception bodies, or credentials. It performs no Notion/Drive calls or cloud writes.

## Proposed interface and request boundary

Use Python 3.11+ standard library only (`argparse`, `getpass`, `json`, `time`, `urllib`, and related standard modules). The school origin is a constant:

`https://canvas.knu.ac.kr`

There is no `--token` option, token environment variable, token file, browser-cookie path, OAuth client secret, or CLI credential handoff. The non-secret course selector is supplied as a validated positive integer argument (synthetic IDs are used in tests); the expected course name, section-distinguishing course code, term, announcement start date, and announcement end date are all required arguments. Before prompting, the command requires `sys.stdin.isatty()`. If stdin is not a TTY, it exits with an explicit safe error and does not call an echoing input fallback. Otherwise it prompts with `getpass.getpass()` in the local terminal for one access token. The token is held only in process memory for the run, used only in an `Authorization: Bearer ...` header, and discarded in a `finally` path after the requests complete. The implementation will avoid claiming that string-memory clearing is guaranteed by Python. No keychain integration is proposed for this one-shot probe: it would extend credential lifetime and add platform-specific behavior without being needed for the requested validation. A future repeated sync would require a separate storage and approval decision.

The parser will use a custom sanitized `ArgumentParser.error()` that emits a fixed argument-error category and never includes the rejected argument text. Unknown options and values, including a supplied `--token` value, therefore fail nonzero without echoing a possible secret. Date arguments are parsed as bounded ISO dates, require `start <= end`, and are limited to a maximum inclusive window of 31 calendar days.

The request layer will construct URLs from fixed, validated API paths and use GET only. It will reject non-HTTPS, non-KNU-origin, user-info, fragments, unexpected paths, and query parameters that are not locally constructed. It will not send a token in a URL. Redirects will be refused by a custom redirect handler, so an SSO, HTML, or cross-origin redirect cannot receive the bearer header. Pagination links from Canvas will be accepted only when they are HTTPS, same-origin, and on the exact expected API path. Their query multimap must equal the immutable locally constructed query plus exactly one locally expected next-page cursor; unknown, duplicate, or changed context/date/filter/page values are rejected, and the next request URL is reconstructed locally. Otherwise the probe fails safely without following the server-supplied URL.

The run has a 45-second monotonic deadline and a 10-second per-socket-operation timeout ceiling. Before each connection, the remaining run time is calculated and passed as `min(10 seconds, remaining)`, with no request started when the remaining budget is exhausted. Reads are chunked and recheck the deadline and byte cap. There will be no automatic retry. The implementation will document that `urllib`'s timeout bounds an individual socket operation rather than guaranteeing a whole transaction; the monotonic deadline is checked before open and during reads, and any observed overrun is reported as incomplete. Successful responses must declare the `application/json` media type. JSON parsing rejects `NaN`, `Infinity`, `-Infinity`, and overflowed numeric literals such as `1e400`; direct sanitizer inputs with nonfinite floats raise a fixed error, and output is pre-serialized with `allow_nan=False` before it reaches stdout. JSON responses are capped at 2 MiB before parsing; each resource is capped at 3 pages and 500 collected items. `per_page` will be bounded (planned value: 50). A malformed, oversized, non-JSON, redirected, or out-of-budget response produces a generic sanitized failure. Unexpected transport/parsing exceptions at the CLI boundary produce only a fixed error category.

## Planned GET calls

1. `GET /api/v1/courses/{course_id}?include[]=term` to identify the explicitly selected course directly. The official Courses API documents `include[]=term` as the switch that returns the enrollment term object. The response must contain non-null `id`, `name`, `course_code`, and `term.name`; missing identity fields fail closed before any resource call. Matching uses normalized exact comparisons for the requested numeric ID, required expected name, required section-distinguishing expected course code, and required expected term. A mismatch stops before resource calls. The output must retain only these safe identity fields, enough to distinguish the selected term/course from another similarly named course.
2. `GET /api/v1/courses/{course_id}/assignments?order_by=name&per_page=50` without `include[]=submission` or other submission/statistics options. Keep only assignment ID, name, due date, lock/unlock dates when present, and safe status/position metadata. Do not infer a missing due date.
3. `GET /api/v1/announcements?context_codes[]=course_{course_id}&start_date={start_date}&end_date={end_date}&per_page=50` using the required, validated, maximum-31-day date arguments. No Canvas default date window is permitted. Keep announcement ID, title, posted/published timestamp, delayed-post timestamp when present, and context code. Do not copy the `message` body or follow attachment URLs.
4. With `--include-files`, `GET /api/v1/courses/{course_id}/files?per_page=50` and keep file ID, display name/name, size, content type, folder ID, and created/modified timestamps where present. Omit all URLs and never call the file download endpoint.
5. With `--include-modules`, `GET /api/v1/courses/{course_id}/modules?include[]=items&per_page=50`. Keep module ID/name/position/published/state/lock metadata and, only when inline items are returned, item ID/title/type/position/published content ID. Include explicit `items_available`, `items_complete`, `items_unavailable`, returned/expected counts, and a fixed error category when inline items are omitted, invalid, truncated, or inconsistent with `items_count`; any such condition makes the resource and overall probe incomplete with a nonzero exit. Do not request or follow item HTML, external-tool URLs, file URLs, or mark-read/progress endpoints. If Canvas omits inline items, report that metadata is unavailable within the bounded probe rather than making an unbounded per-module crawl.

The resource calls will use Canvas `Link` pagination only within the caps. If a page cap is reached while a next link remains, or the run deadline expires before a resource's pagination is complete, that resource is marked incomplete and the overall JSON result is `status: "incomplete"` with a nonzero exit code. It must never be reported as a successful complete probe. The sanitizer will use allowlisted fields rather than copying arbitrary JSON, including omission of `description`, `message`, `html_url`, `url`, `download_url`, submissions, grades, and user objects.

## Test plan

Unit tests use a fake opener/response and never contact KNU, Notion, Drive, a browser, or a keychain. They will cover:

- `getpass` credential handoff only when stdin is a TTY; explicit safe failure for non-TTY stdin with no echoing fallback; rejection of a token CLI argument and absence of environment/file lookup;
- required expected name, section-distinguishing course code, term, start date, and end date arguments; synthetic course response with `include[]=term`; missing/null identity fields and mismatches stopping before all resource calls;
- unknown CLI options and `--token` with a synthetic secret value producing a fixed sanitized error that does not echo the rejected text;
- GET-only method and exact KNU HTTPS origin, with the bearer token present only in the request header and absent from URL/output;
- redirect refusal, including a cross-origin redirect case, and rejection of unsafe pagination links;
- remaining-time socket budget, realistic per-operation timeout semantics, response-byte cap, page/item caps, and no retry behavior;
- page-cap and deadline truncation yielding `status: "incomplete"` and a nonzero exit code, including when truncation happens during optional resources;
- successful pagination only when the immutable query multimap is preserved and the page cursor is locally reconstructed, plus allowlisted field sanitization;
- announcement context-code mismatch, changed pagination filters, duplicate pagination parameters, and missing/non-JSON content types being rejected;
- preservation of a missing assignment due date as JSON `null`;
- omission of bodies, grades, submissions, user data, and file/download URLs;
- synthetic course identity mismatch stopping all resource calls;
- malformed JSON, wrong top-level shape, HTTP errors, and exceptions producing bounded sanitized errors without response-body leakage;
- `--include-files` and `--include-modules` being opt-in and metadata-only, with module item omission, invalid entries, item caps, and `items_count` mismatch explicitly incomplete;

After implementation, run the focused unit tests and the repository-required `ruff check`/`mypy` checks as applicable to the new standalone script and tests. Do not add live tests to the default suite; a real run is a manually invoked validation after user approval.

## Risks and blocked steps

- No new Canvas token has been created or authorized. The profile dialog showed purpose and expiration but no displayed read-only/course scopes. Before any live run, the parent must explain and obtain the user’s specific approval for token creation, purpose, and short expiration. The token must be entered locally through the prompt and never sent in chat. A non-TTY invocation is a hard stop.
- Course matching is fail-closed: the selected course response must include non-null ID, name, course code, and `term.name`, and all required expectations must match before assignments, announcements, files, or modules are requested.
- The token may be broad account-level access. The helper’s GET-only behavior limits this command’s actions, but it cannot narrow permissions that Canvas does not expose in the dialog. This remains a user-owned risk decision at token-creation time.
- KNU may reject API access, return 401/403, or redirect to SSO/HTML. The probe will report only sanitized status categories; a browser block or CLI daemon failure will not be bypassed or treated as API evidence.
- Files and module items may be unavailable or may contain external-tool references. The optional calls will report metadata availability only and will not download or open content.
- Parent acceptance of the API-only plan bundle is recorded. Implementation is complete in `scripts/knu_lms_probe.py` and `tests/unit/test_knu_lms_probe.py`; 37 focused unit tests, Ruff, and strict mypy checks pass. Independent final review remains pending. Live validation is blocked until the parent obtains the separate token-creation approval. Connector discovery and any Notion/Drive application remain with the main agent and are outside this helper. Review artifacts and tests use only synthetic course/resource IDs and synthetic metadata.

## Official references

- [Canvas OAuth2/API authentication and token storage](https://canvas.instructure.com/doc/api/file.oauth.html)
- [Canvas Courses API (`include[]=term`, course identity fields)](https://canvas.instructure.com/doc/api/courses.html)
- [Canvas Assignments API](https://canvas.instructure.com/doc/api/assignments.html)
- [Canvas Announcements API](https://canvas.instructure.com/doc/api/announcements.html)
- [Canvas Files API](https://canvas.instructure.com/doc/api/files.html)
- [Canvas Modules API](https://canvas.instructure.com/doc/api/modules.html)


---

# LMS 연결 검증의 UI 관찰과 표시 기준

이 문서는 실제 관찰을 개인정보와 실제 리소스 ID 없이 정리한 리뷰 입력이다. 제안 화면을 실제 적용 완료로 취급하지 않는다.

## 관찰한 화면/상호작용

- 학교 LMS 계정 설정의 새 액세스 토큰 창에는 목적과 만료일 입력이 있었다. 읽기 전용/과목별 scope 선택은 보이지 않았다. 생성 없이 취소했다.
- 기존 로그인 정보로 Aside의 저장 계정 자동완성을 선택한 뒤 학교 SSO를 거쳐 LMS 대시보드에 도착했다. 비밀번호 값은 읽지 않았다.
- 실제 과제 상세에 `마감일 없음`과 `내용 없음`이 표시됐다. 결측을 일정 없음 또는 조회 실패와 구분해야 한다.
- 실제 공지 첨부를 정상 링크/저장 대화상자로 내려받은 뒤 브라우저는 `469KB · 완료`를 표시했다. 로컬에서 PDF v1.7, 15페이지를 확인했다.
- 별도 LearningX 강의자료는 LCMS 뷰어로 열렸지만 원본 다운로드 버튼을 확인하지 못했다. 첨부파일의 다운로드 성공을 이 자료에 확대 적용할 수 없다.
- 기존 1학기 Notion native 페이지를 connector로 읽어 `내 과목 → 이어서 공부 → To DO → 캘린더 → 파일 확인`의 순서를 확인했다. 새 2학기 페이지는 아직 만들지 않았다.

## 이번 검증에서 필요한 표시

- API 도구를 실행해 보지 않았다면 준비 완료와 실제 API 연결 성공을 구분한다.
- 토큰 발급 전에는 계정 범위 권한일 수 있고 조회 도구만 GET으로 제한된다는 점, 단기 만료, 로컬 비표시 입력을 설명한다. 토큰을 채팅으로 받지 않는다.
- API 인증/형식/페이지 한도/신원 대조 실패는 실패 또는 불완전으로 표시한다. 성공한 빈 목록으로 대체하지 않는다.
- 과목/학기/분반을 명시하고 파일을 학습 세션이나 학습 완료로 자동 변환하지 않는다.
- 새 학기의 자료 한 개 연결은 수동 검증이다. To DO/캘린더/이어 공부는 아직 수집되지 않았음을 표시하며, 할 일이 없거나 전체 동기화가 완료됐다고 표시하지 않는다.
- 사용자 요청이 없는 주기적 알림이나 새 자동화는 만들지 않는다.

## 최종 UI 검증 예정

생성 뒤 Notion 구조와 링크를 readback하고 실제 화면에서 상위/하위 페이지 탐색, 빈 상태와 수동 검증 표시를 확인한다. 사용자 승인 질문은 검증 도구 준비 후 실제 발급 직전에 한다.

## HTTP diagnostic addendum — 2026-09-13

This addendum covers the accepted diagnostic correction after the live launcher returned only `http_error`. Earlier observations above remain historical snapshots. The continuous Luna/high worker owns the helper, its focused tests, and these narrow documentation addenda; root owns final reviews and any authorized live handoff.

- HTTP errors expose only `http_status` (integer 100–599, excluding booleans), local `resource` (`course`, `assignments`, `announcements`, `files`, or `modules`), and boolean `auth_challenge_present`. The last field checks case-insensitive header-name existence, including an empty header; no challenge value, response body, exception message, URL, or token is emitted. Invalid status values produce the fixed `invalid_http_status` failure without echoing the value. Redirects remain rejected.
- A course HTTP failure returns `status: failed`, exit 2, without course metadata or child calls. Once course name/code/term are verified, a later HTTP failure returns `status: incomplete`, exit 3, retaining that course and fully completed preceding resource lists. `incomplete_resources` and `incomplete_reasons` identify the failed stage with `http_error`; its entire list, including earlier pages, is omitted. Requests stop immediately, without retry or subsequent resources. Missing data is not an empty successful list or Ready state.
- JSON stdout contains one complete document. Stderr carries the same safe diagnostic fields: `probe_failed=http_error http_status=401 resource=course auth_challenge_present=false`, or `probe_incomplete=http_error http_status=403 resource=announcements auth_challenge_present=true`. Other incomplete outcomes retain their existing stderr behavior.
- A 401 and the challenge-presence hint do not establish that a token is invalid or authorize regeneration. [Canvas OAuth documentation](https://canvas.instructure.com/doc/api/file.oauth.html) describes authentication and resource-permission possibilities; header presence itself does not authenticate the header's claim. This correction adds no automatic credential action, endpoints, retries, secret storage, or live requests. Existing local getpass-only input, expiry boundary, verified identity requirements, TLS validation, immutable pagination queries, bounds, and strict JSON protections remain in force.
- Acceptance checks use synthetic HTTP errors through both returned-response and raised-`HTTPError` paths: representative statuses and invalid types/ranges, absent/mixed-case/empty/secret-bearing challenge headers, unread error bodies, exact local stages, stop-on-error, preserved completed metadata, and discarded partial pages. All prior 37 regressions remain in the focused suite. Candidate checks are recorded in the evidence addendum; independent final reviews and root acceptance precede any live retry.
