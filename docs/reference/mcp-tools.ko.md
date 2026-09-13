# MCP 도구 레퍼런스

[English](mcp-tools.md) · [레퍼런스](README.ko.md)

현재 Syllva server는 읽기 전용 retrieval surface를 제공합니다. 정확한 schema는 source code에 정의되어 있으며 이 문서는 이름/목적 인덱스입니다.

| 도구 | 목적 |
| --- | --- |
| `uls.ping` | 기본 MCP/server availability 확인. |
| `uls.resolve_entity` | 사용자 표현을 제한된 entity/candidate set으로 resolve. |
| `uls.select_resolution` | 이전 resolution 후보 중 하나 선택. |
| `uls.get_material_context` | 제한된 material context 검색. |
| `uls.get_session_context` | 제한된 session context 검색. |
| `uls.search_concept` | concept 관련 학업 context 검색. |
| `uls.get_exam_context` | 현재 exam-scope 규칙 아래 context 검색. |
| `uls.get_activity_context` | activity/task workflow용 context 검색. |
| `uls.get_user_context` | source evidence와 분리된 허용 user-owned context 검색. |
| `uls.verify_claim` | 허용된/current evidence에 대해 claim 검증. |
| `uls.get_source_chunk` | 현재 locator/capability가 허용하는 제한된 source chunk 조회. |

## 경계

이 도구들은 일반적인 provider-write API가 아닙니다. Drive 파일 업로드, Notion human approval 변경, worker intake gate 우회 도구로 취급하면 안 됩니다.

## 모호성과 capability

entity resolution은 추측 대신 후보를 반환할 수 있습니다. 후속 source access에는 current source state에 결속된 valid bounded capability가 필요할 수 있습니다. capability가 stale/invalid면 요청 범위를 넓히지 말고 다시 resolve하세요.
