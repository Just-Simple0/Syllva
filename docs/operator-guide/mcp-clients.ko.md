# MCP 클라이언트

[English](mcp-clients.md) · [운영자 가이드](README.ko.md)

Syllva MCP server는 읽기 전용 학업 검색 경계입니다. AI client에게 worker credential을 주는 방식으로 이 경계를 무너뜨리면 안 됩니다.

## Codex desktop/CLI — local stdio

절대 실행 경로와 config 경로를 사용합니다.

```bash
codex mcp add uls -- /ABSOLUTE/PATH/uls-venv/bin/uls \
  --config /ABSOLUTE/PATH/config.yaml mcp local
```

동등한 TOML:

```toml
[mcp_servers.uls]
command = "/ABSOLUTE/PATH/uls-venv/bin/uls"
args = ["--config", "/ABSOLUTE/PATH/config.yaml", "mcp", "local"]
```

client가 실제로 실행하는 환경에서 executable/config path를 검증하세요.

## Claude — local MCP

대표 client 설정:

```json
{
  "mcpServers": {
    "uls": {
      "command": "/ABSOLUTE/PATH/uls-venv/bin/uls",
      "args": ["--config", "/ABSOLUTE/PATH/config.yaml", "mcp", "local"]
    }
  }
}
```

저장소에는 `clients/claude/skills/` 아래 Claude skill projection도 있습니다. local MCP/skill 지원 여부는 실제 사용하는 Claude client에 따라 달라집니다.

## ChatGPT / 원격 클라이언트

web client가 로컬 checkout이나 local stdio config를 읽을 수 있다고 가정하지 않습니다. 내장 remote profile은 **development bearer profile**이며 OAuth 배포가 아닙니다. client가 OAuth 또는 별도 gateway를 요구하면 외부 auth layer와 client E2E가 검증될 때까지 deployment-deferred 상태를 유지합니다.

저장소의 remote development 경계는 `deployment/remote-mcp/README.ko.md`를 참고하세요.

## 프로세스 분리

[설정](configuration.ko.md)에 문서화된 read-only MCP credential 경계를 사용하세요. 편의를 위해 worker write credential로 MCP server를 실행하지 않습니다.

`uls mcp local`은 stdout을 MCP protocol message용으로 예약합니다. 운영 log가 stdio protocol output을 오염시키지 않아야 합니다.

## 검증

profile을 신뢰하려면 의도한 환경에서 실제 client가 domain-specific retrieval 검사를 완료해야 합니다. SDK/protocol test는 필요하지만 real-client E2E와 같지 않습니다.
