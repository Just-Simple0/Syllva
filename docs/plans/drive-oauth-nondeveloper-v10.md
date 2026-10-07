# Drive OAuth 비개발자 연결 — 통합 계획 v10

상태: PLAN-only. v9 전체를 아래 A-1 보완과 함께 포함한 독립 계획이다. v9 원본은 동결·불변으로 보존한다. 이번 v10에는 Native A-protocol-runtime-1/-2, B-protected-transactions packet 1/2, C-browser-UI packet 1/2, D-Drive-API-frozen-privacy packet 1/2, D3-frozen-authorities 및 D2-intake-privacy-bounded packet 1/2의 전달된 지적을 누적한다. B packet 2의 선택 지적은 현재 테스트와 예정된 OAuth 회귀를 구별하도록 승계 문구를 정정하고, C/D/D2의 채택 지적을 아래에 반영한다. 본 문서는 현재 전달된 review set의 documentary freeze이며, required integrated Native/Gemini plan acceptance 전에는 제품 implementation GO가 아니다. 제품/테스트/핀/ACL/전역/.env 편집과 테스트 실행은 이 단계에서 금지한다. 아래 v10 규범은 충돌하는 v9 승계 문장만 대체하고, 그 밖의 A/B/C/D/D2/D3 계약은 그대로 유지한다.

## v10 최우선 보완 — Native v9 D2-intake-privacy-bounded packets 1/2

아래 규범은 v9 D2의 identity/layout 조건을 유지하면서, intake 효과 진입점 전체의 identity gate, 처리 호출별 privacy preflight, IntakeWorker의 기존 marker 재사용 경로를 보완하고 production composition 회귀를 구체화한다. 충돌 시 이 절이 기존 v9 D2 본문, D source-scope 행 및 별도 소유라고 적은 설명보다 우선한다. 기존 strict raw `driveId` 파서는 같은 `src/uls/adapters/drive/worker.py` 경계를 사용한다. 다른 D 계약인 `DerivedDriveWriter` 검증과 response-loss no-retry는 그대로 유지한다.

| 근거 | SHA-256 | bytes | 판정 |
|---|---|---:|---|
| Native v9 D2-intake-privacy-bounded-1 canonical harvest | `108fe1ea94f764009df457da7c8aaf5fb820a4ddef2a6963d59f0f886e592203` | 15,549 | REVISE REQUIRED 3 / OPTIONAL 0; 총괄이 세 지적을 모두 채택 |
| Native v9 D2-intake-privacy-bounded-2 original-bound harvest | `fca055925ad945d1d12f50cd125348f7297b32c216d24d2d79f775c87a2ce1de` | 14,489 | GO REQUIRED 0 / OPTIONAL 1; production-composition regression detail adopted; parent reports formal checker re-harvest pending |

### D2-I1 — 모든 공개 효과 진입점의 identity gate와 lock 소유권

`IntakeWorker`의 모든 공개 provider/state 효과 진입점은 첫 effect보다 먼저 동일한 immutable WORKER identity snapshot을 검증해야 한다. 현재 소스에서 확인한 집합은 `sync()`, `run_once()`, `create_input_request()`, `claim_request()`, `process_item()`이다. 후보 변경은 이 전체 집합을 닫고, 추가 공개 effect 진입점이 있는지 회귀 인벤토리로 확인한다. OAuth 설정에서 정확한 fresh WORKER attestation을 제공할 수 없거나 stale latch가 이미 설정된 경우, 해당 진입점은 Drive/Notion 호출, receipt/generation claim·적용, binding 재계산 또는 기타 provider effect 전에 명시적으로 거절한다. Legacy service-account 경로의 기존 동작은 유지한다.

각 공개 진입점은 single-active-worker lock을 한 번 획득하고 그 잠금 아래에서 stale latch와 captured snapshot을 확인한다. 이미 잠금을 쥔 `_sync_unlocked()`/`_claim_request_unlocked()`/`_process_item_unlocked()` 등 내부 경로는 공통 unlocked helper를 사용하며 재진입 공개 wrapper나 중첩 잠금을 호출하지 않는다. 특히 현재 공개 `create_input_request()`를 잠금 소유 wrapper와 `_create_input_request_unlocked()`로 나눠 `sync()` 및 `_claim_request_unlocked()`의 잠금 보유 호출은 unlocked 경로로 통일한다. Identity drift를 처음 관측한 순간 latch를 세우고, 이후 요청은 기존 binding을 새 identity로 재계산하거나 재결속하지 않는다. `run_once()`의 stale identity 실패도 local-only여야 하며, 상위 job-error 처리에서 Notion에 투영하지 않는다.

**회귀:** 실제 public entrypoint 각각을 stale latch 및 fresh-refresh identity drift에서 직접 호출한다. `Drive create/move`, `Notion create/update`, receipt claim/application, request generation 변경 및 binding recomputation이 모두 0이어야 한다. 공개 wrapper와 내부 unlocked helper의 lock recorder는 lock 획득 1회 및 중첩 획득 0회를 보인다. Same-identity OAuth positive와 기존 legacy SA positive를 유지한다. 테스트는 진입점 호출 전 provider·receipt·generation 카운터를 기준선으로 잡고, 낡은 binding으로 이미 준비된 request를 재사용하지 않는 것도 확인한다.

Production composition positive는 `tests/integration/test_intake_worker_preview.py`에서 port-injection shortcut을 사용하지 않는다. Helper와 SDK constructor만 fake로 바꾸고 `build_intake_worker()`의 실제 `google_worker_service()`/adapter construction branch를 통과시킨다. 첫 helper 호출은 정확히 1회여야 하고, adapter와 `IntakeWorker`의 snapshot은 같은 object (`is`)이며 binding은 바로 그 snapshot에서 계산된 값과 같아야 한다. 같은 파일에 legacy SA WORKER composition positive도 추가하되, compiled-pinned `tests/unit/test_runtime_google_credentials.py`는 수정하지 않는다.

### D2-I2 — processing invocation 전용 fresh layout/error-projection barrier

각 processing invocation은 `IntakeWorker._process_item_unlocked()`에서 현재 semester workspace를 `_workspace_for_item(item)`으로 resolve한 직후, Notion request page read, receipt 검증, 취소/USER 변경 판정 및 processing error projection보다 먼저 해당 semester의 전체 registered Drive target chain을 새 metadata readback으로 검증한다. 기존 `validate_registered_drive_layout()`을 사용하며, 앞선 `sync()`나 이전 tick의 성공 결과를 재사용하지 않는다. 해당 invocation 안에서 이 검증이 성공한 뒤에만 local `layout_preflight_passed` barrier를 세운다.

Workspace를 resolve할 수 없거나 현재 layout readback이 실패·불완전·drift 상태이면 실패는 local-only다. 이 barrier가 통과하지 않은 상태에서는 어떤 processing error도 `_record_item_error()`, `_record_request_error()`, `_record_job_error()`의 Notion projection, `_project_file_intake*()` 또는 다른 Notion create/update로 투영하지 않는다. `run_once()`의 상위 failure handler도 동일 invocation barrier를 확인한다. Gate 실패는 provider Drive mutation 역시 0이어야 한다. Fresh layout이 통과한 뒤 발생한 일반 request/receipt 오류는 기존 D2 오류 처리 규칙을 유지한다.

**회귀:** fake-only integration에서 claim 이후 layout을 drift시키고 normal receipt, cancelled receipt, claim 후 USER 변경, request-page readback failure를 교차한다. 각각 processing 진입 전 layout gate가 실패하면 gate 호출 이후 측정한 Drive mutation 및 Notion create/update는 0이다. Request-readback failure fixture는 layout 검증보다 먼저 도달하지 못하는지 확인하고, workspace resolve/readback 자체가 실패하는 경우도 local-only로 끝나는지 확인한다. Control에서는 layout이 정상일 때 기존 오류 투영을 보존한다. Claim 이전의 허용된 Session Pending 생성까지 0이라고 주장하지 않는다.

### D2-I3 — `_stage_derivative()` marker reuse의 동일 privacy postcondition

`src/uls/intake/worker.py::_stage_derivative()`에서 `search_marker()`가 돌려준 기존 ID는 locator로만 취급한다. 기존 match를 반환하기 전에 같은 file ID로 adapter의 fresh `read_metadata()`를 호출해 그 live readback에서 요청 ID 일치, 정확한 parent tuple, `text/markdown` MIME, exact marker (`uls_entity`/`uls_source` 포함), `trashed is False`, `owned_by_me is True`, 개인 Drive (`drive_id is None`), 완전하고 owner-only인 permission readback, broad-share가 아님을 검증한다. `_check_private_drive_metadata()`의 기존 privacy 판정을 재사용하며 `can_edit is True` 등 이 경로가 필요로 하는 capability를 확인한다. `can_move`는 해당 파일에 뒤이은 허용 작업이 실제 move를 요구하는 경우에만 추가로 필수다. 그리고 동일 ID에서 내려받은 bytes가 이번의 정확한 derivative bytes와 일치해야 한다. Malformed raw `driveId`는 `DriveMetadata` 생성 전에 같은 `GoogleDriveWorkerAdapter._metadata()` 경계에서 거절된다.

새로 create한 derivative와 기존 marker match에는 공통 postcondition을 적용한다. Create 응답만으로 성공 처리하지 않으며, 같은 file ID의 fresh metadata·bytes가 위 조건을 모두 통과한 뒤에만 durable `READBACK_OK` 및 후속 pointer/Ready 경로로 진행한다. 기존 match가 조건을 통과하지 못하면 reconciliation/fail-closed로 중단하며, 다른 file 생성·자동 delete·ACL 수정·원본 이동·provenance 재결속을 하지 않는다. 실패가 발생하기 전에 이미 허용된 Session Pending 생성은 취소되었다고 주장하지 않는다.

**회귀:** `tests/integration/test_intake_worker_preview.py`의 fake provider에서 static registered layout과 원본은 정상으로 두고, exact marker가 일치하는 기존 derivative의 fresh readback만 `owner_only=False`, anyone/domain broad-share, permission readback 누락/불완전, 필요한 capability 누락/false, MIME 불일치로 각각 바꾼다. Search 결과 객체가 아니라 동일 ID의 fresh adapter readback을 검증했음을 확인한다. 각 실패에서 해당 derivative의 pointer publication과 Ready 승격은 0이며, 추가 create/recreate, ACL update, source move, 자동 delete, provenance rebind는 0이다. Session Pending은 측정 assertion에서 제외한다. 정상 existing derivative와 신규 create 경로의 positive는 동일 postcondition을 통과한 뒤에만 pointer/Ready를 허용한다.

### D2-I source/test closure 및 checker 경계

변경 후보는 기존 v9 D2 scope의 `src/uls/intake/worker.py`, `tests/integration/test_intake_worker_preview.py`, `tests/unit/test_intake_registry.py`와 같은 adapter boundary의 `src/uls/adapters/drive/worker.py`, `tests/unit/test_c2_drive_marker_recovery.py`다. D-owned `src/uls/worker.py`/`tests/integration/test_native_runtime.py` DerivedDriveWriter 검증은 별도 경로로 그대로 유지된다. Packet 2는 새 파일을 만들지 않고 동일 preview test에 production `google_worker_service()`/adapter construction 분기와 legacy SA WORKER positive를 추가한다. `tests/unit/test_runtime_google_credentials.py`는 compiled-pinned legacy SA positive로 읽기/실행 기준선이며 편집하지 않는다. D2-I는 active checker, inventory, pins, 그 밖의 pinned source를 수정할 권한을 추가하지 않는다. 기존 checker/pin 결정은 별도 exact-candidate·review·human gate로 유지한다.

**D2-I 필수 수락:** public identity gate 5개 진입점의 fake regression, per-invocation layout/error-projection barrier 교차행렬, marker reuse/new create 공통 postcondition, registry privacy/capability matrix, raw mapping `driveId` parser negative/positive 및 pinned SA positive를 포함한다. D2 focused command는 `./.venv/bin/pytest -q tests/unit/test_intake_registry.py tests/integration/test_intake_worker_preview.py tests/unit/test_runtime_google_credentials.py tests/unit/test_c2_drive_marker_recovery.py tests/integration/test_native_runtime.py`이다. Native D release suite 및 A/B/C 통합검사와 함께 전체 D2 result는 동일 final candidate source manifest에 결속한다. 이번 단계에서는 이 명령이나 다른 테스트를 실행하지 않는다.

## v10 누적 정정 — Native v9 B-protected-transactions packet 2

Native packet 1은 GO / REQUIRED 0 / OPTIONAL 0, packet 2는 GO / REQUIRED 0 / OPTIONAL 1이다. 선택 지적은 기존 transaction/recovery/peer-separation 테스트 기준선과 아직 추가해야 하는 OAuth handoff-ledger·service-level replay 회귀를 분명히 구별하라는 문서 상태 정정이다. 이 정정은 테스트가 이미 존재한다고 주장하지 않으며 신규 회귀 계획을 약화하지 않는다.

| 근거 | SHA-256 | bytes | 판정 |
|---|---|---:|---|
| Native B-protected-transactions packet 1 canonical harvest | `73714b27b3647a929b716a79a82339303eb7bd328a3f4567d7f11fd735ecadaf` | 14,713 | GO REQUIRED 0 / OPTIONAL 0 |
| Native B-protected-transactions packet 2 canonical harvest | `9b72c0691d280d37e8b958f6799520bc4a86623eacbf2e1aea40bd34637165bf` | 14,222 | GO REQUIRED 0 / OPTIONAL 1; 총괄이 선택 지적 채택 |

승계된 B composition 절의 기존 표현 중 replay 회귀가 이미 존재한다고 오해할 수 있는 부분은 아래 의미로 정정한다: “Existing transaction/recovery/peer-separation baseline; OAuth handoff ledger and service-level replay regressions are planned additions. Composition identity and cross-entrypoint replay regressions are added separately in the composition suite.” B service 회귀와 composition 회귀는 각각 계획된 추가이며, 현재 존재하는 기준선 테스트로 간주하지 않는다.

## v10 우선 적용: Native v9 A-protocol-runtime-1/-2 보완

