# Syllva v2 — 데스크탑 앱 전환 계획 (rev 3)

상태: 계획 (2026-11-02, rev 3: MCP 필수 아님 → 도구 없는 에이전트 구조). 상세 설계는 `syllva-v2-design-draft.md`(rev 3)가 우선한다. 프로즌 계약(v1.2)을 바꾸므로 R0에서 v2 개정서를 먼저 승인받는다.

rev 2 변경 요지: 독립 검토(Fable, 코드 대조)에서 human-only 증명, 에이전트 격리, MCP 연결 방향, 요약 freshness, R1 완료 기준, intake 워커 범위, 프로세스 모델이 빠지거나 틀렸음이 확인되어 §3·§4·§8·§9를 고쳤다.

## 1. 확정된 결정 (사용자, 2026-11-02)

| 항목 | 결정 |
|---|---|
| AI 접근 | **공식 CLI 에이전트**를 앱이 서브프로세스로 구동. **도구 없이 텍스트 입출력만**, Core가 자료를 골라 프롬프트에 넣는다. **MCP는 필수 아님**(선택 어댑터). 구독 토큰 추출·비공식 호출 금지 (**약관 허용 여부는 R0에서 공식 문서로 확인, §3**) |
| 첫 지원 CLI | Claude(Claude Code), ChatGPT(Codex CLI) |
| Notion / Drive | **제거**. 저장소는 로컬 라이브러리 + SQLite 단일화 |
| GitHub 어댑터 | 선택 기능으로 격하 (기본 꺼짐, 어댑터 경계 유지) |
| 메인 화면 | **HTML UI**. 코어가 로컬에서 서빙하고 앱 셸(웹뷰)이 표시 |
| 셸 | **Tauri 2** |
| 플랫폼 | macOS 전용 아님. **Windows 동시 지원**, **모바일은 후속으로 고려** |
| 데이터 이관 | 하지 않음. 새 라이브러리로 시작 |
| 브랜드 | **Syllva 유지** |
| 리뷰 | 외부 이중 리뷰 파이프라인 폐지. 구현자 자체 리뷰 + 계약 테스트 (§7) |

## 2. 목표 아키텍처

```
┌ Tauri 셸 (얇게) ───────────────────────────────────────┐
│ HTML UI: 라이브러리 · 과목 · 과제 · 시험 | 우측 AI 대화 패널 │
│   (컨텍스트 칩: 질문에 쓰일 자료를 사용자가 확인)           │
└──────────────┬─────────────────────────────────────────┘
               │ http://127.0.0.1:<port> (UI 세션 토큰)
       ┌───────▼─────────────┐   프롬프트(자료 포함)   ┌──────────────┐
       │ Syllva core service │ ─────────────────────▶ │ CLI 에이전트   │
       │ (Python, 재사용)     │ ◀───────── 텍스트 ───── │ (도구 없음)    │
       │ Retrieval Service   │                        └──────────────┘
       └──┬───────┬──────────┘
   SQLite + 파일 라이브러리   Canvas 어댑터
```

- **UI = 웹 앱.** 셸은 창·트레이·자동 시작·업데이트·사람 확인 다이얼로그만 맡는다. 기존 `src/syllva/settings/static`과 Starlette 서버가 출발점이다.
- **셸:** Tauri 2. Windows·macOS 지원, 모바일 경로 있음. 원격 URL IPC·`shell_secret` 전달은 R2 초기 스파이크로 검증한다.
- **코어:** 기존 Python 재사용. 검색 엔진(`RetrievalEngine`)은 Core가 직접 호출한다(MCP 불필요).
- **에이전트:** 도구 0개. 질문마다 Core가 자료를 골라 `ContextPackage`로 프롬프트에 넣고, 출력의 인용을 Core가 검증한다. 모호하면 사용자가 칩으로 자료를 고른다.
- **MCP:** 필수 아님. 자유 대화에서 사전 선택 컨텍스트가 부족하다고 확인되면 별도 개정으로 읽기 전용 프록시를 추가한다(설계서 §7.3). 현재 MCP 코드는 선택 어댑터로 격리해 두고 삭제하지 않는다.
- **프로세스 모델:** 단일 Core 서비스가 DB를 여는 유일한 프로세스다(SQLite WAL).
- **비밀정보:** OS 키체인(`keyring`). 로그·번들·프롬프트·에이전트 환경에 금지.

