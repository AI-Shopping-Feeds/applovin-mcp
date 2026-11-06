"""Configuration: per-user folders, keys file, .env lookup, aliases and validation."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from applovin_mcp import credentials
from applovin_mcp.config import is_loopback, load_config


def test_defaults_live_in_the_user_folder(_isolated_env: Path) -> None:
    config = load_config(env_file=Path("missing.env"))
    assert config.audit_log == _isolated_env / ".applovin-mcp" / "logs" / "audit.jsonl"
    assert config.keys_file == _isolated_env / ".applovin-mcp" / "secrets" / "keys.json"
    assert config.cm_api_key is None
    assert config.max_increase_pct == 20.0
    assert config.allow_remote is False


def test_keys_file_is_private_and_env_wins(
    _isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _isolated_env / ".applovin-mcp" / "secrets" / "keys.json"
    credentials.save_keys_file(
        path,
        {
            "APPLOVIN_MAX_REPORT_KEY": "file-max-report-key",
            "APPLOVIN_ACCOUNT_ID": "77",
            "UNRELATED": "dropped",
        },
    )
    assert json.loads(path.read_text()) == {
        "APPLOVIN_MAX_REPORT_KEY": "file-max-report-key",
        "APPLOVIN_ACCOUNT_ID": "77",
    }
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    config = load_config(env_file=Path("missing.env"))
    assert config.max_report_key == "file-max-report-key"
    assert config.account_id == "77"
    monkeypatch.setenv("APPLOVIN_MAX_REPORT_KEY", "env-max-report-key")
    assert load_config(env_file=Path("missing.env")).max_report_key == "env-max-report-key"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_loose_keys_file_is_tightened(_isolated_env: Path) -> None:
    path = _isolated_env / "keys.json"
    path.write_text(json.dumps({"APPLOVIN_AD_REVIEW_KEY": "review-key-value"}))
    os.chmod(path, 0o644)
    assert credentials.read_keys_file(path)["APPLOVIN_AD_REVIEW_KEY"] == "review-key-value"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_user_env_file_is_read(_isolated_env: Path) -> None:
    folder = _isolated_env / ".applovin-mcp"
    folder.mkdir()
    (folder / ".env").write_text("APPLOVIN_ACCOUNT_ID=55\nAPPLOVIN_DRY_RUN=true\n")
    config = load_config(env_file=Path("missing.env"))
    assert config.account_id == "55"
    assert config.dry_run is True


def test_official_readonly_name_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPLOVIN_READONLY", "true")
    assert load_config(env_file=Path("missing.env")).read_only is True


def test_side_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPLOVIN_TOOL_GROUPS", "max, request")
    assert load_config(env_file=Path("missing.env")).tool_groups == [
        "max_reporting",
        "max_ad_units",
        "max_ad_review",
        "request",
    ]
    monkeypatch.setenv("APPLOVIN_TOOL_GROUPS", "Ads-Reporting")
    assert load_config(env_file=Path("missing.env")).tool_groups == ["ads_reporting"]


@pytest.mark.parametrize(
    ("var", "value", "message"),
    [
        ("APPLOVIN_ACCOUNT_ID", "abc", "numeric"),
        ("APPLOVIN_ADS_ACCOUNT_TYPE", "tv", "app or web"),
        ("APPLOVIN_TRANSPORT", "websocket", "streamable-http"),
        ("APPLOVIN_TOOL_GROUPS", "ads,nope", "nope"),
        ("APPLOVIN_UPLOAD_DIR", "/definitely/not/here", "not a folder"),
        ("APPLOVIN_MAX_DAILY_BUDGET", "lots", "number"),
    ],
)
def test_invalid_values_are_explained(
    monkeypatch: pytest.MonkeyPatch, var: str, value: str, message: str
) -> None:
    monkeypatch.setenv(var, value)
    with pytest.raises(RuntimeError, match=message):
        load_config(env_file=Path("missing.env"))


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("127.0.0.2", True),
        ("0.0.0.0", False),
        ("192.168.1.5", False),
        ("example.com", False),
    ],
)
def test_is_loopback(host: str, expected: bool) -> None:
    assert is_loopback(host) is expected


def test_redact_covers_query_keys_headers_and_dict_keys() -> None:
    credentials.register_secret("registered-secret-1")
    text = credentials.redact(
        "GET https://r.applovin.com/report?api_key=abc123&x=1 Authorization: tok "
        "registered-secret-1"
    )
    assert "abc123" not in text
    assert "tok" not in text
    assert "registered-secret-1" not in text
    assert credentials.redact({"Api-Key": "v", "nested": [{"api_key": "w"}]}) == {
        "Api-Key": "***",
        "nested": [{"api_key": "***"}],
    }
