# MCP와 AI 클라이언트

[English](mcp-and-clients.md) · [사용자 가이드](README.ko.md)

Syllva는 MCP를 통해 학업 검색 기능을 제공하므로 여러 AI 클라이언트가 동일한 서버 강제 컨텍스트 규칙을 사용할 수 있습니다. client instruction/skill은 행동에 영향을 주지만, 실제 데이터 접근 범위·freshness·provenance는 Retrieval Engine이 책임집니다.

## 현재 베타 지원 상태

| Client/profile | 사용자 관점 상태 |
| --- | --- |
| Claude local MCP | Experimental; local stdio/config template과 skill이 있지만 실제 client domain E2E 기록은 아직 필요 |
| Codex desktop/CLI local MCP | local stdio 등록 경로 제공; 사용자 환경 검증 필요 |
| Generic MCP SDK | Experimental; protocol test와 실제 최종 사용자 client 검증은 다름 |
| ChatGPT remote MCP/App | 계정 연결성과 필요한 auth flow 검증 전까지 Deployment deferred |

특정 client의 실제 검증이 끝나기 전에 repository에 설정 예제가 먼저 존재할 수 있습니다. 설정 파일이 있다는 사실보다 위 상태표를 우선해 해석하세요.

## 읽기 전용 도구 표면

현재 서버에는 ping, entity resolve/select, material/session/concept/exam/activity/user context, claim verification, 제한된 source chunk retrieval 도구가 있습니다. 정확한 이름과 입력은 [MCP 도구 레퍼런스](../reference/mcp-tools.ko.md)를 참고하세요.

사용자 입장에서는 한 가지 원칙만 기억하면 됩니다. **MCP 검색은 질문하고 검증하기 위한 경계이지, 학업 워크스페이스를 몰래 수정하는 인터페이스가 아닙니다.**

## 클라이언트 설정

client 등록, 절대 실행/설정 경로, credential, 원격 네트워크는 운영자 작업입니다. [운영자: MCP 클라이언트](../operator-guide/mcp-clients.ko.md)를 참고하세요.

연결 문제를 해결하려고 provider token을 prompt, instruction file, chat message에 붙여 넣지 마세요.
