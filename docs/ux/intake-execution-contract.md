# UX-C1: 입력·수업 연결·학습 노트 실행 계약

버전: rev10, 2026-09-13. 상태: **설계 수용 완료 — 다음 버전 명세 개정안**.

이 문서는 [사용자 UX 정의](file-intake.md)의 실행 의미를 고정한다. 현재 v1.2의 배포 완료를
뜻하지 않는다. 기존 frozen 문서는 그대로 보존한다. 아래 §9는 다음 버전에 반영할 정확한
명세 변경이며, 현행 v1.2 코드에서 계약을 우회해 적용하지 않는다. 재리뷰 GO는 이 설계 묶음의
수용이고, 운영 연결·배포·실제 자료 이동이나 새 유료 AI 연결의 승인은 아니다.

## 1. 첫 구현에서 고정하는 선택

- 학기 단일 `+ 업로드`가 기본이고, 등록된 과목 하위 폴더도 같은 접수기에 연결한다.
- Notion은 기본 페이지·원본 데이터 소스의 연결된 뷰·속성 편집·템플릿을 사용한다.
  HTML 임베드, 외부 웹훅 수신 서버, 공개 호스팅은 첫 흐름의 필수 조건이 아니다.
  실제 학습 UI는 Notion 내부이며 공통 페이지 링크는 대시보드·현재 학기 과목·파일 확인으로
  제한한다. 수업은 과목의 Sessions 연결 뷰에서 연다. HTML은 이 탐색과 상태를 시험하는
  합성 모형으로만 사용한다. 자세한 화면 대응은 [탐색 결정](navigation-notion.md)을 따른다.
- 과목·종류·날짜 입력은 **일반 입력 요청**이다. 사람 권한으로 자료 사용을 확정하는
  **Automation Queue 승인**과 별도 데이터·권한·화면으로 둔다.
- 요청은 Notion에 저장하고 로컬 단일 worker가 다음 실행에서 읽는다. Notion 편집이
  로컬 PC를 즉시 깨우는 방식으로 설명하지 않는다. 다음 버전의 기본 동기화 목표 간격은
  60초이며 PC가 켜지고 연결된 동안만 적용한다. 현행 설정 예시의 10분에서 바뀌는 명시적
  설정 개정은 §9 C8이다. UI는 실제 설정값을 읽어 표시하며 실시간/최대 지연을 보장하지 않는다.
- 상세 학습 노트는 사용자가 요청한 경우에만 worker의 별도 생성·쓰기 경로가 만든다.
  `StudyNoteGenerator`는 모델 중립 포트이며, 최초 설정에서 사용자가 선택·연결한 실행 가능한
  AI 공급자만 사용한다. 미연결 상태는 ‘AI 생성 연결 필요’다. 연결 없이 작업을 생성 중으로
  표시하거나 웹 구독을 자동 API 자격증명으로 취급하지 않는다. 공급자·전송 범위·비용 설정을
  확인하기 전에는 새 공급자를 호출하지 않는다. 이번 문서는 그 연결을 수행하지 않는다.

## 2. 저장 위치와 소유권

기존 여섯 학술 DB와 Automation Queue에 접수용 가짜 학술 개체를 넣지 않는다.
다음 운영/개인 DB는 **다음 버전의 추가 모델**이며 현재 설치돼 있다고 가정하지 않는다.

| 기준 저장소 | 용도 | 사용자 편집 | 시스템 편집 |
| --- | --- | --- | --- |
| 로컬 SQLite `intake_items` | 감지·분류·이동·승격의 지속적인 처리 기록 | 직접 편집 없음 | worker만 상태·identity·재시도 기록 |
| Notion `파일 접수함` | intake의 표시와 입력 요청 링크 | 없음; 제안 수정을 원하면 입력 요청 열기 | 이름·원본 링크·제안·처리 상태·오류·반영 결과 |
| Notion `입력 요청` | 과목·유형·날짜·수업 선택·사용 범위 초안 제출 | 입력 필드, 제출/취소 | 처리 상태·오류·반영 결과만 |
| Notion `학습 요청` | 노트 생성/갱신의 명시적 요청 | 수업·근거 모드·요청 사항·제출/취소 | 처리 상태·근거 명세·결과 링크만 |
| 로컬 `request_receipts`, `note_jobs`, `note_artifacts` | 요청 수신·중복 방지·작업·근거·출력 참조 | 직접 편집 없음 | worker만 |
| Notion `내 일정과 할 일` | 개인 할 일, 학기 공통 일정의 원본 | 제목·학기·날짜·종류·완료·선택 과목·출처 링크 | 사람 내용을 자동 변경하지 않음 |
| Notion 세션의 AI 영역 | 노트의 학습용 표현 | 직접 수정하면 자동 교체를 멈춤 | 허용된 writer가 소유한 AI 블록만 |
| Drive 해당 개체의 `derived` | 버전별 AI 노트 본문과 근거 manifest | 직접 편집을 요구하지 않음 | 불변 파생 산출물 생성·검증 |

SQLite에는 원문·AI 노트 본문·자격증명을 저장하지 않는다. identity·관계·hash·작업 상태와
출력 참조만 보관한다. Notion 표시 페이지를 지웠다고 원본 파일을 삭제하지 않는다.
`파일 접수함`에는 SYSTEM 소유 문자열 속성 `Intake ID`를 필수로 둔다. 이는 로컬 intake_id의
projection이며 페이지 생성 응답 유실 시의 정확한 복구 조회 키다. 외부 편집으로 값을 바꾸면
신뢰하지 않고 원장과 대조하며, 중복/불일치는 표시 복구 오류로 처리한다.

`내 일정과 할 일`은 학기별로 복제하지 않는 단일 원본 DB이며 학기로 필터링한다.
`종류=할 일/학사 일정`, 날짜 선택, 과목 선택(0 또는 1), 완료는 USER 소유다.
기존 일정 DB가 있으면 별도 연결 뷰로 그대로 추가한다. 스키마가 맞는다는 이유만으로
기존 기록을 새 DB로 옮기거나 대체하지 않는다. 과목 과제·시험은 Activities/Exams의 원본을
각각 연결 뷰로 보여준다. 첫 대시보드는 이들을 시각적으로 묶되 하나의 중복 DB로 합치지 않는다.

### 2.1 화면 조작과 실제 저장

| 화면 문구 | 첫 버전에서 실제로 하는 조작 | 처리 결과 |
| --- | --- | --- |
| 자료 추가 | Drive `+ 업로드` 링크를 연다 | Drive에서 업로드; 아직 worker 접수는 아님 |
| 과목·날짜 입력 | 접수 항목의 ‘입력 요청’ 페이지를 열고 속성 편집, `제출=true` 체크 | 일반 요청 수신 후 유효성 검사 |
| 기존 수업 | 입력 요청의 Relation에서 정확한 Sessions 페이지 하나 선택 | §4의 연결 검증 |
| 사용 범위 입력 | 별도 범위 입력 요청에서 `미확인/지정 페이지/전체 자료` 선택 | 정확한 확인 제안 작성 또는 미확인 유지 |
| 사용 승인 | Automation Queue의 완성된 제안 내용을 읽고 `Decision=Approve` 선택 | 기존 ApprovalReader → HumanApprovalApplier |
| 노트 만들기 | 세션의 연결된 `학습 요청` 뷰에서 템플릿으로 새 요청 생성, 근거 모드 확인 후 제출 | 요청 수신·근거 준비·생성·반영 상태 |
| 내 할 일 완료 | 해당 원본 할 일의 완료 체크 | 같은 기록의 모든 뷰에 반영 |

프로토타입의 ‘입력 제출’ 등 버튼은 위 체크/템플릿 동작을 한 번에 시험하는 모형이다.
실제 Notion에 임의 버튼 API가 이미 구현됐다는 뜻이 아니다. 필요한 연결 뷰/템플릿은 최초
설정에서 준비하고 원본 data source ID를 worker에 등록한다. 연결 뷰 자체를 API 원본으로 삼지 않는다.

### 2.2 요청의 일회성 수신과 변경 충돌

입력/학습 요청의 `request_id`는 Notion page ID다. 사용자는 ID나 실행 번호를 입력하지 않는다.
공통 필드는 대상 Relation, 타입별 입력, `제출`(기본 false), `취소`(기본 false)이며,
worker 결과 필드는 별도로 둔다. 일반 입력에 `Decision`이나 `Verified`를 추가하지 않는다.

1. worker는 설정된 원본 요청 DB를 페이지네이션해 읽는다. 제출되지 않은 초안은 실행하지 않는다.
2. 제출된 요청의 USER 입력 필드만 정규화 JSON으로 만들고 hash, 작성/수정 주체, 대상의
   current identity/version을 영속 receipt에 담는다. 시스템 결과 갱신으로 USER 입력 hash는 바뀌지 않는다.
3. 동일 page ID는 한 번만 수신한다. 1초 간격의 두 읽기에서 입력이 같고 필수값이 유효해야
   claim한다. 이 안정성 대기는 사람 승인의 증명이 아니며 단지 Notion의 분리 저장을 완화한다.
4. 읽기 직전/각 외부 쓰기 직전에 제출 유지·취소 여부·입력 hash와 현재 대상을 다시 확인한다.
   바뀌면 이번 요청은 `내용 변경으로 중단`으로 끝내고 새 요청으로 제출하게 한다.
   이미 끝난 외부 동작은 기록해 중복 적용하지 않는다. 부분 반영이 있으면 그 결과를 정확히 표시한다.
5. 처리 중/처리 후 원래 요청을 편집해도 다른 과목·수업으로 조용히 재실행하지 않는다.
   수정은 ‘새 입력 요청’ 템플릿에서 수행한다. 원래 선택은 결과에 남아 비교할 수 있다.
6. 단순 연결 실패의 재시도는 같은 receipt와 동작 키로 수행한다. 사용자 필드를 worker가
   체크 해제하거나 덮어쓰지 않는다. 새 요청이 필요할 때 이유와 입력 페이지 링크를 표시한다.

Notion 속성 숨김·뷰 잠금은 보안 경계가 아니다. 시스템 소유 필드는 SQLite 원장과 비교하고
외부 편집을 신뢰하지 않는다. 기존 승인 경로는 이 일반 요청 receipt로 대체하지 않는다.

#### 요청 타입과 필드 계약

`입력 요청`의 필수 USER Select `Request Type`은 `ASSIGN_COURSE / FILE_DETAILS / USAGE_RANGE`
세 값만 허용한다. 템플릿이 초기값을 넣더라도 worker가 다시 검증한다. 학습 요청 DB의 타입은
`STUDY_NOTE` 하나다. Relation 속성은 각각 `대상 접수`(파일 접수함), `과목`(Courses),
`수업`(Sessions), `자료`(Materials), `대상 사용 관계`(Material Usage)로 별도 정의하며,
하나의 다형 Relation으로 처리하지 않는다.
제출/취소·요청 타입 외에 다음 matrix가 모든 실행 입력을 결정한다. 제목은 실행 입력이 아니다.

