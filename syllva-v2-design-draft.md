# Syllva v2 — Design Revision (DRAFT, R0)

상태: **초안 rev 3 — 에이전트 도구 없는 구조로 단순화. rev 2의 독립 검토(Fable, NO-GO) 지적 반영 위에서 재구성. 사용자 승인 전, 구현 금지.** 승인되면 `v2-design-frozen`으로 동결하고 구현 명세서를 별도로 작성한다.
작성: 2026-11-02 · 기반: `docs/plans/desktop-app-v2.md`, 동결 v1.2 설계서·구현 명세서
관계: 이 문서는 v1.2 동결 문서를 **절 단위로 대체/유지/삭제**한다. 명시되지 않은 v1.2 계약은 그대로 유효하다. 충돌하면 승인된 이 문서가 우선한다(CLAUDE.md 개정 후).

rev 3 요지: **MCP는 필수가 아니다(사용자 결정, 2026-11-02).** 에이전트는 도구를 갖지 않고, Core가 검색 엔진으로 자료를 골라 프롬프트에 근거를 넣어 준다. rev 2의 MCP 프록시·에이전트 채널·Scope Grant는 삭제했고 그 보안 요건은 "에이전트에 도구 없음"으로 대체된다. MCP는 필요가 확인되면 추가할 수 있는 선택 어댑터로 내렸다.

---

## 0. 목적과 범위

v1.2는 "MCP 중심, Notion/Drive 기반의 개인 학업 지식·검색 시스템"이었다. v2는 같은 신뢰 불변식을 유지하면서 제품 형태를 **로컬 우선 데스크탑 앱**으로 바꾼다.

- 저장소: 로컬 라이브러리 + SQLite 단일화. Notion·Drive 제거. GitHub 소스 어댑터는 선택 기능.
- UI: 코어가 서빙하는 HTML UI를 Tauri 2 셸이 표시. Windows·macOS 동시, 모바일은 후속.
- AI: 공식 CLI 에이전트(Claude Code, Codex CLI)를 앱이 서브프로세스로 구동. **도구 없이 텍스트 입출력만** 한다. 앱은 구독 토큰을 읽거나 저장하지 않는다.
- 기능: 자료 보관·분류, Canvas 연동, 요약, 과제 관리, 시험 대비.

### 0.1 개정된 핵심 원칙

v1.2: *ULS가 어떤 컨텍스트가 허용/관련되는지 결정하고, AI 클라이언트는 ULS가 제공한 컨텍스트 위에서 추론한다.*

v2: **Syllva Core가 어떤 컨텍스트가 허용/관련되는지 결정해 프롬프트로 직접 제공하고, 에이전트는 그 컨텍스트 위에서만 추론한다. 에이전트는 어떤 도구도 갖지 않는다. 사람이 해야 하는 판단은 에이전트가 도달할 수 없는 경로로만 기록된다.**

### 0.2 비목표

- 클라우드 동기화, 다중 사용자, 서버 호스팅.
- 악의적인 **동일 OS 사용자 권한 프로세스**(악성코드)로부터의 방어(§5.6).
- 모바일 독립 실행. 모바일은 데스크탑 코어 접속 클라이언트(§10, R6).
- 에이전트의 자율적 도구 사용(검색 반복 호출 등). 필요가 확인되면 §7.3의 선택 어댑터로 별도 개정한다.

---

## 1. v1.2 → v2 절별 처분표

