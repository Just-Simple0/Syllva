# Drive OAuth PLAN v4 addendum — B protected transactions

단계: 완성된 PLAN 보완본이다. Native B의 REVISE4를 닫기 위한 기술 계약이며 제품 구현 GO나 checker 적용 결정은 아니다. v2/v3 및 과거 리뷰 입력은 원문·해시 그대로 유지한다. 제품·테스트·체커 소스 변경은 이 문서 작성 범위가 아니다.

## 고정 근거

- v2: `docs/plans/drive-oauth-nondeveloper-v2.md`, SHA-256 `6a1924274508ebd535052e6a93e038b1a916dc904ddda34a38ffefe1ab613b98`.
- v3: `docs/plans/drive-oauth-nondeveloper-v3.md`, SHA-256 `33e890ee72ea9565ae93ac97226cb3b09ca7cb117cf47405ec9815fcbb93a080`.
- Native B 원문: `.insane-review/drive-oauth-20261005/native-plan-v3-B-protected-transactions/response_Syllva_20261005_191151_41508_950ad2.md`, SHA-256 `d0addff363e07797618fdd786548597797217523f81a13af3d1d8c6f9197c3c3`; REVISE 4건.
- 이 v4 작성 전 보존본: `.insane-review/drive-oauth-20261005/drive-oauth-nondeveloper-v4-precompletion.md`, SHA-256 `5fe280a733c6eb1b62c4f14a78fe7e93b42fe4b0f62c3609df054f8495c87d72`.
- 최초 source/test snapshot은 `.insane-review/drive-oauth-20261005/drive-oauth-v4-source-inventory.json`, SHA-256 `84e9166755f0178721bbe7c904b68a24d7c8a1859da1db759231d86b555fd4c5`로 보존한다. 사용자 승인 후 project-only 17-pin 반영과 일치시킨 현재 snapshot은 `.insane-review/drive-oauth-20261005/drive-oauth-v4-source-inventory-current17.json`, SHA-256 `6aa727caee5719b01238297399d468f29d6cbd39c282d9605527fba6fa79e57a`다. 73개 경로를 대조했고 세 checker/context 파일의 이전 snapshot 대비 변경 외에는 차이가 없었다. 미읽은 파일까지 검토했다고 주장하지 않는다.
- 확인한 구현 경로: `CredentialService.source/effective/_separate/_verify/save/_continue/recover` (`src/uls/settings/credential_service.py`), `structural_credential/LiveReadOnlyTransport._google/ProviderChecks` (`src/uls/settings/provider_checks.py`), `GoogleCredentialPayload/CredentialResolver._diagnose_one` (`src/uls/config/credentials.py`). 현재 `_separate`는 유효 선택 peer와 canonical managed peer를 별도 읽고, Google은 service-account 형식만 structural/provider 경로가 처리한다. `save()`의 generation/source 확인은 config lock 안, stage 및 verify는 admission·role·named journal operation을 보유한 기존 흐름, config CAS는 `_continue()`의 config lock 안에서 수행한다. restart recovery는 flow registry에 의존하지 않고 journal/staged bytes를 읽는다.

## Native B 필수 보완 계약

### R1 — 잠금 소유와 recovery

v2의 provider-lock 문구는 callback 최초 code exchange에만 적용한다. 그 교환은 admission/role/config lock 밖에서 수행한다. 그 뒤 `CredentialService.save()` 및 `recover()`의 bounded 검증은 현재 서비스의 admission·role·named-operation·config lock 획득 순서와 각 lock 보유 구간을 유지한다. 특히 save의 provider verify 위치를 옮기거나 lock을 해제하지 않고, recover는 기존대로 lock 아래 live state/config 검증 후 필요한 경우 staged authorized-user를 다시 검증한다. Flow registry가 사라진 restart도 기존 journal의 operation/action 및 protected staged bytes에서 이어져야 하며 새 journal/admission schema를 요구하지 않는다.

