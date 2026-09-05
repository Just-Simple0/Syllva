# Phase 3 — Enrichment & Freshness (구현 계획서 · rev3)

**작성:** 총괄/관리 (Opus 4.8)
**rev2:** 1차 2중 계획 리뷰(GPT-5.6 Sol High + AGY Gemini 3.8 Flash High, **둘 다 REVISE**) 반영 + Opus 명세 재확인.
**rev3:** 2차 재검토(AGY=GO, Sol=REVISE) **엇갈림 → Opus 코드 재현 타이브레이크로 Sol 채택**. Sol 잔여 4건(required-kind completeness, top-level symbolic_hints 소비자 정합, §36 enum 이름 drift, Topics option 확정) 반영.
**명세 근거:** impl §6.2/§6.4/§16/§17/§18/§19/§35/§36/§44/§50.4/§50.5/§53, design §6(SOURCE/AI/USER)/§16/§17/§22/§34(Enrichment Fingerprints)/§39(Inv.4/11/13/16/17). 충돌 시 **명세 우선**.

> 표기 규칙: `impl-§N` = 구현 명세서, `design-§N` = 설계서.
> **주의(rev2):** enrichment fingerprint 계약은 **design-§34 + impl-§18**이다. `impl-§34`는 GitHub Adapter 로 무관. SOURCE/AI/USER 는 **design-§6**(design-§4.2 는 "Canonical normalized derivatives" — derivative 는 자기 source version 에 한해서만 권위이며 검증에서 원본을 능가하지 못함).

---

## 0. 배경 — 소비자 계약은 이미 고정 (Phase 2)

Retrieval Engine 은 이미 enrichment 를 **소비**한다(Phase 2, `6572feb`):

- `NotionReader.get_session_enrichment(entity_id) -> EnrichmentRecord | None` (`adapters/notion/base.py`).
- `EnrichmentRecord`(payload + based_on fingerprint + processor_version) 와 `assess_freshness`/`is_fresh` (`enrichment/schemas.py`, `enrichment/freshness.py`).
- 엔진 `_session_signals()`: fingerprint 를 현재 source 와 비교해 **FRESH → factual signal, STALE → symbolic hint 재검증(`revalidate_locator`)**.
- `_fresh_signal_items()` 가 읽는 payload 키: `summary`, `topics`, `professor_signals`/`signals`, `exam_signals`, `professor_emphasis`, `professor_examples`/`examples`, `likely_confusions`; `_symbolic_hints()` 는 `hints`/`symbolic_hints`/`topics`/`content_index`/`signals`/`professor_signals`.
- **소비자 위험 표면(rev2 확인):** `_fresh_signal_items()` 는 `source_class` 를 **absent 일 때만** `ai_enrichment` 로 `setdefault` 한다(engine.py:1011). 즉 payload item 이 `source_class` 를 갖고 있으면 **그대로 통과** → 생산자 직렬화 경계에서 반드시 strip/강제해야 함(§2.6 참조).

**Phase 3 = "생산자(producer)" 구현.** 위 payload 를 생성하고 fingerprint·evidence locator·explicit/inferred 라벨을 부여해 AI 소유로 영속화한다. **소비자 계약(키 이름/EnrichmentRecord 형태/freshness 판정)은 동결** — 생산자 출력을 소비자에 맞춘다. (미세 불일치 발견 시 소비자가 아니라 생산자를 수정; 소비자 변경이 불가피하면 리뷰에서 명시 승인 + 회귀 테스트.)

---

## 1. 목표 (수직 슬라이스)

```text
정규화된 현재 source derivative (transcript / material)
  → [입력 게이트] derivative front-matter{entity_id,source_version,source_hash} == 권위 현재 SourceFingerprint 검증 (불일치→fail-closed, §2.5-a)
  → LLM Adapter(enrich_session / enrich_material) 구조화 호출 (impl-§35)
  → 후보 items (summary/topics/emphasis/examples/exam_signals/likely_confusions/content_index)
  → [증거 검증] 각 item 의 evidence locator 를 현재 derivative 로 typed 재해석; slice 밖/미해석/근거없음 → DROP (fail-closed, §2.3)
  → [라벨] EXPLICIT(slice-내 verbatim quote) vs INFERRED(범위 locator만) 결정론적 판정 (§2.3)
  → [메타 강제] based_on = 검증된 현재 fingerprint, ownership=AI, control-metadata strip (§2.6)
  → [publish 직전 재검증] 현재 fingerprint 재확인(TOCTOU 차단) → 변경 시 abort (§2.4)
  → AI 소유로 영속화(impl-§17 커밋 순서; READY 마지막; SOURCE 재라벨 금지; human-only 필드 불가침)
  → (소비는 기존 RetrievalEngine 이 그대로: FRESH=factual, STALE=symbolic-only)
```

