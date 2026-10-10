# Google Drive OAuth — 비개발자 연결 계약과 구현 계획

**단계:** PLAN 초안 제출. 제품·테스트 소스는 아직 변경하지 않았다. Native/Gemini PLAN 리뷰와 총괄의 통합 GO 뒤에만 구현한다.

## 승인 범위와 현재 경계

사용자 결정은 Google Drive 연결의 기본 경로를 사용자 OAuth로 바꾸는 것이다. 이 결정은 Notion이나 Canvas의 인증 방식을 바꾸라는 뜻이 아니다. 기존 Drive `READONLY`와 `WORKER` 역할은 계속 분리하고, frozen v1.2의 읽기 전용 MCP 계약과 USER 승인·`Partial` 규칙을 그대로 지킨다.

현재 코드는 Google 자격 증명을 `GOOGLE_MCP_CREDENTIALS_FILE`과 `GOOGLE_WORKER_CREDENTIALS_FILE`로 분리하고, 각 파일을 안전하게 한 번 읽어 `GoogleCredentialPayload`로 전달한다. Runtime은 MCP에 `drive.readonly`, Worker에 `drive`를 사용한다. Settings의 Google 업로드 검증은 현재 service-account JSON만 허용한다. Local Settings는 `127.0.0.1` 임시 포트에 단일 사용자 세션으로 열리고 브라우저를 실행할 수 있다. 이를 OAuth 인증 흐름의 진입점으로 재사용하되 현재 CSRF, 세션, 잠금, journal, config generation CAS 계약을 유지한다.

현재 소스에는 Drive Desktop OAuth client 설정이 없다. 총괄의 비밀값을 노출하지 않는 준비 점검은 Remote MCP용 Web client만 준비되어 있음을 기록했다. 그 client와 `openid email` broker token은 Drive 권한을 주지 않으므로 재사용하지 않는다. Desktop app 등록과 client ID가 준비되지 않으면 연결 상태는 `OAuth 앱 설정 대기`로 남고 로그인 버튼은 동작하지 않는다. 일반 사용자에게 Google Cloud Console 설정이나 client ID/secret 입력을 요구하지 않는다.

## 제안 계약

### 사용자 흐름

1. 사용자가 Local Settings에서 목적을 확인하고 `Google Drive 읽기 전용 연결` 또는 `Google Drive 작업 연결`을 명시적으로 선택한다. 페이지 열기만으로 OAuth나 provider 요청을 시작하지 않는다.
2. 설정된 Desktop OAuth app이 없으면 일반 문구와 관리자에게 요청할 다음 행동을 표시한다. raw client ID, provider 오류 본문, URL, credential은 보여주지 않는다.
3. 사용자가 연결을 누르면 앱은 OS 기본 브라우저에서 Google의 고정 authorization endpoint를 연다. 사용자는 계정을 선택하고 해당 목적에 필요한 권한을 직접 동의한다. callback은 현재 Settings 프로세스의 정확한 loopback 주소로 돌아온다.
4. 서버는 callback을 검증하고 Google에서 grant를 확인한 뒤 Drive의 현재 계정 신원과 허용 scope를 다시 읽는다. 일치하면 기존 CredentialService의 정상 journal/staging/검증/config-CAS 경로로 저장한다. 저장이 끝나기 전에는 기존 설정과 active credential이 유지된다.
5. Settings는 저장된 자격 증명을 새 카드 상태로 다시 읽어 보여 준다. READONLY 연결 성공은 Worker 연결·활성화나 전체 School 흐름 통과를 뜻하지 않는다.

사용자는 취소·브라우저 종료·만료·세션 재시작 때 기존 credential이 바뀌지 않았음을 확인하고 다시 시작할 수 있어야 한다. 이미 다른 credential이 있으면 OAuth grant를 검증하기 전까지 기존 credential을 교체하지 않는다. 외부 service-account 파일을 쓰는 기존 설정은 자동 삭제·수정하지 않는다. OAuth로 전환할 때는 기존 file을 그대로 두고, 새 managed credential을 검증한 뒤 generation CAS로 참조만 전환하는 journalled replacement를 제안한다. 충돌·검증 실패·중단은 기존 참조와 저장 bytes를 보존해야 한다.

### OAuth 프로토콜 및 구성

