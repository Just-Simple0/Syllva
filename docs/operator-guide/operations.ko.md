# 운영과 복구

[English](operations.md) · [운영자 가이드](README.ko.md)

## 일반 상태 확인

다음부터 시작합니다.

```bash
uls doctor
uls status
uls jobs --limit 20
```

그 다음 의도적으로 bounded work를 실행합니다.

```bash
uls sync --max-jobs 20
uls process --max-jobs 20
uls run --max-jobs 20
```

`uls retry <job-id>`는 실패 원인을 이해한 뒤 사용하세요. `uls reprocess <entity-id>`는 명시적 운영자 동작이며 불확실한 외부 상태를 숨기는 용도로 사용하지 않습니다.

## Single active worker

worker mutation 명령은 local single-active-worker lock을 공유합니다. 두 번째 process가 다른 worker가 활성 상태라고 보고하면 진행을 위해 lock을 우회하지 마세요. 먼저 기존 owner/process를 조사합니다.

이 lock은 local process safety이며 distributed lease가 아닙니다.

## Scheduler

저장소에는 다음 파일이 있습니다.

- `deployment/macos/com.syllva.uls.plist`
- `deployment/windows/uls-task.xml`

절대 경로를 수정하고 scheduler 등록 전 수동 `uls run` 동작을 검증하세요. scheduled process는 interactive shell environment를 자동 상속하지 않을 수 있으므로 credential 공급을 private OS/service mechanism으로 명시적으로 처리해야 합니다.

선택적 LMS schedule은 별도 gate 통과 전 활성화하지 않습니다.

## 백업

다음을 함께 백업합니다.

- `config.yaml` (secret은 내장하지 않음);
- metadata-only `sources.json`;
- 안전한 SQLite backup mechanism으로 만든 SQLite state.

WAL write가 활성화된 상태에서 main SQLite file만 복사하는 것을 완전한 백업으로 가정하지 마세요. credential은 Syllva archive 안이 아니라 OS secret storage에서 별도 백업합니다.

## 복구

1. worker scheduler와 MCP process를 중지합니다.
2. 현재 상태를 별도 recovery copy로 보존합니다.
3. 선택한 config/state를 함께 복원합니다.
4. `uls status`, `uls doctor`를 실행합니다.
5. 제한된 source/context smoke check를 수행합니다.
6. 자동화 활성화 전 복원된 local provenance와 Drive/Notion 상태를 reconcile합니다.

과거 SQLite DB를 복원해도 외부 provider나 human approval이 되돌아가는 것은 아닙니다.

## 외부 결과가 불명확할 때

provider mutation timeout이나 응답 유실이 발생하면 readback으로 확인하기 전까지 결과를 unknown으로 처리합니다. “요청이 오류를 반환했다”를 곧바로 “외부 write가 절대 발생하지 않았다”로 바꾸지 않습니다.
