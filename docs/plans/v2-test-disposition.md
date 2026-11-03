# v2 테스트 처분표 (R1b-6)

구현 명세서 §15 R1b 완료 기준의 "기존 테스트 파일 158개를 파일별로 재작성/폐기 판정해 목록 문서화"에 해당한다.
기준: 2026-11-02, `main` = R1b-5 후속 머지 직후. 대상은 `tests/contract/v2/`와 아래 "v2 신규" 목록을 뺀 모든 `tests/**/test_*.py`.

이 표는 `tests/contract/v2/test_v2_test_disposition.py`가 파일 시스템과 대조한다. 레거시 테스트를 지우거나 새로 만들면 이 표도 같은 PR에서 고친다(누락·잔존 행은 실패).

## 판정 정의

| 판정 | 뜻 |
|---|---|
| 유지 | v2에서 유지되는 모듈(정규화, EphemeralStore와 그 위의 `retrieval.capabilities` allowlist 관리 — 설계서 §1 EphemeralStore 행·§6.1, locator, 락·보안 파일 프리미티브)을 그대로 검사한다. 파일을 옮기거나 단언을 v2 모듈로 바꾸는 정도만 필요 |
| 재작성 | 불변식·동작 의미는 v2에도 있다. 해당 단계에서 v2 구현 위에 새로 쓴다. 레거시 파일은 대개 그때 지우지만, R1c에서 지울 모듈을 (직접 또는 테스트 헬퍼를 거쳐) import하는 파일은 R1c에서 먼저 지우고 행은 남긴다: 대상 칸이 `R1c 삭제 → Rn`인 행(16개)이며, 파일이 없어도 표 대조 테스트가 허용하는 유일한 경우다 |
| 대체 | 같은 불변식을 v2 계약 테스트(C-xx) 또는 v2 테스트 파일이 이미(또는 표시된 단계에서) 고정한다. 레거시 파일은 대응 코드와 함께 삭제 |
| 폐기 | 기능 자체가 v2에서 없어진다(Notion, Drive, Google OAuth, `study_notes`, 외부 LLM 보조 분류, provider 쓰기 복구) |
| 보류 | 선택 기능(MCP 어댑터, GitHub 어댑터, 원격 MCP)이다. 기본 비활성이며 어댑터 작업 때 다시 판정 |

## 집계

| 판정 | 파일 수 |
|---|---|
| 유지 | 20 |
| 재작성 | 47 |
| 대체 | 1 |
| 폐기 | 6 |
| 보류 | 4 |
| **합계** | **78** (R1c 삭제 후 표에 남은 행; R3-1에서 1개 이동) |

R1c 삭제 PR 이후 표에 남은 행이다(초기 판정 158개 중 79개는 아래 'R1c에서 삭제된 파일'로 옮김). 재작성 47개의 단계: R2 10 · R3 23 · R5 11 · R6 3. 그중 23개는 R1c에서 파일이 먼저 지워져 행만 남는다(`R1c 삭제 → Rn`). 남은 `폐기`·`대체` 7개는 아직 살아 있는 코드(설정의 Google OAuth·provider 점검 — R2 설정 재작성, Behavior Contract v3 — R4)를 검사하므로 그 코드와 함께 지운다. 집계는 `test_v2_test_disposition.py`가 표에서 다시 계산해 대조한다.

## 삭제 순서와 import 금지

