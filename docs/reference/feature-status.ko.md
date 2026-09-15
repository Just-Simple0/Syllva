# 기능 상태

[English](feature-status.md) · [레퍼런스](README.ko.md)

이 문서는 어떤 기능·문서가 Syllva가 **지금 실제로 하는 일**을 설명하는지, 아니면 다음 버전을 위한 설계 제안인지를 한 곳에서 확인하기 위한 문서입니다. 이 저장소의 여러 UX·계획 문서(특히 `docs/ux/`, `docs/plans/` 아래)는 명시적으로 다음 버전 설계 제안이라고 표시돼 있지만, 문서 하나만 따로 읽을 때는 이 구분을 놓치기 쉬워 이 표를 둡니다.

| 상태 | 의미 |
| --- | --- |
| **현재 사용 가능** | 구현·배포됐고 frozen v1.2 설계/구현 명세가 다루는 범위입니다. 지금 바로 신뢰하고 사용해도 됩니다. |
| **제한된 preview** | 구현돼 있고 테스트 가능하지만 추가·opt-in 성격이며 아직 기본 경로이거나 완전히 굳어진 상태는 아닙니다. 의존하기 전에 연결된 상태 노트를 읽으세요. |
| **설계만 수용(다음 버전)** | 설계 문서가 리뷰를 통과해 다음 버전의 목표로 수용됐다는 뜻입니다. 아직 이를 구현한 운영 코드는 없으며 현재 v1.2 동작은 그대로입니다. |
| **운영 보류(기본 정지)** | 구현은 돼 있지만 운영자의 명시적 결정(예: credential 부여, 스케줄링 opt-in) 전까지 활성화하지 않습니다. |

## 검색/조회

| 기능 | 상태 | 비고 |
| --- | --- | --- |
| Read-only MCP 검색 표면(`get_context` 등) | 현재 사용 가능 | v1.2의 핵심 release invariant이며 read-only입니다. [MCP 도구](mcp-tools.ko.md) 참고. |
| 기존 전역 Notion/Drive registry(7개 전역 데이터 소스) | 현재 사용 가능 | frozen 명세가 설명하는 v1.2 검색 경로입니다. |
| 학기 단위 native Notion workspace + Drive 학기 registry | 제한된 preview | 구현된 intake slice입니다. `docs/ux/intake-v1.3-preview.md` 참고. intake worker 자신의 `readiness()` 출력이 별도의 read-only MCP 구성을 `NOT_PROVEN_BY_INTAKE_PREVIEW`로 명시합니다 — intake 성공이 그 자체로 MCP를 통한 검색 가능성을 증명하지 않습니다. |
| 개념 검색용 bounded LLM re-rank | 운영 보류(기본 정지) | `retrieval.allow_bounded_llm_rerank`로 opt-in하며 기본값은 결정적 lexical/index 검색입니다. |

## Intake·파일 처리

| 기능 | 상태 | 비고 |
| --- | --- | --- |
| Intake worker(sync/process/run, single active worker lock) | 현재 사용 가능 | `worker.enabled: true`와 별도의 Google/Notion credential이 필요합니다. [운영자 가이드: Intake와 Notion](../operator-guide/intake-and-notion.ko.md) 참고. |
| Bounded PDF 텍스트 추출 | 현재 사용 가능 | byte/page/텍스트 크기 상한이 있는 결정적 추출이며 `Ready`/`Partial`/`Needs Review`를 반환하고 부분 결과를 조용히 승격하지 않습니다. |
| 단일 `+ 업로드` 요청 흐름, Automation Queue 분리, Notion 내부 학습 UI | 설계만 수용(다음 버전) | `docs/ux/intake-execution-contract.md`(rev10)에서 수용된 설계입니다. 문서 자체가 "다음 버전 명세 개정안이며 현재 v1.2의 배포 완료를 뜻하지 않는다"고 명시합니다. |
| KNU/Canvas LMS probe/sync sidecar | 운영 보류(기본 정지) | `scripts/` 아래 독립 스크립트이며 핵심 검색의 전제조건이 아닙니다. 기본 정지 안전 규칙은 [LMS Sync](../operator-guide/lms-sync.ko.md) 참고. |

## 원격 접속

| 기능 | 상태 | 비고 |
| --- | --- | --- |
| Local MCP transport | 현재 사용 가능 | 기본값 `mcp.mode: local`. |
| Remote MCP transport(bearer/OAuth, TLS) | 운영 보류(기본 정지) | 기본값이 `remote_mcp.enabled: false`이며, 명시적으로 활성화했을 때만 `uls doctor`가 관련 credential을 검사합니다. |

## 문서 자체의 상태 표시를 읽는 법

이 요약과 문서 자체의 명시적 상태 문구가 다르면 문서 쪽을 우선하세요 — 이 페이지는 지도일 뿐 대체 권위가 아닙니다. 문서 상단 근처에서 다음과 같은 표현을 찾으세요.

- `docs/ux/intake-execution-contract.md`의 "상태: **설계 수용 완료 — 다음 버전 명세 개정안**"
- `docs/ux/intake-v1.3-preview.md`의 "This additive status note describes the implemented semester intake slice"
- `docs/operator-guide/lms-sync.md`의 "LMS support is optional and should be treated as a separately gated sidecar"

`docs/plans/`나 `docs/ux/` 아래 문서에 현재/미래를 구분하는 명시적 표시가 전혀 없다면, 현재 동작 설명이 아니라 과거 설계·검증 작업의 엔지니어링 기록으로 간주하고 frozen `university-learning-system-v1.2-design-frozen.md`/`-implementation-spec-frozen.md`나 소스 코드로 다시 확인하세요.
