# Drive OAuth 비개발자 연결 — 통합 계획 v8

상태: PLAN-only. Native v7 C REQUIRED 4건, D REQUIRED 3건, D3 REQUIRED 2건을 현재 읽은 소스에 맞춰 보완한다. 독립 Native/Gemini 계획 재검토와 총괄의 통합 GO 전에는 제품·테스트 편집이나 실행을 하지 않는다. v7의 R1/R2/R3 및 A/B/B2 계약은 그대로 승계하며, composition singleton 회귀는 v7의 B composition coverage를 REQUIRED로 승격한다. v8의 D 계약은 v7의 `D unchanged Drive API/privacy` 행과 A/B/C-only release gate를 명시적으로 대체한다. 추가 D2 review가 오면 같은 소유자가 이 v8에 반영한다.

## 규범 우선순위와 승계

이 문서는 v7 전체를 포함하는 독립 계획이다. 아래 C override는 v7의 `C browser/UI`, `Config/UI` 수락 항목 및 일반 흐름 중 UI가 의존하는 readiness/wire 부분을 대체한다. B composition override는 v7의 singleton ledger/service construction·acceptance를 보강해 production identity assertion을 REQUIRED로 승격한다. D override는 v7의 `D unchanged Drive API/privacy` no-change 선언, D의 회귀 소유권, privacy/readback 수락 항목 및 A/B/C-only release gate를 대체한다. 이 override들과 충돌하지 않는 v7 본문은 모두 그대로 유지한다. 특히 v7 R1/R2/R3 및 A/B/B2 구현·보안·transaction 계약, callback 경계, replay ledger semantics, provider budgets, payload parser, external-source consent, generic service-account 경계는 축약하거나 재해석하지 않는다. B composition override는 ledger consume·expiry·capacity·tombstone 의미는 유지하되 composition이 singleton object를 만들고 B service가 consume authority를 갖는다고 명확히 한다. 이어지는 v7 본문은 원 v7 파일의 제목행 뒤 bytes를 그대로 보존해 넣었다. v7의 당시 상태·리뷰 기록은 역사 증거이며 현재 v8의 재검토나 구현 승인을 뜻하지 않는다.

| 근거 | SHA-256 | bytes | 사용 |
|---|---|---:|---|
| Native v7 C canonical response | `95f84b1dd60912c7d3bae4dfba6ba83b66487df4b07945982d19a5d0ed4c7ed3` | 12,862 | 전문 판정 입력; REVISE REQUIRED 4 / OPTIONAL 0 |
| Native v7 D canonical response | `5c291c0bfad16e01771d43f95cd93dea9cc5c40e53884e6dec835e92d74d7de1` | 9,962 | 전문 판정 입력; REVISE REQUIRED 3 / OPTIONAL 0 |
| Native v7 D3 canonical response | `1ffde01bb2aefde72ae5fcb24e5a40cf46ea64f7f18f37ea2068ca2c714aae52` | 10,003 | 전문 판정 입력; REVISE REQUIRED 2 / OPTIONAL 0 |
| v7 inherited plan | `2e70457b952db30ed9f80b5e2997b301545b318e2765675809493fcb940386a2` | 57,845 | R1/R2/R3 및 A/B/B2 포함 complete inherited body |

## C-R1 — readiness와 A↔C wire 계약

### Read-only readiness producer

`GET /P/api/v1/overview`의 기존 응답에 `google_oauth_readiness`를 추가한다. producer는 `src/uls/settings/status.py::settings_overview()` 하나다. `settings_overview()`는 이미 검증되어 읽힌 config snapshot을 A-owned pure helper에 전달하고, helper의 목적별 boolean 결과를 exact `ready`/`not_ready` enum으로 투영한다. 이 경로에서 Google/provider network call은 하지 않는다. 값은 purpose별 Desktop OAuth bundle의 유효성과 별도 검증된 provider deployment gate가 **모두** 맞을 때만 `ready`다. bundle 파일/값이 있다는 사실만으로 live OAuth를 준비 완료로 보지 않는다. provider 호출, 계정 정보, client ID/secret, project ID, token, scope 목록, 경로, 오류 문자열을 넣지 않는다. `setup_ready`, `setup_steps`, `worker_enabled`를 바꾸거나 worker를 켜지 않는다.

```json
"google_oauth_readiness": {
  "mcp": "ready",
  "worker": "not_ready"
}
```

중첩 객체의 정확한 키는 `mcp`, `worker`; 값은 각각 `ready` 또는 `not_ready`뿐이다. 목적의 bundle 전체가 빠진 경우는 `not_ready`; bundle이 완전하고 기존 v7 validator를 통과하더라도 아래 provider deployment record가 없거나 stale·mismatch·incomplete면 `not_ready`다. 부분·잘못된 OAuth bundle은 계속 startup-fatal이며 readiness로 숨기지 않는다. Deployment record 결손·미결·잘못된 형식은 OAuth를 fail-closed `not_ready`로 둔다. C는 필드 누락, 목적 누락, 예상 밖 값/키, 잘못된 타입, Overview fetch/JSON 실패를 `not_ready`로 간주하지 않는다. 이때는 OAuth 시작을 막고 고정 중립 문구 “Google connection status could not be checked. Refresh status before trying again.”만 표시한다. 유효한 `not_ready`일 때는 해당 purpose의 OAuth CTA보다 준비 안내를 우선한다. A의 begin도 동일한 bundle과 deployment gate를 독립 확인하고 UI 우회 요청에 `OAUTH_APP_NOT_READY`를 돌려준다.

#### D-R1 — provider deployment readiness and live-only distribution gate (Native D REQUIRED 1 + D3 REQUIRED 2)

MCP와 WORKER는 각각 다음의 별도 배포 tuple을 가진다. 앱은 두 purpose에 서로 다른 Google Cloud project와 Desktop OAuth client를 사용한다. MCP scope는 `https://www.googleapis.com/auth/drive.readonly`, WORKER scope는 `https://www.googleapis.com/auth/drive`다. School root/course allowlist는 Google permission grant를 좁히지 않는다. Remote Web client 설정은 Desktop client의 대체물이 아니다.

| Purpose | Required exact scope | Project/client | Consent·publishing posture | Restricted-scope verification/security assessment |
|---|---|---|---|---|
| MCP | `https://www.googleapis.com/auth/drive.readonly` | 별도 Google Cloud project와 Desktop OAuth client; 실제 ID/ref는 외부 준비가 관측되기 전까지 미확인 | 해당 실제 client의 현재 consent 및 publishing posture를 operator evidence에 기록; 임의 상태를 가정하지 않음 | applicability를 실제 deployment 조건으로 판정해 기록하고, required면 완료 증거 필요; 판정/완료 미확인은 not-ready |
| WORKER | `https://www.googleapis.com/auth/drive` | MCP와 다른 Google Cloud project와 Desktop OAuth client; 실제 ID/ref는 외부 준비가 관측되기 전까지 미확인 | 해당 실제 client의 현재 consent 및 publishing posture를 operator evidence에 기록; 임의 상태를 가정하지 않음 | applicability를 실제 deployment 조건으로 판정해 기록하고, required면 완료 증거 필요; 판정/완료 미확인은 not-ready |

이 상태는 A-owned `google_oauth.py`의 별도 operator-managed deployment record가 소유한다. Record의 exact schema는 `purpose` (`mcp|worker`), `scope` (위의 정확한 URI), `project_id`, `desktop_client_id`, non-empty `consent_posture`, non-empty `publishing_posture`, `assessment_applicability` (`required|not_required|unknown`), `assessment_status` (`pending|complete|not_required|unknown`), `nondeveloper_deployment_eligible` (boolean), non-secret `evidence_ref`, `observed_at` (RFC 3339 timestamp)다. 추가·누락 key, 잘못된 타입, 빈 posture/evidence, 잘못된 timestamp는 fail-closed다. `assessment_applicability=required`는 `assessment_status=complete`만, `not_required`는 `assessment_status=not_required`만 통과한다. `unknown`·`pending` 조합은 절대로 통과하지 않는다. `nondeveloper_deployment_eligible=true`는 실제 외부 준비를 확인한 operator의 별도 attestation이며 config bundle 또는 development/test-user 로그인 성공만으로 자동 생성되지 않는다.

Record의 scope/project/client는 실제 Desktop bundle의 purpose/scope/project/client와 모두 일치해야 한다. UI나 begin request가 record를 만들거나 수정할 수 없다. Unknown/absent/stale/mismatched record, 미판정 applicability, required-but-incomplete assessment, `nondeveloper_deployment_eligible=false`, 또는 bundle tuple 교체 뒤 남은 예전 record는 `OAUTH_APP_NOT_READY`다. GET overview는 Google에 접속하지 않고 record를 읽어 readiness enum만 반환한다. 정확한 Google 심사 단계나 필요 여부는 이 계획에서 만들어 내지 않는다.

현재 관측은 Desktop OAuth client 미설정, Remote Web client 설정 존재(값은 읽지 않음)다. 따라서 두 purpose 모두 live readiness는 `not_ready`; client/project identifiers, consent/publishing posture 및 provider assessment 상태는 미확인이다. 외부 provider 준비는 별도 human/live evidence만으로 닫는다. Synthetic 테스트의 일치하는 fake bundle+fake deployment record는 코드의 상태 계산만 검증하며 실제 Google deployment 준비를 입증하지 않는다.

**회귀 수락:** bundle만 존재하고 deployment record가 없는 상태, 한 purpose의 bundle/record 누락, scope·project·client 불일치, consent/publishing posture 미기록, applicability unknown, required assessment pending, stale record는 모두 `not_ready`와 일반 비개발자 begin 0 effect여야 한다. 목적별 complete matching fake record만 synthetic response를 `ready`로 바꿀 수 있다. 추가 HTTP/metadata key, raw purpose config 또는 provider message를 UI에 노출하지 않는다. Live activation은 별도 기록에 exact scope/project/client, posture, applicability/completion 근거가 제출된 뒤에만 사람이 결정한다.

**Live-only operating gate (Native D3 REQUIRED 2, merged with D-R1):** 외부 readiness evidence가 닫히기 전, 실제 Google 사용은 해당 purpose의 실제 provider deployment posture가 허용하는 operator-led developer/test-user 범위에 한정한다. 이는 비개발자 배포 준비 완료, 일반 사용자의 `ready`, full School-flow acceptance 또는 School output publication을 뜻하지 않는다. `google_oauth_readiness`는 계속 `not_ready`이며 일반 비개발자 UI는 준비 완료나 성공 copy를 보이면 안 되고, ordinary nondeveloper begin은 `OAUTH_APP_NOT_READY`/동등한 not-ready로 막는다. CI와 synthetic fake record는 Google 외부 verification/security assessment 완료를 증명하지 않는다. Release record에서 두 purpose의 exact scope/project/client, observed consent/publishing posture, assessment applicability/completion 및 human/live evidence를 사람이 확인하기 전까지 비개발자 배포, full School acceptance, School output publication을 금지한다. Google 심사 단계나 applicability는 추정해 만들지 않는다. Test-user 사용은 실제 provider consent posture가 허용할 때만 개발/검증 목적으로 가능하며 제품 readiness를 올리거나 외부 publication을 허용하지 않는다.

### 공통 wire 형태와 strict validation

OAuth 전용 응답은 `application/json`이어야 한다. 아래 success object와 error object는 나열된 key 집합과 type만 허용하며 extra/missing key, unknown enum/code, invalid HTTP success status, malformed JSON, type mismatch, 요청한 purpose/flow와 다른 ID/epoch 또는 내부 조합 오류는 모두 neutral failure다. C는 알 수 없는 필드나 provider 응답을 상태 전이·copy·focus에 사용하지 않는다. OAuth 전용 error에는 provider의 `message`, `fields`, body, URL, token 또는 exception text가 없어야 한다. 공통 API wrapper가 `error.message`를 반환하더라도 OAuth 경로는 렌더링하지 않는다.

Begin request는 POST `/P/api/v1/google-oauth/{purpose}/begin`, `purpose ∈ {mcp, worker}`이며 정확한 JSON body는 다음과 같다.

```json
{"generation":"<current-config-generation>","replace":false}
```

기존 effective credential이 있으면 사용자의 명시적 replacement 확인 뒤에만 `replace:true`를 쓴다. 확인 취소는 POST 0회다. 최초 연결은 `replace:false`다. 성공 HTTP 201은 아래 `FlowSnapshot` exact object이며 보통 `pending`; 브라우저 opener가 false/exception을 반환한 경우에도 flow ID를 포함한 `launch_failed` terminal snapshot을 반환한다. `OAUTH_APP_NOT_READY`, `FLOW_BUSY`, `CONFIGURATION_CHANGED` 같은 begin 거절은 error envelope다.

GET `/P/api/v1/google-oauth/{purpose}/{flow_id}`는 body가 없다. POST `.../{flow_id}/cancel`은 exact body `{}`; POST `.../{flow_id}/commit`은 exact body `{"generation":"<captured-config-generation>"}`이다. Poll, cancel, commit의 정상 HTTP 200 body는 모두 같은 `FlowSnapshot`이며, cancel이 HTTP 200을 받았다는 이유만으로 UI가 취소 성공을 표시하지 않는다. 실제 snapshot state가 `cancelled`일 때만 취소로 분류한다. `committing`에서 cancel은 HTTP 409 error code `COMMIT_IN_PROGRESS`; complete snapshot이 돌아오면 commit/readback 경로를 계속한다. Commit은 `awaiting_commit`에서만 실행한다. duplicate commit은 두 번째 B save를 만들지 않으며 진행 중이면 `COMMIT_IN_PROGRESS`, 완료 뒤 재조회면 이미 저장된 동일 complete snapshot만 반환한다.

**FlowSnapshot의 정확한 key/type**은 다음 여섯 가지다. 성공 JSON에 이를 제외한 key를 추가하지 않는다.

| Key | Exact value |
|---|---|
| `flow_id` | 서버가 발급한 32자리 소문자 hex string; C가 처음 받은 값과 이후 응답이 일치해야 함 |
| `flow_epoch` | 양의 safe integer; 같은 C owner의 모든 응답에서 일치 |
| `state` | `pending`, `exchanging`, `awaiting_commit`, `committing`, `complete`, `cancelled`, `denied`, `account_mismatch`, `launch_failed`, `expired`, `failed` 중 하나 |
| `code` | 아래 표와 state에 정확히 대응하는 enum 또는 `null` |
| `expires_in_seconds` | integer 0–300; active state는 1–300, terminal state는 0 |
| `config_generation` | active 및 pre-commit terminal은 `null`; `complete`만 non-empty generation string |

