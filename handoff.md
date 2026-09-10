# Syllva (ULS v1.2) — Handoff

**Last updated:** 2026-09-10

**Repo / merged main:** https://github.com/Just-Simple0/Syllva · Phase4는 PR1로 `main`에 `e55705f`로 병합됨 (2026-09-10 09:32:55 KST)

**정책 후속 work branch:** `codex/syllva-project-agents` · Phase4 병합 이후 정책 문서 작업용, 이 정책 후속 변경은 원격 `main`에 미반영(push 없음)

**Phase4 구현 커밋:** `3f190fc`  ·  **Phase4 완료·검증 기록:** `d9fe77e`  ·  **정책 채택 기록:** `f974c7f`

**Phase4 당시 시작 기준:** `bfc592b` (Phase3 인수인계)

## 최신 인수인계 — Phase4 완료 및 정책 정렬 후속

**Phase4 구현 rev10은 독립 리뷰 GO와 총괄 검증·수용을 통과했고 PR1로 `main`에 병합됐다.** 아래 Phase4 완료 근거와 역사 기록은 보존한다. 현재 후속 작업은 정책 문서 정렬만 다루며 Phase5–8 구현이나 push를 포함하지 않는다.

### 현재 지침과 경계

- 모델/effort 선택, orchestration, review, safety는 적용 가능한 global Codex `AGENTS.md`와 프로젝트 `AGENTS.md`를 따른다. 이 handoff는 전역 정책을 복제하지 않는다. `CLAUDE.md`는 Claude 전용이다.
- 제품의 `Single-active-worker`는 ULS runtime 제약이며 Codex subagent 동시성을 정하지 않는다.
- **MCP search surface (MCP 검색 표면)**는 v1.2에서 read-only인 계약/스캐폴드 경계다. 현재 MCP 배포나 실제 클라이언트 검증 완료를 주장하지 않는다.
- 승인·확인은 human-owned다. AI와 일반 자동화는 독립적으로 승인·승격할 수 없다. 자동 적용에서는 지정된 `HumanApprovalApplier`만 정책·freshness·identity 검사를 모두 통과한, 현재 유효하고 human attribution이 있는 승인 변경을 적용할 수 있으며 human approval 자체를 만들 수 없다.
- 현재 승인된 제품 범위는 **Phase4까지**다. Phase5–8은 새 사용자 지시 없이 시작하지 않고, push도 하지 않는다.
- 승인 계획은 **rev6**, 완료 구현은 **rev10**이다. 계획의 과거 UNAPPROVED 헤더는 후속 리뷰 기록으로 승인됐으므로 수정하지 않는다. 승인 계획과 두 frozen 명세의 해시는 그대로 유지했다.
- 정책 정렬·호환성 감사와 검증 근거는 [Codex 정책 채택 기록](docs/codex-policy-adoption.md)에 보존한다. 전역 Codex 지침은 일반적으로 `~/.codex/AGENTS.md`, 프로젝트 지침은 [AGENTS.md](AGENTS.md)를 따른다.

### 완료한 동작과 검증

Material Usage 제안 생성, 정규화된 승인 Queue, 사람 승인에 따른 MATERIAL_USAGE/PAGE_RANGE 적용, 불확실한 쓰기 결과의 보수적 복구, 다중 자료 검색과 후속 권한 철회를 구현했다. 마지막 웹 지적 두 건도 수정했다.

1. 유한 페이지 범위는 일부 페이지만 발견돼서는 승인되지 않는다. 현재 검증된 자료에 요청한 모든 페이지가 있어야 한다.
2. `effect_observed` 표식 저장 후 대상과 전체 근거를 다시 검증한다. 그 사이 인간 복원이나 의존성 변경이 있으면 표식을 유지하고 조정을 기다리며, 대상을 다시 쓰거나 완료 감사를 남기지 않는다. 정상 처리와 유효한 감사 재시도는 성공한다.

**2026-09-09 Phase4 검증 기록이며 이번 문서 변경에서 재실행하지 않음.**

| 검증 | 최종 결과 |
| --- | --- |
| 전체 테스트, Python 3.11.16 | **866 passed** |
| 전체 테스트, Python 3.14.7 | **866 passed** |
| 새 rev10 회귀 테스트 | 두 환경 각각 **30 passed**; 수정 전 코드에서는 16 expected failures / 14 passes |
| 고정 소스 복사본 및 실제 웹 첨부 복원 | 각각 **731 passed**, 126파일 누락·내용 불일치 없음 |
| 컴파일·Behavior projection·diff check | 통과 |
| 제안→승인→검색→재적용→철회, p39–40/p40, 두 결함 before/after | 통과 |
| Ruff / mypy | **188 findings / 74 errors** — 정적 검사는 clean이 아님; 새 정규화 mypy 오류 없음 |

리뷰한 126파일은 최종 구현과 해시가 일치한다. 세부 완료 근거와 명세 §45 대응은 [Phase4 검증 기록](docs/plans/phase4-verification.md), 범위와 과거 진행 기록은 [실행 기록](docs/plans/phase4-8-execution.md), 승인된 불변 계획은 [Phase4 계획](docs/plans/phase4-material-usage.md)에 있다.

### 독립 리뷰와 근거 위치

