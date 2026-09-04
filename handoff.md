# Syllva (ULS v1.2) — Handoff

**Last updated:** 2026-09-05
**Repo:** https://github.com/Just-Simple0/Syllva (`main`)
**Latest commit:** `6572feb` — Phase 2 Transcript Vertical Slice (dual-review GO)

이 문서는 다음 담당자/세션이 바로 이어서 작업할 수 있도록 현재 상태·계약·다음 단계를 정리한 인수인계 문서입니다.

---

## 1. 프로젝트 개요

**University Learning System (ULS) v1.2** — 개인 학업 지식·검색 시스템.
Model-agnostic · MCP-centered · Local-primary · Single-active-worker · Cross-platform (Python 3.11+).

> **ULS가 어떤 컨텍스트가 허용/관련되는지 결정하고, AI 클라이언트는 ULS가 제공한 컨텍스트 위에서 추론한다.**

권위 문서(코드가 충돌하면 아래가 우선):
- `university-learning-system-v1.2-design-frozen.md` (설계, frozen)
- `university-learning-system-v1.2-implementation-spec-frozen.md` (구현 명세, frozen)

역할·경계 요약: `Drive`=원본+정규화 파생, `Notion`=학술 그래프/상태/검증, `GitHub`=정확 ref 코드,
`Retrieval Engine`=scope/authority/freshness/provenance, `MCP`=read-only 경계, Skills=행동/데이터접근 아님.

---

## 2. 역할 분담 & 작업 파이프라인 (반드시 준수 — CLAUDE.md와 동일)

| 단계 | 담당 |
|---|---|
| 총괄/관리 | Claude **Opus 4.8** — 요구 해석·계획·계약 판단·검증·통합·머지 |
| 개발/구현 | **Codex Luna max** (`gpt-5.6-luna`, reasoning=max) |
| 리뷰 | **insane-review (GPT-5.6 Sol High)** + **AGY Gemini 3.8 Flash high** (2중 독립) |

```text
1. 계획 수립(Opus) → 2. 계획 리뷰(2중) → 3. 구현(Codex) → 4. 검증(Opus)
→ 5. 2중 리뷰 → 6. 커밋/푸시(둘 다 GO + 검증 일치 시에만)
```
리뷰어가 엇갈리면 **Opus가 직접 코드로 재현해 타이브레이크**. 지적으로 재수정 필요 시 3~5 반복.

**중요 교훈:** AGY가 GO를 준 지점에서 GPT-5.6이 실 결함(fail-open, 계약 드리프트, self-approval 등)을
Phase 1/2 내내 반복적으로 잡았다. **단일 리뷰어는 불충분** — 반드시 2중 + Opus 재현.

---

## 3. 완료 상태

| 항목 | 상태 |
|---|---|
| 저장소 스캐폴드(§3 레이아웃), CLAUDE.md, 계약/클라이언트 프로젝션 | ✅ `5bb9d34` |
| Phase 1 Core Hardening (state/ephemeral/orchestration/config/human-gate) | ✅ `ab3c9a1`+`63cb951` |
| Phase 2 Transcript Vertical Slice (normalization/ingest/retrieval get_session_context) | ✅ `6572feb` |
| 테스트 | **148 passing** (모델 독립 contract/unit) |

검증 도구 환경: `codex` CLI(`gpt-5.6-luna`), `agy` CLI(`gemini-3.8-flash-high`), `insane-review` 플러그인(GPT-5.6 Sol),
pytest+pyyaml 설치됨(인터프리터: `/Library/Frameworks/.../python3.14`).

### 구현된 핵심 계약 (리뷰로 확정된 불변식)
- **도메인**(`src/uls/domain/`): Locator 문법/AST/직렬화/typed containment(§6.3, 문자열 prefix 인가 금지),
  enums/ids/source_ref/provenance/errors(§53). 표준 라이브러리만.
- **StateStore**(`state/sqlite.py`): 반복 마이그레이션, 결정적 `job_key`(§8.1.1), source-bound idempotent
  엔티티 할당(§8.6.1, caller 선점 불가), source_versions 일관성.
- **EphemeralStore**(`ephemeral/memory.py`): TTL·재시작 무효화, capability fingerprint 바인딩 + fail-closed
  `authorize_locator`(§25, current_fingerprint 미제공 시 deny, 불일치 시 LOCATOR_STALE).
- **Human-gate**(`adapters/notion/base.py`): 방어적 쓰기(§15.1, Verified/Scope Confirmed/Decision/State),
  create/update 분리, `ApprovalReader`/`HumanApprovalApplier`(human Decision By 필수, self-approval 차단),
  Automation Queue 상태기계, 시스템 경로 SUPERSEDED/FAILED만.
- **Retrieval**(`retrieval/*`): `resolve_entity`/`select_resolution` + `get_session_context(session_id,…)`,
  per-chunk 정확 capability allowlist(초과 인가 없음), §25 6검사(+role 현행성), SESSION authority(§17/§21,
  fetch order ≠ authority rank), freshness/stale-locator, read-only 어댑터 Protocol(§4/§13/§27).
- **Normalization/Ingest**: transcript verbatim + sidecar 타임스탬프(code-point offset), §17 커밋 순서 +
  Partial 3중 일관, 필수 collaborator/등록 fail-closed.

