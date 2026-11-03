# R3 계획 — Canvas·과제 (rev 2, 2026-11-03)

> rev 2: 독립 계획 검토(REQUEST_CHANGES, 필수 6건)를 §6에 반영했다. **§6이 §2~§4의 해당 문장보다 우선한다.**

근거: 설계서 §2(I1·I2·I4·I9), §4.1, §5.5, §8(자격증명), 구현 명세 §11.2(녹음 달력), §12(Canvas), §10.3(`/api/canvas/...`, `/api/settings/...`), E13(활동 맥락), E20(녹음 달력은 R3), E22(관측기 복원), E24(게이트 액션), 로드맵 R3("Canvas 연결 UI + 과제 관리 + 첨부 로컬 적재, 실제 학기 데이터 확인").

**보안 경계 변경:** 자격증명 경로(Canvas 토큰)와 Human-Proof 액션 추가(`SET_SUBMISSION_STATUS`)가 포함된다. 따라서 이 계획은 구현 전에 독립 읽기 전용 검토를 받는다(CLAUDE.md).

## 1. 현재 상태 (조사 요약)

**재사용할 자산**

- `settings/canvas_checks.py`(836줄): Canvas HTTP 클라이언트. 테스트가 붙어 있다.
  - HTTPS 원점 고정, DNS 응답 전부 공인 IP여야 함, 핀 고정 TLS, GET 허용목록.
  - 크기·시간 상한, 다운로드 리다이렉트는 같은 원점 또는 허용 파일 호스트로만(토큰 제거).
  - 정확한 크기 검증.

**걸리는 자산**

- `settings/canvas_service.py`와 자격증명 스택(`credential_service/admission/stores/roles`, `journal.py`, `config/credentials.py`, 합계 약 4,300줄).
  - v1.2 YAML 설정 파일과 키체인을 함께 쓰기 위한 저널·예약 표식 기계다.
  - Notion/Google 역할이 남아 있다. Canvas는 darwin 전용이고, `sync_available=False`다.
  - v2 모듈은 이것을 import하지 않는다(`syllva setup`만 도달).
- 관측기·첨부(`539877d^`의 `canvas_observer/attachments/composition`): 레거시 상태 API 12종과 Drive 업로드에 묶여 있다.
  - 쪽 번호 증명(완결성)과 첨부 적격성 규칙은 그대로 가치가 있다.
- v2 스키마:
  - `activities`에 Canvas 과제 id와 `updated_at`이 없다.
  - Canvas 과목 연결·관측·첨부 연결·녹음 달력 테이블이 없다.
  - `source_files.provider/origin`은 `canvas`/`CANVAS`를 이미 허용한다.
  - `intake_candidates.channel`은 `CANVAS`를 이미 허용한다.
- `intake.v2.pipeline`:
  - `receive(..., channel=)`는 있지만 `provider`를 넘기지 않는다.
  - `classify`는 녹음 달력 없이 동작하므로, 전사문은 항상 `NEEDS_REVIEW`다.
- 게이트 결함(R2 산출물): `summaries.SUBMISSION_LABELS`가 `{"SUBMITTED","NOT_SUBMITTED","LATE"}`다.
  - 스키마는 `LATE_SUBMITTED`·`EXCUSED`이므로 해당 값에서 KeyError가 난다.
  - `fingerprints.submission(activity_id, current_status, updated_at)`의 `updated_at`에 해당하는 열이 없다.
- `scripts/knu_lms_*.py`(약 5,000줄): Notion 일정 프로젝션용 v1.2 보조 CLI다.
  - 기준선 실패 10건은 Linux에서 키체인 플랫폼 검사에 걸린 것이다.

## 2. 결정 (검토 요청 대상)

### D1. 자격증명: 새 최소 경로, 레거시 스택은 삭제

v2에서 비밀은 Canvas 토큰 하나뿐이다(설계 §8). 비밀이 아닌 Canvas 설정은 YAML이 아니라 **v2 SQLite**에 둔다.

- 비밀이 아닌 설정: 원점, 사용자 id·이름, 과목 연결, 허용 파일 호스트, 마지막 동기화.

