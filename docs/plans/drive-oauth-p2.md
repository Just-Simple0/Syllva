# Drive OAuth P2 계획

상태: PLAN-only. P1 Drive 개인정보 보강의 구현·수락 뒤 진행한다. 제품·테스트·핀·ACL·설정 파일 변경 및 테스트 실행은 이 계획의 독립 검토와 별도 구현 GO 전에는 하지 않는다.

## 1. 목표와 고정 결정

오픈소스 공개를 전제로, 한 사용자가 자기 Google Cloud 프로젝트의 Desktop OAuth client 하나로 자기 Syllva에 Google Drive를 연결한다. 공개 저장소·패키지·fixture에는 client ID/secret 실값을 넣지 않는다. 두 목적은 같은 client를 쓰되 각각 따로 동의하고 서로 다른 refresh token을 기존 역할 저장소에 보관한다.

| 용도 | 요청·허용하는 정확한 scope | Google 권한 의미 |
|---|---|---|
| 읽기 전용 검색(MCP) | `https://www.googleapis.com/auth/drive.readonly` | Drive 읽기 |
| 자료 처리(WORKER) | `https://www.googleapis.com/auth/drive` | Drive 전체 읽기·쓰기 |

실제 authorization/token 응답의 granted scope 집합이 요청한 단일 scope와 정확히 같지 않으면 거부한다. `drive.file`, Picker 및 scope 축소 주장은 제외한다. School 내부 폴더 allowlist는 Syllva가 선택하는 처리 대상일 뿐 Google grant를 제한하지 않는다. WORKER grant는 넓은 Drive 권한이다. Google 계정에서 앱 권한을 철회하면 같은 client의 두 목적 연결이 모두 끊길 수 있다.

앱 준비 상태는 사용자 config bundle에 유효한 `client_id`와 `client_secret`이 모두 있는지로만 표시한다. Google 심사·보안 평가·배포 준비 기록·경과 시간 gate는 없다. 기존 service-account JSON 고급 연결과 그 동작은 유지한다. remote MCP용 Notion token은 Google OAuth와 별개이며 재사용하지 않는다.

## 2. 불변식

- I1. client 값은 user-local config bundle에서만 읽고, 코드·저장소 예시·package·test fixture에는 실제 값을 두지 않는다.
- I2. `mcp`와 `worker`는 code-owned role/scope에 결속되고 각각 별도의 protected refresh credential을 쓴다. HTTP 입력으로 role, scope, parser 종류를 선택할 수 없다.
- I3. callback은 state·PKCE·정확한 loopback authority/path와 실제 Google 응답을 검증하지만 credential 저장은 0회다.
- I4. 저장은 원래 Settings session의 CSRF 보호 commit POST만 수행한다. flow 잠금 안에서 `awaiting_commit → committing`을 한 번 바꾸고 같은 요청에서 `CredentialService.save_google_oauth(candidate)`를 기다린다.
- I5. 실제 scope, configured client, fresh account identity, purpose 또는 generation이 맞지 않으면 active/config 변경은 0회다. 계정 불일치는 B의 고정 `ACCOUNT_MISMATCH` 결과다.
- I6. `authorized_user`는 정확한 6-key 전용 parser를 통과해야 하며 generic `structural_credential()`/`CredentialService.save()`는 기존 service-account 전용 동작을 유지한다.
- I7. Resolver가 만든 Google payload snapshot은 한 번만 읽어 사용한다. runtime이 `.env`, 환경변수 또는 credential file을 별도로 다시 읽지 않는다.
- I8. WORKER는 매 효과성 public entry에서 scope/client/permission ID를 fresh 검증한다. ID/client drift 뒤 Drive file call, Notion write, receipt/generation 효과는 0회다.
- I9. remote revoke·ACL 변경·worker 자동 활성화는 하지 않는다. local disconnect는 로컬 credential/config 제거뿐이다.
- I10. 한 POST 응답이 유실되면 저장을 자동 재시도하지 않는다. 사용자는 credentials/Overview를 다시 읽어 결과를 확인한다.

