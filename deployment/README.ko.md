# Syllva 데스크톱 및 원격 운영

[English](README.md)

저장소의 profile은 macOS와 Windows에서 로컬 Syllva 운영을 지원합니다. Syllva 0.1.3은 베타이므로 저장소의 scheduler/profile 파일은 template 및 implementation artifact일 뿐, 사용자의 환경에 scheduler나 remote endpoint가 이미 설치되었다는 증거가 아닙니다.

## 로컬 설정

Python 3.11+에서 checkout의 패키지를 설치합니다.

```bash
pip install -e '.[dev,mcp,drive,notion,pdf]'
```

다음 실행:

```bash
uls --config /absolute/path/config.yaml init
uls --config /absolute/path/config.yaml doctor
uls behavior lint
```

`init`은 없는 local file만 만듭니다. 실제로 사용할 정확한 course, Notion, Drive, optional GitHub identity를 구성하세요. 상대 config path는 config file 기준으로 해석합니다.

canonical source body는 Drive 같은 source storage에 유지하고 SQLite는 orchestration 및 완료 processing provenance를 저장합니다. 새로운 durable source binding은 임의 URL/title이 아니라 검증된 workflow record에서 나와야 합니다.

## Credential

secret은 process environment 또는 private service wrapper로 제공합니다. runtime은 `.env`를 자동 로드하지 않습니다. secret file을 checkout 밖에 두고 scheduler definition, client instruction, prompt, log에 넣지 마세요.

| 프로세스 | 해당 provider path 활성화 시 필요한 환경 변수 |
| --- | --- |
| Worker | `GOOGLE_WORKER_CREDENTIALS_FILE`, `NOTION_WORKER_TOKEN` |
| Local/remote MCP | `GOOGLE_MCP_CREDENTIALS_FILE`, `NOTION_MCP_TOKEN`; 설정된 private repository 사용 시 `GITHUB_READ_TOKEN` |
| Remote development bearer | `REMOTE_MCP_SECRET`, `REMOTE_MCP_EXPIRES_AT` |

provider workflow가 다른 권한을 요구한다면 read-only MCP와 worker mutation에 별도 Notion/Drive credential을 사용하세요. credential filename만으로 scope가 증명되는 것은 아니므로 provider console의 실제 grant를 확인해야 합니다.

## Source registration

설정된 경우 `uls init`은 빈 explicit source-registry 경로를 만듭니다. metadata-only source registry는 source를 설정된 course/location과 연결하며 source 본문이나 credential을 붙여 넣는 곳이 아닙니다.

현재 beta에는 별도 semester-intake preview도 있습니다. [운영자: 학기 Intake와 Notion](../docs/operator-guide/intake-and-notion.ko.md)을 참고하세요. preview data-source identity와 legacy read-only retrieval registry를 섞거나 두 lane 사이의 자동 fallback을 가정하지 마세요.

## Worker 명령

```bash
uls --config /absolute/path/config.yaml sync --max-jobs 20
uls --config /absolute/path/config.yaml process --max-jobs 20
uls --config /absolute/path/config.yaml run --max-jobs 20
uls --config /absolute/path/config.yaml status
uls --config /absolute/path/config.yaml jobs --limit 20
uls --config /absolute/path/config.yaml retry JOB_ID
uls --config /absolute/path/config.yaml reprocess ENTITY_ID
```

- `sync`: work 발견/register/project.
- `process`: 이미 발견된 durable work 처리.
- `run`: 두 단계를 한 번 수행하고 종료.
- mutation command는 local single-active-worker lock 공유.
- 외부 write 결과가 불확실하면 blind replay 대신 inspection/readback 필요.

## 데스크톱 Scheduler

template:

- `macos/com.syllva.uls.plist`
- `windows/uls-task.xml`

절대 경로를 수정하고 scheduler 등록 전 `uls run`을 수동 검증하세요. scheduled process가 interactive shell environment를 상속하지 않을 수 있으므로 private OS/service mechanism을 통해 credential 접근을 명시적으로 구성해야 합니다.

