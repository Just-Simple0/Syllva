# Phase 2 — Transcript Vertical Slice (구현 계획서 · rev2)

**작성:** 총괄/관리 (Opus 4.8) · **rev2:** 2중 계획 리뷰(GPT-5.6 Sol High + AGY Gemini 3.8 Flash High, 둘 다 REVISE) 반영
**명세 근거:** impl §4/§6.3/§6.4/§9/§10.1/§11/§13/§16/§17/§18/§19/§20/§21/§24.5/§25/§26/§27/§43/§51/§53, design §4.2/§17/§39(Inv.13,15). 충돌 시 명세 우선.

> 표기 규칙: `impl-§N` = 구현 명세서, `design-§N` = 설계서.

---

## 1. 목표 (수직 슬라이스)

```text
Alt transcript source
  → 정규화 transcript derivative (원문 verbatim + sidecar 타임스탬프 인덱스)
  → Session 그래프/상태 (Notion, 별칭 포함)
  → RetrievalEngine: resolve_entity → (필요시 select_resolution) → get_session_context
  → bounded ContextPackage + fingerprint·role 바인딩 context capability
```
모델/MCP 없이 **엔진 레벨 직접 호출·검증**(impl-§4, §20). provider SDK 대신 Fake 어댑터로 검증(impl-§51).

## 2. 범위 (이번 Phase 구현) — 리뷰 반영 결정 고정

### 2.1 정규화 — `normalization/transcript.py`, `schemas.py`, `validators.py`
- 출력 `uls.transcript.v1`(impl-§16). **본문은 verbatim**(정규화 ≠ 요약 ≠ 교정). 유일한 본문 변형은 **개행 정규화(CRLF/CR→LF)** 뿐이며 그 외 문자는 불변. (M1)
- **타임스탬프는 본문을 재작성하지 않는다.** 원문의 `[HH:MM:SS]`(및 허용 변형) 마커를 파싱해 **sidecar 인덱스**로만 보관. canonical `TimeLocator`(impl-§6.3)는 인덱스에서 생성. (M1)
- 인덱스 항목: `TimestampMark(start_seconds:int, char_offset:int)`. **offset 단위 = LF 정규화 후 Python str 인덱스(유니코드 code point), 0-기준.** (M2)
- front matter(impl-§16): schema/entity_id/course_key/source_ref/source_hash/source_version/processor_version/normalized_at/status. **status 는 front matter 한 곳만 source of truth**(중복 필드 금지). typed 스키마가 이를 읽음. (M4)
- 타임스탬프 파싱/추출 실패가 있으면 `status=partial` 로 강등(조용한 ready 금지, impl-§17/§32). LLM 사용 금지(결정적 파싱).
- 시그니처(안):
  - `def normalize_transcript(raw, *, entity_id, course_key, source_ref, source_hash, source_version, processor_version, now) -> NormalizedTranscript`
  - `NormalizedTranscript`: `front_matter`(typed) + `body`(str, verbatim/LF) + `marks: tuple[TimestampMark,...]`; `status` 는 `front_matter.status` 프로퍼티로만 노출.

