# R2 계획 — UI 서버·Tauri 셸·Human-Proof (rev 2)

근거: 구현 명세서 §1.2, §2(프로세스·프로토콜), §6(Human-Proof), §10(UI HTTP API), §13.1(로깅), §13.6(패키징), §15 R2, 정오표 E23–E25; 설계서 §3.1, §5.1, §5.4a(rev 5), §5.6. 계약: C-04, C-05, C-13(나머지), C-17, C-18, C-19, C-20.

검토 이력:

- **rev 1**: 독립 계획 검토에서 REQUEST_CHANGES를 받았다(필수 12건).
- **rev 2(이 문서)**: 필수 12건과 일부 선택 항목을 반영했다.
- **사용자 결정(2026-11-03)**: 웹뷰는 커스텀 프로토콜로 중계한다(설계 rev 5). 패키지 이름과 게이트 액션 연기는 정오표로 처리한다(E23, E24).

## 0. 완료 기준 (명세 §15 R2)

- **자동 검증**: C-04/05/17/19/18이 통과하고, C-20의 R2 범위도 통과한다.
  - Python 쪽은 CI 3개 OS에서 돌린다.
  - Rust 순수 크레이트는 `cargo test`를 3개 OS에서 돌린다.
- **C-20**: 아래 항목은 R4(에이전트 spawn 경로가 생길 때)까지 PENDING으로 남긴다.
  - 에이전트 자식이 프로토콜 파이프를 상속하지 않음
  - 자식 프로세스 env
- **C-04/05/17**: R2에서는 `VERIFY_MATERIAL`만 활성화한다(E24).
- **사람 단계(R2 끝에서 요청)**:
  - macOS·Windows에서 네이티브 확인 다이얼로그를 수동 시연한다.
  - 서명·공증 인증서를 쓸지 결정한다.

## 1. 패키지 (명세 §1.2, E23)

| 패키지 | 내용 | import 계약 |
|---|---|---|
| `syllva.gate` | `canonical.py`(RFC 8785 호환), `summaries.py`(순수 `summarize`, 4개 액션), `fingerprints.py`(4개 액션 문자열 형식 + VERIFY 조회), `nonces.py`(메모리 전용 레코드·상태 기계), `mac.py`(§6.3.2), `service.py` | 신규 `v2-gate-clean`. 금지: `settings`·`cli`·`mcp`·`intake`·`adapters`·`retrieval`·`ui`·`core`. 쓰기는 `StateWriter.commit_gate`만 사용 |
| `syllva.state.v2.writer` | `commit_gate(...)`(§6.6, 아래 R2-2) | 기존 유지 |
| `syllva.core` | `stdio.py`(JSONL 루프), `main.py`(`syllva core --stdio` / `--headless`), `logging.py`(§13.1 + 아래 확장 정화), `secret.py`(`Secret` 래퍼) | 정적 규칙 `core-stdout` 적용(이미 이 경로를 검사) |
| `syllva.ui` | `http.py`(Starlette v2 앱, 보안 헤더), `sessions.py`(bootstrap·창 결속·세션·CSRF), `routes/*`, `static/` | 정적 규칙 `ui-raw-html` 적용(이미 `src/syllva/ui/static` 검사). `settings.security`의 검사 함수만 재사용 |
| `shell/proto` (Rust, Tauri 의존 없음) | JSONL 프레이밍(64 KiB), MAC(공유 벡터), Core spawn 환경 allowlist 빌더, 메시지 타입 | `cargo test` 3 OS |
| `shell/app` (Rust, Tauri 2) | 창, 커스텀 프로토콜 중계, `confirm_gate`, 다이얼로그, 이동 제한, capability | `cargo clippy -D warnings` + `cargo build` 3 OS |

새 import 계약: `ui-core-boundary`. 그 밖의 Core 계층(`domain`·`state`·`retrieval`·`intake`·`gate` 등)은 `syllva.ui`·`syllva.core`를 import하지 않는다.

## 2. 슬라이스

### R2-0 스파이크 (버림용, 먼저 수행) — 웹뷰 로드·IPC origin 확정

설계 rev 5에 따라 커스텀 프로토콜 중계 방향으로 확인한다.

