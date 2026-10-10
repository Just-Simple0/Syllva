"""C6 local configuration shape.

Owned entirely inside this package so parent composition (`config.study_notes`
in the global YAML schema) can construct this dataclass without this package
depending on `uls.config.schema`.  Defaults match the values the orchestrator
specified for the eventual global config section; the global schema/loader/
validation themselves are not touched here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StudyNoteConfig:
    """Local, deployment-level opt-in and versioning for the C6 service.

    `generator_config_version` identifies the *submission port* version
    (this package's contract with whatever MCP client submits a draft), not a
    verified AI provider/model identity -- no such identity is ever asserted
    by the server.  Any model metadata a client optionally includes with a
    draft is stored as unverified, client-supplied text and never labeled as
    a verified fact.
    """

    enabled: bool = False
    local_caller_id: str = "uls-local-study-notes"
    template_version: str = "study-note.v1"
    generator_config_version: str = "mcp-client-draft.v1"
    grant_ttl_seconds: int = 1800
    max_draft_chars: int = 100_000

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be a boolean")
        for name in ("local_caller_id", "template_version", "generator_config_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if isinstance(self.grant_ttl_seconds, bool) or self.grant_ttl_seconds < 1:
            raise ValueError("grant_ttl_seconds must be a positive integer")
        if isinstance(self.max_draft_chars, bool) or self.max_draft_chars < 1:
            raise ValueError("max_draft_chars must be a positive integer")


__all__ = ["StudyNoteConfig"]
