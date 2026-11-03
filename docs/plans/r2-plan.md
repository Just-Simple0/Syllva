# R2 계획 — UI 서버·Tauri 셸·Human-Proof

근거: 구현 명세서 §2(프로세스·프로토콜), §6(Human-Proof), §10(UI HTTP API), §13.1(로깅), §13.6(패키징), §15 R2; 설계서 §3.1, §5.1, §5.4a, §5.6. 계약: C-04, C-05, C-13(나머지), C-17, C-18, C-19, C-20.

CLAUDE.md "보안 경계 변경" 규칙에 따라 이 계획은 구현 전에 독립 읽기 전용 검토를 받는다.

## 0. 완료 기준 (명세 §15 R2)

- C-04/05/17/19/20/18 통과(Python 쪽은 CI 3 OS, Rust 쪽은 `cargo test` 3 OS).
- 두 OS(macOS, Windows)에서 네이티브 확인 다이얼로그 **수동 시연 기록** — 사람이 해야 하는 단계라 R2 끝에서 사용자에게 요청한다.
- 서명·번들 스파이크(§13.6): 인증서가 필요하면 사용자 결정 사항으로 넘긴다.

## 1. 패키지 배치와 의존 방향

| 패키지 | 계층 | 내용 | import 계약 |
|---|---|---|---|
| `syllva.gate` | Core 내부 | `canonical.py`(RFC 8785 호환 canonical JSON), `summaries.py`(순수 `summarize`), `fingerprints.py`, `nonces.py`(메모리 전용 저장소, 상태 기계, 세션당 PENDING 1·쿨다운), `mac.py`(§6.3.2), `service.py`(`begin` / `view` / `confirm` / `deny`) | 신규 `v2-gate-clean`: `settings`·`cli`·`mcp`·`intake`·`adapters`·`retrieval` 금지. `state.v2.writer`만 쓰기 경로 |
| `syllva.state.v2.writer` | 저장 | `commit_gate(record, confirmed_at)` (§6.6): 한 트랜잭션에서 대상 UPDATE + `human_gate_log` INSERT. 유일하게 human-only 열을 쓰는 경로 | 기존 계약 유지. `GATE_ONLY_TABLES` 쓰기는 이 메서드만 |
| `syllva.app` (신규, 바깥 계층) | 앱 | `stdio.py`(JSONL 프로토콜 루프), `sessions.py`(bootstrap 토큰·창 결속·세션·CSRF), `http.py`(Starlette v2 앱: 보안 헤더, `/api/*`), `logging.py`(§13.1 `log_event` + 정화 필터), `main.py`(`syllva core --stdio`) | 신규 `app-boundary`: Core 계층(`domain` 등)은 `syllva.app`을 import하지 않는다. `syllva.app`은 `settings.security`의 Host/Origin/Sec-Fetch 검사를 재사용 |
| `shell/` (Rust, Tauri 2) | Shell | `src/main.rs`(Core spawn, `shell_secret`, JSONL), `src/gate.rs`(`confirm_gate` 커맨드, 네이티브 다이얼로그, HMAC), `capabilities/main.json` | Cargo 워크스페이스. Python과 MAC 테스트 벡터 공유(`tests/fixtures/gate_mac_vectors.json`) |

`settings/`는 §1.1대로 **출발점**이다. 보안 헤더·Host 검사 로직은 `settings/security.py`에서 재사용하고, v2 앱은 `syllva.app.http`에 새로 둔다. v1.2 설정 화면(카드·저널)은 R2 동안 그대로 두고 R3(Canvas 토큰) 때 v2 설정 엔드포인트로 옮긴다.

## 2. 슬라이스 (PR 단위)

### R2-1 Core stdio 프로토콜 + 세션 + HTTP 골격 (C-13 나머지, C-20 Python 쪽)

- `syllva core --stdio`:
  - `hello`를 받기 전에는 리스너를 열지 않는다(§2.2-4).
  - `hello`를 받은 뒤 순서: 락 → 마이그레이션 → `127.0.0.1:0` 바인딩 → `ready{port, bootstrap}`.
  - 메시지 검사: 줄당 64 KiB 상한, `v:1`, 알 수 없는 `t`는 오류.
  - 핑/퐁과 30초 무응답 종료, stdin EOF 종료, `shutdown`/`bye`.
  - 종료 코드는 §2.5를 따른다.
  - stdout은 프로토콜 전용이다. 정적 규칙 `core-stdout`을 `syllva.app`까지 확장한다.
