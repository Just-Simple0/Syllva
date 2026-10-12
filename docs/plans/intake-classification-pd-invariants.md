# P-D — 구간 태그(S4)와 검색 필터 불변식 표 (2026-10-12, rev2: 계획 리뷰 r1 반영)

권위: `intake-classification-v2.md` §2.2, §3.6, §7, §8, §9 P-D. 이 표는 **구현 방식 결정**을 포함한다.
구현 리뷰의 REQUIRED = 표 위반이 도달 가능한 경로에서 구체적 트리거로 (a) 과제 지시 구간이 과제 제외 intent의
기본 결과/capability/`get_source_chunk`에 노출되거나, (b) 잘못된 영속 상태·외부 쓰기를 만드는 경우뿐이다.
HARDENING(주입된 저장소 실패, 실제 파서가 만들지 못하는 입력, 문구)은 GO를 막지 않는다.

## 결정 (계획 대비 편차 — 리뷰 대상)

- **S4는 P-D에서 결정적 규칙만 쓰고 Jev를 호출하지 않는다.** 태그 결정은 **검색 시점에** 검증된 현재
  derivative의 청크에서 순수 계산한다(저장·캐시 없음). 따라서 STALE/PARTIAL 영속 상태, 백필 작업, 새
  outbound 경로가 없다(MCP는 계속 read-only, 네트워크 호출 추가 0). P-A의 `chunk_tag_*` 테이블은
  후속 Jev 정제용으로 남기고 P-D에서는 쓰지 않는다. 계획의 "애매한 청크만 Jev"는, 애매함(어휘 적중)을
  **보수적으로 YES(과제 지시 가능)** 로 처리해 같은 안전 결과(기본 검색 차단)를 얻는다. Jev 정제(적중했지만
  과제 아님 → NO)는 후속.
- **청크 단위는 P-D에서 페이지(Material)/타임스탬프 창(전사문)이다**(현재 `page_chunks`/`timestamp_chunks`). 계획 §7·§9의
  "같은 `M01:p2` 안에 NO/YES 청크 공존" 수용 사례는 재현할 수 없으므로 **수용 조건을 개정한다**: 페이지(또는 창)에
  과제 신호가 하나라도 있으면 그 페이지 전체가 YES로 차단된다(더 보수적). 섹션 단위 청킹은 후속.
- **어휘 비적중은 NO의 증명이 아니다.** NO는 "강한 어휘 없음"으로만 확정하고(문서 종류가 혼합 지시문을 담지 않는 경우에 한해),
  `LAB_MATERIAL`처럼 지시문이 본질인 문서는 규칙으로 NO를 증명할 수 없어 항상 미해결(PARTIAL)로 차단한다. 강한 어휘로 잡히지 않는
  표현의 과제 지시가 강의/보충/과목소개/전사문 문서에 섞여 있으면 탐지되지 않는 것은 **알려진 한계**(Jev 정제 후속)다.
- **MCP 도구 스키마는 바꾸지 않는다**(frozen 표면). `include_assignment`는 엔진 API 인자로만 존재(기본 False)하며
  MCP 노출은 명세 개정 뒤의 후속.

## 용어

- doc = 검색 문서 하나(Material `M…` 또는 Session 전사문). 청크 집합 C(doc) = 검증된 현재 derivative의
  `page_chunks`(Material) / `timestamp_chunks`(전사문) 전체에서 빈 내용을 뺀 것(엔진이 이미 쓰는 집합).
- `chunk_id = sha256(doc_id | locator | sha256(content))`.
- **과제 제외 경로** = CONCEPT·SESSION·EXAM과 **직접 Material 조회(`get_material_context`, scope MATERIAL)** 및 그 capability/`get_source_chunk`.
  `include_assignment=True`(엔진 API 인자, 기본 False)만 이를 푼다. ACTIVITY·VERIFY·USER_NOTE는 대상이 아니다(D9).

## 행

