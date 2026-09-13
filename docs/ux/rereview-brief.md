# UX rev7 독립 재리뷰 요청

대상: `file-intake.md`, `intake-execution-contract.md`, `intake-prototype.html`.
목표: **다음 버전의 설계 정의 수용**. 제품 구현/배포 승인이나 v1.2 코드 전체 정확성 리뷰가 아니다.
현재 frozen 문서와 코드는 현실 기준이고, UX-C1 §9의 C1–C8은 명시적 명세 개정 후보다.
코드가 아직 새 설계를 구현하지 않는다는 사실 자체는 결함이 아니다. 개정 후보가 불명확하거나
불변식을 약화하거나 사용 경로를 완결하지 못하는 부분을 찾아 달라.

## 고정된 사용자 선택

- Notion 학기 대시보드 → 과목 → 세션; 과제·시험·공통 학사 일정·개인 할 일·이어서 공부.
- Drive 학기 단일 `+ 업로드`가 기본, 선택 과목별 폴더도 동일 접수기 사용.
- 파일 번호와 수업 차시는 다르다. 실제 날짜를 업로드 날짜로 대체하지 않는다.
- 짧은 접수 요약 대신 예제·증명/계산·오개념·문제/해설·출처를 갖춘 학습 노트.
- SOURCE/AI/USER 소유권, 사람 승인, freshness, Partial, read-only 검색 MCP는 유지한다.
- 사용자가 Pro 사용량 제한을 명시했고 웹 `매우 높음` 대체 리뷰를 요청했다.

## 집중해서 판단할 계약

1. UNKNOWN은 PageRange(None,None)에 흘러가지 않는다. 명시적 전체 선택과 정확한 사용 승인을 분리했다.
2. 기존 수업 연결은 source allocator 우회가 아니라, exact provider target 검증과 durable binding·ID reserve를 갖춘 신규 worker 포트다.
3. canonical source 이전 SQLite intake + Notion 운영 표시/입력 요청을 분리했다. 일반 입력을 승인 큐로 포장하지 않는다.
4. Notion 원본 데이터 소스의 속성/템플릿/연결 뷰 → 제출 → local worker polling → receipt → 단계별 readback을 정했다.
5. 상세 노트의 명시적 생성 요청·모델 중립 generator·허용된 context·불변 산출물·AI 영역·근거 manifest·재시작/freshness를 정했다.
6. 개인 일정 원본은 USER DB 하나, 학술 과제/시험은 기존 원본 뷰; 가짜 Course와 복제 완료 동기화를 만들지 않는다.
7. two-part timestamp 문법을 공유하고 약한 유형 fallback을 자동 확정하지 않는다.

## 이번 개정에서 구체화한 경계

- §2.2: Request Type enum, 타입별 별도 Relation, 타입별 필수/금지 필드 및 cardinality matrix.
  `파일 접수함.Intake ID`는 외부 생성 응답 유실 시의 정확한 projection 복구 키다.
- §4: Course 전체 provider Session inventory/ID seed gate가 신규 allocation보다 먼저다.
  write 후에는 포인터뿐 아니라 page/Course/app ID/Sessions 원본 membership·strict S ID·Date 전체 tuple을 검증한다.
- §6/§9 C6: `derived/ai-study-notes`는 명시적인 Drive 저장 역할 개정이며
  AI snapshot namespace/role이다. source 및 deterministic normalized 수집에서 제외한다.
- §3.2: canonical 등록 전 과목 입력 정정, 등록 후 자동 정정 미지원의 UI/처리 경계를 구분한다.
- §5.1: 새 범위 입력 세대가 이전 미적용 제안을 SUPERSEDED로 만들고 승인 실행 직전 세대를 검사한다.
  UNKNOWN 또는 변경 제안 Reject는 기존 유효한 Usage의 철회가 아니다.
- 모형은 수업별 상태를 분리하고, 파일 버전이 같은 범위 변경도 노트 근거 identity에 반영한다.
  새 수업/기존 수업 전사 분리, 제안 거절, 이전 승인 보존, UNKNOWN supersede를 실제 조작 검사했다.
- §4.3: 새/기존 수업 binding은 PENDING/APPLIED/RECONCILE_REQUIRED/RELEASED이며,
  mutation 시작 기록 전에 실패했음이 증명된 예약만 새 검증 요청으로 교체한다. 응답 유실은 reconcile한다.
