# Syllva v2 — Design Revision (DRAFT, R0)

상태: **초안 — 사용자 승인 전, 구현 금지.** 승인되면 `v2-design-frozen`으로 동결하고 구현 명세서를 별도로 작성한다.
작성: 2026-11-02 · 기반: `docs/plans/desktop-app-v2.md` (rev 2), 동결 v1.2 설계서·구현 명세서
관계: 이 문서는 v1.2 동결 문서를 **절 단위로 대체/유지/삭제**한다. 명시되지 않은 v1.2 계약은 그대로 유효하다. 충돌하면 승인된 이 문서가 우선한다(CLAUDE.md 개정 후).

---

## 0. 목적과 범위

v1.2는 "MCP 중심, Notion/Drive 기반의 개인 학업 지식·검색 시스템"이었다. v2는 같은 신뢰 불변식을 유지하면서 제품 형태를 **로컬 우선 데스크탑 앱**으로 바꾼다.

- 저장소: 로컬 라이브러리 + SQLite 단일화. Notion·Drive 제거. GitHub 소스 어댑터는 선택 기능.
- UI: 코어가 서빙하는 HTML UI를 Tauri 2 셸이 표시. Windows·macOS 동시, 모바일은 후속.
- AI: 공식 CLI 에이전트(Claude Code, Codex CLI)를 앱이 서브프로세스로 구동. 앱은 구독 토큰을 읽거나 저장하지 않는다.
- 기능: 자료 보관·분류, Canvas 연동, 요약, 과제 관리, 시험 대비.

### 0.1 개정된 핵심 원칙

v1.2: *ULS가 어떤 컨텍스트가 허용/관련되는지 결정하고, AI 클라이언트는 ULS가 제공한 컨텍스트 위에서 추론한다.*

v2: **Syllva Core가 어떤 컨텍스트가 허용/관련되는지 결정하고, 에이전트는 Core가 발급한 읽기 전용 범위 안에서만 추론한다. 사람이 해야 하는 판단은 에이전트가 도달할 수 없는 경로로만 기록된다.**

### 0.2 비목표

- 클라우드 동기화, 다중 사용자, 서버 호스팅.
- 악의적인 **동일 OS 사용자 권한 프로세스**(악성코드)로부터의 방어. 이 위협 모델은 §5.6에 명시한다.
- 모바일 독립 실행. 모바일은 데스크탑 코어 접속 클라이언트(§10, R6).

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
| §14–15 Retrieval Engine / Policy | **유지(Reader 계약 개정)** | Notion/Drive Reader 제거 → 로컬 Reader. §7 |
| §16 Freshness Contract, §34 Fingerprints | **유지·적용 확대** | AI 산출물 전체 필수. §6 |
| §17–18 Source Authority / Retrieval Modes | **유지** | |
| §19–20 MCP Boundary / Tool Semantics | **개정** | 외부 클라이언트용이 아닌 에이전트용 내부 계약. 도구 의미 유지. §5.1, §7.1 |
| §21 Context Package | **유지** | |
| §22 Read-Only MCP Capability Model | **유지·강화** | 에이전트 도구 정책과 결합. §5 |
| §23 Credential Separation | **개정** | 워커/MCP 자격증명 분리 → 신뢰 도메인 분리. §3.2, §8 |
| §24–26 Behavior Contract / Skills / Skill vs MCP | **개정** | 앱이 에이전트 시스템 프롬프트에 직접 주입. Behavior Contract v4 |
| §27–28 Client Abstraction / MCP Transport | **개정** | 원격 MCP 제거(R6 전까지). 로컬 stdio만 |
| §29 Local-Primary Execution | **유지** | §29.1 가용성 계약은 모바일(R6)에서 재사용 |
| §30 Retrieval Manifest | **유지** | |
| §31 Privacy and Sharing | **유지** | 공개 링크 공유 금지 |
| §32 Job and Processing Status | **유지** | |
| §33 Human-Gate Write Protection | **유지, 증명 방식 대체** | §5 (신규 Human-Proof) |
| §35 GitHub Contract | **선택 기능** | "정확한 ref만 증거" 유지, 기본 꺼짐 |
| §36+ 기술 검증 순서(Spike C0/M0/VS0/G) | **대체** | R0~R6 (`docs/plans/desktop-app-v2.md` §8) |
| 구현 명세서 §9 EphemeralStore | **유지** | §6.3에서 위치 명시 |
| 구현 명세서 §8 SQLite 스키마 | **개정** | `drive_*`/`notion_*` 컬럼·`provider` 분기 제거. 새 마이그레이션 |
| 구현 명세서 §50 (acceptance 불변식) | **재작성** | §11 계약 테스트 목록 |

