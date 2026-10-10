# Intake 분류 v2 — P-B3 불변식 체크리스트와 구현 계획 (계획 리뷰용, 2026-10-11)

권위 문서는 `intake-classification-v2.md`(r14 PLAN GO)와 `intake-classification-pb-slices.md`다. P-B2a/P-B2b와 같은
방식으로 구현 전에 각 불변식을 **구현 위치, 검증 테스트, crash/응답 손실 처리**와 짝지어 계획 리뷰를 받는다.
종료 기준: REQUIRED = 계획 계약 위반 + 도달 가능한 구체 트리거 + 잘못된 영속 상태/무권한 외부 쓰기. 방어 강화만 남으면 GO.

## 조사로 확인한 현재 상태 (이 표의 전제)

- 상태 계층(P-A)에는 `canvas_observations`/`canvas_classifications`/`canvas_drive_bindings`, `recording_calendar_current/courses`,
  `intake_request_generations`(derivation_version), `terminal_change_ledger`(+`record_/transition_/get_terminal_change`),
  `handover_records`(`record_handover`)가 이미 있다. 워커 쪽 소비자만 없다.
- **워커의 요청 생성(`_create_input_request_with_context`, `planner.make_request_generation`)은 V2 키 파생
  (`superseded_request_key` + `request_generation`)을 아직 지원하지 않는다.** `_assert_request_generation_current`도 영속 generation의
  `derivation_version`으로 파생을 고르지 않는다. 새 HUMAN generation(터미널 스캔·handover)의 선행 조건이다.
- 제품용 Canvas 전송은 이미 있다: `settings/canvas_checks.py`(`validate_canvas_origin`, DNS 핀 연결, 응답 크기·시간 제한,
  `CanvasConnection.request`, `_next_link` origin 검증)와 `settings/canvas_service.py`(Keychain 토큰, 검증된 프로필). 기존
  `scripts/knu_lms_probe.py`는 같은 규칙의 파일럿 스크립트다. 어댑터는 이 전송을 재사용한다.
- 설정: `intake.classification.canvas_course_map`(검증된 Canvas course → course_key), `max_terminal_scan`(50)이 있다.
  Canvas 관측 활성화 플래그는 없다(신규, 기본 off).

## 슬라이스와 순서

P-B3a Canvas 관측 어댑터 → P-B3b V2 request generation → P-B3c 터미널 스캔 + handover + ASSIGN_COURSE 보호.
각 슬라이스는 이 표의 해당 행을 구현 리뷰 대상으로 삼는다(계획 리뷰는 표 전체를 한 번 받는다).
`intake.classification.enabled=false`(기본)에서는 현행 동작이 바이트 단위로 같아야 한다.

## P-B3a — Canvas 메타 관측 어댑터 (§6.3)

