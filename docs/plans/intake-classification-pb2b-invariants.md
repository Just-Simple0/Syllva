# Intake 분류 v2 — P-B2b 불변식 체크리스트와 구현 계획 (계획 리뷰용, 2026-10-11)

권위 문서는 `intake-classification-v2.md`(r14 PLAN GO)와 `intake-classification-pb-slices.md`다. 이 문서는 P-B2b의
각 불변식을 **구현 위치, 검증 테스트, crash/응답 손실 처리**와 짝지어 구현 전에 한 번 리뷰받기 위한 표다.
P-B2a 리뷰가 17차까지 이어진 이유(불변식이 구현 리뷰에서 하나씩 나옴)를 반복하지 않는 것이 목적이다.

## 범위

포함: 단계 B 승격(`AUTO_PENDING → PLANNED`), AUTO job, `ClassificationExecutionContext`, 실행 preflight와 쓰기 직전 재확인,
AUTO 과목 workspace 결속, TRANSCRIPT/Material 실행 경로(Session NEW/EXISTING, `REGISTER_OPAQUE_NO_RETRIEVAL`),
job claim 권한 검사, 공개 `process_item()` 직접 실행 거부, SUPERSEDED/RECONCILE 중 job VOID.

제외: S2(Jev), S4 태그, retrieval 필터(P-D), Canvas 관측 어댑터·터미널 스캔·handover·새 HUMAN generation(P-B3).
`intake.classification.enabled=false`(기본)에서는 현행 동작이 바이트 단위로 같아야 한다.

## 이미 P-B2a에서 확보된 것 (재사용, 재설계 금지)

`stage_a_blockers`, `_stage_a_blockers`(바이트 증명·중복 게이트), `_session_binding`, `_auto_preflight`(원본·parent·바이트·fingerprint·
provenance·eligibility·중복·item 상태), `_closure_gate`, `_judge_human_requests`(모든 HUMAN receipt·AutoResolved sibling 판정),
`abort_auto_resolve_intent_superseding`, `supersede_intake_plan`/`reconcile_intake_plan`(job VOID + item HUMAN 단일 트랜잭션),
`promote_auto_plan`(state: receipt·intent·generation 검사), `_unresolved_closure` 장벽, 터미널 receipt 거부.

## 불변식 표

