# Remote MCP OAuth 2.0 / OIDC 인증 전환 설계 (계획서 · rev3)

**작성:** Astra (총괄) · **상태:** rev3 (rev2에 대한 독립 리뷰 2건 모두 반영)
**리뷰 기록 (rev2, 2026-09-18):**
- insane-review (웹 ChatGPT GPT-5.6 Sol 매우 높음): **REVISE** (.insane-review/response_Syllva_20260918_001955_64120_2e0daa.md).
  지적: §4.3 validation이 oauth_or_bearer의 Bearer-only 호환성을 깨뜨림, doctor()에서 OIDC 모드 시 Bearer secret 진단 제외 누락, JWKS trust bootstrap HTTPS/redirect/cache 규칙 명시, 모든 토큰에서 sub 클레임 필수화.
- Gemini 3.8 Flash high (agy --mode plan): **REVISE** (remote_mcp_oauth_oidc_plan_review.md).
  지적: §4.3 유효성 검사의 하위 호환성 파괴 모순(insane-review와 동일 지적), asyncio.Lock의 실행 루프 지연 생성 명시, PyJWT 의존성 정의.

두 리뷰어가 짚어낸 모든 핵심 계약 모순 및 세부 요건을 1:1로 대응하여 rev3에 완전히 반영하고 구현을 진행한다.

**명세 근거:** university-learning-system-v1.2-implementation-spec-frozen.md §28, university-learning-system-v1.2-design-frozen.md §28.1, src/uls/mcp/transports/remote.py, deployment/remote-mcp/README.md

> 이 문서는 **설계 계획서**다.

---

## 0. rev2 → rev3 변경 요약 (리뷰 지적 대응표)

| 분류 | rev2의 결함 | rev3의 수정 내용 | 지적 출처 |
|---|---|---|---|
| **P0 호환성** | §4.3에서 oauth_or_bearer 모드 시 무조건 oidc.issuer를 요구하여 기존 Bearer-only 설정이 검증 실패하는 모순 | **상태 머신 명확화**: oauth_or_bearer에서 oidc.issuer가 설정된 경우에만 OIDC 필수 필드를 검증(불완전 시 fail-closed). oidc.issuer가 비어있으면 OIDC 필드를 요구하지 않고 Bearer 자격증명만으로 유효 통과. | insane-review, Gemini 공통 |
| **P0 시크릿 격리** | doctor()가 OIDC 모드에서도 ALLOWED_SOURCES의 REMOTE_MCP_SECRET을 무조건 diagnose하여 파일 읽기 발생 가능 | doctor()의 diagnose() 호출 시 remote_mcp.enabled and auth_mode == "oidc"이면 **REMOTE 자격증명을 입력 집합에서 제외(0회 읽기 보장)**. | insane-review |
| **P0 보안** | JWKS Discovery 및 jwks_uri trust bootstrap 경계 미비 | 1) jwks_uri 및 Discovery URL **HTTPS-only 강제** (HTTP 리다이렉트 금지).<br>2) Discovery 문서의 issuer와 설정된 issuer의 정확 일치 검증.<br>3) 클라이언트 제공 헤더(jku, x5u) 신뢰 소스 배제.<br>4) 캐시 TTL: IdP 헤더 존중하되 최소 5분, 최대 24시간, 기본 1시간 바운딩. | insane-review |
| **P1 불변식** | authorized_email 사용 시 sub 클레임이 누락된 토큰이 통과될 여지 | **모든 유효 토큰에서 sub 클레임 필수화**: caller_identity 해시 생성을 위해 iss와 sub는 필수 문자열이어야 함(누락 시 401). | insane-review |
| **P1 동시성** | asyncio.Lock 모듈 로드 시점 생성 시 다른 이벤트 루프 바인딩 위험 | JwksKeyManager 내부에서 락을 **실행 중인 이벤트 루프 내에서 지연 생성(Lazy Initialization)**. | Gemini |
| **P1 범위 명시** | 계획서가 "OAuth flow"처럼 기술되어 있으나 실제 범위는 JWT Resource Server임 | 본 계획의 범위를 **JWT OIDC Resource Server (토큰 검증 및 인가)**로 명확히 한정하고 배포 문서 갱신 명시. | insane-review |