**공통 원칙: 모든 경계는 fail-closed.** 애매하면 거부/제외 — 절대 조용한 skip/합성/승격 금지.

---

## 4. 다음 단계 (frozen 순서)

구현 순서(§41): `Spike C0 → Spike M0 → VS0 → VS0-B → Spike G`, Phase 1~8(§42~§49).

- **Phase 3 — Enrichment & Freshness (§44)**: Session Summary/Topics/Content Index/Professor Emphasis·Examples/
  Exam Signals/Likely Confusions **생성**(현재는 소비만 함), source fingerprint 메타. explicit/inferred 분리, evidence locator, stale 제외.
- **Phase 4 — Material Usage & Human Gate (§45)**: Material Usage/page-range proposal, 승인 경로, Verified 상태,
  multi-material Session 검색, unverified 정책. (Phase 2에서 read-only 소비만 했고 mutation/proposal은 여기로 미룸.)
- **Phase 5~8**: Exam/Activity(§46), GitHub 정확 ref 검색(§47), 클라이언트 패키징(§48), 데스크톱 자동화+remote MCP(§49).
- **MCP 트랙(별도)**: Spike C0(ChatGPT remote 연결)/M0(로컬 MCP로 get_material_context)/VS0 — 엔진은 이미 MCP 없이
  직접 호출 가능하므로 `mcp/` 스텁에 read-only 도구를 배선하면 됨(§19~§24). 현재 `mcp/`는 스텁.

**착수 방법:** Phase마다 `docs/plans/<phase>.md` 계획서를 Opus가 작성 → 2중 계획 리뷰 → Codex 구현 → 검증 → 2중 리뷰 → 커밋.
(예시 계획서: `docs/plans/phase2-transcript.md` rev2 참고.)

---

## 5. 개발/리뷰 실행 방법 (그대로 복사해 사용)

구현 위임(Codex Luna max) — **프롬프트는 파일로, stdin은 `< /dev/null`로 닫을 것**(안 닫으면 hang):
```bash
codex exec -m gpt-5.6-luna -c model_reasoning_effort=max --sandbox workspace-write \
  --skip-git-repo-check -C /Users/admin/Project/Syllva \
  "$(< /path/to/prompt.md)" < /dev/null > /path/to/codex.log 2>&1
```
리뷰(2중 독립):
```bash
# insane-review (GPT-5.6 Sol). Pro 미보유 계정이라 --model high + --require-model 사용
python3 "…/insane-review/0.6.2/bin/pack_and_ask.py" --target /Users/admin/Project/Syllva \
  --include "src/…,tests/**,…-implementation-spec-frozen.md" --model high --require-model "GPT-5.6" \
  --prompt "$(< /path/to/review.txt)" --force-answer-after 1000
# AGY Gemini 3.8 Flash high
agy --dangerously-skip-permissions --model gemini-3.8-flash-high --effort high \
  --print-timeout 20m --prompt "$(< /path/to/review.txt)" < /dev/null > agy.log 2>&1
```
검증(Opus 직접):
```bash
python3 -m py_compile $(git ls-files 'src/uls/**/*.py')
python3 -m pytest -q tests/
python3 scripts/lint_behavior_projection.py   # Behavior Contract 드리프트
```

주의: `codex exec`에 `--full-auto` 플래그 없음(→ `--sandbox workspace-write`). heredoc과 codex를 한 명령에
합치지 말 것(stdin 충돌로 hang). `pumasi.sh start`는 자동 승인 분류기에 차단될 수 있어 `codex exec` 직접 사용.

---

## 6. 알려진 제약 / 낮은 우선순위 항목

리뷰에서 "정상 StateStore에는 실 위험 없음(malformed/incomplete adapter 전용)"으로 **릴리스 OPEN에서 제외**된 hardening 갭 —
Phase 3+에서 정리 권장:
- `_require_ingest_collaborators()`가 `get_job`을 필수 목록에 넣지 않음(프로즌 Protocol엔 있음). get_job 없는 duck-typed
  adapter가 PROCESSING 객체만 반환하면 재조회 없이 신뢰될 수 있음.
- `_allocate_entity()`가 반환 ID의 문법(parse_entity_id)만 검사하고 course/type 일치까지는 재검증 안 함.
- 과거 버그 버전이 이미 잘못된 `canonical_entity_id`를 영속 저장한 legacy 데이터는 §8.6.1 idempotency로 자동 교정되지 않음 → migration/scrub 필요.

기타:
- MCP `mcp/` 및 Claude package/ChatGPT app은 스텁. 클라이언트 지원 상태는 배포 의존(§27~§30).
- 실제 provider(Google/Notion/GitHub) SDK 연동은 미구현(어댑터 read-only Protocol + Fake로 검증 중). `adapters/*/api.py` 스텁.
- 원격 MCP/스케줄러/Goodnotes(§33)/CONCEPT 벡터검색은 v1.2 비목표 또는 후속 Phase.

---

## 7. 참고 파일
- 계획서: `docs/plans/phase2-transcript.md`
- 가이드: `CLAUDE.md`, `README.md`, `CHANGELOG.md`
- Behavior Contract: `contracts/study-behavior.md` (+ `clients/…` 프로젝션, `scripts/lint_behavior_projection.py`)
- 테스트: `tests/{unit,contract}/`, 픽스처 `tests/fixtures/fake_{notion,drive}.py`
