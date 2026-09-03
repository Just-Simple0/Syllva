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
| **총괄 / 관리 (Oversight & Management)** | Claude | **Opus 4.8** |
| **개발 / 구현 (Development & Implementation)** | Codex | **Codex Luna (max)** |
| **리뷰 (Review)** | 이중 리뷰 | **insane-review** + **AGY Gemini 3.8 Flash (high)** |

운영 원칙:

- **총괄/관리(Opus 4.8):** 요구사항 해석, 작업 분해, 계약(설계/명세) 준수 판단, 최종 통합·머지 결정.
- **개발/구현(Codex Luna max):** 실제 코드 작성과 구현 작업 수행.
- **리뷰(insane-review + AGY Gemini 3.8 Flash high):** 구현 결과를 독립적으로 검토. 두 리뷰어는 서로의 결과를 참조하지 않고 각각 독립 리뷰한다.

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
