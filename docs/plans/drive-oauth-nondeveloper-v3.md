# Drive 사용자 OAuth — PLAN 보완 v3

상태: Native A(protocol/runtime/config/HTTP) original-bound 응답 전문은 확인했고 REVISE 필수 5건·선택 2건이다. canonical harvest는 다른 프로젝트 Native profile 점유로 pending이며 우회하지 않는다. 이 문서는 독립 PLAN 검토용 수정 계약이지 제품 변경 허가가 아니다. v1·v2와 모든 기존 review/pin은 보존한다. 아래에 적지 않은 기능 범위·파일 scope·외부 acceptance는 [v2](drive-oauth-nondeveloper-v2.md) 그대로이며, 이 v3는 그 계약에 아래 5 required와 2 optional만 대체·추가한다.

## 적용 경계와 기존 코드 근거

현재 [app.py](../../src/uls/settings/app.py)의 mutation helper는 Origin/session/CSRF 검증, explicit activity, `MutationBarrier`, worker-thread 순으로 실행된다. [security.py](../../src/uls/settings/security.py)의 `SecurityBoundary`는 일반 세션 경계와 mutation을 보호한다. [credential_service.py](../../src/uls/settings/credential_service.py)의 `save()`는 config generation을 `ConfigFileLock` 안에서 확인한 뒤 admission/journal/stage/provider verify/CAS 경로를 소유한다. 따라서 OAuth도 그 서비스 경로를 재사용한다.

현재 [provider_checks.py](../../src/uls/settings/provider_checks.py)는 in-process/socket budget이 OS DNS resolver 대기를 끊지 못하고, [runtime.py](../../src/uls/runtime.py)의 authenticated Drive `about().get(...).execute()`는 해당 budget을 통과하지 않는다. [launcher.py](../../src/uls/settings/launcher.py)는 config/journal 및 base services 준비 후 socket→port→prefix/session→app 순서다. [config/loader.py](../../src/uls/config/loader.py)는 `load_config()`에서 validation 오류를 시작 전에 거부한다. R3/R4/R5 계약은 이 현재 동작에 맞춰 아래처럼 고정한다.

## Required 1 — GET 조회와 POST commit 분리

`GET /P/api/v1/google-oauth/{purpose}/{flow_id}`는 순수 read-only polling이다. 상태·provider 호출·activity timestamp·candidate 소비·credential/config/journal·commit 전이를 바꾸지 않는다. callback도 저장하지 않는다.

유일한 저장 진입점은 `POST /P/api/v1/google-oauth/{purpose}/{flow_id}/commit`이다. 기존 app mutation 경로를 그대로 사용해 exact same-origin/current-session/CSRF/explicit-activity 검증과 `MutationBarrier`를 거친다. JSON 본문은 정확히 `{"generation":"<flow 시작 때 캡처한 값>"}`이며 추가·누락 키를 거부한다. CSRF/body/session 검증에 실패하면 flow candidate는 소비하지 않는다. Barrier 안에서 단일 flow owner가 lock으로 session epoch, purpose/role/client, expiry, `awaiting_commit`, generation 일치를 확인하고 한 번만 `awaiting_commit → committing`으로 바꾼다. Lock을 놓은 뒤 기존 `CredentialService.save(role, candidate, generation, replace=...)`를 worker thread에서 한 번 호출한다. 최신 config generation은 `save()`의 기존 config lock/CAS에서 다시 확인한다. 중복·동시 POST는 save를 두 번 실행하지 않는다. callback, poll, result page는 commit을 호출하거나 대신할 수 없다. 저장 결과 UI는 기존 journal/card 및 fresh credential GET readback을 따른다.

수락 회귀: awaiting 상태의 **GET poll 반복은 provider/save 모두 0회**이며 active/staged/backup/config/journal bytes 불변. callback은 code exchange와 account check까지 허용(최대 token POST 1회+identity GET 1회)하되 save 0회이고 credential/config/journal bytes 불변이다. missing/wrong CSRF, cross-site POST, 만료·교체된 session, stale generation은 save 0회다. 동시 duplicate commit 요청은 전체 save 진입 정확히 1회이며 나머지는 0회; 유효한 한 POST만 기존 service/barrier/CAS를 한 번 실행한다.

## Required 2 — CSPRNG, 일회 소비, exact authorization URL