1. **origin 판정**: `syllva://`(Windows는 `http://syllva.localhost` 형태로 매핑됨) 등록 시 IPC가 이 origin에서만 동작하고, 그 밖의 origin에서는 거부되는지 확인한다.
2. **중계 손실**: 중계가 요청 메서드·헤더·본문(multipart), 응답 헤더(CSP, Set-Cookie)를 잃지 않는지 확인한다. 쿠키 대신 중계 계층이 세션을 붙이는 방식이 필요한지도 판단한다.
3. **fragment 보존**: `syllva://app/bootstrap#token`의 fragment가 보존되는지 확인한다.
4. **이동 제한 훅**: `on_navigation`으로 앱 origin 밖 이동을 차단하고, 외부 링크를 OS 브라우저로 넘길 수 있는지 확인한다.

결과는 E25 행을 갱신해 기록한다. 스파이크 코드는 머지하지 않는다.

GUI가 없는 이 환경에서는 컴파일과 단위 테스트까지만 할 수 있다. 1·3·4의 실제 동작은 R2 끝의 수동 시연 항목에 포함한다. 결과가 rev 5 방향을 깨면(중계 불가 등) 구현 전에 사용자에게 다시 묻는다.

### R2-1 Core stdio 프로토콜 + 세션 + HTTP 골격 (C-13 나머지, C-20 Python 범위)

**프로토콜**

- `hello`를 받기 전에는 리스너를 열지 않는다.
- `hello`를 받은 뒤 순서: 락 → 마이그레이션 → `127.0.0.1:0` → `ready`.
- 다음은 모두 exit 2다: 두 번째 `hello`, `hello` 전에 온 메시지, 64 KiB 초과 줄, 잘못된 JSON. 알 수 없는 `t`는 오류 응답만 보내고 계속한다(명세 §2.3).
- `ping`/`pong`을 주고받고, 30초 무응답·stdin EOF·`shutdown`이면 종료한다.

**채널 무결성**

- Core는 시작 즉시 fd 1을 `dup`해 프로토콜 전용 writer로 쓴다. `sys.stdout`은 stderr로 돌린다. 의존성의 `print`나 경고가 프로토콜 줄을 오염시키지 못하게 하려는 것이다.
- Shell은 JSON이 아닌 stdout 줄을 프로토콜 위반으로 처리한다(Core 종료). stderr는 프로토콜로 해석하지 않는다.

**비밀**

- `Secret` 래퍼를 둔다.
  - `repr`/`str`은 `<secret>`이다.
  - `__reduce__`/`__getstate__`/JSON 직렬화는 오류를 낸다.
- `hello` 원문 줄은 어디에도 남기지 않는다.

**로깅(§13.1 확장, 정오표에 기록)**

- 정화 키 패턴에 다음을 추가한다: `bootstrap|mac|nonce|sid|csrf|secret_b64|session`.
- 프로토콜 줄은 로그로 남기지 않는다. `t`와 크기만 기록한다.
- Starlette는 `debug=False`로 두고, uvicorn 접근 로그는 끈다. 대신 자체 접근 로그를 남기는데, 경로만 기록하고 쿼리·fragment는 지운다.
- 트레이스백은 locals 없이 남긴다.

**세션(§10.2)**

- bootstrap 토큰 소비는 원자적이다. 동시 `POST /api/session` 두 건 중 하나만 성공한다(테스트).
- 토큰은 1회용이고 60초 안에 써야 하며 `window_label`에 결속된다.
- 한 창에 세션은 하나다. 같은 `window_label`로 새 세션이 생기면 이전 세션은 무효가 된다.
- 유휴 8시간이 지나면 만료된다.
- 쿠키는 `syllva_sid`(`HttpOnly; SameSite=Strict; Path=/`)다.
- CSRF 토큰은 응답 본문으로만 주고, JS 메모리에만 보관한다(`localStorage` 금지).
- `/bootstrap` 페이지는 fragment를 읽은 뒤 `history.replaceState`로 지운다.
- `session.bootstrap`은 stdio에서만 받는다. 즉 Shell만 새 창 토큰을 요청할 수 있다.

**HTTP**

- §10.4 헤더·CSP를 모든 응답에 붙인다.
- Host는 정확히 일치해야 한다(DNS rebinding 차단).
- **변경 요청에서는 Origin 정확 일치가 필수**다. 명세의 "있으면"보다 강화한 것이다. 브라우저는 변경 요청에 항상 Origin을 보낸다.
- `Sec-Fetch-Site`는 `same-origin`/`none`만 허용하고, `same-site`는 거부한다.
  - 루프백의 서로 다른 포트는 same-site라서 `SameSite`만으로는 분리되지 않는다.
  - 이 경우는 Host·Origin·CSRF 헤더가 막는다.
