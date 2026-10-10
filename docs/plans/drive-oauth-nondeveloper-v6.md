# Drive 사용자 OAuth — 통합 구현 계획 v6

상태: Native B v5 원본 응답은 REVISE 필수3·선택2다. 이 문서는 동결 v5 계약을 유지하며 B1/B3/B5/B6 및 B2 external-source 회귀 matrix를 구체화한 통합 PLAN-only다. 원본 응답은 original-bound COMPLETE이나 canonical harvest가 native_guard BlockingIOError로 제출 전 막혀 formal review acceptance는 미완료다. 제품/test/핀 변경 또는 구현 GO가 아니다.

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

## R2 — A-owned typed authorization과 grant/candidate 결속

A 파일 src/uls/settings/google_oauth.py가 GoogleOAuthCommitAuthorization type과 private factory를 소유한다. A flow/session owner만 발급할 수 있다. B가 소유한 service API는 CredentialService.save_google_oauth(role, candidate_bytes, authorization, ...)이며 HTTP/CLI generic API로 authorization을 만들 수 없다. Handoff는 exact candidate raw bytes와 object를 한 호출로 전달한다.

Immutable object의 필수 fields: role/purpose, flow ID/epoch, session epoch, captured config generation, configured client ID, callback의 canonical actual-granted-scope tuple, callback의 fresh Drive permission ID, exact candidate_bytes SHA-256, expires_at_monotonic, user_replace_authorized. Token/code/refresh token/credential bytes 자체는 object나 journal에 넣지 않는다.

A는 session epoch와 flow ID/epoch의 currentness, awaiting_commit, TTL 및 supersession 여부를 확인하고 atomic하게 committing으로 전이한 뒤 authorization을 발급한다. Expired/cancelled/replaced/superseded flow는 B 호출 0회다. Committing flow는 다른 flow로 대체할 수 없고 같은 목적의 신규 begin은 busy다. Flow/session epoch 및 supersession의 authoritative owner는 A이며 B가 A registry를 재조회한다고 주장하지 않는다.

B는 method entry에서 typed object, exact role/client/config binding, candidate digest, canonical scope, expiry와 one-shot handoff를 확인한다. Candidate swap/digest mismatch, 만료, 재사용, role/client/generation mismatch면 provider call/admission marker/journal/stage/effect가 모두 0이다. Lock 대기 뒤에도 initial config lock 안에서 publish/create 전 expiry와 live config binding을 재검사한다. A가 committing으로 봉인한 handoff 후에는 새 supersession이 불가능하며 정상 transaction deadline을 따른다.

Save-time provider verification은 candidate를 다시 refresh/about한다. 실제 server scope tuple과 callback-attested tuple이 exact-equal이고 fresh permission ID도 callback-attested ID와 exact-equal이어야 promotion/CAS 가능하다. Actual proof mismatch는 현재 stage→verify ordering에 따라 journal operation 안에서 reject cleanup한다. 이 경우 “zero effect”는 최종 active/config/external mutation이 없고 stage가 삭제되며 journal이 terminal/released인 것을 뜻한다. Temporary stage/journal/provider-read는 숨기지 않는다. Strict pre-effect no-op assertion은 authorization/digest/expiry/supersession preflight rejection에 적용한다.

Tests: exact A candidate positive; 동일 client/scope/account이나 refresh token이 다른 swapped candidate negative; digest mismatch zero calls/zero durable effects; expired/reused/wrong epoch/session/superseded capability는 handoff/provider/admission/journal/stage 0; callback/save-time scope mismatch와 permission ID mismatch는 promotion 0 및 existing cleanup 이후 original exact bytes; restart recovery는 flow object 없이 journal binding, candidate hash, staged-state ID 및 exact staged bytes로 새 context에서 재검증한다.

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

v5는 v2/v3/v4 규약을 standalone로 통합한다. 원본 문서는 수정하지 않는다.

| 주제 | v5 적용 규칙 |
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

다음 B review packet은 v6 전문, credential_service.py, provider_checks.py, test_settings_credential_service.py, test_settings_provider_checks.py 전문, docs/plans/provider-connection-nondeveloper-scope-20261005.md 전문(SHA-256 11a9d2b7705785cce8dc3305e0014b08cb4c5c30044e20a6e96e6801870e66f0, 7,441 bytes) 및 current inventory 근거를 포함한다. 특히 direct B-owned test_settings_provider_checks.py를 hash/excerpt로 대체하지 않는다. Complete pack은 fixed 120,000-token limit 안에 실제로 audit한다. 한도 때문에 direct-owned full source를 떼거나 source alias를 쓰지 않는다. v5는 독립적으로 읽을 수 있으며 reviewer가 v2/v3/v4에 의존하지 않는다.

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