A1의 필수 6건·선택 2건과 A2의 필수 2건을 아래에서 닫는다. 이 절과 충돌하는 v9 A 문구만 대체한다. 실제 현재 SHA/크기의 전체 read-set은 `.insane-review/drive-oauth-20261005/drive-oauth-v10-A1-source-inventory.json`에 기록한다.

| 근거 | SHA-256 | bytes | 판정 |
|---|---|---:|---|
| Native A-protocol-runtime-1 canonical harvest | `0cfa05b8043aefef0c57339efd0b88e3b66c0beb854325c4ba515df696bb544e` | 14,103 | REVISE REQUIRED 6 / OPTIONAL 2; 총괄이 모두 채택 |
| Native A-protocol-runtime-2 canonical harvest | `1c6b7d61b9b86ac8517a03e04d087030c1ce2e7945eb3ff324c9089f48f5c977` | 14,740 | REVISE REQUIRED 2 / OPTIONAL 0; 총괄이 모두 채택 |
| 동결 v9 plan | `74185e3f52c985effb0b91005781245f7732ba0262aeca63d252b2fa61db95c2` | 127,709 | 완전 승계 원본; 수정하지 않음 |

### A1-R1 — authorized_user 런타임 갱신의 단일 제한 경계

`src/uls/runtime.py`의 현재 Google 런타임은 service-account와 OAuth user 경로를 명시적으로 분리한다. 기존 `type=service_account`의 구성·refresh·positive 동작은 byte-level 회귀 기준으로 유지한다. `authorized_user`는 OAuth 내부 caller가 purpose를 이미 바인딩한 경우에만 새 `src/uls/settings/google_oauth.py`의 strict parser와 bounded runtime refresh adapter를 통과한다. Generic `structural_credential()`/CLI/import 경로에서 종류를 고르거나 OAuth parser로 승격할 수 없다.

- google-auth/Drive SDK가 자체 HTTP refresh를 실행하지 않게 한다. SDK refresh callback이 필요하면 OAuth runtime의 한 bounded helper에만 위임한다. access token을 SDK에 넘기기 전에 token endpoint 응답의 실제 granted `scope`, 내부 role↔purpose 고정표 및 현재 Desktop bundle의 client ID 일치, purpose 일치, 유효한 `expires_in`, 같은 token을 사용한 fresh `about.user.permissionId` readback을 전부 확인한다. `scopes=[scope]`, payload/SDK가 광고한 scopes, 설정 텍스트는 server grant 증거가 아니다. 응답 scope가 없거나 불명확하거나 누락·초과면 거부한다.
- refresh helper 한 번의 최대 provider wire는 2회다: token refresh POST 1회 + 검증된 token의 Drive identity GET 1회. 전체 monotonic deadline 30초, 각 connect/read/socket timeout 10초 이하, redirect 0, retry 0, 요청·응답/pipe 출력 합계 1 MiB 이하. Cap은 streaming 중 적용하고 초과 시 child를 즉시 terminate/reap한다. helper 재호출로 시간·호출·byte budget을 초기화하지 않는다. 이는 callback, Connection Test, B save/recovery의 v9 별도 budget을 바꾸지 않는다.
- token, refresh token, authorization code, client secret은 child stdout/stderr/log/argv/env, journal, HTTP response/error에 쓰지 않는다. 검증된 access token은 provider SDK 통신용 현재 프로세스의 in-memory credential adapter에만 전달 가능하며 UI/browser/disk/journal/log에는 전달하지 않는다. MCP↔WORKER fallback은 금지한다. 확인 전에는 SDK에 token을 주지 않고 Drive file API 요청도 0회다.

**회귀:** 기존 SA positive 유지; synthetic authorized_user refresh의 최대 2 wire; local `scopes=`만 맞고 실제 grant 누락/불일치 시 거부; client/purpose/expiry/permission ID 누락·불일치 거부; redirect/retry/second refresh/DNS hang/socket·TLS timeout/trickle/cap+1에서 종료·reap; attestation 전 file API 0회; secret sentinel이 argv/env/stdout/stderr/log/API/journal에 없음. pinned `tests/unit/test_runtime_google_credentials.py`는 실행만 하고 수정하지 않는다.

### A1-R2 — deployment readiness의 단일 저장 경계·세대·재검증

v9의 operator-managed deployment record는 논리상 별도 record로 유지하되 별도 sidecar·별도 cache는 두지 않는다. 유일한 durable owner는 기존 설정 YAML을 읽는 `ConfigStore`; A의 `OAuthDeploymentAuthority`(`google_oauth.py`)가 동일 `LoadedConfig` snapshot의 raw `google_oauth` mapping을 strict-parse/readiness 투영하는 단일 typed reader다. `google_oauth` root의 허용 키는 `mcp`, `worker`, 선택적 `deployment`뿐이고, deployment mapping의 허용 키는 `mcp`, `worker`뿐이다. absent deployment 또는 목적별 record 결손은 해당 purpose `not_ready`; malformed record도 v9대로 fail-closed `not_ready`이며 OAuth bundle의 partial/malformed fatal 규칙과 혼합하지 않는다. Record는 같은 YAML의 `google_oauth.deployment.mcp` 및 `.worker`; OAuth client bundle은 `google_oauth.mcp` 및 `.worker`다. 목적별 bundle의 기존 exact key/schema는 v9 그대로고 각 deployment record의 exact field set와 enum/type 규칙도 유지한다. 앱/API/UI 및 generic settings patch는 record를 만들거나 바꿀 수 없다. status/begin/callback/commit은 각각의 용도에서 ConfigStore snapshot을 읽고 provider network로 freshness를 확인하지 않는다.

Begin은 하나의 `ConfigStore.load()`에서 purpose bundle+record를 검증하고, 내부 flow에 raw-config `LoadedConfig.generation`과 record+bundle의 non-secret canonical fingerprint를 캡처한다. Fingerprint는 외부에 내보내지 않는다. record는 exact scope/project/client, consent/publishing posture, assessment/eligibility 조건을 모두 만족해야 한다. `observed_at`은 timezone-aware RFC3339 instant로 파싱해 UTC로 정규화한다. Production 기준 시각은 predicate 평가 때 한 번 읽는 `datetime.now(timezone.utc)`이며, tests는 timezone-aware UTC instant를 주입한다. Naive·해석 불가 timestamp/clock 및 미래 `observed_at`은 stale이다. 상수 `DEPLOYMENT_EVIDENCE_MAX_AGE = timedelta(seconds=86_400)`; exact freshness 판정식은 `age = now_utc - observed_at_utc; timedelta(0) <= age < DEPLOYMENT_EVIDENCE_MAX_AGE`다. 정확히 만료 경계에 도달하면 stale/not-ready, 경계 직전은 fresh다. 이 wall-clock policy는 OAuth flow/authorization의 process-local monotonic 300초 TTL과 별개다. 24시간은 로컬 operator-attestation freshness 기준이며 Google 정책/심사 유효기간을 주장하지 않는다.

Stale/live predicate는 Overview와 begin이 동일한 A-owned pure function으로 평가하며 모두 충족해야 통과한다: (1) 하나의 ConfigStore snapshot과 strict parse 성공, (2) begin에서 캡처한 기존 ConfigStore raw-byte SHA-256 generation과 현재 generation이 동일, (3) 현재 snapshot의 purpose bundle+deployment record fingerprint가 캡처값과 동일, (4) tuple·posture·assessment·eligibility 유효, (5) `timedelta(0) <= age < DEPLOYMENT_EVIDENCE_MAX_AGE` freshness 식 통과. 하나라도 absent/malformed/false/mismatch/stale이면 not-ready다. Overview는 `not_ready`, begin은 `OAUTH_APP_NOT_READY`로 닫고 시간/record 해석 실패도 동일하게 fail-closed한다. Browser/request는 `now`, max-age, record, predicate를 선택·연장·갱신할 수 없다. Config generation은 `ConfigStore`가 이미 계산하는 값이고 파생 fingerprint는 메모리 내 비교만 한다.

검사 지점은 begin flow 생성 직전, callback에서 code exchange/provider wire 직전, B commit authorization 발급 직전이다. Callback에서 stale이면 기존 flow를 `failed/FLOW_FAILED`로 끝내고 provider wire 0회다. Commit 직전 stale이면 B authorization 발급·B save·active/config/external effect가 0이고 flow는 failed terminal이다. 새 begin은 `OAUTH_APP_NOT_READY`다. Begin 이후 설정의 무관한 key라도 generation이 바뀌면 기존 flow는 다시 쓰지 않는다. A authorization 발급 직후의 race를 닫기 위해 B는 기존 admission→role→operation→config lock 순서를 지키며 config lock 아래 같은 pure predicate와 captured generation/fingerprint를 다시 확인하고 success인 경우에만 ledger consume/transaction을 진행한다. 이 재검사는 network를 하지 않는다.

이 마지막 B 재검증은 v9의 `src/uls/settings/credential_service.py` 경계를 사용한다. 해당 파일은 현재 compiled pin이므로 미래 구현에서 bytes를 바꾸려면 exact candidate 독립 review와 인간의 정확한 pin 적용 결정이 별도로 필요하다. 이 v10은 pin/checker 갱신을 허가하지 않는다.

**회귀:** 양 purpose 각각 normal age, max-age 직전(`age < 86,400s`), exact boundary(`age == 86,400s`), boundary 이후, 미래, parse/clock 해석 실패를 같은 fake record/time으로 Overview와 begin에 넣어 판정이 일치하는지 검사한다. 정상/직전은 ready 및 begin 허용, 나머지는 not_ready/`OAUTH_APP_NOT_READY`; 거부 begin의 flow/opener/provider/save는 모두 0회다. C stale fixture는 `observed_at = now_utc - DEPLOYMENT_EVIDENCE_MAX_AGE`로 만들어 exact stale을 보장한다. record/bundle mismatch·assessment pending/unknown, begin 뒤 bundle/record/config generation 변경, callback stale에서 exchange 0, commit stale에서 B authorization/provider/admission/journal/stage/store/config/active/external mutation 0, status/begin/callback/commit의 단일 predicate·ConfigStore snapshot spy, terminal 후 fresh begin `OAUTH_APP_NOT_READY`도 검사한다. API/UI는 record, client/project/path/provider text를 노출하지 않는다.

### A1-R3 — SessionSecurity 소유 epoch와 OAuth 무효화 순서

`SessionSecurity`가 session별 non-secret opaque `session_epoch`의 유일한 owner다. 새 session bootstrap/replacement는 새 epoch를 발급하고 close, idle 만료, 교체 및 process restart는 이전 epoch를 즉시 무효화한다. 내부 `is_epoch_live(captured_epoch, now)`는 session 존재·epoch 동일성·idle을 확인하고, 만료를 발견하면 OAuth invalidation까지 완료한 후 false를 반환한다. Epoch는 HTTP/body/cookie/localStorage/log에 노출하지 않는다.

OAuth flow owner와 B authorization/one-shot replay key에 session epoch를 결속한다. callback/poll/cancel/commit/provider completion 전후 owner epoch를 재검사한다. session close/replacement/idle 때 해당 epoch의 미커밋 flow를 먼저 무효화하고 verifier/candidate/token을 지운 뒤 child cancel 및 기존 MutationBarrier drain을 수행한다. Launcher handover는 OAuth invalidation 완료 후 `barrier.close()`를 호출한다. Committing을 cancelled로 위장하지 않고 기존 drain/journal recovery를 따른다. 새 process는 이전 in-memory flow/authorization을 재사용할 수 없다.

이는 v9 process/flow epoch에 session epoch를 더한다. B ledger key/typed authorization에도 session epoch를 포함해야 하므로 `credential_service.py`의 candidate bytes가 바뀐다. 그 변경에는 A1-R2와 동일한 별도 exact pin-review/human-application gate가 적용된다.

**회귀:** bootstrap·replacement·close·idle epoch, process restart stale flow 거부, idle 경계, callback stale면 provider call 0, barrier와 replacement 경쟁, launcher의 OAuth invalidation 완료가 `MutationBarrier.close()`보다 먼저임, old child/browser completion이 새 session 상태를 바꾸지 못함. SA legacy와 ordinary session/API contract 불변.

### A1-R4 — Fetch Metadata 네 헤더 exact compatibility

경계는 `Sec-Fetch-Site`, `Sec-Fetch-Mode`, `Sec-Fetch-Dest`, `Sec-Fetch-User` 네 헤더를 검사한다. 모두 absent 또는 모두 단일 present만 허용하며 일부 present·duplicate·empty·비정상 값은 거부한다. Present인 경우 Mode=`navigate`, Dest=`document`, User=`?1`이 정확히 필요하다.

| 경로 | 네 헤더 모두 present | 네 헤더 모두 absent |
|---|---|---|
| OAuth callback | Site=`cross-site`, Mode=`navigate`, Dest=`document`, User=`?1` | v9 exact host/loopback peer/fixed path, bounded query, live one-use state/session epoch를 모두 검증 |
| OAuth result | Site=`same-origin` 또는 `cross-site`, Mode=`navigate`, Dest=`document`, User=`?1` | v9 exact host/peer/fixed path/query-empty data-free page; cookie/session/flow 불요 |
| 기타 Settings route | 기존 정책 그대로 | 기존 정책 그대로 |

Partial/duplicate, iframe, subresource, `no-cors`, 잘못된 User, route별 표 밖 값은 거부한다. Redirect-chain site 분류와 일반 route 응답은 v9와 동일해야 한다.

### A1-R5 — account_mismatch는 B post-verification terminal

Callback이 아니라 purpose/account/peer 검증을 소유한 B가 exact typed `account_mismatch` 결과를 만든다. A flow owner는 이 결과를 한 번만 `account_mismatch/ACCOUNT_MISMATCH`로 project한다. B가 provider read, named journal/stage/cleanup을 거친 뒤 발생할 수 있으므로 v9의 “B effect 전”/“아무것도 저장되지 않았다” 문구는 대체한다. 허용되는 확정 copy는 최종 active credential, config, 외부 credential bytes가 바뀌지 않았다는 범위뿐이다. 내부 read/journal/temp stage/cleanup의 부재는 주장하지 않는다. B error typing과 C fixed copy를 연결하고 raw message/provider text는 금지한다.

