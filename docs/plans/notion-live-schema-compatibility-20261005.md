# Notion live schema compatibility — PLAN only

**상태:** 2026-10-05 읽기 전용 계획. 범위는 School preflight에서 관측된 두 parser 호환성 문제뿐이다. 제품·테스트 소스, 설정, credential, Notion/Drive 자료, worker 상태, checker pin은 변경하거나 실행하지 않았다. Drive OAuth PLANv4의 동결·리뷰 대기와도 별개다.

## 확인된 근거

- `.insane-review/gui23-pr13-fixes-20261004/school-sdk-shape-diagnosis-20261005.json`은 Notion SDK `2025-09-03`의 다섯 configured data source를 읽기 전용으로 진단했다. 각 source의 ID와 property 이름은 설정과 일치했고, SDK response에는 `parent.database_id`와 `database_parent.page_id`가 있었다. 별도 database readback에서도 실제 database parent가 설정된 School parent와 일치하고 해당 database에 exact data source가 포함됨을 확인했다. 기존 validator는 다섯 source 모두를 `data-source parent mismatch`로 거부했다. detached 진단 사본에 검증된 parent만 투영했을 때 다음 거부가 다섯 source의 `status.groups` list였다. detached 사본은 runtime에 전달되지 않았다.
- 현재 `src/uls/adapters/notion/intake.py`의 `_data_source_parent_page_id()`는 legacy direct `page_id` 형태만 읽는다. `_status_group_names()`는 group-name → option-list `Mapping`만 읽고, status option ID를 group name으로 해석하지 않는다.
- 기존 `tests/integration/test_intake_worker_preview.py::test_notion_readiness_requires_exact_current_parent_schema_and_relations`는 legacy direct-parent 및 mapping-group fixture에서 five-source/76-property `VERIFIED`를 확인하고 잘못된 parent는 거부한다. 즉 legacy schema가 현재 regression 기준이다.
- 이전 live-flow disposition은 worker Notion auth 성공, `NOTION_MCP_TOKEN` 부재, worker disabled, provider/state/config mutation 없음, 전체 흐름 미통과를 기록했다. 이 계획에서는 doctor나 credential resolver를 다시 실행하지 않았고 `.env`·credential 값을 읽지 않았다. `NOTION_WORKER_TOKEN`은 MCP token의 대체가 아니다.

