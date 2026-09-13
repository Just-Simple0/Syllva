# 실제 전사문 테스트 — 알고리즘 1, 2026-03-06

## 학습 노트 보완 — 2026-09-11

사용자는 이전 AI 개요의 내용은 맞지만 학습용으로 너무 간단하다고 피드백했다.
연결 테스트용 개요를 학습 결과물로 제시한 것은 수용 기준 부족이었다.
실제 수업 페이지의 AI 영역을 아래 깊이로 다시 작성했다. SOURCE/USER와 속성은 보존했다.

- 학습 목표 → 정렬의 두 요구사항 → 1-based 의사코드와 변수 설명.
- 강의 배열 `[4,3,1,5,2]`의 전체 진행과 j=3 내부의 중간 배열·key/i 추적.
- 초기화·유지·종료의 증명, 반복 시작/끝의 구간 구별, 원소 보존과 종료성 보충.
- c_i와 t_j 구별, 줄별 비용/횟수, 최선·최악 sigma 유도와 실제 n=5 비교.
- 59:27과 1:02:03의 교수 자기 정정을 연결해 최선 t_j=1 / 최악 t_j=j 설명.
- 오개념 대조표, 난도를 높인 AI 연습문제 10개와 접힌 해설, 새 알고리즘에 증명 적용.
- 강의 timestamp 근거, AI 해설/문제 및 외부 선행 표기의 분리, 전사 용어 읽기 보조표.
- 긴 내용은 9개 단원별 native toggle heading 아래 배치했다. 상세 AI 자식 블록은
  USER 루트 영역과 분리하며, 수업 루트의 불필요한 대량 블록 증가도 피한다.

검산: 강의 예제 t_j=(2,3,1,4), 이동=(1,2,0,3); 연습 예제 이동 합 9,
while 검사 합 14; n=1..20 최선·최악 합 공식 및 중복/음수 예제 확인.
예제 검산은 정확성 증명의 대체물로 표현하지 않았다.
본문은 약 17,000자, 표 8개, 연습문제 10개, 별도 해설 toggle 11개다.
Notion readback에서 내용 끝까지 존재, 표·해설 구조, SOURCE/USER 동일성을 확인했다.

PDF connector 텍스트는 읽었다. 브라우저 PDF 도표 확인은 자동 승인 심사가 Google
로그인 origin 접근을 차단해 수행하지 못했고, 로컬 같은 파일 검색에도 결과가 없었다.
이 제한을 노트에 명시하고 이미지 수식·손글씨를 교수 SOURCE로 전사하지 않았다.
의사코드/수식표는 전사의 명시적 설명으로 재구성했으며 PDF 시각 검증 완료가 아니다.

앞으로 같은 학습용 결과를 평가할 때는 ‘핵심 내용을 나열했는가’뿐 아니라,
학습자가 원문을 반복해서 뒤지지 않고 **동작을 추적하고 이유를 설명하며 새 문제에
적용할 수 있는가**를 확인한다. 이 기준은 이번 시연 결과물의 품질 기준이며 frozen
Behavior Contract나 자동 enrichment 파이프라인을 변경·구현했다는 뜻은 아니다.

로컬 작성/검산 증거: `/tmp/uls-live-20260306/study-note.ai.md`,
`study-note.notion.md`, `study-note-calculations.json`, `study-note-qa.json`.
새 product-code 수정은 없으며 이전 1,058개 테스트를 이번 문서 변경에서 반복하지 않았다.

## 범위와 결과

사용자가 지정한 비공개 Drive 전사문 1개로 정규화·수업 등록·검색 흐름을 확인한다.
강의 날짜는 사용자 확인값 2026-03-06이다. 원문 로컬 임시 저장도 명시적으로 승인받았다.
기존 직접 실행 지시에 따라 같은 assistant가 수정과 검증을 담당한다.
Native worker 자격증명, 읽기 전용 provider grant와 실제 AI client 연결은 별도 검증이다.

## 확인된 입력과 결정

- 원본: Drive `1IHlAxm6hMTGDXyeB9mRxtho0Bk9Lf_nB`, `1주차.md`, 63,857 bytes.
- 원문 SHA-256: `43af339810bbfdd9606330bbce3f27d1857fa03b93636a93b26f8f5bd471fab7`.
  커넥터 텍스트의 UTF-8 크기가 Drive metadata와 일치한다. Provider checksum 대조는 아님.
- 시간 마커 123개: 분·초 113개, 시·분·초 10개. 처음 0:01, 마지막 1:05:13.
- Drive 폴더명의 COMP0319002를 Code COMP0319 / Section 002로 해석했다.
  Course Key는 `2026-1_COMP0319-002`, 수업 ID는 source allocator로 확정한다.
- Lecture 2 PDF는 관련 원본 링크로만 제공한다. 사용 페이지나 Verified를 확정하지 않는다.
- `.md` 유지 권장. TXT도 동일한 UTF-8 본문이면 가능하다. 정규화는 요약·오탈자 교정이 아니다.

## 변경과 검증

- `src/uls/normalization/transcript.py`: raw M:SS/MM:SS 수용, 잘못된 짧은 시간은 Partial.
- `tests/unit/test_transcript_normalization.py`: 시간 형식 전환, 원문/offset 보존,
  derivative 재파싱, canonical locator, 잘못된 마커, provider 괄호 회귀.