**회귀:** wrong account/peer에서 fixed state/code, final active/config/external bytes unchanged, 실제 journal/stage/cleanup이 있으면 정확히 허용·관측, callback exchange와 mismatch 결과 혼동 0, API/UI에 raw provider message/sentinel 0. Existing B transaction/recovery 계약 유지.

### A1-R6 — OAuth 전용 fixed-code error projection

OAuth route와 callback/result 경계는 `app.py::_service_error()`를 부르지 않는다. OAuth 전용 allowlist mapper만 body `{"error":{"code":"..."}}`를 만든다. Exception message/fields, provider body/URL/traceback/sentinel은 제외한다. Unknown code/type/exception은 fixed `OAUTH_UNAVAILABLE`/503으로 정규화한다. OAuth path의 `CONFIGURATION_BUSY`는 `OAUTH_UNAVAILABLE`/503; `SecurityBoundary`의 OAuth body-size rejection은 `REQUEST_TOO_LARGE`/413으로 매핑해 OAuth allowlist에 넣는다. 다른 v9 OAuth codes는 유지한다. 일반 Settings path는 기존 `_service_error()`와 body/status를 유지한다.

**회귀:** 모든 OAuth route/domain/security exception, CONFIGURATION_BUSY, REQUEST_TOO_LARGE, unknown code와 raw provider/error sentinel에 대해 status/fixed code만 허용; extra fields/raw text 0; OAuth route에서 `_service_error()` 호출 0; ordinary API 오류 body/status unchanged. ASGI에서 app 진입 전 생성되는 413도 path-aware fixed projection 적용.

### A1-O1 — google_oauth typo near-miss

`config/loader.py::_check_top_level_typos()`에 `google_oauth` 하나만 `credentials`와 같은 제한적 near-miss 검사를 추가한다. section/bundle 부재는 v9 not-ready, 가까운 오타는 validation failure다. 전역 unknown-key rejection이나 unrelated unknown top-level 호환성 변경은 금지한다. `google_oatu`/`google_oauthh` 등 distance 1–2 negative와 exact key positive, 무관한 unknown key 기존 동작을 회귀로 둔다.

### A1-O2 — expires_in_seconds rounding

`ceil(max(0, expires_at_monotonic-now_monotonic))`를 사용한다. `pending|exchanging|awaiting_commit`의 양수 잔여시간은 1..300, 만료 terminal은 0이다. `committing`은 A2-R2의 별도 범위(0..300)를 따른다. 남은 300초, 1초를 넘는 소수, 1초 미만 양수, exact expiry, expiry 이후, 300초 초과를 검사한다.

### A1 file closure와 pin 경계

새 제품 모듈은 v9처럼 `src/uls/settings/google_oauth.py` 하나다. A1 후보·read-only 경계의 실제 SHA/size와 현재 compiled 17 pin 여부는 아래 inventory JSON에 보존한다. 핵심 후보는 `runtime.py`, `settings/app.py`, `security.py`, `composition.py`, `launcher.py`, `status.py`, `static/app.js`, config `schema.py/loader.py/validation.py`, `config.example.yaml`; `config_service.py`는 ConfigStore snapshot read-only dependency다. B `credential_service.py`는 v9 candidate이며 session epoch 및 locked recheck 구현 시 pin gate가 별도로 필요하다. R5 fixed copy는 UI owner source/tests에 반영한다. 신규 `tests/contract/test_settings_google_oauth.py`를 중심으로 unpinned HTTP/composition/UI/launcher tests와 harness에 회귀를 둔다. 기존 v9 source/test closure는 모두 수락 검사 범위로 승계한다.

현재 compiled pin에 포함된 A1 관련 파일: `src/uls/settings/credential_service.py`, `src/uls/config/credentials.py`, `tests/contract/test_settings_credential_http.py`, `tests/contract/test_settings_credential_service.py`, `tests/unit/test_config_loader_credentials.py`, `tests/unit/test_runtime_google_credentials.py`, `tests/unit/test_credential_resolver.py`, `tests/unit/test_credential_resolver_file_source.py`. 기존 SA positive 및 모든 pinned bytes는 우선 변경하지 않는다. 필요한 pin-file 변경은 exact candidate/source hash별 독립 검토와 인간의 적용 결정 전 HOLD; checker, `source_pins.json`, global policy/settings 불변.

A1/A2 보완만으로 전체 implementation readiness가 아니다. 독립 Native/Gemini 계획 재검토, 총괄 통합 disposition 및 명시적 GO 전까지 제품·테스트 변경과 실행은 없다. 나머지 v9 Native 묶음은 같은 v10에 누적한다.

### A2-R2 — commit TTL 뒤에도 committing 소유와 wire 상태 유지

이 조항은 v9의 `FlowSnapshot.expires_in_seconds` active-state 행, C TTL/cancel cleanup, A/B commit lifecycle을 아래 범위에서 대체한다. `pending`, `exchanging`, `awaiting_commit`만 300초 flow TTL을 적용한다. `committing`은 B transaction이 진행 중인 상태이므로 TTL 경과만으로 `expired`/`cancelled` terminal이 되지 않는다.

- Exact `expires_in_seconds`: `pending|exchanging|awaiting_commit`은 1–300; `committing`은 0–300; terminal은 0. `committing`은 flow TTL 전까지 countdown을 보이고 `now >= flow.expires_at_monotonic`이면 정확히 0이다. `committing + 0`은 새 commit authorization이 더는 유효하지 않다는 뜻일 뿐 기존 transaction의 취소나 완료가 아니다. 나머지 FlowSnapshot key/state/code mapping은 v9 그대로다.
- Commit dispatch 뒤 B가 initial locked recheck를 통과하면 같은 transaction을 completion/recovery까지 따른다. Authorization expiry와 B ledger tombstone TTL을 늘리지 않는다. 재발급·재consume·두 번째 B save/transaction도 금지한다. Initial locked recheck 시각에 이미 만료됐다면 v9대로 durable publish/create/stage 전 거부하며 해당 recheck 조건은 그대로 유지한다.
- A owns the `committing` state/wire schema; C-R4 owns the login-countdown versus commit-result observer timers, copy, focus, duplicate-request prevention, and cleanup rules. After commit POST dispatch or an observed `committing` snapshot, TTL cannot mark the flow expired/cancelled/reconnectable, abort result observation, release its owner, start a new begin/commit, or roll back. With a live session, the same owner performs one-at-a-time status/readback polling. Cancel `COMMIT_IN_PROGRESS` is not cancellation. A verified complete plus exact-generation fresh credentials and Overview are required for success; uncertain observation ends as neutral unconfirmed with status/recovery guidance. Session invalidation follows v9 R3.

**필수 연결 회귀:** fake monotonic clock 299초에 commit 시작, B ledger consume와 initial locked recheck 통과 뒤 stage를 만든 상태에서 provider verification을 hold하고 300초를 넘긴다. Poll은 `state=committing, code=null, expires_in_seconds=0, config_generation=null` exact schema를 반환한다. Cancel은 취소/만료 성공을 표시하지 않는다. 경계 통과로 새 consume/save/transaction이 생기지 않는다(이미 시작된 동일 transaction 1개만 유지). Verification release 뒤 같은 flow가 `complete/COMMITTED`가 되고, 그 captured generation의 fresh credentials·Overview readback 뒤에만 success한다. 별도 케이스에서 initial locked recheck 전에 만료시키면 기존 rejection 및 durable/persistent mutation 0을 유지한다. C fake-browser regression은 299→300 crossing, cancel response, polling continuity, same-flow generation-bound readback을 연결한다.

### A2 판정 대응

Native A-protocol-runtime-2 REQUIRED 1은 A1-R2 동일 freshness predicate·시간 경계식·양 purpose Overview/begin 회귀에 통합했다. REQUIRED 2는 이 절의 exact committing schema/lifecycle 및 A/B/C 연결 race에 반영했다. 이 보완은 구현 승인이나 다른 packet의 수락을 뜻하지 않는다.

## v10 누적 보완 — Native v9 C-browser-UI packet 1

Native 판정은 REVISE / REQUIRED 3 / OPTIONAL 0이며 총괄이 세 건을 채택했다. 이 절은 승계 C-R1/C-R2/C-R4의 상충 문구를 아래 범위에서 대체한다. v9 원본과 이 plan의 다른 경계는 수정하지 않는다.

| 근거 | SHA-256 | bytes | 판정 |
|---|---|---:|---|
| Native C-browser-UI packet 1 canonical harvest | `5889bc4ea01f04be15e9389683e746e950420bb5e1e896585a5d68de6be1fa24` | 13,633 | REVISE REQUIRED 3 / OPTIONAL 0; 총괄이 세 건 채택 |

### C1-R1 — Login TTL과 이미 시작된 commit 결과 관측 수명

A의 A2-R2가 소유하는 wire는 `committing`에서 `expires_in_seconds=0`을 허용하며 이는 신규 권한·취소·완료를 뜻하지 않는다. C-R4는 login countdown timer와 commit 결과/read-only polling owner의 수명을 분리한다. Commit POST가 in-flight이거나 `committing` snapshot을 관측한 뒤에는 TTL만으로 `expired`, `cancelled`, reconnectable을 표시하지 않고 결과 polling/readback도 종료하지 않는다. 세션이 유효한 동안 같은 owner가 한 번에 하나씩 status/readback을 계속한다. TTL은 새 commit 권한을 막을 뿐 진행 중 transaction에 재commit, 새 begin, rollback을 유발하지 않는다. 관측을 끝내야 하는 경우에는 unconfirmed와 status/recovery 안내를 표시하며 저장됨/저장 안 됨을 단정하지 않는다.

Timer cleanup은 나눠서 적용한다. TTL 경계에서 login countdown만 정리한다. Commit 결과 poll과 owner는 terminal/readback completion, explicit cancel에서 실제 `cancelled/FLOW_CANCELLED`를 확인한 경우, 또는 v9 SessionSecurity 소유의 session invalidation에서 정리한다. `COMMIT_IN_PROGRESS`는 cancel 성공이 아니므로 결과 polling과 저장 확인을 계속한다. A는 state/code/expiry wire transition을, C-R4는 timer·copy·focus·중복 요청 차단을 소유한다.

**통합 회귀:** A2의 단일 fake-clock 시나리오를 재사용한다. 299초에 commit POST를 dispatch하고 B initial locked recheck와 stage를 지난 뒤 provider verification을 보류해 300초를 넘긴다. snapshot은 `committing`, `code=null`, `expires_in_seconds=0`이며 Cancel과 polling 교차 중에도 expired/cancelled/reconnectable copy, 새 begin/commit, rollback이 0이다. Login countdown은 끝나도 result owner/poll은 유지한다. Complete라면 동일 flow의 exact-generation credentials 및 Overview readback 이후에만 success; 관측 실패면 unconfirmed/status 안내만 보이고 자동 재시도나 재연결 없이 owner는 session 정책에 따라 정리한다. 별도 대조군은 initial locked recheck 전에 만료되어 기존 거절·durable publish/create 0을 확인한다. A2와 C-R4는 이 경합을 별도 중복 시나리오로 세지 않고 하나의 증거에 연결한다.

### C1-R2 — `FLOW_BUSY`의 known-owner와 unknown-owner 처리

`FLOW_BUSY`는 flow ID/epoch를 반환하지 않으므로 C는 owner 신뢰성에 따라 나눈다. Known-owner란 같은 session/purpose에 대해 strict validation을 통과한 ID/epoch와 현재 C owner가 이미 보존된 경우다. 이때 duplicate Connect는 successor를 만들거나 owner/timer를 무효화하지 않고 추가 begin은 0이다. UI는 실제 상태에 맞는 Cancel control 또는 저장 상태 확인 영역으로 이동한다. `committing`이면 Cancel 대신 status/result 확인에 focus한다.

Unknown-owner란 새로고침·다른 탭 또는 begin 응답을 strict validation에서 폐기한 경우처럼 현재 C가 검증된 owner를 갖지 않은 상태다. ID 추정 및 poll/cancel/commit 요청은 모두 0이다. 해당 C 요청의 local busy만 해제하고 서버 flow를 취소했다고 표시하지 않으며, 다른 purpose card와 Canvas는 계속 사용할 수 있다. 카드 status에 focus하고 다음 고정 문구를 표시한다: “Another Google sign-in is already in progress. Return to the Settings window that started it, or close and reopen Settings to review the connection.” 자동 session 종료·begin·credential write는 금지한다. 사용자가 Settings를 다시 열면 credentials, Overview, recovery를 새로 읽는다. Discovery endpoint나 브라우저 저장 필드는 추가하지 않는다.

**회귀:** backend active owner는 유지한 채 새로운 C/DOM을 열고 `FLOW_BUSY`를 반환한다. Unknown-owner 경로에서 Cancel focus와 추정 ID request가 0, local busy만 해제, 다른 role/Canvas 조작은 가능해야 한다. Known-owner duplicate click은 기존 owner와 timer, 현재 status/Cancel focus를 보존하며 추가 begin 0이어야 한다.

### C1-R3 — 성공은 현재 purpose의 fresh deployment readiness도 요구

C-R2의 최종 성공 predicate에 `freshOverview.google_oauth_readiness[owner.purpose] === "ready"`를 추가한다. 이는 complete snapshot 및 exact-generation credentials readback 뒤의 **같은 fresh Overview**에서 검사한다. 반대 purpose의 `not_ready`, 전체 `setup_ready=false`, School `Partial`은 기존 좁은 계정 연결 성공과 양립한다. 현재 purpose가 유효하게 `not_ready`이면 성공 문구와 success focus를 억제하고 고정 설치 준비/status 안내를 표시한다. 이미 완료된 credential을 자동 rollback/delete/revoke하지 않고 “저장되지 않음”이라고 말하지 않는다. readiness가 missing/malformed/unknown이면 기존 neutral failure를 유지한다.

**회귀:** complete 및 exact credentials readback 이후 마지막 Overview에서 현재 purpose만 `ready→not_ready`로 바뀌면 connected success/focus와 자동 write/retry/revoke/delete는 모두 0이며 fixed setup/status 안내만 허용한다. 대조군은 현재 purpose `ready`, 반대 purpose `not_ready`, School `Partial`에서 기존 좁은 연결 성공이 허용됨을 확인한다. Malformed readiness는 neutral로 남는다.

