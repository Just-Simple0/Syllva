# Drive 개인정보 보강 P1 계획

상태: PLAN-only. 구현 전 독립 Native 계획 검토가 필요하다. 이 계획은 OAuth 기능과 무관한 현재 Drive/Intake 경계 보강만 다룬다.

## 1. 목표와 고정 결정

현재 Drive API 응답의 `driveId` 형식 오류가 개인 Drive로 오인될 수 있고, 생성·재사용 파생 파일과 처리 대상 폴더의 개인정보 상태를 매번 충분히 확인하지 않는다. P1은 이 네 경계를 기존 adapter·registry·단일 worker 흐름에 맞춰 닫는다.

- raw `driveId`가 없으면 개인 Drive로 해석해 `drive_id=None`을 허용한다. key가 있으면서 값이 명시적 null 또는 비문자열이면 고정된 안전 오류로 거부한다. 문자열 ID는 보존하고 기존 개인 Drive 검사에서 거부한다.
- 파생 파일은 USER 소유의 개인 Drive에만 두고 `owner_only`, 전체 permission readback, 비공개 공유, 필요한 capability를 확인한다. ACL을 자동 수정하거나 권한을 완화하지 않는다.
- provider create가 dispatch된 뒤 결과가 불명확하면 자동 재시도하지 않는다. 성공을 가장하거나 파생 파일을 자동 삭제하지 않는다.
- 처리 호출마다 등록된 semester Drive layout을 새로 검증한다. 이번 호출의 검증 전·실패 단계의 오류는 Notion에 투영하지 않는다.
- P1에는 OAuth 계정 식별값 결속과 OAuth identity gate를 넣지 않는다. 해당 범위는 P2다. 기존 service-account 허용 동작과 Notion USER/Partial 정책은 유지한다.

## 2. 불변식

- I1. provider raw mapping의 `driveId` 검사는 `DriveMetadata` 생성 전에 끝난다.
- I2. absent `driveId`만 개인 Drive 기본값이며, explicit null·bool·number·array·object는 거부한다.
- I3. create·기존 marker 재사용은 같은 file ID의 fresh metadata와 exact content가 모두 검증될 때만 유효하다.
- I4. publication 직전에도 parent, MIME, marker, trashed, ownership, 공유, capability 및 bytes를 다시 확인한다.
- I5. create 결과 또는 후속 readback이 불명확하면 해당 create dispatch는 최대 1회이며 retry·requeue는 0회다.
- I6. 오류·불확실성 때 ACL 변경, 자동 delete/recreate, 원본 이동, Notion pointer 또는 Ready 승격은 0회다.
- I7. Intake 처리의 layout 검증은 호출별·lock 안에서 fresh이며 이전 sync/tick 결과로 대체하지 않는다.
- I8. 호출별 layout 검증이 성공하기 전 발생한 processing 오류는 local-only로 기록한다.

## 3. 파일별 변경 계약