| v1.2 (설계서) | 처분 | v2 |
|---|---|---|
| §1–3 목적·핵심 원칙·시스템 역할 | **개정** | §0.1, §3 |
| §4 Authority Model (Notion authority §4.3) | **개정/삭제** | 로컬 DB가 권위. §4 |
| §5 Enforcement Model | **유지** | CODE_ENFORCED/INSTRUCTION_ENFORCED 구분 유지. 신규 규칙은 CODE_ENFORCED |
| §6 SOURCE / AI / USER | **유지, 강제 방식 개정** | 쓰기 경로·컬럼 강제. §4.3 |
| §7 Google Drive Layout, §8 Canonical Source Reference | **대체** | 로컬 content-addressed 저장소·`SourceRef` 개정. §4.1–4.2 |
| §9 Academic Identity / Natural-Language Handles | **개정** | 자연어 해석을 필수 기능으로 격상. §7.2 |
| §10 Ingestion & Revision Matching | **유지(입력 경로 개정)** | 입력 = 앱 드롭/Canvas. 수정본은 버전 체인. §4.4 |
| §11 Normalization Contract | **유지** | 정규화 ≠ 요약, Partial ≠ Ready |
| §12 Goodnotes Contract | **보류** | Spike G 미수행, v2 범위 밖. 후속 |
| §13 Notion Knowledge Model | **삭제** | SQLite 스키마로 대체. §4.5 |
| §14–15 Retrieval Engine / Policy | **유지(Reader 계약 개정)** | Notion/Drive Reader 제거 → 로컬 Reader. Core가 직접 호출. §7 |
| §16 Freshness Contract, §34 Fingerprints | **유지·적용 확대** | AI 산출물 전체 필수. §6 |
| §17–18 Source Authority / Retrieval Modes | **유지** | |
| §19–20 MCP Boundary / MCP Tool Semantics | **격하** | 필수 경계가 아님. 도구 의미는 Core 내부 **Retrieval Service**의 연산 의미로 유지. MCP는 선택 어댑터(§7.3) |
| §21 Context Package | **유지·핵심화** | 프롬프트에 주입되는 단위. §7.1 |
| §22 Read-Only MCP Capability Model | **재해석** | 에이전트 도구 0개 + 선택 MCP 어댑터가 켜지면 읽기 전용. §5 |
| §23 Credential Separation | **개정** | 워커/MCP 자격증명 분리 → 신뢰 도메인 분리. §3.2, §8 |
| §24–26 Behavior Contract / Skills / Skill vs MCP | **개정** | 도구 라우팅 규칙 → 앱이 주입하는 프롬프트 템플릿(인용 형식, 비신뢰 콘텐츠 처리). Behavior Contract v4 |
| §27–28 Client Abstraction / MCP Transport | **보류** | 외부 MCP 전송(원격 포함)은 비활성. 선택 어댑터로만 존재 |
| §29 Local-Primary Execution | **유지** | §29.1 가용성 계약은 모바일(R6)에서 재사용 |
| §30 Retrieval Manifest | **유지** | |
| §31 Privacy and Sharing | **유지** | 공개 링크 공유 금지 |
| §32 Job and Processing Status | **유지** | |
| §33 Human-Gate Write Protection | **유지, 증명 방식 대체** | §5 (신규 Human-Proof) |
| §35 GitHub Contract | **선택 기능** | "정확한 ref만 증거" 유지, 기본 꺼짐 |
| §36+ 기술 검증 순서(Spike C0/M0/VS0/G) | **대체** | R0~R6 (`docs/plans/desktop-app-v2.md` §8) |
| 구현 명세서 §9 EphemeralStore | **유지, 용도 변경** | 에이전트 세션 상태가 아니라 UI 턴 컨텍스트(allowlist) 저장. §6.1 |
| 구현 명세서 §8 SQLite 스키마 | **개정** | `drive_*`/`notion_*` 컬럼·`provider` 분기 제거. 새 마이그레이션 |
| 구현 명세서 §15.2 / §50.21 (Proposal·Approval 라이프사이클, stale approval) | **의미 유지, 구현 재작성** | 승인은 "정확한 현재 제안"에 결속, 지문 불일치 시 SUPERSEDED. §5.1(d) |
| 구현 명세서 §27 (RO 자격증명 분리) | **대체** | 신뢰 도메인 분리. §3.2, §8 |
| 구현 명세서 §28 (원격 MCP) | **보류(R6)** | |
| 구현 명세서 §54 (Security Acceptance), §55 (Cross-Platform) | **재작성** | §11, §10 |
| 구현 명세서 `study_notes` MCP (`submit_study_note_draft`, 쓰기 가능 MCP 서버) | **폐기** | Core 내부 `ai_artifacts` 경로로 흡수. 에이전트 도달 가능 쓰기 표면 0. §6 |
| `tests/contract/test_human_gate_self_approval.py` (Notion `Decision By` 결속) | **폐기 후 대체** | C-04, C-05, C-17 |
| 구현 명세서 §50 (acceptance 불변식) | **재작성** | §11 계약 테스트 목록 |

---

## 2. 불변식 (Release-blocking) — v2 판

CLAUDE.md의 핵심 불변식은 그대로 유지하고 v2에서 다음과 같이 해석한다.

| # | 불변식 | v2에서의 강제 |
|---|---|---|
| I1 | 검색 표면은 read-only | Retrieval Service는 읽기 전용 연산만 가진다. 선택 MCP 어댑터가 켜져도 같은 서비스만 노출(C-01, C-02) |
| I2 | SOURCE/AI/USER 구분, AI→SOURCE 재라벨 금지, USER 자동 덮어쓰기 금지 | 저장소 쓰기 함수가 `owner`를 인자로 받고 `owner`별 허용 호출자를 검사 (C-03) |
| I3 | 정규화 ≠ 요약, Partial은 조용히 Ready가 되지 않는다 | 상태 전이 함수에서 강제 |
| I4 | `Verified`/`Scope Confirmed` human-only | **Human-Proof** (§5) |
| I5 | 신선도는 엔진이 검증, stale enrichment는 factual evidence에서 제외 | AI 산출물 지문 필수 (§6) |
| I6 | 에이전트가 근거로 쓸 수 있는 청크는 Core가 발급한 allowlist 안으로 한정 | **턴 컨텍스트 allowlist**: 프롬프트에 넣은 evidence만 인용 가능, 인용 검증(§6, C-10, C-22) |
| I7 | 공개 링크 공유를 검색 편의에 사용하지 않음 | 유지 (§10) |
| **I8 (신규)** | **에이전트는 도구를 갖지 않는다.** 입력은 Core가 조립한 프롬프트, 출력은 텍스트뿐이다. 라이브러리·DB·토큰·키체인에 닿는 도구·경로가 없다. (OS 수준 격리는 약속하지 않는다 — §5.6) | Agent Isolation (§5.2–5.4), C-01, C-21 |
| **I9 (신규)** | 비밀정보(Canvas 토큰, UI 세션 토큰, shell secret)는 에이전트 환경·로그·프롬프트·번들에 존재하지 않는다 | 환경 스크러빙 + 프롬프트 조립 시험 (C-07, C-23) |

