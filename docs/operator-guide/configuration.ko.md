# 설정

[English](configuration.md) · [운영자 가이드](README.ko.md)

Syllva는 명시적 설정과 process environment credential을 사용합니다. runtime은 `.env` 파일을 자동으로 읽지 않습니다.

## 원칙

1. identity/configuration 데이터와 secret을 분리합니다.
2. 상대 경로는 shell의 현재 디렉터리가 아니라 config file 기준으로 해석합니다.
3. provider ID, relation, parent, permission을 검증할 수 없으면 fail closed합니다.
4. 편하다는 이유만으로 worker와 read-only MCP가 같은 credential을 공유하지 않습니다.

`config.example.yaml` 또는 `uls init`이 만든 파일에서 시작하세요.

## Credential 경계

저장소의 deployment profile에는 다음 environment variable이 문서화되어 있습니다.

| 프로세스 | 환경 변수 |
| --- | --- |
| Worker | `GOOGLE_WORKER_CREDENTIALS_FILE`, `NOTION_WORKER_TOKEN` |
| Local/remote MCP | `GOOGLE_MCP_CREDENTIALS_FILE`, `NOTION_MCP_TOKEN`; private GitHub source 사용 시 `GITHUB_READ_TOKEN` |
| Remote development bearer | `REMOTE_MCP_SECRET`, `REMOTE_MCP_EXPIRES_AT` |

provider 측 least privilege를 적용하세요. provider가 지원하면 MCP credential은 read-only여야 합니다. Worker credential은 명시적으로 구성한 작업에 필요한 write 권한만 가져야 합니다.

실제 token을 다음 위치에 넣지 마세요.

- `config.yaml`;
- `sources.json`;
- client skill/instruction file;
- CLI argument;
- issue/PR 본문 또는 diagnostic log.

## 과목과 학기 identity

현재 학기 intake는 명시적 course key와 정확한 provider ID를 사용합니다. 대표 구조:

```yaml
courses:
  - course_key: "2026-2_COURSE001-001"
    name: "예시 과목"
    code: "COURSE001"
    section: "001"
    semester: "2026-2"
```

name은 표시/탐색에 유용하지만 durable binding은 title을 추측하기보다 설정된 identity를 사용해야 합니다.

## Intake preview 설정

preview lane은 같은 학기에 대해 Drive semester registry와 Notion semester workspace가 모두 있어야 합니다. 대표 필드:

```yaml
google_drive:
  semester_registries:
    - semester: "2026-2"
      folder_id: "<semester-folder-id>"
      upload_folder_id: "<upload-folder-id>"
      course_folder_ids:
        "2026-2_COURSE001-001": "<course-folder-id>"

notion:
  semester_workspaces:
    - semester: "2026-2"
      academic_courses_data_source_id: "<courses-data-source-id>"
      sessions_data_source_id: "<sessions-data-source-id>"
      materials_data_source_id: "<materials-data-source-id>"
      file_intake_data_source_id: "<file-intake-data-source-id>"
      input_requests_data_source_id: "<input-requests-data-source-id>"
```

이 다섯 current-semester intake data source ID와 기존 legacy read-only retrieval composition의 global ID를 섞지 마세요. 두 lane 사이에 암묵적 fallback은 없습니다.

## Source registration

`sources.json`은 metadata-only 명시적 source registration 경로입니다. source를 설정된 course/location에 결속해야 하며 원문 본문이나 secret을 저장하는 곳이 아닙니다.

## 검증

중요한 설정 변경 후:

```bash
uls doctor
uls status
uls behavior lint
```

필요한 credential을 준비했고 read-only live provider probe를 의도적으로 실행할 때만 `uls doctor --live`를 사용하세요. probe 성공만으로 최종 AI client 검증이 끝나는 것은 아닙니다.