- v1.2의 Referer 폴백은 쓰지 않는다.
- 엔드포인트: `GET /healthz`, `GET /bootstrap`, `POST /api/session`.

**헤드리스(`--headless`)**: `shell_secret`이 없으므로 게이트는 `GATE_UNAVAILABLE`이다.

**테스트**: 테스트 전용 Shell 시뮬레이터(`tests/support/shell_sim.py`, 하위 프로세스 구동)로 다음을 검증한다.

- 위 프로토콜 규칙 전부
- 두 번째 Core는 `DB_LOCKED`로 exit 3
- 센티널 비밀이 stderr·로그 파일·HTTP 응답·접근 로그에 나타나지 않음
- `0.0.0.0` 바인딩 설정 거부(C-13)

### R2-2 Human-Proof 게이트 (C-04, C-05, C-17, C-19 Python 범위)

**순수 함수**

- `canonical`에 RFC 8785 벡터를 넣는다.
- 네 액션 모두에 대해 `summarize`와 `target_fingerprint`의 형식을 정하고 벡터 테스트를 둔다(E24).

**`POST /api/gate/begin {action, target_id, params}`**(세션+CSRF+Origin)

- 요청 키는 정확히 이 셋만 허용한다. `summary`나 알 수 없는 키가 있으면 400이다(C-19).
- params는 ID와 열거형 값만 받는다.
- R2에서는 `VERIFY_MATERIAL`이 아니면 `ACTION_NOT_AVAILABLE`이다.
- 세션당 PENDING은 1개다. 이미 있으면 `409 GATE_BUSY`다.
- **거절·만료·DENIED 직후 2초는 쿨다운**이다.
- TTL은 60초이고 `monotonic` 기준이다.
- 응답은 `{nonce_id}`(16바이트 난수)뿐이다.
- 종단 레코드는 5분 동안 보관한다(C-17 재제출 응답용).

**stdio**

- `gate.get`은 `gate.view` 또는 `gate.none(WRONG_WINDOW|EXPIRED|UNKNOWN|SESSION_GONE)`을 돌려준다.
- `gate.confirm`과 `gate.deny`가 있다.
- 결과는 `gate.result{status, reason?}`다.

**커밋 원자성** — 검토 필수 1. 핵심은 확인 시점과 쓰기 시점 사이에 틈이 없게 하는 것이다.

1. 게이트 서비스 락을 잡는다. 이 락은 단계 1부터 메모리 전이까지 **계속** 쥔다.
2. 다음을 검사한다:
   - 레코드가 `PENDING`이고 기한 안이다.
   - 세션이 **여전히 유효**하다(begin 뒤 만료되면 거부).
   - 세션의 `window_label`이 확인 요청의 `window_label`과 같다.
   - MAC이 일치한다.
3. 레코드를 `COMMITTING`으로 옮긴다(CAS).
4. `StateWriter.commit_gate(record)`를 호출한다. 이 호출은 `BEGIN IMMEDIATE` **안에서** 다음을 한다:
   - 대상 지문을 다시 읽고, 요약을 다시 만들고, `request_hash`를 다시 계산한다.
   - 다르면 롤백하고 `TARGET_CHANGED`를 반환한다.
   - 같으면 대상 UPDATE(human-only 열 + `*_gate_id`)와 `human_gate_log` INSERT를 한 트랜잭션에서 한다.
5. 성공이면 `COMMITTED`, 실패면 `REJECTED(reason)`.
6. 락을 놓는다.

`human_gate_log.nonce_id UNIQUE`와 DB 트리거는 마지막 방어선일 뿐 정상 경로가 아니다.

**SUPERSEDED의 위치** — 검토 필수 2

- v1.2 제안 객체는 R1c에서 삭제됐다. 그래서 R2에서 "해당 제안 SUPERSEDED"는 nonce 종단 상태 `REJECTED`에 `reason="TARGET_CHANGED"`, `superseded=true`를 기록하는 것으로 정한다.
- 이 결과는 `gate.result`로 셸과 UI에 전달된다.
- R3/R5에서 제안 객체가 생기면 그 제안의 상태로 옮긴다. 정오표에 기록한다.

**재시작**: nonce가 메모리에만 있으므로 재시작하면 이전 nonce는 `UNKNOWN`이 된다.

**시뮬레이터 경계**: 프로덕션에서 비밀이 들어오는 경로는 `hello`뿐이다. 정적 검사로 `tests/` 밖에서 `shell_sim`을 import하지 않음을 확인한다.

