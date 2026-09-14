# Syllva 사용자 가이드

[English](README.md)

이 가이드는 **Syllva로 실제 공부하는 사람**을 위한 문서입니다. provider ID, credential 설정, scheduler 구성, 내부 상태 머신의 세부사항은 가능한 한 제외했습니다. 그런 내용은 [운영자 가이드](../operator-guide/README.ko.md)에 있습니다.

## 여기서 시작하세요

1. [처음 시작하기](getting-started.ko.md) — 워크스페이스를 이해하고 첫 학습 루프를 완료합니다.
2. [매일 사용하기](daily-use.ko.md) — 대시보드, 과목, 수업, 자료, To DO, 캘린더.
3. [AI와 공부하기](studying-with-ai.ko.md) — 설명, 복습, 시험 준비, 출처 확인 요청 방법.
4. [MCP와 클라이언트](mcp-and-clients.ko.md) — 지원 AI 클라이언트가 할 수 있는 것과 없는 것.
5. [문제 해결](troubleshooting.ko.md) — 사용자가 자주 만나는 문제.

## 기본 동선

일반적인 탐색 구조는 다음과 같습니다.

```text
학기 대시보드
  ├─ 내 과목
  │    └─ 과목
  │         └─ 수업 목록
  │              └─ 수업 / 자료 / 필기
  ├─ 이어서 공부
  ├─ To DO
  ├─ 캘린더
  └─ 파일 확인
```

Syllva는 수업마다 전역 메뉴를 늘리는 방식이 아닙니다. 수업은 과목 아래에 두고, 대시보드는 자주 쓰는 진입점만 작게 유지합니다.

## 시작 전 준비

사용 가능한 워크스페이스는 운영자가 미리 구성하고 검증해야 합니다. 저장소를 직접 clone했지만 Drive/Notion/provider 연결을 아직 설정하지 않았다면 먼저 [운영자 가이드](../operator-guide/README.ko.md)를 따라가세요.

Syllva 0.1.3은 베타입니다. 문서에서 어떤 흐름을 지원한다고 설명하더라도, 사용자의 실제 환경에서 외부 provider나 최종 AI client 연결까지 이미 검증되었다는 뜻은 아닙니다.
