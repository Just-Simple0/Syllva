# Intake 분류 v2 — P-B 구현 슬라이스 (2026-10-10)

권위 문서는 `docs/plans/intake-classification-v2.md`(r14 PLAN GO)이며, 이 문서는 그 §9 P-B 행을
**리뷰 가능한 세 슬라이스**로 나눈 실행 순서만 정한다. 계약·불변식은 v2 계획서가 우선한다.
각 슬라이스는 P-A와 같은 방식으로 Native FINAL 리뷰(120k 토큰 패킷)를 받고 GO 후 커밋·PR·머지한다.

## 공통 경계

- `intake.classification.enabled=false`(기본)에서는 어떤 슬라이스도 현행 HUMAN-only 동작을 바꾸지 않는다.
- `enabled=true`여도 Notion 프로필이 `legacy5-cls`/`c5-range-v2`로 VERIFIED되기 전에는 제안·분류 속성을 Notion에 쓰지 않고 로컬 `intake_suggestions`·`intake_items` 컬럼에만 보관한다(§5 R4).
- S2(Jev)는 P-C 범위다. P-B에서 S1 미결 항목은 `CLASSIFIER_DISABLED` 사유로 S3(HUMAN)로 간다(§4 credential 미명시 = DISABLED).
- `retrieval.v2_exposure_gate=false`(기본)에서 v2 신규 Material은 어떤 intent에서도 검색 노출 0건(§9 P-B 음성 테스트).

## P-B1 — S0/S1 분류 + S3 제안 필드 + DEFERRED 게이트 (자동 실행 없음)

범위(§3.1, §3.3, §4 예산 전이 중 `_sync_unlocked()` 경계, §5, §6.1 매트릭스 사전조건):
1. `src/uls/intake/classification/pipeline.py`(신규): `classify_intake_item()` — S0 출처 메타(업로드 폴더/`canvas_drive_bindings` 결속 → origin), S1 `classify_by_rules`, 과목·주차·날짜 축(`transcript_signals`, alias index = config `courses[].aliases` + 과목명/코드), 회차 달력 매칭(`recording_calendar_current` projection → `build_calendar`/`match_transcript`), 처리 방식(`handling_mode`; NORMALIZE 후보는 다운로드 바이트 ≤ `max_source_bytes`로 서명·probe, 초과 `SOURCE_TOO_LARGE`), 결정 유형(RULE / S3 사유 코드). 순수 함수 + state 조회만.
2. `_sync_unlocked()`: `enabled`일 때 발견 항목마다 ① 분류 → ② `classification_records`(결정된 경우)·`intake_items` 분류 컬럼·`intake_suggestions` 영속 → ③ `classification_state` 전이(`HUMAN`이 된 뒤에만 Input Request 생성; `DEFERRED`는 P-C에서만 발생하지만 억제 경로는 지금 넣음) → ④ draft 생성 시 §3.3 제안 6필드 기록(v2 프로필 VERIFIED일 때만; USER 필드는 비움). ASSIGN_COURSE → FILE_DETAILS 후속 draft에도 같은 제안 재기록.
3. File Intake projection에 `AI Kind`/`Origin`/`Classification Source`/`Classification Record`(v2 프로필에서만).
4. HUMAN FILE_DETAILS claim의 plan 생성 전 `handling_mode` 사전조건(§6.1 r10 R4): 조합이 매트릭스에 없으면 외부 쓰기 전 `NEEDS_INPUT`(`Error=FORMAT_KIND_MISMATCH`).
5. `_claim_request_unlocked()` 시작부 터미널 receipt 고정 거부(`REQUEST_TERMINAL`, §3.4 R2).
6. readiness `classification` 블록(§4 O2; P-B1에서는 `DISABLED`/`READY` + 카운터).

수용: fixture 80항목 중 Drive 전사문 8건이 S1 TRANSCRIPT 확정 → 달력 부재/불일치로 `NEEDS_INPUT` + 제안(과목·주차·Kind·날짜·source·note) + Session 0건; UNKNOWN origin Material 비발행(P-B1은 AUTO 자체가 없음); 형식-Kind 부적격은 S3 사유 코드; `enabled=false`에서 기존 preview 통합 테스트 전부 불변; v2 프로필 미검증 시 Notion 제안 쓰기 0건.

## P-B2 — AUTO 권한 활성화·Draft 자동 종료·AUTO 실행 (§3.4, §3.5, §3.7, §6.1 opaque)

