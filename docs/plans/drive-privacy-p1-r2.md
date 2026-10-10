# Drive 개인정보 보강 P1 계획 R2

상태: PLAN-only. 구현·테스트 변경과 실행은 이 계획의 독립 검토 및 별도 GO 뒤에 한다. 이 문서는 단독 수락 기준이며 `drive-privacy-p1.md`는 변경하지 않는다.

## 1. 목표와 고정 결정

Drive metadata의 잘못된 `driveId`가 개인 Drive로 오인되는 경로, 파생 파일 create/reuse의 불완전한 검증, Intake 처리 전후의 stale layout·오류 투영·재큐잉 경로를 닫는다. 적용 범위는 기존 3개 제품 파일과 지정된 4개 테스트 파일이다. 테스트는 fake provider/SDK/Notion 및 임시 상태 저장소만 쓴다.

- `driveId` key 부재만 개인 Drive의 `None`으로 허용한다. 명시적 null과 비문자열은 안전 오류로 거부한다.
- 파생 파일 생성·재사용은 exact identity, 위치, provenance marker, bytes, USER 소유와 private permission/capability readback을 통과해야 한다. ACL 변경, 자동 삭제·재생성, 원본 이동은 하지 않는다.
- create dispatch 이후 결과가 불확실하면 자동 재시도·재큐잉하지 않는다. 기존 시도 증거를 보존하고 NEEDS_REVIEW/reconciliation으로 멈춘다.
- Intake layout 검증은 호출별로 fresh하다. 실패한 호출은 Notion request 조회나 오류·상태 projection으로 진행하지 않는다.
- `src/uls/intake/registry.py`와 `src/uls/orchestration/runner.py`는 수정하지 않는다. OAuth identity/account binding, P2 범위는 제외한다.

## 2. 불변식

- I1. raw Google `driveId`는 `DriveMetadata` 생성 전에 key 부재와 값의 타입을 구분한다.
- I2. 생성 또는 marker 재사용의 기대 source/entity/parent/marker/content는 호출 입력에서 고정하며 provider 응답으로부터 기대값을 만들지 않는다.
- I3. create 응답은 새롭고 유효한 file ID를 제공해야 하며, 그 ID의 fresh metadata와 exact bytes를 확인한 뒤에만 staged reference가 유효하다.
- I4. publication 직전 같은 ID의 metadata와 bytes를 다시 검증한다. 불일치는 pointer·Ready 전에 fail-closed다.
- I5. provider create가 dispatch된 뒤 불확실한 Native 결과는 `ProviderUnavailableError`가 아닌 permanent 오류, Intake 결과는 `IntakeReconcileRequired`로 끝나며 자동 retry/requeue는 0회다.
- I6. Intake의 처리 entry마다 등록 layout을 해당 호출에서 검증한다. 이전 tick, 앞선 public 호출, 인스턴스 필드의 성공값을 재사용하지 않는다.
- I7. 검증 컨텍스트는 호출 단위이고 예외를 받는 caller까지 전달된다. 검증 실패 시 `_record_item_error()`와 `_record_request_error()`의 Notion projection은 모두 건너뛴다.
- I8. Registry는 fail-fast 순서와 ID 중복 read cache를 유지한다. 정상 검증은 모든 고유 ID를 한 번 읽고, 실패는 실패 지점까지 읽은 prefix만 방문한다.
- I9. 모호한 상태에서 ACL 수정, 자동 delete/recreate, 원본 이동, pointer publication 또는 Ready 승격은 0회다.

## 3. 파일별 변경 계약

