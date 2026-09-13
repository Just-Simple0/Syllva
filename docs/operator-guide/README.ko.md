# Syllva 운영자 가이드

[English](README.md)

이 가이드는 Syllva를 설치하고, 외부 provider를 설정하고, worker를 실행하고, MCP client를 등록하고, 선택적 자동화를 언제 활성화할지 판단하는 사람을 위한 문서입니다.

Syllva 0.1.3은 베타입니다. 일부 설정만 된 시스템을 준비 완료처럼 보이게 만드는 것보다 명시적 검증과 fail-closed 동작을 우선합니다.

## 권장 순서

1. [설치](installation.ko.md)
2. [설정](configuration.ko.md)
3. [학기 Intake와 Notion](intake-and-notion.ko.md)
4. [MCP 클라이언트](mcp-clients.ko.md)
5. [LMS 동기화](lms-sync.ko.md) — 선택 기능
6. [운영과 복구](operations.ko.md)

## 운영자 책임

운영자는 다음을 책임집니다.

- secret을 저장소, prompt, client instruction에 넣지 않기;
- write/retrieval 활성화 전 정확한 Drive/Notion/GitHub identity 검증;
- 필요한 경우 worker와 MCP credential 분리;
- 활성화 전 `uls doctor`와 필요한 read probe 실행;
- LMS credential/connection gate 통과 전 scheduler paused 유지;
- 불확실한 외부 write 결과를 자동 성공이 아니라 reconcile 대상으로 처리;
- 사람이 소유하는 verification/scope 결정을 보존.

## 버전 모델

- **패키지:** 0.1.3 beta, 미출시.
- **코어 동작 프로토콜:** 1.2, frozen.

프로토콜 번호만 보고 패키지 성숙도를 판단하지 않습니다.