**테스트 목록(명세 §14)**

- 일반 API로는 human-only 쓰기가 불가능하다.
- 다른 창의 nonce를 확인할 수 없다.
- begin 뒤 세션이 만료되면 confirm이 거부된다.
- 재시작 뒤 이전 nonce는 `UNKNOWN`이다.
- 틀린 MAC, 만료, 재사용은 거부된다.
- 동시 confirm 2건 중 1건만 커밋된다.
- 지문이 바뀌면 `REJECTED(TARGET_CHANGED)` + superseded가 된다.
- `COMMITTED` 재제출은 idempotent하다.
- `REJECTED` 재제출은 거부된다.
- begin 응답에 요약·해시가 없다.

### R2-3 v2 UI 엔드포인트 + 정적 UI (C-18)

**엔드포인트**(§10.3 중 R1c 기능)

- 자료 목록: `GET /api/library/items`
- 확인 대기 후보: `GET /api/intake/candidates`
- 업로드: `POST /api/library/upload` — multipart 스트리밍, 크기 상한
- 확인: `POST /api/library/{candidate}/classify` — `UI_USER`로 처리
- 교체: `POST /api/library/{material}/replace`
- 삭제: `DELETE /api/library/{id}` — 논리 삭제
- 근거 열람: `GET /api/evidence/...` — `caller_scope`는 UI 세션 id
- 게이트 시작: `POST /api/gate/begin`

**정적 UI**(`syllva/ui/static/`)

- 텍스트는 `textContent`로만 넣는다.
- 인라인 스크립트와 외부 리소스는 쓰지 않는다.
- 외부 링크는 Shell을 통해 OS 브라우저로 연다.

**C-18**

- Node + jsdom 하네스로 악성 payload를 넣었을 때 실행 가능한 노드가 생기지 않는지 확인한다.
- CSP 헤더를 검사한다.
- 정적 sink 린트가 `syllva/ui/static`을 검사한다.
- **CI에서 건너뛰지 않는다.**
  - v2 잡에 `setup-node`를 넣는다.
  - `CI`가 설정돼 있는데 node가 없으면 실패로 처리한다(기존 `test_settings_ui.py`의 skip 방식은 쓰지 않는다).

### R2-4 Tauri 셸 (C-19 셸 설정 검사, C-20 셸 범위)

**`shell/proto`**(Tauri 의존 없음)

- 32바이트 비밀을 `getrandom`으로 만든다.
- Core spawn 환경은 allowlist(`PATH`, `HOME`/`USERPROFILE`, `LANG`, `SYLLVA_DATA_DIR`)만 넣는다. 테스트로 확인한다.
- JSONL 프레이밍: 64 KiB 상한, JSON이 아닌 줄은 오류.
- MAC은 Python과 같은 벡터 파일(`tests/fixtures/gate_mac_vectors.json`)로 검증한다.

**`shell/app`**

- **커스텀 프로토콜 중계**(rev 5): `syllva://` 요청을 Core `127.0.0.1:<port>`로 전달한다.
- **이동 제한**:
  - `on_navigation`은 앱 origin만 허용한다.
  - 새 창 요청은 차단한다.
  - 외부 `http(s)`는 OS 브라우저로 연다.
  - 릴리스 빌드에서는 devtools를 끈다.
- **stderr 처리**: Core stderr를 수집할 때 §13.1과 같은 정화를 적용한다. stderr는 프로토콜로 쓰지 않는다.
- **`confirm_gate(nonce_id)`**
  - `window_label`은 호출한 창에서 Rust가 직접 얻는다.
  - `gate.get` → 네이티브 다이얼로그 → `gate.confirm`(HMAC) 또는 `gate.deny` 순으로 진행한다.
  - 다이얼로그는 Tauri dialog의 **Rust API**나 `rfd`로 띄운다. 웹뷰 capability에는 넣지 않는다.
  - **다이얼로그 내용**(§6.3(3), §6.5):
    - Core가 만든 요약
    - `target_id`
    - 원본 식별자 `source_ids` + sha256 앞 12자
    - 남은 시간
    - 취소 / 확인 버튼
  - 반환값은 `gate.result{status, reason?}`뿐이다. `gate.view` 필드는 JS에 주지 않는다.