## v10 누적 보완 — Native v9 C-browser-UI packet 2

Native 판정은 REVISE / REQUIRED 2 / OPTIONAL 1이다. REQUIRED 1 (`FLOW_BUSY` unknown owner)과 REQUIRED 2 (commit 중 TTL 이후 결과 관측)는 C1-R2 및 C1-R1/A2-R2와 같은 내용이므로, 아래 canonical 응답을 추가 근거로만 묶고 기존 규범·단일 회귀를 재사용한다. 새 endpoint, owner state, TTL, transaction 또는 추가 acceptance branch를 만들지 않는다.

| 근거 | SHA-256 | bytes | 판정 |
|---|---|---:|---|
| Native C-browser-UI packet 2 canonical harvest | `486818f507a6a4880663f800c8807e8e635eefda75492075cf39c09842d13699` | 12,951 | REVISE REQUIRED 2 / OPTIONAL 1; 총괄이 REQUIRED 2의 중복 근거 처리와 OPTIONAL 1 채택 |

### C2-O1 — Unknown/malformed readiness copy와 적용 시점

Initial readiness fetch/parse와 commit 뒤 fresh Overview readiness 검증에서 `google_oauth_readiness`가 missing key, unknown enum/value, 잘못된 type 또는 malformed/invalid JSON이면 동일한 fixed copy를 사용한다: “Google connection status could not be checked. Refresh status before trying again.” Initial 실패는 begin을 막는다. Commit 뒤 실패는 success copy/focus를 막고 unconfirmed/status 안내만 남기며, 이미 완료된 credential을 rollback/delete/revoke하거나 저장되지 않았다고 말하지 않는다. 이 readiness-specific copy는 각 단계의 해당 purpose가 유효하게 `not_ready`인 경우와도 구별된다. `not_ready`는 현재 purpose readiness/setup 안내를 쓰고, malformed/unknown은 위 중립 status copy를 쓴다. Commit 후 credentials readback의 별도 failure, pending recovery 또는 mismatch는 기존 recovery/status 문구를 유지한다.

**회귀:** initial readiness와 post-commit Overview 각각에서 missing key, unknown enum, wrong type, invalid JSON을 주입한다. 여덟 케이스 모두 exact readiness-neutral copy를 사용해야 한다. Initial 케이스는 begin/provider/save 0; post-commit 케이스는 connected success/focus와 자동 write/retry/revoke/delete 0, rollback 0이어야 한다. 대조군인 유효한 `not_ready`에는 준비 안내를, 일반 credentials/pending-recovery failure에는 기존 unconfirmed/recovery copy를 써서 적용 조건을 구별한다.

## v9 승계 본문


상태: PLAN-only. Native v7 C·D·D3의 수락된 규약과 기존 v8 전체를 승계하며, Native v7 D2 intake/privacy REQUIRED 2건을 아래 최상위 규범으로 추가한다. 이 절과 승계된 v8이 충돌하면 이 절만 우선한다. v8 원본 파일은 변경하지 않았다. 승계 본문 안의 v8 당시 단계·리뷰 상태·D2 후속 예정 문구는 역사 기록이며, 현재 단계는 v9 integrated PLAN review 대기다. 독립 Native/Gemini v9 통합 PLAN 리뷰와 총괄의 구현 GO 전에는 제품·테스트 편집이나 실행을 하지 않는다.

기준 문서 v8: docs/plans/drive-oauth-nondeveloper-v8.md, SHA-256 13d7c34c915c7ce41741dd664791dae2f71bc0c018ac87ff73b8b9c68d571791, 111,608 bytes. 아래 v8 승계 본문은 원래 첫 제목 줄을 제외해 포함하며, v10 B packet 2의 replay-test-status 정정과 C packet 1의 TTL/owner/readiness 규범만 명시적으로 대체한다. 이번 D2 리뷰 원문은 .insane-review/drive-oauth-20261005/native-plan-v7-D2-intake-privacy-bounded/harvest/response_harvest_20261006_033548_58528_7fb742.md, SHA-256 44e635fb45551608d665bf48a412d34dc1fc69c27be9256bd0aa9d29463c9eac, 10605 bytes다. 원문 판정은 REVISE / REQUIRED 2 / OPTIONAL 0. 총괄이 원문·전체 source audit·checker 결과를 확인하고 두 지적을 채택했다고 전달했다. 여기서는 테스트 실행이나 checker 검증을 주장하지 않는다.

## v9 규범 보완 — Native v7 D2 intake identity·privacy

v8의 C, D, D3 규약과 모든 A/B OAuth protocol·transaction 계약은 그대로 유지한다. 특히 v8의 D release gate, D3 singleton·deployment gate, C fake-browser matrix, D DerivedDriveWriter 검증은 약화하거나 대체하지 않는다. v8에서 src/uls/intake/worker.py를 D2 소유의 변경 없는 관련 파일로 적은 문장, tests/integration/test_intake_worker_preview.py를 별도 소유의 변경 없는 추가 gate로 적은 문장, 그리고 “추가 D2 review를 v8에 반영”한다는 문장은 이 v9에서 대체한다. v9는 intake 구현·회귀 후보와 통합 수락 조건을 아래에 정확히 고정한다.

### D2-R1 — WORKER OAuth identity와 durable intake binding

build_intake_worker()의 production composition은 v8 OAuth runtime이 발행한 동일한 fresh, non-secret WORKER identity attestation을 통해 GoogleDriveWorkerAdapter, IntakeWorker, 그리고 durable account/app binding을 하나의 immutable composition snapshot으로 구성한다. Snapshot은 적어도 purpose=WORKER, 실제 인증된 Drive permissionId, 실제 OAuth credential의 client ID, 설정된 WORKER client ID와 일치 여부, 서버에서 확인한 실제 granted-scope 집합, 현재 credential/runtime epoch를 담는다. access/refresh token, authorization code, client secret은 snapshot·binding·receipt·log에 넣지 않는다. attestation은 requested/local scopes= 인수만 보고 만들지 않는다.

1. WORKER snapshot은 v8의 bounded OAuth runtime/helper가 반환한 identity와 서버의 authoritative granted scopes에서만 만든다. 실제 client ID가 설정된 WORKER client ID와 다르거나 purpose/scope가 v8 WORKER 정책과 다르면 구성을 fail-closed한다. drive.readonly인 MCP snapshot은 WORKER 용도로 전달·재사용할 수 없다.
2. src/uls/runtime.py::build_intake_worker()는 이 snapshot을 정확히 한 번 받아 같은 객체 identity로 worker adapter와 IntakeWorker에 전달한다. adapter가 들고 있는 snapshot과 IntakeWorker가 검증하는 snapshot은 값 동등성만으로 대체하지 않고 같은 immutable instance여야 한다. Binding은 기존 src/uls/intake/identity.py::provider_binding_id()를 변경 없이 호출해 정확히 provider_binding_id("google_drive", <fresh Drive permissionId>, <configured WORKER client_id>)로 계산한다. raw identity 값은 durable storage에 저장하지 않고 기존 해시 binding만 ledger/request generation에 사용한다.
3. Worker credential refresh 때마다 v8의 같은 bounded attestation boundary로 새 permissionId, actual client ID, purpose, authoritative granted scopes를 다시 확인한다. refresh에서 이 중 하나라도 composition snapshot과 다르면 해당 worker runtime을 stale/unavailable로 latch하고, 기존 binding을 재사용하거나 새 credential에 맞춰 조용히 재계산하지 않는다. v10 D2-I1은 이 gate를 현재 관측된 모든 public effect entrypoint로 확장한다. 그중 `run_once()`은 lock 직후 `_sync_unlocked()`, `_submitted_request_keys()`, `_claim_request_unlocked()` 또는 processing 전에, `process_item()`은 lock 직후 gate를 통과한다. adapter는 이후 각 provider mutation 호출 직전 refresh가 발생하면 fresh identity comparison이 끝나기 전 SDK 요청을 보내지 않는다. 불일치 후 기존 receipt, plan, request generation, operation key를 재생하거나 다른 identity에 재결속하지 않는다. 이미 durable한 USER/source 기록은 바꾸거나 지우지 않는다. 새 정체성은 정상 구성의 새 snapshot·binding·generation에서만 사용한다.
4. Legacy service-account 경로는 기존 google_worker_service() / worker_provider_binding()의 identity·binding 의미를 유지한다. WORKER OAuth 검사로 service-account 입력을 재해석하지 않고, SA allowlist를 넓히거나 기존 양의 SA 동작을 제거하지 않는다. MCP credentials를 worker identity로 승격하는 fallback은 없다.

tests/integration/test_intake_worker_preview.py에 fake-only composition/refresh regressions를 추가한다. 같은 fresh WORKER permissionId+client와 동일 허용 scope snapshot은 같은 binding, permission ID 또는 client ID가 바뀌면 다른 binding이어야 한다. MCP purpose/snapshot 주입, configured-vs-actual client 불일치, scope/purpose drift는 fail-closed다. 이미 claim된 receipt/request generation이 있는 상태에서 fake refresh identity를 바꾸고 `sync()`, `run_once(sync=False, process=True)`, `create_input_request()`, `claim_request()`, `process_item()` 각 public API를 직접 호출한다. 각 불일치에서 Drive create/move, Notion create/update, receipt claim/application, generation 변경 및 binding recomputation이 0회이며, 이전 binding으로 기존 request/receipt를 처리하지 않는다. 동일 public wrapper에서 nested-lock이 없음을 recorder로 확인한다. 정상 same-identity refresh는 계속 진행한다. 기존 tests/unit/test_runtime_google_credentials.py의 legacy SA positive는 그대로 유지하고 변경하지 않는다.

### D2-R2 — claim 이후 processing 전 fresh registered Drive layout gate

src/uls/intake/worker.py::_sync_unlocked()의 validate_registered_drive_layout() 호출은 run_once(sync=False, process=True)와 public process_item()이 보장하는 freshness가 아니다. 두 경로 모두 기존 single-active-worker lock 안에서 processing 단계가 시작되기 직전에 해당 semester의 현재 등록된 전체 workspace chain을 다시 검증해야 한다. 검증을 sync 성공 여부나 이전 tick의 결과에 의존시키지 않는다.

IntakeWorker._process_transcript()와 _process_material()가 이미 통과하는 _require_mutation_capability()에 workspace-aware preflight를 결속한다. 입력은 _workspace_for_item(item)으로 확인한 semester에 대해 _group_workspaces()[semester]가 반환하는 등록된 workspace 전체다. 기존 src/uls/intake/registry.py::validate_registered_drive_layout()을 재사용하고, 검증기는 매 호출마다 새 metadata readback을 수행한다. module/process 사이 캐시로 이전 결과를 재사용하지 않는다. 이 gate는 run_once()의 local worker lock과 process_item()의 public lock이 보유된 동안 실행되어야 한다.

각 registered static target에서 현재 DriveMetadata가 모두 다음을 증명해야 한다: 요청한 file ID와 readback ID 일치, expected exact parent tuple 및 개인 Drive 경계, folder MIME, trashed is False, owned_by_me is True, owner_only is True 및 완전한 permission readback, is_publicly_shared is False(anyone/domain 포함), can_edit is True, can_move is True. permission_count, permission list 또는 capability 누락·오류·불확실성은 거부한다. 연결된 school root, semester, upload, course, recordings, materials 및 설정된 optional upload chain 전체에 같은 규칙을 적용한다. 실패 시 첫 processing-originated Drive create/move 또는 Notion create/update 전에 중단한다. ACL을 수정하거나 owner_only를 완화하지 않는다.

Run-loop failure handler도 이 순서를 깨면 안 된다. 현재 _record_job_error()가 일반 처리 오류를 Notion File Intake/Input Request에 투영할 수 있으므로, identity/layout preflight 거부는 별도의 typed pre-mutation failure 경로로 local state에만 안전하게 기록한다. 이 경로는 _record_item_error()의 Notion projection, _record_job_error()의 Input Request update, _project_file_intake*()를 호출하지 않는다. run_once는 local job failure를 기록해도 되지만 이 gate의 거부 결과로 Notion create/update를 시도하지 않는다. public process_item()은 안전 오류를 반환/전파하되 provider write를 하지 않는다. 오류 copy/log에는 원시 permission, account 또는 credential 값이 들어가지 않는다.

#### D2-R2 meaningful regressions

tests/unit/test_intake_registry.py에 현재 positive layout 및 trashed/shared-drive/parent-conflict tests를 유지하면서 exact readback matrix를 직접 추가한다. 각 경우 하나의 registered folder를 바꾸고 거부를 확인한다: owner_only=False/추가 permission, is_publicly_shared=True의 anyone 또는 domain, permission list/count/owner_only readback 누락, can_edit=False 또는 누락, can_move=False 또는 누락. API parser가 malformed permission/capability를 반환하는 경우도 통과시키지 않는다.

tests/integration/test_intake_worker_preview.py에서 claim과 immutable USER receipt/request generation을 먼저 설정한 뒤, processing 진입 직전에 fake provider의 recordings 또는 materials target metadata를 각각 owner_only=False, broad-share, permission readback missing, can_edit=False/missing, can_move=False/missing으로 바꾼다. 두 진입점 run_once(sync=False, process=True)와 process_item(intake_id)을 모두 검사한다. 실패로 인해 processing-originated Drive create, move, delete/ACL effect 및 Notion create/update가 전부 0이어야 한다. run_once의 local failure completion은 허용하되, preflight error projection은 외부로 쓰지 않아야 한다. 이 시험은 이미 claim된 request를 사용하므로 USER 승인/claim 동작은 측정 구간 밖에 두고, processing 결과의 provider effect counter를 정확히 비교한다.

OAuth binding drift 회귀와 layout drift 회귀는 receipt/request generation 재사용이 안전한지 직접 검증한다. D2에서 기존 tests/integration/test_intake_worker_preview.py를 변경·확장하는 것은 이제 승인된 구현 후보이며 “unchanged/separately owned”라는 v8 설명이 우선하지 않는다. D2는 v8의 USER ownership, explicit USER Submitted 승인, Partial 비승격, worker 단일성, source/provenance 경계를 그대로 유지한다.

