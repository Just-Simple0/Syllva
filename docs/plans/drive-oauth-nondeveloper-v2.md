# Drive 사용자 OAuth — 구현 계약 v2

단계: 독립 PLAN 검토용 계약이다. 제품·테스트 변경, 실제 로그인, source pin 갱신, 전체 School 흐름 수락은 아직 없다. `drive-oauth-nondeveloper.md`와 초기 71파일 inventory는 v1 조사 기록으로 보존한다. 현재 구현 범위와 리뷰 기준은 이 문서다.

## 인간 결정과 범위

사용자는 Drive 기본 연결을 OAuth로 수정하고 Notion·Canvas를 비개발자 관점에서 평가하도록 지시했다. 이어 권한 선택에 **“기존 School 유지: 검색은 읽기 전용, 자료 처리는 Drive 관리 권한으로 분리 (권장)”**라고 답했다. 따라서 고정 목적은 MCP=`https://www.googleapis.com/auth/drive.readonly`, WORKER=`https://www.googleapis.com/auth/drive`다. 이번 구현에 `drive.file`, Picker, 선택 파일만 가져오는 별도 corpus를 추가하지 않는다.

Google 권한은 각각 Drive 전체 읽기/관리다. Syllva의 실제 작업 대상은 기존 설정의 School·학기·과목 ID 및 승인된 작업으로 제한하며, Google 동의가 폴더 한정 권한이라고 표현하지 않는다. 읽기·쓰기 token/client/저장 slot을 분리한다. Remote MCP의 `openid email` 로그인과 Syllva-issued token은 별도이며 재사용하지 않는다. Notion REST parser의 실제 호환성 교정, Notion/Canvas 인증 변경, worker 지속 활성화, 외부 OAuth 앱 등록은 이 product diff에 넣지 않는다.

이번 연결 해제는 **로컬 연결 해제**다. Google revoke API, project-wide 원격 철회 journal, 자동 ACL 수정은 구현하지 않는다. Google 계정의 앱 권한 관리 페이지를 별도로 안내할 수 있으나 자동 철회 성공으로 표현하지 않는다. 이로써 OAuth code exchange 이후 원격 permission 삭제와 local CAS가 엇갈리는 새 transaction을 도입하지 않는다.

## 기본 사용자 흐름

Connections에 Google 기본 browser 연결을 표시한다. 검색용과 자료 처리용이 하는 일을 짧게 설명하고 별도로 연결한다. 사용자에게 Cloud Console, client ID/secret 입력, JSON 다운로드를 요구하지 않는다. Advanced service-account import와 기존 environment/external-file 설정은 호환 경로로 남긴다. 앱 준비가 없거나 macOS 보호 저장소를 사용할 수 없으면 기본 버튼을 막고 해결 행동을 설명한다.

연결 전 사용자는 purpose, 이미 연결된 credential의 교체 여부, Google에서 받는 실제 권한을 확인한다. 시스템 브라우저에서 계정을 선택하고 동의한 뒤 원래 Settings 창에서 완료를 확인한다. 다른 목적이 이미 OAuth로 연결됐다면 같은 Google 계정인지 실제 Drive permission ID로 비교한다. 다른 계정은 저장하지 않고 먼저 기존 목적의 연결을 정리하거나 같은 계정을 선택하라고 안내한다. 최초 계정 선택은 사용자의 Google consent에 귀속하고, 이후 School 소유권은 실제 provider 검사로 별도 판정한다.

취소·거부·timeout·실패·session 종료에는 기존 연결이 유지되고 다시 시도할 수 있어야 한다. 브라우저 창 닫힘을 OS browser에서 즉시 탐지한다고 약속하지 않는다. 사용자가 취소하거나 TTL이 지나면 대기 상태를 해제한다. 화면 문구·token/error projection은 기존 UI 언어와 스타일을 유지한다.

## 앱 준비와 설정 계약