---

## 3. 신뢰 도메인과 프로세스 모델

### 3.1 프로세스

| 프로세스 | 수명 | 역할 | DB 접근 |
|---|---|---|---|
| **Shell** (Tauri/Rust) | 앱 | 창, 트레이, 업데이트, **사람 확인 다이얼로그(§5.1)**, sidecar 수명 | 없음 |
| **Core** (Python sidecar) | 앱 | UI HTTP 서버, Retrieval Service(검색 엔진 in-process), 프롬프트 조립, 에이전트 실행·출력 검증, 워커(수집·분류·정규화·Canvas), 모든 쓰기 | 읽기/쓰기 (**DB를 여는 유일한 프로세스**) |
| **Agent** (`claude`/`codex`) | 질문 1건 또는 대화 세션 | 프롬프트를 받아 텍스트를 반환 | **없음** |

v1.2의 `uls run`(워커 락)·`uls mcp local`·Settings 서버는 위 구조로 통합된다. 워커 단일 활성 락(`orchestration/locks.py`)은 Core 내부 불변식으로 유지한다.

### 3.2 신뢰 도메인 (v1.2 §22 "ingestion/enrichment 쓰기와 interactive AI는 별도 trust domain"의 일반화)

| 도메인 | 구성 | 허용 |
|---|---|---|
| **T0 사람** | 사용자 + Shell 네이티브 UI | 모든 human-only 확정 |
| **T1 Core** | Core 프로세스 | 쓰기. 단 human-only 필드는 T0 증명 없이는 불가 |
| **T2 Agent** | Agent 프로세스 | 텍스트 입력 → 텍스트 출력. 도구·파일·네트워크 도구 없음 |

규칙: T2는 T1의 어떤 경로에도 호출 수단이 없다(도구 없음). T1은 T0 증명 없이 human-only를 만들 수 없다(§5.1).

### 3.3 저장소와 동시성

- SQLite **WAL 모드**. **DB를 여는 프로세스는 Core 하나뿐**이다. `busy_timeout` 유지. 백업은 SQLite online backup API로 한다(파일 복사 금지).
- 구현 요건: v1.2 `SQLiteStateStore`는 생성자에서 마이그레이션을 실행하고 워커 락을 만든다(`state/sqlite.py`). Core 내부에서 **writer(`StateStore`)와 읽기 전용 리더**(`state/reader.py` 계열, `mode=ro`)를 클래스로 분리하고, Retrieval Service는 리더만 받는다. 같은 프로세스 안이므로 최소권한 구조이지 OS 격리가 아니다.
- `RetrievalEngine`은 현재 `notion_reader`/`drive_reader`를 필수 생성자 인자로 받는다(`retrieval/engine.py`). 로컬 Reader 계약은 "유지"가 아니라 **생성자·호출 지점 재작성**이다(R1b).
- 앱 데이터 디렉터리는 OS 표준 위치(`~/Library/Application Support/Syllva`, `%APPDATA%\Syllva`)이며 **에이전트 작업 디렉터리 밖**이다(§5.2). (v1.2의 `~/.uls`·`workspace_dir` 기본값은 R1b에서 폐기.)

---

## 4. 저장소 모델

### 4.1 로컬 라이브러리 (v1.2 §7 대체)

- **콘텐츠 주소 지정 파일 저장소**: `blobs/<sha256 앞 2자>/<sha256>`. 원본은 불변이며 삭제는 참조 해제 후 GC.
- 사용자가 보는 "파일 위치"는 DB의 논리 경로(과목/종류/날짜)이며 디스크 레이아웃과 무관하다.
- 원본 파일명은 NFC로 정규화해 보존한다(macOS NFD 문제 재발 방지).

### 4.2 `SourceRef` / `SourceFingerprint` / `Locator` (v1.2 §8, 구현 명세서 §6 개정)

- `SourceRef.provider`: `google_drive` 단일 제약 제거 → `local`(기본), `canvas`, `github`(선택). `local`의 식별자는 `blob_sha256` + 논리 `source_id`.
- `SourceFingerprint`와 `Locator`의 **의미는 변경 없음**: 지문이 바뀌면 이전 locator는 새 버전에 대해 신뢰하지 않는다(구현 명세서 §1.4 stale locator).
- 변경은 `provider` 값 집합과 식별자 구성뿐이다. 해시·승인 ID에 들어가는 고정 문자열(`uls.*.v1`)은 R1b에서 한 번에 새 이름으로 재발급한다(데이터 이관 없음).

### 4.3 소유 구분 강제 (I2)

- 모든 레코드/청크/산출물에 `owner ∈ {SOURCE, AI, USER}` 컬럼.
- 쓰기 API는 `(owner, actor)` 쌍을 검사한다: `SOURCE` ← 수집기만, `AI` ← Core(검증된 에이전트 출력을 저장하는 경우만), `USER` ← UI 경로만. `AI`→`SOURCE` 변경 함수는 존재하지 않는다.
- `USER` 레코드의 갱신은 UI 경로에서 낸 명시적 편집만 허용(자동 덮어쓰기 금지).

### 4.4 수정본 교체 (버전 체인)