## 3. AI 대화 패널 (CLI 에이전트)

- `AgentAdapter`: `start(session_context) / send / stream / stop`. 초기 구현은 Claude Code, Codex CLI. 설치·로그인 상태는 앱이 감지해 안내만 하고 대신 로그인하지 않는다. 비대화형 구조화 출력(`claude -p --output-format stream-json`, `codex exec --json`)을 기반으로 한다.
- **R0 조사 결과 (2026-11-02, 공식 문서 직접 열람 여부 표시):**
  1. **Claude Code — 조건부 허용 (VERIFIED, code.claude.com/docs/en/legal-and-compliance).** 사용자가 수정되지 않은 `claude` 바이너리에 자기 구독으로 로그인하는 것은 막지 않는다. 반면 Claude.ai 로그인을 앱에 내장하거나, 구독 자격증명으로 사용자 대신 요청을 중계하거나, 토큰·세션을 수집·저장하는 것은 **금지**다. 제품에서 Agent SDK를 쓰는 경우 API 키 인증을 쓰라고 명시한다. 따라서 앱은 **바이너리를 수정 없이 실행만 하고 로그인은 사용자가 CLI에서 직접** 하며 토큰을 읽거나 저장하지 않는다. `claude -p`/Agent SDK/서드파티 앱 사용은 구독 한도를 소모하며(2026-06-15 변경 일시 중단 공지), 정책이 다시 바뀔 수 있다. Commercial Terms 적용 여부와 "일반적·개인적 사용"에 해당하는지는 문구상 불명확해 **법무 확인 필요(UNVERIFIED)**.
  2. **Codex CLI — 미확정 (UNVERIFIED).** 이 세션 네트워크가 `developers.openai.com`·`help.openai.com`을 허용하지 않아 공식 약관·`codex exec` 문서를 읽지 못했다. GitHub README의 "ChatGPT 계정 로그인 권장"만 확인. 제3자 앱의 구동 허용 여부, 비대화형 옵션, 네이티브 Windows 지원 수준(README는 PowerShell 설치, `docs/install.md`는 WSL2만 언급 — 불일치)은 도메인 허용 후 재조사한다. 그때까지 Codex는 경로 B(내장 터미널, 사용자가 조작) 또는 C(API 키)를 기본으로 하고 A는 보류.
  3. **Claude Code 사양 (VERIFIED, cli-reference·mcp 문서):** `-p --output-format stream-json`, `--mcp-config`+`--strict-mcp-config`, `--tools ""`/`--disallowedTools`/`--restricted`(명령 도구 제거, 파일 도구는 작업 디렉터리로 제한), `--permission-mode`. 도구 없는 구조에서는 `--tools ""`와 빈 MCP 설정(`--strict-mcp-config`)으로 도구를 모두 끈다(MCP를 나중에 추가하면 stdio 서버 `env`로 토큰을 줄 수 있음). 작업 디렉터리는 플래그가 아니라 서브프로세스 cwd로 지정.
  4. **Windows:** Claude Code는 네이티브 지원이지만 **네이티브 Windows에서는 명령이 샌드박스 없이 실행**된다(샌드박스는 WSL2 한정). 도구가 없으므로 모델이 호출할 행동은 없지만, 훅·설정 파일 같은 CLI 자동 로드 경로는 어댑터 적합성 시험으로만 막는다는 한계를 개정서에 명시한다.
  5. 앱은 사용자에게 다음을 표시한다: 구독 한도가 소모된다는 점, 로그인은 각 CLI에서 직접 하며 앱은 토큰을 저장하지 않는다는 점, 네이티브 Windows에서 OS 샌드박스가 없다는 점, API 키 대체 경로.
