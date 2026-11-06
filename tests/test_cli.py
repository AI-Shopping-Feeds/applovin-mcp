"""CLI: transports and the HTTP binding guard, key setup, purge and describe."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from applovin_mcp import cli


class _FakeServer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, transport: str, **kwargs: Any) -> None:
        self.calls.append((transport, kwargs))


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch):
    server = _FakeServer()
    monkeypatch.setattr(cli, "build_server", lambda config: server)
    warnings: list[str] = []
    monkeypatch.setattr(cli.logger, "warning", lambda msg, *args: warnings.append(msg % args))

    def invoke(*args: str, ok: bool = True):
        result = CliRunner().invoke(cli.cli, ["--env", "missing.env", *args, "run"], obj={})
        if ok:
            assert result.exit_code == 0, result.output
        return result, server.calls, warnings

    return invoke


def test_stdio_is_the_default(fake_run) -> None:
    _, calls, warnings = fake_run()
    assert calls == [("stdio", {})]
    assert warnings == []


def test_streamable_http_on_loopback(fake_run) -> None:
    _, calls, _ = fake_run("--transport", "streamable-http", "--port", "9123")
    assert calls == [("streamable-http", {"host": "127.0.0.1", "port": 9123})]


def test_non_loopback_http_is_refused(fake_run) -> None:
    result, calls, _ = fake_run("--transport", "streamable-http", "--host", "0.0.0.0", ok=False)
    assert result.exit_code != 0
    assert "APPLOVIN_ALLOW_REMOTE" in result.output
    assert calls == []


def test_allow_remote_flag_binds_with_a_warning(fake_run) -> None:
    _, calls, warnings = fake_run(
        "--transport", "streamable-http", "--host", "0.0.0.0", "--allow-remote"
    )
    assert calls == [("streamable-http", {"host": "0.0.0.0", "port": 8000})]
    assert any("no authentication" in w for w in warnings)


def test_allow_remote_env(fake_run, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPLOVIN_ALLOW_REMOTE", "true")
    _, calls, _ = fake_run("--transport", "streamable-http", "--host", "10.0.0.5")
    assert calls[0][1]["host"] == "10.0.0.5"


def test_setup_saves_keys_privately_without_echoing(_isolated_env: Path) -> None:
    answers = "\n".join(
        [
            "cm-key-from-prompt",  # Campaign Management key
            "123456789",  # account ID
            "",  # Ads Manager Report Key: skip
            "max-report-from-prompt",
            "",
            "",
        ]
    )
    result = CliRunner().invoke(
        cli.cli, ["--env", "missing.env", "setup"], input=answers + "\n", obj={}
    )
    assert result.exit_code == 0, result.output
    path = _isolated_env / ".applovin-mcp" / "secrets" / "keys.json"
    assert json.loads(path.read_text()) == {
        "APPLOVIN_CAMPAIGN_MANAGEMENT_API_KEY": "cm-key-from-prompt",
        "APPLOVIN_ACCOUNT_ID": "123456789",
        "APPLOVIN_MAX_REPORT_KEY": "max-report-from-prompt",
    }
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "cm-key-from-prompt" not in result.output
    assert "max-report-from-prompt" not in result.output


def test_purge_deletes_audit_and_keys(_isolated_env: Path) -> None:
    folder = _isolated_env / ".applovin-mcp"
    audit = folder / "logs" / "audit.jsonl"
    keys = folder / "secrets" / "keys.json"
    audit.parent.mkdir(parents=True)
    keys.parent.mkdir(parents=True)
    audit.write_text("{}\n")
    keys.write_text("{}")
    result = CliRunner().invoke(cli.cli, ["--env", "missing.env", "purge", "--yes"], obj={})
    assert result.exit_code == 0, result.output
    assert not audit.exists()
    assert not keys.exists()


def test_describe_hides_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPLOVIN_MAX_MANAGEMENT_KEY", "describe-secret-value")
    result = CliRunner().invoke(cli.cli, ["--env", "missing.env", "describe"], obj={})
    assert result.exit_code == 0, result.output
    assert "describe-secret-value" not in result.output
    assert json.loads(result.output)["keys_set"]["APPLOVIN_MAX_MANAGEMENT_KEY"] is True


def test_unknown_transport_is_rejected() -> None:
    result = CliRunner().invoke(cli.cli, ["--transport", "websocket", "run"], obj={})
    assert result.exit_code != 0
    assert "streamable-http" in result.output