`syllva/canvas/credential.py`(신규, 약 150줄 예상):

- **저장 위치**
  - 키체인 service `"Syllva Canvas"`, account `"profile:<profile_id>"`(기존 이름을 유지해 기존 사용자 항목과 호환).
  - `profile_id = "c" + sha256(origin "\n" user_id)[:32]`.
- **저장 순서**
  1. 메모리에 있는 토큰으로 `verify_canvas_user`를 실행한다(원점 고정 GET `/api/v1/users/self`).
  2. 성공하면 키체인에 쓰고 다시 읽어 확인한다.
  3. DB 트랜잭션으로 `canvas_profile` 행을 커밋한다.
  - 실패하면 키체인 항목을 지운다. 순서가 고정이므로 저널이 필요 없다.
  - **DB가 진실의 원천**이다. 시작 시 DB에 없는 profile의 키체인 항목(우리 service 이름 아래)은 고아로 보고 삭제한다.
  - 2단계와 3단계 사이에 충돌이 나면 고아 하나가 남고, 다음 시작에서 정리된다.
- **토큰 교체**
  - 같은 profile이면 검증 → 키체인 덮어쓰기 → DB `token_set_at` 갱신.
  - 다른 사용자나 원점이면 새 profile이다. 기존 profile과 연결은 사용자가 끊어야 한다.
- **잊기**: DB 행 삭제를 커밋한 뒤 키체인 항목을 삭제한다. 삭제에 실패하면 고아 정리가 맡는다.
- **백엔드 주입**
  - 시험은 메모리 백엔드를 주입한다(실제 키체인 미접촉).
  - 실제 백엔드는 darwin `macOS.Keyring`, win32 `WinVaultKeyring`만 허용한다(번들 스모크와 같은 이름 검증).
  - Linux는 `SecretService`도 허용한다(개발용). 그 밖의 백엔드(`fail`, `null`, `chainer`의 평문 파일 백엔드 등)는 `KEYCHAIN_UNAVAILABLE`로 거부한다.
- **I9**
  - 토큰은 HTTP 요청 본문으로 한 번 들어와 메모리에서만 쓰인다. DB·로그·응답·예외 메시지·`repr`에 나타나지 않는다.
  - 오류는 고정 코드다(`canvas_checks`의 코드 체계).

**레거시 스택 삭제(R3-5)**

- 대상: `settings/` 전체, `config/credentials.py`·`_keyring_backend.py`·`_secure_file.py`·`google_oauth.py`·`mutation.py` 중 v2가 쓰지 않는 것, `syllva setup` CLI, `scripts/knu_lms_*`·`_lms_platform.py`.
- `canvas_checks.py`는 `syllva/canvas/http.py`로 옮긴다.
- 처분표의 "재작성 R3 (I9)" 11개(credential 계열)와 `knu_lms` 3개는 이 결정에 따라 **대체/폐기**로 판정을 바꾼다.
  - 새 v2 시험이 의미를 대신 고정한다.
  - 기준선 실패 10건이 사라진다.
  - 정오표 E32로 기록한다.

### D2. 스키마 마이그레이션 `004_canvas.sql`

모든 행은 SOURCE 소유이며, 새 Actor `CANVAS_COLLECTOR`가 쓴다. 단, 아래에 USER로 표시한 것은 예외다.

- **`canvas_profile`**(단일 행): `profile_id, origin, user_id, display_name, file_hosts(JSON, 정확한 호스트명 ≤ 4), token_set_at, created_at`
  - USER 설정(Actor `UI_USER`)이며, 토큰 열은 **없다**.
- **`canvas_courses`**: `canvas_course_id UNIQUE, course_id → courses, term_name, name, code, linked_at`
  - Canvas 과목 ↔ 로컬 과목 연결. 사용자가 고른다(USER, 일반 설정 — human-only 아님).
- **`canvas_collections`**: `canvas_course_id, started_at, finished_at, status ∈ {COMPLETE, INCOMPLETE, FAILED}, reason`
  - 완결성 증명 결과.
