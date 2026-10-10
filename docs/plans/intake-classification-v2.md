# Intake 분류 v2 — 자동 분류 계획서 r14 (2026-10-10)

상태: **r14 — 계획 리뷰 GO(REQUIRED 0 / OPTIONAL 2, 2026-10-10, GPT-6 Extra High, original-bound)**. 리뷰 이력 r1(13/3)·r2(12/2)·r3(9/4)·r4(8/3)·r5(4/3)·r6(4/2)·r7(5/2)·r8(6/2)·r9(4/2)·r10(4/2)·r11(2/2)·r12(2/1)·r13(1/2)·r14(0/2). r14 선택 2건은 아래에 반영(재리뷰 불필요). 사람 승인·구현·Notion 변경·live 검증은 별도. r13 처분 `.insane-review/intake-classification-20261009/native-plan-icv2-r13/parent-disposition.json`. r12 처분 `.insane-review/intake-classification-20261009/native-plan-icv2-r12/parent-disposition.json`. r11 처분 `.insane-review/intake-classification-20261009/native-plan-icv2-r11/parent-disposition.json`. r10 처분 `.insane-review/intake-classification-20261009/native-plan-icv2-r10/parent-disposition.json`. r9 처분 `.insane-review/intake-classification-20261009/native-plan-icv2-r9/parent-disposition.json`. r8 처분 `.insane-review/intake-classification-20261009/native-plan-icv2-r8/parent-disposition.json`. r7 처분 `.insane-review/intake-classification-20261009/native-plan-icv2-r7/parent-disposition.json`. r6 처분 `.insane-review/intake-classification-20261009/native-plan-icv2-r6/parent-disposition.json`. r5 처분 `.insane-review/intake-classification-20261009/native-plan-icv2-r5/parent-disposition.json`. r4 처분 `.insane-review/intake-classification-20261009/native-plan-icv2-r4/parent-disposition.json`. r3 처분 `.insane-review/intake-classification-20261009/native-plan-icv2-r3/parent-disposition.json`. r2 처분 `.insane-review/intake-classification-20261009/native-plan-icv2-r2/parent-disposition.json`. 조사 `intake-classification-survey-20261009.md`, 항목별 기대값 fixture `intake-classification-fixtures-20261009.json`(80항목), r1 처분 `.insane-review/intake-classification-20261009/native-plan-icv2/parent-disposition.json`.
작성: Claude Fable 5.1(총괄, 사용자 지시 단독 진행). frozen 설계서/명세서는 바꾸지 않고 intake preview 계약을 **확장**한다. 채택 시 명세 개정(revision) 항목으로 기록한다.

사용자 결정(2026-10-09, 유지): TypeSafe 키는 Syllva 전용 Keychain 항목(`Syllva LLM` / `typesafe_api_key`), 임계값 confidence ≥ 0.80 · 최고확률 ≥ 0.70, 전사문 ↔ Canvas 회차 달력 매칭 채택.

## 0. 한 줄 요약

업로드/수집된 교수 자료를 **별도의 자동 분류 계획 권한(AUTO_CLASSIFICATION)** 아래에서 결정적 규칙 + Jev(TypeSafe Choice)로 과목·주차·종류까지 배정하고, 확신이 낮거나 사람 입력이 존재하는 항목은 **기존 사람 요청 권한(HUMAN_REQUEST)** 경로를 그대로 탄다. 두 권한은 서로의 receipt·필드를 위조하거나 덮어쓰지 않는다. 문서 내부 혼합 내용은 AI 소유 구간 태그로 표현하되, 검색 노출은 기존 source authorization 뒤에서만 필터된다.

## 1. 목표 / 비목표

목표
- G1. Drive 업로드 폴더·Canvas 모듈·Canvas 공지에서 들어온 항목을 사람 입력 없이 `과목 / 주차·날짜 / Kind`로 배정한다.
- G2. 문서 내부 혼합 내용을 구간 태그로 표현해 "과제 부분만", "예제 코드만" 같은 질의에 답할 수 있게 한다.
- G3. 모호하거나 사람 입력이 있는 항목만 사람에게 가며, 그 비율·오분류율·외부 호출 수를 로컬 지표로 남긴다(O1).

비목표
- 학생 제출물(계획서·발표·제출 코드)은 intake 대상이 아니다(USER 소유, SOURCE 혼입 금지).
- 설치 프로그램 바이너리(VMware 등)는 본문 수집·Material 생성을 하지 않는다.
- `Material Usage.Verified`, `Exam.Scope Confirmed`의 human-only 승격은 변경하지 않는다.
- MCP 검색 표면 read-only, 기존 `legacy5`·`c5-range-v1` Notion shape 불변.
- 기존 USER 소유 Notion 필드의 **기존 값**을 자동으로 수정하지 않는다. 유일한 예외는 §2.5 "생성 초기값 예외"로, 새 행을 만들 때 한 번만 초기값을 넣는 현행 계약(HUMAN 경로가 Actual Date로 NEW Session.Date를, Material Role로 Materials.Type을 초기화하는 것)과 동일하다.

## 2. 분류 체계

### 2.1 문서 단위 Kind (정확히 1개, 자료의 "주 목적")

| Kind | 정의 | 관측 예 |
|---|---|---|
| `TRANSCRIPT` | 강의 녹취 전사문(사용자가 만든 기록) | `2029.09.17_알고리즘2_2주차.md` |
| `LECTURE_SLIDES` | 강의 슬라이드·강의 노트 | `Lec.01_…(Chap.4.5)`, `week2_chap1`, `Chapter 03-Address` |
| `LAB_MATERIAL` | 실습 안내·실습 자료(과제 포함 가능) | `week1_lab` |
| `PROVIDED_CODE` | 공부용 참고 소스코드(교수 제공 예제) | `tcp_server.c 소스 파일` |
| `ASSIGNMENT_BRIEF` | 과제 안내 본문/첨부 | Canvas Assignment `실습과제 01`, `Lab2-1: …` |
| `ASSIGNMENT_RESOURCE` | 과제 수행용 데이터셋·서식·참고 코드 | `mbti`, 프로젝트 서식 |
| `SETUP_GUIDE` | 환경 설정/설치 안내 문서 | `00-Windows VMWare_Ubuntu설치` |
| `COURSE_INFO` | 과정 소개·강의계획·실습 안내 총론 | `00-과정소개`, `Lec.00_강의소개및실습안내` |
| `SUPPLEMENT` | 보조 자료·교재 발췌·Appendix | `Appendix01-fgets_scanf` |
| `EXAM` | 시험 안내 문서·기출문제 파일 | 기출문제 PDF(공지 첨부) |
| `ANNOUNCEMENT` | Canvas 공지 본문 | `중간고사 및 기출문제 공지` |
| `RECORDING` | 녹화 강의/회차 메타데이터(본문 수집 없음) | 날짜 제목 ExternalTool `2026-09-03` |
| `UNSUPPORTED` | 수집 금지 또는 분류 불가 | 설치 바이너리, 학생 제출물 |

Kind는 **소유권·권위를 결정하지 않는다**(R5). 소유권은 §2.4 `origin`이 따로 가진다.

### 2.2 구간 단위 태그 (0개 이상, AI 소유 라벨, R12)

`assignment`, `example_code`, `exercise`, `exam_hint`, `setup_step`, `schedule`, `grading`, `submission_guide`, `reference`, `resource_link`