새 파일이 기존 자료의 수정본이면 사용자 확인 후 **새 `source_version`** 으로 체인에 연결한다. 이전 버전의 locator/턴 컨텍스트는 무효, 이전 버전 기반 AI 산출물은 STALE(§6).

### 4.5 스키마 (구현 명세서 §8 대체 — 요지)

`source_files`, `source_versions`, `blobs`, `processing_records`, `jobs`, `checkpoints`, `entity_allocations`(유지·개정) + 학업 개체 `courses/sessions/materials/activities/exams` + `material_usage`(Verified 포함) + `ai_artifacts`(§6) + `human_gate_log`(§5.5). Notion 스냅샷·receipt·intent 계열 테이블은 제거(로컬 단일 트랜잭션으로 단순화). 상세 DDL은 구현 명세서에서 정의한다.

---

## 5. Human-Proof 모델과 Agent Isolation (규범적)

### 5.1 Human-Proof — v1.2 §33 대체

human-only 필드는 다음 **전부**를 만족하는 요청으로만 변경된다.

- 대상: `Material Usage.Verified = true`, `Exam.Scope Confirmed = true`, 과제 제출 상태 변경, 문제/요약의 USER 승격, 기타 §5.5 목록.
- **(a) UI 세션**: 유효한 UI 세션 토큰 + CSRF + same-origin 검사(v1.2 `settings/security.py` 계열 유지).
- **(b) 네이티브 사용자 제스처**: Core가 요청 해시(`action`, `target_id`, `canonical_params_hash`, **요약 문자열**, 대상 지문)에 대한 **일회용 nonce**를 발급 → Shell이 **OS 네이티브 다이얼로그**(웹뷰 JS가 아닌 Rust 측)로 요약을 표시 → 사용자가 확인하면 **Shell만이 가진 `shell_secret`** 으로 HMAC(`nonce ‖ request_hash`)을 계산해 Core에 제출.
- **(c) Core 검증**: nonce 미사용·미만료(짧은 TTL), request_hash 일치, HMAC 유효일 때만 커밋. (에이전트에게 도구가 없으므로 rev 2의 "비-baseline 에이전트 세션 없음" 조건과 unrestricted 모드는 삭제했다. 사용자가 별도로 실행하는 CLI 터미널은 `shell_secret`·UI 토큰을 갖지 못하므로 human-only 쓰기를 만들 수 없다.)
- **(d) 대상 재검증**: 요청 해시는 대상의 **현재 지문/제안 버전**을 포함한다. 커밋 시점에 대상이 바뀌었으면(구현 명세서 §15.2.2 stale approval) 쓰기를 거부하고 해당 제안을 `SUPERSEDED`로 표시한다.
- **요약 결속(TOCTOU 방지)**: 다이얼로그의 "사람이 읽는 요약"은 **Core가 canonical params에서 서버측으로 생성**한다. 웹뷰는 `nonce_id`만 Shell에 넘기며, Shell이 Core에 직접 질의(자신의 인증 채널)해 요약을 받아 표시한다. 요약 문자열은 `request_hash` 입력에 포함되어 HMAC에 묶인다. 웹뷰가 요약 텍스트를 공급하는 경로는 존재하지 않는다.

`shell_secret`은 Shell이 sidecar 기동 시 **stdin 파이프로만** 전달(환경변수·파일·명령행 금지)하고 Core 메모리에만 보관한다.

### 5.2 Agent Isolation — 에이전트 실행 baseline

Core의 단일 함수 `build_agent_launch(adapter, turn)`가 모든 에이전트 실행 인자·환경·작업 디렉터리를 생성한다. 다른 경로로 에이전트를 띄우는 것은 금지된다(C-09).

1. **도구 없음**: 모델에 노출되는 도구는 **하나도 없다**. 내장 셸·파일·편집·웹·서브에이전트 도구와 MCP 서버 모두 비활성.
   - Claude Code: `-p --output-format stream-json`(또는 `json`), `--tools ""`, 빈 MCP 설정과 `--strict-mcp-config`, `--permission-mode dontAsk`(또는 동등한 비대화형), 필요 시 `--disallowedTools "*"`로 이중 차단. 정확한 플래그 조합은 R4 스파이크에서 적합성 시험(7번)을 통과하는 구성으로 확정한다.
   - Codex CLI: 공식 문서 재조사 전(미확정). 적합성 시험을 통과하기 전까지 어댑터를 활성화하지 않는다. 대체는 경로 B(사용자가 조작하는 내장 터미널, 라이브러리 접근 없음) 또는 C(API 키).