| 파일 | 계획 계약 |
|---|---|
| `src/uls/adapters/drive/worker.py` | `_metadata()`에서 raw mapping의 key 존재를 구분한다. `driveId` absent는 `None`; explicit null과 모든 비문자열은 dataclass 생성 전에 `SourceUnavailableError` 계열의 고정 오류로 거부한다. string ID는 그대로 보존해 기존 `require_private_ownership()`가 shared-drive로 차단한다. |
| `src/uls/worker.py` | `DerivedDriveWriter`는 folder를 매번 fresh-read하고 기존 private ownership 검사를 유지한다. raw `files.create(fields='id')` 대신 adapter의 full-metadata create 응답을 검증한다. 반환 ID가 canonical source ID와 다르고 유효해야 한다. 동일 ID의 fresh metadata와 content를 즉시 읽어 exact parent tuple, `text/markdown`, exact `uls_entity`/`uls_source`, `trashed is False`, `owned_by_me is True`, `drive_id is None`, 완전한 owner-only permission, broad-share false, `can_edit=True`, `can_move=True`, exact bytes를 확인한다. `publish_staged_derived()` 직전 동일 조건을 새 metadata/content readback으로 재확인한 뒤 같은 file ID만 반환한다. |
| `src/uls/worker.py` | create dispatch 이후 adapter의 transport failure는 `DriveCreateOutcomeUnknownError`처럼 `ProviderUnavailableError`를 상속하지 않는 안전한 permanent 분류로 변환한다. `WorkerRunner`의 기존 분류를 이용하며 runner 자체는 수정하지 않는다. response/readback 검증 실패도 pointer/Ready 전에 끝낸다. 사전 로컬 검증 실패는 기존 오류 의미를 유지한다. |
| `src/uls/intake/worker.py` | `_stage_derivative()`의 `search_marker()` 결과는 locator로만 사용한다. 기존 ID에 `read_metadata()`를 다시 호출하고 새 create와 같은 parent/MIME/marker/trashed/USER ownership/personal Drive/complete owner-only permission/no broad-share/capability 검사를 한 뒤 같은 ID의 bytes를 비교한다. `can_move`는 이후 허용된 move가 요구되는 경우에 확인한다. 성공 create와 기존 match는 같은 postcondition을 통과해야 한다. 불일치 시 재생성·ACL 수정·delete·source move 없이 중단한다. |
| `src/uls/intake/worker.py` | `_process_item_unlocked()`에서 local item과 semester workspace를 resolve한 직후, plan/receipt 검증 및 Notion request page read보다 먼저 `validate_registered_drive_layout(self.drive, semester_workspaces)`를 매 호출 수행한다. 기존 single-worker lock을 유지한다. 호출별 `layout_verified=False`를 두고 검증 성공 뒤에만 true로 한다. `run_once()`의 `_record_job_error()` 경로는 이 값이 false인 오류를 local-only 처리하여 `_record_item_error()`/`_record_request_error()` 등 Notion projection을 건너뛴다. workspace resolve·layout readback 실패도 false 상태다. |
| 지정 테스트 4개 | 아래 수락 표의 raw parser, 단계별 create/readback, registry privacy, intake reuse/layout barrier를 fake SDK/provider/store로 검증한다. 새 전역 제어·실제 provider·credential은 추가하지 않는다. |

`src/uls/intake/registry.py`의 `validate_registered_drive_layout()`와 `src/uls/orchestration/runner.py`는 기존 동작을 재사용하며 수정하지 않는다. Registry 검사에는 모든 semester의 root/semester/upload/course/recordings/materials 및 설정된 optional target의 fresh metadata가 포함된다.

## 4. 수락 테스트

모든 테스트는 synthetic raw mapping, fake SDK/provider, 임시 local store만 사용한다. 기대 수는 시험 호출에서 새로 발생한 effect다. 사전 fixture 구성의 호출은 별도 계수한다.