---

## 2. 불변식 (Release-blocking) — v2 판

CLAUDE.md의 핵심 불변식은 그대로 유지하고 v2에서 다음과 같이 해석한다.

| # | 불변식 | v2에서의 강제 |
|---|---|---|
| I1 | MCP 검색 표면은 read-only | MCP 프로세스는 SQLite를 `mode=ro`로만 열고 쓰기 도구를 등록하지 않는다(C-01, C-02) |
| I2 | SOURCE/AI/USER 구분, AI→SOURCE 재라벨 금지, USER 자동 덮어쓰기 금지 | 저장소 쓰기 함수가 `owner`를 인자로 받고 `owner`별 허용 호출자를 검사 (C-03) |
| I3 | 정규화 ≠ 요약, Partial은 조용히 Ready가 되지 않는다 | 상태 전이 함수에서 강제 |
| I4 | `Verified`/`Scope Confirmed` human-only | **Human-Proof** (§5) |
| I5 | 신선도는 엔진이 검증, stale enrichment는 factual evidence에서 제외 | AI 산출물 지문 필수 (§6) |
| I6 | `get_source_chunk`는 발급된 capability allowlist 안 locator만 | 유지 (§6.3) |
| I7 | 공개 링크 공유를 검색 편의에 사용하지 않음 | 유지 (§10) |
| **I8 (신규)** | 에이전트는 라이브러리·DB·토큰·키체인에 MCP 외 경로로 접근할 수 없다 | Agent Isolation (§5.2–5.4) |
| **I9 (신규)** | 비밀정보(Canvas 토큰, UI 세션 토큰, shell secret)는 에이전트 환경·로그·프롬프트·번들에 존재하지 않는다 | 환경 스크러빙 + 테스트 (C-07) |

---

## 3. 신뢰 도메인과 프로세스 모델

### 3.1 프로세스

| 프로세스 | 수명 | 역할 | DB 접근 |
|---|---|---|---|
| **Shell** (Tauri/Rust) | 앱 | 창, 트레이, 업데이트, **사람 확인 다이얼로그(§5.3)**, sidecar 수명 | 없음 |
| **Core** (Python sidecar) | 앱 | HTTP UI 서버, 워커(수집·분류·정규화·Canvas), 에이전트 관리, 모든 쓰기 | 읽기/쓰기 (유일한 writer) |
| **Agent** (`claude`/`codex`) | 대화 세션 | 사용자 질문에 추론 | **없음** |
| **MCP** (Python, Agent가 자식으로 spawn) | 대화 세션 | 읽기 전용 검색 도구 | `mode=ro` (읽기) |

v1.2의 `uls run`(워커 락)·`uls mcp local`·Settings 서버는 위 구조로 통합된다. 워커 단일 활성 락(`orchestration/locks.py`)은 Core 내부 불변식으로 유지한다.

### 3.2 신뢰 도메인 (v1.2 §22 "ingestion/enrichment 쓰기와 interactive AI retrieval은 별도 trust domain"의 일반화)

| 도메인 | 구성 | 허용 |
|---|---|---|
| **T0 사람** | 사용자 + Shell 네이티브 UI | 모든 human-only 확정 |
| **T1 Core** | Core 프로세스 | 쓰기. 단 human-only 필드는 T0 증명 없이는 불가 |
| **T2 Agent** | Agent + MCP 자식 | 범위가 정해진 읽기만. 어떤 쓰기도 불가 |

규칙: T2는 T1의 쓰기 경로에 어떤 방식으로도 도달할 수 없다(§5.4). T1은 T0 증명 없이 human-only를 만들 수 없다(§5.1).

### 3.3 저장소와 동시성

