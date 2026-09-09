# CLAUDE.md

이 파일은 이 저장소에서 작업하는 Claude Code(및 협업 에이전트)를 위한 가이드입니다.

## 프로젝트 개요

**University Learning System (ULS) v1.2** — 개인 학업 지식·검색 시스템.

- **아키텍처:** Model-agnostic · MCP-centered · Local-primary · Single-active-worker · Cross-platform
- **핵심 언어:** Python 3
- **핵심 원칙:** ULS가 어떤 컨텍스트가 허용/관련되는지 결정하고, AI 클라이언트는 ULS가 제공한 컨텍스트 위에서 추론한다.

권위 있는 문서(코드가 충돌하면 아래 설계서가 우선한다):

- `university-learning-system-v1.2-design-frozen.md` (설계서, frozen)
- `university-learning-system-v1.2-implementation-spec-frozen.md` (구현 명세서, frozen)

## 역할 분담 (Agent Roles)

| 단계 | 담당 | 모델 / 도구 |
|---|---|---|
| **총괄 / 관리 (Oversight & Management)** | 현재 Codex 총괄 | **현재 주 에이전트** |
| **계획 초안** | Sonnet | **Claude Sonnet 5 (high)** |
| **개발 / 구현 (Development & Implementation)** | Codex | **Codex Luna (max)** |
| **리뷰 (Review)** | 이중 리뷰 | **insane-review (Pro, 불가 시 승인된 최신/매우 높음)** + **AGY Gemini 3.8 Flash (high)** |

운영 원칙:

- **총괄/관리(현재 주 에이전트):** 요구사항 해석, 작업 분해, 계약(설계/명세) 준수 판단, 최종 통합·머지 결정. Phase 4~8은 2026-09-08 사용자 승인에 따라 이 역할로 진행하며, 아래 기존 파이프라인의 Opus 표기는 이 총괄 역할로 해석한다. Sonnet 5가 계획 초안을 작성한다.
- **개발/구현(Codex Luna max):** 실제 코드 작성과 구현 작업 수행.
- **리뷰(insane-review + AGY Gemini 3.8 Flash high):** 결과를 독립적으로 검토. 두 리뷰어는 서로의 결과를 참조하지 않고 각각 독립 리뷰한다.
- **Sonnet 리뷰 제한(2026-09-09 사용자 정정):** Sonnet 리뷰 요청은 일회성이었다. 앞으로 리뷰용 Sonnet 서브에이전트는 호출하지 않는다. 리뷰는 Codex native insane-review와 Gemini3.8Flashhigh만 사용한다. Sonnet의 기존 계획 역할과는 구분한다.
- **리뷰 설정(2026-09-08 사용자 지정):** Phase 4부터 insane-review는 `--model pro --require-model "GPT-6"`로 실행한다. 실제 모델명과 Pro 추론 단계 검증 실패 시 중단하며 다른 모델로 자동 대체하지 않는다. 정밀 리뷰에는 `--force-answer-after`를 사용하지 않는다. 브라우저 로그인·GPT-6 Pro 접근 가능 여부는 실제 실행 전에 확인한다.
- **현재 리뷰 실행(2026-09-09 사용자 지정):** Codex에 설치된 `insane-review-codex` 플러그인을 우선 사용한다. 확인된 설치본은 `/Users/admin/.codex/plugins/cache/gptaku-codex/insane-review-codex/0.6.8`; 먼저 `skills/insane-review/SKILL.md`를 읽고 `bin/pack_and_ask.py --ensure-env`로 확인한다. 패킹·첨부·회수는 해당 native engine을 사용한다. 현 UI 호환 보조 코드는 표시 중인 메뉴의 실제 선택 상태를 검증하는 범위로 한정하고, 설치 파일을 임의 변경하지 않는다. 전송 후 timeout은 동일 대화의 `--harvest`로 회수하며 중복 전송하지 않는다. 정밀 리뷰의 타임아웃에서도 조기 답변을 강제하지 않는다. 이전 Claude plugin0.6.2 wrapper는 현재 기본 경로가 아니다.
- **사용자 승인 fallback(2026-09-08):** Pro 사용 불가 시 insane-review의 **매우 높음**으로 계속한다. UI의 선택된 `최신` 모델과 `매우 높음` 추론을 모두 확인하고, UI가 숫자 모델 버전을 노출하지 않으면 임의로 GPT-6라고 기록하지 않는다. Sol 서브에이전트 리뷰로 바꾸는 것이 아니며 Gemini 독립 리뷰는 유지한다.