- 고정된 Google authorization, token, revoke endpoint만 코드가 소유한다. UI, config, CLI 요청은 endpoint, redirect, scope를 선택하거나 덮어쓸 수 없다.
- 흐름은 system browser, OS가 할당한 `127.0.0.1` loopback port, Authorization Code, S256 PKCE, 고엔트로피 일회용 `state`를 사용한다. Flow state는 메모리에서만 유지하며 짧은 만료시간, exact callback host/path, loopback peer, 현재 Settings session, role, purpose, config generation, client/project binding, redirect URI, requested scopes에 결속한다. 브라우저 거절·Cancel·만료·process restart·callback replay는 해당 flow를 폐기한다.
- `state`/PKCE 검증 전에 code를 교환하지 않는다. 중복 query key, 잘못된 host/path/source, state mismatch, 잘못된 code/error 조합, replay, 만료, redirect 변형은 fail closed 한다. callback은 토큰·code를 화면/API/로그에 반환하지 않고 즉시 query 없는 같은-origin 화면으로 redirect한다. callback 응답은 `no-store`와 `no-referrer`로 제한하고 OAuth 경로 access log를 남기지 않는다.
- Desktop OAuth 설정은 비밀이 아닌 배포자 설정으로 주입한다. 제안 설정 키는 `google_oauth.mcp.{client_id,cloud_project_id}`와 `google_oauth.worker.{client_id,cloud_project_id}`다. 클라이언트 유형은 코드에서 Desktop/public으로 고정하고 브라우저가 보낸 client ID를 신뢰하지 않는다. remote Web client ID는 별도 namespace다. 실제 ID와 프로젝트 등록은 아직 없다고 가정하며, 없으면 production login은 pending이다. 사용자에게 OAuth app 등록을 떠넘기지 않는다.
- 사용자는 같은 Google 계정을 두 목적에 쓸 수 있지만, 앱은 role별로 별도 authorization grant와 저장 payload를 요구한다. 하나의 token/grant를 두 역할에서 공유하지 않는다. runtime의 configured client ID, payload client ID, 실제 grant scope, Drive `about.user(permissionId,emailAddress)` readback을 role에 묶고 모두 일치하지 않으면 저장/사용을 거절한다. account email은 화면의 사용자가 확인하는 데만 쓰고, 로그·journal·state에는 permission ID 및 계정의 salted/HMAC 식별자만 둔다.

### 목적별 scope 및 Google 프로젝트

- MCP/READONLY의 현재 고정 `drive.readonly`를 보존한다. 권한 추가나 Worker payload 재사용은 금지한다. 더 좁은 scope로 바꾸는 일은 현재 PLAN 범위에서 결정하지 않는다.
- WORKER는 우선 후보로 `drive.file`의 정확한 Drive API coverage를 검증한다. 기존 static folder IDs 아래의 자료 목록, 기존 파일 metadata/download, folder marker, create, move, fresh readback 각각을 fake provider 계약과 Google 공식 scope 문서로 대조한다. `drive.file`이나 Picker에서 선택한 폴더가 기존 폴더의 전체/향후 하위 트리에 권한을 준다고 가정하지 않는다. Picker가 필요하면 그 선택이 기존 School 자료와 이후 업로드를 실제로 포괄하는지 별도 수락한다.
- `drive.file`로 필요한 기존 계층을 안전하게 처리할 수 없으면 실패를 `Partial`/미설정으로 표시하고 더 넓은 권한으로 자동 승격하지 않는다. 대안은 (a) 명시적인 Picker 기반 재구성 또는 (b) 정확한 API 작업과 Google restricted-scope/배포 요구를 설명한 뒤 사용자·총괄이 선택하는 더 넓은 Worker scope다. `drive` scope 선택은 별도 명시 결정과 Native/Gemini 검토 및 배포 readiness 확인이 필요하다. MCP scope는 그 선택에 따라 넓어지지 않는다.
- Google revoke의 프로젝트 경계를 반영한다. 같은 Cloud project에 등록된 여러 OAuth client의 grant가 함께 무효화될 수 있으므로, 기본 배포 계약은 READONLY와 WORKER를 각각 다른 client ID와 다른 Google Cloud project에 등록하는 것이다. Remote MCP Web OAuth project와도 revoke 영향을 분리한다. 실제 project 분리를 확인할 수 없으면 `Google에서 철회` 기능은 비활성으로 두고 `이 컴퓨터에서 연결 해제`만 제공한다. 같은 프로젝트를 쓰려면 revoke 시 영향을 받는 모든 peer grant를 stale로 표시하고 재인증을 요구한다는 사실을 동의 전에 고지하는 별도 운영 선택이 필요하다.

### 저장, 갱신, 복구 및 기존 경로