| ID | 불변식 (계획 §) | 구현 위치 | 검증 테스트 | crash / 응답 손실 |
|---|---|---|---|---|
| A1 | 읽기 전용 GET만, origin 고정(`validate_canvas_origin`), 허용 리소스는 `modules`(+items), `assignments`, `announcements`, 과목 메타뿐; 페이지·항목·응답 바이트·실행 시간 상한 (§6.3) | 신규 `src/uls/intake/canvas_observer.py`: `CanvasObserver`(전송 주입), `CanvasConnection` 재사용 | 허용 밖 경로/비-GET 거부; 다른 origin의 `next` 링크 거부; 상한 초과 → 불완전 | 상한 초과·오류 = 불완전 수집(A4) |
| A2 | 토큰은 Settings Canvas 연결의 Keychain 항목만, 없으면 어댑터 비활성(DISABLED); 토큰은 DB·로그·오류·예외 문자열에 없음 (§6.3, CLAUDE.md) | `CanvasObserver`가 토큰 공급자 콜러블을 받음(워커 구성에서 `CanvasService` 저장 토큰 조회) | 토큰 부재 → 호출 0·readiness DISABLED; 오류 경로 문자열에 토큰 값 없음 | 해당 없음 |
| A3 | 관측 기록: modules items → `module_item`, assignments → `assignment`, announcements → `announcement`. revision 규칙은 `updated_at`만이 아니라 **저장 payload 전체**(title, module_name, module_week, item_type, updated_at)를 canonical fingerprint로 최신 revision과 비교한다: 동일 → 변경 0, 하나라도 다르면(`updated_at` 불변·누락 포함) `observation_revision`+1의 새 행. 같은 revision 내용은 불변(P-A `record_canvas_observation`의 immutable 검사 재사용, 충돌 예외를 삼키지 않음) (§6.3, §5) | `observe_course()` → `state.record_canvas_observation` | 같은 응답 재관측 → 변경 0; updated_at 불변 + 제목 변경 → 새 revision; 모듈 이동; updated_at 누락 후 메타 변경; 모듈 주차 파싱(`^(\d+)주차$`만, module position은 주차로 쓰지 않음) | 재실행 멱등 (fingerprint 비교) |
| A4 | (구현 위치에 달력 소비 지점 포함: P-B1 `build_calendar`/`match_transcript` 경로, P-B2b AUTO preflight, `_reserve_session`, `_ensure_session_page`에 COMPLETE·매핑·verification 재검사 연결; 병렬 상태 저장소 금지) 완전성 증명과 **fail-closed 영속 장벽**: 과목 수집 시작 전에 `canvas_collection_state(course) = IN_PROGRESS`(영속)와 해당 과목 달력의 `matchable=false`를 **단일 SQLite 트랜잭션**으로 먼저 기록하고(두 기록 사이 분리 금지), 모든 컬렉션을 상한 안에서 끝까지 읽어 `reconcile_canvas_collection(collection_complete=True)`와 달력 갱신이 끝난 뒤 **같은 트랜잭션**에서 `COMPLETE`로 확정한다. **다대일 매핑**: 한 `course_key`에 매핑된 모든 유효 Canvas course ID가 같은 매핑 epoch에서 전부 `COMPLETE`일 때만 그 course_key의 달력을 열고(하나라도 `IN_PROGRESS`/`INCOMPLETE`/미관측이면 닫힘 유지, 틱당 과목 상한으로 일부만 관측된 경우 조기 개방 0), projection hash는 모든 구성 course의 최신 완전 관측으로 계산한다. **달력 소비자(매칭·AUTO preflight)는 course collection state가 `COMPLETE`이고 A5 자격이 현재 유효할 때만 달력을 매칭에 사용**한다. 불완전/오류/상한 도달은 비활성화 0건이며 상태는 `INCOMPLETE`로 남고 달력은 닫힌 채 유지. 재시작 시 `IN_PROGRESS`로 발견된 과목은 `INCOMPLETE`로 복구(달력 닫힘 유지). 과거 observation 행은 보존하되 달력 매칭 가능성은 별도로 닫는다 (§2.3, P-A r10 R2) | `observe_course()` 시작/종료부 + 틱 시작 복구; 상태 저장은 기존 달력 테이블의 closed/verified 표현을 우선 재사용하고 부족할 때만 소형 course 상태 컬럼 추가 | C1 COMPLETE / C2 INCOMPLETE에서 자동 Session 0건, 양쪽 COMPLETE 후 날짜 충돌 → AMBIGUOUS, 틱 상한으로 일부만 관측 시 조기 개방 0; 시작 장벽의 두 기록 사이 강제 종료(원자성)·첫 페이지 반영 직후 강제 종료 → 재시작 → Drive transcript 분류에서 자동 Session 생성 0건; 중간 페이지 오류·상한 → 비활성화 0·달력 닫힘; 완전 → 사라진 recording 비활성화 + `COMPLETE` | 장벽이 수집 시작 전에 영속되므로 어느 시점의 crash도 fail-closed |
| A5 | **과목 게이트(관측 이전)**: `observe_course()`는 GET·observation upsert·classification·달력 갱신 전에 `canvas_course_map`에 항목이 있고 해당 Canvas 과목 registry의 `verification_state == api_code_verified`임을 검증한다. 하나라도 아니면 네 효과(GET, observation, classification, calendar) 모두 0건 (§2.3 매핑 없는 과목 ID 수집 금지). **자격 철회**: 달력 매칭과 AUTO preflight는 매 사용 시점에 현재 Canvas course ID의 `canvas_course_map` 매핑·`api_code_verified`·course_key 일치를 재확인한다. 매핑 삭제/다른 과목 재매핑/검증 철회 시 기존 projection은 자동 매칭에 쓰지 않고 S3 또는 RECONCILE로 제한(행은 보존). RECORDING → 달력: ExternalTool + 날짜 제목 관측만 `RecordingEntry`, 학기 범위·완전 수집 조건으로 기존 `build_calendar`(새 알고리즘 금지) → `replace_recording_calendar_current(…)`; 최신 활성 observation만 투영 (§2.3, §6.3) | `observe_course()` 선두 `_course_gate()` + `_project_calendar()` | 미매핑 course, registry 검증 실패 course 각각 네 효과 0건; 완전 수집 + 매핑 → 달력 갱신; 날짜 충돌 → AMBIGUOUS; 완료된 달력 → 매핑 삭제/다른 과목 재매핑/verification 철회 → AUTO Session 생성 0건; 과거 revision 제외, anchor 중복/cadence anomaly/학기 범위 변경/삭제 후 복구 | 달력 투영 해시 불변이면 변경 0 |
| A6 | 메타 전용 경로와 분류 계약: 기존 P-B1 `classify_by_rules`를 Canvas 메타 입력(`ItemSignals`)에 재사용해 P0 → P1 → P2 → P3/P4 우선순위를 적용하고, 결정·주차·origin 후보·고정 사유 코드(예: `CANVAS_METADATA_ONLY`, `UNSUPPORTED`)를 `canvas_classifications`에 **observation fingerprint(resource, observation_revision)에 결속**해 저장. 미결 항목은 S3 사유만 로컬 저장하며 Input Request·AUTO plan/job·File Intake 행·Material 생성 0 (§6.3 M1) | `observe_course()`가 `classify_by_rules` 호출 후 `state.record_canvas_classification`(신규 얇은 메서드) | §9 Canvas fixture(Assignment, announcement, ExternalTool, 금지 확장자 P0)를 구조화 기대값(Kind, decision, course, week, origin, 사유 코드)과 대조; 관측 1회 전후 plan/job/File Intake/Material/Request 수 불변 | 해당 없음 |
| A7 | 본문을 가져올 수 없는 ExternalTool: Kind만 기록, Material 없음, Ready 간주 안 함 (§6.3) | 분류 결과 필드 | ExternalTool 항목 → classification만, Material 0 | 해당 없음 |
| A8 | 틱 통합: `intake.classification.canvas_observation.enabled`(기본 false)일 때만 `_run_once_attested`가 sync 앞에서 관측(신규 관측·AUTO 생성의 on/off와 기존 효과 보호는 분리, T8 참조); 틱당 과목 수 상한(남은 과목은 다음 틱에 이어 관측하며 그동안 해당 course_key 달력은 A4 규칙대로 닫힘); 실패는 해당 과목만 `INCOMPLETE`로 표시하고 다른 과목·Drive intake를 막지 않음. 이전 관측 행은 보존되지만 달력은 A4 장벽으로 닫힘 (§4) | 워커 `_observe_canvas()` + 설정 로더 | 기본 off → 호출 0·기존 테스트 불변; 한 과목 실패 → 나머지 진행, 해당 과목 달력 닫힘 | 실패 시 이전 observation 보존, 달력은 fail-closed |
| A9 | 실제 네트워크 호출 0 (테스트): 전송은 주입된 가짜 `CanvasTransport`; 어댑터 코드는 requests/urllib 직접 사용 금지 | 구성 | 소켓 접근 차단 하에서 전체 테스트 통과 | 해당 없음 |