현재 Notion API의 Status schema 문서는 `groups`를 `{id, name, color, option_ids}` 요소의 배열로 기술한다. 이는 live 진단에 저장된 list shape와 부합하는 구조 근거이며, 실제 School payload를 다시 조회한 결과로 간주하지 않는다. [Notion API — Data source properties: Status](https://developers.notion.com/reference/property-object)

## 최소 변경 경로와 동결 범위

| 구분 | 파일과 현재 SHA-256 / bytes | 계획된 역할 |
|---|---|---|
| 변경 후보 | `src/uls/adapters/notion/intake.py` — `74062f85f6dd115a86d70b13bbb209c417ae3725845af5d3b06ea443fc315407` / 42,775 | `_data_source_parent_page_id()`에서 exact modern database-parent shape를 해석. `_status_group_names()`에서 legacy mapping과 modern array 두 형태를 같은 expected group→option-name map으로 정규화. |
| 변경 후보 | `tests/integration/test_intake_worker_preview.py` — `faf5fa7f2f3b9d5a83cd4160a7f07dbcf9ed09eac7f538f63161e2761891206e` / 32,386 | 기존 legacy 성공/parent 거부 회귀를 유지하고 synthetic modern SDK shape 회귀를 추가. |
| 읽기 전용 의존 | `src/uls/adapters/notion/api.py` — `183ae483b2aba27cdebb0d7f7117e3bbeb4246c866e1813673753700599f42d5` / 13,659 | Retrieval의 `NotionAPIReader` 경계 확인. 변경 대상 아님. |
| 읽기 전용 의존 | `src/uls/runtime.py` — `ccc992e3c506c4fc980a5cfcdd52d0a5b85af38e0af7a24064c8f16701921136` / 12,574; `tests/contract/test_mcp_runtime.py` — `ea82e73347e26348e2e0854b7ac53177370fc22835043246e624913ddbdaa2d5` / 38,954 | MCP는 별도 `NOTION_MCP_TOKEN`을 요구하며 worker token fallback을 금지한다. `test_registry_exact_read_only_schema_and_no_caller_override` 및 `test_mcp_credentials_never_fall_back_to_worker` 경계를 보존. 변경 대상 아님. |
| 동결 contract | `contracts/study-behavior.md` — `987d09ec152f91e368e070c5ccbe961602a18da8b6113afd968ac965406aae1a` / 5,533; `university-learning-system-v1.2-design-frozen.md` — `45499aa642a52d97b59995d4bd525be77b05cd7ea7944f62e7a41938336cd6fa` / 48,653 | SOURCE / USER / AI provenance와 read-only MCP invariants 유지. 변경 대상 아님. |

Modern parent 해석은 `parent.type == "database_id"`와 비어 있지 않은 `parent.database_id`, `database_parent.type == "page_id"`와 비어 있지 않은 `database_parent.page_id`가 함께 있을 때만 허용한다. 선택된 page ID는 기존 `_same_provider_id`로 configured parent와 정확 비교한다. legacy direct `parent.page_id` 및 기존 내부 `_parent_page_id` 입력은 유지한다. shape가 불완전하거나 여러 경로가 서로 다르면 거부한다. 별도의 API version upgrade, database schema 수정, 추가 provider 요청은 제안하지 않는다.

Modern group array는 각 원소의 고유한 non-empty `name` 및 `option_ids`를 해당 status property's `options[].id → options[].name`과 연결한다. 참조되지 않는 ID, 중복 ID/name, malformed item, 빠진/추가 group 또는 option은 거부한다. 그 결과는 기존 `STATUS_GROUPS`와 현재 option-name exact-set 비교에 그대로 입력한다. Legacy mapping의 비교 동작은 유지하며 `legacy5` schema와 Notion data를 재작성하지 않는다.

## 계획된 meaningful 회귀와 합격 조건

1. **기존 기준:** 현재 legacy fixture의 direct `parent.page_id` + mapping groups가 기존대로 five sources/76 properties `VERIFIED`이고, wrong parent는 `NOT_VERIFIED`다.
2. **현행 SDK positive:** fake-only fixture에서 five data sources 모두 `parent.database_id` + `database_parent.page_id` 및 status groups `{id, name, color, option_ids}` array를 사용한다. option IDs를 같은 property's options로 해석해 정확히 같은 five/76 `VERIFIED`를 반환한다. 호출은 `data_sources.retrieve`뿐이며 create/update 호출은 0회다.
3. **Fail-closed parent negative:** missing/empty database ID, missing page ID, wrong configured parent, conflicting direct/modern parent identifiers 중 하나라도 있으면 `NOT_VERIFIED`; 기존 source ID/property validation을 우회하지 않는다.
4. **Fail-closed group negative:** 알 수 없는/중복 option ID, 중복·누락·추가 group, 잘못된 element/container는 `NOT_VERIFIED`; 실제 schema mismatch가 Ready/Verified가 되지 않는다.
5. **보존 경계:** legacy5 property names/options/relations와 SOURCE·USER·AI ownership 분류가 그대로다. parser는 status definitions만 읽고 Notion 값을 수정하거나 USER 제출/승인을 만들어내지 않는다. MCP의 read-only 도구와 worker/MCP credential 분리는 변경하지 않는다.

기존 baseline은 현재 shape에서 parent 단계의 첫 실패와 detached projection 뒤 groups 단계의 다음 실패를 이미 보여준다. 위 modern positive/negative는 이후 승인된 구현의 fake-only before/after acceptance다. 이 PLAN 작업에서는 pytest, Ruff, mypy, doctor, SDK 또는 실제 provider를 실행하지 않았다. 이후 구현 시 우선 `./.venv/bin/pytest tests/integration/test_intake_worker_preview.py::test_notion_readiness_requires_exact_current_parent_schema_and_relations` 및 추가한 modern/negative cases를 실행하고, 관련 Ruff/mypy를 확인한다. 이 결과만으로 실제 Notion live intake·USER submission·Drive 저장·MCP retrieval 통과를 선언하지 않는다. 실제 read-only MCP 경로는 `NOTION_MCP_TOKEN`이 별도로 준비되고 worker와 분리된 뒤 별도 수락한다. worker는 현재 disabled이며 이번 PLAN은 활성화나 provider 접속을 허용하지 않는다.

## Evidence binding

| 기록 | 현재 SHA-256 / bytes |
|---|---|
| `.insane-review/gui23-pr13-fixes-20261004/school-sdk-shape-diagnosis-20261005.json` | `61fbb321b81b67af4c7dea2c3c9cd57d4fa91a4e4f37b3a7f0d14509d9802bc7` / 9,416 |
| `.insane-review/gui23-pr13-fixes-20261004/school-live-preflight-v2-20261005.json` | `e9627c1c89d780aa144b33975f772557dd6964f907ff4d214189f5da308c4291` / 6,706 |
| `.insane-review/gui23-pr13-fixes-20261004/school-live-flow-disposition-20261005.json` | `f2a7e3734e6d3622d91eeaccaf987f3995368f25c1eca9aa9ef8637f54b07f3a` / 4,778 |
| `docs/plans/school-live-flow-verification-20261005.md` | `c0f10083f87a9b85bcbe09890389e5af1cad6117e760a2e924917b940130ac40` / 12,515 |

현재 source/test SHA는 이 문서의 PLAN-only baseline이다. 구현 승인/리뷰, live readiness 또는 전체 흐름 통과 주장이 아니다.