`google_oauth.mcp`와 `google_oauth.worker`에 배포자가 Desktop/public client bundle을 제공한다. 허용 키는 `client_id`, `client_secret`, `cloud_project_id`이며 provider endpoints/type/scope/redirect는 설정으로 바꾸지 못한다. Google Desktop client의 client secret은 공용 앱에서 비밀을 유지할 수 없는 public-client 값이며 사용자 refresh token과 구별한다. google-auth 교환/refresh가 요구하는 해당 값은 배포자 bundle로 제공하고 일반 화면·진단에는 표시하지 않는다. 실제 secret/config 값은 이번 설계 검토에서 읽거나 패키징하지 않는다.

기본 운영은 두 다른 Cloud project/client 및 Remote MCP와 다른 project다. client ID 차이만으로 프로젝트 분리나 서버 grant 범위를 입증했다고 주장하지 않는다. 배포자가 등록 유형과 project 대응을 검증·기록하는 readiness가 필요하다. code는 동일 client/project 값을 두 purpose에 배정하는 설정을 거절한다. 빠진 bundle은 `OAUTH_APP_NOT_READY`이며 개발자 ID 입력 화면 대신 관리자에게 앱 준비를 요청하는 안내다. 현재 실제 Desktop bundle은 없다. 출시용 restricted scope 검증과 testing refresh-token 만료 조건도 앱 준비 문서에서 구분한다.

## 서버 API와 flow 상태

`GoogleOAuthService`를 새 `src/uls/settings/google_oauth.py`에 둔다. provider transport와 clock/random/browser opener는 테스트 주입 가능하되 live transport endpoint와 role scope는 코드가 소유한다. 각 Settings 실행 프로세스의 메모리 안에서 최대 purpose당 1개, 총 2개 flow만 허용한다. TTL은 시작부터 5분, code exchange transport budget은 최대 30초/제한된 응답 크기다. 지연된 transport가 취소된 flow를 저장하지 못하도록 모든 후속 단계에서 flow epoch와 terminal 상태를 재검증한다.

현재 launch prefix를 `P`, origin을 `http://127.0.0.1:<reserved-port>`라 할 때 다음만 추가한다.

| 경로 | 입력·인증 | 결과 |
|---|---|---|
| `POST /P/api/v1/google-oauth/{purpose}/begin` | 기존 same-origin/session/CSRF/명시 활동; exact JSON `{generation, replace}`; purpose mcp/worker 고정 | opaque `flow_id`, `state=pending`, TTL seconds와 fixed 안내. 서버가 검증된 Google URL을 OS browser로 연다. URL/code/verifier는 반환하지 않는다. |
| `GET /P/api/v1/google-oauth/{purpose}/{flow_id}` | 기존 same-origin/session; 시작한 session만 허용; polling은 명시 활동 갱신 아님 | safe `{flow_id,state,code}` 및 terminal 결과만. credential/code/URL/계정 ID 반환 없음. |
| `POST /P/api/v1/google-oauth/{purpose}/{flow_id}/cancel` | 기존 session/CSRF; exact empty JSON | pending/exchanging/awaiting-commit flow 무효화, safe cancelled 결과. 저장 시작 이후에는 이미 효과가 진행 중임을 명시하고 journal 결과를 확인한다. |
| `GET /P/oauth/google/callback` | 아래 좁은 callback 전용 gate, Google state+code 또는 state+error | 정상 session cookie를 발급하거나 CSRF 우회 권한을 만들지 않는다. `303` query 없는 고정 완료 안내로 이동한다. |
| `GET /P/oauth/google/result` | 고정 무데이터 문서, callback 전용 completion page | 원래 Settings 창으로 돌아가 결과를 확인하라는 안내만. bootstrap/session/flow ID/token 없음. |

상태는 `pending → exchanging → awaiting_commit → committing → complete` 또는 committing 전 `cancelled/denied/expired/failed`다. duplicate begin은 기존 flow를 조용히 supersede하지 않고 busy 반환한다. 동일 purpose의 동시 credential mutation이 flow generation을 바꾸면 commit하지 않는다. 시작에 session identity, purpose, role binding, config generation, configured client/project, requested scope, exact redirect와 무작위 state/PKCE를 묶는다. session replace/close/process restart는 미커밋 flow를 전부 무효화한다.

