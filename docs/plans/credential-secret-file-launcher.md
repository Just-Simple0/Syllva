# 메인 워커 Protected Secret File 설계 (계획서 · rev5 — launcher는 §7.2에서 폐기됨)

**작성:** Astra (총괄) · **상태:** rev5 — rev1~rev4 각각에 대한 독립 리뷰 2건씩(총 8건) 완료.
rev4 재리뷰에서 Gemini 3.8 Flash high는 **GO**, insane-review는 "매우 좁은 REVISE"(payload
ownership 문장 1개 + credential-resolver.md 실제 개정 + 3분류 매핑 완결 + 편집 잔재 2곳)였다.
지적사항을 전부 rev5로 반영했다(본 문서 인라인 수정 + credential-resolver.md 실제 amend).
재리뷰 대기.
**리뷰 기록 (rev1, 2026-09-17):**
- insane-review(웹 ChatGPT, 모델 `GPT-5.6 Sol (매우 높음)` — Pro 티어 비활성으로 사용자 승인된
  fallback 사용, Pro라 칭하지 않음): **REVISE**. 저장 `.insane-review/response_Syllva_20260917_103047_41839_ddbcf6.md`.
- Gemini 3.8 Flash high (`agy`, `--mode plan`, 읽기 전용): **REVISE**.

두 리뷰 모두 핵심 결함을 **동일하게** 지적했다: launcher가 secret file을 읽어 environment로 다시
올리는 구조가 `source: file`을 지원하는 `CredentialResolver`와 **secret read boundary가 중복/충돌**한다는
것. 이 지점을 rev2의 최우선 수정으로 반영했다. 그 외 TOCTOU 순서, 원자적 쓰기, Windows DACL,
CLI UX(설정 동기화, 이중 입력, 취소 처리, 신뢰 피드백) 지적도 모두 반영했다.

**배경:** `handoff.md` "향후 후속 작업 안내" 1번(2026-09-16) + 사용자 지적(2026-09-17, credential 주입 UX 문제)
**관련 문서:** `docs/plans/credential-resolver.md`(rev3, PLAN GO), `src/uls/config/credentials.py`,
`src/uls/config/_keyring_backend.py`, `scripts/knu_lms_sync.py`, `scripts/_lms_platform.py`
(`open_nofollow`/handle-relative 검증 패턴 — rev2의 secure read boundary가 재사용하는 대상)

> 이 문서는 **계획서**다. 구현은 AGENTS.md 워크플로에 따라 독립 계획 리뷰 GO 이후에만 시작한다.
> 이 작업은 인증/자격증명 코드 변경이므로 project AGENTS.md 기준 **risky** 로 분류한다.

---

## 0. rev1 → rev2 변경 요약 (리뷰 지적 대응표)

| 우선순위 | rev1의 문제 | rev2의 수정 | 지적한 리뷰 |
|---|---|---|---|
| P0 | launcher가 secret file을 읽어 environment로 재주입 → resolver의 `source:file`과 read boundary 중복/모순 | **launcher에서 raw secret 주입 전면 제거.** `CredentialResolver(source=file)`가 `uls` 프로세스 내부에서 secret을 읽는 **유일한 경계**가 됨. scheduler는 `uls`를 (필요 시 비밀 아닌 path 변수만 주입하는 얇은 launcher를 거쳐) 직접 실행 | insane-review, Gemini 공통 |
| P0 | `os.path.islink()` 사전검사 후 별도 open → TOCTOU | 신뢰 디렉터리 no-follow open → 그 handle 기준 target no-follow open → **동일 fd/handle**에서 regular-file/owner/mode-or-DACL/size 전부 검증 후 그 fd로 읽기 | insane-review, Gemini 공통 |
| P0 | 파일 크기 상한 없음 (DoS) | 최대 4096바이트, 초과 시 `secret_file_too_large` | insane-review, Gemini 공통 |
| P0 | 기존 `_atomic_json_write`(실패 시 temp 보존)를 그대로 재사용 예정 | secret 전용 writer: temp를 `O_CREAT|O_EXCL|O_WRONLY, 0o600`으로 **생성 시점부터** private, 실패 경로는 전부 temp unlink, fsync 후 `os.replace` | insane-review, Gemini 공통 |
| P0 | Windows DACL "현재 계정만" 요구가 SYSTEM/Administrators와 충돌 | 허용 trustee를 **현재 사용자 SID + SYSTEM(S-1-5-18) + Administrators(S-1-5-32-544)**로 명시, security descriptor read-back 검증 | insane-review, Gemini 공통 |
| P0 | `GOOGLE_WORKER_CREDENTIALS_FILE`이 environment-only로 남아 launcher가 못 채움 → 무인 실행 갭 해결 안 됨 | 이 두 값은 **secret이 아니라 경로**이므로 credential 저장소에서 분리: config.yaml의 일반(non-secret) 필드로 격상하고, 얇은 launcher가 그 경로 값만(비밀 아님) 환경변수로 주입 | insane-review |
| P0 | `credential set`이 TTY/echo 보장, 이중입력, 입력값 검증 없이 `getpass.getpass()`만 언급 | `sys.stdin.isatty()` 필수, `GetPassWarning` 발생 시 fail-closed(경고를 예외로 승격), **2회 입력 일치 확인**, 제어문자/개행 포함 시 거부 | insane-review, Gemini 공통 |
| P0(Windows) | Task 계정 identity/launcher lifecycle 미정의 | provisioning은 **Task UserId와 동일 계정 컨텍스트**에서 실행해야 함을 명시, launcher는 절대경로 실행파일 고정 + 자식 종료 대기 + exit code 전달 | insane-review |
| P1 | `credential set`이 `environment` source를 그냥 거부만 함(닭-달걀) | `config.yaml`에 **이미 선언된 source에 대해서만** `set` 허용(암묵적 `--source` 전환 제거). `environment`로 선언된 이름은 여전히 대상 밖이지만, 그 경우 어떤 저장소가 애초에 허용되는지(`ALLOWED_SOURCES`)와 config에 어떤 줄을 추가해야 하는지 정확히 안내 | Gemini |
| P1 | 성공 시 값 노출 없는 피드백이 "정말 등록됐나" 불안 유발 | 값은 여전히 안 보여주되 **길이/마스킹 접두사/저장 경로/권한 비트**를 요약 출력 | Gemini |
| P1 | 덮어쓰기 확인 없음, `Ctrl+C` 처리 없음 | 기존 값 존재 시 확인 프롬프트(또는 `--overwrite`), `KeyboardInterrupt`는 트레이스백 없이 `Aborted.`(exit 130) | Gemini |
| P1 | `GOOGLE_*_CREDENTIALS_FILE` 권한 검사가 doctor 전용 ad-hoc | 공유 secure-file validator를 **credential의 post-resolution 단계**로 통합 — doctor/run/MCP가 동일한 fail-closed 결과를 봄 | insane-review |
| P2 | 에러 코드만 노출, 조치 방법 없음 | 고정 에러 코드 + 그 코드에 대응하는 **조치 안내 문구**(예: `chmod 700 <경로>`) 페어링 | Gemini |
| P2 | cross-credential 원자성/백업 문서 미정의 | `credential set`은 **정확히 하나의 NAME만** 원자적으로 교체(묶음 rotation 비목표로 명시), 백업/복구 문서에 protected file/keyring이 일반 백업 대상이 아니라는 점 추가 | insane-review |