| 시나리오 / 테스트 파일 | 기대 효과 수 |
|---|---|
| Raw `driveId`: absent, explicit null, bool/number/list/object, valid string ID — `tests/unit/test_c2_drive_marker_recovery.py`의 실제 `GoogleDriveWorkerAdapter.read_metadata()` 경로 | absent: metadata 1회, `drive_id=None`; null·비문자열: 안전 거부, 객체 생성/Drive mutation 0; string: ID 보존 후 private guard 거부; create/move 0 |
| Native 정상 파생 파일 — `tests/integration/test_native_runtime.py` | create 1회, 같은 ID metadata fresh read 2회(즉시·publication 직전), content read 2회, move/ACL/delete 0, pointer publication 1회 및 기존 Ready 동작 1회 |
| destination folder preflight privacy 실패 — `tests/integration/test_native_runtime.py` | folder metadata read 1회; create, pointer, Ready, ACL 변경, source move, delete 모두 0 |
| create response loss 또는 불명확한 post-dispatch 오류 — `tests/integration/test_native_runtime.py`에서 fake NativeWorker/WorkerRunner 실행 후 다음 tick 포함 | create dispatch 정확히 1회, 자동 retry/requeue 0회, pointer/Ready/ACL 변경/source move/delete 0회 |
| create response, immediate metadata/content, publication 직전 metadata/content 단계별 fault — `tests/integration/test_native_runtime.py`; folder와 generated-file metadata 저장소 분리 | 도달한 post-create 단계마다 create 1회 먼저 확인; pointer/Ready/추가 create/move/ACL/delete 0회. 기존 preflight 거부는 create 0회 유지 |
| 기존 marker match의 owner_only=false, broad share, permission/capability 누락·불가, MIME/parent/marker/trashed/ownership/shared-drive/bytes drift — `tests/integration/test_intake_worker_preview.py` | 재사용 create 0회; 해당 derivative pointer/Ready 0회; delete/recreate/ACL/source move 0회 |
| 등록 target의 owner_only=false, anyone/domain, permission readback 누락, can_edit/can_move false 또는 누락 — `tests/unit/test_intake_registry.py` | 각 fixture에서 모든 고유 등록 folder ID를 fresh-read 1회; validation 거부; mutation 0회 |
| 사전 claim된 처리 job에서 layout drift/readback failure — `tests/integration/test_intake_worker_preview.py`, `run_once(sync=False, process=True)` 및 `process_item()` | 호출마다 layout 검증 재실행; 실패 전후 processing Drive create/move/delete와 Notion request read/error projection/create/update 0회. 이전 tick의 성공 결과 재사용 0회 |
| workspace resolve 또는 fresh layout 전에 생긴 processing 오류 — `tests/integration/test_intake_worker_preview.py` | per-call barrier false; local job 실패 기록만 허용; Notion error projection/create/update 0회 |
| 정상 layout 대조군 — `tests/unit/test_intake_registry.py`, `tests/integration/test_intake_worker_preview.py` | 기존 처리 흐름과 USER receipt 유지; 매 processing 호출에서 새 layout readback; privacy 검사 외 부작용 추가 0회 |

관련 집중 명령은 `./.venv/bin/pytest -q tests/unit/test_c2_drive_marker_recovery.py tests/integration/test_native_runtime.py tests/unit/test_intake_registry.py tests/integration/test_intake_worker_preview.py`다. 이 계획 단계에서는 실행하지 않는다.

## 5. 승인된 17핀 영향

현재 project checker의 compiled literal인 `PINNED_SOURCE_PINS`를 기준으로 P1 후보 7개 경로와 직접 대조했다. Checker는 `scripts/review_evidence_checker_candidate/state_check.py` SHA-256 `a82d0a88a2a35fe53293c433210029895a0e674d15231a2d9e1ed1a8f378ef08` (27,911 bytes)이며 compiled source map은 17개다. 이 7개 후보의 compiled pin 경로 교집합은 0이다.

| P1 후보 경로 | compiled 17-pin |
|---|---|
| `src/uls/adapters/drive/worker.py` | 대상 아님 |
| `src/uls/worker.py` | 대상 아님 |
| `src/uls/intake/worker.py` | 대상 아님 |
| `tests/unit/test_c2_drive_marker_recovery.py` | 대상 아님 |
| `tests/integration/test_native_runtime.py` | 대상 아님 |
| `tests/unit/test_intake_registry.py` | 대상 아님 |
| `tests/integration/test_intake_worker_preview.py` | 대상 아님 |

비교는 `state_check.py`의 현재 compiled map에 대한 경로 일치 확인이며 `source_pins.json`을 권위로 사용하지 않았다. Checker·pin·inventory는 변경하지 않는다. 구현 시작 시 compiled map이 달라졌다면 다시 대조한다.

## 6. 범위 밖

OAuth 연결·배포·token 처리, OAuth account/permission ID 결속과 identity gate(P2), service-account 허용목록 변경, `src/uls/intake/registry.py` 구현 변경, `src/uls/orchestration/runner.py` 변경, journal/receipt schema 변경, Notion API·UI·Canvas 변경, ACL 생성/삭제/완화, Drive source 이동, 자동 cleanup/recreate, 실제 Google/School 연결은 범위 밖이다. 제품·테스트·핀·전역·`.env` 파일 수정 및 테스트 실행은 하지 않는다.