| `state` | `code` | 처리 |
|---|---|---|
| `pending`, `exchanging`, `awaiting_commit`, `committing` | `null` | pending 상태 문구; `awaiting_commit`에서만 현재 owner가 commit을 이어감 |
| `complete` | `COMMITTED` | 저장 완료 후보; 아직 UI 성공이 아님. `config_generation`을 포함한 fresh readback 필수 |
| `cancelled` | `FLOW_CANCELLED` | 고정 취소 문구 |
| `denied` | `ACCESS_DENIED` | 고정 거부 문구; B commit 전이라 기존 credential 유지 |
| `account_mismatch` | `ACCOUNT_MISMATCH` | 고정 계정 확인 문구; B의 effect 전 fail-closed |
| `launch_failed` | `BROWSER_LAUNCH_FAILED` | 고정 브라우저 시작 실패 문구 |
| `expired` | `FLOW_EXPIRED` | TTL 만료 문구 |
| `failed` | `FLOW_FAILED` | 중립 확인 실패; 원문 오류 금지 |

`FlowSnapshot.code`의 terminal code와 HTTP error code는 별도 allowlist다. Terminal record가 TTL 후 사라진 poll/cancel/commit은 HTTP 404 `{"error":{"code":"FLOW_NOT_FOUND"}}`다. 모든 OAuth API error는 정확히 `{"error":{"code":"<allowlisted-code>"}}` 하나의 envelope를 쓴다. HTTP error code는 `OAUTH_APP_NOT_READY`, `FLOW_BUSY`, `FLOW_NOT_FOUND`, `FLOW_TERMINAL`, `COMMIT_IN_PROGRESS`, `CONFIGURATION_CHANGED`, `OAUTH_UNAVAILABLE`, `INVALID_REQUEST`, `JSON_REQUIRED`, `SESSION_EXPIRED`, `SESSION_REPLACED`, `SAME_ORIGIN_REQUIRED`, `CSRF_REJECTED`뿐이다. Domain terminal (`ACCESS_DENIED`, `ACCOUNT_MISMATCH`, `BROWSER_LAUNCH_FAILED`, `FLOW_EXPIRED`, `FLOW_CANCELLED`, `FLOW_FAILED`)은 `FlowSnapshot`의 state/code pair로만 반환한다. A의 OAuth route HTTP mapping은 begin 201 snapshot (`pending` 또는 `launch_failed`); poll 200 snapshot; cancel 200 snapshot이며 committing 중 409 `COMMIT_IN_PROGRESS`; commit 200 complete 또는 commit 시 검증된 terminal snapshot이며 진행 중 409 `COMMIT_IN_PROGRESS`, 이미 종료된 미완료 flow는 409 `FLOW_TERMINAL`; 400 `INVALID_REQUEST`; 415 `JSON_REQUIRED`; 401 session codes; 403 origin/CSRF codes; 404 `FLOW_NOT_FOUND`; 409 readiness/busy/generation/terminal/commit-in-progress codes; 503 `OAUTH_UNAVAILABLE`다. `complete`를 재조회하는 commit은 기존 snapshot만 돌려주고 B save를 재호출하지 않는다. Existing callback/result HTTP exception, CSRF, Fetch Metadata, callback state 처리와 v7의 A 계약은 변하지 않는다.

### C 고정 error projection

코드·state를 고정 표에 매핑한다. `error.message`, provider body, Google URL, account/token 값은 보지 않고 아래 copy만 쓴다. HTTP success지만 schema가 틀렸거나 unknown code/state이면 마지막 neutral 행으로 처리한다.

| Code/state | 고정 화면 문구 | 다음 행동 / focus |
|---|---|---|
| readiness `not_ready` / `OAUTH_APP_NOT_READY` | “Google account sign-in is not ready on this installation. Use Advanced setup or ask the administrator to finish app setup.” | OAuth begin 금지; Advanced setup help로 focus |
| `FLOW_BUSY` | “A Google sign-in is already in progress. Finish it or cancel before starting another.” | 기존 flow로 이동; 그 flow의 Cancel control에 focus |
| `BROWSER_LAUNCH_FAILED` | “Syllva could not open the Google sign-in page. Try again or use Advanced setup.” | 같은 purpose Connect retry; Connect control focus |
| `ACCESS_DENIED` | “Google access was not granted. You can try again when ready.” | retry 또는 종료; Connect control focus |
| `ACCOUNT_MISMATCH` | “Syllva could not verify this Google account for the current connection. Use the account required by your setup and try again.” | 새 flow로 재시도; Connect control focus |
| `FLOW_EXPIRED` | “This Google sign-in expired. Start again to retry.” | 새 flow만 가능; Connect control focus |
| `FLOW_CANCELLED` | “Google sign-in was canceled. You can connect an account later.” | 시작 카드에 focus; credential 결과를 바꾸지 않음 |
| `COMMIT_IN_PROGRESS` 또는 snapshot `committing` | “Syllva is finishing the Google connection and checking its status.” | cancel-success로 표현하지 않음; 같은 flow poll 유지 |
| commit 뒤 readback/Overview unavailable, pending, mismatch | “Syllva could not confirm the Google connection. Check the refreshed status and any recovery item before retrying.” | recovery/status 확인; 새 write를 자동 반복하지 않음; 카드 status focus |
| `FLOW_NOT_FOUND`, `FLOW_FAILED`, `OAUTH_UNAVAILABLE`, unknown code/state, extra/malformed object, unknown readiness | “Syllva could not verify the Google connection status. Refresh status before trying again.” | read-only refresh만; 알 수 없는 ID/action 실행 금지; 카드 status focus |
| `SESSION_EXPIRED`, `SESSION_REPLACED`, `CSRF_REJECTED`, `SESSION_UNREACHABLE` | 기존 Settings session-ended 화면 | OAuth가 focus/notice를 덮지 않음; 종료된 세션에서 retry 금지 |

Provider 원문이나 API의 `message`는 DOM, notice, accessible label, inline help 또는 retry label에 나타나지 않는다. OAuth C 경로에서 provider가 준 `message`/`body` sentinel을 주입해 이를 입증한다. Public `client_id`는 OAuth authorization protocol상 Google navigation에만 갈 수 있고 Syllva DOM/error copy에 넣지 않는다. client secret, access/refresh token, callback code, verifier/state 및 provider body는 Syllva UI/API/error text 어디에도 나타나지 않는다.

## C-R2 — flow owner, 비동기 소유권, 종료 및 성공 판정

C는 purpose별 active flow owner와 별도의 shared presentation generation을 가진다.

```text
CFlowOwner = {
  sessionEpoch, purpose, flowId, flowEpoch,
  purposeSubmissionGeneration, presentationGeneration,
  phase, timerId, inFlightAbortControllers
}
```

`sessionEpoch`는 Settings session 시작 시 C가 보유하고 session replacement/expiry/close 때 증가·무효화한다. `purposeSubmissionGeneration`은 해당 목적의 accepted Connect/reconnect flow마다 증가한다. `presentationGeneration`은 Connections에서 notice/focus/busy를 바꿀 수 있는 accepted credential/Canvas/OAuth action마다 증가한다. Session, purpose, flow ID/epoch, purpose submission generation 중 하나라도 다르면 이전 owner는 stale다. 기존 `generation`은 config generation이며 위 UI generation들과 혼합하지 않는다.

새 OAuth submission owner는 첫 `await`보다 먼저 동기적으로 생성한다. Existing effective credential이면 replacement dialog의 긍정 확인 시점이 accepted submission이다. 사용자가 dialog를 취소하면 OAuth HTTP 요청은 0회이고 focus는 호출 버튼으로 돌아간다. Owner를 만든 뒤 readiness Overview 확인, begin, opener-result polling, cancel, commit, `/credentials`, Overview의 **모든** awaited fetch/promise 완료와 실패 직후 DOM render, notice, focus, timer, per-purpose busy 또는 shared busy를 바꾸기 전에 owner를 검사한다. 같은 검사 규칙을 `finally` cleanup에도 적용한다.

- `flowOwnerCurrent(owner)`가 참일 때만 해당 목적의 flow row/status와 해당 owner의 timer/controller를 만진다.
- `presentationGeneration`도 현재일 때만 공용 notice, dialog focus, global busy, retry focus를 게시·해제한다. 새 Canvas/다른 credential action은 기존 OAuth의 공용 presentation 소유권을 가져간다. 이전 flow가 자체 purpose polling을 계속하더라도 새 동작의 notice/focus/busy를 지우거나 덮을 수 없다.
- Busy는 purpose별 owner-token을 저장한다. 이전 owner의 `finally`는 자신과 같은 token의 busy만 해제한다. OAuth login 대기 동안 unrelated Canvas 및 다른 목적 card 전체를 잠그지 않는다.
- 각 owner는 자신이 만든 timeout/poll timer와 AbortController만 보유한다. terminal, explicit cancel, TTL, session end/replacement, 같은 purpose의 successor flow에서는 timer를 clear하고 fetch를 abort 시도하고 Promise rejection을 안전하게 수거한다. Abort가 이미 실행 중인 Promise를 끝낸다고 가정하지 않는다. 늦게 resolve/reject한 Promise는 owner guard 뒤 무효 처리한다.
- C는 browser close를 감지해 flow가 끝났다고 표시하지 않는다. 사용자가 Cancel을 누르거나 backend 300초 TTL이 끝날 때까지 pending으로 남는다. TTL timer는 best-effort UI 표시이며 backend expiry가 authoritative다. poll은 flow별 단 하나만 outstanding이며 고정 1초 간격을 넘겨 겹치지 않는다.
- Cancel 버튼은 `POST .../cancel`과 exact `{}`만 전송한다. exact `cancelled/FLOW_CANCELLED` snapshot을 확인하기 전엔 cancelled 표시가 없다. Cancel 시 `committing`/`complete`를 읽으면 저장/확인 경로를 계속하며 cancel success를 표시하지 않는다. Cancel fetch rejection은 fixed neutral/current owner 문구, timer와 busy cleanup을 남기고 자동 credential write/retry를 하지 않는다.
- `awaiting_commit` snapshot을 확인한 현재 owner만 exact generation body로 commit을 시작한다. `committing` snapshot이면 poll만 계속한다. 두 번째 commit은 UI에서 시작하지 않는다. Stale flow의 late commit이 backend transaction 결과를 되돌리려 하지 않고, 새 presentation도 쓰지 않는다.

Commit HTTP 2xx (`complete/COMMITTED`)는 성공 표시가 아니다. 현재 flow owner가 `GET /P/api/v1/credentials`를 다시 수행하고 exact `config_generation`이 complete snapshot의 generation과 일치하며, 카드 배열에서 정확히 하나의 `role=google-mcp`/`purpose=mcp` 또는 `role=google-worker`/`purpose=worker`, `provider=google` card를 확인해야 한다. 그 card는 `managed === true`, `state === "configured"`, `pending_operation === null`이어야 한다. missing/duplicate/wrong role, mismatched generation, partial/pending recovery, malformed body, request failure는 neutral unconfirmed다. 그 뒤 같은 owner의 fresh `GET /P/api/v1/overview`를 확인하고 readiness subobject strict validation에 성공해야 한다. Overview의 `setup_ready`가 false/Partial인 것은 성공 문구를 막지 않지만, 그 상태를 전체 School 자료 처리 성공이라고 표현하지 않는다. 두 fresh readback 중 어느 하나라도 실패하거나 owner가 바뀌면 success와 success focus는 없다. 모든 조건을 만족한 뒤에만 “Google account connected to Syllva.”를 표시하고 해당 purpose card heading에 focus한다.

## C-R3 — Google card actions와 non-developer 안내

Google MCP/WORKER card의 기본 CTA label은 준비된 purpose의 **“Connect Google account”**다. OAuth readiness가 `not_ready`면 Connect보다 준비 안내를 먼저 표시하고 begin은 0회다. Readiness unknown/malformed, card `can_mutate=false`, unknown card fields, 또는 nonterminal `pending_operation`이 있으면 새 OAuth flow를 시작하지 않는다. Google scope와 app setup 안내는 user consent 전에 보인다.

| Purpose | 승인 전 고정 안내 |
|---|---|
| MCP | “This grants Syllva read-only access to all files in the signed-in Google Drive account.” |
| WORKER | “This grants Syllva read and write access to all files in the signed-in Google Drive account.” |
| 둘 다 | “School folder and course allowlists limit where Syllva processes data; they do not narrow Google's permission grant.” |

MCP는 OAuth scope `https://www.googleapis.com/auth/drive.readonly`, WORKER는 `https://www.googleapis.com/auth/drive`다. School root/course allowlist는 Syllva 내부 대상 제한이지 Google 권한의 축소가 아니다. drive.file/Picker를 추가하거나 권한을 폴더 전용이라고 표현하지 않는다. Remote MCP OAuth client/token을 쓰지 않는다.

Existing effective credential(`card.can_test === true`)을 OAuth로 바꾸려면 별도의 explicit replacement confirmation을 먼저 보인다. Dialog는 현재 Drive connection을 Google user credential로 바꾼다는 사실, 위 purpose scope, app allowlist가 Google scope를 줄이지 않는다는 사실을 모두 담는다. Affirmative confirm만 `replace:true`로 시작하고 decline/cancel은 begin 0회다. No effective credential이면 `replace:false`. Denied, launch failure, expiry, callback/account mismatch는 B commit 전에 끝나 기존 credential을 보존한다. Commit/readback이 uncertain인 경우 기존 저장 상태가 유지됐다고 추정하지 않고 neutral copy만 쓴다. External file/environment 원본 bytes와 remote permission은 건드리지 않는다.

기존 service-account JSON 경로는 별도 **“Advanced setup” → “Service account JSON”** secondary action으로 보존한다. 기존 file input/change, `file.text()` read, size/error 안내, Cancel 후 POST 0회, secret clear, dialog trap/return-focus 동작을 변경하지 않는다. 미준비 Desktop app은 기본 OAuth action 앞에 준비 안내를 표시하되, Advanced 경로를 자동 실행하거나 OAuth로 위장하지 않는다.