| ID | 불변식 (계획 §) | 구현 위치 | 검증 테스트 | crash / 응답 손실 |
|---|---|---|---|---|
| E1 | 승격은 같은 intake에 receipt가 없거나 **모든 receipt가 `AutoResolved`**일 때만 (§3.4 M2, 단계 B). Applied/Cancelled HUMAN receipt는 승격 허가가 아니라 사람 우선 신호다: `_judge_human_requests`가 이미 HUMAN 판정으로 plan을 SUPERSEDED 처리하며, 미해결 intent·generation이 있으면 승격 0건 | 신규 `_promote_ready_plans()`를 틱의 분류 이후 단계에서 호출 → `state.promote_auto_plan` | 승격 전 Draft 존재 → 0건; 모든 Draft DONE → 1건; 미해결 intent → 0건; **Applied/Cancelled receipt 존재 → 승격 0건·SUPERSEDED** | 승격은 단일 immediate 트랜잭션; 재실행 멱등(이미 PLANNED면 변경 0) |
| E2 | 승격 직전 `_closure_gate` 동등 검사(원본 preflight → HUMAN 전체 재판정) 후에만 승격 (§3.4 M2, M3) | `_promote_ready_plans`가 `_auto_preflight` + `_judge_human_requests` 호출 | 승격 직전 HUMAN 변경 주입 → SUPERSEDED, job 0건 | 검사와 승격 사이 변경 → 실행 진입 preflight가 다시 잡음(E6) |
| E3 | AUTO job은 plan에서 `plan_revision`/`plan_authority`/과목을 복사하며 plan당 정확히 1개 (§3.4 Job 결속) | `_enqueue_plan_job`에 AUTO 분기(`course_key=record.course_key`), `job_key`는 plan_revision에서 파생. 승격과 job 생성을 하나의 state 트랜잭션 메서드(`promote_and_enqueue`)로 묶고, 그래도 남을 수 있는 `PLANNED + job 없음`은 틱 시작 repair가 멱등 생성 | 승격 재실행 → job 1건; HUMAN job 불변; **승격 직후·job 생성 전 crash 주입 → 재시작 후 job 정확히 1건** | 단일 트랜잭션 + repair 멱등 |
| E4 | job claim 시 job·item·plan의 revision/authority 삼자 일치, VOID/SUPERSEDED/RECONCILE는 claim 불가; 공개 `process_item()`은 AUTO plan 직접 실행 거부 (§3.4 O1). **claim한 job의 불변 `(job_id, plan_revision, plan_authority)`를 `_process_item_unlocked`까지 인수로 전달해 진입 직전에 다시 삼자 일치를 검증한다** (item의 현재 plan이 그 사이 바뀌었으면 중단) | `claim_job`(voided 제외) + `_run_jobs`가 job 식별자를 `_process_item_unlocked(..., claimed=ClaimedJob)`로 전달 + `process_item` 직접 실행 AUTO 거부 | VOID job 재시작 후 미실행; 불일치 권한 claim 거부; **claim 후 item.plan_revision 교체 → 실행 0건**; 직접 `process_item(AUTO)` 거부 | claim 후 plan이 닫힘 → 진입 검증에서 중단(E6) |
| E5 | `_process_item_unlocked`는 plan 조회 직후 authority로 먼저 분기, AUTO는 **자기 `_receipt_for_plan` 조회와 가짜 Submitted receipt 생성 없이** 불변 `ClassificationExecutionContext`를 공급 (§3.4 실행 컨텍스트). `bind_job_source_identity`는 `authority`와 `classification_revision_hash`를 영속 기록하고 복구 시 검증한다 | 신규 frozen dataclass + 분기; `bind_job_source_identity`(state) 컬럼·검증 확장; Kind dispatch/`_plan_operation_key`/`_ensure_*`는 공용 프로토콜 | AUTO 경로의 `_receipt_for_plan` 호출 0회·가짜 receipt 0건(E7이 요구하는 기존 HUMAN receipt readback과 AutoResolved terminal 대조는 별개로 허용); context 필드 불변성; 영속 source identity의 authority·revision hash 저장값; HUMAN 경로 바이트 동일 | context는 record에서 파생(재시작 시 재구성 동일) |
| E6 | 실행 진입 preflight(status=PLANNED 요구): 원본·parent·name·mime·바이트·source version·fingerprint·record 결속·alias·Canvas·달력·학기·Session inventory·중복·item 상태 (§3.4 M3). **자체 효과 예외(§2.3)**: 같은 plan_revision이 이미 만든 효과는 외부 변경과 구별해 재개한다 — NEW Session은 동일 reservation·operation key·`READBACK_OK`이며 ID/Course/Date가 record와 일치할 때만, Drive 이동은 동일 move intent·파일 ID·목적지·privacy readback으로만 인정하고, 이 경우 해당 항목(Sessions inventory, parent)은 "효과 반영 후 기대값"과 비교한다. 그 밖의 inventory·parent 변화는 계속 차단 | `_assert_classification_unchanged` = `_auto_preflight`의 PLANNED 변형 + `_own_effects(plan_revision)` 조회 | 항목별 변경 주입 → 외부 쓰기 0, plan RECONCILE_REQUIRED, job VOID; **Session 생성 응답 손실 후 재시작 → 처리 완료 또는 계약상 복구 상태 도달; 이동 후 provenance 전 crash 후 재시작 → 완료** | 자체 효과는 기존 operation key 멱등 복구 |
| E7 | 각 외부 쓰기 직전 (a) authority 유효 (b) 소스 불변 (c) HUMAN 요청 전체 재판정(AutoResolved terminal snapshot 포함) (d) 과목·origin 근거 재검증, 그리고 **Session 관련 쓰기 전에는 E9의 전용 point-of-use 검사 필수**: Session 예약, Session 생성, Material 예약/생성, 폴더 생성, binding, 이동, derivative. 자체 효과 예외는 E6과 동일 | 각 `_ensure_*`/`_move_*`/`_apply_binding` 앞에 공통 `_auto_write_gate(context)` | 쓰기 지점별 변경 주입 → 이후 추가 쓰기 0, 완료된 쓰기는 보존 | 기존 operation key 멱등 readback 복구(plan_revision 범위 키) |
| E8 | AUTO 과목은 `record.course_key`가 유일한 권위; `_workspace_for_item`은 `selected_course_key`/첫 workspace fallback 금지 (§3.4 M1) | `_workspace_for_item` AUTO 분기; Course relation·폴더 parent·provenance·job.course_key 동일 값 | 한 학기 2과목, AUTO 대상이 두 번째 과목 → 모든 위치 일치; workspace 없음 → job 거부 | 해당 없음 |
| E9 | TRANSCRIPT: record의 `session_mode`/`session_id`로 실행. **point-of-use 검사(§2.3)**: NEW — 다음 Session ID 선택 직전과 `_reserve_session()`·`_ensure_session_page()` 직전에 같은 과목·날짜의 Sessions 0건, 달력 projection 해시, `semester_range_basis`를 재조회해 record와 비교; EXISTING — 예약 시 inventory 해시·점유, 그리고 **Normalized Transcript 포인터를 바꾸기 직전** 다른 파일의 포인터·source binding을 재검사. operation key에 session_mode 포함 | `_reserve_session`/`_ensure_session_page` 공용 프로토콜 + `_session_point_of_use(context)` (P-B2a `_session_binding`·`_eligibility_mismatch` 재사용); `_plan_operation_key` | NEW→Session 1건; EXISTING→동일 ID 재사용; **preflight 직후 같은 날짜 Session 생성 주입 → Session 신규 생성 0; 예약 후 다른 전사문 포인터 주입 → 포인터 교체 0**; 응답 손실 후 중복 0 | 기존 Session 예약·readback 복구 |
| E10 | Material: `material_type_initial`(§5 표), HUMAN Role 우선 규칙 불변; NORMALIZE×PDF는 현행 경로; `REGISTER_OPAQUE_NO_RETRIEVAL`은 §6.1 r11 R1 순서 ①예약+private 검증 → ②생성(`Text Source=Unavailable`, `Text Status=Needs Review` 즉시) + 정확 readback → ③binding → ④REGISTERED→이동(ID 보존)→ORGANIZED → ⑤provenance·derivative 비발행. **PROVIDED_CODE(및 opaque 자료)는 로컬 `exposure_block_reason`을 영속 기록하고 재조회·재시작 후에도 보존한다** (§6.1, 검색 비노출 사유) | `_process_material` 처리 방식 분기, `_ensure_material_page` opaque 속성, `exposure_block_reason` 기록 | `.c`·`.csv`·`.json`·`.xlsx`: Material 1·binding 1·이동 1·derivative 0·검색 provenance 0·`exposure_block_reason` 영속/재시작 보존; 위장 MIME·서명 불일치 → S3; MATERIAL_PDF×PDF 기존 경로; PDF 생성 후 추출 실패 → Material 보존·Needs Review/Partial; **HUMAN이 Kind 기본값과 다른 유효 Role 선택 → 생성된 Materials.Type이 HUMAN Role**; 생성 응답 손실·binding 직후 crash·이동 응답 손실 후 중복 0 | 각 단계 기존 멱등 복구 |
| E11 | 처리 방식(NORMALIZE/OPAQUE/METADATA_ONLY/S3)은 AUTO·HUMAN 공통 함수로 Kind dispatch 앞에서 결정; HUMAN v2 Kind의 non-PDF는 OPAQUE 경로로 (현재 "not available yet" 거부를 대체) | `_process_item_unlocked` dispatch, `_human_handling_errors` 재사용 | HUMAN PROVIDED_CODE×.c → opaque 등록; HUMAN LAB×PDF → 기존; 매트릭스 밖 → 외부 쓰기 전 `FORMAT_KIND_MISMATCH` | 해당 없음 |
| E12 | 실행 중 plan이 SUPERSEDED/RECONCILE가 되면 다음 쓰기 경계에서 중단, 이미 성공한 쓰기는 삭제·재생성하지 않고 plan_revision 범위 멱등 복구 대상으로 남김; 새 HUMAN generation·handover는 P-B3 (§3.4) | `_auto_write_gate`(a), job VOID | 쓰기 중간 supersede → 추가 쓰기 0, 중복 entity 0 | 기존 복구 경로 |
| E13 | `_submitted_request_keys`/`_run_layout_workspaces`의 터미널 판정에 AutoResolved 포함 (P-B2a 완료, 회귀 유지) | 기존 | 기존 테스트 유지 | 해당 없음 |
| E14 | SOURCE/AI/USER와 데이터베이스별 속성 (§5, §8): **File Intake** — AI Kind/Origin/Classification Source/Classification Record(SYSTEM_DERIVED); **Materials** — AI Kind·Week, 그리고 *생성 시 초기값* `Type`(§5 매핑; HUMAN Role이 있으면 우선); **Sessions** — *생성 시 초기값* `Date`(=record.date). 기존 행의 USER 필드, `Verified`, `Scope Confirmed`, human-only 필드는 쓰지 않는다. `_plan_operation_key`는 HUMAN의 `request.material_role`과 AUTO의 `context.material_type_initial`을 권한별로 명시 매핑한다 | `_file_intake_properties`, Material·Session 생성 속성 빌더 | 신규 Session.Date = record.date; 신규 Material.Type·Week; 기존 USER 필드·human-only 필드 불변; 데이터베이스에 없는 속성 쓰기 0 | 해당 없음 |
| E15 | `retrieval.v2_exposure_gate=false`에서 v2 신규 Material 노출 0건; MCP 표면 read-only (§9 P-B 음성 테스트) | 기존 게이트 확인 + 음성 테스트 | `Text Status=Ready`여도 intent(CONCEPT·SESSION·EXAM·ACTIVITY·VERIFY) × `include_assignment`(true/false) × 표면(context, 신규 capability 발급, 기존 capability 재조회 `get_source_chunk`) 전 조합에서 노출 0 | 해당 없음 |
| E16 | 기본값·legacy·capability 부재: 승격·job·실행 0건, 현행 동작 바이트 동일 | `_auto_enabled()` 게이트를 승격·claim 경계에 적용 | enabled=false / legacy / full_intake=false → 승격 0, 기존 통합 테스트 전부 불변; **enabled=true에서 job 생성 후 기능/capability 비활성화·재시작 → 대기 job 실행 0·plan 보류(E12 경로)** | 해당 없음 |
| E17 | readiness: `planned_auto_plans`, `auto_jobs_pending/failed`, 기존 카운터 유지 (§4 O2) | `_classification_readiness` | 카운터 값 검증 | 해당 없음 |

