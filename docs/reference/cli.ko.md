# CLI 레퍼런스

[English](cli.md) · [레퍼런스](README.ko.md)

`uls` console entry point는 패키지가 정의합니다. 대부분의 명령은 저장소/config 기본값 또는 명시적 `--config` 경로를 사용할 수 있습니다.

## 설정과 진단

```text
syllva init
syllva doctor
syllva status
syllva behavior lint
```

- `init` — 없는 로컬 config/state scaffold 생성.
- `doctor` — 설정을 검증하고 readiness 문제 보고; live probe는 별도 명시적 동작.
- `status` — 외부 bridge가 실행 중이라고 주장하지 않고 로컬 durable status/readiness 보고.
- `behavior lint` — 공통 contract 대비 client behavior projection 검증.

## Worker

```text
syllva sync [--max-jobs N]
syllva process [--max-jobs N]
syllva run [--max-jobs N]
syllva jobs [--limit N]
syllva retry <job-id>
syllva reprocess <entity-id>
```

- `sync` — full queue 처리 없이 work 발견/register/project.
- `process` — 이미 발견된 durable work 처리.
- `run` — discovery + processing을 한 번 수행 후 종료.
- `jobs` — durable job 확인.
- `retry` — 진단 후 known failed/retryable job 재시도.
- `reprocess` — known entity의 명시적 운영자 재처리.

worker mutation은 local single-active-worker lock을 공유합니다.

## MCP

```text
syllva mcp local
syllva mcp remote
syllva mcp status
```

- `local` — stdio MCP server; stdout은 protocol output.
- `remote` — 설정된 경우 저장소의 remote-development transport/profile 실행.
- `status` — configuration/status report; remote endpoint reachable 증거는 아님.

## 전역 config 경로

저장소 예시는 다음 형태를 자주 사용합니다.

```bash
syllva --config /ABSOLUTE/PATH/config.yaml doctor
syllva --config /ABSOLUTE/PATH/config.yaml mcp local
```

client/scheduler 등록에서는 launch process working directory에 의존하지 않도록 절대 경로를 사용하세요.