| 타입 | 필수 USER 입력과 정확한 개수 | 나머지 실행 필드 |
| --- | --- | --- |
| ASSIGN_COURSE | 대상 접수 1개 이상, 과목 정확히 1개 | 종류·자료 역할·날짜·수업 모드·수업·자료·범위·학습 입력은 비워야 함 |
| FILE_DETAILS / 전사 | 대상 접수 1개, 과목 1개, 종류=TRANSCRIPT, 실제 날짜, 수업 모드=NEW 또는 EXISTING | EXISTING은 수업 1개 필수, NEW는 수업 0개; NEW에만 실제 차시(양의 정수) 선택 입력, 모르면 비움; 자료 역할·자료·범위·학습 입력 금지 |
| FILE_DETAILS / PDF | 대상 접수 1개, 과목 1개, 종류=MATERIAL_PDF, 자료 역할=강의자료 또는 교과서 | 날짜·수업 모드·수업·자료·범위·학습 입력 금지 |
| USAGE_RANGE / CREATE | Usage Operation=CREATE, 수업 1개, 자료 1개, 사용 역할 Primary/Supporting/Reference 중 1개, 범위 모드 | 대상 사용 관계 0개; BOUNDED는 start/end 필수, UNKNOWN/WHOLE은 page 값 금지 |
| USAGE_RANGE / UPDATE | Usage Operation=UPDATE, 대상 사용 관계 1개, 수업 1개, 자료 1개, 범위 모드 | 사용 역할 입력 금지(현재 관계의 Role 보존); page 값 규칙은 CREATE와 동일 |
| STUDY_NOTE (별도 DB) | 수업 1개, 근거 모드 1개; 추가 학습 요청은 선택 | 임시 모드만 자료 1개 이상 허용; 다른 모드는 자료 0개; 접수·과목·종류·자료 역할·날짜·수업 모드·범위 금지 |

FILE_DETAILS는 파일 MIME/현재 타입과 일치해야 하고 AI/USER 파일을 공식 SOURCE로 바꾸는
권한을 주지 않는다. 지원하지 않는 역할은 보류한다. Session/Material의 Course 일치는 현재
provider 관계에서 검증한다. 미지 타입, 금지 필드의 잔존 값, Relation 개수 위반은 claim/외부
쓰기 전에 필드별 오류로 반환한다. 혼합 입력에서 유형을 추론하거나 일부 값을 무시해 실행하지
않는다. 오류 요청은 수정 후 새 템플릿으로 제출한다.
USAGE_RANGE 두 행에는 접수·과목·종류·자료 역할·날짜·수업 모드·학습 입력이 금지된다.
다른 요청 타입에서는 Usage Operation·사용 역할·대상 사용 관계를 비워야 한다.
실제 차시는 FILE_DETAILS/전사/NEW 외의 모든 요청에서 금지한다. 기존 수업의 차시 변경은
이 연결 요청에 끼워 넣지 않고 기존 USER 속성 편집으로 다룬다.
전사의 NEW/EXISTING은 기본 미선택이며, EXISTING의 정확한 Session Relation도 기본 미선택이다.
동일 날짜의 첫 항목을 자동 선택하지 않는다. UNKNOWN/WHOLE로 바꾼 뒤 남은 페이지 값은
입력 오류로 보여주고 사용자가 직접 비운 후 제출한다. 숨긴 값도 검증에서 제외하지 않는다.

묶음 입력은 **선택한 접수 파일들에 같은 과목을 지정하는 요청**만 지원한다. 대상 Relation에
접수 항목 여러 개와 과목 하나를 담고, 항목별 실행 키는 `(request_id, intake_id)`다.
각 항목을 독립 검증·반영하고 ‘2개 중 1개 반영, 1개 날짜 입력 필요’처럼 결과를 보여준다.
다른 파일의 유형·날짜·수업을 일괄 복제하지 않는다. 전사는 후속 개별 요청에서 날짜·수업을
선택하며, PDF는 필요한 값이 갖춰지면 먼저 진행할 수 있다. 범위 입력/수업 연결/학습 요청은
정확한 대상 하나씩만 받는다. 서로 다른 요청이 같은 항목을 건드리면 먼저 수신한 유효 계획을
기준으로 직렬 처리하고, 다른 요청은 현재 계획과 충돌하면 새 입력을 요구한다.
단, **USAGE_RANGE는 이 충돌 거절 규칙의 예외**이며 §5.1의 새 입력 세대 규칙이 우선한다.
미반영 binding의 검증된 회복 요청은 §4.3을 따른다.

Notion 읽기와 외부 쓰기는 원자적 트랜잭션이 아니다. 마지막 검사와 외부 응답 사이의 편집은
취소를 소급 보장하지 못한다. 각 쓰기 후에도 입력/취소를 readback하여 감지된 변경 이후의
후속 동작을 중단하고 이미 반영된 결과를 표시한다. ‘취소 요청’과 ‘취소 완료’를 구분하며,
원본 이동을 자동으로 되돌리거나 사용자 편집을 복구 명목으로 덮어쓰지 않는다.

## 3. 접수·분류·이동

### 3.1 접수 identity와 상태

`intake_items`의 유일 키는 `(provider, provider_file_id)`다. 식별자의 학기별 복제는 금지한다.
필드: `intake_id`, 등록 Inbox ID/semester, 원래/관측 parent ID, 표시 이름, MIME,
관측 hash/revision, 과목·유형 후보와 근거, 선택 과목·유형·수업 계획, receipt 참조,
canonical source/entity 참조, 상태, 마지막 오류 코드·시각·재시도 시각·시도 수.

기본 상태는 `OBSERVED → NEEDS_INPUT → PLANNED → REGISTERED → MOVING → ORGANIZED`다.
확정적인 폴더/유형 연결과 필요한 정보가 이미 있으면 NEEDS_INPUT을 건너뛴다.
오류는 `RETRYABLE_ERROR`, 입력 충돌은 `NEEDS_INPUT`, 지원하지 않는 형식은 `UNSUPPORTED`로
표시하며 마지막 성공 단계는 별도 보존한다. `ORGANIZED`는 읽기/학습 완료가 아니다.

- 학기 + 폴더에서 과목은 명시적 등록 mapping 또는 사용자의 과목 선택으로만 확정한다.
  AI/파일명/본문 코드의 추정은 후보다. 같은 source가 다른 학기/과목 폴더에서 발견되면
  재배정하지 않고 기존 연결과 충돌을 보여준다.
- 전사는 유형+과목+실제 날짜+새/기존 수업 선택이 갖춰져야 정식 Session 등록으로 넘긴다.
  PDF Material은 과목+유형으로 §3.4의 검증된 등록을 시작할 수 있다. 날짜를 만들지 않는다.
- `1주차.md` 같은 이름과 UTF-8 MD/TXT를 허용한다. 두 부분/세 부분 시간 문법을
  normalizer와 공유한다. 시간 표시는 유형 후보 근거이며 제목·역할 충돌은 확인 대상으로 둔다.
  현재 `MATERIAL confidence=0.5` 같은 fallback은 자동 정식 등록에 사용하지 않는다.
- 실제 형식 지원표는 MD/TXT 전사·텍스트 PDF가 첫 묶음이다. 스캔/OCR·PPT 변환·음성 STT·
  복합 실습 파일은 보관/접수 상태와 미지원 이유를 보여주고 다음 지원 묶음으로 확장한다.
  미지원 파일을 읽기 완료로 만들지 않는다.

### 3.2 지속성·재시작·표시 복구

감지 이벤트는 파일을 이동하기 전에 SQLite에 commit한다. Notion 표시는 outbox로 재시도한다.
페이지 생성 성공 응답을 잃은 경우 `intake_id`로 기존 페이지를 조회해 재사용하고,
동일 키의 페이지가 여러 개면 임의로 고르지 않고 표시 복구 오류로 멈춘다.
Notion에 표시를 쓰지 못해도 파일은 원위치에 있고 intake는 보존된다.
원본이 삭제/권한 상실되면 ‘원본 접근 불가’로 표시하며 다른 파일로 대체하지 않는다.

receipt의 유효한 계획을 기록한 뒤에만 canonical source를 등록/할당한다.
등록 후 외부 처리가 실패해도 할당 ID를 유지한다. 재시작 때 같은 source와 계획을 재사용한다.
일반 접수 정보로 Material Usage.Verified 또는 Exam.Scope Confirmed를 설정하지 않는다.

canonical entity/binding 생성 **전**에는 새 입력 요청으로 과목을 바로잡을 수 있다.
이미 생성된 뒤에는 일반 intake 요청이 source·entity·Usage의 Course를 변경할 수 없다.
첫 묶음은 이 post-canonical 과목 정정 자동화를 제공하지 않는다. ‘과목 연결 수정 필요 · 자동
수정 미지원’과 원본/현재 과목/관련 수업 링크를 보여주며, 새 과목 선택을 완료 동작으로 제시하지
않는다. 별도 정정 절차가 구현되기 전에는 담당자 점검 대상으로 둔다. 다른 폴더로 이동시키거나
새 파일로 재업로드하면 해결된다고 안내하지 않는다. 기존 참조·USER 내용은 보존한다.

### 3.3 원본 이동의 경계

첫 지원은 **같은 비공개 My Drive 안의 등록된 학기 Inbox → 등록된 보관 루트**다.
shared drive 간 이동, 소유권 변경, 권한 확대, 임의 폴더 재귀 정리는 지원하지 않는다.
원본은 file ID를 유지하는 parent 변경으로 이동한다. 다른 파일 덮어쓰기·복사 후 삭제는 하지 않는다.

이동 키는 `sha256(canonical_json(["intake.move.v1", provider, file_id, original_parent_id,
target_parent_id, source_hash, plan_revision]))`다. 모든 hash는 UTF-8 정렬 키 JSON,
`ensure_ascii=false`, compact separators `(',', ':')`, 비유한 수치 금지의 동일 직렬화로 계산한다.
plan_revision은 영속 receipt hash다.

이동 전 file ID·현재 hash·parent·소유 drive·대상 루트·권한을 재조회한다.
원래 parent이면 이동하고, 이미 정확한 target이면 성공으로 조정하며, 다른 parent이면
‘파일 위치 변경 확인 필요’로 중단한다. 성공 후 ID/parent/접근 가능성을 readback해 기록한다.
성공 응답 유실 시 같은 검사를 다시 한다. 파일 내용이 바뀌면 과거 계획을 적용하지 않는다.
처리 중 사용자 이동을 강제로 되돌리거나 또 다른 장소로 이동시키지 않는다.

최초 설정에서 허용할 Inbox/보관 루트와 원본 이동 방식을 사용자에게 보여주고 연결한다.
구현 수용 테스트에서 private 시험 폴더의 ID·접근권·재시도 보존을 확인한 뒤 활성화한다.
권한 검증 실패 파일은 원위치에 두고 ‘정리 대기’로 표시한다. 공개 공유를 추가하지 않는다.