| # | 불변식 | 검증 |
|---|---|---|
| D1 | 태그 결정은 순수 함수 `assess_document(doc_id, chunks, doc_kind)`가 **검증된 현재 derivative의 청크와 현재 문서 Kind**만으로 계산한다. 같은 입력 → 같은 결정(결과에 `TAG_VOCAB_VERSION`/`QUESTION_SET_VERSION`/`TAG_RULE_VERSION` 포함). 네트워크·저장소 쓰기 없음 | 단위 |
| D2 | `assignment` 결정은 청크마다 {YES, NO, UNRESOLVED}. **YES**: 강한 과제 어휘(대소문자 무시, 라틴은 단어 경계·한글은 부분 일치, 버전 고정: assignment, homework, hw\d, deliverable, due, deadline, submit, upload, hand in, 과제, 숙제, 제출, 마감, 기한, 업로드, 레포트/리포트, 배점 등)가 청크에 하나라도 있음. **UNRESOLVED**: 문서 종류가 `LAB_MATERIAL`인 모든 청크(지시문이 본질이라 규칙으로 NO를 증명할 수 없음; **어휘 검사보다 우선**). **NO**: 그 외(강한 어휘 없음). 약한 어휘(implement, exercise, 구현, 실습, 문제 등)는 정보 라벨(`exercise`)만 만들고 YES를 만들지 않는다. 필수 단위는 assignment뿐이며 나머지 태그(`example_code`, `exam_hint`, `setup_step`, `schedule`, `grading`, `submission_guide`, `reference`, `resource_link`, `exercise`)는 규칙 기반 정보 라벨로 필터·권위에 쓰지 않는다(`exam_hint`가 `Scope Confirmed`를, `assignment`가 `Material Usage.Verified`를 바꾸지 않음). 어휘 목록·경계 규칙은 `TAG_RULE_VERSION`에 고정되고 고정 회귀 fixture를 둔다(영문 "Upload the .py file to Canvas before Friday" → YES) | 단위 |
| D3 | `tags_status=COMPLETE` ⇔ 현재 청크 집합의 모든 청크가 {YES, NO}로 결정됨. 하나라도 UNRESOLVED이거나 derivative를 읽지 못함/파싱 실패/빈 청크 집합이면 COMPLETE가 아니며 그 문서는 과제 제외 경로에서 **문서 전체 차단**. 결정은 현재 바이트로 매번 재계산되어 fingerprint·정규화·규칙 버전 변경 후 옛 결정이 쓰이는 경로가 없다 | 단위+통합 |
| D4 | 문서 단위 차단(과제 제외 경로, opt-in 아님): AI Kind ∈ {ASSIGNMENT_BRIEF, ASSIGNMENT_RESOURCE} → 문서 전체 제외(청크 결과 무관). AI Kind가 비었고 Material Type이 `Professor Notes`이면 과제 안내서가 아님을 증명할 수 없으므로 제외. 그 외 Material은 D3 COMPLETE일 때만 청크 필터 대상. v2 Material은 기존 `v2_exposure_gate`로도 숨겨진다(변경 없음). **직접 Material 조회(`get_material_context`)도 같은 규칙**이며 opt-in 없이는 ASSIGNMENT_* 문서의 내용·capability를 반환하지 않는다 | 통합 |
| D5 | 청크 단위 필터: 과제 제외 경로의 결과에서 assignment=YES 청크를 제거한다. **Locator 범위 규칙**: capability 발급·`get_source_chunk` 전에 요청 locator 범위와 겹치는 현재 fingerprint의 **모든** 청크를 평가하고, 하나라도 YES/UNRESOLVED/판정불가이면 그 범위 전체 거부(부분 슬라이스 없음). 청크가 페이지/타임스탬프 창이므로 한 페이지 안의 혼합은 페이지 전체 YES다 | 통합 |
| D6 | `CapabilityBinding`에 불변 정책 모드(`assignment_opt_in: bool`)를 추가한다. 발급은 모드를 기록하고, `get_source_chunk`/`authorize` 재검증은 binding의 모드로 D3–D5를 현재 derivative에서 다시 계산한다(발급 당시 NO여도 현재 YES/UNRESOLVED이면 거부). 모드가 없는 옛 binding은 fail-closed로 default 모드로 취급한다. `CapabilityManager.issue`·`issue_for_evidence`·`authorize`와 독립 헬퍼(`issue_context_capability` 별칭, 모듈 수준 `authorize_locator`)·엔진 재조회 경로가 모두 같은 정책 함수를 호출하며, 현재 derivative 정책 검증기를 쓸 수 없으면 과제 제외 대상의 발급·승인을 거부한다 | 통합 |
| D7 | EvidenceItem 호환 확장 필드 `usage_policy ∈ {NORMAL, NO_ANSWER_GENERATION}`, `assignment_decision ∈ {YES, NO, UNRESOLVED, STALE}`를 `evidence_item_to_dict`와 `CapabilityBinding`/`get_source_chunk` 결과까지 직렬화한다. 반환 범위와 겹치는 모든 청크로 집계(STALE>UNRESOLVED>YES>NO); 하나라도 YES/UNRESOLVED/STALE → `NO_ANSWER_GENERATION`; 겹치는 청크 집합을 확인할 수 없으면 NORMAL 금지. 문서 Kind ASSIGNMENT_* 및 `source_class=official_activity` 항목은 **항상** `NO_ANSWER_GENERATION`(어휘 적중과 무관; 확인·안내·요약은 유지) | 단위+통합 |
| D8 | `include_assignment=True`는 D4–D5의 차단을 풀되 반환 항목에 D7 필드가 붙고 completeness/authority를 유지한다(capability는 opt-in 모드로 발급). EXAM에서도 `exam_hint`를 범위 확정 증거로 표시하지 않는다. MCP 스키마·도구 시그니처 불변 | 통합 |
| D9 | 대상 밖: USER_NOTE(USER 구간)·VERIFY(존재·출처 확인)·ACTIVITY(공식 지시 derivative의 별도 경로; D7 정책만 적용). ANNOUNCEMENT·PROVIDED_CODE는 v2에서 비노출(변경 없음) | 통합 |
| D10 | `professor_signals`(enrichment 신호), 과제 제외 경로: (a) 신호가 **검증 가능한 locator**를 갖고 그 locator가 현재 derivative·출처 fingerprint에 대해 유효하면, 그 범위와 겹치는 모든 청크에 D3–D5를 적용해 하나라도 YES/UNRESOLVED/판정불가이면 **신호 전체를 제거**한다(본문에 강한 어휘가 없어도). (b) locator로 출처를 증명할 수 없는 신호는 모든 문자열 값을 D2 규칙으로 평가해 강한 어휘가 있으면 제거한다. 제거 시 경고 코드 `SIGNAL_ASSIGNMENT_WITHHELD`를 남긴다. 테스트: YES 청크에 연결된 신호(본문에 어휘 없음)와 어휘를 인용한 locator 없는 신호 모두 기본 SESSION에서 제거 | 통합 |
| D11 | 기존 동작 보존: D4와 D5를 통과한 후보(문서 차단·locator 범위 차단을 통과하고 전부 NO인 것)에 한해 내용·순서·예산이 이전과 동일하다. 기존 Professor Notes(AI Kind 없음) 차단과 정상적인 전부-NO 문서의 보존은 서로 다른 케이스로 각각 테스트한다. 기존 `get_session_context`/`get_material_context`/`get_exam_context`/`search_concept` 테스트는 그대로 통과(과제 어휘 없는 fixture) | 회귀 |
| D12 | Behavior Contract 개정(v3): intent와 무관한 전역 규칙 "과제 지시 구간은 출처 확인·안내·요약에만 쓰고 답안·코드·제출물 생성에 쓰지 않는다", `usage_policy`/`assignment_decision` 해석 규칙, projections(Claude Skills/ChatGPT Instructions) 재생성, 해시·버전 갱신, drift lint 통과 | contract |
| D13 | 비밀·권한: 필터는 권한을 넓히지 못한다(기존 source authorization 뒤에 적용). provider 쓰기·MCP 쓰기·새 네트워크 호출 0, `Verified`/`Scope Confirmed` 불변 | 통합 |
| D14 | 원 계획 수용 테스트와의 대응(영속 manifest → 읽기 시점 재계산): prompt/model 변경 STALE → 해당 없음(모델 없음); 원문 불변·규칙 버전만 변경 → 새 규칙으로 즉시 재계산(옛 결정 재사용 불가를 테스트); 재시작 후 coverage → 매 호출 재계산; 저확신 negative → 해당 없음; 중복 청크 응답 → 해당 없음; capability/get_source_chunk 우회 불가 → D6; 같은 페이지 혼합 → 페이지 전체 YES(D5) | 통합 |