### 2.2 인제스션 오케스트레이터 — `ingestion/transcript_ingest.py`(신규), `classifier.py`
- `ingest_transcript(...)` 가 **impl-§17 커밋 순서 강제**: `job=PROCESSING → staged derived write → validate → atomic publish → Notion 메타 갱신 → processing record → job=READY(마지막)`. (H6/#5)
- 부분만 유효하면 **job/Notion Text Status/derivative status 3중 모두 Partial**(impl-§32). 
- Session entity id 할당은 `state.allocate_entity`(impl-§8.6.1), source/version 등록은 기존 StateStore 재사용. Drive 쓰기는 write-capable 어댑터로(엔진 아님). 
- **Notion Session 쓰기는 impl-§14.2 exact schema 필드만**(ID/Normalized Transcript/Recording Status/Status 등). `Text Status`/`Source Hash`/`Source Version` 은 Sessions 스키마에 없다(그건 Materials §14.3) — Session 에 쓰지 말 것. source hash/version 추적은 StateStore source_versions 로. (프로즌 명세가 계획보다 우선)
- **atomic write 계약**: staged-write + atomic-publish 전용 메서드만 사용. 비원자적 `write_derived_file`/`publish_derived` 로의 duck-typed fallback 금지(없으면 실패).
- **Phase 4 경계**(M5): 이번 Phase 는 이미 존재하는(pre-existing) verified 관계의 **read-only 소비**만. Material Usage proposal/verification mutation/일반 multi-material policy 는 Phase 4.

### 2.3 Read-only 어댑터 Protocol — `adapters/notion/base.py`, `adapters/drive/base.py` (H3)
- 엔진은 **읽기 전용 좁은 Protocol** 에만 의존(write 메서드 노출 금지, impl-§13/§27):
  - `NotionReader`: `get_session(entity_id)`, `find_sessions_by_alias(course, alias_norm)`, `list_course_sessions(course)`, `get_material_usage(session_id)`(verified 플래그 포함), `get_session_enrichment(entity_id) -> EnrichmentRecord | None`, `get_course_by_alias(alias_norm)`.
  - `DriveReader`: `read_derived(source_ref) -> str`, `get_current_fingerprint(entity_id|source_ref) -> SourceFingerprint`.
- Fake 구현(2.7)은 이 Protocol 만 만족. RetrievalEngine 생성자는 write 어댑터를 받지 않음.

### 2.4 Enrichment 읽기·freshness 계약 — `enrichment/schemas.py`, `retrieval/freshness.py` (H4)
- `EnrichmentRecord`: `payload` + `based_on_source_version:int` + `based_on_source_hash:str` + `processor_version:str`(impl-§16/§18).
- 엔진은 enrichment 의 based_on fingerprint 를 **현재 source fingerprint 와 비교**: 일치=FRESH(factual 가능), 불일치=STALE → **factual evidence 제외, symbolic 힌트로만**(impl-§16/§19).
- **stale-locator 재해석**(impl-§19): `revalidate_locator(symbolic_hint, current_derivative) -> Locator | None` — 힌트(heading/term/topic)로 현재 정규화 본문/인덱스에서 재탐색해 현재 locator 산출, 실패 시 폐기. 절대 stale absolute locator 를 그대로 읽지 않음.
- **derivative-vs-source 유효성**(M3): 읽은 derivative front matter 의 source_version/hash 가 현재 canonical 과 다르면 현재 professor transcript 로 반환 금지(SOURCE_PARTIAL/stale 처리).

### 2.5 Resolver — `retrieval/resolver.py` (H1/H7/#2/#3)
- **쿼리 정규화**: casefold + 공백 정규화. 의도 키워드 접미/접두 제거는 **화이트리스트 토큰**만(예: "정리","설명","요약","알려줘")를 별도 분리하되 매칭에는 사용 안 함. 매칭은 **토큰 단위 정확 일치**(substring 금지 — "15강"/"제5강의실" 오탐 방지). (H7/#2)
- Session No 추출: 정규식 `(?:^|\s)(\d+)\s*강(?:\b|$)`, `(\d+)\s*번째\s*강의` 등 명시 패턴만.
- 우선순위(impl-§10.1): 정확 entity ID → Course+Session No → 정확 alias(정규화 토큰 일치) → 결정적 메타 → 제한적 fuzzy → ambiguous. **Course 별칭도 course-first 해석에 참여**(impl-§14.0/§14 Courses aliases). (H7)
- 반환 타입 **단일화**(H1/#3): `resolve_entity(query, *, course_hint=None, entity_type="session") -> ResolutionResult` 이며 `ResolutionResult.status ∈ {resolved, ambiguous}`; ambiguous 는 EphemeralStore 로 `resolution_id`+candidates 발급(impl-§9/§15.1). 표시 텍스트 아닌 id 로만 확정.
- 2차 턴: `select_resolution(resolution_id, candidate_id) -> ResolvedEntity`(impl-§20/§24.3). 잘못된 candidate → `INVALID_CANDIDATE`; 만료 → `RESOLUTION_EXPIRED`.

### 2.6 Retrieval Engine — `retrieval/engine.py`(+scope/authority/freshness/chunking/context/capabilities/provenance)
- 생성자: `RetrievalEngine(notion_reader: NotionReader, drive_reader: DriveReader, state_store, ephemeral, config)`. (H3, impl-§4)
- **API 분리**(H1/#8, impl-§24.5):
  - `resolve_entity(...)`, `select_resolution(...)` (2.5)
  - `get_session_context(session_id: str, *, query: str | None = None, include_provisional: bool = False, caller_scope: str | None = None) -> ContextPackage` — **이미 해석된 session_id 를 받음.** 모호성은 여기서 표현하지 않음(먼저 resolve).
- 절차(impl-§18 SESSION): resolve 된 session_id →
  1) 정규화 transcript 청크(타임스탬프) →
  2) **verified** Material Usage 청크(impl-§17; unverified 는 기본 제외, include_provisional=True 일 때만 provisional 표시 포함) →
  3) 관련 USER annotation **참조만**(본문 노출 금지) →
  4) professor signals(enrichment, freshness 통과분만) →
  5) provenance 조립 → 6) ContextPackage + capability 발급.
