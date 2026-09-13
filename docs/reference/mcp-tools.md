# MCP Tool Reference

[한국어](mcp-tools.ko.md) · [Reference](README.md)

The current Syllva server exposes a read-only retrieval surface. Exact schemas are defined in the source code; this page is a name/purpose index.

| Tool | Purpose |
| --- | --- |
| `uls.ping` | Basic MCP/server availability check. |
| `uls.resolve_entity` | Resolve a user-facing reference into a bounded entity/candidate set. |
| `uls.select_resolution` | Select one candidate from a previously issued resolution set. |
| `uls.get_material_context` | Retrieve bounded material context. |
| `uls.get_session_context` | Retrieve bounded session context. |
| `uls.search_concept` | Search concept-relevant academic context. |
| `uls.get_exam_context` | Retrieve context under current exam-scope rules. |
| `uls.get_activity_context` | Retrieve context for an activity/task workflow. |
| `uls.get_user_context` | Retrieve allowed user-owned context separately from source evidence. |
| `uls.verify_claim` | Check a claim against allowed/current evidence. |
| `uls.get_source_chunk` | Fetch a bounded source chunk allowed by the current locator/capability. |

## Boundary

These tools do not form a generic provider-write API. They must not be treated as tools for uploading Drive files, changing Notion human approvals, or bypassing worker intake gates.

## Ambiguity and capabilities

Entity resolution can return candidates instead of guessing. Follow-up source access may require a valid bounded capability tied to current source state. Re-resolve when a capability is stale or invalid rather than broadening the request.