### D2 implementation/test source closure and pin status

| Current path | SHA-256 | bytes | v10 disposition |
|---|---|---:|---|
| src/uls/runtime.py | ccc992e3c506c4fc980a5cfcdd52d0a5b85af38e0af7a24064c8f16701921136 | 12,574 | candidate: build one WORKER runtime/adapter/identity snapshot in build_intake_worker() |
| src/uls/adapters/drive/worker.py | 157dccadff17dcc0b65fa78fbe560506798a2bd96d8dd0bab7886d05bd9efa16 | 30,632 | candidate: carry snapshot and block SDK calls on refresh identity drift |
| src/uls/intake/worker.py | fa95cbc4c720ffb5677350c03eec663ff703c4162d4a9f27c90e0769d884d07d | 131,637 | candidate: bind snapshot; gate all public effect entrypoints; perform per-invocation fresh layout/error-projection barrier; validate new and marker-reused `_stage_derivative()` outputs |
| src/uls/intake/registry.py | 0f49da79eb587752cd139cc1c75a1479b96470c1c965467a189a5ce788362367 | 4,533 | unchanged dependency: reuse fresh validate_registered_drive_layout() |
| src/uls/intake/identity.py | 838e546d5e356d177d5076c3bff8c86785d11812cd6bb1e9fa9679c61c3215ae | 9,940 | unchanged dependency: reuse exact provider_binding_id() |
| src/uls/config/intake.py | ec5c299da3f7ff00c1b1282242d44f5b4a5601e58cafeb559da2e87fd34e7337 | 9,461 | unchanged registered workspace/semester source |
| tests/integration/test_intake_worker_preview.py | faf5fa7f2f3b9d5a83cd4160a7f07dbcf9ed09eac7f538f63161e2761891206e | 32,386 | candidate: WORKER binding/refresh, every public effect gate, layout/error barrier cross-matrix, and marker-reuse/new derivative postconditions |
| tests/unit/test_intake_registry.py | f95e21483336a0b0dffd0f91fd8ed197b75a22cccd2d56400df0930f5bb37a07 | 8,543 | candidate: direct owner/privacy/capability readback regressions |
| tests/unit/test_c2_drive_marker_recovery.py | 5a812b5416c2bc7252f867b9c755a07843be7ee096b146826428dc4c21032c44 | 13,312 | same adapter-boundary raw `driveId` parser regression; absent/null/non-string/string cases |
| tests/unit/test_c2_drive_marker_recovery.py | 5a812b5416c2bc7252f867b9c755a07843be7ee096b146826428dc4c21032c44 | 13,312 | unchanged-by-v9 baseline; v10 D raw-adapter `driveId` parser regression candidate |
| tests/unit/test_runtime_google_credentials.py | 0f88130c489f2484ec15d9b837e9922e91c04ca04ec31726803b7734904dab14 | 4365 | unchanged, compiled-pinned legacy service-account positive; do not edit |

The active Syllva checker currently compiles exactly 17 source paths from scripts/review_evidence_checker_candidate/state_check.py::PINNED_SOURCE_PINS. Direct membership inspection found src/uls/intake/worker.py, src/uls/intake/registry.py, src/uls/runtime.py, src/uls/adapters/drive/worker.py, src/uls/intake/identity.py, tests/unit/test_intake_registry.py, and tests/integration/test_intake_worker_preview.py are not in that compiled 17-path set or the current review-only source_pins.json. The legacy positive test tests/unit/test_runtime_google_credentials.py is pinned and remains byte-unchanged. No checker update is in this PLAN or authorized by it. Existing v8 changes to other pinned files still require their own exact current candidate/review/human pin decision at the later implementation stage; this D2 finding does not widen that authority.

### D2 integrated acceptance gate

The complete implementation gate is v8 A+B+C+D plus D2. It includes every inherited v8 plan and final acceptance, v8 D's `tests/unit/test_intake_registry.py`, direct Drive-adapter and derived-output tests, the C current-hash browser matrix, and the A/B OAuth tests, plus D2's public-entrypoint identity gates, per-invocation layout/error-projection barrier, derivative marker-reuse regressions and unchanged SA positive. The D2 focused command is `./.venv/bin/pytest -q tests/unit/test_intake_registry.py tests/integration/test_intake_worker_preview.py tests/unit/test_runtime_google_credentials.py tests/unit/test_c2_drive_marker_recovery.py tests/integration/test_native_runtime.py`; the pinned SA test remains unchanged. The D2 release record names exact commands and actual collected/passed/failed counts tied to one sorted current source manifest; no inherited baseline count substitutes for the current candidate. D2 acceptance also reasserts no ACL updates, no source moves, no automatic account rebind, no real credential/provider/School effect, and no Partial→Ready promotion. Full School acceptance/publication remains behind the separate v8 live deployment, fresh private-ACL and human/operator gates.

## 누적 지적별 v10 반영 위치

| 리뷰 묶음·지적 | v10 반영 절 | 중복 처리 |
|---|---|---|
| A-1 REQUIRED 1–6 | A1-R1, A1-R2, A1-R3, A1-R4, A1-R5, A1-R6 | 각 runtime, readiness, session, callback, account mismatch, error projection 계약에 직접 반영 |
| A-1 OPTIONAL 1–2 | A1-O1, A1-O2 | near-miss 범위 및 expiry rounding |
| A-2 REQUIRED 1 | A1-R2 및 A2 판정 대응 | D-1 REQUIRED 1, D-2 REQUIRED 1, v9 D3-frozen-authorities REQUIRED 1의 stale timestamp를 동일 freshness predicate로 병합 |
| A-2 REQUIRED 2 | A2-R2, C1-R1의 단일 fake-clock 통합 회귀 | C-1/C-2 중복 TTL 지적도 이 한 전이·관측 계약에 귀속 |
| B-1 REQUIRED 0 / OPTIONAL 0 | B transaction/recovery baseline 유지 | replay authority·lock·parser 계약을 수정하지 않음 |
| B-2 OPTIONAL 1 | B shared composition invariant 및 Required composition and cross-service regression | 현존 baseline과 추가할 OAuth service/composition replay 회귀를 구별 |
| C-1 REQUIRED 1 | A2-R2, C1-R1, C4 fake-browser matrix | commit TTL 전이 A, UI timer/copy/poll 소유 C로 분리하되 한 시나리오 |
| C-1 REQUIRED 2 | C1-R2 | known/unknown `FLOW_BUSY` 분기 및 no-ID-inference regression |
| C-1 REQUIRED 3 | C1-R3 | 현재 purpose의 fresh readiness만 성공 predicate에 추가 |
| C-2 REQUIRED 1–2 | C1-R2 및 A2-R2/C1-R1 | C-1과 같은 두 계약의 추가 출처; 중복 endpoint·상태·회귀 없음 |
| C-2 OPTIONAL 1 | C2-O1 | initial/post-commit malformed readiness의 fixed copy·적용 시점 |
| D-1 REQUIRED 1, D-2 REQUIRED 1, v9 D3-frozen-authorities REQUIRED 1 | A1-R2 및 A2 판정 대응 | 서로 다른 review source의 stale finding을 하나의 `DEPLOYMENT_EVIDENCE_MAX_AGE` predicate로 병합 |
| D-1 REQUIRED 2, v9 D3-frozen-authorities REQUIRED 2 | D `_metadata()` raw `driveId` contract, D-owned implementation/regression | absent-only personal default와 explicit-null/non-string rejection; 같은 adapter boundary |
| D-1 OPTIONAL 1 | D no-retry outcome contract 및 D-owned NativeWorker/WorkerRunner regression | post-dispatch ambiguity는 단 한 번 dispatch, retry 0 |
| D-2 OPTIONAL 1 | D-owned regression and no-side-effect acceptance | folder/file metadata 분리, 3 fault stage 도달 및 create 1회 후 부작용 검사 |
| Native v7 D3 REQUIRED 1–2 | B shared OAuth commit composition invariant; D-R1 deployment gate | composition singleton 및 restricted-scope live gate 유지 |
| v9 D3-frozen-authorities B replay confirmation | B replay/composition contract | 이미 닫힌 authority 확인만 기록; 의미 변경·새 scope 없음 |
| D2(v7) REQUIRED 1–2 | D2-R1, D2-R2, D2 integrated acceptance gate | 이전 WORKER binding·locked layout 계약 유지 |
| D2-1 REQUIRED 1–3 | v10 D2-I1, D2-I2, D2-I3 | public identity gate, per-call layout/error barrier, existing derivative marker reuse |
| D2-2 OPTIONAL 1 | D2-I1 production composition regression | injected-port shortcut 금지; helper 1회, shared snapshot `is`, same-snapshot binding, SA WORKER positive |

이 누적표는 review source와 현재 반영 위치를 연결할 뿐, v9에서 이미 닫힌 shared replay/composition authority, R1 lock-held regions, R3 candidate/peer parser 경계, callback의 Fetch Metadata 예외 범위, B/B2 transaction lock·budget·recovery 의미를 재정의하지 않는다. B-1과 B-2의 기존 transaction 계약은 그대로 유지한다.

## 계약 접점의 변경 후보 파일과 17-pin 영향

| 영역 | 계획상 변경 후보 소스 | 계획상 변경/추가 후보 테스트 | 현재 compiled 17-pin 영향 |
|---|---|---|---|
| A protocol/runtime/config/HTTP | 신규 `src/uls/settings/google_oauth.py`; `src/uls/settings/app.py`, `security.py`, `composition.py`, `launcher.py`; `src/uls/runtime.py`; `src/uls/config/schema.py`, `loader.py`, `validation.py`, `credentials.py`; `config.example.yaml` | 신규 `tests/contract/test_settings_google_oauth.py`; `tests/contract/test_settings_http.py`, `test_settings_credential_http.py`, `test_settings_composition.py`; `tests/unit/test_config_loader_credentials.py`; launcher/resolver tests는 source closure 및 acceptance | `src/uls/config/credentials.py`, `tests/contract/test_settings_credential_http.py`, `tests/unit/test_config_loader_credentials.py`는 compiled pin byte-change 후보. `tests/unit/test_runtime_google_credentials.py`는 pinned positive만 실행하고 변경 금지. Resolver tests는 pinned acceptance closure이며 현재 변경 후보가 아님 |
| B transaction/provider check | `src/uls/settings/credential_service.py`, `provider_checks.py` | `tests/contract/test_settings_credential_service.py`, `test_settings_provider_checks.py`, `test_settings_composition.py` | `credential_service.py` 및 `test_settings_credential_service.py`는 compiled pin byte-change 후보. 변경 시 exact candidate의 별도 독립 review와 인간의 적용 결정 전 checker HOLD |
| C browser/UI | `src/uls/settings/status.py`, `src/uls/settings/static/app.js`, `index.html`, `styles.css` | `tests/contract/test_settings_ui.py`, `tests/contract/settings_ui_harness.cjs` | 이 C 후보들은 현재 17 compiled pins 밖; pin/checker는 수정하지 않음 |
| D/D2 Drive·intake privacy | `src/uls/worker.py`, `src/uls/adapters/drive/worker.py`, `src/uls/intake/worker.py` | `tests/integration/test_native_runtime.py`, `tests/unit/test_c2_drive_marker_recovery.py`, `tests/unit/test_intake_registry.py`, `tests/integration/test_intake_worker_preview.py`; `tests/contract/test_worker_cli.py`, `test_mcp_runtime.py`는 변경 없이 실행 | D/D2 변경 후보는 현재 compiled 17 pins 밖. pinned `test_runtime_google_credentials.py`는 SA compatibility acceptance에만 사용하고 바이트 불변 |

위 표는 exact implementation delta 승인이나 pin 적용 승인이 아니다. 현재 compiled 17 중 planned byte-change 후보는 `src/uls/config/credentials.py`, `src/uls/settings/credential_service.py`, `tests/contract/test_settings_credential_http.py`, `tests/contract/test_settings_credential_service.py`, `tests/unit/test_config_loader_credentials.py`다. `tests/unit/test_runtime_google_credentials.py`는 legacy SA positive를 위해 변경 없이 실행한다. `tests/unit/test_credential_resolver.py`와 `tests/unit/test_credential_resolver_file_source.py`는 현재 acceptance closure에서 변경 없이 다루며, 실제 변경 필요가 발견되면 같은 별도 gate가 필요하다. 나머지 17-pin 경로(`credential_set`, admission/roles/stores, journal 및 관련 pinned tests)는 no-change다. 후보 pinned bytes가 실제 변경되면 checker는 의도적으로 stale/HOLD가 되며, active `state_check.py`, source pin map/inventory, pinned tests 또는 전역 정책은 이 PLAN에서 수정하지 않는다. Pin 적용은 exact candidate hash를 대상으로 한 별도 독립 검토와 인간 결정에 남긴다.

## 변경/리뷰 경계

이번 산출물은 v10 PLAN과 관련 source-inventory/task-record 문서뿐이다. v9 및 이전 review 원문, checker/pins, 제품·테스트·ACL·환경·실제 provider는 바꾸지 않았고 테스트도 실행하지 않았다. D2 packet 2의 기술 판정은 GO/0/1이지만 정식 checker re-harvest는 대기 중으로 별도 기록한다. 제품 구현에는 요구된 전체 Native/Gemini 통합 PLAN disposition 및 총괄의 명시적 GO가 필요하다. D2 fake-only 수락은 live School acceptance, ACL 정리 승인, OAuth 외부 배포 readiness를 대체하지 않는다.

## v8 승계 본문


상태: PLAN-only. Native v7 C REQUIRED 4건, D REQUIRED 3건, D3 REQUIRED 2건을 현재 읽은 소스에 맞춰 보완한다. 독립 Native/Gemini 계획 재검토와 총괄의 통합 GO 전에는 제품·테스트 편집이나 실행을 하지 않는다. v7의 R1/R2/R3 및 A/B/B2 계약은 그대로 승계하며, composition singleton 회귀는 v7의 B composition coverage를 REQUIRED로 승격한다. v8의 D 계약은 v7의 `D unchanged Drive API/privacy` 행과 A/B/C-only release gate를 명시적으로 대체한다. 추가 D2 review가 오면 같은 소유자가 이 v8에 반영한다.