### 3.4 새 PDF Material의 예약·생성

첫 묶음의 PDF는 기존 Material에 추측 연결하지 않고 신규 Material을 만든다. 같은 provider/file ID가
이미 APPLIED이면 기존 연결을 재사용한다. 다른 파일을 같은 자료의 새 판본으로 대체하는 작업은
이 입력 계약에 포함하지 않는다. 그 경우 기존 revision 확인 경로로 보류한다.

`reserve_material_entity(validated_plan)`은 §4.1의 Session과 동등한 Course별 **Materials 전체
provider inventory gate**를 거친다. 등록한 Materials 원본 data_source membership, 정확한 Course,
strict M app ID를 검증하고 모든 페이지네이션을 읽는다. 중복/잘못된 ID·조회 실패는 생성 전 중단한다.
로컬 할당과 대조해 BEGIN IMMEDIATE로 최대 sequence 이상을 seed하고 receipt/source별 PENDING
예약을 만든다. 예: M01/M07, 빈 로컬 원장 → 다음 M08 이상. 신규 입력에서 legacy allocate_entity를
호출하거나 이때 canonical_entity_id를 쓰지 않는다. source당 활성 예약은 하나다.

예약 snapshot에 다음 creation payload를 고정한다. 생성 전 필요한 property/Select/Status와
authority mapping이 설정과 일치하는지 검사하며, 없으면 설정 확인 대기다. 실행 중 임의 enum을
추가하지 않는다. 새 기본값은 §9 C3의 다음 버전 초기 설정에 포함한다.

| Material 필드 | 첫 생성 값과 소유 근거 |
| --- | --- |
| parent / ID / Course | 등록 Materials data_source / 예약 strict M ID / 검증된 Course page ID 하나 |
| Name / Original Filename | receipt에 고정한 원래 표시 파일명 전체(제목만으로 다른 개체를 식별하지 않음) |
| Type | USER 자료 역할 강의자료 → Lecture Slides, 교과서 → Textbook; 각각 professor_material / supplemental_reference mapping 검증 |
| Source Folder | 등록된 비공개 보관 루트 안, 예약 entity의 raw 폴더를 생성·readback한 URL |
| Text Status / Text Source | Pending / Unavailable; 추출 결과를 검증하기 전 읽기 완료로 표시하지 않음 |
| Visual Dependency / AI Priority | Unknown / Normal; Unknown은 새 설정 옵션이며 시각 정보가 없다는 뜻이 아님 |
| Current Source Version | 원장에 등록한 현재 관측 source version; 신규 source는 1 |
| 그 외 선택 필드 | 생성 시 비움; Page Count·Normalized Source 등은 실제 정규화 검증 후 별도 기록 |

raw 폴더 준비도 외부 mutation 단계이며 §4.3의 write-attempt 원장과 응답 유실 회복을 적용한다.
최초 설정에서 등록·검증한 Course 보관 루트를 시작점으로, 새로 필요한 entity/raw/derived 등
각 하위 폴더를 부모부터 생성한다. 각 단계는 다음 **provider에 검색 가능한 복구 identity**를
생성 payload 자체에 포함한다. 이름만 같은 폴더를 예약의 결과로 채택하지 않는다.

```text
folder_key = lowercase_hex(SHA256(canonical_json([
  "intake.folder.v1", provider, provider_account_binding_id, parent_folder_id,
  reservation_id, source_file_id, entity_app_id, folder_role
])))
Drive files.create payload: appProperties = {"uls_folder_key": folder_key}
```

folder_role은 해당 예약 트리 안에서 유일한 상대 역할(예: entity/raw/derived)이며 원장에 고정한다.
원장은 marker/payload hash/부모 ID/예상 MIME/동작 키와 create 시도 여부·회수한 provider ID를
호출 전에 commit한다. marker는 SYSTEM 의미의 불변값이며 provider 자체의 변경 불가 속성이라는
뜻은 아니다. 변경/삭제/다른 부모/권한은 검증 실패다. appProperties 읽기/검색은 최초 연결과
같은 OAuth 앱·계정 결속에서만 수행한다. 결속/접근 검증 실패를 폴더 부재로 해석하지 않는다.

최초 호출 전과 회복 때 marker의 **전체 검색 결과**를 읽는다. 0개이고 이전 create 시도가 없으면
생성 가능, 정확히 1개면 그 marker·folder MIME·parent·등록 루트·접근권을 readback해 같은 예약에
결속하고 재사용, 2개 이상이면 임의 선택/삭제 없이 reconcile 대기다. create 시도 후 0개 또는
검색/응답 불확실은 성공 부재의 증거가 아니므로 즉시 재생성하지 않는다. 자식/Material 생성과
원본 이동은 중단한다. provider ID를 이미 회수했으면 해당 ID의 직접 readback도 대조한다.
이 규칙은 raw만이 아니라 이 경로가 만드는 모든 중간 폴더에도 적용한다. 응답 유실 전 성공한
부모를 이름으로 새로 만들지 않으며, 각 부모 ID 확정 후에만 자식 payload를 고정한다.

이 기능은 **worker용 provider-neutral 폴더 복구 포트**로 실행한다. 다음 버전 §13 DriveAdapter의
명시적 확장(C2)은 (a) 생성 요청에 marker를 함께 기록하는 create, (b) 같은 계정/앱 결속의 exact
marker 전체 페이지 검색, (c) provider ID 직접 metadata readback을 제공한다. 검색 결과는
complete-zero / exactly-one / multiple / lookup-indeterminate를 구분한다. 페이지 일부 성공을
complete로 반환하거나 조회 오류를 빈 목록으로 바꾸지 않는다. readback 결과에는 검증에 필요한
ID/marker/MIME/parent/루트·접근 정보가 포함된다. SDK와 Google 쿼리 문법은 adapter 내부에 둔다.
worker/core는 이 포트를 사용하며 복구를 위해 provider SDK를 우회 호출하지 않는다. 이 쓰기/검색
운영 포트는 read-only MCP 도구로 노출하지 않는다. 인터페이스 이름보다 위 결과 의미가 규범이다.

원본 파일은 아직 Inbox에 있고 이동은 REGISTERED 이후다. Source Folder가 있다는 사실만으로
원본 이동 완료나 읽기 완료를 표시하지 않는다. 동일 생성 재시도는 예약 ID와 payload를 재사용한다.
생성 직전 Course+app ID 전체 조회에서 0개이고 이전 create 시도도 없을 때만 새로 생성한다.
1개면 이미 시작한 동일 예약의 생성 증거와 아래 snapshot을 검증해 회수하며, 그 증거가 없으면
외부 ID 충돌로 멈춘다. create 시도 후 0개는 응답 유실이므로 즉시 재생성하지 않고 reconcile한다.
생성 응답 후·응답 유실 회복은 **정확히 하나**를 요구한다. 둘 이상은 항상 충돌이다.
data_source/page ID/ID/Course/Type/원본명/Source Folder/초기 상태·version을 snapshot과
대조한다. 원본 file ID/hash/version·현재 parent와 receipt도 다시 확인한다. 중복/불일치/조회 불능은
RECONCILE_REQUIRED 또는 확인 대기이며 임의 페이지 선택·재생성·USER 값 덮어쓰기를 하지 않는다.

전체 검증 후 `apply_material_binding(handle, verified_readback)`이 원장 트랜잭션에서만
canonical_entity_id와 APPLIED를 확정한다. 그 전에는 REGISTERED/ORGANIZED로 진행하지 않는다.
PENDING/APPLIED/RECONCILE_REQUIRED/RELEASED와 번호 비재사용, 무쓰기 증거에 한한 해제,
성공 산출물 보존·응답 유실 후 보수적 회복은 §4.3과 동일하다. 최초 확정 이후 재처리는 Name 등
USER 편집을 creation snapshot으로 되돌리지 않는다. 유형·권한 변경은 현재 근거 검증에서 다룬다.

## 4. 새 수업과 기존 수업의 연결

사용자는 동일 과목 안에서 `새 수업` 또는 정확한 `기존 수업`을 선택한다.
기존 수업의 날짜·제목·차시를 보여주고, 입력 날짜가 다르면 연결하지 않고 확인을 요청한다.
제목/날짜가 같아도 자동 병합하지 않는다. 교차 과목 선택·삭제된 수업은 오류다.

### 4.1 새 수업

신규 할당 전에 Course별 **provider Session 목록 검증·ID reserve/seed 완료** gate가 필수다.
모든 페이지네이션을 읽어 provider page ID·Course·유효 Session app ID를 검증하고,
중복/잘못된 ID·권한/조회 실패가 있으면 `기존 수업 확인 대기`로 멈추며 ID를 소비하지 않는다.
완전한 목록과 로컬 할당을 대조한 뒤 BEGIN IMMEDIATE로 현재 최대 sequence 이상으로
카운터를 seed하고 inventory fingerprint/검증 시각을 기록한다. 단일 source의 기존 binding
reserve만으로 이 Course 전체 bootstrap을 대체하지 않는다.

각 신규 할당 직전에 목록의 현재성을 다시 확인해 새 외부 Session을 reserve한다.
예를 들어 provider에 S01/S07만 있고 로컬이 비어 있으면 bootstrap 전 할당은 금지하고,
완료 뒤 다음 ID는 S08 이상이다. provider 생성 직전/직후 같은 app ID의 정확한 대상을
조회한다. Notion에는 유일성 제약이 없으므로 외부 편집과 경합해 중복이 생기면 자동 합치거나
삭제하지 않고 `ID 충돌 · 부분 반영`으로 중단한다. 이런 경합을 원자적으로 막았다고 주장하지 않는다.

이 gate를 통과한 새 입력 경로는 **`reserve_session_entity(validated_plan) -> reservation_handle`**을
호출한다. 현행 `allocate_entity`를 이 신규 요청 경로에서 호출하지 않는다. 예약 API는
BEGIN IMMEDIATE로 해당 receipt/source의 기존 활성 예약을 재사용하거나 다음 sequence를
소비해 PENDING을 기록한다. `source_files.canonical_entity_id`는 여기서 쓰지 않는다.
같은 요청의 재시도는 같은 예약/ID다. 새로 할당하는 번호는 provider inventory와 충돌하지 않는다.
Session 생성 요청/응답 유실 시 Course+app ID로 조회해 재사용하며 중복이면 중단한다.
UI에는 생성 완료 전 ‘등록 중’으로 표시한다.
다음 버전의 이 신규 생성 경로도 §4.3의 예약 lifecycle을 사용한다. 할당한 번호는 PENDING
계획이 소유하고 최종 source/entity 연결은 전체 readback 뒤 APPLIED로 확정한다.
안전하게 예약을 해제하더라도 소비한 신규 번호를 재사용하지 않는다. 기존 완료된 source-bound
할당의 멱등성은 유지하며, 예약 지원 자체는 C2의 명시적 allocator 확장이다.