- **`canvas_items`**: `canvas_course_id, kind ∈ {ASSIGNMENT, MODULE_ITEM, ANNOUNCEMENT, FILE}, resource_id, title, module_name, week, item_type, due_at, canvas_updated_at, attachment_json(파일 id·이름·크기·content-type만), active, first_seen_at, last_seen_at`
  - UNIQUE(`canvas_course_id`, `kind`, `resource_id`). 본문 HTML은 저장하지 않는다(XSS·크기). 링크는 저장하지 않는다.
- **`canvas_files`**: `canvas_file_id UNIQUE, canvas_course_id, display_name, size, content_type, canvas_updated_at, sha256, candidate_id → intake_candidates, state ∈ {PENDING, STORED, INELIGIBLE, FAILED}, reason`
- **`recording_calendar`**: `course_id, held_on(DATE), week, canvas_resource_id, revision, active`
  - Canvas `RECORDING` 관측에서 투영한다(§12, E20). 투영은 완결 수집 직후에만 바뀐다(fail-closed).
- **`activities`**: `ALTER TABLE ADD COLUMN` `canvas_assignment_id`(UNIQUE 부분 색인), `canvas_updated_at`, `updated_at`, `origin ∈ {CANVAS, USER}`
  - 동기화는 제목·마감만 갱신한다. `submission_status`는 절대 건드리지 않는다(트리거가 이미 강제).
  - Canvas가 보고하는 제출 여부는 `canvas_items`의 SOURCE 정보로만 보이고, 사용자가 게이트로 바꾼다.

### D3. 수집(관측기 복원)

`syllva/canvas/collect.py`가 `539877d^` 관측기의 쪽 번호 증명 규칙을 그대로 옮긴다.

- **쪽 번호 증명**
  - next가 정확히 `page=n+1`이어야 한다.
  - 가득 찬 쪽에 next가 없으면 n+1을 탐침한다.
  - `rel=last`로 확인한다.
  - 같은 path·query 유지, 중복 id면 불완전으로 본다.
  - 상한: 10쪽·500개·45초.
- **읽는 경로**(허용목록 기존 그대로)
  - assignments: `due_at`을 쓴다. `include[]=submission`은 허용목록에 추가가 필요하다 → 이번 범위에서 **제외**한다. 제출 여부 표시는 후속이다.
  - modules·items, announcements.
- **INCOMPLETE/FAILED이면**
  - 기존 관측을 비활성화하지 않는다.
  - 활동 마감을 바꾸지 않는다.
  - 녹음 달력을 닫는다(투영 전 상태 유지, 자동 확정 차단).
- **실행**
  - 사용자의 "지금 동기화" 버튼, 그리고 앱 시작 후 마지막 완결 수집이 6시간 넘었을 때 1회.
  - 한 번에 하나만 돈다(`CanvasSync` 스레드 + 잠금). 쓰기는 기존 Writer로 한다.
- **LTI 강의자료(KNU)는 수집하지 않는다.** UI에 "직접 업로드" 안내를 둔다(§12).

### D4. 첨부 → 로컬 blob → intake

- **적격성**(레거시 규칙)
  - 파일명 ≤ 200자, 제어·서식 문자 없음.
  - 확장자별 content-type 허용목록: v2 intake가 받는 `.pdf`·`.txt`·`.md`만. 그 밖은 `INELIGIBLE(UNSUPPORTED_TYPE)`로 기록하고 내려받지 않는다.
  - 잠기지 않음.
  - 크기 ≤ intake `max_bytes`.
- **다운로드**: `http.download(expected_size=)`. 같은 원점이거나 사용자가 설정한 `file_hosts` 정확 일치만 허용하며, 그 호스트로는 토큰을 보내지 않는다.
- **적재**
  - `LocalIntake.receive(..., channel="CANVAS", provider="canvas", course_hint=연결된 course_key)`.
  - `receive`에 `provider` 인자를 추가한다(`ingest_new`가 이미 받음).