## P-B3b — V2 request generation (§3.4 generation 계약)

| ID | 불변식 | 구현 위치 | 검증 테스트 | crash / 응답 손실 |
|---|---|---|---|---|
| G1 | 키 파생: LEGACY/기록 없음은 기존 파생 그대로(기존 요청 불변), V2는 기존 입력 + `superseded_request_key` + `request_generation`; 원본·설정이 불변이어도 `new_key != old_key`. **하나의 전달 경로**: `identity.derive_request_key` / `derive_request_revision` → `requests.build_request_generation` → `planner.make_request_generation` → 워커 `_create_input_request_with_context`(+ `_assert_request_generation_current`)에 모두 선택 인자 전달, 기본값 LEGACY는 종전 결과를 바꾸지 않음 | 위 5개 함수 | 기존 키 값 회귀 고정; V2 키가 old와 다름; 같은 입력 → 같은 키; **통합**: `_create_input_request_with_context`가 만든 키를 `_assert_request_generation_current`가 재계산해 일치 | 해당 없음 |
| G2 | 생성 순서(**단일 reserve-or-resume 계약**, T2와 공유): `reserve_or_resume_generation(basis)`가 단일 SQLite 트랜잭션에서 (a) 같은 basis(HUMAN: 요청 키 / 터미널: `(receipt, changed_hash)`)의 기존 예약을 찾으면 그 generation을 그대로 재사용하고, (b) 없을 때만 새 번호를 예약하며 (c) 이전 basis의 Request-create가 미해결이면 새 예약 자체를 거부한다(T2 예약 이전에 적용). `_create_input_request_with_context`는 이미 결속된 generation/key를 받아 재예약하지 않는다. create 이후의 `bind_request_generation_key`는 선결속(예약·키·terminal ledger를 단일 트랜잭션에서 create 이전에 수행)의 멱등 확인/상태 갱신일 뿐이다. 이어 `reserve_request_generation`(생성 호출 전 영속, 위 계약의 저장 단계) → **동일 Request Key의 provider readback으로 존재 여부 해소(기존 pending Request-create 복구·장벽 재사용, 새 장벽 설계 금지)** → 없을 때만 create → `bind_request_generation_key`. **이미 create 시도가 영속된 키는 빈 조회 결과만으로 부재로 간주하지 않는다**(기존 durable attempt 정보 재사용, 새 테이블 금지): 부재 확정(기존 복구 계약의 증명 조건)이 없으면 pending 장벽 유지 + RECONCILE_REQUIRED. 존재가 불명확하면 새 create 금지, RECONCILE_REQUIRED. 이전 basis 미해결 → 새 basis 거부 | `_create_input_request_with_context(..., supersede=...)` | provider가 생성에 성공했으나 응답만 유실 + 재시작 + 재시도 + readback 불명 조건에서 Draft 최대 1건; 예약 직후 crash의 generation 행·카운터(고아 N+1 없음), H1 pending-create 도중 H2 변경, create 성공 → 응답 손실 → 첫 조회 빈 결과 → 재시작·재시도에서도 두 번째 create 0건; 이전 basis 미해결 거부 | readback-before-create가 중복의 방어(영속 generation은 번호 안정성만 제공) |
| G3 | claim-time: `_assert_request_generation_current`가 영속 generation의 `derivation_version`으로 파생을 고른다(V2 → V2 파생, LEGACY/없음 → 기존); 영속 데이터 없이 V2 형태이거나 충돌 → RECONCILE_REQUIRED, 추측 금지 | `_assert_request_generation_current` | legacy Draft/Claimed claim 성공; V2 Draft Submitted→Claimed→HUMAN plan 성공; 데이터 부재·충돌 → reconcile | 해당 없음 |
| G4 | 새 generation Draft: §3.3 제안 필드 채움, `Result Reference`에 이전 키 + `AUTO_SUPERSEDED`, 기존 터미널 키는 계속 터미널 | 생성 경로 | 새 Draft 제안 필드·참조; 이전 키 재Claim 0 | 해당 없음 |