모델/MCP 없이 **생성기/엔진 레벨 직접 호출·검증**. 실제 LLM provider 대신 **Fake LLM Adapter**(고정 구조화 출력)로 모델 독립 검증(impl-§51).

## 2. 범위 (이번 Phase 구현)

### 2.1 LLM Adapter 계약 — `adapters/llm/base.py`, `adapters/llm/structured.py` (impl-§35)
- `LLMAdapter` Protocol. 이번 Phase 는 **`enrich_session`, `enrich_material` 만** 노출/구현(`parse_activity_requirements`/`propose_material_usage`/`propose_exam_scope`/`propose_goodnotes_match`/`rerank_bounded_concept_candidates` 는 §35 "may include" — Phase 4/5/CONCEPT 트랙, 이번 Phase 미구현. §35 은 이들을 요구하지 않으므로 지연은 위반 아님).
- 구조화 출력 `LLMEnrichmentResult` 은 **impl-§35 필드 전부 보존**:
  `output`(후보 items), `evidence`(item 별 후보 locator+quote), `confidence`, **`proposal/fact classification`(§35 필수 — 삭제/치환 금지)**, `provider_provenance`(model/provider), `based_on`(후보 fingerprint).
- **explicit/inferred 는 §35 축을 대체하지 않고 별도 축으로 추가**(rev2, Sol MAJOR): `classification`(§35 proposal|fact)과 `explicitness`(EXPLICIT|INFERRED)를 **독립 필드**로 둔다. 매핑: enrichment 는 본질적으로 `proposal`(AI 해석; VERIFY 로 factual 검증 불가, impl-§21 VERIFY). `explicitness` 는 그 proposal 이 source 원문에 명시적으로 근거하는지를 나타냄.
- **어댑터는 순수(pure)**: 입력 derivative(+예산 내 청크)만 받아 구조화 출력 반환. Notion/Drive write·human-only 필드 변경 없음(impl-§35 마지막 문장). 어댑터가 돌려준 `based_on`/`provider_provenance` 는 **신뢰하지 않으며**(§2.6) 시스템이 검증·주입한다.
- 입력은 **bounded**(design-§22, impl-§26 예산 재사용): 전체 코퍼스 금지, 대상 derivative + 예산 내 청크만.
- 에러 분류는 impl-§36 taxonomy 반환/매핑만; 재시도 루프는 기존 `orchestration/retry.py` 재사용(중복 구현 금지, `POLICY_DENIED` 무재시도 유지).