- **Authority(M8, design-§17/§21)**: fetch order ≠ authority rank. 기본 권위 = Professor Material > Professor Transcript > Official Activity/Exam > USER > Supplemental > AI enrichment > External. `policy_for_session()` 결과를 각 `EvidenceItem.authority` 메타에 기입.

### 2.7 ContextPackage / EvidenceItem / 예산 (H5/#6, impl-§6.4/§21/§26)
- 반환은 도메인 `ContextPackage`(entity/scope/sources/professor_signals/user_context/warnings/context_id) 사용.
- 각 `EvidenceItem`: source_class/entity_id/locator/fingerprint/authority/content/provenance/freshness(impl-§6.4).
- 예산(config, 기본값 고정; impl-§26): `max_evidence_items=12`, `max_chars_per_item=4000`, `max_total_chars=24000`, `max_followup_chunks=8`. truncation 시에도 provenance 보존.

### 2.8 Capability 발급/인가 (H2/#4, impl-§25, design-§39 Inv.13)
- 발급 allowlist = **반환된 각 EvidenceItem 의 정확한 locator range 개별 등록**. 엔티티 전체/convex-hull 금지(초과 인가 방지). (#4)
- 각 allow 항목에 `entity_id, locator_range, source_hash, source_version, source_class`(role) 바인딩.
- 인가는 impl-§25 6개 검사 모두:
  1) context 존재, 2) 만료 아님(→CONTEXT_EXPIRED), 3) caller scope 일치, 4) typed containment(범위 밖→LOCATOR_NOT_ALLOWED),
  5) 현재 fingerprint 일치(불일치→LOCATOR_STALE; 미제공→deny, Phase 1 계약), 6) **요청 role/source-class 가 여전히 허용**.
- (6)의 계층: 존재/만료/scope/containment/fingerprint 는 EphemeralStore(Phase 1)에서, **role/source-class 및 verified-관계 현행성**은 엔진의 source-chunk 경로에서 NotionReader 로 재확인(예: 발급 후 관계가 unverified 로 바뀌면 hash 동일해도 인가 취소). 두 계층 합으로 §25 충족.