## 3. 구성요소별 계약

### 설정과 readiness

`google_oauth` bundle은 `client_id`/`client_secret` 두 문자열을 모두 받거나 비운다. 부분·잘못된 값은 안전 오류다. Schema 필드는 `repr=False`; 값은 log/error/Overview에 나오지 않는다. Overview에는 config 존재만 뜻하는 `google_oauth_client_configured: boolean`만 추가한다. 값이 없으면 begin은 `OAUTH_APP_NOT_READY`, browser/provider 0회다.

### Flow API와 상태

`P`는 현재 Settings launcher가 정한 exact path prefix다. purpose는 `mcp` 또는 `worker`만 허용한다. 모든 POST는 기존 session·same-origin·CSRF 검사를 통과해야 하며 요청 JSON은 아래 key set과 타입이 정확해야 한다.

| 요청 | 정확한 계약과 응답 |
|---|---|
| `POST /P/api/v1/google-oauth/{purpose}/begin` | body `{generation, replace}`. `generation`은 해당 role의 현재 cards generation. 저장 credential이 있으면 `replace:true`, 없으면 `false`만 허용한다. session epoch에 묶인 flow를 만들고 system browser를 연다. 성공 `{flow_id,state:"pending",expires_in_seconds:300,result_code:null}`. URL/state/verifier는 반환하지 않는다. 동일 purpose flow는 `409 FLOW_BUSY`; 기존 flow는 유지한다. |
| `GET /P/api/v1/google-oauth/{purpose}/{flow_id}` | 시작한 같은 live Settings session만 읽는다. body `{flow_id, state, expires_in_seconds, result_code}`만 반환한다. credential, URL, code, client 값, token, permission ID 및 provider message는 없다. GET은 save 0회다. |
| `POST /P/api/v1/google-oauth/{purpose}/cancel` | body `{flow_id,generation}`. pending/exchanging/awaiting_commit만 취소; 성공 `{flow_id,state:"cancelled",result_code:"CANCELLED"}`. committing은 `409 COMMIT_IN_PROGRESS`. |
| `GET /P/oauth/google/callback` | Google의 `state+code` 또는 `state+error`만 받는다. state·PKCE·session epoch·TTL·exact host/port/path/loopback peer를 확인하고 code를 교환한다. success candidate는 process memory에만 두며 save 0회. 결과는 query 없는 고정 `/P/oauth/google/result`로 303한다. |
| `GET /P/oauth/google/result` | query-empty, 고정 안내만 있는 data-free 문서다. session·cookie·flow·token을 만들거나 조회하지 않는다. |
| `POST /P/api/v1/google-oauth/{purpose}/commit` | body `{flow_id,generation}`. CSRF/session/role generation/replace를 재검증하고 flow lock 아래 `awaiting_commit → committing` 한 번 후 같은 요청에서 `save_google_oauth(candidate)`를 호출한다. 응답은 `SAVED` 또는 고정 code. 동시·반복 저장 진입 합계 1회. |

Flow는 `pending → exchanging → awaiting_commit → committing → complete` 또는 `cancelled/expired/denied/account_mismatch/failed`다. Process당 purpose별 flow 1개, 총 2개. state/verifier는 독립 32-byte CSPRNG/base64url 43자, challenge는 S256이다. 처음 세 상태만 monotonic 300초 TTL. Commit은 한 요청에서 끝나며 별도 committing poll/TTL 재개가 없다. 응답 유실은 결과 미확인으로 두고 자동 commit/begin하지 않는다. Restart는 미완료 flow를 버리고 이미 시작한 저장만 journal이 복구한다. Approval object, replay ledger, tombstone은 추가하지 않는다.

### Callback, HTTP 및 오류 경계

