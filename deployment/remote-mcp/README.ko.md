# 인증된 원격 개발 프로필

[English](README.md)

내장 remote profile은 production OAuth 배포가 아니라 **development validation**용입니다.

direct TLS 위에서 짧게 살아 있는 bearer credential을 사용합니다. 대상 client가 OAuth/OIDC 또는 다른 gateway를 요구하면 해당 외부 auth layer와 client E2E를 별도로 구성/검증할 때까지 deployment-deferred로 유지해야 합니다.

## 설정

대표 `remote_mcp` 설정:

```yaml
remote_mcp:
  enabled: true
  auth_mode: oauth_or_bearer
  public_unauthenticated: false
  public_url: https://uls.example/mcp
  host: 127.0.0.1
  port: 8765
  tls_certfile: /private/path/fullchain.pem
  tls_keyfile: /private/path/privkey.pem
```

target client가 신뢰하는 certificate와 명시적으로 구성한 network route를 사용하세요. 다른 bind address를 직접 구성하지 않으면 listener는 loopback에 유지됩니다. Syllva가 DNS, firewall rule, tunnel, public endpoint를 자동 생성하지 않습니다.

## Bearer credential

private secret storage에서 최소 32자의 random URL-safe token을 만들고 이 development profile에서는 `REMOTE_MCP_EXPIRES_AT`을 최대 한 시간 이내의 절대 Unix timestamp로 설정합니다.

실행:

```bash
uls --config /absolute/config.yaml mcp remote
```

token 값은 config file, client instruction, command argument, log, PR/issue 본문에 나타나면 안 됩니다. 만료 후 새 credential로 restart하고 하나의 development bearer를 자동 영구 credential로 연장하지 않습니다.

## HTTP/TLS 경계

저장소 profile은 direct TLS와 인증된 endpoint를 전제로 합니다. exact Host/Origin/Authorization 검사가 의도된 경계의 일부입니다. forwarded header는 명시적 trust configuration을 대신하지 않습니다.

TLS termination proxy가 plain HTTP로 전달한다면 trust boundary가 달라지므로 여기서는 이를 검증된 production architecture라고 표현하지 않습니다.

in-memory capability model을 위해 MCP app은 single process로 유지해야 합니다. state model을 재설계하지 않은 채 multiple worker를 추가하지 마세요.

## 권한

bearer credential은 engine policy 아래 설정된 사용자의 Syllva corpus에 접근할 수 있는 high-value credential입니다. per-course ACL이 아닙니다.

MCP retrieval에는 별도의 read-only Drive/Notion/GitHub credential만 사용하세요. ingestion worker는 MCP에 의해 시작되지 않으며 MCP tool로 노출되지 않습니다.
