# Configuration

[한국어](configuration.ko.md) · [Operator Guide](README.md)

Syllva uses explicit configuration and process-environment credentials. The runtime does not implicitly load `.env` files.

## Principles

1. Keep identity/configuration data separate from secrets.
2. Resolve relative paths against the config file rather than the shell's current directory.
3. Fail closed when a provider ID, relation, parent, or permission cannot be verified.
4. Do not make the worker and read-only MCP share credentials merely for convenience.

Start from `config.example.yaml` or the files created by `uls init`.

## Credential boundaries

The checked-in deployment profile documents these environment variables:

| Process | Environment |
| --- | --- |
| Worker | `GOOGLE_WORKER_CREDENTIALS_FILE`, `NOTION_WORKER_TOKEN` |
| Local/remote MCP | `GOOGLE_MCP_CREDENTIALS_FILE`, `NOTION_MCP_TOKEN`; `GITHUB_READ_TOKEN` when private GitHub sources are used |
| Remote development bearer | `REMOTE_MCP_SECRET`, `REMOTE_MCP_EXPIRES_AT` |

Use provider-side least privilege. MCP credentials should be read-only where the provider supports it. Worker credentials may need write permissions for the specific configured workflow.

Do not put real tokens in:

- `config.yaml`;
- `sources.json`;
- client skill/instruction files;
- CLI arguments;
- issue/PR text or diagnostic logs.

## Course and semester identity

Current-semester intake uses explicit course keys and exact provider IDs. A representative structure is:

```yaml
courses:
  - course_key: "2026-2_COURSE001-001"
    name: "Example Course"
    code: "COURSE001"
    section: "001"
    semester: "2026-2"
```

Names are useful for presentation/search, but durable bindings should use the configured identities rather than guessing from a title.

## Intake preview configuration

The preview lane requires both a Drive semester registry and a Notion semester workspace for the same semester. Representative fields include:

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

Do not mix these five current-semester intake data-source IDs with the existing global IDs used by the legacy read-only retrieval composition. There is no implicit fallback between those lanes.

## Source registrations

`sources.json` is a metadata-only explicit source-registration path. It should bind a source to a configured course/location; it is not a place for source bodies or secrets.

## Validation

After each material configuration change:

```bash
uls doctor
uls status
uls behavior lint
```

Use `uls doctor --live` only when you intentionally want read-only live provider probes and have supplied the required credentials. A successful probe does not by itself validate an end-user AI client.