NEW의 provider creation snapshot은 다음과 같다. 이 표 외의 입력을 추가 요구하거나 파일명에서
수업 제목/차시를 추측하지 않는다. 예약할 때 결정된 값을 원장에 고정해 재시작에도 재사용한다.

| Session 필드 | 첫 생성 값과 소유 근거 |
| --- | --- |
| parent / ID / Course | 등록 Sessions data_source / 예약 strict S ID / 검증된 Course page ID 하나 |
| Session No | USER가 명시한 실제 차시만 기록; 모르면 null. 다음 버전에서 필수→선택으로 명시 변경(C3) |
| Date | USER가 입력한 실제 날짜 YYYY-MM-DD; 시간대 변환으로 날짜를 바꾸지 않음 |
| Name | SYSTEM 초기값 `YYYY-MM-DD · 수업 SNN` (예: `2026-03-06 · 수업 S08`); 이후 USER 편집 보존 |
| Status | 고정 초기값 `Not started`; 개인 복습 완료와 무관하며 이후 USER 편집 보존 |
| Recording Status | 첫 생성 `Pending`; 정규화 결과 검증 후 실제 Ready/Partial/Needs Review/Failed로 갱신 |
| Normalized Transcript | 첫 생성 비움; 같은 예약의 검증된 normalized SOURCE 포인터만 후속 기록 |
| 그 외 선택 필드 | 첫 생성 비움; 강의 주제/녹음 폴더를 추측하지 않음 |

`Not started`/`Pending` 등 초기 property 옵션은 C3 초기 설정에서 준비하고 존재를 먼저 검증한다.
새 Session 생성 → 검증된 전사 정규화/포인터 게시 → §4.2 전체 tuple readback → APPLIED 순서다.
생성 payload만의 readback으로 최종 source binding을 확정하지 않는다. 생성과 SOURCE 포인터 쓰기는
별도 durable 단계다. 응답 유실은 동일 ID/page를 회수하며, 최초 creation snapshot 불일치는 확인
대기로 멈춘다. 이미 생성된 페이지의 Name/Date/Status를 후속 전사 처리에서 재작성하지 않는다.
이 신규 단계별 경로는 현행 TranscriptNotionWriter의 생성 기본값을 암묵적으로 호출하지 않는다.

내부 S ID sequence와 학술 차시는 별개다. S08 예약을 해제한 뒤 실제 8차시를 넣으면
ID=S09 이상, Session No=8이다. 실제 차시가 없으면 날짜와 정확한 page/ID로 접근하며
`9강` 같은 번호별 별칭을 S09에서 자동 생성하지 않는다. 다음 버전 resolver의 ‘N강’은 명시적인
Session No 또는 USER alias만 사용하고, S ID는 exact ID로만 해석한다. 같은 차시가 여러 개면
정확한 수업을 선택하도록 후보를 반환한다. 이 resolver 변경도 C2/C3에 포함되며 legacy
ID suffix→차시 fallback을 새 입력 수업에 적용하지 않는다. 필수 차시를 새로 묻는 대신
nullable Session No를 채택한 이유는 날짜만 아는 전사도 안전하게 등록하기 위해서다.

### 4.2 기존 수업 — 명세 변경 BIND_EXISTING_TRANSCRIPT

현재 caller의 `entity_id`를 믿는 방식으로 allocator를 우회하지 않는다.
새 내부 포트 `bind_existing_transcript(validated_plan)`을 **worker에만** 추가한다.
외부/MCP 입력은 plan이나 privilege 객체를 직접 만들 수 없다. 계획 생성기는 receipt와
현재 provider 기록을 읽어 다음을 검증한다.

1. 선택된 Notion page의 parent는 설정에 등록한 **Sessions 원본 data_source_id**여야 하며,
   정확한 Course 하나와 strict parser 기준 entity type `S`의 app ID, 실제 Date가 일치해야 한다.
   이는 DB membership와 app ID의 종류 검사다. 새로운 Notion `Type` 속성을 추가하지 않는다.
   `Session ID` 입력 문자열만으로 선택하지 않는다.
2. source가 다른 Course/entity에 묶여 있지 않다. 기존 수업이 다른 전사 source에 묶여 있으면
   자동 교체하지 않는다. 분할 전사/다른 file ID로 전사 교체는 이번 묶음에서 확인 대기로 둔다.
3. 로컬 `session_source_bindings`의 활성 예약/연결은 `(course_key, session_id)`와
   `(provider,file_id)` 각각 유일하다. BEGIN IMMEDIATE 안에서 source 등록, **PENDING 예약**,
   ID 카운터 seed를 함께 commit한다. 같은 계획이면 재사용한다. 다른 활성 연결은 충돌이며,
   PENDING 회복은 §4.3의 증거 조건을 만족한 경우에만 별도 수행한다.
4. 기존 수업의 ID는 이미 사용 중인 것으로 reserve하고 후속 신규 할당이 충돌하지 않게 한다.
   기존 Session 목록의 ID와 로컬 할당을 최초 동기화 때 대조한다. 명세상 허용되지 않는
   임의 외부 ID/중복 ID는 조용히 고치지 않고 설정 오류로 표시한다.
5. provider 대상/receipt를 쓰기 직전에 재확인한다. 로컬 연결 commit 후 provider 쓰기 실패는
   같은 binding으로 재시도한다. 성공 readback은 receipt의 **provider page ID + Course identity
   + Sessions 원본 data_source_id + strict S app ID + 실제 Date + 새 SOURCE 전사 포인터** 전체가
   현재 대상과 일치해야 한다.
   포인터만 맞아도 다른 필드가 변경됐으면 `대상 변경 감지 · 부분 반영`으로 멈추고 완료/Ready를
   기록하지 않으며 후속 정리·생성을 시작하지 않는다. 이미 수행된 포인터 쓰기를 몰래 되돌리거나
   USER 관계/날짜를 원래 값으로 덮어쓰지 않는다. 새 수업 생성 readback에도 같은 검사를 적용한다.

기존 `allocate_entity`의 완료된 source-bound 할당 동작은 그대로 두고 새 입력 worker의 호출
경로에서 제외한다. 새/기존 수업 연결 중에는 내부 worker가 현재 receipt에 결속된 예약 handle로만
PENDING 대상 ID를 사용한다. 이미 APPLIED이면 해당 canonical ID를 그대로 재사용한다.
일반 caller가 PENDING 예약을 인자로 주장해 ID 검사를 건너뛸 수 없다.
`ingest_transcript`의 caller consistency 검사와 worker의 원본 freshness 검사는 유지한다.
새 원본을 이미 바인딩된 수업에 강제로 끼워 넣는 기능은 제공하지 않는다.
이미 있는 USER 필기·수업 제목·날짜·복습 상태는 전사 연결로 덮어쓰지 않는다.

### 4.3 연결 예약의 실패 후 회복

신규 생성과 기존 수업 연결의 binding lifecycle은 `PENDING → APPLIED`, `PENDING → RECONCILE_REQUIRED`,
`PENDING → RELEASED`다. RELEASED 이력은 보존하고 활성 유일성 대상에서만 제외한다.
APPLIED는 일반 입력으로 해제/교체할 수 없다. 예약 receipt ID, plan hash, source/target snapshot,
외부 쓰기 단계·동작 키·시도 시작/응답/readback 증거를 기록한다.

모든 관련 외부 mutation **호출 전에** `write_attempt_started`를 durable commit한다.
따라서 크래시로 호출 여부가 불명확하면 '안 썼다'고 추측하지 않는다. 마지막 pre-write 검사에서
대상이 바뀌었고 어떤 외부 mutation도 시작하지 않았음이 원장으로 증명되면, 새 검증 요청이
동일 Course 내에서 PENDING을 RELEASED로 바꾸고 새 대상을 예약할 수 있다. 두 동작은 단일
로컬 트랜잭션이며 source identity는 유지한다. provider에서 사용 중인 Session ID 예약/카운터는
되감지 않는다. `source_files`에 최종 entity binding을 확정하는 시점은 APPLIED다.
`apply_session_binding(reservation_handle, verified_readback)`은 현재 예약/receipt/hash와 전체
provider tuple의 검증 증거를 재확인한 로컬 트랜잭션에서만 canonical_entity_id를 기록하고
APPLIED로 전환한다. 예를 들어 S08 예약을 mutation 전에 안전하게 RELEASED하면 canonical은
null이고 번호는 소비된 상태다. 다음 새 요청은 S09 이상을 예약하며, 성공 readback 이후에만
canonical=S09가 된다. 이전 예약 handle은 다시 사용할 수 없다.

쓰기 시도 기록이 하나라도 있거나 응답이 유실되면 RECONCILE_REQUIRED이며 자동 release는
금지한다. 같은 source/대상/동작 키를 readback해 모든 단계의 결과를 확인한다. 정확한 대상 tuple과
포인터가 맞으면 APPLIED로 조정한다. provider 연결 mutation이 발생하지 않았음이 확정되고
다른 성공 단계가 있다면 그 산출물을 미완료 이력으로 보존·현재 근거에서 제외한 뒤에만 새 요청의
예약 교체가 가능하다. 실패 응답만으로 부재를 단정하지 않는다. 증거 불충분·다른 대상 포인터는
‘연결 확인 필요’로 보류하고 담당자 점검 경로를 제공한다. 원본/USER 내용을 자동 복원하지 않는다.

Notion 입력 화면은 ‘연결 예약됨’, ‘연결 반영됨’, ‘연결 확인 필요’를 구분한다. 안전한 예약 해제가
가능하면 새 수업 선택 요청 링크를 제공하고, 불명확한 결과에는 재선택이 완료 가능한 것처럼
표시하지 않는다. post-canonical Course 변경 미지원과 동일 Course의 미완료 예약 회복은 별개다.

## 5. 사용 범위와 사람 승인

범위 입력 초안은 `UNKNOWN / BOUNDED / WHOLE` 중 하나이며 기본 UNKNOWN이다.
이는 **입력 요청의 초안 상태**다. 기존 PageRange의 None/None 의미를 바꾸지 않는다.

| 사용자 선택 | 유효성 | 정식 승인 제안 |
| --- | --- | --- |
| 미확인 | 페이지 값 없어도 됨 | 전체 사용 제안을 만들지 않음; 전사만 학습 가능 |
| 지정 페이지 | PDF 기준 정수 start/end, 1 ≤ start ≤ end ≤ 확인된 페이지 수 | 명시한 범위에만 생성/갱신 제안 |
| 전체 자료 | ‘이 수업에서 전체 자료 사용’ 명시 선택 | whole-source 의도 포함 제안; 확인 후만 None/None 사용 |