- **capability**: 허용 식별자를 **열거**한다. 아래 목록 밖의 식별자는 C-19 테스트에서 실패한다.
  - 허용: `confirm_gate`, 창 최소화·최대화·닫기·드래그 정도.
  - 명시 금지:
    - `core:webview:allow-create-webview-window`
    - `core:window:allow-create`
    - `allow-eval`
    - URL 설정·이동 계열
    - `allow-internal-toggle-devtools`
    - 모든 `dialog`/`shell`/`fs`/`http` 플러그인
  - `remote.urls`는 없어야 한다.
- **C-19 Python 테스트**: `shell/app/capabilities/*.json`과 `tauri.conf.json`을 파싱해 다음을 확인한다.
  - 허용 목록이 위 열거와 같다.
  - `remote` 설정이 없다.
  - 릴리스에서 devtools가 꺼져 있다.
  - 이동 제한 설정이 있다.
- **CI `shell` 잡**:
  - `proto`: 3개 OS에서 `cargo test`.
  - `app`: 3개 OS에서 `cargo clippy -D warnings` + `cargo build`(ubuntu에는 `libwebkit2gtk-4.1-dev` 설치).
  - 예상 비용: CI 시간이 약 2배가 된다.

### R2-5 번들 스파이크 (§13.6)

- PyInstaller onedir로 Core sidecar를 만들고 스모크 테스트를 한다(`keyring`, `pypdf` 포함).
- 서명·공증은 인증서가 있어야 하므로 사용자 결정 사항이다.

## 3. 위협별 대응

| 위협 | 대응 | 테스트 |
|---|---|---|
| 웹뷰 XSS로 human-only 쓰기 | HTTP에 커밋 경로가 없다. 커밋은 stdio `gate.confirm`(셸 HMAC)만 가능하고, 웹뷰는 `nonce_id`만 안다 | C-04 |
| 위조 요약 다이얼로그 | 요약은 Core가 ID·열거형 params로 만들고 해시에 포함된다. begin은 요약 필드를 거부한다 | C-19 |
| 다른 로컬 페이지가 IPC 호출 | 커스텀 프로토콜 origin에만 IPC를 연다. 원격 URL은 허용하지 않는다(rev 5) | C-19 |
| 웹뷰가 외부·다른 origin으로 이동 | `on_navigation` 허용 목록, 새 창 차단, 외부 링크는 OS 브라우저 | C-19 |
| XSS가 새 창을 만들어 다른 URL을 연다 | 창 생성·이동·eval 권한을 명시적으로 금지하고 열거 목록 밖은 테스트 실패 | C-19 |
| 다른 창·세션의 nonce | nonce ↔ 세션 ↔ `window_label` 결속, 커밋 시 세션 생존 재확인 | C-04 |
| 같은 창에서 세션 탈취 후 재수립 | 창당 세션 1개(새 세션이 이전 세션 무효화), 원자적 bootstrap 소비 | C-04 |
| DNS rebinding·다른 루프백 포트 | Host 정확 일치, 변경 요청 Origin 필수 일치, `same-site` 거부, CSRF 헤더 | C-13, C-04 |
| 다이얼로그 스팸 | PENDING 1개 + 거절·만료·DENIED 후 2초 쿨다운 | C-05 |
| TOCTOU(확인 후 대상 변경) | 재계산·쓰기를 한 `BEGIN IMMEDIATE` 안에서, 서비스 락은 끝까지 유지, `COMMITTING` CAS | C-05, C-17 |
| 재전송·이중 커밋 | CAS, 종단 레코드 5분 보관, `nonce_id UNIQUE`, 트리거 | C-05, C-17 |
| 비밀 유출 | stdin 1회 전달, `Secret` 래퍼, 정화 패턴 확장, 프로토콜 줄 미기록, 트레이스백 locals 제외 | C-20 |
| 프로토콜 채널 오염 | fd 1 전용화, 셸은 JSON이 아닌 줄을 위반으로 처리, stderr는 프로토콜 아님 | C-20 |
| 헤드리스 Core로 우회 | 비밀이 없으면 `GATE_UNAVAILABLE` | C-04 |
| 같은 OS 사용자의 악성 프로세스, 루프백 쿠키에 `Secure` 불가 | 방어 대상 아님(§5.6), 한계로 인정 | — |

## 4. 사람 단계 (R2 끝)

1. macOS·Windows에서 시연한다. 앱 로드(`syllva://`, fragment 교환) → "검증됨으로 표시" → 다이얼로그 내용 확인 → 확인·취소 → 외부 링크가 OS 브라우저로 열리는지. 결과를 기록으로 남긴다.
2. 서명·공증 인증서를 쓸지 결정한다.