---

## 1. 문제 정의 및 목표

### 1.1 현재 구조의 한계
현재 ULS의 Remote MCP 프로필(src/uls/mcp/transports/remote.py)은 개발용 단기 Bearer 인증(BearerCredential)만을 제공한다:
- 운영자가 32~256자의 임의 시크릿 문자열(REMOTE_MCP_SECRET)과 1시간 이내의 미래 Unix 타임스탬프(REMOTE_MCP_EXPIRES_AT)를 수동으로 계산하여 주입해야 한다.
- 최대 유효기간이 1시간으로 제한되어 있어 장기 실행 시 주기적으로 프로세스를 재시작하거나 토큰을 재발급해야 한다.
- 외부 클라이언트 연동 시 공유 정적 토큰 관리가 번거롭다.

### 1.2 목표
1. **정적 장기 시크릿 제거**: REMOTE_MCP_SECRET 및 REMOTE_MCP_EXPIRES_AT 없이도 표준 OIDC(OpenID Connect) IdP(Google, GitHub, Auth0 등)가 발급한 JWT ID Token (또는 RFC 9068 JWT)을 검증하여 인증하는 **OIDC Resource Server** 모드를 도입한다.
2. **단일 사용자 인가 불변식 유지**: ULS v1.2는 개인 학술 시스템이다. IdP가 정상 발급한 토큰이더라도 **지정된 소유자(authorized_subject 또는 authorized_email)가 아니면 즉시 거부(fail-closed)**한다.
3. **하위 호환성 보장**: 기존 개발용 auth_mode: bearer 및 auth_mode: oauth_or_bearer를 보존하여 기존 로컬 개발/테스트 환경을 깨뜨리지 않는다.
4. **기밀성 및 로깅 정책 엄격 준수**: 토큰 원문, 디코딩된 JWT 페이로드, IdP 클라이언트 시크릿은 로그, CLI 인자, 에러 메시지, 예외 체인(__cause__)에 절대 남기지 않는다(from None 강제).

---

## 2. 핵심 불변식 및 보안 요구사항

1. **엔진 검색 표면 읽기 전용 불변식 (Release Invariant)**:
   - Remote MCP는 오직 읽기 전용 검색 도구 11개만을 제공한다.
   - 워커의 쓰기 자격증명(Notion/Drive Worker Token)은 원격 전송 계층에 절대 노출되지 않으며, 원격 어댑터는 읽기 전용 자격증명 스냅샷만을 소비한다.
2. **무인가 공용 엔드포인트 금지 (Public Unauthenticated Endpoint Forbidden)**:
   - remote_mcp.public_unauthenticated는 반드시 False여야 하며, 유효한 토큰 없이는 /health를 포함한 모든 엔드포인트가 401 Unauthorized(WWW-Authenticate: Bearer, Cache-Control: no-store)를 반환한다.
3. **직접 TLS 및 Host/Origin/서버 불변식 (Direct TLS & Single Process Boundary)**:
   - scope['scheme'] == 'https', Host 헤더 정확 일치, Origin 헤더 일치(제공 시) 검증을 유지한다.
   - 단일 프로세스 메모리 상태 모델을 보존하기 위해 workers=1을 강제한다.
   - 프록시 헤더 스푸핑을 차단하기 위해 proxy_headers=False를 강제한다.
   - TLS 종료 프록시가 아닌 직접 cert/key 로딩을 통한 direct TLS 구동을 유지한다.
   - DNS Rebinding 방어(TransportSecuritySettings) 및 access_log=False를 유지한다.
4. **암호학적 알고리즘 제한**:
   - 비대칭 키 서명 알고리즘(RS256, ES256)만 허용한다.
   - none 알고리즘 및 대칭키 HMAC(HS256)은 원천 차단한다.
5. **호출자 신원 및 인가 스코프 결속 (Collision-Resistant Principal)**:
   - 인증 성공 시 caller_identity 컨텍스트 변수에 remote:oidc:<sha256(iss + ":" + sub)> (전체 256비트 해시)를 설정한다.

