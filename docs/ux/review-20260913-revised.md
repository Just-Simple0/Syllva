# 학습 UX 정의 수정·재리뷰 기록

작성일: 2026-09-13. 대상: [UX 정의 rev10](file-intake.md),
[UX-C1 실행 계약](intake-execution-contract.md), 합성 입력·승인 모형.

## 범위와 최종 판정

설계 정의와 실행 계약을 고도화하는 작업이다. 제품 코드, 현행 frozen 문서, 운영 Drive/Notion,
AI 공급자 연결은 변경하지 않았다. 기존 전사 정규화 수정과 테스트 작업도 보존했다.
**최종 판정: 설계 묶음 수용 GO.** 각 변경 묶음의 독립 웹 검토와 수정, 마지막 C6 재리뷰 GO,
Gemini의 UI/오류 흐름 GO 및 합성 검사 증거를 통합해 현재 assistant가 수용했다.
필수 미해결 지적은 없다. 이는 다음 버전 구현 기준의 수용이며 제품 구현·운영 적용 완료가 아니다.
마지막 웹 검토는 단일 C6 변경에 대한 집중 검토이고, 전체 묶음 수용은 앞선 넓은 검토와
후속 수정 검토를 누적한 판단이다. 마지막 2개 파일만으로 전체 저장소를 검증했다고 주장하지 않는다.

## 반영한 결정

| 사용자 경험 | 고정한 처리 계약 |
| --- | --- |
| 학기 → 과목 → 수업에서 공부 | Notion 기본 페이지·연결 뷰·템플릿 사용; 개인 할 일·공통 일정은 USER 원본 DB |
| 학기 `+ 업로드`에 파일 넣기 | 지속적인 pre-canonical intake, 선택 과목 폴더 지원, 같은 file ID로 정리 |
| 과목·날짜·수업 입력 | 일반 요청의 타입/필수·금지 필드/대상 개수 고정; 사람 승인과 분리 |
| 기존 수업에 전사 연결 | 전체 Session ID 확보 선행, 정확한 기존 대상 binding, 쓰기 전후 전체 identity 검사 |
| 자료 사용 범위 확인 | 미확인·지정 페이지·전체 구분; 새 입력은 과거 미적용 제안을 종료 |
| 변경 제안 거절 | 그 제안만 종료; 기존 유효한 사용 승인은 보존 |
| 자세한 학습 노트 요청 | 별도 생성·쓰기 경로, 허용된 근거, AI snapshot과 Notion AI 영역, 부분/갱신 상태 |
| 잘못된 과목·오프라인·실패 | 등록 전 정정과 등록 후 미지원 경계, 최근 동기화 시각, 재시도/부분 반영 표시 |

## 리뷰 의견의 처리

1차 리뷰의 6개 보완 사항을 반영한 rev2를 웹과 독립 Gemini에 전달했다.
웹은 3 P1/2 P2를 추가로 지적했다. 요청 타입 matrix, 신규 ID 할당의 선행 gate,
binding 후 전체 대상 readback, Drive AI snapshot의 명세 변경, 등록 후 과목 정정 경계를
모두 rev3에 반영했다.

Gemini의 초기 GO 이후 현재 assistant가 연속 조작의 모순을 발견해 추가 점검을 요청했다.
Gemini도 이를 확인해 REVISE로 교정했다. 미확인 재입력 후 과거 제안이 남는 문제,
범위만 바뀐 노트의 freshness 누락, 새 수업 선택이 고정된 기존 화면을 바꾸는 문제를 수정했다.
‘제안 거절 시 무조건 범위 미확인으로 복귀’라는 권고는 기존 승인까지 철회할 수 있어 거부하고,
기존의 유효한 Usage와 새 제안을 분리하는 규칙으로 바꿨다.

rev3 웹 검토의 추가 6개도 반영했다. Usage를 새로 만들 때 사용 역할을 명시하고 기존 관계를
바꿀 때는 정확한 identity와 Role을 보존한다. binding은 예약·적용·확인 필요·해제 이력으로
구분해 외부 쓰기 전 실패의 안전한 재선택과 응답 유실의 보수적 회복을 정했다.
범위 요청은 일반 충돌 거절 규칙에서 제외하고 같은 poll의 수신 순서를 고정했다.
60초 기본값은 C8로 개정 목록에 넣었으며, 모형은 수업 선택과 페이지 잔존 값 검사를 강화했다.

