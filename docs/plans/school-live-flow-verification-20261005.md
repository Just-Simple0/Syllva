# School 실제 자료 흐름 검증 — 2026-10-05

현재 결과: **전체 흐름 미통과. 읽기·PDF 추출은 확인했으며 intake/Notion·Drive 저장 전 선행 검사에서 멈췄다.** 사용자의 “흐름 검증 진행해보고 통과 되면 PR 생성 후 커밋 진행”에 따라 PR/커밋/푸시는 전체 흐름 통과 후에만 진행한다. GUI-2/3의 기존 로컬 교정·리뷰 수락은 유지하며 실제 운영 호환성 수락과 구분한다.

## 최신 인간 결정

사용자의 “일단 드라이브 쪽 부터 OAuth로 수정하고, Notion, Canvas 쪽도 비개발자 scope로 바라보자”에 따라 Drive OAuth 구현으로 진행 경로가 정해졌다. 아래 권장안 정정과 선택지 문단은 결정 전 checkpoint다. 기존 실제 검증 결과는 그대로이며 OAuth 설계·필수 리뷰·구현·검사와 실제 동의/권한 준비 뒤 흐름을 재검증한다. Notion·Canvas 비개발자 평가는 `docs/plans/provider-connection-nondeveloper-scope-20261005.md`에 기록한다.

### 이후 scope 결정 및 실제 권한 귀속 확인

최신 답변에서 인간은 두 서비스 계정이 개발 테스트용이며 OAuth 완성 이후 정리할 수 있다고 설명했다. OAuth 완료·실제 두 연결 확인까지 현재 접근권과 `owner_only` 검사를 유지한다. 서비스 계정 allowlist로 저장 검사를 완화하는 경로는 채택하지 않는다. 정리 직전 정확한 대상과 권한을 새로 확인하고 구체적인 승인 후 접근권을 변경한다. 이 답변을 즉시 ACL 삭제나 Cloud 서비스 계정·키 삭제 승인으로 확대하지 않는다. 현재 구현 계약은 동결된 `docs/plans/drive-oauth-nondeveloper-v2.md`와 `docs/plans/drive-oauth-nondeveloper-v3.md`의 전체 계약이며, 리뷰·검사 집계는 `.insane-review/drive-oauth-20261005/parent-progress.json`이다.

인간은 기존 School 유지·검색 읽기 전용/자료 처리 Drive 관리 권한 분리를 선택했다. 또 추가2권한이 `syllva-mcp`와 `worker`라고 설명했다. 정상 credential snapshot의 registered SA identity와 실제 School root/PDF permission을 GET으로 비교하여 정확한2match/no-public-or-domain을 확인했다. root MCP reader/PDF MCP writer이고 worker는 둘 다 writer다. 값·이메일·credential은 결과에 노출하지 않았다. 이는 미등록 principal 의심을 해소하는 귀속 근거이며, 현재 코드의 owner_only 검사 통과나 ACL 삭제 승인으로 해석하지 않는다. 기존 검사 결과와 자료 bytes는 유지된다. 구현 계약은 `docs/plans/drive-oauth-nondeveloper-v2.md`, 실제 귀속 근거는 `.insane-review/drive-oauth-20261005/registered-school-permission-attribution.json`이다.

## 관측과 수락 범위

아래는 최초 preflight 당시 관측이다. 당시 추가 principal 미판정은 위의 후속 귀속 확인으로 해소됐으며, 저장·전체 흐름 미통과 결과는 그대로다.

