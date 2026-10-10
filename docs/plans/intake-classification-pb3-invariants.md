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
| A3 | 관측 기록: modules items → `module_item`, assignments → `assignment`, announcements → `announcement`; `observation_revision`은 `updated_at`이 바뀔 때만 +1, 같은 revision 내용 불변 (§6.3, §5) | `observe_course()` → `state.record_canvas_observation` | 같은 응답 재관측 → 변경 0; updated_at 변경 → 새 revision; 모듈 주차 파싱 | 재실행 멱등 |
| A4 | 완전성 증명: 모든 페이지를 상한 안에서 끝까지 읽은 컬렉션만 `collection_complete=True`로 `reconcile_canvas_collection` (사라진 항목 비활성화); 불완전 컬렉션은 비활성화 0건이고 해당 과목 달력을 닫음 (§2.3, P-A r10 R2) | `observe_course()` 종료부 | 중간 페이지 오류·상한 도달 → 비활성화 0·달력 닫힘; 완전 → 사라진 recording 비활성화 | 부분 결과를 완전으로 승격하지 않음 |
| A5 | RECORDING → 달력: ExternalTool + 날짜 제목 관측만 `RecordingEntry`; 학기 범위·완전 수집 조건으로 `build_calendar` → `replace_recording_calendar_current(course_key, canvas_course_id, …)`; 과목 매핑은 `canvas_course_map`에 있는 검증된 항목만 (§2.3, §6.3) | `observe_course()` → `_project_calendar()` | 완전 수집 + 매핑 → 달력 current 갱신; 매핑 없음 → 달력 갱신 0; 날짜 충돌 → AMBIGUOUS | 달력 투영 해시 불변이면 변경 0 |
| A6 | 메타 전용 경로: AUTO plan/job, File Intake 행, Material, Input Request 생성 0; 분류 결과는 `canvas_classifications`에만 (§6.3 M1) | `observe_course()`가 `state.record_canvas_classification`(신규 얇은 메서드)만 호출 | 관측 1회 전후 plan/job/File Intake/Material/Request 수 불변 | 해당 없음 |
| A7 | 본문을 가져올 수 없는 ExternalTool: Kind만 기록, Material 없음, Ready 간주 안 함 (§6.3) | 분류 결과 필드 | ExternalTool 항목 → classification만, Material 0 | 해당 없음 |
| A8 | 틱 통합: `intake.classification.canvas_observation.enabled`(기본 false)일 때만 `_run_once_attested`가 sync 앞에서 관측; 틱당 과목 수 상한; 실패는 해당 과목만 불완전로 표시하고 다른 과목·Drive intake를 막지 않음 (§4) | 워커 `_observe_canvas()` + 설정 로더 | 기본 off → 호출 0·기존 테스트 불변; 한 과목 실패 → 나머지 진행 | 실패 시 이전 관측·달력 보존 |
| A9 | 실제 네트워크 호출 0 (테스트): 전송은 주입된 가짜 `CanvasTransport`; 어댑터 코드는 requests/urllib 직접 사용 금지 | 구성 | 소켓 접근 차단 하에서 전체 테스트 통과 | 해당 없음 |

## P-B3b — V2 request generation (§3.4 generation 계약)

| ID | 불변식 | 구현 위치 | 검증 테스트 | crash / 응답 손실 |
|---|---|---|---|---|
| G1 | 키 파생: LEGACY/기록 없음은 기존 파생 그대로(기존 요청 불변), V2는 기존 입력 + `superseded_request_key` + `request_generation`; 원본·설정이 불변이어도 `new_key != old_key` | `identity.derive_request_key/derive_request_revision`·`planner.build_request_generation`에 선택 인자 추가 | 기존 키 값 회귀 고정; V2 키가 old와 다름; 같은 입력 → 같은 키 | 해당 없음 |
| G2 | 생성 순서: `reserve_request_generation`(생성 호출 전 영속) → create → `bind_request_generation_key`; 재시작·응답 손실 후 같은 generation 번호로 복구, 중복 요청 0 | `_create_input_request_with_context(..., supersede=...)` | 생성 응답 손실 후 재시도 → Draft 1건; 이전 basis 미해결 → 새 basis 거부 | 영속 generation이 중복의 유일한 방어 |
| G3 | claim-time: `_assert_request_generation_current`가 영속 generation의 `derivation_version`으로 파생을 고른다(V2 → V2 파생, LEGACY/없음 → 기존); 영속 데이터 없이 V2 형태이거나 충돌 → RECONCILE_REQUIRED, 추측 금지 | `_assert_request_generation_current` | legacy Draft/Claimed claim 성공; V2 Draft Submitted→Claimed→HUMAN plan 성공; 데이터 부재·충돌 → reconcile | 해당 없음 |
| G4 | 새 generation Draft: §3.3 제안 필드 채움, `Result Reference`에 이전 키 + `AUTO_SUPERSEDED`, 기존 터미널 키는 계속 터미널 | 생성 경로 | 새 Draft 제안 필드·참조; 이전 키 재Claim 0 | 해당 없음 |