## 5. 정오표로 남길 항목

- E23: 패키지 이름.
- E24: 액션 연기.
- E25: 커스텀 프로토콜 로드.
- R2-1 구현 PR에서 추가할 것: 정화 패턴 확장, 변경 요청 Origin 필수, SUPERSEDED 위치.

## 6. 재검토 승인 조건 (rev 2 APPROVE, 각 PR에 반영)

- **A (R2-4)**: 중계는 비밀 경로에 있다(Set-Cookie, `X-Syllva-CSRF`, 업로드 본문). 중계 로그는 메서드·경로(쿼리·fragment 제거)·상태·크기만 남기고 헤더·본문은 남기지 않는다. `shell/proto` 로그 포매터 단위 테스트로 고정한다.
- **B (R2-0 → E25, R2-4)**: 중계의 전달 규칙을 고정한다.
  - 대상은 `ready`의 `port`, 주소는 `127.0.0.1`만이다.
  - `syllva://` host는 고정값 `app`이다.
  - 리다이렉트는 따르지 않는다.
  - `Origin`·`Sec-Fetch-*`·`Cookie`는 받은 그대로 전달하고 새로 만들지 않는다.
  - Core가 허용하는 앱 origin 문자열(host 고정값 `app`: darwin/linux `syllva://app`, win32 `http(s)://syllva.app` — Tauri의 Windows 매핑은 R2-0에서 확인; E26)은 `hello.platform`에서 정하고, Shell이 붙인 헤더로 정하지 않는다.
  - 커스텀 프로토콜 처리기는 요청 본문을 버퍼링하므로 업로드 크기 상한을 Shell에서도 적용한다. R2-3의 "multipart 스트리밍"은 "Core는 스트리밍, Shell은 상한 적용 버퍼"로 바꾼다.
- **C (R2-0)**: GUI가 없어 런타임 확인(앱 origin 한정 IPC, 쿠키·fragment 보존)은 R2 끝 수동 시연에서 처음 나온다. 스파이크에 두 항목을 더한다.
  - (i) 커스텀 프로토콜로 스트리밍 응답(R4의 SSE)이 되는지. 안 되면 R4는 루프백 `connect-src`가 필요하므로 지금 결정한다.
  - (ii) WKWebView·WebView2에서 `Set-Cookie`가 유지되는지. 유지되지 않으면 대안으로 중계가 세션을 붙인다. 이 경우 Tauri 2 scheme handler가 호출 웹뷰 label을 주므로, 중계가 Rust에서 얻은 창 label을 모든 요청에 붙여 §6.4-2를 강화할 수 있다. 결과는 E25에 기록한다.
  - 시연에서 IPC 한정이 실패하면 D§12-3에 따라 설계를 다시 개정한다. 쿠키·스트리밍만 실패하면 E25만 바뀐다.
- **D (R2-2)**: `state.v2`가 `gate`를 import하면 안 된다(`v2-storage-clean`). `commit_gate(record, *, confirmed_at, verify)`의 `verify: Callable[[StateWriter], GateCheck]`는 게이트 서비스가 넘기며, writer의 `BEGIN IMMEDIATE` 안에서 실행된다. 이렇게 재계산과 쓰기를 한 트랜잭션으로 묶는다.
- **선택**:
  - capability 허용 목록은 정확한 식별자로 테스트 fixture에 고정한다. 대상은 `core:window:allow-minimize|maximize|unmaximize|close|start-dragging`이다.
  - 다이얼로그는 남은 시간 카운트다운을 보여 주고 0이 되면 자동으로 닫는다.
  - E25에 "변경 요청 Origin 필수"를 명시한다.

## 7. R2-0 실측 결과와 중계 인증 (보완안 — 구현 전 독립 검토 대상)

### 7.1 실측 (2026-11-03, Linux WebKitGTK 2.52, Xvfb, Tauri 2 커스텀 프로토콜 `syllva://localhost`)

| 항목 | 결과 |
|---|---|
| `syllva://localhost/bootstrap#token` 로드, 정적 파일 중계 | 됨 |
| fragment 보존(앱이 토큰을 읽어 `POST /api/session`) | 됨 |
| 웹뷰 요청의 `Origin` / `Sec-Fetch-Site` | **보내지 않음**(모든 요청에서 없음) |
| `Set-Cookie` 후 다음 요청의 `Cookie` | **보내지 않음** |
| IPC(`window.__TAURI__`)·네이티브 다이얼로그 | GUI 조작 없이는 미확인 → 수동 시연 |