### 2.2 Enrichment payload 스키마 — `enrichment/schemas.py` (확장), `domain/enums.py` (impl-§6.4/§16/§18, design-§6/§34)
- `EnrichmentRecord.payload` 를 typed payload 로 구체화하되 **소비자 키 이름 불변**:
  - `EnrichmentSignal`(frozen): `kind`, `content`, `explicitness: Explicitness`(EXPLICIT|INFERRED), `evidence: tuple[EvidenceLocator, ...]`(**≥1, 필수**), `confidence: float`, `symbolic_hint: str`(topic/heading/term — STALE 재검증용, 필수).
  - `EvidenceLocator`: 파싱된 `Locator`(impl-§6.3, typed — 문자열 prefix 판단 금지) + `quote: str | None`. **quote 의미 규칙(§2.3 로 단일화)**: EXPLICIT 은 quote 필수(그리고 해당 locator slice 내부에서 재확인), INFERRED/synthesis 는 quote 미요구(범위 locator 만).
  - `SessionEnrichmentPayload`: `summary`, `topics`, `professor_emphasis`, `professor_examples`, `exam_signals`, `likely_confusions`.
  - `MaterialEnrichmentPayload`: `content_index`, `topics`.
  - `as_dict()` 는 소비자 `_fresh_signal_items()` 가 읽는 **정확한 factual 키**(summary/topics/professor_emphasis/professor_examples/exam_signals/likely_confusions)로 직렬화. **추가로(rev3, Sol MAJOR) top-level `symbolic_hints` 리스트를 반드시 직렬화** — 모든 routable signal 의 `{topic/heading/term(=symbolic_hint), locator?}` 를 top-level 로 투영한다. 이유: 동결된 소비자 `_symbolic_hints()`(engine.py:1027)는 **top-level 키 `hints/symbolic_hints/topics/content_index/signals/professor_signals` 만** 읽으므로, item 내부에 중첩된 `symbolic_hint` 는 STALE 재검증 경로에 도달하지 못한다. `professor_emphasis/professor_examples/exam_signals/likely_confusions` 의 stale 라우팅은 이 top-level 투영이 있어야 작동한다(없으면 STALE 시 해당 종류는 §19 대로 **폐기** — 안전하나 rev2 가 약속한 rerouting 미작동, 그래서 투영을 요구).
  - `source_class`/`freshness` 는 **직렬화하지 않음**(소비자가 부여; 생산자가 넣으면 스머글링 벡터, §0/§2.6). `EnrichmentRecord.from_mapping` 라운드트립 유지.
- `Explicitness` enum → `domain/enums.py`(stdlib only, 모델 독립). §35 `proposal/fact` 는 LLM 어댑터 결과 타입에만 존재(도메인 오염 없음).
- **불변식(design-§6)**: 모든 item ownership = **AI**. SOURCE 재라벨 금지. summary 는 verbatim 발췌가 아닌 해석(정규화 ≠ 요약과 구분).

### 2.3 증거 검증 & 라벨링 규칙 (rev2 핵심 — fail-closed 단일화; design-§39 Inv.17/16/11)
후보 item 은 다음을 **모두** 통과해야 유지되고, 하나라도 실패하면 **예외 없이 DROP**(합성/추정 locator 로 통과 금지, INFERRED 강등으로 구제 금지):

**(a) 근거(grounding) — 모든 kind 공통, Summary 포함:**
- 최소 1개 `EvidenceLocator` 의 파싱 `Locator` 가 현재 derivative 로 **typed 재해석**되어야 함:
  entity_id 일치, locator kind(page|time)·range·subtype 이 derivative 와 정합, transcript 는 sidecar mark 인덱스 범위 내, material 은 page 범위 내.
- **재해석 불가/locator 부재 → DROP.** (Inv.17: 근거 없는 것을 course evidence 로 표현하지 않음.)
- 각 item 에 `symbolic_hint`(topic/term) 부착 필수. **STALE 재검증 작동 조건(rev3):** 그 symbolic_hint 가 `as_dict()` 의 **top-level `symbolic_hints`** 로 투영되어야 소비자 `_symbolic_hints()`→`revalidate_locator`(impl-§19)에 도달한다(§2.2). 투영되지 않은 종류는 STALE 시 폐기(fail-closed, 안전).

**(b) EXPLICIT 판정 — 강한 조건(결정론적, 소스-백):**
- item 이 EXPLICIT 이려면: (a) 통과 + 그 evidence 의 `quote` 가 **해당 locator 가 가리키는 정확한 현재 source slice 내부**에 verbatim 으로 존재(엔진이 locator→slice 재해석 후 대조) + 그 quote 가 신호의 근거 문장이어야 함.
- **cross-locator 위조 차단(Sol Risk):** quote 가 "현재 본문 어딘가"에 있는 것으로는 불충분 — 반드시 그 item 의 locator slice 안에 있어야 함.
- 위 강한 조건 미충족 → item 은 **EXPLICIT 이 될 수 없음.** (a)만 통과한 grounded item 은 `INFERRED` 로 분류(단, 이는 (a)를 이미 통과한 범위-근거 item 에 한함 — (a) 실패 item 을 살리는 우회가 아님).