- **중복 처리**
  - 같은 바이트는 intake의 `DUPLICATE` 규칙을 따른다.
  - 같은 `canvas_file_id`에 `canvas_updated_at`이 바뀌고 바이트도 다르면, 기존 자료가 있을 때 `replace()`로 버전 체인을 만든다(§4.4, 사용자 확인 없이 SOURCE 갱신; USER·AI 산출물은 신선도 규칙으로 stale 처리 — I5).
- 첨부는 자동 확정 조건(§11.2)을 그대로 따른다. 과목이 연결되어 있고 종류 분류가 확실할 때만 자동이다.

### D5. 녹음 달력 → 전사문 자동 확정

- 과목 연결이 있고 완결 수집으로 투영된 `recording_calendar`가 있으면, `classify()`가 달력을 넘긴다(현재 `calendar=None`).
- 일치하는 날짜의 세션을 찾거나 만들고(번호는 기존 규칙), 불일치나 충돌이면 `NEEDS_REVIEW`로 둔다(기존 `CALENDAR_UNMATCHED`·`NOTE_CANVAS_WEEK_CONFLICT`).

### D6. 과제와 `SET_SUBMISSION_STATUS` 게이트

**게이트 결함 수정**

- `SUBMISSION_LABELS`를 스키마 4값(`NOT_SUBMITTED`, `SUBMITTED`, `LATE_SUBMITTED`, `EXCUSED`)에 맞춘다.
- 지문은 `activity_id|submission_status|submission_gate_id|title|due_at`이다(`updated_at` 대신 대상 표시에 쓰이는 값 전체).
- 벡터 픽스처를 갱신한다. 이는 C-19 벡터 변경이므로 E32에 기록한다.

**게이트 연결**

- `begin`의 params는 `{activity_id, status}`(열거형만)이다. `WIRED`에 `SET_SUBMISSION_STATUS`를 추가한다.
- `commit_gate`가 `activities`를 갱신하도록 대상 종류를 `activity`로 일반화한다.
  - 지금은 `verify` 콜백과 material 고정 → 액션별 적용 함수 표.
- C-04/05/17을 활동에 대해 활성화한다(`test_v2_pending_contracts.py`에서 PENDING 해제).

**UI**

- "과제" 화면: 과목별 마감 순서, Canvas 원본 표시, 상태 칩.
- "제출 완료로 표시" 등은 게이트 `begin`을 호출하고 네이티브 다이얼로그로 확인한다(R2 경로 재사용).

**`get_activity_context`**(E13): `RetrievalService`에 읽기 전용 연산을 추가한다.

- 범위: 과제 + 같은 과목·주차의 자료 근거.
- 과제 정책 필터는 fail-closed다(`test_pd_assignment_filter` 재작성).

### D7. HTTP 표면 (§10.3 표 채움)

| 메서드·경로 | 요구 | 설명 |
|---|---|---|
| `GET /api/settings/canvas` | 세션 | profile(토큰 없음, `token_set_at`만), 과목 연결, file_hosts, 마지막 수집 |
| `PUT /api/settings/canvas/token` | 세션+CSRF | `{origin, token}`. 검증 → 키체인 → DB. 응답에 토큰·사용자 외 정보 없음. 본문 상한 4 KiB |
| `DELETE /api/settings/canvas` | 세션+CSRF | 잊기 |
| `PUT /api/settings/canvas/file-hosts` | 세션+CSRF | 정확한 호스트명 목록(검증: 공인 DNS 이름 규칙, ≤ 4) |
| `GET /api/canvas/courses` | 세션 | 발견된 Canvas 과목(토큰으로 읽음, 1회/10초 제한) |
| `PUT /api/canvas/courses/{canvas_course_id}` | 세션+CSRF | 로컬 과목 연결/해제 |
| `POST /api/canvas/sync` | 세션+CSRF | 동기화 시작(진행 중이면 409) |
| `GET /api/canvas/status` | 세션 | 진행/마지막 결과(고정 코드) |
| `GET /api/activities` | 세션 | 과제 목록 |

`POST /api/gate/begin`은 `SET_SUBMISSION_STATUS`를 받는다.