- §5.2: CREATE의 명시적 Usage Role과 기존 derive_usage_id/완전한 create_usage semantics,
  UPDATE의 exact Usage Relation·canonical target ID·기존 Role 보존을 정했다.
  정확한 provider identity tuple의 usage_slot_key가 head를 결속하고 같은 slot의 중복 Usage는 보류한다.
- §2.2/§5.1: USAGE_RANGE는 일반 충돌 거절의 명시적 예외다. 같은 poll은 전체 조회 후
  created_time/page ID 순으로 claim하며 이미 claim된 요청은 재정렬하지 않는다.
- C8: 다음 버전 poll_interval_minutes 기본 1(60초) 및 UI의 실제 설정값 표시를 명세 개정에 추가했다.
- 모형 NEW/EXISTING과 exact Session은 미선택 기본값이다. 같은 날짜의 두 수업도 구분한다.
  UNKNOWN/WHOLE의 잔존 페이지는 오류이며 사용자가 비운 뒤만 제출한다. Role 누락도 제안을 막는다.
- §5.3: 기존 action semantics는 유지하되 proposal identity envelope v2에 slot/request/generation을
  결속한다. 같은 request retry는 같은 ID, 동일 action의 새 generation은 다른 ID이며 옛 Decision을 재사용하지 않는다.
- §5.1: 취소/제출 철회/입력 변경은 current head inactive + 미적용 proposal SUPERSEDED다.
  HAA는 active·원 요청 상태/hash도 확인하고, 이미 APPLIED된 Usage는 되돌리지 않는다.
- §4.1: 신규 입력은 legacy allocate_entity 대신 reserve_session_entity API를 사용한다.
  예약은 canonical_entity_id를 쓰지 않고 apply_session_binding의 성공 readback 뒤에만 확정한다.
- §4.2: type의 뜻은 등록된 Sessions data_source membership + strict entity type S의 app ID다.
  Notion의 새 Type property를 요구하지 않는다.
- §6.2: manifest는 선택한 evidence dependency set만 포함한다. 전사만 노트는 무관한 PDF/Usage 변경으로
  갱신 필요가 되지 않는다. 확인/임시 모드의 선택된 dependencies와 membership은 기록한다.

## 증거 범위

### rev7 집중 재검토

이번 검토는 다음 변경 묶음과 그 직접 영향을 중심으로 판정한다. 이미 완결된 구현 계약의
SQL/SDK 구현 코드를 추가 설계 요구로 바꾸거나 모형에 모든 backend 상태 재현을 요구하지 말라.
구체적인 불변식 위반·모순·사용 경로 차단은 계속 지적하되, 선택적 개선은 분리하라.

- 실제 학술 Session No와 내부 S ID를 분리했다. 사용자 입력을 늘려 차시 미상 전사를 막는 대신
  Session No를 다음 버전에서 nullable로 개정하고 USER 실제 차시만 저장한다. N강 resolver도
  명시 Session No/USER alias만 쓰도록 C2/C3에 개정, ID suffix를 차시로 해석하지 않는다.
- §6.2 study_note_heads와 request/job/artifact 분리: 최신 active request만 현재 AI 영역 게시,
  쓰기 전후 head/취소/hash 검사, 늦은 결과는 이력, 같은 job의 다른 request 취소는 격리한다.
- Proposal ID는 prefix 없는 64자리 소문자 hex 한 형식으로 고정했다.
- 모형에 명시 자료 포함과 정확한 Material 선택, 선택된/비선택 자료의 변경, 실제 poll 목표 표시를
  추가했다. 최신 요청과 차시 입력 UI도 포함한다. late finish·공유 job은 실제 구현 후 A45로 시험한다.
- §3.4 첫 Material create 전 0개(과거 시도 없음)/같은 예약의 1개 회수/2개 이상 충돌을 구분했다.
  생성 시도 후 부재·불명확 응답을 곧바로 중복 생성하는 경로는 금지한다.

### 이번 rev6의 변경 묶음

직전 검토에서 남았던 생성 identity·approval wire·상태 표시의 계약을 닫았다. 이번에는 다음
변경과 영향을 받는 불변식 중심으로 검토한다. 현재 구현에 새 포트가 없다는 지적은 구현 과제다.