- v2 모듈(`state.v2`, `library`, `ingestion.local`, `normalization.local`, `normalization.chunks`, `retrieval.natural`, `retrieval.ranking`, `retrieval.service`, `domain.source_ref`)은 삭제 예정 코드(`adapters.notion`, `adapters.drive`, `study_notes`, 구 `domain.approval_identity`, v1.2 엔진·resolver·청크 분할기, v1.2 `state` 모듈들, `enrichment.writer`, `config.google_oauth`, `proposal`), v2 런타임에서 금지된 외부 LLM 어댑터(`adapters.llm`, `no-external-llm-adapter`; 존폐는 R5에서 결정)와 바깥 계층(`intake`, `settings`, `cli`, `mcp`, `runtime`, `worker`)을 직접·간접 어느 경로로도 import하지 않는다(`pyproject.toml` `v2-no-legacy`, 예외 없음).
- 실제 삭제는 R1c 삭제 PR에서 했다(정오표 E19): 위 import 금지 대상과 그것에 의존하던 v1.2 런타임(CLI 명령, 워커, `runtime`, 레거시 intake·enrichment 생성기·S2·분류 원장)을 지우고, import-linter 예외(`ignore_imports`)를 모두 비웠다. `scripts/invariants_baseline.json`에는 보류 중인 원격 MCP(`mcp/transports/oauth.py`, R6) 1건만 남는다. 설정 서버(`settings/` app·launcher·security·static)는 명세 §1.1대로 R2 UI 서버의 출발점이라 남겼다.

## 파일별 판정