**없는 것(MUST NOT)**

- 토큰을 돌려주는 엔드포인트.
- 제출 상태를 직접 쓰는 엔드포인트.
- Canvas에 쓰는 요청. 클라이언트에 GET 외 메서드가 없다(기존 `CHECK_NOT_READ_ONLY`).

## 3. 불변식과 계약 시험 (구현보다 먼저)

| 불변식 | 시험 (신규 파일) |
|---|---|
| I9 토큰 격리 | `test_v2_canvas_token.py` — 아래 확인 항목 참조 |
| I1/읽기 전용 | `test_v2_canvas_http.py`(기존 `canvas_checks`·`canvas_download` 시험 이동) — GET 외 메서드 없음, 허용목록 밖 경로·질의 거부, 원점 밖 토큰 전송 없음, 리다이렉트 규칙 |
| 완결성(fail-closed) | `test_v2_canvas_collect.py` — 쪽 번호 위반·상한·중복 id면 INCOMPLETE; INCOMPLETE는 기존 관측·마감·달력을 바꾸지 않음; 완결 수집에서 사라진 항목만 비활성 |
| I2 소유 | 동기화가 `submission_status`·USER 노트·사용자 분류를 바꾸지 않음; Canvas 제목 변경은 SOURCE 열만 |
| I3 | 첨부 PARTIAL은 READY가 되지 않음(기존 intake 시험이 Canvas 채널로도 통과) |
| I4 (C-04/05/17 활동) | `test_v2_gate.py` 확장 — 게이트 없는 상태 변경 거부, nonce 재사용·다른 창·지문 변경 거부, 4값 요약 |
| 첨부 | `test_v2_canvas_attachments.py` — 적격성, 정확 크기, sha256, `provider=canvas`/`channel=CANVAS`, 같은 파일 갱신 → 버전 체인 |
| 녹음 달력 | `test_v2_recording_calendar.py` — 완결 투영만 반영, 날짜 일치 시 자동 확정, 불일치는 NEEDS_REVIEW |
| 활동 맥락 | `test_v2_activity_context.py` — 읽기 전용, 정책 필터 fail-closed |

`test_v2_canvas_token.py` 확인 항목:

- DB 전체 덤프, 로그 파일, 모든 응답 본문·헤더, 예외 `str/repr`에 토큰 문자열이 없다.
- 실패 시 키체인 항목이 정리된다.
- 고아 항목은 시작 시 정리된다.
- 허용되지 않은 백엔드는 거부된다.

실제 Canvas 호출 시험은 없다(가짜 transport/resolver만). Linux CI는 메모리 키체인 백엔드를 쓴다.

## 4. 슬라이스 (각각 PR → 독립 검토 → 병합)

| 슬라이스 | 범위 | 완료 기준 |
|---|---|---|
| R3-1 | `canvas/http.py`(이동) + `canvas/credential.py` + 마이그레이션 004(프로필·과목 연결만) + 설정 엔드포인트 + 설정 UI(토큰 입력·잊기·과목 연결) + 게이트 라벨 결함 수정 | I9·읽기 전용 시험 통과 |
| R3-2 | 수집기 + `canvas_items`/`canvas_collections` + 활동 upsert + 동기화 트리거·상태 UI | 완결성·I2 시험 |
| R3-3 | 첨부 적재 + 녹음 달력 투영 + 전사문 자동 확정 | 첨부·달력 시험 |
| R3-4 | 과제 화면 + `SET_SUBMISSION_STATUS` 게이트 + `get_activity_context` | C-04/05/17(활동) 활성 |
| R3-5 | 레거시 삭제(`settings/`, 자격증명 스택, `syllva setup`, `knu_lms` 스크립트) + 처분표·정오표 E32 + 기준선 갱신 | import-linter·정적 불변식 유지, 전체 시험 |

**R3 완료**

- 위 시험 전부 통과, 3 OS CI 그린.
- **사용자의 실제 학기 데이터 확인**(실제 KNU Canvas 토큰으로 연결 → 동기화 → 과제·첨부 확인). 이는 사람 확인 항목이다.