**(c) Summary / Likely Confusions(고차 합성):**
- 본질적으로 INFERRED. (a)의 범위 locator(요약 대상 span)만 요구, verbatim quote 미요구. **절대 EXPLICIT 될 수 없음.** 이로써 정상 요약이 verbatim 강제로 부당 drop 되거나 quote 를 날조하는 문제 회피(AGY finding 2).

> 결과: 근거 없는 후보는 전부 drop, EXPLICIT 은 slice-내 verbatim 으로만 성립, INFERRED 는 항상 현재-소스 범위에 묶임. 환각은 (최악의 경우) 실제 span 을 가리키는 low-authority AI-owned INFERRED 로만 존재 가능 — SOURCE/EXPLICIT/factual-verify 로는 절대 승격 불가.

### 2.4 영속화 경계 (worker-side write) — `enrichment/writer.py`(신규) + Notion/Drive write 어댑터
- `EnrichmentWriter` 는 write-capable 경계(엔진 아님; RetrievalEngine 은 계속 read-only Protocol 만 의존, impl-§4/§13/§27).
- **영속 권위(rev2, Sol §17):** compact persistent enrichment 의 **정본은 Notion AI enrichment 레코드**(소비자 `get_session_enrichment`/`get_material_enrichment` 가 읽는 소스). **제2의 canonical enrichment 저장소를 만들지 않는다.** ("staged" 는 원자적 published 전 준비이며 별도 정본 아님.)
- **impl-§17 커밋 순서:** `job=PROCESSING → 생성/검증(§2.3) → [publish 직전 fingerprint 재검증] → AI enrichment 원자적 기입 → processing record → job=READY(마지막)`.
- **TOCTOU 차단(rev2, Sol BLOCKING):** publish 직전 권위 현재 fingerprint 를 재조회해 §2.5-a 게이트 값과 동일함을 확인. 그 사이 source 갱신 시 **abort(READY 아님)**, 재처리로 미룸. based_on 은 이 재검증된 fingerprint 로 확정.
- Notion 쓰기는 **AI region 전용**(`write_ai_region`, `actor=AUTOMATION`). human-only 필드(Verified/Scope Confirmed/Decision/State) 미변경(impl-§15.1 방어 경계 우회 금지).
- fingerprint(source_version/hash/processor_version) + `provider_provenance` 를 영속 레코드에 포함(design-§34; provenance 영속 위치 명시 — AGY/Sol).

### 2.5 입력 유효성 & Partial 처리 (rev2, Sol BLOCKING/MAJOR)
- **(a) 입력 fingerprint 게이트:** 생성 전, derivative front-matter `{entity_id, source_version, source_hash}` 를 **권위 현재 SourceFingerprint**(`DriveReader.get_current_fingerprint`/state 소스)와 대조. 불일치(오래된 derivative + 새 fingerprint 등) → **fail-closed, 생성 안 함.** based_on 은 caller/model 값이 아니라 이 검증된 값에서만 유도(스푸핑 차단).
- **(b) Partial derivative:** front-matter `status ∈ {partial, needs_review, failed, pending, processing}` → **정상 READY enrichment 생성 금지**(impl-§17/§50, "Partial never silently Ready"). Partial 입력은 enrichment 를 산출하지 않거나 명시적 non-Ready 산출만 허용(정본 factual enrichment 로 published 금지).

### 2.6 Control-metadata 신뢰 경계 (rev2, Sol Risk)
- 생산자 직렬화 경계에서 **model/adapter 가 공급한 제어 메타데이터는 무시·제거·강제**:
  - `based_on`/fingerprint → §2.5-a/§2.4 의 검증값으로 **강제 대체**(model 값 거부).
  - `source_class`/ownership → 영속 payload 에서 **strip**(소비자가 AI 로 부여; 생산자가 `professor_transcript`/SOURCE 등 주입 불가).
  - `freshness`/`factual` → payload 에 넣지 않음(freshness 는 소비 시 엔진이 판정).
  - human-state 유사 키(`Verified`/`Scope Confirmed`/`Decision`/`State`) → strip + 절대 write 금지.
- 즉 LLM 이 구조화 출력에 위 키를 심어도 **저장 payload/Notion 에 반영되지 않는다.** (테스트 §5 에서 악성 키 주입으로 증명.)

