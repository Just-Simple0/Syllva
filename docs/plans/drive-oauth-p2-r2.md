# Drive OAuth P2 구현 계획 r2

상태: 총괄에게 제출할 계획 초안이며 독립 검토 대기(DRAFT / PENDING)다. P1 Drive 개인정보 보강의 구현·수락과 P2 계획 검토 및 별도 구현 GO 전에는 제품·테스트·fixture·설정·핀·ACL 변경이나 테스트 실행을 하지 않는다. 기존 docs/plans/drive-oauth-p2.md와 과거 리뷰 증거는 원본으로 보존한다. 본 문서는 보완판이다.

## 1. 목표와 경계

한 사용자가 자기 Google Cloud Desktop OAuth client 하나로 자기 Syllva에 Drive를 연결한다. 공개 저장소·package·fixture에는 실제 client ID/secret·token을 두지 않는다. 같은 client로 목적별 동의를 각각 받아 서로 다른 MCP와 WORKER refresh credential을 기존 보호 저장 경로에 둔다.

| 역할 | 유일한 허용 scope | Google 권한 |
|---|---|---|
| 읽기 전용 MCP | https://www.googleapis.com/auth/drive.readonly | Drive 읽기 |
| 자료 처리 WORKER | https://www.googleapis.com/auth/drive | Drive 전체 읽기·쓰기 |

실제 OAuth grant에서 확인한 scope가 역할의 단일 scope와 정확히 같아야 한다. drive.file, Picker, scope 축소 주장은 제외한다. School 폴더 allowlist는 처리 대상을 고를 뿐 Google 권한을 제한하지 않는다. 계정 설정에서 같은 client를 철회하면 두 연결 모두 끊길 수 있다. service-account JSON 고급 경로는 현 동작을 보존하며 google_oauth가 비어 있어도 활성화된다. remote MCP용 Notion token은 Google OAuth와 별개다.

불변식: client/config 외 노출 0, role/scope/parser는 code-owned, purpose별 protected refresh credential은 분리한다. Callback은 state·PKCE·loopback·grant·계정을 검증하지만 저장 0이며 commit만 원래 session의 CSRF POST로 허용한다. 검증 불일치는 journal/reservation/staging/promote/config/active 변경 전에 거부한다. 단일 resolver snapshot만 쓰고 env/file 재조회는 없다. WORKER fresh gate는 authorized_user만 적용하고 service_account 경로/binding은 유지한다. 원격 revoke·ACL 변경·자동 활성화는 없으며 forget은 지정 role의 로컬 상태만 제거한다. 응답 유실 뒤 저장을 자동 재시도하지 않는다.

## 2. 설정, route와 flow

google_oauth는 client_id/client_secret 두 문자열을 모두 받거나 모두 비운다. 부분·잘못된 쌍은 안전 오류다. overview에는 설정 존재를 뜻하는 boolean만 둔다. 값이 없으면 begin은 OAUTH_APP_NOT_READY이며 opener/provider 호출은 0이다. 선택적 O1: 기존 config 파일은 현재 사용자 소유여도 group/world 권한이 열려 있을 수 있다. POSIX에서는 google_oauth 값이 있는 config의 권한이 안전하지 않으면 fail-closed readiness 오류를 내고 자동 chmod하지 않는다. Windows 기존 동작과 현재 config 보호 의미는 유지한다. 별도 secret store를 만들지 않는다.

P는 launcher의 현재 path prefix, host/port는 실제 예약 loopback socket의 authority다. 모든 mutation POST는 기존 session·same-origin·CSRF gate와 exact JSON key/type을 통과한다.

