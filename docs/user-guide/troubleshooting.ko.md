# 문제 해결

[English](troubleshooting.md) · [사용자 가이드](README.ko.md)

## 과목/수업이 보이지 않아요

- 올바른 학기를 보고 있는지 확인합니다.
- 먼저 과목으로 들어가세요. 수업은 의도적으로 전역 메뉴에 두지 않습니다.
- 그래도 예상한 수업이 없다면 중복 기록을 만들기 전에 운영자에게 정확한 Course relation/binding을 확인해 달라고 요청하세요.

## 새 파일이 처리되지 않아요

Drive에 파일이 보인다는 사실만으로 intake 설정과 준비가 끝났다는 뜻은 아닙니다.

- **파일 확인**에서 intake/request 행이 생성되었는지 봅니다.
- 필요한 과목/수업/날짜/의도 필드를 채웠는지 확인합니다.
- 요청을 명시적으로 제출했고 취소하지 않았는지 확인합니다.
- **Needs Input**이면 빠진 정보를 보완합니다.
- **Reconcile Required** 또는 **Failed**이면 재시도 전 운영자가 worker/provider 증거를 확인해야 합니다.

## AI가 내 자료를 찾지 못해요

가능한 원인은 다음과 같습니다.

- client가 실제로 Syllva MCP에 연결되지 않음;
- 자료가 아직 Partial이거나 current source가 아님;
- 질문에 충분한 범위가 없음;
- source가 허용된 과목/수업/자료 범위 밖에 있음;
- 해당 client profile의 실제 환경 검증이 완료되지 않음.

이 문제를 해결하려고 private credential이나 전체 비공개 source 파일을 prompt에 붙여 넣지 마세요. 운영자에게 `uls doctor`, MCP 등록, source readiness를 확인해 달라고 요청하세요.

## AI가 컨텍스트가 모호하다고 해요

client가 과목/수업/자료 후보를 보여주면 올바른 후보를 선택합니다. 모호할 때 멈추는 것은 정상적인 fail-closed 동작이며, 첫 후보를 추측해 선택하는 것보다 안전합니다.

## To DO/캘린더가 비어 있어요

실제 등록된 항목이 없다면 빈 화면이 정상일 수 있습니다. 대시보드를 채우기 위해 가짜 과제나 시험 날짜를 만들지 않습니다.

## 운영자 도움이 필요해요

보이는 status/error code와 영향을 받은 과목/수업/request identity만 전달하세요. secret은 보내지 않습니다. 운영자 진단은 [운영자 가이드](../operator-guide/README.ko.md)의 `uls doctor`, `uls status`, `uls jobs`부터 시작합니다.