rev4에서 남은 5개 경계도 보완했다. 동일 범위 재요청은 새 세대가 결속된 제안 ID를 만들고,
취소는 active head와 미적용 제안을 무효화한다. 신규 예약 API가 canonical binding을 조기에
기록하지 않도록 분리했다. 수업 타입은 실제 Sessions 원본 membership와 S형 app ID로 정의했고,
노트 manifest는 선택한 근거 의존성만 추적하도록 명시했다.

rev5의 추가 6개도 rev6에 반영했다. 새 PDF의 Materials inventory·예약·readback·canonical 확정과
새 수업의 필수 생성 필드/초기값을 고정했다. 승인 envelope는 별도 SYSTEM Rich text와
canonical JSON 형식을 정해 재시작 뒤에도 동일한 ID를 검증할 수 있게 했다.
REJECTED/APPLIED 등 종료 이력 보존, 기본 노트가 선택하는 강의자료 목록,
노트의 부분·영구 실패·게시 실패·연결 대기와 다음 행동을 명확히 했다.
새 실패 화면은 이전 노트 보존과 함께 직접 조작·캡처했다.

rev6의 5개 지적은 rev7에 반영했다. 실제 차시와 내부 ID를 분리하고, 차시 미상 전사를 막지
않도록 Session No를 선택 입력으로 개정했다. 리뷰의 ‘차시 필수 입력’ 해결책 대신 nullable
필드와 명시 차시/USER alias만 쓰는 resolver 계약을 채택했다. 최신 학습 요청만 현재 AI 영역을
갱신하는 head와 공유 job의 요청별 취소 격리도 정의했다. Proposal ID 문자열을 소문자 hex로
고정했으며, 모형에 명시 자료 선택과 동기화 목표 설정 표시를 추가했다.
직전에는 선택 개선으로 분류된 모형 2건도 이번에 구현해 확인 가능하게 했다.
통합 검토에서 Material 생성 직전 0개/1개 회수/중복 충돌의 분기도 추가로 명확히 했다.

rev7의 잔여 3개를 rev8에 반영했다. Drive 폴더 생성 payload에 예약별 복구 marker를 결속하고
중간 폴더까지 같은 회복 규칙을 적용한다. 취소된 작업의 같은 note_key로 새 요청이 오면
과거 취소 이력을 보존한 채 새 attempt를 연다. 날짜는 수업 후보를 좁히는 용도로만 설명하고
정확한 페이지 선택을 유지한다. 전사 연결과 PDF 사용 상태도 분리해 표기했다.
A37의 ‘M ID 거부’는 Session에 M형 ID가 들어온 경우를 뜻하므로 ‘S형이 아닌 ID(M 등)’로
명확히 했다. 합성 proposal ID를 가짜 64자리 hash로 꾸미라는 선택 권고는 채택하지 않았다.
모형이 실제 v2 암호학적 검증을 수행하지 않는다는 경계를 유지한다.

rev8의 잔여 2개는 rev9에서 계약 간 연결을 확정했다. 폴더 복구를 수행하는 worker 전용 포트를
DriveAdapter 명세 변경 대상으로 추가했다. 같은 note_key의 실행 중 요청은 하나의 active attempt에
reference로 연결하며, terminal 뒤 재요청만 새 attempt를 열도록 조건을 고정했다.
취소와 늦은 응답이 다른 요청/현재 실행 상태를 되돌리지 않는 identity 검사도 명시했다.

rev9에서 남은 C6 1건은 rev10으로 수정했다. verified artifact를 재사용할 때에도 새 attempt를
예약해 현재 요청/상태를 결속한다. STAGED에서 시작하므로 generator 추가 호출은 없고, 이전
취소/실패 이력을 바꾸지 않으면서 재게시 성공·실패를 새 시도에 기록할 수 있다.
C2/DriveAdapter는 rev9 검토에서 별도 GO로 확인했다.