## 규범 우선순위와 승계

이 문서는 v7 전체를 포함하는 독립 계획이다. 아래 C override는 v7의 `C browser/UI`, `Config/UI` 수락 항목 및 일반 흐름 중 UI가 의존하는 readiness/wire 부분을 대체한다. B composition override는 v7의 singleton ledger/service construction·acceptance를 보강해 production identity assertion을 REQUIRED로 승격한다. D override는 v7의 `D unchanged Drive API/privacy` no-change 선언, D의 회귀 소유권, privacy/readback 수락 항목 및 A/B/C-only release gate를 대체한다. 이 override들과 충돌하지 않는 v7 본문은 유지하되, v10 B packet 2와 C packet 1이 지정한 replay-test-status 및 C TTL/owner/readiness 표현만 정확히 정정한다. 특히 v7 R1/R2/R3 및 A/B/B2 구현·보안·transaction 계약, callback 경계, replay ledger semantics, provider budgets, payload parser, external-source consent, generic service-account 경계는 축약하거나 재해석하지 않는다. B composition override는 ledger consume·expiry·capacity·tombstone 의미는 유지하되 composition이 singleton object를 만들고 B service가 consume authority를 갖는다고 명확히 한다. 이어지는 v7 본문은 원 v7 파일의 제목행 뒤를 기준으로 하며, 위에 적은 v8 C/D 및 v10 B-2/C1 정정만 우선 적용한다. v7의 당시 상태·리뷰 기록은 역사 증거이며 현재 v8의 재검토나 구현 승인을 뜻하지 않는다.

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
| `expires_in_seconds` | integer 0–300; `pending|exchanging|awaiting_commit`은 1–300, `committing`은 0–300, terminal은 0 |
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
| `FLOW_BUSY` (known owner) | “A Google sign-in is already in progress. Continue checking its status or cancel it.” | 검증된 같은-purpose owner를 보존; cancellable이면 실제 Cancel, `committing`이면 저장 확인 영역에 focus; 추가 begin 0 |
| `FLOW_BUSY` (unknown owner) | “Another Google sign-in is already in progress. Return to the Settings window that started it, or close and reopen Settings to review the connection.” | ID 추정 및 poll/cancel/commit 0; 카드 status focus; local busy만 정리하고 서버 flow는 유지 |
| `BROWSER_LAUNCH_FAILED` | “Syllva could not open the Google sign-in page. Try again or use Advanced setup.” | 같은 purpose Connect retry; Connect control focus |
| `ACCESS_DENIED` | “Google access was not granted. You can try again when ready.” | retry 또는 종료; Connect control focus |
| `ACCOUNT_MISMATCH` | “Syllva could not verify this Google account for the current connection. Use the account required by your setup and try again.” | 새 flow로 재시도; Connect control focus |
| `FLOW_EXPIRED` | “This Google sign-in expired. Start again to retry.” | 새 flow만 가능; Connect control focus |
| `FLOW_CANCELLED` | “Google sign-in was canceled. You can connect an account later.” | 시작 카드에 focus; credential 결과를 바꾸지 않음 |
| `COMMIT_IN_PROGRESS` 또는 snapshot `committing` | “Syllva is finishing the Google connection and checking its status.” | cancel-success로 표현하지 않음; 같은 flow poll 유지 |
| commit 뒤 readback/Overview unavailable, pending, mismatch | “Syllva could not confirm the Google connection. Check the refreshed status and any recovery item before retrying.” | recovery/status 확인; 새 write를 자동 반복하지 않음; 카드 status focus |
| `FLOW_NOT_FOUND`, `FLOW_FAILED`, `OAUTH_UNAVAILABLE`, unknown code/state, extra/malformed non-readiness object | “Syllva could not verify the Google connection status. Refresh status before trying again.” | read-only refresh만; 알 수 없는 ID/action 실행 금지; 카드 status focus |
| Initial 또는 commit 후 `google_oauth_readiness` missing/unknown/malformed/wrong-type/invalid JSON | “Google connection status could not be checked. Refresh status before trying again.” | 두 단계가 같은 readiness-neutral copy 사용; initial이면 begin 금지, commit 후면 success/focus와 자동 mutation 금지; C2-O1 적용 |
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
- 각 owner는 자신이 만든 timeout/poll timer와 AbortController만 보유한다. TTL은 commit POST dispatch 전의 login countdown과 awaiting-commit 권한만 끝낸다. Commit POST가 in-flight이거나 `committing`을 관측한 뒤에는 countdown만 정리하고 result poll/owner를 유지한다. Terminal, confirmed explicit cancel, session end/replacement 또는 같은-purpose의 실제 successor가 되면 해당 owner가 가진 timer를 정리하고 fetch abort를 시도하며 Promise rejection을 수거한다. Abort가 실행 중인 Promise를 끝낸다고 가정하지 않는다. 늦게 resolve/reject한 Promise는 owner guard 뒤 무효 처리한다.
- C는 browser close를 감지해 flow가 끝났다고 표시하지 않는다. Commit dispatch 전에는 backend 300초 TTL이 login 대기와 새 commit 권한의 경계다. Dispatch 뒤에는 그 TTL만으로 이미 시작된 결과 관측을 종료하지 않는다. Login countdown은 best-effort UI이며 backend expiry가 authoritative다. Poll은 flow별 하나만 outstanding이고 고정 1초 간격을 넘겨 겹치지 않는다.
- Cancel 버튼은 `POST .../cancel`과 exact `{}`만 전송한다. Exact `cancelled/FLOW_CANCELLED` snapshot을 확인하기 전엔 cancelled 표시가 없다. Cancel 시 `committing`/`complete` 또는 `COMMIT_IN_PROGRESS`를 읽으면 저장/확인 경로를 계속하며 cancel success를 표시하지 않는다. Cancel fetch rejection은 fixed neutral/current-owner 문구를 남기고, committing이면 result poll/owner를 보존한다. 다른 terminal/cancel/session 경계에서만 해당 owner의 timer와 busy를 정리하며 자동 credential write/retry를 하지 않는다.
- `awaiting_commit` snapshot을 확인한 현재 owner만 exact generation body로 commit을 시작한다. `committing` snapshot이면 poll만 계속한다. 두 번째 commit은 UI에서 시작하지 않는다. Stale flow의 late commit이 backend transaction 결과를 되돌리려 하지 않고, 새 presentation도 쓰지 않는다.

Commit HTTP 2xx (`complete/COMMITTED`)는 성공 표시가 아니다. 현재 flow owner가 `GET /P/api/v1/credentials`를 다시 수행하고 exact `config_generation`이 complete snapshot의 generation과 일치하며, 카드 배열에서 정확히 하나의 `role=google-mcp`/`purpose=mcp` 또는 `role=google-worker`/`purpose=worker`, `provider=google` card를 확인해야 한다. 그 card는 `managed === true`, `state === "configured"`, `pending_operation === null`이어야 한다. Missing/duplicate/wrong role, mismatched generation, partial/pending recovery, malformed body, request failure는 neutral unconfirmed다. 그 뒤 같은 owner의 fresh `GET /P/api/v1/overview`를 확인하고 readiness subobject strict validation에 성공해야 하며, 최종 성공 predicate는 `freshOverview.google_oauth_readiness[owner.purpose] === "ready"`를 포함한다. 현재 purpose의 유효한 `not_ready`는 success 문구/focus 대신 fixed install-readiness/status 안내를 내고, 이미 완료된 저장을 rollback/delete/revoke하거나 저장되지 않았다고 말하지 않는다. Missing/malformed/unknown/wrong-type readiness 또는 invalid Overview JSON은 C2-O1의 readiness-neutral copy를 사용한다. 반대 purpose `not_ready`, 전체 `setup_ready=false`, School `Partial`은 좁은 연결 성공을 막지 않으며 이를 전체 School 자료 처리 성공으로 표현하지 않는다. 두 fresh readback 중 어느 하나라도 실패하거나 owner가 바뀌면 success와 success focus는 없다. 모든 조건을 만족한 뒤에만 “Google account connected to Syllva.”를 표시하고 해당 purpose card heading에 focus한다.

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
| Readiness and card order | MCP ready/worker not_ready 및 역방향; 둘 다 ready; 둘 다 not_ready; bundle-only, missing/stale/mismatched deployment record, provider assessment unknown/pending, Overview readiness missing/extra key/unknown value/wrong type/JSON error. Initial readiness와 commit 후 Overview 각각에서 missing key/unknown enum/wrong type/invalid JSON은 같은 exact “Google connection status could not be checked. Refresh status before trying again.”을 써야 하며 initial begin 0, post-commit success/focus 및 auto-mutation 0. 유효한 `not_ready`에는 별도 setup 안내를 쓴다. `ready`는 bundle+matching deployment gate가 모두 검증된 purpose만 가능; `not_ready`는 안내 우선/begin POST 0회; unknown은 neutral/disabled. Complete+credentials 이후 마지막 Overview에서 현재 purpose가 `ready→not_ready`로 바뀌면 setup/status 안내만 보이고 success copy/focus와 write/retry/revoke/delete 0; 현재 purpose ready+반대 purpose not_ready+School Partial은 좁은 성공 허용. Synthetic matching attestation은 계산만 검증하며 live deployment 완료 증거로 인정하지 않는다. MCP/WORKER exact scope copy + allowlist disclaimer가 consent 전에 visible. |
| Begin and response contract | 각 purpose의 exact begin JSON (`generation`,`replace` only), exact 201 snapshot; app-not-ready, `FLOW_BUSY`, generation conflict, launch opener success/false/exception. wrong status, missing/extra keys, unknown field/code/state, wrong flow ID/epoch, malformed JSON, `message`/provider sentinel은 fixed-neutral, second request/commit 0, timer/busy cleanup. |
| `FLOW_BUSY` owner split | Server-side active flow를 유지한 채 새 C/DOM을 열어 unknown-owner busy를 받는다: id 추정/poll/cancel/commit 0, status focus, exact fixed reopen 안내, local busy만 release, 다른 purpose/Canvas 사용 가능, automatic begin/session close/write 0. 사용자가 Settings를 다시 열었을 때 credentials/Overview/recovery를 fresh readback한다. Known-owner duplicate Connect는 기존 owner/timer 유지, state에 맞는 Cancel 또는 status focus, 추가 begin 0. |
| Poll state matrix | `pending`, `exchanging`, `awaiting_commit`, `committing`, `complete`, `cancelled`, `denied`, `account_mismatch`, `launch_failed`, `expired`, `failed`; 각 state/code/expiry/config-generation pair의 exact mapping과 가능한 transition만 실행. `FLOW_NOT_FOUND` (404/TTL 후)에는 새 flow만 제안; old ID를 commit/cancel에 재사용하지 않음. |
| Cancel, TTL, browser close | Explicit Cancel sends exact `{}` and only terminal cancelled snapshot says canceled. At 299→300 seconds, dispatch commit then hold B verification past TTL: committing schema permits `expires_in_seconds=0`; login countdown ends while commit result owner/poll persists; Cancel/`COMMIT_IN_PROGRESS` never says cancelled/expired/reconnectable and causes no second commit, begin, rollback or abort. Complete proceeds to fresh readbacks; uncertain result is unconfirmed/status-recovery only. Separate pre-lock expiry still rejects before durable publish/create. Fake browser close alone has no completion guarantee. Terminal/confirmed cancel/session invalidation cleans only owned timers; duplicate/outstanding poll is 0. |
| Consent, scopes, Advanced SA | Existing managed, external-file, environment credential each require affirmative replacement consent; decline emits begin 0; yes emits exact `replace:true`; no credential emits false. Denied/mismatch/expired before commit preserves old card bytes/state in fake backend. Advanced JSON path keeps existing `file.text()` success/error/cancel, POST-0 on cancel, input clear, modal/return-focus tests. |
| Commit and fresh readback | `awaiting_commit` → exact commit body once; duplicate commit/save 0 extra. Success only after complete 2xx generation equals fresh credentials generation, exactly one matching managed configured card and null pending operation, then the same fresh Overview has `google_oauth_readiness[owner.purpose] === "ready"`. Current-purpose not_ready suppresses success/focus and yields fixed setup/status guidance without rollback/delete/revoke or “not saved”; malformed readiness remains neutral. Opposite-purpose not_ready and School Partial are compatible with narrowly worded success. |
| Owner races | Late poll after cancel/reconnect; old same-purpose flow after successor; known-owner duplicate Connect; unknown-owner FLOW_BUSY after reload/another tab/strict response discard; late commit vs new credential action; session expiry/replacement; A completion after newer Canvas connect/replace/forget; newer Google/Notion credential action while A poll/Overview is in flight; stale Overview success and rejection. Assert old completion cannot change newer notice, focus, dialog, card mutation owner or busy token; it can release only resources bearing its own owner token. Every promise rejection is caught and cleanup is owner-conditional. |
| Fixed errors / privacy / focus | Each code: app-not-ready, known/unknown busy, launch-failure, denied, account mismatch, expired, flow-not-found, commit-in-progress, readback-unconfirmed, session-ended; assert exact fixed copy, next retry target and semantic focus. FLOW_BUSY unknown-owner never infers/uses an ID; known-owner focuses the actual state-appropriate control. Inject secret/client-secret/access-token/refresh-token/code/verifier/provider-body sentinels and hostile `error.message`; none appear in DOM/accessibility/error copy. Unknown API responses never choose a DOM ID, route or action. |
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

### v10 누적 보완 — Native v9 D-Drive-API-frozen-privacy packets 1/2 및 D3-frozen-authorities