---

## 1. 문제 정의 (rev1과 동일, 유지)

### 1.1 UX 문제
현재 크리덴셜을 넣는 유일한 경로는 터미널에서 직접 `export GOOGLE_WORKER_CREDENTIALS_FILE=...`
같은 명령을 실행하는 것이다. keyring source가 이미 허용된 세 크리덴셜조차, 실제로 keyring에 값을
써 넣는 대화형 진입점이 `uls` 메인 CLI에 없다.

### 1.2 무인 실행 문제
`deployment/README.md`는 "process environment 또는 private service wrapper로 공급하라"고만
안내하고 구체적인 wrapper 설계가 없다. launchd/Task Scheduler 설정에는 환경 주입 수단이 전혀 없다.

### 1.3 범위 밖
- `REMOTE_MCP_SECRET`의 OAuth/OIDC 전환, 실제 운영 환경 라이브 e2e 확인 — 별도 후속 항목.
- `GOOGLE_*_CREDENTIALS_FILE`이 가리키는 provider JSON **파일 내용물**을 이 저장소 형식으로
  옮기는 것 — 이 값들은 경로이므로 credential 저장소 밖(§2.4)에서 다룬다.

---

## 2. 제안 아키텍처 (rev2)

### 2.1 새 source 타입: `"file"` — CredentialResolver가 유일한 read boundary

`ALLOWED_SOURCES`에 원시 시크릿 문자열을 담는 두 항목에 `"file"`을 추가한다:

| 크리덴셜 | 허용 source (rev2) |
|---|---|
| `NOTION_WORKER_TOKEN` | `environment`, `file` |
| `REMOTE_MCP_SECRET` | `environment`, `file` |

**중요한 변경(P0):** `source: file`로 선언된 크리덴셜은 `CredentialResolver._diagnose_one()`이
**직접** 보호된 파일을 읽는다(§2.3의 secure read boundary 재사용). **다른 어떤 프로세스(launcher
포함)도 이 secret을 environment로 옮기지 않는다.** 이렇게 하면:
- 기존 `diagnose()`/`resolve()` 1회 read, downstream은 snapshot만 소비한다는 계약이 그대로 유지된다.
- worker run이 필요로 하지 않는 크리덴셜(예: `REMOTE_MCP_SECRET`)까지 프로세스 시작 전에 읽거나
  검증 실패로 worker 기동을 막는 일이 없다 — 기존 composition-root별 `required`/`optional` 집합
  분리가 그대로 보존된다.
- silent fallback 금지 계약도 keyring과 동일하게 유지(파일이 없거나 검증 실패 시 `"error"`만 반환,
  `environment`로 넘어가지 않음).

`GOOGLE_WORKER_CREDENTIALS_FILE`/`GOOGLE_MCP_CREDENTIALS_FILE`은 §2.4에서 별도로 다룬다(경로이지
raw secret이 아니므로 이 표에서 제외).

### 2.2 보호된 secret 파일 — 위치, 쓰기 계약

- macOS: `~/Library/Application Support/Syllva/secrets/<name-lowercase>.secret`, 디렉터리 `0700`.
- Windows: `%LOCALAPPDATA%\Syllva\secrets\<name-lowercase>.secret`.
- 최대 크기 **4096바이트**. 초과분은 쓰기 단계에서 거부(`secret_value_too_large`).
- 값에 개행(백슬래시-n 또는 백슬래시-r 문자)이나 NUL이 포함되면 등록 단계에서 거부한다(자동 strip 금지 — 임의
  normalization은 값을 조용히 바꿀 수 있어 위험하다는 리뷰 지적 반영). 저장 시에는 사용자가 입력한
  bytes 그대로(개행 하나 추가 없이) 쓴다.
- **원자적 쓰기 계약(secret 전용, 기존 `_atomic_json_write` 재사용 금지):**
  1. 대상과 같은 디렉터리에 `.tmp_<name>_<pid>` 임시 파일을 `os.open(path, O_CREAT|O_EXCL|O_WRONLY, 0o600)`으로
     **생성 시점부터** private 권한으로 연다(생성 후 `chmod`가 아님 — 레이스 방지).
  2. Windows에서는 이 시점에 이미 §2.3의 canonical DACL이 적용된 상태여야 한다(secret bytes를
     쓰기 전에 보호가 걸려 있어야 함).
  3. secret bytes를 쓰고 `flush`+`fsync`.
  4. `os.replace(tmp, target)`으로 원자적 교체, 부모 디렉터리 fsync.
  5. **1~4 중 어떤 단계에서든 실패하면 반드시 temp 파일을 unlink**한다(기존 `_atomic_json_write`는
     "secret 없는 문서"라는 전제로 실패한 temp를 보존하는데, secret writer는 이 정책을 쓰지 않는다).

### 2.3 Secure file read boundary (TOCTOU 제거 — rev2 핵심 수정)

기존 `os.path.islink()` 사전검사는 검사 시점과 실제 오픈 시점 사이에 파일이 교체될 수 있는
TOCTOU 취약점이다. rev2는 `scripts/_lms_platform.py`의 `open_nofollow`+`os.fstat(fd)` 패턴을
공용 모듈(`src/uls/config/_secure_file.py` 신설 후보)로 추출해 아래 **단일 순서**로 고정한다.

1. **신뢰 디렉터리 먼저 검증**: secrets 디렉터리 자체를 no-follow로 열고, 그 디렉터리의
   owner/권한(POSIX `0700`, Windows canonical DACL)을 그 오픈된 디스크립터 기준으로 검증한다.
   (부모 디렉터리를 마지막에 검사하던 rev1의 순서를 뒤집는다 — 디렉터리가 이미 변조 가능하면
   파일 검사 자체가 무의미하다.)
2. 검증된 디렉터리 handle을 기준으로 target 파일을 **동일하게 no-follow**로 연다
   (POSIX: `os.open(name, O_RDONLY|O_NOFOLLOW|O_CLOEXEC, dir_fd=dir_fd)`; Windows:
   `CreateFileW(FILE_FLAG_OPEN_REPARSE_POINT)`로 reparse point면 즉시 거부).
3. **그 열린 fd/handle 하나에 대해** `os.fstat(fd)`로 다음을 모두 검증한다: regular file인지
   (`stat.S_ISREG`), 소유자 일치(`st_uid == os.getuid()` 또는 Windows 소유자 SID 일치), 권한 비트가
   `0o600`을 초과하지 않는지(POSIX) / DACL이 허용 trustee 목록 밖의 principal에 접근을 주지
   않는지(Windows, 아래), 파일 크기가 4096바이트를 넘지 않는지.
4. 검증을 통과한 **그 동일 fd**에서만 내용을 읽는다. 경로 문자열로 다시 열지 않는다 — 검증한
   객체와 읽는 객체가 항상 같은 객체임을 fd/handle 결속으로 보장한다.