Production `state`와 PKCE `verifier`는 서로 독립된 `secrets.token_bytes(32)` CSPRNG 출력(각 256-bit entropy)을 padding 없는 base64url 43자로 만든다. Verifier는 RFC7636 허용 길이(43–128자)에 있고 challenge는 그 값의 SHA-256/base64url `S256`이다. deterministic RNG는 테스트 전용이다.

Google authorization URL은 고정 HTTPS endpoint와 정확히 이 query allowlist만 사용한다: `client_id`, exact `redirect_uri`, `response_type=code`, purpose별 exact `scope`, `state`, `code_challenge`, `code_challenge_method=S256`, `access_type=offline`, `prompt=consent`. 이 offline-consent 조합으로 refresh token을 요청한다. caller/browser/config가 endpoint나 추가 query를 주입하지 못한다. code exchange 결과에 refresh token 또는 exact server-granted scope가 없으면 기존 credential을 보존하고 거절한다.

Callback은 첫 외부 exchange 전에 하나의 flow-owner lock 안에서 exact host/path/loopback peer, query, live session epoch, TTL, state constant-time match 및 `pending` 상태를 검사하고 state를 소비하며 `pending → exchanging`으로 원자 전이한다. 이것이 유일한 consume 지점이다. 동일 state 동시 callback 중 최대 하나만 token endpoint를 호출한다. replay·mismatch·expired·cancelled·replaced 요청은 wire call 0회이며 실패 후 같은 state를 재사용하지 않는다.

Callback의 raw query는 최대 **8,192 bytes**, 성공 key는 `state`,`code`, 거부 key는 `state`,`error`만 허용한다. state는 정확히 43 ASCII 문자, decoded code는 1–4,096 bytes, error code는 최대 64 ASCII 문자다. duplicate/unknown key, 제어문자, 개별 또는 합산 한도 초과는 교환 전에 거부한다.

수락 회귀: deterministic known vector로 state/verifier/challenge와 전체 authorization URL exact 비교; query key 누락·추가·변조 거부; 동시 callback 두 개 중 token POST 정확히 1회; replay/mismatch/expired 요청 0회; refresh token/scope 누락 시 기존 bytes 불변.

## Required 3 — 숫자로 제한된 DNS 포함 transport 경계

DNS가 thread/socket timeout 밖에서 멈출 수 있으므로 exchange/refresh/Drive account identity GET 및 OAuth save/provider verification의 네트워크는 새 `google_oauth.py` 안의 고정 worker entrypoint를 이용한 **단일 killable subprocess 경계**에서만 수행한다. Child는 고정 Google endpoint/operation만 실행하고 child process를 만들지 않는다. 제3자 의존성은 추가하지 않는다. 이 경계는 OS scheduler/kill 자체가 정지한 상황까지 무조건적인 wall-clock 보장이라고 주장하지 않으며, 정상 OS scheduling 아래 user-space DNS/TLS/socket 정지를 종료시킨다.

숫자 상한은 다음과 같다.

- 한 논리 작업의 시작부터 결과 검증까지 monotonic deadline **30.0초**; connect/TLS/read socket timeout 각각 최대 **10.0초**. Deadline/cancel 때 부모가 subprocess를 kill하고 reap한다.
- 각 HTTP 응답 body 최대 **1,048,576 bytes**, stdout/private result frame도 최대 **1,048,576 bytes**. 부모가 stdout을 streaming으로 제한하고 cap을 넘기는 첫 byte에서 child를 종료·reap한 뒤 frame을 버린다. unbounded `communicate()`로 전부 메모리에 모은 뒤 cap을 검사하는 방식은 금지한다. Cap 확인 전에 JSON parse하지 않는다.
- Redirect **0**, 자동/SDK retry **0**. Code-exchange 작업은 token `POST` 1회와 identity `GET` 1회 이내. Save/provider verify는 refresh `POST` 1회, Drive `about` identity `GET` 1회, configured target GET 최대 8회로 전체 **10 wire calls** 이내다. 추가 호출 요구는 거부한다.