기존 managed Google credential의 local removal은 현재 API를 재사용한다: POST `/P/api/v1/credentials/{role}/forget`, 여기서 role은 정확히 `google-mcp` 또는 `google-worker`, body는 `{"generation":"<current-config-generation>","confirm_role":"<same-role-slug>"}`. 기존 confirmation과 config-generation/admission transaction을 보존한다. 의미는 Syllva가 관리하는 local credential과 그 config association을 제거하는 것뿐이다. Google 권한 revoke, Google Cloud service-account/entity/key 삭제, Drive ACL 삭제, `owner_only` 변경, 외부 file/environment 제거를 하지 않는다. Google account permission은 사용자가 Google 계정의 앱 권한 설정에서 직접 관리하도록 안내한다. External-file “Stop using this credential”은 기존 detach route/semantics 그대로 두며 file 자체는 보존한다. 현재 credential card가 `can_mutate`/`can_detach`를 제한하면 UI가 이를 넓히지 않는다.

각 outcome은 위 C-R1 fixed-code table의 copy/retry/focus만 사용한다. Account mismatch는 “required account를 사용해 새 flow로 재시도”로 안내하고 provider identity를 표시하지 않는다. Browser launch fail은 같은 purpose Connect retry 또는 Advanced setup. Denied는 나중에 Connect 재시도. Expired/flow-not-found는 기존 ID를 재사용하지 않고 새 flow. `committing`은 저장 확인 중임을 보이고 Cancelled라고 하지 않는다. Readback failure/pending recovery/mismatch는 status와 recovery item을 확인하게 하며 자동 retry/중복 commit/원문 provider error는 금지한다.

## C-R4 — C fake-browser acceptance 및 FINAL hash 결속

`tests/contract/settings_ui_harness.cjs`는 real `app.js`와 DOM harness를 사용하고 API/browser/clock은 fake다. `tests/contract/test_settings_ui.py`의 parameterized contract test가 아래 모든 행을 실행하며 scenario별 outputs와 요청 body/effect/visible copy/focus/timer 상태를 assert한다. Actual Google login, external browser, School, credentials, Keychain, `.env`, live provider, worker enable은 실행하지 않는다.

| Group | C-owned fake-browser scenarios and required assertions |
|---|---|
| Readiness and card order | MCP ready/worker not_ready 및 역방향; 둘 다 ready; 둘 다 not_ready; bundle-only, missing/stale/mismatched deployment record, provider assessment unknown/pending, Overview readiness missing/extra key/unknown value/wrong type/JSON error. `ready`는 bundle+matching deployment gate가 모두 검증된 purpose만 가능; `not_ready`는 안내 우선/begin POST 0회; unknown은 neutral/disabled. Synthetic matching attestation은 계산만 검증하며 live deployment 완료 증거로 인정하지 않는다. MCP/WORKER exact scope copy + allowlist disclaimer가 consent 전에 visible. |
| Begin and response contract | 각 purpose의 exact begin JSON (`generation`,`replace` only), exact 201 snapshot; app-not-ready, duplicate busy, generation conflict, launch opener success/false/exception. wrong status, missing/extra keys, unknown field/code/state, wrong flow ID/epoch, malformed JSON, `message`/provider sentinel은 fixed-neutral, second request/commit 0, timer/busy cleanup. |
| Poll state matrix | `pending`, `exchanging`, `awaiting_commit`, `committing`, `complete`, `cancelled`, `denied`, `account_mismatch`, `launch_failed`, `expired`, `failed`; 각 state/code/expiry/config-generation pair의 exact mapping과 가능한 transition만 실행. `FLOW_NOT_FOUND` (404/TTL 후)에는 새 flow만 제안; old ID를 commit/cancel에 재사용하지 않음. |
| Cancel, TTL, browser close | explicit Cancel sends exact `{}` and only terminal cancelled snapshot says canceled; cancel rejection cleanup; committing 중 Cancel returns/observes `COMMIT_IN_PROGRESS`, says saving/checking and never canceled. Fake browser close alone leaves server state pending before TTL; explicit cancel 또는 300초 TTL만 끝낸다. Timer가 terminal/session end/new flow에서 한 번만 정리되고 중복/outstanding poll이 0. |
| Consent, scopes, Advanced SA | Existing managed, external-file, environment credential each require affirmative replacement consent; decline emits begin 0; yes emits exact `replace:true`; no credential emits false. Denied/mismatch/expired before commit preserves old card bytes/state in fake backend. Advanced JSON path keeps existing `file.text()` success/error/cancel, POST-0 on cancel, input clear, modal/return-focus tests. |
| Commit and fresh readback | `awaiting_commit` → exact commit body once; duplicate commit/save 0 extra. Success only after complete 2xx generation equals fresh credentials generation, exactly one matching managed configured card and null pending operation, then valid fresh Overview. Readback unavailable, stale/superseded generation, wrong role/purpose, duplicate/missing card, unmanaged, state partial/error, pending recovery, missing/invalid readiness, Overview error or setup `Partial` produce no false School-ready claim; `Partial` may coexist only with narrowly worded account-connection success after all required identity/readback checks. |
| Owner races | Late poll after cancel/reconnect; old same-purpose flow after successor; late commit vs new credential action; session expiry/replacement; A completion after newer Canvas connect/replace/forget; newer Google/Notion credential action while A poll/Overview is in flight; stale Overview success and rejection. Assert old completion cannot change newer notice, focus, dialog, card mutation owner or busy token; it can release only resources bearing its own owner token. Every promise rejection is caught and cleanup is owner-conditional. |
| Fixed errors / privacy / focus | Each code: app-not-ready, busy, launch-failure, denied, account mismatch, expired, flow-not-found, commit-in-progress, readback-unconfirmed, session-ended; assert exact fixed copy, next retry target and semantic focus. Inject secret/client-secret/access-token/refresh-token/code/verifier/provider-body sentinels and hostile `error.message`; none appear in DOM/accessibility/error copy. Unknown API responses never choose a DOM ID, route or action. |
| Local disconnect | Managed `google-mcp` and `google-worker` confirmation sends exact `/credentials/{role}/forget` body; effect is local fake managed-store/config only. Assert no revoke/ACL/Cloud project/entity/key delete endpoint/call; external-file detach retains its file; no environment mutation. |
| Existing regressions retained | Existing stale credential result/reconcile, newer Canvas notice, Overview request ordering and stale recovery projection, credential/Canvas fixed notices, schema-3/recovery display, Advanced SA file-reader cancel/error/focus, session end, origin inline guidance all remain in `test_settings_ui.py`. In particular preserve `test_older_overview_response_cannot_restore_a_recovery_card`, `test_role_refresh_cannot_publish_overview_after_newer_credential_submission`, `test_independent_credential_reconcile_cannot_replace_newer_canvas_submission_notice`, `test_credential_outcome_invalidation_does_not_clear_newer_canvas_notice`, `test_canvas_actions_use_fixed_action_specific_success_and_neutral_notices`, and the Google file cancel/focus tests. |

FINAL requires a new candidate manifest for every current C product/test file (`status.py`, `app.js`, `index.html`, `styles.css`, `test_settings_ui.py`, `settings_ui_harness.cjs`) with actual SHA-256 and bytes; no v7/earlier capture is current-source proof. The manifest's canonical sorted path/hash/size bytes define a new aggregate `candidate_sha256`; the report names both it and all per-file hashes. Bind actual command and collected/passed/failed/skipped counts from that same candidate to the aggregate hash, and record any failing run as failing rather than carrying forward an earlier count. Minimum C checks after implementation are `./.venv/bin/pytest -q tests/contract/test_settings_ui.py` (which invokes the harness with Node), `node --check src/uls/settings/static/app.js`, `./.venv/bin/ruff check src/uls/settings/status.py tests/contract/test_settings_ui.py`, scoped mypy for status.py, and `git diff --check`. No tests were run while authoring this PLAN.

## C source closure and current fingerprints

C-owned product candidates remain the exact v7 set, with `status.py` explicitly selected as readiness producer: `src/uls/settings/status.py`, `src/uls/settings/static/app.js`, `src/uls/settings/static/index.html`, `src/uls/settings/static/styles.css`. C-owned tests remain `tests/contract/test_settings_ui.py` and `tests/contract/settings_ui_harness.cjs`. A owns OAuth endpoints and wire HTTP tests; B owns service/replacement/authorization semantics. This C plan adds no file or dependency beyond those six C files. Existing `app.py`, `credential_service.py`, and `credential_roles.py` observations below ground the reused API/role semantics and are unchanged C dependencies.

| Current path | SHA-256 | bytes | Scope note |
|---|---|---:|---|
| `.insane-review/drive-oauth-20261005/native-plan-v7-C-browser-UI/harvest/response_harvest_20261006_023450_34985_9da1ab.md` | `95f84b1dd60912c7d3bae4dfba6ba83b66487df4b07945982d19a5d0ed4c7ed3` | 12,862 |
| `docs/plans/drive-oauth-nondeveloper-v7.md` | `2e70457b952db30ed9f80b5e2997b301545b318e2765675809493fcb940386a2` | 57,845 |
| `src/uls/settings/status.py` | `83c39700bb012b4f8b83dc104ba6941e4335d6cb6b7ea873c0a0c35029c4730b` | 6,338 |
| `src/uls/settings/app.py` | `bb3b19c6069f15e414831c16dadc5f5fc9032a5e3cc5576ca08c99611af4e538` | 19,068 |
| `src/uls/settings/static/app.js` | `58afb07530b6ee80a585524fb874880e0e2fd621d3ccdb1a5c2021024b695c00` | 84,148 |
| `src/uls/settings/static/index.html` | `95a26b1782daee128ef73cc29dc963762e65c00c824bd81cd262456a8f9462d6` | 13,033 |
| `src/uls/settings/static/styles.css` | `94ba20f548da36e11697e5f834d9da6489cc19a3d4ccc071041a0eced5f93449` | 5,783 |
| `tests/contract/test_settings_ui.py` | `19e555f3d6363c1e1e7de66964bc256b0302a9eccd000e288f1319309beb11c6` | 38,552 |
| `tests/contract/settings_ui_harness.cjs` | `cc2e0514fa0ce8d62cf217f48ccf2b4f52c6e0a90a31d41170cc17d19ea00aba` | 84,831 |
| `src/uls/settings/credential_service.py` | `76537fcee777553cfd71c627b028d378ea666c091117956aed540f3468a5902c` | 34,900 |
| `src/uls/settings/credential_roles.py` | `f9f3ea23807747cc42ad7c08a9947ac255942619660218695e0e84eb58d763c9` | 4,222 |

The fingerprints above are read-only source bindings for this PLAN. They do not claim a complete implementation review, test run, Google login, or live School verification. Implementation is still blocked on independent Native/Gemini v8 PLAN dispositions, any D2/D3 review additions, and explicit parent integrated GO. No code, test, pin, ACL, global, environment, provider, or live School change was made, and no test command was run for this PLAN.

## D — Drive API privacy, derived-file validation, and release ownership

### D-owned implementation contract

v7의 `D unchanged Drive API/privacy`는 이 절로 대체한다. `src/uls/worker.py::DerivedDriveWriter`는 실제 `NativeWorker.process()`가 사용하는 write branch이므로 no-change가 아니다. SA-only로 격리하는 대안은 채택하지 않는다. 같은 worker branch가 OAuth worker credential로도 실행되므로, credential 종류를 근거로 약한 파일 검증을 허용하지 않고 `DerivedDriveWriter` 자체에서 frozen private/provenance 조건을 지킨다.

D3 동결 문서 검토는 private-copy나 원본 이동을 추가하지 않는 현재 경계를 확인했다. 이 OAuth 변경은 private-copy workaround, original-source move, public sharing을 도입하지 않는다. Derived output은 별도 staged file ID와 기존 provenance marker를 유지한다.

기존 `GoogleDriveWorkerAdapter` 경계를 재사용한다. 현재 `create_file_with_marker()`, `read_metadata()`, `require_private_ownership()`는 full metadata response, exact parent/MIME/marker, owner-only privacy, no shared-drive/broad sharing 및 worker capability를 확인한다. `DerivedDriveWriter`는 raw Drive create의 `fields='id'` 호출을 제거하고 다음 순서로 동작한다.

1. 기존 destination-folder fresh read 및 private ownership 검사 후 `create_file_with_marker(folder_id, entity_id + '.staged.md', 'text/markdown', content.encode('utf-8'), {'uls_entity': entity_id, 'uls_source': source_ref.file_id})`를 사용한다. 생성 반환 metadata의 ID를 고정한다.
2. 즉시 같은 ID로 `read_metadata()`를 다시 호출하고 생성 반환값과 live readback 양쪽에서 ID, 정확히 한 parent=`folder_id`, MIME, exact `uls_entity`/`uls_source`, `trashed is False`, `owned_by_me is True`, 개인 Drive(`drive_id is None`), permission readback 완전성 및 owner-only, broad-share false, `can_edit is True`, `can_move is True`를 검증한다. Missing/extra permission이나 capability가 불명확하면 통과시키지 않는다.
3. 그 ID의 content bytes를 다시 읽어 `content.encode('utf-8')`와 byte-for-byte 비교하고, publication 직전 metadata를 다시 읽어 같은 전체 identity/privacy/marker 조건과 content를 재확인한다. `SourceRef`는 이 검증을 통과한 동일 file ID로만 만든다. 검증 중 source_ref의 file ID로 덮어쓰기·이동하지 않는다.
4. `publish_staged_derived()`는 전달된 staged/source/entity identity를 다시 일치 확인하고 위의 fresh metadata+bytes 검증을 거친 후에만 `staged_ref`를 반환한다. 그 반환 뒤 기존 Notion writer만 URL pointer/Ready 효과를 낼 수 있다. 검증 실패·provider timeout·readback 누락·결과 모호성은 fail-closed/ambiguous이며 자동 Drive delete, ACL 변경, source move 또는 재시도를 하지 않는다. 이미 create가 dispatch됐을 수 있는 경우 새 파일을 임의 cleanup하지 않고 기존 worker failure 결과로 남기며 Notion pointer/Ready를 발행하지 않는다.

### D-owned regression and no-side-effect acceptance

`tests/integration/test_native_runtime.py`는 위 legacy `DerivedDriveWriter` 경로를 실제 fake `NativeWorker`에서 실행하는 주 contract regression이다. exact byte equality만으로 성공시키지 않고 다음 metadata/content cases 각각에서 Notion pointer publication 0회와 `Ready` 전환 0회를 assert한다: exact bytes+wrong parent, wrong MIME, `uls_entity`/`uls_source` drift, missing/true trashed, `owned_by_me` false, extra user 또는 service-account permission (`owner_only=false`), anyone/domain permission, shared drive, permission list absent/malformed, `can_edit` 또는 `can_move` false/missing, create response와 subsequent fresh readback 불일치, 그리고 create 후 publication 전에 metadata/content drift. Positive case는 exact ID/parent/MIME/markers/private capability와 bytes를 모두 read back한 뒤에만 pointer/Ready를 허용한다. 모든 negative case에서 permission update, ACL cleanup, source move, derivative auto-delete가 0이다.