## 5. 위험과 열린 질문

1. **키체인 백엔드 정책**: macOS/Windows 외에 Linux `SecretService`를 개발용으로 허용할지. 허용 시 잠긴 키링에서 대화 상자가 뜰 수 있다.
2. **`file_hosts`**: KNU Canvas 파일 저장소 호스트를 사용자가 직접 넣어야 한다. 비우면 같은 원점 다운로드만 된다. 기본값을 두지 않는 것이 안전하다.
3. **레거시 스택 삭제 범위**: `mcp/transports/remote.py`(R6 보류)가 `settings/`를 참조하는지 R3-5에서 확인한다. 참조하면 그 부분만 남기거나 함께 보류 처리한다.
4. **제출 여부(`include[]=submission`)**: 허용목록 확장이 필요하다. 사용자 본인의 제출 정보만 반환되지만, 이번 범위에서는 뺀다.
5. **동기화 주기**: 앱이 열려 있는 동안만 돈다. 백그라운드 서비스는 R6 설치본 단계다.

## 6. rev 2 — 계획 검토 반영 (규범)

### 6.1 D1 자격증명 (필수 1)

**DB 쪽 의도 행**

- 키체인 API에는 열거가 없으므로 "고아 항목 열거 삭제"는 불가능하다. 대신 DB에 의도 행을 둔다. `canvas_profile`에 `token_state ∈ {PENDING, SET}` 열을 둔다.
- 저장 순서:
  1. 메모리 토큰으로 `verify_canvas_user`를 실행한다.
  2. DB에 `token_state='PENDING'` 행을 커밋한다. 교체이면 기존 행을 PENDING으로 바꾼다.
  3. 키체인에 쓰고 정확히 다시 읽어 확인한다.
  4. DB `token_state='SET'`, `token_set_at`을 커밋한다.
- 실패 처리: 3단계에서 실패하면 키체인 항목과 행을 지운다(교체였다면 연결 해제 상태가 된다).
- 시작 시 `PENDING` 행이 있으면 해당 키체인 항목과 행을 지우고, 설정 화면에 "토큰 다시 입력 필요"를 표시한다.
- 잊기 순서: 행을 PENDING으로 바꾼다 → 키체인을 삭제한다 → 행을 삭제한다. 중간에 충돌이 나도 시작 시 정리된다.

**키체인 백엔드 (`config/_keyring_backend.py`의 불변식을 `canvas/credential.py`로 옮긴다)**

- **명시 클래스 생성**: `keyring.get_keyring()`을 쓰지 않는다. `PYTHON_KEYRING_BACKEND`나 `keyringrc.cfg`를 통해 `keyrings.alt` 평문 백엔드로 갈 수 있기 때문이다.
- macOS 대체 키체인을 차단한다(`backend.keychain = None` 후 재확인).
- 쓰기 후 정확히 다시 읽는다. 빈 값은 거부한다.
- Linux `SecretService`는 Core 환경변수 `SYLLVA_DEV_KEYRING=secretservice`일 때만 허용한다. Shell 환경 allowlist에 없으므로 앱에서는 켜지지 않는다.

**I9**

- 토큰 보유자는 `core.secret.Secret`과 같은 `__repr__` 규칙을 따르는 `CanvasToken`이다. `test_secret_reveal_has_two_callers` 고정은 의도적으로 갱신한다.
- 본문 상한 4 KiB는 `Boundary._limit`이 경로별로 강제한다(업로드 경로와 같은 방식).
- Canvas 라우트는 예외 메시지를 응답에 넣지 않는다. 키체인 예외는 `KEYCHAIN_*` 고정 코드, `CanvasCheckError`는 그 코드만 쓴다.
- `canvas_items.attachment_json`과 모든 저장 JSON에서 Canvas `url`·`preview_url`·`verifier`를 제거한다. 다운로드 검증값이 들어 있기 때문이며, 시험으로 단언한다.
- UI 토큰 입력란은 `autocomplete=off`이고 제출 후 비운다.

### 6.2 D2 스키마 (필수 5)