### 2.7 Session Topics 취급 (rev3 — option 1 확정, Sol PARTIAL 해소)
- 생성된 Topics 는 **fingerprinted AI enrichment 레코드 내부에만** 보관한다(단일 정본). Notion Sessions `Topics` Multi-select **속성 mirror 는 이번 Phase 미사용**(별도 unversioned 사본을 만들지 않음).
- 이유: Topics 는 CONCEPT 라우팅(design-§22) 입력이므로, unversioned 속성 mirror 가 enrichment fingerprint 보다 오래 살아 **stale-routing 우회**가 되는 것을 원천 차단. 소비자는 `get_session_enrichment` 의 fingerprinted payload(`topics` 키)로만 Topics 를 읽으므로 freshness 판정이 그대로 적용된다.
- 이 결정으로 §2.4 "Notion write 는 AI region 전용"과의 문언 충돌 제거(속성 `update_properties` mirror 경로 없음). (속성 mirror 는 fingerprint 결속·stale-gated read 를 완전 정의할 수 있는 후속 Phase 에서 재검토.)

### 2.8 Material enrichment reader/writer (rev2, AGY/Sol — 누락 보강)
- `NotionReader` 에 `get_material_enrichment(material_id: str) -> EnrichmentRecord | None` 추가(`adapters/notion/base.py`) + `tests/fixtures/fake_notion.py` Fake 구현.
- `EnrichmentWriter` 가 Material Content Index enrichment 를 §2.4 순서로 durable 영속(정본 = Notion AI region), 라운드트립 테스트로 증명(§5).

## 3. 수용 기준 (impl-§44 — 릴리스 게이트, 모두 모델 독립 테스트로 증명)
1. **explicit/inferred 분리**: 각 item 이 EXPLICIT/INFERRED 로 결정론적 라벨; slice-내 verbatim 없는 "explicit" 주장은 EXPLICIT 불가(§2.3-b/c).
2. **evidence locators**: 유지된 모든 item 은 현재 derivative 로 재해석되는 typed locator(+symbolic hint) 보유; 근거 없는 후보는 drop(§2.3-a).
3. **stale enrichment factual 제외**(impl-§50.4): 현재 v3 / based_on v2 → 소비 시 STALE, factual 미포함(생산자 부착 fingerprint 로 기존 소비자 판정).
4. **stale absolute locator 재해석/폐기**(impl-§19/§50.5): symbolic_hint 보유 → STALE 시 소비자 재해석 성공/실패(폐기) end-to-end.
5. **source fingerprint metadata**: 영속 레코드에 source_version/source_hash/processor_version(+provenance) 존재(design-§34), 라운드트립 성공.
6. **ownership/human-gate/anti-spoof 불변**: enrichment AI 소유, SOURCE 재라벨 없음, human-only 미변경, model 공급 제어 메타 무효화(§2.6), 입력/파브리시 fingerprint 게이트+TOCTOU(§2.5/§2.4).

## 4. 에러/결과 계약 (impl-§36/§53 — 결정론적)
- **에러 분류(rev3, Sol MAJOR — enum 이름 drift 제거):** job/domain 분류는 **frozen impl-§36 enum 그대로** = `TRANSIENT`/`RATE_LIMITED`/`PERMANENT`/`AMBIGUOUS`/`POLICY_DENIED`. LLM provider/adapter 내부 예외명(`LLM_*` 등)은 job 분류 **전에 반드시 이 §36 enum 으로 매핑**한다(내부명이 계약이 아님). 재시도는 기존 정책 재사용(무한재시도 금지, 기본 3회, `POLICY_DENIED`·`PERMANENT` 무재시도, `AMBIGUOUS`→NEEDS_REVIEW).
- **완료 상태 단일 계약(rev3 — required-kind completeness):**
  - **per-kind 완료 상태(Sol MAJOR):** aggregate count(생성/drop 수)만으로는 "LLM 이 required kind 자체를 아예 미출력"한 경우를 못 잡는다. 따라서 §44 required kind(session: `summary`,`topics`,`professor_emphasis`,`professor_examples`,`exam_signals`,`likely_confusions`; material: `content_index`,`topics`)마다 **`produced` | `legitimately_empty` | `omitted_or_failed`** 상태를 완료 레코드에 기록한다.
  - `job=READY` 조건: 모든 required kind 가 `produced` 또는 `legitimately_empty`(근거 있는 정당한 빈 — 예: 해당 세션에 exam signal 이 실제로 없음, 명시적 판정)로 해소될 때만. **어느 required kind 라도 `omitted_or_failed` → `job=NEEDS_REVIEW`(READY 금지).** (모든 kind 가 항상 non-empty 여야 하는 것은 아니나, 침묵 누락은 성공으로 위장 금지 — impl-§17.)
  - 유지 item ≥1 이어도 위 per-kind 게이트를 통과해야 READY. 드롭된 후보는 로그만(위조 금지).
  - **전 item drop / 근거 0 → `ENRICHMENT_NO_EVIDENCE` → `job=NEEDS_REVIEW`.** "빈-결과 READY" 옵션 없음.
  - 입력 게이트/Partial 실패(§2.5) → 생성 안 함, 구버전 enrichment 유지(조용한 빈-덮어쓰기 금지).
  - 완료 레코드는 기계 판독 필드(per-kind 상태 + 생성/드롭 수 + 입력 fingerprint)를 포함해 부분성이 은폐되지 않게 한다.