## P-B3c — 터미널 스캔 + handover + ASSIGN_COURSE 보호 (§3.4)

| ID | 불변식 | 구현 위치 | 검증 테스트 | crash / 응답 손실 |
|---|---|---|---|---|
| T1 | `AutoResolved` receipt bounded scan: 영속 커서 `auto_resolved_scan_cursor`, 틱당 `max_terminal_scan`개, 정확한 provider page 재조회, 영속 `terminal_snapshot_hash`와 비교 (§3.4 관측 경로) | 신규 `_scan_terminal_requests()`를 틱 시작 복구 다음에; 커서는 state 신규 메타 | 변경 없는 터미널 요청 → generation 0·쓰기 0; 상한 초과분은 다음 틱; 재시작 후 커서 유지 | 커서는 항목 처리 후 갱신(재처리는 멱등) |
| T2 | 변경 확인 → `record_terminal_change(receipt, changed_hash, state)` → generation 예약 → 새 Draft 생성(V2) → ledger에 generation·키 기록; 같은 `(receipt, changed hash)` 재관측은 같은 generation, 카운터 불변 | `_scan_terminal_requests` | 변경 후 연속 3틱·재시작 → 새 Draft 정확히 1건 | ledger 행이 중복 방지, 단계별 `transition_terminal_change` |
| T3 | 해당 intake에 미처리 HUMAN generation(Draft/Submitted/Claimed)이 이미 있으면 새 generation 없이 그 Draft의 `Error`에 `TERMINAL_CHANGED_AGAIN`만 표시 | 스캔 분기 | 기존 generation 제출 중 옛 터미널 재관측 → 중복 claim 0 | 해당 없음 |
| T4 | 터미널 receipt 불변: 변경이 감지돼도 receipt를 되살리지 않고 기존 키로 plan/job을 만들지 않는다; 변경 감지 시 아직 살아 있는 AUTO plan은 SUPERSEDED + job VOID (§3.4 R7) | 스캔 분기 + `supersede_intake_plan` | 기존 키 claim 거부(`REQUEST_TERMINAL`); plan/job 닫힘 | 닫기와 generation 생성은 순서 독립(둘 다 멱등) |
| T5 | handover 자격: AUTO plan의 effect(예약, provider page ID, source binding, 폴더 marker, 이동 intent, derivative)가 없으면 새 HUMAN plan은 정상 실행; 있으면 `record_handover(from, to, adopted_effects)` 후 각 효과를 정확 재검증(예약 ID·entity ID·Course/Date/Type·readback, 폴더 marker, 이동 목적지·privacy, binding file ID·version)하고 채택해 이어서 실행; 하나라도 증명 못 하면 RECONCILE_REQUIRED + 새 entity 생성 금지 (§3.4 r8 R3) | 신규 `_handover_effects()`; HUMAN 실행이 채택된 효과의 reservation/operation key를 새 plan_revision로 재발급하지 않고 `handover_records` 매핑 사용 | Session 생성 후·Material 생성 후·이동 후 HUMAN 변경 각각 → entity·폴더 중복 0, canonical binding 보존 | 채택은 읽기 검증 후 기록, 이후 단계는 기존 멱등 키 |
| T6 | ASSIGN_COURSE 재실행 보호: AUTO 효과가 있는 intake의 새 HUMAN generation은 `_claim_request_unlocked` ASSIGN_COURSE 분기 앞에서 handover 자격을 먼저 검사; 자격 있으면 과목 선택 절차를 재실행하지 않고 확인된 결과를 이어받는 FILE_DETAILS 경로/명시 reconcile; 사람이 다른 과목을 고르면 자동 이동 없이 RECONCILE_REQUIRED (§3.4 r10 R2) | `_claim_request_unlocked` | 기존 ASSIGN_COURSE Draft → AUTO 완료 → 사람 변경 → 새 제출 → Session/Material/폴더/binding 보존·상태 회귀 0 | 해당 없음 |
| T7 | 항목 상태·canonical binding·완료 상태를 검증 없이 되돌리지 않는다 | handover 경로 | 상태 회귀 0 테스트 | 해당 없음 |
| T8 | 기본값·off·capability 부재: 스캔·handover 0건 | `_auto_enabled()` 게이트 | enabled=false → 스캔 호출 0 | 해당 없음 |
| T9 | readiness: `terminal_scan_cursor`, `terminal_changes_open`, `canvas_observation_status` 카운터 (§4 O2) | `_classification_readiness` | 값 검증 | 해당 없음 |

## 범위 밖

S2(Jev), S4 태그, retrieval 필터(P-D), 공지·첨부 intake(P-E), 새 Canvas 토큰 저장/연결 UI(Settings 기존 기능 재사용), Canvas 파일 다운로드 어댑터(P-E).