**마이그레이션은 슬라이스별로 나눈다**

| 파일 | 슬라이스 | 내용 |
|---|---|---|
| `004_canvas_profile.sql` | R3-1 | `canvas_profile`, `canvas_courses` |
| `005_canvas_collect.sql` | R3-2 | `canvas_collections`, `canvas_items`, `activities` 열 추가, `activities_no_delete` |
| `006_canvas_files_calendar.sql` | R3-3 | `canvas_files`, `recording_calendar` |

**쓰기 권한**

- Writer 열 권한(ownership)에 규칙을 추가한다: `CANVAS_COLLECTOR`는 `origin='CANVAS'` 행의 `activities.{title, due_at, canvas_updated_at, updated_at}`만 UPDATE하고, INSERT는 `submission_status='NOT_SUBMITTED'`로만 한다.
- `activities_no_delete` 트리거를 둔다. `human_gate_uses`가 문자열로 참조하므로 삭제는 감사 기록을 고아로 만든다.

**수명·한도**

- 완결 수집에서 사라진 과제는 활동 행을 유지하고 `canvas_items.active=0`이 된다. UI에 "Canvas에서 사라짐"을 표시하며, 삭제하지 않는다.
- 활동 id는 `<CODE>-A<NN>`이다. 과목당 99개를 넘으면 그 과제는 `canvas_items`에만 남고 수집 결과에 `ID_SPACE_EXHAUSTED`를 기록한다. 수집 자체는 COMPLETE이다.
- 004~006은 마이그레이션 단계에서 적용된다(E7: 실행 중 DDL 금지 authorizer 이전).

### 6.3 D3 수집 (필수 2)

**달력 장벽 우선**

1. 수집 시작 시 **자체 트랜잭션**으로 해당 과목 녹음 달력을 닫고, `canvas_collections` 행(`finished_at` NULL)을 만든다.
2. COMPLETE일 때만 한 트랜잭션에서 항목·활동을 반영하고 달력을 다시 투영한다.
3. INCOMPLETE/FAILED이면 collections 행 외에 이번 실행이 쓴 것은 없다. 부분 upsert를 하지 않는다.

- 시작 시 `finished_at IS NULL`인 행은 `FAILED(INTERRUPTED)`이고, 달력은 닫힌 채로 둔다.
- **주체 확인**(선택 항목 채택): 동기화마다 `users/self`의 `id`가 profile의 `user_id`와 같아야 한다. 다르면 `FAILED(PRINCIPAL_CHANGED)`이고 달력은 닫힌 채로 둔다.

**INCOMPLETE 사유(레거시 `_pages/_collect/_plain/_link_values` 전부)**

`test_v2_canvas_collect.py`가 사유마다 시험한다.

| 사유 | 조건 |
|---|---|
| `MALFORMED_BODY` | JSON이 아님, 또는 목록이 아님 |
| `LINK_MALFORMED` | Link 필드가 비었거나 해석 불가 |
| `LINK_UNRECOGNIZED` | `rel=next`로 이해되지 않는 next 표기 |
| `PAGE_SEQUENCE` | next가 `page=n+1`이 아님 |
| `LAST_WITHOUT_NEXT` | `rel=last`가 현재 쪽보다 뒤인데 next가 없음 |
| `PAGINATION_SCOPE` | scheme·netloc·path, `page`를 뺀 query가 바뀜 |
| `FULL_PAGE_PROBE` | 가득 찬 쪽에 next가 없을 때 n+1 탐침 결과가 비어 있지 않음 |
| `PAGE_LIMIT` / `ITEM_LIMIT` / `TIME_LIMIT` | 상한 초과 |
| `DUPLICATE_ID` | 같은 id가 두 번 나옴 |
| `MALFORMED_ITEM` | id·title 형식 오류, 모듈 id가 10진수가 아님 |
| `MODULE_ITEMS_INCOMPLETE` | `items_count`가 `items` 길이와 다름 → `/modules/{id}/items` 재조회 → 여전히 다름 |

### 6.4 D4 첨부 (필수 3)