## 3. 수용 기준 (impl-§43 — 릴리스 게이트, 모두 테스트 증명)
1. **타임스탬프 보존**: 정규화 후 sidecar 인덱스로 `tHH:MM:SS` 참조 가능, 본문 verbatim.
2. **transcript 무단 교정 금지**: 본문이 원문과 (LF 정규화 제외) verbatim 일치.
3. **stale transcript enrichment 필터링**: 구버전 fingerprint enrichment 는 factual 제외.
4. **"5강 정리" 해석**: alias("5강")·Session No(5)로 해석, "15강"·"제5강의실" 오탐 없음, 모호 시 candidates.
5. **capability 범위 한정**: allowlist == 반환된 청크별 range 정확 일치; 범위 내 ALLOW, 범위 밖/타 엔티티 LOCATOR_NOT_ALLOWED, fingerprint 불일치 LOCATOR_STALE, role 무효화 시 거부.

## 4. 에러/결과 계약 (M7, impl-§53)
- `ENTITY_NOT_FOUND`(해석 실패), `ENTITY_AMBIGUOUS`/ambiguous 핸들, `RESOLUTION_EXPIRED`, `INVALID_CANDIDATE`,
  `SOURCE_PARTIAL`(부분 transcript — 명시 플래그와 함께 제한적 반환, 절대 ready 로 위장 안 함), `SOURCE_UNAVAILABLE`(derivative 없음/읽기 실패), `LOCATOR_STALE`.
- partial transcript 정책: `status=partial` 이면 ContextPackage.warnings 에 partial 표시 + 사용 가능한 범위만 반환.

## 5. 테스트 계획 (모델 독립, fakes; tests/**)
- `tests/fixtures/fake_notion.py`(신규·재사용 가능): Course/Session/Material/Material Usage 관계 + aliases + enrichment + verified 플래그 지원. (#1 CRITICAL)
- `tests/fixtures/fake_drive.py`(신규): source/derived 본문 + fingerprint 조회. (#1)
- `tests/unit/test_transcript_normalization.py` — verbatim(LF만), sidecar 인덱스 offset(code point), 타임스탬프 보존, front matter 스키마, partial 강등.
- `tests/contract/test_session_resolver.py` — 정확 id / Course+No / alias 토큰 정확 일치 / "5강 정리" / "15강"·"제5강의실" 오탐 없음 / Course 별칭 course-first / ambiguous 핸들 / select_resolution / 잘못된 candidate·만료.
- `tests/contract/test_get_session_context.py` — transcript+verified usage 반환; unverified 기본 제외; include_provisional 표시; capability allowlist == 반환 청크 range(초과 인가 없음); 내부 timestamp ALLOW / 미반환 구간·타 엔티티 DENY; role 무효화 시 거부; stale enrichment 제외; stale locator 재해석/폐기; derivative-behind-source 는 현재로 반환 안 함; provenance/예산 준수.
- `tests/contract/test_transcript_ingest_ordering.py` — §17 커밋 순서 및 partial 3중 상태.

## 6. 비목표 (이번 Phase 제외)
- MCP transport/tool 배선(별도 Stage M0/VS0 작업 — 검증 순서상 별개 트랙이며 Phase 2 는 엔진 레벨). (M6 표현 교정: "later" 아님)
- CONCEPT 검색(impl-§22), Goodnotes(impl-§33), enrichment **생성**(Phase 3), 실제 provider SDK, 실 스케줄러,
  Material Usage proposal/verification mutation·일반 multi-material policy(Phase 4, M5).

## 7. 리스크 / 불변식
- **보안**: capability 초과 인가 금지(청크별 정확 range) + role 현행성 재확인(design-§39 Inv.13).
- **verbatim**: LLM 금지, 개행 외 본문 불변(M1).
- **freshness/stale-locator**: 엔진이 판단, 클라이언트에 fingerprint 비교 위임 금지(impl-§19).
- **레이어링(impl-§4)**: 엔진은 read-only Protocol·도메인·ephemeral·state 에만 의존, write 어댑터 미노출(H3/§27).
- **커밋 순서(impl-§17)**: READY 는 마지막, partial 3중 일관.

## 8. 완료 정의
§43 수용 기준 5개가 모델 독립 테스트로 증명 + 전체 pytest 통과 + impl-§4 경계 유지 + 계약 에러 taxonomy 준수 + 2중 구현 리뷰 GO.