## P-B3c — 터미널 스캔 + handover + ASSIGN_COURSE 보호 (§3.4)

| ID | 불변식 | 구현 위치 | 검증 테스트 | crash / 응답 손실 |
|---|---|---|---|---|
| T1 | `AutoResolved` receipt bounded scan: 안정적인 로컬 receipt 순서(receipt id)의 **순환 cursor**(`auto_resolved_scan_cursor`) — 끝에 도달하면 처음으로 wrap하여 전체 집합을 반복 재방문; 틱당 `max_terminal_scan`개, 정확한 provider page 재조회, 영속 `terminal_snapshot_hash`와 비교. 재조회 실패 항목은 cursor를 그 항목 앞에 두지 않고 진행하되 다음 순환에서 재시도(오류 카운트 기록) (§3.4 관측 경로) | 신규 `_scan_terminal_requests()`를 틱 시작 복구 다음에; cursor는 state 신규 메타 | 변경 없는 터미널 요청 → generation 0·쓰기 0; receipt 수 > `max_terminal_scan`에서 한 바퀴 완료 후 첫 항목을 수정하면 이후 틱에 Draft 정확히 1건; 재시작 후 cursor 유지 | cursor는 항목 처리 후 갱신(재처리 멱등) |
| T2 | 변경 확인 → `record_terminal_change(receipt, changed_hash, state)` → G2의 `reserve_or_resume_generation`(**`(receipt, changed_hash) → generation → Request Key` 결속을 단일 트랜잭션으로 provider create 이전에 영속**, crash 후 같은 changed_hash는 기존 예약 재사용·미해결 basis면 새 예약 거부; `transition_terminal_change` 포함) → 새 Draft 생성(V2, G2 복구 경로); 같은 `(receipt, changed hash)` 재관측은 같은 generation, 카운터 불변 | `_scan_terminal_requests` | 변경 후 연속 3틱·재시작 → 새 Draft 정확히 1건; generation 예약 직후·key 결속 직후·provider 생성 직후 crash를 각각 주입해 동일 changed hash에 generation과 Draft가 정확히 하나 | ledger 행이 중복 방지, 단계별 `transition_terminal_change` |
| T3 | 해당 intake에 미처리 HUMAN generation(Draft/Submitted/Claimed)이 이미 있으면 새 generation 없이 그 Draft의 `Error`에 `TERMINAL_CHANGED_AGAIN`만 표시 | 스캔 분기 | 기존 generation 제출 중 옛 터미널 재관측 → 중복 claim 0 | 해당 없음 |
| T4 | 터미널 receipt 불변: 변경이 감지돼도 receipt를 되살리지 않고 기존 키로 plan/job을 만들지 않는다; 변경 감지 시 아직 살아 있는 AUTO plan은 SUPERSEDED + job VOID (§3.4 R7) | 스캔 분기 + `supersede_intake_plan` | 기존 키 claim 거부(`REQUEST_TERMINAL`); plan/job 닫힘 | 닫기와 generation 생성은 순서 독립(둘 다 멱등) |
| T5 | handover 자격: AUTO plan의 effect(예약, provider page ID, source binding, 폴더 marker, 이동 intent, derivative)가 없으면 새 HUMAN plan은 정상 실행; 있으면 **각 effect를 정확 readback 검증(예약 ID·entity ID·Course/Date/Type, 폴더 marker, 이동 목적지·privacy, binding file ID·version)한 뒤에만** `record_handover(from, to, adopted_effects)`로 검증된 매핑을 원자적으로 영속화하고 채택해 이어서 실행한다. **채택 전에 검증된 AUTO 효과를 새 HUMAN RequestInput(Course, Kind, Actual Date, Session 선택, Material Role, source identity)과 비교**하여 하나라도 다르면 조용히 채택·재생성하지 않고 RECONCILE_REQUIRED, HUMAN mutation 직전에도 채택 효과의 유효성을 재확인한다. 선기록은 두지 않으며(필요하면 `UNVERIFIED` 상태와 HUMAN mutation 차단), 하나라도 증명 못 하면 RECONCILE_REQUIRED + 새 entity 생성 금지 (§3.4 r8 R3) | 신규 `_handover_effects()`; HUMAN 실행이 채택 효과의 reservation/operation key를 새 plan_revision로 재발급하지 않고 `handover_records` 매핑 사용 | Session 생성 후·Material 생성 후·이동 후 HUMAN 변경 각각 → entity·폴더 중복 0, canonical binding 보존; 효과 ID 불일치·Course/Date/Type 불일치·binding version 변경과, 검증/기록 사이 crash 주입 → 잘못된 채택·신규 entity 생성 0; 날짜·Kind·Session·Material Role 중 하나만 바꾼 HUMAN generation 각각 → 잘못된 채택·추가 외부 entity 0 | 검증 → 원자 기록 → 이후 단계는 기존 멱등 키 |
| T6 | (off 상태에서도 유지, T8) ASSIGN_COURSE 재실행 보호: AUTO 효과가 있는 intake의 새 HUMAN generation은 `_claim_request_unlocked` ASSIGN_COURSE 분기 앞에서 handover 자격을 먼저 검사; 자격 있으면 과목 선택 절차를 재실행하지 않고 확인된 결과를 이어받는 FILE_DETAILS 경로/명시 reconcile; 사람이 다른 과목을 고르면 자동 이동 없이 RECONCILE_REQUIRED (§3.4 r10 R2) | `_claim_request_unlocked` | 기존 ASSIGN_COURSE Draft → AUTO 완료 → 사람 변경 → 새 제출 → Session/Material/폴더/binding 보존·상태 회귀 0 | 해당 없음 |
| T7 | 항목 상태·canonical binding·완료 상태를 검증 없이 되돌리지 않는다 | handover 경로 | 상태 회귀 0 테스트 | 해당 없음 |
| T8 | **신규 생성과 기존 효과 보호의 분리**(기존 AUTO receipt의 **제한적 terminal reconciliation도 off에서 유지**: 터미널 스캔은 기존 AutoResolved receipt에 한해 off에서도 동작하고 V2 HUMAN generation만 만들며 신규 AUTO 효과는 0. 즉 off의 금지 대상은 신규 AUTO 생성·Canvas 관측이며, 기존 AutoResolved receipt 복구가 명시적 예외): `_auto_enabled()` off는 신규 AUTO plan/job 생성·Canvas 관측·터미널 스캔에 의한 신규 generation 생성만 막는다. 기존 AUTO receipt/intent/effect가 있는 intake의 HUMAN claim은 off 상태에서도 T5/T6 보호를 수행하거나 명시적 RECONCILE_REQUIRED로 차단한다. 기존 AUTO 효과가 없는 HUMAN-only 경로는 현행 그대로 (바이트 동일) | `_auto_enabled()` 게이트 + `_claim_request_unlocked`의 효과 존재 검사 | AUTO 효과 생성 → 새 HUMAN Draft → 기능 off → Submitted/Claimed → 중복 entity·binding·이동 0; 효과 없는 off 경로는 기존 테스트 불변; AUTO 완료 → 기능 off → 터미널 USER 변경 → 재시작에서 기존 키 재Claim 0, 새 HUMAN generation 정확히 1건, 신규 AUTO 효과 0 | 해당 없음 |
| T9 | readiness: `terminal_scan_cursor`, `terminal_changes_open`, `canvas_observation_status` 카운터 (§4 O2) | `_classification_readiness` | 값 검증 | 해당 없음 |