자료 관계만 고르거나 칸을 비웠다고 WHOLE로 바뀌지 않는다. 인쇄 쪽수와 PDF 위치가 다르면
검증된 변환 범위를 보여주고, 미확인 쪽수는 후보로 남긴다. 페이지 수/이미지 해석이 불완전하면
정확히 검증 가능한 범위만 제안하며 Partial을 Ready로 올리지 않는다.

입력 요청을 받은 producer는 §5.2의 정확한 Usage 대상·Role·Session·Material 버전·범위·근거의 불변 제안을
Automation Queue에 생성한다. 사용자는 제안 페이지의 한국어 요약을 읽고 `Decision`을
`Approve/Reject`로 고른다. UNKNOWN이면 이 승인 제안 자체가 없다.
범위를 고치려면 새 입력 요청을 만들고 새 제안을 받는다. 승인된 payload를 인라인 변경하지 않는다.

이후 기존 ApprovalReader/HumanApprovalApplier가 attribution·현재 fingerprint·대상·관계와
범위를 검증한다. UI는 `내 승인 선택됨 → 반영 대기 → 적용됨`을 구분한다.
stale/revoked/deleted/범위 변경이면 `새 확인 필요` 또는 `실패`이며 과거 승인을 재사용하지 않는다.
시험 범위 확인과 별개의 승인이다. AI·일반 worker가 Decision을 만들어내지 않는다.

### 5.1 새 범위 입력·거절과 기존 적용 결과

§5.2의 검증된 `usage_slot_key`마다 로컬 `range_intent_heads`에 가장 최근에 수신한 유효 입력의
`request_id`, 단조 증가 `intent_generation`, 정확한 현재/제안 Usage target을 기록한다.
같은 입력 요청 재시도는 세대를 올리지 않는다.
head에는 `active`와 비활성 사유도 저장한다. 새 유효 요청 수신은 active=true로 만들며,
UNKNOWN의 active는 ‘현재 입력’만 뜻하고 승인 가능한 proposal의 존재를 뜻하지 않는다.
UNKNOWN도 명시적으로 제출하면 새 입력 세대다. 새 유효 입력 수신 시 이전 세대의
`PENDING_REVIEW` 또는 `APPROVED` 제안만 `SUPERSEDED`로 종료한다. `REJECTED`, `APPLIED`,
기존 `SUPERSEDED` 등 terminal State와 사람 Decision 이력은 보존한다. 이미 mutation을
시작한 제안은 상태를 단정하지 않고 기존 readback/부분 반영 회복을 먼저 수행한다.
범위 입력을 처리하는 coordinator는 같은 대상의 새 제출/취소를 수신·검증한 후 승인 적용을
처리한다. HumanApprovalApplier의 기존 검사에 더해 **현재 입력 세대 일치**를 실행 직전 확인하며,
이 검사를 할 수 없으면 적용하지 않는다. 이 추가 freshness 입력은 §9 C5의 명세 개정 대상이다.
다른 문구를 표시하는 것만으로 기존 승인 코드를 우회하지 않는다.

현재 원 요청의 취소·제출 철회·USER 입력 hash 변경을 관측하면 해당 head를 active=false로
바꾸고 사유를 기록하며 **앞서 열거한 적용 가능 상태의 제안만 SUPERSEDED**로 종료한다. HAA는 §5.3의 ID에 결속된
세대 일치뿐 아니라 head active, 원 요청의 submitted/not-cancelled, receipt USER hash 일치도
실행 직전 요구한다. active=false인 과거 세대는 새 요청 없이 다시 활성화하지 않는다.
이전 세대 요청의 뒤늦은 취소는 그 제안에만 영향을 주며 더 최신 head를 비활성화하지 않는다.
이미 APPLIED된 Usage와 그 승인 이력은 취소로 되돌리지 않는다. 이를 참조한 유효한 노트도
단순 요청 취소로 stale이 되지 않는다. mutation이 이미 시작됐으면 §2.2의 readback/부분 반영
규칙으로 실제 결과를 확인하고 ‘취소 완료·변경 없음’으로 표시하지 않는다.

새 제출이 아직 worker에 수신되기 전에는 ‘범위 입력 제출됨 · 이전 제안 갱신 대기’라고 표시한다.
‘미확인으로 유지’는 실제 반영 확인 후에만 표시한다. 이미 승인 선택을 했어도 아직 적용 전인
이전 세대는 새 세대와 함께 적용하지 않는다. §2.2의 외부 쓰기 경합 제한은 동일하게 적용한다.

`Decision=Reject`는 **해당 변경 제안만** 거절 종료한다. 유효한 기존 Usage가 있으면
‘변경 제안 거절됨 · 기존 확인 범위 유지’라고 표시하고, 최초 제안이면 ‘제안 거절됨 · 범위 미확인’이다.
UNKNOWN 제출이나 새 제안 거절은 이미 적용된 Usage의 Verified를 철회하지 않는다.
기존 적용 결과와 새 입력/제안 상태를 별도 표시한다. 실제 확인 철회는 사람의 명시적 별도
확인 변경 경로로 수행하며, 단순 범위 초안으로 이를 대신하지 않는다.

같은 poll에서 발견한 미수신 USAGE_RANGE는 전체 페이지네이션과 필드 검증을 마친 다음,
`(Notion created_time UTC, Notion page ID의 정규형)` 오름차순으로 claim한다. page ID는 동률
tie-breaker다. USER/worker 편집의 `last_edited_time`을 정렬 키로 쓰지 않는다. 이 순서는
사용자의 실제 클릭 시각을 추측하지 않는 **수신 순서**이고, UI에는 채택한 요청과 수신 시각을 표시한다.
이미 claim된 요청은 뒤로 되돌리거나 재정렬하지 않는다. 늦게 제출된 오래된 초안은 새 poll에서
수신되면 그 시점의 새 세대가 된다. 세대 부여·head 갱신·이전 미적용 제안 outbox 무효화는
로컬 트랜잭션으로 저장하고, 승인 적용은 그 poll의 새 입력들을 모두 처리한 뒤 수행한다.
pagination/검증 중 접근 실패한 대상은 완료된 목록처럼 취급하지 않고 해당 slot 승인을 보류한다.

### 5.2 Usage 역할·생성·갱신의 정확한 대상

‘자료 역할’(강의자료/교과서)과 **그 수업에서의 사용 역할**(Primary/Supporting/Reference)은
다른 필드다. CREATE에서는 사용자가 ‘주자료 / 보조자료 / 참고자료’ 중 하나를 명시 선택하고
proposal 요약에도 Role을 넣는다. 기본은 미선택이며 Material.Type을 Role로 자동 복사하지 않는다.
UPDATE는 정확한 Material Usage Relation 하나를 선택하고 provider의 현재 Role을 보존한다.
수업·자료·Role 변경은 PAGE_RANGE 갱신에 끼워 넣지 않는다.

첫 자동 입력 묶음은 `(정확한 Course page ID, Session page ID, Material page ID, Usage Role)`
한 조합에 활성 Usage 하나만 관리한다. 각 page의 app ID와 Course 관계를 현재 provider에서
검증하고, 해당 tuple의 canonical JSON hash를 `usage_slot_key`로 사용한다. 이름/문자열 입력을
provider identity 대신 쓰지 않는다. 같은 slot에 이미 여러 Usage가 있으면 자동 병합/임의 선택
없이 관계 정리 확인 대상으로 둔다. 다른 Role의 Usage는 다른 slot이다.

- **CREATE:** 현재 slot의 Usage가 없을 때만 허용한다. BOUNDED/WHOLE의 정확한
  `target_entity_id`는 기존 `derive_usage_id(session_app_id, material_app_id, role, page_range)`로
  정한다. 완전한 `create_usage` semantics를 기존 builder로 만들며 Proposal Type은 MATERIAL_USAGE다.
  old snapshot의 부재, Role·Course·source class·버전·근거·범위를 포함해 사람이 확인한다.
  생성 response 유실은 그 target ID와 전체 semantics의 정확한 readback으로만 회복한다.
- **UPDATE:** 선택한 Usage의 provider page ID + app ID, Session/Material/Role과 slot 일치를
  재검증한다. 현재 canonical ID를 유지하는 `update_range` semantics/Proposal Type PAGE_RANGE를
  만들고 old snapshot을 포함한다. 표시된 Role을 바꿔 새 Usage처럼 만들지 않는다.
- **UNKNOWN:** slot/역할까지는 검증하지만 Usage를 만들거나 변경하지 않는다. canonical Usage가
  아직 없으면 target도 없음으로 표시한다. 이 경우의 slot head는 pre-canonical 입력 상태이며
  승인 가능한 가짜 Usage가 아니다. 이미 있는 Usage는 그대로 보존한다.

head에는 검증된 identity tuple, 현재 Usage page/app ID(있을 때), 현재 제안의 target ID와
operation을 저장한다. CREATE 적용 후 provider readback으로 현재 Usage identity를 같은 slot에
결속한다. 이후 입력은 UPDATE여야 한다. CREATE를 재선택했는데 기존 Usage가 이미 있으면
‘기존 사용 관계 변경 요청을 여세요’라고 안내하며 새 Usage를 중복 생성하지 않는다.
모든 상태에서 기존 HAA의 완전한 semantics·attribution·source/관계 freshness 검사를 유지한다.

### 5.3 제안 ID와 새 입력 세대의 결속

다음 버전의 USAGE_RANGE는 action semantics와 별도로 **세대가 결속된 proposal identity v2**를
사용한다. `_ACTION_KEYS`를 임의 추가하거나 processor_version/review_reason에 nonce를 숨기지 않는다.
기존 builder의 완전한 canonical action semantics는 그대로 검증하고, producer와 HAA가 공유하는
다음 새 envelope로 ID를 계산한다. 직렬화는 §3.3과 같다.

```text
generation_token = [usage_slot_key, request_id, intent_generation]
proposal_id = SHA256(canonical_json([
  "uls.usage-proposal.v2", proposal_type, canonical_action_semantics, generation_token
]))
```

Queue `Proposal ID`와 outbox의 저장 문자열은 위 digest의 **64자리 소문자 hexadecimal** 그대로다.
타입 prefix·공백·대문자·base64·raw bytes 표현은 허용하지 않는다. producer/Reader/HAA/recovery는
모두 이 exact 형식을 재계산·비교한다. Proposal Type은 별도 속성과 hash 입력에 이미 결속된다.

동일 receipt 재시도는 같은 token/ID다. 새 요청 세대는 action이 완전히 같아도 새 ID다.
Role/target/source/old snapshot/desired range 등 action의 기존 의미와 검사는 완화하지 않는다.
token은 local receipt/head로 검증하며 caller/Queue의 표시 속성만 믿지 않는다.
wire format은 다음 **한 가지**만 허용한다. Queue의 기존 `Proposed Action` Rich text에는
기존 canonical action JSON만 저장한다. 새 SYSTEM 소유 **`Proposal Envelope` Rich text**에는
아래 JSON을 §3.3 직렬화로 저장한다. 동일 bytes/action/hash/envelope를 local proposal outbox에
영속 저장한 뒤 Notion에 게시하며, provider readback을 통과해야 사람 검토 대상으로 표시한다.