`tests/unit/test_intake_registry.py`에 root/course registry의 privacy regression closure를 명시하고, 현재 shared-drive/trashed 거부 외에 extra user/SA, absent permission readback, broad domain/anyone share, edit/move false 또는 missing, permission/capability drift를 parameterize한다. Direct Drive-adapter 경계는 `tests/unit/test_c2_drive_marker_recovery.py`에서 동일 metadata semantics를 포함해 검증한다. `tests/contract/test_worker_cli.py`와 `tests/contract/test_mcp_runtime.py`는 기존 CLI/MCP privacy regression으로 함께 실행한다.

OAuth connect 및 OAuth Connection Test는 ACL·Drive file permission을 바꾸거나 School source를 이동하지 않는다. A-owned `tests/contract/test_settings_google_oauth.py`와 B-owned `tests/contract/test_settings_provider_checks.py`의 fake-service acceptance에서 permission create/update/delete와 source `files.update`/move effect counter를 0으로 확인하며, D owner가 이 결과를 D closure에 결속한다. `tests/integration/test_intake_worker_preview.py`의 intake 호출 순서, `Partial`, privacy acceptance는 D2 owner 소유로 유지한다. D의 통과를 D2/D3의 검토 완료나 live School acceptance로 해석하지 않는다.

Existing test service-account ACL을 자동 삭제하거나 완화하지 않는다. Live D write는 실제 fresh readback에서 `owner_only=true` 및 나머지 exact private/capability 조건을 만족해야 한다. OAuth 연결과 target readback 뒤의 서비스 계정 접근권 정리는 별도 exact-target fresh ACL readback 및 인간 승인이 필요한 후속 effect다. 그 승인이 오기 전 `owner_only=false`는 fail-closed다. Google service-account identity/key 자체를 삭제한다는 뜻은 아니다.

### D file closure and pin check

| Path | Current SHA-256 | bytes | D disposition |
|---|---|---:|---|
| `.insane-review/drive-oauth-20261005/native-plan-v7-D-Drive-API-frozen-privacy/harvest/response_harvest_20261006_025501_42930_cdfbbf.md` | `5c291c0bfad16e01771d43f95cd93dea9cc5c40e53884e6dec835e92d74d7de1` | 9,962 | Full response read; REVISE REQUIRED 3 / OPTIONAL 0 |
| `src/uls/worker.py` | `3c13828f8006b5c4ee01101e313c513c19f7fe70ca88dbce07bd44f28bc9c0f9` | 15,109 | D product change candidate: `DerivedDriveWriter` create/readback/publication validation |
| `src/uls/adapters/drive/worker.py` | `157dccadff17dcc0b65fa78fbe560506798a2bd96d8dd0bab7886d05bd9efa16` | 30,632 | Existing metadata/create/private-ownership boundary to reuse; unchanged unless review proves an exact gap |
| `src/uls/adapters/drive/google.py` | `4ba1b0929451ce84791b5ecc525e106b87233ce6f4722a64b6d18902ee0eb86f` | 3,547 | Related reader, read-only dependency |
| `src/uls/study_notes/drive.py` | `3fa1923897de52e68ef50298d4f3ef22cf75c06408aa608bd42a599cae7cdd52` | 22,765 | Stronger neighboring metadata/readback pattern, read-only reference |
| `src/uls/intake/worker.py` | `fa95cbc4c720ffb5677350c03eec663ff703c4162d4a9f27c90e0769d884d07d` | 131,637 | D2-owned call-order/Partial/privacy surface; unchanged by D |
| `tests/integration/test_native_runtime.py` | `63f8d651a567fd16265eb7b8c2fe449e6899d8f4d8cb85c16ccb8decec5fe939` | 17,061 | D test candidate: fake runtime derivative/readback/no-pointer cases |
| `tests/unit/test_intake_registry.py` | `f95e21483336a0b0dffd0f91fd8ed197b75a22cccd2d56400df0930f5bb37a07` | 8,543 | D test candidate: exact privacy/capability matrix |
| `tests/unit/test_c2_drive_marker_recovery.py` | `5a812b5416c2bc7252f867b9c755a07843be7ee096b146826428dc4c21032c44` | 13,312 | Direct Drive-adapter regression candidate |
| `tests/contract/test_worker_cli.py` | `61a8e947d63522c5760172c6128173a0f7d570b68189a6ac80b8d7d25bdbeba5` | 9,726 | Existing privacy/call-surface regression, run in D closure |
| `tests/contract/test_mcp_runtime.py` | `ea82e73347e26348e2e0854b7ac53177370fc22835043246e624913ddbdaa2d5` | 38,954 | Existing read-only MCP boundary regression, run in D closure |
| `tests/integration/test_intake_worker_preview.py` | `faf5fa7f2f3b9d5a83cd4160a7f07dbcf9ed09eac7f538f63161e2761891206e` | 32,386 | D2 ownership, separately required before overall release |
| `university-learning-system-v1.2-design-frozen.md` | `45499aa642a52d97b59995d4bd525be77b05cd7ea7944f62e7a41938336cd6fa` | 48,653 | Frozen privacy/provenance authority, unchanged |
| `university-learning-system-v1.2-implementation-spec-frozen.md` | `10ab4498946af4bbf4b3f0cd20772a2f965ea095c50ee26fc2f1682b44f0d11f` | 71,442 | Frozen implementation authority, unchanged |
| `contracts/study-behavior.md` | `987d09ec152f91e368e070c5ccbe961602a18da8b6113afd968ac965406aae1a` | 5,533 | Behavior Contract authority, unchanged |
| `scripts/review_evidence_checker_candidate/state_check.py` | `a82d0a88a2a35fe53293c433210029895a0e674d15231a2d9e1ed1a8f378ef08` | 27,911 | Active checker, read-only pin-count inspection: 17 compiled paths; `src/uls/worker.py` absent |
| `scripts/review_evidence_checker_candidate/source_pins.json` | `2e6dfae356072bb4c0228cca4ba7073d01819088b5b10b88abbb3a326eba6340` | 4,021 | Review-only inventory, not runtime authority; worker.py absent |

`worker.py` is not in the currently active compiled 17-path pin map or review-only inventory. The bounded D change therefore requires no checker/pin delta. Do not edit `state_check.py`, `source_pins.json`, or checker tests. A future proposal to pin `worker.py` is a separate exact-candidate, independent-review, human-decision gate; this plan does not authorize one. `src/uls/adapters/drive/worker.py` remains read-only; if the current adapter contract proves insufficient and a change there appears necessary, report that exact gap before any edit. The D2/D3 source/review scopes remain separately owned, and any new source needed outside this closure is reported before editing.

### D release gate (v8 normative)

The OAuth implementation release gate is A+B+C+D, not A+B+C only. The same owner binds D-owned test commands, actual collected/passed/failed counts, scoped lint/type results, and final SHA/size to the exact candidate source. At minimum the D regression command covers `tests/unit/test_intake_registry.py`, `tests/unit/test_c2_drive_marker_recovery.py`, `tests/integration/test_native_runtime.py`, `tests/contract/test_worker_cli.py`, and `tests/contract/test_mcp_runtime.py`; the exact A/B fake connect/check no-mutation tests are included in the combined acceptance record. D2's `test_intake_worker_preview.py` remains an additional independently owned required gate. The overall final candidate manifest uses the sorted union of every changed A/B/C/D product and test path; the C browser evidence and D regression result are each separately bound to that same current-source snapshot. No live provider readiness, School write, ACL cleanup, or real account grant can be inferred from these fake suites.

## B — shared OAuth commit composition invariant (Native v7 D3 REQUIRED 1)

This is now a REQUIRED B acceptance condition, not optional coverage. The production OAuth-enabled Settings composition creates exactly one process `OAuthRuntimeContext`, exactly one B-owned `_OAuthHandoffLedger`, and one canonical `CredentialService`; the exact same context/ledger/service identities are wired to every OAuth Settings commit entrypoint, including `POST /P/api/v1/google-oauth/{purpose}/{flow_id}/commit`. `composition.py` owns singleton construction/registration. A owns context type/lifetime; B owns the ledger type and atomic-consume semantics. The canonical `CredentialService` is the production route target; any additional OAuth-capable `CredentialService` is allowed only when explicitly injected with the same context and ledger, so all service consumers still share one replay authority. This supersedes any v7 reading that each service privately constructs its own ledger, but preserves v7 key, expiry, capacity, tombstone and process-epoch semantics. No entrypoint or per-request factory creates a replacement instance.

An OAuth-capable `CredentialService` receives both objects explicitly. There is no implicit private `_OAuthHandoffLedger` fallback in OAuth-enabled construction. A generic service built without OAuth capability may retain current generic/SA behavior but cannot be registered as an OAuth commit entrypoint. Any additional OAuth-capable service is accepted only with the identical context and ledger objects from the canonical composition. A second service with a fresh context or private ledger is rejected during construction/registration, before it becomes reachable from an app route. Existing generic save, service-account validation, and non-OAuth callers are unchanged.

### Required composition and cross-service regression

`tests/contract/test_settings_composition.py` asserts one production composition instance for each object and exact `is` identity at the service and every commit-route dependency. It constructs a second OAuth-capable service only with explicit injection of the canonical context/ledger. A deliberately fresh private ledger/context must fail at construction/registration and leave every effect counter at zero. The current service-level replay tests remain in `tests/contract/test_settings_credential_service.py`; cross-composition assertions belong to the composition contract suite.

Using one A-issued immutable authorization and the same candidate, submit through two Settings commit entrypoints or two explicitly shared-ledger service instances:

- Sequential A→B: exactly one `consume` succeeds and exactly one transaction begins. The replay loses before provider, admission, journal, store, config, or active effects.
- Barrier-synchronized concurrent A/B: exactly one `consume` and one transaction total; the losing call has provider/admission/journal/store/config/active effects all zero.
- Fresh-ledger second service: service registration fails before route exposure; both original and attempted services show no provider/admission/journal/store/config/active effects from that construction attempt.

This complements, rather than replaces, v7 TTL, fixed-capacity/tombstone, process-epoch, service-level sequential/concurrent, and crash/recovery tests. The factory test must prove actual shared object identity; equal values, same type, or separately constructed ledgers do not pass.

`src/uls/settings/composition.py` and `tests/contract/test_settings_composition.py` are the direct D3 source/test candidates. `src/uls/settings/credential_service.py` and `tests/contract/test_settings_credential_service.py` remain B-owned implementation/regression files from v7; the latter are currently compiled checker pins. Any changed bytes to pinned files leave the active checker at HOLD until the separately authorized exact-candidate review/human pin decision; v8 does not apply or refresh pins. The composition regression should be added to the unpinned composition suite where possible without weakening the existing pinned service tests.

| D3 input/current source | SHA-256 | bytes | Use |
|---|---|---:|---|
| `.insane-review/drive-oauth-20261005/native-plan-v7-D3-frozen-authorities/harvest/response_harvest_20261006_031501_50723_447f3e.md` | `1ffde01bb2aefde72ae5fcb24e5a40cf46ea64f7f18f37ea2068ca2c714aae52` | 10,003 | Full original-bound response read; REVISE REQUIRED 2 / OPTIONAL 0 |
| `src/uls/settings/composition.py` | `451835ba4a70bac795c054dff00e89efa9fae5b81546a90e1537fa0d9d51ab04` | 3,859 | Current production service composition; change candidate |
| `src/uls/settings/credential_service.py` | `76537fcee777553cfd71c627b028d378ea666c091117956aed540f3468a5902c` | 34,900 | Existing ledger/transaction implementation; B-owned candidate, pinned |
| `tests/contract/test_settings_composition.py` | `680cabe47a208f7ad3e189cf04139f79a740a8a114009b2b6120868eed714af3` | 19,039 | D3 object identity/construction/routing regression candidate |
| `tests/contract/test_settings_credential_service.py` | `1eeae0b30657492c8d67a829acf41f16398075f9527c891854b6af4dff4520e2` | 38,729 | Existing B service replay tests; compiled pin, retain semantics |

---

## v7 inherited contract (verbatim after its original title; subordinate only where the v8 C/D overrides above say so)


상태: Native v6 B 원본은 canonical harvest와 15 source/current/body audit를 마쳤고 공식 판정은 REQUIRED1·OPTIONAL1이다. Native 기록에 연결된 Gemini v6 supplements와 active approved17 checker consistency exit0도 확인됐다. 이 v7은 v6의 R2 one-shot replay-state 공백과 digest 명칭만 보완하는 PLAN-only다. 이는 implementation GO나 전체 Drive OAuth acceptance가 아니다.

## v7의 우선순위와 검토 근거

이 문서는 v6 전체를 포함한다. 아래 R2 절이 v6 §R2의 one-shot 소유권·preflight·clock·replay 검사와 candidate digest 명칭을 대체하며, 나머지 v6 계약은 유지한다. v6 원본 bytes는 수정하지 않는다.

- 동결 v6: `docs/plans/drive-oauth-nondeveloper-v6.md`, SHA-256 `59b3477d8668ae5d67d50186955088918ae35fe7e77b3fbce2a3a8a911f993e0`, 48,994 bytes.
- Native v6 B original response: `.insane-review/drive-oauth-20261005/native-plan-v6-B-protected-transactions/response_Syllva_20261005_231440_61089_94f35f.md`, SHA-256 `f729046ef1e36bc7a421ffe165d8353055447515fe25e5507c1158144908f5c7`, 6,499 bytes. Canonical response `.insane-review/drive-oauth-20261005/native-plan-v6-B-protected-transactions/harvest/response_harvest_20261005_233446_62087_1e6cca.md`는 같은 SHA이며 body SHA-256 `eab43606b9cfb88a72760a4d94fa76a38b59c13619d955199139ff9fa39179a4`다. Original-bound source/model/body audit, 15 current sources, REQUIRED1/OPTIONAL1은 `.insane-review/drive-oauth-20261005/native-v6-b-required-checkpoint.json` (SHA-256 `9763ca20cd238dbd002c35d6c8fc88a3275e6215993f9b63b4e5c942e2a57845`, 2,477 bytes)에 기록돼 있다. 그 기록의 active17 consistency exit0은 evidence consistency이지 제품 acceptance가 아니다.
- R2에서 읽어 대조한 current source: `credential_service.py` save 271–314 및 recovery 511–591; `journal.py` `_IMMUTABLE_FIELDS` 266–273 및 `JournalStore.create_operation()` 566–636; `credential_stores.py` `value_id()` 110–113. Read-only fingerprint는 아래와 같다.