오류 코드: `secret_file_missing`, `secret_dir_missing`, `secret_file_is_symlink`,
`secret_file_not_regular`, `secret_file_too_large`, `secret_file_permissions_too_open`,
`secret_file_owner_mismatch`, `secret_dir_permissions_too_open`, `secret_dir_owner_mismatch`.
값은 어떤 오류에도 노출하지 않는다.

**Windows canonical DACL (rev2 신규 명시):** 허용 trustee는 정확히 **현재 사용자 SID**,
`NT AUTHORITY\SYSTEM`(`S-1-5-18`), `BUILTIN\Administrators`(`S-1-5-32-544`) 세 가지만이다. 이
집합 밖의 principal에 대한 ACE가 하나라도 있으면 거부한다. `icacls` 실행 후 exit code만 믿지
않고, 실제 security descriptor를 read-back해 위 집합과 정확히 일치하는지 검증한다
(`scripts/_lms_platform.py`의 기존 Windows ACL 코드 재사용/확장).

### 2.4 `GOOGLE_*_CREDENTIALS_FILE` — secret이 아닌 경로로 재분류, 공유 validator로 통합

이 두 값은 credential 저장소(keyring/file) 대상에서 완전히 제외한다. **경로 자체는 secret이
아니기 때문**이다. 대신:
- `source: environment`는 그대로 유지하되, `CredentialResolver`가 값을 얻은 **직후**(diagnose 내부,
  doctor 전용이 아님) §2.3과 동일한 secure-file validator를 그 경로에 적용한다. 통과하지 못하면
  `"error"`(예: `credential_file_permissions_too_open`)로 diagnose 결과 자체가 바뀐다 — 즉
  `uls run`, `uls doctor`, MCP 모두 **동일한 fail-closed 판정**을 공유한다(rev1처럼 doctor에만
  별도 ad-hoc 검사를 붙이지 않는다).
- **[rev4 갱신 — launcher는 폐기됨, §2.5/§7.2 참고]** 이 경로 값 자체를 스케줄 실행에 공급하는
  문제(§1.2)는 launcher가 아니라 §8.3의 composition-root config snapshot handoff로 푼다.
  검증(diagnose)과 소비(provider 전달)가 어떻게 동일 fd/payload에 결속되는지는 §8.2를 따른다 —
  이 값을 "path 문자열 그대로 provider에 전달"하지 않는다.

### 2.5 [rev3에서 폐기됨 — §7.2 참고]

이 절이 설계했던 launcher(`scripts/uls_launcher.py`)는 rev3에서 **전면 폐기**됐다. 이유와 대체
설계는 §7.2를 참고. macOS/Windows 스케줄러는 launcher 없이 `uls` 절대경로를 직접 호출한다.

### 2.6 대화형 등록 UX — `uls credential set <NAME>` (rev2 수정)

- 대상 `NAME`은 `ALLOWED_SOURCES`에 있는 이름만 허용.
- **rev2 변경: "이미 config.yaml에 선언된 source에 대해서만 `set` 허용."** 암묵적 `--source`
  플래그로 이번 쓰기만 다른 저장소로 바꾸는 기능은 제거한다(설정 파일과 실제 저장 위치가
  어긋나는 "닭-달걀" 문제의 원인이었다). `config.yaml`에 아직 `credentials.<NAME>.source`가
  없거나 `environment`로 선언되어 있으면:
  - 그 이름이 `file`/`keyring`을 지원하는 목록(`ALLOWED_SOURCES[NAME]`)에 있다면, 정확히 어떤
    줄을 `config.yaml`에 추가해야 `set`을 쓸 수 있는지 안내하고 종료(값을 요구하지 않음).
  - `environment`만 지원하는 이름(`REMOTE_MCP_EXPIRES_AT` 등)은 실행할 `export` 한 줄만 안내.
- source가 `file`/`keyring`으로 이미 선언된 경우의 흐름:
  1. **TTY 필수**: `sys.stdin.isatty()`가 거짓이면 즉시 `credential_tty_required`로 거부(파이프
     입력 비허용 — 값이 로그/transcript에 남을 위험 차단). `getpass.GetPassWarning`이 발생하면
     (no-echo 보장 불가 환경) 경고를 예외로 승격해 fail-closed.
  2. **이중 입력**: "값 입력:" / "다시 입력(확인):" 두 번 마스킹 입력을 받아 일치하지 않으면
     재시도(무한 루프 방지를 위해 최대 3회, 초과 시 `Aborted.`).
  3. **입력값 검증**: 제어문자(개행/NUL/tab 등) 포함 시 거부, 최대 4096바이트 초과 시 거부. 값
     자체를 임의로 strip/normalize하지 않는다.
  4. **덮어쓰기 확인**: 대상에 이미 값이 있으면 "덮어쓰시겠습니까? [y/N]"을 확인(비대화형 자동화용
     `--overwrite` 플래그로 생략 가능).
  5. `Ctrl+C`(`KeyboardInterrupt`)는 트레이스백 없이 `Aborted.` 출력, exit code 130.
  6. `file` source면 §2.2 원자적 쓰기, `keyring` source면 `_explicit_os_keyring()` 패턴 재사용.
  7. 쓰기 직후 §2.3/`_diagnose_one`과 동일한 fail-closed 재검증을 실행하고, **값은 노출하지 않되**
     다음 비밀 아닌 메타데이터를 출력한다(마스킹 미리보기는 §7.5에서 제거됨 — secret 유래 문자
     0개): 저장 방식(file/keyring), 저장 경로(file인 경우), 파일 권한 비트, 입력 길이(바이트),
     최종 상태(`ready`).
  8. `credential set`은 **정확히 하나의 NAME만** 원자적으로 교체한다. 여러 크리덴셜을 하나의
     트랜잭션으로 묶어 교체하는 기능은 이 설계의 범위 밖이며 필요해지면 별도 계획으로 다룬다.
- **값을 CLI 인자로 절대 받지 않는다**(`--value` 없음). 명시적 확인 플래그(`--overwrite`)만
  별도 인자로 허용.
- 실패 시 고정 오류 코드와 함께 **조치 안내 문구**를 페어링한다(예: `secret_dir_permissions_too_open`
  → `조치: chmod 700 "<경로>"`).
- `uls doctor`는 `file` source에 대해 `keyring`과 동일한 `ready|absent|error` 3분류를 노출하며,
  `GOOGLE_*_CREDENTIALS_FILE`도 §2.4의 통합 validator를 거친 동일 판정을 노출한다.

---

## 3. 비목표 / 후속 유지 항목 (rev1과 동일)
- `REMOTE_MCP_SECRET`의 OAuth/OIDC 전환.
- 실제 운영 환경 라이브 e2e 확인.
- 여러 크리덴셜의 묶음(cross-credential) rotation — §2.6에서 명시적으로 범위 밖으로 고정.
- 백업/복구 문서(`deployment/README.md` 등)에 protected secret file/keyring 항목이 **일반
  Syllva 백업에 포함되지 않으며 머신별로 별도 provisioning이 필요하다**는 점을 반영하는 작업은
  구현 단계에서 함께 처리한다(이 rev2에서 필요성만 기록).