| 파일 | 판정 | 대상 단계·대체 | 근거 |
|---|---|---|---|
| `tests/contract/test_additional_tools.py` | 재작성 | R1c 삭제 → R6 (선택 MCP 어댑터 재도입 시, C-01·C-13) | MCP 서버 도구 목록. 어댑터 재도입 시 C-01·C-13 기준으로 재작성 |
| `tests/contract/test_behavior_contract_exam_activity.py` | 대체 | C-15·C-23 (R4) | Behavior Contract v3 → v4 프롬프트 템플릿 |
| `tests/contract/test_capability_stale.py` | 유지 | R1c에서 v2 서비스 연결 확인 | EphemeralStore capability 신선도(유지 모듈) |
| `tests/contract/test_enrichment_commit_ordering.py` | 재작성 | R1c 삭제 → R5 (C-06) | Notion 커밋 순서 → `ai_artifacts` 저장 순서 |
| `tests/contract/test_enrichment_completeness_states.py` | 재작성 | R1c 삭제 → R5 (C-06, C-12) | AI 산출물 완전성 상태 |
| `tests/contract/test_enrichment_input_gates.py` | 재작성 | R1c 삭제 → R5 (C-06) | 입력 게이트(Partial·stale 입력 거부) |
| `tests/contract/test_enrichment_metadata_trust.py` | 재작성 | R1c 삭제 → R5 (C-06) | 산출물 메타데이터 신뢰 경계(I2) |
| `tests/contract/test_ephemeral_capability.py` | 유지 | — | EphemeralStore(유지) |
| `tests/contract/test_ephemeral_resolution.py` | 유지 | — | EphemeralStore 해석 선택(유지) |
| `tests/contract/test_ephemeral_restart.py` | 유지 | — | 재시작 시 휘발(C-11과 정합) |
| `tests/contract/test_exam_scope_lifecycle.py` | 재작성 | R1c 삭제 → R5 (+C-04·C-05) | 시험 범위 제안·확정 수명주기 |
| `tests/contract/test_get_activity_context.py` | 재작성 | R1c 삭제 → R3 (E13) | `get_activity_context`를 `RetrievalService` 연산으로 |
| `tests/contract/test_get_exam_context.py` | 재작성 | R1c 삭제 → R5 (E13) | `get_exam_context`를 `RetrievalService` 연산으로 |
| `tests/contract/test_github_retrieval.py` | 보류 | 선택 GitHub 어댑터 | 정확한 ref만 증거(설계서 §1 선택 기능, 기본 꺼짐) |
| `tests/contract/test_locator_containment.py` | 유지 | — | locator 포함 관계(도메인 유지) |
| `tests/contract/test_material_enrichment_generation.py` | 재작성 | R1c 삭제 → R5 (C-06, C-21) | 외부 LLM 어댑터 → 도구 없는 에이전트 |
| `tests/contract/test_mcp_dispatch_distinctness.py` | 재작성 | R1c 삭제 → R6 (선택 MCP 어댑터 재도입 시; 신뢰 도메인 분리) | 워커/MCP 자격증명 분리(신뢰 도메인 분리로 대체 예정) |
| `tests/contract/test_mcp_exam_activity_serialization.py` | 보류 | 선택 MCP 어댑터 | MCP 직렬화 |
| `tests/contract/test_mcp_runtime.py` | 재작성 | R1c 삭제 → R6 (선택 MCP 어댑터 재도입 시, C-01·C-13) | MCP 전송·read-only 경계 |
| `tests/contract/test_pd_assignment_filter.py` | 재작성 | R1c 삭제 → R3 | 과제 정책 필터(fail-closed) |
| `tests/contract/test_persistence_jobkey.py` | 유지 | — | 잡 키 유도(`orchestration.jobs`, 설정 저널이 사용) |
| `tests/contract/test_phase4_capability_review.py` | 유지 | — | capability 관리(ephemeral, 유지) |
| `tests/contract/test_phase5_capability_revocation.py` | 유지 | — | capability 폐기(ephemeral, 유지) |
| `tests/contract/test_session_enrichment_generation.py` | 재작성 | R1c 삭제 → R5 (C-06, C-21) | 세션 요약 생성 → 도구 없는 에이전트 |
| `tests/contract/test_settings_canvas_checks.py` | 재작성 | R3 | Canvas 목적지·bounded GET(토큰 원점 고정) |
| `tests/contract/test_settings_canvas_service.py` | 재작성 | R3 | Canvas 연결 서비스 |
| `tests/contract/test_settings_cas.py` | 재작성 | R2 | 설정 CAS(설정 화면은 Tauri UI). 고정 sleep 불안정 테스트 포함 |
| `tests/contract/test_settings_composition.py` | 재작성 | R2 | 설정 조립·fake 모드 격리 |
| `tests/contract/test_settings_config.py` | 재작성 | R2 | 허용 목록 설정 스냅샷 |
| `tests/contract/test_settings_credential_admission.py` | 재작성 | R3 (I9) | 자격증명 등록(Canvas 토큰만 남음) |
| `tests/contract/test_settings_credential_http.py` | 재작성 | R3 (I9) | 자격증명 HTTP 경계 |
| `tests/contract/test_settings_credential_journal.py` | 재작성 | R3 (I9) | 자격증명 저널 |
| `tests/contract/test_settings_credential_roles.py` | 재작성 | R3 (I9) | 자격증명 역할(Notion/Google 역할 제거) |
| `tests/contract/test_settings_credential_service.py` | 재작성 | R3 (I9) | 자격증명 서비스·키체인 |
| `tests/contract/test_settings_credential_stores.py` | 재작성 | R3 (I9) | 자격증명 저장소 |
| `tests/contract/test_settings_google_oauth.py` | 폐기 | — | Google OAuth |
| `tests/contract/test_settings_google_oauth_composition.py` | 폐기 | — | Google OAuth |
| `tests/contract/test_settings_google_oauth_http.py` | 폐기 | — | Google OAuth |
| `tests/contract/test_settings_google_oauth_save.py` | 폐기 | — | Google OAuth |
| `tests/contract/test_settings_http.py` | 재작성 | R2 (C-04, C-13, C-20) | UI 세션·CSRF·헤더 경계 → Core 로컬 HTTP |
| `tests/contract/test_settings_journal.py` | 재작성 | R2 | 작업 저널·크래시 복구 |
| `tests/contract/test_settings_provider_checks.py` | 폐기 | — | Notion/Drive provider 점검(Canvas 점검은 R3) |
| `tests/contract/test_settings_ui.py` | 재작성 | R2 (C-18) | 브라우저 설정 셸 → Tauri UI |
| `tests/integration/test_enrichment_end_to_end.py` | 재작성 | R1c 삭제 → R5 | 적재→요약→검색 e2e |
| `tests/integration/test_exam_activity_end_to_end.py` | 재작성 | R1c 삭제 → R5 | 시험·활동 e2e |
| `tests/integration/test_intake_worker_canvas_attachments.py` | 재작성 | R1c 삭제 → R3 | Canvas 첨부 다운로드(Drive 바인딩 제거) |
| `tests/integration/test_intake_worker_canvas_auto_calendar.py` | 재작성 | R1c 삭제 → R3 | Canvas 자료 자동 계획 |
| `tests/integration/test_intake_worker_canvas_observation.py` | 재작성 | R1c 삭제 → R3 | Canvas 메타데이터 관찰 |
| `tests/integration/test_native_runtime.py` | 재작성 | R1c 삭제 → R2 | Core sidecar e2e(인메모리 엔드포인트) |
| `tests/unit/test_activity_normalization.py` | 유지 | 청크 단언은 R1c에서 `normalization.chunks`로 | 활동 정규화(유지) |
| `tests/unit/test_c4_classifier_timestamp_grammar.py` | 유지 | — | 분류기·정규화 타임스탬프 문법 공유 |
| `tests/unit/test_c8_poll_interval_default.py` | 재작성 | R2 | 폴링 기본값(설정 재정의와 함께) |
| `tests/unit/test_canvas_attachments.py` | 재작성 | R1c 삭제 → R3 | Canvas 첨부 → 로컬 blob(Drive 제거) |
| `tests/unit/test_canvas_composition.py` | 재작성 | R1c 삭제 → R3 | Canvas 관찰자 조립 |
| `tests/unit/test_canvas_observer.py` | 재작성 | R1c 삭제 → R3 | Canvas 메타데이터 관찰(`state.sqlite` → v2) |
| `tests/unit/test_capability_manager_purge.py` | 유지 | — | capability 만료 정리 |
| `tests/unit/test_config_loader_credentials.py` | 재작성 | R3 (I9) | 자격증명 설정 절 |
| `tests/unit/test_config_validation.py` | 재작성 | R2 | 설정 검증(설정 화면이 Tauri UI로 옮겨질 때 Notion/Drive 항목 제거) |
| `tests/unit/test_credential_resolver.py` | 재작성 | R3 (I9) | 자격증명 해석 |
| `tests/unit/test_credential_resolver_file_source.py` | 재작성 | R3 (I9) | 파일 출처 자격증명 |
| `tests/unit/test_credential_set_cli.py` | 재작성 | R1c 삭제 → R3 (I9) | 자격증명 설정 CLI |
| `tests/unit/test_enrichment_schemas.py` | 재작성 | R5 (C-06) | AI 산출물 스키마 |
| `tests/unit/test_google_oauth_config.py` | 폐기 | — | Google OAuth |
| `tests/unit/test_intake_classification_config.py` | 유지 | — | 분류 설정(분류기는 `intake.v2`가 재사용) |
| `tests/unit/test_intake_classification_pipeline.py` | 유지 | — | S0/S1/S3 순수 결과(분류기는 `intake.v2`가 재사용) |
| `tests/unit/test_intake_classification_rules.py` | 유지 | — | 결정적 규칙·분류표(분류기는 `intake.v2`가 재사용) |
| `tests/unit/test_knu_lms_apply_lock.py` | 재작성 | R3 | LMS 동기화 적용 락 |
| `tests/unit/test_knu_lms_probe.py` | 재작성 | R3 | LMS 탐침 |
| `tests/unit/test_knu_lms_sync.py` | 재작성 | R3 | LMS 동기화(현 키체인 의존 기준선 실패 10건) |
| `tests/unit/test_lms_platform.py` | 유지 | — | OS별 소유권·락 프리미티브 |
| `tests/unit/test_local_worker_lock.py` | 유지 | R1c 워커에서 사용 | 로컬 워커 락 |
| `tests/unit/test_mcp_oauth.py` | 보류 | 원격 MCP (R6) | 원격 MCP OAuth |
| `tests/unit/test_mcp_oidc.py` | 보류 | 원격 MCP (R6) | 원격 MCP OIDC |
| `tests/unit/test_normalization_pdf.py` | 유지 | — | PDF 추출(유지) |
| `tests/unit/test_retry_backoff.py` | 유지 | — | 재시도 백오프 |
| `tests/unit/test_secure_file.py` | 유지 | — | 보안 파일 쓰기 |
| `tests/unit/test_settings_launcher.py` | 재작성 | R2 | 설정 런처 → Tauri 셸 |
| `tests/unit/test_transcript_normalization.py` | 유지 | 청크 단언은 R1c에서 `normalization.chunks`로 | 전사문 정규화(유지) |

