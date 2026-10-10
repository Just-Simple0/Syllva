# Drive 개인정보 보강 P1 계획 R3

상태: PLAN-only. 이 문서는 변경 범위와 수락 조건을 고정한다. 제품·테스트 변경 및 지정 테스트 실행은 독립 계획 검토 수락과 별도 GO 뒤에 한다.

## 1. 목표와 고정 결정

Drive metadata의 잘못된 `driveId` 해석, 파생 파일 생성·재사용 검증, Intake의 layout drift 뒤 Notion 접근·오류 투영·재큐잉 경로를 닫는다. 변경 후보는 아래 7개 코드·테스트 파일이다. 검사는 fake provider/Notion과 임시 상태만 사용한다.

- `driveId` key 부재만 개인 Drive의 `None`으로 허용한다. 명시적 null·비문자열은 dataclass 생성 전에 거부한다.
- 파생 파일은 exact identity·parent·marker·bytes·USER 소유·owner-only permission·private capability 검증을 통과한 뒤에만 재사용·공개한다. ACL 변경, 자동 삭제·재생성, 원본 이동은 금지한다.
- create dispatch 뒤 불확실한 결과는 기존 시도 증거를 보존하고 NEEDS_REVIEW/reconciliation으로 끝낸다. 재시도·재큐잉하지 않는다.
- Intake layout 검증은 호출별 fresh context다. 미검증 context에서는 request read/claim, Notion 오류·상태 투영을 하지 않는다. 실패 context를 처리·오류 caller까지 전달한다.
- School ACL, `owner_only` 정책, 전역 설정, checker/pin은 바꾸지 않는다. Registry와 `WorkerRunner`도 변경하지 않는다.

## 2. 불변식

- I1. raw Google `driveId`는 key 부재와 값 형식을 구별한다. 부재만 `None`; present null·비문자열은 안전 오류, 문자열은 기존 shared-drive 검사로 보낸다.
- I2. 파생 파일의 기대 source/entity/parent/marker/content는 입력에서 고정하고 provider 응답에서 만들지 않는다.
- I3. 신규 create는 유효한 새 file ID를 반환해야 한다. 같은 ID의 fresh metadata와 exact bytes 확인 전에는 staged reference가 유효하지 않다.
- I4. publication 직전 동일 ID metadata/bytes를 다시 확인한다. 불일치면 Notion pointer·Ready 전에 중단한다.
- I5. create dispatch 뒤 불확실한 Native 오류는 runner에서 비재시도 permanent, Intake 오류는 `IntakeReconcileRequired`다. 자동 재시도·재큐잉은 0회다.
- I6. run-level request scan, public claim, 각 item/job, direct request creation은 각각 검증된 layout context를 갖는다. 이전 tick·다른 호출·worker 필드의 성공값은 재사용하지 않는다.
- I7. layout 실패 시 request page read, claim, validation-error projection, `_record_item_error()`/`_record_request_error()`의 Notion projection은 0회다. 실패한 job은 필요한 local 상태만 보존한다.
- I8. Registry의 fail-fast 순서와 호출 내 중복 ID read cache를 유지한다. 매 validation 호출은 새 cache로 시작한다.
- I9. 모호하거나 privacy 검증에 실패한 상태에서 ACL 수정, 자동 delete/recreate, source 이동, pointer publication, Ready 승격은 0회다.

## 3. 파일별 변경 계약

