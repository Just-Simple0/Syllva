# Syllva 클라이언트 패키지

[English](README.md)

모든 client는 동일한 읽기 전용 MCP 도구와 서버가 강제하는 evidence scope를 사용합니다. 공통 학습 행동의 canonical 문서는 `contracts/study-behavior.md`입니다. 각 instruction/skill projection은 공통 contract와의 관계를 유지해야 하며 packaging 전 `uls behavior lint`가 통과해야 합니다.

Syllva 0.1.3은 베타입니다. 저장소에 client projection이나 config 예제가 있다는 것은 integration 형태가 존재한다는 뜻이며, 실제 최종 사용자 client의 live domain E2E가 자동으로 완료되었다는 뜻은 아닙니다.

## Portable bundle 만들기

저장소 checkout에서:

```bash
python scripts/package_clients.py --output /absolute/path/uls-clients.zip
```

archive에는 canonical behavior contract, Claude skill, ChatGPT instruction, local MCP config 자료, support metadata, E2E checklist가 포함됩니다. credential이나 사용자 config는 포함하지 않으며 기존 output file을 덮어쓰지 않습니다.

## Claude local MCP

Syllva를 `mcp,drive,notion` extra와 함께 설치합니다. MCP process 환경에 read-only provider credential을 구성합니다. `claude/mcp-config.example.json`의 executable/config path를 절대 경로로 바꾼 뒤 local MCP를 지원하는 Claude client에 등록합니다.

`claude/skills/*`는 해당 skill mechanism을 지원하는 Claude 환경에만 복사하세요. Claude surface마다 capability가 다르므로 저장소는 모든 Claude client가 동일하다고 가정하지 않습니다.

현재 상태: 대상 환경에서 실제 client domain E2E가 기록되기 전까지 **EXPERIMENTAL**.

## Codex desktop/CLI

Codex도 지원되는 MCP 설정을 통해 동일한 local stdio MCP server를 사용할 수 있습니다. 명령/TOML 예시는 [운영자: MCP 클라이언트](../docs/operator-guide/mcp-clients.ko.md)를 참고하세요.

Codex가 실제로 실행하는 환경에서 executable/config path를 검증하세요. local config가 존재한다는 것과 최종 사용자 E2E가 완료되었다는 것은 다릅니다.

## ChatGPT remote MCP/App

`chatgpt/instructions/study-behavior.md`는 대상 ChatGPT 환경에서 지원되는 인증 MCP 연결과 함께 사용할 때만 behavior projection 역할을 합니다.

내장 remote profile은 짧게 살아 있는 **development bearer** integration이며 OAuth가 아닙니다. OAuth 또는 다른 gateway가 필요한 target은 해당 외부 auth layer, account connectivity, domain E2E가 독립 검증될 때까지 **DEPLOYMENT_DEFERRED**로 유지합니다.

ChatGPT web은 이 checkout의 local stdio config를 읽지 않습니다. ChatGPT instruction이나 prompt에 credential을 붙여 넣지 마세요.

## 검증

`support-matrix.json`, `e2e-checklist.md`를 참고하세요. 자동 SDK/protocol test는 저장소 동작을 검증하지만 실제 client 지원을 증명하지는 않습니다.

client profile은 대상 환경의 전체 E2E가 기록된 뒤에만 validated라고 부르는 것이 안전합니다. Primary PC가 오프라인이면 remote request에 응답할 수 없으며 public/anyone-with-link sharing을 우회책으로 사용하지 않습니다.