macOS에서는 수정한 plist를 `plutil -lint`로 검증한 뒤 등록합니다. Windows에서는 import된 Task Scheduler 설정과 Last Run Result를 확인합니다. scheduler에는 business logic이 없으며 Syllva가 local process lock을 계속 강제합니다.

### Windows: 로그아웃 상태에서의 무인 실행

`windows/uls-task.xml`은 `LogonType=Password`를 사용합니다. 이는 해당 계정이 로그아웃 상태여도
실행되는 방식으로, 콘솔에 활성 로그온 세션이 있을 때만 도는 `InteractiveToken`과 다릅니다. 이를
위해서는 계정 비밀번호를 Task Scheduler에 등록해야 하며, 저장소에 포함된 XML 자체에는 비밀번호가
없고 있어서도 안 됩니다. 프로세스 생성 감사 로깅(Windows Event ID 4688 등)에 명령줄 인수가 평문으로
기록될 수 있으므로, 명령줄에 비밀번호를 직접 평문으로 넘기지 마세요. 등록 시점에 비밀번호를
안전하게 프롬프트로 입력하려면 `/rp *`를 사용하세요:

```powershell
schtasks /create /tn "ULS" /xml "windows\uls-task.xml" /ru "DOMAIN\ServiceUser" /rp * /f
```

Task Scheduler는 작업 자격증명을 디스크에 암호화된 LSA secret으로 저장하며 XML 파일에 저장하지
않습니다. 개인 로그인 계정보다는 이 용도의 전용 low-privilege 로컬 계정을 권장하며, 비밀번호
교체도 XML을 직접 수정하지 말고 동일한 안전한 대화형 프롬프트(또는 동등한 Task Scheduler UI /
PowerShell `Register-ScheduledTask` 자격증명 입력 흐름)로 수행하세요. 사용자가 로그인해 있을 때만
실행해도 되는 환경이라면 `InteractiveToken`을 유지하고 `UserId`/`Password` 필드를 제거한 뒤, 그
제약을 운영자에게 문서로 명시하세요.

선택적 LMS scheduling은 별도 credential/connection/application gate 통과 전 paused로 유지하세요. [운영자: LMS 동기화](../docs/operator-guide/lms-sync.ko.md)를 참고하세요.

## MCP profile

`uls mcp local`은 stdio로 제공되고 stdout을 MCP protocol message용으로 예약합니다. 설정된 server composition과 동일한 retrieval policy를 사용합니다.

[클라이언트 패키지](../clients/README.ko.md)와 [운영자: MCP 클라이언트](../docs/operator-guide/mcp-clients.ko.md)를 참고하세요.

development-only remote 운영은 [remote-mcp/README.ko.md](remote-mcp/README.ko.md)를 참고하세요. `uls mcp status`는 config/durable status를 보고하지만 remote bridge가 실제 reachable하다는 증거는 아닙니다. read-only live probe와 최종 client E2E는 별도 검증입니다.

## 백업과 복구

`config.yaml`, metadata-only source registration, SQLite를 안전한 SQLite backup mechanism으로 함께 백업합니다. active WAL write 중 main DB file만 복사한 것을 완전한 백업으로 가정하지 마세요.

credential은 OS secret storage에 별도 보관하며 Syllva backup archive에 포함하지 않습니다.

복원 순서:

1. scheduler와 MCP process 중지;
2. 현재 상태를 별도 recovery copy로 보존;
3. 선택한 config/state를 함께 복원;
4. `uls status`, `uls doctor` 실행;
5. bounded source/context smoke check 수행;
6. 자동화 재활성화 전 복원된 local provenance와 Drive/Notion을 reconcile.

local state 복원은 외부 provider나 human approval을 되돌리지 않습니다.

## 원격 가용성

remote retrieval은 설정된 machine, network route, credential, bridge가 모두 online일 때만 가능합니다. public sharing은 보안 우회책이 아닙니다. 이 deployment command를 실행했다는 이유만으로 사람 소유의 verification/scope 결정이 생성되지 않습니다.