- OAuth code/access token은 저장하지 않는다. 필요한 offline refresh grant만 검증된 role credential payload로 만들어 현재 `CredentialResolver`의 single-read/`read_secure_file` 경계와 immutable `GoogleCredentialPayload`를 통해 runtime에 전달한다. JSON은 허용된 정확한 service-account 또는 authorized-user 형태만 받고 token URI는 고정 endpoint와 일치해야 한다. 임의 URL·scope·추가 credential source를 허용하지 않는다. client/refresh data는 `repr=False`; API, log, error, journal, review output에서 비밀값을 제외한다.
- OAuth가 service-account JSON path를 우회하는 새 저장소를 만들지 않는다. 기존 role-specific 보호 저장소와 CredentialService admission, staging/backup, provider verification, journaled config CAS를 사용한다. callback 검증 중에는 config/store lock을 잡고 기다리지 않는다. 저장 직전 현재 role binding, peer role, generation, client/project/scope 및 기존 active bytes를 재확인한다. CAS 실패는 새 grant를 저장하지 않고 기존 credential을 보존한다.
- 자격 증명 만료 뒤에는 검증된 refresh grant로만 access token을 갱신한다. Refresh 거절, account/client/scope mismatch, provider timeout은 `재연결 필요`/중립 실패로 처리한다. service-account 경로로 자동 fallback하지 않는다. refresh token이 새 발급 응답에 없으면 이전 grant를 유지하고 새 연결은 저장 실패로 처리한다.
- `연결 해제`는 local store/config에서만 제거하고 provider revoke 완료로 표현하지 않는다. 별도 `Google access 철회`는 사용자 확인 후 고정 revoke endpoint에 요청한다. 원격 revoke 성공 후 local CAS가 실패하거나 응답이 모호하면 journal을 보존하고 peer/client 영향과 재인증 필요를 표시한다. Provider/프로젝트 분리가 검증되지 않으면 실제 revoke를 수행하지 않는다. 재시도는 현재 grant ID와 project binding을 다시 확인하는 journal recovery를 거친다.
- 기존 service-account JSON은 고급 수동 경로로 유지한다. 기존 외부 file, service-account 값, `credential_set` CLI, legacy MCP read-only behavior를 자동 migration/삭제/덮어쓰기하지 않는다. 사용자 UI에서는 기본 Google browser 연결과 별도 고급 service-account 경로를 구분한다. 기존 service-account 파일은 동일 protected read path로 계속 읽고, OAuth-only metadata 검사를 강제하지 않는다.
- Local Settings의 credential mutation은 현재 macOS 전용이다. 이 작업에서 Windows 또는 unsupported platform storage를 추가 지원한다고 약속하지 않는다.

## School 실제 권한 계약과 acceptance blocker

OAuth 계정으로 전환해 `ownedByMe=true`가 되더라도 현재 worker의 소유/비공개 검사는 별개다. `require_private_ownership`와 Drive worker는 permission readback이 불충분하거나 `owner_only != true`, shared-drive, broad sharing, edit/move capability 부재이면 거절한다. 총괄의 읽기 전용 School 점검에서는 대상 Drive 자료가 소유자 외 두 계정에도 공유되어 `owner_only=false`였다. 이 값이나 실제 ACL은 이 PLAN/구현에서 바꾸지 않는다.

따라서 School write/intake acceptance는 **blocked**다. 진행 가능한 선택은 (1) 인간이 승인한 별도 sole-owner private workspace로 필요한 자료를 복사·등록하고 source provenance를 다시 검증하거나 (2) 특정 협업 권한을 허용하려면 exact permission attribution, allowed principal 계약, 변경·누락·추가 권한의 fail-closed 규칙을 만들고 frozen privacy spec revision을 제안해 별도 리뷰·인간 승인을 받는 것이다. 이 계획은 현재 2개 permission을 인정하거나 share ACL을 자동 완화하지 않는다. 전체 실제 자료 흐름을 통과했다고 기록하려면 privacy blocker를 먼저 닫아야 한다.

OAuth 연결만으로 전체 School 흐름이 성공했다고 판정하지 않는다. 현재 운영 기록에 있는 별도 Notion SDK readback mismatch도 선행 blocker다. Notion 실제 `parent.database_id`/`database_parent.page_id` 및 배열형 `status.groups` 보정은 이미 승인된 School readiness 후속 코드수정으로 분류하되 Drive OAuth diff에는 넣지 않는다. Notion REST 공개 연결은 client_secret 교환을 쓰고, 공식 hosted Notion MCP의 OAuth2+PKCE/DCR은 별도 MCP lane이다. 그 MCP token을 REST adapter에 넣지 않는다. Canvas는 기존 PAT setup 흐름과 범위를 유지하며 OAuth 방식 전환은 승인되지 않았다. 자세한 비개발자 옵션은 총괄 문서 `docs/plans/provider-connection-nondeveloper-scope-20261005.md`가 담당한다.

