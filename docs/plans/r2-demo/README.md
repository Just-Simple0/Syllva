# R2 시연 기록

R2 완료 기준(명세 §15 R2)의 "네이티브 확인 다이얼로그 수동 시연 기록"과 R2-0 스파이크 결과(r2-plan §7, 정오표 E29)를 남긴다.

## Linux (2026-11-03, 자동 시연)

- **환경**: Ubuntu 컨테이너, WebKitGTK 2.52, Xvfb 1280×900.
- **실행**: `shell/target/debug/syllva-shell`을 `SYLLVA_CORE_BIN=$(which syllva)`로 실행했다.
- **조작**: xdotool로 클릭했고, 사람은 조작하지 않았다.

| 단계 | 결과 | 화면 |
|---|---|---|
| 앱 시작 | Shell이 Core를 띄우고 `syllva://localhost/bootstrap#token`을 로드했다. 중계 인증과 Shell 보관소의 세션으로 "준비됨"이 표시됐다. | `linux/1-library.png` |
| 자료 열기 | 중계를 통해 근거 원문(locator별)이 표시됐다. | `linux/2-material.png` |
| "검증됨으로 표시" | 앱 origin에서 IPC `confirm_gate` 호출이 됐다. 창 label이 일치해 `gate.view`를 받았다. **네이티브 GTK 다이얼로그**가 떴다. | `linux/3-dialog.png` |
| "확인" | 셸 MAC으로 `gate.confirm`을 보냈고 `COMMITTED`를 받았다. DB 결과: `material_usage.verified = 1`, `human_gate_log` 1행(`VERIFY_MATERIAL`, `NET-M01`). | `linux/4-after.png` |

다이얼로그 내용(§6.3(3), §6.5):
- Core가 만든 요약
- 대상 `NET-M01`
- 원본 `SRC-…`과 sha256 앞 12자
- 남은 시간
- 확인/취소 버튼

R2-0 실측(Linux): 웹뷰는 `syllva://` 요청에 Origin/Sec-Fetch/Cookie를 보내지 않는다. 그래서 중계 인증을 쓴다(E29).

## macOS / Windows — 사람 시연 필요

같은 단계를 각 OS에서 실행하고 화면과 결과를 이 표에 추가한다. 확인할 항목은 다음과 같다.

1. 앱 origin(`syllva://localhost`, Windows `http://syllva.localhost`)에서 IPC가 동작하고, 다른 origin에서는 동작하지 않는다.
2. 창 label이 일치한다(확인 다이얼로그가 뜬다).
3. 네이티브 다이얼로그의 내용과 확인/취소 결과.
4. 앱 밖 링크로 이동할 수 없다.
5. WKWebView/WebView2가 `Origin`을 보내는지와 그 값(`null`이면 E29 재검토), Tauri IPC가 CSP 때문에 postMessage로 대체되는지(콘솔 경고).

### Linux 시연 직후 DB 조회 (sqlite3)

```
material_usage: [(1, 1)]                       # (verified, verified_gate_id)
gate log: [('VERIFY_MATERIAL', 'NET-M01', 64, '0.1.0')]   # (action, target_id, length(request_hash), shell_version)
```

### Core 감시 확인 (Linux, PR #52 재검토 후)

- 두 번째 실행(같은 `SYLLVA_DATA_DIR`): 앱이 종료되지 않고 "the Core refused to start: DB_LOCKED" 다이얼로그를 띄운다.
- 실행 중 Core를 강제 종료(`kill -9`): 셸이 `syllva: the Core stopped (exited)`를 남기고 2초 안에 끝난다.