수락 회귀는 exchange 단계의 lock 부재, save/recover의 기존 lock 순서·보유 증거, stage 뒤 crash→restart recovery에서 flow registry 없이 재검증, 성공/거절/주입 crash 경계별 active/staged/backup/config/journal raw bytes를 포함한다. stale generation, malformed staged bytes, peer/config mismatch에서는 CAS나 승격이 없어야 한다.

### R2 — 사용자의 교체 허가와 managed sink 동작 분리

기존 begin JSON key `replace`는 정확한 boolean이며 flow 내부에서는 `user_replace_authorized`로 고정한다. commit 본문은 v3 그대로 정확히 `{"generation":"<captured-generation>"}`만 받는다. OAuth handler는 typed authorization을 가진 `CredentialService.save_google_oauth(...)`를 한 번 호출한다. 기존 `CredentialService.save(role, value, generation, *, replace=False, candidate=None, fault_hook=None)` signature와 generic caller 동작은 바꾸지 않는다. `user_replace_authorized`는 사용자 동의를 뜻하고 managed sink의 replacement 여부를 뜻하지 않는다.

save 시작 시 기존 lock 아래 generation과 현재 effective source/presence 및 canonical managed active slot을 다시 판정한 뒤, journal/stage/provider save effect를 시작한다. 현재 선택 credential이 존재하지만 허가가 없으면 save·journal·stage 0회다. external file/environment 경로가 선택된 경우 허가된 교체도 external bytes를 수정하지 않는다. external source가 present이고 managed target absent면 managed enrollment와 config CAS만 허용한다. external source가 선택된 채 stale canonical managed active bytes가 있으면 그 hidden bytes를 사용자 허가만으로 덮지 않고 변경 전 fail-closed한다. unreadable configured external path는 absent로 취급하지 않는다. managed source/present slot은 허가가 있을 때만 기존 managed replacement로 연결한다. config generation·source/presence·target-state를 provider 검증 후 config CAS 직전에 다시 확인하며 불일치면 기존 transaction recovery 규칙으로 중단한다.

회귀 matrix: managed present × 허가 on/off; external present+managed absent × 허가 on/off; external present+stale managed active × 허가 on/off; external missing/unreadable; save 중 config generation/source/target 변경; enrollment/replacement 각각 config CAS 직전 crash 및 exact recovery. 모든 external file bytes는 전후 동일해야 한다. stale canonical active 선택은 위와 같이 거부로 고정한다.

### R3 — peer 집합·typed separation·fresh account 증명

Google 목적 peer 집합은 현재 effective selected peer와 canonical managed peer이며, 두 read 결과의 bytes가 정확히 같으면 하나의 view로 dedupe한다. `_separate()`는 각 view를 pre-stage에 읽고 순수하게 type/schema/client/blob/refresh-grant 분리를 검증한다. 어느 한쪽이라도 unreadable, malformed, unknown type이면 stage 전에 fail-closed한다. 모든 distinct OAuth peer의 configured-purpose client와 refresh token/blob은 incoming 및 상대 purpose와 재사용하지 않는다. OAuth account 동일성은 로컬 JSON metadata가 아니라 Google token refresh의 실제 scope와 fresh `about.user.permissionId` 결과로 입증하며 incoming account와 각 distinct authorized-user peer account를 비교한다.

SA-SA는 기존 `client_email/private_key_id` 비교를 유지한다. SA-OAuth 조합은 양쪽 각자의 type/schema를 검증하되 SA에 OAuth field를 요구하지 않는다. peer read나 parse가 실패하면 어떤 save effect도 허용하지 않는다.

### R3 부속 — save/recover aggregate transport 한도