| 경로 | 계약 |
|---|---|
| POST /P/api/v1/google-oauth/{purpose}/begin | body {generation, replace}. role generation 및 실제 target 상태와 일치해야 한다. purpose별 flow 하나, process당 최대 두 개. browser를 열되 URL/state/verifier는 반환하지 않는다. busy면 409 FLOW_BUSY, 기존 flow는 보존한다. |
| GET /P/api/v1/google-oauth/{purpose}/{flow_id} | 시작한 동일 live session의 고정 상태 필드만 읽는다. credential·URL·code·client·token·permission ID·provider 문구는 없다. 저장 0회. |
| POST /P/api/v1/google-oauth/{purpose}/cancel | body {flow_id,generation}. pending/exchanging/awaiting_commit만 취소 가능. committing은 취소·재작성하지 않고 409 COMMIT_IN_PROGRESS. |
| GET /P/oauth/google/callback | state+code 또는 state+error만 받는다. state one-use, PKCE, session epoch, TTL, authority/path/loopback peer를 검증한 뒤 code 교환. candidate는 메모리에만 둔다. 저장 0회. 완료 후 query 없는 고정 result URL로 303. |
| GET /P/oauth/google/result | 정확한 authority/prefix/loopback peer와 빈 query일 때만 정적 data-free 페이지를 돌려준다. 이 경로만 cookie/session gate 전 좁게 통과시킨다. route 검증 뒤 session·flow·token·cookie를 읽거나 만들지 않는다. cookie 없는 요청도 성공한다. |
| POST /P/api/v1/google-oauth/{purpose}/commit | 동일 session의 CSRF, flow, generation, replace 및 candidate를 재검증한다. flow lock 아래 awaiting_commit→committing을 한 번 바꾸고 같은 요청에서 save_google_oauth(candidate)를 기다린다. 저장 진입 합계 1회. |

Callback은 live one-use state/session epoch와 code exchange를 요구하지만 result는 route 검사 외 session/flow 권한을 쓰지 않는다. 다른 route의 cookie gate는 유지한다. Fetch Metadata가 전부 없으면 state/epoch를 확인한다. 있으면 callback은 cross-site/navigate/document/?1, result는 cross-site 또는 same-origin/navigate/document/?1만 받는다. partial/duplicate, iframe/subresource, route/method/authority/peer/query 오류, replay/session 교체는 거부한다.

flow는 pending→exchanging→awaiting_commit→committing→complete 또는 cancelled/expired/denied/account_mismatch/failed다. state와 verifier는 독립 32-byte CSPRNG/base64url 43자, challenge는 S256. 최초 세 상태만 monotonic 300초 TTL이다. committing은 취소·TTL 재개 대상이 아니다. process restart는 미완료 flow를 버리고 시작된 저장만 기존 journal recovery에 맡긴다. replay ledger·approval object·tombstone은 추가하지 않는다. 응답/완료 page는 no-store/no-referrer다.

query ≤8192 bytes, state 43 ASCII, code 1–4096 bytes, error ≤64 ASCII. HTTPS endpoint/query allowlist는 code-owned. HTTP connect/read 10초, redirect/retry 0, response ≤1 MiB. subprocess/call-count/end-to-end/DNS deadline은 보장하지 않는다. OAuth 오류는 provider/message/fields 없이 고정 code만 반환한다: OAUTH_APP_NOT_READY, FLOW_BUSY, FLOW_NOT_FOUND, FLOW_EXPIRED, FLOW_CANCELLED, FLOW_STATE_REJECTED, OAUTH_ACCESS_DENIED, OAUTH_GRANT_MISMATCH, ACCOUNT_MISMATCH, RECONNECT_REQUIRED, CSRF_REJECTED, SESSION_EXPIRED, SAVE_FAILED.

## 3. 저장 transaction과 credential type

authorized_user 전용 parser는 resolver가 만든 payload.info의 정확한 여섯 logical key {type, client_id, client_secret, refresh_token, token_uri, scopes}와 각 타입·값을 검증한다. type은 authorized_user, client는 config와 일치, token_uri는 고정 Google endpoint다. scopes representation은 정확히 하나의 문자열을 담은 JSON array이며 목적 scope와 같아야 한다. string/null/복수 원소/중복 원소/누락·추가 key는 거부한다. resolver의 json.loads→dict 변환에서 raw duplicate member 정보는 이미 소실된다. 따라서 원시 입력의 중복 key를 runtime에서 거부한다고 주장하거나 해당 regression을 요구하지 않는다. persisted OAuth record는 save_google_oauth가 검증 candidate에서 canonical JSON으로 만드는 경로로 한정한다. raw ingress 검증과 parser 이후 logical-key 검증은 구별한다.