callback은 code를 교환하되 **credential 저장을 직접 실행하지 않는다**. 검증된 candidate는 짧게 메모리에만 유지한다. 원래 Settings 창의 session-authorized poll이 현재 generation/epoch를 확인한 뒤 existing mutation barrier 안에서 committing을 시작한다. 저장 시작 시점을 넘긴 cancel/close는 이미 journalled mutation의 정상 drain/recovery 규칙을 따른다. session 없는 callback이 권한을 저장하거나 active worker를 켜는 일은 없다. 완료 후 fresh credential GET/Overview readback으로만 UI를 갱신한다.

## callback 보안 경계

Google redirect는 cross-site이고 기존 SameSite=Strict cookie가 없을 수 있으므로 기존 일반 API gate를 통과한다고 가정하지 않는다. `SecurityBoundary`에 위 **두 GET exact path만** 좁은 예외로 둔다. 일반 prefix/host/duplicate-header/body/security-header 검사와 기존 모든 POST/session/CSRF 규칙은 유지한다. callback gate는 loopback peer, exact expected Host/path/method, 하나씩의 query key, bounded query length, state 세션 결속·미사용·TTL, code/error 배타 조합을 검증한다. 예외는 arbitrary OAuth route나 bootstrap으로 확장하지 않는다.

PKCE verifier는 서버의 해당 flow에만 있고 S256 challenge를 authorization에 보낸다. 교환은 같은 client/verifier/exact redirect를 사용한다. code exchange의 PKCE 검증은 Google authorization server가 수행한다. state mismatch·중복 query·replay·다른 host/port/path·session replaced·timeout은 code 교환/저장을 거절한다. callback code가 표준 OAuth query에 순간 나타나는 것은 인정하며, access log/query/error 출력 금지, no-store/no-referrer, 즉시 query-free redirect로 잔존을 줄인다. token을 브라우저로 보내지 않는다. 고정 Google HTTPS endpoints만 허용하고 HTTP redirect를 따라 credential을 전달하지 않는다.

## 실제 grant와 runtime

server exchange/refresh의 실제 응답으로 scope를 판정한다. 로컬 JSON `scopes`, SDK constructor 인수, `has_scopes`만을 Google 권한 증거로 인정하지 않는다. live transport는 고정 Google token endpoint로 code 또는 refresh grant를 교환하고 bounded response의 실제 `scope`를 받는다. 목적의 **정확한 scope 집합**이 없거나 불일치하면 fail closed 한다. 응답에 scope가 없어 권한을 입증하지 못하면 재연결 필요이며 가정해서 통과시키지 않는다. refresh 요청에도 고정 목적 scope를 사용하고 매 응답을 다시 확인한다.

Google account는 그 token으로 `Drive about.user(permissionId)`를 읽어 확인한다. configured role client와 교환 client 일치, fresh purpose grant, 실제 account 및 OAuth peer 계정 일치를 묶는다. config에 적은 project 문자열만으로 Google의 project identity를 독립 attestation 했다고 주장하지 않는다. intake binding은 현재 runtime의 account permission ID/client ID digest를 사용하며 account/client raw 값을 journal·state·로그에 남기지 않는다.

authorized-user 저장 payload는 표준 `type/client_id/client_secret/refresh_token/token_uri/scopes`와 코드가 소유하는 purpose metadata의 정확한 allowlist다. token URI는 Google endpoint 고정이다. access token/code/id token은 저장하지 않는다. 새 grant에 refresh token이 없으면 연결 실패로 기존 grant를 유지한다. configured role client와 다른 payload는 runtime에서 거절한다. arbitrary external authorized-user payload도 신뢰하지 않고 같은 fixed endpoint/client/fresh grant 검증을 거친다. OAuth live loader는 매 load/refresh에 동일 검증을 적용해 SDK가 자동 refresh하면서 검사를 우회하지 못하게 한다. 필요하면 작은 Google OAuth credential adapter를 같은 새 module에 두고 google-auth의 public interface를 사용한다. 별도 resolver나 두 번째 file read는 만들지 않는다.