- 대상: School/2026-2/종합설계프로젝트1 (002)/강의자료의 `2026F_CDP1(OT).pdf`, 480,043 bytes. 설정된 과목은 `2026-2_LMS67504-002`다.
- 정상 application credential resolver를 통해 이미 설정된 worker credentials를 사용했다. 실제 `.env`를 읽거나 값·이메일·키·토큰을 출력하지 않았다. 개인 설정/공유 권한/운영 DB/스케줄러·worker 활성화를 변경하지 않았다.
- Sandbox 네트워크 실패 뒤 같은 읽기 전용 SDK 검사만 host 권한으로 재실행했다. 감사 script 첫 시도에서 `input_requests` logical key를 잘못 사용한 것은 진단 도구 오류이며 실제 workspace 결함 근거로 채택하지 않았다. 수정한 두 번째 시도의 근거를 사용한다.
- Drive의 실제 credential class는 `google.oauth2.service_account.Credentials`다. 인증된 계정 조회 후 `google_worker_service`는 고정 오류 `authenticated OAuth application identity is unavailable`로 거부했다. 서비스 계정 class에는 현재 코드가 요구하는 OAuth client attribute가 없다. 같은 configured-worker identity로 읽기 전용 metadata 진단만 수행했으며 이 경로를 쓰기 활성화의 fallback으로 쓰지 않았다.
- School/root/semester/upload/course/Materials/source PDF는 비공개이며 shared drive에 속하지 않는다. 실제 worker 계정 기준 `ownedByMe=false`, `owner_only=false`, owner 1명과 추가 user permission 2개다. 수정·이동 capability가 있어도 운영 `require_private_ownership`은 거부한다. 추가 계정이 등록된 service identity와 정확히 일치하는지 아직 판정하지 않았다. 임의 공유 허용·소유권 override를 하지 않았다.
- Notion worker bot 인증은 성공했다. 5개 datasource의 ID, database containment, 실제 부모 page와 설정된 부모 ID, 속성 이름 집합은 일치한다. SDK 응답은 `parent.database_id` 및 `database_parent.page_id`다. 현재 parser는 직접 `parent.page_id`/보조 `parent_page_id`만 읽어 부모 불일치를 잘못 보고한다.
- 실제 database를 추가 읽어 부모 관계를 확인한 뒤 분리된 메모리 진단 사본에서만 부모를 투영하자 다음 거부는 상태 그룹 shape였다. 실제 `status.groups`는 배열이고 현재 parser는 mapping을 기대한다. 진단 사본을 worker에 전달하거나 수락·활성화에 사용하지 않았다.
- 실제 PDF를 메모리에서 읽고 production `extract_pdf`를 호출했다. SHA-256 `e1b3c4af0e1c4c536b28882108a0648e65d27421f871950fe9edb8d59b1e1a3b`, 15쪽 중 14쪽, 2,676자, 누락 15쪽으로 **Partial**이다. 전후 source metadata가 같았다. 진단 entity label은 예약/등록하지 않았다. 본문·정규화 결과를 Notion/Drive/SQLite에 저장하지 않았다. 이 결과를 Ready나 전체 흐름 통과로 승격하지 않는다.
- `uls doctor`는 별도의 `NOTION_MCP_TOKEN` 부재, credential separation 및 remote profile 미충족을 보고한다. worker 인증 성공은 read-only MCP/retrieval 성공을 대신하지 않는다.

## 진행 경로 결정이 필요한 부분

### 비개발자 사용성 기준 권장안 정정

사용자가 서비스 계정/API key 발급 부담과 기존 OAuth 설계를 지적했다. 현재 구현의 변경량에 치우친 앞선 서비스 계정 유지 권장안을 정정한다. **비개발자용 제품 기본 경로는 Drive 사용자 OAuth 연결을 권장한다.** 이는 새 추천·설계 기준이며 사용자의 OAuth 구현/credential 변경 승인으로 해석하지 않는다. 앞선 선택 질문의 “서비스 계정 유지 권장” 표기는 이 기준으로 대체한다.

기존 `docs/plans/remote-mcp-google-oauth.md` 및 `src/uls/mcp/transports/oauth.py`의 OAuth는 Google `openid email`로 소유자를 확인하고 Syllva 전용 MCP token을 발급하는 Remote MCP 로그인 경로다. Google Drive의 파일 읽기/저장 권한을 획득하는 연결 흐름은 구현돼 있지 않다. GUI-2의 Google credential admission은 service-account JSON으로 한정돼 있다. 기존 broker 토큰을 Drive용으로 재사용하지 않는다.

목표 사용자 경험은 “Google로 연결 → 계정 로그인·필요한 권한 동의 → School 선택 → 연결 확인”이다. OAuth 앱 등록/배포 준비는 제품 개발자가 맡고 일반 사용자에게 Google Cloud 프로젝트 생성·서비스 계정 키 다운로드·OAuth client ID/secret 입력을 기본 절차로 요구하지 않는 방향을 설계한다. credential의 읽기/쓰기 목적 분리, 실제 provider scope, token 보호/갱신·철회, 계정 일치, 사용자 소유권·sharing freshness, 취소·실패 복구는 별도 계약·독립 리뷰로 확인한다. 이 목표가 현재 구현됐거나 한 번의 동의로 모든 provider 설정이 끝난다고 주장하지 않는다. Notion과 Canvas 인증도 각각 별도 사용성 검토 대상이다.

공식 Google 데스크톱 OAuth 지침: <https://developers.google.com/identity/protocols/oauth2/native-app>. 앱의 system browser 로그인/동의 및 API 접근 authorization 경로를 제공하며 앱 client 등록은 별도 전제다.