- **`shell_secret`**:
  - Core 메모리에만 둔다(`bytes`, 로그 직렬화 불가 래퍼 `Secret`).
  - `repr`/`str`는 `<secret>`으로 표시한다.
- **세션**(§10.2):
  - bootstrap 토큰은 1회용이며 60초 안에 써야 하고 `window_label`에 결속된다. 첫 토큰은 `main` 창에 결속된다.
  - `POST /api/session {bootstrap}` → `syllva_sid` 쿠키(`HttpOnly; SameSite=Strict; Path=/`) + 응답 본문의 CSRF 토큰.
  - 세션 → `window_label` 매핑은 Core 메모리에 둔다. 유휴 8시간이 지나면 만료된다.
  - `session.bootstrap{window_label}`(stdio)로 새 창 토큰을 받는다.
- **HTTP 골격**:
  - §10.4 보안 헤더·CSP를 모든 응답에 붙인다.
  - Host 정확 일치, Origin·Sec-Fetch-Site 검사.
  - 엔드포인트: `GET /healthz`, `GET /bootstrap`(정적 페이지: fragment를 읽어 교환), `POST /api/session`.
  - 접근 로그는 쿼리·fragment를 제거해 남긴다.
- **로깅**(§13.1): `log_event(code, **fields)`와 키 이름 정화 필터. 프롬프트·본문·파일 경로는 남기지 않는다.
- **테스트**: 테스트 전용 Shell 시뮬레이터(`tests/support/shell_sim.py`)가 Core를 하위 프로세스로 띄워 프로토콜을 검증한다.
  - `hello` 전에 포트가 열리지 않는다.
  - 두 번째 Core는 `DB_LOCKED`로 exit 3.
  - 잘못된 `hello`는 exit 2.
  - stdin을 닫으면 종료한다.
  - 센티널 비밀이 stderr·로그 파일·HTTP 응답·자식 env에 나타나지 않는다(C-20).
  - `0.0.0.0` 바인딩 설정을 거부한다(C-13).
- **헤드리스 모드**(`syllva core --headless`, 개발·CI용): `shell_secret`이 없으므로 게이트는 항상 `GATE_UNAVAILABLE`이다(§6.7).

### R2-2 Human-Proof 게이트 (C-04, C-05, C-17, C-19 Python 쪽)

- **`gate.canonical`**: RFC 8785 벡터 테스트. 숫자는 정수만, 문자열은 NFC.
- **`gate.summaries.summarize(action, params, view)`**: §6.5 문장. 표시명은 80자로 자르고 제어문자를 제거한다. ID·열거형 params만 받는다.
- **`gate.fingerprints`**: §6.2 액션별 지문.
  - R2 범위는 `VERIFY_MATERIAL`이다(자료와 `material_usage`가 R1c에 있음).
  - `CONFIRM_EXAM_SCOPE`·`SET_SUBMISSION_STATUS`·`ADOPT_AI_ARTIFACT`는 대상 기능 단계(R3/R5)에서 연결한다. 그 전까지 `begin`은 `ACTION_NOT_AVAILABLE`로 거부한다.
- **`POST /api/gate/begin {action, target_id, params}`**(세션+CSRF):
  - 세션당 PENDING 1개(`409 GATE_BUSY`), 거절·만료 직후 2초 쿨다운.
  - TTL은 60초이며 `monotonic` 기준이다.
  - 응답은 `{nonce_id}`뿐이다. 요약이나 해시를 돌려주지 않는다.
  - 요청 본문에 `summary`나 알 수 없는 키가 있으면 400이다(C-19).
- **stdio 메시지**:
  - `gate.get{nonce_id, window_label}` → `gate.view` 또는 `gate.none(WRONG_WINDOW|EXPIRED|UNKNOWN)`.
  - `gate.confirm{nonce_id, window_label, mac_b64}` → §6.4 1~5를 **같은 처리 안에서** 수행한 뒤 `commit_gate`.
  - `gate.deny`.
  - 결과는 `gate.result{status}`.
