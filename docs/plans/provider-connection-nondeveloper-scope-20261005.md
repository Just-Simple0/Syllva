# 비개발자용 연결 범위 — 2026-10-05

사용자 결정: “일단 드라이브 쪽 부터 OAuth로 수정하고, Notion, Canvas 쪽도 비개발자 scope로 바라보자.” Drive 사용자 OAuth를 기본 연결로 구현한다. Notion·Canvas는 실제 사용자가 해야 할 절차와 제품 개발자가 미리 준비할 조건을 평가한다. 이 문서는 후속 인증 구현·외부 서버 배포의 승인이나 완료 근거가 아니다.

## Drive: 이번 구현 대상

일반 사용자는 Google 연결 버튼, 시스템 브라우저의 계정 선택·동의, School 연결 확인 순서로 진행한다. Google Cloud 프로젝트 생성, 서비스 계정 JSON 다운로드, client ID/secret 입력은 제품 개발자의 배포 준비에 속한다. Desktop OAuth client 설정이 없으면 연결을 준비 중으로 표시하고 다음 행동을 안내한다. 현재 안전한 설정 존재 여부 검사에서는 Remote MCP Web client만 설정돼 있고 Drive Desktop client는 없다. Remote MCP의 `openid email` 로그인 토큰을 Drive에 쓰지 않는다.

읽기 전용 MCP와 쓰기 worker는 실제 Google scope 및 앱 identity를 분리해야 한다. 화면에서만 목적을 구별하거나 writer token을 검색에 재사용하지 않는다. 시스템 브라우저·loopback·PKCE/state, 토큰 보호·갱신·철회, 취소·재시작·계정 변경, 기존 service-account 경로 보존을 독립 PLAN/FINAL 리뷰와 실제 화면 검사에 포함한다.