```json
{"schema":"uls.usage-proposal.v2","usage_slot_key":"<64자리 소문자 SHA256>","request_id":"<정규형 Notion page UUID>","intent_generation":1}
```

예시의 표기는 설명용 placeholder다. 실제 값은 검증된 slot/receipt다. key는 위 네 개만 허용하며
중복/미지/누락 key, bool·float·문자열 generation, 1 미만 generation을 거부한다. request_id는
소문자 하이픈 UUID 정규형, hash는 소문자 hex 64자리다. Rich text 여러 segment는 순서대로
plain_text를 합쳐 읽고 canonical JSON bytes와의 roundtrip 일치를 요구한다. provider의 자동
서식은 의미값이 아니며 wrapper나 action에 envelope 필드를 섞는 다른 형식은 거부한다.
Reader/HAA는 action의 기존 strict validator와 envelope validator를 각각 통과시켜 동일 ID를
재계산하고, Queue ID·로컬 불변 outbox·현재 active head·원 요청 hash와 모두 대조한다.
action/token/ID/형식 변조나 로컬 원장 부재는 적용 거부다. Notion 속성 숨김은 보안 수단이 아니다.
사람 Decision의 attribution/freshness도 이 정확한 새 제안에 대해 다시 받아야 한다.
옛 REJECTED/SUPERSEDED row의 Decision/State를 새 제안으로 되살리거나 복사하지 않는다.

v2 포맷과 Reader/HAA 지원은 C5의 명시적 계약 변경이다. 다음 버전의 모든 MATERIAL_USAGE/
PAGE_RANGE 적용 진입점은 동일한 v2 guard를 사용하며 legacy v1 ID로 fallback하지 않는다.
미적용 v1 제안은 ‘새 제안 필요’로 보류하고 새 요청과 사람 확인으로 v2를 만든다.
이미 APPLIED된 v1 Usage는 현재 권한·source·관계 검사를
유지하며 보존한다. 다른 Proposal Type의 기존 identity는 변경하지 않는다.

## 6. 학습 노트 생성·저장·갱신

### 6.1 입력과 실행

세션의 `학습 요청` 연결 뷰에서 새 요청 템플릿을 연다. 기본 근거 모드는 **전사만**이고,
선택 모드는 `전사+확인된 강의자료`, `임시 자료 포함`이다. 임시 모드는 자료를 명시 선택한다.
첫 confirmed 선택 정책 `confirmed-lecture.v1`은 정확한 Session의 현재 Verified Usage 중,
검증된 source class가 professor_material이고 Material.Type이 Lecture Slides 또는 Professor Notes인
항목 **전체**다. Usage Role은 Primary/Supporting/Reference 모두 포함한다. Type→authority mapping과
현재 Course/source/승인 freshness를 검증하며 실패한 항목을 정상 근거로 넣지 않는다.
Syllabus, Textbook, Reference, Supplementary 및 supplemental_reference는 자동 포함하지 않는다.
동일 자료의 여러 유효 범위는 Usage별 identity를 유지하고 읽을 때만 겹친 구간을 합칠 수 있다.
정렬은 `(Material app ID, Usage app ID, provider page ID)` 오름차순이며 role 우선순위를 추측하지 않는다.
교과서를 추가하려면 첫 묶음에서는 `임시 자료 포함`에서 정확한 Material을 선택한다. 이 모드는
선택한 자료의 현재 확인/미확인 상태를 그대로 표시하며 Verified를 바꾸지 않는다. 확인된 강의자료도
함께 원하면 같은 명시 목록에 넣는다. 자유문에 책을 언급한 것만으로 근거 목록을 자동 확대하지 않는다.
해당 모드가 임시 근거를 허용하는 기존 retrieval 정책과 맞지 않으면 실행하지 않는다.
확인된 PDF가 없는데 ‘확인된 자료 포함’을 선택했으면 전사만으로 조용히 축소하지 않고
`자료 사용 확인 필요`로 둔다. 이미 동등한 준비 노트가 있으면 새 생성 대신 그 결과를 보여준다.

request에는 Session relation, 근거 모드, 선택 Material(임시 모드), 추가 학습 요청, 제출/취소가 있다.
worker는 §2.2의 immutable receipt를 만들고 내부 RetrievalEngine으로 허용된 context를 얻는다.
생성기는 발급된 capability의 허용 locator만 확장할 수 있다. 전체 Drive를 임의로 읽는 권한을
생성기에 넘기지 않는다. model-agnostic 입력은 원문 근거·부분 누락·구간·교수 SOURCE/USER/AI
출처를 보존한다. 이 작업을 검색 MCP의 mutating tool로 노출하지 않는다.

AI 연결이 준비된 경우에만 생성한다. 일시 실패는 같은 job 재시도이며 새 요청을 강요하지 않는다.
입력 부족·공급자 미연결·권한 문제는 생성 중과 구분해 필요한 행동을 표시한다.
사용자 취소 또는 source/관계 변경을 감지하면 결과를 현재 노트로 게시하지 않는다.

### 6.2 작업 identity와 freshness

Session provider page ID마다 durable `study_note_heads`에 current request_id, 단조 증가 generation,
active, 선택 mode/materials 및 receipt hash를 둔다. 새 유효 STUDY_NOTE를 전체 조회·검증한 뒤
§5.1과 같은 `(created_time UTC, 정규형 page ID)` 순서로 claim하고 마지막 수신 요청을 head로 한다.
동일 receipt 재시도는 세대를 올리지 않는다. 현재 요청 링크와 과거 요청별 상태를 별도로 표시한다.
늦게 제출된 오래된 초안도 수신 시점에는 새 요청이며, 이를 사용자의 클릭 순서로 포장하지 않는다.

각 request receipt는 독립 lifecycle이며 note_key는 공유 가능한 artifact/job identity다.
현재 active head만 Session의 current AI 영역을 publish/replace할 수 있다. publish 직전과
readback 뒤에 head generation·request hash·submitted/not-cancelled를 검사한다. 그 poll의 새
입력을 먼저 수신한 뒤 게시한다. 불일치한 성공 artifact는 AI 이력으로만 남기고 현재 노트는
교체하지 않는다. 쓰기 중 경합을 관측하면 부분 반영을 표시하며 USER 영역을 되돌리지 않는다.
교체 대기 동안 기존 current artifact는 마지막 검증 시각/근거 상태와 함께 보존한다.

R1 실행 중 R2를 제출해도 새 요청을 버리지 않는다. R2가 head가 된 후 R1이 늦게 끝나면
R1은 이력이고 R2가 현재 노트를 소유한다. 단일 active worker라도 이 정책은 재시도·재시작에
동일하게 적용된다. 동일 note_key를 공유하는 R1/R2 중 R1 취소는 R1만 끝내며 R2의 job을
취소하지 않는다. job의 실행 수요는 유효 request 참조로 판단한다. 현재 head 취소/편집/제출 철회는
active=false로 만들고 이전 head를 자동 부활시키지 않는다. 이미 게시된 유효 artifact는 취소만으로
철회하지 않는다. 새 요청으로 준비된 artifact를 재사용할 때도 현재 head/근거 검증 뒤만 게시한다.

노트 작업은 기존 source job 키를 임의로 확장하지 않고 다음 신규 비원본 작업 키를 사용한다.

```text
note_key = SHA256(canonical_json([
  "study-note.v1", course_key, session_id, evidence_manifest_hash,
  learner_request_hash, template_version, generator_config_version
]))
```

evidence manifest는 **이 요청에서 실제 선택한 evidence dependency set**만 포함한다.
정렬된 source provider/file ID/version/hash, locator·선택 범위, 정규화 버전, Session/Course identity,
근거 모드·누락/Partial/예산 잘림과 다음 모드별 dependency를 기록한다.

- 전사만: transcript/Session 의존성만 기록한다. 무관한 PDF/Usage 변경은 note_key/status를 바꾸지 않는다.
- 확인된 강의자료 포함: 해당 계획에서 선택한 verified Usage의 provider/app ID·Role·Verified·
  범위·적용된 승인 상태와 Material 의존성을 기록한다. `confirmed-lecture.v1`과 전체 eligible
  membership snapshot(읽기 예산 적용 전)을 보존해
  그 정책에 해당하는 자료의 추가/제외를 감지한다. 단순 대기 제안/거절/취소 이력은 적용된 Usage가 아니다.
- 임시 자료 포함: 사용자가 명시 선택한 Material과 실제 임시/미확인 상태를 기록한다.
  확인된 자료로 바뀌면 선택된 dependency가 달라졌음을 표시한다. 선택하지 않은 다른 자료는 무관하다.

선택했으나 읽지 못한 근거는 누락 dependency로 남겨 coverage를 Partial로 표시한다.
**선택된** 관계/범위/확인 상태가 바뀌면 다른 manifest다. 생성기 설정 버전에는 실제 provider/model/출력 의미에 영향을 주는 설정을 담고
자격증명은 포함하지 않는다. 동일 논리 요청은 하나의 job/artifact를 재사용한다.

실행·쓰기 직전·readback 후에 engine으로 freshness와 권한을 재검증한다.
모든 필요한 근거를 제공하지 못하면 coverage를 정확히 표시한다. ‘준비됨’은 요청한 범위를
충족한 산출물에만 사용한다. 일부 근거로 만든 유용한 결과는 `부분 노트`로 구별한다.

### 6.3 저장과 재시작

생성 결과는 worker가 검증해 Drive `derived/ai-study-notes/<note_key>/`에 AI 표시된 불변
본문+manifest로 stage하고
readback한다. 이후 `write_ai_region` 계열의 제한된 writer로 Session의 시스템 소유 AI 하위
영역에 넣는다. SOURCE/USER와 기존 수업 메타데이터는 수정하지 않는다.
job은 단계 `REQUESTED → WAITING_CONTEXT → GENERATING → STAGED → PUBLISHING → READY/PARTIAL`
및 `FAILED/CANCELLED/STALE`를 가진다. 실패의 마지막 성공 단계를 유지한다.

SQLite `note_artifacts`는 note_key, output identity/hash, manifest hash, 작성 버전,
AI region/block IDs, 마지막 게시 hash, 상태/시각만 보관한다. 외부 파일/블록 생성의 응답을
잃으면 note_key가 붙은 산출물을 조회·검증해 재사용한다. 중복 후보를 임의 선택하지 않는다.
이미 stage가 검증됐다면 Notion 실패 재시도에 AI를 다시 호출하지 않는다.
USER가 AI 영역까지 직접 편집한 경우 게시 hash 불일치를 감지해 ‘내 편집 확인 필요’로 멈추고,
완성된 새 산출물 링크는 별도로 보여준다. 사용자 편집을 삭제하며 덮어쓰지 않는다.