`Popen`은 고정 executable/module/args, `shell=False`, `close_fds=True`를 사용하고 event loop 밖에서 실행한다. code/verifier/refresh token은 argv/env가 아닌 길이 제한 private stdin pipe로만 보낸다. Code-exchange/저장검증 child는 access token을 child 안에서만 사용하고, 성공 시 검증된 refresh-token candidate와 최소 binding만 private pipe로 부모 메모리에 반환한다. **Runtime refresh는 별도 반환 규칙**을 쓴다: helper가 실제 scope, account permission ID, client 및 purpose를 검증한 뒤 access token·expiry·검증 binding을 capped private pipe로 부모의 in-memory public Google credential adapter에 전달한다. Adapter는 token을 SDK RPC authorization header에만 적용하며 브라우저/API response/log/journal/disk에는 노출·저장하지 않는다. SDK `apply()`용 access token 반환은 허용하되 refresh/network 요청은 항상 helper로 위임한다. Runtime `google_worker_service`는 unbounded `googleapiclient ... about().get(...).execute()`를 다시 호출하지 않고 동일 helper가 반환한 verified account attestation/binding을 재사용한다. stderr는 버리고 오류 frame은 fixed code만 허용한다. Cancellation/session invalidation은 child 종료를 요청하고 late result는 무효 flow epoch로 폐기한다. 일반 Drive file I/O 전체의 deadline까지 보장한다고 확대 주장하지 않는다.

수락 회귀: fake child에서 DNS-before-socket, connect/TLS, header/body trickle, cap+1, 302, hidden retry/두 번째 refresh, timeout과 cancel을 유도해 30초 전에 종료·reap, save 0회 및 기존 credential/config/journal exact bytes 보존 확인. Secret sentinel이 argv/env/log/stdout error/API에 없는지 검사한다. App event loop 및 browser cancel이 계속 응답해야 한다.

## Required 4 — launcher 순서와 owner 직렬화

Composition 순서는 다음으로 고정한다: (1) config load, `ConfigStore`/`JournalStore` 및 기존 credential/canvas base service 준비, (2) `reserve_loopback_socket()` 후 exact port 확정, (3) prefix와 `SessionSecurity` session/epoch 발급, (4) 그 port/prefix/session epoch에 immutable하게 묶인 exact redirect를 가진 `GoogleOAuthService` 생성, (5) app/server 생성·시작, (6) browser opener 실행. OAuth service를 port/session 확정 전에 만들지 않는다.

Browser opener 및 provider/subprocess I/O는 event loop 밖에서 수행한다. `webbrowser.open()` false/예외는 safe launch failure로 끝내고 저장하지 않는다. OS browser 종료를 즉시 탐지한다고 약속하지 않으며 cancel 또는 active TTL 300초가 종료 수단이다. Flow registry의 모든 transition은 하나의 owner lock/loop에서 직렬화한다. Worker는 immutable 결과만 반환하며 flow ID/epoch/state를 재검증한 후 반영한다.

Session close, idle expiry, replacement는 해당 epoch의 uncommitted `pending|exchanging|awaiting_commit` flow를 먼저 invalidate하고 secret/candidate를 지우며 실행 child를 취소한 후 `MutationBarrier` close/drain을 한다. 이미 barrier 안에서 `committing`인 service save는 취소한 척하지 않고 기존 drain/journal recovery를 따른다. 늦은 child/browser 결과는 새 session이나 저장 상태로 되돌릴 수 없다.

수락 회귀: reserved port/prefix/session/redirect exact binding; exchange 또는 opener가 block 중이어도 event loop, cancel, replacement, close가 응답; invalidate 뒤 late success가 `awaiting_commit`/save를 만들지 않음; 이미 committing된 작업은 barrier가 drain할 때까지 대기.

## Required 5 — absent readiness와 malformed fatal 경계

`google_oauth` 전체 부재 또는 한 purpose bundle 부재는 valid config로 두고 Settings를 정상 시작시킨 뒤 그 purpose만 `OAUTH_APP_NOT_READY`로 표현한다. 다른 purpose bundle이 준비된 상태도 유효하다.

Section/bundle이 존재하면 top-level/nested mapping type, 정확 allowlist 키, non-empty 형식, required `client_id`, `client_secret`, `cloud_project_id`를 엄격히 검증한다. Wrong type, unknown key, 부분·빈·잘못된 값은 not-ready로 강등하지 않고 config validation에서 fail-closed한다. 두 완성 purpose가 client 또는 Cloud project를 공유하면 거부한다. Bundle 값은 overview/error/log에 나타나지 않으며 `CredentialResolver`, provider credential payload, `.env` secret source나 Remote MCP OAuth 설정에 합치지 않는다. 이 추가 validation은 `google_oauth`에만 적용하고 unrelated unknown top-level handling을 바꾸지 않는다.