- SQLite **WAL 모드**, Core만 writer. MCP 자식은 `mode=ro` 연결. `busy_timeout` 유지.
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
- 쓰기 API는 `(owner, actor)` 쌍을 검사한다: `SOURCE` ← 수집기만, `AI` ← Core(에이전트 출력을 받아 저장하는 경우만), `USER` ← UI 경로만. `AI`→`SOURCE` 변경 함수는 존재하지 않는다.
- `USER` 레코드의 갱신은 UI 경로에서 낸 명시적 편집만 허용(자동 덮어쓰기 금지).

### 4.4 수정본 교체 (버전 체인)

새 파일이 기존 자료의 수정본이면 사용자 확인 후 **새 `source_version`** 으로 체인에 연결한다. 이전 버전에서 발급된 capability/locator는 무효, 이전 버전 기반 AI 산출물은 STALE(§6).

### 4.5 스키마 (구현 명세서 §8 대체 — 요지)

`source_files`, `source_versions`, `blobs`, `processing_records`, `jobs`, `checkpoints`, `entity_allocations`(유지·개정) + 학업 개체 `courses/sessions/materials/activities/exams` + `material_usage`(Verified 포함) + `ai_artifacts`(§6) + `human_gate_log`(§5.5). Notion 스냅샷·receipt·intent 계열 테이블은 제거(로컬 단일 트랜잭션으로 단순화). 상세 DDL은 구현 명세서에서 정의한다.

---

## 5. Human-Proof 모델 (규범적) — v1.2 §33 대체

### 5.1 요구

human-only 필드는 다음 **전부**를 만족하는 요청으로만 변경된다.

- 대상: `Material Usage.Verified = true`, `Exam.Scope Confirmed = true`, 과제 제출 상태 변경, 문제/요약의 USER 승격, 기타 §5.5 목록.
- **(a) UI 세션**: 유효한 UI 세션 토큰 + CSRF + same-origin 검사(v1.2 `settings/security.py` 계열 유지).
- **(b) 네이티브 사용자 제스처**: Core가 요청 해시(`action`, `target_id`, `canonical_params_hash`)에 대한 **일회용 nonce**를 발급 → Shell이 **OS 네이티브 다이얼로그**(웹뷰 JS가 아닌 Rust 측)로 사람이 읽을 수 있는 요약을 표시 → 사용자가 확인하면 **Shell만이 가진 `shell_secret`** 으로 HMAC(`nonce ‖ request_hash`)을 계산해 Core에 제출.
- **(c) Core 검증**: nonce 미사용·미만료(짧은 TTL), request_hash 일치, HMAC 유효, **현재 비제한 에이전트 세션이 없음**(§5.2의 baseline 위반 세션이 살아 있으면 거부)일 때만 쓰기를 커밋.

`shell_secret`은 Shell이 sidecar 기동 시 **stdin 파이프로만** 전달(환경변수·파일·명령행 금지)하고 Core 메모리에만 보관한다.

### 5.2 Agent Isolation — 에이전트 실행 baseline (규범적)

Core의 단일 함수 `build_agent_launch(adapter, scope)`가 모든 에이전트 실행 인자·환경·작업 디렉터리를 생성한다. 다른 경로로 에이전트를 띄우는 것은 금지된다(코드 검색 계약 테스트 C-09).

baseline 요건:

1. **도구**: 모델에 노출되는 도구는 **MCP 도구만**. 내장 셸·파일 읽기/쓰기/편집 도구는 비활성.
   - Claude Code: `--tools ""`, `--strict-mcp-config --mcp-config <생성한 파일>`, `-p --output-format stream-json`, `--permission-mode dontAsk`(또는 동등한 비대화형 모드), 필요 시 `--disallowedTools`로 이중 차단.
   - Codex CLI: 공식 문서 재조사 전(미확정) → **Codex 어댑터는 이 baseline을 만족함이 검증되기 전까지 활성화하지 않는다.** 대체로 경로 B(사용자 조작 내장 터미널, 라이브러리 접근 없음) 또는 C(API 키).