| 파일 | 계약 |
|---|---|
| `src/uls/adapters/drive/worker.py` | `_metadata()`가 raw mapping의 `driveId` key 존재를 먼저 검사한다. 부재만 `None`; present null/bool/number/list/object는 고정 `SourceUnavailableError`로 거부한다. 문자열은 보존한다. 실제 Google adapter parser를 통과시킨다. `InMemoryDriveWorker.read_metadata()`의 trashed/missing 동작은 예외를 내도록 유지한다. |
| `src/uls/worker.py` | `DerivedDriveWriter`가 source ID, entity ID, configured parent, marker, MIME, content bytes/digest의 기대 tuple을 writer-local로 결속한다. create 결과의 새 ID를 확인하고 metadata·bytes를 fresh-read한다. 조건은 exact ID/parent, `text/markdown`, marker, untrashed, USER-owned personal Drive, 완전한 owner-only permission, broad-share 없음, 필요한 capability와 exact bytes다. `validate_derived(staged_ref, content)`는 writer-local source/entity/parent/marker와 content를 검사한다. `publish_staged_derived()`는 추가로 전달된 source/entity가 writer-local 값과 같은지 확인하고, pointer 직전 같은 ID를 재검증한다. dispatch 뒤 응답·readback 불확실성은 `ProviderUnavailableError` 하위가 아닌 permanent 오류로 끝낸다. |
| `src/uls/intake/worker.py` | `run_once()`는 local lock 후 처리 대상 semester의 설정 workspace와 local pending job/nonterminal receipt workspace 합집합을 Notion request scan보다 먼저 검증한다. 검증 실패면 scan/claim/error projection/readiness 호출 없이 종료하고 receipt/job을 보존한다. 내부 claim에는 이 run context를 전달한다. `claim_request()` 직접 호출은 lock 뒤 receipt→item→workspace를 local에서 해석하고, 첫 request read 및 Needs Input/Claimed update 전에 그 semester layout을 새 context로 검증한다. 실패면 claim·내부 validation-error projection은 0회다. `_process_item_unlocked()`와 direct `process_item()`도 local item/plan/receipt/workspace 해석 뒤 request read 전에 새 context를 검증한다. `create_input_request()` 직접 호출은 item/workspace 해석 뒤 pending intent/receipt 변경, Notion 검색·읽기·쓰기 전에 fresh 검증한다. 내부 호출은 같은 호출에서 이미 성공한 context만 전달할 수 있다. 오류 context는 `_record_job_error()`, `_record_item_error()`, `_record_request_error()`까지 전달하고 미검증 시 Notion 투영을 억제한다. `_stage_derivative()`의 재사용·생성 postcondition은 exact ID/parent, `text/markdown`, 입력 marker, untrashed, USER-owned personal Drive, 완전한 owner-only permission, broad-share 없음, 필요한 capability와 exact bytes다. 오류 경계는 (a) 후보 선택 전·신규 create 전 일반 preflight/search 실패를 기존 분류로 유지, (b) 기존 marker 후보 선택 뒤 동일 ID metadata/content 확인의 조회 예외와 불일치를 prior 유무 및 create 유무와 무관하게 `IntakeReconcileRequired`로 변환, (c) 신규 create dispatch 뒤 응답/readback 실패도 `IntakeReconcileRequired`로 바꾸고 attempt 증거를 보존한다. 어느 단계에서도 선택 후보 검증 실패를 빈 검색으로 바꾸거나 같은 호출에서 create로 전환하지 않는다. |
| `src/uls/intake/registry.py`, `src/uls/orchestration/runner.py` | 수정하지 않는다. 기존 layout 검증 순서/read cache와 `IntakeReconcileRequired`의 NEEDS_REVIEW·비재시도 분류를 사용한다. |
| `tests/unit/test_c2_drive_marker_recovery.py` | typed metadata 직접 주입 없이 실제 `GoogleDriveWorkerAdapter.read_metadata()` raw parser 경로의 absent/null/비문자열/string 회귀를 추가한다. |
| `tests/integration/test_native_runtime.py` | NativeWorker/WorkerRunner와 fake Google/Notion에서 folder metadata와 generated-file metadata를 분리한다. create 응답·후속 GET을 각각 거쳐 metadata/bytes를 확인하고 pointer 직전 재검증을 계수한다. |
| `tests/unit/test_intake_registry.py` | 기존 단일-course fixture에서 정상 6회, root 실패 1회, course 실패 4회 및 fail-fast prefix만 읽는 계약을 보강한다. |
| `tests/integration/test_intake_worker_preview.py` | 실제 request-list 경로, direct claim/create/process barrier, 호출별 오류 context, derivative create/reuse를 검증한다. 선택 후보 오류 주입은 `InMemoryDriveWorker`/해당 테스트 fake에서 하며 generic `tests/fixtures/fake_drive.py`는 변경하지 않는다. 그 fixture는 retrieval binding용이며 marker metadata/readback port가 아니다. |

`readiness()`와 `intake_ready`는 Notion workspace schema/status 진단만 하는 read-only 조회다. 개별 Input Request/USER 행을 읽거나 claim·투영하지 않으므로 이 P1의 처리·변경 barrier 범위에서 제외한다. `sync()`의 선행 Notion 조회도 schema readiness 확인뿐이며, 개별 request를 읽거나 바꾸지 않는다. 실제 discovery와 projection은 기존 `_sync_unlocked()`의 fresh semester layout 검증 뒤에 둔다.

## 4. 수락 테스트

효과 수는 테스트 호출에서 발생한 provider/Notion 효과다. fixture 준비·독립적인 layout validation read는 별도 계수한다.