현재 서비스 계정 연동과 기존 `ownedByMe/owner_only` 경계가 충돌한다. 이는 단순 ID 교정이 아니라 provider identity·권한 계약 결정이다. 전역 AGENTS의 “Ask about material security, UX, scope or risk-acceptance decisions”를 적용한다. 단순 가드 해제나 현재 공유 상태를 자동 승인하는 방법은 사용하지 않는다.

1. **현재 서비스 계정 방식 보완:** 사용자 소유·비공개 School을 유지하면서 정확히 등록·검증된 worker/MCP service identity만 허용하는 별도 경계와 서비스 계정 identity binding을 설계한다. owner·계정·permission attribution/allowlist, 변경·누락·추가 공유 시 fail-closed, marker/readback/id-preserving move 조건을 명시하고 필수 독립 PLAN/FINAL 리뷰를 거친다. 현재 공유 권한을 바꾸지 않는다. 실제 SDK shape를 반영하는 Notion parser 수정도 같은 운영 호환 교정 범위에서 검토한다. 구현 전에 구체 계약/수락 조건을 동결한다.
2. **소유자 OAuth 운영 경로로 변경:** 계정 소유권 기준을 유지하며 소유자 OAuth worker 및 별도 read-only credential 등록·GUI admission/운영 계약을 설계·리뷰한다. 새 credential의 발급/등록은 인간이 수행하며 기존 service-account 값이나 sharing을 자동 교체하지 않는다.

어느 경로든 스키마·공유 권한 자동 수정, USER Submitted/Verified/Scope Confirmed 임의 생성, 기존 School 원본 이동, 저장·게시를 선행하지 않는다. 현재 5개 datasource 구조 자체는 맞으며 schema 변경이 필요하다고 단정하지 않는다. 추가 SDK compatibility 문제가 없는지는 교정 후 다시 검증한다.

## 이후 전체 흐름 수락 조건

1. 선택한 provider identity/권한 계약과 실제 credential/read-only retrieval 준비를 검증한다.
2. 같은 single-active-worker lock 아래 정확한 과목/학기·workspace·source bytes/permission freshness를 검증한다. 지속 worker 활성화와 1회 운영 실행을 구분한다.
3. 사용자 승인 범위의 검증용 source copy를 upload에 두고 원본을 보존한다. File Intake와 아직 제출되지 않은 Input Request를 운영 경로로 생성한다.
4. 정확한 과목, MATERIAL_PDF, 자료 역할 및 요청 generation을 인간에게 제시한다. 명시적·귀속 가능한 인간 입력/제출을 받아 처리한다. AI가 USER submit/approval을 대신 만들지 않는다.
5. 정상 처리 또는 명시적 Partial 상태, immutable derivative, Notion Material/File Intake/Input Request 결과, Drive copy·folder·marker·hash/readback을 대조한다. 오류·중복·부분 반영이 있다면 수락하지 않고 근거를 보존한다.
6. 정상 별도 read-only retrieval composition으로 같은 source version의 context/capability/허용 chunk를 확인한다. Partial과 누락 쪽을 그대로 드러내고 재처리 멱등성·USER 보존을 확인한다.
7. 필요한 교정의 리뷰/검사와 전체 실제 흐름 통과 뒤 검증된 GUI-2/3 교정·체커·운영 호환 교정·결과 문서만 커밋/푸시하고 새 PR을 생성한다. 기존 GUI-4/RESEARCH 변경은 포함하지 않는다. 원격 main에 직접 push/merge하지 않는다.

## 근거와 배정

- 실제 SDK preflight: `.insane-review/gui23-pr13-fixes-20261004/school-live-preflight-v2-20261005.json` (최초 네트워크/진단 key 실패 기록은 별도 원본 보존).
- 실제 SDK shape/PDF 추출: `.insane-review/gui23-pr13-fixes-20261004/school-sdk-shape-diagnosis-20261005.json`.
- Notion 공식 응답 구조: <https://developers.notion.com/reference/data-source>. 2025-09-03 API의 `parent`와 `database_parent`를 구분한다.
- 총괄: 기존 실제 `gpt-6.1-sol/high`, installed `sol-orchestrator`. 같은 담당 Sagan `gpt-6-luna/max`에 읽기 전용 운영 경로·계약 확인을 맡겼다. 실제 identity 변경 없음; Jev `not_configured` 재설정 없음. Google Drive 0.1.16 skill은 실제 자료 위치/metadata grounding에 선택했고 쓰기를 실행하지 않았다.
- #13과 #14는 현재 `MERGED`이고 열린 PR은 없다. 현재 작업 브랜치는 `codex/gui23-pr13-followups`, HEAD `f55ffa4f1bba3526111734f543582670ce2c695e`; PR/커밋/푸시 없음.