Packet 1은 REVISE / REQUIRED 2 / OPTIONAL 1, packet 2는 REVISE / REQUIRED 1 / OPTIONAL 1, D3-frozen-authorities는 REVISE / REQUIRED 2 / OPTIONAL 0이다. 총괄이 packet 1의 모든 항목과 packet 2의 OPTIONAL 1을 채택했다. D3 REQUIRED 두 건도 각각 A1-R2/A2-R1의 공통 deployment-evidence freshness predicate와 D packet 1의 raw `driveId` parser gap에 이미 대응하므로 source 근거만 더하며 새로운 규칙이나 중복 회귀를 만들지 않는다. D3은 B shared replay authority와 digest 구분의 기존 계약이 닫혔다고 확인했으며, 여기서 B scope를 추가하지 않는다. Packet 1 REQUIRED 2는 D2가 이미 변경 후보로 둔 동일 `src/uls/adapters/drive/worker.py`에 정확한 raw `driveId` parser 계약을 추가한다. Packet 1 OPTIONAL 1은 실제 fake `NativeWorker`/`WorkerRunner` 경로의 post-dispatch response-loss 및 no-retry 회귀다. Packet 2 OPTIONAL 1은 folder metadata와 generated-file metadata를 분리하고 생성 응답·immediate readback·pre-publication readback에 각각 fault injection하여 positive/negative가 의도한 단계에 도달했음을 증명하도록 D 회귀 fixture를 구체화한다.

| 근거 | SHA-256 | bytes | 판정 |
|---|---|---:|---|
| Native v9 D-Drive-API-frozen-privacy packet 1 canonical harvest | `a735c6c203a83be79636f4e85124c09c20348f73e7882f359f3692cdc01c2494` | 13,922 | REVISE REQUIRED 2 / OPTIONAL 1; 총괄이 REQUIRED 1을 A1-R2/A2-R1 근거로 연결하고 REQUIRED 2 및 OPTIONAL 1을 채택 |
| Native v9 D-Drive-API-frozen-privacy packet 2 canonical harvest | `3f4b0fa9aafcf0f2ce182be9e99fc09f26aa66e0bfc9bd1ca73e0b2a132037b8` | 13,183 | REVISE REQUIRED 1 / OPTIONAL 1; 총괄이 REQUIRED 1을 stale 출처로 연결하고 OPTIONAL 1을 채택 |
| Native v9 D3-frozen-authorities canonical harvest | `ed28f68547a72b184bea7843e3ebd1e947dfe68006017dfc3027a5732f07c86d` | 14,724 | REVISE REQUIRED 2 / OPTIONAL 0; stale 및 `driveId` 지적은 기존 규칙의 추가 근거, B replay authority는 계약상 닫힘 확인 |

이 D packet 1/2의 current read-set과 실제 SHA/size는 `.insane-review/drive-oauth-20261005/drive-oauth-v10-D1-D2-source-inventory.json`에 기록한다. 기존 A1 inventory는 이전 review snapshot으로 보존한다.

Packet 2의 adopted fixture 규칙은 `tests/integration/test_native_runtime.py`에서 기존 `derived_metadata_overrides`(generated folder 전용)의 역할을 변경하지 않고, folder metadata와 생성 file별 create-response·immediate-readback·pre-publication-readback metadata를 분리하는 fake store/fault stages를 요구한다. 기존 생성 전 unsafe-folder rejection은 create 0회를 유지한다. 생성 후 단계의 각 failure case는 해당 단계에 도달했음을 기록하고 먼저 fake create dispatch가 정확히 1회였음을 확인한 뒤, 추가 create/move, Notion pointer/Ready, ACL update, source move, automatic delete가 모두 0회인지 확인한다.

`_metadata()`는 `DriveMetadata`를 만들기 전에 원시 provider mapping을 검사한다. `driveId` key가 absent이면 개인 Drive의 canonical 입력으로 `drive_id=None`을 허용한다. key가 present이고 값이 string이면 원문 string을 보존하며 기존 `require_private_ownership()`가 모든 non-None ID를 shared-drive로 거부한다. key가 present인 explicit `null`은 물론 number, boolean, array/list, object/dict 등 모든 non-string 값을 malformed로 안전하게 거부한다. 이를 `None`으로 정규화하지 않으며 오류는 fixed `SourceUnavailableError` 계열이고 provider payload를 노출하지 않는다. Thus absent와 explicit null은 다른 입력이다.

파생 파일 `create_file_with_marker()`가 provider request를 dispatch할 수 있는 경계에 들어간 뒤 create 응답·metadata 검사 또는 staged-file의 immediate readback/content/final pre-publication 검증이 신뢰 불가능하면, adapter/`DerivedDriveWriter`는 같은 D-owned `DriveCreateOutcomeUnknownError`(예: `SourceUnavailableError`의 구체형)로 끝낸다. 이 형식은 `ProviderUnavailableError`의 subtype이 아니어야 한다. `WorkerRunner`는 `ProviderUnavailableError`만 `TRANSIENT`로 분류하고 다른 오류는 `PERMANENT`로 끝내므로, 모호한 create outcome은 재시도되지 않는다. provider 호출이 시작되기 전의 로컬 validation failure는 기존 오류 의미를 유지한다. 이 no-retry 분류는 derived-file create와 그 staged 검증 구간에만 적용하고 기존 marker-folder create/reconciliation 및 일반 provider retry 동작은 바꾸지 않는다. 오류 text/code는 고정되고 provider body나 비밀을 포함하지 않는다.

### D-owned implementation contract

v7의 `D unchanged Drive API/privacy`는 이 절로 대체한다. `src/uls/worker.py::DerivedDriveWriter`는 실제 `NativeWorker.process()`가 사용하는 write branch이므로 no-change가 아니다. SA-only로 격리하는 대안은 채택하지 않는다. 같은 worker branch가 OAuth worker credential로도 실행되므로, credential 종류를 근거로 약한 파일 검증을 허용하지 않고 `DerivedDriveWriter` 자체에서 frozen private/provenance 조건을 지킨다.

D3 동결 문서 검토는 private-copy나 원본 이동을 추가하지 않는 현재 경계를 확인했다. 이 OAuth 변경은 private-copy workaround, original-source move, public sharing을 도입하지 않는다. Derived output은 별도 staged file ID와 기존 provenance marker를 유지한다.

기존 `GoogleDriveWorkerAdapter` 경계를 재사용한다. `create_file_with_marker()`, `read_metadata()`, `require_private_ownership()`는 full metadata response, exact parent/MIME/marker, owner-only privacy, no shared-drive/broad sharing 및 worker capability를 확인한다. D2-R1의 immutable WORKER attestation/binding 전달도 이 adapter 경계에서 수행한다. D packet 1이 확인한 현재 parser 결함 때문에 `src/uls/adapters/drive/worker.py`는 D2-R1과 D packet 1의 공동 change candidate이며 단순 재사용/unchanged 의존성이 아니다. `DerivedDriveWriter`는 raw Drive create의 `fields='id'` 호출을 제거하고 다음 순서로 동작한다.

1. 기존 destination-folder fresh read 및 private ownership 검사 후 `create_file_with_marker(folder_id, entity_id + '.staged.md', 'text/markdown', content.encode('utf-8'), {'uls_entity': entity_id, 'uls_source': source_ref.file_id})`를 사용한다. 생성 반환 metadata의 ID를 고정한다.
2. 즉시 같은 ID로 `read_metadata()`를 다시 호출하고 생성 반환값과 live readback 양쪽에서 ID, 정확히 한 parent=`folder_id`, MIME, exact `uls_entity`/`uls_source`, `trashed is False`, `owned_by_me is True`, 개인 Drive(`drive_id is None`), permission readback 완전성 및 owner-only, broad-share false, `can_edit is True`, `can_move is True`를 검증한다. `_metadata()`의 raw `driveId` 검사도 dataclass 생성 전 각 provider response에 적용한다. Missing/extra permission이나 capability가 불명확하면 통과시키지 않는다.
3. 그 ID의 content bytes를 다시 읽어 `content.encode('utf-8')`와 byte-for-byte 비교하고, publication 직전 metadata를 다시 읽어 같은 전체 identity/privacy/marker 조건과 content를 재확인한다. `SourceRef`는 이 검증을 통과한 동일 file ID로만 만든다. 검증 중 source_ref의 file ID로 덮어쓰기·이동하지 않는다.
4. `publish_staged_derived()`는 전달된 staged/source/entity identity를 다시 일치 확인하고 위의 fresh metadata+bytes 검증을 거친 후에만 `staged_ref`를 반환한다. 그 반환 뒤 기존 Notion writer만 URL pointer/Ready 효과를 낼 수 있다. 검증 실패·provider timeout·readback 누락·결과 모호성은 fail-closed/ambiguous이며 자동 Drive delete, ACL 변경, source move 또는 재시도를 하지 않는다. 특히 derived create dispatch 뒤 response loss, malformed create response 또는 staged 검증 readback 실패는 `DriveCreateOutcomeUnknownError`로 분류되어 `WorkerRunner`의 transient retry에 들어가지 않는다. 이미 create가 적용됐을 수 있으면 새 파일을 임의 cleanup하지 않고 job을 failure/review 경로에 남기며 Notion pointer/Ready를 발행하지 않는다.

### D-owned regression and no-side-effect acceptance

`tests/integration/test_native_runtime.py`는 위 legacy `DerivedDriveWriter` 경로를 실제 fake `NativeWorker`와 `WorkerRunner`에서 실행하는 주 contract regression이다. Fixture는 folder metadata(기존 `derived_metadata_overrides`)와 generated-file metadata를 서로 분리하고, create-response, immediate readback, pre-publication readback을 독립 fault-injection 단계로 둔다. 각 post-create test는 대상 단계 도달 event를 확인하므로 folder preflight가 먼저 실패해 의도한 생성 후 경로를 건너뛸 수 없다. Exact byte equality만으로 성공시키지 않고 다음 metadata/content cases 각각에서 Notion pointer publication 0회와 `Ready` 전환 0회를 assert한다: exact bytes+wrong parent, wrong MIME, `uls_entity`/`uls_source` drift, missing/true trashed, `owned_by_me` false, extra user 또는 service-account permission (`owner_only=false`), anyone/domain permission, shared drive, permission list absent/malformed, `can_edit` 또는 `can_move` false/missing, create response와 subsequent fresh readback 불일치, 그리고 create 후 publication 전에 metadata/content drift. `run_once(sync=False, process=True)` 및 public `process_item()`의 preflight에서 raw `driveId` 거부는 첫 processing mutation 전이어야 하며 Drive create/move 및 Notion create/update는 0이다. Raw API mapping에 대한 `driveId` case는 direct adapter unit test에서 별도로 검사한다.

동일 integration suite는 provider가 derived-file create를 적용한 뒤 response를 잃는 case도 실행한다. 실제 fake NativeWorker/runner에 대해 create dispatch 총 1회였음을 우선 assert하고, 자동 retry/requeue 0회(다음 runner tick 포함), Notion pointer/Ready 0회, 자동 delete/ACL update 0회를 확인한다. Create-response fault는 create가 한 번 적용된 뒤 acknowledgment를 잃도록 하고, immediate-readback 및 pre-publication fault는 앞 단계가 완료된 후 각각 그 단계에서 실패시킨다. 각 생성 후 실패는 target stage 도달 counter와 create 1회를 먼저 증명한 후 side-effect 0을 확인한다. 이때 provider dispatch 후 또는 staged 검증 구간의 불명확한 실패는 `DriveCreateOutcomeUnknownError`로 변환되어 generic `ProviderUnavailableError` retry를 우회한다. `create_file_with_marker()`가 돌려준 metadata에 malformed `driveId`가 있거나 후속 fresh readback에서 malformed raw `driveId`가 발견되어도, 이미 dispatch된 create 이외에 추가 create/move, cleanup, pointer, Ready는 0회다. 기존 marker-folder response-loss/reconciliation 회귀와 생성 전 folder rejection의 create 0 assertion은 그대로 보존한다. Positive case는 exact ID/parent/MIME/markers/private capability와 bytes를 모두 read back한 뒤에만 pointer/Ready를 허용한다. 모든 negative case에서 permission update, ACL cleanup, source move, derivative auto-delete가 0이다.

`tests/unit/test_intake_registry.py`에 root/course registry의 privacy regression closure를 명시하고, 현재 shared-drive/trashed 거부 외에 extra user/SA, absent permission readback, broad domain/anyone share, edit/move false 또는 missing, permission/capability drift를 parameterize한다. Direct Drive-adapter 경계는 `tests/unit/test_c2_drive_marker_recovery.py`에서 complete raw provider JSON을 반환하는 fake SDK의 실제 `GoogleDriveWorkerAdapter.read_metadata()`를 호출해 검사한다. 각 fixture는 정상 metadata에서 raw `driveId` 하나만 변경한다. `driveId` absent는 positive; explicit null, number, boolean, list/array, object/dict는 dataclass 생성 전 fixed safe error; 정상 string shared-drive ID는 parse 후 기존 private-ownership validator가 거부하는 negative다. Typed `DriveMetadata(drive_id=None)`만 주입하는 검사는 parser regression을 대체하지 못한다. `tests/contract/test_worker_cli.py`와 `tests/contract/test_mcp_runtime.py`는 기존 CLI/MCP privacy regression으로 함께 실행하며 generic `ProviderUnavailableError` 재시도 기준선은 그대로 둔다.

OAuth connect 및 OAuth Connection Test는 ACL·Drive file permission을 바꾸거나 School source를 이동하지 않는다. A-owned `tests/contract/test_settings_google_oauth.py`와 B-owned `tests/contract/test_settings_provider_checks.py`의 fake-service acceptance에서 permission create/update/delete와 source `files.update`/move effect counter를 0으로 확인하며, D owner가 이 결과를 D closure에 결속한다. `tests/integration/test_intake_worker_preview.py`의 intake 호출 순서, `Partial`, privacy acceptance는 D2 owner 소유로 유지한다. D의 통과를 D2/D3의 검토 완료나 live School acceptance로 해석하지 않는다.

Existing test service-account ACL을 자동 삭제하거나 완화하지 않는다. Live D write는 실제 fresh readback에서 `owner_only=true` 및 나머지 exact private/capability 조건을 만족해야 한다. OAuth 연결과 target readback 뒤의 서비스 계정 접근권 정리는 별도 exact-target fresh ACL readback 및 인간 승인이 필요한 후속 effect다. 그 승인이 오기 전 `owner_only=false`는 fail-closed다. Google service-account identity/key 자체를 삭제한다는 뜻은 아니다.

### D file closure and pin check