| 경로 | SHA-256 | bytes |
|---|---|---:|
| `src/uls/settings/credential_service.py` | `76537fcee777553cfd71c627b028d378ea666c091117956aed540f3468a5902c` | 34,900 |
| `src/uls/settings/journal.py` | `9d9b512e13f84dc45da305b21364d6b218264f5909e5a3853a23171c0a847417` | 58,827 |
| `src/uls/settings/credential_stores.py` | `6ec843bcf25ef2046d3a580180468e658f923a816760c89a55eda7c5c7b88896` | 5,608 |
| `tests/contract/test_settings_credential_service.py` | `1eeae0b30657492c8d67a829acf41f16398075f9527c891854b6af4dff4520e2` | 38,729 |

Native v6 R2 판정은 B-owned process-local atomic replay ledger, exact non-secret authorization key, effect 전 consume, 만료까지 tombstone 보존, consume 뒤 실패에도 재사용 금지와 순차/동시 회귀를 요구한다. 선택 보완으로 OAuth raw candidate digest, 기존 journal config candidate hash, staged credential state ID를 서로 구분하고 journal schema 변경을 금지한다. 이 v7은 그 두 항목만 닫는다. R1/R3/O1/O2 및 ordinary OAuth 10-call, SA 8-call, generic SA-only 경계는 v6에서 재검토돼 추가 필수 지적이 없었고 변경하지 않는다.

## v6 보완의 규범 우선순위와 출처

이 문서는 v5 전문을 포함하고 그 중 B1, B3, B5, B6, B2 external test matrix를 아래 계약으로 대체한다. O1/O2는 추가 회귀 규약이다. 다른 v5 규약과 A/B/C/D closure는 유지한다. 충돌 시 이 v6 절이 우선하며 기존 v5 bytes는 바꾸지 않는다.

동결 v5: docs/plans/drive-oauth-nondeveloper-v5.md, SHA-256 149dba3bf0f260d79eaee0be1dcd3d739103867fad2e11e72a1dad659a7a05a7, 34,459 bytes. 원본 owner 제출본과 path correction receipt는 별도 보존한다.

Native B 원문: .insane-review/drive-oauth-20261005/native-plan-v5-B-protected-transactions/response_Syllva_20261005_221142_55065_db5aa1.md, SHA-256 145a309eaac4f6d8f8c97f5ca95f5226c546ca84cd8264f15350a8fca137f0a8, 11,966 bytes. Manifest SHA-256 6a642b9ae81b49f013d5e345e31c0b070f2d9d76f86955503d8bd2cfc080c162, 2,275 bytes. 응답은 REQUIRED3/OPTIONAL2이며 original-bound COMPLETE다. 사용자 보고상 canonical --harvest는 native_guard 점유 중 BlockingIOError로 제출 전에 막혔다. 재전송하지 않는다. Formal disposition과 implementation GO는 pending이고, 원본은 technical input으로만 사용한다.

실제 source 근거는 src/uls/settings/credential_service.py의 save 271–314, _continue 326–357, recover 511–591, _separate 110–175, src/uls/settings/provider_checks.py의 TransportCheckBudget 67–85, _google 241–255, ProviderChecks.check 307–344, _check 346 이후다. current17 inventory 및 v5에서 읽은 source SHA는 보존 기준이다.

## R1 — fresh save와 recovery lock 구간

Fresh save를 단일 admission→role→operation→config 순서로 기술하지 않는다. 실제 CredentialService.save() 순서는 다음과 같다.

1. Pair admission context가 admission/physical guard를 획득하고 journal role lock을 획득한다.
2. Initial ConfigFileLock 안에서 config bytes/generation, effective source/presence, managed target, peer snapshot, consent, payload/binding, staged/backup 슬롯을 확인한다. 이 단계의 _separate는 pure parsing/snapshot 검사이며 network를 하지 않는다.
3. 같은 initial config lock 안에서 admission marker publish와 journal.create_operation()을 수행한다. 이 시점에는 named operation lock을 아직 잡지 않았다.
4. ConfigFileLock을 놓고 admission/physical 및 role lock은 유지한다. 그 다음 journal.operation(operation_id)을 획득한다.
5. Named operation 아래 credential_stage를 기록·적용하고 provider verification을 수행한다. Fresh verify 동안 role+operation은 held, config는 released다.
6. Verify 실패 cleanup은 role+operation을 유지한 채 ConfigFileLock을 재획득해 reject/abandon branch, staged_delete와 terminal update를 처리한다. 기존 admission release 순서를 따른다.
7. 성공 시 _continue()는 role+operation held 상태에서 호출되고 함수 내부가 ConfigFileLock을 얻어 CAS/effects를 수행한다. 종료 후 기존 순서대로 release한다.

Recovery는 별도 순서다. Read-only journal/action 검증 뒤 pair recovery admission을 얻고 role lock→same named operation lock→ConfigFileLock을 획득한다. 이 동시-held 구간에서 live record/config/store/effect/generation/stage 조건을 확인하고 필요하면 staged candidate를 verify한다. 해당 구간을 끝낸 뒤 admission은 유지하되 role/op/config를 해제한다. 다음으로 role lock과 동일 operation lock을 다시 획득하고 _continue()를 호출하며, _continue()가 config lock을 새로 얻는다. 두 구간 사이에 role/operation이 계속 held라고 하지 않는다. Terminal recovery도 first held region 안에서 처리된다.

새 lock-event recorder 회귀는 fresh enrollment/replacement와 recovery를 각각 계측한다. 각 lock acquire/release, admission publish, create_operation, credential_stage, verify, cleanup, _continue 및 config CAS 시점의 held-lock set과 순서를 assertion한다. Fresh path에서 create_operation은 initial config lock 안/operation lock 전, stage와 verify는 operation 아래/config 밖이어야 한다. Recovery의 live-check/verify held set과 release/reacquire 후 continuation held set을 별도로 증명한다. Crash point별 active/staged/backup/config/journal raw bytes, admission state, exact cleanup/release도 검사한다.

## R2 — A-owned authorization과 B-owned one-shot replay ledger

### 발급 권한과 immutable handoff

`src/uls/settings/google_oauth.py`의 A flow/session owner만 `GoogleOAuthCommitAuthorization`을 발급한다. A는 live session epoch·flow ID/epoch·supersession을 유일하게 판단한다. Commit POST에서 awaiting_commit을 atomic하게 committing으로 전이한 뒤 authorization을 한 번 발급한다. Committing 이후 같은 flow에 authorization을 재발급하거나 flow를 supersede하지 않는다. B는 A registry를 조회하지 않는다. B 오류 뒤 동일 flow로 새 authorization을 만들지 않고 A는 결과를 failed/terminal로 정리한다.

Immutable authorization의 필드는 role/purpose, `server_process_epoch`, flow ID/epoch, session epoch, captured config generation, configured client ID, callback의 canonical actual-granted-scope tuple, fresh Drive permission ID, `oauth_candidate_sha256`, `issued_at_monotonic`, `expires_at_monotonic`, `user_replace_authorized`다. Token/code/refresh token/credential bytes는 객체 밖에도 로그·HTTP·journal에 쓰지 않는다. `server_process_epoch`는 server composition이 프로세스 시작 때 한 번 생성하는 non-secret epoch이며, A와 B에 동일하게 전달하고 disk/API/log에는 저장·출력하지 않는다.

### Clock domain, B ledger 소유권과 제한

`OAuthRuntimeContext`의 type은 A 소유 `src/uls/settings/google_oauth.py`에 두고, `src/uls/settings/composition.py`가 한 번 생성해 A와 B에 같은 instance를 주입한다. A와 B는 이 동일 context를 사용하며 요청마다 새 context를 만들지 않는다. Production monotonic source는 `time.monotonic()`이며 테스트는 A와 B에 동일한 fake clock/context 객체를 주입한다. `issued_at_monotonic`과 `expires_at_monotonic`은 이 clock domain에서만 비교한다. Wall clock 변환은 하지 않는다. Flow TTL 상한은 기존 300초다. 발급과 B 진입에서 `issued_at_monotonic <= now_monotonic < expires_at_monotonic` 및 `0 < expires_at_monotonic - issued_at_monotonic <= 300s`를 확인한다. 새 process는 새 `server_process_epoch`와 clock context를 만들어 이전 process의 object를 항상 거절한다. Monotonic 숫자를 process 경계 밖으로 복원하거나 journal에 저장하지 않는다.

B의 유일한 replay-state owner는 `CredentialService`가 보유한 process-local `_OAuthHandoffLedger`다. Production composition은 요청마다 service/ledger를 만들지 않고 한 `CredentialService`를 구성해 모든 Settings route에서 재사용한다. 같은 OAuth runtime에 추가 service instance가 필요한 경우에도 동일 ledger instance를 주입하며 instance별 빈 ledger를 허용하지 않는다. Ledger는 thread-safe atomic consume, 고정 상한 256개, entry당 exact key와 `expires_at_monotonic`만 저장한다. Key는 `(server_process_epoch, role.slug, purpose, session_epoch, flow_id, flow_epoch)`이며 secret이나 credential bytes를 포함하지 않는다. 만료된 항목만 `now >= expires_at_monotonic` 이후 제거한다. 미만료 tombstone은 공간 압박·오류·취소·후속 transaction 실패로도 조기 제거하지 않는다. 256개 모두 미만료이면 새 consume은 fail-closed `OPERATION_IN_PROGRESS`로 거절하고 기존 항목을 축출하지 않는다. Tombstone은 해당 capability 만료 때까지 살아 있다. Process restart 후 ledger가 새로워도 old object는 process epoch 검사에서 거절된다.

### `save_google_oauth()` preflight 및 consume 순서

유일한 B API는 `CredentialService.save_google_oauth(role, candidate_bytes, authorization, ...)`다. Generic `save()` 시그니처/동작은 그대로이며 HTTP/CLI/browser는 authorization 또는 parser mode를 만들거나 고를 수 없다. B는 순서대로 아래 작업을 수행한다. 이 preflight 전 구간은 provider/network, `_admission_context`, admission publish, journal, store, config CAS 효과가 없는 읽기/검사다.

1. A factory의 정확한 immutable authorization type/version, 필드 타입·정규형 및 현재 `server_process_epoch`를 검증한다.
2. Method의 Google role/purpose와 authorization role/purpose/client를 일치시킨다. 현재 ConfigStore의 read-only snapshot에서 config generation과 purpose client ID를 읽어 authorization 값과 비교한다. Snapshot mismatch면 거절한다.
3. Raw `candidate_bytes`를 strict six-key OAuth parser로 검사하고 `SHA256(candidate_bytes)`를 `authorization.oauth_candidate_sha256`와 constant-time 비교한다. Mismatch/invalid bytes는 ledger consume 전에 거절한다.
4. 동일 injected monotonic clock으로 발급·만료 시각, 300초 최대 TTL, 현재 만료 여부를 검사한다.
5. `_OAuthHandoffLedger.consume(key, expiry, now)`를 하나의 lock critical section에서 수행한다. Key가 이미 소비됐거나 ledger가 가득 찼으면 거절한다. 성공 시 tombstone을 즉시 기록한다.

Ledger consume 성공 뒤에만 기존 admission→role lock→initial config lock/save transaction에 진입한다. Initial ConfigFileLock 아래 publish/create 바로 전에 만료·generation·configured client·role/source/presence를 다시 읽어 전부 exact-match 확인한다. Lock 대기 중 만료, config drift, provider 오류, admission/journal/stage/CAS 오류 등 consume 뒤 어떤 실패도 tombstone을 삭제하거나 capability를 재사용 가능하게 만들지 않는다. Preflight 뒤/lock 안의 mismatch는 publish/create 전에 실패하며 기존 v6 cleanup/recovery semantics를 따른다. OAuth save의 다른 단계 및 R1 lock held-region은 v6 그대로다.

Candidate authorization preflight rejection, 이미 소비된 key, wrong process epoch, 만료, 잘못된 role/client/config/digest는 provider, admission publish, journal create, stage/backup, config commit, active mutation 각각 0이다. 첫 consume 뒤 실제 save가 시작된 다음 fresh server scope 또는 permission ID가 callback-attested value와 다르면 v6의 stage→verify 순서에 따른 reject cleanup을 적용한다. 이는 최종 active/config/external mutation 0을 뜻하며 이미 생긴 stage/journal/provider read를 없는 것으로 주장하지 않는다.

### Required/optional replay·digest regression

- Exact A-issued object + exact candidate positive: 같은 authorization key는 replay ledger를 정확히 한 번 통과한다.
- Sequential replay: 첫 시도 후 같은 object/bytes를 다시 전달하면 pre-effect에서 거절한다. 두 번째 시도 직전 baseline 대비 provider 호출, admission publish, journal create, stage/backup, config CAS, active bytes delta가 모두 0이다.
- Concurrent replay: barrier로 같은 key/object를 두 thread가 동시에 제출한다. Ledger lock 아래 정확 한 호출만 consume gate를 통과하고 다른 호출은 pre-effect 거절된다. 거절된 호출의 provider/admission/journal/store/config 변화는 0이다.
- Post-consume lock wait expiry와 post-consume config generation/client drift 각각을 주입한다. 첫 호출은 durable publish/create 이전 거절되고 tombstone은 expiry까지 유지된다. 같은 authorization retry도 pre-effect 거절되며 capability는 되살아나지 않는다.
- Provider/transaction error, admission error, stage failure 등 consume 뒤 실패도 tombstone을 유지한다. 독립된 다른 live flow key는 정상 통과할 수 있다.
- Wrong role/purpose/client/generation/process epoch, wrong candidate bytes/digest, malformed/extra-key payload, expired/future-issued/TTL>300s object는 consume 전에 거절되고 모든 provider/admission/journal/store/config/active 효과가 0이다. Current config가 사전 snapshot 후 lock 재검증 전에 바뀌는 race도 거절된다.
- 만료 전 key는 ledger 용량이 차도 축출되지 않는다. 256-entry capacity에서 257번째 distinct unexpired key는 fail-closed; `now >= expiry`인 entry만 prune하여 capacity를 회복한다. 용량 초과 시 provider/durable effect는 0이다.
- Process restart/new `server_process_epoch` 뒤 old handoff는 A registry 조회에 의존하지 않고 B preflight에서 거절된다. 이미 staged된 transaction recovery는 flow/ledger를 요구하지 않고 기존 journal 및 exact staged-state binding으로 새 transaction context에서 진행된다.
- Optional naming assertion: `authorization.oauth_candidate_sha256 = SHA256(exact raw authorized_user candidate bytes)`는 A→B in-memory preflight 전용이다. 기존 `journal.candidate_hash = SHA256(serialized config candidate payload bytes)`는 config CAS/recovery fingerprint로 유지한다. `credential_stage`의 기존 `stores.value_id(role, exact credential bytes)` intended state ID는 credential store binding으로 유지한다. 세 값은 대체/혼용하지 않고 journal/admission schema field는 0개 추가한다. Restart recovery는 journal의 config candidate hash, recorded staged state ID, protected staged bytes를 현재 resolver로 대조하며 OAuth raw digest나 authorization object를 journal에 복제하지 않는다.