## 범위 밖

S2(Jev), S4 태그, retrieval 필터(P-D), 공지·첨부 intake(P-E), 새 Canvas 토큰 저장/연결 UI(Settings 기존 기능 재사용), Canvas 파일 다운로드 어댑터(P-E).

## 계획 리뷰 처분

### r4 (REVISE: REQUIRED 1 / HARDENING 5) — 수용

| 지적 | 처분 |
|---|---|
| R1 다대일 course_key 완전성 | A4: 같은 course_key의 모든 유효 Canvas course가 COMPLETE일 때만 달력 개방, projection hash는 전체 구성, 3종 테스트; A8 상한 시 닫힘 유지 |
| H1 G2 문구 | G2: create 이후 bind는 선결속의 멱등 확인 |
| H2 T8 문구 | T8: off 금지 대상과 예외 명시 |
| H3–H5 | 구현 테스트에 포함: 같은 changed_hash 재관측 시 TERMINAL_CHANGED_AGAIN 미기록, SUPERSEDED↔generation 양방향 crash 등 |

### r3 (REVISE: REQUIRED 2 / HARDENING 3) — 모두 수용

| 지적 | 처분 |
|---|---|
| R1 G2↔T2 순서 충돌 | G2/T2: 단일 reserve-or-resume 트랜잭션, 재예약 금지, 미해결 basis 거부, crash/H1→H2 테스트 |
| R2 T8 복구 단절 | T8: 기존 AutoResolved receipt의 terminal reconciliation은 off에서도 유지(V2 HUMAN generation만) |
| H1–H3 | A4 구현 위치 보강; 연결 프로필 일치와 T3/T4 상태 보존·crash 테스트는 구현 테스트에 포함 |