Callback만 SameSite=Strict/session gate의 좁은 예외다. Exact loopback host/port/path/peer 필수. Fetch Metadata 4개가 모두 없으면 live one-use state/session-epoch/query를 확인한다. 모두 있으면 callback은 `cross-site/navigate/document/?1`만 허용한다. Result는 exact host/peer/path와 빈 query를 요구하며 headers all-absent 또는 all-present `cross-site|same-origin/navigate/document/?1`만 받는다. Partial/duplicate, iframe/subresource, wrong route/method, replay/session replacement 거부. 일반 API/POST gate는 불변이다.

Callback query ≤8,192 bytes, state 43 ASCII, decoded code 1–4,096 bytes, error ≤64 ASCII. `state+code` 또는 `state+error`만 받는다. HTTPS endpoints/query allowlist는 code-owned. HTTP connect/read 각 10초, redirect/retry 0, 응답 ≤1 MiB. Subprocess·call-count·end-to-end deadline은 없다. DNS 지연의 즉시 감지를 약속하지 않으며 cancel/TTL 뒤 늦은 결과는 폐기한다.

오류 body는 `{"error":{"code":"..."}}` 형식뿐이다. OAuth 경로는 `_service_error()`의 provider/message/fields 직렬화를 사용하지 않는다. 최소 fixed code는 `OAUTH_APP_NOT_READY`, `FLOW_BUSY`, `FLOW_NOT_FOUND`, `FLOW_EXPIRED`, `FLOW_CANCELLED`, `FLOW_STATE_REJECTED`, `OAUTH_ACCESS_DENIED`, `OAUTH_GRANT_MISMATCH`, `ACCOUNT_MISMATCH`, `RECONNECT_REQUIRED`, `CSRF_REJECTED`, `SESSION_EXPIRED`, `SAVE_FAILED`다. code·token·client secret·permission ID·provider sentinel은 URL log, response, journal, config metadata에 넣지 않는다. 응답/완료 page는 no-store/no-referrer다.

### OAuth credential, B 저장과 재연결

`parse_authorized_user_for_google_oauth()`는 정확한 6-key set `{type, client_id, client_secret, refresh_token, token_uri, scopes}`만 받는다. Type은 `authorized_user`, client는 config 일치, token URI는 fixed Google endpoint, scope는 중복 없는 목적별 단일 값이다. Missing/extra/duplicate key와 잘못된 타입/client/scope는 거부한다. Generic SA parser는 그대로다.

Callback은 actual granted scope가 exact하고 `about.get(fields="user(permissionId)")`가 성공할 때만 memory candidate를 만든다. Callback 저장은 0회. B는 candidate·현재 role generation·client와 fresh grant/account를 다시 확인한다. 다른 목적에 OAuth가 설정되어 있으면 fresh `permissionId`가 같아야 한다. SA peer의 동등성은 추정하지 않는다. 불일치는 `ACCOUNT_MISMATCH`; stage/promote/config/active 0.

`save_google_oauth(candidate)`는 고정 MCP/WORKER role만 받는 내부 경로다. Generic `save()`/HTTP upload는 OAuth를 받지 않는다. OAuth 검증 뒤 기존 admission→role→named-operation→config lock, protected active/staged/backup store, journal, config CAS, `_continue()`와 recovery를 재사용한다. 해당 role의 refresh token만 저장하고 실패 시 기존 active/config를 유지한다. Connection Test도 내부 type dispatch로 OAuth verifier를 쓰며 SA fallback은 금지한다. 기존 SA 동작은 유지한다.

기존 local disconnect는 `POST /P/api/v1/credentials/google-mcp/forget` 또는 `.../google-worker/forget` 경로와 기존 generation 계약을 사용한다. 이는 해당 로컬 credential/store 및 config reference만 제거한다. Google remote revoke, 다른 purpose credential 제거, Google Cloud client 삭제, Drive ACL 변경은 하지 않는다. 계정 권한 철회는 사용자가 Google 계정 설정에서 직접 수행한다.

### Runtime과 WORKER binding