- **경로 결정:** Claude는 A(공식 바이너리 구동, 구독 로그인) 가능하되 B를 안전한 폴백으로, 제품 안에서 Agent SDK를 쓸 때만 C(API 키). Codex는 R0 재조사 전까지 B/C.
- **에이전트 격리(R0 결정, R4로 미루지 않음):**
  - 에이전트에는 **도구를 하나도 노출하지 않는다**(MCP 포함). 입력은 Core가 조립한 프롬프트, 출력은 텍스트. unrestricted 모드는 두지 않는다.
  - SQLite·라이브러리·세션 토큰·키체인 접근은 에이전트 작업 디렉터리 **밖**에 두고, 에이전트 프로세스에는 환경변수·파일·로그로 UI 세션 토큰을 절대 전달하지 않는다.
  - CLI 자체 샌드박스(`--sandbox read-only`, 권한 모드)는 정책 보조일 뿐이다. OS 수준 격리는 macOS/Linux만 신뢰할 수 있고 Windows는 약하다는 점을 인정하고, Windows에서는 "도구 비활성 + 경로 분리"에 의존한다는 한계를 개정서에 명시한다.
- 컨텍스트는 앱이 허용 범위를 결정하고 사용자 확인 후 전달한다. 에이전트는 라이브러리를 **읽기만** 한다. 노트·요약 저장은 앱이 AI 소유 라벨로 대신 기록한다.

## 4. 불변식 매핑 (R0 개정서의 뼈대)

| v1.2 불변식 | v2 |
|---|---|
| Notion 권위 (§4.3, §13) | **삭제**. 로컬 DB가 권위 |
| Drive 레이아웃·SourceRef (§7, §8) | 로컬 content-addressed 저장소로 대체. `SourceRef`의 provider 제약(현재 `google_drive`만 허용), SourceFingerprint/Locator 의미는 유지·개정 |
| SOURCE / AI / USER 소유 구분 | **유지** (DB 컬럼 + 쓰기 경로 강제) |
| `Verified` / `Scope Confirmed` human-only | **유지하되 증명 방식을 새로 정의.** v1.2는 "Decision By가 Notion 사람 사용자인가"(`_is_human_decision_by`, `approval_identity.py`)로 증명했다. v2 human-proof 모델(아래) |
| 검색 표면 read-only | 유지 (Retrieval Service는 읽기 전용, MCP는 선택 어댑터) |
| freshness / stale enrichment 제외 | 유지. **AI 요약·enrichment 저장 시 `source_version / source_hash / processor_version` 필수**(없으면 stale 판정 불가) |
| 정규화 ≠ 요약, Partial → Ready 금지 | 유지 |
| 공개 링크 공유 금지 | 유지. 모바일 접속도 인증된 경로만 |
| GitHub 계약 (§35) | 선택 기능. "정확한 ref만 증거" 유지 |

### v2 human-proof 모델 (R0에서 확정할 초안)

세션 토큰 보유만으로는 "사람"의 증명이 되지 않는다. 같은 PC에서 도는 에이전트가 토큰을 얻거나, `curl`/서브프로세스로 `Sec-Fetch-Site` 같은 헤더를 위조해 쓸 수 있기 때문이다(현 보안층은 Host·Sec-Fetch-Site 판정에 의존).

- human-only 쓰기(Verified, Scope Confirmed, 제출 상태 변경, 승인)는 **(a) UI 세션 토큰 + (b) Tauri 네이티브 측 사용자 제스처 확인**(코어가 발급한 일회용 nonce를 셸 IPC가 사용자 클릭 후에만 제출)을 모두 요구한다.
- 토큰은 에이전트 환경과 완전 분리(§3). 에이전트가 쓸 수 있는 HTTP 경로에는 human-only 엔드포인트가 존재하지 않는다.
- 각 human-only 쓰기는 감사 로그(누가·언제·어떤 nonce)를 남긴다.
- **계약 테스트(구현 전 작성, 릴리스 블로커):** 에이전트 토큰/환경으로 human-only 엔드포인트 호출 시 거부, nonce 재사용 거부, 에이전트 실행 인자에 도구 없음, DB 파일이 에이전트 작업 영역 밖에 있음.

## 5. 기능 계획