2. **작업 디렉터리**: 에이전트 cwd는 비어 있는 임시 디렉터리(세션 종료 시 삭제). 앱 데이터 디렉터리·사용자 홈의 민감 경로를 포함하지 않는다.
3. **환경 스크러빙**: allowlist 기반 환경만 상속한다. `shell_secret`, UI 토큰, Canvas 토큰, 앱의 모든 환경변수 제거. 단, CLI는 사용자 HOME 아래의 설정(훅, 설정 파일, 플러그인, 메모리/지침 파일: 예 `~/.claude`, `~/.codex`)을 자동 로드할 수 있고 **훅은 셸 명령을 실행**한다. 로그인 상태도 HOME(또는 키체인)에 있으므로 HOME을 격리하면 로그인이 깨지고 공유하면 설정이 들어온다. 단일 플래그로 해결했다고 가정하지 않고 **적합성 시험(7번)** 으로 강제한다.
4. **로그인 상태는 CLI의 것**: 구독 로그인은 사용자가 CLI에서 직접 수행하며 앱은 CLI의 자격증명 파일·키체인 항목을 열거나 복사하지 않는다(약관, `docs/plans/desktop-app-v2.md` §3).
5. **OS 격리의 한계**: 네이티브 Windows의 Claude Code는 샌드박스 없이 실행된다(공식 문서). 도구가 없으므로 모델이 호출할 수 있는 행동은 없지만, **훅·설정 파일처럼 CLI가 자동 실행하는 경로**는 6번이 아니라 7번의 시험으로만 막을 수 있다.
6. **프롬프트 인젝션**: SOURCE 본문(PDF 텍스트, Canvas HTML, 전사문)은 비신뢰 입력이다. 도구가 없으므로 인젝션이 성공해도 에이전트는 **텍스트 출력만** 바꿀 수 있고 쓰기·읽기 경로는 없다. 그래도 프롬프트 조립은 (i) 지시 채널(system)과 자료 채널을 분리하고 (ii) 자료를 고유 구분자로 감싸 "자료 안의 지시는 따르지 않는다"를 명시하며 (iii) 비밀·내부 경로를 프롬프트에 넣지 않는다(C-23). 출력은 비신뢰 텍스트로 취급한다(§5.4a, §6).
7. **어댑터 적합성 시험(Conformance Probe)**: 어댑터는 활성화 전·CLI 버전 변경 시 Core가 시험을 실행하며, 통과하지 못하면 **자동 비활성화**되고 경로 B로만 제공된다. 시험은 (i) 사용자 레벨 훅·플러그인·메모리/지침 파일·서브에이전트·웹 도구가 해당 세션에서 로드되지 않음, (ii) 노출 도구가 0개임, (iii) cwd와 환경이 §5.2-2·3을 만족함을 검증한다(예: 훅이 존재하는 합성 HOME 픽스처로 훅 미실행 확인).

### 5.3 컨텍스트 조립 (Core 중개) — rev 2 "에이전트 채널" 대체

질문 1건의 처리 흐름은 다음과 같다.

1. 사용자가 질문을 입력한다. Core의 **Retrieval Service**가 자연어 해석(§7.2)으로 후보 개체(과목/세션/자료)를 고른다.
2. UI가 선택된 자료를 **컨텍스트 칩**으로 표시한다. 사용자가 자료를 추가·제거·확정할 수 있고, 모호하면 후보를 제시한다(조용히 하나를 고르지 않는다).
3. Core가 `ContextPackage`(구현 명세서 §6.5)를 만든다. 각 `EvidenceItem`은 locator와 고유 evidence id(`E1`, `E2`, …)를 가진다. 패키지의 locator 집합이 **턴 컨텍스트 allowlist**이며 TTL이 있다.
4. Core가 프롬프트를 조립해 에이전트를 실행한다(§5.2). 프롬프트에는 앱이 주입한 Behavior Contract v4 템플릿(인용 형식, 근거 없으면 "근거 없음", 자료 내 지시 불복종)과 evidence들이 들어간다.
5. 에이전트의 출력은 Core가 검증한다(§6): 인용된 evidence id가 패키지 안에 있는지, stale 여부, 근거 없는 주장 표시. 에이전트가 더 많은 자료를 요구하는 표준 채널은 없다. 더 필요하면 사용자가 칩을 추가하거나 "더 보기"로 Core가 청크를 확장한다(사람이 개입하는 확장).

사용자가 보지 않은 자료가 구독 제공자로 나가지 않도록, 전송 대상은 칩 목록과 동일하다(§5.6).

### 5.4 에이전트가 쓸 수 있는 표면

에이전트에게는 도구가 없으므로 HTTP 서버·DB·파일시스템에 접근할 수단이 없다. 그래도 구조로 심층 방어한다.

- UI HTTP 서버는 Core의 `127.0.0.1` 바인딩이며 UI 세션 토큰이 필요하다. 토큰은 Shell→웹뷰 경로로만 전달되고 에이전트 환경에 없다(I9).
- human-only 엔드포인트는 §5.1(b)(c)가 없으면 항상 거부된다.

### 5.4a UI 콘텐츠 안전 (XSS)

AI 산출물·SOURCE 본문(PDF 추출 텍스트, Canvas HTML, 요약)은 UI 세션 토큰을 가진 웹뷰에 렌더링된다. 저장·반사 XSS 한 건이면 §5.1(a)가 자동 충족되고 (b)의 요청도 웹뷰에서 시작될 수 있다. 따라서 다음을 규범으로 한다.