| 시나리오 / 테스트 | 기대 결과 |
|---|---|
| raw `driveId` absent/null/bool/number/list/object/string — `test_c2_drive_marker_recovery.py` | absent만 `drive_id=None`; present null·비문자열은 dataclass 전 거부·mutation 0; 문자열은 보존 후 기존 shared-drive guard가 거부한다. 실제 adapter parser 사용. |
| Native 성공 및 실패 — `test_native_runtime.py` | 성공은 folder preflight 1회, generated-file create 1회, create 후 metadata/bytes readback, publication 직전 재검증 후 pointer 1회. privacy·tuple·content/readback 실패는 지점별 create를 계수하고 pointer/Ready/ACL/source move/delete는 0회. dispatch 뒤 불명확 결과는 create 1회, retry/requeue 0회, 다음 runner tick 추가 create/pointer 0회. |
| Intake 신규 derivative post-create 실패 — `test_intake_worker_preview.py` | derivative create 정확히 1회, `UNKNOWN`/`DISPATCHED` evidence 보존, job NEEDS_REVIEW, 재큐잉 0. 다음 tick의 추가 derivative create·pointer·Ready는 0. dispatch 전 실패는 기존 분류 유지. |
| 기존 marker 선택 뒤 검증 불가 — 같은 테스트 | `prior=None`을 포함한다. search 성공 뒤 동일 후보의 `read_metadata()`에 `SourceUnavailableError`, `ProviderUnavailableError`, content read 실패를 각각 주입하고, metadata/content tuple 불일치도 확인한다. 모두 NEEDS_REVIEW, 재큐잉 0; 다음 tick의 추가 derivative create·pointer·Ready 0. trashed fake row는 반환 대신 `SourceUnavailableError`를 낸다. |
| Registry read 수 — `test_intake_registry.py` | 단일 course 정상 6회, root privacy/readback 실패 1회, course 실패 4회. 실패 뒤 ID 0회, 매 호출은 fresh cache. |
| run-level barrier — `test_intake_worker_preview.py` | 유효 receipt와 PENDING job 및 미claim submitted request를 준비한다. drift 시 request-list/read, claim, projection create/update 0; 기존 receipt/job 불변; 실패 경로의 `readiness()` Notion 검사도 0. 실제 `_submitted_request_keys()` 경로에서 순서를 관측한다. |
| direct `claim_request()` — 같은 테스트 | 유효 receipt 후 layout drift를 주입하고 필수 입력 부족 request와 정상 request를 각각 호출한다. 두 경우 모두 request read, validation-error update, claim update 0; receipt/job 불변. direct public lock 경로를 사용한다. run 내부 claim은 run의 검증 context를 전달한다. |
| direct `create_input_request()` — 같은 테스트 | local item/workspace 해석 뒤 drift를 주입한다. pending key/receipt 변경 및 Notion request search/read/create/update 0. 내부 호출은 검증 context를 전달한다. |
| item-level barrier — 같은 테스트 | Claimed receipt와 PENDING job으로 run-level validation 및 request scan을 먼저 통과시킨 다음 item 검증 직전에 backing layout을 바꾼다. 앞선 정상 효과와 분리해 실패 item context 뒤 request-page read 및 `_record_item_error()`/`_record_request_error()` projection을 각각 센다. 모두 0이다. local job 오류는 기존 분류에 따라 기록한다. |

## 5. 승인 17핀 영향

`state_check.py`의 compiled `PINNED_SOURCE_PINS` 17개 경로를 변경 후보 7개와 경로 비교했다. 교집합은 0이다. 계획 문서도 `PINNED_DOCUMENT_PINS` 대상이 아니다. checker, pins, inventory는 변경하지 않는다.

| 변경 후보 | compiled source pin |
|---|---|
| `src/uls/adapters/drive/worker.py` | 아니오 |
| `src/uls/worker.py` | 아니오 |
| `src/uls/intake/worker.py` | 아니오 |
| `tests/unit/test_c2_drive_marker_recovery.py` | 아니오 |
| `tests/integration/test_native_runtime.py` | 아니오 |
| `tests/unit/test_intake_registry.py` | 아니오 |
| `tests/integration/test_intake_worker_preview.py` | 아니오 |

## 6. 범위 밖

`src/uls/intake/registry.py`, `src/uls/orchestration/runner.py`, journal/receipt schema, OAuth identity/account binding, service-account 허용 정책, School ACL/`owner_only`, ACL 조정, 자동 삭제·재생성, source 이동, Notion/Canvas UI, 실제 Google/School 데이터·provider 접근, 전역 설정 및 review-evidence checker/pins는 제외한다. 구현 중 7개 변경 후보를 넘어야 하는 발견은 범위 재검토 대상이다.