### r2 (REVISE: REQUIRED 5 / HARDENING 3) — 모두 수용

| 지적 | 처분 |
|---|---|
| R1 A4 장벽 원자성 | A4: 시작 상태+달력 차단 단일 트랜잭션, 소비자는 COMPLETE만 매칭, 기록 사이 crash 테스트 |
| R2 A5 자격 철회 | A5: 사용 시점 재확인(매핑·verified·course_key), 철회 시 매칭 금지, 테스트 |
| R3 G2 빈 조회 | G2: 시도 영속된 키는 빈 조회로 부재 간주 금지, RECONCILE |
| R4 T2 ledger 결속 | T2: (receipt, changed_hash)→generation→key를 create 이전 영속, 3지점 crash 테스트 |
| R5 T5 입력 일치 | T5: 채택 전·mutation 직전 HUMAN 입력과 의미 비교, 4종 테스트 |
| H1–H3 | 구현 테스트에 포함(follow-up) |

### r1 (REVISE: REQUIRED 9 / HARDENING 3) — 모두 수용

| 지적 | 처분 |
|---|---|
| R1 A3 payload fingerprint | A3 재작성: payload 전체 canonical 비교, updated_at 불변·누락에서도 새 revision |
| R2 A4/A8 중간 crash fail-closed | A4 재작성: 수집 전 `IN_PROGRESS` + 달력 closed 선기록, 완료 시 원자 확정, 재시작 복구; A8 정합 |
| R3 A5 과목 게이트 | A5 재작성: 관측 이전 `canvas_course_map` + `api_code_verified` 검사, 4개 효과 0건 |
| R4 A6/A7 S1·P0 계약 | A6 재작성: `classify_by_rules` 재사용, observation fingerprint 결속, §9 fixture 대조 |
| R5 G1 함수명 | G1 재작성: 실제 5개 함수의 단일 전달 경로 + 통합 테스트 (`make_request_generation`, `build_request_generation`) |
| R6 G2 create 응답 손실 | G2 재작성: 같은 Request Key readback 해소를 create 이전에, 불명이면 RECONCILE |
| R7 T1 cursor 순환 | T1 재작성: wrap-around cursor, 실패 항목 재시도 |
| R8 T5 순서 모순 | T5 재작성: 검증 후 원자 기록, 선기록 금지/UNVERIFIED 차단 |
| R9 T8 off 시 handover | T8 재작성: 신규 생성 게이트와 기존 효과 보호 분리 |
| H10–H12 | follow-up: A3/A5 어댑터 테스트 확장(A3·A5 테스트 열에 일부 반영), T4 중간 상태 crash 테스트, 연결 프로필 일치·TERMINAL_CHANGED_AGAIN이 USER/Status 불변 테스트를 구현 테스트에 포함 |
