# MCP와 클라이언트

ULS custom MCP와 native Notion connector는 서로 다른 표면이다. v1.3 preview의
5개 data source는 파일 등록과 처리용이고, 기존 7개 global-ID data source는 custom
ULS MCP의 legacy read-only reader용이다. preview가 구성되어도 legacy reader를
자동으로 대체하거나 그 반대로 fallback하지 않는다.

native Notion connector의 현재 workspace 구조와 readback은 별도 운영 경계에서
검증되었다. custom ULS MCP client의 전체 E2E 연결은 사용자 환경에서 별도 config와
자격증명이 필요하고, 현재 문서는 그 연결 성공을 보장하지 않는다. 두 경로 모두
MCP 검색 표면 자체는 read-only다.

## CODEX desktop/CLI

운영자: 저장소 루트에서 의존성을 설치하고 설정을 초기화한다.

```sh
python -m venv /ABSOLUTE/PATH/uls-venv
source /ABSOLUTE/PATH/uls-venv/bin/activate
pip install -e '.[dev,mcp,drive,notion,pdf]'
uls --config /ABSOLUTE/PATH/config.yaml init
uls --config /ABSOLUTE/PATH/config.yaml doctor
```

CODEX desktop/CLI에서는 다음 명령으로 local stdio 서버를 추가한다. 경로는 실제
절대 경로로 바꾼다.

```sh
codex mcp add uls -- /ABSOLUTE/PATH/uls-venv/bin/uls \
  --config /ABSOLUTE/PATH/config.yaml mcp local
```

설정 파일을 직접 관리하는 경우에는 다음 TOML 항목을 사용할 수 있다.

```toml
[mcp_servers.uls]
command = "/ABSOLUTE/PATH/uls-venv/bin/uls"
args = ["--config", "/ABSOLUTE/PATH/config.yaml", "mcp", "local"]
```

서버 프로세스를 직접 확인할 때는 stdio stdout에 MCP protocol 외 로그를 섞지 않는다.

```sh
/ABSOLUTE/PATH/uls-venv/bin/uls \
  --config /ABSOLUTE/PATH/config.yaml mcp local
```

현재 저장소의 tool 이름은 `uls.ping`, `uls.resolve_entity`,
`uls.select_resolution`, `uls.get_material_context`, `uls.get_session_context`,
`uls.search_concept`, `uls.get_exam_context`, `uls.get_activity_context`,
`uls.get_user_context`, `uls.verify_claim`, `uls.get_source_chunk`이다. 이 도구들은
검색·bounded context 조회용이며 upload, 파일 분류, Notion 자동 작성, 승인 필드
승격을 수행하지 않는다.

## Claude local MCP

아래 `mcpServers` JSON 형식은 Claude client용이다. CODEX desktop/CLI 설정에 이
JSON 형식을 사용하지 않는다.

```json
{
  "mcpServers": {
    "uls": {
      "command": "/ABSOLUTE/PATH/uls-venv/bin/uls",
      "args": ["--config", "/ABSOLUTE/PATH/config.yaml", "mcp", "local"]
    }
  }
}
```

## ChatGPT web 및 원격 연결

ChatGPT web에서 local config를 읽는다고 가정하지 않는다. 원격 연결은 별도 원격
연결 설정이며 계정별 실제 지원을 확인해야 한다. OAuth provider/gateway와 client
E2E 증거가 없으면 연결을 배포된 것으로 표시하지 않는다.

## 안전한 요청 예

```text
이 과목의 확인된 SOURCE 근거만 검색하고 USER 메모는 별도 표시해줘.
현재 자료가 Partial이면 부족한 입력과 다음 확인 단계를 알려줘.
```