## R3 — peer parser 권한, mixed-type predicate, transaction aggregate

Candidate parser와 peer parser capability를 분리한다. parse_authorized_user_for_google_oauth는 OAuth candidate save, OAuth recovery validation, persisted OAuth Connection Test에만 허용된다. 새 private parse_google_peer_for_separation은 CredentialService._separate()의 code-owned peer path만 호출한다. 따라서 generic service-account save/recovery가 opposite-purpose OAuth peer를 검사할 수 있지만, OAuth candidate를 generic save()로 저장할 수는 없다. Browser, HTTP, CLI는 candidate kind나 parser mode를 선택할 수 없다. SA candidate는 계속 structural_credential의 SA-only 검증을 받는다.

Predicate:
- SA↔SA: 현재 _separate의 client_email/private_key_id 비교 semantics와 기존 SA-SA positive/conflict를 그대로 보존한다.
- OAuth↔OAuth: 각각 exact purpose schema/client/grant로 검증하고 same fresh permission ID를 요구한다. Cross-purpose client, refresh grant 및 exact credential blob 재사용은 거절한다.
- SA↔OAuth 양 방향: 각 payload는 자기 type/schema로 검증한다. Cross-type account equivalence를 요구하거나 주장하지 않고 SA에 OAuth field를 요구하지 않는다.
- Effective selected peer와 canonical managed peer를 각각 검사한다. Raw bytes가 exact-identical이면 한 distinct peer로 dedupe한다. Wrong scope/client, malformed/unreadable/unknown peer는 first store mutation 전에 거절한다.

한 Google save/recover invocation은 candidate type과 관계없이 하나의 GoogleTransactionVerificationContext를 소유한다. 14-wire-call cap, 30초 deadline, per-call socket max 10초, redirect/retry 0 및 response/frame 1MiB cap을 _separate/_verify/ProviderChecks 호출 전체에서 공유하며 helper마다 재설정하지 않는다. Initial config lock의 _separate는 peer typed parse/snapshot과 attestation target 등록만 수행하고 network하지 않는다. Named operation stage/verify에서 peer/account와 candidate provider proof를 같은 context로 수행한다. Repeated _separate와 _continue의 config-locked final check는 pure fingerprint revalidation이며 second network attestation을 만들지 않는다.

Accounting:
- OAuth candidate + 두 distinct OAuth peers + target: candidate refresh/about 2 + peer attest 2+2 + target GET 최대 8 = 14.
- OAuth candidate + SA peer: OAuth candidate 2 + distinct OAuth peers 최대 4 + target GET 최대 8. SA peer parse는 wire 0.
- SA candidate + 두 distinct OAuth peers + SA provider verification: peer attest 최대 4 + 기존 SA verification 최대 8 = 최대 12; 모두 transaction의 shared 14 cap에 반영한다. SA verifier의 기존 8-call subcap도 유지한다.
- SA candidate + SA peer는 OAuth peer attest 0, existing SA verification 최대 8.
- Effective/canonical OAuth raw bytes 동일 시 그 peer refresh/about는 한 번만 수행한다.

Tests: OAuth candidate+SA peer, SA candidate+OAuth peer 각각 positive/negative; effective OAuth와 canonical OAuth가 다를 때 두 peer 모두 attested; identical-byte dedupe; malformed/wrong scope/unreadable peer denial; SA-SA prior cases; 14/15 boundary; SA candidate+두 OAuth peers+8-call SA check 총합; repeated _separate/helper가 budget을 reset하지 않는지 검사한다. No token/permission ID/raw bytes in journals or logs.

## O1 — ProviderChecks 공통 lifecycle/result wrapper

ProviderChecks의 공통 _run_check(role, operation) 한 곳이 _active, per-role _last/cooldown, results, safe exception mapping, checked_at 및 cleanup을 소유한다. Generic check(), OAuth check_google_oauth_connection_test() 및 B transaction verification 모두 같은 wrapper를 호출하고 각자는 operation만 제공한다. Public generic signature는 유지된다. SA Connection Test는 legacy 8-call budget, ordinary OAuth test는 독립 test-local 10-call context, transaction verification은 caller-owned shared 14-call context를 쓴다. Wrapper가 어느 context도 새로 만들거나 reset하지 않는다.

Race regression: 같은 role의 OAuth check가 blocking일 때 SA/direct check는 CHECK_BUSY; 반대 방향도 동일; busy 요청은 _active/_last/results를 변경하지 않고 기존 result를 덮지 않는다. Success/failure 이후 각 path는 같은 cooldown와 sanitized results map을 사용하고 permitted completion은 role당 한 번만 기록한다. 다른 role key는 독립이다. Timeout/provider exception 모두 동일한 safe code projection, secret-free result를 사용한다.

## O2 — external origin을 분리한 matrix

B2 acceptance는 external_file과 environment 각각을 독립 origin으로 시험한다. 각 origin에 managed sink absent/stale-active × user_replace_authorized false/true와 source/config/target race를 적용한다. Environment는 fake injected mapping만 사용한다.

Expected matrix:
- external_file + managed absent + consent false: 기존 external credential이 effective이므로 transaction effect 0.
- external_file + managed absent + consent true: 승인된 managed enrollment/config CAS만 허용; external file raw bytes 불변.
- external_file + stale canonical managed active + consent on/off: hidden managed bytes overwrite 금지, fail closed.
- environment + managed absent + consent false: transaction effect 0.
- environment + managed absent + consent true: 승인된 managed enrollment/config CAS만 허용; injected environment value 불변.
- environment + stale canonical managed active + consent on/off: hidden bytes overwrite 금지, fail closed.
두 origin 모두 missing/unreadable은 absent로 강등하지 않는다. Fake environment/config가 managed locator와 같은 문자열을 가리켜도 origin은 external/environment로 유지하고 sink 재분류를 금지한다. Initial config 이후 origin/generation/presence/target race와 CAS 직전 drift를 넣고 external source bytes, active/staged/backup/config/journal raw bytes 및 release state를 확인한다. 이 Optional 보완은 새 UX/permission scope를 추가하지 않는다.

## Native v5 판정 항목 매핑

- v5 REQUIRED1: dedicated ordinary OAuth 10-call Connection Test 및 legacy SA 8-call 유지 — v5 B4, v6 O1 wrapper ownership.
- v5 REQUIRED2: generic parser/save SA-only와 direct valid OAuth zero-effect/HTTP denial — v5 B5, v6 R3는 peer-only exception만 추가.
- v5 OPTIONAL1: exact six-key schema가 v2 purpose metadata를 대체 — v5 규칙 유지.
- v5 OPTIONAL2: 다음 reviewer packet에 tests/contract/test_settings_provider_checks.py 전문 — v5 review-input closure 유지.
- 새 REQUIRED R1/R2/R3와 OPTIONAL O1/O2는 위 lock trace, A-owned typed factory, parser/budget matrix, common wrapper, external-origin regressions로 닫는다.
이 mapping은 Native v5 원본의 canonical harvest나 formal acceptance를 대신하지 않는다.

## 근거와 상태

동결 기준:
- v2: docs/plans/drive-oauth-nondeveloper-v2.md — SHA-256 6a1924274508ebd535052e6a93e038b1a916dc904ddda34a38ffefe1ab613b98, 18,941 bytes.
- v3: docs/plans/drive-oauth-nondeveloper-v3.md — SHA-256 33e890ee72ea9565ae93ac97226cb3b09ca7cb117cf47405ec9815fcbb93a080, 16,087 bytes.
- v4: docs/plans/drive-oauth-nondeveloper-v4.md — SHA-256 69a155f7bf43981c1b65dca434873dcb996aece7b1f7319a1ea6b7368358733b, 30,751 bytes.
- Current source inventory: .insane-review/drive-oauth-20261005/drive-oauth-v4-source-inventory-current17.json — SHA-256 6aa727caee5719b01238297399d468f29d6cbd39c282d9605527fba6fa79e57a, 20,433 bytes, 73 entries.

Native B 원문은 .insane-review/drive-oauth-20261005/native-plan-v4-B-protected-transactions/response_Syllva_20261005_211249_49454_ef3002.md — SHA-256 25461a389ce8b3746605ce0712265c56553f9a7ea8ccaaa2d0762ad69ef7d02d, 9,337 bytes다. Original response, manifest와 body/source/model audit은 일치한다. Audit는 canonical harvest 및 formal disposition 미완료, implementation GO 없음으로 명시한다. 따라서 이 원문은 technical input으로만 쓴다.

현재 읽은 경로에서 CredentialService.test()는 ProviderChecks.check()로 위임하며, ProviderChecks._check()가 legacy TransportCheckBudget을 생성한다. Google structural_credential()은 service_account payload만 허용한다. v5는 이 generic 경계를 그대로 두고 OAuth용 내부 경로를 분리한다.

B 직접 소스/테스트의 현재 binding:

| 경로 | SHA-256 | bytes |
|---|---|---:|
| src/uls/settings/credential_service.py | 76537fcee777553cfd71c627b028d378ea666c091117956aed540f3468a5902c | 34,900 |
| src/uls/settings/provider_checks.py | 6f001daeb292af029cf1c6732c08b7f189ceb0d9dcb8a69612d96ef90303466d | 17,855 |
| tests/contract/test_settings_credential_service.py | 1eeae0b30657492c8d67a829acf41f16398075f9527c891854b6af4dff4520e2 | 38,729 |
| tests/contract/test_settings_provider_checks.py | bfdc141062eca33ed97ed76f1d1b23cefb7bc4bc56d987f7970e45595551eb9e | 35,592 |

같은 담당자 gpt-6-luna/max가 계획, 이후 구현과 관련 수정까지 연속 책임진다. 프로토콜·credential 경계·transaction이 결합되어 기존 소유자가 맥락을 유지한다. 실제 model-selection hook은 도구 결과에 나타나지 않아 unobserved다. 모델 변경은 없다.

## 사용자 결정과 고정 범위

Purpose scope는 MCP 검색용 https://www.googleapis.com/auth/drive.readonly, WORKER 자료 처리용 https://www.googleapis.com/auth/drive다. drive.file 및 Picker는 제외한다. 두 scope는 Drive 전체 권한이다. School root/course IDs는 Syllva app의 대상 allowlist일 뿐 Google permission을 폴더 한정으로 만들지 않는다.

MCP와 WORKER는 별도 credential, OAuth client ID, Cloud project를 쓴다. Google project-wide revoke 영향을 독립 철회로 표현하지 않는다. Remote MCP의 openid email 로그인/token은 별도이고 Drive OAuth에 재사용하지 않는다. 이번 disconnect는 로컬 연결 해제와 Google 계정의 앱 권한 관리 안내뿐이다. Remote revoke, ACL 자동 변경, worker 자동 활성화, service-account identity/key 삭제는 제외다.

사용자는 테스트용 service-account 접근권을 OAuth 연결 완료 후 정리하는 방향을 선택했다. 실제 OAuth 연결 후 exact target ACL을 새로 읽고, 접근 변경은 대상과 효과를 명시한 별도 인간 승인 뒤에만 실행한다. 현재 ACL과 owner_only 조건을 유지한다. Desktop OAuth app/client가 준비되지 않으면 OAUTH_APP_NOT_READY다. client ID, 동의, USER attribution, School write/readback을 지어내지 않는다. App 등록, login/consent, target/ACL, 실제 School 흐름은 후속 acceptance dependency다. 실제 전체 흐름 전에 PR/commit을 만들지 않는다.

docs/plans/notion-live-schema-compatibility-20261005.md는 SHA-256 fd98519f230c183511688a34f69c4586e04f769dd60458ad424b2302dd96c9d3, 8,573 bytes로 보존한다. 이 작업은 Notion parser/REST/MCP token이나 Canvas 인증을 바꾸지 않는다.

## 이전 계약 충돌의 우선순위

v5가 v2/v3/v4 규약을 standalone로 통합한 기본 규칙을 v6/v7에서도 유지한다. v7은 위 R2 one-shot 조항만 대체하며 원본 문서는 수정하지 않는다.

| 주제 | 유지되는 기본 규칙 |
|---|---|
| Poll/callback 저장 해석 | v3의 CSRF 보호 POST commit만 저장 가능. GET poll, callback, result는 save 0회. |
| v2 purpose metadata | v4 R4의 authorized_user 정확 6-key schema가 대체한다. Persisted purpose metadata 없음. |
| v3 save 10-call 설명 | v4 save/recover 14-call aggregate가 대체한다. Callback 2, ordinary OAuth test 10, 기존 SA test 8은 별도. |
| Generic Google parser/save | 기존 generic parser 및 generic service save는 service-account-only. OAuth는 code-owned internal capability만. |
| SDK access token | Code exchange/save verifier child는 access token을 반환하지 않는다. Runtime refresh child는 검증 후 capped private pipe로 부모의 in-memory public adapter에 전달 가능. Browser/API/log/journal/disk 노출 금지. |
| Fetch Metadata | v4 최종 4-header 규칙을 적용한다. 이전 three-header shorthand는 적용하지 않는다. |

다른 v2/v3/v4 계약은 아래 세부 규칙을 따른다.

## OAuth bundle과 일반 흐름