---

## 4. 테스트 계획 (rev1 대비 추가)
- **read boundary**: 신뢰 디렉터리 검증 우선순위, 동일 fd/handle 기준 regular-file/owner/size 검증,
  심링크·reparse point·FIFO·과대 크기 파일 거부가 전부 값 노출 없이 `"error"`만 반환하는지.
- **secret writer**: 실패 시 temp 파일이 항상 unlink되는지(주입된 실패로 검증), 성공 경로에서
  temp가 생성 시점부터 `0600`인지(경합 없이 chmod 이전 구간이 없는지).
- **CredentialResolver(source=file) 단일 경계**: launcher/다른 코드 경로가 secret을 environment로
  옮기지 않는지에 대한 회귀 테스트(예: 특정 secret 파일이 있을 때 해당 이름이 `os.environ`에
  나타나지 않아야 한다는 assertion을 `uls run` 통합 테스트에 추가).
- **credential set CLI**: TTY 아님/GetPassWarning 시 fail-closed, 이중 입력 불일치 재시도, 덮어쓰기
  확인, `Ctrl+C` 처리, config에 source가 없을 때 값 요구 없이 안내만 하고 종료, 성공 시 출력에
  원본 값이 어떤 형태로도 포함되지 않는지(§7.5 이후 마스킹 미리보기 자체가 없으므로, 출력 전체에
  secret 파생 문자가 0개임을 정규식으로 검증), 빈 값(길이 0) 등록 시도가 쓰기 전에 거부되는지.
- **GOOGLE_*_CREDENTIALS_FILE 통합 validator**: `doctor`와 `resolve()` 둘 다 동일한 fail-closed
  판정을 내리는지(둘 사이의 결과 drift가 없는지) 회귀 테스트.
- `tests/contract/test_worker_cli.py`: 스케줄러 설정 템플릿이 launcher 없이 `uls` 절대경로
  실행파일을 직접 가리킨다는 계약 테스트 추가(§7.2, launcher 폐기 반영).
- **substitution-race 회귀 테스트(rev4 신규)**: `GOOGLE_*_CREDENTIALS_FILE`을 검증한 직후 그
  경로의 파일을 다른 내용으로 교체해도, provider가 실제로 사용하는 값은 검증 시점에 이미 메모리로
  읽어 들인 payload여서 교체된 내용이 반영되지 않는지 확인.

---

## 5. 리스크 및 리뷰
- **분류: risky** (AGENTS.md — 인증/자격증명 코드 변경).
- **재리뷰 필수**: rev2를 insane-review(가능하면 Pro, 현재는 매우 높음 fallback 유지 가능성 있음 —
  사용자 승인된 fallback, Pro라 칭하지 않음) + Gemini 3.8 Flash high 양쪽에 다시 보낸다. 이번 rev1
  리뷰 두 건이 지적한 core 아키텍처 결함(§2.1/§2.5의 read-boundary 중복)이 rev2에서 실제로
  해소됐는지를 최우선으로 확인해야 한다.
- 두 리뷰 모두 GO 이후에만 구현 착수.

---

## 6. 다음 단계
1. **[다음 실행]** 이 rev2를 insane-review + Gemini high에 재발송(같은 파일 세트 + rev2 diff 강조).
2. 재리뷰 REVISE면 남은 지적사항으로 rev3 작성 → 재리뷰 반복.
3. 둘 다 GO 이후 구현 착수(같은 worker가 설계~구현 이어서 진행).
4. 구현 후 root 검증(pytest, ruff, mypy, behavior projection lint) → 독립 최종 리뷰 → GO 시에만
   커밋(푸시는 사용자 명시 지시 시에만).


---

## 7. rev2 재리뷰 결과 및 rev3 변경 (2026-09-17)

**재리뷰 기록:**
- insane-review(`GPT-5.6 Sol (매우 높음)`, 사용자 승인 fallback, Pro 아님): **REVISE**.
  저장 `.insane-review/response_Syllva_20260917_105110_42425_25fbf4.md`.
- Gemini 3.8 Flash high(`agy --mode plan`, 읽기 전용): **REVISE**.

두 리뷰 모두 rev1→rev2에서 고친 핵심 아키텍처(launcher의 raw secret 주입 제거, TOCTOU-safe
read boundary, secret 전용 원자적 writer, Windows trustee 3종 지정, TTY/이중입력/덮어쓰기 확인)는
**정확히 해결됐다고 확인**했다. 다만 각각 새로운 P0/P1을 발견했다.

### 7.1 rev3 반영 대응표

| 우선순위 | rev2의 남은 문제 | rev3의 수정 | 지적한 리뷰 |
|---|---|---|---|
| P0 | `GOOGLE_*_CREDENTIALS_FILE`을 §2.4 validator가 **검증한 파일**과 provider가 실제로 **여는(reopen)** 파일이 같은 객체라는 보장이 없음(validate-then-reopen TOCTOU) | Google 자격증명 경로도 §2.1의 secure read boundary 안에서 **다루도록 재설계**(§7.2) — validator가 단순 boolean 통과가 아니라 실제 consumption과 결속되게 함 | insane-review |
| P0 | launcher가 `config.yaml`을 `uls` 프로세스와 **별도로** 다시 읽어, 그 사이 config가 바뀌면 launcher와 uls가 서로 다른 config snapshot을 쓰게 됨 | **launcher를 전면 폐기**(§7.2). scheduled 실행도 `uls run`을 절대경로로 직접 호출 — 별도 config reader 자체가 사라지므로 snapshot split 문제가 구조적으로 없어짐 | insane-review |
| P0 | Windows DACL이 trustee 이름만 정의하고 ACE rights/상속/protection/owner 의미가 없음. `scripts/_lms_platform.py`는 스스로 "DACL hardening 미구현"이라고 명시하는데 rev2는 이를 이미 있는 것처럼 인용함 | Windows DACL 계약을 구체 ACE 수준으로 명시(§7.3), `_lms_platform.py`에 DACL hardening이 아직 없다는 사실을 정정 반영 | insane-review |
| P1 | `docs/plans/credential-resolver.md`가 여전히 `file` source를 "deferred"로 명시, 코드도 `environment`/`keyring` 두 가지만 앎 | 구현 단계에서 `credential-resolver.md`를 공식 개정 대상으로 명시(§7.4) — 계획 문서 간 불일치를 rev3 시점에 인정하고 구현 PR에서 함께 고친다고 기록 | insane-review |
| P1 | 마스킹 미리보기(처음 4자+`****`+마지막 2자)가 짧은 secret에서 전체 노출 가능 | **마스킹 미리보기를 완전히 제거.** 성공 피드백은 secret 유래 문자를 0개 포함(§7.5) | insane-review, Gemini 공통 |
| P1 | 인코딩 계약 없음(str↔bytes round-trip 방식 미정의) | UTF-8 strict 인코딩/디코딩 고정, 인코딩 실패·크기 초과는 쓰기 전에 거부(§7.5) | insane-review |
| P1 | "값 있으면 덮어쓰기 확인"만 있고 기존 저장소가 손상/신뢰 불가(symlink, 잘못된 owner 등)인 경우가 "값 없음"과 구분 안 됨 | `absent`(비어있음) / `ready`(정상) / `untrusted`(존재하지만 검증 실패)를 구분해 `untrusted`면 확인 없이 덮어쓰되 이전 상태가 신뢰 불가였다는 경고를 출력(§7.5) | insane-review |
| P1 | Day-0 온보딩: `config.example.yaml`에 `credentials:` 섹션이 아예 없어 "이미 선언된 source에만 set 허용" 규칙이 첫 사용자를 막다른 골목에 몰아넣음 | `config.example.yaml`에 권장 `credentials:` 블록을 **주석으로** 포함(§7.6). CLI의 config-mutation 자동화는 도입하지 않음(그 자체가 새 mutation 공격면이라는 게 이전 라운드의 리뷰 기조와도 맞음) — 대신 정확한 스니펫을 그대로 복사-붙여넣기 가능하게 제공 | Gemini |
| P2 | Windows blind typing 안내 부족, 클립보드 개행 에러 힌트 부족 | 프롬프트 문구와 에러 메시지에 구체적 안내 추가(§7.6) | Gemini |
| P2 | launcher child의 스케줄러 timeout/stop 시 lifecycle 미정의 | launcher 폐기로 **해당 우려 자체가 소멸**(§7.2) | insane-review |