rev10 웹 재리뷰는 필수 결함 없음/GO다. 게시 성공의 READY는 전체 coverage일 때이며,
부분 산출물은 PARTIAL이라는 조건도 그대로 유지한다. Gemini의 ‘완벽’, ‘수학적으로 차단’ 같은
평가 수사는 수용 근거로 채택하지 않았다. 형식 증명이나 실제 동시성 시험은 수행하지 않았으며,
수용 근거는 계약 일관성 검토와 명시한 합성 UX 검사다.

필요 없는 기능 확장이나 문구만의 수정으로 결함을 덮지 않았다. 기본 업로드 방식 재질문,
공개 HTML 서버, 필수 웹훅, 미지원 Queue enum, 일반 입력을 사람 승인으로 취급하는 우회는 없다.

## 검증 증거와 한계

- 합성 모형 **34개 검사 묶음 통과**, JavaScript 오류 0건.
- 날짜 누락·기존 수업 날짜 불일치, 오프라인 대기, 반복 동기화, 입력 변경/취소.
- 미확인 범위, 잘못된 PDF 페이지, 승인 선택/실제 적용 분리, 승인 후 자료 변경.
- 확인된 PDF가 없는 노트 요청, 파일/사용 범위 변경 후 갱신 필요, 기존 사용 승인 보존.
- 새 수업과 기존 수업의 탐색·전사·제안 identity 분리, USER 할 일 상태 보존.
- 영구 생성 오류·게시 재시도 소진·부분 노트·AI 미연결과 회복·이전 산출물 보존.
- 명시 교과서 선택·미선택 자료 변경 무영향·최신 요청 표시·학술 차시/내부 ID 분리·동기화 목표.
- 320/390/736/1024px 가로 넘침 검사, 데스크톱·모바일·다크 화면 시각 점검.

이 검사는 **로컬 상태 모형**이다. 실제 Notion 연결 뷰나 Drive 이동, SQLite migration,
동시 편집·응답 유실, AI 공급자 호출, capability·freshness 검사 통과를 증명하지 않는다.
실제 구현 수용 조건은 UX-C1의 A01–A45와 §9의 명세 개정 C1–C8이다.
특히 A44의 할당 실패/재시작과 A45의 늦은 완료/공유 job 취소는 모형에서 구현하지 않았으며,
관련 입력과 현재 요청 표시의 UX만 검사했다.
Notion의 비원자적 편집 때문에 이미 완료된 외부 쓰기의 취소를 소급 보장하지도 않는다.

## 리뷰 실행 기록