Resolver의 단일 `ResolvedCredentials` snapshot/payload를 재사용한다. `service_account`는 현행 경로, exact `authorized_user`만 OAuth parser/refresh 경로다. Refresh 뒤 server-authoritative scope·config client ID/secret 일치가 필수며 local `scopes=`나 저장 scope는 증명이 아니다. Grant 증거 누락도 거부한다. 이후 identity 전용 `about.get(fields="user(permissionId)")`만 허용하고 그 proof 전 Drive file/resource 및 Notion effect는 0이다. permission ID/peer가 틀리면 target call 0.

WORKER binding은 `provider_binding_id("google_drive", fresh_permission_id, configured_WORKER_client_id)`며 raw ID는 기록하지 않는다. `build_intake_worker()`의 같은 attested snapshot을 adapter와 IntakeWorker에 전달한다. `run_once`, `process_item`, `sync`, `claim_request`, `create_input_request`는 매 호출 효과 전에 fresh 검증한다. Drift/`invalid_grant`는 fail-closed/`RECONNECT_REQUIRED`; Drive·Notion mutation 및 receipt claim/generation 0. 목적 fallback은 없다.

## 4. 파일별 변경 계약

| 구분 | 경로 | 책임 |
|---|---|---|
| 신규 | `src/uls/config/google_oauth.py` | 정확한 OAuth 6-key parser와 목적 scope 검증. |
| 신규 | `src/uls/settings/google_oauth.py` | purpose-bound flow, state/PKCE, fixed HTTP transport, callback, status/cancel/commit coordination, OAuth refresh/grant/account verifier. |
| 설정 | `src/uls/config/schema.py`, `loader.py`, `validation.py` | user-local `google_oauth.client_id/client_secret`의 all-or-none config. |
| Settings API | `src/uls/settings/app.py`, `security.py`, `status.py` | Overview boolean, 정확한 begin/status/cancel/commit/callback/result routes, fixed code mapper, narrow callback Fetch Metadata exception. |
| 구성·실행 | `src/uls/settings/composition.py`, `launcher.py` | 한 flow service를 current exact loopback prefix/port/session epoch에 결속하고 system browser를 연다. close/replacement/idle 시 미완료 flow를 먼저 무효화한다. |
| credential transaction | `src/uls/settings/credential_service.py` | 내부 `save_google_oauth(candidate)`와 persisted OAuth Connection Test dispatch를 추가하고 기존 트랜잭션/복구를 재사용한다. |
| runtime | `src/uls/runtime.py`, `src/uls/intake/worker.py` | authorized_user 전용 dispatch, actual grant/account 검증, WORKER binding, 전 효과성 public entry gate. SA 경로 유지. |
| 신규 테스트 | `tests/unit/test_google_oauth_config.py`, `tests/unit/test_google_oauth_runtime.py`, `tests/contract/test_settings_google_oauth.py`, `tests/contract/test_settings_google_oauth_http.py`, `tests/contract/test_settings_google_oauth_save.py`, `tests/contract/test_settings_google_oauth_composition.py`, `tests/contract/test_intake_worker_google_oauth.py` | fake transport/browser/SDK·임시 store를 쓴다. |

P3 전용 UI 파일 `src/uls/settings/static/app.js`, `index.html`, `styles.css`, `tests/contract/test_settings_ui.py`, `tests/contract/settings_ui_harness.cjs`는 변경 후보가 아니다. P2는 API 응답만 제공한다. 기존 `src/uls/config/credentials.py`, `src/uls/settings/provider_checks.py`, `credential_roles.py`, `credential_stores.py`, `credential_admission.py`, `journal.py`는 수정하지 않는다. `config.example.yaml`·package에도 실제 OAuth 값이나 값처럼 보이는 예시를 추가하지 않는다.

## 5. 수락 테스트

모든 자동화는 synthetic nonfunctional client/refresh token, fake provider/HTTP/browser, temporary protected file/keyring/journal/config만 사용한다. 실제 Google/School 로그인은 구현·FINAL 뒤 별도 사람 확인이며 이 계획 작성 단계에서 수행하지 않는다.