- 모든 AI/SOURCE/USER 텍스트는 **기본적으로 text로만 렌더링**한다(`textContent`). 마크다운은 허용 목록 방식 sanitizer를 거친 안전한 부분집합만 렌더링하고, 원시 HTML·`javascript:`·이벤트 핸들러·원격 이미지는 거부한다.
- CSP: `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'`(인라인 스크립트 금지). v1.2 Settings 화면의 CSP·`textContent` 관행(`settings/security.py`, `static/app.js`)을 v2 UI 전체의 기준선으로 확대한다.
- 네이티브 확인 커맨드는 Tauri IPC에서 **`nonce_id` 하나만 받는** 단일 커맨드이며, 표시 내용은 Core가 생성한 요약이다(§5.1). 웹뷰가 임의 문자열을 다이얼로그에 넣을 수 없다. 남는 위험은 XSS가 *진짜 요약을 가진 다이얼로그를 띄우는 것*뿐이며, 사용자가 읽고 거절할 수 있다.
- Tauri는 원격 URL(`127.0.0.1`)에 IPC capability를 `remote.urls`로 열어야 하며 그 origin의 모든 스크립트가 허용 커맨드를 호출할 수 있다. 그러므로 허용 커맨드는 최소(위 확인 커맨드 + 창 제어)로 제한한다.

### 5.5 human-only 쓰기 목록과 감사 로그

| 행위 | 비고 |
|---|---|
| `Material Usage.Verified=true` | v1.2 §33 |
| `Exam.Scope Confirmed=true` | v1.2 §33 |
| 과제 제출 상태 변경 | v2 신규 |
| AI 산출물 → USER 승격(노트로 확정) | v2 신규 |

`human_gate_log`: `(id, action, target_id, request_hash, nonce_id, confirmed_at, shell_version)`. 로그에 비밀·본문을 남기지 않는다.

### 5.6 위협 모델과 잔여 위험 (정직한 한계)

방어 대상: **프롬프트 인젝션된 에이전트 출력**, 에이전트 출력으로 인한 UI 공격(XSS), 오류로 인한 AI 산출물 승격, 에이전트 경로로의 승인 위조.

방어 대상 아님(명시): 동일 OS 사용자 권한으로 실행되는 **악성 프로세스**. 그런 프로세스는 DB를 직접 수정하거나 Shell 메모리를 읽을 수 있다. 이 수준의 방어는 OS의 몫이며 v2는 약속하지 않는다.

**데이터 이동(공식 한계)**: Core가 프롬프트에 넣은 자료는 구독 제공자(Anthropic/OpenAI)로 전송된다. v2의 Local-primary는 **저장과 권위**가 로컬이라는 뜻이며 "추론 시 데이터가 로컬에 머문다"가 아니다. 완화: 전송 대상은 사용자가 확인한 컨텍스트 칩 목록으로 한정하고(§5.3) 대화 시작 시 UI가 이를 고지한다.

**잔여 위험**: 인젝션된 자료가 출력을 사실처럼 오도할 수 있다. 완화는 인용 검증과 "근거 없음" 표시(§6)이며, 사용자가 인용 근거를 열어 볼 수 있어야 한다. 완전 방지는 약속하지 않는다.

---

## 6. AI 산출물과 신선도

- 요약·문제·해설·분류 제안 등 모든 **AI 산출물**은 `ai_artifacts`에 저장하며 `owner=AI`, `based_on{source_version, source_hash}`, `processor_version`, `agent_adapter`, `prompt_template_version`, `evidence_locators[]`을 **필수**로 가진다. 필수 필드가 없으면 저장을 거부한다(C-06).
- 저장 주체는 항상 **Core**다. 에이전트는 텍스트만 반환하고, Core가 출력의 **인용 id를 파싱해 턴 컨텍스트 allowlist와 대조**한 뒤 `evidence_locators`를 채워 저장한다. 패키지 밖 id를 인용한 주장은 "근거 없음"으로 표시하고 사실 근거로 쓰지 않는다(C-22). v1.2의 `study_notes` 쓰기 MCP(`submit_study_note_draft`)는 폐기하고 이 경로로 흡수한다.
- 검색 엔진은 반환 전에 `is_stale`을 판정한다. stale 산출물은 라우팅/색인에는 쓸 수 있으나 **현재 사실 근거로는 제외**한다(v1.2 §16 유지).
- 근거 인용이 없는 문제·요약은 "근거 없음"으로 표시하고 시험 대비 기능에서 사실 근거로 쓰지 않는다.
- 사용자가 AI 산출물을 노트로 채택하는 것은 §5.5의 human-only 행위이며, 채택 후에도 원본 `AI` 레코드는 변경되지 않고 `USER` 레코드가 새로 만들어진다.

### 6.1 EphemeralStore 위치와 용도

`EphemeralStore`(구현 명세서 §9)는 **Core 프로세스의** `MemoryEphemeralStore` 한 인스턴스이며 용도는 (i) UI의 자료 선택 세션(`resolution` — 사용자가 후보를 고르는 흐름), (ii) 턴 컨텍스트 allowlist(`context capability` — locator 집합, TTL 15분 기본)다. 에이전트 세션 상태가 아니다. Core 재시작 시 모두 무효(§9.5와 동일), 사용자는 같은 질문을 다시 보내면 된다.

---

## 7. 검색 계약 변경

### 7.1 Retrieval Service

