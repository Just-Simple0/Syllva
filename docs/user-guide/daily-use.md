# 매일 사용하기

## 자료를 등록하는 사용자 흐름

v1.3 preview가 설정되고 검증된 경우 사용자는 구현 세부사항보다 다음 순서만 따라
자료를 등록한다.

`학기 업로드 폴더 → FileIntake 확인 → Input Request 입력 → Submitted=true → Request Status 확인`

1. 운영자가 알려준 현재 학기의 업로드 폴더에 PDF, 슬라이드 또는 기타 자료를 넣는다.
2. `FileIntake`에서 파일이 보이고 대상 과목이 맞는지 확인한다.
3. `Input Request`에 과목, 세션/날짜, 자료 종류와 필요한 설명을 적는다.
4. 제출할 때만 `Submitted` 체크박스를 `true`로 바꾼다.
5. 처리 결과는 `Request Status`에서 확인한다.

`Submitted`와 `Cancelled`는 USER 체크박스다. `Request Status`는 `Draft`,
`Submitted`, `Claimed`, `Applied`, `Needs Input`, `Reconcile Required`,
`Cancelled`, `Failed` 중 시스템이 기록하는 값이며 USER 승인 필드가 아니다. 다음
작업을 멈추려면 `Cancelled=true`로 표시한다. 메모나 요약으로 상태를 승인 상태로
바꾸지 않는다.

파일명만으로 과목·세션·날짜를 정하지 않는다. PDF 자료는 설치 시 `pdf` extra가
필요하며, 자동 강의노트 생성이나 원본 전체 archive를 약속하지 않는다.

## 운영자 실행

설정 검증과 provider readback을 마친 운영자는 저장소 루트에서 다음을 실행한다.

```sh
uls status
uls sync --max-jobs 20
uls process --max-jobs 20
uls run --max-jobs 20
uls jobs --limit 20
```

`sync`는 발견·projection, `process`는 이미 발견된 작업 처리, `run`은 두 단계를
수행한다. 반복 실행은 같은 local single-active-worker lock을 공유한다. 실패한
작업은 원인을 확인한 뒤 `uls retry <job-id>`를 사용한다. preview lane은
`semester_registries`와 `semester_workspaces`가 모두 명시된 경우에만 선택되며,
legacy lane으로 자동 전환하지 않는다.

## preview와 legacy reader의 구분

preview registration은 학기별 5개 native data source(Academic Courses, Sessions,
Materials, File Intake, Input Request)를 사용한다. 기존 7개 global-ID data source는
custom ULS MCP reader의 legacy read-only 검색 표면이다. 두 표면은 같은 자료를
자동으로 동기화하거나 서로의 설정을 대신하지 않는다.

기존 `sources.json` transcript 등록은 별도의 명시적 입력 경로다. 현재 구현은
임의 파일을 자동 Material/Session으로 만들거나 Drive 폴더를 계속 감시하지 않는다.

## 상태와 문제 해결

| 상태/증상 | 확인할 것 |
| --- | --- |
| preview가 선택되지 않음 | 해당 학기의 Drive registry와 Notion workspace가 둘 다 있는지 확인 |
| 설정 검증 실패 | 과목 키, 학기, provider ID가 registry/workspace에 정확히 일치하는지 확인 |
| `Needs Input` | 과목, 세션/날짜, 자료 의도를 Input Request에 보완 |
| `Reconcile Required` | receipt와 대상 위치를 운영자가 readback하고 재실행 여부를 결정 |
| `Failed` | 오류 원인과 provider 상태를 확인한 뒤 같은 작업을 재시도 |
| `Cancelled` | USER가 `Cancelled`를 해제하고 다시 제출할지 결정 |
| 이미 실행 중 | 기존 worker가 끝난 뒤 다시 실행; lock을 우회하지 않음 |
| MCP 질문에 쓰기 동작을 요청함 | custom ULS MCP는 read-only이며 upload/Notion 자동작성은 별도 경계 |

자격증명 오류는 문서나 명령 인자에 값을 복사해 해결하지 않는다. 운영자가 provider
설정과 권한을 확인하고, `doctor --live`의 read probe 결과를 별도로 검토한다.

## LMS 조회

검증된 LMS snapshot은 과목별로 별도의 결과를 만든다. 한 과목의 실패를 학기 전체
성공으로 해석하지 않으며, 날짜가 없는 과제는 날짜를 추측하지 않는다. 과목별 과제
목록은 하나의 일정 목록에서 확인하고, 제출 상태나 사용자 완료 상태를 자동으로
바꾸지 않는다.