기존 v3 candidate-only save 검증은 refresh POST 1 + `about` identity GET 1 + configured target GET 최대 8회다. 위 peer account 재검증을 더한 **authorized-user service invocation 하나의 상한은 최대 14 wire calls**다: candidate refresh/about 2회, effective 및 canonical 두 distinct OAuth peer 각각 refresh/about 최대 4회, target GET 최대 8회. bytes-identical peer는 한 번만 센다. 각 wire call은 redirect/retry 0이며 existing 30초 monotonic deadline, call별 최대 10초 socket timeout, 1 MiB 응답 상한을 하나의 supervisor-owned budget으로 공유한다. 각 child/helper가 독립 budget을 새로 만들어 상한을 우회하면 안 된다. 상한·deadline 초과 시 fail-closed다. Service account 전용 기존 경로와 callback exchange의 2-call 상한은 변하지 않는다.

`_separate()`는 매 호출에서 same in-memory service-invocation snapshot(세대, effective source 분류/presence, candidate 및 두 peer raw-byte digests, dedupe 결과)을 순수 재검증한다. network attestation은 해당 snapshot fingerprint에 결속하고 같은 invocation에서 distinct OAuth bytes마다 최대 1회만 실행한다. `_continue()`/CAS 직전 fingerprint 불일치는 attestation 무효화 및 commit 거절이며 per-helper budget reset은 금지한다. Restart recovery는 이전 process의 cache를 믿지 않고 새 recovery invocation의 한계 내에서 fresh attestation을 만든다. Permission ID·token·raw credential은 journal/log에 기록하지 않는다.

### R4 — authorized-user exact schema와 immutable single-read

authorized-user JSON의 exact top-level key set을 `{type, client_id, client_secret, refresh_token, token_uri, scopes}`로 고정한다. 값은 각각 정확한 discriminator, configured purpose의 client id/secret, 비어 있지 않은 불투명 refresh-token string, 고정 Google token URI, 중복 없는 exact role-scope string array다. whitespace/잘못된 scalar/container/누락·추가 key/중복·과다·누락 scope는 거부한다. 별도 persisted purpose metadata는 두지 않는다. purpose는 role/config binding으로 결정하며 JSON metadata는 grant·account 증거로 신뢰하지 않는다. server exchange/refresh가 반환한 실제 scope와 fresh account identity가 유일한 Google authorization 증명이다.

`CredentialResolver` protected file은 기존 보안 reader로 정확히 한 번 읽는다. authorized-user용 typed snapshot은 `scopes`를 immutable canonical tuple로 normalize하고 입력/중첩 container 복사 및 deep-freeze를 거친다. google-auth/public adapter에 넘길 때만 필요한 독립 mutable SDK input을 만든다. service-account parsing/allowlist와 기존 호환은 소급 변경하지 않는다.

수락 회귀: exact key/type/normal form positive; unknown/missing/nested wrong type/duplicate or wrong scopes/client/token URI negative; 실제 server-granted scope 누락·초과·타 purpose는 fail-closed; resolver read count 1; resolve 뒤 원본 mapping/list 변경이 snapshot을 바꾸지 않음; SA-SA 및 SA-OAuth 기존 분리, OAuth-OAuth blob/client/grant/account 중복, selected external+latent canonical managed peer, malformed/unreadable peer no-effect; aggregate 14/15 call 경계·누적 helper-call과 CAS fingerprint invalidation.

## 파일 경계 및 후속 gate

Native B의 v2 scope를 다음 실제 변경 지점으로 한정한다: `src/uls/settings/credential_service.py`의 `source/effective/_separate/_verify/save_google_oauth/_continue/recover`; `src/uls/settings/provider_checks.py`의 OAuth typed verification 및 invocation budget 전달. `src/uls/config/credentials.py`의 authorized-user typed payload/single-read는 A의 설정·runtime 경계에서만 소유하며, B는 그 공개 typed snapshot을 사용한다. B 회귀는 `tests/contract/test_settings_credential_service.py`와 `tests/contract/test_settings_provider_checks.py`다. A의 resolver/runtime 회귀는 `tests/unit/test_credential_resolver_file_source.py`와 `tests/unit/test_runtime_google_credentials.py`에 둔다. `google_oauth.py`의 begin `replace`→flow `user_replace_authorized` 전달은 A 소유 경로이며 HTTP body나 commit body 규약은 바꾸지 않는다.

