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
| Remote MCP OAuth browser login | `REMOTE_MCP_GOOGLE_CLIENT_SECRET` plus non-secret `remote_mcp.oauth.google_client_id` / `authorized_email` config |

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

## Semester-scoped read-only retrieval

The v1.2 global registry remains the default. To read one current-semester
workspace directly, opt in with an exact semester:

```yaml
retrieval:
  notion_lane: semester_workspace
  semester: "2026-2"
```

This mode uses the selected workspace's Courses, Sessions, and Materials
`data_source_id` values directly; it never copies them into or falls back to
the legacy global `*_db_id` fields. Course relations are checked against the
selected Courses source and semester before a Course, Session, or Material can
be returned.

`material_usage_data_source_id` is optional. When it is absent, Session
retrieval remains available from transcript evidence and reports Material Usage
as unavailable; it does not treat the missing source as a verified empty
database. Semester-scoped Exam and Activity retrieval is unavailable until
explicit mappings exist. `uls doctor --live` probes a Course from the
selected semester when this lane is enabled.

To return to the frozen v1.2 path, use `notion_lane: legacy_global` with an
empty `semester`.

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