### 7.2 launcher 전면 폐기 — scheduled 실행은 `uls run` 직접 호출

rev2까지 남아 있던 launcher의 유일한 존재 이유는 "secret이 아닌 Google 경로를 스케줄 환경의
environment로 넣어주는 것"이었다. 두 리뷰 모두 이것이 **두 번째 config reader**를 만들어 새로운
snapshot-split 위험을 낸다고 지적했다. rev3는 이 경로 값 자체를 environment/launcher 없이
`uls`의 기존 config composition root가 **직접** 읽도록 바꿔 launcher를 완전히 없앤다.

- `config.yaml`에 non-secret 필드 `google_worker_credentials_path`/`google_mcp_credentials_path`를
  추가한다(선택적). `uls`의 기존 config loader(단일 config 읽기 지점)가 이 필드를 읽는다.
- `CredentialResolver`가 `GOOGLE_WORKER_CREDENTIALS_FILE`을 diagnose할 때: config에 위 필드가
  있으면 그 값을 우선 사용하고, 없으면 기존처럼 `environment`를 본다(둘 다 §7.3의 동일한
  secure-file validator를 통과해야 `ready`). **어느 경로로 얻었든 diagnose 호출 1회 안에서
  일어나므로 별도 프로세스/재읽기가 없다** — snapshot split이 구조적으로 불가능해진다.
- `deployment/macos/com.syllva.uls.plist`, `deployment/windows/uls-task.xml`은 **이미 현재처럼**
  `uls`(절대경로 실행파일)를 직접 실행한다. launcher 관련 항목(`scripts/uls_launcher.py`,
  `ProgramArguments`/`Command` 변경)은 rev2에서 되돌린다 — **작업이 필요 없어졌다.**
- 부작용: launcher child 프로세스의 스케줄러 timeout/stop lifecycle 문제(insane-review P1)도
  launcher 자체가 없으므로 자동으로 사라진다.

### 7.3 `GOOGLE_*_CREDENTIALS_FILE` — validate/consume 동일 객체 보장

§2.4의 "validator를 통과하면 path 문자열만 snapshot에 남긴다"는 계약은 검증한 파일과 provider가
나중에 다시 여는 파일이 다를 수 있는 TOCTOU를 남긴다. rev3는 이를 닫기 위해:

- `CredentialResolver.diagnose()`가 `GOOGLE_*_CREDENTIALS_FILE`을 `ready`로 판정할 때, §2.3과
  동일한 no-follow 방식으로 **연 fd/handle을 그 자리에서 바로 provider adapter에 넘기는 형태를
  구현 단계에서 우선 검토**한다(provider SDK가 file-like object/이미 읽은 JSON dict를 받을 수
  있는지 확인). 만약 provider SDK가 반드시 경로 문자열만 받는다면, 최소한 **provider 호출
  직전에** 동일한 no-follow open + fstat 재검증을 하고, 그 두 번째 open에서 얻은 fd로 직접
  읽어 provider에 전달한다(경로만 넘기고 provider가 내부적으로 다시 여는 방식은 금지).
- 두 접근 모두 "validator가 확인한 객체 ≠ 실제 사용되는 객체"라는 간극을 없앤다. 정확한 구현
  방식은 provider adapter(§2.7, 향후 실제 Google SDK 연동) 설계와 맞물리므로, 이 rev3는 **원칙
  (검증과 소비가 동일 fd/handle에 결속되어야 한다)**을 계획에 고정하고, 구체적 adapter 시그니처는
  구현 PR에서 확정한다.

### 7.4 `credential-resolver.md`와의 문서 정합성

`docs/plans/credential-resolver.md`(rev3, 기존 GO)는 `file` source를 "deferred"라고 명시하고
있고, 현재 `credentials.py` 코드도 `environment`/`keyring` 두 가지만 인지한다. 이 계획
(`credential-secret-file-launcher.md`)이 `file` source를 공식 도입하는 것이므로, **구현
PR에서 `credential-resolver.md`에 "file source는 이 계획에 의해 도입되었다"는 개정 각주를
추가**한다. 계획 단계에서 두 문서를 미리 병합하지는 않되(범위 밖 변경 회피), 구현 완료 후 문서
불일치가 남지 않도록 이 조건을 구현 완료 조건에 포함한다.

### 7.5 `credential set` 성공 피드백 — 마스킹 제거, 인코딩 계약, 3분류

- **마스킹 미리보기 전면 삭제.** §2.6 항목 7의 "처음 4자+`****`+마지막 2자" 표시를 제거한다.
  성공 시 출력은 저장 방식(file/keyring), 저장 경로(file인 경우), 파일 권한 비트, **길이만**
  (바이트 수), 최종 상태(`ready`) — secret에서 파생된 문자는 0개.
- **인코딩 계약**: 입력은 UTF-8 strict로 인코딩한다. 인코딩 실패(비-UTF-8 바이트가 필요한 입력)는
  쓰기 전에 거부(`credential_encoding_invalid`). 4096바이트 상한은 **인코딩된 바이트 길이**
  기준으로 측정한다.
- **3분류(absent/ready/untrusted)**: 기존 저장소 상태를 확인할 때 "완전히 없음"(`absent`)과
  "존재하지만 신뢰 불가"(`untrusted` — symlink, 잘못된 owner, 초과 권한 등 §2.3 검증 실패)를
  구분한다. **[rev4 정정 — §8.7 참고]** `untrusted`도 `ready`와 동일하게 반드시 확인
  프롬프트를 거친다(자동 덮어쓰기 금지). 값 자체는 어떤 경우에도 노출하지 않는다.

### 7.6 Day-0 온보딩 마찰 완화 (config-mutation 자동화 없이)