- **`commit_gate`**:
  - `BEGIN IMMEDIATE` 아래 대상 UPDATE와 `human_gate_log` INSERT를 한다.
  - 기존 DB 트리거(GATE_REQUIRED/GATE_REUSED, E7)가 마지막 방어선이다.
  - 메모리 상태는 커밋 성공 뒤에만 `COMMITTED`로 바꾼다. 같은 nonce로 재제출하면 idempotent하다(C-17).
- **동시성**: 게이트 서비스 락 하나로 CAS를 보호한다. 동시 `confirm` 2건 중 1건만 커밋된다(C-05).
- **Core 재시작**: 메모리 nonce가 사라지므로 이전 nonce는 `UNKNOWN`이 된다(C-20).
- **시뮬레이터 경계**: 프로덕션 코드에는 비밀 주입 경로가 `hello`뿐이다. `tests/` 밖에서 시뮬레이터를 import하지 않음을 정적 검사로 확인한다(C-04).

### R2-3 v2 UI 엔드포인트 + 정적 UI 골격 (C-18)

- **엔드포인트**(§10.3 중 R1c 기능에 해당하는 것):
  - `GET /api/library/items`
  - `POST /api/library/upload`(multipart, 스트리밍, 크기 상한) → `LocalIntake.receive` + `classify`
  - `GET /api/intake/candidates`
  - `POST /api/library/{candidate}/classify`(확인 = `LocalIntake.confirm`, `UI_USER`)
  - `POST /api/library/{material}/replace`
  - `DELETE /api/library/{id}`(논리 삭제)
  - `GET /api/evidence/...`(`RetrievalService.get_source_chunk`, `caller_scope` = UI 세션 id)
  - `POST /api/gate/begin`
- **정적 UI**(`src/syllva/app/static/`): 라이브러리, 확인 필요, 자료 보기, "검증됨으로 표시" 버튼(→ `begin` → Tauri `confirm_gate`).
  - 모든 텍스트는 `textContent`로만 넣는다. 인라인 스크립트는 쓰지 않는다.
  - 외부 리소스는 쓰지 않는다.
- **C-18**:
  - Node + jsdom 하네스(기존 `settings_ui_harness.cjs` 방식)로 악성 payload를 넣어, DOM에 실행 가능한 노드가 생기지 않음을 확인한다.
  - CSP 헤더를 검사한다.
  - 정적 sink 린트를 `app/static`까지 확장한다.

### R2-4 Tauri 셸 (C-19 셸 설정 검사, C-20 셸 쪽)

- **`shell/` 크레이트**(Tauri 2):
  - `getrandom`으로 32바이트 비밀을 만든다.
  - Core는 환경 allowlist(`PATH`, `HOME`/`USERPROFILE`, `LANG`, `SYLLVA_DATA_DIR`)만 넘겨 spawn한다.
  - stdin 첫 줄로 `hello`를 보내고 `ready`를 받으면 웹뷰를 연다.
  - 10초마다 ping을 보낸다. 앱이 끝나면 `shutdown`을 보낸다.
- **`confirm_gate(nonce_id)` 커맨드**:
  - `window_label`은 호출한 창에서 Rust가 직접 얻는다.
  - `gate.get` → 네이티브 다이얼로그(rfd 또는 Tauri dialog **Rust API**; 웹뷰 capability에는 넣지 않음) → `gate.confirm`(HMAC) 또는 `gate.deny` → `gate.result`를 반환한다.
- **capability**: 창 제어 + `confirm_gate`만 둔다. `dialog`/`shell`/`fs`/`http` 플러그인은 금지다.
  - Python 계약 테스트가 `shell/capabilities/*.json`과 `tauri.conf.json`을 파싱해 검사한다(C-19).
- **Rust 테스트**:
  - MAC 벡터는 Python과 같은 파일을 쓴다.
  - JSONL 프레이밍(64 KiB)을 검사한다.
  - spawn 환경이 allowlist만 담는지 검사한다.