2. **작업 디렉터리**: 에이전트 프로세스 cwd는 비어 있는 임시 디렉터리(세션 종료 시 삭제). 앱 데이터 디렉터리·사용자 홈의 민감 경로를 포함하지 않는다.
3. **환경 스크러빙**: allowlist 기반 환경(PATH, HOME 류 최소값 + 해당 CLI의 로그인 상태에 필요한 변수)만 상속. `shell_secret`, UI 토큰, Canvas 토큰, 앱의 모든 환경변수 제거.
4. **로그인 상태는 CLI의 것**: 구독 로그인은 사용자가 CLI에서 직접 수행하며 앱은 CLI의 자격증명 파일·키체인 항목을 열거나 복사하지 않는다(약관, `docs/plans/desktop-app-v2.md` §3).
5. **OS 격리의 한계**: 네이티브 Windows의 Claude Code는 샌드박스 없이 실행된다(공식 문서). 따라서 Windows의 격리는 1~3(도구 비활성·경로 분리·환경 스크러빙)에만 의존하며, 이는 요건이지 선택이 아니다.
6. **비-baseline 모드(‘unrestricted agent’)**: 사용자가 설정에서 명시적으로 에이전트에 셸/파일 도구를 허용할 수 있다. 이 모드에서는 (i) 세션 동안 §5.1(c)에 의해 **모든 human-only 쓰기가 거부**되고 (ii) UI에 상시 경고가 표시되며 (iii) 라이브러리를 에이전트에 노출하지 않는다(MCP 토큰 비발급). 기본값은 꺼짐.

### 5.3 MCP 스코프 전달 (에이전트용 capability)

CLI가 MCP 서버를 자식 프로세스로 띄우므로, Core가 만드는 `--mcp-config`의 stdio 항목에 **Scope Grant**를 `env`로 전달한다.

- Scope Grant: `{grant_id, allowed_course_ids[], max_ttl, issued_at, hmac}` — Core가 서명. MCP 자식은 이를 검증하고 허용 범위 밖 조회를 거부한다.
- MCP 자식은 **독립 프로세스**이므로 `MemoryEphemeralStore`(구현 명세서 §9)를 자기 프로세스 안에서 쓴다. 이는 §9.6 "한 서버 프로세스 내 단일 인스턴스"를 만족한다. 대화 세션이 끝나면 MCP가 종료되고 모든 `resolution_id`/`context_id`가 무효화된다(§9.5와 동일 의미).
- Scope Grant에는 쓰기 권한 개념이 없다. DB는 `mode=ro`로 연다.
- Grant 서명 키는 에이전트 환경에 존재하지 않는다. MCP 자식은 **검증 키(공개)** 만 받는다(비대칭 서명 또는 동등 구성은 구현 명세서에서 확정).

### 5.4 에이전트가 쓸 수 있는 HTTP 표면

에이전트는 HTTP 서버에 접근할 이유가 없다. 이를 구조로 보장한다.

- UI HTTP 서버는 Core의 `127.0.0.1` 바인딩이며 UI 세션 토큰이 필요하다. 토큰은 Shell→웹뷰 경로로만 전달되고 에이전트 환경에 없다(I9).
- human-only 엔드포인트는 §5.1(b)(c)가 없으면 항상 거부된다. 따라서 에이전트가 어떤 방법으로 로컬 HTTP를 호출해도 human-only 쓰기는 성립하지 않는다(심층 방어). 비-human-only 쓰기도 UI 세션 토큰이 없으면 거부된다.

### 5.5 human-only 쓰기 목록과 감사 로그

| 행위 | 비고 |
|---|---|
| `Material Usage.Verified=true` | v1.2 §33 |
| `Exam.Scope Confirmed=true` | v1.2 §33 |
| 과제 제출 상태 변경 | v2 신규 |
| AI 산출물 → USER 승격(노트로 확정) | v2 신규 |
| 비-baseline 에이전트 모드 켜기 | 설정 변경도 확인 필요 |

`human_gate_log`: `(id, action, target_id, request_hash, nonce_id, confirmed_at, shell_version)`. 로그에 비밀·본문을 남기지 않는다.

### 5.6 위협 모델과 잔여 위험 (정직한 한계)

방어 대상: **프롬프트 인젝션된 에이전트**, 에이전트의 도구 오남용, 에이전트가 파일/HTTP로 승인 위조 시도, 오류로 인한 AI 산출물 승격.

방어 대상 아님(명시): 동일 OS 사용자 권한으로 실행되는 **악성 프로세스**. 그런 프로세스는 DB를 직접 수정하거나 Shell 메모리를 읽을 수 있다. 이 수준의 방어는 OS의 몫이며 v2는 약속하지 않는다.

