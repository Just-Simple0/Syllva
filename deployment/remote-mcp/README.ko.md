# 인증된 Remote MCP 프로필

[English](README.md)

Syllva는 표준 **MCP OAuth + Google 로그인**, 기존 **OIDC JWT Bearer** resource-server 모드, 개발용 **단기 bearer credential** 프로필을 지원합니다.

일반 원격 클라이언트에는 `mcp_oauth`를 사용합니다. 클라이언트가 MCP URL을 등록하면 Syllva의 OAuth discovery/PKCE 흐름을 사용하고, Google은 설정된 단일 소유자의 로그인 확인에만 사용됩니다. Google token을 MCP token으로 전달하지 않으며 Syllva가 정확한 `/mcp` resource에 결속된 자체 access/refresh token을 발급합니다.

OIDC 모드에서는 신뢰할 수 있는 IdP(Google, GitHub, Auth0 등)가 서명한 표준 OIDC ID Token(또는 RFC 9068 JWT)을 검증하여 인증합니다. IdP의 JWKS 공개키 세트로 서명을 검증하고, 단일 소유자의 `authorized_subject`(또는 `email_verified=true`인 `authorized_email`)와 일치할 때만 인가됩니다. 런타임에 디스크에 정적 장기 시크릿을 저장할 필요가 전혀 없습니다.

## 설정

대표 `remote_mcp` 설정:

```yaml
remote_mcp:
  enabled: true
  auth_mode: mcp_oauth  # "mcp_oauth" | "oidc" | "bearer" | "oauth_or_bearer"
  edge_mode: cloudflare_tunnel  # 기본값은 direct_tls
  public_unauthenticated: false
  public_url: https://uls.example/mcp
  host: 127.0.0.1
  port: 8765
  oauth:
    google_client_id: your-google-web-client.apps.googleusercontent.com
    authorized_email: owner@example.com
```

Google OAuth Web client secret은 `REMOTE_MCP_GOOGLE_CLIENT_SECRET`로 environment 또는 OS keyring에 저장하고 YAML에 넣지 마세요. Google Web redirect URI는 정확히 `<public-origin>/oauth/google/callback`입니다.

`direct_tls`는 기존 동작을 보존하는 기본 edge mode입니다. `cloudflare_tunnel`은 `mcp_oauth`에서만 허용되며 loopback listener가 필요합니다. 이 모드에서 local HTTP는 loopback peer에서 온 경우에만 허용되고 public URL은 계속 HTTPS입니다. forwarded header로 public identity를 복원하지 않습니다. tunnel이 origin `Host`를 바꾸면 Syllva 검사를 완화하지 말고 tunnel 쪽에서 public Host를 전달하도록 설정합니다.

기존 OIDC direct-TLS 프로필도 그대로 사용할 수 있습니다.

## Bearer credential

private secret storage에서 최소 32자의 random URL-safe token을 만들고 이 development profile에서는 `REMOTE_MCP_EXPIRES_AT`을 최대 한 시간 이내의 절대 Unix timestamp로 설정합니다.

실행:

```bash
uls --config /absolute/config.yaml mcp remote
```

token 값은 config file, client instruction, command argument, log, PR/issue 본문에 나타나면 안 됩니다. 만료 후 새 credential로 restart하고 하나의 development bearer를 자동 영구 credential로 연장하지 않습니다.

## OAuth 및 HTTP/TLS 경계

OAuth discovery/authorize/token/register/revoke/Google callback control-plane만 MCP access token 이전에 도달할 수 있습니다. 이 경로는 학술 데이터를 노출하지 않고 Host/Origin/edge 검사를 계속 통과해야 합니다. `/mcp`, `/health`, 11개 retrieval tool은 계속 인증이 필요합니다.

OAuth 상태는 `system.workspace_dir/remote-oauth.sqlite3`에 보호된 권한으로 저장합니다. authorization code와 MCP access/refresh token은 digest만 저장합니다. 소유자, Google client ID, public issuer/resource 또는 고정 scope 정책이 바뀌면 기존 grant는 무효화됩니다.

in-memory capability model을 위해 MCP app은 single process로 유지해야 합니다. state model을 재설계하지 않은 채 multiple worker를 추가하지 마세요.

## 권한

bearer credential은 engine policy 아래 설정된 사용자의 Syllva corpus에 접근할 수 있는 high-value credential입니다. per-course ACL이 아닙니다.

MCP retrieval에는 별도의 read-only Drive/Notion/GitHub credential만 사용하세요. ingestion worker는 MCP에 의해 시작되지 않으며 MCP tool로 노출되지 않습니다.