- 태그 레코드는 `(chunk_id, source_fingerprint, normalization_version, tag, provenance{model|rule, version}, created_at)`로 묶이며, source fingerprint 또는 정규화 버전이 바뀌면 자동 무효(재생성 전까지 `tags_status=STALE`).
- 태그는 권위 증명이 아니다: `exam_hint`는 `Exam.Scope Confirmed`를, `assignment`는 `Material Usage.Verified`를 절대 바꾸지 않는다.
- **문서 완료 증명(M7, r6 R3, r8 R5)**: 청크·질문 단위 영속 결정 `chunk_tag_decisions(doc_id, chunk_id, question_id, decision yes|no, source rule|jev, rule_id|probabilities+confidence, source_fingerprint, normalization_version, tag_vocab_version, question_set_version, tag_rule_version, s4_payload_policy_version, prompt_version, model, decided_at)`과 문서 단위 `document_tag_manifest(doc_id, source_fingerprint, normalization_version, tag_vocab_version, question_set_version, tag_rule_version, s4_payload_policy_version, prompt_version, model, chunk_ids_hash, status)`. COMPLETE는 카운트가 아니라 **(청크 × 필수/적용 질문) 조합 전부에 현재 버전 튜플의 결정이 존재하는 정확한 coverage**로 계산하고 재시작 후 독립 재검증 가능해야 한다. 중복 청크 응답은 1건만 인정, 질문 누락은 미해결. prompt_version·model이 바뀌면 원문이 같아도 manifest STALE. **필수 질문**: 기본 검색 대상이 되는 모든 청크에 대해 `assignment` yes/no는 반드시 생성·해결되어야 하며, 질문을 생성하지 않은 청크가 하나라도 있으면 COMPLETE가 될 수 없다. 문서는 모든 청크의 필수·적용 가능한 태그 질문 전부가 (a) 결정적 규칙(`tag_rule_version`) 또는 (b) 임계값(confidence ≥ 0.80, 선택 확률 ≥ 0.70 — yes/no 양방향 동일)을 만족하는 검증된 Jev 결과로 해결됐을 때만 `COMPLETE`. 저확신 negative·검증 실패·전송 부적격·미처리 질문은 **해결되지 않은 것**으로 PARTIAL에 남는다(negative로 취급 금지). 청크 집합·fingerprint·정규화/vocab/question set/**태그 규칙/최소화 정책 버전** 중 하나라도 바뀌면 원문이 같아도 manifest는 STALE이 되어 재판정 전 기본 검색이 차단된다. S4 캐시 키에도 `tag_rule_version`, `s4_payload_policy_version`을 포함한다.
- 태그 vocabulary와 규칙 테이블은 버전(`tag_vocab_version`, `rule_table_version`)을 레코드에 기록한다(O3).

### 2.3 배정 축 (Kind와 독립, R4)

- **과목(course_key)**: 결정적. 출처별 해석기는 다음 순서이며 유일하지 않으면 과목 미정(S3).
  1. Canvas 출처: config `canvas.course_map`(검증된 `canvas_course_id → course_key`, registry `verification_state=api_code_verified` 행만). 매핑 없는 과목 ID는 수집하지 않는다.
  2. Drive 파일명: `courses[].name`(괄호 분반 제거), `courses[].code`, 신규 `courses[].aliases: list[str]`(config) + Notion Academic Courses `Aliases`(USER, 쉼표 구분) 를 합친 alias 집합. 시작 시 학기 내 alias 중복을 검사해 중복 alias는 해당 과목들에 대해 비활성(그 alias로는 항상 S3)하고 readiness에 사유를 남긴다.
- **주차**: 명시 표기만 인정한다. Canvas 모듈 이름 `^(\d+)주차$`, Drive 파일명 `_(\d+)주차`. 모듈 `position` 숫자는 주차로 해석하지 않는다.
- **날짜**: Drive 파일명 `YYYY.MM.DD`/`YYYY-MM-DD`, Canvas 항목 제목 `YYYY-MM-DD`. 형식 외는 날짜 없음.
- **회차 달력(사용자 결정 채택)**: Canvas `RECORDING` 항목(과목, 모듈 주차, 날짜)으로 과목별 `(date → week)` 인덱스를 만든다. 달력은 **메타데이터 인덱스**이며 그 관측 자체로 Session을 만들지 않는다.
- **Session 바인딩 규칙(TRANSCRIPT만)**:
  - 파일명 날짜가 같은 과목 달력의 회차 날짜와 정확히 1개 일치하고, 파일명 주차가 있으면 달력 주차와 같아야 한다.
  - 그 과목·날짜의 Session이 **없으면** `NEW` + 초기 Date=그 날짜로 생성한다. 이는 현행 HUMAN 경로가 Actual Date로 새 Session의 Date를 초기화하는 것과 동일한 "초기값" 계약이며, 이후 Date/Session No는 USER 소유로 자동 수정하지 않는다.
  - Session이 **정확히 1개 있으면** `EXISTING`으로 바인딩만 한다. Date/Session No/Topics 등 USER 필드는 건드리지 않는다. **기존 결속 보호(r7 R3)**: 그 Session에 다른 provider file의 source binding(`record_session_source_binding`)이나 `Normalized Transcript` 포인터가 이미 있으면 EXISTING 적격이 아니다. 같은 provider file ID·source version·plan의 멱등 재진입만 허용하고, 다른 전사문이 점유 중이면 포인터 교체 없이 S3(`Suggestion Note=SESSION_OCCUPIED`). 이 검사는 단계 A와 포인터 갱신 직전에 재확인한다. P-B/P-F 테스트: 빈 EXISTING / 동일 파일 재진입 / 다른 전사문 점유 → 자동 포인터 교체 0건.
  - 불일치(예: 2029년), 달력 없음, 날짜 중복 회차, Session 2개 이상, 기존 Session Date와 충돌 → S3(제안값만 동반).
  - **실행 시점 재검증(r6 R1)**: AUTO preflight와 `_reserve_session()`/`_ensure_session_page()` 직전에 과목 달력 projection과 현재 Notion Sessions inventory를 다시 조회해 record의 `calendar_projection_revision_hash`·`sessions_inventory_hash`와 일치해야 한다. NEW면 같은 과목·날짜 Session이 **0개**, EXISTING이면 **정확히 1개**이고 ID·Course·Date가 record와 일치해야 하며, `_reserve_session()` NEW 경로는 다음 Session ID 선택 전에 같은 날짜 Session 부재를 검사한다. **자체 효과 예외(r7 R2)**: 같은 AUTO plan이 이미 성공시킨 쓰기는 외부 변경이 아니다. NEW에서 같은 과목·날짜 Session이 정확히 1개 있고 그것이 동일 `plan_revision`·reservation·operation key로 생성되어 `READBACK_OK`인 Session이면 ID/Course/Date를 재검증해 **멱등 재개**로 인정한다(현행 `_ensure_session_page()`의 예약 ID 재검증 경로 사용). 사람이 만들었거나 plan 결속을 증명할 수 없는 Session은 차단, NEW→EXISTING 조용한 전환 금지. Drive 이동이 끝난 뒤 provenance 발행이 실패한 경우도 동일 move intent와 목적지·privacy readback이 확인될 때만 재개한다. `sessions_inventory_hash` 비교는 자체 효과를 제외한 inventory로 수행한다. P-B/P-F 테스트: Session 생성 직후 실패, 생성 응답 손실, 이동 완료 후 provenance 실패, 재시작 재개 → 중복 Session·폴더·이동 0건. 근거가 바뀌면 NEW↔EXISTING을 조용히 바꾸지 않고 `RECONCILE_REQUIRED` 또는 S3로 중단한다. P-B/P-F 테스트: 달력 날짜 수정·리소스 삭제, 계획 후 같은 날짜 Session 생성, USER Date 변경, 중복 Session 시도 → 불일치 이후 새 Session 생성 0건.
  - **달력 이상치(M6)**: 달력 항목은 학기 범위 안이어야 하고 주차 순서와 날짜 순서가 단조(앞 주차의 날짜 < 뒤 주차의 날짜)여야 한다. 관측된 알고리즘2 4주차 `2026-12-10`처럼 순서가 깨지거나 학기 밖이면 그 항목은 `ANOMALY`로 표시해 매칭에서 제외하고, 그 날짜의 전사문은 S3로 보낸다. 결정 규칙(M2): 과목별 달력에서 **주차 cadence**로 판정한다. 가장 작은 주차의 항목을 anchor로 두고 `expected(week) = anchor_date + 7일 × (week − anchor_week)`를 계산해, `|date − expected| > 6일`인 항목을 `ANOMALY`로 표시한다(이웃 비교가 아니라 anchor 기준이므로 순서 독립·결정적). anchor 자체가 학기 범위 밖이거나 anchor 포함 유효 항목이 2개 미만이면 그 과목 달력 전체를 `AMBIGUOUS`로 두고 매칭하지 않는다(S3). 학기 범위는 config `semester_registries[].start_date/end_date`(없으면 Canvas term 기간, 둘 다 없으면 AMBIGUOUS)에서 온다. 관측 예(알고리즘2): 1주차 09-03(anchor), 2주차 09-10, 3주차 09-17, 5주차 10-01은 유효, 4주차 12-10은 expected 09-24 대비 77일이라 ANOMALY — fixture 기대값과 일치. 같은 날짜 항목이 2개 이상이면 전부 보존하고 `AMBIGUOUS`로 표시해 매칭 불가(S3).
  - **활성 관측 projection(R1)**: `canvas_observations`는 revision 이력을 보존하지만 달력 계산은 **resource별 최신 활성 observation 1건**만 담는 `recording_calendar_current(course_key, canvas_course_id, resource_id, date, week, observation_revision, status)` projection에서 수행한다. 같은 resource의 이전 revision은 집계에 들어가지 않는다(revision 갱신·날짜 수정은 행 교체, 삭제·미관측은 행 제거). projection 갱신과 중복 수 재계산은 해당 과목 모듈 수집이 **bounded pagination을 끝까지 완료**(probe의 `complete` 판정)한 관측에서만 확정하고, 불완전 수집이면 과목 달력 전체를 `AMBIGUOUS`로 닫는다. anchor(최소 주차)에 날짜가 다른 항목이 2개 이상이면 anchor를 고르지 않고 과목 달력 `AMBIGUOUS`(S3). 모호한 달력으로 NEW Session이 생성되는 경우는 0건이어야 한다. P-B 테스트: 같은 resource revision 갱신, 날짜 수정·삭제, 서로 다른 resource 같은 날짜, 첫 주차 다중 녹화, 페이지 일부 누락.

### 2.5 생성 초기값 예외 (M6)

USER 소유 필드에 자동값이 들어가는 경우는 **새 행 생성 시 한 번**뿐이며 다음 두 가지로 한정한다. 기존 행의 USER 값은 어떤 경로도 자동 수정하지 않는다.
1. NEW Session 생성: `Date` 초기값 = 검증된 달력 날짜(§2.3). 현행 HUMAN 경로가 Actual Date로 초기화하는 것과 같은 계약.
2. 신규 Material 생성: `Type` 초기값 = §5 표. 현행 HUMAN 경로가 Material Role로 초기화하는 것과 같은 계약.

### 2.4 origin (출처·작성 주체, Kind와 독립, R5)

`PROFESSOR_SOURCE`(Canvas 모듈/과제/공지에서 온 교수 자료), `USER_AUTHORED_TRANSCRIPT`(사용자가 만든 강의 전사문), `USER_NOTE`(사용자 필기·메모), `STUDENT_SUBMISSION`(학생 제출물), `UNKNOWN`(업로드 폴더의 기타 파일).

- Drive 업로드 폴더: 전사문 파일명 규칙 일치 → `USER_AUTHORED_TRANSCRIPT`; 그 외 전부 `UNKNOWN`. `UNKNOWN`은 **Jev 전송 금지**, S1 규칙만 적용하고 미결이면 S3.
- **UNKNOWN의 권위 상한(M5)**: `UNKNOWN` origin 항목은 S1으로 Kind를 저장할 수 있어도 AUTO plan으로 Material을 **Ready·검색 발행하지 않는다**. 이런 항목은 S3로 가며 Suggested Kind를 동반하고, 사람이 Material Role/Type을 확정하거나(HUMAN 경로) 검증된 Canvas provenance가 연결될 때만 `material_type_source_class` 권위가 적용된다. 파일명 일치만으로 `professor_material`이 되지 않음을 P-B/P-F 테스트로 고정한다.
- Canvas 출처: `PROFESSOR_SOURCE`. 학생 제출(Assignment submission, 발표 자료)은 수집 대상이 아니므로 `STUDENT_SUBMISSION`은 Canvas 메타에서 관측만 되고 intake를 만들지 않는다.
- 전사문 내부 혼합: 정규화 단계에서 USER 메모 구간은 기존 locator subtype `:user`로 분리 저장하며, 교수 발화 구간(`:source`)만 Jev 전송·태그 대상이 된다. 분리 표식이 없는 전사문은 전체를 source 구간으로 두는 현행 전사 계약을 유지한다.
- 증거 권위(EvidenceItem authority)는 기존 설계대로 origin과 Materials.Type(USER) 매핑에서 나오며 Kind가 바꾸지 않는다.

## 3. 분류 파이프라인과 권한

```
관측 → S0 출처 메타 → P0 금지 → S1 규칙 테이블 → (미결) S2 Jev Choice → (저확신/부적격) S3 Input Request(제안 필드만)
                                         ↘ 확정 → AUTO_CLASSIFICATION plan → 기존 처리 → 정규화 → S4 구간 태그
```

### 3.1 S1 규칙 테이블 우선순위 (R6)

같은 우선순위 단계에서 정확히 하나의 Kind가 나오면 확정하고 하위 단계는 보지 않는다. 한 단계에서 둘 이상이면 S2(origin이 Jev 부적격이면 S3). 어느 단계에서도 없으면 S2/S3.

| 우선 | 신호 | 결과 |
|---|---|---|
| P0 | 금지 확장자 `.exe .dmg .pkg .msi .iso .zip(설치)` 또는 제목 `설치 프로그램` | `UNSUPPORTED` **터미널**: 본문 다운로드·Jev·Material 생성 없음, File Intake `Status=UNSUPPORTED` + 사유 코드만 기록 |
| P1 | Canvas 출처 유형: Assignment 항목 → `ASSIGNMENT_BRIEF`; 공지 본문 → `ANNOUNCEMENT`; 날짜만인 제목 ExternalTool → `RECORDING` | 확정 |
| P2 | 명시 식별자: `과정소개|강의소개|강의계획|syllabus` → `COURSE_INFO`; `기출|중간고사|기말고사` **파일** → `EXAM`(공지 본문은 P1에서 이미 ANNOUNCEMENT) | 확정 |
| P3 | 제목 규칙: `Lec\.\d+`, `week\d+_chap\d+`, `Chapter \d+` → `LECTURE_SLIDES`; `week\d+_lab`, `실습` → `LAB_MATERIAL`; `설치|install|setup` → `SETUP_GUIDE`; `Appendix|부록` → `SUPPLEMENT`; `소스 (파일|코드)` → `PROVIDED_CODE`; 업로드 폴더 전사문 규칙 `^(\d{4})\.(\d{2})\.(\d{2})_(.+?)_(\d+)주차(\.md)+$`(이중 `.md.md` 허용, MIME text/markdown) → `TRANSCRIPT` | 확정/다중이면 S2 |
| P4 | 확장자: `.c .cpp .h .py .java .js .sql .ipynb` → `PROVIDED_CODE`; `.csv .json .xlsx`는 과제 provenance(Canvas Assignment 첨부) 또는 제목 신호가 있을 때만 `ASSIGNMENT_RESOURCE`, 없으면 미결(S2/S3); `.pdf .pptx .docx .md` 단독 → 미결 | 확정/미결 |

규칙 결과 레코드는 `decision={type:"rule", rule_id, rule_table_version}`이며 확률을 기록하지 않는다. 예: `Lec.00_강의소개및실습안내`는 P2에서 `COURSE_INFO`로 확정(P3의 LECTURE/LAB 충돌에 도달하지 않음). `Week1`처럼 P3 규칙이 없고 확장자도 없는 제목은 S2.

### 3.2 S2 Jev Choice

- 적격: origin이 `PROFESSOR_SOURCE` 또는 `USER_AUTHORED_TRANSCRIPT`의 source 구간. `UNKNOWN`/`USER_NOTE`/`STUDENT_SUBMISSION`은 전송 금지.
- 입력: 제목, 출처 메타(과목 키, 모듈 주차, 항목 유형), 확장자, 본문 발췌(텍스트 ≤ 6KB, PDF 추출 첫 2쪽, 코드 첫 120줄).
- **outbound 최소화(M7)**: payload의 **모든 문자열 필드**(제목·메타·발췌)는 직렬화 전에 데이터 최소화·식별자 제거(이름·학번·이메일·전화)를 거치고, URL은 경로·쿼리를 통째로 고정 placeholder `<url>`로 치환한다(O3). 직렬화 직후 최종 payload를 다시 검사해 식별자·URL 패턴이 남아 있으면 Jev 호출 없이 S3(`Suggestion Note=PII_UNCERTAIN`). 전사문은 정규화로 `:source`/`:user` 구간 분리가 **완료된 뒤**에만 source 구간을 전송하며, 분리 전 원문·분리 불가 전사문은 전송하지 않는다(S1 또는 S3). 개인정보 포함 제목 회귀 테스트를 둔다. 직렬화된 최종 요청 바이트가 16,384 초과면 발췌를 줄이고, 그래도 초과면 S3.
- 질문: `type: choice`, 옵션 = §2.1 Kind(UNSUPPORTED·RECORDING·ANNOUNCEMENT 제외) + `no_match`.
- 확정: `confidence ≥ 0.80` 그리고 최고확률 ≥ 0.70(사용자 결정). 미달·`no_match`·provider 실패·검증 실패 → S3. **예산 소진은 S3가 아니라 `DEFERRED`**(§4 상태 전이 규칙). 임계값은 정확도 주장이 아니며 §10 평가 fixture로 오분류·폴백률·Kind별 confusion matrix·확률 구간별 정답률을 측정한다.
- 레코드: `decision={type:"model", model:"jev-1.13.0", prompt_version, option_set_version, probabilities, confidence}`.

### 3.3 S3 Input Request — 제안 필드만 (R2)

- 기존 USER 필드(Course, Kind, Actual Date, Session, Session Mode, Session No, Material Role, Submitted, Cancelled)는 **초기 draft 그대로 비워 둔다**. `allow_user_defaults` 우회를 쓰지 않는다.
- 신규 SYSTEM_DERIVED 필드로 제안만 적는다: `Suggested Course`(rich_text course_key), `Suggested Kind`(select, §2.1), `Suggested Date`(date), `Suggested Week`(number), `Suggestion Source`(rich_text: `rule:<id>` | `jev:<ver>` | `calendar`), `Suggestion Note`(rich_text 고정 사유 코드). 사람은 보고 USER 필드에 직접 입력한다.
- 제안 필드는 draft **생성 시**에만 쓰고, 이후 사람이 USER 필드에 무엇이든 입력했거나 Submitted/Claimed이면 다시 쓰지 않는다. `normalized_user_hash`와 Submitted 의미는 변하지 않는다. ASSIGN_COURSE 다음에 생성되는 FILE_DETAILS draft에도 같은 검증된 제안값을 **새 draft 생성 시** 다시 적는다(O2; USER 필드 변경 없음, `target_snapshot`과 무관).

### 3.4 AUTO_CLASSIFICATION 계획 권한 (R1)

- `IntakePlan.plan_authority ∈ {HUMAN_REQUEST, AUTO_CLASSIFICATION}` 추가. HUMAN은 현행(receipt + Submitted + normalized_user_hash). AUTO는 Input Request/receipt를 만들지 않으며 **절대** Submitted=True나 가짜 receipt를 쓰지 않는다.
- AUTO plan의 불변 입력(`classification_record`): `intake_id, provider_file_id, byte_sha256, byte_md5, snapshot_sha256, calendar_projection_revision_hash(TRANSCRIPT: 과목 `recording_calendar_current` 전체의 canonical hash — 완전 수집 여부·활성 resource/revision·날짜·주차·이상치 상태 포함), semester_range_basis(실효 학기 시작일·종료일과 출처 `config|canvas_term`; r13 O1 — preflight와 `_reserve_session()` 직전에 재검증, 변경 시 NEW Session 생성 0건), sessions_inventory_hash(과목·날짜의 Notion Sessions 조회 결과 hash), course_basis(r7 R4: `{type: config_alias|notion_alias|canvas_map, alias_inventory_hash(Notion Academic Courses Aliases readback canonical hash; alias 기반일 때), canvas_binding(course_id, resource_kind, resource_id, observation_revision, attachment_id, drive_file_id, byte_sha256; Canvas 기반일 때, O2 — 같은 resource/revision의 첨부 A·B record가 서로 구별됨)}`), source_version, observation_id, workspace_fingerprint, config_fingerprint(과목/alias/canvas 매핑), rule_table_version, decision(§3.1/3.2), course_key, kind, origin, week, date, session_mode, session_id`. `classification_revision_hash = sha256(canonical json)`가 `request_revision_hash` 자리를 대신한다.
- **바이트 동일성(R3)**: 관측 `source_hash`가 metadata surrogate(`sha256:metadata-*`)일 수 있으므로 AUTO 확정 전에 실제 다운로드 바이트의 SHA-256과 MD5를 계산해 `byte_sha256`, `byte_md5`로 record에 고정하고(O1), preflight와 각 외부 쓰기 직전 freshness 경계에서 provider가 md5를 주면 `byte_md5`와, 주지 않으면 재다운로드 바이트의 SHA-256과 `byte_sha256`을 대조한다. 바이트 증명을 확보할 수 없으면(다운로드 실패·크기 상한 초과) AUTO plan을 활성화하지 않고 S3 또는 reconcile로 제한한다. 로컬 다운로드·파서 probe의 파일당 상한은 config `intake.classification.max_source_bytes`(기본 50 MiB, O1)이며 초과는 S3(`Suggestion Note=SOURCE_TOO_LARGE`). P-B/P-F 테스트: checksum 미제공 파일, 분류 후 내용 변경.
- 실행 검증 `_assert_classification_unchanged`: 처리 직전 현재 source hash/version·workspace fingerprint·config fingerprint·항목 상태(PLANNED, 사람 요청 없음)·classification_revision_hash 일치를 요구한다. 불일치 → `RECONCILE_REQUIRED`(현행과 동일 복구 경로), 효과 0.
- **Job 결속(r8 R2)**: `Job` 모델에 불변 `plan_revision`과 `plan_authority`를 추가한다. `_enqueue_plan_job()`이 plan에서 복사하고, job claim 시점과 `_process_item_unlocked()` 진입 직전에 `job.plan_revision == item.plan_revision == plan.plan_revision` 및 authority 일치를 검사한다. plan이 `SUPERSEDED`로 닫히면 그 plan의 미처리 job을 영속 상태 `VOID`로 닫아 재시작 후에도 실행되지 않게 하고(`Job.is_terminal`에 VOID 포함, 상태 전이·영속 복구의 터미널 값, O1), 새 HUMAN plan의 job만 실행한다. 공개 `process_item()` 직접 실행도 같은 plan revision/authority 검사를 받으며 AUTO plan의 직접 실행은 거부한다(O1). 테스트: AUTO job PENDING → HUMAN supersession → 새 plan → worker 재시작 → 이전 job의 provider mutation 0건, 새 HUMAN plan 중복 실행 0건.
- **실행 컨텍스트(R1)**: `_process_item_unlocked()`는 plan을 찾은 직후 `plan_authority`로 **먼저 분기**한다. HUMAN은 현행(`_receipt_for_plan` → `RequestInput`). AUTO는 receipt를 찾지 않고 불변 `ClassificationExecutionContext`를 공급한다: `course_key, kind, origin, session_mode(NEW|EXISTING|None), session_id, actual_date, week, material_type_initial(§5 표), source_identity(provider_file_id, byte_sha256, source_version), workspace_fingerprint, config_fingerprint, classification_revision_hash, record_id`. Kind dispatch(`_process_transcript`/`_process_material`), `_plan_operation_key()`(session_mode/material_type_initial을 키에 포함), `_ensure_session_page`, `_ensure_material_page`, 이동·발행 함수는 `RequestInput | ClassificationExecutionContext` 공용 프로토콜(필요 필드만)을 받도록 바꾸고, 각 권한 검사 지점에서 `authority`에 따라 HUMAN guard(`_assert_receipt_unchanged`) 또는 AUTO guard(preflight/재확인)를 주입한다. `bind_job_source_identity`에 `authority`와 revision hash를 기록한다. HUMAN `RequestInput`·Submitted receipt는 위조하지 않는다.
- **과목별 workspace 결속(M1)**: AUTO job의 과목은 `classification_record.course_key`가 유일한 권위다. `_workspace_for_item()`은 `plan_authority=AUTO`면 `selected_course_key`(HUMAN 선택 필드, 비워 둠)가 아니라 record의 course_key로 workspace를 고르고, 학기 첫 workspace로의 fallback을 **금지**한다(과목 workspace가 없거나 검증 실패 → job 거부, 효과 0). `_enqueue_plan_job()`의 `job.course_key`, `_publish_processing_provenance()`의 provenance course, File Intake `Course` relation projection(기존 SYSTEM_CONTROLLED 필드)에 같은 record course_key를 전달하고, job claim 시점에 record·job·workspace의 course_key 삼자 일치를 검사한다. P-B 테스트: 한 학기 2과목, AUTO 대상이 첫 번째가 아닌 과목, receipt 0건 → Course relation·폴더 parent·job.course_key·provenance 전부 일치하며 Session/Material dispatch를 거쳐 완료.
- **중복 콘텐츠 게이트(r6 R2)**: `AUTO_PENDING → PLANNED` 전에 `byte_sha256` 기준으로 **다른 provider file ID**의 현재 intake 항목·source version·canonical binding과 충돌하는지 조회한다(현행 `route_intake(duplicate_file_ids=...)` 정책을 AUTO 경계에 연결). 같은 ID·같은 source version은 재진입(기존 plan 재사용), 다른 ID·같은 콘텐츠는 자동 생성하지 않고 S3(`Suggestion Note=DUPLICATE_CONTENT`), 이미 canonical binding이 있는 중복 업로드는 S3 + 기존 binding 참조. 조회가 불완전하거나 판정 불가면 AUTO 확정 금지. P-B 테스트: 같은 SHA-256·다른 Drive ID, 같은 ID 재관측, canonical binding 존재 중복.
- **활성화 2단계(r7 R1)**: AUTO 적격성 검사는 두 단계로 분리되며 순서가 고정된다.
  - **단계 A — `AUTO_PENDING` 적격성(Draft 변경 없음)**: Kind·과목·달력/Session 근거·형식/파서 probe·바이트 동일성(`byte_sha256`)·중복 콘텐츠·provenance(origin/binding)·alias 근거를 **전부** 확인한다. 이 단계에서는 수정되지 않은 기존 HUMAN Draft의 존재를 허용하되 USER snapshot을 엄격히 확인하고, 어떤 검사라도 실패하면 Draft를 건드리지 않고 S3 또는 `RECONCILE_REQUIRED`로 끝낸다.
  - **단계 B — `PLANNED` 전이**: 단계 A 통과 후에만 §3.5 Auto Resolved intent를 수행하고, intent `DONE`(readback 완료)과 실행 시점 불변성(§3.4 preflight의 상태 조건은 "HUMAN 요청이 없거나 AutoResolved 터미널")을 확인해 `PLANNED`로 전이한다. 단계 B의 preflight는 `AUTO_PENDING` 상태를 전제로 하며 PLANNED를 요구하지 않는다(실행 진입 preflight와 구분).
  - P-B/P-F 테스트: 중복 SHA-256, 손상 PDF, 단계 A 실패 시 기존 Draft가 Auto Resolved로 바뀌지 않음.
- **사람 우선 선행검사(M2)**: AUTO plan은 `AUTO_PENDING`으로 만들어지고, 다음을 모두 확인한 뒤에만 `PLANNED`(실행 가능)로 전이한다. (1) 같은 intake에 HUMAN 요청이 없거나, 있다면 §3.5 자격을 모두 만족해 `Auto Resolved`로 **먼저** 종료되고 로컬 receipt가 터미널 `AutoResolved`로 전이됨; (2) 응답 불명의 pending Request create(§3.5 복구 대상)가 0건; (3) 사람이 USER 필드를 하나라도 바꾼 Draft, Submitted, Claimed가 있으면 AUTO plan을 `SUPERSEDED`로 닫고 HUMAN 경로만 진행. 이미 `PLANNED`인 AUTO plan도 사람 제출이 관측되면 실행 전 `SUPERSEDED`. `_submitted_request_keys()`·`_run_layout_workspaces()`의 터미널 판정에 `AutoResolved`를 `Applied/Cancelled`와 같은 터미널로 추가한다.
- **mutation preflight(M3)**: AUTO 실행 진입 시(`status=PLANNED` 요구), 첫 외부 쓰기(Session 예약·폴더 생성·Material 생성·Drive 이동·derivative 발행) **이전에** 원본 ID·owner-only/private 상태·정확한 parent·source version/hash·workspace/config fingerprint·HUMAN 요청 상태·classification_revision_hash를 한 번에 검증한다(`_assert_classification_unchanged` = preflight). 이후 각 외부 쓰기 지점(`_ensure_session_page`, `_ensure_material_page`, 폴더 생성, 이동, 발행) 직전의 재확인은 (a) authority 유효성(SUPERSEDED 아님), (b) 소스 불변·fingerprint, (c) **해당 intake의 HUMAN 요청 readback: 터미널 상태와 AUTO 전용 snapshot이 preflight 때와 동일(R3, r8 R4)** — AUTO snapshot = `sha256(정확한 USER 필드값 전체, Submitted(엄격 bool), Cancelled(엄격 bool), Request Status, Request Key, receipt generation)`로 정의하며 `normalized_user_hash()`(Submitted/Cancelled 미포함)와 별개이고 그 의미를 바꾸지 않는다; 체크박스가 bool이 아니면 변경으로 간주 —, (d) **과목·origin 근거(r7 R4)**: `course_basis`가 alias 기반이면 Notion Aliases readback hash 일치, Canvas 기반이면 `canvas_drive_bindings`의 정확한 행이 아직 유효(revision 교체·해제 없음) — 네 가지를 검사하며, (d) 불일치 시 record를 다른 과목·origin으로 재해석하지 않고 후속 쓰기를 중단한다(`RECONCILE_REQUIRED`). `_config_fingerprint()`는 Notion USER Aliases를 포함하지 않으므로 별도 hash로 다룬다. P-B/P-F 테스트: alias 수정, Canvas revision 교체, binding 해제, 제목만 같은 미결속 Drive 파일. 항목 상태는 현행 상태기계가 기대하는 값(`REGISTERED`, `MOVING` 등)을 그대로 허용한다(O1). (c)가 어긋나면 이후 쓰기를 중지하고 AUTO를 `SUPERSEDED`(사람 변경) 또는 `RECONCILE_REQUIRED`로 닫되, 이미 성공한 외부 쓰기는 삭제·재생성하지 않고 기존 멱등 readback 복구 경로에 남긴다. 같은 intake의 **기존 HUMAN 요청 전체**(ASSIGN_COURSE 적용 후 생성된 FILE_DETAILS 포함)를 검사 대상으로 한다. P-B/P-F 테스트: preflight 이후·Session/Material 생성 직전·Drive 이동 직전의 HUMAN 변경 주입 → 완료된 쓰기는 존재, 변경 이후 추가 쓰기 0건; Submitted만 false→true, Cancelled만 false→true, USER 필드만 수정, 응답 손실 후 단독 변경 각각 → 추가 AUTO mutation 0건. HUMAN 경로의 호출 순서는 재설계하지 않는다.
- **터미널 receipt 재진입 차단(R2)**: `_claim_request_unlocked()` 시작부에서 로컬 receipt state가 `Applied`/`Cancelled`/`AutoResolved`면 신규 plan·job·Notion 쓰기 없이 `IntakeReconcileRequired`가 아닌 고정 거부(`REQUEST_TERMINAL`)로 종료한다. **AutoResolved DONE 이후 HUMAN 변경(r7 R5)**: 종료된 요청 페이지에서 사람이 USER 필드를 수정하거나 Submitted=True로 바꾸면 AUTO guard가 실행을 중단하고 AUTO plan을 `SUPERSEDED`로 닫은 뒤, 터미널 receipt를 되살리지 않고 **새 HUMAN 요청 generation**을 만든다: 같은 intake에 대해 **요청 revision 계약을 확장**해 새 Draft를 만든다(r8 R1): 현행 Request Key 입력(provider binding, semester, request type, observation/source version, config fingerprint, target snapshot)에 `superseded_request_key`와 영속 `request_generation`(intake별 카운터; `intake_request_generations(intake_id, generation, superseded_request_key, created_at)`에 **생성 호출 전에** 영속화하고 재시작 시 같은 값으로 복구)을 추가하므로 원본·설정이 불변이어도 `new_key != old_key`가 보장된다. `_create_input_request_with_context(request_type=동일, target_snapshot=None, generation=…)`로 호출하며, **claim-time 검증(r9 R4)**: `_assert_request_generation_current()`는 receipt의 영속 generation 레코드를 조회해 그 레코드의 **`derivation_version`**(P-A 마이그레이션이 백필한 기존 요청은 generation 번호와 무관하게 `LEGACY`, v2가 예약한 supersession generation은 `V2`)에 따라 — `V2`면 v2 derivation(기존 입력 + `superseded_request_key` + `request_generation`)으로, `LEGACY`거나 레코드가 없으면 기존 derivation으로 — Request Key/Revision Hash를 재계산한다(P-A r9 R2: 한 intake에 legacy 요청이 여럿이면 생성 순서대로 generation 1, 2, …를 받되 모두 `LEGACY`다). 영속 generation 데이터가 없는데 v2 키 형태이거나 충돌하면 추측하지 않고 `RECONCILE_REQUIRED`. 업그레이드 이전 Draft/Claimed는 기존 derivation으로 그대로 검증된다(P-A 마이그레이션은 기존 요청에 generation=1 레코드를 백필). 테스트: legacy Draft/Claimed claim 성공, 새 generation Draft의 Submitted→Claimed→HUMAN plan 성공, 생성 응답 손실 후 동일 generation 복구. `Result Reference`에 이전 요청 키와 `AUTO_SUPERSEDED` 사유를 기록, 제안 필드는 §3.3대로 채운다. 기존 키는 계속 터미널이다. 테스트: 원본·설정 불변에서 old_key ≠ new_key, 새 Draft 정확히 1건, 기존 터미널 키의 신규 plan/job 0건, create 응답 손실 후 중복 요청 0건. **관측 경로(r9 R1)**: AUTO job이 끝난 뒤(File Intake `ORGANIZED` 등)의 사람 변경은 mutation guard가 더 이상 돌지 않고 원본도 업로드 폴더를 떠났으므로, `_run_once_attested()`에 **AutoResolved receipt 전용 bounded reconciliation scan**을 둔다: 영속 cursor(`auto_resolved_scan_cursor`)와 틱당 상한(config `intake.classification.max_terminal_scan`, 기본 50)으로 `AutoResolved` receipt를 순회해 정확한 provider page를 재조회하고 영속 AUTO snapshot(§3.4 (c))과 현재 USER 필드·Submitted·Cancelled를 비교한다. 변경이 확인되면 기존 터미널 키는 그대로 두고 위 새 HUMAN generation을 만들며, 이미 만들어진 Session·Material은 handover/reconcile 계약으로 이어받는다(삭제·재생성 없음). 변경 없는 터미널 요청은 건드리지 않는다. **스캔 멱등성(r10 R1)**: 기준 snapshot은 두 종류다 — `pre_close_snapshot`(§3.5 intent 검사용, Draft 상태)과 `terminal_snapshot`(AutoResolved readback 직후 확정·영속; Request Status=Auto Resolved 포함). 스캔은 `terminal_snapshot`과만 비교하므로 시스템 자신의 종료 전이를 사람 변경으로 오인하지 않는다. 변경 처리는 영속 `terminal_change_ledger(original_receipt_id, changed_user_snapshot_hash, created_generation, created_request_key, state)`로 결속하며 같은 `(receipt, changed hash)` 재관측은 기존 generation을 반환하고 카운터를 올리지 않는다. 해당 intake에 미처리(Draft/Submitted/Claimed) HUMAN generation이 이미 있으면 추가 변경은 새 generation을 만들지 않고 그 generation의 `Error`에 `TERMINAL_CHANGED_AGAIN`으로 reconcile 표시만 한다(세대 교체는 사람이 기존 generation을 취소한 뒤에만). 테스트: AUTO 완료 → USER 변경 → 연속 3틱·재시작에서 새 Draft 정확히 1건; 변경 없는 AutoResolved는 generation 0건; 기존 generation 제출 중 옛 터미널 페이지 재관측 → 중복 claim 0건. 테스트: AUTO 완료 → ORGANIZED → USER 수정/Submitted 변경 → 다음 틱 → 새 HUMAN Draft 정확히 1건, 기존 receipt 재Claim 0건, 중복 entity 0건; 미변경 터미널 요청 변경 0건. 사람이 그 새 요청을 제출하면 HUMAN 경로가 처리한다. 기존 canonical binding·부분 완료된 외부 쓰기는 삭제하지 않는다. **ASSIGN_COURSE 재실행 보호(r10 R2)**: AUTO 효과가 있는 intake(effect ledger 비어 있지 않음 또는 상태 ≥ REGISTERED/ORGANIZED)에 대한 새 HUMAN generation은 `_claim_request_unlocked()`의 ASSIGN_COURSE 분기(`update_intake_item(... status=NEEDS_INPUT)` + FILE_DETAILS 생성)보다 **먼저** handover 자격을 검사한다. 자격이 있으면 과목 선택 절차를 재실행하지 않고 확인된 결과(course_key·Kind·Session/Material binding)를 이어받는 FILE_DETAILS 경로 또는 명시적 reconcile로 진행하며, 항목 상태·canonical binding·완료 상태를 검증 없이 되돌리지 않는다. 사람이 다른 과목을 선택한 경우는 자동 이동·재배치가 아니라 `RECONCILE_REQUIRED`(사람 결정)로 남긴다. 테스트: 기존 ASSIGN_COURSE Draft → AUTO 완료 → 사람 변경 → 새 HUMAN 제출 → Session/Material·폴더·canonical binding 보존, 상태 회귀 0건, 중복 처리 0건. **교차 권한 인계 규칙(r8 R3)**: AUTO plan의 effect ledger(reservation, provider page ID, source binding, 폴더 marker, move intent, derivative 발행)가 **비어 있으면** 새 HUMAN plan은 정상 실행한다. 효과가 하나라도 있으면 새 HUMAN plan은 `handover_record(from_plan_revision, to_plan_revision, adopted_effects)`를 영속화하고 각 효과를 **정확히 재검증**(예약 ID·entity ID·Course·Date/Type·readback, 폴더 marker, 이동 목적지·privacy, binding의 provider file ID·source version)한 뒤 그대로 **채택**해 이어서 실행한다. 채택된 효과의 reservation/operation key는 새 plan_revision으로 재발급하지 않고 handover_record가 매핑한다. 하나라도 동일성이 증명되지 않으면 `RECONCILE_REQUIRED`로 제한하고 새 entity 생성을 금지한다. 새 요청 생성이 불가능해도 `RECONCILE_REQUIRED`. 테스트: Session 생성 후·Material 생성 후·Drive 이동 후 HUMAN 변경 각각 → 새 Session·Material·폴더 중복 0건, canonical binding 보존. P-B/P-F 테스트: AutoResolved DONE → USER 수정 → Submitted=True → AUTO 중단 → 새 HUMAN 요청 1건 생성, 기존 터미널 키로의 plan/job 0건. Auto Resolved 종료 직전과 응답 손실 복구에서도 Request Key·USER snapshot·Request Status·receipt 터미널 일치를 재확인한다. P-B 테스트: AutoResolved → 사용자가 Submitted=True → `claim_request()` 직접 호출 → 효과 0.
- 교차 실행·응답 손실·재시작 후 Submitted 변경은 §9 P-B/P-F 테스트로 고정한다.
- `classification_source=human` 항목은 자동 재분류 대상이 아니다. 자동값은 config/규칙 버전이 바뀌어도 이미 PLANNED/처리된 항목을 되돌리지 않는다(새 관측·새 source version에서만 재분류).

### 3.5 기존 draft 자동 종료 (R3)

업로드 폴더의 현재 8개 요청처럼 **v2 이전에 만들어진 Draft**는 다음을 모두 만족할 때만 자동 종료한다.
1. Request Status = Draft, Submitted=false, Cancelled=false, 로컬 receipt state ∈ {Draft, 없음}(Claimed 아님).
2. USER 필드가 초기 draft 값 그대로(초기 draft USER 값 해시 = 현재 readback 해시; Intake Items 관계 외 전부 blank, 두 체크박스 false).
3. Input Hash/Request Revision Hash가 로컬 기록과 일치, 해당 intake의 observation·source version이 현재와 일치.
4. AUTO plan이 같은 intake에 대해 확정됨.

그러면 Request Status를 신규 SYSTEM_CONTROLLED 옵션 **`Auto Resolved`**(status `complete` 그룹에 추가)로 바꾸고 `Result Reference=classification_record id`를 적는다. `Result Status`(콘텐츠 상태)는 건드리지 않는다. 로컬 receipt는 터미널 `AutoResolved`로 바뀌어 재Claim 불가. 조건 하나라도 미충족(사람이 뭔가 입력, 제출, Claim) → 사람 경로 우선, 자동 종료 없음.
- **종료 쓰기 = AUTO의 첫 provider mutation(R2)**: Auto Resolved 쓰기 직전에 §3.4 mutation preflight 전체(원본 현재 바이트/md5·parent·privacy·workspace/config fingerprint·HUMAN USER snapshot·classification_revision_hash)를 수행한다. 원본이 바뀌었으면 Draft를 닫지 않고 AUTO plan을 `RECONCILE_REQUIRED`로 돌린다. 순서: preflight → durable pending intent(`auto_resolve_intents(record_id, request_key, expected_user_snapshot_hash, state=PENDING)`) 기록 → Notion 쓰기 → readback(Request Key·USER snapshot·Status) → **로컬 receipt `AutoResolved` 전이와 intent `DONE`을 하나의 SQLite 트랜잭션으로 원자 커밋(r9 R2)** → AUTO plan `PLANNED`(DONE 검증 후 멱등 재개 가능). 따라서 재시작 시 상태는 두 가지뿐이다: `intent PENDING + receipt Draft`(응답 손실·롤백 복구, §3.5 (ii)/(iii)/(iv)) 또는 `intent DONE + receipt AutoResolved`(터미널 유지; 이후 사람 변경은 §3.4의 새 generation 경로). `receipt AutoResolved + intent PENDING` 조합은 존재할 수 없으며 발견되면 불변식 위반으로 `RECONCILE_REQUIRED`. 테스트: receipt/intent 갱신 사이 crash 주입(원자성으로 불가 확인), 재시작 전 Submitted 변경, readback 손실 → 터미널 키 재Claim 0건, 중복 HUMAN generation 0건, 잘못된 AUTO 활성화 0건. intent가 `PENDING`인 동안(응답 불명·crash) `_submitted_request_keys()`와 직접 `claim_request()`는 그 request_key에 대해 새 HUMAN/AUTO plan을 만들지 못하며(영속 장벽), 재시작 복구가 readback으로 intent를 `DONE`/`ABORTED`/`RECONCILE`로 닫은 뒤에만 진행한다.
- **복구 결과 계약(r6 R4)**: readback에서 (i) USER snapshot 불변 + Request Status=`Auto Resolved` 정확 일치 → `DONE`, 로컬 receipt `AutoResolved`, AUTO plan `PLANNED` 가능. (ii) USER snapshot 변경(필드 수정·Submitted 여부 무관) → AUTO plan `SUPERSEDED`; Notion Status가 아직 Draft면 intent `ABORTED`로 닫고 HUMAN 경로 그대로. (iii) USER 변경 + Notion Status가 이미 `Auto Resolved`로 반영됨(이때 로컬 receipt는 원자성에 의해 반드시 Draft) → 별도 멱등 write intent(`auto_resolve_rollback`)로 Request Status를 `Draft`(또는 Submitted=true면 `Submitted`)로 되돌리고 `Result Reference`를 비운 뒤 readback 확인 후 intent `ABORTED`; 로컬 receipt는 Draft 유지, 사람이 계속 처리 가능(터미널 receipt를 되살리는 경우는 없음). (iv) readback 불명·응답 손실 재발 → intent `PENDING` 유지(장벽 지속) + `RECONCILE_REQUIRED` 보고, 새 AUTO 활성화·Claim 차단. Notion 갱신은 USER 입력과 원자적이지 않으므로 이 복구 계약이 provider status·로컬 receipt·AUTO plan의 삼자 일치를 보장한다. P-B/P-F 테스트: preflight 직후 USER 필드 수정, Notion 업데이트 응답 손실, 업데이트 성공 직후 Submitted 변경, Submitted=false인 Draft 변경 — 복구 뒤 세 상태가 일치. P-B/P-F 테스트: 분류 후 원본 변경, Auto Resolved 응답 손실, 로컬 receipt 갱신 전 crash, 그 뒤 Submitted 변경 → 잘못된 AUTO 활성화·중복 plan·중복 job 0건.

### 3.6 S4 구간 태그

정규화 후 청크마다 규칙(코드 블록 → `example_code`, `과제|Assignment|제출` 헤더 → `assignment`, 마감/날짜 패턴 → `schedule`) 우선, 애매한 청크만 Jev(태그별 yes/no Choice를 한 요청에 묶음). **S4는 S2와 동일한 outbound 적격성·최소화 guard(§3.2)를 통과해야 한다(R6)**: origin `UNKNOWN`/`USER_NOTE`/`STUDENT_SUBMISSION` 청크, `:user` 구간, 분리 미완료 전사문, PII 검사 미통과 청크는 전송하지 않고 규칙 결과만 남긴다. 전송 불가·provider 실패·오프라인·예산 소진으로 미해결 청크가 있으면 `tags_status`는 `PARTIAL`이며 COMPLETE로 만들지 않는다. 태그 레코드 결속은 §2.2. P-C/P-D 테스트: S4 PII·USER 구간·provider 실패 회귀.

### 3.7 실행 경로·crash 지점 표 (구현 리뷰용)

| 단계 | 담당 함수 | 영속 상태 | crash/응답 손실 시 복구 | 사람 변경 감지 |
|---|---|---|---|---|
| 관측·S1/S2 | `_sync_unlocked` → classifier | `classification_records`, `classification_state` | 재관측 시 record id 멱등; S2 캐시 | 해당 없음 |
| 단계 A(AUTO_PENDING) | 적격성 검사 묶음 | `intake_plans(AUTO_PENDING)` | 재실행 멱등(Draft 불변) | 수정된 Draft → S3 |
| Draft 종료 | §3.5 intent → Notion 쓰기 → readback → **receipt+intent 단일 트랜잭션** | `auto_resolve_intents`, receipt | PENDING: readback 복구 (ii)/(iii)/(iv); DONE: 터미널 | pre_close_snapshot |
| 단계 B(PLANNED)·job | `_enqueue_plan_job` | plan, `Job(plan_revision, plan_authority)` | SUPERSEDED → job VOID | §3.4 (c) |
| 실행 preflight | `_assert_classification_unchanged` | 없음(검사) | 실패 → RECONCILE_REQUIRED, 효과 0 | AUTO snapshot (c), 근거 (d) |
| Session 예약/생성 | `_reserve_session`, `_ensure_session_page` | reservation, Notion page | 자체 효과 멱등 재개(동일 plan_revision) | 쓰기 직전 (c)(d) |
| Material 생성 | `_ensure_material_page` | reservation, Notion page | 동일 | 동일 |
| binding | `_apply_binding` | canonical source binding | 재실행 멱등 | 동일 |
| 이동 | `_move_after_freshness` | move intent, REGISTERED→ORGANIZED | 동일 move intent 재개 | 동일 |
| 정규화·발행 | `_publish_processing_provenance`(opaque 제외) | processing record, derivative | 기존 Partial/Needs Review 복구 | 동일 |
| S4 태그 | S4 runner | `chunk_tag_decisions`, manifest | PARTIAL 재개, STALE 재판정 | 해당 없음 |
| 터미널 스캔 | `_run_once_attested` 스캔 | `terminal_change_ledger`, cursor | 동일 (receipt, hash) → 같은 generation | terminal_snapshot |
| 새 HUMAN generation | `_create_input_request_with_context(generation)` | `intake_request_generations`, `handover_record` | 생성 전 영속 generation으로 중복 0 | 이후 HUMAN 경로 |

## 4. Jev 어댑터 경계 (R7, R8)

- 위치: `src/uls/adapters/llm/typesafe_choice.py` (신규). 기존 `jev_recommend.py` helper는 재사용하지 않는다.
- 인증: `TYPESAFE_API_KEY`를 `ALLOWED_SOURCES`에 `{"keyring", "environment"}`로, `KEYRING_BINDINGS`에 `("Syllva LLM", "typesafe_api_key")`로 추가. config `credentials.TYPESAFE_API_KEY.source`는 **명시 필수**이며 미명시 시 분류 기능 `DISABLED`(기본 environment fallback 금지). composition root가 CredentialResolver로 1회 해석한 불변 snapshot을 어댑터에 주입하고, 어댑터는 환경·파일·Keychain을 직접 읽지 않는다. 다른 Jev 도구의 Keychain 항목은 재사용하지 않는다.
- 전송: 고정 endpoint `https://api.typesafe.ai/v1/systemone`, 고정 모델 `jev-1.13.0`, TLS 검증, proxy/redirect 차단, 재시도 0, socket timeout 10s, **전체 호출 경과 상한 15s**(초과 시 중단·fallback), 직렬화 후 요청 ≤ 16,384B, 응답 수신 중 누적 ≤ 65,536B(초과 시 중단), HTTP 200 + `application/json`만.
- 응답 검증(전부 실패 시 fallback): 중복 JSON 키 거부, NaN/Infinity 거부, **`answers`의 question ID 집합이 요청과 정확히 일치(누락·미요청 질문 거부; S4 다중 질문 포함, O2)**, `answers[question].type == "choice"`, 옵션 집합이 요청과 정확히 일치(추가·누락 거부), 확률·confidence는 bool이 아닌 유한 실수 0–1, 합 1±1e-6, `choice`가 최고 확률이며 유일, `model` 문자열 정확 일치, `usage` 구조 유효.
- 캐시(M4): **S2 전용**. 키 = sha256(`snapshot_sha256(모델에 보낸 정확한 발췌 바이트), payload_fingerprint(최종 최소화 payload sha256), byte_sha256, source_version, title, course_key, module_week, item_type, origin, option_set_version, prompt_version, model`). metadata surrogate `source_hash`는 키에 쓰지 않는다. 같은 `snapshot_sha256`을 `classification_record`에도 고정한다. S4는 별도 namespace: 키 = sha256(`chunk_id, source_fingerprint, normalization_version, tag_vocab_version, question_set_version, tag_rule_version, s4_payload_policy_version, prompt_version, model`). 값은 검증된 decision만(원문·비밀 저장 안 함). P-C 테스트: surrogate 동일·내용 상이 → 미적중; 같은 문서의 청크 2개 → 서로 다른 결과 유지.
- 예산: 틱당 외부 호출 총예산(config `intake.classification.max_calls_per_tick`, 기본 50)을 S2·S4가 공유, 항목당 S2 HTTP 1회, 청크당 S4 HTTP 1회(S4의 복수 태그 질문은 **한 요청에 질문 여러 개**로 묶어 1회로 계산; 묶을 수 없으면 1개 질문으로 제한).
- **예산 상태 전이(M8, R7)**: 소진은 실패가 아니다. S2 대기 항목은 로컬 영속 상태 `IntakeItem.classification_state=DEFERRED`(Notion File Intake Status는 변경하지 않고 `Error`에 고정 사유 `CLASSIFIER_BUDGET_EXHAUSTED`만 표시)로 두고 다음 틱에 재개한다. **`_sync_unlocked()` 경계**: 발견 항목의 HUMAN Input Request 생성은 `classification_state ∈ {NONE, DEFERRED}`인 동안 억제되고, 분류 파이프라인이 실제 S3 결정을 내려 `classification_state=HUMAN`으로 전이한 뒤에만 생성한다(순서: 관측 → 분류 → S3 결정 → 요청 생성). 다음 틱은 DEFERRED 항목을 새 관측보다 먼저 재개한다. 테스트: 예산 경계 직전·직후·재시작에서 DEFERRED 항목의 Input Request 생성 0건. S4는 `tags_status=PARTIAL`로 두고 다음 틱에 이어서 태그한다. provider 실패·검증 실패·저확신·부적격만 S3로 간다. 모두 readiness 카운터로 노출. DEFERRED가 다음 틱에 해소되면 File Intake `Error`의 `CLASSIFIER_BUDGET_EXHAUSTED`를 같은 projection에서 제거한다(O3; SYSTEM_CONTROLLED 필드).
- 오류는 고정 코드(`CLASSIFIER_DISABLED`, `CLASSIFIER_TIMEOUT`, `CLASSIFIER_INVALID_RESPONSE`, `CLASSIFIER_BUDGET_EXHAUSTED`, `CLASSIFIER_TRANSPORT`)로 축약. 값·원문·provider 메시지는 로그·Notion·응답 어디에도 쓰지 않는다.
- readiness(O2): `classification: {status, reason, rule_table_version, tag_rule_version, calls_remaining, deferred_items, human_fallback_items, partial_tag_documents, stale_tag_documents, invalid_response_count, transport_failure_count}`. `status` 우선순위: credential 미해석/미명시 → `DISABLED`; 직전 틱에 transport/invalid 실패가 있거나 예산 소진 → `DEGRADED`; 그 외 `READY`. 개인정보·provider 메시지는 포함하지 않는다. P-C 수용 조건에 이 필드 집합을 명시한다.

## 5. 데이터 모델 / Notion 변경 (R9)

로컬 state
- `IntakeItem`: `origin`, `classified_kind`, `classification_source`(`rule:<id>` | `jev:<ver>` | `human`), `classification_record_id`, `inferred_course_key`, `inferred_week`, `inferred_date`, `calendar_match`(`MATCHED|NO_CALENDAR|MISMATCH|AMBIGUOUS`).
- `IntakeItem.classification_state`(`NONE|DEFERRED|CLASSIFIED|HUMAN`) (R7), `intake_suggestions`(프로필 확인 전 로컬 제안 보관, R4).
- `classification_records`(불변, PK record_id), `intake_plans.plan_authority` + `classification_revision_hash`, `chunk_tags`(§2.2), `canvas_observations`(PK origin+course_id+resource_kind+resource_id+observation_revision), `canvas_classifications`(PK observation fingerprint), `canvas_drive_bindings`(PK drive_file_id; UNIQUE canvas resource+observation_revision+attachment_id, r10 R3), `recording_calendar`(이력, PK canvas_course_id+resource_id+observation_revision) + `recording_calendar_current`(활성 projection, PK canvas_course_id+resource_id; 보조 인덱스 course_key+date; 같은 날짜 활성 관측 수 ≥2 → `AMBIGUOUS`; 완전 수집 후에만 재계산, M3/R1). 모두 P-A 마이그레이션에 포함(재실행 멱등).

Notion (사람이 옵션을 추가하고 readback이 확인된 뒤에만 사용)
- 프로필: 기존 `legacy5`, `c5-range-v1` shape 불변. 신규 `legacy5-cls` = legacy5 + 분류 속성, `c5-range-v2` = c5-range-v1 + 분류 속성. 활성 프로필은 config로 선택하며 readback 정확 검증(속성 집합·옵션·status 그룹)을 그대로 적용한다.
- File Intake 추가: `AI Kind`(select §2.1), `Origin`(select §2.4), `Classification Source`(rich_text), `Classification Record`(rich_text). `Observed Kind` 옵션은 불변.
- Input Request 추가: §3.3 제안 필드 6개. Request Status 옵션에 `Auto Resolved` 추가(complete 그룹). **HUMAN Kind 확장(M4, 총괄 결정·사용자 확인 필요)**: 신규 프로필의 `FILE_KINDS_V2`를 정확히 다음 집합으로 선언한다: `{TRANSCRIPT, LECTURE_SLIDES, LAB_MATERIAL, PROVIDED_CODE, ASSIGNMENT_BRIEF, ASSIGNMENT_RESOURCE, SETUP_GUIDE, COURSE_INFO, SUPPLEMENT, EXAM, MATERIAL_PDF}`(M5). `MATERIAL_PDF`는 AUTO가 생성하지 않는 **HUMAN 호환 입력**이며, `validate_request_input()`/`route_intake()`/`_process_item_unlocked()`는 이를 사용자가 고른 Material Role과 함께 현행 PDF Material 경로로 처리하고 AI Kind를 소급 확정하지 않는다(AI Kind는 비움). 프로필 전환 readback 회귀에 기존 Draft/Claimed(MATERIAL_PDF) 요청을 포함한다. 이에 맞춰 `FILE_KINDS`(프로필별), `validate_request_input()`, `route_intake()`, HUMAN material dispatch(§6.1 표의 Kind별 처리기)를 함께 개정하고, `MATERIAL_PDF`는 신규 프로필에서 `LECTURE_SLIDES`/`SUPPLEMENT`의 호환 입력으로만 남긴다(legacy 프로필 불변). 확장이 readback으로 확인되기 전에는 신규 Kind가 필요한 S3 항목을 `MATERIAL_PDF`로 강제하지 않고 `Needs Input`에 둔다. **(R4)** 신규 프로필 readback이 확인되기 전에는 제안값(§3.3)을 Notion에 쓰지 않고 로컬 state(`intake_suggestions`)에만 보관한다; 기존 프로필에 없는 속성은 절대 쓰지 않는다.
- **HUMAN Material Role 매트릭스(R4)**: 신규 프로필에서 Input Request `Material Role` 옵션을 Materials.Type 전체(`Lecture Slides, Professor Notes, Syllabus, Textbook, Reference, Supplementary`)로 확장한다. Kind별 입력 검증: TRANSCRIPT → Role 금지(현행); 그 외 모든 Material Kind → Role 필수이며 허용 집합은 §5 표의 초기값을 기본 제안으로 하되 사용자가 어떤 Type 값이든 고를 수 있다. 우선순위: **USER가 확정한 Role이 Materials.Type 생성값**이고, §5 표는 AUTO 경로와 HUMAN 제안값에만 쓰인다.
- Materials 추가: `AI Kind`(select), `Week`(number, SYSTEM_INITIAL_USER_PRESERVE). `Type`(USER)은 불변이며 권위 매핑(`material_type_source_class`)의 입력으로 계속 쓰인다. 새 Material 생성 시 `Type` 초기값은 현행과 같은 "초기값" 계약으로 다음 표에서 정한다(이후 사람이 바꿀 수 있음).

| AI Kind | Type 초기값 | 권위(기존 매핑) |
|---|---|---|
| LECTURE_SLIDES | Lecture Slides | professor_material |
| LAB_MATERIAL, ASSIGNMENT_BRIEF | Professor Notes | professor_material |
| COURSE_INFO | Syllabus | professor_material |
| SUPPLEMENT | Supplementary | supplemental_reference |
| PROVIDED_CODE, ASSIGNMENT_RESOURCE, SETUP_GUIDE, EXAM | Reference | supplemental_reference |

마이그레이션(P-A, 멱등)
- 기존 Material: `Type`, `Material Role` 사람 선택, source hash/version, canonical ID 전부 보존. `AI Kind`는 Lecture Slides→LECTURE_SLIDES만 백필하고 **Textbook을 포함한 그 외 Type은 NULL로 유지**(O1; 검증된 Kind가 생길 때만 확정), 모두 `classification_source=human`으로 표시해 자동 재분류에서 제외. AI Kind NULL 행은 §7(R4)대로 COMPLETE 전 기본 검색 차단.
- 기존 TRANSCRIPT 불변. 재실행 시 변경 0건이어야 한다.

## 6. 종류별 처리 경로 (R10, R11)

### 6.1 Drive 파일 Kind

| Kind | 허용 MIME/확장자 | 저장 | 정규화·derivative | 콘텐츠 상태 | 검색 노출 |
|---|---|---|---|---|---|
| TRANSCRIPT | text/markdown, text/plain | 현행 recordings 폴더 이동 + Session | 현행 전사 정규화 | 현행 | 예 |
| LECTURE_SLIDES, SUPPLEMENT, COURSE_INFO, SETUP_GUIDE, EXAM, LAB_MATERIAL, ASSIGNMENT_BRIEF | application/pdf만(v2; pptx/docx는 S3) | 현행 materials 폴더(**신규 하위 폴더 없음**, Kind는 Notion/state로 구분) | 현행 PDF 추출 + 청크 + S4 | 생성 전 probe 실패 → S3·plan 없음; 생성 후 추출 실패 → Material 보존 + `Needs Review`/`Partial`(M6) | 예(§7 필터) |
| ASSIGNMENT_RESOURCE | csv/json/xlsx/pdf | materials 폴더, Material 등록 | 표 형식은 정규화 없이 `Text Source=Unavailable` + `Text Status=Needs Review`(기존 옵션 조합; Text Status 옵션 변경 없음), pdf는 위와 동일 | 위와 동일 | pdf만 |
| PROVIDED_CODE | 코드 확장자 | materials 폴더, Material 등록(`Text Source=Unavailable`, opaque 자료 공통) | 본문은 보관만; 코드 locator 계약(line 단위)이 명세 개정되기 전까지 청크·태그·검색 **비노출** | `Text Status=Needs Review`(기존 옵션; 비노출 사유는 로컬 `exposure_block_reason`) | 아니오(v2 범위 밖) |
| UNSUPPORTED | 금지 목록 | 업로드 폴더 그대로 | 없음 | File Intake 사유 코드 | 아니오 |

실패 분리: 추출 실패 Material은 재시도 가능 상태로 유지하고 이미 만들어진 Material 행을 삭제하지 않는다(현행 Partial/Needs Review 계약).

**적격성 매트릭스(M10, R8)**: AUTO plan 생성 전에 `(Kind, MIME, 확장자, 파일 서명 magic bytes)`로 **처리 방식**을 결정한다. 방식은 네 가지뿐이다.

| 처리 방식 | 조합 | 효과 |
|---|---|---|
| `NORMALIZE` | TRANSCRIPT×(text/markdown, text/plain); PDF Material Kind×(application/pdf, `%PDF` 서명) | 현행 정규화(`extract_pdf`/전사)·청크·S4·검색 발행 |
| `REGISTER_OPAQUE_NO_RETRIEVAL` | PROVIDED_CODE×코드 확장자(텍스트 서명); ASSIGNMENT_RESOURCE×(csv/json/xlsx) | Drive 보관 + Material 등록(`Text Source=Unavailable`, `Text Status=Needs Review`), 정규화·locator·검색 발행 **없음**(extractor 불필요). **필수 실행 순서(r11 R1)**: ① Material 예약 + private 폴더 검증 → ② Material 생성(생성 속성에 `Text Source=Unavailable`, `Text Status=Needs Review`를 즉시 설정; 현행 `_ensure_material_page()`의 `Pending` 기본값을 opaque 경로에서 대체, O1) + 정확 readback(두 속성 포함) → ③ `_apply_binding()`(source identity/version 영속화, canonical binding) → ④ `REGISTERED` → `_move_after_freshness()`(파일 ID 보존 이동) → `ORGANIZED` → ⑤ 검색 provenance(processing record)·derivative **비발행**, `Needs Review` 유지. 기존 `_process_material()`에서 `extract_pdf`·derivative·pointer 단계만 건너뛰고 binding→이동 선후관계는 그대로 둔다. 테스트: `.c`·`.csv` 각각 Material 1·canonical binding 1·ID 보존 이동 1·derivative 0·검색 provenance 0; Material 생성 응답 손실·binding 직후 crash·이동 응답 손실·재시작 → Material/폴더/이동 중복 0 |
| `METADATA_ONLY` | Canvas 관측 항목(§6.3), RECORDING | 로컬 레코드만 |
| `S3` | 그 외 전부: 조합 불일치(예: `실습` 제목 `.csv`→LAB_MATERIAL), 위장 MIME(확장자와 서명 불일치), PPTX/DOCX(extractor 미명세), 추출 실패 | plan 없음, `Suggestion Note=FORMAT_KIND_MISMATCH` 등 고정 코드 |

이 매트릭스가 **유일한 권위**이며 위 Kind 표는 요약이다(M6). **AUTO·HUMAN 공통 사전조건(r10 R4)**: 처리 방식 선택은 AUTO plan 생성 전뿐 아니라 HUMAN FILE_DETAILS claim의 plan 생성 전에도 같은 함수로 수행한다(`_process_item_unlocked()`의 Kind dispatch 앞). HUMAN이 고른 Kind는 보존하고 재해석하지 않되, (Kind, MIME, 확장자, 서명) 조합이 매트릭스에 없으면 외부 쓰기 전에 `NEEDS_INPUT`(요청 `Error=FORMAT_KIND_MISMATCH`) 또는 명시적 reconcile로 중단한다. `MATERIAL_PDF`×PDF는 기존 HUMAN 경로 그대로. §5의 "HUMAN Role이 Type 초기값보다 우선" 규칙은 유지. 테스트: HUMAN PROVIDED_CODE×PDF, ASSIGNMENT_RESOURCE×PPTX → plan/Material/derivative/Ready 0건; PROVIDED_CODE×.c, ASSIGNMENT_RESOURCE×.csv → opaque 등록 1건·검색 발행 0건; MATERIAL_PDF×PDF → 기존 경로. **추출 실패 시점 구분**: AUTO plan 생성 전에 `NORMALIZE` 후보는 다운로드 바이트로 서명·파서 probe(`extract_pdf` dry-run: 페이지 수·텍스트 유무)를 수행하고 실패하면 S3·plan 없음·Material 없음. 생성 이후(정규화 단계)에 발생한 실패는 이미 만든 Material을 보존하고 `Needs Review`/`Partial` 복구 경로를 쓴다. `_process_material()`은 처리 방식으로 분기하고 `extract_pdf()`는 `NORMALIZE`×PDF에서만 호출한다. P-B/P-F 테스트: 정상 `.c`, `.csv`, 위장 MIME, 손상 PDF(생성 전/후), DOCX/PPTX 각각에 대해 plan 수·Material 수·Text Status·검색 발행 수를 검증. 확장자만 맞고 서명이 다른 파일, 내부 형식 불일치, 추출 실패, locator 부재는 Ready·검색 발행이 일어나지 않는다(P-B/P-F 테스트).

### 6.2 ANNOUNCEMENT
- 수집·Notion 공지 행 create/update는 **기존 KNU LMS 시간별 수집기 소유**. v2는 그 canonical 행을 `LMS 키`(`<origin>/courses/<id>/announcements/<id>`)로 읽어 로컬 청크·태그만 만든다. v2의 공지 행 쓰기는 **0건**(테스트로 고정).
- 로컬 청크 키 = `(LMS 키, 본문 content hash)`. 행 수정 시 재청크, 행 삭제·누락 시 청크 `STALE`, 수집기가 본문을 불완전하게 기록한 행(`수집 범위` ≠ 전체)은 `Partial`로 두고 Ready 취급하지 않는다.
- **Locator 계약(M8)**: LMS 키·content hash는 정규 Locator(Page/Time)가 아니므로 v2에서 ANNOUNCEMENT 청크는 **로컬 태그·메타데이터까지만** 처리하고 context/capability/`get_source_chunk` 어디에도 노출하지 않는다. 공지 본문 검색은 별도 정규 locator·source binding·capability 명세 개정 뒤의 후속 범위다. LMS 키를 page/time locator로 흉내 내지 않는다. P-E 테스트: 공지 행 쓰기 0건 + locator 부재 시 노출 0건.
- 공지 첨부 파일은 기존 설계(`knu-lms-api-semester.md`: Canvas 다운로드 → Drive 개인 보관 → intake 발견)로 Drive 항목이 되어 §6.1을 탄다. 이때 origin=PROFESSOR_SOURCE, 공지 LMS 키와 첨부 `attachment_id`를 provenance로 연결(첨부마다 binding 1행). 테스트: 공지 1건에 첨부 A·B → Drive ID 2개·binding 2행; 중복 재관측 → 추가 binding·Material 0건; revision 변경 → 기존 binding 재사용 차단.
- 한계: LMS 수집기 소스(`scripts/knu_lms_sync.py`)는 이 패킷에 없으므로 이중 쓰기 금지는 P-E 리뷰에서 그 소스를 첨부해 검증한다.

### 6.3 Canvas 메타데이터 관측 (RECORDING 포함)
- Drive intake와 분리된 `canvas_observations` 테이블: `(origin, course_id, resource_kind, resource_id, title, module_name, module_week, item_type, updated_at, observed_at)`. Drive 파일 ID·source hash를 갖지 않는다.
- 읽기 전용 GET, 기존 probe의 origin 고정·허용 리소스(`modules`, `assignments`, `announcements`)·페이지 상한·토큰 getpass 규칙을 제품 어댑터로 옮긴다(토큰은 Settings Canvas 연결의 Keychain 항목; 별도 결정 전에는 기존 LMS pilot 토큰 경로만).
- **메타데이터 전용 경로(M1)**: `canvas_observations` 항목의 분류 결과(Kind·주차·origin 후보·decision)는 `canvas_classifications`에만 저장한다. 이 단계에서는 AUTO plan/job, File Intake 행, Material, S3 Input Request를 **만들지 않는다**(P0 UNSUPPORTED도 관측 레코드의 사유 코드로만 기록). 실제 파일이 개인 Drive로 확보되어(§6.2 첨부 경로 또는 모듈 파일 다운로드 경로, P-E) Drive 관측으로 source identity·MIME·hash 검증을 통과했을 때만 Drive intake가 되고, 그 때 `canvas_classifications`의 결과 중 **검증된 course/origin/provenance와 활성 observation의 `module_week`**(r14 O1; 같은 파일에 충돌하는 주차 근거가 있으면 자동 선택 없이 S3)만 S0 출처 메타로 재사용하고 **Kind는 실제 Drive 파일 기준으로 새로 판정**한다(O1: Assignment 첨부 `.csv`는 ASSIGNMENT_RESOURCE 후보, 공지 첨부 강의 PDF는 ANNOUNCEMENT가 아니라 자체 Kind). 검증된 Canvas Assignment 첨부 provenance가 있는 PDF는 제목의 `실습`/`실습과제` 신호를 P3 일반 실습 규칙으로 쓰지 않고(ASSIGNMENT_BRIEF vs LAB_MATERIAL 모호), 다른 명확한 신호가 없으면 S2/S3로 보낸다(r13 O2; 첨부 CSV의 ASSIGNMENT_RESOURCE 처리는 유지). P-E 테스트: 공지 본문 ANNOUNCEMENT + 첨부 PDF 자체 Kind; Assignment 본문 ASSIGNMENT_BRIEF + 첨부 CSV ASSIGNMENT_RESOURCE; `Chapter 03-Address.pdf` + Canvas 3주차 → Material Week=3. **출처 결속(R5)**: 재사용과 `PROFESSOR_SOURCE` 부여는 신뢰된 Canvas 다운로드 경로가 기록한 `canvas_drive_bindings(canvas_course_id, resource_kind, resource_id, observation_revision, attachment_id, attachment_filename, attachment_size, drive_file_id, byte_sha256, bound_at)` 행이 **정확히** 일치할 때만 허용한다(r10 R3: 한 공지/과제에 첨부가 여러 개일 수 있으므로 Canvas 첨부 자체의 안정 식별자 `attachment_id`를 포함; UNIQUE는 `(resource, observation_revision, attachment_id)`와 `drive_file_id` 각각). 제목 유사·이름 일치만으로는 결속하지 않으며 그런 파일은 `UNKNOWN` → S3다. `canvas_observations`/`canvas_classifications`/`canvas_drive_bindings`/`recording_calendar`의 고유 키·마이그레이션은 §5/P-A에 포함한다. Canvas 메타 기반 S2 캐시 키는 Drive hash 대신 observation fingerprint `sha256(origin, course_id, resource_kind, resource_id, observation_version, title, module_week, item_type, option_set_version, prompt_version, model)`를 쓴다.
- `RECORDING`은 `recording_calendar`에만 기록된다. Session 생성은 §2.3의 TRANSCRIPT 바인딩 규칙에서만 일어난다.
- 본문을 가져올 수 없는 ExternalTool은 제목으로 Kind만 기록하고 Material을 만들지 않으며 Ready로 간주하지 않는다.

## 7. Retrieval 경계 (R12)

- 순서: 기존 source authorization(capability allowlist, locator 검증) → 범위 안에서 태그 필터. 태그는 권한을 넓히지 못한다.
- 기본 정책: `assignment` 태그 청크는 CONCEPT/SESSION 기본 결과에서 제외하고, ACTIVITY intent 또는 명시적 `include_assignment=true`(허가 범위 내) 요청에서만 반환한다. 반환 시 `locator`, `completeness(Partial 여부)`, `source_authority`, `conflict_state`를 함께 전달하고 절단된 과제 안내는 전체 요구사항으로 표시하지 않는다.
- **태그 불완전 시 fail-closed(M11, r13 R1)**: 태그 부재는 "과제 아님"의 증거가 아니다. 아래 규칙은 **과제 제외 intent 공통**(`assignment_excluding_intent = CONCEPT | SESSION | EXAM`, `include_assignment != true`)이며 context·capability·`get_source_chunk`에서 동일하게 적용된다. (1) Kind가 `ASSIGNMENT_BRIEF`·`ASSIGNMENT_RESOURCE`인 문서는 태그 결과(전부 NO·COMPLETE라도)와 무관하게 기본 결과에서 **문서 전체** 차단(`usage_policy`는 활용 제한이지 제외 조건의 대체가 아님). (2) 혼합 가능 Kind(**`TRANSCRIPT`**, `LAB_MATERIAL`, `LECTURE_SLIDES`, `SUPPLEMENT`, `COURSE_INFO`, `SETUP_GUIDE`, `EXAM`, `ANNOUNCEMENT` — 즉 기본 검색 가능한 모든 Kind)는 `tags_status ∈ {COMPLETE}`일 때만 청크 단위 필터를 적용하고, `PARTIAL`/`STALE`/미생성이면 문서 전체를 기본 결과에서 제외한다(ACTIVITY/opt-in 경로는 completeness 플래그와 함께 반환). (R9) 이 필터는 context 반환뿐 아니라 **context capability 발급(allowlist 구성)과 `get_source_chunk` 재조회**에도 같은 규칙으로 적용되어 우회되지 않는다. **Locator 범위 단위 판정(r11 R2, r12 R1)**: 청크는 Page/Time Locator보다 좁을 수 있으므로(같은 `M01:p2` 안에 assignment=NO 청크와 YES 청크 공존), **과제 제외 intent 전부(CONCEPT·SESSION·EXAM; `include_assignment=true`가 아닌 한)**에서 capability를 발급하거나 `get_source_chunk`를 수행하기 전에 요청 Locator 범위와 **겹치는 현재 fingerprint의 모든 청크**에 태그 정책을 적용한다. 하나라도 `assignment=YES|UNRESOLVED|STALE`이거나 문서가 검색 부적격이면 그 Locator 범위(페이지·시간 범위 전체)의 기본 발급·재조회를 거부하고, 허용 청크만 정확히 분리해 돌려주는 계약은 v2에서 도입하지 않는다(새 Locator 문법 없음). 이미 발급된 capability도 태그가 STALE이 되면 재조회에서 거부된다. ACTIVITY·명시 opt-in은 기존 `NO_ANSWER_GENERATION`/`assignment_decision`/completeness 규칙을 유지. P-D 테스트: 같은 페이지에 NO·YES 청크 공존 PDF, 겹치는 페이지 범위, 시간 범위가 겹치는 전사문을 CONCEPT·SESSION·**EXAM** 기본 질의로 요청 → 기본 context·capability·get_source_chunk에서 금지 청크 노출 0; STALE 이후 기존 capability 재사용 실패. **(R4)** `AI Kind=NULL`인 기존 Material, 알 수 없는 Kind, legacy HUMAN `MATERIAL_PDF` 호환 행은 모두 "태그 미완료 혼합 문서"로 취급해 현재 fingerprint의 `tags_status=COMPLETE`가 확보되기 전에는 과제 제외 intent 전부(CONCEPT·SESSION·EXAM)의 기본 결과·capability·`get_source_chunk`에서 차단한다(r13 R1). 기존 TRANSCRIPT와 기본 검색 가능한 기존 Material 전부가 S4 백필 대상이며, 백필을 P-D의 첫 작업으로 둔다(ACTIVITY/명시 opt-in의 completeness 공개 정책은 유지). (3) ACTIVITY의 공식 제약(마감·제출 방식 등)은 AI 태그가 아니라 확인된 공식 source locator와 typed constraint metadata에서만 생성한다. Behavior Contract 개정은 보조이며 이 경계를 대신하지 않는다.
- Behavior Contract(r8 R6, r9 R3): **intent와 무관한 전역 규칙** "과제 지시 구간(`assignment` 태그, ASSIGNMENT_* Kind, **또는 assignment 결정이 미해결/STALE인 청크**)은 출처 확인·안내·요약에만 사용하고 답안·코드·제출물 생성에 사용하지 않는다"를 추가한다. 반환 envelope는 `EvidenceItem`의 호환 확장 필드 `usage_policy ∈ {NORMAL, NO_ANSWER_GENERATION}`와 `assignment_decision ∈ {YES, NO, UNRESOLVED, STALE}`를 가지며, ACTIVITY·명시 opt-in 경로에서 반환되는 미해결/STALE 청크에는 `NO_ANSWER_GENERATION` + `UNRESOLVED|STALE`을 붙인다(또는 반환하지 않음). 이 두 필드는 context 반환·capability 발급·`get_source_chunk` 재조회에 동일하게 전달된다. COMPLETE이고 `NO`인 청크만 `NORMAL` — 단 문서 Kind가 ASSIGNMENT_BRIEF/ASSIGNMENT_RESOURCE면 청크 결정과 무관하게 항상 `NO_ANSWER_GENERATION`(O1; envelope 테스트로 우선순위 고정). **범위 단위 정책 집계(r12 R2)**: `EvidenceItem`의 `usage_policy`/`assignment_decision`은 선택된 청크가 아니라 **반환 Locator 범위와 겹치는 현재 fingerprint의 모든 청크**로 집계한다. 전부 `NO`(COMPLETE)이고 문서 Kind가 허용이면 `NORMAL`; 하나라도 `YES|UNRESOLVED|STALE`이면 범위 전체 `NO_ANSWER_GENERATION`; `assignment_decision`은 안전 우선순위 `STALE > UNRESOLVED > YES > NO`로 집계하되 불완전성·미해결 정보를 함께 유지한다. 겹치는 청크 집합을 완전히 확인할 수 없으면 `NORMAL`을 발급하지 않는다. capability 재조회(`get_source_chunk`) 시 현재 fingerprint로 정책을 재계산한다. ACTIVITY의 출처 확인·안내·요약은 그대로 허용된다. P-D 테스트: 같은 `M01:p2`에 NO/YES 청크, ACTIVITY와 명시 opt-in으로 페이지 전체 반환 → 모든 경로 `NO_ANSWER_GENERATION` 유지, 일부 청크 STALE 후에도 NORMAL 강등 없음. 테스트: S4 저확신/오프라인으로 PARTIAL인 LAB_MATERIAL·TRANSCRIPT에 ACTIVITY와 CONCEPT/SESSION+opt-in → 출처·불완전성 안내 가능, 미해결 청크 기반 답안 생성 차단, COMPLETE 청크의 개념 설명 유지. 검색 authorization과 답안 생성 정책은 별개의 게이트다. projection lint로 drift를 막는다. 테스트: CONCEPT/SESSION + `include_assignment=true`에서 출처·완전성은 제공되고 답안 생성 경로는 차단.
- intent별 `assignment` 구간 정책(O1):

| intent | 기본 결과 | 명시 요청(`include_assignment=true`, 허가 범위 내) |
|---|---|---|
| CONCEPT, SESSION | 제외 | 반환 + completeness·authority 플래그 |
| ACTIVITY | 반환(공식 locator·완전성·충돌 상태 동반; 답안 생성 금지) | 동일 |
| EXAM | 제외(§7 Locator 범위 단위 판정 동일 적용; 시험 범위 증거는 human-confirmed scope만) | 반환, 단 `exam_hint` 태그는 범위 확정 증거가 아님을 표시 |
| VERIFY | 과제 지시의 **존재·출처 확인**에만 반환(답안 생성 경로 아님) | 동일 |
| USER_NOTE | 제외(USER 구간만) | 해당 없음 |
- PROVIDED_CODE는 v2에서 검색 비노출(§6.1). ANNOUNCEMENT 로컬 청크도 v2에서 검색 비노출(§6.2, M8).
- **활성화 순서(M7)**: config `retrieval.v2_exposure_gate`(기본 off). P-D의 fail-closed 필터(context·capability·get_source_chunk)가 구현·테스트되기 전에는 v2 경로(AUTO plan, 신규 Kind, S4)로 들어온 **새 콘텐츠는 기본 검색에 노출되지 않는다**. P-B/P-C가 먼저 배포돼도 이 게이트가 노출을 막는다.

## 8. 사람 게이트와 불변식

- 사람에게 가는 조건: S1 다중/무매치 + S2 부적격·저확신·provider/검증 실패, 과목 미정, 주차/날짜 불일치, 달력 충돌·이상치, origin UNKNOWN의 Material, 형식-Kind 부적격, 중복 콘텐츠, 추출 실패. 예산 소진은 DEFERRED(§4).
- 사람 입력은 항상 자동값보다 우선. 자동 경로는 USER 필드·receipt를 쓰지 않고, `classification_source=human` 항목을 재분류하지 않는다.
- SOURCE/AI/USER: 분류·태그·제안은 AI(SYSTEM_DERIVED), 원문·권위는 origin과 기존 계약, 사용자 메모는 USER. `Verified`/`Scope Confirmed` 불변. MCP read-only, 분류기는 worker 틱 안에서만 호출.

## 9. 단계와 수용 조건 (R13)

| 단계 | 내용 | 수용 조건 |
|---|---|---|
| P-A 체계·데이터 모델 | 규칙 테이블/태그 vocab 버전, state 테이블, Notion `legacy5-cls`/`c5-range-v2` 프로필(HUMAN Kind 확장·`Auto Resolved` 포함), 마이그레이션, alias/canvas 매핑 config, §2.5 초기값 예외 | legacy5·c5-range-v1 shape 불변 테스트; v2 프로필 정확 readback(실제 shape 회귀 포함; Text Status 옵션 불변); HUMAN Kind 확장 입력 검증·dispatch; 마이그레이션 재실행 변경 0건; human 라벨 보존 |
| P-B 결정적 분류기 + AUTO 권한 + 달력 매칭 + 제안 필드 | S0/S1, Canvas 메타 관측(읽기, 메타 전용 경로), AUTO plan(AUTO_PENDING→PLANNED 선행검사, preflight), 기존 draft 자동 종료, S3 제안 필드, 적격성 매트릭스 | fixture 80항목 전부 구조화 기대값과 기계 대조(Canvas 항목은 `CANVAS_METADATA_ONLY`, material_created=false); S1 다중·무매치·alias 중복·과목 불명·달력 부재/충돌/이상치 케이스; **전사문 8개는 S1 Kind 확정 후 달력 불일치로 `NEEDS_INPUT`**(과목·주차·Kind 제안 동반, Session 생성 0); UNKNOWN origin Material 비발행; 형식-Kind 부적격 S3; P-D 이전 음성 테스트(O2, r14 O2): 새 Material이 `Text Status=Ready`여도 `v2_exposure_gate=off`에서는 **intent(CONCEPT·SESSION·EXAM·ACTIVITY·VERIFY)와 `include_assignment` 값에 관계없이** context·신규 capability·기존 capability 재조회에서 노출 0건; AUTO plan 복구(쓰기 손실·재시작) 멱등; 수정된 Draft/Submitted/Claimed/pending create 존재 시 AUTO plan SUPERSEDED·사람 우선; AutoResolved 터미널 재Claim 불가; preflight 실패 시 외부 쓰기 0 |
| P-C Jev 어댑터 | §4 전부, fake provider 계약 테스트, 실제 키로 합성 요청 1회 live 검증 | 명시 source 해석(기본 배포는 Keychain `Syllva LLM/typesafe_api_key`; `environment`는 config에 명시했을 때만; 미명시 → DISABLED, 묵시 환경 fallback 없음), 키 비노출(로그/Notion/readiness/예외 문자열), 전송 적격(origin), strict JSON 15케이스, 총 timeout, 응답 크기 중단, proxy/redirect 차단, 총 호출 예산·DEFERRED/PARTIAL |
| P-D 구간 태그 + retrieval 필터 | 기존 전사문·기존 Material(AI Kind NULL 포함) S4 백필, chunk_tags, 태그 결속·무효화, 검색 필터(fail-closed; context·capability·get_source_chunk 동일), Behavior Contract 개정 | fingerprint 변경 시 태그 무효; authorization 뒤 필터; ASSIGNMENT_* 문서 전체 차단; 모든 검색 가능 Kind(TRANSCRIPT 포함)는 tags_status=COMPLETE에서만 청크 필터, PARTIAL/STALE/미생성은 문서 전체 비노출; Partial 전사문의 assignment 청크 기본 비노출·ACTIVITY completeness 공개; Type=Professor Notes·AI Kind=NULL·manifest 미생성 기존 Material 차단(재발급·기존 capability 재조회 포함); EXAM 기본 질의에서 ASSIGNMENT_BRIEF(전 청크 NO·COMPLETE)와 legacy MATERIAL_PDF(AI Kind NULL·태그 미생성) 문서·capability·재조회 노출 0; 원문 불변·태그 규칙만 변경 → STALE·차단; prompt만 변경·model만 변경 → STALE; 전체 negative 후 재시작 → coverage로 COMPLETE 재검증; 중복 청크 응답; 청크 일부 질문 미생성 → PARTIAL; 저확신 negative → 미해결; capability/get_source_chunk 우회 불가; ACTIVITY 제약은 공식 locator에서만; S4 PII/USER/provider 실패 회귀; projection lint 통과 |
| P-E 공지·첨부 | LMS canonical 행 읽기, 로컬 청크, 첨부 intake 연결 (`knu_lms_sync.py` 첨부 리뷰) | 공지 행 create/update 0건; LMS 키 정확 매칭; 수정·중복·누락·본문 불완전 처리; 첨부 provenance |
| P-F School 전체 흐름 | 전사문 8개(사람 날짜 확정 후) + Canvas 모듈 자료 샘플 + 공지로 end-to-end, retrieval 질의 | 사람 요청과 AUTO plan 교차 실행; 외부 쓰기 응답 손실 후 복구; SOURCE/AI/USER 분리; human-only 필드 불변; 분류 지표(O1) 산출 |

각 단계: 계획 리뷰 → 구현 → 전체 테스트 → 구현 리뷰(insane-review GPT-6 Extra High). 어떤 단계도 "통과한 테스트"로 선언되지 않는다; 위 조건은 구현 시 추가할 테스트다.

## 10. 테스트·평가

- 달력 실패 우선순위(O2): 전사문 날짜 검사는 (1) 학기 범위 밖 → `MISMATCH`, (2) 과목 달력 없음 → `NO_CALENDAR`, (3) 달력 AMBIGUOUS → `AMBIGUOUS`, (4) 날짜 불일치 → `MISMATCH`, (5) 유일 일치 → `MATCHED` 순서로 결정한다. 2029년 전사문 8개는 (1)에서 전부 `MISMATCH`다(달력이 없는 과목도 동일). S2 기대값(`Week1`, `mbti`)은 fake provider의 고정 응답으로 검사하고 live Jev 판단과 분리한다. 합성 S2 fixture(O2)에는 항목별 fake 응답(확률 분포·confidence)과 기대 결과(선택 Kind 또는 폴백 사유)를 함께 둔다: 예 `Week1`→{LECTURE_SLIDES 0.72, LAB_MATERIAL 0.20, …; confidence 0.83}→확정, `mbti`→{ASSIGNMENT_RESOURCE 0.55, SUPPLEMENT 0.30, …; confidence 0.61}→S3 `LOW_CONFIDENCE`, 동률·`no_match`·검증 실패 케이스 포함.
- fixture: `intake-classification-fixtures-20261009.json`(Canvas 5과목 모듈 항목·과제·공지 + 업로드 전사문 8개). 항목마다 구조화 기대값: `expected_kind`, `expected_decision`, `expected_classification_stage`(S1/S2/S3), `expected_final_intake_state`(`CANVAS_METADATA_ONLY` | `AUTO_PLANNED` | `NEEDS_INPUT` | `UNSUPPORTED` | `DEFERRED`), `expected_fallback_reason`(고정 코드 또는 null), `expected_session_mode`, `expected_session_created`, `expected_material_created`, `expected_origin`, `expected_course_key`, `expected_week`, `expected_calendar_match`. 자유 텍스트 기대값은 쓰지 않는다. 규칙 테이블 변경 시 fixture 버전도 올린다.
- Jev 검증 실패 케이스(≥15): 합 오차, 동률, 옵션 누락/추가, 모델 불일치, bool 확률, NaN, 중복 키, 크기 초과(요청/응답), timeout(socket/total), redirect, proxy env 무시, non-JSON, 2xx 외, usage 손상.
- 파이프라인: S1 확정/S2 확정/S3 폴백/사람 우선/재분류 금지/예산 소진/캐시 적중·충돌(메타 변경 시 미적중).
- 평가(O1): 사람이 라벨한 검증 표본으로 자동 처리율·폴백률·오분류율·호출 수를 로컬 집계(원문·개인정보 저장 없음). 임계값 재조정은 이 수치로만 제안한다.
- 합성 콘텐츠 fixture(O2): Kind별(COURSE_INFO·LECTURE_SLIDES·LAB_MATERIAL·ASSIGNMENT_RESOURCE·PROVIDED_CODE·TRANSCRIPT 달력 일치, 그리고 EXAM·SETUP_GUIDE·SUPPLEMENT·ASSIGNMENT_BRIEF의 정상 PDF와 PARTIAL 사례) 실제 바이트를 가진 Drive 합성 항목으로 AUTO 양성 경로(plan 생성→처리→Notion→노출 게이트)를 검증한다.
- 기존 contract 테스트 전부 유지.

## 11. 리스크 / 결정

- R-1. ExternalTool 본문 다운로드 가능 여부는 과목마다 다를 수 있다 → P-E에서 과목별 확인, 불가 항목은 메타만.
- R-2. Jev 비용·지연 → 항목당 1회 + 캐시 + 틱 예산, 일괄 분류는 여러 틱에 걸쳐 DEFERRED로 진행됨을 readiness에 표시.
- R-3. 과제 구간 노출 → §7 retrieval 경계 + Behavior Contract.
- 결정됨: Keychain 전용 항목 / 0.80·0.70 / 회차 달력 매칭 채택.
- 총괄 결정(사용자 확인 대기): (a) HUMAN Input Request `Kind` 옵션을 §2.1로 확장(M4 선결). (b) Materials `Type` 초기값 표(§5). 열림: (c) PROVIDED_CODE 검색 노출용 line locator 명세 개정 시점. (d) PPTX/DOCX extractor 도입 시점.
