# 문제 해결

## 기본 상태

사용자: `uls doctor`, `uls status`, `uls jobs`를 순서대로 실행한다. `doctor`가
credential separation 또는 provider ID를 지적하면 값을 문서에 붙여 넣지 말고
운영자가 private config와 provider 권한을 확인한다. `uls behavior lint` 실패는
client projection을 배포하기 전에 해결한다.

## LMS 상태

`partial`은 pagination, resource shape, bounded timeout 또는 하나의 과목 수집이
끝나지 않았다는 뜻이다. `failed`는 identity/config/route 검증 실패다.
`needs_verification`은 API course code 또는 registry binding이 확인되지 않은
상태다. 이 상태를 title, 파일명, module 개수로 보완하지 않는다. 학술 과목 중
하나라도 complete가 아니면 semester apply-ready가 아니다. 비교과 후보는 별도
관찰 결과로 남고 학술 분모에는 넣지 않는다.

Drive에 파일을 넣었다고 자동 intake가 시작되거나 Notion page가 생성된다고 가정하지
않는다. 현재 실행 가능한 경로는 명시적 transcript registration과 bounded 실행 경로다.
PDF/OCR/PPT/STT, upload watching, automatic material/session creation은 기본 사용자
흐름에 포함되지 않는다.

MCP 연결이 안 되면 local server 실행 경로, client가 지원하는 transport, 계정별
remote 연결 가능 여부를 운영자와 확인한다. native Notion readback에서 source
binding, parent/privacy, source hash, 또는 USER edit conflict가 보이면 connector
apply를 중단하고 fixed error code와 sanitized receipt만 전달한다.