이 저장 영역은 다음 버전에서 추가하는 `artifact_role=AI_STUDY_NOTE` snapshot이다.
frozen의 결정적 normalized derivative와 저장 역할이 다르므로 §9 C6에서 Drive 역할도 개정한다.
manifest에 AI provenance·note_key·generator 설정 버전·evidence hash를 필수로 기록하며,
원본 검색/수집은 해당 namespace와 artifact role을 제외한다. SOURCE verification이나 정규화
전사/자료의 포인터로 등록할 수 없다. 파일이 derived 아래 있다는 사실만으로 SOURCE evidence가
되지 않는다. AI 이력으로 열거나 정책이 허용하는 AI context로 제공할 때만 별도 역할을 유지한다.

근거 변경을 worker가 관측하면 기존 노트는 `갱신 필요`가 되고 engine의 현재 사실 근거에서
제외한다. 과거 노트는 AI 이력으로 보존한다. 오프라인 때는 ‘마지막 검증 시각’ 기준의 표시임을
명확히 하며 UI가 현재 freshness를 보장한다고 표현하지 않는다. 내 복습 상태는 자동 완료하지 않는다.

### 6.4 학습 품질 수용

결과에는 학습 목표/핵심 개념, 단계별 풀이 예제, 필요한 증명·계산, 오개념, 연습문제와 접힌
해설, 원문 위치, AI 보충 구분이 있다. 고정 분량 채우기로 품질을 판단하지 않는다.
해당 수업에 증명이 없으면 억지로 교수 발언을 만들지 않고 학습상 필요한 AI 설명으로 표시한다.
원문과 보충 설명의 차이, ASR 추정, 읽지 못한 그림/수식도 표시한다. 사용자가 예제를 따라하고
문제를 풀 수 있는지를 실제 샘플로 평가한다. 접수 요약만으로 이 수용 조건을 통과할 수 없다.

### 6.5 사용자 상태 projection

요청 상태와 마지막 유효 노트는 분리한다. 갱신 요청이 실패해도 이전 노트 링크·작성 시각·근거
상태는 보존한다. 아래 오류 필드에는 source 본문/자격증명을 넣지 않는다.

| durable 상태/조건 | 사용자 표시 | 함께 보여줄 정보·다음 행동 |
| --- | --- | --- |
| job 없음 | 아직 없음 | 학습 요청 템플릿 링크 |
| REQUESTED | 요청됨 · 실행 대기 | 수신 시각·최근 동기화 |
| WAITING_CONTEXT, 근거/권한 부족 | 입력·연결 필요 | 부족한 전사/Usage 또는 접근권, 정확한 입력 링크 |
| WAITING_CONTEXT, generator 미연결 | AI 생성 연결 필요 | 최초 설정 링크; 호출/과금 없음 |
| GENERATING / STAGED / PUBLISHING | 만드는 중 / 저장됨·노션 반영 중 | 마지막 성공 단계; 검증된 stage가 있으면 AI 재호출 안 함 |
| 위 비종료 상태의 retryable 오류 | 재시도 대기 | 마지막 성공 단계·오류 이유·다음 시도 시각 |
| PARTIAL | 부분 노트 | 읽은/누락 범위·artifact 링크·필요한 보완 |
| FAILED, 영구 오류 또는 재시도 소진 | 생성 실패 | 마지막 성공 단계·실패 이유·자동 재시도 없음; 원인 해결 후 새 요청 링크 |
| CANCELLED, 실제 중단 확인 | 취소됨 | 이미 저장/게시된 단계가 있으면 별도 명시 |
| READY | 준비됨 | 요청 범위 전체 충족·근거 모드·마지막 검증 시각 |
| STALE | 갱신 필요 | 변경 근거·이전 AI 이력·새 학습 요청 링크 |

재시도 정책은 단계별 최대 3회 재시도(최초 시도와 별도), 1/5/15분 뒤를 다음 버전 기본값으로
고정한다. 영구 오류는 즉시 FAILED다. restart는 durable 시도 수/시각을 이어받고 무한 대기하지 않는다.
FAILED 또는 CANCELLED 이후 새 독립 active 요청도 동일 note_key와 검증된 stage를 회복할 수 있다.
CANCELLED는 과거 request/실행 attempt의 terminal 결과이며 note_key의 영구 금지 상태가 아니다.
실행 identity는 `(note_key, attempt_no)`이며 **한 note_key의 non-terminal attempt는 최대 하나**다.
새 유효 request가 오면 로컬 트랜잭션에서 아래 순서로 판정한다.

1. non-terminal attempt가 있으면 그 attempt에 request reference를 추가한다. 새 attempt/AI 호출을
   중복 생성하지 않는다. 이미 검증된 stage가 있으면 같은 실행이 이를 이어서 게시한다.
2. active attempt가 없고 재사용 가능한 verified artifact가 있으면 **재게시용 새 attempt_no**를
   원자적으로 예약하고 현재 request reference와 current_attempt_no를 그 attempt에 결속한다.
   현재 head·freshness를 검증한 artifact를 seed로 STAGED부터 진행하며 **generator를 호출하지 않는다**.
   publish/readback·재시도·READY/PARTIAL/FAILED 전이는 모두 이 새 attempt identity로 기록한다.
   이미 같은 artifact가 정확한 current AI 영역에 있으면 readback으로 쓰기를 생략할 수 있지만
   상태 기록은 여전히 새 attempt에 귀속된다. 과거 실패/취소 receipt·attempt는 재활성화하지 않는다.
3. active attempt와 재사용 artifact가 모두 없으면 최초 실행, 또는 마지막 FAILED/CANCELLED
   이후의 유효한 새 요청에 대해 다음 attempt_no를 원자적으로 예약한다. 같은 receipt 재감지는
   다음 번호를 열지 않는다. 완료된 PARTIAL의 동일 요청은 기존 부분 결과를 보여주며 누락 입력/
   근거가 바뀌면 새 note_key로 처리한다. STALE도 변경 근거를 재검증하여 새 key로 계획한다.

취소는 해당 request reference만 끝낸다. 다른 유효 reference가 있으면 현재 attempt는 계속한다.
마지막 유효 reference가 없을 때만 중단을 요청하고 실제 중단 결과를 확인해 CANCELLED를 기록한다.
중단 요청 중 새 reference가 오면 아직 중단 전이면 계속 사용하고, 이미 돌이킬 수 없는 중단이
시작됐으면 그 결과를 확인한 뒤 새 reference를 다음 attempt로 옮긴다. 새 request까지 과거 취소로
끝내거나 두 non-terminal attempt를 동시에 만들지 않는다.

이전 receipt·terminal attempt 이력은 불변이다. job의 현재 상태는 current_attempt_no의 projection이고
key 자체는 공유 container identity다. 단계 완료·재시도·취소 결과는 자신이 가진 attempt identity와
current pointer가 일치할 때만 current projection을 바꾼다. 이전 attempt의 늦은 결과는 그 이력에만
기록하며 현재 실행을 과거 상태로 되돌리지 않는다. artifact 채택/게시에는 별도로 현재 head와
freshness 검사가 필요하다. attempt 예약·reference 변경·current pointer 전이는 로컬 트랜잭션이다.
source/USER 편집이나 과거 취소 receipt를 되살리지 않는다.
부분 결과는 사용 가능한 산출물을 stage/readback한 경우에만 PARTIAL이며, 검증되지 않은 생성물은
부분 노트로 게시하지 않는다. 합성 모형의 오류 재현은 이 표시 의미만 검사하며 실제 retry를 구현하지 않는다.

## 7. 대시보드·오류·알림

대시보드 순서는 오늘 할 일/마감 → 과목 → 이어서 공부 → 학사 일정 → 파일 확인이다.
원본 과제/시험과 개인 일정은 각자 원본 뷰를 유지하며, 복제된 일정의 자동 완료 동기화를 만들지 않는다.
세션 페이지에 전사, 사용 범위, 노트의 근거 모드/coverage와 마지막 검증 시각, 내 필기를 둔다.

- Drive 업로드 성공은 Drive에서 확인한다. worker가 관측하기 전에는 Notion ‘접수됨’을 표시할 수 없다.
- ‘파일 확인’ 하단에 ‘업로드 후 다음 동기화 시 표시’를 항상 안내한다. 동기화 주기는 연결된
  PC에서의 목표이며 최대 60초 이내 처리 보장으로 표현하지 않는다.
- worker는 성공/실패와 마지막 실행 시각을 운영 상태에 기록한다. 오래된 시각은
  ‘최근 동기화 없음 — PC 또는 연결 확인’으로 표현한다. PC가 꺼졌다고 단정하지 않는다.
- 기본 알림은 Notion의 확인 항목/상태 배지다. 외부 push/email/mobile은 이 묶음의 보장에 없다.
- 오류 항목은 파일/요청 이름, 마지막 성공 단계, 사용자에게 필요한 다음 행동, 원본/요청 링크를
  제공한다. 재시도 중이면 다음 시도 시각을 표시한다. 정상 입력마다 별도 승인 팝업을 만들지 않는다.
- 세션 노트에는 `전사만 / 확인된 PDF 포함 / 임시 자료 포함`, `전체/부분`, `갱신 필요`를
  글자로 표시한다. 색상만으로 의미를 전달하지 않는다. 모바일에서는 같은 순서로 한 열로 배치한다.

## 8. 대표 수용 시나리오

