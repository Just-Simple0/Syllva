# CLAUDE.md

이 파일은 이 저장소에서 작업하는 Claude Code(및 협업 에이전트)를 위한 가이드입니다.

## 프로젝트 개요

**Syllva v2** — 로컬 우선 학업 지식·검색 데스크탑 앱. (v1.2 "University Learning System, MCP 중심" 계보의 후속. 내부 패키지는 `syllva`, v1.2 시절 이름 `uls`는 CLI 별칭으로만 남음.)

- **아키텍처:** Local-primary · Tauri 셸 + Python Core(sidecar) · HTML UI · Windows/macOS 동시(모바일은 후속) · Single-active-worker
- **저장소:** 로컬 라이브러리(콘텐츠 해시 blob) + SQLite. Notion·Drive는 제거됨. GitHub 소스 어댑터는 선택 기능.
- **AI:** 공식 CLI 에이전트(Claude Code, Codex CLI)를 앱이 서브프로세스로 구동하되 **도구는 0개**. Core가 자료를 골라 프롬프트에 넣고, 에이전트는 텍스트만 반환한다. MCP는 필수가 아니며 선택 어댑터(기본 비활성).
- **핵심 언어:** Python 3 (Core), Rust/Tauri (Shell), HTML/JS (UI)
- **핵심 원칙:** Syllva Core가 어떤 컨텍스트가 허용/관련되는지 결정해 프롬프트로 직접 제공하고, 에이전트는 그 컨텍스트 위에서만 추론한다. 사람이 해야 하는 판단은 에이전트가 도달할 수 없는 경로로만 기록된다.

## 권위 문서

우선순위(위가 우선):

1. `syllva-v2-design-frozen.md` — v2 설계서 (FROZEN, 2026-11-02 승인). 위반하는 변경은 **개정(revision)** 을 요구한다.
2. `syllva-v2-implementation-spec-frozen.md` — v2 구현 명세서 (FROZEN, 2026-11-02). 계약 테스트 C-01~C-26의 파일·단언(§14)과 단계별 PR 분해(§15)가 여기 있다.
3. `university-learning-system-v1.2-design-frozen.md`, `university-learning-system-v1.2-implementation-spec-frozen.md` — v1.2 동결 문서. v2 설계서 §1 처분표에서 **유지**로 표시된 부분만 유효하고, 대체·삭제된 부분(Notion/Drive, MCP 경계, Behavior Contract v3 등)은 역사 기록이다.
4. `docs/plans/desktop-app-v2.md` — 전환 계획(로드맵 R0~R6). 설계서와 충돌하면 설계서가 우선.

## 작업 방식

과거의 다중 에이전트 역할 분담과 이중 외부 리뷰 파이프라인(Codex 구현 + insane-review + AGY Gemini)은 폐지되었다(2026-11-02 사용자 결정). 이전 문안은 git 이력(`ff5f7ea` 이전)에 있다.

각 작업 단위(Phase/슬라이스)는 다음 순서로 진행한다.

```text
1. 계획      — 설계서 근거로 범위와 영향받는 불변식·계약 테스트를 명시
2. 계약 테스트 — 새 불변식/동작의 테스트를 구현보다 먼저 작성 (릴리스 블로커는 pytest -m contract)
3. 구현
4. 게이트    — pytest(전체), ruff, mypy, 프로젝션/계약 린트, 앱 스모크를 직접 실행해 결과 확인
5. 자체 리뷰  — diff를 설계서 불변식 표에 대조. 별도 컨텍스트(서브에이전트 또는 code-review 스킬)로 한 번 더 읽기 전용 검토
6. 커밋/푸시  — 게이트 통과와 자체 리뷰 반영 후에만
```

- **자기 편향 보완:** 자체 리뷰의 한계는 계약 테스트를 먼저 쓰는 것으로 보완한다. human-only 승격 불가, 에이전트 도구 0개, 소유 구분, 인용 정합성 같은 불변식은 반드시 테스트로 고정한다.
- **보안 경계 변경:** Human-Proof, Agent Isolation, 프롬프트 조립, 자격증명 경로를 바꾸는 변경은 구현 전 계획 단계에서 독립 읽기 전용 검토를 한 번 받는다.
- **반복 지적 규율:** 같은 컴포넌트에서 지적이 3건 이상 반복되면 모양별 땜질을 멈추고 구조를 바꾼다.
- **외부 독립 리뷰:** 릴리스 후보 단계(R5 이후)에서 필요 여부를 사용자에게 다시 묻는다.
- **결과 보고:** 테스트·검증은 실제 실행 결과로만 보고한다. 실패는 숨기지 않고, 기준선 실패와 새 실패를 구분한다.
- 의미 있는 변경은 지정된 브랜치에 커밋·푸시한다. PR은 사용자가 요청할 때만 만든다.

## 핵심 불변식 (Release-blocking)

설계서 §2의 I1~I9. 요지:

- **I1** 검색 표면은 read-only. Retrieval Service는 읽기 전용 연산만 가지며 선택 MCP 어댑터가 켜져도 같은 서비스만 노출한다.
- **I2** `SOURCE / AI / USER` 소유 구분 유지. AI 출력은 SOURCE로 재라벨 금지, USER 콘텐츠는 자동으로 덮어쓰지 않음.
- **I3** 정규화(normalization)는 요약이 아니다. `Partial`은 절대 조용히 `Ready`가 되지 않는다.
- **I4** `Material Usage.Verified = true` / `Exam.Scope Confirmed = true` 등 human-only 행위는 **Human-Proof**(UI 세션 + Shell 네이티브 확인 + `shell_secret` HMAC + 대상 재검증) 없이는 AI/자동화가 승격할 수 없다.
- **I5** 신선도(freshness) 검증은 엔진이 수행하며, stale enrichment는 factual evidence에서 제외한다. AI 산출물은 `source_version / source_hash / processor_version` 지문을 필수로 가진다.
- **I6** 인용은 **이번 턴에 Core가 프롬프트에 넣은 evidence**에 한한다(출처 정합성). 이는 접근 통제가 아니며 내용이 근거에 뒷받침된다는 보증도 아니다. UI는 "검증됨" 라벨을 쓰지 않고 원문 청크를 바로 보여 준다.
- **I7** 공개(anyone-with-link) 공유를 검색 편의를 위해 사용하지 않는다.
- **I8** 에이전트는 **도구를 갖지 않는다.** 입력은 Core가 조립한 프롬프트, 출력은 텍스트뿐이다. CLI 자동 로드(훅·플러그인·메모리)·슬래시/`@파일` 확장·세션 영속은 요건과 매 실행 단언으로 차단한다.
- **I9** 비밀정보(Canvas 토큰, UI 세션 토큰, `shell_secret`)는 에이전트 환경·로그·프롬프트·번들에 존재하지 않는다. 프롬프트·응답 본문은 로그에 남기지 않는다.

위협 모델의 한계: 같은 OS 사용자 권한의 악성 프로세스는 방어 대상이 아니다. 구독 제공자로 전송되는 데이터는 사용자가 확인한 컨텍스트 칩 목록으로 한정된다.

## 저장소 레이아웃

```text
src/syllva/       Python 코어 (domain, retrieval, state, ingestion, normalization, enrichment, intake, adapters, mcp[선택], settings[UI 출발점], ...)
contracts/        v1.2 Behavior Contract (v4에서 프롬프트 템플릿으로 전면 재작성 예정)
clients/          v1.2 외부 클라이언트 프로젝션 (v4에서 폐기 예정)
deployment/       v1.2 launchd / Task Scheduler / remote-mcp (v2에서 재정의 예정)
scripts/          Behavior Contract 해시/드리프트 린트 (v4 전환 시 대체)
tests/            unit / contract / integration / e2e / fixtures
docs/plans/       전환 계획과 과거 설계 기록 (desktop-app-v2.md가 현행)
```

Tauri 셸 디렉터리(`shell/`)는 R2에서 추가된다.

## 개발 워크플로

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,mcp,web,pdf,drive,notion,github,keyring]"

pytest -m contract        # 모델 독립 계약 테스트 (릴리스 블로커)
pytest -m unit
pytest                    # 전체
ruff check src tests
mypy

syllva --help                                          # CLI (uls는 별칭)
python scripts/project_behavior_contract.py --print   # v1.2 Behavior Contract 버전/해시 (v4 전환 전까지)
python scripts/lint_behavior_projection.py            # 프로젝션 드리프트 검사
```

기준선(2026-11-02, R1a 직후, Linux): 전체 3533 passed / 10 failed(`tests/unit/test_knu_lms_sync.py`의 키체인 의존 테스트) / 5 skipped. ruff 지적 다수, mypy 134 errors. 이 기준선과 비교해 **새로 생긴 실패**를 판단한다.

## 구현 순서 (로드맵)

`R0 설계 개정` → `R1a 이름 변경(완료)` → `R1b 로컬 저장소·Retrieval Service 재구성` → `R1c 로컬 intake 워커` → `R2 UI·Tauri 셸·Human-Proof 스파이크` → `R3 Canvas·과제` → `R4 AI 패널(도구 없는 에이전트·적합성 시험)` → `R5 요약·시험 대비` → `R6 설치본·모바일 접속`

R0 완료: 설계서·구현 명세서 동결, 이 파일 갱신. 상세는 `docs/plans/desktop-app-v2.md` §8.

## 규칙

- 코어 모듈(`domain/ ingestion/ normalization/ enrichment/ retrieval/ state/ ephemeral/`)은 클라이언트 SDK, UI, 셸, OS 스케줄러에 의존하지 않는다.
- Retrieval Engine은 MCP 서버나 에이전트 없이도 테스트에서 직접 호출 가능해야 한다.
- 에이전트 CLI는 `build_agent_launch()` 외의 경로로 실행하지 않는다(C-09).
- 비밀정보(credential/token)는 커밋, 프롬프트, 로그, 번들에 절대 포함하지 않는다.
- 사용자 UI에 표시하는 AI/SOURCE/USER 텍스트는 text로만 렌더링하고, 원시 HTML을 허용하지 않는다(C-18).
- 프로즌 계약을 위반하는 변경은 설계서 개정(revision)을 요구한다. 임의로 설계서를 수정하지 않는다.
- `RESEARCH/` 접근·추가, 실제 provider 호출을 하는 테스트, 요청 없는 전역 설정 변경은 금지.