P-B2는 리뷰 단위로 다시 둘로 나눈다.

### P-B2a — 단계 A·AUTO_PENDING·Draft 자동 종료·장벽 (실행 없음)
- 단계 A 적격성(순수 `ClassificationOutcome.stage_a_blockers()` + worker의 바이트 증명·중복 콘텐츠 게이트): 통과 항목만 `AUTO_PENDING` plan(`plan_revision = sha256(auto-plan, classification_revision_hash, workspace_fingerprint)`, 멱등)을 받고 `classification_state=CLASSIFIED`가 되며 **HUMAN draft를 만들지 않는다**; 하나라도 막히면 S3(HUMAN)로 draft + 제안 + 차단 사유 코드(`AUTO_BLOCK_*`, `DUPLICATE_CONTENT`, `AUTO_NOT_ENABLED`).
- §3.5 자동 종료: 같은 intake의 untouched Draft(상태 Draft·체크박스 false·USER 필드 blank·revision hash 일치)에 한해 preflight(바이트 재증명·source version) → PENDING intent(+pre_close snapshot) → `Auto Resolved`/`Result Reference` 쓰기(write-attempt ledger) → readback → **receipt+intent 원자 커밋**. 사람이 손댄 Draft/Submitted/Claimed는 AUTO plan `SUPERSEDED`(+job VOID) + HUMAN 유지.
- 복구 (i)~(iv)는 틱 시작(발견·claim 이전)에 수행; PENDING intent는 `_submitted_request_keys()`·`claim_request()`의 영속 장벽. `_run_layout_workspaces()`/큐의 터미널에 `AutoResolved` 포함. readiness에 `auto_enabled`, `auto_pending_plans`, `pending_auto_resolve_intents`.
- 실행·승격은 없다: `AUTO_PENDING` plan은 P-B2b까지 대기한다(기본 `enabled=false`라 운영 영향 없음).

### P-B2b — 단계 B 승격·AUTO 실행 컨텍스트·preflight·opaque 등록

1. 단계 A(`AUTO_PENDING` 적격성): Kind·과목·달력/Session 근거·probe·바이트 동일성(`byte_sha256`/`byte_md5`)·중복 콘텐츠 게이트·provenance·alias 근거. 실패 → S3/RECONCILE, Draft 불변.
2. 단계 B: §3.5 Auto Resolved intent(preflight → PENDING intent → Notion 쓰기 → readback → receipt+intent 원자 커밋) → `promote_auto_plan`. 복구 (i)~(iv)와 rollback intent.
3. 실행: `ClassificationExecutionContext`, `_process_item_unlocked()`의 authority 분기, mutation preflight와 각 쓰기 직전 (a)~(d) 재확인, `_workspace_for_item()`의 AUTO 과목 결속, job claim 시 plan revision/authority 삼자 검사, SUPERSEDED → job VOID.
4. `REGISTER_OPAQUE_NO_RETRIEVAL` 경로(§6.1 r11 R1 순서) — HUMAN v2 Kind의 non-PDF도 이 경로로.
5. `_submitted_request_keys()`/`_run_layout_workspaces()` 터미널 판정에 `AutoResolved` 포함.

수용: §9 P-B 행의 AUTO 복구 멱등·사람 우선·AutoResolved 재Claim 불가·preflight 실패 시 외부 쓰기 0·opaque 등록 테스트.

## P-B3 — Canvas 메타 관측 어댑터 + 터미널 스캔 + handover (§6.3, §3.4 관측 경로·인계 규칙)

1. 읽기 전용 Canvas 관측 어댑터(기존 probe 규칙 이관) → `record_canvas_observation`/`reconcile_canvas_collection`/`replace_recording_calendar_current`.
2. AutoResolved receipt bounded reconciliation scan(`auto_resolved_scan_cursor`, `max_terminal_scan`) → `terminal_change_ledger` → 새 HUMAN generation(`derivation_version=V2`) + `handover_record`.
3. ASSIGN_COURSE 재실행 보호·교차 권한 인계 재검증.

수용: §9 P-B 행의 잔여 항목 + §6.3 테스트.

## 구현 메모

- `classification_records.course_basis.type`은 구현에서 `upload_folder|config_alias|notion_alias`를 쓴다. Canvas course map은 근거 유형이 아니라 단계 A와 첫 mutation preflight의 교차 검증이며 `canvas_map` 유형은 P-B3에 남긴다(P-B2a r14 O1).