---

## 3. 제안 아키텍처

### 3.1 인증 모드 (auth_mode) 및 레인 상태 머신

| auth_mode | OIDC 레인 | Bearer 레인 | 동작 규칙 |
|---|---|---|---|
| oauth_or_bearer (기본값) | oidc.issuer 설정 시 VALID 필수 | Bearer 토큰 설정 시 VALID 필수 | 최소 1개 이상의 레인이 VALID해야 함. 설정된 레인은 반드시 유효해야 함(불완전 설정 시 fail-closed). |
| oidc | 반드시 VALID | 비활성화 (검사/소비 0회) | 정적 시크릿 불필요. 오직 OIDC 토큰만 허용. |
| bearer | 비활성화 | 반드시 VALID | 오프라인 / 개발용. 오직 1시간 단기 Bearer만 허용. |

### 3.2 토큰 형식에 따른 사전 레인 결정 (Pre-dispatch Lane Binding)

```python
def _classify_token_lane(token: str) -> str:
    if token.count('.') == 2:
        return "oidc"
    if re.fullmatch(r'[A-Za-z0-9_-]{32,256}', token):
        return "bearer"
    return "invalid"
```
- oidc 레인 토큰은 OIDC 검증 파이프라인으로만 처리되며, 검증 실패 시 **절대 Bearer로 폴백하지 않고 즉시 401 fail-closed**.
- bearer 레인 토큰은 Bearer 자격증명이 주입된 경우에만 검증.

### 3.3 단일 사용자 인가 게이트 (Identity Authorization Policy)

1. **sub 클레임 필수성**: 모든 토큰은 유효한 비어있지 않은 문자열 sub와 iss를 가져야 함.
2. **authorized_subject (기본 방식)**: 토큰의 sub 클레임과 1:1 완전 일치 검증.
3. **authorized_email (이메일 방식)**:
   - 토큰의 email 클레임과 대소문자 무시 일치 검증.
   - **[필수 불변식] 토큰의 email_verified 클레임이 반드시 True여야 함.**
4. 대상 규격: OIDC ID Token 또는 RFC 9068 JWT.

### 3.4 비동기 논블로킹 JWKS 매니저 (JwksKeyManager)

- **HTTPS Trust Bootstrap**:
  - jwks_uri 및 Discovery URL({issuer}/.well-known/openid-configuration)은 반드시 https://만 허용.
  - Discovery 문서의 issuer는 설정된 issuer와 정확히 일치해야 함.
  - HTTP로의 리다이렉트는 즉시 거부.
  - JWT 헤더의 jku, x5u 등 클라이언트 제공 URL은 완전히 무시.
- **비동기 I/O**:
  - Discovery 및 JWKS fetch는 asyncio.to_thread를 통해 스레드 풀에서 실행 (ASGI 루프 정지 방지). 타임아웃 5초.
- **Lazy Single-Flight Lock**:
  - asyncio.Lock을 실행 중인 루프 내에서 지연 생성하여 이벤트 루프 바인딩 충돌 방지.
  - 미등록 kid 수신 시 단 1개의 태스크만 네트워크 조회를 수행하고, 최소 60초 쿨다운 적용.
- **Fail-Closed**:
  - 키가 없거나 캐시가 만료된 상태에서 네트워크 조회 실패 시 즉시 401 반환.

---

## 4. 설정 스키마, 로더 및 유효성 검사

### 4.1 스키마 정의 (src/uls/config/schema.py)

```python
@dataclass
class OidcCfg:
    issuer: str = ""              # 예: "https://accounts.google.com"
    audience: str = ""            # 예: "my-client-id.apps.googleusercontent.com"
    authorized_subject: str = ""   # IdP 고유 sub 식별자
    authorized_email: str = ""     # 소유자 이메일 (email_verified 필수)
    jwks_uri: str = ""            # 선택적 (HTTPS 필수, 생략 시 Discovery)
    leeway_seconds: int = 60       # 시계 오차 허용 (최대 120초)

@dataclass
class RemoteMcpCfg:
    enabled: bool = False
    auth_mode: str = "oauth_or_bearer"  # "oauth_or_bearer" | "oidc" | "bearer"
    public_unauthenticated: bool = False
    public_url: str = ""
    host: str = "127.0.0.1"
    port: int = 8765
    tls_certfile: str = ""
    tls_keyfile: str = ""
    oidc: OidcCfg = field(default_factory=OidcCfg)
```