generic structural_credential()/CredentialService.save()/기존 upload는 service-account 전용으로 둔다. OAuth 전용 save_google_oauth(candidate), Connection Test, _continue와 recovery는 정확한 credential type으로 dispatch한다. OAuth 검증 실패를 service-account parser로 재시도하지 않는다. SA/SA는 기존 동작을 유지한다. 혼합 SA/OAuth peer는 CREDENTIAL_TYPE_CONFLICT로 journal·reservation·staging 전에 거부하며, 전환은 기존 role을 local forget한 뒤 새로 연결한다. OAuth/OAuth peer는 fresh account permission ID가 같아야 한다. raw ID는 저장하지 않는다.

commit transaction은 다음 순서를 따른다. 1) ephemeral pair admission과 두 role의 physical-binding lock을 획득한다. 2) target role lock을 얻고 config/client/generation, 양쪽 peer 상태와 현재 credential source를 snapshot한다. 3) pair/physical locks를 유지한 채 config lock 밖에서 fresh grant/client/account 검증을 수행한다. 4) provider HTTP가 끝난 뒤 config lock을 다시 얻어 config/client/generation 및 양쪽 peer/current source snapshot을 CAS 재검증한다. 5) 일치할 때만 named journal/reservation/staging/promote/config 순서로 진행한다. provider HTTP 동안 config lock은 잡지 않는다. 어느 검사든 불일치하면 journal·reservation·stage·active·config 변경은 0이다.

replace는 transaction 시점의 양방향 조건이다. replace=false는 관리 대상 active가 실제로 없고 유효한 environment/external_file 충돌 source도 없을 때만 set한다. replace=true는 관리 대상 active가 실제로 있고 environment/external_file override가 없을 때만 교체한다. Google source가 environment 또는 external_file이면 OAuth set/replace를 거부한다. 자동 detach, 환경 값 읽기/출력, 외부 파일 이동은 하지 않는다. 사용자가 환경 설정을 별도 정리하거나 기존 지원 detach 절차를 먼저 쓴다. 호출 시점의 cards만으로 replace를 결정하지 않는다. config가 유지되면 실패 후 기존 active bytes를 보존하고 recovery는 저장된 type으로 재검증한다.

fresh account 불일치는 고정 ACCOUNT_MISMATCH다. candidate는 callback에서 검증된 최소 필드만 유지하며 response/log/journal/config metadata에 client secret·token·permission ID/provider body를 넣지 않는다. forget은 기존 role transaction을 재사용하고 해당 local credential/config reference만 제거한다. Google remote revoke·다른 purpose credential 제거·client 삭제·ACL 변경은 없다.

## 4. Settings 조립과 생명주기

build_settings_services() 반환 계약은 기존 (CredentialService, CanvasService) 2-tuple 그대로 둔다. launcher는 service를 먼저 중복 구성하지 않는다. loopback socket 예약 후 실제 port, new_path_prefix(), SessionSecurity.issue()가 정해진 다음, 그 exact authority/prefix/session epoch와 공유 CredentialService를 쓰는 OAuth flow service 하나를 만든다. 같은 객체 identity를 create_settings_app과 browser/lifecycle callback에 전달한다.

공통 lifecycle hook은 close/replacement/idle 만료에서 먼저 새 flow/commit 진입을 원자 차단하고 pre-commit flow만 무효화한다. committing은 취소·상태 재작성하지 않는다. 그다음 mutation barrier를 닫고 이미 진입한 commit을 drain한 뒤 security.close()/replace()/idle 종료를 수행한다. 현재 app close는 security.close() 후 on_close이고, launcher replacement는 security.replace() 후 barrier.close이며 idle은 launcher가 보기 전에 lifecycle state를 만료시킬 수 있으므로 세 경로 모두 순서를 통합한다. barrier close와 flow 상태 전환 사이의 경쟁도 원자 계약으로 다룬다.

## 5. Runtime과 WORKER effect gate

P2는 ResolvedCredentials 단일 snapshot/payload를 쓴다. service_account는 현재 google_worker_service()와 provider binding 경로를 그대로 유지한다. authorized_user만 역할 scope/config client/fresh grant를 검증하고 identity-only about.get(fields=user(permissionId))로 현재 계정을 확인한다. 저장 scopes/local credential metadata만으로 grant를 증명하지 않는다. WORKER binding은 provider_binding_id("google_drive", fresh permission ID, configured WORKER client ID)의 digest이며 원 ID를 기록하지 않는다.

