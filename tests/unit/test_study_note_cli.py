from argparse import Namespace
from pathlib import Path

import pytest

from syllva.cli import main as cli
from syllva.config.errors import ConfigurationError
from syllva.config.schema import SyllvaConfig
from syllva.runtime import build_study_note_submission_server


def test_submission_is_separate_and_disabled_by_default():
    config = SyllvaConfig()
    assert config.mcp.read_only
    assert cli.parser().parse_args(["study-notes", "local"]).mode == "local"
    with pytest.raises(ConfigurationError, match="explicitly true"):
        build_study_note_submission_server(config)


def test_submission_cli_never_resolves_provider_credentials_and_closes_on_failure(monkeypatch):
    config = SyllvaConfig()
    config.study_notes.enabled = True
    events = []

    def credential_access(*args, **kwargs):
        raise AssertionError("submission must not load provider credentials")

    class Server:
        def close(self):
            events.append("closed")

    def serve(server):
        events.append("serve")
        raise RuntimeError("transport ended")

    monkeypatch.setattr(cli, "_config", lambda _: config)
    monkeypatch.setattr(cli, "CredentialResolver", credential_access)
    monkeypatch.setattr("syllva.runtime.build_study_note_submission_server", lambda _: Server())
    monkeypatch.setattr("syllva.mcp.transports.local.run_local", serve)
    args = Namespace(command="study-notes", mode="local", config=Path("unused.yaml"))
    with pytest.raises(RuntimeError, match="transport ended"):
        cli.dispatch(args)
    assert events == ["serve", "closed"]


def test_submission_status_does_not_claim_client_readiness(monkeypatch):
    config = SyllvaConfig()
    monkeypatch.setattr(cli, "_config", lambda _: config)
    status = cli.dispatch(Namespace(command="study-notes", mode="status", config=Path("unused.yaml")))
    assert status["enabled"] is False
    assert status["client_e2e"] == "not_proven"
    assert status["search_read_only"] is True