일반 Google JSON 업로드 API는 service-account 전용을 유지한다. OAuth candidate는 서버 내부 service 호출로만 전달하며 browser 요청의 OAuth JSON을 받지 않는다. SA-SA 기존 `client_email/private_key_id` 분리는 유지한다. OAuth-OAuth는 별도 client와 refresh grant를 요구하되 같은 account를 허용한다. SA-OAuth 혼합은 유효한 typed peer와 목적을 확인하며 SA에 OAuth metadata를 강제하지 않는다. 같은 blob/refresh grant 또는 OAuth client를 두 purpose에 사용하지 않는다. malformed/unreadable peer는 기존처럼 fail closed 한다.

`CredentialResolver`는 정상 protected file을 한 번 읽어 `GoogleCredentialPayload`를 만든다. runtime load와 Settings structural/provider checks가 authorized-user를 처리하도록 최소 보완한다. MCP composition은 worker credentials를 fallback하지 않고 read-only adapter만 만든다. worker connection check도 GET-only 검사다. worker credential의 OAuth scope가 drive라는 사실과 connection check의 GET-only 효과를 구별한다. test 단계가 자료를 변경하지 않는다.

## 저장·기존 경로·복구

OAuth는 기존 role managed slot의 파일명을 그대로 재사용해 migration source 종류를 늘리지 않는다. 저장은 `CredentialService.save`의 admission, full journal, role/config locks, staged/backup, verified candidate, config generation CAS 및 exact recovery를 사용한다. 신규 external-path 연결은 기존 bytes를 수정하지 않고 managed enrollment와 config 참조 전환으로 처리한다. 이미 managed slot이 있으면 기존 replacement 규칙을 따른다. 기존 external path/bytes는 자동 삭제하지 않는다.

추가 validation은 저장 직전 current config/client/purpose/peer 및 flow generation을 다시 확인한다. provider exchange 동안 config/role lock을 잡지 않는다. `save`의 기존 read-only verify와 config-CAS 실패/부분 진행 복구 의미를 재사용하고 raw provider text는 폐기한다. late callback/cancel/session 종료와 CAS collision 테스트는 active/staged/backup/config/journal의 exact bytes를 관측한다. 실패 상태가 unresolved journal이면 기존 safe recovery choices를 표시하며 Clean staged copy를 과거 credential 복구로 잘못 해석하지 않는다.

## 실제 변경 범위와 리뷰 분할

구현 담당은 같은 Sagan `gpt-6-luna/max`, 총괄은 `gpt-6.1-sol/high`다. 부모가 critical path의 v2 계약 통합을 맡고 owner는 PLAN 지적 반영·제품·검사·FINAL 수정의 연속 책임을 유지한다. Native web ChatGPT 및 Gemini ultra PLAN 두 gate 수락 전에는 제품 변경이 없다.

| 대상 | 최소 변경 |
|---|---|
| 새 `settings/google_oauth.py` | flow registry, fixed transport, PKCE/callback, safe status/commit coordination, authorized-user loader/refresh validation |
| `config/schema.py`, `loader.py`, `validation.py`, `config.example.yaml` | 배포자 Google Desktop bundle 구조·검사, 비밀값 없는 missing/readiness |
| `config/credentials.py`, `runtime.py` | payload doc/allowlist와 single-read snapshot 연결, purpose-configured OAuth loader; 기존 SA 유지 |
| `settings/provider_checks.py`, `credential_service.py` | typed structural/peer separation와 GET-only OAuth 검사·server-owned save; 기존 journal transaction 보존 |
| `settings/app.py`, `security.py`, `composition.py`, `launcher.py` | API wiring, exact callback/result GET exception, active session poll/mutation barrier, server browser opener와 shutdown invalidation |
| `settings/status.py`, `static/app.js`, `index.html`, 필요한 최소 CSS | OAuth default/Advanced, readiness·대기·취소·완료·재연결·local disconnect의 safe UI와 기존 경합 규칙 |
| 새 `tests/contract/test_settings_google_oauth.py`, 해당 runtime/config/provider/HTTP/UI/credential-service tests와 harness | 아래 acceptance 검증 |