- §3.4: Materials 전체 provider inventory, strict M ID seed/reserve/apply, deterministic creation
  snapshot·required fields·초기 enum, exact readback/recovery·등록 전 source 확인을 규정했다.
- §4.1: NEW Session의 Name/Date/Status/Recording Status 포함 creation snapshot과 단계 순서를
  고정했다. 초기 제목은 날짜+예약 ID이며 파일명의 자료 번호를 수업 차시로 추측하지 않는다.
- §5.3: Queue `Proposed Action`과 새 SYSTEM `Proposal Envelope` Rich text를 분리한 단일 wire
  format, strict canonical JSON·원장 대조·재시작·변조 검사, C3/C5 변경을 명시했다.
- §5.1: PENDING_REVIEW/APPROVED만 supersede, REJECTED/APPLIED 등 terminal 이력 보존.
- §6.1–2: confirmed-lecture.v1의 exact eligible Type/authority/Session·모든 Usage Role·순서·예산 전
  membership을 고정했다. 교과서는 명시 자료 모드에서 직접 선택하며 자유문으로 자동 확대하지 않는다.
- §6.5: durable 사용자 상태표·마지막 성공 단계·유한 재시도·부분/실패/준비/갱신 구분을 추가했다.
  모형은 영구 오류·게시 실패·부분 결과·AI 미연결·이전 산출물 보존 화면을 조작할 수 있다.

`intake-prototype.html`은 원격 호출이 없는 합성 화면/상태 모형이다. Notion 실제 배치/버튼/API가
이미 설치됐다는 뜻이 아니다. 모형 버튼은 실제 Notion 템플릿 생성·속성 편집·제출 체크·Decision
선택을 축약한다. `다음 동기화 재현`, `자료 변경 재현`은 모형 제어이며 제품 기능이 아니다.
실제 강의 전사, 강의 파생 내용, Drive/Notion 개인 식별자와 과거 live 시연 보고서는 제외했다.
문서의 상대 링크는 근거 위치 안내일 뿐 이번 첨부에 없는 파일을 읽었다고 가정하지 말라.

로컬 Playwright로 34개 검사 묶음을 통과했다. 날짜/과목 누락, 기존 수업 날짜 불일치,
오프라인 대기, 기존 Session 재시도, UNKNOWN, PDF 범위, 승인 선택/적용 분리,
승인 후 자료 변경, 자료 없는 확인 근거 요청, 노트 stale, USER 할 일 유지,
입력 변경/취소, 범위만 변경한 노트 stale, 과거 미적용 제안 무효화, 거절 시 기존 사용 범위 보존, 새 날짜 수업 분리, 320/390/736/1024px에서 가로 넘침 없음, JS 오류 없음을 포함한다.
이는 모형 검사이며 A01–A45 전체 backend/provider 수용 시험 완료가 아니다.
Drive 이동·서버 원자성·AI 생성·provider readback은 **정의만** 있고 실제 구현 후 검증 대상이다.
공식 Notion/Drive 기능 근거는 UX-C1 §10의 직접 링크에 있다. 특정 서비스 기능 불가를 추측하지 말라.

## 요청하는 결과

다른 리뷰의 결론을 참고하지 않는 독립 판단으로 한국어 답변을 달라.

1. 다음 버전 설계 정의 수용: GO 또는 REVISE, 핵심 이유.
2. 필수 수정은 최대 6개, P1/P2 구분. 각각 구체적 사용자 상황 → 잘못된 결과 →
   **판정마다 파일/라인/코드조각을 인용하라** → 최소 수정 → 검증할 수용 사례.
3. 불변식 위반과 미결정 실행 경로를 우선하고, 선택적인 UI 개선은 별도 최대 3개.
4. 이미 명시적으로 결정/유예된 범위를 새 미결정 사항으로 재포장하지 말라.
   새 기술/유료 공급자/공개 서버/필수 웹훅을 임의 추가하지 말라.
5. 사용자 화면이 준비/부분/대기/실패/갱신 필요와 승인 선택/실제 적용을 오해 없이 구분하는지 검토하라.
6. 구현 후 live 검증으로 남는 항목을 설계 수용 판정과 분리하라. GO면 잔여 사항이 모두
   구현 수용 시험인지, 설계를 바꿔야 하는 결함인지 명시하라.