`credential_admission.py`, `journal.py`, `credential_stores.py`, `config/_secure_file.py` 및 기존 lock/admission/journal schema는 변경하지 않는 dependency다. 직접 수정 필요가 새로 입증되면 구현하지 말고 정확 경로·호출 이유·새 리뷰 gate를 먼저 제출한다. 사용자가 이 프로젝트 전용 17-pin/four-file checker bundle을 승인했고 총괄이 적용했다. 이 적용은 기존 6 synthetic source test bytes를 바꾸지 않았으며 OAuth product source pins, ACL removal, commit 권한은 포함하지 않는다. OAuth 구현 source가 현재 exact pins 범위를 벗어나면 별도 OAuth candidate/review와 인간 exact-scope 적용 결정을 기다린다. active checker 적용 자체는 OAuth product change approval을 대체하지 않는다. 이 문서는 Native/Gemini 통합 PLAN 수락이나 product implementation GO가 아니다.

## 구체 service API와 잠금·CAS 경로

현재 호출 경로에 맞춘 전용 API는 다음 형태다. 타입은 immutable하고 secret/token을 보유하지 않는다.

```python
@dataclass(frozen=True)
class GoogleOAuthCommitAuthorization:
    role_slug: Literal["google-mcp", "google-worker"]
    flow_id: str
    flow_epoch: int
    config_generation: str
    client_id: str
    granted_scopes: tuple[str, ...]       # exchange에서 받은 실제 서버 scope
    account_permission_id: str            # callback fresh attestation; memory only
    user_replace_authorized: bool

CredentialService.save_google_oauth(
    role, candidate_bytes, authorization, *, fault_hook=None
)
```

`GoogleOAuthService.commit`는 기존 CSRF/활성 session/`MutationBarrier` 아래 flow lock에서 purpose·role·client·generation·epoch·expiry·`awaiting_commit`과 authorization을 확인하고 candidate를 원자적으로 한 번 소비한 다음 위 method를 worker thread에서 한 번 호출한다. 실제 flow-owned type/shape 검증은 `credential_service.py`에서 다시 한다. Commit은 HTTP에서 credential JSON이나 사용자 지정 role/client/scope를 받지 않는다. `recover(operation_id, action)` signature는 그대로고 flow registry를 참조하지 않는다.

일반 `CredentialService.save()`와 기존 JSON upload/action route는 Google service-account JSON만 계속 받는다. 변경된 global structural parser를 이용해 browser가 arbitrary `authorized_user` JSON을 저장하거나 `user_replace_authorized`를 생략하는 경로를 만들지 않는다. OAuth authorized-user validation과 저장은 내부 flow commit이 typed `save_google_oauth`로 진입할 때만 허용하며, 기존 CLI/service-account callers는 원래 경로를 유지한다.

기존 lock 구간은 source 그대로 유지한다: (1) 최초 code exchange/callback의 bounded Google token POST와 account GET은 service admission/role/config lock 밖이다; callback은 `save`를 호출하지 않는다. (2) `save`는 admission → role lock을 얻고, config lock 안에서 generation·현재 source/presence·managed state 및 peer snapshot을 캡처/확인하고 동의 gate를 먼저 적용한다. gate 통과 전에는 admission marker publish, journal create, stage 등 operation effect가 0회다. 그 뒤 journal operation lock 아래 stage와 bounded verification을 진행한다. 기존 save verification 시점에는 admission/role/operation lock은 유지되고 config lock은 이미 풀려 있다. (3) 실패 cleanup은 기존 config lock으로 재진입한다. 성공 후 `_continue`는 role/operation lock을 유지하며 config lock을 얻어 snapshot/CAS를 다시 확인한다. (4) recovery의 live check와 staged OAuth 재검증은 admission → role → named operation → config lock을 함께 보유하는 기존 구간에서 수행한다. 그 nested lock context가 끝난 뒤 admission은 유지되고, role + named operation을 다시 획득한 상태로 `_continue`를 호출하며 `_continue`가 config lock/CAS를 얻는다. 두 구간 사이 role/operation lock이 계속 잡혀 있다고 주장하지 않는다. OAuth 변경은 이 순서와 획득/해제 구간을 옮기지 않는다.