수락 회귀: 기존 section 없는 config load 및 Settings start 성공 + purpose not-ready; 각 malformed/unknown/partial/duplicate client-project 조합은 fatal; status/overview/log/CredentialResolver/Remote MCP에 bundle 값이 없는지 검사.

## Optional 1 — terminal cache bounded

Terminal이 되는 즉시 purpose slot을 풀어 새 begin을 허용한다. Secret/state/verifier/code/candidate는 즉시 삭제한다. Read-only terminal ID/code/session digest만 최대 300초 보관하고 목적별 최근 4개(전체 최대 8개)로 제한한다. TTL/eviction 후 poll은 fixed `FLOW_NOT_FOUND`다. Terminal result는 commit 재시도에 쓰지 않는다.

## Optional 2 — callback/result navigation Fetch Metadata

Fetch Metadata의 `Sec-Fetch-Site`는 redirect chain 전체를 반영한다([Fetch Metadata §4.1](https://www.w3.org/TR/fetch-metadata/#redirects)). 따라서 callback은 `mode=navigate`, `dest=document`, `site=cross-site`; opaque fixed result는 `mode=navigate`, `dest=document`, `site=cross-site|same-origin`을 허용한다. 세 헤더가 모두 없는 브라우저만 exact host/peer/path/query/state/session gate로 compatibility 허용한다. 중복·일부 누락·다른 mode/dest/site·subresource/iframe/no-cors는 거부한다. 예외는 callback/result 두 GET에 한정한다.

수락 회귀: 실제 fake-browser Google→callback→303→result redirect chain이 `cross-site,navigate,document`로 result에 도달하고 fixed no-query page를 표시한다. Same-origin result도 허용한다. Callback/result subresource·iframe, partial/duplicate headers와 잘못된 mode/dest/site는 거부한다. Header 전부 부재한 호환 경로는 정확 state/peer/path 검사를 계속 요구한다.

## 기존 범위와 남은 acceptance — 변경 없음

v2의 인간 권한 결정, 두 purpose의 별도 Desktop client/project/token, exact full-Drive scope, public-client 설정, service-account/기존 path 호환, `CredentialResolver` single-read, `CredentialService.save` protected transaction/CAS/recovery, UI 범위, 파일 후보, 테스트 대상 및 A/B/C/D 분할은 그대로다. 범위 밖은 CLI, Remote MCP, Drive/Intake kernel, Notion/Canvas 변경, 자동 worker 활성화, ACL mutation, revoke API, checker/pins/global, GUI4/RESEARCH다. bounded helper는 이미 계획된 새 `google_oauth.py` 안에서만 구현하며 새 third-party/file scope를 추가하지 않는다. Native/Gemini independent PLAN과 통합 GO 전 source/test 변경은 금지다.

A는 protocol/runtime/config/HTTP, B는 credential transaction/admission/journal/store/CAS, C는 browser/UI/harness, D는 unchanged Drive API/privacy contract의 complete related-source review다. 각 packet은 120,000-token 상한에서 관련 원본 전문을 포함하며 excerpt/hash-only 대체를 쓰지 않는다. 사용자 전체흐름의 기존 조건부 gate도 유지한다: 등록된 실제 Desktop clients와 human login/consent, School USER attribution, 실제 자료 freshness/readback/retrieval, Notion REST 별도 호환성 및 승인, 기존 ACL 보존을 확인한 뒤에만 전체 flow acceptance/PR/commit 검토가 가능하다. 현재 ACL을 자동 수정하거나 owner-only 조건을 완화하지 않는다. 실제 provider/School/credential, `.env`, Keychain 값은 PLAN·fake test에서 읽지 않는다.

## Source basis

이번 bounded 수정은 위에서 인용한 현재 파일들만 확인해 작성했다: `src/uls/settings/app.py`, `security.py`, `credential_service.py`, `provider_checks.py`, `runtime.py`, `launcher.py`, `composition.py`, `config/schema.py`, `config/loader.py`, `config/validation.py`. v2 full implementation scope와 source pins는 변경하지 않았다. 이 문서 작성 중 제품/test source, v1/v2, pins는 수정하지 않았다.