1. **라이브러리:** 드래그 앤 드롭 → 기존 분류기(`intake/classification`, Notion 의존 거의 없음)로 과목·종류 제안 → 한 화면 확인. 수정본 교체(버전 체인)는 **구 버전 locator 무효화 규칙(명세 §19, stale locator)** 을 따른다. `.txt` 등 현재 Kind가 없는 형식을 R2에서 해결한다.
2. **자연어 해석(핵심 결함 대응):** midterm에서 가장 큰 문제였던 "ID 없이 못 찾음"(과목 이름·주차·날짜·별칭)을 R2의 별도 항목으로 책임지는 `resolve_entity` 개정으로 다룬다. 완료 기준은 실제 질의 목록(예: "TCP", "4주차", 날짜, 과목 이름)이다.
3. **Canvas:** 기존 관측기 재사용. 단 `canvas_attachments.py`는 첨부를 Drive로 업로드하는 경로(Drive 참조 다수)라 **로컬 라이브러리 적재로 재작성**한다. LTI 강의자료는 수동 업로드(숨은 API 금지 유지).
4. **요약:** 자료·주차 단위, AI 라벨 + 근거 locator + freshness 지문 필수.
5. **과제 관리:** Canvas 과제·마감 → 목록/캘린더. 제출 상태 변경은 human-proof 필요.
6. **시험 대비:** 범위 확정(human-proof) → 복습 스케줄 → 문제 생성(근거 인용 필수) → 모의고사 → 채점 → 오답 노트.

## 6. 크로스플랫폼·모바일

- **Windows:** 키체인, 경로(NFC·대소문자·긴 경로), 파일 잠금, 프로세스 종료를 R1부터 CI 매트릭스에 포함. 현재 Windows는 settings/credential 계열 195건 실패가 알려진 한계라 R1에서 정리한다. Codex CLI의 Windows 지원 여부(§3)가 R4 범위를 좌우한다.
- **모바일(후속):** CLI 에이전트는 모바일에서 실행 불가 → 모바일은 데스크탑 코어에 접속하는 클라이언트. 데스크탑이 켜져 있어야 한다. 현재 보안층은 `LOOPBACK_PEERS`(루프백만 허용) 전제라 **서버층 보안 재설계가 필요**하다(페어링, 토큰, TLS). anyone-with-link 금지. UI는 처음부터 반응형·터치 가능하게 설계하되 서버층 재작업은 R6에 둔다.

## 7. 리뷰·품질 운영 (CLAUDE.md의 이중 리뷰 파이프라인 대체)

R0에서 CLAUDE.md의 "역할 분담/작업 파이프라인/리뷰 설정" 절을 아래로 교체한다.

1. 단계마다 계획 → 구현 → **게이트 실행**(`pytest -m contract`, `pytest -m unit`, ruff, mypy, 프로젝션 린트, 앱 스모크) → **구현자의 자체 리뷰**(diff를 불변식 표에 대조, 별도 컨텍스트의 `code-review` 패스 포함) → 커밋.
2. 자체 리뷰의 자기 편향은 **불변식 계약 테스트를 구현 전에 작성**해 보완한다(§4의 human-proof 테스트 포함).
3. **보안 경계를 바꾸는 변경은 R0 개정서 단계에서 독립 읽기 전용 검토를 한 번 더 받는다**(human-proof 모델, 에이전트 격리). 이후 단계는 자체 리뷰로 진행하고, R5 이후 릴리스 후보에서 외부 검토 여부를 사용자에게 묻는다.
4. 같은 컴포넌트에서 지적이 3건 이상 반복되면 모양별 땜질을 멈추고 구조를 바꾼다(handoff 운영 규율 승계).