- 웹 rev2: 실제 선택 검증된 **GPT-5.6 Sol (매우 높음)**, 436초의 완성 응답, REVISE.
  [검토 대화](https://chatgpt.com/g/g-p-6a9fdbd2dc3081919990a6607f8fe7c4-syllva-eeb93c01/c/6aa59b65-42a4-83e8-a3f8-aa4a95d00915).
- 웹 rev3: **GPT-5.6 Sol (매우 높음)**, 403초 완성 응답, REVISE.
  [검토 대화](https://chatgpt.com/g/g-p-6a9fdbd2dc3081919990a6607f8fe7c4-syllva-eeb93c01/c/6aa59e4a-d4e0-83ee-9776-58c8c886b968).
- 웹 rev4: **GPT-5.6 Sol (매우 높음)**, 342초 완성 응답, REVISE.
  [검토 대화](https://chatgpt.com/g/g-p-6a9fdbd2dc3081919990a6607f8fe7c4-syllva-eeb93c01/c/6aa5a26a-847c-83ee-a76e-c8feebc8d568).
- 웹 rev5: **GPT-5.6 Sol (매우 높음)**, 588초 완성 응답, REVISE.
  [검토 대화](https://chatgpt.com/g/g-p-6a9fdbd2dc3081919990a6607f8fe7c4-syllva-eeb93c01/c/6aa5a62b-990c-83ee-8e47-22d61609c1ed).
- 웹 rev6: **GPT-5.6 Sol (매우 높음)**, 346초 완성 응답, REVISE.
  [검토 대화](https://chatgpt.com/g/g-p-6a9fdbd2dc3081919990a6607f8fe7c4-syllva-eeb93c01/c/6aa5ac45-6e2c-83ee-82d7-c72318be80ec).
- 웹 rev7: **GPT-5.6 Sol (매우 높음)**, 326초 완성 응답, REVISE.
  [검토 대화](https://chatgpt.com/g/g-p-6a9fdbd2dc3081919990a6607f8fe7c4-syllva-eeb93c01/c/6aa5af29-37e4-83ee-8e57-371a5d75af04).
- 웹 rev8: **GPT-5.6 Sol (매우 높음)**, 215초 완성 응답, REVISE.
  [검토 대화](https://chatgpt.com/g/g-p-6a9fdbd2dc3081919990a6607f8fe7c4-syllva-eeb93c01/c/6aa5b1dc-7174-83ee-9190-653ed5779a81).
- 웹 rev9: **GPT-5.6 Sol (매우 높음)**, 176초 완성 응답, C2 GO / C6 단일 수정 REVISE.
  [검토 대화](https://chatgpt.com/g/g-p-6a9fdbd2dc3081919990a6607f8fe7c4-syllva-eeb93c01/c/6aa5b394-4024-83ee-98f7-c171915551d9).
- 웹 rev10: **GPT-5.6 Sol (매우 높음)**, 137초 완성 응답, C6 단일 수정 **GO**.
  [검토 대화](https://chatgpt.com/g/g-p-6a9fdbd2dc3081919990a6607f8fe7c4-syllva-eeb93c01/c/6aa5b4e1-b228-83e8-b043-94e0600628ea).
- Gemini: 독립 `google-antigravity/gemini-3.8-flash / high`. rev2 추가 점검 REVISE → rev3–rev10 GO,
  추가 필수 결함 0건. 별도 맥락에서 시작해 같은 reviewer가 관련 수정과 화면 증거를 재검토했다.
- Pro 사용량 제한에 대해 사용자가 요청한 대체 방식이며 **Pro 리뷰로 표기하지 않는다**.
- 웹 rev2–rev7 각 묶음 14개, rev8–rev9 집중 검토 각 6개 파일의 전체 내용을 원문과 행별 대조했다.
  집중 검토는 변경한 계약/UX·동일 HTML·현행 frozen 명세·질문을 포함하며, 이전 검토의 제품 코드는
  수정되지 않았다. 압축·강제 조기 답변 없음.
  rev10은 이미 수용된 C2/화면을 재확장하지 않고 전체 최신 실행 계약과 단일 수정 질문 2개 파일로
  마지막 C6 변경의 직접 영향을 검토한다. 각 단계의 판정·수정 범위를 합쳐 최종 묶음을 판단한다.
  실제 강의 전사·비공개 시연 보고서·운영 Drive/Notion 식별자는 포함하지 않았다.

로컬 감사: `.review/ux-rev2-inputs.json`, `.review/ux-rev3-inputs.json`, `.review/ux-rev4-inputs.json`,
`.review/ux-rev5-inputs.json`, `.review/ux-rev6-inputs.json`, `.review/ux-rev7-inputs.json`,
`.review/ux-rev8-inputs.json`, `.review/ux-rev9-inputs.json`, `.review/ux-rev10-inputs.json`,
`.review/ux-rev7-prototype-checks.json`, 각 revision의 Gemini 보고서.
이 파일들은 검토 증거이고 제품 데이터나 운영 승인 기록이 아니다.

## 최종 상태와 다음 구현 경계

검토 후 실행 계약/UX의 상태 표시만 ‘설계 수용 완료’로 바꿨다. 검토한 기술 내용은 동일하며
프로토타입은 rev7 검사 시점과 byte 동일하다. 링크·코드 블록·공백·`git diff --check`와
frozen/기존 전사 코드·테스트 hash 보존을 최종 확인했다. 제품 테스트는 이 문서 작업에서
재실행하지 않았다. 커밋·푸시·운영 쓰기·AI 공급자 설정은 수행하지 않았다.

후속 구현은 C1–C8 명세 개정의 채택과 함께 시작하고, A01–A45 및 A38-folder/A45-a/b/c를
실제 SQLite/worker/adapter 시험으로 검증한다. UI의 학기→과목→수업, 단일 업로드함,
전체 학습 노트 품질 목표는 유지한다. 현재 사용자에게 추가로 답을 받아야 할 질문은 없다.