| 영역 | 시나리오 → 기대 효과 |
|---|---|
| Config/readiness | absent=false, 유효 pair=true, partial/malformed=안전 오류. Overview/log/repr/error의 값 노출과 Google call 0. |
| Begin/session | scope/offline consent/state/PKCE/loopback exact. config·generation·CSRF/session 실패는 opener/provider/store 0. duplicate begin은 `FLOW_BUSY`, 기존 flow 보존; live flow 최대 2. |
| Callback HTTP | exact scope/about ID는 `awaiting_commit`, save 0. scope/client/account/refresh-token 실패는 fixed code/store 0. host/path/peer/query/state/replay/expiry/header 오류는 교환 0. all-absent와 실제 cross-site redirect chain 시험. |
| CSRF commit/single save | valid POST의 B save 1회, GET/callback/result save 0. CSRF/generation/cancel/expiry/replay reject는 transaction effects 0. 응답 유실 뒤 자동 retry 0; credentials/Overview GET만. |
| B protected transaction | set/explicit replace 성공; verify/account mismatch/CAS/crash에서 이전 bytes 보존 또는 기존 journal recovery. Generic save/upload의 OAuth 거절; SA positive 불변. |
| Local disconnect | 해당 purpose active/store/config만 기존 forget transaction으로 제거; remote revoke·ACL·상대 purpose credential·SA JSON 변경은 0. |
| Runtime scope/account | grant/client/permission drift, grant proof 누락, `invalid_grant` 뒤 target Drive/Notion 효과 0. WORKER 다섯 entrypoint 각각 검사; reconnect code와 purpose fallback 0. |
| Composition/snapshot/SA | 한 flow service/`CredentialService`를 route와 OAuth caller가 공유함을 identity로 확인. Resolver payload 1회, env/file reread 0. Pinned SA positive는 수정 없이 확인. |

## 6. 승인 17핀 영향

현 compiled 17-pin map과 대조한 직접 교집합은 `src/uls/settings/credential_service.py` 하나다. 기존 transaction 연결과 OAuth Test dispatch에 필요해 피할 수 없다. 변경 후 active checker는 HOLD이며 exact candidate/hash review와 별도 인간 pin 적용 결정 전 갱신·PASS 주장을 하지 않는다.

`src/uls/config/credentials.py`도 pin이나 resolver가 만든 단일 protected-read `GoogleCredentialPayload`를 새 parser에 전달해 변경을 피한다. `credential_admission.py`, `credential_roles.py`, `credential_stores.py`, `journal.py`와 pinned tests는 현 API 그대로 둔다. OAuth 회귀는 새 tests에 두며 checker, `source_pins.json`, global 설정은 변경하지 않는다.

## 7. 사람 작업과 범위 밖

구현 단계에서 `docs/setup/google-drive-oauth.md`를 작성한다. 사용자에게 자기 Google Cloud project 생성, Drive API 사용 설정, Desktop OAuth client 생성, consent 화면과 자기 계정 설정, client 값의 local config 입력을 안내한다. Testing 상태에서는 Drive refresh token이 7일 뒤 만료될 수 있음을 알리고, 필요하면 사용자가 앱을 `Production (unverified)`로 직접 바꾸는 절차와 표시될 unverified 경고를 설명한다. Google 검증 완료나 안전성을 보증하지 않는다. `invalid_grant`는 다시 연결해야 한다고 안내한다.

향후 비개발자 대상 운영 확장은 공용 client, Google 검증, 배포 준비 표시가 필요할 수 있는 별도 P계획으로만 남긴다. 이번 P2는 사용자 소유 client/config 경계만 둔다. Picker/drive.file, 공용·다중 사용자 client, remote revoke, ACL/owner_only/서비스계정 권한 정리, Notion/Canvas 인증, GUI 카드·문구·focus, worker toggle, 실제 School 데이터·provider 작업은 범위 밖이다. 실제 OAuth login, credentials/Overview 재확인과 승인된 School flow가 통과하기 전에는 PR/commit을 진행하지 않는다.