배포자 config는 google_oauth.mcp 및 google_oauth.worker로 분리하고 각 bundle은 client_id, client_secret, cloud_project_id만 받는다. Endpoint, role scope, redirect, purpose 및 parser mode는 config/browser가 바꾸지 못한다. Desktop client secret은 public-client 설정값이며 refresh token과 구분한다. UI, status, URL, errors, logs에 노출하지 않는다.

OAuth section 또는 purpose bundle 전체 부재는 유효 config다. 앱이 시작하고 해당 목적만 OAUTH_APP_NOT_READY로 보인다. Partial bundle, wrong type, empty value, unknown key, purpose 간 client/project 공유는 시작 전 fatal validation이다. Missing과 malformed를 같은 상태로 합치지 않는다. Remote MCP bundle을 재사용하지 않는다.

화면은 검색과 자료 처리의 목적 및 Google 권한을 동의 전에 분리해 설명한다. 일반 사용자에게 Cloud Console, client 값, JSON 제출을 요구하지 않는다. 기존 Advanced service-account import와 external/environment 설정 호환은 유지한다. Cancel, 거부, timeout, failure 시 기존 credential을 보존한다. Browser close 즉시 탐지를 약속하지 않고 explicit cancel 또는 300초 TTL로 끝낸다.

## API, lifecycle, callback commit

새 module은 src/uls/settings/google_oauth.py다. 실제 endpoint, role, scope 및 callback path는 code-owned다. Clock/transport/CSPRNG/browser opener는 테스트 주입만 허용한다. Process당 purpose별 active flow 1개, 총 2개, TTL 300초다. Duplicate begin은 기존 flow를 암묵 교체하지 않고 busy를 반환한다.

| Method/path | 보호/요청 | 효과 |
|---|---|---|
| POST /P/api/v1/google-oauth/{purpose}/begin | same-origin/session/CSRF/explicit activity, 정확히 두 key인 {"generation":"<current-generation>","replace":true 또는 false} | code-owned purpose/client/scope로 flow 생성 |
| GET /P/api/v1/google-oauth/{purpose}/{flow_id} | flow를 시작한 원래 session | read-only status poll만, provider/state/activity/candidate/save 변경 0 |
| POST /P/api/v1/google-oauth/{purpose}/{flow_id}/cancel | same session/CSRF, exact empty JSON object {} | 미커밋 flow 취소. Committing은 journal 결과를 기다림 |
| GET /P/oauth/google/callback | 좁은 callback gate | code exchange 및 identity 확인, save 0, query-free 303 |
| GET /P/oauth/google/result | fixed no-data document gate | 고정 안내만, cookie/session/flow/query/data 없음 |
| POST /P/api/v1/google-oauth/{purpose}/{flow_id}/commit | same-origin/session/CSRF/explicit activity/MutationBarrier | 유일한 save; body 정확히 {"generation":"<captured-generation>"} |

전이는 pending → exchanging → awaiting_commit → committing → complete이며 commit 전 cancelled/denied/expired/failed terminal이 가능하다. Callback은 awaiting_commit까지만 만든다. Poll은 상태 read뿐이다. CSRF/body 검증 실패는 candidate를 소비하지 않는다. Barrier 안에서 owner가 session epoch, role/purpose/client, config generation, expiry 및 flow epoch를 확인하고 awaiting_commit → committing을 한 번만 한다. 동시 duplicate commit의 총 service save는 정확히 1회다. Process restart는 미커밋 memory flow를 무효화하나 durable journal operation은 기존 recovery가 맡는다.

State와 PKCE verifier는 서로 독립한 secrets.token_bytes(32)에서 생성해 padding 없는 base64url 43자로 만든다. Challenge는 S256이다. Authorization URL은 fixed HTTPS endpoint 및 exact query allowlist: client_id, exact redirect_uri, response_type=code, purpose scope, state, code_challenge, code_challenge_method=S256, access_type=offline, prompt=consent. Caller가 endpoint/query를 주입하지 못한다. 실제 refresh token이 응답되지 않으면 현재 credential을 유지하고 실패한다.

Callback query cap은 8,192 bytes; state는 43 ASCII; decoded code 1–4,096 bytes; error 최대 64 ASCII다. 성공은 state+code, 오류는 state+error의 정확한 조합만 받는다. Duplicate/unknown key, control chars, cap 초과는 교환 전에 거부한다. Callback은 lock 안에서 exact host/port/path/method, loopback peer, live session epoch, TTL, constant-time state, unused pending을 확인하고 atomic consume 후 exchanging으로 간다. 같은 state 동시 요청 중 token POST는 최대 1회다. Replay/mismatch/expired/cancelled/session replacement는 provider wire 0회다.

Composition 순서는 config 및 config/journal store와 base service 초기화 → reserve_loopback_socket()으로 port 확정 → prefix 및 SessionSecurity session/epoch 생성 → exact port/prefix/session epoch를 결속한 OAuth service 생성 → app/server start → browser opener 순이다. Browser opener와 subprocess/provider I/O는 event loop 밖에서 수행한다. Open false/exception은 safe launch failure이며 저장하지 않는다. 모든 state transition은 단일 flow owner lock/loop로 직렬화한다. Session close, idle expiry, replacement 시 해당 epoch의 미커밋 flow를 먼저 invalidate하고 secret/candidate를 지운 다음 child를 취소하고 MutationBarrier drain을 수행한다. 이미 committing인 transaction은 취소된 것처럼 처리하지 않고 기존 drain/journal recovery를 따른다. Late child/browser completion은 새 session이나 새 flow에 반영할 수 없다.

Terminal 시 목적 슬롯을 즉시 비우고 state, verifier, code, candidate 및 secret을 지운다. Read-only terminal ID/code와 session digest만 최대 300초 보관하며 목적별 최근 4개, 전체 최대 8개를 넘지 않는다. TTL/eviction 후 poll은 fixed FLOW_NOT_FOUND다. Terminal record는 commit retry나 재생에 사용할 수 없다.

## Fetch Metadata와 callback의 HTTP 경계

SameSite Strict cookie가 cross-site Google redirect에 없을 수 있으므로 일반 cookie gate 통과를 가정하지 않는다. SecurityBoundary에는 exact callback/result 두 GET만 예외다. 일반 API와 모든 POST의 host/origin/session/CSRF/activity 규칙은 유지하고 이 예외로 mutation 권한을 만들지 않는다.

전체 Fetch Metadata는 Sec-Fetch-Site, Sec-Fetch-Mode, Sec-Fetch-Dest, Sec-Fetch-User 4개다. 전부 absent면 callback도 exact host/loopback peer/path, bounded query, live one-use state/session epoch를 확인한다. Result는 exact host/peer/path/query-empty인 fixed no-data page만 제공하고 flow/session/cookie를 요구하지 않는다. 하나라도 있으면 각 header가 단일 값이어야 한다. Callback은 cross-site/navigate/document; result는 cross-site 또는 same-origin/navigate/document만 허용한다. Partial/duplicate/wrong site/mode/dest, iframe, subresource, no-cors는 거부한다. Redirect chain site 분류는 전체 URL chain을 따른다. Code/token은 logs, body, errors, redirect에 넣지 않고 no-store/no-referrer와 즉시 query-free 303을 사용한다. Fixed Google HTTPS endpoint 이외 host와 credential-bearing redirect는 거부한다.

## Killable transport 및 budget

DNS가 socket timeout 밖에서 멈출 수 있으므로 OAuth token/identity/verification traffic은 fixed child operation의 단일 killable subprocess 안에서 실행한다. Fixed executable/module/args, shell=False, close_fds=True; event loop 밖에서 launch한다. Child process를 추가 생성하지 않는다. Secret은 argv/env가 아닌 bounded private stdin으로 전달하고 stderr는 폐기한다. Error는 fixed safe code만 반환한다.

| 논리 경로 | 정확한 call cap | 구성 |
|---|---:|---|
| Initial callback | 2 | token POST 1 + fresh about.user.permissionId GET 1; save 0 |
| authorized_user save/recover | 14 | candidate refresh/about 2 + distinct effective/canonical OAuth peers 각각 최대 2 + target GET 최대 8 |
| Ordinary OAuth Connection Test | 10 | test-local refresh/about 2 + target GET 최대 8 |
| 기존 service-account generic Connection Test | 8 | 현재 legacy ProviderChecks behavior 유지 |

각 invocation monotonic deadline은 30초, 각 socket timeout 최대 10초, redirect/retry 0이다. HTTP body 및 stdout/result frame 각 1 MiB 이하. Parent는 stdout을 streaming cap으로 읽고 cap+1 첫 byte에서 child를 kill/reap하고 결과를 폐기한다. Unbounded communicate 후 검사 금지, cap 전 JSON parse 금지. 정상 OS scheduling에서 DNS/TLS/socket stall을 종료시키되 OS 자체 scheduling 중단까지 보장한다고 주장하지 않는다. Callback, save/recover, ordinary test는 독립 context다. 재호출마다 budget을 reset하지 않는다.

Code exchange/save verifier child는 access token을 반환하지 않는다. Runtime refresh helper만 실제 granted scope, configured client/purpose, fresh permission ID, expiry 검증 뒤 access token/expiry/binding을 capped private pipe로 부모의 in-memory public Google credential adapter에 반환할 수 있다. Adapter가 public SDK apply()로 auth header를 만들 때만 메모리에서 쓴다. Browser/API/log/journal/disk 노출 금지. Runtime refresh network는 항상 helper를 거친다. google_worker_service는 동일 helper attestation을 쓰고 unbounded about().execute()를 별도 호출하지 않는다. 일반 Drive file I/O 전체가 이 deadline에 포함된다고 주장하지 않는다.

## Exact authorized_user payload와 grant 증명

Generic Google structural_credential()는 service_account-only로 유지한다. OAuth만을 위한 strict internal parser parse_authorized_user_for_google_oauth(role, raw_bytes, purpose_config)를 분리한다. HTTP/browser/CLI caller는 credential kind나 parser mode를 선택할 수 없다. 허용 callers는 OAuth save, OAuth recovery verifier, 저장 credential을 위한 Connection Test dispatch뿐이다.

Exact top-level key set은 {type, client_id, client_secret, refresh_token, token_uri, scopes}다. Type은 authorized_user, client 값은 목적별 bundle과 일치하는 non-empty scalar, refresh token은 opaque non-empty scalar, token URI는 fixed Google endpoint, scopes는 중복 없는 exact role-scope array다. Missing/extra key, wrong type/container, whitespace-only, client/URI/scope mismatch는 fail-closed다. Persisted purpose/account metadata는 없다. 이 six-key 규칙이 v2의 purpose metadata 문구를 대체한다.

Local scopes=, SDK advertised scopes, 임의 payload metadata는 Google grant 증거가 아니다. Code exchange/refresh의 실제 server-granted scope가 role의 exact scope와 일치해야 한다. Scope 누락/초과/불명확은 거부한다. 같은 token으로 fresh Drive about.user(permissionId)를 읽어 account를 확인한다. JSON account/project, ID token, config text는 신뢰하지 않는다. Token/permission ID는 journal/log/API에 기록하지 않는다.

CredentialResolver의 protected file reader에서 no-follow, private mode, owner, size 및 opened FD identity 검사를 보존하며 정확히 한 번 읽는다. Typed snapshot은 mapping/nested list를 복사하고 scope를 immutable canonical tuple로 만든다. SDK input은 별도 mutable copy다. 이후 caller mutation은 snapshot을 바꾸지 않는다. SA loader/parser를 authorized_user까지 넓히지 않는다.

## B — CredentialService transaction and provider checks

B product edit 후보는 src/uls/settings/credential_service.py 및 src/uls/settings/provider_checks.py뿐이다. Admission/role/named-journal/config lock, journal schema, store API, secure-file reader는 unchanged dependencies다. 직접 변경 필요가 새로 드러나면 exact path/call path를 보고하고 그 전에는 편집하지 않는다.

### B1. Code exchange와 잠금
Callback의 최초 exchange와 identity GET은 admission/role/config lock 바깥이다. save_google_oauth 및 recover는 existing admission → role → named operation → config lock 순서와 held regions를 보존한다. Save는 config lock 아래 generation, selected source/presence, managed target, peer snapshot을 확인하고 consent gate를 먼저 적용한다. 통과 전 admission publish/journal/stage/provider save effect 0이다. Stage/verify는 existing operation region 안에서, config lock 범위를 바꾸지 않고 실행한다. 실패 cleanup은 현재 config lock 재진입을 쓴다. _continue()가 기존 CAS를 수행한다.

Restart recovery는 flow registry 없이 exact journal binding, candidate hash, protected staged bytes에서 진행한다. Live state와 stage revalidation은 기존 admission/role/named operation/config lock 구간에서 한다. 그 구간 후 현재대로 admission을 유지하고 role+named operation을 재획득해 _continue()한다. 사이에 lock이 계속 held라고 가정하지 않는다. Journal/recovery schema에 OAuth metadata를 추가하지 않는다.

필수 회귀는 callback exchange lock 부재, save/recover의 lock 순서와 보유 구간, stage 뒤 process restart 및 flow registry 부재 복구, malformed staged bytes/stale generation/peer mismatch, 각 crash point의 active/staged/backup/config/journal raw bytes다.

### B2. User consent와 managed sink mode
Begin의 replace bool은 flow 내부 user_replace_authorized다. 사용자 승인 의미이며 sink operation mode가 아니다. Generic CredentialService.save(role, value, generation, *, replace=False, candidate=None, fault_hook=None)는 signature와 semantics를 유지한다. 별도 typed CredentialService.save_google_oauth(role, candidate_bytes, authorization, ...)만 OAuth authorization을 받는다.

Current lock 아래 effective source/presence 및 canonical managed active state를 재확인한다. Effective credential present이고 승인이 false면 journal/stage/provider/save/config effects 0이다. 승인 true도 external file/environment를 수정 또는 삭제하지 않는다. External source가 present이고 managed target absent면 managed enrollment+기존 config CAS만 가능하다. External source가 선택된 채 stale canonical managed active가 있으면 hidden bytes overwrite를 막고 fail-closed한다. Unreadable configured external path는 absent가 아니다. Managed active present일 때만 승인 true로 existing managed replacement를 쓴다. Journal action은 sink pre-state로 결정한다. Verification 뒤/CAS 직전 generation/source/target 변화는 기존 recovery 방식으로 거절한다.

회귀 matrix: managed-present × consent on/off; external-present+managed-absent × on/off; external-present+stale-managed × on/off; external missing/unreadable; generation/source/target race; enrollment/replacement의 CAS 직전 crash와 recovery. 모든 external bytes는 전후 동일하다.