구체 후보 interface는 하나의 WorkerEntryAttestor다. runtime이 authorized_user payload와 authenticated service를 조립할 때 하나만 만들고 동일 객체를 GoogleDriveWorkerAdapter와 IntakeWorker에 전달한다. attest_entry()는 매 entry에서 fresh grant/client/permission identity를 검증해 immutable WorkerEntryAttestation을 반환한다. 그 snapshot은 role, generation, exact scope 및 binding digest만 표현하며 token·secret·raw permission ID를 담지 않는다. IntakeWorker는 operation-scoped context로 같은 attestation 객체를 adapter에 전달한다. Adapter는 그 context가 없거나 만료/불일치면 provider file/resource call을 거부한다. SA 경로에는 이 gate를 추가하지 않는다. 별도 replay ledger는 만들지 않는다.

검증 실패, grant/client/account drift, missing proof, invalid_grant이면 fixed RECONNECT_REQUIRED로 fail-closed한다. 매 direct public invocation은 첫 effect보다 먼저 gate한다.

| entrypoint | 검증 위치와 이후 효과 경계 |
|---|---|
| run_once | local worker lock, _sync_unlocked, discovery/state registration, request claim/process, extension snapshot/receive/publish와 after_publish 전에 1회. 한 immutable attestation을 전체 tick에 유지한다. |
| sync | lock/_sync_unlocked, Drive layout/discovery 및 register_semester_registration 전. |
| claim_request | receipt/generation/plan mutation 전, 특히 첫 Notion page read와 claim status write 전. |
| process_item | request read, plan/job/reservation, Drive read/write, Notion write 전. |
| create_input_request | local item/workspace 조회 뒤, _fresh_item_layout_context의 Drive read와 pending-key/receipt/generation/Notion effect 전에 gate한다. run_once/sync 내부 helper는 outer attestation을 전달한다. |

composition에서 runtime.build_intake_worker()는 현재 GoogleDriveWorkerAdapter(service), IntakeWorker(binding)을 만든 뒤 두 installer를 호출한다. C5 install_usage_range는 bridge.validate_workspace() Notion read 후 coordinator를 등록한다. C6 install_study_notes는 StudyNoteStore/local source를 같은 coordinator에 등록한다. run_once는 각 coordinator의 snapshot→receive→publish를 실행하고 C5 after_publish도 Notion approval을 읽고 쓴다. 따라서 두 설치 전 startup attestation, 각 run_once 전 fresh attestation을 두고 같은 context를 모든 phase에 유지한다. 직접 entrypoint는 매번 새 attestation을 받는다. 현재 두 constructor에는 attestor가 없다.

R3 packet scope 구분: 별도 Worker review packet의 worker.py, adapters/drive/worker.py, intake/identity.py 전문과 이번 보완에서 intake/composition.py, intake/study_note_composition.py의 설치 body를 함께 대조한다. 이전 Runtime packet에서 composition 두 파일이 빠졌던 것은 검토 범위 증거 공백이지 그 자체가 제품 계약 결함은 아니다. 검토는 별도 Worker review에서 완료돼야 하며 현재 통과로 간주하지 않는다.

## 6. 구현 파일 후보와 범위

