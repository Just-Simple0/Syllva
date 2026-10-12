# P-C2 — S2 (Jev Choice) 연동 불변식 표 (2026-10-12)

권위: `intake-classification-v2.md` §3.2, §4, §9 P-C. 범위는 **Jev 호출이 일어나는 경로**로 한정한다
(S4 태그는 P-D). 구현 리뷰의 REQUIRED = 아래 표 위반이 도달 가능한 경로에서 구체적 트리거로 잘못된
영속 상태/외부 쓰기를 만드는 경우뿐이다.

| # | 불변식 | 검증 |
|---|---|---|
| S1 | S2는 S1 미결 + 원본 바이트 증명(`byte_sha256`) + Canvas 결속 검증으로 origin=PROFESSOR_SOURCE일 때만 호출. 전사문(USER_AUTHORED_TRANSCRIPT)은 `:source`/`:user` 분리 정규화 전에 분류가 일어나므로 P-C2에서는 전송하지 않는다(§3.2). UNKNOWN/USER_NOTE/STUDENT_SUBMISSION은 호출 0, 노트 `S2_ORIGIN_INELIGIBLE`. 이미 HUMAN 결정된 항목은 재호출 없음 | 단위+통합 |
| S2 | 전송 payload의 모든 문자열은 최소화(이메일·전화·학번 유사 숫자열·라벨 뒤 이름/학번·URL 전체→`<url>`)를 거치고, 직렬화 후 재검사에서 패턴이 남으면 호출 0 + `PII_UNCERTAIN`. 요청 >16,384B면 발췌를 줄이고 그래도 초과면 호출 0 + S3 | 단위 |
| S3 | 키 미해석/미명시 → 클라이언트 없음 → `CLASSIFIER_DISABLED`(P-B 동작 불변). 키·payload 원문·provider 메시지는 로그/노트/readiness/예외 어디에도 없다 | 단위+통합 |
| S4 | 확정 = confidence ≥ 0.80 AND 최고확률 ≥ 0.70 AND choice ≠ no_match. 미달 → S3(`LOW_CONFIDENCE`/`CLASSIFIER_NO_MATCH`); provider timeout/transport/invalid → S3(고정 코드). 모델이 UNSUPPORTED/RECORDING/ANNOUNCEMENT를 고를 수 없다(옵션 = S2_CHOICE_KINDS + no_match) | 단위+통합 |
| S5 | 확정 결과는 `decision={type:"model", model, prompt_version, option_set_version, probabilities, confidence}`로 record에 영속, `snapshot_sha256`=전송한 정확한 발췌 바이트 해시, `classification_source=jev:<model>`. 이후는 기존 Stage A 게이트(과목·origin·handling·중복·바이트)를 그대로 통과해야 AUTO | 통합 |
| S6 | 틱당 호출 총예산 `max_calls_per_tick`; 소진 시 해당 항목은 `classification_state=DEFERRED` + `last_error_code=CLASSIFIER_BUDGET_EXHAUSTED`, **HUMAN Input Request 생성 0**; 다음 틱에 새 관측 항목보다 먼저 재개(학기 경계와 무관: 대기 중인 DEFERRED 항목 수만큼 새 항목의 호출 예산을 남겨 둔다); 해소되면 그 오류 코드 제거. 캐시 적중은 예산을 쓰지 않음 | 통합 |
| S7 | 캐시 키 = sha256(snapshot_sha256, payload_fingerprint, byte_sha256, source_version, title, course_key, module_week, item_type, origin, option_set_version, prompt_version, model). surrogate `source_hash` 미사용; 값은 검증된 decision만. 내용이 다르면 미적중 | 단위+통합 |
| S8 | readiness `classification`(`status`는 P-B 의미 유지, S2 상태는 별도 `s2_status`):  `calls_remaining`, `deferred_items`, `human_fallback_items`, `invalid_response_count`, `transport_failure_count`(직전 틱), status 우선순위 DISABLED > DEGRADED > READY | 통합 |
| S9 | 호출 실패는 분류 예외로 번지지 않는다: 한 항목의 실패가 다른 항목/Drive intake를 막지 않고, S2 자체의 Drive/Notion 콘텐츠 변경은 0(Jev POST와 S3 HUMAN 요청 생성은 계획이 허용) | 통합 |