| 파일 | 계약 |
|---|---|
| `src/uls/adapters/drive/worker.py` | `_metadata()`가 raw mapping에서 `driveId` key 존재를 먼저 판별한다. absent는 `None`; present 값의 explicit null, bool, number, list, object는 `SourceUnavailableError` 계열의 고정 오류로 dataclass 생성 전에 거부한다. 문자열은 보존해 기존 private/shared-drive guard가 판단하게 한다. 직접 Google adapter parser 경로를 사용한다. |
| `src/uls/worker.py` | `DerivedDriveWriter`는 create 전에 기대 tuple을 writer-local 불변 상태로 결속한다: source file ID, entity ID, configured parent, 예상 `appProperties` marker(`uls_entity`/`uls_source`), MIME, content digest/bytes. create 응답에서 유효한 새 ID를 확인한 뒤 같은 ID를 fresh-read한다. metadata 조건은 exact file ID·parent·`text/markdown`·marker·`trashed is False`·`owned_by_me is True`·개인 Drive·완전한 owner-only permission·broad-share 없음·필요 capability이며 bytes도 exact match해야 한다. provider가 돌려준 marker는 기대값으로 사용하지 않는다. `validate_derived()`와 `publish_staged_derived()`는 writer-local tuple 및 인자로 받은 source/entity/content가 일치하는지 확인하고, Notion pointer를 바꾸기 직전에 동일 ID metadata/bytes를 다시 읽는다. |
| `src/uls/worker.py` | create dispatch가 시작된 뒤 응답 유실·불명확한 결과 및 metadata/content readback 실패는 `DriveCreateOutcomeUnknownError` 같은 비재시도 오류로 변환한다. 이 오류는 `ProviderUnavailableError`를 상속하지 않아 `WorkerRunner`의 기존 PERMANENT 경로를 탄다. dispatch 전 folder/source 검증·조회 실패는 기존 오류 타입을 유지한다. `WorkerRunner` 코드와 retry 정책은 수정하지 않는다. |
| `src/uls/intake/worker.py` | `_stage_derivative()`의 search 결과는 locator로만 취급한다. 검색 결과를 얻은 뒤에도 동일 file ID의 fresh `read_metadata()`와 bytes read를 수행한다. 재사용 및 신규 create에 같은 postcondition을 적용한다: exact ID, MIME, parent, 입력에서 계산한 marker, untrashed, USER-owned, personal Drive, complete owner-only permission, broad-share 없음, 필요한 capability, exact bytes. `_check_private_drive_metadata()`만으로 대체하지 않는다. 불일치 또는 create 이후 readback 불확실성은 기존 시도의 `UNKNOWN`/`DISPATCHED`, target ID와 dispatch evidence를 보존하고 `IntakeReconcileRequired`로 올려 job을 NEEDS_REVIEW로 끝낸다. 이 오류를 transient로 바꾸거나 재큐잉하지 않는다. create dispatch 전 검증·조회 실패는 이 변환 대상이 아니다. |
| `src/uls/intake/worker.py` | `run_once(sync=False, process=True)`는 local state lock 안에서 `_submitted_request_keys()`보다 먼저 처리 scope의 semester workspace를 검증한다. 범위는 설정된 `self.workspaces`와 로컬 pending job/nonterminal receipt가 가리키는 workspace의 합집합으로 결정하고, Notion 조회로 범위를 추론하지 않는다. 알 수 없는/현재 미설정 semester 또는 검증 실패 시 request list/read, claim, `_record_request_error()` 및 projection으로 진행하지 않고 조기 반환한다. 이 반환 경로에서 Notion 검증을 수행하는 `readiness()`도 호출하지 않는다. |
| `src/uls/intake/worker.py` | `_process_item_unlocked()`는 local item/plan/receipt/workspace를 확인한 뒤 Notion request page를 읽기 전에 해당 workspace의 layout을 매번 fresh 검증한다. direct `process_item()`도 같은 경계를 통과한다. run-level request scan과 각 item/job은 별도 호출별 context(`layout_verified=False`로 시작)를 쓴다. job context는 `_process_item_unlocked()`에서 exception handler까지 전달하고, 실패 시 `_record_job_error()`는 local job 전이만 처리하며 `_record_item_error()` 및 `_record_request_error()`의 Notion 투영을 모두 억제한다. request claim 오류의 `_record_request_error()`는 선행 run-level 검증 성공 context가 있을 때만 가능하다. `_sync_unlocked()`의 기존 검증과 정상 projection 동작은 유지한다. |
| `tests/unit/test_c2_drive_marker_recovery.py` | 실제 `GoogleDriveWorkerAdapter.read_metadata()` raw-response parsing 회귀를 추가한다. typed `DriveMetadata`를 직접 주입해 parser를 우회하지 않는다. |
| `tests/integration/test_native_runtime.py` | `NativeWorker`/`WorkerRunner`와 fake Google/Notion을 사용한다. folder metadata와 generated-file metadata store를 분리하고 create response와 모든 후속 GET이 각기 그 경계를 통과하도록 한다. response metadata를 readback으로 간주하지 않는다. |
| `tests/unit/test_intake_registry.py` | 기존 registry 계약의 조회 횟수와 fail-fast 순서만 보강한다. `registry.py`는 수정하지 않는다. |
| `tests/integration/test_intake_worker_preview.py` | 실제 submitted-request 목록 경로, per-call error gate, derivative reuse/create 회귀를 추가한다. 응답 유실 hook은 entity folder create가 아니라 derivative file create에 건다. |

## 4. 수락 테스트

각 효과 수는 테스트 호출에서 발생한 provider/Notion 효과다. 로컬 fixture 준비와 별도 fresh validation read 수는 해당 행에서 구분한다.