- **프로젝트 기본 동작:** 각 리뷰는 저장소 ChatGPT 프로젝트 안의 새 채팅에서 진행한다. **2026-09-09 사용자 명시 예외:** 프로젝트가 열리지 않으면 새 프로젝트 또는 일반 새 채팅(`--no-project`)으로 진행할 수 있다. 실패한 이전 실행의 전송 여부를 확인해 중복 리뷰를 방지하고, 실제 모델·추론단계·첨부 검증은 그대로 유지한다.
- **리뷰 범위/빈도(2026-09-09 사용자 지시):** 너무 좁은 범위로 자주 리뷰하지 않는다. 회수된 지적들을 먼저 통합 수정하고 전체 테스트를 통과한 뒤 Phase 전체 흐름과 경계를 묶어 리뷰한다. 대용량 첨부의 전체 검토가 불가능하면 불완전한 검토를 GO로 기록하지 않는다.

## 작업 파이프라인 (각 Phase/작업 단위마다)

```text
1. 계획 수립   (Opus 4.8) — 명세 근거로 구현 계획서 작성
2. 계획 리뷰   (insane-review + AGY, 2중 독립) — 계획의 계약 준수/누락/리스크 검토 → Opus가 반영
3. 구현        (Codex Luna max) — 확정된 계획대로 구현
4. 검증        (Opus 4.8) — 게이트(py_compile/pytest/독립 스모크) 직접 실행·재현
5. 2중 리뷰    (insane-review + AGY, 2중 독립) — 구현 결과 리뷰 → Opus가 판정(불일치 시 타이브레이크)
6. 커밋/푸시   (Opus 4.8) — 두 리뷰어 GO + 검증 일치 시에만 커밋/푸시
```

리뷰어가 엇갈리면 Opus가 직접 코드로 재현해 판정한다. 리뷰 지적으로 재수정이 필요하면 3~5를 반복한다.

## 핵심 불변식 (Release-blocking)

- MCP 검색 표면은 **read-only** (v1.2).
- `SOURCE / AI / USER` 소유 구분 유지. AI 출력은 SOURCE로 재라벨 금지, USER 콘텐츠는 자동으로 덮어쓰지 않음.
- 정규화(normalization)는 요약이 아니다. `Partial`은 절대 조용히 `Ready`가 되지 않는다.
- `Material Usage.Verified = true` / `Exam.Scope Confirmed = true` 는 **human-only** — AI/자동화가 승격 불가.
- 신선도(freshness) 검증은 엔진이 수행하며, stale enrichment는 factual evidence에서 제외한다.
- `get_source_chunk` 은 사전 발급된 context capability의 allowlist 안 locator만 허용.
- 공개(anyone-with-link) 공유를 검색 편의를 위해 사용하지 않는다.

## 저장소 레이아웃

```text
contracts/        model-neutral Behavior Contract (study-behavior.md)
clients/          Claude Skills / ChatGPT Instructions 프로젝션
src/uls/          Python 코어 (domain, retrieval, mcp, adapters, state, ephemeral, ...)
deployment/       macOS launchd / Windows Task Scheduler / remote-mcp
scripts/          Behavior Contract 해시/드리프트 린트
tests/            unit / contract / integration / e2e / fixtures
```

## 개발 워크플로

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest -m contract        # 모델 독립 계약 테스트 (릴리스 블로커)
pytest -m unit
ruff check src tests
mypy

python scripts/project_behavior_contract.py --print   # 정규 Behavior Contract 버전/해시
python scripts/lint_behavior_projection.py            # 프로젝션 드리프트 검사 (CI)
```

## 구현 순서 (frozen)

`Spike C0 → Spike M0 → VS0 → VS0-B (cross-client) → Spike G (Goodnotes)`

이후 Phase 1(Core Hardening) → Phase 8(Desktop Automation & Remote MCP)까지 명세서 §42–§49 순서.

## 규칙

- 코어 모듈(`domain/ ingestion/ normalization/ enrichment/ retrieval/ state/ ephemeral/`)은 클라이언트 SDK나 OS 스케줄러에 의존하지 않는다.
- Retrieval Engine은 MCP 서버 없이도 테스트에서 직접 호출 가능해야 한다.
- 비밀정보(credential/token)는 커밋, Skills/Instructions, 로그에 절대 포함하지 않는다.
- 프로즌 계약을 위반하는 변경은 명세 개정(revision)을 요구한다.