- `SOURCE_UNAVAILABLE`(derivative 없음/읽기 실패). 절대 금지: 합성 locator·cross-locator quote·false EXPLICIT·STALE→FRESH 승격·human-only write·model fingerprint 신뢰.

## 5. 테스트 계획 (모델 독립, fakes; tests/**)
- `tests/fixtures/fake_llm.py`(신규): 정상/근거없는 후보/slice-밖 quote(cross-locator)/false-explicit/부분실패/에러클래스/**악성 제어키(source_class,freshness,factual,based_on,Verified,Scope Confirmed,Decision,State) 주입** 출력을 돌려주는 `LLMAdapter` Fake.
- `tests/fixtures/fake_notion.py`(확장): `get_material_enrichment` + AI region write 캡처.
- `tests/unit/test_enrichment_schemas.py` — typed payload ↔ `as_dict()`(소비자 factual 키 정합, source_class/freshness 미직렬화, **top-level `symbolic_hints` 투영 존재**) ↔ `from_mapping` 라운드트립; **§35 `classification`(proposal/fact)와 `explicitness`(EXPLICIT/INFERRED) 두 축 독립성**(한 축 값이 다른 축을 강제하지 않음).
- `tests/contract/test_session_enrichment_generation.py` — 근거 없는 후보 drop, EXPLICIT=slice-내 verbatim only, cross-locator quote 거부, INFERRED 강등이 근거없는 항목을 구제하지 않음, Summary=범위 locator만(EXPLICIT 불가), symbolic_hint 부착, 부분통과.
- `tests/contract/test_enrichment_metadata_trust.py`(신규) — **§2.6 증명**: model 공급 based_on/source_class/freshness/human-only 키가 저장 payload/Notion 에 반영되지 않음(strip/강제), ownership=AI.
- `tests/contract/test_enrichment_input_gates.py`(신규) — **§2.5 증명**: old derivative(v2)+authoritative v3 → 생성 거부(스푸핑 차단); Partial/non-Ready derivative → 정상 READY enrichment 불가.
- `tests/contract/test_enrichment_commit_ordering.py` — impl-§17 순서, publish 직전 fingerprint 재검증(TOCTOU: 중간 source 변경 시 abort), human-only 미변경, AI region 전용 write, LLM 실패 시 구버전 유지.
- `tests/contract/test_material_enrichment_generation.py` — content_index page locator 검증·drop.
- `tests/contract/test_enrichment_completeness_states.py`(신규) — 전 item drop→NEEDS_REVIEW(READY 금지), 부분 drop→completeness 필드 정확, **required kind 미출력(omitted)→NEEDS_REVIEW**, 정당한 empty(legitimately_empty)→READY 허용.
- `tests/contract/test_error_taxonomy_mapping.py`(신규) — LLM 내부 예외명이 frozen §36 enum(`TRANSIENT`/`RATE_LIMITED`/`PERMANENT`/`AMBIGUOUS`/`POLICY_DENIED`)으로 매핑됨.
- `tests/integration/test_enrichment_end_to_end.py` — 생산자→(fake Notion 저장)→기존 RetrievalEngine 소비: FRESH=factual, STALE(v2 vs v3)=factual 제외(§50.4), STALE+symbolic 재해석 성공/폐기(§50.5). **stale 시 professor_emphasis/exam_signals 등의 top-level `symbolic_hints` 투영이 실제 소비자 `_symbolic_hints()`→revalidate 경로에 도달함을 검증**(투영 없으면 폐기됨도 대비). Session + **Material Content Index 라운드트립** 모두.

## 6. 비목표 (이번 Phase 제외)
- Material Usage proposal/page-range/Verified(Phase 4 §45), Exam scope/Scope Confirmed/Activity 파싱·컨텍스트(Phase 5 §46), GitHub(§47), 클라이언트 패키징(§48), 스케줄러/remote MCP(§49).
- CONCEPT 검색 쿼리·LLM reranker 소비(design-§22 — Content Index **생성**만 포함), 실제 LLM/Notion/Drive provider SDK(Fake 검증), Goodnotes(§33).
- 소비자(RetrievalEngine) freshness/stale-locator/capability 로직 재구현(Phase 2 완료 — 정합성만 확인).

## 7. 리스크 / 불변식
- **anti-hallucination(최우선, 양쪽 리뷰 Critical)**: 근거 없는 후보 예외 없이 drop; EXPLICIT 은 slice-내 verbatim only; INFERRED 는 항상 현재-소스 범위 결속. (design-§39 Inv.17/16.)
- **anti-spoof/TOCTOU(Sol BLOCKING)**: 입력 fingerprint 게이트 + publish 직전 재검증; based_on/제어메타는 server-controlled, model 값 거부(§2.5/§2.6).
- **소비자 계약 동결**: payload 키/EnrichmentRecord/freshness 판정 불변; 생산자를 소비자에 맞춤; source_class/freshness 미직렬화; **stale rerouting 을 원하는 signal 은 top-level `symbolic_hints` 로 투영**(소비자 `_symbolic_hints()` 도달 조건, rev3 Sol).
- **§36 enum 동결**: 내부 LLM_* 예외명은 job 분류 전 frozen §36 enum 으로 매핑(rev3 Sol).
- **ownership(design-§6)**: AI 소유, SOURCE 재라벨 금지, human-only 불가침(impl-§15.1).
- **freshness(impl-§18/design-§34)**: 엔진 판정, fingerprint 는 생산자가 검증값으로 부착; STALE→symbolic-only; Topics 도 fingerprint 결속(§2.7).
- **§35 계약 보존(Sol MAJOR)**: proposal/fact classification 유지 + explicit/inferred 별도 축 추가.
- **레이어링(impl-§4)**: 생성/영속은 worker write 경계, RetrievalEngine read-only 유지, LLM 어댑터 pure(write 없음).
- **커밋 순서/완료(impl-§17)**: READY 마지막, 전-drop→NEEDS_REVIEW, Partial 위장 금지, LLM 실패 시 조용한 덮어쓰기 금지.
- **문서화된 cross-store 잔여 위험**: Notion AI publish와 별도 StateStore READY commit 사이의 zero-visibility window는 frozen status-blind consumer 및 cross-store atomicity 한계로 결정론적으로 제거할 수 없다. 따라서 publish 후 verified read-back rollback과 unresolved leak의 hard `EnrichmentSafetyError`(재시도 포함)를 필수 완화책으로 둔다.

## 8. 완료 정의
§44 수용 기준(explicit/inferred 분리 · evidence locators · stale enrichment factual 제외 · stale absolute locator 재해석/폐기 · source fingerprint metadata) + §2.5/§2.6 anti-spoof/anti-hallucination 이 모델 독립 테스트로 증명 + 전체 pytest(기존 148 + 신규) 통과 + `scripts/lint_behavior_projection.py` 통과 + impl-§4 경계·§15.1 human-gate 불변 유지 + 계약 에러 taxonomy 준수 + **2중 구현 리뷰(GPT-5.6 Sol High + AGY Gemini 3.8 Flash High) 둘 다 GO + Opus 검증 일치**.