| 시나리오 / 테스트 | 기대 결과 |
|---|---|
| Raw `driveId` absent, null, bool/number/list/object, 문자열 — `test_c2_drive_marker_recovery.py` | absent만 `drive_id=None`; 명시 null·비문자열은 dataclass 생성 전 안전 거부, mutation 0; 문자열은 보존되고 기존 shared-drive guard가 거부. 실제 adapter parser 사용. |
| Native 성공 — `test_native_runtime.py` | folder preflight 1회; generated file create 1회; create 응답 ID 확인 후 즉시 metadata/content fresh read, publication 직전 metadata/content 재검증. 두 시점에서 exact tuple/private 조건 통과 후 기존 Notion pointer 1회. ACL/delete/source move 0. |
| Native private folder 또는 create 응답/metadata/content/publication 전 검증 실패 — 같은 테스트 | 실패 지점별 create dispatch를 먼저 계수한다. preflight 실패는 create 0; dispatch 이후 불명확 결과는 create 총 1회, 재시도/requeue 0, pointer/Ready/ACL/source move/delete 0. 다음 runner tick도 추가 create/pointer 0. |
| Intake create 응답 유실 및 immediate metadata/content readback 실패 — `test_intake_worker_preview.py` | 오류를 derivative file create에만 주입한다. derivative create 정확히 1회, attempt의 UNKNOWN/DISPATCHED evidence 유지, job NEEDS_REVIEW, transient requeue 0. 다음 tick에서 추가 derivative create·pointer·Ready 0. dispatch 전 실패는 기존 분류를 유지한다. |
| 기존 marker reuse — 같은 테스트 | 유효한 `search_marker()` 결과를 먼저 얻은 뒤 backing row만 바꾸고, 같은 locator ID의 fresh `read_metadata()`가 marker 변경 또는 `trashed=True`를 관찰하게 한다. 해당 ID 재사용은 거부되고 derivative create·pointer·Ready·delete/recreate 0. MIME/parent/identity/ownership/private permission/capability/bytes drift도 같은 postcondition으로 거부한다. |
| Registry 정상·실패 read 수 — `test_intake_registry.py` | 단일 course, optional target 없음: 정상 layout은 고유 ID 6개 각각 1회; root privacy/readback 실패는 root 1회; course 실패는 root→semester→upload→course까지 4회. 실패 전 방문 ID는 1회, 뒤의 ID는 0회. 다음 validation 호출은 이전 호출 cache 없이 다시 읽는다. |
| run-level barrier, Claimed receipt + PENDING job 및 미claim submitted request — `test_intake_worker_preview.py` | `run_once(sync=False, process=True)` 전 drift 시 실제 `_submitted_request_keys()`/request-list 경로를 유지한 관측에서 barrier가 그 경로보다 먼저 실패한다. request list/read, claim, projection create/update는 0. 기존 Claimed receipt와 PENDING job도 미변경. `readiness()`의 Notion 검증 호출도 0. |
| item-level barrier direct/runner 호출 및 이전 성공 후 다음 호출 실패 — 같은 테스트 | `process_item()` 및 각 PENDING job이 별도 fresh context로 검증한다. 실패 context를 exception caller에 전달해 `_record_item_error()`/`_record_request_error()`를 모두 억제한다. 앞선 성공은 뒤 호출을 승인하지 않는다. 검증 실패 뒤 request page read 및 projection create/update 0; 다음 호출은 재검증한다. |

## 5. 승인 17핀 영향

프로젝트 checker `scripts/review_evidence_checker_candidate/state_check.py`의 현재 compiled `PINNED_SOURCE_PINS` 17개 경로와 아래 7개 변경 후보를 경로 단위로 대조했다. 교집합은 0이다. checker, pin, inventory는 이 계획의 변경 대상이 아니다.

| 변경 후보 | 17-pin 대상 |
|---|---|
| `src/uls/adapters/drive/worker.py` | 아니오 |
| `src/uls/worker.py` | 아니오 |
| `src/uls/intake/worker.py` | 아니오 |
| `tests/unit/test_c2_drive_marker_recovery.py` | 아니오 |
| `tests/integration/test_native_runtime.py` | 아니오 |
| `tests/unit/test_intake_registry.py` | 아니오 |
| `tests/integration/test_intake_worker_preview.py` | 아니오 |

## 6. 범위 밖

`src/uls/intake/registry.py`, `src/uls/orchestration/runner.py`, journal/receipt schema, OAuth identity/account binding, service-account 허용 정책, ACL 조정, 자동 삭제·재생성, source 이동, Notion/Canvas UI, 실제 Google/School 데이터·provider 접근은 제외한다. 구현 단계에서도 이 문서의 7개 경로를 넘어야 하는 발견이 있으면 먼저 범위 재검토를 요청한다.