v1.2의 도구 의미(`resolve_entity`, `select_resolution`, `get_material_context`, `get_session_context`, `search_concept`, `get_exam_context`, `get_activity_context`, `get_user_context`, `verify_claim`, `get_source_chunk`)는 Core 내부 **Retrieval Service의 연산**으로 유지한다. 호출자는 Core(UI 요청 처리, 컨텍스트 조립, 시험 대비 생성)뿐이며 모두 읽기 전용이다. `RetrievalEngine`은 MCP 없이 직접 호출 가능해야 한다는 기존 규칙(CLAUDE.md)이 그대로 이점이 된다. 연산 이름 접두사(`uls.*`)는 MCP 어댑터를 켤 때만 노출 이름으로 의미가 있으며 R1b에서 `syllva.*`로 바꾼다.

### 7.2 자연어 해석 (v1.2 §9·구현 명세서 §10 개정, 필수 요건)

실사용(handoff, 2026-10-14)에서 ID 없이 자료를 못 찾는 것이 최대 결함이었다. 도구 없는 구조에서는 **자료 선택의 품질이 곧 답변의 품질**이므로 이 요건이 더 중요하다. Core의 resolver는 다음을 **반드시** 해석한다: 과목 이름/코드/별칭, 주차("4주차"), 날짜, 자료 제목의 부분 일치, 한·영 혼용 별칭. 모호하면 후보를 칩으로 제시한다(조용히 하나를 고르지 않는다).
완료 기준: 질의 목록(`tests/fixtures/resolve_queries.yaml`, 예: "TCP", "Chapter 04", "4주차", "09.17", 과목 이름)에 대해 기대 개체를 반환하거나 모호성 후보를 반환.

**선택적 개선(결정적 resolver가 부족할 때만)**: 에이전트에게 *도구 없이* 질문과 **개체 카탈로그(과목/세션/자료의 제목·별칭만, 본문 없음)** 를 주고 후보 id를 JSON으로 반환받는다. Core는 id를 카탈로그와 대조해 유효한 것만 쓰고, 최종 선택은 사용자 확인을 거친다. 이 호출은 본문을 전송하지 않으므로 §5.6 노출이 작다.

### 7.3 MCP (선택 어댑터)

MCP 서버/전송(`syllva mcp ...`)은 **기본 비활성, 외부 노출 없음**이다. 필요가 확인되어(예: 자유 대화에서 사전 선택된 컨텍스트가 부족) 다시 도입할 때는 **별도 설계 개정**을 거친다. 그 개정은 최소한 다음을 다뤄야 한다: 에이전트 도구 표면 격리, 범위 강제를 Core가 하는 구조(프록시 방식), Grant, 적합성 시험 확장, C-01/C-02/C-11/C-23 확장. 현재 코드의 MCP 모듈은 R1에서 삭제하지 않고 선택 어댑터로 격리해 둔다(GitHub 어댑터와 같은 취급).

---

## 8. 자격증명

| 비밀 | 보관 | 에이전트 접근 |
|---|---|---|
| Canvas 토큰 | OS 키체인(`keyring`) | 없음 |
| UI 세션 토큰 | Core 메모리 + Shell | 없음 |
| `shell_secret` | Shell 메모리 → Core 메모리(stdin) | 없음 |
| CLI 구독 로그인 | **각 CLI 소유** (앱은 열거·복사·저장하지 않음) | CLI 프로세스 자체만 |

v1.2의 "워커 vs MCP 자격증명 분리"(§23)는 Drive/Notion이 사라지므로 위 표의 **도메인 분리**로 대체된다. 비밀은 커밋·로그·프롬프트·번들에 포함하지 않는다(I9).

---

## 9. 오류·실패 원칙 (v1.2 유지)

fail-closed. 증명 불가는 거부. `Partial`은 `Ready`가 아니다. 부분 실패한 수집은 사용자에게 보인다. 에이전트 실행 실패는 사용자에게 명시하고 재시도하며, 조용히 다른 경로로 대체하지 않는다.

---

## 10. 플랫폼·접속

- **Windows 동등 지원**: 경로(긴 경로·대소문자·NFC), 파일 잠금, 프로세스 종료, 키체인(Credential Manager)을 CI 매트릭스에 포함.
- **모바일(R6)**: 데스크탑 Core에 접속하는 클라이언트. v1.2 §29.1 가용성 계약(데스크탑이 켜져 있어야 함)을 그대로 적용. 서버층은 루프백 전용 전제(`LOOPBACK_PEERS`)를 깨므로 **페어링·TLS·장치별 토큰을 별도 개정**으로 설계한다. human-only 확정은 모바일에서 불가(데스크탑 Shell 확인 필수) — 개정 전까지 모바일은 읽기+비-human 쓰기만.
- anyone-with-link 금지(I7).

---

## 11. 계약 테스트 (구현 전 작성 — 릴리스 블로커)