**과목 범위 File 재조회**

- 다운로드 전에 `/api/v1/courses/{cid}/files/{fid}`를 다시 읽는다.
- id·`display_name`·`size`가 목록과 일치해야 한다.
- 다운로드 URL과 `expected_size`는 이 메타데이터에서만 가져온다. `len(data) == size`를 확인한다.
- 이 재조회가 "이 파일이 연결된 과목에 속한다"는 증명이다.

**자동 교체**

- 수집기 전용 경로 `LocalIntake.replace_from_source(..., actor=CANVAS_COLLECTOR)`를 쓴다. `Actor.UI_USER`로 상태를 바꾸지 않는다(C-03).
- **검증된 자료(`material_usage.verified=1`)는 자동 교체하지 않는다.** 새 후보를 `NEEDS_REVIEW`로 두고 차단 사유 `REPLACES_VERIFIED`를 붙인다. 사용자가 확인하면 기존 사용자 교체 경로를 탄다.
- 검증되지 않은 자료만 자동으로 버전 체인을 만든다.
- I5: 새 원본 버전에 맞춰 파생물·AI 산출물은 지문 불일치로 stale이 된다.

**`file_hosts` 입력 검증**(선택 항목 채택): IP 리터럴, `localhost`, 점이 없는 이름, 포트 표기는 거부한다. 사용 시점의 공인 IP 확인은 기존대로다.

### 6.5 D6 게이트 (필수 4)

**`begin` 검증**

- `params = {activity_id, status}`이다.
- `status`가 4값 열거형이 아니면 `400 BAD_REQUEST`다(`summarize`에서 KeyError가 나지 않음).
- 현재 상태와 같으면 `409 ALREADY_DONE`이다.

**`commit_gate` 일반화의 고정 조건**

- 가장 바깥의 `BEGIN IMMEDIATE`에서 `verify`(액션별 `_recheck`)를 먼저 실행한다.
- 게이트 로그 INSERT와 대상 UPDATE는 같은 트랜잭션이다. 대상은 일반 UPDATE이며 `rowcount == 0`이면 `TARGET_CHANGED`다(upsert 금지, E9).
- 새 상태는 **nonce 기록의 params에서만** 가져온다(confirm 메시지에서 받지 않음).
- 액션↔`target_kind` 표 밖의 조합은 `PolicyViolation`이다.

**활동에 대해 활성화할 시험**

- C-04/05/17.
- DB 트리거 쪽(`GATE_REQUIRED`/`GATE_REUSED`/`GATE_MISMATCH`).

**다이얼로그 표시(§6.3(3))**

- `gate.view`에 활동 필드를 추가한다: `target_label` = 과제 제목, `due_at`, `current_status` → `new_status`.
- `source_ids`/`source_hash_prefixes`는 VERIFY_MATERIAL 전용이며, 활동에서는 빈 목록이다.
- 요약 문장에 마감일을 넣는다(선택 항목 채택). 요약 벡터 픽스처를 확장한다.

### 6.6 정오표 E32와 §15 R3 완료 기준 (필수 6)

E32에 기록할 것:

- §1.1 `config/` 행의 "유지" → R3-5 삭제로 변경한다. 키체인 불변식은 `canvas/credential.py`로 옮긴다.
- `settings/` 삭제(E23 연장).
- 처분표 23행의 새 파일 대응.
- 게이트 라벨·지문 수정.
- 마이그레이션 분할.

§15 "R3 — Canvas·과제"의 빈 완료 기준은 다음으로 채운다.

- C-04/05/17(활동)이 활성화되고 통과한다.
- I9 토큰 시험이 통과한다.
- 수집 완결성·첨부·녹음 달력·활동 맥락 시험이 통과한다.
- 처분표 R3 행이 모두 새 파일로 대응되거나 폐기된다(근거 포함).
- 3 OS CI가 그린이다.
- **사용자의 실제 학기 데이터 확인**: 실제 Canvas 토큰으로 연결 → 동기화 → 과제·첨부·녹음 달력을 확인한다. 사람 확인 항목이다.