잔여 위험: 사용자가 비-baseline 모드를 켜고 에이전트가 일반 파일 도구로 DB를 직접 쓰는 경우 — §5.2-6에 의해 human-only 쓰기가 거부되지만 비-human 데이터 훼손은 가능하다(경고로 고지).

---

## 6. AI 산출물과 신선도

- 요약·문제·해설·분류 제안 등 모든 **AI 산출물**은 `ai_artifacts`에 저장하며 `owner=AI`, `based_on{source_version, source_hash}`, `processor_version`, `agent_adapter`, `prompt_template_version`, `evidence_locators[]`을 **필수**로 가진다. 필수 필드가 없으면 저장을 거부한다(C-06).
- 저장 주체는 항상 **Core**다. Core가 비대화형으로 에이전트를 호출해 텍스트 결과를 받고, 근거 locator가 capability allowlist 안에 있는지 검증한 뒤 저장한다. 에이전트가 직접 저장하지 않는다.
- 검색 엔진은 반환 전에 `is_stale`을 판정한다. stale 산출물은 라우팅/색인에는 쓸 수 있으나 **현재 사실 근거로는 제외**한다(v1.2 §16 유지).
- 근거 인용이 없는 문제·요약은 "근거 없음"으로 표시하고 시험 대비 기능에서 사실 근거로 쓰지 않는다.
- 사용자가 AI 산출물을 노트로 채택하는 것은 §5.5의 human-only 행위이며, 채택 후에도 원본 `AI` 레코드는 변경되지 않고 `USER` 레코드가 새로 만들어진다.

### 6.1 EphemeralStore 위치

대화형 검색의 `ResolutionHandle`/`ContextCapability`는 **MCP 자식 프로세스 내부**의 `MemoryEphemeralStore`에 둔다(§5.3). Core가 비대화형 호출을 할 때도 해당 호출용 MCP 자식이 별도로 뜬다.

---

## 7. 검색 계약 변경

### 7.1 도구

도구 의미(`resolve_entity`, `select_resolution`, `get_material_context`, `get_session_context`, `search_concept`, `get_exam_context`, `get_activity_context`, `get_user_context`, `verify_claim`, `get_source_chunk`)는 유지한다. 접두사는 R1b에서 `syllva.*`로 바꾸고 **Behavior Contract를 v4로 올린다**(해시 재발급, `clients/` 프로젝션 재생성). 외부 클라이언트용 원격 MCP(OIDC/OAuth 전송)는 R6 모바일 전까지 비활성.

### 7.2 자연어 해석 (v1.2 §9·구현 명세서 §10 개정, 필수 요건)

실사용(handoff, 2026-10-14)에서 ID 없이 자료를 못 찾는 것이 최대 결함이었다. v2 `resolve_entity`는 다음을 **반드시** 해석한다: 과목 이름/코드/별칭, 주차("4주차"), 날짜, 자료 제목의 부분 일치, 한·영 혼용 별칭. 모호하면 기존 `select_resolution` 프로토콜로 후보를 제시한다(조용히 하나를 고르지 않는다).
완료 기준: 질의 목록(`tests/fixtures/resolve_queries.yaml`, 예: "TCP", "Chapter 04", "4주차", "09.17", 과목 이름)에 대해 기대 개체를 반환하거나 모호성 후보를 반환.

---

## 8. 자격증명

| 비밀 | 보관 | 에이전트 접근 |
|---|---|---|
| Canvas 토큰 | OS 키체인(`keyring`) | 없음 |
| UI 세션 토큰 | Core 메모리 + Shell | 없음 |
| `shell_secret` | Shell 메모리 → Core 메모리(stdin) | 없음 |
| Scope Grant 서명 키 | Core 메모리 | 없음(MCP는 검증 키만) |
| CLI 구독 로그인 | **각 CLI 소유** (앱은 열거·복사·저장하지 않음) | CLI 프로세스 자체만 |

v1.2의 "워커 vs MCP 자격증명 분리"(§23)는 Drive/Notion이 사라지므로 위 표의 **도메인 분리**로 대체된다. 비밀은 커밋·로그·프롬프트·번들에 포함하지 않는다(I9).