- **웹 GO:** Codex native insane-review-codex 0.6.8, UI 검증된 Latest / 매우 높음. 1,254초 후 정상 회수(exit0). 이전과 동일한 123파일의 내용 동일성을 확인한 뒤 이전 전체 읽기를 재사용했고, 변경된 어댑터 4,512줄과 새 테스트 두 파일은 전체 재검토했다. 복원한 현재 코드로 731테스트·컴파일·projection을 실행하고 두 기존 결함 및 추가 복구·sibling 조합을 독립 재현했다. 최종 보고서 `.review/phase4-rev10-insane-review-final.md`, [웹 리뷰 대화](https://chatgpt.com/c/6aa13845-5fdc-83e9-a3e9-b3e32c1e9bdb). 선택적 회귀 테스트 제안은 비차단이며 미해결 구현 결함이 아니다.
- **Gemini GO:** Gemini 3.8 Flash high, Descartes `01a085ef-7f18-7b41-9c12-6678da68018b` 종료. 251페이지 전체 출력과 인용 5개를 총괄이 원본 대조했고, 독립 731테스트·projection 실행을 확인했다. 줄 번호, 격리 mypy 68건과 전체 74건의 구분, 재현 스크립트 import 경로 및 lint 설명의 정정은 `.review/phase4-rev10-gemini-{final.md,addendum.md,audit.json}`에 보존했다.
- 총괄 최종 수용: `.review/phase4-rev10-final-acceptance.json`. 전체 검증 로그: `.review/phase4-root-rev10-results.json`. 고정 파일 해시: `.review/phase4-integrated-review-hashes-rev10.json`.
- `.review/`와 `.insane-review/`는 Git에서 제외된 **이 작업 환경의 로컬 증거**다. 다른 checkout에는 자동으로 전달되지 않는다. 커밋된 검증 문서와 웹 대화 링크를 먼저 참조하고, 원본 증거가 필요하면 현재 작업 환경에서 확인한다. 구 Claude 0.6.2 임시 실행기는 `.review/legacy-review-gpt6-pro-claude062.py`에 보관했다.

### 남아 있는 한계

Provider-neutral/fake 테스트를 통과한 것이며 live SDK, MCP 배포 또는 실제 클라이언트 검증 완료를 의미하지 않는다. 명세 §41 선행 live 검증은 기존 deferred 상태다. Single-active-worker 전제와 최종 확인부터 Queue 감사 쓰기 사이의 non-CAS 가시성 한계도 유지한다.

---

**아래는 과거 진행 이력이다.** “현재”, “대기”, “미완료”, “커밋 없음” 등의 표현은 당시 상태이며, 위 최신 인수인계보다 우선하지 않는다. 완료된 리뷰를 다시 시작하거나 과거 Sonnet 리뷰 호출을 반복하지 않는다.

## 이전 인수인계 — Phase4 rev10 검증 완료, native 웹 + Gemini 재검토 (2026-09-09)

**최신 사용자 지시: Sonnet 리뷰는 일회성이었으므로 앞으로 리뷰용 호출 금지. 이후 리뷰는 Codex native insane-review + Gemini만 사용한다. Phase4 완료/커밋은 아직 아니다.**

- Luna Ampere 종료. 새 두 회귀 파일 30 tests는 두 Python 모두 통과, sealed rev9에서는 16 expected failures /14 passes. Root가 새 테스트 전체를 검토했다.
- Root 전체 **866 passed × Python3.11.16/3.14.7**. Ruff188/mypy74 기존 부채, compile/projection/전체 흐름/부분 페이지 및 effect-marker 반례/diff check 통과.
- rev10 고정126파일 source-copy **731 passed**, 작업 중 hash 변경 없음. `.review/phase4-rev10-pack-audit.json`, `.review/phase4-integrated-review-hashes-rev10.json`. 페이지 helper251개(0–250).
- Native 웹 session **86490**, `.review/phase4-rev10-insane-review.log`; launcher `/tmp/syllva-phase4-rev10-native-review.py`. 같은 독립 웹 리뷰 대화 https://chatgpt.com/c/6aa13845-5fdc-83e9-a3e9-b3e32c1e9bdb 에 새 전체 첨부. Latest/매우 높음 UI 검증, 강제 답변 없음. 이전과 동일한 파일은 실제 내용 동일성 확인 후 이전 독립 읽기 재사용 가능, 변경/추가 파일은 전체 재검토. 새 응답 turn 기준을 전송 전에 수집해 이전 REVISE와 구분한다. Timeout은 같은 URL native harvest로 회수.
- Gemini3.8Flash high Descartes **01a085ef-7f18-7b41-9c12-6678da68018b**: 독립적으로 sealed126파일/251페이지 전체 읽기, 최종 후 raw output 및 인용 감사 필요. 다른 리뷰 판정 미전달.
- **rev10 Gemini 완료:** 전체251원본출력/5인용root대조, 독립731pytest/projection통과 확인. static/줄번호/재현 import provenance 정정과 root qualification 후GO수용. Descartes종료. `.review/phase4-rev10-gemini-{final.md,addendum.md,audit.json}`. 웹은대기중.
- 다음: 현재 웹 최종 수집/검증 → 지적 있으면 root 재현·수정 → 해당 revision의 두 GO와 root 수용 후 완료 문서/로컬 커밋. **Phase5–8/push 금지. 승인 plan rev6/frozen 불변.**

## 이전 인수인계 — Phase4 rev9 웹 REVISE2 확인, rev10 통합 수정 (2026-09-09)

**최신 사용자 정정:** Sonnet 리뷰는 일회성 요청이었다. 앞으로 리뷰용으로 호출 금지. 현재 Sonnet 작업은 모두 종료했고, 이후 리뷰는 Codex native insane-review + Gemini3.8Flashhigh만 사용한다. 기존 추가리뷰 기록은 과거 사실로만 보존한다.

**이 절이 아래 기록보다 우선한다. 완료/커밋 아님.** Codex native insane-review가1810초 후정상회수/exit0. 최종 `.review/phase4-rev9-insane-review-final.md`는 전체124파일/701tests검토후 REVISE2건이다. Root가둘다양operation재현했다. 이전rev9Gemini/SonnetGO는완료게이트로사용하지않는다.

1. 부분만존재하는범위허용: 실제페이지1–40, graphPageCount41, desired40–41이면MATERIAL_USAGE/PAGE_RANGE 모두APPLIED였음. Root가공유 `_phase4_page_chunks`를완전한범위증명으로수정(유효단일페이지로케이터의집합크기, 거대range순회없음). 정상승인/복구모두같은helper사용.
2. effect_observed표식외부쓰기중인간old복원 후잘못APPLIED: Root가표식검증후/최종Queue검사전에 fullstrict target/dependency audit-only재검증추가. 복원/변경/불확실이면marker보존+APPROVED조정대기, 재쓰기/감사없음. 정상적용/기존audit-only재시도/partialAPPLIED복구모두이검사통과필요.

- Root증거 `.review/phase4-rev10-root-{reproduce.py,before.json,after.json}`: 부분범위는두operation모두SUPERSEDED/writes0; 효과표식중복원은APPROVED/writes1/인간값보존/audit없음/marker보존으로수정확인.
- **현재 Luna max Ampere** `01a085e1-8469-7010-878d-84ab99433618`: 새 `tests/contract/test_phase4_rev10_pages.py`, `test_phase4_rev10_audit.py`만소유. Root가base.py통합수정, Luna가permanentregressions(부분range/valid39–40/40/복구;markerwrite중old/sibling/Course/Type/sourcebinding/fingerprint변경)병렬담당. 기존tests/docs/sourceedit금지. Focused두Python+새testRuff필요.
- 웹프로세스36666종료. URL https://chatgpt.com/c/6aa13845-5fdc-83e9-a3e9-b3e32c1e9bdb , 원본 `.insane-review/response_Syllva_20260909_194259_74569_ddb89e.md`. nativeplugin0.6.8/Latest매우높음/완전본문/강제답변없음. 다음리뷰도native launcher를rev10으로갱신해사용.
- 다음: Luna회귀합치고전체두Python+root반례/스모크/static/컴파일/projection → 완전한rev10첨부감사 → 현재웹+Gemini독립통합리뷰. **Phase4만, Phase5–8/push금지, 커밋없음.** 계획rev6/frozen불변.

## 이전 인수인계 — Phase4 rev9 검증 완료, native 웹+Gemini 통합 리뷰 진행 (2026-09-09)

**이 절이 아래 기록보다 우선한다. 최종 완료/커밋은 아직 아니다.** rev8 웹 REVISE2건을 수정하고 root 검증을 마쳤다. 승인계획rev6/두 frozen문서는 hash불변이다.

- Luna Carson 수정 완료/종료. durable prepared/effect_observed 표식으로 restart 전 실제 효과 확인 여부를 구분한다. Prepared-only + 나중 desired는 APPROVED/조정대기, target/audit 없음. 실제 write+신뢰할 수 있는 desiredreadback 후 effect표식 저장/검증한 경우만 audit-only recovery 가능. phase-less/unknown marker보수적거절. 정상 승인/복구의 material validation은 전체 strictpageindex 사용.
- Root rev3 기존 unreadable→나중desired성공 기대2건을 새 보수적계약에 맞게 APPROVED/감사없음으로 강화. divergent거절 유지. 신규37회귀, 기존 집중108tests×두Python통과. 루트전체 **836passed × Python3.11.16/3.14.7**. 두operation의 preparedcrash→humanDesired는writes0/audit없음; 실제producer→p40→승인→검색→재적용→철회 모두통과.
- `.review/phase4-root-rev9-results.json`: compile/projection/smoke/page40/repro/diff통과. Ruff188(기준190, rev8=186; effect표식의 보수적예외처리2건증가)/mypy74(기준76), 새normalized타입오류없음. 정적검사clean은아님.
- 고정 manifest/hash `.review/phase4-integrated-review-{files,hashes}-rev9.*`:124파일. Sourcecopy격리 **701passed**, `.review/phase4-rev9-pack-audit.json` root `/var/folders/p7/6kdby1xx3t148xw4sys5ql340000gn/T/syllva-phase4-rev9-pack-cd74doqp`. 모든파일읽기 helper `.review/phase4-rev9-read-page.py`,249pages0–248.
- **현재 native웹**: unifiedsession36666, `.review/phase4-rev9-insane-review.log`, launcher `/tmp/syllva-phase4-rev9-native-review.py`; Codex plugin `/Users/admin/.codex/plugins/cache/gptaku-codex/insane-review-codex/0.6.8/bin/pack_and_ask.py`. 실제첨부라인/격리test감사를 업로드전 hook으로 수행한다. Latest/매우높음 visibleUI검증, forcedanswer비활성. Timeout은동일URL nativeharvest로만회수. 전송URL/최종report는로그확인.
- **현재 Gemini3.8Flashhigh** Rawls `01a085c3-ea13-76e1-a082-9c4b0aff92ce`: 새sealed124files/249pages 전체통합독립리뷰. 다른리뷰결과미전달. 최종후 `.review/phase4-rev9-audit-agent-pages.py AGENT_ID`로 rawrollout customtooloutputs 포함 전체원본대조; 인용대조. incomplete는GO아님.
- **rev9 Gemini 완료:** 전체249출력/5인용root일치, GO수용, Rawls종료. `.review/phase4-rev9-gemini-{final.md,audit.json}`. 테스트701passed는root실행로그를검토한것이며 독립실행아님을정정. Producer전체None, applierMaterialNone/Session8을실제코드대조. 실제첨부124파일/411858tokens/701tests와격리projection도통과. 웹 URL https://chatgpt.com/c/6aa13845-5fdc-83e9-a3e9-b3e32c1e9bdb , 계속생성중.
- **추가 Sonnet5high 현재rev9리뷰:** 웹대기중사용자추가리뷰요청을현재수정본에도적용. Franklin `01a085cc-c462-7651-9a0b-058bd0b7238f`가동일124files/249pages전체독립검토중. 다른리뷰결과미전달. `.review/phase4-rev9-audit-agent-pages.py AGENT_ID`로읽기원본감사. 이전rev8SonnetGO와구분한다.
- **rev9 Sonnet 완료:** Franklin GO,249페이지원본출력/5정확인용대조완료/종료. `.review/phase4-rev9-sonnet-{final.md,addendum.md,audit.json}`. Source-only검토. 계획헤더수정제안은불변조건에따라철회했고 configNone기본값은실제테스트된의도된동작으로정정. Gemini+Sonnet현재GO수용, **웹최종판정만대기**.
- 다음: 실제첨부감사확인+두현재독립GO+root확인 후 완료문서/로컬커밋. **Phase4만, Phase5–8/push금지.** 사용자요청nativeplugin실제harvest도성공했으며 자세한증거는아래기록.

## 이전 인수인계 — Phase4 rev8 웹 REVISE 회수, rev9 수정 중 (2026-09-09)

**이 절이 아래 기록보다 우선한다. Phase4 완료/커밋은 아직 아니다.** 지연된 웹 최종 보고서를 회수했다. 결과는 **REVISE 2건**이며 root가 두 operation 모두에서 재현했다. 기존 Gemini/Sonnet rev8 GO는 현 완료 게이트로 사용할 수 없다. 게이트 대체 질문은 더 이상 진행의 전제가 아니며, 수정 후 웹+Gemini 독립 GO 조건을 유지한다.

- 웹 보고서: `.review/phase4-rev8-insane-review-final.md`, 회수 증거 `.review/phase4-rev8-web-harvest.json`; 대화 https://chatgpt.com/c/6aa10f98-9608-83e9-bda7-b7c0352c55c3 . 전체122파일/664테스트 검토 후 최종 REVISE.
- 결함1: prepared 표식 저장 후 target 호출 전 프로세스 종료 → 인간이 desired 상태로 변경 → 새 applier가 target_mutations0인데 APPLIED/감사를 기록한다. durable prepared/effect_observed 구분으로 보완 중이다.
- 결함2: 정상 승인과 복구가 material32chunks로 제한되어 유효한 p40 제안을 SUPERSEDED로 거절한다. 완전한 strict 페이지 인덱스로 보완 중이다.
- Root 재현: `.review/phase4-rev9-root-reproduce.py`, `.review/phase4-rev9-root-before.json`. MATERIAL_USAGE/PAGE_RANGE 모두 두 결함 재현.
- Luna max Carson `01a085b0-fa8d-7ef1-a31c-3b02a84182dd` 활성: `src/uls/adapters/notion/base.py`, 새 `tests/contract/test_phase4_rev9_{recovery,pages}.py` 소유. root는 통합검증/문서/리뷰 준비 담당.
- 사용자 지시로 다음 리뷰부터 **Codex native insane-review-codex0.6.8** 사용. Skill `/Users/admin/.codex/plugins/cache/gptaku-codex/insane-review-codex/0.6.8/skills/insane-review/SKILL.md`; engine 같은 root의 `bin/pack_and_ask.py`. ensure-env 모두 정상(CDP9222/Chrome/loginok). 기존 Claude plugin wrapper는 기본 실행 경로로 쓰지 않는다. 현 UI Pro disabled/Latest checked/매우높음 slider3 실측. Native 선택기의 hidden요소/구형메뉴 호환은 `/tmp/syllva-phase4-rev9-native-review.py`의 작은 실행 adapter로 보완했고 `.review/phase4-rev9-native-model-probe.json`에서 검증 true. native engine의 패킹·첨부·회수·harvest 유지, 설치 파일 변경 없음. 전송 후 timeout은 native `--harvest`로 회수하며 중복 전송/조기 답변 강제 금지. 실제 rev8 native harvest도 exit0로 성공: `.insane-review/response_harvest_20260909_193410_73952_a93120.md` (11,555자/REVISE), `.review/phase4-rev8-native-harvest.log`.
- 다음: 두 수정 통합 → 전체 두Python 및 root 반례/흐름검증 → 완전한 rev9 첨부 감사 → 새 독립 통합리뷰. 승인계획rev6와 frozen2문서 불변. **Phase4만, Phase5–8/push 금지, 커밋 없음.**

## 이전 인수인계 — Phase4 rev8 구현·검증 및 Gemini/Sonnet GO, 웹 게이트 결정 대기 (2026-09-09)

**이 절이 아래 모든 기록보다 우선한다. Phase4 최종 완료/커밋은 아직 아니다.** 구현 rev8은 승인 계획 rev6에 따라 완료했고, 전체 테스트와 두 독립 서브리뷰는 통과했다. 필수였던 웹 리뷰는 연결 오류와 타임아웃으로 최종 판정을 내지 못했다. 사용자에게 이번 완료 조건을 Gemini+Sonnet GO로 대체할지, 웹 GO 조건을 유지할지 질문했으며 아직 답변이 없다. **명시적 답변 없이 웹 게이트를 대체하거나 완료·커밋하지 않는다.**

- **Root 검증:** Python3.11.16 /3.14.7 각각 **799 passed**. 실제 웹 첨부 및 source-copy 복원 환경은 **122파일 /664 passed**, Behavior projection lint도 통과. 컴파일/전체 흐름 스모크/diff check 통과. Ruff186·mypy74는 기존 부채(기준190·76), 새 정규화 타입 오류 없음.
- **Gemini3.8Flash high: GO.** 기존 긴 문맥의429를 새 서브에이전트로 복구해 전체205페이지를 다시 읽었다. 실제 모든 출력과 인용3개 원본 대조 완료. `.review/phase4-rev8-gemini-final.md`, `.review/phase4-rev8-gemini-fresh-audit.json`. Agent Sagan `01a08538-988a-7632-9743-c88a64afc7a9` 종료.
- **Sonnet5 high: GO.** 사용자가 웹 대기 중 추가 독립 리뷰를 요청했다. 전체205페이지/122파일 원본 실행 로그와 인용5개를 대조했다. `.review/phase4-rev8-sonnet-final.md`, `.review/phase4-rev8-sonnet-raw-audit.json`. Lagrange `01a08542-e184-7493-938f-2c91b7eed986` 종료. 처음 누락처럼 보인188–204는 app read_thread가 묶음 functions.exec 출력을 생략한 조회 문제였고, 원본 로그에서 처음부터 읽었음을 확인했다. Sonnet의 읽기 누락이 아니며 사용자에게 정정했다. 기존 Phase3의 비차단 dead-code 지적은 범위 밖으로 유지.
- **웹: 최종 판정 없음.** UI Latest/매우높음 검증 후 같은 첨부를 읽고664tests/compile/projection 통과를 확인했지만, 추가 시스템 검토 지연→연결 끊김으로 첫3600초 회수는exit1. 같은 대화·같은 모델에서 자연스러운 검토 재개를 요청했으나 추가1800초도timeout/exit1. 조기 답변 강제나 다른 모델 대체는 하지 않았다. 두 로컬 수집 프로세스88516/43368 모두 종료. 웹 대화 자체는 남아 있고 이후 결과는 아직 확인되지 않았다.
- 웹 URL: https://chatgpt.com/c/6aa10f98-9608-83e9-bda7-b7c0352c55c3 . `.review/phase4-rev8-insane-review.log`, `.review/phase4-rev8-web-initial-failure.json`, `.review/phase4-rev8-web-resume.log`, `.review/phase4-rev8-web-resume-result.json`. 최종 웹 report는 생성되지 않았다. 첨부 `.insane-review/pack_Syllva_20260909_164856_67907_7072d1.md` (122파일/404,347tokens), 두 복원 검증 모두664passed.
- **최종 무결성:** `.review/phase4-rev8-final-integrity.json`에서122파일 모두 리뷰 snapshot과 일치, 승인계획 및 두 frozen문서 hash불변, diff check0. HEAD `bfc592b4fc15df68df599bc8b0811cace521b1f7`, branch `codex/phase4-8`; 이번 작업 커밋/push 없음. 모든 구현/문서 변경은 작업 트리에 보존.
- 다음 담당자: 먼저 사용자의 게이트 선택을 반영한다. Gemini+Sonnet 대체를 승인하면 완료 문서를 갱신하고 로컬 구현/인수인계 커밋까지 마무리(공유 스냅샷에 변경이 생겼다면 필요한 검증 갱신). 웹 조건 유지라면 같은 대화의 실제 최종 판정부터 회수/검증한다. 웹 미완료를 GO로 취급하지 않는다. **Phase4만, Phase5–8/push 금지.**

## 이전 인수인계 — Phase4 rev8 검증 완료, 통합 리뷰 진행 (2026-09-09)

**이 절이 아래 기록보다 우선한다.** 이전 웹 리뷰는 오류 종료가 아니라 정상 회수/exit0이었으며, 세 지적 모두 rev7–8에서 수정했다. 현재 새 rev8 웹 리뷰와 Gemini 통합 리뷰를 진행한다. 사용자 상태 질문은 작업 중단 지시가 아니다.

- Luna Goodall/Kuhn 수정 완료 및 종료. rev8은 실제 시도 없는 APPLIED 오인 방지, 무관한 malformed Usage 격리를 보완했다. 기존 targetID/rawexact sibling 거절과 prior-marker 복구는 유지한다. 새40회귀, 전체 **799 passed × Python3.11/3.14**.
- 정확한122파일 snapshot과 실제 웹첨부 복원 모두 **664 passed**. Behavior projection lint도 격리 환경 통과. `.review/phase4-rev8-{pack-audit,packed-content-audit}.json`. Ruff186/mypy74 기존부채, 새타입오류없음. 승인계획/두 frozen 문서 hash불변.
- 웹: process88516, `.review/phase4-rev8-insane-review.log`, `.insane-review/pack_Syllva_20260909_164856_67907_7072d1.md`, Latest/매우높음 UI검증 및전송완료. 대화 https://chatgpt.com/c/6aa10f98-9608-83e9-bda7-b7c0352c55c3 . 추가시스템검토안내표시중이나검토내용이추가되고있음. 결과/실제전체커버리지 확인 필요.
- Gemini3.8Flashhigh: 기존 Planck `01a08524-7809-7ce0-a027-04c81230181a`는0–139완료후긴문맥재개429반복으로종료. 사용자가Gemini서브에이전트재시도를요청했고 새 Sagan `01a08538-988a-7632-9743-c88a64afc7a9`는 `GEMINI_SUBAGENT_OK` 정상응답. 새agent에서 같은122files/205pages **전체를0부터다시읽기완료**, 모든실제출력감사완료/최종**GO**, 인용3개원본대조/현재rev8root수용. `.review/phase4-rev8-gemini-final.md`. 새agent종료, 이제웹최종판정대기. `.review/phase4-rev8-gemini-fresh-audit.json`. 기존부분커버리지를새GO에합산하지않음. 각fullpage출력감사, 모두읽은뒤전체판정1회. helper `.review/phase4-rev8-read-page.py` 고정archive. Sonnet대체승인은없음.

- 사용자추가지시: 웹대기동안Sonnet리뷰요청. Sonnet5high Lagrange `01a08542-e184-7493-938f-2c91b7eed986` 같은122files/205pages 독립통합리뷰(기존리뷰결과미전달). **전체읽기원본로그대조완료**, 최종**GO**/인용5개원본일치/root수용/agent종료. `.review/phase4-rev8-sonnet-final.md`. `.review/phase4-rev8-sonnet-raw-audit.json`:223개출력모두실제source라인일치,205pages커버. 주의: app read_thread는 functions.exec묶음출력을누락하여188–204가안보였음. root가처음잘못누락판정/재읽기요청했지만원본rollout에서처음부터읽었음을확인하고사용자/agent에정정. Sonnet누락이아님. 원본 `/Users/admin/.codex/sessions/2026/09/09/rollout-2026-09-09T17-22-28-01a08542-e184-7493-938f-2c91b7eed986.jsonl`의response_item custom_tool_call_output까지감사할것. 기존GeminiGO/웹게이트를대체한다는지시는아니며추가리뷰다.

- 웹현재: 약54분시점연결끊김안내가나타남. 같은대화reload후에도동일, stop-buttonvisible/최종본문미완성. 첫collector60분제한/exit1종료, 같은대화다시열어도최종판정없음. 사용자에게이번완료게이트를Gemini+Sonnet독립GO로대체해로컬커밋할지/웹GO조건유지할지비동기질문전달. **답변전대체완료나커밋하지않는다.** 현재두서브리뷰GO는원본출력/코드인용으로검증완료이며코드799tests×두Python통과.

- 웹복구재개: 사용자완료조건선택은아직답변없어기존웹조건유지. 오류로멈춘**같은대화**에독립전체검토계속요청을전송(Latest/매우높음재검증, 새첨부/다른리뷰결과없음, 조기답변강제없음). 현재process43368, `.review/phase4-rev8-web-resume.log`, script `/tmp/syllva-phase4-rev8-resume-web.py`, 최대1800초. 성공시 `.review/phase4-rev8-insane-review-final.md`와resultJSON저장. 이전process88516은exit1종료. 첫실패 `.review/phase4-rev8-web-initial-failure.json`.

- 다음: 두 current독립GO+root검증, 문서완료기록/로컬커밋. **Phase4만, Phase5–8/push금지, 아직커밋없음.** 이전GO는현코드완료게이트로사용하지않음.

## 이전 인수인계 — Phase 4 구현 rev8 보완 진행 (2026-09-09)

**이 절이 아래 모든 기록보다 우선한다.** rev6 웹 리뷰는 추가 시스템 검토 안내로 지연됐지만 **정상 회수/exit0 종료**했다. 현재 실행 중인 웹 리뷰는 없다. 최종 `.review/phase4-rev6-insane-review-final.md`는 REVISE3건이며, 1건은 rev7에서 해결했고 나머지2건을 Luna로 보완 중이다. 사용자가 ‘웹리뷰 오류 같음’을 물었고, 정상 회수·종료 및 현재 수정 상태를 설명했다. 중단 지시는 아니다.

1. 일반예외+oldread-back만으로 복구 표식을 해제하는 문제: rev7 완료. trusted `ProviderWriteNotAppliedError` 보장 +즉시정확한old 확인만 해제 허용. wire code는기존PROVIDER_UNAVAILABLE. 새14회귀, 전체759tests×두Python 통과. Root `.review/phase4-rev7-root-unknown-old-fixed.txt`에서 인간복원보존/target_mutations1 확인. manifest112파일/624복원tests 통과.
2. **실제 시도 없는 APPLIED 오인**: virgin/no-marker 승인 제안에서 외부인이 먼저 desired 값을 만든 경우, 또는 이번marker arm중 target writer호출전에desired로 바뀐 경우, 감사/APPLIED를기록하면안된다. 과거의유효한attemptmarker+desired audit-only복구는유지하되 이번호출이시도하지않았음을아는경로는targetdrift로거절한다. Goodall Luna max `01a084dc-b70d-77a2-aedc-644117aedafc`가 notion/base.py 및새 `test_phase4_rev8_approval.py` 담당.
3. **무관한 잘못된Usage의전역거절**: no-ID나중복ID를가진 무관한Usage가 정상독립candidate/target까지막는다. 대상ID물리유일성은해당basis에한정하고, rawexacttuple검사는Verified/ID유효성과분리하여정확한형제는계속차단한다. Goodall이applier, Kuhn Luna max `01a084b7-9d0e-7ec2-94c6-20728eb82690`이 producer/material_usage.py 및새 `test_phase4_rev8_producer.py` 담당. 기존잘못된전역거절테스트가있으면계획을대조하여근거없이약화하지않는다.

- 총괄2/3재현 `.review/phase4-rev7-root-new-findings.txt`: virginDesired가APPLIED/targetwrites0; 무관한Referencep1 no-ID행이approvalSUPERSEDED/producerSourcePartialError를일으킴.
- rev7검증로그 `.review/phase4-root-rev7-pytest{311,314}.txt`, mypy74/Ruff186(기존부채, 새타입오류없음), 컴파일/스모크/diff통과. 아직새수정반영전체검증은전이다.
- 이전rev6GeminiGO는전체194페이지/코드인용5개를실제대조했지만root반례때문에완료게이트로인정안함. `.review/phase4-rev6-gemini-audit.json`. reviewer종료.
- 이전웹대화 https://chatgpt.com/c/6aa106e4-0ab8-83e8-aec2-972d78bfd701 , 원본 `.insane-review/response_Syllva_20260909_161150_65933_83322b.md`, 로그 `.review/phase4-rev6-insane-review.log`, process86031종료.
- 준비된rev7외부리뷰는전송하지않았다. 남은두지적을통합해다음스냅샷(rev8)으로전체검증/완전패킹감사/두독립전체리뷰를실행한다. 현재두구현에이전트만활성.
- 승인계획rev6와frozen두문서불변. **Phase4만, Phase5–8/push금지, 커밋없음.** 두현재독립GO및root확인후완료문서/로컬커밋까지마무리한다.

## 이전 검증 스냅샷 — Phase4 구현 rev6 (2026-09-09)

**이 절이 아래 모든 기록보다 우선한다.** rev5 웹 리뷰의 두 차단 결함(감사 실패 후 인간 수정 덮어쓰기, 초기 읽기 중 바뀐 입력의 모델 전달)을 Luna max 두 에이전트가 수정했고 모두 종료했다. Sonnet5 high의 복구 방식 자문은 총괄이 쓰기 예외/표식 보호/부분 감사 조건을 보완해 적용했다. Phase4 최종 완료는 아직 두 새 독립 판정 대기 중이다.

- 구현 rev6 전체 pytest: Python3.11.16 /3.14.7 모두 **745 passed**. 컴파일, Behavior projection, Producer→승인→검색→재적용→철회 스모크, diff check 통과. `.review/phase4-root-rev6-pytest{311,314}.txt`.
- 신규 `test_phase4_rev6_recovery.py`29건, `test_phase4_rev6_producer.py`16건. 이전700→745. 복구 수정 전6failed/4passed, Producer 수정 전9failed/3passed. 관련 집중검증99/97건씩 두Python 통과. 총괄 전체검사에서 잡은 Queue삭제시 거절 방식 차이도 기존계약대로 수정한 뒤 전체재실행했다.
- 총괄 재현: 초기원본변경 두건은 proposer_calls0/writes0. 감사실패/부분감사/State만APPLIED기록 후 인간VerifiedFalse 복원 세건은 새applier에서도 target_mutations1 유지, 인간값보존, APPLIED미보고. `.review/phase4-rev6-root-{premodel,recovery}-fixed.txt`.
- 승인된 계획 rev6 SHA256 `901c3e046ede6b39f9863d84bf4aeb41b4bbf3b4244ff9280e6940d01a768058`, 두 frozen 문서 불변. 구현리비전번호와 계획리비전은 별개다.
- 정적검사 Ruff186 vs baseline190, mypy74 vs baseline76, 새normalizedtype오류없음. 복구 경로의 fail-closed 예외 처리/cleanup 후read-back 패턴을 포함해 부채가 남아 있다. clean이라고 쓰지 않는다. `.review/phase4-root-rev6-static-diff.json`.
- sealed manifest `.review/phase4-integrated-review-files-rev6.txt`, 해시 `.review/phase4-integrated-review-hashes-rev6.json`: **111파일**. SQL/전이의존성 포함 별도복원 **610passed**. 실제첨부 행별대조 및 첨부만복원한610tests도 통과: `.review/phase4-rev6-packed-content-audit.json`.
- 실제 전체첨부 `.insane-review/pack_Syllva_20260909_161150_65933_83322b.md`, **395,205tokens**, 주석/본문/빈줄 생략없음.
- insane-review Latest/매우높음 UI·첨부·전송 검증. 사용자허용 일반새채팅 https://chatgpt.com/c/6aa106e4-0ab8-83e8-aec2-972d78bfd701 . 로그 `.review/phase4-rev6-insane-review.log`, unified session **86031**, 최대3600초, 현재생성중. 최종답변만회수하며 강제답변금지.
- 독립 Gemini native `google-antigravity/gemini-3.8-flash` high, **Feynman** ID `01a08502-98bb-7a63-bfbc-ffe29636d0c0`. 전체111파일을194개의 해시고정페이지로 읽는중: `.review/phase4-rev6-review-pages.json`, `.review/phase4-rev6-read-page.py`. 현재0–59, 다음60–119, 마지막120–193. 각명령의전체END PAGE출력/잘림을총괄확인후 하나의전체통합판정을받는다. 다른reviewer결과전달안함.
- 과거rev5웹보고서 `.review/phase4-rev5-insane-review-final.md`는REVISE2건. 해당프로세스47542종료. 과거GeminiGO는새코드에적용불가.
- 다음: 두현재리뷰회수/필요시지적재현수정 → 해당스냅샷의두GO와총괄검증 → handoff/verification/execution 완료기록/로컬커밋. **Phase5–8과push는진행하지않는다.** 아직커밋없음.

## 이전 검증 스냅샷 — Phase 4 rev5 (2026-09-09)

**이 절이 아래 모든 기록보다 우선한다.** Phase 4의 이전 차단 4건을 수정한 rev4에서 두 추가 결함이 발견됐다. 총괄이 논리 Session/Material ID drift와 malformed Verified exact sibling 승인 문제를 직접 재현했고, Luna max 두 에이전트가 공통 검사·Producer·승인·검색·SessionResolver 및 회귀 테스트를 수정했다. 두 구현 에이전트는 종료했다. Phase 5–8과 push는 진행하지 않는다.

- rev4 insane-review 최종 **REVISE, 차단 2건**: `.review/phase4-rev4-insane-review-final.md`. 107파일 전체 검토/복원499tests를 명시. 기존 Gemini GO만으로 완료하지 않았다.
- rev5 총괄 전체 pytest: Python 3.11.16 / 3.14.7 모두 **700 passed**. 컴파일, Behavior projection, 통합 Producer→승인→검색→재적용→철회 스모크, diff check 통과. `.review/phase4-root-rev5-pytest{311,314}.txt`.
- 새 회귀: `tests/contract/test_phase4_rev5_identity.py` **38**, `test_phase4_rev5_siblings.py` **28**. ID 수정 전 25 failed/6 passed, sibling 소비자 통합 전 12 failed/15 passed. 최종 두 Python에서 모두 통과. 총괄의 원래 승인 재현 세 건도 모두 SUPERSEDED/대상 쓰기0으로 확인: `.review/phase4-rev5-root-reproductions-fixed.txt`.
- 정적 검사: Ruff **174** vs baseline190, mypy **74** vs baseline76, 새 normalized type error 없음. 기존 부채가 남아 있으므로 clean으로 표기하지 않는다. `.review/phase4-root-rev5-static-diff.json`. 총괄은 마지막 타입 narrowing과 import/string formatting만 정리한 뒤 전체700tests×2를 다시 실행했다.
- 승인된 rev6 계획 SHA256 `901c3e046ede6b39f9863d84bf4aeb41b4bbf3b4244ff9280e6940d01a768058` 및 두 frozen 문서 유지.
- sealed manifest/hashes: `.review/phase4-integrated-review-{files,hashes}-rev5.{txt,json}` (실제 각각 files-rev5.txt / hashes-rev5.json), **109파일**. 의존성/SQL 포함 복원본 **565 passed**. 실제 첨부 전 소스 행 대조 및 첨부 복원565passed: `.review/phase4-rev5-packed-content-audit.json`, `.review/phase4-rev5-packed-pytest311.txt`.
- 실제 전체 첨부 `.insane-review/pack_Syllva_20260909_151218_62743_317a70.md`, **378,685 tokens**. 주석/빈 줄/본문 생략 없음.
- insane-review 최신/매우 높음 UI 검증·첨부·전송 확인. 사용자 승인 일반 새 채팅: https://chatgpt.com/c/6aa0f8cf-3a00-83ee-b6ff-971014c9ca76 . 로그 `.review/phase4-rev5-insane-review.log`, unified process session **47542**, 최대 대기3600초. 최종 응답만 회수, 강제 답변 금지. 현재 생성 중.
- 독립 Gemini native `google-antigravity/gemini-3.8-flash` high, ID `01a084cc-3319-74c2-8b15-dd6194691ab6` (Herschel). 같은109파일을187개 해시 고정 읽기 페이지로 전달한다: `.review/phase4-rev5-review-pages.json`, `.review/phase4-rev5-read-page.py`. 전체187페이지의 실제 명령/END PAGE 출력과 누락/잘림을 총괄이 확인했다. 최종 **통합 GO 승인**: `.review/phase4-rev5-gemini-final.md`, `.review/phase4-rev5-gemini-accepted-audit.md`. 최종 코드 인용4개도 실제 소스와 일치했다. reviewer 종료. 다른 reviewer 결과를 전달하지 않는다.
- **최종 완료 게이트는 아직 대기 중**. 적용 가능한 두 독립 GO와 총괄 확인 후 handoff/verification/execution 문서를 일치시키고 로컬 커밋한다. 소스는 리뷰 중 고정한다. 커밋/푸시하지 않았다.

## 이전 검증 스냅샷 — Phase 4 rev4 (2026-09-09)

**이 절이 아래 모든 과거 기록보다 우선한다.** 새 세션에서 Phase 4를 재개했고, 이전 통합 리뷰의 차단 결함 4개 수정과 영구 회귀 테스트를 완료했다. Phase 5–8은 비활성이고 push하지 않는다.

- 구현: Luna max 두 에이전트가 승인/Producer와 Retrieval을 분담했다. Sonnet 5 high가 frozen §45의 7개 완료 조건을 테스트와 대조했고, 별도 Luna가 실제 Producer → Queue → 승인 → 같은 검색 엔진 → 재적용 → 철회 흐름을 영구 테스트로 보강했다. 모든 구현 에이전트 종료.
- 승인된 rev6 계획 SHA256 `901c3e046ede6b39f9863d84bf4aeb41b4bbf3b4244ff9280e6940d01a768058` 유지. 두 frozen 문서도 변경하지 않았다.
- 총괄 전체 pytest: Python 3.11.16 / 3.14.7 모두 **634 passed**. 컴파일, Behavior projection, diff check, 별도 통합 스모크 통과. 로그 `.review/phase4-root-rev4-pytest{311,314}.txt`.
- 기존 취약 소스를 별도 디렉터리에 복원한 비교 재현: 동일한 19개 테스트 중 이전 소스 **18 failed / 1 passed**, 현재 소스 **19 passed**. 실제 위조 승인 허용/쓰기/철회 미검출로 실패했으며 import/fixture 오류가 아니다. `.review/phase4-rev4-{before,after}-fix-reproduction.txt`.
- 정적 검사: Ruff **174** vs baseline 190; mypy **74** vs baseline 76, 새 normalized type error 없음. 기존 lint/type 부채가 남아 있으므로 clean으로 표기하지 않는다. `.review/phase4-root-rev4-static-diff.json`.
- 새 회귀 파일: `tests/contract/test_phase4_rev4_{approval,producer,retrieval}.py`. 총 168건 추가 및 guarded 통합 1건 추가(기존 465 → 634). provider read 중 실제 mutation 실행을 확인하는 spy/assertion 포함.
- 리뷰 manifest는 Python 전이 의존성 및 `src/uls/state/migrations/001_initial.sql`을 포함한 **107파일**. 소스 복원본의 포함 테스트 **499 passed**. `.review/phase4-integrated-review-files-rev4.txt`, `.review/phase4-integrated-review-hashes-rev4.json`, `.review/phase4-rev4-pack-audit.json` 참고. 실제 첨부의 전체 내용을 대조하고 첨부만으로 복원한 실행도 **499 passed**: `.review/phase4-rev4-packed-content-audit.json`.
- 실제 insane-review 첨부: `.insane-review/pack_Syllva_20260909_141046_59144_939a8f.md`, **366,774 tokens**, 전체 코드/주석/빈 줄 유지. 큰 첨부의 전체 커버리지 확인이 GO 필수 조건이다.
- 최신/매우 높음 사전 UI 검증 성공. 기존 프로젝트 오류에 대한 사용자 예외로 일반 새 채팅 리뷰를 실행했다. 실행 로그 `.review/phase4-rev4-insane-review-retry1.log`, 프로세스 unified session `20541`. 모델 검증·첨부·전송 확인, 대화 https://chatgpt.com/c/6aa0ea62-8088-83e8-a59d-428b12918ef9 에서 생성 중. 아직 리뷰 결과 미회수. 첫 시도는 모델 검증에서 전송 전 종료됐고, 전송 없는 UI 재진단 성공 후 재시도했다.
- 독립 Gemini는 native subagent `google-antigravity/gemini-3.8-flash` high, ID `01a0847f-715b-7182-b35c-97c40d1e7209`. 사전 READY는 연결 확인일 뿐 판정이 아니다. sealed 107파일 리뷰의 첫 GO는 총괄의 실제 도구 실행 대조에서 전체 커버리지 주장이 입증되지 않아 **미승인**이다. 두 번째 GO도 잘못된 파일 길이·누락 본문 때문에 미승인이다. 같은 reviewer에게 해시 고정된 전체 소스를 182개 읽기 페이지로 전달해 검토를 마치도록 했다(`.review/phase4-rev4-read-page.py`, index `.review/phase4-rev4-review-pages.json`). 페이지 0–59는 실제 명령/END PAGE 출력을 총괄이 모두 확인했다(`.review/phase4-rev4-gemini-pages-0-59.json`, 누락 0). 60–119도 실제 명령/END PAGE 출력을 모두 확인했다(`.review/phase4-rev4-gemini-pages-60-119.json`, 누락 0). 마지막 120–181도 실제 명령/END PAGE 출력을 모두 확인했다(누락 0). 전체 182페이지/107파일 검토 후 최종 통합 **GO를 승인**했다. 최종 보고서 `.review/phase4-rev4-gemini-final.md`, 승인 근거 `.review/phase4-rev4-gemini-accepted-audit.md`. reviewer는 종료했다. 이는 동일 스냅샷 전체 리뷰의 전달 과정이며 범위별 GO를 받지 않는다. `.review/phase4-rev4-gemini.md`, `.review/phase4-rev4-gemini-audit.md`, `.review/phase4-rev4-gemini-command-audit.json` 참고. 다른 리뷰 결과를 주지 않았다.
- **Phase 4 최종 완료 게이트는 아직 열리지 않았다.** 두 적용 가능한 독립 GO와 총괄 판정 후 이 절과 verification/execution 문서를 완료 상태로 갱신한다. 소스는 리뷰 중 변경하지 않는다. 커밋/푸시하지 않았다.

## 과거 인수인계 — 사용자 요청으로 중단 (2026-09-09)

**이 절이 아래 모든 과거 진행 기록보다 우선한다.** 사용자는 “중단하고 handoff.md를 만들어 새 세션에서 시도”하도록 지시했다. Phase 4는 미완료이며, 새 세션에서 재개해야 한다. Phase 5–8은 진행하지 않는다.

### 저장소와 작업 범위

- 작업 위치: `/Users/admin/Project/Syllva`, 브랜치 `codex/phase4-8`, 기준 HEAD `bfc592b`.
- Phase 4의 기존 구현과 이번 수정은 모두 미커밋 상태다. 사용자 변경을 포함한 작업 트리를 reset/revert하지 말 것. 커밋/푸시하지 않았다.
- 승인된 계획: `docs/plans/phase4-material-usage.md` rev6. SHA256 `901c3e046ede6b39f9863d84bf4aeb41b4bbf3b4244ff9280e6940d01a768058`.
- 두 frozen 문서와 계획은 변경하지 않는다. Provider-neutral / strict Fake 구현 범위이며 live SDK와 Phase 5–8 확장은 요구하지 않는다.
- 사용자 선호: 수정들을 먼저 통합하고 전체 검증 후 넓은 Phase 4 통합 리뷰. 작게 나눠 빈번히 재리뷰하지 않는다. Sol 리뷰 사용 금지.
- 역할: 총괄은 현재 Codex, 구현은 Luna max, 계획은 Sonnet 5 high, 독립 리뷰는 insane-review + AGY Gemini 3.8 Flash high. 실제 사용 가능한 모델/도구는 새 세션에서 확인한다.

### 가장 최근 외부 리뷰 — 회수 완료

- **insane-review: REVISE, 차단 지적 4개.** 전문: `.review/phase4-integrated-insane-review.md`.
- 원본: `.insane-review/response_Syllva_20260909_122229_53260_f1e396.md`.
- 채팅: https://chatgpt.com/c/6aa0d123-0018-83ee-9dfa-2098b3cd6f1e
- 실제 검증 모델: `ChatGPT Latest (매우 높음 / Extended; UI version unspecified)`. 숫자 버전을 GPT-6로 임의 확정하지 않는다. Pro 사용 불가 시 매우 높음 fallback은 사용자 승인됨.
- **Gemini 재검증: GO.** `.review/phase4-integrated-gemini.md`, 근거 대조 `.review/phase4-integrated-gemini-audit.md`. 코드 인용 277줄 일치. 실행 명령 주장은 별도로 인증되지 않았으므로 root 검증을 대체하지 않는다.
- 처음 Gemini GO는 없는 API/틀린 소스 참조가 있어 폐기했다: `.review/phase4-integrated-gemini-unverified.md`. 이를 완료 근거로 쓰지 않는다.
- 두 리뷰 결과가 다르고, 이후 코드 수정도 시작했으므로 **현재 코드에 적용할 최종 GO는 없다**.

### 차단 지적과 이번 중단 직전 수정 — 아직 완료/전체 검증 아님

1. **호출자 승인 의미 비교 누락** — `src/uls/adapters/notion/base.py`, `_assert_supplied_matches_current`.
   - 정당한 저장 Queue가 APPROVED인 상태에서 호출자 객체의 Course, Source Ref, Review Reason, Source Hash=None을 위조해도 APPLIED 및 대상 쓰기 1회가 발생함을 root가 직접 재현했다.
   - 이번 수정: Phase 4만 canonical semantics를 기준으로 호출자의 존재하는 의미 필드를 검사한다. domain `_field`로 중복 별칭을 거절하고, 명시적 null도 검증한다. SourceRef는 기존 canonical parser의 provider/file identity 규칙을 사용한다. 인간 lifecycle/audit의 오래된 복사본은 의미 비교에 포함하지 않는다.
   - 수정 후 위 4개 재현은 모두 PolicyViolation, 대상 쓰기 0회였다.
   - **아직 영구 회귀 테스트 미추가.** 부분 매핑/Proposal-ID 문자열/정확한 매핑/SourceRef navigation-only 차이/명시 null/모든 별칭/Target DB 호환성을 추가 확인해야 한다. private domain helper 의존과 `_merge_records` 상호작용도 검토할 것.
2. **Producer 최종 body read 도중 source/graph 변경** — `src/uls/proposal/material_usage.py`, `_reread_trusted_inputs`.
   - 기존에는 fingerprint를 얻은 뒤 body를 읽고, body read 내부에서 상태가 바뀌어도 이전 상태로 생성할 수 있었다.
   - 이번 마지막 수정: 모든 body가 반환된 뒤 Session 및 후보 Material을 재조회하고 Course/Type/source binding/current fingerprint로 snapshot을 다시 비교한다. 다른 자료를 읽는 동안 Session이 바뀌는 경우도 검사하려는 구조다.
   - **이 패치는 추가한 직후 중단됐다. 구문/테스트 실행도 아직 하지 않았다.** post-read fingerprint와 body, mutable graph 객체, navigation-only alias 동작을 반드시 검증한다.
3. **초기 context 발급 도중 권한 변경** — `src/uls/retrieval/engine.py`.
   - 이번 수정: `_retain_current_evidence`를 추가했다. Session/직접 Material context에서 외부 읽기 이후 각 evidence-binding 쌍을 `_current_fingerprint_for_binding`으로 재검사하고 함께 제외한다. 독립적인 겹침 근거를 개별로 유지하는 의도다.
   - 직접 Material의 raw Type을 body read 이전에 캡처하도록 변경했다.
   - **아직 신규 회귀 테스트 미추가.** evidence/bindings의 zip 1:1 가정, 예외 처리, transcript/직접 Material/Usage, Verified/Role/range/Type/Course/source rebind, 독립 겹침 및 provisional 표기 등을 확인할 것.
4. **get_source_chunk 최종 body read 후 신선도 누락** — 같은 `engine.py`.
   - 이번 수정: chunk 파싱 후 반환 직전 `_current_fingerprint_for_binding(binding)`을 다시 호출한다. 권한 근거 소실은 LocatorNotAllowed, issued/body와 fingerprint 불일치는 LocatorStale로 거절한다.
   - **아직 신규 회귀 테스트 미추가.** 최종 read 내부에서 fingerprint 또는 전체 권한 근거를 변경하고 이전 body를 반환하는 사례를 검사한다.

3/4와 1 패치 후 기존 `test_phase4_rev3_retrieval.py` + `test_phase4_rev3_approval.py` **45 passed**. 이는 신규 결함 회귀 테스트도 아니고 전체 검증도 아니다. 그 이후 2번 Producer 패치를 추가했으므로 현재 전체 소스가 검증됐다고 말하면 안 된다. 최종 확인 뒤의 변경은 없다는 이전 90파일 hash 맵도 이제 과거 snapshot이다.

### 검증 기준과 검토 패킹 의존성 누락

- **이번 부분 수정 이전** 전체 테스트: Python 3.11.16 / 3.14 모두 465 passed. 기록 `.review/phase4-root-rev3-pytest311.txt`, `phase4-root-rev3-pytest314.txt`.
- 이전 정적 검사: Ruff 175 vs baseline 190, mypy 74 vs baseline 76. 기존 오류가 있으므로 “타입 오류 0”이 아니다. `.review/phase4-root-static-audit.md` 참고.
- 기존 통합 패킹은 90파일 / 약 309,593 tokens였으며 두 테스트의 실제 import 의존성이 빠졌다. Reviewer는 실행 가능 subset 325 passed를 보고했고, 이 누락 때문에 향후 GO 불가라고 명시했다.
- root가 AST 전이 import를 추적해 13파일을 추가한 **초안**: `.review/phase4-integrated-review-files-rev4.txt` (현재 103파일). state/sqlite, enrichment/writer 및 관련 orchestration 등을 포함한다.
- 별도 복원 디렉터리 `/tmp/syllva-phase4-rev4-pack-check`에서 실행한 결과 **326 passed, 4 failed**. 원인은 `src/uls/state/migrations/*.sql` 데이터 파일 누락에 따른 `no such table`이다. **SQL 파일은 아직 manifest에 추가하지 않았다.** Python import closure만으로 부족함을 확인한 상태다.
- 다음 담당자는 SQL 및 필요한 비-Python 리소스, 새 회귀 테스트까지 manifest에 포함하고, 패킹만으로 복원한 디렉터리에서 포함 테스트 전체를 실행해 closure를 검증해야 한다. 복원본은 현재 소스 패치보다 오래됐으므로 다시 복사할 것.
- 패킹은 comments/blank lines/function bodies를 제거하지 않는다. 대용량 truncation 경고가 있었으므로 전체 검토 확인 없이 GO로 처리하지 않는다.

### 승인/외부 실행 문제와 브라우저 상태

- 자동 승인 `auto_review`에서 **검토 자체가 90초 deadline**에 걸려 CreateProcess가 실행 전에 거절된다. 위험 판정이 아니다.
- 분석 기록: `.review/approval-timeout-diagnosis.md`, `.review/approval-timeout-evidence.json`. 누적 승인 문맥의 pre-turn compaction이 deadline을 소진한 근거가 있다. 설정으로 deadline을 늘리는 지원 항목은 찾지 못했다.
- 사용자가 사용자 승인 모드로 바꾼 뒤 실제 외부 리뷰 실행에 성공했다. **이번 마지막 턴 환경은 다시 auto_review**였고, Luna 실행도 같은 시간 초과로 프로세스 생성 전에 실패했다. 사용자가 새 세션에서 모드를 확인해야 한다. 제한을 우회하거나 승인 정책을 임의 변경하지 말 것.
- 실패한 Luna 명령: `codex exec -m gpt-5.6-luna -c 'model_reasoning_effort="max"' --sandbox workspace-write ...`. 프롬프트 `/tmp/syllva-phase4-rev4-implementation.txt`. **Luna는 실행되지 않았고 보고서도 없다.** root가 위 부분 패치를 수행했다.
- 새 세션이 Luna에게 위 프롬프트를 재사용하면 이미 root 부분 패치가 있다는 사실을 반드시 추가할 것. 전체 과정을 다시 처음부터 시작하거나 다른 변경을 덮어쓰지 않도록 한다.
- 기존 Syllva 프로젝트 URL: `https://chatgpt.com/g/g-p-6a9fdbd2dc3081919990a6607f8fe7c4-syllva-eeb93c01/project`. 로그인은 정상인데 프로젝트 화면이 ‘다시 시도하기’ 오류/정상 화면을 오가며 검증 실패했다.
- **사용자 명시 예외 승인:** “프로젝트가 안 열리면 새로 하나 만들던지 새 채팅으로 진행”. 이 허용으로 앞 리뷰는 일반 새 채팅 `--no-project`에서 완료했다. 기존 프로젝트 강제 조건 때문에 무한 반복하지 않는다. 전송 여부를 먼저 확인해 중복 리뷰를 방지한다.
- `scripts/review_gpt6_pro.py`는 설치된 insane-review 0.6.2를 호출하는 호환 wrapper. 최신/매우 높음 검증, 완료된 assistant 응답만 회수, 강제 답변 금지, 기존 URL 보존, pinned cached repomix를 지원한다.
- 이번 wrapper 수정: 프로젝트 readiness를 최대 45초 기다리고, 같은 URL의 정상 composer를 이미 확인했으면 중복 navigation을 피한다. 컴파일은 통과했으나 해당 개선으로 프로젝트 실행이 성공한 것은 아니다.
- cached repomix: `SYLLVA_REPOMIX_CLI=/Users/admin/.npm/_npx/a3bdd89f716944ac/node_modules/repomix/bin/repomix.cjs` (1.15.0, 원래 security 검사는 유지).
- 성공했던 새 채팅 launcher `/tmp/syllva-phase4-side-new-chat-review.py`는 **과거 90파일 manifest를 사용**한다. 다음 리뷰에서 그대로 실행하지 말고 완성된 새 manifest/prompt/log로 갱신한다.
- Gemini launcher 형태: `agy --model gemini-3.8-flash-high --effort high --mode plan --sandbox --print-timeout 30m --output-format text --print ...`. 참고 prompt `/tmp/syllva-phase4-integrated-gemini-evidence-prompt.txt`. 실제 파일 인용을 요구하고 이전 리뷰 결과는 주지 않는다.
- 새 세션에서도 동일 working tree를 사용해야 `.review` 및 `/tmp` 자료를 바로 이용할 수 있다. `.review`는 Git에 포함되지 않는 로컬 자료이므로 새 clone에는 따라가지 않는다.

### 새 세션의 실행 순서

1. 이 최신 절, `CLAUDE.md`, rev6 계획과 최신 insane-review 전문을 읽는다. 현재 미커밋 소스를 보존한다.
2. 부분 패치를 점검하고 4개 지적의 영구 회귀 테스트를 작성해 재현/수정을 완성한다. read 중 상태 변경을 직접 주입하고, CAS를 보장한다고 주장하지 않는다.
3. Python 3.11/3.14 전체 테스트, 필요한 compile/Behavior projection/기존 baseline 대비 정적 검사를 수행한다. 오류가 나면 수정 후 필요한 검사만 재실행한다.
4. 새 테스트·전이 import·SQL 리소스가 닫힌 review manifest를 만들고 별도 복원본에서 포함 테스트 전체를 실행한다. 새 hash/audit를 기록한다.
5. **한 번의 넓은 통합** insane-review + 독립 Gemini 리뷰를 실행·회수한다. Pro 불가 시 최신/매우 높음 검증 유지. 결함은 먼저 일괄 수정하고 전체 검증 후 재리뷰한다.
6. 두 리뷰의 적용 가능한 GO 및 총괄 검증이 충족돼야 Phase 4 완료로 기록한다. 이후 handoff/verification/execution 문서를 일치시킨다. Phase 5–8이나 push는 진행하지 않는다.

**중단 시 실행 상태:** 완료된 두 외부 리뷰 수집 프로세스는 종료했다. 마지막 Luna는 승인 단계에서 시작되지 않았다. 별도 패킹 검증 pytest도 종료했다. 이 작업에서 새 리뷰/수정/테스트를 백그라운드로 계속 돌리지 않는다. 아래 과거 기록의 “다음 작업” 문구보다 위 순서를 따른다.

---

## 1. 프로젝트 개요

**University Learning System (ULS) v1.2** — 개인 학업 지식·검색 시스템.
Model-agnostic · MCP-centered · Local-primary · Single-active-worker · Cross-platform (Python 3.11+).

> **ULS가 어떤 컨텍스트가 허용/관련되는지 결정하고, AI 클라이언트는 ULS가 제공한 컨텍스트 위에서 추론한다.**

권위 문서(코드가 충돌하면 아래가 우선):
- `university-learning-system-v1.2-design-frozen.md` (설계, frozen)
- `university-learning-system-v1.2-implementation-spec-frozen.md` (구현 명세, frozen)

역할·경계 요약: `Drive`=원본+정규화 파생, `Notion`=학술 그래프/상태/검증, `GitHub`=정확 ref 코드,
`Retrieval Engine`=scope/authority/freshness/provenance, `MCP`=read-only 경계, Skills=행동/데이터접근 아님.

---

## 2. 역할 분담 & 작업 파이프라인 (반드시 준수 — CLAUDE.md와 동일)

| 단계 | 담당 |
|---|---|
| 총괄/관리 | **현재 Codex 주 에이전트** — 요구 해석·계약 판단·검증·통합·머지 |
| 계획 초안 | **Claude Sonnet 5 high** |
| 개발/구현 | **Codex Luna max** (`gpt-5.6-luna`, reasoning=max) |
| 리뷰 | **insane-review (GPT-6 Pro, 불가 시 사용자 승인한 최신/매우 높음)** + **AGY Gemini 3.8 Flash high** (2중 독립) |

**2026-09-08 사용자 지정:** Phase 4부터 insane-review를 GPT-6 Pro로 실행한다(`--model pro --require-model "GPT-6"`). 실제 모델·Pro 추론 단계 검증 실패 시 중단하고 자동 대체하지 않는다. 설치/의존성·브라우저·로그인·활성 GPT-6 Pro 검증 완료(아래 실행 래퍼 사용). 아래 과거 Phase의 GPT-5.6 리뷰 기록은 당시 이력이다.

**Phase 4~8 연속 작업 착수(2026-09-08):** 사용자 승인으로 현재 Codex가 총괄하고 Sonnet 5가 계획 초안을 작성한다. 아래 파이프라인의 Opus 역할은 현재 총괄이 수행한다. 기준 테스트 238개·Behavior Contract lint·108개 Python 파일 구문 검사 통과. Sonnet 서브에이전트는 encrypted-task 전달 오류로 실행 불가하여 Claude CLI로 전환했고, 사용자 로그인 후 인증 복구를 확인했다. Gemini는 `agy` CLI로 실행 가능. ChatGPT 전용 브라우저 로그인 및 **GPT-6 Pro 활성 UI 검증 통과**. 설치된 insane-review 0.6.2의 모델 판독이 숨겨진 구모델 목록을 잘못 읽어 `scripts/review_gpt6_pro.py`에서 활성 헤더·Pro 슬라이더 검증만 보정한다. Phase 4 계획 작성 중이며 Phase 4~8 구현/리뷰 완료를 의미하지 않는다.

**최신 실행 상태:** Sonnet Phase 4 rev1 → Gemini 독립 리뷰 REVISE → Sonnet rev2 → 현재 총괄 통합 rev3를 `docs/plans/phase4-material-usage.md`에 저장했고 **Gemini 재리뷰 GO**를 받았다(`.review/phase4-plan-rev3-gemini.md`). 새 채팅의 Pro 메뉴는 사용량 한도로 비활성화되어 **“2026년 9월 13일 후에 다시 시도”** 안내를 확인했다. 앞선 `6 Pro` 선택 표시와 달리 신규 GPT-6 Pro 리뷰 실행은 현재 불가능하며, 리뷰 프롬프트는 전송되지 않았다. 사용자에게 접근 복구/리뷰 경로 변경 여부를 요청했고 답변 대기 중이다. 상세 상태·재개 지점은 `docs/plans/phase4-8-execution.md` 참조. Phase 4 구현 관문은 아직 통과하지 않았고 Phase 5~8도 미착수다. 커밋/푸시는 하지 않았다.

```text
1. 계획 수립(Opus) → 2. 계획 리뷰(2중) → 3. 구현(Codex) → 4. 검증(Opus)
→ 5. 2중 리뷰 → 6. 커밋/푸시(둘 다 GO + 검증 일치 시에만)
```
리뷰어가 엇갈리면 **Opus가 직접 코드로 재현해 타이브레이크**. 지적으로 재수정 필요 시 3~5 반복.

**중요 교훈:** AGY가 GO를 준 지점에서 GPT-5.6이 실 결함(fail-open, 계약 드리프트, self-approval 등)을
Phase 1/2 내내 반복적으로 잡았다. **단일 리뷰어는 불충분** — 반드시 2중 + Opus 재현.

---

## 3. 완료 상태

**재개 승인(2026-09-08):** 사용자가 "Pro 안되면 매우 높음으로 진행"을 명시했다. Pro quota 복구를 기다리는 조건은 해제되었으며, insane-review 최신/매우 높음 + Gemini high로 Phase 4 계획 리뷰부터 재개한다. 실제 UI model/effort를 검증·기록하고 Phase 4~8 관문을 계속 적용한다.

| 항목 | 상태 |
|---|---|
| 저장소 스캐폴드(§3 레이아웃), CLAUDE.md, 계약/클라이언트 프로젝션 | ✅ `5bb9d34` |
| Phase 1 Core Hardening (state/ephemeral/orchestration/config/human-gate) | ✅ `ab3c9a1`+`63cb951` |
| Phase 2 Transcript Vertical Slice (normalization/ingest/retrieval get_session_context) | ✅ `6572feb` |
| Phase 3 Enrichment & Freshness (producer: LLM adapter/enrichment schemas·generators/writer) | ✅ `f4ab8b0` |
| 테스트 | **238 passing** (모델 독립 contract/unit/integration) |

검증 도구 환경: `codex` CLI(`gpt-5.6-luna`), `agy` CLI(`gemini-3.8-flash-high`), `insane-review` 플러그인(GPT-5.6 Sol),
pytest+pyyaml 설치됨(인터프리터: `/Library/Frameworks/.../python3.14`).

### 구현된 핵심 계약 (리뷰로 확정된 불변식)
- **도메인**(`src/uls/domain/`): Locator 문법/AST/직렬화/typed containment(§6.3, 문자열 prefix 인가 금지),
  enums/ids/source_ref/provenance/errors(§53). 표준 라이브러리만.
- **StateStore**(`state/sqlite.py`): 반복 마이그레이션, 결정적 `job_key`(§8.1.1), source-bound idempotent
  엔티티 할당(§8.6.1, caller 선점 불가), source_versions 일관성.
- **EphemeralStore**(`ephemeral/memory.py`): TTL·재시작 무효화, capability fingerprint 바인딩 + fail-closed
  `authorize_locator`(§25, current_fingerprint 미제공 시 deny, 불일치 시 LOCATOR_STALE).
- **Human-gate**(`adapters/notion/base.py`): 방어적 쓰기(§15.1, Verified/Scope Confirmed/Decision/State),
  create/update 분리, `ApprovalReader`/`HumanApprovalApplier`(human Decision By 필수, self-approval 차단),
  Automation Queue 상태기계, 시스템 경로 SUPERSEDED/FAILED만.
- **Retrieval**(`retrieval/*`): `resolve_entity`/`select_resolution` + `get_session_context(session_id,…)`,
  per-chunk 정확 capability allowlist(초과 인가 없음), §25 6검사(+role 현행성), SESSION authority(§17/§21,
  fetch order ≠ authority rank), freshness/stale-locator, read-only 어댑터 Protocol(§4/§13/§27).
- **Normalization/Ingest**: transcript verbatim + sidecar 타임스탬프(code-point offset), §17 커밋 순서 +
  Partial 3중 일관, 필수 collaborator/등록 fail-closed.

**공통 원칙: 모든 경계는 fail-closed.** 애매하면 거부/제외 — 절대 조용한 skip/합성/승격 금지.

---

## 4. 다음 단계 (frozen 순서)

구현 순서(§41): `Spike C0 → Spike M0 → VS0 → VS0-B → Spike G`, Phase 1~8(§42~§49).

- **Phase 3 — Enrichment & Freshness (§44)** ✅ **완료 (`f4ab8b0`)**: producer 구현 — `enrichment/{schemas,_common,session,material,exam,writer}.py`,
  `adapters/llm/{base,structured}.py`, `domain/enums.py(Explicitness)`, `NotionReader.get_material_enrichment`.
  explicit/inferred 분리·evidence locator 재해석·stale 제외·source fingerprint 전부 fail-closed. 소비자(RetrievalEngine)는 미변경.
  계획서 `docs/plans/phase3-enrichment.md` rev3, §7 에 **문서화된 결정론적 한계**(cross-store 가시성 창, 부정어 가드 밖 의미 판정) 명시.
  교훈: 구현 리뷰에서 Sol(GPT-5.6)이 AGY GO 를 여러 번 뒤집으며 실 fail-open(증거 상속, empty-READY, TOCTOU, §17 순서, 부정어 위조)을 잡음 — 5라운드 재수정 후 둘 다 GO.
- **다음 단계 → Phase 4 — Material Usage & Human Gate (§45)**: Material Usage/page-range proposal, 승인 경로, Verified 상태,
  multi-material Session 검색, unverified 정책. (Phase 2에서 read-only 소비만 했고 mutation/proposal은 여기로 미룸.)
- **Phase 5~8**: Exam/Activity(§46), GitHub 정확 ref 검색(§47), 클라이언트 패키징(§48), 데스크톱 자동화+remote MCP(§49).
- **MCP 트랙(별도)**: Spike C0(ChatGPT remote 연결)/M0(로컬 MCP로 get_material_context)/VS0 — 엔진은 이미 MCP 없이
  직접 호출 가능하므로 `mcp/` 스텁에 read-only 도구를 배선하면 됨(§19~§24). 현재 `mcp/`는 스텁.

**착수 방법:** Phase마다 `docs/plans/<phase>.md` 계획서를 Opus가 작성 → 2중 계획 리뷰 → Codex 구현 → 검증 → 2중 리뷰 → 커밋.
(예시 계획서: `docs/plans/phase2-transcript.md` rev2 참고.)

---

## 5. 개발/리뷰 실행 방법 (그대로 복사해 사용)

구현 위임(Codex Luna max) — **프롬프트는 파일로, stdin은 `< /dev/null`로 닫을 것**(안 닫으면 hang):
```bash
codex exec -m gpt-5.6-luna -c model_reasoning_effort=max --sandbox workspace-write \
  --skip-git-repo-check -C /Users/admin/Project/Syllva \
  "$(< /path/to/prompt.md)" < /dev/null > /path/to/codex.log 2>&1
```
리뷰(2중 독립):
```bash
# insane-review (GPT-6 Pro). 모델명과 Pro 추론 단계를 모두 검증; 접근 불가 시 중단
python3 scripts/review_gpt6_pro.py --target /Users/admin/Project/Syllva \
  --include "src/…,tests/**,…-implementation-spec-frozen.md" --model pro --require-model "GPT-6" \
  --prompt-file /path/to/review.txt
# AGY Gemini 3.8 Flash high
agy --dangerously-skip-permissions --model gemini-3.8-flash-high --effort high \
  --print-timeout 20m --prompt "$(< /path/to/review.txt)" < /dev/null > agy.log 2>&1
```
검증(Opus 직접):
```bash
python3 -m py_compile $(git ls-files 'src/uls/**/*.py')
python3 -m pytest -q tests/
python3 scripts/lint_behavior_projection.py   # Behavior Contract 드리프트
```

주의: `codex exec`에 `--full-auto` 플래그 없음(→ `--sandbox workspace-write`). heredoc과 codex를 한 명령에
합치지 말 것(stdin 충돌로 hang). `pumasi.sh start`는 자동 승인 분류기에 차단될 수 있어 `codex exec` 직접 사용.

---

## 6. 알려진 제약 / 낮은 우선순위 항목

리뷰에서 "정상 StateStore에는 실 위험 없음(malformed/incomplete adapter 전용)"으로 **릴리스 OPEN에서 제외**된 hardening 갭 —
Phase 3+에서 정리 권장:
- `_require_ingest_collaborators()`가 `get_job`을 필수 목록에 넣지 않음(프로즌 Protocol엔 있음). get_job 없는 duck-typed
  adapter가 PROCESSING 객체만 반환하면 재조회 없이 신뢰될 수 있음.
- `_allocate_entity()`가 반환 ID의 문법(parse_entity_id)만 검사하고 course/type 일치까지는 재검증 안 함.
- 과거 버그 버전이 이미 잘못된 `canonical_entity_id`를 영속 저장한 legacy 데이터는 §8.6.1 idempotency로 자동 교정되지 않음 → migration/scrub 필요.

기타:
- MCP `mcp/` 및 Claude package/ChatGPT app은 스텁. 클라이언트 지원 상태는 배포 의존(§27~§30).
- 실제 provider(Google/Notion/GitHub) SDK 연동은 미구현(어댑터 read-only Protocol + Fake로 검증 중). `adapters/*/api.py` 스텁.
- 원격 MCP/스케줄러/Goodnotes(§33)/CONCEPT 벡터검색은 v1.2 비목표 또는 후속 Phase.

---

## 7. 참고 파일
- 계획서: `docs/plans/phase2-transcript.md`
- 가이드: `CLAUDE.md`, `README.md`, `CHANGELOG.md`
- Behavior Contract: `contracts/study-behavior.md` (+ `clients/…` 프로젝션, `scripts/lint_behavior_projection.py`)
- 테스트: `tests/{unit,contract}/`, 픽스처 `tests/fixtures/fake_{notion,drive}.py`

## 2026-09-08 Phase 4 review recovery and revision in progress

- User explicitly directed continued revision after the recovered insane-review verdict. Sonnet 5 high is preparing draft rev4 via authenticated Claude CLI; log `/tmp/syllva-phase4-sonnet-rev4.log`.
- Current rev3: Gemini GO, insane-review REVISE. Preserve the latter report at `.review/phase4-plan-rev3-insane-review.md`; root validated all five required changes in `.review/phase4-rev3-root-response.md`. No Phase 4 source implementation gate has passed.
- Review ran with UI-verified Latest / 매우 높음, per user-approved fallback when Pro is unavailable. Do not label this as numeric GPT-6 or Pro.
- Repository ChatGPT project repaired and review moved: https://chatgpt.com/g/g-p-6a9fdbd2dc3081919990a6607f8fe7c4/project. Default project mode is mandatory for subsequent reviews; omit `--no-project`.
- Next: integrate and verify Sonnet rev4 → independent Gemini and insane-review on exact same snapshot → implementation after dual GO. Continue toward Phase 8 under original authorization.


### Phase4 rev6 gate passed; implementation active

- Independent plan gates: insane-review Part A GO and Part B GO, plus Gemini full-plan GO. Reports `.review/phase4-plan-rev6-{a,b}-insane-review.md` and `.review/phase4-plan-rev6-gemini.md`. No blockers remain on the reviewed canonical SHA256 `901c3e046ede6b39f9863d84bf4aeb41b4bbf3b4244ff9280e6940d01a768058`.
- Both ChatGPT reviews used the repository project and UI-verified Latest / 매우 높음. No forced answer.
- Luna max implementation dispatched on the existing branch, prompt `/tmp/syllva-phase4-luna-implementation-request.txt`, log `/tmp/syllva-phase4-luna-implementation.log`, final report `/tmp/syllva-phase4-luna-implementation.md`. Implementation gate is not a completion/release gate. Root verification and both independent implementation reviews remain required before a local commit. No push authorized.
- Optional review refinements for implementation QA: Queue APPLIED audit commits then raises must replay idempotently; use shared validated page index; preserve exact Course cardinality and dereference checks.
- Phase5/6 Sonnet raw drafts remain unapproved; Phase7 planning awaits Sonnet session reset (03:40 Asia/Seoul), not login repair. Continue original Phase4–8 scope.


### User scope update: Phase4 only

User explicitly narrowed active work to Phase4 only. Finish current Luna Phase4 implementation, root verification, independent implementation review and fixes. Do not start Phase5–8 planning/implementation or retry Sonnet Phase7 quota automatically. Existing future-phase drafts are retained as inactive reference, not active work.


### Root regression steering checkpoint

- First Luna run deliberately interrupted after baseline migration (239 full-suite passes) to deliver root findings; process31313 exited, unified session71738 closed. Root temporary regression suite found17 failures/11 passes, report `.review/phase4-root-regressions-first-run.txt`. Do not mistake baseline239 for Phase4 acceptance.
- Continuing same working tree with Luna max corrections: prompt `/tmp/syllva-phase4-luna-fixes-request.txt`, log `/tmp/syllva-phase4-luna-fixes.log`, final report `/tmp/syllva-phase4-luna-fixes.md`. The prompt explicitly includes all11 root finding groups and requires all28 temporary regressions plus permanent broader Phase4 tests. No active first-run implementer remains. Root still owns final verification and independent implementation reviews.
- AGY actual model ID verified: `gemini-3.8-flash-high`, run CLI with escalation (read-only service/log initialization needs host access), --mode plan --sandbox --effort high; no permission-bypass flags.
- User scope Phase4 ONLY remains in force.


### Phase4 implementation verification and independent review (2026-09-09)

- Active user scope remains Phase4 ONLY. Luna max corrections finished; root added immediate-prewrite approval revocation and Python3.11 compatibility corrections, and permanent follow-up revocation/independent-overlap tests.
- Root full verification:296 passed on Python3.11 and3.14; compileall, Behavior projection lint and git diff check pass. Static baseline: Ruff190 ->188 diagnostics, mypy76 ->74 errors; existing debt remains and intentional fail-closed exception handling is documented in `.review/phase4-root-static-audit.md`.
- Independent AGY Gemini3.8FlashHigh implementation review GO, zero blockers; `.review/phase4-implementation-gemini.md`. Root independently applied the same optional page-local variable naming correction.
- insane-review implementation is in progress as four full-source partitions: A Queue/guard/applier; B producer/trusted derivative; C engine/scopes/freshness; D capability/store/resolver. Each uses the existing Syllva ChatGPT project, verified Latest/매우 높음 (numeric version unspecified), no forced answer and no code compression. Full-source manifests `.review/phase4-implementation-{a,b,c,d}-files.txt`; all relevant implementation union plus root/Gemini integration review covers cross-partition calls.
- Completion/local commit remains gated on all web review verdicts and resolution of any concrete blockers. No push. Phase5–8 inactive.


### Integrated smoke found additional blockers; completion gate remains closed

- Root actual GuardedNotionWriter→producer→ApprovalReader→applier smoke found two defects after initial Gemini GO: new Usage branch uninitialized operation, and wrapper requiring create fields on partial updates. Root fixed both; permanent guarded workflow4cases cover create/reuse, update preserving Verifiedfalse/true, producer retry and approval replay. Full suites now300 passed each on3.11/3.14.
- New unresolved root reproductions: global payload char budget30 emits60 across Session+Material; required Usage ID=None accepted by wrapper. Recorded in `.review/phase4-root-inflight-findings.md` items14–17.
- Four initial web implementation reviews sent; A/B already exposing additional concrete boundary issues and C/D still checking. Retrieve final reports, reproduce/fix blockers, then re-review affected snapshots plus Gemini; initial Gemini GO is not final approval after semantic corrections. Logs `/tmp/syllva-phase4-implementation-{a,b,c,d}.log`. Do not commit or claim Phase4 complete yet.


### Active repair ownership after web implementation REVISE

- B,C,D finalreports saved `.review/phase4-implementation-{b,c,d}-insane-review.md` (allREVISE); A originalcaptureinvalid(userprompt), samechat harvestrunning. Do not treatinvalidAasverdict. Collector wrapperstrictassistantGO/REVISE+DOMcapture now avoidsdisconnectedplaceholder/sharedclipboard miscapture.
- Luna producer run owns only `src/uls/proposal/material_usage.py` and `tests/contract/test_phase4_producer_boundaries.py`; prompt/log/final `/tmp/syllva-phase4-producer-review-fixes{,-request}.txt/.log/.md` (actualrequestfilename `...-request.txt`). Initialassignedglobalbudget/evidence/multicandidate; fullBreportarrivedlater and requires follow-up for remaining B defects after thisrun.
- Separate Luna retrieval run owns retrieval/*,domain/page_range.py,domain/course_identity.py,config/validation.py and relevanttests exceptrootwriter/producerfiles. Prompt `/tmp/syllva-phase4-retrieval-review-fixes-request.txt`, log/final samebase `.log/.md`. ImplementsallC/D and BsharedUsageappID/strictfloat issues. Root mustintegrate helper intoNotion;producer followup uses samehelper.
- Root owns Notionbase/guarded,FakeNotion,writerboundary+guardedworkflowtests. No concurrenteditstosameownedfiles. Fullsuite duringactiveedits may transientfail; only finalstableverificationcounts.
- User Phase4ONLY, no commitsuntilfinaldualreviewGO, no push.


### Repair checkpoint (producer follow-up active)

- First producer runfinished; report `/tmp/syllva-phase4-producer-review-fixes.md`,6newboundarytests;87Phase4cases atthatintermediatesnapshot. SecondproducerLunamax nowactive: request `/tmp/syllva-phase4-producer-review-final-request.txt`, log `/tmp/syllva-phase4-producer-review-final.log`, final `/tmp/syllva-phase4-producer-review-final.md`; sameexclusiveproducer+producerboundarytestownership. Addresses fullBremainingconfidence/preflight/stale-suppliedMaterial/configdefaults/navigationidentity/strictappIDs/floats/rawType.
- Retrieval/domainLuna stillactive on C/D and sharedhelpers. RootNotionintegrated usage_app_id and rawTypecomparisons. Openrootregression: flatlowercaseid fallback stillmisreadas appID; test_phase4_writer_boundary missing_app_id case currentlyfails. Fixsharedhelperafterownerfinishes; do notweaken assertion.
- A originalreview timedout atserver; harvesterstoppedPID38797. Invalidcapturearchived, noAverdict. FreshcorrectedA reviewrequired. B/C/D REVISE reports retained.


### Root prewrite graph/source correction

- Added7callbackregressions to test_phase4_prewrite_approval.py: dependencyreadchanges UsageRole/range/Session, MaterialType, CourseKey, fingerprint, or exactsibling. All7failed beforefix; prewrite hadonlyQueuegrantrefresh.
- Root nowruns fulltarget/dependencyreconciliation beforetargetmutation AND desiredauditrecovery, withpostbodygraph/ref/fingerprintchecks,currentpagevalidation,physicalUsageuniqueness andexactduplicates. Queuegrantremains immediatelyprewrite.52prewrite/remainingcases and9positiveguarded/lifecyclepass; latest16prewrite+guardedpass. FullsuitependingtwoactiveLunas.
- Source ofrootfindings andtestlogs: `.review/phase4-root-inflight-findings.md`, `.review/phase4-root-prewrite-graph-before.txt`, `.review/phase4-root-preflight-after.txt`.


### Corrected implementation is stable; rev2 reviews active

- Both Luna correction runs finished. Reports `.review/phase4-{producer,retrieval}-correction-report.md`. Root finalcurrent fullpytest373passed onPython3.11 and3.14; behaviorlint,3.11compileall,diffcheckpass. No implementation writers active.
- Static: mypy74 vsbaseline76 withno new normalizeddiagnostics; Ruff175 vsbaseline190. Two root-test I001importsortwarnings remain toclean afteractivereviews (implementationlogicunchanged);othernewlintdiagnostics intentionalvalueparsing/failclosedexceptionhandling. Details `.review/phase4-root-static-audit.md`.
- Exactsource/testSHA256 `.review/phase4-implementation-rev2-all-hashes.json`; per-partreviewhashesandmanifests `.review/phase4-implementation-rev2-{a,b,c,d}-{hashes.json,files.txt}`. Allsnapshotfilesunchanged afterroot373tests.
- Rev2 webreviews A,C,D,B dispatchedinexistingSyllvaproject Latest/매우높음, strictassistant-completionguard+DOMcapture, noforcedanswer. Logs `/tmp/syllva-phase4-implementation-rev2-{a,b,c,d}.log`. Rootpackaudits nofileomissions; tokensA117510 B119313 C118872 D116736.
- IndependentGemini fullcorrectedreviewactive: `/tmp/syllva-phase4-implementation-rev2-gemini.log`, modelgemini-3.8-flash-high/read-only plan+sandbox. Keepindependence: noreviewverdictinput.
- Gates stillclosedpendingreviews; fixes/re-reviewsifneeded, thentest-onlyformatcleanup/finalverification/handoff/localcommit. No push; Phase4ONLY.