`user_replace_authorized`와 sink action은 독립 판정이다. 현재 effective credential이 있고 허가가 false면 journal/stage/provider/save/config effect 0회다. 허가 true여도 external file/environment를 수정하거나 삭제하지 않는다. external source가 현재 선택되고 present이며 managed slot이 absent면 managed enrollment와 config CAS만 수행한다. external source가 선택된 채 hidden canonical managed active bytes가 있으면 fail-closed한다. 현재 managed active slot이면 동의가 있을 때 기존 replacement journal action을 고른다. journal `action_kind`는 managed sink의 pre-state로 정하고 user consent flag를 재해석하지 않는다. Operation은 승인 검사를 통과한 뒤에만 생성되므로, crash 후 `recover()`는 기존 exact operation binding·candidate hash·generation·staged bytes를 durable continuation 근거로 삼고 flow registry 없이 fresh provider/source 검증을 다시 한다. journal/admission schema에 동의 필드나 OAuth metadata를 추가하지 않는다.

재사용 가능한 검증 문맥은 한 `save_google_oauth` 또는 한 `recover` 호출에만 산다. `GoogleVerificationContext`는 monotonic deadline 30초, hard wire-call count 14, authorized-user payload digest당 fresh scope/account attestation 최대 1개, immutable source snapshot fingerprint를 소유한다. `ProviderChecks.check(..., *, google_context=None)`의 기본값은 기존 generic behavior(기존 8-call `TransportCheckBudget`) 그대로다. OAuth 전용 bounded verifier는 하나의 killable child invocation으로 candidate 및 distinct peer attestation과 configured target GET을 수행한다. `_separate` 재호출은 캡처된 snapshot에서 순수 schema/identity/digest 분리만 하며 네트워크나 새 budget을 만들지 않는다. `_continue`/config CAS 전 fingerprint가 바뀌면 attestation을 재사용하지 않고 commit을 거부한다. recover는 process 재시작 후 새 context와 fresh attestation을 만든다.

Aggregate budget은 callback exchange와 분리한다. Callback은 code token POST 1회 + identity GET 1회 이내다. 각 authorized-user save/recover provider 검증은 candidate refresh POST+`about` GET 2회, effective selected peer와 canonical managed peer 각각 최대 2회(동일 raw bytes이면 정확 dedupe), configured target GET 최대 8회로 합계 최대 14 wire calls다. Verified helper는 redirect/retry 0, response/stdout frame 각 1 MiB 이하, 동일 30초 monotonic deadline·요청당 최대 10초 socket timeout을 지킨다. Budget은 `_verify`, `_separate`, `ProviderChecks` helper 재호출에서 재설정할 수 없다. Helper는 fixed operation·endpoint만 수행하고 stdout은 streaming cap 초과 즉시 child 종료/reap 후 frame을 버린다. v3의 save/provider verification 총 10-call 문구는 이 authorized-user transaction에 한해 v4의 14-call 상한으로 대체한다; callback의 2-call 한도와 기존 service-account 경로는 그대로다.

## Google snapshot·peer·typed payload 조건

잠금 아래 snapshot에는 current config generation, effective selected source kind/presence 및 secure read에서 얻은 exact candidate/peer raw bytes의 in-memory digest, canonical managed active state/digest, role/client/scope binding이 들어간다. `Path.read_bytes()`로 protected source를 다시 열지 않고 기존 protected reader의 identity/NOFOLLOW/mode/size 검증을 유지한다. Snapshot 자체는 memory-only이며 raw bytes/token/permission ID/external filesystem path/project metadata를 journal/log/API에 기록하지 않는다. 기존 journal의 symbolic role/store locator binding은 schema를 그대로 유지한다.