따라서 웹뷰가 보내는 헤더만으로는 R2-1의 Origin 필수 검사와 쿠키 세션을 쓸 수 없다. 조건 C의 대안을 택한다. macOS/Windows에서는 수동 시연으로 같은 항목을 기록한다.

### 7.2 보완안: Shell 중계가 창 신원과 세션을 붙인다

1. **중계 인증 헤더**
   - Shell은 커스텀 프로토콜 처리기의 호출 웹뷰 label(`ctx.webview_label()`, Rust가 얻는 값)로 다음 두 헤더를 모든 중계 요청에 붙인다.
     - `X-Syllva-Window: <label>`
     - `X-Syllva-Relay: base64url(HMAC-SHA256(shell_secret, "syllva-relay-v1" ‖ 0x00 ‖ len32(label) ‖ label))`
   - 웹뷰가 같은 이름의 헤더를 보내면 지우고 다시 쓴다.
2. **Core(stdio 모드)의 검증**
   - 모든 요청(GET 포함, `/healthz` 제외)에 유효한 중계 헤더를 요구한다. 없거나 틀리면 403이다.
   - 결과적으로 Core 루프백 HTTP는 중계만 쓸 수 있다. 같은 머신의 다른 브라우저 페이지와 비-Shell 클라이언트는 차단된다(§10.1 "헤더 검사는 보조"보다 강함).
   - Origin 필수 검사는 stdio 모드에서 이 중계 검사로 대체한다. `Sec-Fetch-Site`는 있으면 기존 규칙대로 검사한다. 헤드리스 모드는 지금처럼 루프백 Origin을 쓴다.
3. **세션 쿠키는 Shell 메모리에 둔다**
   - 중계는 Core 응답의 `syllva_sid` `Set-Cookie`를 **웹뷰에 전달하지 않는다**. 대신 창 label별 쿠키 보관소에 저장하고, 그 창의 이후 요청에 `Cookie`로 붙인다.
   - 결과적으로 세션 id는 웹뷰(XSS)에 닿지 않는다(강화). CSRF 토큰은 지금처럼 응답 본문으로 JS 메모리에 두고 계속 요구한다(심층 방어).
4. **창 결속**
   - Core는 세션의 `window_label`과 요청의 `X-Syllva-Window`가 같은지 매 요청 검사한다.
   - 따라서 다른 창의 세션을 쓸 수 없다(§6.4-2 강화, 조건 C가 언급한 강화).
   - bootstrap 교환도 토큰의 창과 요청 창이 같아야 한다.
5. **로그**: 중계 로그는 조건 A 그대로(메서드·경로·상태·크기)다. 중계 MAC과 쿠키 값은 기록하지 않는다.

**정오표**: E25를 갱신한다. 설계 §5.1(a)의 "same-origin 검사"는 "중계 인증(창 label에 결속된 shell_secret MAC)"으로 구현하며, 보증은 더 강하다. 설계서 개정이 필요한지는 검토 의견을 따른다.

### 7.3 검토 반영 (보완안 검토 REQUEST_CHANGES 필수 R1–R7) 및 사용자 결정 (2026-11-03)

**사용자 결정**
- 중계 인증은 정오표(E25)로 진행하고 설계서는 바꾸지 않는다. 조건 B의 쿠키 부분 변경을 승인했다.
- R4 답변 스트리밍은 중계 안의 짧은 폴링으로 한다. SSE를 쓰지 않고 루프백 `connect-src`도 열지 않는다. 기록은 E29.

**R1 — 조건 B 개정**
- Origin·`Sec-Fetch-*`는 받은 그대로 전달한다.
- **Cookie는 웹뷰에서 오는 값을 절대 전달하지 않는다.** 대신 창 label별 보관소에서 만들어 붙인다.
- **`Set-Cookie`는 웹뷰로 절대 전달하지 않는다.**
- `X-Syllva-Window`·`X-Syllva-Relay`는 중계가 만들어 붙인다.
- 웹뷰 헤더는 허용 목록 방식이다. 허용 목록에 `cookie`·`x-syllva-window`·`x-syllva-relay`가 없다는 것 자체가 차단 장치이며, 테스트로 고정한다.