## 종료 기준 (합의)

- REQUIRED = 위 불변식 또는 계획 계약 위반이며 도달 가능한 구체 트리거와 잘못된 영속 상태/무권한 외부 쓰기가 있는 경우.
- HARDENING = 방어 강화, 문서 정합성, 성능, 계획 밖 인터리빙. REQUIRED 0건이면 GO로 기록하고 HARDENING은 후속으로 둔다.
- 구현 단계의 각 라운드 전에 지적마다 같은 부류를 이 표로 전수 점검한 뒤 전송한다.

## 계획 리뷰 처분 (2026-10-11, 1차: REVISE 5/6 → 반영)

- R1 E14: 데이터베이스별 속성과 생성 시 초기값(Session.Date, Material.Type·Week) 예외로 재작성, `_plan_operation_key` 권한별 매핑 명시.
- R2 E9/E7: NEW/EXISTING별 point-of-use 검사(Session 0건·달력·학기·포인터 교체 직전 점유)와 E7 필수 호출.
- R3 E6/E7: 같은 plan_revision의 자체 효과(Session reservation+READBACK_OK, 이동 intent)를 외부 변경과 구별하는 예외와 재시작 완료 테스트.
- R4 E4/E5: claim한 job 식별자를 `_process_item_unlocked`로 전달해 진입 직전 재검증, `bind_job_source_identity`에 authority·revision hash 영속.
- R5 E10: PROVIDED_CODE 등의 로컬 `exposure_block_reason` 영속·재시작 보존.
- H1 E3 승격+job 트랜잭션/repair, H2 E1 Applied/Cancelled는 승격 허가 아님, H3 E5 테스트 문구 범위, H4 E10 테스트 확대, H5 E15 교차 조합, H6 E16 job 생성 후 비활성화 — 표에 반영.