## R1c에서 삭제된 파일 (기록)

R1c 삭제 PR(정오표 E19)에서 대응 코드와 함께 지웠다. `폐기`는 기능 제거, `대체`는 표시된 v2 계약 테스트가 같은 불변식을 고정, `재작성(R1c)`은 v2 intake(`test_v2_intake_local.py`, `test_v2_intake_replace.py`)와 v2 저장소 테스트로 대체되었다.

- `tests/contract/test_automation_queue_lifecycle.py` — 대체 (C-17 (R2)): Notion 자동화 큐 구현. 적용 재제출 idempotent·거부 후 적용 불가·원본 변경 시 SUPERSEDED 의미는 C-17(설계서 §1 "Proposal·Approval 라이프사이클 — 의미 유지")
- `tests/contract/test_c5_haa.py` — 대체 (C-04·C-05·C-17 (R2 Human-Proof)): Notion HAA 마커 승인 경로. 사용 범위 의미는 R2에서 v2 게이트 위에 재작성
- `tests/contract/test_c5_state_preconditions.py` — 대체 (C-04·C-05·C-17 (R2 Human-Proof)): 승인 채택 전제조건(SQLite v1.2)
- `tests/contract/test_c5_usage_range.py` — 대체 (C-04·C-05·C-17 (R2 Human-Proof)): 사용 범위 영수증·Notion 쓰기
- `tests/contract/test_doctor_credential_resolver.py` — 폐기: Notion/Google 자격증명 doctor 진단. 설정 진단은 R2에서 새로 정의
- `tests/contract/test_doctor_purpose_scoped_diagnostics.py` — 폐기: 같은 이유
- `tests/contract/test_domain_contract_conformance.py` — 재작성 (R1c): `state.sqlite` → `state.v2`
- `tests/contract/test_error_taxonomy_mapping.py` — 재작성 (R1c): 오류 분류·재시도 매핑(Notion/Drive fake 제거)
- `tests/contract/test_exam_scope_identity.py` — 대체 (C-04·C-05·C-17 (R2 Human-Proof)): `Scope Confirmed` human-only. 시험 범위 식별 의미는 R5
- `tests/contract/test_get_session_context.py` — 대체 (C-02·C-10·C-25, C-06 (R5)): 원본·파생물 버전 단언은 `test_v2_retrieval_service.py`·`test_v2_chunk_strategy.py`가 고정. stale enrichment 제외(I5)는 C-06(R5), `Verified` 미확인 사용 제외·비불리언 거부는 R3(과제)·R5(요약)에서 Human-Proof 승격 값 기준으로 재작성
- `tests/contract/test_human_gate_self_approval.py` — 대체 (C-04·C-05·C-17 (R2 Human-Proof)): 설계서 §1 처분표에 명시된 대체
- `tests/contract/test_human_gate_writes.py` — 대체 (C-04·C-05·C-17 (R2 Human-Proof)): Notion human-gate 쓰기
- `tests/contract/test_intake_worker_google_oauth.py` — 폐기: Google OAuth 워커 attestation
- `tests/contract/test_job_lifecycle.py` — 재작성 (R1c): 처리 잡 상태(§3.7 `jobs`)
- `tests/contract/test_jobkey_enforcement.py` — 재작성 (R1c): 잡 키 유일성
- `tests/contract/test_persistence_allocation.py` — 재작성 (R1c): v1.2 SQLite id 할당 → v2 스키마
- `tests/contract/test_persistence_migration.py` — 대체 (`test_v2_state_migrate.py`): v2 마이그레이션 러너로 대체
- `tests/contract/test_phase4_followup_revocation.py` — 대체 (C-10): v1.2 엔진 capability 기반 → v2 allowlist
- `tests/contract/test_phase4_guarded_workflow.py` — 대체 (C-04·C-05·C-17 (R2 Human-Proof)): Notion guarded writer 승인 흐름
- `tests/contract/test_phase4_prewrite_approval.py` — 대체 (C-04·C-05·C-17 (R2 Human-Proof)): 승인 최신성 재검증 → 대상 재검증(D§5.1)
- `tests/contract/test_phase4_producer_boundaries.py` — 폐기: Notion 큐 producer 경계
- `tests/contract/test_phase4_remaining_contracts.py` — 폐기: Phase4 Notion/Drive 안전 경계(해당 불변식은 C-03·C-04·C-10이 v2에서 고정)
- `tests/contract/test_phase4_retrieval_review.py` — 대체 (C-02·C-10·C-14): v1.2 엔진 검색 회귀
- `tests/contract/test_phase4_rev10_audit.py` — 폐기: provider 쓰기 후 마커 정합(로컬 트랜잭션으로 불필요)
- `tests/contract/test_phase4_rev10_pages.py` — 폐기: Notion 페이지 범위
- `tests/contract/test_phase4_rev3_approval.py` — 대체 (C-04·C-05·C-17 (R2 Human-Proof)): 큐 승인 귀속
- `tests/contract/test_phase4_rev3_identity.py` — 폐기: Drive 큐 미러·구 `approval_identity`
- `tests/contract/test_phase4_rev3_producer.py` — 폐기: Notion 큐 producer
- `tests/contract/test_phase4_rev3_retrieval.py` — 대체 (C-10): v1.2 엔진 capability 경계
- `tests/contract/test_phase4_rev4_approval.py` — 대체 (C-04·C-05·C-17 (R2 Human-Proof)): 호출자 제공 승인 기록 거부
- `tests/contract/test_phase4_rev4_producer.py` — 폐기: Notion producer 최종 읽기 신선도
- `tests/contract/test_phase4_rev4_retrieval.py` — 대체 (C-02·C-10 (I5)): 읽기 시점 신선도 → 현재 버전 파생물만 근거
- `tests/contract/test_phase4_rev5_identity.py` — 폐기: Notion 논리 그래프 ID 드리프트
- `tests/contract/test_phase4_rev5_siblings.py` — 폐기: Notion 형제 식별
- `tests/contract/test_phase4_rev6_producer.py` — 폐기: Notion producer 본문 읽기
- `tests/contract/test_phase4_rev6_recovery.py` — 폐기: provider 쓰기 복구
- `tests/contract/test_phase4_rev7_recovery.py` — 폐기: provider 쓰기 복구
- `tests/contract/test_phase4_rev8_approval.py` — 대체 (C-04·C-05·C-17 (R2 Human-Proof)): 승인 귀속
- `tests/contract/test_phase4_rev8_producer.py` — 폐기: Notion producer 사용 식별
- `tests/contract/test_phase4_rev9_pages.py` — 폐기: Notion 전체 페이지 색인
- `tests/contract/test_phase4_rev9_recovery.py` — 폐기: provider 쓰기 복구
- `tests/contract/test_phase4_writer_boundary.py` — 폐기: Notion provider writer(쓰기 경계는 C-03)
- `tests/contract/test_readiness_funnel.py` — 재작성 (R1c): 처리 상태 집계(`status()`)
- `tests/contract/test_session_resolver.py` — 대체 (C-14): v1.2 엔진 resolver → `NaturalResolver`
- `tests/contract/test_source_version_consistency.py` — 대체 (`test_v2_gate_db_triggers.py`, `test_v2_local_ingest_normalize.py`): 원본 버전 정합 → v2 식별자·버전 트리거
- `tests/contract/test_transcript_ingest_ordering.py` — 재작성 (R1c): 전사문 적재 순서(Drive/Notion fake 제거)
- `tests/contract/test_v2_auto_material_gate.py` — 재작성 (R1c): 자동 분류 자료 노출 게이트 → `NEEDS_REVIEW`/확인 필요 상태
- `tests/contract/test_worker_cli.py` — 재작성 (R1c): 워커 CLI → 로컬 intake 워커
- `tests/integration/test_c5_composition.py` — 대체 (C-04·C-05·C-17 (R2 Human-Proof)): 사용 범위 확장 조립(Notion)
- `tests/integration/test_intake_worker_classification_pb1.py` — 재작성 (R1c): S0/S1 분류(분류기 재사용, Notion 요청 제거)
- `tests/integration/test_intake_worker_classification_pb2a.py` — 재작성 (R1c): AUTO 계획 단계 A
- `tests/integration/test_intake_worker_classification_pb2b.py` — 재작성 (R1c): AUTO 승격·실행
- `tests/integration/test_intake_worker_classification_pb3b.py` — 재작성 (R1c): 요청 세대
- `tests/integration/test_intake_worker_classification_pb3c.py` — 재작성 (R1c): 자동 해소 종단
- `tests/integration/test_intake_worker_classification_pc2.py` — 폐기: S2 외부 LLM 분류(`no-external-llm-adapter`)
- `tests/integration/test_intake_worker_classification_profile.py` — 폐기: Notion 스키마 프로필
- `tests/integration/test_intake_worker_preview.py` — 재작성 (R1c): 워커 미리보기 경로
- `tests/integration/test_notion_classification_profiles.py` — 폐기: Notion
- `tests/integration/test_notion_modern_schema.py` — 폐기: Notion
- `tests/integration/test_study_note_composition.py` — 폐기: `study_notes` 쓰기 MCP(설계서 §1 폐기)
- `tests/integration/test_study_note_lifecycle_wiring.py` — 폐기: `study_notes`
- `tests/unit/study_notes/test_provider_recovery.py` — 폐기: `study_notes`
- `tests/unit/study_notes/test_store_validation.py` — 폐기: `study_notes`(정적 검사 기준선 26건의 출처)
- `tests/unit/test_alias_parser.py` — 재작성 (R1c): 과목 별칭 파서를 Notion 어댑터 밖으로
- `tests/unit/test_c1_state.py` — 재작성 (R1c): intake 식별 상태 → v2 상태 기계(§11.2)
- `tests/unit/test_c2_drive_marker_recovery.py` — 폐기: Drive
- `tests/unit/test_c4_route_intake_gate.py` — 재작성 (R1c): 과목/종류 fail-closed 게이트
- `tests/unit/test_c5_range_intent_state.py` — 대체 (C-04·C-05·C-17 (R2 Human-Proof)): 사용 범위 의도 상태(구 `approval_identity`)
- `tests/unit/test_classification_state.py` — 재작성 (R1c): 분류 원장 → v2 스키마
- `tests/unit/test_google_oauth_runtime.py` — 폐기: Google OAuth
- `tests/unit/test_intake_registry.py` — 재작성 (R1c): intake 등록(Drive 제거)
- `tests/unit/test_intake_request_kinds_v2.py` — 폐기: Notion 스키마별 요청 종류
- `tests/unit/test_intake_s2.py` — 폐기: S2 외부 LLM 보조
- `tests/unit/test_request_coordinator.py` — 재작성 (R1c): 요청 조정자
- `tests/unit/test_retrieval_basis_memo.py` — 대체 (C-02·C-10): v1.2 엔진 근거 메모
- `tests/unit/test_runtime_google_credentials.py` — 폐기: Google 자격증명
- `tests/unit/test_semester_retrieval_config.py` — 재작성 (R1c): 학기 설정 → `NaturalResolver(semester_starts)`
- `tests/unit/test_study_note_cli.py` — 폐기: `study_notes`
- `tests/unit/test_typesafe_choice.py` — 폐기: 외부 LLM TypeSafe Choice 어댑터(`no-external-llm-adapter`)

## R3에서 옮긴 파일 (기록)

- `tests/unit/test_canvas_download.py` → `tests/contract/v2/test_v2_canvas_http.py` (R3-1, 유지 판정 그대로; `settings/canvas_checks.py` → `syllva/canvas/http.py`)

## v2 신규 (판정 대상 아님)

- `tests/contract/v2/` 전체
- `tests/unit/test_v2_library_blobs.py`
- `tests/unit/test_v2_library_paths.py`
- `tests/unit/test_v2_resolver_cues.py`
- `tests/unit/test_v2_state_migrate.py`
- `tests/unit/test_v2_cli.py`
- `tests/unit/test_v2_core_selfcheck.py`