Gemini가 지적한 마찰(첫 사용자가 `config.yaml`을 손으로 먼저 편집해야 함)은 인정하되, CLI가
config를 자동으로 고쳐 쓰는 기능은 **도입하지 않는다** — 이는 그 자체로 새로운 mutation 계약과
공격면을 만들고, 이전 두 라운드의 리뷰가 반복적으로 지적한 "read boundary/config snapshot을
단순하게 유지하라"는 방향과 어긋난다. 대신:
- `config.example.yaml`에 `credentials:` 권장 블록을 **주석으로** 추가해 `uls init`
  직후에도 사용자가 무엇을 추가해야 하는지 바로 보이게 한다.
- `uls credential set NAME`이 선언 누락으로 거부할 때 출력하는 안내 문구(§2.6)는 그대로
  복사해 `config.yaml`에 붙여넣을 수 있는 정확한 YAML 스니펫이어야 한다(이미 rev2 설계 의도와
  같음 — rev3는 이 문구가 실제로 유효한 YAML 조각임을 테스트로 고정한다).
- Windows `getpass` 프롬프트 문구에 "화면에 표시되지 않습니다" 안내를 명시하고, 제어문자/개행
  거부 에러 메시지에 "클립보드 복사 시 개행이 포함되지 않았는지 확인하십시오" 힌트를 페어링한다.

### 7.7 Windows DACL — ACE 수준 명시 (§2.3 개정)

- 허용 trustee: 현재 사용자 SID, `SYSTEM`(`S-1-5-18`), `Administrators`(`S-1-5-32-544`) 정확히
  세 개. 이 외 principal에 대한 ACE는 **allow든 deny든** 하나라도 있으면 거부.
- 각 trustee의 허용 rights: 현재 사용자는 `FILE_GENERIC_READ | FILE_GENERIC_WRITE | DELETE`,
  `SYSTEM`/`Administrators`는 `FULL_CONTROL`(OS 복구/백업 도구 호환을 위한 최소 관례)까지만.
  이보다 넓은 권한(예: `GENERIC_ALL`을 현재 사용자에게)은 초과로 간주해 거부.
- 상속된(inherited) ACE는 허용하지 않는다 — DACL은 **protected**(상속 차단) 상태여야 한다.
- owner SID는 `scripts/_lms_platform.py`의 기존 `_windows_default_owner_sid()`가 실제로 쓰는
  `TokenOwner`(그룹 SID일 수 있음)와 동일한 정의를 그대로 재사용한다 — rev2처럼 "현재 사용자
  SID"를 `TokenUser`로 암묵 가정하지 않는다.
- **정정**: `scripts/_lms_platform.py`는 현재 DACL hardening을 구현하지 않은 상태다(권한
  검증은 POSIX 위주). rev3의 Windows DACL 코드는 **신규 구현**이며, 기존 코드에 이미 있는 것을
  재사용하는 것이 아니라 그 파일의 owner-SID 헬퍼 정의만 참고해 신규로 작성한다.

### 7.8 다음 단계 (갱신)
1. **[다음 실행]** 이 rev3(§7 전체)를 insane-review + Gemini high에 3차 재발송 — 이번에는 특히
   launcher 폐기가 실제로 두 P0(TOCTOU, snapshot split)를 해소했는지, 마스킹 제거/3분류/인코딩
   계약이 완결됐는지를 우선 확인 대상으로 지정한다.
2. 재리뷰 REVISE면 남은 지적사항으로 rev4 작성 → 재리뷰 반복.
3. 둘 다 GO 이후 구현 착수.
4. 구현 후 root 검증 → 독립 최종 리뷰 → GO 시에만 커밋(푸시는 사용자 명시 지시 시에만).


---

## 8. rev3 재리뷰 결과 및 rev4 수정 (2026-09-17) — 3차 라운드