| 경로 | 계획 책임 |
|---|---|
| src/uls/config/google_oauth.py (신규), schema.py, loader.py, validation.py | parser/config all-or-none; loader/validation은 별도 Runtime review 범위도 유지한다. |
| src/uls/settings/google_oauth.py (신규), app.py, security.py, status.py | flow/routes, result no-cookie gate, callback exception, fixed errors, overview boolean. |
| src/uls/settings/composition.py, launcher.py | 하나의 flow service를 exact authority/prefix/epoch에 결속하고 lifecycle 순서를 통합한다. |
| src/uls/settings/credential_service.py | save_google_oauth, typed continuation/recovery/Connection Test, pair snapshot/CAS 및 source-aware replace. 이 경로는 승인된 17핀 중 직접 교집합으로 후보가 되며 별도 candidate review와 인간의 정확한 pin 결정 전에는 pin을 갱신하지 않는다. |
| src/uls/runtime.py, src/uls/intake/worker.py, src/uls/adapters/drive/worker.py | authorized_user 전용 attestor, 동일 객체 주입/전달, 다섯 entry gate; SA positive unchanged. |
| src/uls/intake/composition.py, src/uls/intake/study_note_composition.py | startup gate 뒤 C5/C6 설치와 run_once coordinator phases의 동일 attestation 보장. |
| 신규 테스트 | tests/unit/test_google_oauth_config.py, tests/unit/test_google_oauth_runtime.py, tests/contract/test_settings_google_oauth.py, tests/contract/test_settings_google_oauth_http.py, tests/contract/test_settings_google_oauth_save.py, tests/contract/test_settings_google_oauth_composition.py, tests/contract/test_intake_worker_google_oauth.py. 모든 provider/browser/HTTP와 credential은 fake/synthetic, 저장소는 임시 격리. |

Review 범위 구분: Settings R7의 loader.py/validation.py는 별도 Runtime packet에 포함되어 있으며 확인된 계약 결함이 아니라 검토 coverage 경계다. src/uls/config/credentials.py는 현재 resolver가 raw JSON을 dict로 변환해 duplicate member 정보가 사라지는 경계이며 이 계획에서 수정하지 않는다. provider_checks.py, credential_roles.py, credential_stores.py, credential_admission.py, journal.py와 17-pin map/checker/global 설정도 변경 후보가 아니다. P3 UI 파일과 UI harness, config.example.yaml, package에는 OAuth 값이나 사용자 UI를 추가하지 않는다. OAuth 후보 테스트는 새 파일에 둔다.

## 7. 수락 시나리오와 남은 게이트

- Parser: 여섯 logical key/type, scopes 단일 원소 JSON array, canonical persisted candidate; raw duplicate-member 요구 없음. SA parser/SA-only positive 유지.
- HTTP: no-cookie exact result는 session/flow/cookie 접근 없이 정적 응답. callback PKCE/one-use epoch, 양 경로 저장 0; 잘못된 route/query/header 거부.
- Transaction/type: pair lock fresh check, HTTP 중 config lock 금지, post-HTTP CAS, mismatch 전 transaction effect 0; replace 양방향 및 environment/external_file 거부. SA-SA 유지, mixed pair는 pre-journal conflict, OAuth peer mismatch 0, save/continue/recovery/Test type dispatch.
- Lifecycle: close/replacement/idle 경합, precommit invalidation, committing 보존/drain 검증.
- WORKER: 다섯 entrypoint마다 grant/client/permission drift·missing proof·invalid_grant 시 Drive/Notion/receipt/generation/plan/job 효과 0. adapter/worker/C5/C6에 동일 snapshot 전달, setup C5 gate, SA-only + empty google_oauth positive 유지.
- No-secret 경계 확인. 실제 Google/School login은 자동 시험에 넣지 않는다.

차기 검토 상태: Native Settings R1–R7/O1, Native Runtime R1–R2를 반영한 계획 수정안이다. Runtime R3는 composition 실제 body까지 보완 대조했지만 별도 Worker review가 미송신이라 수락 대기다. Runtime consistency는 tests/contract/test_doctor_credential_resolver.py의 정확한 경로 예외에 관한 인간 결정 전 HOLD다. checker/pin/alias/기록 생략으로 우회하지 않는다. 새 flow 변경은 기존 9파일 Gemini GO만으로 승인되지 않으며 새 Gemini review도 필요하다. Native FINAL 송신과 review disposition은 총괄이 소유한다. 어떤 review나 checker도 구현 GO 또는 pin 승인으로 대신하지 않는다.

향후 구현에서 docs/setup/google-drive-oauth.md를 작성해 사용자 소유 Google project, Drive API, Desktop client, consent와 local config 절차 및 Testing 상태 refresh-token 만료 가능성을 설명한다. Google 검증·안전성을 보증하지 않는다. picker/drive.file, 공용 client, remote revoke, ACL/owner_only 정책 변경, Notion/Canvas auth, GUI, worker toggle, 실제 School/provider 작업은 범위 밖이다.