### 4.2 로더 (src/uls/config/loader.py)

```python
    remote_raw = _section(raw, "remote_mcp")
    remote_values = dict(remote_raw)
    oidc_raw = remote_raw.get("oidc", {})
    if not isinstance(oidc_raw, Mapping):
        raise ValueError("remote_mcp.oidc must be a YAML mapping")
    remote_values["oidc"] = _from_mapping(OidcCfg, oidc_raw)
```

### 4.3 유효성 검사 (src/uls/config/validation.py)

- remote_mcp.auth_mode 허용 집합: {"oidc", "bearer", "oauth_or_bearer"}.
- remote_mcp.enabled == True일 때:
  - auth_mode == "oidc": OIDC 필수 필드 검증 (issuer/audience/subject-or-email/leeway).
  - auth_mode == "bearer": OIDC 검증 건너뜀 (Bearer 모드).
  - auth_mode == "oauth_or_bearer":
    - oidc.issuer가 설정되어 있으면 OIDC 필수 필드 완전성 검증.
    - oidc.issuer가 비어있으면 OIDC 검증을 건너뛰고 기존 Bearer 모드로 정상 통과.
  - oidc.issuer 또는 oidc.jwks_uri가 존재할 경우 반드시 https:// 스킴 검증.

---

## 5. Composition Root 및 uls doctor 연동

### 5.1 mcp remote 진입점
- auth_mode == "oidc": REMOTE 시크릿 resolve 0회, OidcTokenVerifier 전달.
- auth_mode == "bearer": BearerCredential resolve 후 전달.
- auth_mode == "oauth_or_bearer": OIDC 설정 유효 시 OidcTokenVerifier 생성, Bearer 자격증명 존재 시 BearerCredential도 함께 전달.

### 5.2 uls doctor 진단
- auth_mode == "oidc":
  - diagnose() 입력 집합에서 REMOTE_MCP_SECRET과 REMOTE_MCP_EXPIRES_AT를 제외 (시크릿 파일 읽기 0회).
  - OIDC 설정 필수 필드 확인 및 --live 시 IdP Discovery/JWKS 연결성 핑 수행.
- auth_mode == "bearer": 기존 Bearer 자격증명 검증.
- auth_mode == "oauth_or_bearer": OIDC 또는 Bearer 중 설정된 레인의 유효성 검증.

---

## 6. 테스트 계획

1. **단위 테스트 (tests/unit/test_mcp_oidc.py)**:
   - RS256 / ES256 유효 서명 통과 및 caller_identity 256비트 해시 검증.
   - alg: none, alg: HS256 키 혼동 차단.
   - 만료(exp), 유효 전(nbf), 발급자(iss), 대상자(aud) 불일치 차단.
   - sub 누락 토큰 거부 (401).
   - 타인 sub 차단, 미인증 이메일(email_verified: false) 차단.
   - JWKS Discovery, 캐시 만료, Lazy Lock Single-Flight, HTTP 거부 검증.
   - JWT 형식 오류 시 Bearer 다운그레이드 방지 검증.
   - 카나리아 시크릿 누출 0개 검증.
2. **계약 테스트 (tests/contract/test_mcp_runtime.py)**:
   - Mock IdP 연동 create_remote_app 종단간 테스트 (/health, /mcp).
   - OIDC 모드에서 정적 시크릿 부재 시 정상 기동 및 11개 읽기 전용 도구 목록 확인.
   - 직접 TLS 불변식 회귀 검증 (workers=1, proxy_headers=False, Host/Origin, DNS rebinding).
3. **CLI 진단 테스트 (tests/contract/test_doctor_purpose_scoped_diagnostics.py)**:
   - OIDC 모드에서 시크릿 없이 status: ok.
   - Bearer-only oauth_or_bearer 하위 호환 status: ok.