### B3. Peer separation 및 shared 14-call budget
Peers는 effective selected peer와 canonical managed peer다. Raw bytes가 같으면 하나로 dedupe한다. 기존 protected reader가 연 same FD의 bytes/digest를 snapshot하고 Path.read_bytes() 재호출은 금지한다. _separate() 반복은 같은 immutable invocation snapshot에서 pure schema/type/client/blob/grant check만 수행한다. Network attestation이나 budget을 새로 만들지 않는다.

각 distinct OAuth candidate/peer는 purpose client 및 grant를 재사용할 수 없다. Fresh actual server scope와 about permission ID를 검증하고 candidate 및 peer account가 같아야 한다. SA-SA는 기존 client_email/private_key_id 규칙을 유지한다. SA-OAuth는 각자의 type/schema로 검사하고 SA에 OAuth fields를 요구하지 않는다. Unreadable/malformed/unknown peer는 first mutation 전에 reject다.

한 save 또는 recovery invocation은 candidate refresh/about 2 + distinct effective/canonical OAuth peers 각 최대 2 + target GET 최대 8로 총 14 calls다. Identical bytes peer는 한 번만 검사한다. 30초 deadline, per-call 최대 10초, redirect/retry 0, 1 MiB body/stdout cap은 invocation 전체에서 공유한다. _verify/_separate/helper 재호출이 budget을 reset하지 않는다. Snapshot fingerprint는 config generation, source kind/presence, candidate 및 peer digests, canonical managed state, purpose/client/scope를 binding한다. _continue/CAS 전에 바뀌면 attestation stale로 저장을 거부한다. Recovery restart는 새 context에서 재검증한다. ID/token/raw bytes는 journal/log에 쓰지 않는다.

### B4. Ordinary persisted OAuth Connection Test — Native v4 REQUIRED 1
현재 CredentialService.test()는 ProviderChecks.check()를 호출하고 generic _check()가 기존 8-call TransportCheckBudget을 생성한다. Persisted Google authorized_user를 해당 generic path로 보내지 않는다. Strict internal parse 후 dedicated ProviderChecks.check_google_oauth_connection_test(...)를 부른다. Generic check의 기존 signature/default behavior는 그대로며 arbitrary caller가 oauth context/mode를 제공할 수 없다.

CredentialService.test() 내부 분기만 저장된 Google bytes를 읽어 type discriminator를 확인한다. Exact authorized_user 표식이면 전체 payload를 strict internal parser로 검증한 뒤 OAuth-only method를 호출한다. 그 외 값은 현행 generic path로 전달해 기존 service-account validator가 판단한다. 이 표식은 권한이 아니라 내부 dispatch hint이며 valid full schema, role binding, client/scope와 fresh server proof 없이는 OAuth test가 실행되지 않는다. Malformed payload가 OAuth parser에서 실패하면 SA fallback으로 재시도하지 않는다. Ordinary test의 about 결과는 해당 단일 test invocation의 fresh account attestation이다. Persisted metadata를 신뢰하거나 다른 purpose와 stored account ID를 비교하지 않는다. Save/recover peer same-account enforcement는 B3 규칙이다.

Ordinary test는 save/recover와 cache를 공유하지 않는 test-local killable context를 만든다. Refresh token POST 1 + fresh about.user.permissionId GET 1 + configured target Drive GET 최대 8, 총 10 calls다. Configured purpose client/scope 및 actual server-granted scope를 검증하고 Drive effects는 GET-only다. Refresh POST와 Drive GET 효과를 구별한다. POST/PUT/PATCH/DELETE Drive request는 0회다. Deadline 30s, socket 10s, redirects/retries 0, body/stdout streaming cap 1 MiB.

Service-account ordinary test는 기존 generic ProviderChecks 8-call behavior를 그대로 유지한다. Non-Google path, cooldown, safe result projection은 바꾸지 않는다. OAuth test success/failure는 credential/config/external file, active/staged/backup, journal/admission bytes를 바꾸지 않는다. 기존 in-memory result/cooldown 기록만 허용하며 token/account/body 노출은 금지한다.

두 saved role 각각 exact client/scope/server grant/fresh identity positive와 DNS-before-socket hang, timeout, 302, cap+1, hidden retry/second refresh rejection을 검사한다. 모든 경우 persistent bytes unchanged와 Drive GET-only를 증명하고 SA positive를 보존한다.

### B5. Generic parser/sink no widening — Native v4 REQUIRED 2
Internal entrypoints는 parse_authorized_user_for_google_oauth, save_google_oauth, check_google_oauth_connection_test, OAuth recovery verifier로 분리한다. OAuth commit owner만 typed authorization을 만들 수 있다. Browser/HTTP/CLI는 parser mode/type/internal capability를 선택할 수 없다. Generic structural_credential() 및 CredentialService.save() Google path는 SA-only다.

완전히 valid한 synthetic six-key authorized_user bytes를 direct CredentialService.save()에 Google MCP/WORKER 각각 replace=False/True로 넣는다. 각 케이스는 reject되고 provider call/admission publish/journal create/stage/backup/config/active effect가 전부 0이다. Malformed fixture rejection을 positive evidence로 세지 않는다. Authenticated generic HTTP JSON upload/action에도 같은 no-effect negative를 A-owned HTTP test로 둔다. Existing synthetic SA direct save/import/check positives unchanged.

### B6. Typed auth object
GoogleOAuthCommitAuthorization은 immutable이고 role slug, flow ID/epoch, captured config generation, configured client ID, actual granted scope tuple, fresh permission ID, user_replace_authorized를 가진다. Secret/token/code/refresh token은 포함하지 않는다. Internal flow owner만 생성한다. Service는 type/role/epoch/client/generation/expiry/current config를 재검증한다. CSRF/session/MutationBarrier 통과 후 commit owner가 candidate를 atomic consume하고 dedicated save를 한 번 부른다. Generic save/CLI caller는 기존 계약이다.

### Native B v4 판정 항목 대응
REQUIRED 1은 B4 및 acceptance의 Budget/Connection Test 항목이다. REQUIRED 2는 B5와 authenticated generic HTTP zero-effect 회귀다. OPTIONAL 1은 exact six-key schema 절과 supersession 표가 이행한다. OPTIONAL 2는 아래 B review packet에 direct-owned tests/contract/test_settings_provider_checks.py 전문을 포함하도록 닫는다. 이 mapping은 원본 응답의 formal/canonical 처분 완료를 뜻하지 않는다.

## 파일 closure 및 no-change boundary

새 product module은 src/uls/settings/google_oauth.py 하나다. 정확 후보와 관련 검사는 네 complete-source packet이다.

| Packet | 변경 후보 | 관련 test closure |
|---|---|---|
| A protocol/runtime/config/HTTP | 신규 src/uls/settings/google_oauth.py; src/uls/settings/app.py, src/uls/settings/security.py, src/uls/settings/composition.py, src/uls/settings/launcher.py; src/uls/config/schema.py, src/uls/config/loader.py, src/uls/config/validation.py, src/uls/config/credentials.py; src/uls/runtime.py; config.example.yaml | 신규 tests/contract/test_settings_google_oauth.py; tests/contract/test_settings_http.py, tests/contract/test_settings_credential_http.py, tests/contract/test_settings_composition.py, tests/unit/test_config_loader_credentials.py, tests/unit/test_runtime_google_credentials.py, tests/unit/test_credential_resolver.py, tests/unit/test_credential_resolver_file_source.py, tests/unit/test_settings_launcher.py |
| B protected transaction/check | src/uls/settings/credential_service.py, src/uls/settings/provider_checks.py | tests/contract/test_settings_credential_service.py, tests/contract/test_settings_provider_checks.py |
| C browser/UI | src/uls/settings/status.py, src/uls/settings/static/app.js, src/uls/settings/static/index.html, src/uls/settings/static/styles.css | tests/contract/test_settings_ui.py, tests/contract/settings_ui_harness.cjs |
| D unchanged Drive API/privacy | src/uls/worker.py, src/uls/adapters/drive/google.py, src/uls/adapters/drive/worker.py, src/uls/study_notes/drive.py, src/uls/intake/worker.py; the exact frozen design/spec/Behavior Contract files in current17 inventory | tests/integration/test_intake_worker_preview.py, tests/contract/test_worker_cli.py, tests/contract/test_mcp_runtime.py regression-only |

Related read-only dependencies는 current17 inventory의 exact SHA/size closure다: config service/errors, keyring backend, Drive binding, settings support, CAS/config/admission/journal/roles/stores/secure-file tests 및 native runtime integration. 이는 변경 후보가 아니다.

No-change files/areas: credential_admission.py, credential_roles.py, credential_stores.py, journal.py, config/_secure_file.py, mutation kernel, unrelated CLI/tests, listed runtime integration 밖 Drive/Intake kernel, Notion, Canvas, frozen specs, active checker/source pins/inventory/tests/AGENTS/global settings, GUI4, RESEARCH, ACL, live credential/provider. Journal/admission schema 변경, generic CredentialService.save signature 변경, generic parser broadening 및 추가 third-party dependency는 금지다. Closure 밖 source가 꼭 필요해 보이면 편집 전에 정확한 path/call graph와 이유를 보고한다.

다음 B review packet은 v7 전문, credential_service.py, provider_checks.py, test_settings_credential_service.py, test_settings_provider_checks.py 전문, docs/plans/provider-connection-nondeveloper-scope-20261005.md 전문(SHA-256 11a9d2b7705785cce8dc3305e0014b08cb4c5c30044e20a6e96e6801870e66f0, 7,441 bytes) 및 current inventory 근거를 포함한다. 특히 direct B-owned test_settings_provider_checks.py를 hash/excerpt로 대체하지 않는다. Complete pack은 fixed 120,000-token limit 안에 실제로 audit한다. 한도 때문에 direct-owned full source를 떼거나 source alias를 쓰지 않는다. v5는 독립적으로 읽을 수 있으며 reviewer가 v2/v3/v4에 의존하지 않는다.

Syllva 전용 17-source evidence checker 도입은 이미 있으나 OAuth product pins 승인이 아니다. 컴파일 pin에 걸리는 수정은 exact candidate, 별도 독립 검토, 인간의 exact 적용 결정이 필요하다. 이 PLAN은 pin을 갱신하지 않는다.

## Meaningful acceptance matrix

테스트는 synthetic OAuth, fake provider/helper/DNS/socket, temporary protected stores와 fake browser만 사용한다. 실제 .env, credentials, Keychain, School, live provider mutation과 worker activation은 사용하지 않는다.

| 영역 | Required proof |
|---|---|
| Flow/CSRF | CSPRNG/S256 vectors, exact URL, callback one-use, poll/callback/result save 0, 유효 commit 총 save 1, CSRF/session/generation/replay/query denial은 save 전에 차단 |
| Fetch Metadata | all-four-absent compatibility, real redirect-chain cross-site callback/result, same-origin result, partial/duplicate/wrong header 및 iframe/subresource/no-cors denial |
| Killable transport | DNS-before-socket, TLS/socket timeout, trickle, 302, body/stdout cap+1, retry/second refresh; child terminate/reap 및 no save; secret sentinel이 argv/env/log/stderr/error/API에 없음 |
| Budgets | SA ordinary ≤8 legacy; OAuth Connection Test ≤10 isolated; save/recover aggregate ≤14 over repeated _separate; callback ≤2; 15th transaction call 거절 |
| Connection Test | 양 역할의 exact client/actual scope/fresh identity; refresh POST와 Drive GET-only; success/failure 모두 persistent bytes 불변 |
| Generic deny | valid six-key OAuth direct generic save roles×replace booleans, authenticated generic upload가 zero-effect reject; 기존 SA positive unchanged |
| Replacement | managed/external/stale hidden/missing/unreadable × consent; source/config/target race; external bytes unchanged |
| Peers | selected+canonical, byte-identical dedupe, malformed/unreadable reject, distinct OAuth grants/client/blob 및 same account, SA-SA와 SA-OAuth |
| Snapshot/CAS | opened-FD protected bytes; reader guard/mode/owner/size/no-follow 유지; no path reopen; immutable snapshot; fingerprint drift로 attestation 폐기 |
| Recovery/locks | exact lock order/held spans; stage crash 후 no flow registry restart; raw journal/config/keyring/file bytes와 no duplicate effect |
| Config/UI | missing bundle start-not-ready, malformed bundle fatal, bundle values 미노출, Advanced SA 및 기존 cancel/session arbitration |
| Real acceptance | actual Desktop app/login/consent, USER attribution, exact target privacy, provider write, truthful Partial 및 authoritative Drive/Notion readback/retrieval은 별도 live gate |

검사 결과는 실제 collected/passed/failed와 실제 명령으로만 기록한다. 과거 기준선 횟수를 새 acceptance로 재사용하지 않는다.

## Review/implementation/release gates

이 문서는 PLAN-only다. Exact v5 및 applicable full source packet에 대한 Native와 Gemini의 독립 PLAN review, original-bound response audit와 parent disposition, 명시적 implementation GO 뒤에만 코드/테스트를 수정한다. 현재 Native B v4 REVISE2는 GO가 아니다.

GO 후 동일 owner가 위 A/B/C 범위에서 의미 있는 red→green 회귀, owning test suites와 scoped static checks를 실행하고 관련 수정까지 책임진다. Fake browser 증거는 최종 source hash에 묶는다. 새 material decision 또는 closure 밖 source 필요가 나오면 확대 전 멈추고 경로·증거를 보고한다.

최종 SHA/size와 실제 검사 결과, store invariant를 기록하고 exact-current-source Native/Gemini FINAL review를 받는다. Pin delta가 있으면 별도 exact candidate/review/human application을 거친다. 실제 School 수락은 Desktop app registration, human consent, 귀속 가능한 USER-submitted 자료, 정확한 target/ACL readback, 성공 write 및 Partial 처리, authoritative Drive/Notion readback/retrieval을 관측해야 한다. 이 조건 전에는 full flow acceptance, PR, commit, push 또는 publication을 주장하지 않는다.

## 작성 경계

이번 작업은 이 PLAN 파일만 추가한다. v2/v3/v4, Native response/manifest/audit, current17 inventory, Notion plan, active checker/pins, product/test source, ACL, config, provider state 및 기존 기록은 보존한다. 실제 Google/provider/School 접근이나 테스트 실행은 포함하지 않는다.
