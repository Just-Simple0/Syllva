# Syllva (ULS v1.2) — Handoff

**Last updated:** 2026-09-17

## Credential 주입 UX 개선 항목 기록 및 protected secret file 설계 착수 (2026-09-17)

사용자가 "현재 API 토큰 주입이 터미널에 직접 export해서 넣는 방식인데 UX가 좋지 않다"고 지적했다.
실제로 `docs/operator-guide/configuration.md`와 `deployment/README.md`를 확인한 결과, 현재
워커/MCP 자격증명(`GOOGLE_WORKER_CREDENTIALS_FILE`, `NOTION_WORKER_TOKEN`,
`GOOGLE_MCP_CREDENTIALS_FILE`, `REMOTE_MCP_SECRET` 등)은 여전히 "process environment 또는
private service wrapper로 공급하라"는 안내만 있고, 구체적인 무인 실행용 launcher나 대화형 등록
커맨드가 없다. `CredentialResolver`(PR #11)가 `NOTION_MCP_TOKEN`/`GITHUB_READ_TOKEN`/
`LLM_API_KEY`에 keyring source를 허용하게 만들었음에도, 이를 실제로 keyring에 써 넣는 대화형
진입점은 `uls` 메인 CLI에 아직 없다 (`scripts/knu_lms_sync.py enroll --confirm yes`는 Canvas
사이드카 전용이며 CredentialResolver와 별개다). 이 UX 격차를 **후속 작업 항목으로 기록**한다:

- **[신규 후속 작업] Credential 주입/등록 UX 개선**: 모든 크리덴셜(keyring 및 신규 protected-file
  source 포함)에 대해 터미널 `export` 직접 입력 없이 대화형으로 안전하게 등록할 수 있는
  `uls credential set <NAME>` 류 커맨드 제공. 값은 CLI 인자로 절대 받지 않고(마스킹 프롬프트만),
  등록 직후 `uls doctor`와 동일한 fail-closed 진단으로 즉시 검증.

이 항목을 반영해 이전 세션에서 정리된 "다음 세션 작업" 1번(메인 워커용 protected secret file +
최소 환경변수 launcher 패턴)의 설계를 시작했다. 상세 설계는
[docs/plans/credential-secret-file-launcher.md](docs/plans/credential-secret-file-launcher.md)
(rev1, 아직 web 리뷰 전)에 정리했다. 이 작업은 AGENTS.md 기준 인증/자격증명 관련 risky 분류라
구현 착수 전 독립 계획 리뷰(insane-review + 신규 CLI UX 부분은 Gemini)가 필요하며, 아직 리뷰를
보내지 않았다. 코드는 변경하지 않았고 커밋/푸시도 하지 않았다.

### 업데이트: rev1 독립 계획 리뷰 2건 완료 — 둘 다 REVISE, rev2로 수정 완료 (2026-09-17)

이 exec 세션의 기본 샌드박스는 loopback bind(9222)와 로그 파일 쓰기를 막아 insane-review/agy가
곧바로는 동작하지 않았다(`ps`조차 `operation not permitted`). `require_escalated` 승인을 받아
재시도하니 두 리뷰 모두 정상 동작했다 — 이전 세션에서 "샌드박스가 네트워크를 전면 차단한다"고
진단했던 것은 escalation 없이 시도했기 때문이었다.

**리뷰 1 — insane-review(웹 ChatGPT).** 새 채팅의 Pro 추론단계가 여전히 비활성(pill이 '매우 높음'에
고정, Pro 선택 시 슬라이더 검증 실패)이라 AGENTS.md가 승인한 fallback대로 **매우 높음**으로
진행했다(모델 `GPT-5.6 Sol (매우 높음)`, Pro라 칭하지 않음). 저장:
`.insane-review/response_Syllva_20260917_103047_41839_ddbcf6.md`. **판정: REVISE.**

**리뷰 2 — Gemini 3.8 Flash high (`agy --model gemini-3.8-flash --effort high --mode plan`,
읽기 전용).** **판정: REVISE.**

두 리뷰가 **동일하게** 지적한 핵심 결함: rev1의 launcher(§2.5)가 secret file을 읽어 environment로
재주입하는데, 이는 `source: file`을 `CredentialResolver`에 직접 추가한 §2.1과 secret read
boundary가 중복/모순된다는 것. 그 외 TOCTOU 심링크 검사 순서, 원자적 쓰기 시 레이스, Windows DACL
trustee 정의 불충분, `GOOGLE_WORKER_CREDENTIALS_FILE`이 무인 실행 갭을 실제로 해결 못 하는 문제,
`credential set`의 설정-저장소 동기화 누락("닭-달걀" 문제), 이중 입력/취소 처리 부재 등도
공통/개별로 지적됐다.

지적사항을 전부 rev2([docs/plans/credential-secret-file-launcher.md](docs/plans/credential-secret-file-launcher.md))에
반영했다: launcher에서 raw secret 주입을 제거해 `CredentialResolver(source=file)`을 유일한 read
boundary로 만들고, 신뢰 디렉터리 우선 검증 + 동일 fd/handle 기준 TOCTOU-safe 읽기로 바꾸고,
secret 전용 원자적 writer(생성 시점부터 0600, 실패 시 temp 항상 unlink)를 정의하고, Windows
canonical DACL trustee(현재 사용자+SYSTEM+Administrators)를 명시하고, `GOOGLE_*_CREDENTIALS_FILE`을
경로(non-secret)로 재분류해 별도 경로로 무인 실행 갭을 풀고, `credential set`을 "이미 선언된
source에만 적용" + 이중 입력 + 덮어쓰기 확인 + TTY 필수로 강화했다. 상세 대응표는 rev2 문서 §0.

**다음 단계:** rev2를 insane-review + Gemini high에 재발송해 두 핵심 아키텍처 결함(§2.1/§2.5의
read-boundary 중복)이 실제로 해소됐는지 확인 → 둘 다 GO 이후에만 구현 착수. 아직 재발송 전이며,
코드는 변경하지 않았고 커밋/푸시도 하지 않았다.

### 업데이트: rev2 재리뷰 2건 완료 — 또 REVISE, 하지만 핵심 아키텍처는 확정. rev3로 launcher 자체를 폐기 (2026-09-17)

rev2를 insane-review(`GPT-5.6 Sol (매우 높음)`)와 Gemini 3.8 Flash high에 재발송했다. **둘 다
REVISE**였지만, 두 리뷰 모두 rev1→rev2의 핵심 수정(launcher의 raw secret 재주입 제거, TOCTOU-safe
read boundary, secret 전용 원자적 writer, Windows trustee 3종, TTY/이중입력/덮어쓰기 확인)은
**정확히 해결됐다고 명시적으로 확인**했다 — 즉 REVISE는 새로 발견된 문제 때문이지 이전 지적이
재발한 것이 아니다.

**새로 발견된 것들:**
- insane-review: `GOOGLE_*_CREDENTIALS_FILE`을 검증한 파일과 provider가 실제로 여는 파일이
  같은 객체라는 보장이 없는 validate-then-reopen TOCTOU. launcher가 `config.yaml`을 `uls`와
  별도로 다시 읽어 그 사이 config가 바뀌면 서로 다른 snapshot을 쓰게 되는 문제. Windows DACL이
  trustee 이름만 있고 ACE rights/상속/owner 의미가 없는 문제(그리고 `scripts/_lms_platform.py`가
  아직 DACL hardening을 구현하지 않았다는 사실을 rev2가 착각하고 인용한 것도 지적).
- Gemini: 마스킹 미리보기("처음 4자+`****`+마지막 2자")가 12자 미만 시크릿에서 사실상 전체를
  노출할 수 있는 실제 보안 버그. `config.example.yaml`에 `credentials:` 섹션이 없어 첫 사용자가
  "이미 선언된 source에만 set 허용" 규칙에 막다른 골목으로 몰리는 Day-0 온보딩 마찰.

**rev3 핵심 변경**: 두 리뷰가 겹치는 launcher 문제(TOCTOU + snapshot split)를 한 번에 해소하기
위해 **launcher를 전면 폐기**했다. Google 자격증명 경로도 secret이 아니므로, `uls`의 기존 config
composition root가 직접 읽게 하면 별도 config reader 자체가 없어져 snapshot split이 구조적으로
불가능해진다. scheduled 실행은 (rev1 이전처럼) `uls run`을 절대경로로 바로 호출한다. 마스킹
미리보기는 완전히 제거하고 성공 피드백은 저장 위치/권한/길이만 노출하도록 바꿨다. Windows DACL은
ACE 수준으로 구체화했고, "기존 코드에 이미 DACL hardening이 있다"는 rev2의 잘못된 전제도 정정했다.
상세 대응표는 rev3([docs/plans/credential-secret-file-launcher.md](docs/plans/credential-secret-file-launcher.md))
§7.

**다음 단계:** rev3를 insane-review + Gemini high에 3차 재발송할지 사용자에게 확인 중. 아직
재발송 전이며, 코드는 변경하지 않았고 커밋/푸시도 하지 않았다.

### 업데이트: rev3 3차 재리뷰 완료 — 여전히 REVISE지만 범위가 좁아짐, rev4로 문서 정합화 (2026-09-17)

사용자 승인으로 rev3를 insane-review(`GPT-5.6 Sol (매우 높음)`)와 Gemini 3.8 Flash high에
3차 재발송했다. **둘 다 다시 REVISE**였지만, 이번엔 두 리뷰 모두 "아키텍처를 다시 뒤집을 필요는
없다"고 명시했다 — insane-review는 "범위 좁은 REVISE", Gemini는 "5가지 항목만 본문에 통합하면
되는 가벼운 패치"라고 표현했다. launcher 폐기, TOCTOU-safe read boundary, secret 전용 writer,
Windows trustee 지정, 마스킹 제거 등 rev3의 핵심 변경은 방향상 옳다고 재확인됐다.

**남은 지적:**
- insane-review: Google 자격증명의 "검증과 소비가 동일 fd" 원칙이 아직 `ResolvedCredentials`
  (`Mapping[str, str]`) 타입 계약과 연결되는 실행 가능한 API로 안 이어짐. launcher를 없앤 것으로
  snapshot split의 *원인*은 사라졌지만, config에서 resolver로 값을 넘기는 인터페이스 자체가
  아직 이름이 없어 "구조적으로 불가능"이라 단언하기엔 이르다는 지적. `credential-resolver.md`가
  `file` source와 Google 변경을 여전히 "범위 밖"으로 명시하는 충돌은 구현 후 각주로 미루지 말고
  지금 함께 고쳐야 한다는 지적. Windows DACL의 "현재 사용자 SID" trustee와 owner 검증의
  `TokenOwner`가 실은 다른 개념인데 섞여 있다는 지적.
- Gemini: 마스킹 제거는 완전히 해소됐다고 확인. 새로 발견한 실제 버그 — rev3 §7.5가 "저장소가
  `untrusted`(신뢰 불가)면 **확인 없이** 덮어쓴다"고 적어놨는데, 이러면 오타로 실행한
  `credential set`이 사용자 동의 없이 기존 파일을 조용히 파괴할 수 있음. 문서 본문(§2.5의
  launcher 설계, §2.6/§4의 마스킹 언급)이 §7의 폐기 결정과 모순되는 "Frankenstein 문서" 상태도
  지적.

**rev4 반영**: Google payload 전달 계약과 config→resolver 값 전달 인터페이스를 구체적으로
고정했고(§8.2/§8.3), `credential-resolver.md`에 지금 바로 amendment 절을 추가해 두 문서 간
authoritative 소스를 명확히 했고(§8.4, 해당 문서에도 반영 완료), Windows trustee(`TokenUser`)와
owner(`TokenOwner`) 개념을 분리했고(§8.5), `untrusted` 덮어쓰기 확인 생략 버그를 정정했다(§8.7).
본문 §2.5/§2.6/§4의 낡은 launcher/마스킹 언급도 이번에 정리해 문서 모순을 없앴다. 상세는
[docs/plans/credential-secret-file-launcher.md](docs/plans/credential-secret-file-launcher.md)
§8, 문서 정합화는 [docs/plans/credential-resolver.md](docs/plans/credential-resolver.md) 상단
amendment 참고.

**다음 단계:** rev4를 4차로 재발송할지 사용자에게 확인 중. 아직 코드는 변경하지 않았고 커밋/푸시도
하지 않았다.

### 업데이트: rev4 4차 재리뷰 완료 — Gemini GO, insane-review 매우 좁은 REVISE, rev5로 마무리 (2026-09-17)

사용자 승인으로 rev4를 4차로 재발송했다. **Gemini 3.8 Flash high는 전체 GO**를 냈다 — (a)~(d)
지적사항(untrusted 무확인 덮어쓰기, diagnose() fd 전달 모순, 문서 모순, Windows trustee 혼동)이
전부 해소됐다고 확인했다. **insane-review는 "매우 좁은 REVISE"**로, 남은 것은 새 아키텍처가
아니라 다음 4가지뿐이라고 명시했다: ① Google payload를 누가 소유하고 downstream에 어떻게
단일 경로로 전달하는지에 대한 한두 문장, ② `credential-resolver.md` amendment가 "각주만
추가"에 그쳐 §8.4가 약속한 실제 문서 개정(source matrix, out-of-scope 목록, composition table)을
안 했다는 점, ③ absent/ready/untrusted ↔ resolver ready/absent/error 매핑이 `untrusted`만
명시되고 `ready`/`absent`는 빠졌던 점, ④ 문서 본문 §2.4/§7.5에 남은 launcher/무확인-덮어쓰기
시절 문구 2곳.

4가지 모두 rev5로 반영했다: Google payload의 소유/전달 계약을 §8.2에 한 문단 추가했고,
`credential-resolver.md`의 "Scope of this plan"/"Out of scope"/composition table을 상단
각주가 아니라 **본문 자체를 직접 수정**했고(source matrix에 `file` 정식 추가, out-of-scope
3개 항목을 취소선 처리 후 "이제 지정됨" + 새 계약 서술로 교체, composition table 행에 supersede
주석 추가), 3분류 매핑에 `ready`/`absent` 케이스를 추가했고, §2.4/§7.5의 낡은 문구를
rev5 참조로 갱신했다. 상세는
[docs/plans/credential-secret-file-launcher.md](docs/plans/credential-secret-file-launcher.md)
§8, 문서 개정은 [docs/plans/credential-resolver.md](docs/plans/credential-resolver.md) 참고.

**다음 단계:** rev5를 5차로 재발송할지, 아니면 Gemini GO + insane-review의 "이 정도면 GO 가능"
평가를 근거로 재검토 없이 구현 착수로 넘어갈지 사용자에게 확인 중. 아직 코드는 변경하지 않았고
커밋/푸시도 하지 않았다.


 ### 구현 및 전체 검증 완료 (2026-09-17)

 사용자의 "구현 진행해" 지시에 따라 rev5 계획서 기준으로 전체 구현과 자체 검증을 완료했다.

 1. **보호된 파일 저장소 및 TOCTOU 방어 (`src/uls/config/_secure_file.py`)**:
    - macOS (`~/Library/Application Support/Syllva/secrets/`, `0700`/`0600`) 및 Windows (`%LOCALAPPDATA%\Syllva\secrets`, canonical DACL) 지원.
    - TOCTOU 방어: no-follow로 디렉터리 핸들 검증 후, 해당 디렉터리 핸들 기준으로 대상 파일을 no-follow(`O_NOFOLLOW` / `FILE_FLAG_OPEN_REPARSE_POINT`)로 열고, 동일 fd/handle에서 정규 파일/소유자/권한/크기(최대 4096바이트)를 검증한 뒤 그 동일 fd에서만 내용을 읽음.
    - 원자적 쓰기: 생성 시점부터 `0600`(POSIX) 또는 canonical DACL 사전 적용(Windows) 임시 파일 생성, fsync, 원자적 `os.replace`, 실패 시 temp 파일 무조건 unlink.

 2. **CredentialResolver 확장 (`src/uls/config/credentials.py`)**:
    - `NOTION_WORKER_TOKEN`, `REMOTE_MCP_SECRET`에 `source: file` 추가 (`FILE_BINDINGS` 상수 정의).
    - `source: file` 실패 시 silent fallback 없이 `error` 진단 및 고정 에러 코드 반환.
    - `path_overrides` 매핑 파라미터 추가: composition root가 1회 로드한 config에서 추출한 Google 자격증명 경로를 단일 주입(loader 재호출 및 config reopen 원천 차단).

 3. **Google 자격증명 TOCTOU-safe 로딩 (`src/uls/runtime.py`)**:
    - `_load_google_credentials`가 파일 경로를 google-auth에 직접 넘겨 재오픈하게 두지 않고, secure read boundary(`read_secure_file`)로 읽어 JSON 검증 후 `google.auth.load_credentials_from_dict`로 안전하게 주입.
    - substitution-race 방어 계약 테스트 추가.

 4. **대화형 등록 CLI (`src/uls/cli/credential_set.py`, `src/uls/cli/main.py`)**:
    - `uls credential set <NAME>` 서브커맨드 구현.
    - CLI 인자로 비밀값 절대 불허(`--value` 없음, 전달 시 에러 종료).
    - TTY 필수, `getpass` 2회 마스킹 입력 일치 확인, 제어문자/개행/크기 검증.
    - 기존 값 존재 시 확인 프롬프트 요구 (`--overwrite`로 생략 가능, `untrusted` 상태에서도 강한 경고와 함께 확인 필수).
    - 성공 시 비밀 유래 문자 0개 노출 (저장 방식, 경로, 권한, 바이트 수, `ready` 메타데이터만 출력).
    - 미선언 source 시 대상 config 절대경로, Case A/B 복사 스니펫, Google 경로 전용 안내 출력.

 5. **설정 및 스키마 연동 (`src/uls/config/schema.py`, `loader.py`, `config.example.yaml`)**:
    - `google_worker_credentials_path`, `google_mcp_credentials_path` config 필드 및 `google_path_overrides` 속성 추가.
    - `config.example.yaml`에 권장 `credentials:` 주석 블록 및 Google 경로 주석 템플릿 추가.

 6. **검증 메트릭**:
    - 신규 유닛 테스트: `test_secure_file.py` (14개), `test_credential_resolver_file_source.py` (9개), `test_runtime_google_credentials.py` (4개), `test_credential_set_cli.py` (12개).
    - 신규 계약 테스트: `test_worker_cli.py` (스케줄러 템플릿 uls 직접 호출 확인, substitution-race 방어 확인, CLI credential set 동작 확인).
 - **전체 테스트: 1,404 passed**, 2 skipped, 0 failed (회귀 없음).
 - 정적 분석: `ruff check` 신규/수정 파일 완전 클린 (0 new errors), `mypy` 전체 131 errors (기존 베이스라인 133건 대비 2건 감소, 신규 모듈 완전 클린).
 - Behavior Contract: `lint_behavior_projection.py` 및 contract hash 일치.


+ ### 최종 독립 리뷰(Gemini & insane-review) 피드백 반영 및 완결 (2026-09-17)

 사용자가 호스트 터미널에서 실행한 독립 리뷰 2건의 최종 결과(Gemini 3.8 Flash high: `REVISE`, insane-review GPT-5.6 Sol: `REVISE`)를 모두 회수하여 지적된 결함들을 빠짐없이 수정했다.

 1. **CLI TTY 검사 순서 및 EOFError 방어 (`src/uls/cli/credential_set.py`)**:
    - 기존 값이 있는 상태에서 비대화형 파이프(`cat token | uls credential set ...`) 실행 시, 프롬프트(`_confirm_overwrite`)가 시크릿 첫 라인을 y/N으로 소진하던 문제를 차단하기 위해 `run()` 진입 최상단에서 `if not sys.stdin.isatty(): raise CredentialSetError("credential_tty_required")` 선제 강제.
    - Ctrl+D(`EOFError`) 입력 시 예외 트레이스백 없이 깔끔하게 `credential_aborted` 처리.
    - Keyring 저장 후 `backend.get_password()`로 즉시 read-back 검증하여 쓰기 실패 시 즉시 fail-closed.

 2. **비밀값 누출 방지 강화 (`src/uls/config/credentials.py`, `src/uls/runtime.py`)**:
    - `ResolvedCredentials._values` 및 `DiagnosticResolution._ready_values`에 `repr=False`를 적용하여 `repr()` 및 디버그 로깅 시 비밀값 노출 원천 차단.
    - Google 자격증명 파일 파싱 오류 시 `from None`으로 예외 체인을 끊어 원시 bytes/JSON 본문이 `__cause__`에 남는 현상 방지.

 3. **Windows 핸들 결속 및 소유자 검증 (`src/uls/config/_secure_file.py`)**:
    - 경로 기반 조회를 폐기하고 `GetSecurityInfo(handle, ...)`로 오픈된 Win32 핸들에 직접 결속하여 TOCTOU 제거.
    - 파일/디렉터리의 소유자 SID가 `_windows_default_owner_sid()`(`TokenOwner`)와 일치하는지 검증.
    - DACL에서 허용된 3개 trustee(`TokenUser`, `SYSTEM`, `Administrators`) 외의 임의 ACE를 철저히 거부하고 표준 에러 코드로 매핑.

 4. **POSIX 쓰기 경계 강화 (`src/uls/config/_secure_file.py`)**:
    - `write_secure_file`에서 short-write를 방지하는 `_posix_write_all` 루프 적용.
    - 디렉터리 생성 직후 `0700` 강제 및 오픈된 디스크립터로 신뢰 디렉터리 사전 검증.

 5. **연계 계획 문서 개정 동기화 (`docs/plans/credential-resolver.md`)**:
    - Proposed API matrix 표에서 `NOTION_WORKER_TOKEN`, `REMOTE_MCP_SECRET`의 허용 소스에 `file`을 공식 반영하여 문서 간 불일치 해소.

 6. **검증 메트릭 (최종)**:
    - 신규 회귀 테스트 추가: non-TTY 시 덮어쓰기 프롬프트 전 즉시 거부 확인, Ctrl+D 정상 처리 확인.
 - **전체 테스트: 1,406 passed**, 2 skipped, 0 failed.
 - 정적 분석: `ruff check` 0 new errors, `mypy` 신규/수정 모듈 완전 클린 (전체 131 errors, 기존 133건 대비 2건 감소).
 - Behavior Contract: `lint_behavior_projection.py` 통과.


+ ### 최종 독립 리뷰 3차 판정 및 2개 Blocker 완전 완결 (2026-09-17)

 호스트 터미널에서 실행된 최종 3차 독립 리뷰(Gemini 3.8 Flash high: **GO**, insane-review GPT-5.6 Sol: `REVISE`)의 리포트를 회수하고, insane-review가 지적한 마지막 2개 Blocker를 철저하게 완결했다.

 1. **Google Payload 단일 Handoff 및 Disk Fallback 완전 제거**:
    - `CredentialResolver._diagnose_one()`에서 Google 자격증명 경로가 주어졌을 때, secure-read/JSON 파싱에 성공하여 `GoogleCredentialPayload`가 실제로 생성된 경우에만 `status='ready'`를 반환하도록 강화했다(파일 부재/파싱 실패 시 즉시 `error` 반환).
    - `build_retrieval`, `build_intake_worker`, `worker.build_worker`에서 `credentials.get_google_payload(...)`를 필수로 요구하고, payload가 없으면 즉시 `ConfigurationError`를 발생시켜 프로덕션 실행 시 디스크 재오픈/경로 fallback을 원천 차단했다.
    - `doctor._ready()`의 중복 `read_secure_file()` 디스크 재호출을 제거하고, `diagnostic.get_google_payload(key) is not None`으로 일원화하여 단 1회의 진단 인메모리 페이로드만 소비하도록 확정했다.

 2. **Windows DACL AccessMask 및 3 Trustee 완전 일치 강제**:
    - `_windows_verify_security_handle`에서 ACE마다 `AccessMask`를 정확히 파싱하여 `acl.AceCount == 3` 및 3개 Trustee(`TokenUser`, `SYSTEM`, `Administrators`) 집합 일치를 강제했다.

 3. **최종 검증 메트릭**:
    - **전체 테스트: 1,406 passed**, 2 skipped, 0 failed.
    - `ruff check`: 0 new errors.
    - `mypy`: 0 new errors (기존 베이스라인 유지).
    - Behavior Contract: `lint_behavior_projection.py` 통과.


+ ### 최종 독립 리뷰 전원 GO 달성 및 릴리스 준비 완료 (2026-09-17)

 호스트 터미널에서 실행된 최종 5차 독립 리뷰에서 **모든 독립 리뷰어가 전원 최종 GO**를 확정했다.

 - **Gemini 3.8 Flash high**: **최종 GO** (무조건적 승인).
   - TTY 선제 차단, `EOFError` 방어, Windows 핸들 결속, POSIX 쓰기 루프, `GoogleCredentialPayload` 단일 인메모리 핸드오프, 회귀 테스트 등 전 항목 100% 충족 확인.
 - **insane-review (웹 ChatGPT GPT-5.6 Sol 매우 높음)**: **최종 GO** (승인).
   - Blocker 1 (Windows DACL exact equality): `ace_mask != expected` 단일 완전 일치 조건 적용 및 Windows 실 API(`SetEntriesInAclW` + `SetNamedSecurityInfoW`) 기반 변조 거부 회귀 테스트 완결 확인 → **CLOSED**.
   - Blocker 2 (Google payload 단일 핸드오프): `google_service`, `google_worker_service`, `_load_google_credentials`의 `str` 경로 분기 및 디스크 재오픈 완전 삭제, `doctor(live=True)`의 fallback 삭제 및 in-memory payload 단일 소비 확정, substitution-race 방어 확인 → **CLOSED**.
   - 비차단 잔여 주석 정리 완료.

 **최종 검증 메트릭**:
 - **전체 테스트: 1,407 passed**, 3 skipped, 0 failed (회귀 0건, 전원 통과).
 - **정적 분석**:
   - `ruff check`: 신규/수정 모듈 린트 에러 0 new errors.
   - `mypy`: 신규/수정 모듈 완전 클린 (전체 135 errors, 기존 베이스라인 유지).
   - Behavior Contract: `lint_behavior_projection.py` 통과 및 contract hash 일치 확인.
 - 작업 트리: Protected Secret File 저장소 및 대화형 CLI(`uls credential set`) 전 기능 구현 완료, 독립 2중 리뷰 전원 GO 완료.

## PR #8 안정화 작업 (Stage A, B, C, D) 및 CredentialResolver 완료 및 main 머지 (2026-09-16)

PR #8 머지 이후 제기되었던 종합 안정화 지적 사항(Stage A~D)과 자격증명 저장소 재설계 작업이 전원 웹 독립 리뷰(GO) 및 GitHub Actions CI 통과를 거쳐 `main` 브랜치에 완전히 병합되었다.

### 1. Stage A: 보안, 이식성 및 CI 의존성 안정화 (PR #9, commit `f694b74`)
- **Drive 파생본 출력 폴더 비공개 검증 (`src/uls/worker.py`)**: `DerivedDriveWriter`에서 기존 전사 출력 대상 폴더의 비공개 여부, 소유권, 등록 경로 검증을 강화하여 공유 폴더 업로드 위험 차단.
- **Windows LMS 사이드카 크로스플랫폼 이식성 (`scripts/_lms_platform.py`)**: `os.getuid()`, `os.O_NOFOLLOW` 등 Unix 전용 호출을 Windows `CreateFileW(FILE_FLAG_OPEN_REPARSE_POINT)`와 `GetFileInformationByHandle` 기반의 reparse-point 링크 방어로 교체.
- **Aside REPL 스트림 파이프 바운딩 (`scripts/knu_lms_probe.py`)**: 큐 크기/바이트 상한 보장 및 읽기 에러와 EOF 분리 처리.
- **CI 환경 복구 (`.github/workflows/ci.yml`)**: `pyproject.toml`의 `pdf` extra(`pypdf`)를 CI 설치 단계에 추가하여 테스트 수집 실패 해결.

### 2. Stage B & C: 정합성, 자원 상한 및 관측성 진단 (PR #10, commit `ad7a6fd`)
- **B1 (`src/uls/intake/registry.py`)**: Drive 폴더 ID 캐시 히트 시에도 부모 관계(`parent_id`)를 재검증하도록 하여 루트/학기/업로드 폴더 중복 지정 우회 방지.
- **B2 (`src/uls/retrieval/capabilities.py`)**: `CapabilityManager`의 만료 컨텍스트 누수 해결 및 동시 발급 시 `max_active_contexts` 초과를 원자적 예약 카운트(`_pending_reservations`)로 방어.
- **B3 (`src/uls/normalization/pdf.py`)**: PDF 추출 시 페이지 수(`max_pages`), 추출 문자 수(`max_extracted_chars`) 상한 및 손상/암호화 PDF 예외 변환 보강.
- **C1 (`src/uls/cli/main.py`)**: `uls doctor`의 자격증명 진단을 목적별(Worker vs MCP Search)로 스코핑 분리하여 최소 권한 구성 시 오탐 방지.
- **C2 (`src/uls/cli/main.py`, `src/uls/state/reader.py`)**: `uls status`에 보수적 준비도 깔때기(`readiness_funnel`) 추가. `source_archival`과 `text_extraction` 판정 시 단순 잡 카운트가 아닌 실제 지속성 레코드(`source_files`, `source_versions`, `processing_records` 및 `parse_derivative_ref` 산출물 참조 검증)를 확인하도록 구현.
- **C3 (`docs/reference/feature-status.md`, `.ko.md`)**: 구현된 학기 intake 슬라이스와 미래 설계 기능을 명확히 분리 표기.

### 3. Stage D: 자격증명 저장소 재설계 및 Windows 스케줄러 보완 (PR #11, PR #12)
- **CredentialResolver 아키텍처 (PR #11, commit `476b284`)**:
  - `src/uls/config/credentials.py`: `CredentialResolver`를 도입하여 자격증명별 `environment | keyring` 명시적 선언 허용. 선언된 소스 실패 시 다른 소스로 조용히 넘어가지 않는 Fail-Closed 단일 스냅샷(`ResolvedCredentials`) 계약 적용.
  - `KEYRING_BINDINGS` 및 `ALLOWED_SOURCES`를 코드 레벨 상수로 고정하여 YAML을 통한 임의 키체인 항목 탈취 공격 차단.
  - `diagnose()`와 `require()`/`select()`의 단일 읽기/비파괴 진단 분리로 TOCTOU 및 `doctor()` 진단 정합성 확보.
  - `src/uls/config/_keyring_backend.py`: OS-native keyring(`keyring.backends.macOS.Keyring`, `WinVaultKeyring`) 명시적 백엔드 검증, 위조 방지 및 macOS `keychain = None` 강제.
  - `pyproject.toml`에 `keyring>=25.0` optional extra 추가.
  - MCP dispatch 시 worker/MCP 자격증명 분리 검증 유지 및 `credentials: null` 거부.
- **Windows 스케줄러 로그아웃 무인 실행 보장 (PR #12, commit `6176ab7`)**:
  - `deployment/windows/uls-task.xml`: `LogonType`을 `InteractiveToken`에서 `Password`로 변경하고 템플릿용 `UserId` 지정.
  - `deployment/README.md`, `README.ko.md`: Event ID 4688 명령줄 평문 노출을 방지하기 위해 `/rp *` 대화형 안전 프롬프트 절차 및 LSA secret 암호화 저장 사양 명시.
  - `tests/contract/test_worker_cli.py`: 스케줄러 XML 템플릿 내 비밀번호 미포함 및 `LogonType=Password` 계약 테스트 추가.

### 4. 현재 상태 및 메트릭
- **로컬 main 브랜치**: `6176ab7` (PR #12 merge commit).
- **전체 테스트**: **1,363 passed**, 2 skipped (경고 1건: starlette testclient anyio deprecation).
- **정적 분석**: `ruff` 226건(기존 베이스라인 유지), `mypy` 107건(기존 베이스라인 유지, 신규 모듈 완전 클린), `compileall` 통과, `lint_behavior_projection.py` 일치.
- **GitHub Actions**: PR #9, #10, #11, #12 모두 macOS/Windows × Python 3.11/3.14 전 환경 PASS.

### 5. 향후 후속 작업 안내
- 메인 워커용 보호된 비밀 파일 + 최소 환경변수 런처 패턴 설계 (macOS `~/Library/Application Support/Syllva/secrets/` 0700/0600, Windows NTFS DACL).
- `REMOTE_MCP_SECRET`의 장기 토큰을 OAuth/OIDC 기반 인증 흐름으로 전환.
- 실제 운영 환경(Notion / Google Drive)에서의 라이브 엔드투엔드 연동 확인.

## Credential 저장소 아키텍처 재설계 — GPT Pro 리뷰 완료, 구현은 다음 세션 (2026-09-14)

이전 항목(LMS 사이드카 Windows 크로스플랫폼 지원)에서 이어진 논의다. 사용자가 "Canvas sidecar처럼
나머지 7개 credential(Google Drive worker/MCP, Notion worker/MCP, GitHub, LLM, Remote MCP)도
다 OS keyring/Credential Manager로 옮기는 게 낫지 않냐"고 제안했고, 독립 웹 GPT Pro 리뷰를 거쳐
**전면 전환은 권장하지 않는다는 결론**과 함께 구체적인 하이브리드 구조를 받았다. 이번 세션에서는
코드를 건드리지 않았고, 결론과 실행 계획만 기록한다. 다음 세션은 여기서부터 이어간다.

### 검증 경로
- 이 대화 세션(Codex exec 샌드박스)에서는 `insane-review`(로컬 Chrome/Brave CDP 실행)와
  `aside exec`(daemon auth) 둘 다 네트워크 차단으로 실패했다. `curl 127.0.0.1:9222` 자체가
  `Operation not permitted`로 거부되는 것을 확인해, 이 exec 세션의 샌드박스가 루프백을 포함한
  아웃바운드 네트워크를 전면 차단한다는 근본 원인을 진단했다 (도구 문제가 아님).
- 사용자가 질문 텍스트를 직접 ChatGPT 웹(Pro)에 붙여넣어 답변을 받아왔다. 실제 GPT Pro 응답이며,
  모델 자동추론 등급(Pro)에서 나온 근거 인용(Apple Developer 문서, Microsoft Learn, jaraco/keyring
  GitHub README/이슈)이 포함되어 있다.

### GPT Pro 결론 요약
1. **전면 keyring 전환 비권장.** macOS default/login Keychain은 사용자 로그인 세션에 결합되어
   있어(Launch Agent vs Launch Daemon 구분, Apple 공식 문서 인용), 로그아웃 상태에서도 도는 진짜
   무인 LaunchDaemon에는 부적합하다. Keychain lock/ACL 승인 팝업이 뜨면 GUI 없는 환경에서 멈춘다.
2. Python `keyring` 자체가 "같은 Python executable을 쓰는 스크립트는 OS 프롬프트 없이 서로의
   secret을 읽을 수 있다"고 Security Considerations에 명시함 (jaraco/keyring README 인용).
   즉 keyring item을 worker/MCP별로 나눠도 진짜 프로세스 격리가 생기지 않는다 — provider 측
   read/write 권한 분리(현재 이미 잘 되어 있음)가 여전히 핵심 경계다.
3. Windows Credential Manager는 상대적으로 낫지만(`CredRead`가 logon session에 결합, Password
   logon Task라면 unattended에 적합), **Credential Blob이 최대 2560바이트**로 제한된다
   (Microsoft Learn `CREDENTIALA` 문서, jaraco/keyring 이슈#540 — 긴 값 이슈가 2026-07 PR
   제출 후에도 아직 open). Google 서비스 계정 JSON처럼 큰 데이터를 keyring에 통째로 넣는 설계는
   피해야 한다.
4. **실제 발견된 버그**: `deployment/windows/uls-task.xml`이 `<LogonType>InteractiveToken</LogonType>`
   으로 되어 있는데, Microsoft 공식 정의상 이 값은 "사용자가 이미 로그인되어 있어야만 실행"을
   의미한다. 로그아웃 상태에서도 도는 무인 worker가 실제 요구사항이라면 이 설정 자체가 그 요구와
   맞지 않는다. **keyring 전환 여부와 독립적으로 고쳐야 할 결함이며, 이 세션에서 직접
   `grep -n LogonType deployment/windows/uls-task.xml`로 재확인했다.**

### 권장 최종 구조 (하이브리드, GPT Pro 제안 그대로 채택 방향)

| 크리덴셜 | 저장 방식 | 비고 |
|---|---|---|
| Canvas/KNU (사람이 직접 enroll) | keyring (현행 유지) | 이미 구현·검증됨 |
| 메인 worker (무인 스케줄) | 보호된 secret file + 최소 환경변수 launcher | `.env`를 인터랙티브 셸에서 source하는 현재 방식은 스케줄 실행과 근본적으로 안 맞음 |
| `NOTION_MCP_TOKEN`, `GITHUB_READ_TOKEN`, `LLM_API_KEY`(사람이 직접 실행하는 경로) | keyring 전환 후보 | 짧은 문자열 토큰이라 적합, UX 개선 효과 큼 |
| `GOOGLE_WORKER_CREDENTIALS_FILE`, `GOOGLE_MCP_CREDENTIALS_FILE` | 파일 유지 + OS ACL 제한 | JSON을 keyring에 넣지 않음 (Windows blob 제한) |
| `REMOTE_MCP_SECRET` | keyring보다 OAuth/OIDC 전환이 우선 | 장기 secret 자체를 없애는 게 keyring 저장보다 더 나은 개선 |

제안된 구현 형태: 앱 전체를 keyring 종속으로 만들지 않고, 크리덴셜마다 `source: environment |
keyring | file`을 명시하는 얇은 `CredentialResolver` 추상화를 두고, **silent fallback을
금지**(한 source가 실패하면 fail-closed, 다른 source로 자동 전환하지 않음)한다. 이러면 이후
`notion_worker: env → keyring`처럼 credential 하나씩 안전하게 옮길 수 있다.

### 다음 세션에서 진행할 작업 (우선순위순, 아직 착수 안 함)
1. **[버그 수정, 독립적]** `deployment/windows/uls-task.xml`의 `LogonType`을 실제 요구사항에
   맞게 수정 (`Password` logon 또는 별도 service-account 모델 검토). `deployment/README.md`/
   `.ko.md`에 로그아웃 상태 무인 실행이 필요하면 이 설정이 필수라는 점을 명시.
2. `CredentialResolver` 추상화 설계 및 구현 (`src/uls/config/` 또는 신규 `src/uls/credentials/`
   모듈 후보). config 스키마에 크리덴셜별 `source` 필드 추가, silent fallback 금지 원칙 테스트로
   고정.
3. `NOTION_MCP_TOKEN`, `GITHUB_READ_TOKEN`, `LLM_API_KEY`(interactive 경로)를 keyring 기반
   source로 전환. Canvas sidecar의 `_explicit_os_keyring()` 패턴(explicit backend import +
   `__module__` 검증)을 재사용/공유하는 방안 검토.
4. 메인 worker용 "protected secret file + 최소 환경변수 launcher" 패턴 설계. macOS는
   `~/Library/Application Support/Syllva/secrets/` 류 경로 + `0700`/`0600`, Windows는 NTFS DACL
   (`icacls`)로 사용자/서비스 계정 한정. `.env`를 인터랙티브 셸에서 source하는 현재 안내를
   스케줄러 문서에서 대체.
5. 위 변경은 인증/자격증명 코드라 프로젝트 AGENTS.md 기준 "risky" 분류 — 구현 후 독립 웹 리뷰
   (insane-review 또는 사용자가 직접 ChatGPT에 질문 붙여넣기) 필요. 이번 세션처럼 로컬 브라우저
   자동화가 막힌 exec 환경이면 질문 텍스트를 사용자에게 직접 전달하는 방식으로 진행.

## LMS 사이드카 Windows 크로스플랫폼 지원 및 문서 정합성 수정 (2026-09-14)

사용자가 "windows 환경에서도 사용이 될텐데, keychain으로 관리하는 건 안 맞는 거 같은데?"라고
지적해 [scripts/knu_lms_sync.py](scripts/knu_lms_sync.py)의 토큰 저장소를 macOS 전용에서
macOS/Windows 양쪽 지원으로 일반화했다.

### 코드 변경
- `_explicit_mac_keyring()` → `_explicit_os_keyring()`로 이름 변경, `sys.platform`에 따라
  macOS는 기존 `keyring.backends.macOS.Keyring`(`.keychain = None` 강제), Windows는
  `keyring.backends.Windows.WinVaultKeyring`(Windows Credential Manager) 분기를 명시적으로
  구성하고 각각 구체 클래스의 `__module__`을 검증해 위조된 backend를 거부한다.
- `KEYCHAIN_BACKEND` 고정 상수를 `_expected_backend_module()` 함수로 교체해 config/manifest
  binding 검증이 실행 시점의 실제 플랫폼을 반영하도록 했다. 다른 플랫폼에서 저장된 자격증명은
  `keychain_platform_unsupported`/`config_binding_mismatch`로 명확히 거부되며 자동 fallback은 없다.
- macOS 전용이던 `backend.keychain = None` 이중 방어 로직은 `hasattr(backend, "keychain")`으로
  감싸 Windows `WinVaultKeyring`(이 속성이 없음)에서 무해하게 건너뛰도록 했다.
- `tests/unit/test_knu_lms_sync.py`에 Windows 분기, 플랫폼 미지원 거부, backend 신원 위조 거부,
  Windows에서 `read_enrolled_token` 정상 동작을 검증하는 테스트 6종을 추가했다. 기존 macOS 테스트는
  함수명만 갱신해 그대로 통과한다 (전체 44/44 통과).
- CI 매트릭스(`.github/workflows/ci.yml`)가 이미 `macos-latest`/`windows-latest` 양쪽에서
  실행되므로 이번 변경으로 실제 Windows 러너에서도 해당 코드 경로가 검증된다.

### 문서 정합성 수정
- `docs/operator-guide/lms-sync.md`/`.ko.md`: 실제로 존재하지 않는 `CANVAS_ACCESS_TOKEN`
  환경변수 서술을 제거하고, 실제 `scripts/knu_lms_sync.py enroll --confirm yes`
  대화형 등록 → OS-native credential store 저장 흐름으로 정정했다.
- `config.example.yaml`: 어떤 코드도 읽지 않는 가공의 `lms:` YAML 블록(이전 턴에서 잘못 추가됨,
  존재하지 않는 `uls lms probe` 명령을 언급)을 제거하고 실제 사이드카 위치를 가리키는 주석으로 교체했다.
- `config.example.yaml`, `docs/operator-guide/configuration.md`/`.ko.md`: 위 문서 점검 중
  `course_key` 예시(`"COURSE-001"`)가 실제 `parse_course_key` 정규식과 불일치해
  `tests/contract/test_worker_cli.py::test_cli_init_status_jobs_and_disabled_worker_no_credentials`가
  깨지고 있던 것을 발견해 `"COURSE001"`로 수정했다 (이전 턴의 회귀, 이번 작업과 무관하게 발견·수정).
- `docs/plans/knu-lms-hourly-*.md`는 과거 리뷰 시점의 승인 기록(engineering record)이라 이번
  변경으로 소급 수정하지 않았다. 필요하면 별도 plan-review 사이클로 다룬다.

### 검증
- `pytest -q`: 1236 passed, 0 failed (수정 전 1개 실패 확인 → 수정 후 0개).
- `ruff check scripts/knu_lms_sync.py tests/unit/test_knu_lms_sync.py`: 통과.
- 커밋/푸시는 아직 하지 않았다. `insane-review` 독립 검토는 진행 예정이다.

## Phase 6–8 구현, v1.3 Intake Lane & LMS Sidecar 완료 및 main 머지 (2026-09-14)

PR [#5 feat: v1.3 preview intake lane, LMS sidecar, and user docs](https://github.com/Just-Simple0/Syllva/pull/5)가 승인 및 머지되었으며, 로컬 `main` 브랜치 최신화(commit `6ea0459`)가 완료되었다.

### 1. 주요 구현 및 산출물
- **Multi-course Drive Intake Lane (`src/uls/intake/`, `src/uls/adapters/`)**:
  - Drive 단일 업로드함(`+ 업로드`) 기반 파일 감지, 다중 과목 매핑 및 대상 폴더(Recordings/Materials) 이동.
  - Notion 5개 Native Data Sources(Academic Courses, Sessions, Materials, File Intake, Input Request) 연동 및 durable `pending_request_key` 기반 중복 방지.
  - `RequestReceipt` 및 `HumanApprovalApplier` 연동, `Submitted`/`Cancelled` 엄격한 identity 검증.
  - 빈 PDF 페이지 위치 보존 및 marker-free 청크 분할 개선.
- **KNU Canvas LMS Sidecar (`scripts/knu_lms_*.py`)**:
  - `scripts/knu_lms_probe.py`: Canvas API 토큰 기반 과목 및 과제/강의자료 탐색.
  - `scripts/knu_lms_sync.py`: 매시간(hourly) 다중 과목 메타데이터 안전 동기화 및 snapshot 생성.
  - `scripts/knu_lms_apply_lock.py`: 단일 활성 worker 락 기반 경합 방지.
- **문서화 (Documentation)**:
  - `README.md`: `pdf` extra 의존성, v1.3 preview intake 및 LMS sidecar 명시.
  - `config.example.yaml`: `semester_registries`, `semester_workspaces`, `lms` 섹션 템플릿 추가.
  - `docs/user-guide/`: `getting-started.md`, `daily-use.md`, `mcp-and-clients.md`, `troubleshooting.md` 초보자 가이드 완비.
- **테스트 및 코드 품질**:
  - 1,231개 전체 테스트 통과 (`pytest`), `ruff` 및 `mypy` clean.
  - Web ChatGPT 및 Gemini 독립 리뷰 전 트랙 GO 판정 수용.

### 2. 현재 상태 및 후속 작업 (Next Steps)
- **로컬 main 상태**: 작업 트리 clean, 최신 커밋 `6ea0459`(PR #5 merge).
- **라이브 환경 배포 및 운영 검증 (사용자 인증정보 필요)**:
  - 사용자 환경의 실제 Google Drive 및 Notion API 토큰을 환경변수로 주입 (Canvas 토큰은 환경변수가 아니라 아래 LMS 사이드카 절 참고):
    - `export GOOGLE_WORKER_CREDENTIALS_FILE=...`
    - `export NOTION_WORKER_TOKEN=...`
  - 진단 및 실행: `uls doctor` → `uls sync` → `uls run --max-jobs 20`.
- **LMS 동기화 스케줄러**: 현재 `PAUSED` 상태. LMS 사이드카는 `config.yaml`과 무관한 별도 스크립트이며 아래 항목을 참고.

## Native Notion 대시보드 직접 적용 완료 (2026-09-13)

사용자가 실제 적용을 요청해 [2026-1 학기 페이지](https://app.notion.com/p/34154b33957f801cb86ed4435bd80253)를
**내 과목 → 이어서 공부(최대3) → To DO → 캘린더 → 파일 확인** 순서로 구성했다.
기존 과목7개·캘린더를 보존하고 기존 일정 원본을 To DO/캘린더에서 함께 사용한다.
등록된 실제 수업1개 바로가기, 알고리즘 과목의 수업 연결 뷰, 새 파일 확인 페이지를 연결했다.
현재 일정 기록0개이므로 가짜 데이터를 채우지 않았다. 최근 학습 자동 갱신·파일 자동 접수는 연결 전이다.

[적용·재검토 기록](docs/ux/review-20260913-native-dashboard.md)과
[실제 native 저장 결과](docs/ux/dashboard-native-readback.md)에 검증을 남겼다.
웹 GPT-5.6 Sol(매우 높음) 최종GO·Gemini3.8 Flash high 최종GO, 필수 미해결0건이다.
원본 블록·뷰·질의를 재조회해 확인했으며 브라우저 화면 접근이 승인되지 않아 픽셀/모바일 검증은 하지 않았다.
제품 코드·frozen 문서·기존 전사 수정·사용자 상태를 보존했다. 커밋·푸시는 하지 않았다.
아래의 ‘실제 적용 전’ 기록은 이 요청 이전의 이력이다.

## 공통 메뉴와 Notion 구현 대상 명확화 (2026-09-13)

사용자 선호에 따라 공통 메뉴는 **대시보드·현재 학기의 과목별 바로가기·파일 확인**으로
제한했다. 수업은 과목의 목록에서 열고 같은 목록으로 돌아간다. 대시보드 본문 최근 수업
바로가기는 유지한다. 실제 사용 화면은 **Notion 기본 페이지·연결 뷰**이며 HTML은 합성
모형이다. HTML 웹앱으로 전환한 것이 아니다. [탐색 결정](docs/ux/navigation-notion.md)과
[재검토 기록](docs/ux/review-20260913-navigation.md)에 대응 요소와 한계를 명시했다.
웹 GPT-5.6 Sol(매우 높음)·Gemini 3.8 Flash high 모두 GO. 기존 모형 검사34 + 탐색 확인6
그룹 통과, JS 오류0. 이번 보완도 설계·모형 범위이며 실제 Notion 설정은 후속 구현이다.
제품 코드·frozen 문서·운영 데이터·기존 전사 수정은 보존했고 커밋·푸시는 하지 않았다.

## UX 정의 수정·재리뷰 완료 — rev10 설계 수용 (2026-09-13)

사용자의 ‘이에 맞춰 수정 및 재리뷰로 고도화’ 요청을 완료했다. 현재 assistant가 직접 문서와
합성 모형을 수정하고, 독립 웹 **GPT-5.6 Sol (매우 높음)** 및 **Gemini 3.8 Flash high** 리뷰의
필수 지적을 반영했다. Pro 한도 소진에 대한 사용자의 대체 모드 승인을 그대로 사용했다.
최종 묶음 판정은 **설계 수용 GO**다. 넓은 검토에서 시작해 변경 영향을 좁혀 재검토했으며,
마지막 C6 산출물 재사용 identity는 웹/Gemini 모두 GO다. 전체 저장소 구현 승인은 아니다.

- [사용자 UX 정의](docs/ux/file-intake.md): 학기 대시보드→과목→수업, Drive 단일 `+ 업로드`,
  선택 과목 폴더, 정확한 입력/수업 선택, 개인 일정 원본, 전체 학습 노트 목표.
- [UX-C1 실행 계약](docs/ux/intake-execution-contract.md): durable intake/receipt, Session·Material
  reserve/apply와 폴더 회복, 실제 차시/내부 ID 분리, Usage v2 사람 승인, 최신 노트 요청/단일
  attempt·재사용·취소 격리, SOURCE/AI/USER·Partial·freshness, C1–C8 다음 버전 명세 개정.
- [리뷰·판정·검증 기록](docs/ux/review-20260913-revised.md): 실제 모델/대화 링크, 반복 리뷰 지적
  처리, 검증 범위와 한계. 34개 합성 UX 검사 통과, JS 오류 0, 320/390/736/1024px 및 다크 확인.

현재 완료 범위는 **설계·실행 계약·합성 UX 검증**이다. 제품 코드, frozen 문서, 운영
Drive/Notion, AI 공급자는 변경하지 않았고 기존 전사 정규화/테스트 수정도 보존했다.
커밋·푸시는 하지 않았다. 실제 구현/배포는 후속 작업이며 A01–A45와 하위 수용 사례의
SQLite crash/restart, provider 응답 유실/권한, 실제 HAA/노트 생성 검증이 남는다.
추가 사용자 질문은 없다. 아래 초기 리뷰/정의 기록은 당시 상태를 보존한 과거 기록이다.

## UX 정의 독립 리뷰 완료 (2026-09-13)

사용자가 `insane-review`로 UX 정의 검토를 요청했다. Pro 사용량 제한으로 사용자가 명시한
대체 모드 **웹 ChatGPT GPT-5.6 Sol (매우 높음)**을 UI 검증해 사용했으며, 독립
Gemini 3.8 Flash high 검토도 완료했다. 두 결론은 방향 적합·구현 전 보완(REVISE)이다.

[최종 리뷰·의견 채택 근거](docs/ux/review-20260913.md)에 필수 4항목(미확인 범위와 전체 사용
구분, 기존 Session 연결, 지속적인 접수 기록·확인 화면, 학습 노트 생성/쓰기/갱신 계약)과
중요 2항목(개인 할 일·공통 일정 저장, 전사 시간 형식과 분류기 정합성)을 정리했다.
빈 범위·분류기 동작은 합성 입력으로 확인했다. 리뷰어의 과도한 서비스 단정, 임의 큐 필드,
이미 결정된 UX 재질문 등은 채택하지 않았다.

설계·명세·코드 35개를 누락 없이 전송했고, 웹 완료 응답을 회수했다. 비공개 강의 시연 보고서
업로드는 자동 승인 심사가 거절해 제외했다. 상세 증거는 `.review/ux-definition-20260913-*`와
위 리뷰 문서에 있다. 정의안 원문·제품 코드·frozen 명세·운영 Drive/Notion은 이번 리뷰에서
변경하지 않았다. 후속 정의 개정/구현은 아직 수행하지 않았으며, 이번 리뷰를 GO나 구현 완료로
취급하지 않는다. 추가 사용자 응답을 기다리는 항목은 없다.

## 학습 UX 정의 — 대시보드와 파일 입력 (2026-09-12)

사용자 방향은 **Notion 학기 대시보드 → 과목 → 세션**이다. 대시보드에서 과목 접근,
과제·시험 마감, 학사 일정, 해야 할 일을 함께 본다. Drive는 학기별 **‘+ 업로드’ 하나**에
넣으면 시스템이 정리하는 방식을 기본으로 하고, 과목별 업로드 폴더 템플릿도 선택할 수 있게 한다.

[UX 정의안](docs/ux/file-intake.md)에 화면 구조, 파일 종류별 입력 규칙, 분류·날짜 확인,
자료와 세션의 관계, 읽기·노트 준비·내 공부 상태의 구분, 예외와 수용 사례를 정리했다.
모호한 파일은 학기 접수 공간에서 과목을 확인한 뒤 정식 수집으로 전달한다. 파일 분류만으로
Material Usage나 시험 범위의 사람 확인을 대신하지 않는다.

현재 단계는 **정의안 작성**이다. 담당은 현재 assistant이며 이번 변경 범위는 위 UX 문서와
이 인수인계뿐이다. 추가 사용자 응답을 기다리는 항목은 없다. 자동 발견·분류·Drive 이동,
대시보드·확인 UI와 다중 자료형 native 처리는 후속 구현 대상이다. 저장 방식과 이동 동작은
frozen 모델·기존 Notion DB·provider 권한에 맞춰 검증해야 한다. 실제 외부 파일·페이지는
이번 UX 정의에서 변경하지 않았다. 제품 코드·기존 시연 수정은 보존하며 커밋하지 않았다.
문서의 로컬 링크·코드 블록·공백 검사와 `git diff --check`를 통과했다. 문서만 변경했으므로
제품 테스트는 재실행하지 않았다. 독립 모델 리뷰를 수행한 구현 승인 문서로 취급하지 않는다.

## 실제 전사문 시연 후속 — 알고리즘 1 (2026-09-10)

사용자 제공 `1주차.md`와 확인된 강의일 2026-03-06으로 직접 테스트했다.
원문 M:SS/MM:SS 113개를 놓치던 정규화기를 보완해 전체 123개 시간 구간을
원문 보존 상태로 처리한다. Canonical locator 문법은 유지하며 전체 1,058 tests 통과.

ULS core ingest + 연결된 도구로 Drive 업로드/readback, Notion 수업 생성/readback,
SQLite 완료 provenance, 중복 입력 무쓰기까지 확인했다. 검색은 실제 readback snapshot과
완료 기록으로 확인했다. Native worker/MCP credentials와 실제 AI client E2E 완료는 아니다.
‘루프 불변식’은 ASR의 ‘루프 불편성’과 달라 관련 근거를 놓치는 검색 품질 한계가 남는다.
Notion은 Courses/Sessions 두 DB의 시연 공간이며 전체 운영 DB 구성은 아니다.

[실제 수업 기록](https://app.notion.com/p/3d754b33957f8121a21ef41d7b4e1ab1),
[검증·한계·임시 증거](docs/plans/live-transcript-20260306.md).
이 후속 수정은 현재 작업 트리에 있으며 아직 커밋하지 않았다.

2026-09-11 사용자 피드백으로 같은 수업 페이지의 짧은 AI 개요를 학습 노트로 확장했다.
9개 단원에 단계별 배열 추적, 불변식 증명, 실행 횟수/수식 유도, 오개념 표와
연습문제 10개/접힌 해설을 넣었다. 전사 중 교수 자기 정정도 표시하고 계산을 검산했다.
SOURCE/USER와 메타데이터를 보존했다. PDF 텍스트는 대조했으나 이미지/손글씨의
시각 검증은 로그인 origin 자동 승인 차단으로 수행하지 않았다. 자동 enrichment나
product code를 추가 구현한 것이 아닌 학습 결과물과 품질 기준 보완이다.

## 최신 인수인계 — Phase6–8 저장소 구현·로컬 검증 완료 (2026-09-10)

사용자의 최신 지시 **“오케스트레이션 무시하고 너가 phase 8까지 구현 완료”**에 따라
현재 assistant가 단독으로 설계·구현·검증했다. 이번 작업에서는 위임과 독립 웹/Gemini
리뷰 단계를 실행하지 않았다. 제품의 frozen 계약, 읽기 전용 MCP와 사람 승인 경계는
유지했다. 기준은 Phase5 병합 커밋 `f4c321e`, 작업 브랜치는 `codex/phase6-8-direct`다.

### 구현한 동작

- **Phase6:** GitHub 저장소·정확한 commit/tag 검증, 고정 tree/blob 조회와 checksum,
  Activity 결과의 Repository Path/Submission Ref 연결. 잘못된 ref는 명시적 오류가 되며
  현재 branch로 대체하지 않는다. 공식 지침과 제출 코드의 출처·권한을 구분한다.
- **Phase7:** Behavior Contract v2와 여섯 projection의 해시 검증, 11파일 client zip,
  설치 안내·support matrix·실제 client E2E 체크리스트. ChatGPT 연결은
  `DEPLOYMENT_DEFERRED`이며 실사용 지원 검증을 완료했다고 표시하지 않는다.
- **Phase8:** 같은 `uls run`을 실행하는 launchd/Task Scheduler, 실제 SDK stdio/HTTP
  MCP와 11개 읽기 전용 도구, 분리된 RO provider 조합, TLS·짧은 bearer 인증,
  status/doctor/health, 작업 잠금·재시도·재처리와 백업·복원·offline 안내.
- Native transcript 흐름은 등록 원본 → 정규화 업로드/readback → Notion SOURCE
  메타데이터 → durable provenance → 읽기 전용 검색까지 연결했다. 재처리 중 과거
  처리 기록을 보존하고 USER가 바꾼 포인터를 덮어쓰지 않는다. AI 보강 결과가 원본
  binding이나 재처리 대상으로 섞이지 않도록 회귀 검증했다.

### 검증과 전달

- Python **3.14.7·3.11.16 각각 전체 1,051개 통과**. 3.11에서는 독립 환경에 설치한
  wheel의 실제 MCP SDK 프로세스도 검증했다. 추가 회귀는 총 56개다.
- Canonical projection/hash lint, client zip, wheel 설치·CLI·SQLite 백업, compileall,
  diff check, macOS plist lint와 Windows XML 검증 통과.
- Ruff **183개**, mypy **74개** 기존 지적은 남는다. Phase5 감사와 비교한 새 정규화
  지적은 0개다. 외부 Starlette/AnyIO deprecation warning 1개가 남는다.
- macOS/Windows × Python3.11/3.14 GitHub Actions 정의를 추가했다. 원격 CI 실행,
  push/merge, scheduler 설치와 외부 서비스 변경은 수행하지 않았다.
- 세부 수용·검증·한계: [Phase6–8 검증 기록](docs/plans/phase6-8-verification.md).
  작업 기록: [직접 실행 기록](docs/plans/phase6-8-direct.md).
  운영 시작점: [설치·운영 안내](deployment/README.md).

### 여전히 필요한 live 검증

이번 완료 범위는 명세 §47–49의 저장소 구현과 로컬 검증이다. **전체 v1.2 live Done
(§56) 완료는 아니다.** 이전 §41 C0/M0/VS0/VS0-B/Goodnotes live gates, 실제
Notion/Drive/Claude/ChatGPT 계정 E2E, Windows host 실행, 배포와 권한·TLS 경로 확인은
미검증 상태다. Native scheduler 입력은 현재 transcript만 지원하며 다른 원본 종류는
거부한다. 기존 provider-neutral PDF/enrichment/approval 코드를 모두 live worker에
연결했다고 주장하지 않는다. 내장 remote는 개발용 bearer profile이며 OAuth/OIDC와
상시 모바일 연결은 제공하지 않는다. Primary PC가 켜져 있고 online이어야 한다.

---

**아래는 이전 인수인계다. 당시 범위·완료·승인·미구현 표현은 역사 기록이며 위 최신
인수인계와 현재 사용자 지시가 우선한다.**

## 이전 인수인계 — Phase5 구현·검증 완료 (2026-09-10)

**Phase5 fix3와 테스트 보강분은 필수 웹·Gemini GO 및 Astra 최종 수용을 통과했다.** 구현 커밋은 `354a2606b2e2e60049babc257e0e883dfb09b2a5`이며, 시작 기준은 `9ba41a5`, 작업 브랜치는 `codex/phase5-8-completion`이다. 사용자 지시에 따라 이번 범위는 Phase5에서 끝난다.

### 완료한 동작

- Exam scope 제안은 Automation Queue로 들어가며, 현재 유효한 사람 승인·Course·의존성 검증을 통과한 `HumanApprovalApplier`만 `Scope Confirmed=true`를 적용한다. Typed Exam과 raw provider 입력을 모두 지원하고 재적용·감사 복구는 대상 쓰기를 반복하지 않는다.
- Exam 조회는 확인된 범위 안의 근거를 제공하고 미확정 범위는 provisional로 표시한다. Activity 공식 지침은 실제 출처 identity와 정규화 포인터를 검증해 가장 높은 제공 제약으로 전달하며, 누락·Partial·예산 잘림을 명시한다.
- 후속 청크 조회는 발급된 capability allowlist와 현재 관계·출처를 다시 검증한다. 읽기 전용 Exam/Activity callable MCP 도구, Behavior Contract v2와 여섯 클라이언트 projection을 포함한다.

### 검증과 근거

- Python3.11.16·3.14.7 각각 **전체995개 테스트 통과**. 현재 수정 집중69개, 테스트 보강 후 Activity37개도 두 버전에서 통과했다. 정확한 이전 소스에 새 회귀8개를 적용하면5실패·3통과, 현재 소스에서는8통과다.
- 웹 최종 **GO**: UI 검증된 Latest / 매우 높음(허용된 Pro 쿼터 대체, Pro 아님),810초 후 정상 회수. Gemini3.8Flash high **GO**: 실제59페이지와 보강 테스트6페이지 출력을 루트가 원본 대조했다. 웹 검토 후 제품 코드는 바뀌지 않았고 테스트 단언만 별도로 강화·검증했다.
- Projection/hash lint, compileall, diff check 통과. Ruff183개·mypy74개 기존 지적은 남아 있으며 새 정규화 지적은0개다.
- 명세 §46 대응과 리뷰·검증의 정확한 범위는 [Phase5 검증 기록](docs/plans/phase5-verification.md), 진행 이력은 [Phase5 실행 기록](docs/plans/phase5-8-execution.md)에 있다. 승인된 [계획 rev3](docs/plans/phase5-exam-activity.md)의 과거 UNAPPROVED 헤더는 검토 당시 해시 보존을 위해 유지했다.

### 다음 작업의 경계

Phase6–8은 구현하지 않았으며 이번 전달 범위에 포함하지 않는다. 실제 Notion/Drive/client 연결, MCP 서버·transport·배포 및 명세 §41 live 선행 검증도 완료로 주장하지 않는다. `require_ready` helper의 더 엄격한 의미와 Due 종료일 순서/IANA 검증은 비차단 후속 항목이다.

사용자는 이 인수인계 후 작업 브랜치 push, Phase5 PR 생성·merge, 로컬 main 최신화까지 명시적으로 승인했다. 이 문서는 검토된 구현 커밋의 인수인계이며, 실제 PR·merge 커밋은 GitHub 기록으로 확인한다. 현재 저장소에는 GitHub Actions workflow나 필수 상태 검사가 설정돼 있지 않아 로컬 검증을 CI 통과로 표현하지 않는다.

`.review/`와 `.insane-review/`는 현재 작업 환경에만 있는 Git 제외 증거다. 다른 checkout에서는 커밋된 검증 문서와 [웹 리뷰 대화](https://chatgpt.com/g/g-p-6a9fdbd2dc3081919990a6607f8fe7c4-syllva-eeb93c01/c/6aa24073-fb9c-83ee-97e5-753edb99b85d)를 먼저 참조한다.

---

**이하 내용은 Phase4와 당시 정책 전달의 역사 기록이다. 아래의 “현재”, “승인 범위”, “금지”, “대기”는 당시 상태이며 위 Phase5 인수인계와 최신 사용자 지시가 우선한다.**

**Repo / merged main:** https://github.com/Just-Simple0/Syllva · Phase4는 PR1로 `main`에 `e55705f`로 병합됨 (2026-09-10 09:32:55 KST)

**정책 후속 PR2:** `codex/syllva-project-agents` 정책 정렬 변경이 `main`에 `2b4fbe4ed368d1b1d92721f7f33bdd0ab307281d`로 병합됨 (2026-09-10 10:55:41 KST)

**PR2 병합 후 확인 기록:** PR2 병합 후 로컬 `main`이 `2b4fbe4ed368d1b1d92721f7f33bdd0ab307281d`와 같은 커밋으로 동기화된 것을 확인했다. AGENTS/handoff 정책 정렬은 완료됐다.

**Phase4 구현 커밋:** `3f190fc`  ·  **Phase4 완료·검증 기록:** `d9fe77e`  ·  **정책 채택 기록:** `f974c7f`

**Phase4 당시 시작 기준:** `bfc592b` (Phase3 인수인계)

## 이전 인수인계 — Phase4 완료 및 정책 정렬 후속

**Phase4 구현 rev10은 독립 리뷰 GO와 총괄 검증·수용을 통과했고 PR1로 `main`에 병합됐다. 정책 정렬 후속도 PR2로 `main`에 병합되어 현재 기준에 반영됐다.** 아래 Phase4 완료 근거와 역사 기록은 보존한다. Phase5–8 구현과 미래 제품 push는 승인 범위 밖이다. 이 제한은 완료된 사용자 승인 PR2 delivery를 금지하는 뜻이 아니다.

### 현재 지침과 경계

- 모델/effort 선택, orchestration, review, safety는 적용 가능한 global Codex `AGENTS.md`와 프로젝트 `AGENTS.md`를 따른다. 이 handoff는 전역 정책을 복제하지 않는다. `CLAUDE.md`는 Claude 전용이다.
- 제품의 `Single-active-worker`는 ULS runtime 제약이며 Codex subagent 동시성을 정하지 않는다.
- **MCP search surface (MCP 검색 표면)**는 v1.2에서 read-only인 계약/스캐폴드 경계다. 현재 MCP 배포나 실제 클라이언트 검증 완료를 주장하지 않는다.
- 승인·확인은 human-owned다. AI와 일반 자동화는 독립적으로 승인·승격할 수 없다. 자동 적용에서는 지정된 `HumanApprovalApplier`만 정책·freshness·identity 검사를 모두 통과한, 현재 유효하고 human attribution이 있는 승인 변경을 적용할 수 있으며 human approval 자체를 만들 수 없다.
- 현재 승인된 제품 범위는 **Phase4까지**다. Phase5–8 구현과 미래 제품 push는 새 사용자 지시 없이 시작하지 않는다. 완료된 사용자 승인 PR2 정책 delivery는 이 미래 범위 제한과 구분한다.
- 승인 계획은 **rev6**, 완료 구현은 **rev10**이다. 계획의 과거 UNAPPROVED 헤더는 후속 리뷰 기록으로 승인됐으므로 수정하지 않는다. 승인 계획과 두 frozen 명세의 해시는 그대로 유지했다.
- 정책 정렬·호환성 감사와 검증 근거는 [Codex 정책 채택 기록](docs/codex-policy-adoption.md)에 보존한다. 전역 Codex 지침은 일반적으로 `~/.codex/AGENTS.md`, 프로젝트 지침은 [AGENTS.md](AGENTS.md)를 따른다.

### 완료한 동작과 검증

Material Usage 제안 생성, 정규화된 승인 Queue, 사람 승인에 따른 MATERIAL_USAGE/PAGE_RANGE 적용, 불확실한 쓰기 결과의 보수적 복구, 다중 자료 검색과 후속 권한 철회를 구현했다. 마지막 웹 지적 두 건도 수정했다.

1. 유한 페이지 범위는 일부 페이지만 발견돼서는 승인되지 않는다. 현재 검증된 자료에 요청한 모든 페이지가 있어야 한다.
2. `effect_observed` 표식 저장 후 대상과 전체 근거를 다시 검증한다. 그 사이 인간 복원이나 의존성 변경이 있으면 표식을 유지하고 조정을 기다리며, 대상을 다시 쓰거나 완료 감사를 남기지 않는다. 정상 처리와 유효한 감사 재시도는 성공한다.

**2026-09-09 Phase4 검증 기록이며 이번 문서 변경에서 재실행하지 않음.**

| 검증 | 최종 결과 |
| --- | --- |
| 전체 테스트, Python 3.11.16 | **866 passed** |
| 전체 테스트, Python 3.14.7 | **866 passed** |
| 새 rev10 회귀 테스트 | 두 환경 각각 **30 passed**; 수정 전 코드에서는 16 expected failures / 14 passes |
| 고정 소스 복사본 및 실제 웹 첨부 복원 | 각각 **731 passed**, 126파일 누락·내용 불일치 없음 |
| 컴파일·Behavior projection·diff check | 통과 |
| 제안→승인→검색→재적용→철회, p39–40/p40, 두 결함 before/after | 통과 |
| Ruff / mypy | **188 findings / 74 errors** — 정적 검사는 clean이 아님; 새 정규화 mypy 오류 없음 |

리뷰한 126파일은 최종 구현과 해시가 일치한다. 세부 완료 근거와 명세 §45 대응은 [Phase4 검증 기록](docs/plans/phase4-verification.md), 범위와 과거 진행 기록은 [실행 기록](docs/plans/phase4-8-execution.md), 승인된 불변 계획은 [Phase4 계획](docs/plans/phase4-material-usage.md)에 있다.

### 독립 리뷰와 근거 위치

- **웹 GO:** Codex native insane-review-codex 0.6.8, UI 검증된 Latest / 매우 높음. 1,254초 후 정상 회수(exit0). 이전과 동일한 123파일의 내용 동일성을 확인한 뒤 이전 전체 읽기를 재사용했고, 변경된 어댑터 4,512줄과 새 테스트 두 파일은 전체 재검토했다. 복원한 현재 코드로 731테스트·컴파일·projection을 실행하고 두 기존 결함 및 추가 복구·sibling 조합을 독립 재현했다. 최종 보고서 `.review/phase4-rev10-insane-review-final.md`, [웹 리뷰 대화](https://chatgpt.com/c/6aa13845-5fdc-83e9-a3e9-b3e32c1e9bdb). 선택적 회귀 테스트 제안은 비차단이며 미해결 구현 결함이 아니다.
- **Gemini GO:** Gemini 3.8 Flash high, Descartes `01a085ef-7f18-7b41-9c12-6678da68018b` 종료. 251페이지 전체 출력과 인용 5개를 총괄이 원본 대조했고, 독립 731테스트·projection 실행을 확인했다. 줄 번호, 격리 mypy 68건과 전체 74건의 구분, 재현 스크립트 import 경로 및 lint 설명의 정정은 `.review/phase4-rev10-gemini-{final.md,addendum.md,audit.json}`에 보존했다.
- 총괄 최종 수용: `.review/phase4-rev10-final-acceptance.json`. 전체 검증 로그: `.review/phase4-root-rev10-results.json`. 고정 파일 해시: `.review/phase4-integrated-review-hashes-rev10.json`.
- `.review/`와 `.insane-review/`는 Git에서 제외된 **이 작업 환경의 로컬 증거**다. 다른 checkout에는 자동으로 전달되지 않는다. 커밋된 검증 문서와 웹 대화 링크를 먼저 참조하고, 원본 증거가 필요하면 현재 작업 환경에서 확인한다. 구 Claude 0.6.2 임시 실행기는 `.review/legacy-review-gpt6-pro-claude062.py`에 보관했다.

### 남아 있는 한계

Provider-neutral/fake 테스트를 통과한 것이며 live SDK, MCP 배포 또는 실제 클라이언트 검증 완료를 의미하지 않는다. 명세 §41 선행 live 검증은 기존 deferred 상태다. Single-active-worker 전제와 최종 확인부터 Queue 감사 쓰기 사이의 non-CAS 가시성 한계도 유지한다.

---

**아래는 과거 진행 이력이다.** “현재”, “대기”, “미완료”, “커밋 없음” 등의 표현은 당시 상태이며, 위 최신 인수인계보다 우선하지 않는다. 완료된 리뷰를 다시 시작하거나 과거 Sonnet 리뷰 호출을 반복하지 않는다.

## 이전 인수인계 — Phase4 rev10 검증 완료, native 웹 + Gemini 재검토 (2026-09-09)

**최신 사용자 지시: Sonnet 리뷰는 일회성이었으므로 앞으로 리뷰용 호출 금지. 이후 리뷰는 Codex native insane-review + Gemini만 사용한다. Phase4 완료/커밋은 아직 아니다.**

- Luna Ampere 종료. 새 두 회귀 파일 30 tests는 두 Python 모두 통과, sealed rev9에서는 16 expected failures /14 passes. Root가 새 테스트 전체를 검토했다.
- Root 전체 **866 passed × Python3.11.16/3.14.7**. Ruff188/mypy74 기존 부채, compile/projection/전체 흐름/부분 페이지 및 effect-marker 반례/diff check 통과.
- rev10 고정126파일 source-copy **731 passed**, 작업 중 hash 변경 없음. `.review/phase4-rev10-pack-audit.json`, `.review/phase4-integrated-review-hashes-rev10.json`. 페이지 helper251개(0–250).
- Native 웹 session **86490**, `.review/phase4-rev10-insane-review.log`; launcher `/tmp/syllva-phase4-rev10-native-review.py`. 같은 독립 웹 리뷰 대화 https://chatgpt.com/c/6aa13845-5fdc-83e9-a3e9-b3e32c1e9bdb 에 새 전체 첨부. Latest/매우 높음 UI 검증, 강제 답변 없음. 이전과 동일한 파일은 실제 내용 동일성 확인 후 이전 독립 읽기 재사용 가능, 변경/추가 파일은 전체 재검토. 새 응답 turn 기준을 전송 전에 수집해 이전 REVISE와 구분한다. Timeout은 같은 URL native harvest로 회수.
- Gemini3.8Flash high Descartes **01a085ef-7f18-7b41-9c12-6678da68018b**: 독립적으로 sealed126파일/251페이지 전체 읽기, 최종 후 raw output 및 인용 감사 필요. 다른 리뷰 판정 미전달.
- **rev10 Gemini 완료:** 전체251원본출력/5인용root대조, 독립731pytest/projection통과 확인. static/줄번호/재현 import provenance 정정과 root qualification 후GO수용. Descartes종료. `.review/phase4-rev10-gemini-{final.md,addendum.md,audit.json}`. 웹은대기중.
- 다음: 현재 웹 최종 수집/검증 → 지적 있으면 root 재현·수정 → 해당 revision의 두 GO와 root 수용 후 완료 문서/로컬 커밋. **Phase5–8/push 금지. 승인 plan rev6/frozen 불변.**

## 이전 인수인계 — Phase4 rev9 웹 REVISE2 확인, rev10 통합 수정 (2026-09-09)

**최신 사용자 정정:** Sonnet 리뷰는 일회성 요청이었다. 앞으로 리뷰용으로 호출 금지. 현재 Sonnet 작업은 모두 종료했고, 이후 리뷰는 Codex native insane-review + Gemini3.8Flashhigh만 사용한다. 기존 추가리뷰 기록은 과거 사실로만 보존한다.

**이 절이 아래 기록보다 우선한다. 완료/커밋 아님.** Codex native insane-review가1810초 후정상회수/exit0. 최종 `.review/phase4-rev9-insane-review-final.md`는 전체124파일/701tests검토후 REVISE2건이다. Root가둘다양operation재현했다. 이전rev9Gemini/SonnetGO는완료게이트로사용하지않는다.

1. 부분만존재하는범위허용: 실제페이지1–40, graphPageCount41, desired40–41이면MATERIAL_USAGE/PAGE_RANGE 모두APPLIED였음. Root가공유 `_phase4_page_chunks`를완전한범위증명으로수정(유효단일페이지로케이터의집합크기, 거대range순회없음). 정상승인/복구모두같은helper사용.
2. effect_observed표식외부쓰기중인간old복원 후잘못APPLIED: Root가표식검증후/최종Queue검사전에 fullstrict target/dependency audit-only재검증추가. 복원/변경/불확실이면marker보존+APPROVED조정대기, 재쓰기/감사없음. 정상적용/기존audit-only재시도/partialAPPLIED복구모두이검사통과필요.

- Root증거 `.review/phase4-rev10-root-{reproduce.py,before.json,after.json}`: 부분범위는두operation모두SUPERSEDED/writes0; 효과표식중복원은APPROVED/writes1/인간값보존/audit없음/marker보존으로수정확인.
- **현재 Luna max Ampere** `01a085e1-8469-7010-878d-84ab99433618`: 새 `tests/contract/test_phase4_rev10_pages.py`, `test_phase4_rev10_audit.py`만소유. Root가base.py통합수정, Luna가permanentregressions(부분range/valid39–40/40/복구;markerwrite중old/sibling/Course/Type/sourcebinding/fingerprint변경)병렬담당. 기존tests/docs/sourceedit금지. Focused두Python+새testRuff필요.
- 웹프로세스36666종료. URL https://chatgpt.com/c/6aa13845-5fdc-83e9-a3e9-b3e32c1e9bdb , 원본 `.insane-review/response_Syllva_20260909_194259_74569_ddb89e.md`. nativeplugin0.6.8/Latest매우높음/완전본문/강제답변없음. 다음리뷰도native launcher를rev10으로갱신해사용.
- 다음: Luna회귀합치고전체두Python+root반례/스모크/static/컴파일/projection → 완전한rev10첨부감사 → 현재웹+Gemini독립통합리뷰. **Phase4만, Phase5–8/push금지, 커밋없음.** 계획rev6/frozen불변.

## 이전 인수인계 — Phase4 rev9 검증 완료, native 웹+Gemini 통합 리뷰 진행 (2026-09-09)

**이 절이 아래 기록보다 우선한다. 최종 완료/커밋은 아직 아니다.** rev8 웹 REVISE2건을 수정하고 root 검증을 마쳤다. 승인계획rev6/두 frozen문서는 hash불변이다.

- Luna Carson 수정 완료/종료. durable prepared/effect_observed 표식으로 restart 전 실제 효과 확인 여부를 구분한다. Prepared-only + 나중 desired는 APPROVED/조정대기, target/audit 없음. 실제 write+신뢰할 수 있는 desiredreadback 후 effect표식 저장/검증한 경우만 audit-only recovery 가능. phase-less/unknown marker보수적거절. 정상 승인/복구의 material validation은 전체 strictpageindex 사용.
- Root rev3 기존 unreadable→나중desired성공 기대2건을 새 보수적계약에 맞게 APPROVED/감사없음으로 강화. divergent거절 유지. 신규37회귀, 기존 집중108tests×두Python통과. 루트전체 **836passed × Python3.11.16/3.14.7**. 두operation의 preparedcrash→humanDesired는writes0/audit없음; 실제producer→p40→승인→검색→재적용→철회 모두통과.
- `.review/phase4-root-rev9-results.json`: compile/projection/smoke/page40/repro/diff통과. Ruff188(기준190, rev8=186; effect표식의 보수적예외처리2건증가)/mypy74(기준76), 새normalized타입오류없음. 정적검사clean은아님.
- 고정 manifest/hash `.review/phase4-integrated-review-{files,hashes}-rev9.*`:124파일. Sourcecopy격리 **701passed**, `.review/phase4-rev9-pack-audit.json` root `/var/folders/p7/6kdby1xx3t148xw4sys5ql340000gn/T/syllva-phase4-rev9-pack-cd74doqp`. 모든파일읽기 helper `.review/phase4-rev9-read-page.py`,249pages0–248.
- **현재 native웹**: unifiedsession36666, `.review/phase4-rev9-insane-review.log`, launcher `/tmp/syllva-phase4-rev9-native-review.py`; Codex plugin `/Users/admin/.codex/plugins/cache/gptaku-codex/insane-review-codex/0.6.8/bin/pack_and_ask.py`. 실제첨부라인/격리test감사를 업로드전 hook으로 수행한다. Latest/매우높음 visibleUI검증, forcedanswer비활성. Timeout은동일URL nativeharvest로만회수. 전송URL/최종report는로그확인.
- **현재 Gemini3.8Flashhigh** Rawls `01a085c3-ea13-76e1-a082-9c4b0aff92ce`: 새sealed124files/249pages 전체통합독립리뷰. 다른리뷰결과미전달. 최종후 `.review/phase4-rev9-audit-agent-pages.py AGENT_ID`로 rawrollout customtooloutputs 포함 전체원본대조; 인용대조. incomplete는GO아님.
- **rev9 Gemini 완료:** 전체249출력/5인용root일치, GO수용, Rawls종료. `.review/phase4-rev9-gemini-{final.md,audit.json}`. 테스트701passed는root실행로그를검토한것이며 독립실행아님을정정. Producer전체None, applierMaterialNone/Session8을실제코드대조. 실제첨부124파일/411858tokens/701tests와격리projection도통과. 웹 URL https://chatgpt.com/c/6aa13845-5fdc-83e9-a3e9-b3e32c1e9bdb , 계속생성중.
- **추가 Sonnet5high 현재rev9리뷰:** 웹대기중사용자추가리뷰요청을현재수정본에도적용. Franklin `01a085cc-c462-7651-9a0b-058bd0b7238f`가동일124files/249pages전체독립검토중. 다른리뷰결과미전달. `.review/phase4-rev9-audit-agent-pages.py AGENT_ID`로읽기원본감사. 이전rev8SonnetGO와구분한다.
- **rev9 Sonnet 완료:** Franklin GO,249페이지원본출력/5정확인용대조완료/종료. `.review/phase4-rev9-sonnet-{final.md,addendum.md,audit.json}`. Source-only검토. 계획헤더수정제안은불변조건에따라철회했고 configNone기본값은실제테스트된의도된동작으로정정. Gemini+Sonnet현재GO수용, **웹최종판정만대기**.
- 다음: 실제첨부감사확인+두현재독립GO+root확인 후 완료문서/로컬커밋. **Phase4만, Phase5–8/push금지.** 사용자요청nativeplugin실제harvest도성공했으며 자세한증거는아래기록.

## 이전 인수인계 — Phase4 rev8 웹 REVISE 회수, rev9 수정 중 (2026-09-09)

**이 절이 아래 기록보다 우선한다. Phase4 완료/커밋은 아직 아니다.** 지연된 웹 최종 보고서를 회수했다. 결과는 **REVISE 2건**이며 root가 두 operation 모두에서 재현했다. 기존 Gemini/Sonnet rev8 GO는 현 완료 게이트로 사용할 수 없다. 게이트 대체 질문은 더 이상 진행의 전제가 아니며, 수정 후 웹+Gemini 독립 GO 조건을 유지한다.

- 웹 보고서: `.review/phase4-rev8-insane-review-final.md`, 회수 증거 `.review/phase4-rev8-web-harvest.json`; 대화 https://chatgpt.com/c/6aa10f98-9608-83e9-bda7-b7c0352c55c3 . 전체122파일/664테스트 검토 후 최종 REVISE.
- 결함1: prepared 표식 저장 후 target 호출 전 프로세스 종료 → 인간이 desired 상태로 변경 → 새 applier가 target_mutations0인데 APPLIED/감사를 기록한다. durable prepared/effect_observed 구분으로 보완 중이다.
- 결함2: 정상 승인과 복구가 material32chunks로 제한되어 유효한 p40 제안을 SUPERSEDED로 거절한다. 완전한 strict 페이지 인덱스로 보완 중이다.
- Root 재현: `.review/phase4-rev9-root-reproduce.py`, `.review/phase4-rev9-root-before.json`. MATERIAL_USAGE/PAGE_RANGE 모두 두 결함 재현.
- Luna max Carson `01a085b0-fa8d-7ef1-a31c-3b02a84182dd` 활성: `src/uls/adapters/notion/base.py`, 새 `tests/contract/test_phase4_rev9_{recovery,pages}.py` 소유. root는 통합검증/문서/리뷰 준비 담당.
- 사용자 지시로 다음 리뷰부터 **Codex native insane-review-codex0.6.8** 사용. Skill `/Users/admin/.codex/plugins/cache/gptaku-codex/insane-review-codex/0.6.8/skills/insane-review/SKILL.md`; engine 같은 root의 `bin/pack_and_ask.py`. ensure-env 모두 정상(CDP9222/Chrome/loginok). 기존 Claude plugin wrapper는 기본 실행 경로로 쓰지 않는다. 현 UI Pro disabled/Latest checked/매우높음 slider3 실측. Native 선택기의 hidden요소/구형메뉴 호환은 `/tmp/syllva-phase4-rev9-native-review.py`의 작은 실행 adapter로 보완했고 `.review/phase4-rev9-native-model-probe.json`에서 검증 true. native engine의 패킹·첨부·회수·harvest 유지, 설치 파일 변경 없음. 전송 후 timeout은 native `--harvest`로 회수하며 중복 전송/조기 답변 강제 금지. 실제 rev8 native harvest도 exit0로 성공: `.insane-review/response_harvest_20260909_193410_73952_a93120.md` (11,555자/REVISE), `.review/phase4-rev8-native-harvest.log`.
- 다음: 두 수정 통합 → 전체 두Python 및 root 반례/흐름검증 → 완전한 rev9 첨부 감사 → 새 독립 통합리뷰. 승인계획rev6와 frozen2문서 불변. **Phase4만, Phase5–8/push 금지, 커밋 없음.**

## 이전 인수인계 — Phase4 rev8 구현·검증 및 Gemini/Sonnet GO, 웹 게이트 결정 대기 (2026-09-09)

**이 절이 아래 모든 기록보다 우선한다. Phase4 최종 완료/커밋은 아직 아니다.** 구현 rev8은 승인 계획 rev6에 따라 완료했고, 전체 테스트와 두 독립 서브리뷰는 통과했다. 필수였던 웹 리뷰는 연결 오류와 타임아웃으로 최종 판정을 내지 못했다. 사용자에게 이번 완료 조건을 Gemini+Sonnet GO로 대체할지, 웹 GO 조건을 유지할지 질문했으며 아직 답변이 없다. **명시적 답변 없이 웹 게이트를 대체하거나 완료·커밋하지 않는다.**

- **Root 검증:** Python3.11.16 /3.14.7 각각 **799 passed**. 실제 웹 첨부 및 source-copy 복원 환경은 **122파일 /664 passed**, Behavior projection lint도 통과. 컴파일/전체 흐름 스모크/diff check 통과. Ruff186·mypy74는 기존 부채(기준190·76), 새 정규화 타입 오류 없음.
- **Gemini3.8Flash high: GO.** 기존 긴 문맥의429를 새 서브에이전트로 복구해 전체205페이지를 다시 읽었다. 실제 모든 출력과 인용3개 원본 대조 완료. `.review/phase4-rev8-gemini-final.md`, `.review/phase4-rev8-gemini-fresh-audit.json`. Agent Sagan `01a08538-988a-7632-9743-c88a64afc7a9` 종료.
- **Sonnet5 high: GO.** 사용자가 웹 대기 중 추가 독립 리뷰를 요청했다. 전체205페이지/122파일 원본 실행 로그와 인용5개를 대조했다. `.review/phase4-rev8-sonnet-final.md`, `.review/phase4-rev8-sonnet-raw-audit.json`. Lagrange `01a08542-e184-7493-938f-2c91b7eed986` 종료. 처음 누락처럼 보인188–204는 app read_thread가 묶음 functions.exec 출력을 생략한 조회 문제였고, 원본 로그에서 처음부터 읽었음을 확인했다. Sonnet의 읽기 누락이 아니며 사용자에게 정정했다. 기존 Phase3의 비차단 dead-code 지적은 범위 밖으로 유지.
- **웹: 최종 판정 없음.** UI Latest/매우높음 검증 후 같은 첨부를 읽고664tests/compile/projection 통과를 확인했지만, 추가 시스템 검토 지연→연결 끊김으로 첫3600초 회수는exit1. 같은 대화·같은 모델에서 자연스러운 검토 재개를 요청했으나 추가1800초도timeout/exit1. 조기 답변 강제나 다른 모델 대체는 하지 않았다. 두 로컬 수집 프로세스88516/43368 모두 종료. 웹 대화 자체는 남아 있고 이후 결과는 아직 확인되지 않았다.
- 웹 URL: https://chatgpt.com/c/6aa10f98-9608-83e9-bda7-b7c0352c55c3 . `.review/phase4-rev8-insane-review.log`, `.review/phase4-rev8-web-initial-failure.json`, `.review/phase4-rev8-web-resume.log`, `.review/phase4-rev8-web-resume-result.json`. 최종 웹 report는 생성되지 않았다. 첨부 `.insane-review/pack_Syllva_20260909_164856_67907_7072d1.md` (122파일/404,347tokens), 두 복원 검증 모두664passed.
- **최종 무결성:** `.review/phase4-rev8-final-integrity.json`에서122파일 모두 리뷰 snapshot과 일치, 승인계획 및 두 frozen문서 hash불변, diff check0. HEAD `bfc592b4fc15df68df599bc8b0811cace521b1f7`, branch `codex/phase4-8`; 이번 작업 커밋/push 없음. 모든 구현/문서 변경은 작업 트리에 보존.
- 다음 담당자: 먼저 사용자의 게이트 선택을 반영한다. Gemini+Sonnet 대체를 승인하면 완료 문서를 갱신하고 로컬 구현/인수인계 커밋까지 마무리(공유 스냅샷에 변경이 생겼다면 필요한 검증 갱신). 웹 조건 유지라면 같은 대화의 실제 최종 판정부터 회수/검증한다. 웹 미완료를 GO로 취급하지 않는다. **Phase4만, Phase5–8/push 금지.**

## 이전 인수인계 — Phase4 rev8 검증 완료, 통합 리뷰 진행 (2026-09-09)

**이 절이 아래 기록보다 우선한다.** 이전 웹 리뷰는 오류 종료가 아니라 정상 회수/exit0이었으며, 세 지적 모두 rev7–8에서 수정했다. 현재 새 rev8 웹 리뷰와 Gemini 통합 리뷰를 진행한다. 사용자 상태 질문은 작업 중단 지시가 아니다.

- Luna Goodall/Kuhn 수정 완료 및 종료. rev8은 실제 시도 없는 APPLIED 오인 방지, 무관한 malformed Usage 격리를 보완했다. 기존 targetID/rawexact sibling 거절과 prior-marker 복구는 유지한다. 새40회귀, 전체 **799 passed × Python3.11/3.14**.
- 정확한122파일 snapshot과 실제 웹첨부 복원 모두 **664 passed**. Behavior projection lint도 격리 환경 통과. `.review/phase4-rev8-{pack-audit,packed-content-audit}.json`. Ruff186/mypy74 기존부채, 새타입오류없음. 승인계획/두 frozen 문서 hash불변.
- 웹: process88516, `.review/phase4-rev8-insane-review.log`, `.insane-review/pack_Syllva_20260909_164856_67907_7072d1.md`, Latest/매우높음 UI검증 및전송완료. 대화 https://chatgpt.com/c/6aa10f98-9608-83e9-bda7-b7c0352c55c3 . 추가시스템검토안내표시중이나검토내용이추가되고있음. 결과/실제전체커버리지 확인 필요.
- Gemini3.8Flashhigh: 기존 Planck `01a08524-7809-7ce0-a027-04c81230181a`는0–139완료후긴문맥재개429반복으로종료. 사용자가Gemini서브에이전트재시도를요청했고 새 Sagan `01a08538-988a-7632-9743-c88a64afc7a9`는 `GEMINI_SUBAGENT_OK` 정상응답. 새agent에서 같은122files/205pages **전체를0부터다시읽기완료**, 모든실제출력감사완료/최종**GO**, 인용3개원본대조/현재rev8root수용. `.review/phase4-rev8-gemini-final.md`. 새agent종료, 이제웹최종판정대기. `.review/phase4-rev8-gemini-fresh-audit.json`. 기존부분커버리지를새GO에합산하지않음. 각fullpage출력감사, 모두읽은뒤전체판정1회. helper `.review/phase4-rev8-read-page.py` 고정archive. Sonnet대체승인은없음.

- 사용자추가지시: 웹대기동안Sonnet리뷰요청. Sonnet5high Lagrange `01a08542-e184-7493-938f-2c91b7eed986` 같은122files/205pages 독립통합리뷰(기존리뷰결과미전달). **전체읽기원본로그대조완료**, 최종**GO**/인용5개원본일치/root수용/agent종료. `.review/phase4-rev8-sonnet-final.md`. `.review/phase4-rev8-sonnet-raw-audit.json`:223개출력모두실제source라인일치,205pages커버. 주의: app read_thread는 functions.exec묶음출력을누락하여188–204가안보였음. root가처음잘못누락판정/재읽기요청했지만원본rollout에서처음부터읽었음을확인하고사용자/agent에정정. Sonnet누락이아님. 원본 `/Users/admin/.codex/sessions/2026/09/09/rollout-2026-09-09T17-22-28-01a08542-e184-7493-938f-2c91b7eed986.jsonl`의response_item custom_tool_call_output까지감사할것. 기존GeminiGO/웹게이트를대체한다는지시는아니며추가리뷰다.

- 웹현재: 약54분시점연결끊김안내가나타남. 같은대화reload후에도동일, stop-buttonvisible/최종본문미완성. 첫collector60분제한/exit1종료, 같은대화다시열어도최종판정없음. 사용자에게이번완료게이트를Gemini+Sonnet독립GO로대체해로컬커밋할지/웹GO조건유지할지비동기질문전달. **답변전대체완료나커밋하지않는다.** 현재두서브리뷰GO는원본출력/코드인용으로검증완료이며코드799tests×두Python통과.

- 웹복구재개: 사용자완료조건선택은아직답변없어기존웹조건유지. 오류로멈춘**같은대화**에독립전체검토계속요청을전송(Latest/매우높음재검증, 새첨부/다른리뷰결과없음, 조기답변강제없음). 현재process43368, `.review/phase4-rev8-web-resume.log`, script `/tmp/syllva-phase4-rev8-resume-web.py`, 최대1800초. 성공시 `.review/phase4-rev8-insane-review-final.md`와resultJSON저장. 이전process88516은exit1종료. 첫실패 `.review/phase4-rev8-web-initial-failure.json`.

- 다음: 두 current독립GO+root검증, 문서완료기록/로컬커밋. **Phase4만, Phase5–8/push금지, 아직커밋없음.** 이전GO는현코드완료게이트로사용하지않음.

## 이전 인수인계 — Phase 4 구현 rev8 보완 진행 (2026-09-09)

**이 절이 아래 모든 기록보다 우선한다.** rev6 웹 리뷰는 추가 시스템 검토 안내로 지연됐지만 **정상 회수/exit0 종료**했다. 현재 실행 중인 웹 리뷰는 없다. 최종 `.review/phase4-rev6-insane-review-final.md`는 REVISE3건이며, 1건은 rev7에서 해결했고 나머지2건을 Luna로 보완 중이다. 사용자가 ‘웹리뷰 오류 같음’을 물었고, 정상 회수·종료 및 현재 수정 상태를 설명했다. 중단 지시는 아니다.

1. 일반예외+oldread-back만으로 복구 표식을 해제하는 문제: rev7 완료. trusted `ProviderWriteNotAppliedError` 보장 +즉시정확한old 확인만 해제 허용. wire code는기존PROVIDER_UNAVAILABLE. 새14회귀, 전체759tests×두Python 통과. Root `.review/phase4-rev7-root-unknown-old-fixed.txt`에서 인간복원보존/target_mutations1 확인. manifest112파일/624복원tests 통과.
2. **실제 시도 없는 APPLIED 오인**: virgin/no-marker 승인 제안에서 외부인이 먼저 desired 값을 만든 경우, 또는 이번marker arm중 target writer호출전에desired로 바뀐 경우, 감사/APPLIED를기록하면안된다. 과거의유효한attemptmarker+desired audit-only복구는유지하되 이번호출이시도하지않았음을아는경로는targetdrift로거절한다. Goodall Luna max `01a084dc-b70d-77a2-aedc-644117aedafc`가 notion/base.py 및새 `test_phase4_rev8_approval.py` 담당.
3. **무관한 잘못된Usage의전역거절**: no-ID나중복ID를가진 무관한Usage가 정상독립candidate/target까지막는다. 대상ID물리유일성은해당basis에한정하고, rawexacttuple검사는Verified/ID유효성과분리하여정확한형제는계속차단한다. Goodall이applier, Kuhn Luna max `01a084b7-9d0e-7ec2-94c6-20728eb82690`이 producer/material_usage.py 및새 `test_phase4_rev8_producer.py` 담당. 기존잘못된전역거절테스트가있으면계획을대조하여근거없이약화하지않는다.

- 총괄2/3재현 `.review/phase4-rev7-root-new-findings.txt`: virginDesired가APPLIED/targetwrites0; 무관한Referencep1 no-ID행이approvalSUPERSEDED/producerSourcePartialError를일으킴.
- rev7검증로그 `.review/phase4-root-rev7-pytest{311,314}.txt`, mypy74/Ruff186(기존부채, 새타입오류없음), 컴파일/스모크/diff통과. 아직새수정반영전체검증은전이다.
- 이전rev6GeminiGO는전체194페이지/코드인용5개를실제대조했지만root반례때문에완료게이트로인정안함. `.review/phase4-rev6-gemini-audit.json`. reviewer종료.
- 이전웹대화 https://chatgpt.com/c/6aa106e4-0ab8-83e8-aec2-972d78bfd701 , 원본 `.insane-review/response_Syllva_20260909_161150_65933_83322b.md`, 로그 `.review/phase4-rev6-insane-review.log`, process86031종료.
- 준비된rev7외부리뷰는전송하지않았다. 남은두지적을통합해다음스냅샷(rev8)으로전체검증/완전패킹감사/두독립전체리뷰를실행한다. 현재두구현에이전트만활성.
- 승인계획rev6와frozen두문서불변. **Phase4만, Phase5–8/push금지, 커밋없음.** 두현재독립GO및root확인후완료문서/로컬커밋까지마무리한다.

## 이전 검증 스냅샷 — Phase4 구현 rev6 (2026-09-09)

**이 절이 아래 모든 기록보다 우선한다.** rev5 웹 리뷰의 두 차단 결함(감사 실패 후 인간 수정 덮어쓰기, 초기 읽기 중 바뀐 입력의 모델 전달)을 Luna max 두 에이전트가 수정했고 모두 종료했다. Sonnet5 high의 복구 방식 자문은 총괄이 쓰기 예외/표식 보호/부분 감사 조건을 보완해 적용했다. Phase4 최종 완료는 아직 두 새 독립 판정 대기 중이다.

- 구현 rev6 전체 pytest: Python3.11.16 /3.14.7 모두 **745 passed**. 컴파일, Behavior projection, Producer→승인→검색→재적용→철회 스모크, diff check 통과. `.review/phase4-root-rev6-pytest{311,314}.txt`.
- 신규 `test_phase4_rev6_recovery.py`29건, `test_phase4_rev6_producer.py`16건. 이전700→745. 복구 수정 전6failed/4passed, Producer 수정 전9failed/3passed. 관련 집중검증99/97건씩 두Python 통과. 총괄 전체검사에서 잡은 Queue삭제시 거절 방식 차이도 기존계약대로 수정한 뒤 전체재실행했다.
- 총괄 재현: 초기원본변경 두건은 proposer_calls0/writes0. 감사실패/부분감사/State만APPLIED기록 후 인간VerifiedFalse 복원 세건은 새applier에서도 target_mutations1 유지, 인간값보존, APPLIED미보고. `.review/phase4-rev6-root-{premodel,recovery}-fixed.txt`.
- 승인된 계획 rev6 SHA256 `901c3e046ede6b39f9863d84bf4aeb41b4bbf3b4244ff9280e6940d01a768058`, 두 frozen 문서 불변. 구현리비전번호와 계획리비전은 별개다.
- 정적검사 Ruff186 vs baseline190, mypy74 vs baseline76, 새normalizedtype오류없음. 복구 경로의 fail-closed 예외 처리/cleanup 후read-back 패턴을 포함해 부채가 남아 있다. clean이라고 쓰지 않는다. `.review/phase4-root-rev6-static-diff.json`.
- sealed manifest `.review/phase4-integrated-review-files-rev6.txt`, 해시 `.review/phase4-integrated-review-hashes-rev6.json`: **111파일**. SQL/전이의존성 포함 별도복원 **610passed**. 실제첨부 행별대조 및 첨부만복원한610tests도 통과: `.review/phase4-rev6-packed-content-audit.json`.
- 실제 전체첨부 `.insane-review/pack_Syllva_20260909_161150_65933_83322b.md`, **395,205tokens**, 주석/본문/빈줄 생략없음.
- insane-review Latest/매우높음 UI·첨부·전송 검증. 사용자허용 일반새채팅 https://chatgpt.com/c/6aa106e4-0ab8-83e8-aec2-972d78bfd701 . 로그 `.review/phase4-rev6-insane-review.log`, unified session **86031**, 최대3600초, 현재생성중. 최종답변만회수하며 강제답변금지.
- 독립 Gemini native `google-antigravity/gemini-3.8-flash` high, **Feynman** ID `01a08502-98bb-7a63-bfbc-ffe29636d0c0`. 전체111파일을194개의 해시고정페이지로 읽는중: `.review/phase4-rev6-review-pages.json`, `.review/phase4-rev6-read-page.py`. 현재0–59, 다음60–119, 마지막120–193. 각명령의전체END PAGE출력/잘림을총괄확인후 하나의전체통합판정을받는다. 다른reviewer결과전달안함.
- 과거rev5웹보고서 `.review/phase4-rev5-insane-review-final.md`는REVISE2건. 해당프로세스47542종료. 과거GeminiGO는새코드에적용불가.
- 다음: 두현재리뷰회수/필요시지적재현수정 → 해당스냅샷의두GO와총괄검증 → handoff/verification/execution 완료기록/로컬커밋. **Phase5–8과push는진행하지않는다.** 아직커밋없음.

## 이전 검증 스냅샷 — Phase 4 rev5 (2026-09-09)

**이 절이 아래 모든 기록보다 우선한다.** Phase 4의 이전 차단 4건을 수정한 rev4에서 두 추가 결함이 발견됐다. 총괄이 논리 Session/Material ID drift와 malformed Verified exact sibling 승인 문제를 직접 재현했고, Luna max 두 에이전트가 공통 검사·Producer·승인·검색·SessionResolver 및 회귀 테스트를 수정했다. 두 구현 에이전트는 종료했다. Phase 5–8과 push는 진행하지 않는다.

- rev4 insane-review 최종 **REVISE, 차단 2건**: `.review/phase4-rev4-insane-review-final.md`. 107파일 전체 검토/복원499tests를 명시. 기존 Gemini GO만으로 완료하지 않았다.
- rev5 총괄 전체 pytest: Python 3.11.16 / 3.14.7 모두 **700 passed**. 컴파일, Behavior projection, 통합 Producer→승인→검색→재적용→철회 스모크, diff check 통과. `.review/phase4-root-rev5-pytest{311,314}.txt`.
- 새 회귀: `tests/contract/test_phase4_rev5_identity.py` **38**, `test_phase4_rev5_siblings.py` **28**. ID 수정 전 25 failed/6 passed, sibling 소비자 통합 전 12 failed/15 passed. 최종 두 Python에서 모두 통과. 총괄의 원래 승인 재현 세 건도 모두 SUPERSEDED/대상 쓰기0으로 확인: `.review/phase4-rev5-root-reproductions-fixed.txt`.
- 정적 검사: Ruff **174** vs baseline190, mypy **74** vs baseline76, 새 normalized type error 없음. 기존 부채가 남아 있으므로 clean으로 표기하지 않는다. `.review/phase4-root-rev5-static-diff.json`. 총괄은 마지막 타입 narrowing과 import/string formatting만 정리한 뒤 전체700tests×2를 다시 실행했다.
- 승인된 rev6 계획 SHA256 `901c3e046ede6b39f9863d84bf4aeb41b4bbf3b4244ff9280e6940d01a768058` 및 두 frozen 문서 유지.
- sealed manifest/hashes: `.review/phase4-integrated-review-{files,hashes}-rev5.{txt,json}` (실제 각각 files-rev5.txt / hashes-rev5.json), **109파일**. 의존성/SQL 포함 복원본 **565 passed**. 실제 첨부 전 소스 행 대조 및 첨부 복원565passed: `.review/phase4-rev5-packed-content-audit.json`, `.review/phase4-rev5-packed-pytest311.txt`.
- 실제 전체 첨부 `.insane-review/pack_Syllva_20260909_151218_62743_317a70.md`, **378,685 tokens**. 주석/빈 줄/본문 생략 없음.
- insane-review 최신/매우 높음 UI 검증·첨부·전송 확인. 사용자 승인 일반 새 채팅: https://chatgpt.com/c/6aa0f8cf-3a00-83ee-b6ff-971014c9ca76 . 로그 `.review/phase4-rev5-insane-review.log`, unified process session **47542**, 최대 대기3600초. 최종 응답만 회수, 강제 답변 금지. 현재 생성 중.
- 독립 Gemini native `google-antigravity/gemini-3.8-flash` high, ID `01a084cc-3319-74c2-8b15-dd6194691ab6` (Herschel). 같은109파일을187개 해시 고정 읽기 페이지로 전달한다: `.review/phase4-rev5-review-pages.json`, `.review/phase4-rev5-read-page.py`. 전체187페이지의 실제 명령/END PAGE 출력과 누락/잘림을 총괄이 확인했다. 최종 **통합 GO 승인**: `.review/phase4-rev5-gemini-final.md`, `.review/phase4-rev5-gemini-accepted-audit.md`. 최종 코드 인용4개도 실제 소스와 일치했다. reviewer 종료. 다른 reviewer 결과를 전달하지 않는다.
- **최종 완료 게이트는 아직 대기 중**. 적용 가능한 두 독립 GO와 총괄 확인 후 handoff/verification/execution 문서를 일치시키고 로컬 커밋한다. 소스는 리뷰 중 고정한다. 커밋/푸시하지 않았다.

## 이전 검증 스냅샷 — Phase 4 rev4 (2026-09-09)

**이 절이 아래 모든 과거 기록보다 우선한다.** 새 세션에서 Phase 4를 재개했고, 이전 통합 리뷰의 차단 결함 4개 수정과 영구 회귀 테스트를 완료했다. Phase 5–8은 비활성이고 push하지 않는다.

- 구현: Luna max 두 에이전트가 승인/Producer와 Retrieval을 분담했다. Sonnet 5 high가 frozen §45의 7개 완료 조건을 테스트와 대조했고, 별도 Luna가 실제 Producer → Queue → 승인 → 같은 검색 엔진 → 재적용 → 철회 흐름을 영구 테스트로 보강했다. 모든 구현 에이전트 종료.
- 승인된 rev6 계획 SHA256 `901c3e046ede6b39f9863d84bf4aeb41b4bbf3b4244ff9280e6940d01a768058` 유지. 두 frozen 문서도 변경하지 않았다.
- 총괄 전체 pytest: Python 3.11.16 / 3.14.7 모두 **634 passed**. 컴파일, Behavior projection, diff check, 별도 통합 스모크 통과. 로그 `.review/phase4-root-rev4-pytest{311,314}.txt`.
- 기존 취약 소스를 별도 디렉터리에 복원한 비교 재현: 동일한 19개 테스트 중 이전 소스 **18 failed / 1 passed**, 현재 소스 **19 passed**. 실제 위조 승인 허용/쓰기/철회 미검출로 실패했으며 import/fixture 오류가 아니다. `.review/phase4-rev4-{before,after}-fix-reproduction.txt`.
- 정적 검사: Ruff **174** vs baseline 190; mypy **74** vs baseline 76, 새 normalized type error 없음. 기존 lint/type 부채가 남아 있으므로 clean으로 표기하지 않는다. `.review/phase4-root-rev4-static-diff.json`.
- 새 회귀 파일: `tests/contract/test_phase4_rev4_{approval,producer,retrieval}.py`. 총 168건 추가 및 guarded 통합 1건 추가(기존 465 → 634). provider read 중 실제 mutation 실행을 확인하는 spy/assertion 포함.
- 리뷰 manifest는 Python 전이 의존성 및 `src/uls/state/migrations/001_initial.sql`을 포함한 **107파일**. 소스 복원본의 포함 테스트 **499 passed**. `.review/phase4-integrated-review-files-rev4.txt`, `.review/phase4-integrated-review-hashes-rev4.json`, `.review/phase4-rev4-pack-audit.json` 참고. 실제 첨부의 전체 내용을 대조하고 첨부만으로 복원한 실행도 **499 passed**: `.review/phase4-rev4-packed-content-audit.json`.
- 실제 insane-review 첨부: `.insane-review/pack_Syllva_20260909_141046_59144_939a8f.md`, **366,774 tokens**, 전체 코드/주석/빈 줄 유지. 큰 첨부의 전체 커버리지 확인이 GO 필수 조건이다.
- 최신/매우 높음 사전 UI 검증 성공. 기존 프로젝트 오류에 대한 사용자 예외로 일반 새 채팅 리뷰를 실행했다. 실행 로그 `.review/phase4-rev4-insane-review-retry1.log`, 프로세스 unified session `20541`. 모델 검증·첨부·전송 확인, 대화 https://chatgpt.com/c/6aa0ea62-8088-83e8-a59d-428b12918ef9 에서 생성 중. 아직 리뷰 결과 미회수. 첫 시도는 모델 검증에서 전송 전 종료됐고, 전송 없는 UI 재진단 성공 후 재시도했다.
- 독립 Gemini는 native subagent `google-antigravity/gemini-3.8-flash` high, ID `01a0847f-715b-7182-b35c-97c40d1e7209`. 사전 READY는 연결 확인일 뿐 판정이 아니다. sealed 107파일 리뷰의 첫 GO는 총괄의 실제 도구 실행 대조에서 전체 커버리지 주장이 입증되지 않아 **미승인**이다. 두 번째 GO도 잘못된 파일 길이·누락 본문 때문에 미승인이다. 같은 reviewer에게 해시 고정된 전체 소스를 182개 읽기 페이지로 전달해 검토를 마치도록 했다(`.review/phase4-rev4-read-page.py`, index `.review/phase4-rev4-review-pages.json`). 페이지 0–59는 실제 명령/END PAGE 출력을 총괄이 모두 확인했다(`.review/phase4-rev4-gemini-pages-0-59.json`, 누락 0). 60–119도 실제 명령/END PAGE 출력을 모두 확인했다(`.review/phase4-rev4-gemini-pages-60-119.json`, 누락 0). 마지막 120–181도 실제 명령/END PAGE 출력을 모두 확인했다(누락 0). 전체 182페이지/107파일 검토 후 최종 통합 **GO를 승인**했다. 최종 보고서 `.review/phase4-rev4-gemini-final.md`, 승인 근거 `.review/phase4-rev4-gemini-accepted-audit.md`. reviewer는 종료했다. 이는 동일 스냅샷 전체 리뷰의 전달 과정이며 범위별 GO를 받지 않는다. `.review/phase4-rev4-gemini.md`, `.review/phase4-rev4-gemini-audit.md`, `.review/phase4-rev4-gemini-command-audit.json` 참고. 다른 리뷰 결과를 주지 않았다.
- **Phase 4 최종 완료 게이트는 아직 열리지 않았다.** 두 적용 가능한 독립 GO와 총괄 판정 후 이 절과 verification/execution 문서를 완료 상태로 갱신한다. 소스는 리뷰 중 변경하지 않는다. 커밋/푸시하지 않았다.

## 과거 인수인계 — 사용자 요청으로 중단 (2026-09-09)

**이 절이 아래 모든 과거 진행 기록보다 우선한다.** 사용자는 “중단하고 handoff.md를 만들어 새 세션에서 시도”하도록 지시했다. Phase 4는 미완료이며, 새 세션에서 재개해야 한다. Phase 5–8은 진행하지 않는다.

### 저장소와 작업 범위

- 작업 위치: `/Users/admin/Project/Syllva`, 브랜치 `codex/phase4-8`, 기준 HEAD `bfc592b`.
- Phase 4의 기존 구현과 이번 수정은 모두 미커밋 상태다. 사용자 변경을 포함한 작업 트리를 reset/revert하지 말 것. 커밋/푸시하지 않았다.
- 승인된 계획: `docs/plans/phase4-material-usage.md` rev6. SHA256 `901c3e046ede6b39f9863d84bf4aeb41b4bbf3b4244ff9280e6940d01a768058`.
- 두 frozen 문서와 계획은 변경하지 않는다. Provider-neutral / strict Fake 구현 범위이며 live SDK와 Phase 5–8 확장은 요구하지 않는다.
- 사용자 선호: 수정들을 먼저 통합하고 전체 검증 후 넓은 Phase 4 통합 리뷰. 작게 나눠 빈번히 재리뷰하지 않는다. Sol 리뷰 사용 금지.
- 역할: 총괄은 현재 Codex, 구현은 Luna max, 계획은 Sonnet 5 high, 독립 리뷰는 insane-review + AGY Gemini 3.8 Flash high. 실제 사용 가능한 모델/도구는 새 세션에서 확인한다.

### 가장 최근 외부 리뷰 — 회수 완료

- **insane-review: REVISE, 차단 지적 4개.** 전문: `.review/phase4-integrated-insane-review.md`.
- 원본: `.insane-review/response_Syllva_20260909_122229_53260_f1e396.md`.
- 채팅: https://chatgpt.com/c/6aa0d123-0018-83ee-9dfa-2098b3cd6f1e
- 실제 검증 모델: `ChatGPT Latest (매우 높음 / Extended; UI version unspecified)`. 숫자 버전을 GPT-6로 임의 확정하지 않는다. Pro 사용 불가 시 매우 높음 fallback은 사용자 승인됨.
- **Gemini 재검증: GO.** `.review/phase4-integrated-gemini.md`, 근거 대조 `.review/phase4-integrated-gemini-audit.md`. 코드 인용 277줄 일치. 실행 명령 주장은 별도로 인증되지 않았으므로 root 검증을 대체하지 않는다.
- 처음 Gemini GO는 없는 API/틀린 소스 참조가 있어 폐기했다: `.review/phase4-integrated-gemini-unverified.md`. 이를 완료 근거로 쓰지 않는다.
- 두 리뷰 결과가 다르고, 이후 코드 수정도 시작했으므로 **현재 코드에 적용할 최종 GO는 없다**.

### 차단 지적과 이번 중단 직전 수정 — 아직 완료/전체 검증 아님

1. **호출자 승인 의미 비교 누락** — `src/uls/adapters/notion/base.py`, `_assert_supplied_matches_current`.
   - 정당한 저장 Queue가 APPROVED인 상태에서 호출자 객체의 Course, Source Ref, Review Reason, Source Hash=None을 위조해도 APPLIED 및 대상 쓰기 1회가 발생함을 root가 직접 재현했다.
   - 이번 수정: Phase 4만 canonical semantics를 기준으로 호출자의 존재하는 의미 필드를 검사한다. domain `_field`로 중복 별칭을 거절하고, 명시적 null도 검증한다. SourceRef는 기존 canonical parser의 provider/file identity 규칙을 사용한다. 인간 lifecycle/audit의 오래된 복사본은 의미 비교에 포함하지 않는다.
   - 수정 후 위 4개 재현은 모두 PolicyViolation, 대상 쓰기 0회였다.
   - **아직 영구 회귀 테스트 미추가.** 부분 매핑/Proposal-ID 문자열/정확한 매핑/SourceRef navigation-only 차이/명시 null/모든 별칭/Target DB 호환성을 추가 확인해야 한다. private domain helper 의존과 `_merge_records` 상호작용도 검토할 것.
2. **Producer 최종 body read 도중 source/graph 변경** — `src/uls/proposal/material_usage.py`, `_reread_trusted_inputs`.
   - 기존에는 fingerprint를 얻은 뒤 body를 읽고, body read 내부에서 상태가 바뀌어도 이전 상태로 생성할 수 있었다.
   - 이번 마지막 수정: 모든 body가 반환된 뒤 Session 및 후보 Material을 재조회하고 Course/Type/source binding/current fingerprint로 snapshot을 다시 비교한다. 다른 자료를 읽는 동안 Session이 바뀌는 경우도 검사하려는 구조다.
   - **이 패치는 추가한 직후 중단됐다. 구문/테스트 실행도 아직 하지 않았다.** post-read fingerprint와 body, mutable graph 객체, navigation-only alias 동작을 반드시 검증한다.
3. **초기 context 발급 도중 권한 변경** — `src/uls/retrieval/engine.py`.
   - 이번 수정: `_retain_current_evidence`를 추가했다. Session/직접 Material context에서 외부 읽기 이후 각 evidence-binding 쌍을 `_current_fingerprint_for_binding`으로 재검사하고 함께 제외한다. 독립적인 겹침 근거를 개별로 유지하는 의도다.
   - 직접 Material의 raw Type을 body read 이전에 캡처하도록 변경했다.
   - **아직 신규 회귀 테스트 미추가.** evidence/bindings의 zip 1:1 가정, 예외 처리, transcript/직접 Material/Usage, Verified/Role/range/Type/Course/source rebind, 독립 겹침 및 provisional 표기 등을 확인할 것.
4. **get_source_chunk 최종 body read 후 신선도 누락** — 같은 `engine.py`.
   - 이번 수정: chunk 파싱 후 반환 직전 `_current_fingerprint_for_binding(binding)`을 다시 호출한다. 권한 근거 소실은 LocatorNotAllowed, issued/body와 fingerprint 불일치는 LocatorStale로 거절한다.
   - **아직 신규 회귀 테스트 미추가.** 최종 read 내부에서 fingerprint 또는 전체 권한 근거를 변경하고 이전 body를 반환하는 사례를 검사한다.

3/4와 1 패치 후 기존 `test_phase4_rev3_retrieval.py` + `test_phase4_rev3_approval.py` **45 passed**. 이는 신규 결함 회귀 테스트도 아니고 전체 검증도 아니다. 그 이후 2번 Producer 패치를 추가했으므로 현재 전체 소스가 검증됐다고 말하면 안 된다. 최종 확인 뒤의 변경은 없다는 이전 90파일 hash 맵도 이제 과거 snapshot이다.

### 검증 기준과 검토 패킹 의존성 누락

- **이번 부분 수정 이전** 전체 테스트: Python 3.11.16 / 3.14 모두 465 passed. 기록 `.review/phase4-root-rev3-pytest311.txt`, `phase4-root-rev3-pytest314.txt`.
- 이전 정적 검사: Ruff 175 vs baseline 190, mypy 74 vs baseline 76. 기존 오류가 있으므로 “타입 오류 0”이 아니다. `.review/phase4-root-static-audit.md` 참고.
- 기존 통합 패킹은 90파일 / 약 309,593 tokens였으며 두 테스트의 실제 import 의존성이 빠졌다. Reviewer는 실행 가능 subset 325 passed를 보고했고, 이 누락 때문에 향후 GO 불가라고 명시했다.
- root가 AST 전이 import를 추적해 13파일을 추가한 **초안**: `.review/phase4-integrated-review-files-rev4.txt` (현재 103파일). state/sqlite, enrichment/writer 및 관련 orchestration 등을 포함한다.
- 별도 복원 디렉터리 `/tmp/syllva-phase4-rev4-pack-check`에서 실행한 결과 **326 passed, 4 failed**. 원인은 `src/uls/state/migrations/*.sql` 데이터 파일 누락에 따른 `no such table`이다. **SQL 파일은 아직 manifest에 추가하지 않았다.** Python import closure만으로 부족함을 확인한 상태다.
- 다음 담당자는 SQL 및 필요한 비-Python 리소스, 새 회귀 테스트까지 manifest에 포함하고, 패킹만으로 복원한 디렉터리에서 포함 테스트 전체를 실행해 closure를 검증해야 한다. 복원본은 현재 소스 패치보다 오래됐으므로 다시 복사할 것.
- 패킹은 comments/blank lines/function bodies를 제거하지 않는다. 대용량 truncation 경고가 있었으므로 전체 검토 확인 없이 GO로 처리하지 않는다.

### 승인/외부 실행 문제와 브라우저 상태

- 자동 승인 `auto_review`에서 **검토 자체가 90초 deadline**에 걸려 CreateProcess가 실행 전에 거절된다. 위험 판정이 아니다.
- 분석 기록: `.review/approval-timeout-diagnosis.md`, `.review/approval-timeout-evidence.json`. 누적 승인 문맥의 pre-turn compaction이 deadline을 소진한 근거가 있다. 설정으로 deadline을 늘리는 지원 항목은 찾지 못했다.
- 사용자가 사용자 승인 모드로 바꾼 뒤 실제 외부 리뷰 실행에 성공했다. **이번 마지막 턴 환경은 다시 auto_review**였고, Luna 실행도 같은 시간 초과로 프로세스 생성 전에 실패했다. 사용자가 새 세션에서 모드를 확인해야 한다. 제한을 우회하거나 승인 정책을 임의 변경하지 말 것.
- 실패한 Luna 명령: `codex exec -m gpt-5.6-luna -c 'model_reasoning_effort="max"' --sandbox workspace-write ...`. 프롬프트 `/tmp/syllva-phase4-rev4-implementation.txt`. **Luna는 실행되지 않았고 보고서도 없다.** root가 위 부분 패치를 수행했다.
- 새 세션이 Luna에게 위 프롬프트를 재사용하면 이미 root 부분 패치가 있다는 사실을 반드시 추가할 것. 전체 과정을 다시 처음부터 시작하거나 다른 변경을 덮어쓰지 않도록 한다.
- 기존 Syllva 프로젝트 URL: `https://chatgpt.com/g/g-p-6a9fdbd2dc3081919990a6607f8fe7c4-syllva-eeb93c01/project`. 로그인은 정상인데 프로젝트 화면이 ‘다시 시도하기’ 오류/정상 화면을 오가며 검증 실패했다.
- **사용자 명시 예외 승인:** “프로젝트가 안 열리면 새로 하나 만들던지 새 채팅으로 진행”. 이 허용으로 앞 리뷰는 일반 새 채팅 `--no-project`에서 완료했다. 기존 프로젝트 강제 조건 때문에 무한 반복하지 않는다. 전송 여부를 먼저 확인해 중복 리뷰를 방지한다.
- `scripts/review_gpt6_pro.py`는 설치된 insane-review 0.6.2를 호출하는 호환 wrapper. 최신/매우 높음 검증, 완료된 assistant 응답만 회수, 강제 답변 금지, 기존 URL 보존, pinned cached repomix를 지원한다.
- 이번 wrapper 수정: 프로젝트 readiness를 최대 45초 기다리고, 같은 URL의 정상 composer를 이미 확인했으면 중복 navigation을 피한다. 컴파일은 통과했으나 해당 개선으로 프로젝트 실행이 성공한 것은 아니다.
- cached repomix: `SYLLVA_REPOMIX_CLI=/Users/admin/.npm/_npx/a3bdd89f716944ac/node_modules/repomix/bin/repomix.cjs` (1.15.0, 원래 security 검사는 유지).
- 성공했던 새 채팅 launcher `/tmp/syllva-phase4-side-new-chat-review.py`는 **과거 90파일 manifest를 사용**한다. 다음 리뷰에서 그대로 실행하지 말고 완성된 새 manifest/prompt/log로 갱신한다.
- Gemini launcher 형태: `agy --model gemini-3.8-flash-high --effort high --mode plan --sandbox --print-timeout 30m --output-format text --print ...`. 참고 prompt `/tmp/syllva-phase4-integrated-gemini-evidence-prompt.txt`. 실제 파일 인용을 요구하고 이전 리뷰 결과는 주지 않는다.
- 새 세션에서도 동일 working tree를 사용해야 `.review` 및 `/tmp` 자료를 바로 이용할 수 있다. `.review`는 Git에 포함되지 않는 로컬 자료이므로 새 clone에는 따라가지 않는다.

### 새 세션의 실행 순서

1. 이 최신 절, `CLAUDE.md`, rev6 계획과 최신 insane-review 전문을 읽는다. 현재 미커밋 소스를 보존한다.
2. 부분 패치를 점검하고 4개 지적의 영구 회귀 테스트를 작성해 재현/수정을 완성한다. read 중 상태 변경을 직접 주입하고, CAS를 보장한다고 주장하지 않는다.
3. Python 3.11/3.14 전체 테스트, 필요한 compile/Behavior projection/기존 baseline 대비 정적 검사를 수행한다. 오류가 나면 수정 후 필요한 검사만 재실행한다.
4. 새 테스트·전이 import·SQL 리소스가 닫힌 review manifest를 만들고 별도 복원본에서 포함 테스트 전체를 실행한다. 새 hash/audit를 기록한다.
5. **한 번의 넓은 통합** insane-review + 독립 Gemini 리뷰를 실행·회수한다. Pro 불가 시 최신/매우 높음 검증 유지. 결함은 먼저 일괄 수정하고 전체 검증 후 재리뷰한다.
6. 두 리뷰의 적용 가능한 GO 및 총괄 검증이 충족돼야 Phase 4 완료로 기록한다. 이후 handoff/verification/execution 문서를 일치시킨다. Phase 5–8이나 push는 진행하지 않는다.

**중단 시 실행 상태:** 완료된 두 외부 리뷰 수집 프로세스는 종료했다. 마지막 Luna는 승인 단계에서 시작되지 않았다. 별도 패킹 검증 pytest도 종료했다. 이 작업에서 새 리뷰/수정/테스트를 백그라운드로 계속 돌리지 않는다. 아래 과거 기록의 “다음 작업” 문구보다 위 순서를 따른다.

---

## 1. 프로젝트 개요

**University Learning System (ULS) v1.2** — 개인 학업 지식·검색 시스템.
Model-agnostic · MCP-centered · Local-primary · Single-active-worker · Cross-platform (Python 3.11+).

> **ULS가 어떤 컨텍스트가 허용/관련되는지 결정하고, AI 클라이언트는 ULS가 제공한 컨텍스트 위에서 추론한다.**

권위 문서(코드가 충돌하면 아래가 우선):
- `university-learning-system-v1.2-design-frozen.md` (설계, frozen)
- `university-learning-system-v1.2-implementation-spec-frozen.md` (구현 명세, frozen)

역할·경계 요약: `Drive`=원본+정규화 파생, `Notion`=학술 그래프/상태/검증, `GitHub`=정확 ref 코드,
`Retrieval Engine`=scope/authority/freshness/provenance, `MCP`=read-only 경계, Skills=행동/데이터접근 아님.

---

## 2. 역할 분담 & 작업 파이프라인 (반드시 준수 — CLAUDE.md와 동일)

| 단계 | 담당 |
|---|---|
| 총괄/관리 | **현재 Codex 주 에이전트** — 요구 해석·계약 판단·검증·통합·머지 |
| 계획 초안 | **Claude Sonnet 5 high** |
| 개발/구현 | **Codex Luna max** (`gpt-5.6-luna`, reasoning=max) |
| 리뷰 | **insane-review (GPT-6 Pro, 불가 시 사용자 승인한 최신/매우 높음)** + **AGY Gemini 3.8 Flash high** (2중 독립) |

**2026-09-08 사용자 지정:** Phase 4부터 insane-review를 GPT-6 Pro로 실행한다(`--model pro --require-model "GPT-6"`). 실제 모델·Pro 추론 단계 검증 실패 시 중단하고 자동 대체하지 않는다. 설치/의존성·브라우저·로그인·활성 GPT-6 Pro 검증 완료(아래 실행 래퍼 사용). 아래 과거 Phase의 GPT-5.6 리뷰 기록은 당시 이력이다.

**Phase 4~8 연속 작업 착수(2026-09-08):** 사용자 승인으로 현재 Codex가 총괄하고 Sonnet 5가 계획 초안을 작성한다. 아래 파이프라인의 Opus 역할은 현재 총괄이 수행한다. 기준 테스트 238개·Behavior Contract lint·108개 Python 파일 구문 검사 통과. Sonnet 서브에이전트는 encrypted-task 전달 오류로 실행 불가하여 Claude CLI로 전환했고, 사용자 로그인 후 인증 복구를 확인했다. Gemini는 `agy` CLI로 실행 가능. ChatGPT 전용 브라우저 로그인 및 **GPT-6 Pro 활성 UI 검증 통과**. 설치된 insane-review 0.6.2의 모델 판독이 숨겨진 구모델 목록을 잘못 읽어 `scripts/review_gpt6_pro.py`에서 활성 헤더·Pro 슬라이더 검증만 보정한다. Phase 4 계획 작성 중이며 Phase 4~8 구현/리뷰 완료를 의미하지 않는다.

**최신 실행 상태:** Sonnet Phase 4 rev1 → Gemini 독립 리뷰 REVISE → Sonnet rev2 → 현재 총괄 통합 rev3를 `docs/plans/phase4-material-usage.md`에 저장했고 **Gemini 재리뷰 GO**를 받았다(`.review/phase4-plan-rev3-gemini.md`). 새 채팅의 Pro 메뉴는 사용량 한도로 비활성화되어 **“2026년 9월 13일 후에 다시 시도”** 안내를 확인했다. 앞선 `6 Pro` 선택 표시와 달리 신규 GPT-6 Pro 리뷰 실행은 현재 불가능하며, 리뷰 프롬프트는 전송되지 않았다. 사용자에게 접근 복구/리뷰 경로 변경 여부를 요청했고 답변 대기 중이다. 상세 상태·재개 지점은 `docs/plans/phase4-8-execution.md` 참조. Phase 4 구현 관문은 아직 통과하지 않았고 Phase 5~8도 미착수다. 커밋/푸시는 하지 않았다.

```text
1. 계획 수립(Opus) → 2. 계획 리뷰(2중) → 3. 구현(Codex) → 4. 검증(Opus)
→ 5. 2중 리뷰 → 6. 커밋/푸시(둘 다 GO + 검증 일치 시에만)
```
리뷰어가 엇갈리면 **Opus가 직접 코드로 재현해 타이브레이크**. 지적으로 재수정 필요 시 3~5 반복.

**중요 교훈:** AGY가 GO를 준 지점에서 GPT-5.6이 실 결함(fail-open, 계약 드리프트, self-approval 등)을
Phase 1/2 내내 반복적으로 잡았다. **단일 리뷰어는 불충분** — 반드시 2중 + Opus 재현.

---

## 3. 완료 상태

**재개 승인(2026-09-08):** 사용자가 "Pro 안되면 매우 높음으로 진행"을 명시했다. Pro quota 복구를 기다리는 조건은 해제되었으며, insane-review 최신/매우 높음 + Gemini high로 Phase 4 계획 리뷰부터 재개한다. 실제 UI model/effort를 검증·기록하고 Phase 4~8 관문을 계속 적용한다.

| 항목 | 상태 |
|---|---|
| 저장소 스캐폴드(§3 레이아웃), CLAUDE.md, 계약/클라이언트 프로젝션 | ✅ `5bb9d34` |
| Phase 1 Core Hardening (state/ephemeral/orchestration/config/human-gate) | ✅ `ab3c9a1`+`63cb951` |
| Phase 2 Transcript Vertical Slice (normalization/ingest/retrieval get_session_context) | ✅ `6572feb` |
| Phase 3 Enrichment & Freshness (producer: LLM adapter/enrichment schemas·generators/writer) | ✅ `f4ab8b0` |
| 테스트 | **238 passing** (모델 독립 contract/unit/integration) |

검증 도구 환경: `codex` CLI(`gpt-5.6-luna`), `agy` CLI(`gemini-3.8-flash-high`), `insane-review` 플러그인(GPT-5.6 Sol),
pytest+pyyaml 설치됨(인터프리터: `/Library/Frameworks/.../python3.14`).

### 구현된 핵심 계약 (리뷰로 확정된 불변식)
- **도메인**(`src/uls/domain/`): Locator 문법/AST/직렬화/typed containment(§6.3, 문자열 prefix 인가 금지),
  enums/ids/source_ref/provenance/errors(§53). 표준 라이브러리만.
- **StateStore**(`state/sqlite.py`): 반복 마이그레이션, 결정적 `job_key`(§8.1.1), source-bound idempotent
  엔티티 할당(§8.6.1, caller 선점 불가), source_versions 일관성.
- **EphemeralStore**(`ephemeral/memory.py`): TTL·재시작 무효화, capability fingerprint 바인딩 + fail-closed
  `authorize_locator`(§25, current_fingerprint 미제공 시 deny, 불일치 시 LOCATOR_STALE).
- **Human-gate**(`adapters/notion/base.py`): 방어적 쓰기(§15.1, Verified/Scope Confirmed/Decision/State),
  create/update 분리, `ApprovalReader`/`HumanApprovalApplier`(human Decision By 필수, self-approval 차단),
  Automation Queue 상태기계, 시스템 경로 SUPERSEDED/FAILED만.
- **Retrieval**(`retrieval/*`): `resolve_entity`/`select_resolution` + `get_session_context(session_id,…)`,
  per-chunk 정확 capability allowlist(초과 인가 없음), §25 6검사(+role 현행성), SESSION authority(§17/§21,
  fetch order ≠ authority rank), freshness/stale-locator, read-only 어댑터 Protocol(§4/§13/§27).
- **Normalization/Ingest**: transcript verbatim + sidecar 타임스탬프(code-point offset), §17 커밋 순서 +
  Partial 3중 일관, 필수 collaborator/등록 fail-closed.

**공통 원칙: 모든 경계는 fail-closed.** 애매하면 거부/제외 — 절대 조용한 skip/합성/승격 금지.

---

## 4. 다음 단계 (frozen 순서)

구현 순서(§41): `Spike C0 → Spike M0 → VS0 → VS0-B → Spike G`, Phase 1~8(§42~§49).

- **Phase 3 — Enrichment & Freshness (§44)** ✅ **완료 (`f4ab8b0`)**: producer 구현 — `enrichment/{schemas,_common,session,material,exam,writer}.py`,
  `adapters/llm/{base,structured}.py`, `domain/enums.py(Explicitness)`, `NotionReader.get_material_enrichment`.
  explicit/inferred 분리·evidence locator 재해석·stale 제외·source fingerprint 전부 fail-closed. 소비자(RetrievalEngine)는 미변경.
  계획서 `docs/plans/phase3-enrichment.md` rev3, §7 에 **문서화된 결정론적 한계**(cross-store 가시성 창, 부정어 가드 밖 의미 판정) 명시.
  교훈: 구현 리뷰에서 Sol(GPT-5.6)이 AGY GO 를 여러 번 뒤집으며 실 fail-open(증거 상속, empty-READY, TOCTOU, §17 순서, 부정어 위조)을 잡음 — 5라운드 재수정 후 둘 다 GO.
- **다음 단계 → Phase 4 — Material Usage & Human Gate (§45)**: Material Usage/page-range proposal, 승인 경로, Verified 상태,
  multi-material Session 검색, unverified 정책. (Phase 2에서 read-only 소비만 했고 mutation/proposal은 여기로 미룸.)
- **Phase 5~8**: Exam/Activity(§46), GitHub 정확 ref 검색(§47), 클라이언트 패키징(§48), 데스크톱 자동화+remote MCP(§49).
- **MCP 트랙(별도)**: Spike C0(ChatGPT remote 연결)/M0(로컬 MCP로 get_material_context)/VS0 — 엔진은 이미 MCP 없이
  직접 호출 가능하므로 `mcp/` 스텁에 read-only 도구를 배선하면 됨(§19~§24). 현재 `mcp/`는 스텁.

**착수 방법:** Phase마다 `docs/plans/<phase>.md` 계획서를 Opus가 작성 → 2중 계획 리뷰 → Codex 구현 → 검증 → 2중 리뷰 → 커밋.
(예시 계획서: `docs/plans/phase2-transcript.md` rev2 참고.)

---

## 5. 개발/리뷰 실행 방법 (그대로 복사해 사용)

구현 위임(Codex Luna max) — **프롬프트는 파일로, stdin은 `< /dev/null`로 닫을 것**(안 닫으면 hang):
```bash
codex exec -m gpt-5.6-luna -c model_reasoning_effort=max --sandbox workspace-write \
  --skip-git-repo-check -C /Users/admin/Project/Syllva \
  "$(< /path/to/prompt.md)" < /dev/null > /path/to/codex.log 2>&1
```
리뷰(2중 독립):
```bash
# insane-review (GPT-6 Pro). 모델명과 Pro 추론 단계를 모두 검증; 접근 불가 시 중단
python3 scripts/review_gpt6_pro.py --target /Users/admin/Project/Syllva \
  --include "src/…,tests/**,…-implementation-spec-frozen.md" --model pro --require-model "GPT-6" \
  --prompt-file /path/to/review.txt
# AGY Gemini 3.8 Flash high
agy --dangerously-skip-permissions --model gemini-3.8-flash-high --effort high \
  --print-timeout 20m --prompt "$(< /path/to/review.txt)" < /dev/null > agy.log 2>&1
```
검증(Opus 직접):
```bash
python3 -m py_compile $(git ls-files 'src/uls/**/*.py')
python3 -m pytest -q tests/
python3 scripts/lint_behavior_projection.py   # Behavior Contract 드리프트
```

주의: `codex exec`에 `--full-auto` 플래그 없음(→ `--sandbox workspace-write`). heredoc과 codex를 한 명령에
합치지 말 것(stdin 충돌로 hang). `pumasi.sh start`는 자동 승인 분류기에 차단될 수 있어 `codex exec` 직접 사용.

---

## 6. 알려진 제약 / 낮은 우선순위 항목

리뷰에서 "정상 StateStore에는 실 위험 없음(malformed/incomplete adapter 전용)"으로 **릴리스 OPEN에서 제외**된 hardening 갭 —
Phase 3+에서 정리 권장:
- `_require_ingest_collaborators()`가 `get_job`을 필수 목록에 넣지 않음(프로즌 Protocol엔 있음). get_job 없는 duck-typed
  adapter가 PROCESSING 객체만 반환하면 재조회 없이 신뢰될 수 있음.
- `_allocate_entity()`가 반환 ID의 문법(parse_entity_id)만 검사하고 course/type 일치까지는 재검증 안 함.
- 과거 버그 버전이 이미 잘못된 `canonical_entity_id`를 영속 저장한 legacy 데이터는 §8.6.1 idempotency로 자동 교정되지 않음 → migration/scrub 필요.

기타:
- MCP `mcp/` 및 Claude package/ChatGPT app은 스텁. 클라이언트 지원 상태는 배포 의존(§27~§30).
- 실제 provider(Google/Notion/GitHub) SDK 연동은 미구현(어댑터 read-only Protocol + Fake로 검증 중). `adapters/*/api.py` 스텁.
- 원격 MCP/스케줄러/Goodnotes(§33)/CONCEPT 벡터검색은 v1.2 비목표 또는 후속 Phase.

---

## 7. 참고 파일
- 계획서: `docs/plans/phase2-transcript.md`
- 가이드: `CLAUDE.md`, `README.md`, `CHANGELOG.md`
- Behavior Contract: `contracts/study-behavior.md` (+ `clients/…` 프로젝션, `scripts/lint_behavior_projection.py`)
- 테스트: `tests/{unit,contract}/`, 픽스처 `tests/fixtures/fake_{notion,drive}.py`

## 2026-09-08 Phase 4 review recovery and revision in progress

- User explicitly directed continued revision after the recovered insane-review verdict. Sonnet 5 high is preparing draft rev4 via authenticated Claude CLI; log `/tmp/syllva-phase4-sonnet-rev4.log`.
- Current rev3: Gemini GO, insane-review REVISE. Preserve the latter report at `.review/phase4-plan-rev3-insane-review.md`; root validated all five required changes in `.review/phase4-rev3-root-response.md`. No Phase 4 source implementation gate has passed.
- Review ran with UI-verified Latest / 매우 높음, per user-approved fallback when Pro is unavailable. Do not label this as numeric GPT-6 or Pro.
- Repository ChatGPT project repaired and review moved: https://chatgpt.com/g/g-p-6a9fdbd2dc3081919990a6607f8fe7c4/project. Default project mode is mandatory for subsequent reviews; omit `--no-project`.
- Next: integrate and verify Sonnet rev4 → independent Gemini and insane-review on exact same snapshot → implementation after dual GO. Continue toward Phase 8 under original authorization.


### Phase4 rev6 gate passed; implementation active

- Independent plan gates: insane-review Part A GO and Part B GO, plus Gemini full-plan GO. Reports `.review/phase4-plan-rev6-{a,b}-insane-review.md` and `.review/phase4-plan-rev6-gemini.md`. No blockers remain on the reviewed canonical SHA256 `901c3e046ede6b39f9863d84bf4aeb41b4bbf3b4244ff9280e6940d01a768058`.
- Both ChatGPT reviews used the repository project and UI-verified Latest / 매우 높음. No forced answer.
- Luna max implementation dispatched on the existing branch, prompt `/tmp/syllva-phase4-luna-implementation-request.txt`, log `/tmp/syllva-phase4-luna-implementation.log`, final report `/tmp/syllva-phase4-luna-implementation.md`. Implementation gate is not a completion/release gate. Root verification and both independent implementation reviews remain required before a local commit. No push authorized.
- Optional review refinements for implementation QA: Queue APPLIED audit commits then raises must replay idempotently; use shared validated page index; preserve exact Course cardinality and dereference checks.
- Phase5/6 Sonnet raw drafts remain unapproved; Phase7 planning awaits Sonnet session reset (03:40 Asia/Seoul), not login repair. Continue original Phase4–8 scope.


### User scope update: Phase4 only

User explicitly narrowed active work to Phase4 only. Finish current Luna Phase4 implementation, root verification, independent implementation review and fixes. Do not start Phase5–8 planning/implementation or retry Sonnet Phase7 quota automatically. Existing future-phase drafts are retained as inactive reference, not active work.


### Root regression steering checkpoint

- First Luna run deliberately interrupted after baseline migration (239 full-suite passes) to deliver root findings; process31313 exited, unified session71738 closed. Root temporary regression suite found17 failures/11 passes, report `.review/phase4-root-regressions-first-run.txt`. Do not mistake baseline239 for Phase4 acceptance.
- Continuing same working tree with Luna max corrections: prompt `/tmp/syllva-phase4-luna-fixes-request.txt`, log `/tmp/syllva-phase4-luna-fixes.log`, final report `/tmp/syllva-phase4-luna-fixes.md`. The prompt explicitly includes all11 root finding groups and requires all28 temporary regressions plus permanent broader Phase4 tests. No active first-run implementer remains. Root still owns final verification and independent implementation reviews.
- AGY actual model ID verified: `gemini-3.8-flash-high`, run CLI with escalation (read-only service/log initialization needs host access), --mode plan --sandbox --effort high; no permission-bypass flags.
- User scope Phase4 ONLY remains in force.


### Phase4 implementation verification and independent review (2026-09-09)

- Active user scope remains Phase4 ONLY. Luna max corrections finished; root added immediate-prewrite approval revocation and Python3.11 compatibility corrections, and permanent follow-up revocation/independent-overlap tests.
- Root full verification:296 passed on Python3.11 and3.14; compileall, Behavior projection lint and git diff check pass. Static baseline: Ruff190 ->188 diagnostics, mypy76 ->74 errors; existing debt remains and intentional fail-closed exception handling is documented in `.review/phase4-root-static-audit.md`.
- Independent AGY Gemini3.8FlashHigh implementation review GO, zero blockers; `.review/phase4-implementation-gemini.md`. Root independently applied the same optional page-local variable naming correction.
- insane-review implementation is in progress as four full-source partitions: A Queue/guard/applier; B producer/trusted derivative; C engine/scopes/freshness; D capability/store/resolver. Each uses the existing Syllva ChatGPT project, verified Latest/매우 높음 (numeric version unspecified), no forced answer and no code compression. Full-source manifests `.review/phase4-implementation-{a,b,c,d}-files.txt`; all relevant implementation union plus root/Gemini integration review covers cross-partition calls.
- Completion/local commit remains gated on all web review verdicts and resolution of any concrete blockers. No push. Phase5–8 inactive.


### Integrated smoke found additional blockers; completion gate remains closed

- Root actual GuardedNotionWriter→producer→ApprovalReader→applier smoke found two defects after initial Gemini GO: new Usage branch uninitialized operation, and wrapper requiring create fields on partial updates. Root fixed both; permanent guarded workflow4cases cover create/reuse, update preserving Verifiedfalse/true, producer retry and approval replay. Full suites now300 passed each on3.11/3.14.
- New unresolved root reproductions: global payload char budget30 emits60 across Session+Material; required Usage ID=None accepted by wrapper. Recorded in `.review/phase4-root-inflight-findings.md` items14–17.
- Four initial web implementation reviews sent; A/B already exposing additional concrete boundary issues and C/D still checking. Retrieve final reports, reproduce/fix blockers, then re-review affected snapshots plus Gemini; initial Gemini GO is not final approval after semantic corrections. Logs `/tmp/syllva-phase4-implementation-{a,b,c,d}.log`. Do not commit or claim Phase4 complete yet.


### Active repair ownership after web implementation REVISE

- B,C,D finalreports saved `.review/phase4-implementation-{b,c,d}-insane-review.md` (allREVISE); A originalcaptureinvalid(userprompt), samechat harvestrunning. Do not treatinvalidAasverdict. Collector wrapperstrictassistantGO/REVISE+DOMcapture now avoidsdisconnectedplaceholder/sharedclipboard miscapture.
- Luna producer run owns only `src/uls/proposal/material_usage.py` and `tests/contract/test_phase4_producer_boundaries.py`; prompt/log/final `/tmp/syllva-phase4-producer-review-fixes{,-request}.txt/.log/.md` (actualrequestfilename `...-request.txt`). Initialassignedglobalbudget/evidence/multicandidate; fullBreportarrivedlater and requires follow-up for remaining B defects after thisrun.
- Separate Luna retrieval run owns retrieval/*,domain/page_range.py,domain/course_identity.py,config/validation.py and relevanttests exceptrootwriter/producerfiles. Prompt `/tmp/syllva-phase4-retrieval-review-fixes-request.txt`, log/final samebase `.log/.md`. ImplementsallC/D and BsharedUsageappID/strictfloat issues. Root mustintegrate helper intoNotion;producer followup uses samehelper.
- Root owns Notionbase/guarded,FakeNotion,writerboundary+guardedworkflowtests. No concurrenteditstosameownedfiles. Fullsuite duringactiveedits may transientfail; only finalstableverificationcounts.
- User Phase4ONLY, no commitsuntilfinaldualreviewGO, no push.


### Repair checkpoint (producer follow-up active)

- First producer runfinished; report `/tmp/syllva-phase4-producer-review-fixes.md`,6newboundarytests;87Phase4cases atthatintermediatesnapshot. SecondproducerLunamax nowactive: request `/tmp/syllva-phase4-producer-review-final-request.txt`, log `/tmp/syllva-phase4-producer-review-final.log`, final `/tmp/syllva-phase4-producer-review-final.md`; sameexclusiveproducer+producerboundarytestownership. Addresses fullBremainingconfidence/preflight/stale-suppliedMaterial/configdefaults/navigationidentity/strictappIDs/floats/rawType.
- Retrieval/domainLuna stillactive on C/D and sharedhelpers. RootNotionintegrated usage_app_id and rawTypecomparisons. Openrootregression: flatlowercaseid fallback stillmisreadas appID; test_phase4_writer_boundary missing_app_id case currentlyfails. Fixsharedhelperafterownerfinishes; do notweaken assertion.
- A originalreview timedout atserver; harvesterstoppedPID38797. Invalidcapturearchived, noAverdict. FreshcorrectedA reviewrequired. B/C/D REVISE reports retained.


### Root prewrite graph/source correction

- Added7callbackregressions to test_phase4_prewrite_approval.py: dependencyreadchanges UsageRole/range/Session, MaterialType, CourseKey, fingerprint, or exactsibling. All7failed beforefix; prewrite hadonlyQueuegrantrefresh.
- Root nowruns fulltarget/dependencyreconciliation beforetargetmutation AND desiredauditrecovery, withpostbodygraph/ref/fingerprintchecks,currentpagevalidation,physicalUsageuniqueness andexactduplicates. Queuegrantremains immediatelyprewrite.52prewrite/remainingcases and9positiveguarded/lifecyclepass; latest16prewrite+guardedpass. FullsuitependingtwoactiveLunas.
- Source ofrootfindings andtestlogs: `.review/phase4-root-inflight-findings.md`, `.review/phase4-root-prewrite-graph-before.txt`, `.review/phase4-root-preflight-after.txt`.


### Corrected implementation is stable; rev2 reviews active

- Both Luna correction runs finished. Reports `.review/phase4-{producer,retrieval}-correction-report.md`. Root finalcurrent fullpytest373passed onPython3.11 and3.14; behaviorlint,3.11compileall,diffcheckpass. No implementation writers active.
- Static: mypy74 vsbaseline76 withno new normalizeddiagnostics; Ruff175 vsbaseline190. Two root-test I001importsortwarnings remain toclean afteractivereviews (implementationlogicunchanged);othernewlintdiagnostics intentionalvalueparsing/failclosedexceptionhandling. Details `.review/phase4-root-static-audit.md`.
- Exactsource/testSHA256 `.review/phase4-implementation-rev2-all-hashes.json`; per-partreviewhashesandmanifests `.review/phase4-implementation-rev2-{a,b,c,d}-{hashes.json,files.txt}`. Allsnapshotfilesunchanged afterroot373tests.
- Rev2 webreviews A,C,D,B dispatchedinexistingSyllvaproject Latest/매우높음, strictassistant-completionguard+DOMcapture, noforcedanswer. Logs `/tmp/syllva-phase4-implementation-rev2-{a,b,c,d}.log`. Rootpackaudits nofileomissions; tokensA117510 B119313 C118872 D116736.
- IndependentGemini fullcorrectedreviewactive: `/tmp/syllva-phase4-implementation-rev2-gemini.log`, modelgemini-3.8-flash-high/read-only plan+sandbox. Keepindependence: noreviewverdictinput.
- Gates stillclosedpendingreviews; fixes/re-reviewsifneeded, thentest-onlyformatcleanup/finalverification/handoff/localcommit. No push; Phase4ONLY.