| Path | Current SHA-256 | bytes | D disposition |
|---|---|---:|---|
| `.insane-review/drive-oauth-20261005/native-plan-v7-D-Drive-API-frozen-privacy/harvest/response_harvest_20261006_025501_42930_cdfbbf.md` | `5c291c0bfad16e01771d43f95cd93dea9cc5c40e53884e6dec835e92d74d7de1` | 9,962 | Full response read; REVISE REQUIRED 3 / OPTIONAL 0 |
| `.insane-review/drive-oauth-20261005/native-plan-v9-D-Drive-API-frozen-privacy-1/harvest/response_harvest_20261006_064525_31657_1a834f.md` | `a735c6c203a83be79636f4e85124c09c20348f73e7882f359f3692cdc01c2494` | 13,922 | Full original-bound response read; REVISE REQUIRED 2 / OPTIONAL 1; all adopted |
| `.insane-review/drive-oauth-20261005/native-plan-v9-D-Drive-API-frozen-privacy-2/harvest/response_harvest_20261006_065525_35576_6ea3dd.md` | `3f4b0fa9aafcf0f2ce182be9e99fc09f26aa66e0bfc9bd1ca73e0b2a132037b8` | 13,183 | Full original-bound response read; REVISE REQUIRED 1 / OPTIONAL 1; stale evidence linked, fixture clarification adopted |
| `.insane-review/drive-oauth-20261005/native-plan-v9-D3-frozen-authorities/harvest/response_harvest_20261006_071525_43004_862222.md` | `ed28f68547a72b184bea7843e3ebd1e947dfe68006017dfc3027a5732f07c86d` | 14,724 | Full original-bound response read; REVISE REQUIRED 2 / OPTIONAL 0; both findings linked to existing freshness/parser contracts, B replay authority confirmed closed |
| `.insane-review/drive-oauth-20261005/native-plan-v9-D2-intake-privacy-bounded-1/harvest/response_harvest_20261006_073526_50435_1a2319.md` | `108fe1ea94f764009df457da7c8aaf5fb820a4ddef2a6963d59f0f886e592203` | 15,549 | Full response read; REVISE REQUIRED 3 / OPTIONAL 0; all three accepted and mapped to v10 D2-I1/I2/I3 |
| `.insane-review/drive-oauth-20261005/native-plan-v9-D2-intake-privacy-bounded-2/harvest/response_harvest_20261006_090157_55882_4f9a20.md` | `fca055925ad945d1d12f50cd125348f7297b32c216d24d2d79f775c87a2ce1de` | 14,489 | Full original-bound response read; GO REQUIRED 0 / OPTIONAL 1; production composition positive added to D2-I1; formal checker re-harvest pending per parent |
| `src/uls/worker.py` | `3c13828f8006b5c4ee01101e313c513c19f7fe70ca88dbce07bd44f28bc9c0f9` | 15,109 | D product change candidate: `DerivedDriveWriter` create/readback/publication validation |
| `src/uls/adapters/drive/worker.py` | `157dccadff17dcc0b65fa78fbe560506798a2bd96d8dd0bab7886d05bd9efa16` | 30,632 | D2 identity/marker readback adapter boundary plus D packet 1 candidate: strict raw `driveId` parsing and non-transient ambiguous derived-file create result |
| `src/uls/adapters/drive/google.py` | `4ba1b0929451ce84791b5ecc525e106b87233ce6f4722a64b6d18902ee0eb86f` | 3,547 | Related reader, read-only dependency |
| `src/uls/study_notes/drive.py` | `3fa1923897de52e68ef50298d4f3ef22cf75c06408aa608bd42a599cae7cdd52` | 22,765 | Stronger neighboring metadata/readback pattern, read-only reference |
| `src/uls/intake/worker.py` | `fa95cbc4c720ffb5677350c03eec663ff703c4162d4a9f27c90e0769d884d07d` | 131,637 | D2 change candidate: all public identity gates, per-invocation layout/error-projection barrier, and fresh privacy validation for new/reused staged derivatives |
| `tests/integration/test_native_runtime.py` | `63f8d651a567fd16265eb7b8c2fe449e6899d8f4d8cb85c16ccb8decec5fe939` | 17,061 | D test candidate: fake NativeWorker/runner preflight, derivative readback, no-pointer and post-dispatch response-loss/no-retry cases |
| `tests/unit/test_intake_registry.py` | `f95e21483336a0b0dffd0f91fd8ed197b75a22cccd2d56400df0930f5bb37a07` | 8,543 | D test candidate: exact privacy/capability matrix |
| `tests/unit/test_c2_drive_marker_recovery.py` | `5a812b5416c2bc7252f867b9c755a07843be7ee096b146826428dc4c21032c44` | 13,312 | Direct raw adapter parser cases for absent/null/wrong-type/string `driveId`, plus existing marker-recovery regression |
| `tests/contract/test_worker_cli.py` | `61a8e947d63522c5760172c6128173a0f7d570b68189a6ac80b8d7d25bdbeba5` | 9,726 | Existing generic transient retry/privacy regression, run unchanged in D closure |
| `tests/contract/test_mcp_runtime.py` | `ea82e73347e26348e2e0854b7ac53177370fc22835043246e624913ddbdaa2d5` | 38,954 | Existing read-only MCP boundary regression, run in D closure |
| `src/uls/orchestration/runner.py` | `3f23aea172c69ca41edf2fc6b656c5953aabbcc12fdcf713f41098e4669954cb` | 4,176 | Read-only dependency: only `ProviderUnavailableError` is transient; D adapter outcome type must not subclass it |
| `tests/integration/test_intake_worker_preview.py` | `faf5fa7f2f3b9d5a83cd4160a7f07dbcf9ed09eac7f538f63161e2761891206e` | 32,386 | D2 change/test candidate: entrypoint identity and error barrier matrices plus `_stage_derivative()` marker-reuse postcondition |
| `university-learning-system-v1.2-design-frozen.md` | `45499aa642a52d97b59995d4bd525be77b05cd7ea7944f62e7a41938336cd6fa` | 48,653 | Frozen privacy/provenance authority, unchanged |
| `university-learning-system-v1.2-implementation-spec-frozen.md` | `10ab4498946af4bbf4b3f0cd20772a2f965ea095c50ee26fc2f1682b44f0d11f` | 71,442 | Frozen implementation authority, unchanged |
| `contracts/study-behavior.md` | `987d09ec152f91e368e070c5ccbe961602a18da8b6113afd968ac965406aae1a` | 5,533 | Behavior Contract authority, unchanged |
| `scripts/review_evidence_checker_candidate/state_check.py` | `a82d0a88a2a35fe53293c433210029895a0e674d15231a2d9e1ed1a8f378ef08` | 27,911 | Active checker, read-only pin-count inspection: 17 compiled paths; `src/uls/worker.py` absent |
| `scripts/review_evidence_checker_candidate/source_pins.json` | `2e6dfae356072bb4c0228cca4ba7073d01819088b5b10b88abbb3a326eba6340` | 4,021 | Review-only inventory, not runtime authority; worker.py absent |

`src/uls/worker.py` and `src/uls/adapters/drive/worker.py` are absent from the currently active compiled 17-path pin map and review-only inventory. The adapter is already a D2 source candidate; D adds its bounded raw-parser/error-classification behavior to that same file. `src/uls/intake/worker.py` and the D2 integration/unit tests remain the separately bounded D2 candidate set. These facts do not authorize editing `state_check.py`, `source_pins.json`, or checker tests. A future proposal to pin `worker.py` is a separate exact-candidate, independent-review, human-decision gate; this plan does not authorize one. `src/uls/orchestration/runner.py` remains read-only and its existing classification is reused. D2's source scope now includes the three new intake requirements without widening into other Drive/Intake modules; any source needed outside this closure is reported before editing.

### D release gate (v8 normative)

The OAuth implementation release gate is A+B+C+D, not A+B+C only. The same owner binds D-owned test commands, actual collected/passed/failed counts, scoped lint/type results, and final SHA/size to the exact candidate source. At minimum the D regression command covers `tests/unit/test_intake_registry.py`, `tests/unit/test_c2_drive_marker_recovery.py`, `tests/integration/test_native_runtime.py`, `tests/contract/test_worker_cli.py`, and `tests/contract/test_mcp_runtime.py`; the exact A/B fake connect/check no-mutation tests are included in the combined acceptance record. D2's `tests/integration/test_intake_worker_preview.py` is a D2-owned changed candidate and required gate, not an unchanged side dependency. The overall final candidate manifest uses the sorted union of every changed A/B/C/D/D2 product and test path; the C browser evidence and each D/D2 regression result are separately bound to that same current-source snapshot. No live provider readiness, School write, ACL cleanup, or real account grant can be inferred from these fake suites.

## B — shared OAuth commit composition invariant (Native v7 D3 REQUIRED 1)

This is now a REQUIRED B acceptance condition, not optional coverage. The production OAuth-enabled Settings composition creates exactly one process `OAuthRuntimeContext`, exactly one B-owned `_OAuthHandoffLedger`, and one canonical `CredentialService`; the exact same context/ledger/service identities are wired to every OAuth Settings commit entrypoint, including `POST /P/api/v1/google-oauth/{purpose}/{flow_id}/commit`. `composition.py` owns singleton construction/registration. A owns context type/lifetime; B owns the ledger type and atomic-consume semantics. The canonical `CredentialService` is the production route target; any additional OAuth-capable `CredentialService` is allowed only when explicitly injected with the same context and ledger, so all service consumers still share one replay authority. This supersedes any v7 reading that each service privately constructs its own ledger, but preserves v7 key, expiry, capacity, tombstone and process-epoch semantics. No entrypoint or per-request factory creates a replacement instance.

An OAuth-capable `CredentialService` receives both objects explicitly. There is no implicit private `_OAuthHandoffLedger` fallback in OAuth-enabled construction. A generic service built without OAuth capability may retain current generic/SA behavior but cannot be registered as an OAuth commit entrypoint. Any additional OAuth-capable service is accepted only with the identical context and ledger objects from the canonical composition. A second service with a fresh context or private ledger is rejected during construction/registration, before it becomes reachable from an app route. Existing generic save, service-account validation, and non-OAuth callers are unchanged.

### Required composition and cross-service regression

`tests/contract/test_settings_composition.py` asserts one production composition instance for each object and exact `is` identity at the service and every commit-route dependency. It constructs a second OAuth-capable service only with explicit injection of the canonical context/ledger. A deliberately fresh private ledger/context must fail at construction/registration and leave every effect counter at zero. Existing transaction/recovery/peer-separation behavior is baseline only; OAuth handoff-ledger and service-level replay regressions are planned additions in `tests/contract/test_settings_credential_service.py`. Composition identity and cross-entrypoint replay regressions are added separately in the composition contract suite.

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
| `tests/contract/test_settings_credential_service.py` | `1eeae0b30657492c8d67a829acf41f16398075f9527c891854b6af4dff4520e2` | 38,729 | Existing transaction/recovery/peer-separation baseline; OAuth handoff-ledger and service-level replay regressions are planned additions; compiled pin, retain semantics |

---

## v7 inherited contract (v8 C/D and v10 B-2/C1 corrections take precedence)


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
| D Drive API/privacy and derived-file validation | src/uls/worker.py, src/uls/adapters/drive/worker.py, and D2-owned src/uls/intake/worker.py are change candidates; src/uls/adapters/drive/google.py, src/uls/study_notes/drive.py and exact frozen design/spec/Behavior Contract files remain unchanged dependencies | tests/integration/test_native_runtime.py, tests/unit/test_c2_drive_marker_recovery.py, tests/unit/test_intake_registry.py, and D2-owned tests/integration/test_intake_worker_preview.py; tests/contract/test_worker_cli.py and tests/contract/test_mcp_runtime.py remain required unchanged |

Related read-only dependencies는 current17 inventory의 exact SHA/size closure다: config service/errors, keyring backend, Drive binding, settings support, CAS/config/admission/journal/roles/stores/secure-file tests 및 native runtime integration. 이는 변경 후보가 아니다.

No-change files/areas: credential_admission.py, credential_roles.py, credential_stores.py, journal.py, config/_secure_file.py, mutation kernel, unrelated CLI/tests, the enumerated D/D2 paths 밖 Drive/Intake kernel, Notion, Canvas, frozen specs, active checker/source pins/inventory/tests/AGENTS/global settings, GUI4, RESEARCH, ACL, live credential/provider. Journal/admission schema 변경, generic CredentialService.save signature 변경, generic parser broadening 및 추가 third-party dependency는 금지다. Closure 밖 source가 꼭 필요해 보이면 편집 전에 정확한 path/call graph와 이유를 보고한다.

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

이 문서는 PLAN-only다. 이 동결 v10과 applicable complete-source packets에 대한 Native 및 Gemini의 독립 integrated PLAN review, original-bound response/source audit와 parent disposition, 명시적 implementation GO 뒤에만 제품/테스트를 수정한다. 현재 v9 Native packet별 결과는 이 통합 v10 acceptance를 대신하지 않는다. D2-intake packet 2는 technical GO/OPTIONAL 1이며 공식 checker re-harvest 대기를 별도 상태로 보존한다.

GO 후 동일 owner가 위 A/B/C 범위에서 의미 있는 red→green 회귀, owning test suites와 scoped static checks를 실행하고 관련 수정까지 책임진다. Fake browser 증거는 최종 source hash에 묶는다. 새 material decision 또는 closure 밖 source 필요가 나오면 확대 전 멈추고 경로·증거를 보고한다.

최종 SHA/size와 실제 검사 결과, store invariant를 기록하고 exact-current-source Native/Gemini FINAL review를 받는다. Pin delta가 있으면 별도 exact candidate/review/human application을 거친다. 실제 School 수락은 Desktop app registration, human consent, 귀속 가능한 USER-submitted 자료, 정확한 target/ACL readback, 성공 write 및 Partial 처리, authoritative Drive/Notion readback/retrieval을 관측해야 한다. 이 조건 전에는 full flow acceptance, PR, commit, push 또는 publication을 주장하지 않는다.

## 작성 경계

이번 v10 누적 갱신은 이 PLAN과 새 final source-inventory/owner-record snapshot만 포함한다. v2/v3/v4/v9, Native response/manifest/audit, 기존 inventory/task-record snapshot, Notion plan, active checker/pins, product/test source, ACL, config, provider state는 보존한다. 실제 Google/provider/School 접근이나 테스트 실행은 포함하지 않는다.