---

## 9. 오류·실패 원칙 (v1.2 유지)

fail-closed. 증명 불가는 거부. `Partial`은 `Ready`가 아니다. 부분 실패한 수집은 사용자에게 보인다.

---

## 10. 플랫폼·접속

- **Windows 동등 지원**: 경로(긴 경로·대소문자·NFC), 파일 잠금, 프로세스 종료, 키체인(Credential Manager)을 CI 매트릭스에 포함.
- **모바일(R6)**: 데스크탑 Core에 접속하는 클라이언트. v1.2 §29.1 가용성 계약(데스크탑이 켜져 있어야 함)을 그대로 적용. 서버층은 루프백 전용 전제(`LOOPBACK_PEERS`)를 깨므로 **페어링·TLS·장치별 토큰을 별도 개정**으로 설계한다. human-only 확정은 모바일에서 불가(데스크탑 Shell 확인 필수) — 개정 전까지 모바일은 읽기+비-human 쓰기만.
- anyone-with-link 금지(I7).

---

## 11. 계약 테스트 (구현 전 작성 — 릴리스 블로커)

| ID | 검증 |
|---|---|
| C-01 | MCP 프로세스가 등록한 도구 중 쓰기 도구가 0개 |
| C-02 | MCP 프로세스의 DB 연결이 `mode=ro`이며 쓰기 시 오류 |
| C-03 | `(owner, actor)` 위반 쓰기 거부: AI→SOURCE, 에이전트→USER, 자동→USER 덮어쓰기 |
| C-04 | human-only 엔드포인트: UI 토큰만으로 거부, nonce만으로 거부, 유효 HMAC 없이 거부 |
| C-05 | nonce 재사용·만료·request_hash 불일치 거부; 비-baseline 에이전트 세션 중 거부 |
| C-06 | AI 산출물 저장 시 지문·근거 필드 누락 거부; stale 산출물이 factual evidence에서 제외 |
| C-07 | `build_agent_launch()`가 만든 환경에 앱 비밀(`shell_secret`, UI/Canvas 토큰, 서명 키)이 없음 |
| C-08 | 에이전트 cwd가 앱 데이터 디렉터리와 겹치지 않고, DB 경로가 cwd 하위가 아님 |
| C-09 | 코드베이스에서 `build_agent_launch` 외에 에이전트 CLI를 spawn하는 호출이 없음 |
| C-10 | `get_source_chunk`: allowlist 밖 locator, 만료 capability, 이전 `source_version` locator 거부 |
| C-11 | Scope Grant: 위조·만료·허용 과목 밖 조회 거부; MCP 재시작 후 이전 context_id 무효 |
| C-12 | 정규화 Partial이 Ready로 전이되지 않음; 요약이 정규화 산출물로 저장되지 않음 |
| C-13 | 공개 공유/원격 노출 경로가 기본 설정에서 존재하지 않음 |
| C-14 | resolve 질의 목록(§7.2) 통과 |
| C-15 | Behavior Contract 해시·프로젝션 드리프트 린트 통과(v4) |
| C-16 | Windows/macOS 양쪽에서 C-01~C-15 중 OS 비의존 항목 통과 |

---

## 12. 열린 항목 (구현 명세서 작성 전)

1. Scope Grant 서명 방식(비대칭 vs Core 중개 검증) 확정.
2. Codex CLI: 공식 문서(도메인 허용 후) 재조사로 baseline §5.2 만족 여부와 약관 확인. 그 전까지 Codex 어댑터 비활성.
3. Claude Code를 앱이 구동하는 형태의 Commercial Terms 적용 여부(법무 확인).
4. Tauri 원격 URL(`http://127.0.0.1`)에서 네이티브 확인 호출 경로(IPC capability 구성)와 `shell_secret` 전달의 실제 구현 가능성 — R2 초기 스파이크로 검증. 불가하면 §5.1(b)를 대체할 동등한 증명을 이 문서에서 개정한다.
5. 데이터 모델 상세 DDL, 분류 파이프라인(Notion 의존 제거)의 로컬 큐/영수증 모델.
6. GitHub 선택 어댑터의 활성화 UX.
7. 이 문서 승인 전 **독립 읽기 전용 검토 1회**(보안 경계 변경: §5, §3).