`credential_roles.py`/stores/admission/journal/secure_file는 우선 unchanged dependency다. 직접 효과나 recovery schema 변경이 필요해지면 같은 owner가 근거를 보고하고 변경 부분의 PLAN을 추가 검토한다. CLI main/credential_set, Remote MCP, Drive/Intake kernel, Notion, Canvas, frozen spec, checker/pins, GUI4/RESEARCH를 구현 후보에서 제외한다. 기존 imports/shared validators를 통한 호환 영향은 관련 회귀로 확인한다. 필요한 직접 변경은 scope를 몰래 확대하지 않는다.

초기 1.75MB 목록을 두 패키지로 넣지 않는다. fixed120000 tokens 아래 complete bounded full files로 A(protocol/runtime/config/HTTP), B(protected transaction/CAS), C(browser/UI), D(unchanged Drive API/frozen privacy contract)를 나눈다. adjacent concerns는 전문의 공통 계약과 각 companion 리뷰로 연결하고 integrated GO 전에 필요한 Native/Gemini 지적을 전부 처분한다. token 예산을 위해 파일 본문을 잘라내거나 패키지를 압축하지 않는다. D는 intake kernel/Notion 변경 검토나 전체 live flow GO가 아니다.

## 검사와 최종 수락

1. protocol: state entropy/one-use, S256/verifier binding, host/peer/path/query/replay/expiry, absent Strict cookie의 callback 허용과 일반 API 여전한 거절, callback-only 무저장, 원래 session poll만 commit, config/session/flow epoch race, duplicate begin, cancel·late completion·launch exception/false·shutdown.
2. scope/identity: actual exchange·refresh scope의 정확 일치/누락/초과/축소 거절, token endpoint redirect 거절, client/account/peer mismatch, same-account distinct purposes, cross-purpose blob/grant/client 재사용 거절, refresh/no-refresh-token/timeout에서 old bytes 보존.
3. storage: SA/runtime/CLI 회귀, external path untouched→managed CAS 전환, existing recovery exact bytes, mutable config race, staged cleanup 후 이전 active 미복원 문제 재발 없음, protected mode/owner/no-follow/size/single-read.
4. UI: missing app, two purposes/default vs Advanced, accept→busy, async rejection/timeout/cancel→busy 해제, stale poll/Overview/readback supersession, generation/focus/notice/retry, terminal failure와 journal recovery, local disconnect의 실제 의미. fake provider+실제 local browser에서 마지막 source hash에 결속된 화면·동작·최종 screenshot을 확인한다.
5. 같은 scope의 Native/Gemini FINAL, fresh exact-source audit와 필요한 checks, source pin이 바뀐 경우 새 exact candidate 독립 검토와 인간 적용 결정 후 project checker를 수행한다. 기존 GUI97/447은 baseline이며 새 OAuth 검사를 대신하지 않는다.

현재 School에는 owner 외 2개 공유 권한이 있다. 이 계약은 `owner_only`를 완화하거나 공유를 삭제하지 않는다. 인간에게 별도 private-copy 검증 폴더 vs 정확한 기존 ACL 확인·정리 결정 질문이 제시돼 있다. 답이 없으면 실제 쓰기 검증을 진행하지 않으며 auth 코드/로컬 fake 검사는 계속할 수 있다. 실제 Desktop app 등록·인간 로그인·동의, 선택한 private destination·source provenance, Notion 호환 교정/별도 MCP credential, 귀속 가능한 USER 제출, 정상 저장·Partial·readback·검색 capability 및 멱등성까지 관측한 뒤에만 전체 School 흐름을 수락한다. 사용자가 지정한 이 통과 조건 전에는 commit/push/새 PR을 만들지 않는다.