## 8. 로드맵

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| **R0** | v2 개정서(§4 표·human-proof 모델·에이전트 격리·도구 없는 에이전트·컨텍스트 조립·freshness 지문), **CLI 약관·사양 확인(§3)**, CLAUDE.md 운영 절 교체, 계약 테스트 목록, 독립 읽기 전용 검토 1회 | 사용자 승인 |
| **R1a** | 내부 이름 정리 `uls` → `syllva`(모듈·CLI·MCP 도구명, `uls` 명령은 별칭 유지). 동작 불변, 단독 커밋 | 전체 테스트 동일 통과 |
| **R1b** | 로컬 저장소(SQLite WAL·콘텐츠 해시 파일) + 단일 코어 서비스 + `RetrievalEngine`을 Notion/Drive 리더 없이 구성(생성자·45개 호출 지점·`state/sqlite.py`의 `drive_*`/`notion_*` 컬럼과 분기 정리). 새 마이그레이션 | **개정된 계약 테스트(명세 §50 재작성) + 재작성된 fixture 통과.** 기존 테스트 126개 파일은 재작성·폐기를 파일별로 판정. Win/macOS CI |
| **R1c** | **로컬 intake 워커**: 분류기(재사용)와 새 로컬 큐/영수증/복구 모델. `intake/worker.py`(6,116줄)와 `adapters/notion/intake.py`는 단순화가 아닌 재작성 | 로컬에서 파일 투입 → 분류 → 적재 e2e |
| **R2** | HTML UI 골격(라이브러리·분류 확인·수정/삭제/복원·자연어 검색) + Tauri 셸 + 두 OS 서명·번들 검증(PyInstaller 공증/hardened runtime, `keyring`·`pypdf` 네이티브, Windows 코드서명, 업데이트 서명키) + 네이티브 확인(human-proof) | 앱에서 파일 투입 → 검색 e2e, 질의 목록 통과 |
| **R3** | Canvas 연결 UI + 과제 관리 + 첨부 로컬 적재 | 실제 학기 데이터 확인 |
| **R4** | AI 패널(CLI 어댑터, 컨텍스트 칩·프롬프트 조립, 인용 검증, 적합성 시험) 도구 없는 구조. MCP는 범위 밖(선택 어댑터, 필요 확인 시 별도 개정) | 실사용 대화에서 근거 추적, 격리 계약 테스트 통과 |
| **R5** | 요약 + 시험 대비 | 한 과목 1사이클 |
| **R6** | 설치본·자동 업데이트, 모바일 접속(페어링, 보안층 재설계) | 다른 PC 설치 + 폰 접속 |

## 9. 리스크

- **human-only 증명 약화(최대 리스크).** 증명이 Notion 사용자 ID에서 로컬 토큰/제스처로 바뀐다. 에이전트 격리와 §4 human-proof 모델이 실패하면 모든 승인 불변식이 무의미해진다.
- **CLI 약관·사양이 전제를 무너뜨릴 수 있다.** R0 확인 결과에 따라 AI 접근 경로(구독 CLI / 터미널 임베드 / API 키)가 바뀐다.
- **Notion 제거는 어댑터 삭제가 아니라 재설계다:** human-gate, 승인 ID(`approval_identity.py`가 Notion page ID에 결속), 엔진 생성자, 테스트 fixture(126개 파일 중 28개가 fake_notion/fake_drive 직접 import), intake 워커. 공수는 R1b·R1c 두 단계로 잡았다.
- **프로즌 명세 개정 범위가 크다.** 삭제 위주로 제한하고 불변식은 유지한다.
- **Python sidecar 번들**(서명·공증, native 의존성)과 Windows 안정성 → R2 초기에 빈 앱으로 검증. 레포에 JS/Tauri 흔적이 없어 첫 도입 비용이 있다.
- **SQLite 동시 접근:** DB를 여는 프로세스는 Core 하나(§2).
- **Notion이 대신하던 "사람이 직접 고치는 화면"**을 UI가 대체해야 한다(R2).

## 10. 미결

1. **R1a 내부 이름 변경(`uls` → `syllva`)을 R1 앞 단독 단계로 진행할지** — 브랜드 유지 결정에 따른 제안이며 사용자 확정 필요.
2. Codex CLI 약관·`codex exec`·Windows 지원: 공식 문서 도메인(`developers.openai.com`, `help.openai.com`)을 세션 네트워크에서 허용한 뒤 재조사 필요. 그 전에는 Codex를 B/C로 둔다.
3. Claude Code를 앱이 구동하는 형태가 Commercial Terms 대상인지 법무 확인.