**재리뷰 기록:**
- insane-review(`GPT-5.6 Sol (매우 높음)`, 사용자 승인 fallback, Pro 아님): **REVISE — 단, 범위
  좁은 REVISE**("아키텍처를 다시 뒤집는 REVISE가 아니라, 이미 선택한 아키텍처를 API/ownership
  계약으로 끝까지 연결하는 일"). 저장 `.insane-review/response_Syllva_20260917_111344_44088_b44cee.md`.
- Gemini 3.8 Flash high(`agy --mode plan`, 읽기 전용): **REVISE**("새 아키텍처가 필요한 대형
  반려가 아니라 5가지 조치 항목만 본문에 인라인 통합하면 되는 가벼운 패치").

두 리뷰 모두 rev2→rev3의 핵심 변경(launcher 폐기, TOCTOU-safe read boundary, secret 전용 writer,
Windows trustee 지정, 마스킹 제거)이 **방향상 옳다**고 확인했고, 아키텍처를 다시 설계할 필요는
없다고 명시했다. 남은 지적은 이미 정한 방향을 실행 가능한 API 계약으로 끝까지 못박는 것과, 문서
본문(§0~§6)과 개정절(§7)이 서로 모순되는 상태("Frankenstein 문서")를 정리하는 것이었다.

### 8.1 rev4 대응표

| 우선순위 | rev3의 남은 문제 | rev4의 수정 | 지적한 리뷰 |
|---|---|---|---|
| P0 | Google 자격증명 파일의 "검증과 소비가 동일 fd에 결속돼야 한다"는 **원칙**만 있고, 그 결과가 현재 `ResolvedCredentials`(`Mapping[str, str]`) 계약과 어떻게 연결되는지 미정 | §8.2: **검증된 fd에서 끝까지 읽은 payload(bytes/JSON, 문자열이 아님)를 provider adapter에 직접 전달**하는 계약으로 고정. path 재전달·재오픈 금지를 명시적 API 규칙으로 못박음 | insane-review, Gemini 공통 |
| P0 | launcher 제거로 snapshot split의 **원인**은 없앴지만, "config loader가 1회 로드한 값을 resolver에 어떻게 넘기는지" 인터페이스가 미정 → "구조적으로 불가능"이라 단언하기엔 이름 없음 | §8.3: composition root가 config.yaml을 1회 로드 → 그 객체에서 뽑은 Google 경로 값을 `CredentialResolver`에 명시적 파라미터(예: `path_overrides`)로 전달 → resolver/provider는 config 파일이나 loader에 접근할 수 없다는 규칙을 계약으로 고정 | insane-review |
| P0(→P1로 하향, 근거는 이번 회차) | `credential-resolver.md`(기존 GO 문서)가 `file` source와 Google 변경 모두 "범위 밖"이라고 명시하는데, 그 충돌을 구현 완료 후 각주로만 정리하는 건 부족 | §8.4: **rev4와 동시에** `credential-resolver.md`의 source matrix, Google secure payload 형태, composition table을 amend(문서 전면 재작성이 아니라 해당 절만) — 계획 단계에서 authoritative 문서 하나로 수렴 | insane-review |
| P1 | Windows canonical DACL이 "현재 사용자 SID" trustee와 owner 검증의 `TokenOwner`를 같은 개념처럼 써서, elevated 환경에서 `TokenOwner == Administrators`가 되면 trustee 3종 계약이 흔들림 | §8.5: **trustee(ACE 대상)는 항상 `TokenUser`**(실행 계정 본인), **owner 검증은 `TokenOwner`**로 명확히 분리 — 이렇게 하면 elevated 환경에서도 3-trustee 계약이 정확히 유지됨(둘이 우연히 같아져도 문제 없음, 다를 수 있다는 사실만 명시) | insane-review |
| P1 | 짧은/빈 secret 값이 쓰기 전에 거부되지 않음(`0 < len <= 4096` 미검증), read 쪽 UTF-8 strict decode 계약 없음 | §8.6: 쓰기 전 `0 < UTF-8 인코딩 바이트 길이 <= 4096` 강제. 읽기 시 동일 fd에서 얻은 bytes를 UTF-8 strict로 decode, 실패 시 `secret_encoding_invalid` | insane-review |
| P1 | `credential set`의 내부 3분류(absent/ready/untrusted)와 resolver의 공개 3분류(ready/absent/error)가 어떻게 매핑되는지 불명확 | §8.6: `credential set` preflight의 `untrusted`는 resolver 관점에서는 `error`와 동일 범주(둘 다 "선언된 source가 신뢰 가능한 값을 못 준다")로 명시 | insane-review |
| P0(신규 버그, Gemini 발견) | rev3 §7.5가 "`untrusted`면 **확인 없이** 덮어쓴다"고 적어, 오타로 실행한 `credential set`이 사용자 동의 없이 기존(비정상) secret 파일을 조용히 파괴할 수 있음 | §8.7: **정정** — `untrusted`에서도 반드시 확인 프롬프트를 요구하고(오히려 더 강한 경고), 자동 덮어쓰기는 하지 않음 | Gemini |
| P1 | `credential set` 거부 메시지에 대상 config 파일의 절대경로가 없고, `credentials:` 섹션이 이미 있는 경우/없는 경우의 스니펫이 구분되지 않으며, `GOOGLE_*_CREDENTIALS_FILE`에 대한 전용 안내(경로 필드 vs export)가 없음 | §8.8: 에러 메시지 템플릿을 구체화(대상 config 절대경로 표시, Case A/B 스니펫 분리, Google 전용 안내) | Gemini |
| P1 | 문서 본문(§2.5 launcher 설계, §2.6 항목7/§4의 마스킹 언급)이 §7의 폐기/제거 결정과 정면 모순 | **이번 패치로 즉시 정리**(§2.5는 폐기 안내로 교체, §2.6/§4의 마스킹 문구 갱신 — 위에서 이미 적용) | Gemini |

### 8.2 Google 자격증명 — payload 전달 계약 (P0 확정)

TOCTOU 원칙("검증과 소비가 동일 fd에 결속")을 실행 가능한 API 계약으로 고정한다:

- secure-file read boundary(§2.3, provider JSON에도 동일 적용)는 검증을 통과한 **그 fd에서 EOF까지
  전부 읽어** 불변 payload(원시 bytes 또는 이미 파싱된 JSON dict — provider adapter가 필요로
  하는 형태)를 반환한다. 이 함수가 반환하는 것은 **경로 문자열이 아니다.**
- `CredentialResolver`/`ResolvedCredentials`가 `Mapping[str, str]`이라는 기존 타입 계약과
  Google payload(문자열이 아닐 수 있는 JSON dict)를 같은 구조에 억지로 넣지 않는다. Google
  자격증명은 **별도의 좁은 반환 값**(예: `GoogleCredentialPayload` 같은 immutable 컨테이너, 정확한
  타입은 구현 PR에서 확정)으로 다루고, 기존 문자열 전용 credential 파이프라인과 분리한다.
- provider adapter(Google SDK 연동)는 **경로를 받아 스스로 다시 여는 API를 절대 쓰지 않는다.**
  이미 읽은 dict/bytes를 받는 형태(예: `google.oauth2.service_account.Credentials.from_service_account_info(info_dict, ...)`
  류)만 허용한다 — 정확한 SDK 호출 시그니처는 실제 provider adapter 구현 PR에서 확정하되, "path
  reopen 금지"라는 계약 자체는 이 계획에서 고정한다.
- 회귀 테스트(§4에 이미 추가): 검증 직후 파일 내용을 바꿔도 provider가 새 내용을 쓰지 않는지
  확인 — payload가 이미 메모리에 있으므로 이 테스트는 설계상 항상 통과해야 한다(통과하지 않으면
  어딘가에서 경로를 재오픈하고 있다는 뜻).
- **[rev5 추가 — insane-review rev4 재리뷰 요구] payload의 소유/전달 경계(ownership/handoff
  topology)를 다음처럼 고정한다:** Google payload는 그 path 선택과 secure read를 수행한
  **동일한 diagnosis/composition operation의 결과물**로 귀속된다(예: 같은 `diagnose()` 호출
  범위 안에서 획득 — 별도의 두 번째 resolver 호출이나 별도의 secure-file 재호출로 획득하지
  않는다). composition root는 그 결과에서 이미 읽힌 immutable payload를 **읽기 전용 접근자로
  꺼내어** downstream/provider adapter에 **필수 인자**로 전달한다. downstream은 resolver를
  다시 호출하거나, secure-file reader를 다시 호출하거나, path를 다시 여는 어떤 방법으로도 값을
  재획득할 수 없다 — 정확한 컨테이너 타입/함수 이름은 구현 PR에서 정해도 되지만, 이 소유권/단일
  전달 경로 자체는 계획 단계에서 고정한다.

### 8.3 config snapshot handoff — composition root 계약 (P0 확정)

- `uls`의 기존 composition root(예: `cli/main.py`의 명령 진입점)가 config.yaml을 **정확히 1회**
  로드한다(기존과 동일 — 새 reader 아님).
- 그 이미 로드된 config 객체에서 `google_worker_credentials_path`/`google_mcp_credentials_path`
  (있으면)를 추출해, `CredentialResolver` 생성 시 **명시적 값 파라미터**로 전달한다(예:
  `CredentialResolver(declared_sources=..., path_overrides={"GOOGLE_WORKER_CREDENTIALS_FILE": path, ...})`
  — 정확한 파라미터 이름/형태는 구현 PR에서 확정).
- `CredentialResolver`, provider adapter, downstream 소비자 **어느 쪽도 config 파일 경로나
  loader 함수 자체에 접근할 수 없다** — 오직 composition root가 넘겨준 이미 추출된 값만 본다.
  이렇게 하면 "config를 두 번 읽어 서로 다른 스냅샷이 생기는" 경로가 타입 수준에서 봉쇄된다.

### 8.4 `credential-resolver.md` 동시 개정 (P0→P1, 지금 처리)

구현 완료 후 각주만 추가하는 방식은 폐기한다. **rev4와 동시에** `docs/plans/credential-resolver.md`
에서 다음 절을 amend해야 한다(전면 재작성 아님, 해당 부분만):
- source matrix에 `file`을 정식 추가(현재 "deferred" 표기 제거).
- Google credential 관련 절에 "이 계획(`credential-secret-file-launcher.md`)이 Google path를
  secure payload 계약(§8.2)으로 확장한다"는 상호 참조 추가.
- composition table의 `google_service(snapshot['GOOGLE_MCP_CREDENTIALS_FILE'], ...)` 같은 예시를
  §8.2/§8.3의 새 계약(payload 전달, path_overrides)에 맞게 갱신.
(이 개정 자체는 이 rev4 재발송과 함께 별도 diff로 준비하고, 재리뷰 대상에 포함시킨다.)

### 8.5 Windows trustee vs owner 개념 분리 (P1 확정)

§2.3/§7.7의 "허용 trustee: 현재 사용자 SID, SYSTEM, Administrators"에서 **"현재 사용자 SID"는
`TokenUser`를 가리킨다**(실행 중인 프로세스의 실제 사용자 계정 SID). 파일/디렉터리의 **owner
필드 검증은 별도로 `TokenOwner`**를 쓴다(`scripts/_lms_platform.py`의 기존
`_windows_default_owner_sid()`와 동일 정의 재사용 — elevated 환경에서 `Administrators` 그룹
SID가 될 수 있음을 그대로 인정). 즉:
- ACE trustee 집합 = `{TokenUser, SYSTEM(S-1-5-18), Administrators(S-1-5-32-544)}` 정확히 3개,
  각각 정확히 하나의 explicit `ACCESS_ALLOWED` ACE, 상속 플래그 없음, 중복/DENY ACE 있으면 거부.
- owner 필드 자체는 `TokenOwner`와 일치해야 한다(둘이 다른 개념이므로 섞어 검증하지 않는다).
- DACL은 `protected`(상속 차단) 상태여야 하며 NULL/empty DACL은 거부.
- secrets 디렉터리와 개별 secret 파일에 동일한 trustee/rights 모델을 적용한다(mask 값 자체는
  구현 PR에서 Windows API 상수로 확정).

### 8.6 secret 값 계약 마무리 (P1 확정)

- 쓰기 전 검증: `0 < len(value.encode("utf-8", errors="strict")) <= 4096`. 빈 값(길이 0)은
  `secret_value_empty`로 거부 — 기존 정상 값을 실수로 빈 값으로 덮어쓰는 사고를 막는다.
- 읽기 시 검증: §2.3에서 확보한 fd로부터 얻은 bytes를 `UTF-8 strict`로 decode. 실패 시
  `secret_encoding_invalid`(값은 노출하지 않음).
- `credential set`의 내부 preflight 3분류(`absent`/`ready`/`untrusted`)는 **CLI 전용
  개념**이며, `CredentialResolver`의 공개 3분류(`ready`/`absent`/`error`)와는 다음처럼
  대응한다: CLI의 `untrusted`(기존 저장소가 있지만 §2.3 검증 실패)는 resolver 관점에서 `error`와
  같은 범주다 — 즉 "선언된 source가 신뢰 가능한 값을 내주지 못한다"는 동일한 사실을 CLI는
  사용자 경험을 위해 `untrusted`로, resolver는 진단 목적으로 `error`로 부르는 것뿐이다.
  **[rev5 추가 — 매핑 전체 명시]** 나머지 두 상태도 명시한다: CLI `ready`(정상 기존 값) ↔
  resolver `ready`. CLI `absent`(저장소에 값이 전혀 없음, declared source가 `file`/`keyring`인
  경우)도 resolver 관점에서는 `error`다 — `file`/`keyring` source는 값이 없으면 `"absent"`가
  아니라 `"error"`로 취급한다는 §2.1/기존 keyring 계약(값이 없으면 `environment`처럼 `absent`로
  조용히 넘어가지 않는다)과 일치시키기 위함이다. 즉 CLI의 `absent`(등록 전 상태를 사용자에게
  설명하는 개념)와 `environment` source 자체가 미설정일 때의 resolver `"absent"`(선택적
  크리덴셜에서 정상적으로 허용되는 상태)는 **서로 다른 것**이다 — `environment`는 애초에
  이 3분류 대상이 아니며(§2.6에서 이미 별도로 안내 후 종료), `file`/`keyring`으로 선언된
  크리덴셜만 이 매핑의 대상이다.

### 8.7 [버그 정정] `untrusted`도 반드시 덮어쓰기 확인을 거친다

§7.5의 "`untrusted`면 확인 없이 덮어쓰되 경고만 출력한다"는 문장을 **철회**한다. 올바른 동작은:
- 기존 저장소가 `untrusted`(존재하지만 검증 실패)여도 **일반 덮어쓰기 확인과 동일하게
  `[y/N]` 확인을 요구**한다(단, 문구는 더 강한 경고로: "기존 저장소가 검증에 실패한 상태입니다
  (사유는 비밀 아닌 오류 코드만 표시). 그래도 덮어쓰시겠습니까?").
- `--overwrite` 플래그로 비대화형 생략은 `ready`/`untrusted` 양쪽에 동일하게 적용된다 —
  `untrusted`라고 자동으로 확인을 생략하지 않는다.
- 근거: 파일이 신뢰 불가 상태가 된 원인이 항상 공격은 아니다(umask 실수, 사용자의 수동 chmod
  등도 `untrusted`를 유발함). 확인 없는 자동 덮어쓰기는 데이터 손실 사고를 낼 수 있다.

### 8.8 CLI 안내 메시지 템플릿 구체화 (P1)

- `credential set`이 미선언 source로 거부할 때: 대상 config의 **절대경로**를 출력하고, 이미
  `credentials:` 섹션이 있는 경우(Case B, 하위 항목만 붙여넣기)와 없는 경우(Case A, 최상위
  블록 전체)를 구분한 스니펫을 각각 보여준다.
- `GOOGLE_WORKER_CREDENTIALS_FILE`/`GOOGLE_MCP_CREDENTIALS_FILE`에 대해 `credential set`을
  시도하면: "이 값은 secret이 아니라 서비스 계정 키 경로입니다. 무인 실행(launchd/Task
  Scheduler)에는 `config.yaml`의 `google_worker_credentials_path` 필드를, 대화형 1회성
  실행에는 `export GOOGLE_WORKER_CREDENTIALS_FILE=...`을 쓰십시오"처럼 이 값 전용 안내를
  출력한다(일반 `environment`-only 크리덴셜의 범용 export 안내와 분리).

### 8.9 다음 단계 (갱신)
1. **[다음 실행]** `credential-resolver.md` amend diff(§8.4)를 함께 준비한 뒤, rev4(§8 전체 +
   본문 정리)와 그 diff를 insane-review + Gemini high에 4차 재발송한다.
2. 두 리뷰 모두 GO(또는 "구현 중 확정 가능한 세부사항만 남았다"는 명시적 조건부 GO)면 구현 착수.
   REVISE가 다시 나오더라도 이번처럼 "새 아키텍처 blocker"인지 "세부 계약 정리"인지 리뷰 스스로
   구분해 판정하도록 계속 요청한다.
3. 구현 후 root 검증 → 독립 최종 리뷰 → GO 시에만 커밋(푸시는 사용자 명시 지시 시에만).