**R2 — E26과 §3 위협표 개정**
- stdio 모드에서는 `/healthz`를 뺀 **모든 요청에 중계 인증이 필수**다.
- Origin은 **있으면** 허용된 앱 origin과 일치해야 한다(§10.1 원문으로 복귀). WebView2는 `Origin: http://syllva.localhost`를 보낼 수 있다.
- `Sec-Fetch-Site` 규칙과 Host 정확 일치는 그대로다.

**R3 — 모드 두 개, 대체 경로 없음**
- `create_app`은 `hello.secret`이 있을 때만 중계 검증기를 받는다.
- stdio 모드: 쿠키·CSRF·Origin이 모두 맞아도 중계 헤더가 없거나 틀리면 403이다.
- 헤드리스 모드: `X-Syllva-*`를 완전히 무시한다. 창 결속에 쓰지 않으며, 변경 요청의 Origin 필수 규칙(E26)을 유지한다.
- 헤더 검증 규칙:
  - 각 헤더는 정확히 한 번 나와야 한다.
  - label은 `WINDOW_LABEL` 형식이어야 한다.
  - 비교는 `hmac.compare_digest`로 한다.
  - 실패는 코드로만 기록한다.

**R4 — 정화 패턴**
- 정화 패턴에 `relay`를 추가한다.
- 중계 MAC은 `UI_DENIED` 필드와 Shell 로그 어디에도 남지 않는다.

**R5 — 보관소(jar) 의미**
- 메모리에만 둔다.
- 키는 *요청*의 `ctx` label이다.
- `syllva_sid`만 저장하고, 삭제(`Max-Age=0`)를 반영한다. `Domain`/`Path`는 무시한다.
- 창이 닫히면 항목을 지우고 Core에 `DELETE /api/session`을 보낸다.
- Core가 끝나거나 다시 시작하면 보관소 전체를 지운다.
- 오래된 sid로 401을 받으면 Shell은 `session.bootstrap`으로 새 토큰을 받아 그 창을 다시 로드한다.

**R6 — bootstrap 결속**
- 토큰의 label ≠ `X-Syllva-Window`이면 `POST /api/session`은 403이다. 이때도 토큰은 소비된다.
- 모든 라우트는 label이 맞지 않으면 403 `WRONG_WINDOW`이다. 이 검사는 유휴 시계를 건드리기 전에 한다.

**R7 — 테스트 벡터**
- `tests/fixtures/relay_mac_vectors.json`(도메인 `syllva-relay-v1\0`, `len32(label)‖label`)을 Python과 Rust가 함께 쓴다.

**구현 메모**
- 순수 부분을 `shell/proto`로 옮긴다: `relay_mac`, 요청 헤더 정리, 응답 흡수, `Jar`. 3개 OS에서 테스트한다.
- C-19 정적 검사로 확인할 것:
  - uri-scheme 처리기가 `ctx.webview_label()`을 쓴다.
  - `gate.rs`도 같은 label 공간(창 label)을 쓴다.
- 수동 시연에 남는 항목(§4): 앱 origin 한정 IPC, label 공간 일치. Core의 정합성은 더 이상 웹뷰가 무엇을 보내는지에 의존하지 않는다.

### 7.4 R2-4 검토 반영 (PR #52)

- **F1 — Core 종료 처리**: Shell은 프로토콜 위반이나 Core 종료를 감지하면 Core를 끝내고 앱도 끝낸다. 정상 종료와는 구분한다. 감시는 `ready` 이후에만 켜진다: 시작 중 거부(`DB_LOCKED` 등)는 종료 대신 거부 다이얼로그로 보고한다(재검토 지적).
- **F2 — 창 닫힘과 세션 정리**: 창이 닫히면 보관소 항목을 지우고 stdio `session.end`로 Core 세션을 즉시 끝낸다. Core 재시작 경로는 없다. Core가 끝나면 앱도 끝나므로 보관소도 함께 사라진다. §7.3 R5의 "Core 재시작 시 보관소 전체 삭제"는 이것으로 충족된다.
- **F3 — 정오표 기록**: `SYSTEMROOT` 허용과 Core stderr 폐기를 E30에 기록했다.
- **수동 시연 추가 확인 항목**(macOS/Windows):
  - WKWebView가 커스텀 스킴에서 `Origin: null`을 보내는지. 보내면 변경 요청이 `BAD_ORIGIN`이 되므로 E29 Origin 규칙을 재검토한다.
  - Core CSP에 `connect-src ipc:`가 없어 Tauri IPC가 postMessage로 대체될 때 콘솔 경고가 나는지.