Google peer set은 기존 `_separate`가 보는 (a) 설정에서 선택된 effective peer와 (b) canonical managed peer 그대로다. 두 raw byte strings가 완전히 같으면 하나로 dedupe하고, 다르면 각각 독립 검증한다. Peer unavailable/unreadable/malformed/unknown type는 첫 effect 전에 거절한다. Authorized-user JSON은 top-level key가 정확히 `{type, client_id, client_secret, refresh_token, token_uri, scopes}`이며 type/discriminator, scalar type, non-empty 값, fixed token URI, purpose-configured client, 정규화된 중복 없는 exact scope array를 엄격 검증한다. 입력 mapping과 nested scope list는 immutable canonical tuple snapshot으로 복사한다. JSON에 account/project/purpose metadata를 넣거나 이를 grant 증거로 신뢰하지 않는다. Actual server-granted scope 및 fresh Drive `about.user.permissionId`만 authorization/account 증거다.

각 OAuth peer는 해당 purpose의 configured client와 fresh refresh/about를 사용한다. Candidate/peer는 same account permission ID여야 하지만 purpose별 OAuth client·refresh token·credential bytes는 다르고 서로 재사용할 수 없다. SA-SA에서는 현재 `client_email/private_key_id` separation을 그대로 유지한다. SA-OAuth는 각자의 type/schema를 검증하되 service account에 OAuth 필드를 요구하지 않는다. Managed slot이 service account인 것과 OAuth candidate를 managed slot에 replace하는 동의는 구별한다. Peer account refresh는 디스크 metadata를 읽지 않고 위 shared child/budget 안에서만 수행한다.

Runtime `google.auth` public credential adapter의 refresh는 예외 없이 같은 fixed helper 경계를 쓴다. Code exchange/저장 검증 child는 access token을 반환하지 않는다; runtime refresh child만 실제 scope/client/account와 expiry를 확인한 access token·expiry·binding을 streaming-capped private pipe로 부모 in-memory adapter에 반환할 수 있다. Parent는 access token을 public SDK `apply()`가 authorization header를 만들 때에만 메모리에서 사용한다. Browser/API/log/journal/disk 노출은 금지다. `google_worker_service`는 별도 unbounded `about().execute()`를 호출하지 않고 같은 검증 helper의 fresh account attestation을 재사용한다. SA runtime은 기존 동작을 유지한다.

## 서버 API, callback gate와 사용자 범위 수락

v2/v3의 exact endpoints/body/state는 그대로다: `POST .../{purpose}/begin`은 exact `{generation, replace}`; session-bound `GET .../{purpose}/{flow_id}`는 read-only poll; `POST .../{flow_id}/cancel`은 exact empty JSON; `/P/oauth/google/callback`은 좁은 callback GET; `/P/oauth/google/result`은 fixed query-free result page; **유일한 저장 요청**은 same-origin/current-session/CSRF/explicit activity/`MutationBarrier`를 통과하는 `POST .../{flow_id}/commit` with exact `{"generation":"<captured-generation>"}`. Callback 및 GET poll은 save/provider check 0회다. 동시 duplicate commit에서 total service save 진입은 정확 1회다. Callback query≤8192 bytes, state 정확 43 ASCII, code≤4096 decoded bytes, error≤64 ASCII, duplicate/unknown key 금지. State/verifier는 서로 독립 32-byte CSPRNG, S256 PKCE, authorization query allowlist, offline consent, one-use atomic state다. Google public Desktop OAuth client의 실제 client/project 설정이 없으면 flow는 `OAUTH_APP_NOT_READY`; ID나 앱 등록을 지어내지 않는다.