- **CI**: `shell` 잡(ubuntu는 `libwebkit2gtk-4.1-dev` 설치, macOS, Windows)에서 `cargo test` + `cargo clippy -D warnings`.

### R2-5 스파이크 (명세 §13.6, D§12-3)

- **원격 URL IPC**: Tauri 2 `remote.urls`가 `http://127.0.0.1:<임의 포트>` 패턴을 허용하는지 확인한다.
  - 대안 A: 셸이 커스텀 프로토콜(`syllva://`)로 Core HTTP를 프록시해, 웹뷰 origin을 앱 origin으로 만든다.
  - 대안 B: 고정 포트 범위.
  - 결과로 §2.2·§6의 웹뷰 로드 방식을 확정하고 정오표에 남긴다.
- **fragment 보존**: WebView2/WKWebView에서 `/bootstrap#token`이 보존되는지 확인한다(§10.2). 이 환경에는 GUI가 없어 CI 헤드리스로는 일부만 확인할 수 있다. 실제 확인은 수동 시연과 함께 한다.
- **번들**: PyInstaller onedir로 Core를 sidecar로 만든다. 서명·공증은 인증서가 필요해 사용자 결정 사항이다.

## 3. 위협별 대응 대조 (설계서 §5.1, §5.4a, §5.6)

| 위협 | 대응 | 테스트 |
|---|---|---|
| 웹뷰 XSS가 human-only 쓰기를 만든다 | HTTP에 커밋 경로 없음. 커밋은 stdio `gate.confirm`(셸 HMAC)만 가능. 웹뷰는 `nonce_id`만 안다 | C-04 |
| XSS가 위조 요약으로 다이얼로그를 띄운다 | 요약은 Core가 params(ID/열거형)에서 생성하고 해시에 포함한다. begin에 요약 필드가 없다 | C-19 |
| 다른 창·세션의 nonce로 확인한다 | nonce ↔ 세션 ↔ `window_label` 결속, 셸이 창을 직접 판정 | C-04 |
| 다이얼로그 스팸 | 세션당 PENDING 1개 + 2초 쿨다운 | C-05 |
| 확인 후 대상이 바뀐다(TOCTOU) | 커밋 시 지문·요약·해시를 재계산하고, 다르면 REJECTED(TARGET_CHANGED) + SUPERSEDED | C-05, C-17 |
| 재전송·이중 커밋 | 메모리 CAS + `human_gate_log.nonce_id UNIQUE` + DB 트리거 | C-05, C-17 |
| 비밀 유출 | stdin 1회 전달. 로그·env·argv·HTTP·자식 프로세스에 없음. `Secret` 래퍼 | C-20 |
| 같은 OS 사용자의 악성 프로세스 | 방어 대상 아님(§5.6). 루프백 HTTP 세션 쿠키에 `Secure`를 못 붙이는 것도 한계로 인정 | — |
| 헤드리스 Core로 우회 | 비밀이 없으면 게이트 불가(`GATE_UNAVAILABLE`) | C-04 |

## 4. 사람이 해야 하는 단계 (R2 끝에서 요청)

1. macOS·Windows에서 앱을 실행하고 "검증됨으로 표시" → 네이티브 다이얼로그 → 확인/취소를 시연한다. 화면 기록이나 짧은 메모를 남긴다(§15 완료 기준).
2. 서명·공증 인증서(Apple Developer ID, Windows 코드 서명) 사용 여부를 결정한다. 없으면 미서명 개발 번들로 진행한다.

## 5. 열린 질문 (검토 요청)

- `syllva.app`을 새 패키지로 둘지, `settings/`를 확장할지. 명세 §1.1은 "settings를 v2 UI 서버로 확장"이라 했지만, v1.2 설정 저널·카드와 섞이면 C-18·C-19 경계가 흐려진다. 이 계획은 보안 헤더 로직만 재사용하고 앱은 분리한다(정오표로 기록 예정).
- R2에서 `VERIFY_MATERIAL`만 연결하고 나머지 세 액션은 해당 기능 단계로 미룬다.
- Rust 측 HMAC 키를 `zeroize`로 해제할지(동일 사용자 위협 제외라 필수는 아님).