| ID | 검증 |
|---|---|
| C-01 | 에이전트 실행 인자에 도구가 없고(MCP 구성 비어 있음, 도구 비활성), 저장소 전체에 에이전트가 도달할 수 있는 쓰기 서버가 없음(`study_notes` 쓰기 MCP 폐기 포함). 선택 MCP 어댑터가 존재하면 기본 비활성이고 읽기 전용 |
| C-02 | Retrieval Service가 받는 상태 접근자가 읽기 전용이며 쓰기 시 오류 |
| C-03 | `(owner, actor)` 위반 쓰기 거부: AI→SOURCE, 에이전트 출력→USER, 자동→USER 덮어쓰기 |
| C-04 | human-only 엔드포인트: UI 토큰만으로 거부, nonce만으로 거부, 유효 HMAC 없이 거부 |
| C-05 | nonce 재사용·만료·request_hash 불일치 거부; 대상 지문 변경 시 거부+SUPERSEDED |
| C-06 | AI 산출물 저장 시 지문·근거 필드 누락 거부; stale 산출물이 factual evidence에서 제외 |
| C-07 | `build_agent_launch()`가 만든 환경·인자·설정 파일에 앱 비밀(`shell_secret`, UI/Canvas 토큰)이 없음 |
| C-08 | 에이전트 cwd가 앱 데이터 디렉터리와 겹치지 않고, DB 경로가 cwd 하위가 아님 |
| C-09 | Python(`subprocess`, `os.exec*/spawn*`, `asyncio.create_subprocess_*`, `pty`)과 Rust(`Command`, Tauri shell 플러그인) 양쪽에서 `build_agent_launch`/지정된 Shell 경로 외 에이전트 CLI spawn이 없음 |
| C-10 | 턴 컨텍스트 allowlist: 패키지 밖 locator 인용, 만료된 컨텍스트, 이전 `source_version` locator 거부 |
| C-11 | 턴 컨텍스트 수명: TTL 만료·Core 재시작 후 무효, 다른 사용자 턴의 컨텍스트 재사용 불가 |
| C-12 | 정규화 Partial이 Ready로 전이되지 않음; 요약이 정규화 산출물로 저장되지 않음 |
| C-13 | 공개 공유/원격 노출 경로가 기본 설정에서 존재하지 않음 (MCP 포함) |
| C-14 | resolve 질의 목록(§7.2) 통과 |
| C-15 | Behavior Contract 해시·프로젝션 드리프트 린트 통과(v4) |
| C-16 | Windows/macOS 양쪽에서 C-01~C-23 중 OS 비의존 항목 통과 |
| C-17 | 승인 시점 대상 재검증: 제안/지문 변경 후 승인 거부+SUPERSEDED, 자기 승인(에이전트 경로) 불가 (구 `test_human_gate_self_approval` 대체) |
| C-18 | UI 렌더링: 악성 HTML/스크립트/`javascript:`가 포함된 AI·SOURCE 텍스트가 실행되지 않음; CSP 헤더 기준선 |
| C-19 | 확인 다이얼로그 요약은 Core 생성이며 `request_hash`에 포함됨; 웹뷰가 공급한 요약은 무시 |
| C-20 | `shell_secret`이 Core 로그·크래시 덤프·자식 프로세스 환경·HTTP 응답에 나타나지 않음; Shell/Core 재시작 시 secret 재협상, 이전 nonce 무효 |
| C-21 | 어댑터 적합성 시험: 합성 HOME(훅/플러그인/메모리 파일 포함)에서 훅 미실행·도구 0개; 실패 시 어댑터 자동 비활성 |
| C-22 | 인용 검증: 출력의 evidence id가 패키지 밖이면 "근거 없음" 처리, `ai_artifacts.evidence_locators`에 패키지 밖 locator가 저장되지 않음 |
| C-23 | 프롬프트 조립: 시스템 지시와 자료 채널 분리, 자료 구분자 래핑, 프롬프트에 비밀·내부 경로 없음, 컨텍스트 크기 한도 준수, 칩에 없는 자료가 전송되지 않음 |

---

## 12. 열린 항목 (구현 명세서 작성 전)

해결됨: Scope Grant·에이전트 채널·MCP 프록시(삭제), MCP DB 접근(MCP 없음), study-notes 쓰기 MCP(폐기), WAL 2차 프로세스 문제(발생하지 않음).

1. Codex CLI: 공식 문서(도메인 허용 후) 재조사로 §5.2 baseline·적합성 시험 통과 가능 여부와 약관 확인. 그 전까지 Codex 어댑터 비활성.
2. Claude Code를 앱이 구동하는 형태의 Commercial Terms 적용 여부(법무 확인).
3. R2 스파이크: Tauri 원격 URL에서 단일 네이티브 확인 커맨드 호출, `shell_secret` stdin 전달, Shell→Core 직접 질의 채널이 실제로 구성되는지. 불가하면 §5.1(b)를 대체할 동등한 증명을 이 문서에서 개정한다.
4. R4 스파이크: Claude Code가 적합성 시험(C-21)을 통과하는 플래그·HOME 구성(도구 0개, 훅·플러그인·메모리 미로드). 못 찾으면 경로 B만 제공.
5. 프롬프트 조립 상세: 컨텍스트 토큰 예산, 청크 선택 전략, 인용 id 형식, 다중 턴 대화에서 컨텍스트 유지/갱신 규칙.
6. 사전 선택 컨텍스트가 부족할 때의 보완 순서: 결정적 resolver 개선 → 카탈로그 기반 계획 호출(§7.2) → 사용자 "더 보기" → (마지막) MCP 선택 어댑터(§7.3).
7. 데이터 모델 상세 DDL, 분류 파이프라인(Notion 의존 제거)의 로컬 큐/영수증 모델.
8. GitHub 선택 어댑터의 활성화 UX.
9. 구독 제공자로의 데이터 전송 고지 UX(§5.6).
10. 이 개정(rev 3)에 대한 재검토 1회 후 사용자 승인.