| ID | 입력/실패 | 기대 결과 |
| --- | --- | --- |
| A01 | 단일 Inbox의 과목 미정 파일, 재시작 | 같은 intake/확인 항목 유지, 가짜 Course/Session 없음 |
| A02 | 과목 폴더 입력 | 과목 질문 생략, 단일 Inbox와 같은 이후 흐름 |
| A03 | 날짜 없는 전사 | 보류 및 입력 링크; 임의 강의일·새 수업 생성 없음 |
| A04 | 같은 요청 재감지·동일 source 재처리 | receipt/작업/학술 개체 중복 없음 |
| A05 | 기존 수업 선택 | 정확한 Session 하나에 연결, 새 ID/USER 덮어쓰기 없음 |
| A06 | 다른 과목 수업·다른 전사 이미 연결 | 명시 오류, 자동 교체/합치기 없음 |
| A07 | 범위 비움 | UNKNOWN 유지, whole-source 승인 생성 없음 |
| A08 | 지정 범위/전체 명시 후 사람 승인 | 정확한 최신 제안만 적용, 승인 선택과 적용 상태 분리 |
| A09 | 승인 뒤 파일/범위/관계 변경 | 과거 승인 적용 거부, 새 확인 필요 |
| A10 | 전사 두 부분 시간만 존재 | transcript 후보 또는 유형 확인, 자동 Material 확정 없음 |
| A11 | 이동 성공 응답 유실/외부 parent 변경 | 같은 target이면 회복, 다른 위치면 중단; 복사·삭제 없음 |
| A12 | 노트 생성 중 source·Usage 변경 | 현재 노트로 게시하지 않음, 재생성 필요 |
| A13 | Drive stage 후 Notion 쓰기 실패 | 검증된 stage 재사용; AI 재호출/중복 게시 없음 |
| A14 | AI 영역에 사용자 직접 편집 | 교체 중단, USER 편집과 새 산출물 모두 보존 |
| A15 | 개인 일정/학교 공통 일정 | 가짜 과목 없이 표시; 학술 과제는 같은 원본 뷰 |
| A16 | PC off·Notion 실패·AI 미연결 | 접수/생성 완료 오표시 없음, 마지막 확인과 다음 행동 표시 |
| A17 | 제출 후 입력 변경/취소 | 재실행·재라우팅 금지, 이미 반영된 단계 명시, 새 요청 안내 |
| A18 | 전사+확인된 PDF 요청에 PDF 없음 | 입력 부족 표시, 전사만으로 몰래 범위 축소하지 않음 |
| A19 | 범위 제안/승인 선택 후 UNKNOWN 또는 새 범위 제출 | 새 입력 수신 뒤 과거 미적용 제안 SUPERSEDED; 이미 적용된 Usage는 보존 |
| A20 | 기존 Usage가 있는 범위 변경 제안 거절 | 변경 제안만 종료; 기존 유효 범위와 이를 쓴 노트 유지 |
| A21 | 파일은 그대로, Usage 범위/확인/관계만 변경 | 다른 evidence manifest; 해당 노트 갱신 필요, 전사만 노트는 무관한 PDF 변경에 영향 없음 |
| A22 | 새 날짜의 새 수업 선택 | 새 세션 identity로 화면·원문·제안·노트 연결; 기존 수업 원문/USER 내용 보존 |
| A23 | 범위 요청에 날짜/다른 타입 필드 혼합 | claim 전에 오류, 어떤 handler도 부분 실행하지 않음 |
| A24 | provider S01/S07, 빈 로컬 state 또는 중복 S07 | 전체 inventory gate 전 할당 금지; 정상 seed 후 S08 이상, 중복이면 중단 |
| A25 | binding 쓰기 사이 Course/Date 변경 | 포인터만 성공해도 전체 tuple readback 실패; 부분 반영 표시, Ready/후속 처리 금지 |
| A26 | derived의 AI study note 발견 | canonical source/normalized derivative로 수집하지 않음, AI provenance 보존 |
| A27 | 이미 등록한 PDF의 과목 정정 요청 | 자동 재배정 금지·지원 경계 안내, 기존 Usage/USER 참조 보존 |
| A28 | 신규 Usage의 역할 미선택 / 기존 Usage 범위 변경 | 미선택은 제안 없음; UPDATE는 정확한 target ID/Role 보존 |
| A29 | binding 예약 후 pre-write 실패 / write 응답 유실 | mutation 0 증거일 때만 안전한 예약 교체; 불명확하면 reconcile 후 결정 |
| A30 | 동일 slot 미수신 범위 요청 2개와 과거 승인 | 안정적인 claim 순서의 마지막 세대만 적용 후보; 과거 미적용 제안 종료 |
| A31 | NEW/EXISTING 또는 기존 Session 미선택 | 날짜가 있어도 receipt/수업 생성 없음 |
| A32 | BOUNDED 값이 남은 UNKNOWN/WHOLE | 필드 오류로 보류; 사용자가 비운 요청만 수신 |
| A33 | 동기화 설정 변경 | worker와 UI가 같은 설정값 표시, 최대 지연 보장 문구 없음 |
| A34 | R1 동일 범위 거절/대체 후 R2 재제출 | R1/R2 Proposal ID 다름; R2 retry는 같음, R1 Decision/State 보존 |
| A35 | S08 예약 후 무쓰기 실패·해제 | canonical null, S08 소비 유지; 새 요청 S09 이상, readback 성공 후만 canonical 확정 |
| A36 | Approve 후 현재 입력 Cancel/철회/편집 | head inactive, 미적용 proposal SUPERSEDED, Usage mutation 없음; 이미 시작한 쓰기는 실제 readback 결과 표시 |
| A37 | Sessions membership 또는 strict S ID 불일치 | 잘못된 data source 또는 S형이 아닌 ID(M 등) 거부; 새 Notion Type 속성 요구 없음 |
| A38 | provider M01/M07·빈 원장·중복 M07·create 응답 유실 | inventory 후 M08 이상, 중복은 생성 금지, exact Material readback 뒤만 canonical 확정 |
| A38-folder | raw/중간 폴더 create 성공 후 응답 유실·재시작 | marker 1개 exact readback이면 생성 없이 회수; 복수/불확실 부재면 자식·Material 생성/이동 없이 reconcile |
| A39 | NEW 전사 Lec2/1주차 이름·재시작·USER 제목 편집 | 예약 기반 동일 creation payload, 임의 차시 추측 없음, 후속 재처리로 제목 덮어쓰기 없음 |
| A40 | v2 Queue 게시 후 재시작·token/action 변조 | 원장/envelope/action으로 동일 ID 회수; 불일치·미지원 wire format은 적용 거부 |
| A41 | R1 Reject 또는 APPLIED 후 R2 제출 | R1의 Decision/terminal State 보존; PENDING_REVIEW/APPROVED만 supersede |
| A42 | confirmed 슬라이드 M01+교과서 M20 | 기본은 M01만 선택; 명시 자료 모드에서 M20 선택 가능, 미선택 M20 변경은 기본 노트에 무관 |
| A43 | 영구 생성 실패·게시 재시도 소진·부분 산출물 | 생성 실패/마지막 단계/다음 행동, 검증된 일부만 부분 노트, Ready 오표시 없음 |
| A44 | S08 해제 후 실제 8차시 또는 차시 미상 제출 | ID S09 이상, USER Session No=8 또는 null; 9강 별칭 추측 없음 |
| A45-a | 서로 다른 note_key: R1 전사만 실행 중 R2 자료 포함 요청, R1 늦게 완료 | R2만 현재 AI 영역 게시, R1 artifact는 이력 |
| A45-b | R1/K attempt 1 GENERATING 중 R2/K 제출 후 R1 취소 | R2는 attempt 1 reference, attempt 2 없음; R1 receipt만 취소하고 R2 실행 유지 |
| A45-c | R1/K attempt 1 STAGED→CANCELLED 후 같은 K의 R2 제출 | attempt 1 CANCELLED 불변, R2/attempt 2가 current; verified artifact를 seed로 재게시, generator 추가 호출 0회; 성공/실패는 attempt 2만, 늦은 attempt 1은 current 변경 금지 |

## 9. frozen 명세에 반영할 변경 목록

기존 v1.2 구현이 아래 항목을 이미 지원한다고 주장하지 않는다. 구현 브랜치는 이 계약을
다음 버전 명세로 채택한 뒤 변경하며, 현행 frozen의 무변경 호환 기능으로 포장하지 않는다.

| 변경 | 대상 | 다음 버전의 정확한 차이 |
| --- | --- | --- |
| C1 | 구현 명세 §8 state | 별도 intake/receipt/binding/note 작업·참조·study_note_heads 테이블과 §3·§6 유일 키/상태 추가 |
| C2 | 구현 명세 §8.6.1·§13 DriveAdapter, worker 및 학술 handle resolver | Session/Material reserve/apply·inventory·회복; marker 포함 create/전체 검색/indeterminate·ID readback의 worker 전용 포트; N강의 ID suffix 추측 제거 |
| C3 | 구현 명세 §14–15 | 운영 DB·타입별 필드/Relation·ownership, Session No nullable 및 USER 실제 차시, creation snapshot·초기 옵션, Queue Proposal Envelope Rich text 추가; 기존 Queue enum/권한 유지 |
| C4 | 설계 §10, 구현 분류 | pre-canonical semester 접수 및 두 부분 시간 지원; Course 확정 전 source 등록 금지 |
| C5 | PageRange/Usage producer·approval identity/freshness | action semantics 유지; v2 envelope 단일 wire format·세대 ID·재계산/변조 검사, Role/target/slot·claim 순서, active head/취소·원 요청 검증, terminal 이력 보존 |
| C6 | enrichment/state 및 설계의 Drive 저장 역할 | note identity·최대 한 active attempt/reference·terminal 뒤 새 시도(artifact 재사용도 generator 없는 새 attempt)·current projection, 근거 선택·AI writer·freshness/coverage·유한 재시도/상태, AI snapshot SOURCE 수집 배제 |
| C7 | 학기 화면/개인 기록 | 학술 그래프 밖 USER 일정/할 일 원본과 연결 뷰 추가 |
| C8 | 구현 명세 설정·scheduler | 다음 버전 poll_interval_minutes 기본 1(60초), 범위 내 사용자 설정 가능; UI는 실제 값/최근 실행 표시, 최대 지연 보장 아님 |

읽기 전용 MCP, capability allowlist, SOURCE/AI/USER 소유권, Partial, human approval 검증은
이 변경으로 완화되지 않는다. SQL/Notion 원본 스키마 migration은 구현 작업에서 별도 테스트와
백업·readback을 갖춘다. 실제 운영 DB의 무단 migration은 이번 설계 작업에 포함되지 않는다.

## 10. 공식 문서 확인과 live 검증의 구분

2026-09-13 확인. Notion llms.txt에서 관련 상세 문서로 이동했으며 `.md` 오류는 HTML 상세
경로로 확인했다. 현재 코드의 `Notion-Version: 2025-09-03`를 이번 문서에서 자동 변경하지 않는다.

- Notion 연결된 데이터 소스의 페이지·속성 편집은 원본에 반영되고 뷰 필터는 별도다.
  [공식 도움말](https://www.notion.com/en-gb/help/data-sources-and-linked-databases).
- worker는 연결된 뷰가 아니라 권한을 부여한 원본 데이터 소스와 page properties를 사용한다.
  [데이터베이스 안내](https://developers.notion.com/guides/data-apis/working-with-databases),
  [페이지 갱신](https://developers.notion.com/reference/patch-page).
- Drive는 기존 file ID의 parent를 `files.update`로 바꾸는 이동 경로를 제공한다.
  [폴더 이동 안내](https://developers.google.com/workspace/drive/api/guides/folder#move_files_between_folders).
- Drive `appProperties`는 앱 전용 metadata와 검색을 지원한다. 복구 marker의 기능 근거이며
  실제 계정/앱 결속과 응답 유실 동작은 live 검증한다.
  [사용자 정의 속성](https://developers.google.com/workspace/drive/api/guides/properties),
  [속성 검색](https://developers.google.com/workspace/drive/api/guides/search-files).

문서는 기능 근거다. 해당 계정의 권한·뷰 구성·worker/API 버전·동시 편집·재시도 동작은
구현 후 실제 시험 계정/폴더에서 확인해야 한다. 로컬 프로토타입 통과를 그 live 검증으로 대체하지 않는다.