전체 School live acceptance에는 별도로 (1) Google app registration과 두 purpose project/client 준비, (2) 위 Drive privacy 선택, (3) Notion SDK 수정 및 실제 readback, (4) 명시적 USER 제출/귀속, (5) worker enable 여부에 대한 인간의 별도 선택, (6) 실제 Drive 원본·복사·marker·metadata/readback, Notion 결과, retrieval chunk/capability 검증을 요구한다. `Partial`을 `Ready`로 올리지 않고 AI가 USER submit/approval을 만들지 않는다.

## 리뷰 단위, 파일 범위, 수락 검사

Native/Gemini에는 **두 개의 bounded full-source packet**으로 제출한다. A는 OAuth protocol, purpose binding, safe credential read, protected journal/CAS, legacy compatibility와 Local Settings/browser flow. B는 Google Drive scopes/Picker/access capability, owner/privacy checks, School actual-readback preconditions. Shared source는 각 packet에 full file로 포함하고 어떠한 핵심 파일도 토큰 예산에 맞춰 자르지 않는다. 두 packet과 교차 계약의 최종 integration GO가 있기 전 제품·테스트 소스를 수정하지 않는다. exact source path/SHA/byte inventory와 packet membership은 `.insane-review/drive-oauth-20261005/source-scope.json`에 기록한다.

필수 fake-only 회귀는 다음을 포함한다.

- callback state/PKCE/verifier, exact loopback host/path/source, open redirect, duplicate query, replay, expiry, cancel, app restart, browser launch failure, session/config generation race.
- token/code/URL/error-body 비노출; token endpoint redirect/host/scope 조작 거절; timeout/denial/refresh failure에 기존 active/config/store bytes 불변.
- account permissionId, configured client/project, actual grant scope 재검증; role-specific credential/client/project separation; same-account separate grant 허용; cross-purpose token/grant reuse 차단; project-wide revoke peer impact.
- 정상 legacy service-account import/runtime과 MCP `drive.readonly` regression; OAuth authorized-user payload single-read와 protected-file mode/no-follow/owner/size 검사; auth code never persisted; refresh grant만 저장.
- config generation CAS 충돌, admission/recovery 중단, OAuth 새 연결 취소, 기존 external service-account path에서 journaled OAuth 전환 실패/복구, exact provider/local revoke semantics.
- 사용자 UI에서 기본 OAuth 흐름과 고급 service-account 경로 구분, Desktop app missing/pending, 잘못된 계정·취소·재시작·권한 거부·복구, remote MCP credential 독립성. Fake provider/browser만 사용한다.
- 각 Drive API method와 최소 scope의 조합을 시험한다. `drive.file` 선택 시 기존 자료 list/get/download, parent/child enumeration, create/move/readback과 Picker로 선택한 folder 이후 child access를 모두 입증하지 못하면 그 scope를 충분하다고 승인하지 않는다.

실제 Google login, School materials, 실제 credentials/provider, worker 활성화는 리뷰 후 인간이 지정한 환경·계정·자료로만 수행한다. 실제 결과가 완전 수락되어야만 사용자의 조건부 flow pass에 따라 커밋/PR을 진행한다. 그 전에 active checker pin, `.env`, global settings, frozen specs, GUI-4/RESEARCH는 손대지 않는다.

## PLAN 단계의 미해결 dependency

1. Google Desktop OAuth client 등록: 실제 `mcp`/`worker` ID와 cloud project IDs가 없다. 기본안은 두 프로젝트로 분리하고 Remote MCP project와도 분리한다. 앱 등록·Google consent screen/verification은 배포 준비이지 일반 사용자 작업이 아니다.
2. Worker scope: `drive.file` + Picker가 기존/미래 School hierarchy를 실제로 커버하는지 공식 method matrix와 controlled test로 확정한다. 실패하면 더 넓은 Worker scope를 별도 human choice와 review로 결정한다.
3. Drive privacy: 현재 owner-plus-two-share state를 어떻게 처리할지 위 두 선택 중 인간이 결정해야 한다. 기존 checker/adapter를 완화하는 변경은 자동 포함하지 않는다.
4. Google revoke: project separation과 Google의 project-wide revoke semantics를 공식 문서/actual setup으로 다시 확인한다. 분리를 증명하지 못하면 provider revoke는 제공하지 않고 local disconnect만 한다.
5. frozen spec 영향: 현재 frozen read-only MCP와 ownership/human-gate를 보존하는 범위만 구현한다. privacy/scope 계약을 바꿔야 한다면 문서 본문을 수정하지 않고 구체 spec-revision proposal을 별도 제시해 승인·리뷰를 받는다.

**담당/모델:** 기존 owner 연속성 유지, `gpt-6-luna/max`. 인증·credential transaction·privacy boundary가 구현·테스트에 밀접하게 결합되어 동일 담당자가 PLAN 수정, 구현, self-check 및 필요한 범위 내 수정까지 담당한다. PLAN 단계 실행 증거는 없음.