- Canonical evidence locator의 HH:MM:SS 문법은 유지한다.
- 실제 전사문: 원문 bytes 보존, 123개 mark/chunk, 재조합·재파싱·시간 증가 검증 통과.
- 집중 37개 테스트, 전체 **1,058개 테스트 통과** (Python 3.14.7).
  `pytest` executable 직접 실행은 repository-root import 오류 6개로 수집 실패했고,
  `.venv/bin/python -m pytest -q`는 전체 통과했다. 외부 deprecation warning 1개는 기존 항목.
- 변경 Python 두 파일 Ruff 통과, `git diff --check` 통과.
- 해당 normalizer의 mypy 단독 검사에는 기존 `normalized_at=now` 인자 타입 오류 1개가
  남는다. HEAD의 같은 호출/인자 선언을 확인했으며 이번 변경 구간의 새 오류는 없다.
  전체 정적 검사가 clean하다는 의미는 아니다.
- 실제 core `ingest_transcript` + SQLite source allocator 실행으로 `COMP0319-S01` 확정.
  PROCESSING → connector upload → content readback 일치 → Notion 생성/readback →
  processing record → READY 순서를 실행했다. 같은 입력 재실행은 외부 쓰기 0회였다.
- 업로드 파일은 부모 Derived 폴더와 `shared=false`, owner-only metadata를 확인했다.
  공개 공유를 만들지 않았다. 원본을 다시 읽어 최초 수집 내용과 같음을 확인했다.
- 완료 기록의 `ReadOnlyState`/검증된 binding을 이용한 실제 RetrievalEngine 검사:
  `1차시` alias 해석, 질문별 근거, 허용된 후속 chunk 읽기, 미발급 locator 거부 통과.
  원본 fingerprint 변경을 로컬에서 모의하면 이전 capability를 거부했다.
  Notion/Drive 읽기는 live readback snapshot 어댑터이며 native SDK/grant 검증은 아니다.

## 실제 검색에서 발견한 한계

| 질문 | 결과 |
| --- | --- |
| 삽입정렬 | `t00:09:05-00:09:34` |
| 베스트 케이스 | 첫 근거 `t00:58:19-00:58:54` |
| Asymptotic Analysis | `t01:04:40-01:05:12` |
| 루프 불변식 | 정확한 단어 일치 없음. 현 `select_chunks`가 도입부 12청크로 fallback해 관련 구간을 놓침 |
| 루프 불편성 (실제 전사 표기) | 첫 근거 `t00:35:41-00:36:15` |

따라서 근거가 반환됐다는 사실만으로 검색 품질 통과로 판정하지 않았다.
ASR 오류/표준 용어 연결 및 불일치 fallback 안내는 후속 보완 항목이다.
원문 교정이나 새 semantic search 설계를 이번 타임스탬프 호환성 수정에 섞지 않았다.
Notion AI 학습 개요는 assistant 초안이며 validated enrichment/factual SOURCE가 아니다.

## 남은 실제 사용 검증

- Native worker/MCP 각자의 자격증명과 실제 provider grants, AI client 연결.
- 운영용 Notion 전체 DB/config 구성과 임시 테스트 state의 정식 도입 정책.
  이번 공간은 Courses/Sessions 두 DB만 구성한 transcript 시연용이며 전체 운영 schema가 아니다.
- PDF 처리 및 이 수업이 사용한 페이지의 사람 확인. Material Usage/Verified를 생성·승격하지 않았다.
- 사용자에게는 수업 개요와 원문 위치가 실제 강의와 맞는지 확인하도록 안내한다.
  임시 state나 초안을 사람 승인 기록으로 취급하지 않는다.

## 테스트 공간과 증거

- Notion 테스트 페이지: https://app.notion.com/p/3d754b33957f81ba843ddd26e046b47c
- Courses DB: `c0766eff81e34356afc393c2057589a5`.
- Sessions DB: `0c535dbb082d4a959bf3371754fee4b2`.
- Course page: `3d754b33-957f-81d6-a89a-e430ec806211`.
- Session page: https://app.notion.com/p/3d754b33957f8121a21ef41d7b4e1ab1
- Drive Derived 폴더: `1P3Cz9f2-4A4N1x_GTTnadU7fX7a0RbFO`.
- Published derivative: https://drive.google.com/file/d/1eVMfapJDc2YQEAENBiVKg0wxGU3PATrH/view
- 로컬 원문·정규화본·보고서·테스트 state: `/tmp/uls-live-20260306/` (Git에 포함하지 않음).
- Native SDK 연결과 구별하여, core ingestion의 외부 쓰기는 연결된 connector 도구로
  실행하고 readback을 확인한다. 테스트 state를 운영 state로 자동 승격하지 않는다.
- 변경 파일 담당은 현재 assistant. 사용자 원문/USER 영역 및 frozen 명세는 수정하지 않는다.
- 원문 저장 크기 제한/권한 자동 심사 차단은 사용자 승인 후 해결했다. 비밀값 조회는 하지 않는다.
- 이번 변경은 아직 커밋하지 않았다.

임시 실행 증거: `normalization-report.json`, `connector-ingest-report.json`,
`notion-readback.json`, `retrieval-report.json`, `connector-state.sqlite3`.
`run_connector_ingest.py`는 이번 도구 중계용 one-shot harness이고 운영 실행기가 아니다.
`check_retrieval_snapshot.py`는 위 readback 자료에 대한 검색 검사를 재현한다.
