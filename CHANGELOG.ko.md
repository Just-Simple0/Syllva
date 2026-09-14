# 변경 기록

[English](CHANGELOG.md)

주요 저장소 변경을 기록합니다. 형식은 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)의 취지를 따르지만 Syllva는 아직 안정적인 공개 패키지 릴리스를 배포하지 않았습니다.

## [Unreleased]

현재 공개 릴리스는 없습니다.

## [0.1.3] — 베타 저장소 기준선 (미출시)

### 변경

- 공개 프로젝트 이름을 **Syllva**로 정리하고 `uls`는 CLI, University Learning System은 코어 시스템 명칭으로 유지했습니다.
- package metadata/runtime `__version__`을 **0.1.3**으로 설정하고 beta/unpublished 상태를 명시했습니다.
- `PROTOCOL_VERSION = "1.2"`는 유지했습니다. 패키지 릴리스 버전과 frozen core protocol 버전은 서로 독립적입니다.
- **MIT License**를 채택했습니다.
- 공개 README를 제품 목적, 사용자 흐름, 상태, quick start, architecture, trust boundary 중심으로 재작성했습니다.
- 사용자/운영자/개념/레퍼런스/client/deployment 공개 문서에 영문/한국어 쌍(`*.md` + `*.ko.md`)을 추가했습니다.

### 현재 베타 기능

- source/provenance 처리를 포함하는 model-neutral domain/retrieval core.
- SQLite durable orchestration state와 in-memory bounded capability.
- transcript/PDF normalization 경로와 source freshness/authority 검사.
- material/session/exam/activity retrieval 및 proposal/enrichment infrastructure.
- 지원 client projection이 공유하는 read-only MCP tool surface.
- 명시적 Drive/Notion identity와 사용자 submit/cancel gate를 사용하는 semester intake preview.
- 별도 운영 gate가 있는 선택적 KNU/Canvas LMS sidecar.
- macOS/Windows local scheduling template과 development remote-MCP profile.

### 검증 상태

- unit/contract/integration coverage를 저장소에서 유지합니다.
- CI는 macOS/Windows에서 지원 Python 버전을 검사합니다.
- 실제 provider/client 검증은 환경에 따라 추가로 필요합니다.
- Claude local MCP는 대상 환경 domain E2E 기록 전까지 experimental입니다.
- ChatGPT remote MCP/App은 account/auth/client 요구사항이 검증될 때까지 deployment-deferred입니다.

## 코어 구현 이력

아래 항목은 frozen 1.2 protocol을 기준으로 구축한 구현 경로를 요약합니다. 상세 plan/review evidence는 `docs/plans/`, `docs/ux/`, git history, frozen specification에 보존됩니다.

### Phase 1 — Core hardening

- 반복 가능한 SQLite migration, job lifecycle, source file/version, checkpoint, source-bound idempotent allocation, local worker lock, retry/rate-limit policy, strict config validation, human-gate write guard, model-independent contract test를 구현했습니다.
- 독립 review/fix round를 통해 capability/source-fingerprint binding, job-key enforcement, fail-closed configuration, human approval separation, TOCTOU-safe local lock을 강화했습니다.

### Phase 2 — Transcript vertical slice

- verbatim body와 timestamp sidecar를 사용하는 deterministic transcript normalization.
- Partial/Ready 상태를 정직하게 보존하고 source/version provenance를 기록하는 commit ordering.
- retrieval용 read-only provider interface, entity resolve/select, bounded session context, exact locator authorization, authority policy, freshness, structured error.

### 이후 구현된 slice

- session/material enrichment와 freshness 지원.
- material-usage proposal, guarded approval, recovery, retrieval/capability 작업.
- exam/activity context와 capability lifecycle 작업.
- 관련 plan/evidence에 기록된 phase 6–8 추가 구현 및 로컬 검증.
- v0.1.3 semester-intake preview, dashboard/UX integration 기록, LMS sidecar 작업.

## Protocol 1.2 frozen design 기준선

이 저장소는 과거 frozen **v1.2 design/implementation protocol**을 구현하면서 package metadata에 `1.2.0`을 사용했습니다. 공개 패키지 배포 전에 패키지 버전 체계를 beta line **0.1.3**으로 다시 잡았습니다. v1.2 frozen 문서는 계속 authoritative protocol reference이며 Syllva가 안정적인 1.2 제품 릴리스를 공개했다는 뜻이 아닙니다.