SecurityBoundary의 좁은 callback/result GET exception은 기존 Origin/session/CSRF mutation rule을 대체하지 않는다. Header tuple은 현재 `_FETCH_METADATA`의 네 항목 (`Sec-Fetch-Site`, `Sec-Fetch-Mode`, `Sec-Fetch-Dest`, `Sec-Fetch-User`)이다. 네 항목이 전부 absent인 경우 callback은 exact host/loopback peer/path, bounded query, live one-use state와 session epoch 검사를 계속 요구하고, result는 exact host/peer/result path/query-empty fixed no-data page만 허용하며 flow state나 cookie를 요구하지 않는다. 하나라도 전달되면 네 항목이 전부 단일 값으로 있어야 하며 callback은 cross-site/navigate/document, result는 cross-site 또는 same-origin/navigate/document만 허용한다. Partial/duplicate/wrong mode/dest/site, subresource/iframe는 거부한다. 네 항목 기준은 earlier disposition JSON의 “three” shorthand보다 나중에 주어진 명시 사용자 계약을 따른 구현 메모이며, 이전 disposition 원본을 고치지 않는다.

Purpose scope는 MCP=`drive.readonly`, WORKER=`drive` 고정이며 `drive.file`/Picker를 추가하지 않는다. OAuth consent는 Drive 전체 범위다. Syllva의 configured School root/course IDs는 application-side target allowlist일 뿐 Google permission을 폴더 전용으로 축소하지 않는다. 두 Desktop clients는 서로 다른 IDs뿐 아니라 서로 다른 Google Cloud projects에 등록되어야 한다. Google의 project-wide token revoke가 그 project의 모든 client tokens를 무효화하므로 별도 project 없이 독립 철회를 약속하지 않는다([Google native-app OAuth documentation](https://developers.google.com/identity/protocols/oauth2/native-app)). 이번 범위는 remote revoke API가 아니라 local disconnect와 Google 계정의 앱 권한 관리 안내만 포함한다. Google public client registration과 실제 client ID는 현재 부재라 배포 prerequisite다. Scope consent/refresh response의 actual granted scope를 검사하며 local `scopes`만 신뢰하지 않는다.

기존 test service accounts와 현재 exact ACL을 보존한다. `owner_only`/share ACL 완화, automatic ACL deletion, Cloud service-account identity/key deletion은 제외한다. OAuth 연결 완료와 실제 user account 연결이 확인된 뒤 exact target ACL을 fresh readback하고, 시험용 access 제거는 별도 인간 승인 후에만 실행한다. Worker는 credential 연결만으로 활성화하지 않는다. School/live-provider write 흐름과 conditional PR/commit 조건은 이 PLAN의 수락 근거가 아니다.

## 정확한 파일 closure 및 acceptance

현재 경로·SHA-256·byte size는 위 source inventory에 고정한다. 계획된 owned closure는 네 묶음이다.

| 묶음 | 수정 후보 / 새 파일 | 관련 tests |
|---|---|---|
| A protocol/runtime/config/HTTP | 새 `src/uls/settings/google_oauth.py`; `src/uls/settings/app.py`, `security.py`, `composition.py`, `launcher.py`, `src/uls/config/schema.py`, `loader.py`, `validation.py`, `credentials.py`, `src/uls/runtime.py`, `config.example.yaml` | 새 `tests/contract/test_settings_google_oauth.py`; 기존 `test_settings_http.py`, `test_settings_credential_http.py`, `test_settings_composition.py`, `test_config_loader_credentials.py`, `test_runtime_google_credentials.py`, `test_credential_resolver.py`, `test_credential_resolver_file_source.py`, `test_settings_launcher.py` |
| B protected transaction/CAS | `src/uls/settings/credential_service.py`, `provider_checks.py` | `tests/contract/test_settings_credential_service.py`, `test_settings_provider_checks.py` |
| C browser/UI | `src/uls/settings/status.py`, `static/app.js`, `static/index.html`, `static/styles.css` | `tests/contract/test_settings_ui.py`, `settings_ui_harness.cjs` |
| D unchanged Drive API/privacy | `worker.py`, `adapters/drive/google.py`, `adapters/drive/worker.py`, `study_notes/drive.py`, `intake/worker.py`, frozen design/spec/Behavior Contract sources | `tests/integration/test_intake_worker_preview.py`, `tests/contract/test_worker_cli.py`, `test_mcp_runtime.py`은 regression-only |

추가 read-only integration dependencies/regressions는 인벤토리에 SHA·크기로 기록한다: `src/uls/settings/config_service.py`, `src/uls/config/errors.py`, `_keyring_backend.py`, `src/uls/adapters/drive/binding.py`; `tests/contract/_settings_support.py`, `test_settings_cas.py`, `test_settings_config.py`, `test_settings_credential_admission.py`, `test_settings_credential_journal.py`, `test_settings_credential_roles.py`, `test_settings_credential_stores.py`, `test_settings_journal.py`, `tests/unit/test_secure_file.py`, `tests/integration/test_native_runtime.py`. 이들은 current lock/store/security/recovery/runtime compatibility 맥락과 회귀 확인용이며 수정 후보가 아니다.

`credential_admission.py`, `credential_roles.py`, `credential_stores.py`, `journal.py`, `config/_secure_file.py`, `config/mutation.py`, CLI source/tests, Drive/Intake implementation and frozen specs outside the listed A runtime integration, Notion/Canvas, checker code/tests/inventory/pins/global settings, GUI4, RESEARCH, and ACL are no-change boundaries. D는 complete contextual review와 regression only이며 API/kernel/privacy 변경 또는 whole School completion claim이 아니다. 새 OAuth module 외 새 product module/dependency는 추가하지 않는다. New test file은 표에 적힌 protocol contract 하나만이다. Source pin/HOLD disposition은 implementation 이후 exact candidate와 별도 review/human decision 문제다; 이 plan/inventory는 active checker나 six-pin private candidate를 수정·적용하지 않는다.

필수 meaningful acceptance: (1) B-1..4의 각 positive/negative matrix를 service level과 authenticated POST route에서 검증하고 no-effect 경계에서는 journal/admission/staged/config/external-source bytes를 비교한다. (2) exact peer effective/canonical pair, bytes-identical dedupe, malformed/unreadable/unknown, SA-SA·SA-OAuth·OAuth-OAuth, server-fresh scope/account, 14-call pass / 15th-call fail, 30초 누적 deadline 및 repeated `_separate`가 budget을 초기화하지 않음을 fake child로 검사한다. (3) existing generic `save()` signature, CLI and service-account checks, recovery/action projection, launcher/session/mutation behavior는 동일하다. Crash-after-stage 후 flow registry 없이 `recover()`가 fresh config/source/peer/provider evidence를 재검증하여 완료하거나 fail-closed한다; 모든 before/after stores/config/journal bytes를 확인한다. (4) Callback/poll/cancel/commit, CSRF/duplicate/replay/session replacement, Fetch Metadata redirect chain/fully-absent/partial/duplicate/subresource matrix, client missing/malformed, API redaction은 fake HTTP/browser로 검사한다. (5) worker disabled, no real credential/provider/School/`.env`/Keychain은 유지한다. (6) A/B/C/D complete-source Native/Gemini PLAN/FINAL gates, 필요한 실제 browser evidence와 current source hashes, active checker의 exact candidate/review/human decision은 별도로 충족해야 한다.

## 수행 근거 및 남은 전제

작성자는 `gpt-6-luna/max`를 유지한다. OAuth protocol/security/credential transaction이 단일 change surface에서 교차하므로 기존 owner가 설계부터 implementation, 회귀 수정, 최종 source 수락까지 이어 맡는 선택이다. 이 문서 작성은 plan과 file inventory만 수행했다. Current17 inventory SHA는 현재 bytes의 binding이고 전체 source 검토/외부 Native·Gemini GO/통합 GO/제품 승인이 아니다. Desktop OAuth app 등록/client IDs, human login/consent, actual School submission/readback/retrieval, ACL removal 승인, **OAuth product pin** update/human apply는 각각 여전히 별도 acceptance dependency다. Google desktop app code exchange의 offline access는 refresh token 발급에 필요하지만, 실제 refresh token·granted scope는 runtime response로 검증한다([Google OAuth web-server flow](https://developers.google.com/identity/protocols/oauth2/web-server?authuser=19)).
