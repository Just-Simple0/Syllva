# Syllva 인계 — Local Settings GUI-2/GUI-3

## 사용자 결정: Windows 완전 지원 보완 보류·PR 머지 — 2026-10-04

- 사용자 결정: “후속 수정은 나중에. 일단 handoff.md 최신화하고 PR 두 개다 머지해. 윈도우즈는 일단 호환 정도로 하고 완벽 지원은 개발 이후에 제대로 해보자.” Windows 완전 지원을 위한 admission/journal/config/CLI(B2) 후속 수정은 개발 이후 과제로 보류한다. 현재는 검증된 호환 범위로 진행하며 완전 지원이나 전체 CI 통과를 선언하지 않는다.
- 실제 Windows3.11·3.14의 secure-file 집중 검사는 각각 `12 passed, 6 skipped`로 검증됐지만 전체 검사에는 각각 195개 실패가 남아 있다. 사용자가 이 알려진 제한을 수용하고 기존 두 PR의 머지를 지시했다. 검사 실패를 숨기거나 CI checks를 성공으로 변경하지 않는다. 이 결정은 향후 release gate·비밀 값 접근·고정 checker source pin 확대·전역 설정 변경의 승인이 아니다.
- 현재 기준 브랜치에 따라 [PR #14](https://github.com/Just-Simple0/Syllva/pull/14) → `codex/gui23-settings`, 이후 [PR #13](https://github.com/Just-Simple0/Syllva/pull/13) → `codex/gui1-reviewed-base` 순서로 일반 merge를 진행한다. 실제 merged/head/base/SHA는 private CI progress의 `merge_followup`에 기록한다. 기존 GUI PR 지적은 해결 완료로 표시하지 않고 후속 기록에 남긴다. GUI4·5는 설계부터 대기를 유지한다.

## Windows 보안 파일 보완·실제 CI 판정 — 2026-10-04

- 승인한 프로젝트 문서 예외 `dac0e6f7f5b6c54ae0a7bc7b0ba5af1c4575c706d3cc9d79ba2c2c649c7b700d`와 기존 Python 9핀·문서 1핀을 유지한다. 사용자가 유지 결정을 재확인했으며 개인 전역 검사기·hooks/config는 이 작업에서 변경하지 않았다.
- 이전 실제 Windows 집중 검사의 유일한 실패는 missing-file 테스트의 준비 과정이었다. 같은 담당자 `gpt-6-luna/max`가 public writer로 synthetic sibling을 먼저 작성해 canonical Windows 디렉터리 DACL을 마련하고 missing target 부재를 확인하도록 한 테스트만 보완했다. 제품 guard·공유 helper·CI·checker는 동결했다. 로컬 targeted `1 passed`, owning Ruff/diff-check PASS다.
- 전체 9개 관련 소스의 Native Latest/Chat/Extra High 최대 `[0,3,3]` FINAL은 **GO / REQUIRED0**다. 원본 identity-bound 회수와 active project checker exit0을 확인했다(`final-missing-fixture-ready/task-record.json`). Pro effort 부재 fallback은 `pro_option_unavailable`; 새 UI/flow 변경이 없어 Gemini N/A다. 같은 exact source hash에 실제 CI 증거를 추가해 의견을 재사용했다.
- 한 테스트 수정 커밋 `be6f1b0cb887c448e972ff0956828424d05e84d8`을 [PR #13](https://github.com/Just-Simple0/Syllva/pull/13)에 push했고, 일반 merge `2b90815d280b2db096093a3983d85577fc0ba792`로 [PR #14](https://github.com/Just-Simple0/Syllva/pull/14)에 반영했다. 두 remote SHA·비보호 ref preflight·한 파일 변경 범위·기존 dirty 보존을 확인했다.
- [실제 CI run 37168540862](https://github.com/Just-Simple0/Syllva/actions/runs/37168540862)의 Windows Python **3.11·3.14 집중 검사는 각각 `12 passed, 6 skipped`**다. skip 6개는 POSIX mode/symlink/FIFO 전용이고 Windows raw directory/file ACL·missing/extra mask 거부·partial SID cleanup·TokenOwner 검사는 실제 실행됐다. 두 버전의 전체 suite는 각각 **`195 failed, 2139 passed, 23 skipped, 1 warning`**이며 macOS 두 버전은 통과했다. 3.11 이전 실패 목록과 비교하면 missing-file 한 건만 해결됐고 새 실패는 없다.
- 현재 `_secure_file.py` SHA `325e7a970e02210d12c04e1c87edf77ac7244b71b9b3ea916729303648735559`, test SHA `e3c91864fe116567922740d7cceff859af3a48a57cadeb7b6ceccd930d772e37`, CI SHA `0a21b598577d8bf72328cb562f57295e97c4cdb675e46e9fe5f4d3462d455a8f`를 검토·게시·CI snapshot과 대조했다. 원문 로그/SHA·최종 처분은 `.insane-review/gui23-20261002/ci-corrections-20261004/corrected-runtime-final-disposition.json` 및 같은 `progress.json`에 보존한다.
- 이 보안 파일 보완은 수락했지만 **전체 Windows CI·프로젝트는 완료가 아니다**. Windows `os.getuid()` 등 admission/journal/config/CLI(B2) 범위와 이전 GUI PR 지적은 별도 미결이다. B2 구현이나 고정 source pin 확대를 이번 수락으로 승인하지 않는다. GUI4·5는 설계부터 대기를 유지한다. 모델 호출 ledger는 hash 보충 참조를 기록했으나 자동 lifecycle 관측 0·hook trust/완전성 미확인으로 기록 공백을 남겼다. 실제 선택은 runtime/Native 원본 근거이며 시간·비용을 추정하지 않는다.

아래 절은 각 게시 시점의 역사 기록이며 현재 판정은 위 절과 최신 private progress를 따른다.

## Windows DACL 수정·프로젝트 문서 예외 적용 완료 / CI 게시 준비 — 2026-10-04

- 사용자 “세가지 모두 진행”에 따라 같은 담당자 `gpt-6-luna/max`가 Windows specific mask, directory/file 양쪽의 독립 raw ACL 검증, missing/extra 권한 거부 회귀를 구현했다. Native에서 추가로 발견한 부분 SID 변환 실패의 메모리 해제 누락도 같은 담당자가 보완했다. 현재 source SHA는 `_secure_file.py` `325e7a970e02210d12c04e1c87edf77ac7244b71b9b3ea916729303648735559`, test SHA는 `31b3461ad2a4ebd330beb3c97cd0863732a197fbb6dee32569eb2bfcf2cc666f`다. 부모 확인 `15 passed, 3 Windows-only skipped`, owning Ruff와 diff-check PASS다.
- 현재 CI는 기존 OS/Python 4개 matrix와 전체 pytest를 유지하면서 full-suite 성공·실패 뒤 Windows secure-file 집중 검사를 실행한다. 전체 22개 관련 소스의 Native Latest/Chat/Extra High `[0,3,3]` 최대 FINAL에서 **DACL·CI SOURCE GO / REQUIRED0**, 별도 **문서 pin 후보 GO / REQUIRED0**를 받았다. Pro effort 부재 근거와 원본 결속 회수는 `.insane-review/gui23-20261002/ci-corrections-20261004/final-corrected-scopes/`에 보존한다. Gemini는 UI/flow 변경 없음으로 N/A다.
- 기존 오탐 보완은 정확한 Python 9개 source pin에 한정됐다. 이번 정상 설계 문서 `docs/plans/credential-secret-file-launcher.md`는 기존 예외 밖이었다. 사용자 “예외 추가하고 실제 3.11, 3.14는 CI 통해서 할 거지?” 결정 후, 한 문서의 exact path/SHA/48,413 bytes만 독립 immutable map으로 추가한 검토본 `dac0e6f7f5b6c54ae0a7bc7b0ba5af1c4575c706d3cc9d79ba2c2c649c7b700d`를 프로젝트 내부에 적용하고 AGENTS·채택 문서를 정합화했다. 적용 위치에서 `36 passed, 113 subtests passed`, 신규 test Ruff PASS, 실제 active checker의 `applied-current-task-record.json` exit0을 확인했다. 기존 9핀·reader·artifact/review gates 및 전역 설정은 불변이다. 적용 전 원본 기록/정책은 보존하고 전체 22개 패키지 source와 적용된 정확 bytes의 별도 mapping을 감사했다.
- 제품/CI 정확한 3파일은 커밋 `6b4b28cdd182add99a685c5b7a1e2ece4d6656cb`로 기존 PR #13에 push했고 원격 head를 확인했다. 실제 GitHub Actions `37166278082`(pull_request)와 `37166275847`(push)가 새 소스로 진행 중이다. Windows3.11/3.14 집중 검사의 실제 결과는 아직 없다. PR #14에는 프로젝트 검사기 예외·문서 커밋과 이 제품 커밋의 일반 merge를 반영한다. 이전 PR #14 head `2386b44b25f7296a3e7947ef6d3d4892e6c1517c`의 Windows 실패는 새 소스의 결과가 아니다. 실제 runner 판정은 같은 CI progress에 이어 기록한다. Windows admission/journal/config(B2)와 기존 GUI PR 지적은 이 DACL 소스 GO로 완료되지 않는다. GUI4·5는 설계부터 대기를 유지한다.

## CI 보완 A+B1 게시·실제 재검사 — 2026-10-04

- 사용자 “CI 보완 해서 재테스트 해보자”에 따라 동일 Luna/max 담당자가 7개 파일을 보완했다. CI `web` extra·Windows 한정 `tzdata`·fixture/Node UTF-8·정확한 POSIX GUI lifecycle 10개 Windows skip·portable 인증/nonce 및 거부 우선순위 검사를 유지했다. Windows owner-SID helper의 ctypes 서명 6개와 native direct 회귀 1개만 추가했다. 전체 OS/Python 4개 조합과 전체 pytest는 유지한다.
- Native Latest/Chat/Extra High `[0,3,3]` 최대 검토: A PLAN REVISE3의 지정 수정 반영, 별도 B1 PLAN GO, 21개 전체 관련 소스의 FINAL GO/REQUIRED0 및 원본 identity-bound 회수. 변경하지 않은 프로젝트 checker에서 현재 FINAL record exit0을 확인했다. UI 동작 변경 없는 이 범위의 새 Gemini는 N/A다.
- 로컬 A 전체 pytest는 B1 전 `2345 passed, 9 skipped, 2 warnings`; loopback 권한을 갖춘 launcher `16 passed`로 해당 6개 환경 skip을 별도 확인했다. B1 후 secure-file `14 passed, 2 skipped`이며 두 native Windows 검사는 이 macOS에서 실행되지 않았다. 변경 Python Ruff, compile, projection lint, TOML parse PASS. 이를 실제 Windows 성공으로 확대하지 않는다.
- 보완 커밋 `950931d3d86686e5e5d42500839bf22d67713d9b`를 기존 [PR #13](https://github.com/Just-Simple0/Syllva/pull/13)에 push하고 네 조합 Actions를 재실행했다. 같은 소스를 후속 [PR #14](https://github.com/Just-Simple0/Syllva/pull/14)에 일반 merge로 반영한다. 현재 실제 CI 판정·run/job/head 및 남은 결함은 `.insane-review/gui23-20261002/ci-corrections-20261004/progress.json`에 이어 기록한다. 이 문서 커밋 시점에는 CI 전체 통과·전체 PR 수락을 선언하지 않는다.
- 첫 실제 재검사에서 Windows 두 Python 조합은 `232 failed, 2100 passed, 23 skipped`로 같은 결과였다(이전 `267 failed, 2070 passed, 13 skipped, 4 errors`). macOS는 `2351 passed, 4 skipped`다. `-rs`의 실패 요약 누락은 `-ra`로 보완해 별도 Native FINAL GO/원본 gate PASS를 확인하고 실패 이름·원인을 포함한 재검사를 이어간다. 변경 없는 20개 소스 의견과 현재 workflow 의견을 결합해 21개 현재 source coverage를 확인했다. 전체 CI 통과는 아니다.
- Windows 공통 credential admission/CLI(B2)는 별도 계획 상태이며 아직 구현하지 않았다. 디렉터리 durability·native 보안 동등성과 고정 3개 소스 pin의 새 인간 범위 결정이 남았다. checker 코드/컴파일된 9핀/전역 설정은 불변이다. GUI23 기존 UI 지적 3건과 GUI4·5 대기는 유지한다. 근거 원문·전체 해시·실제 검사·부모 처분은 같은 private CI 작업 기록에 보존했다. 아래 절은 이전 게시 snapshot이다.

## 게시 완료 및 추가 PR 리뷰·CI 보완 대기 — 2026-10-04

- 커밋·push 완료: GUI-2·3 `e464e814961963808dce28a48ab5306f7a57e20a`, 프로젝트 전용 검사기 `4bc17f44b0f9d05dcad1b3841a0d0a86a19ac1b3`. 원격 세 브랜치 SHA를 실제 확인했다. [PR #13](https://github.com/Just-Simple0/Syllva/pull/13)은 GUI-1 기준 `codex/gui1-reviewed-base`와 비교하고, [PR #14](https://github.com/Just-Simple0/Syllva/pull/14)는 GUI-2·3과 비교한다. 기존 `main` 대비 36개 누적 커밋은 이 두 PR의 변경 범위 밖이며, 기준 브랜치도 `main`에 병합되지 않았다.
- GitHub Codex의 추가 리뷰는 두 원본 커밋에서 완료됐다. #14는 주요 지적 없음. #13은 복구 버튼과 가능한 transaction branch의 불일치, 불완전 Canvas identity/registry의 Ready 표시, 최초 등록 실패에 기존 credential 보존 문구 표시의 세 지적이다. 총괄이 실제 소스와 대조했고, 동일 Luna/max 담당자에게 읽기 전용 진단·구체 수정안을 배정했다. 동일 Gemini ultra는 해당 GUI-2·3 UX 지적을 독립 판정한다. 이 추가 GitHub 리뷰의 실제 모델·강도는 노출되지 않아 기존 Native Latest/max·Gemini 정책 gate를 대체하지 않는다.
- CI는 문서 빌드와 다수 macOS 실행을 통과했지만, macOS/Python 3.11의 Google 동시 replacement 한 실행에서 양쪽 `OPERATION_IN_PROGRESS`가 관측됐다. Windows 실행도 인코딩·보안 파일 처리·POSIX 전용 GUI 테스트 등에서 실패했다. 같은 커밋의 다른 macOS 통과를 실패 무시 근거로 삼지 않는다. 아직 CI 전체 통과·새 지적 CLOSED·병합 가능 판정은 없다.
- 기존 GUI-2·3 범위 수락 증거는 보존하지만 **새 PR 지적·CI 실패에 대한 통합 수락은 대기**다. 제품 코드 추가 수정이나 checker의 고정 9핀 변경은 아직 하지 않았다. GUI-4·5 대기와 실제 secrets/provider 접근 금지를 유지한다. 게시·검사·리뷰 원본과 진단 범위는 `.insane-review/gui23-20261002/publication-20261004.json`, `publication-pr13-findings.md`, `publication-ci-failure-111254320118.txt`, `publication-windows-summary-*.txt`에 기록했다.
- 게시 결과를 이 인계와 작업 기록에 추가하는 문서 커밋은 검사기 PR에 포함한다. GUI-4 master/mock 수정, 신규 GUI-4 계획서와 `RESEARCH/`는 로컬에 보존한다. 임시 기준 게시 worktree는 recoverable archive로 정리했다. 자동 merge·기존 heartbeat 재활성화는 하지 않았다. 아래 게시 준비·수정 전 수락 문구는 해당 당시 범위다.

## GUI-2·3 커밋·PR 게시 준비 — 2026-10-04

- 사용자가 커밋·push·PR 생성 및 리뷰 진행을 지시했다. 누적 변경이 크다는 지적에 따라 GUI-1 커밋 `2f39f19`을 `codex/gui1-reviewed-base` 비교 기준으로 보존하고, 수락된 GUI-2·3은 `codex/gui23-settings`, 프로젝트 전용 증거 검사기는 별도 후속 브랜치/PR로 나눈다. 기존 작업을 `main`에 한 번에 합치거나 자동 merge하지 않는다.
- GUI-2·3 제품 소스는 기존 수락 snapshot과 동일하다. interaction mock은 검토된 전체 376줄/SHA `1b0d08dd877cc38bf2de07053a5fe203dce89bac0e23e58a5ef6c1c7fd72c845`를 커밋에 담고, 현재 GUI-4 문서 변경은 로컬에 보존한다. `RESEARCH/`와 GUI-4 신규 계획서는 게시 범위에서 제외한다.
- 이번 게시의 검사 근거는 기존 Native/Gemini 범위별 수락과 유효한 자체 검사다. CLI26/peer27 프로젝트 gate를 게시 전 재확인해 exit0을 관측했다. GitHub PR/CI 결과는 별도 확인하며 기존 리뷰를 새 실행으로 표시하지 않는다. GUI-4·5 대기는 유지한다.
- 이 절은 게시 준비 기록이다. 실제 커밋·원격 hash·PR URL·추가 리뷰 상태는 게시 완료 후 기록한다. 아래의 commit/push 제외 문구는 이전 지시의 역사 상태다.

## 사용자 지시로 GUI-4·GUI-5 대기 — 2026-10-04

- 최신 지시: **“권장 방식으로 진행해. 그리고 GUI4-5 단계는 설계도 시작하지말고 대기.”** R7 권장 A를 방향 결정으로 확정했다. 설정은 Configured로 저장하고, 실행 때 동일 자격증명 snapshot과 실제 소비할 매핑으로 목적별 읽기 전용 재검증을 수행하는 방식이다. 과거 검사만으로 현재 실행 가능 상태를 표시하지 않는다. 학사 설정과 검색 범위의 독립 선택도 유지한다.
- 이 결정은 구현 GO가 아니다. **GUI-4·GUI-5의 추가 설계·구현·검사·리뷰 제출은 모두 사용자 재개 지시까지 대기한다.** GUI-4는 이미 설계 초안이 있으므로 그대로 보존하고 추가 작업을 멈춘다. GUI-5는 시작하지 않는다. 같은 담당자와 독립 reviewer에게 대기 지시를 전달했다.
- 중단 전 담당자 제출 계획서 실제 SHA-256은 `ba39cae589635a5b50db5cbbc63e69e0c4c85ee635567fe7c17bb25b4df2cde3`이다. 제품 구현·전체 GUI-4 계획 수락·FINAL 수락은 없으며, 기존 독립 리뷰와 필수 미결 조건은 보존한다. GUI-2/GUI-3 승인 묶음의 수락은 유지한다. 아래 진행·답변 대기 문구는 이전 상태다.
- 총괄은 이 결정과 대기 상태만 기록한다. 자동 후속 `syllva`는 기존 PAUSED를 유지하며 전역 변경·실제 자격증명/provider 접근·commit/push는 없다.

## GUI-4 최신 상태 — 2026-10-04

- 인간 결정: **학사 설정과 검색 범위를 별도로 선택한다.** `academic.active_semester`는 Academic/worker용이며 `retrieval.notion_lane/semester`는 별도의 명시적 검색 선택이다. 기존 `legacy_global` 검색은 Academic 저장으로 바꾸지 않는다.
- GUI4는 **계획 보완·독립 검토 중이며 제품 구현 전**이다. 첫 Native authority PLAN은 Latest/Chat/Extra High `[0,3,3]`에서 REVISE9. 같은 Chandrasekhar `gpt-6-luna/max`가 plan/master/mock만 보완했다. 총괄이 원문을 읽고 R1–R9를 채택했으며, 원본 리뷰·해시·기록은 역사 snapshot으로 보존했다.
- R7에는 실제 남은 설계 문제가 있다. GUI 난수 `credential_revisions`가 CLI·직접 managed-slot 교체까지 추적하지 못하므로 durable current Verified의 근거가 될 수 없다. 과거 검사 메타데이터와 같은 credential snapshot의 실행 시점 읽기 전용 preflight를 분리하는 좁은 대안은 Native가 조건6을 전제로 적합 의견을 냈으며, 인간 선택과 전체 GUI4 계획 수락은 아직 없다. 같은 Gemini ultra는 제한 UX 자문으로 이를 추천했지만, 전체 PLAN GO·실행 증거·인간 결정은 아니다.
- Native R7 scoped architecture 원본 COMPLETE canonical 회수 및 현재 프로젝트 gate exit0을 확인했다. Latest/Chat/max3를 첨부 전후 확인했고 기술 의견은 **해당 아키텍처에만 GO**, 조건6 및 실제 소비 소스 closure가 남는다. 인간에게 Configured 저장+실행 재검증+증거 전 Overall 확인 필요 표시와 새 학기 기능 보류의 선택을 요청했으며 답변 대기다. 다른 source scopes의 새 PLAN 리뷰·Gemini material-flow 재리뷰·전체 source closure/gate·부모 구현 GO가 남았다. 제품 테스트·새 GUI4 화면·FINAL 리뷰는 아직 없다.
- 실제 기존77-source snapshot과 비교해 변경은 plan/master/mock 3문서이며 나머지74는 동일했다. 프로젝트 checker와 전역 원본 SHA는 불변이다. 큰 SQLite/Notion 스키마 및 관련 provider/fixture 소스도 손실 없이 분할 검토해야 한다. checker pin 갱신이나 필수 소스 누락으로 통과시키지 않는다.
- 현행 상세 상태: `.insane-review/gui4-20261003/progress.json`; 최초 Native 처분: `parent-plan-r1-disposition.md`; R7 소스 검토: `plan-source-revision/`. 전체 프로젝트 완료·GUI4 구현 완료·commit/push를 선언하지 않는다. 아래 GUI23 수락과 이전 HOLD/진행 문구는 해당 역사 범위다.

## GUI-2/3 수락 및 GUI-4 진행 — 2026-10-03

- **GUI-2/GUI-3 승인된 묶음 수락 완료.** 최신 CLI26·peer27 GO/REQUIRED0의 원본 기록·manifest·canonical 응답·패키지는 불변으로 보존하고, 정책 선호와 실제 실행을 구별한 파생 기록으로 현행 프로젝트 전용 checker exit0을 확인했다. 실제 Native는 Latest/Chat/Extra High 최대3이며 Pro 실행으로 바꾸지 않았다. R2/R3 FINAL 및 corrective PLAN도 프로젝트 명령 exit0.
- UI는 19개 변경 없는 소스의 Native/Gemini 의견만 재사용했고 역사22 패키지 전체 원본 SHA를 재구성했다. 바뀐 backend3은 최신 peer27 의견으로 대체한다. 동일 Gemini3.8Flash/ultra가 재사용 GO0 확인; 과장된 완벽/100% 및 실제 없는 CREDENTIAL_BUSY는 부모가 수락하지 않는다. 옛22 전체 current-record PASS나 새 화면·266 재실행을 주장하지 않는다. 상세 행렬·원문·선택·최종 처분: `.insane-review/gui23-20261002/reconciled-20261003/`.
- 사용자 **진행하자**에 따라 다음은 **GUI-4 Academic/Automation 구현·검사·독립 리뷰**다. 동일 Chandrasekhar `gpt-6-luna/max`가 현재 상세 설계를 작성하고, 총괄 `gpt-6.1-sol/high`가 기획·통합·판정을 맡는다. 새 Native Latest/max 및 Gemini ultra PLAN/FINAL, 실제 합성 화면/동작 근거를 갖춘 후 별도로 수락한다. 중단으로 worker도 interrupted/notLoaded였으며 같은 기존 채팅에 모델 override 없이 후속 메시지를 보내 연속 책임을 유지했다. close/resume·새 worker 없음.
- 승인된 checker 코드·고정9pin·root·전역 원본 불변. GUI4는 실제 서비스 startup의 적용 설정과 원하는 설정을 구별해야 한다. 실제 credentials/env/provider/Keychain, scheduler/collector 활성화, GUI5, 전역·다른OS 확장, commit/push는 제외. 전체 Syllva 프로젝트 완료가 아니며 HEAD `2f39f19193fe4abc01e61c8d3b2d9aabbd857f37` 유지. 아래 HOLD와 진행 문구는 당시 역사다.

## 현재 재개 상태 — 2026-10-03

- **Checker 로컬 구현·리뷰·Syllva 전용 사용 완료.** 사용자 `권장안에 맞춰서 진행하자. 구현 및 리뷰 진행해` 뒤 **“아니. 이 프로젝트 내에서만.”** 결정으로 전역 적용 제안을 거절했다. 같은 Luna/max 제출 19tests/113subtests PASS, candidate 새 Ruff 진단0/test진단0/compile PASS. 총괄이 SHA/함수 AST/pin/root/current CLI·peer source를 확인했다. Native PLAN과 FINAL 모두 Latest/max3 GO/REQUIRED0, COMPLETE 원본 canonical 회수와 무손실 source/body/package audit 및 **후보 자신의 리뷰 기록은 변경하지 않은 원본 checker exit0**이다. 검토된 코드 SHA `a1004ffae36d06378ffe44a9b242699bc2c7c2109272a0ef6f0abe5c5892b836` 그대로 `scripts/review_evidence_checker_candidate/state_check.py`를 프로젝트 AGENTS 전용 명령으로 지정했다. 현재 선택 `docs/plans/project-review-evidence-checker.md`, 실행 증거 `.insane-review/checker-path-20261003/project-activation.json`, 판정 `final-disposition.md`/단계 `progress.json`. 원본 전역 checker·ULS source·hook 미변경. 전역 적용 승인을 다시 요청하지 않는다.
- **현행 최종 처분: 필수 기술 지적 CLOSED, 기존 리뷰 기록 정합성으로 전체 수락 HOLD.** CLI full26와 GUI peer/service/admission full27의 최신 Native 재리뷰 모두 GO/REQUIRED0다. 마지막 동시 교체 loser config/journal exact preservation 검사 지적도 닫혔다. 실제 총괄 `gpt-6.1-sol/high`, 같은 담당자 `gpt-6-luna/max`를 유지했다. 현재 gate는 사용자가 선택한 Syllva 전용 검사기이며 전역 적용 승인은 더 이상 미결이 아니다.
- 제품 보완 당시 담당자의 동일8suite266PASS를 재사용하고, 이후 테스트 assertion만 보강해 focused3PASS/Ruff/compile을 확인했다. 현재 test SHA `1eeae0b30657492c8d67a829acf41f16398075f9527c891854b6af4dff4520e2`, scoped 제품·CLI 소스는 그대로다. 총괄은 실제 소스/해시/응답을 대조했으며 전체266 반복 실행이나 Native pytest 실행을 주장하지 않는다.
- 원본 COMPLETE canonical 응답: CLI `final-r1-cli-266/response_harvest_20261003_173431_50138_294934.md`, peer `final-r1-peer-preservation/response_harvest_20261003_180403_50953_5b44e0.md`. Latest/Chat/Pro effort 부재/max3를 전후 검증하고 손실 없는 전체 소스·identity/body/package SHA를 확인했다. 새로운 material UI 변경 없어 새 Gemini N/A, 과거 UI 의견은 제한된 역사 근거다.
- **현재 남은 것은 기존 CLI/peer 기록의 강도 메타데이터 정합성과 프로젝트 전용 gate 재확인이다.** 사용자 지시로 이 canonical Syllva workspace의 consistency command만 검토된 로컬 검사기를 사용하며 개인 전역 원본은 보존한다. 실제 프로젝트 명령에서 checker 자신의 PLAN/FINAL record exit0, old CLI/peer record는 source stage PASS이나 `requested_effort: extra_high`가 보존된 `requested_effort == pro` gate와 불일치해 full validate exit2다. 구형 기록·원문을 변경하지 않았으며 전체 GUI 수락은 HOLD다. 전역 적용은 거절됐으므로 미결 승인 항목이 아니다. Rename/source 누락·승인 생성 없음.
- 자동 heartbeat `syllva`는 허용된 후속을 모두 마치고 인간 결정만 남아 도구로 PAUSED 처리, 저장 상태/target 확인. 담당자 close/resume 및 모델 변경 없음. 전체 수락·새 commit/push 없음. HEAD `2f39f19193fe4abc01e61c8d3b2d9aabbd857f37`.
- 상세 최종 판정: `.insane-review/gui23-20261002/final-bounded-disposition.md`, 현재 단계/소유/미결: `progress.json`, 제출 snapshot: `owner-selfcheck-peer-preservation.md`/`owner-freeze-peer-preservation.json`. 아래 중간 제출/진행 문구는 역사이며 이 현행 처분을 우선한다.

## 이번 단계 진행 이력 — 2026-10-03 (역사)

- **peer266 최신 판정·검사만 보강 중:** 원본 COMPLETE canonical response_harvest_20261003_174349_50500_9d916f.md는 production REQUIRED0, direct acceptance-test REQUIRED1. GUI EACCES/invalidUTF8 보완과 이전나머지지적은 CLOSED; 동시 Notion/Google replace loser의 config exactbytes 및 journal filename→bytes 무변경 assertion만 실제누락이다. 같은담당자에 서비스test2함수만 보강/집중3case·Ruff·compile 배정, 제품/CLI/fixture/계획 동결. 유효266제품검사 재사용, 전체266반복 불필요. 수정본 current27 전체소스 Native재리뷰는 총괄이 진행. CLI GO0재사용, 원본checker exit2HOLD, 전체수락/commitpush 없음. 진행Chrome없음.
- **CLI266 Native GO·peer266 재리뷰 시작:** CLI full26 원본 COMPLETE canonical harvest response_harvest_20261003_173431_50138_294934.md 판정 REQUIRED0, 이전CLI2 CLOSED. 총괄 identity/body/source/package 감사PASS, originalcheckerexit2·credential_admission.py 파일명 오탐HOLD. GUI peer/service/admission full27,461396bytes/SHA49455ad4…eb4361 단일실행17016을 시작해 응답 대기. Native는 정적검토이며 테스트실행/전체수락 아님. CLI OPTIONAL state_key cause관찰은 공급된 peer전체소스에서 독립판정하며 가설만으로source확장하지않는다.
- **현재266 결합 제출 확인·Native 재리뷰 진행:** same Luna/max의 GUI stores.read genuine ENOENT 원인 확인과 service._separate Notion strictUTF8/원래bytes 비교 보완을 총괄이 실제 소스·3파일 SHA로 확인했다. Red3→green3 및 동일8suite266PASS/owning Ruff·mypy·compile은 담당자 실행 근거이며 총괄266 반복 실행은 없다. CLI263/admission/공유secure_file/UI/계획은 동결해 유지됐다. Immutable owner-selfcheck-266-gui-followup.md와 owner-freeze-266.json으로 결합 snapshot을 보존했다. 최신26파일 CLI Native를 final-r1-cli-266에서 단일 전송했으며 Latest/Chat/Pro effort 부재와 slider 최대[0,3,3]를 첨부 전후 확인했다. 원본 manifest_Syllva_20261003_172726_49927_c17501.json USER_BOUND; 실행65186 응답 대기. Peer27전체소스는 CLI 회수·판정 이후 순차 진행한다. 아래224/250/256/263단계는 역사이며 현재 GUI수정대기 문구를 현행상태로 해석하지 않는다. 원본 checker HOLD·전체수락/commit/push 없음.
- 승인 범위: R1 per-user Syllva managed shared slots + 현재 workspace effective external/env peer. 다른 workspace external/env index·GUI4/5·실제 키/provider/Keychain/`.env` 접근·수집/sync 활성화·다른 OS 저장소 확장·commit/push는 제외한다. 기존 dirty/untracked·`RESEARCH/`·`CLAUDE.md`를 보존한다.
- 역할: 총괄 실제 `gpt-6.1-sol/high`, 연속 담당 Chandrasekhar 실제 `gpt-6-luna/max`, 기존 독립 UI/error-flow Newton 실제 `google-antigravity/gemini-3.8-flash/ultra`. 총괄이 기획 겸임, 고정 배정 Jev N/A. 모델 변경·추가 verifier 없이 기존 담당 책임을 유지한다.
- **마지막 확인 제출·263PASS:** same Luna/max가 CLI 일반read-error와positiveENOENT를 구분하고 canonicalfile strictUTF8를검증했다. 집중 red4실패/2통과→green10통과, 동일8suite263PASS와 CLI owning검사는 담당자 실행근거다. 총괄 actualCLI SHA `cada62beb0c0faacf34ed64e44eebb1cf24b6d6574380d0291943d709bc09e09`/test `a36d47e6cb0e66dd341512cd0833416e56de85cab42900e439dcfa130b6a61d1`와 unchanged service6case SHA를 확인하고 immutable `owner-selfcheck-263-cli-gui-repro.md`로 보존했다. 이전250/256/224는 각 당시snapshot. 이후GUI필수수정 진행중이며263을미래finalsnapshot으로부르지않고총괄263반복실행주장없음.
- **Native admission 재리뷰 GO — 이전 REQUIRED1 CLOSED, 추가 REQUIRED0:** 최신28전체소스466867bytes; 요청된 Latest(`최신`)/linked slider 최대 `[0,3,3]`/`extra_high`, Chat 및 Pro effort 부재 확인. 원본 `manifest_Syllva_20261003_145129_43375_301426.json` COMPLETE identity-bound harvest `response_harvest_20261003_150153_43783_f708ed.md`. 전체 기록은 `.insane-review/gui23-20261002/final-r1-admission-rereview-full/task-record.json`. Static source/direct regression 검토이며 Native가 테스트를 실행한 것은 아니다. OPTIONAL 혼재 구버전 프로세스 rolling compatibility는 범위 밖으로 보류한다.
- **Native peer 검사 재리뷰 REVISE — 제품 REQUIRED0, 검사 REQUIRED3:** full27 whole-source447402bytes/112386tokens 원본 `manifest_Syllva_20261003_160725_46397_7e7f1c.json` COMPLETE 및 canonical `response_harvest_20261003_162511_47011_d8b523.md` identity/body/source/package SHA를 확인했다. 기존 Google enroll 동시성·abrupt exit·v2/legacy/exact identity 검사는 CLOSED. 규범의 교차 workspace 동시 replace, configured-unreadable peer의 save 차단, enrollment leave/abandon 뒤 opposite-purpose 재판정 직접 검사가 남아 같은 담당자의 service 테스트 한 파일에만 부족분을 배정했다. 제품/CLI/testCLI/fixture/계획은 동결한다. Native는 정적 검사이며250 실행이나 첨부물만으로 suite 재구성을 주장하지 않는다. 소유 코드의 새 필수 결함 재현 시 source 수정 전에 총괄에게 보고한다.
- **Service 필수검사3 보강 checkpoint 확인:** 같은 담당자가 동시 replace의 loser 기존값 보존, configured unsafe peer save의 무변경, enrollment abandon 후 opposite same-value 재등록을6directcase로 보강했다. 변경 service test SHA `001407cfe6e1d85fae93c55a5c13df08599bb116f6cb1a6263badddbf6423e0e`/동결 제품·fixture·plan을 총괄이 확인했고, focused6/service42/관련8suite256PASS 및 test Ruff·compile은 담당자 실행 근거다. Immutable256 보고서 보존. CLI 필수2 및 GUI 유사 경로 조사는 계속 진행 중이므로 이 checkpoint를 전체 최종 snapshot으로 부르지 않고, 안정된 결합 제출 후 필수 Native 재리뷰한다.
- **수정 CLI 재리뷰 REVISE REQUIRED2:** whole26source417173bytes 원본 `manifest_Syllva_20261003_162711_47163_664469.json` COMPLETE, canonical `response_harvest_20261003_163535_47841_c621eb.md` identity/body/package/source 감사 확인. Latest/Chat/Pro effort 부재/max3 전후 gate 확인. Empty canonical peer 수정은 CLOSED. 일반 POSIX OSError가 *_missing으로 매핑돼 peer 부재/target확인생략으로 오인하는 경로, canonical file invalidUTF8 통과가 남아 same-owner CLI-local 최소 수정과 재현검사에 배정했다. shared _secure_file 변경과 OPTIONAL확장은 하지 않는다. GUI CredentialStores/Notion peer에도 같은 경로가 있을 수 있다는 총괄 가설은 합성 재현 보고 전 확정하지 않으며 GUI제품은 동결한다. 해당2항목은263 제출로 최소 수정됐으나 Native 수정본 판정은 아직 남았다. GUI 의심은 아래의 실제 합성 재현으로 확정됐고 기존단계의가설문구를 현재미결상태로해석하지않는다.
- **GUI canonical peer 결함 합성 재현·최소 보완 배정:** owner의임시workspace/FakeKeyring/FakeProvider/environ{}에서 actualsecurefile os.open EACCES→stores.read None→opposite save targetwritten, canonical invalidUTF8→opposite save targetwritten을재현했다. 실제secret/provider 접근없는별도GUI결함이며CLI Nativefinding으로섞지않는다. 승인된같은bounded안전계약상필수로판정해 같은담당자에게 credential_stores.read positiveENOENT원인확인, credential_service._separate 모든presentNotionpeer strictUTF8검증/원래bytes비교, permanentdirectregression/필요stores검사를배정했다. 공유_secure_file/state_key/journal/admission/CLI/UI/plan은동결. 새실제결함은확장수정전총괄보고. source/check가안정된결합제출뒤CLI+peer를순차전체소스로재리뷰한다.
- **Gemini 리뷰 재사용 한계:** 이전 bounded UI/error-flow GO 및 당시217PASS는 과거snapshot이며 현재263전체실행/전체source리뷰가 아니다. 현행UI/static/factory/기존fixederror흐름의 제한된근거로만 유지한다. 이번 stores/service 보완은 기존positiveabsence·invalidpeerfailclosed 계약을 기존INVALID_CREDENTIAL로 복구하는 좁은backend수정이며 새materialUI/layout/copy/code 없음으로 새Gemini N/A. 과거service해시를 이후수정서비스의current검증으로주장하지않는다.
- **개인 원본 checker HOLD:** 새 admission GO 기록에서도 원본 state_check exit2, first filename rejection `src/uls/settings/credential_admission.py`. 정상 source path의 SECRET_PART 오탐 진단만 했으며 PASS·승인 증거가 아니다. 전역 수정/설치·rename 우회·source 누락·guard 면제 없음. 해시/metadata/0600 제안은 논의이며 적용 승인 없음. 공개 임시파일로만600의 같은UID 읽기 허용을 확인했다.
- **자동 후속 확인:** 이 채팅에 heartbeat `syllva` ACTIVE,10분 간격, 저장 target/status 확인. 완료 원문/의미 있는 새지적/인간 결정 필요만 중복 없이 알리고 동일 상태는 조용히 기다린다. 아직 peer/CLI 독립 작업이 남아 있어 자동 후속을 종료하지 않는다. 담당자 채팅 「R1 설계와 파일 범위 확인」은 사용자 요청 맥락에 따라 앱에서 열었다.
- **전체 수락·새commit·push 없음**, HEAD `2f39f19193fe4abc01e61c8d3b2d9aabbd857f37`. 다음은 같은담당자의확정GUIpeer 최소수정/red-green/currenthash/check제출 확인과결합snapshot 필수Native전체소스재리뷰다. 진행중Chrome없음. 원본checkerHOLD와별도인간결정은기술리뷰로대체하지않는다.
- 전체 단계/소유/의존성/검사/판정: `.insane-review/gui23-20261002/progress.json`; 분야 현재 sourceSHA/실행: `r1-bounded-worker-selfcheck.md` (같은 private evidence root). Pre-R1 metadata22소스 GO/114PASS 및 화면은 역사 근거이며 현재전체R1 snapshot이나 신규R1 화면으로 재사용하지 않는다.

## 이전 metadata 단계 기록 — 2026-10-02 (pre-R1)

- 요청 범위: `handoff.md` 확인 후 GUI-2/GUI-3 독립 리뷰와 필수 보완. GUI4/5, 실제 provider·사용자 credentials·Keychain, 수집/sync 활성화, Windows/Linux 저장소 확장, push는 범위 밖이다. 기존 dirty/untracked·`RESEARCH/`·`CLAUDE.md`와 이전 인계를 보존했다.
- 오케스트레이터 실제 `gpt-6.1-sol/high`; 연속 구현 작업자 Chandrasekhar `gpt-6-luna/max`; 독립 Newton `google-antigravity/gemini-3.8-flash/ultra` 실제 선택을 확인했다. source/store metadata·비밀 값 비노출 경계의 연속 보완에 같은 작업자를 유지했다. 기존 작업자 인스턴스 `not_found` 이후 같은 설치 profile/model을 복구한 것이며 identity 변경이 아니다. 고정 배정 Jev N/A.
- **최신 기술 리뷰: native 웹 ChatGPT Latest(`최신`)/`extra_high` GO, REQUIRED0; Gemini ultra GO.** Chat mode·selected radio 및 linked slider `[0,3,3]` 최대를 첨부 전후 검증했다. `pro_option_unavailable`은 Latest reasoning slider의 Pro effort 부재이며 다른 모델의 Pro 선택이나 quota 소진을 뜻하지 않는다. 22개 전체 최신 소스 audit, package 447240bytes/112158tokens/SHA `2f2ae7b44138970bdb545ba395e093eef5c9b4e5affbe0b7ee9eb3c872955ffc`, 원본 schema2 identity-bound manifest에 결속한 전체 응답 회수 완료.
- **UI와 metadata 필수 보완 동결:** 초기 UI1–6 및 focus/file 경합 보완, env/external 상태·부재 시 Provided-by 오표기·저장 위치 표시, Google config-relative/expanduser/canonical managed 분류, main/standalone 두 fake factory의 공유된 fixed-store 격리 모두 최신 native 리뷰에서 CLOSED. production raw path secure-read/nofollow 유지; fake 환경은 env/external 값을 읽지 않는다. 최신 Native OPTIONAL future smoke artifact 제안은 필수 조건이 아니므로 추가 작업하지 않았다.
- **현재 관련 계약 테스트 부모·작업자 114 PASS**, 알려진 Starlette deprecation warning1. owning Ruff/mypy/diff 및 양쪽 fresh-interpreter import-order factory smoke PASS. 현재 전체 test/lint/type clean을 주장하지 않는다. 과거 전체2135PASS/9skip 및 기존 mypy/loader 진단은 역사 기록이다.
- **최신 실제 UI 증거:** `.insane-review/gui23-20261002/final-ui-metadata-factories-evidence.json`, `screens/metadata-factories-current-cards.png`. 최신8소스 해시가 화면 수집 전후 일치한다. 상대 경로의 가짜 Google MCP/worker 관리 카드 표시와 clean-close를 관측했다. 실제 사용자 키·provider·Keychain은 사용하지 않았다. 화면은 카드 렌더링 근거이며 byte-read 격리는 backend tests 근거다. 중단된 oversize chooser는 NOT PASS; keyboard/races/21→20/POST ending은 harness 근거다. 임시 서버와 정상 테스트 탭은 종료했다.
- **R3 standalone Canvas network만 최종 수락:** exact list path/unique active·term·per_page50/bounded page, bad-next dispatch 차단. checks73+service66PASS, owning Ruff/mypyPASS, native bound FINAL GO 및 당시 original state_check PASS. UI 없음으로 Gemini N/A. `canvas_checks.py` SHA `1a8551b10ad2bd273d1889f2e13959ac7d6d973928b1919fc291e4e2e3a3c9d0` 동결. 전체 Canvas service/config/store/UI 승인 아님.
- **R2 adapter는 GO 의견·최종 수락 HOLD:** real HTTPResponse framing/rawcap, SDK 예외 비밀 값 비노출, 실패 last_check 보완 및 F1–F3 CLOSED. 당시 provider52+credentialService/HTTP21=73PASS, owning Ruff/mypyPASS. `provider_checks.py` SHA `6f001daeb292af029cf1c6732c08b7f189ceb0d9dcb8a69612d96ef90303466d` 동결.
- **원본 증거 검사기 HOLD:** 최신 기록에 original `state_check.py` 실행 exit2, `state_check: validation failed`. 변경하지 않은 원본 함수 진단은 `_verify_sources:175 → _safe_relative:43 → _require:31`, 첫 차단 소스 `src/uls/settings/credential_roles.py`의 SECRET_PART 파일명 오탐이다. 진단은 수락 증거가 아니다. 파일명 변경·소스 누락·guard 우회·global 수정/설치 없음. 정확한 소스 path+digest 대상 보완의 설계·독립 리뷰를 추가할지 인간 질문 미답이며 실제 적용 승인은 없다. 제안 `.insane-review/gui23-20261002/state-check-path-finding.md`.
- **R1 권장 범위 승인 — 2026-10-02:** 공유 managed store+현재 프로젝트 effective external/env peer만 대상으로 구현한다. 다른 프로젝트 external/env index 확장은 제외. 기존 native bounded PLAN은 전체11소스/패키지 hash 일치로 재사용하며 같은 Luna/max가 분야 설계·구현·자체 검사·리뷰 보완을 연속 담당한다. Gemini bounded admission/error-flow PLAN 확인 중. 자세한 결정은 `.insane-review/gui23-20261002/r1-scope-decision.md`. 실제 키 변경·재발급/사용 없음.
- **전체 최종 승인·새 commit·push 없음.** HEAD `2f39f19193fe4abc01e61c8d3b2d9aabbd857f37`. 다음 단계는 승인된 bounded R1 보완·필수 검사/리뷰와 개인 checker 접근 방식 선택이다. 기술 GO가 이 선택이나 original checker gate를 대체하지 않는다. 이미 유효한 검사·리뷰 증거는 재사용하고 새 필수 결함 없이 추가 구현/리뷰를 만들지 않는다.

## 리뷰·검사 증거

- 최신 FINAL 기록: `.insane-review/gui23-20261002/final-ui-metadata-factories/task-record.json`; original manifest `manifest_Syllva_20261002_143815_83502_37f7d8.json`; 전체 bound harvest `response_harvest_20261002_144727_83910_ee8f1a.md`. 두 reviewer 의견·실제 모델/effort·전체 소스/응답 hash·원본 checker 결과·HOLD를 기록했다.
- 최신 Gemini 전체 의견/선택 증거/부모 판정: `gemini-final-metadata-factories.md`, `gemini-final-metadata-factories-selection.json`, `gemini-final-metadata-factories-parent-disposition.md` (위 private evidence directory). 전체16 turn_context 실제 ultra 확인; 과장된 제품 전체 보장은 수락하지 않고 named source/tests/가짜 화면 근거로 한정했다.
- corrective PLAN `.insane-review/gui23-20261002/plan-r2/task-record.json`: 원본 bound full GO/native 당시 checkerPASS, Gemini ultraGO. R1 인간 범위나 전체 승인 대체 아님. 계획 `docs/plans/local-settings-gui-23-review-corrections.md`.
- R3 FINAL `.insane-review/gui23-20261002/final-r3/task-record.json`; R2 full GO `.insane-review/gui23-20261002/final-r2-rereview/task-record.json`. UI·metadata의 중간 REVISE/GO 원문은 각 private evidence directory에 보존했고 최신 CLOSED를 이전 의견으로 소급하지 않는다.
- 단계/소유/질문/검사/판정: `.insane-review/gui23-20261002/progress.json`.

## 이전 인계 — 2026-10-01 (역사)

갱신: 2026-10-01 (Asia/Seoul)
저장소: `/Users/admin/Project/Syllva`
브랜치: `codex/protected-secret-file-and-credential-set`

## 이번 채팅의 목표와 현재 상태

사용자가 **GUI-2/GUI-3 구현과 자체 검증까지만 완료하고, 독립 리뷰는 새 채팅에서 진행**하도록
지시했다. 최종 리뷰·출시 승인·커밋·push는 이번 채팅 범위가 아니다.

| 범위 | 현재 상태 |
|---|---|
| GUI-1: 로컬 설정 셸·세션·설정 CAS·복구 저널 | 과거 승인 및 로컬 커밋 `2f39f19` 완료, push 없음 |
| GUI-2: Notion/Drive 역할별 자격증명 | 구현·자체 검증 완료, 독립 리뷰 대기 |
| GUI-3: Canvas 연결·강좌 선택·접근 lease | 서비스·네트워크·화면 통합 및 자체 검증 완료, 독립 리뷰 대기 |
| GUI-2/3 최종 독립 리뷰 | 사용자가 새 채팅으로 연기, 아직 실행하지 않음 |
| GUI-2/3 최종 승인·커밋 | 대기 |

구현 완료는 이번 합의 범위의 코드와 자체 검증 완료를 뜻한다. 독립 리뷰·최종 승인 완료를 뜻하지 않는다.
GUI-2/3 변경은 아직 작업 디렉터리에 있으며 새 커밋이나 push는 없다.

## 구현 범위와 경계

GUI-2는 Notion MCP/worker 및 Google Drive MCP/worker의 네 역할에 대해 등록·교체·로컬 삭제,
외부 파일 사용 중단, 읽기 전용 연결 확인, 저널 기반 복구와 설정 generation 갱신을 제공한다.
브라우저는 역할만 선택하고 실제 Keychain 서비스·계정·파일 경로는 코드가 결정한다.
역할별 값의 분리, 비밀 없는 저널, 역할·물리 저장소 단위 동시 접근 제어와 설정 CAS를 유지한다.

GUI-3는 하나의 Canvas profile, `CANVAS_PAT`, 계정 확인, 토큰 교체·삭제, 학기·강좌 조회 및
사용자 선택 저장, 최대 30일 접근 lease 갱신을 제공한다. 모든 provider 요청은 읽기 전용이며,
DNS 응답에 비공인 주소가 섞이면 거부한다. 검증된 주소에 연결하면서 TLS 호스트 검증을 유지한다.
토큰 삭제는 provider 호출 없이 가능해야 한다. 토큰과 lease는 별개다.

- Windows는 사용자 결정으로 후순위이며 GUI 실행을 거부한다.
- macOS는 자격증명을 저장한다. Linux 자격증명 카드는 읽기 전용이며 보안 저장소 추가는 후순위다.
- Canvas 자료·파일·과제·성적 수집, Drive 업로드, intake 실행은 포함하지 않는다.
- Canvas sync 활성화는 `FEATURE_DEFERRED`; 비활성화는 토큰·lease를 삭제하지 않는다.
- GUI-4의 학사 매핑·재시작 fingerprint, GUI-5의 자동화·Remote MCP는 포함하지 않는다.
- 개발·자동 테스트는 가짜 Keychain, 임시 비밀 디렉터리, 가짜 provider transport만 사용한다.
  실제 Canvas/Notion/Drive 호출이나 사용자 자격증명으로 smoke test는 수행하지 않는다.

## 담당자와 파일 소유

오케스트레이터: `gpt-6.1-sol` high. 작업 기록·인계 문서·통합 증거를 담당한다.
실제 `uls setup` 서비스 연결 누락을 보완하기 위해 `composition.py`, `launcher.py`,
`test_settings_composition.py`도 직접 담당했다. 작업자의 fake-mode 실행 제한을 보존해 병합했다.

- Banach `01a0e755-d3a3-7820-9521-e41564aad48b`, `gpt-6-luna` max:
  GUI-2 전체, 공통 config/roles/stores/journal/CLI, GUI-2/GUI-3 HTTP·status·static UI 및 통합 테스트.
  기술 적합성: 자격증명 트랜잭션·CAS·장애 복구를 연속 담당한 기존 작업자.
- Sartre `01a0f4e1-7d5e-70f1-873d-1cf692cdc93b`, `gpt-6-luna` high:
  `canvas_checks.py`, `canvas_service.py` 및 두 파일의 계약 테스트.
  기술 적합성: DNS·HTTP 경계와 Canvas 서비스에 집중하는 기존 작업자.
- 공통 CredentialService가 작성된 뒤 Canvas 서비스 소유를 Sartre에게 추가했다.
  이유: 공통 파일과 독립된 서비스 구현을 병렬로 진행해 통합 대기 시간을 줄이기 위해서다.
- Gibbs `01a0f590-3f6e-79c0-94e2-ab15d11ff968` 리뷰 작업은 사용자 지시에 따라 종료했다.

## 설계·과거 리뷰 근거

현재 설계 문서:

- `docs/plans/local-settings-web-gui.md`
- `docs/plans/local-settings-web-gui-interaction-mock.md`
- `docs/plans/local-settings-gui-1-worker-plan.md`
- `docs/plans/local-settings-gui-2-worker-plan.md`
- `docs/plans/local-settings-gui-3-worker-plan.md`

GUI-1 과거 최종 GO:
`.insane-review/web_gui1_5c_rereview4_response_20260930.md` 및 Gemini 최종 재검토 기록.
GUI-2 계획은 web Pro r4 GO, GUI-3 계획은 web Pro r2 GO로 당시 오케스트레이터가 승인했다.

- `.insane-review/web_gui2_plan_rereview4_response_20261001.md`
- `.insane-review/web_gui3_plan_rereview2_response_20261001.md`
- `.insane-review/gemini_ultra_gui2_plan_20260930.md`
- `.insane-review/gemini_gui3_plan_rereview2_20261001.md`
- 상세 진행·결함 처리 기록: `docs/plans/local-settings-web-gui-task-record.md`

**과거 리뷰 provenance와 최신 정책을 구분할 것.** 과거 웹 리뷰는 Aside 수동 회수 경로이며,
최신 사용자 지침의 native/v2 identity-bound 회수라고 주장하지 않는다.
GUI-3 Gemini 리뷰는 ultra를 요청했지만 실제 runtime은 `gemini-3.8-flash-high` / high로 보고됐다.
이를 ultra 실행 증거로 취급하지 않는다. 새 채팅은 최신 정책에 필요한 plan/final 증거를 확인하고
부족한 gate를 보완한다. 이번 구현-only 요청은 리뷰 완료나 커밋 승인이 아니다.

## 자체 검증

최신 작업자 자체 검증과 오케스트레이터의 누락된 통합 검증 결과:

- `.venv/bin/python scripts/lint_behavior_projection.py`: 통과.
  canonical version=2,
  hash=`sha256:987d09ec152f91e368e070c5ccbe961602a18da8b6113afd968ac965406aae1a`.
- Canvas 계층 계약 테스트: 110개 통과(네트워크 44개, 서비스 66개), 담당 네 파일 ruff/mypy 통과.
- 실제 launcher 서비스 조립·fake-mode 격리 계약 테스트: 5개 통과;
  `composition.py`, `launcher.py`, `test_settings_composition.py` ruff 통과,
  앞의 두 Python 소스에 `mypy --follow-imports=silent` 통과.
- 전체 pytest: **2135 passed, 9 skipped**(부모의 persistent fake-store 교체 실패 회귀 포함).
  명령: `PYTHONPATH=. .venv/bin/python -m pytest -q`.
  deselect 없음. skip은 작업자 환경에서 loopback bind가 차단된 launcher 6개와 macOS에서
  실행할 수 없는 Windows DACL/reparse-point/SID 3개다. 기존 keyring/env 테스트는 포함해 통과했다.
- 누락된 launcher 검증을 부모 실행 환경에서 보완:
  `PYTHONPATH=. .venv/bin/python -m pytest -q tests/unit/test_settings_launcher.py` → **16 passed**,
  skip 없음. 작업자 검사에서 제외된 loopback 6개도 여기서 실행됐다.
- 변경 파일 ruff 통과. `loader.py`의 기존 `TRY004` 18건은 잔존하며 전체 lint 통과라고 주장하지 않는다.
- 전체 mypy 진단: 기존 138건 → 현재 135건, 새 진단 없음. 전체 타입 검사 통과 상태는 아니다.
  현재 `.venv/bin/mypy`, baseline은 `git archive HEAD src pyproject.toml`로 임시 루트에
  추출한 HEAD에 같은 venv의 `mypy --no-incremental`을 실행하고 줄 번호를 정규화해 비교했다.
  로그: `/tmp/syllva-gui23-mypy-final.txt`, `/tmp/syllva-gui23-mypy-head.txt`.
- `git diff --check`: 통과.
- 실제 `uls setup --no-browser` fake-mode 브라우저 검증:
  Canvas 연결 → 강의 검색 → 선택 미리보기 → 적용 → 접근 lease 갱신 → sync 비활성화 → 로컬 해제 확인.
  재시작한 최신 demo에서 잘못된 Notion 교체 뒤 Configured·Replace/Forget 유지,
  Canvas 해제 뒤 이전 강의 목록·선택·주소 초기화도 확인했다.
  실제 provider/Keychain 호출은 없었다. GUI-1 과거 화면은 `/tmp/syllva-gui1-shots-r2/`에 있으나
  GUI-2/3 화면 증거로 재사용하지 않는다.
- 과거 환경 의존 실패 기록:
  `tests/unit/test_credential_resolver.py::test_keyring_dependency_missing_is_configuration_error`.
  sandbox 밖 macOS에서는 실제 keyring backend가 설치되어 있고 이 테스트는 keyring 부재를 가정한다.
  GUI-1 때 1개 deselect했으나 이번 전체 검사는 이 항목도 포함했으며 실패하지 않았다.

변경 파일 lint 명령(둘 다 통과):

```bash
.venv/bin/ruff check src/uls/settings src/uls/config/mutation.py src/uls/config/credentials.py src/uls/config/_keyring_backend.py src/uls/config/_secure_file.py src/uls/config/schema.py src/uls/cli/credential_set.py tests/contract/test_settings_*.py tests/unit/test_credential_set_cli.py
.venv/bin/ruff check src/uls/config/loader.py --ignore TRY004,RUF100
```

loader의 기존 TRY004 18건은 검사 명령에서만 제외했다. 코드나 suppression을 추가하지 않았다.
RUF100 제외는 TRY004를 제외한 검사에서 기존 noqa가 unused로 판정되는 것을 피하기 위해서다.

가짜 화면을 다시 실행하려면 저장소 루트에서 `.venv/bin/python -m uls.settings.demo`를 사용한다.
매번 `/tmp/syllva-settings-demo-*` 아래 새 설정·workspace·가짜 저장소를 생성하고 bootstrap URL을 출력한다.
실제 비밀을 넣지 않는다. fake JSON은 검증용 값만 사용한다. `--no-browser` 경로이며,
fake-mode launcher는 임시 루트 밖의 config/workspace/runtime이나 자동 브라우저 실행을 거부한다.
이번 마지막 demo 루트는 `/tmp/syllva-settings-demo-8_p890kk`; 검증 서버와 임시 탭은 종료했다.
GUI-2/3 화면은 이 채팅의 CUA 상호작용으로 확인했으나 영구 스크린샷 파일은 저장하지 않았다.
새 UI 리뷰는 최신 소스로 다시 실행해 실제 화면·오류·복구·해제 흐름 증거를 수집한다.

## 핵심 구현 파일과 다음 리뷰의 확인점

- `credential_roles.py`, `credential_stores.py`, `credential_admission.py`, `credential_service.py`:
  코드가 정하는 역할·locator, staging/backup, 비밀 없는 상태 ID, 공통 GUI/CLI admission과 복구.
- `provider_checks.py`, `config/{credentials,loader,schema,mutation,_secure_file,_keyring_backend}.py`:
  provider 읽기 전용 확인과 고정 오류 코드, 역할 revision·Canvas 타입, reserved-slot 차단 및 저장 경계.
- `canvas_checks.py`, `canvas_service.py`: 목적지 검증, account/profile 일치, bounded 조회,
  `save_selection` 미리보기 후 `apply_selection(..., candidate_hash)`에서 재조회·CAS 적용.
- `app.py`, `config_service.py`, `journal.py`, `security.py`, `status.py`, `static/*`:
  HTTP 인증·body 한도, 복구 상태와 오류 흐름, write-only 입력 제거, Canvas 선택 상태 초기화.
- `composition.py`, `launcher.py`, `fake_mode.py`, `demo.py`: 실제 실행 경로의 서비스 조립과 격리된 재현.
- `tests/contract/test_settings_credential_*.py`, `test_settings_provider_checks.py`,
  `test_settings_canvas_*.py`, `test_settings_composition.py`, `test_settings_{config,http,journal,ui}.py`,
  `settings_ui_harness.cjs`, `tests/unit/test_credential_set_cli.py`: 변경에 대한 기능·장애·UI 계약 증거.

다음 리뷰에서 저장·삭제·중단 시점별 저널 복구, 공유 저장소의 GUI/CLI·workspace 간 충돌,
역할 분리, secret redaction, profile-bound 선택 CAS, DNS/TLS/timeout 경계와 Linux 읽기 전용 동작을 본다.
staging 쓰기 전에 중단된 경우에는 `MANUAL_REVIEW`로 안전 차단하는 경로가 있다.
자동 복구 가능한 단계와 수동 확인이 필요한 단계를 구분해 검토한다.

## 새 채팅에서 리뷰 시작하는 순서

1. 이 문서, 최신 AGENTS.md, 작업 기록, 실제 `git status` 및 구현 파일을 읽는다.
   코드 작성 완료와 자체 검증, 독립 리뷰 승인, 커밋 상태를 구분한다.
2. 현재 설치된 insane-review 스킬·스크립트를 찾아 읽고 **native Chrome/CDP 전용 격리 프로필**의
   설정을 따른다. 과거 Aside 경로를 기본 경로로 이어 쓰지 않는다.
3. 환경 확인은 설치 스크립트의 `--ensure-env`, 제출은 정상 workflow,
   회수는 원본 **v2 identity-bound manifest**를 인자로 `--harvest`한다.
   URL-only/legacy/manual 또는 `original_run_bound=false` 응답은 참고용이다.
4. 저장소 프로젝트 `Syllva · eeb93c01` 소속을 검증하고 새 독립 대화를 시작한다.
   과거 프로젝트 URL은
   `https://chatgpt.com/g/g-p-6a9fdbd2dc3081919990a6607f8fe7c4-syllva-eeb93c01/project`.
5. 관련 전체 소스·호출자·테스트·계획을 압축 없이 패킹한다. 실제 포함 파일, 현재 소스 일치,
   크기·SHA-256·비밀 검사·제외 범위를 감사한다. `.insane-review/gui2_final_scope_20261001.txt`와
   `gui2_final_review_prompt_20261001.txt`는 **작성 중 준비한 참고 초안**이며 최종 scope는 재선별한다.
6. 실제 모델·effort를 UI에서 검증한다. Pro 우선; Pro quota 소진일 때만 허용된 Very high fallback.
   Gemini는 `google-antigravity/gemini-3.8-flash` ultra 독립 context에서 실제 화면·흐름 증거로 검토한다.
   요청된 effort와 실제 실행을 구분한다.
7. GUI-2와 GUI-3의 저장·복구·오류/승인/비밀값 제거 흐름을 검토한다. 필수 결함은 묶어서 수정하고
   필요한 웹/Gemini 재검토 후 최종 승인한다. 그 전에는 커밋·push하지 않는다.

GUI-2/3 구현 리뷰를 전송한 대화나 회수할 미완료 final manifest는 현재 없다.
이전 계획/GUI-1 대화를 구현 final 리뷰로 오인하거나 재전송하지 않는다.

## 보존할 다른 작업과 제품 불변조건

- `RESEARCH/`, `CLAUDE.md`, 실제 비밀 파일과 사용자 설정은 건드리지 않는다.
- 보호 브랜치(main/master/release)에 push·merge 승인은 없다.
- MCP 검색 표면은 read-only, SOURCE/AI/USER 구분과 인간 소유 학사 승인 규칙을 유지한다.
- frozen design/spec가 프로젝트 구현의 권위다. `Partial`을 자동으로 `Ready`로 승격하지 않는다.
- KNU/LMS 후보는 별도 계획이고 이번 GUI에서 활성화·자동 마이그레이션하지 않는다:
  `docs/plans/knu-lms-api-semester.md`, `docs/plans/knu-lms-reservation-reconciliation.md`,
  `scripts/knu_lms_*` 및 기존 sidecar 자격증명·registry.
- 과거 기반: C5/C6 `a868039`, Remote MCP OAuth `216e996`, semester retrieval `2489fac`.
  `syllva.dev`/`mcp.syllva.dev` 경로 및 Claude/Codex `uls.ping`은 과거 확인됐으며,
  Antigravity/Gemini remote ping은 미검증·후순위다. 이번 채팅에서 live 상태를 재검증하지 않았다.
- 과거 학사 데이터가 없는 `SOURCE_UNAVAILABLE` 상황에 가짜 Session/Material을 만들어 대응하지 않는다.