`drive.file`은 앱이 생성하거나 사용자가 앱에 선택·공유한 파일 접근에 적합하다. School 폴더 선택이 모든 기존·향후 하위 자료에 자동 접근권을 준다고 가정하지 않는다. 전체 기존 자료의 검색·처리를 위해 더 넓은 `drive.readonly`/`drive`를 선택한다면 restricted scope와 배포 검증 조건을 명시하고 구체 권한 결정을 받아야 한다. [Google Drive scope 지침](https://developers.google.com/workspace/drive/api/guides/api-specific-auth), [Desktop OAuth 지침](https://developers.google.com/identity/protocols/oauth2/native-app).

현재 School에는 owner 외 추가 사용자 권한 2개가 있다. 소유자 OAuth로 바뀌어도 단독 소유권 검사가 자동 통과하지 않는다. 기존 공유 권한을 자동 삭제하거나 `owner_only` 검사를 완화하지 않는다. 기존 자료를 읽는 범위와 비공개 저장 대상의 수락 조건을 계획에서 구체화한다. 실제 자료 흐름 통과 전에는 사용자 조건에 따라 commit/push/PR을 진행하지 않는다.

## Notion: OAuth의 두 경로를 구별

현재 Syllva의 Notion adapter는 REST API와 별도 목적별 integration token을 사용한다. 실제 worker 인증은 성공하지만 `database_parent.page_id` 및 배열형 `status.groups` 응답 호환 문제가 있다. 읽기 전용 MCP token은 아직 없다. OAuth 검토가 이 호환성 문제나 credential 분리를 대신하지 않는다.

1. **REST 공개 연결:** 사용자는 Notion 로그인, workspace 선택, 공유할 페이지 선택으로 설치할 수 있다. 개발자는 public connection과 redirect를 준비해야 하며 code 교환에는 client secret이 필요하다. 배포 desktop에 공용 비밀키를 내장하는 구조는 권장하지 않는다. 현재 REST 흐름을 유지하려면 비밀키 교환을 안전하게 처리하는 구조와 local-primary 제약·토큰 경계·운영 비용을 별도로 설계·리뷰해야 한다. [공개 연결 인증](https://developers.notion.com/guides/get-started/authorization).
2. **공식 hosted Notion MCP:** OAuth2+PKCE 및 dynamic client registration을 지원한다. 이는 별도 MCP transport이며 현재 REST adapter에 같은 토큰을 넣는 교체 방식이 아니다. 필요한 data-source schema 검사, 페이지·블록 readback, 멱등 쓰기, SOURCE/AI/USER 소유권, 읽기·쓰기 목적 분리를 실제 도구 계약으로 충족하는지 검증한 뒤 이행 여부를 판단한다. 별도 비밀키 broker 없는 후속 후보로 평가한다. [Notion MCP client 지침](https://developers.notion.com/guides/mcp/build-mcp-client).

기본 UX 목표는 “Notion 연결 → 로그인·페이지 선택 → 구조 확인”이다. 개발자 portal에서 integration 생성·token 복사를 일반 사용자 필수 절차로 삼는 것은 배포용 기본 경로에 적합하지 않다. 기존 internal integration은 고급 설정과 개발·운영 호환 경로로 유지할 수 있다. 최신 문서의 API 버전은 현재 runtime `2025-09-03`과 다르므로 문서를 이유로 버전을 자동 올리지 않는다.

## Canvas: 학교가 제공하는 연동 조건부터 확인

Canvas Cloud OAuth developer key는 학교 관리자가 발급한다. 학생이 자기 계정만으로 Syllva 공개 앱 키를 준비할 수 있다고 안내하면 안 된다. 현재 Canvas profile은 미설정이며 학교의 Syllva용 key·승인 앱 지원 여부는 확인되지 않았다. [Canvas OAuth 설정](https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth).

지원 학교에서는 학교 선택·로그인·필요한 읽기 권한 동의로 연결한다. 공식 endpoint 문서는 code/refresh 교환에 client secret을 요구하므로 Google desktop loopback 설계를 그대로 복사하지 않는다. 허용 redirect, 학교별 key, scope, 안전한 교환 구조를 별도로 검토한다. [Canvas OAuth endpoints](https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth_endpoints).

지원 key가 없는 경우에는 “학교에서 간편 연결을 제공하지 않음”과 수동 연결 경로를 명확히 보여준다. 수동 경로는 해당 학교 Canvas의 token 관리 화면으로 안내하고 복사·한 번 붙여넣기·연결 검사를 제공하는 제한적 대안이다. 비밀번호 입력, 세션 쿠키 추출, 숨은 API 우회, token 자동 생성은 포함하지 않는다. token 생성 자체가 학교 정책으로 불가능하다면 연결 불가를 정직하게 표시한다.

## 공통 수락 기준과 실제 배정

- 정상 연결·동의 취소·브라우저 종료·만료·갱신 거부·계정 불일치·재연결·로컬 연결 해제가 다음 행동을 보여주고 busy 상태를 해제한다.
- 일반 화면·로그·리뷰 패키지에 token, secret, provider의 임의 오류 본문을 출력하지 않는다. API 권한과 앱 내부 범위를 구별해서 설명한다.
- 설치 준비, 실제 인간 동의, provider 검사, 전체 intake·저장·검색 통과는 각각 관측 근거를 갖춰 수락한다.
- 총괄 `gpt-6.1-sol/high`가 정책·범위·기획·통합·판정을 맡고 같은 Sagan `gpt-6-luna/max`가 Drive 설계부터 구현·검사·수정까지 책임진다. 같은 Bohr `google-antigravity/gemini-3.8-flash/ultra`가 UI/flow PLAN·FINAL 독립 검토를 맡는다. Native web ChatGPT PLAN·FINAL도 별도 필수다. Jev는 기존 `not_configured`, identity 변경 없음.
- `harness-frontend-design` 0.1.1-candidate와 `docs-guide` 1.4.4를 로그인 흐름·실패 동작 및 공식 문서 확인에 적용했다. Google/Canvas llms index fetch 실패는 공식 detail fallback, Notion index는 성공 후 정확한 href를 사용했다. `.md`의 web content-type 거부는 같은 공식 HTML 페이지로 회수했다. 조사 결과이며 구현·리뷰 수락 주장 없음.
