"""Shared fixtures. Every test runs offline: HTTP is mocked with respx and all
keys are obviously fake."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from applovin_mcp.audit import AuditLog
from applovin_mcp.client import AppLovinClient
from applovin_mcp.config import Config

ADS = "https://api.ads.axon.ai/manage/v1"
R = "https://r.applovin.com"
MAX = "https://o.applovin.com/mediation/v1"
REVIEW = "https://api-safedk.applovin.com"
ACCOUNT = "4242"
CM_KEY = "fake-cm-key-0001"
REPORT_KEY = "fake-report-key-0002"
MAX_REPORT_KEY = "fake-max-report-key-0003"
MGMT_KEY = "fake-mgmt-key-0004"
REVIEW_KEY = "fake-review-key-0005"


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake HOME and no APPLOVIN_* variables from the developer's shell."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for var in list(os.environ):
        if var.startswith("APPLOVIN_"):
            monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    return home


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr("applovin_mcp.client.time.sleep", lambda s: slept.append(s))
    return slept


@pytest.fixture
def tmp_audit_log(tmp_path: Path) -> Path:
    return tmp_path / "logs" / "audit.jsonl"


@pytest.fixture
def config(tmp_audit_log: Path, tmp_path: Path) -> Config:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    return Config(
        cm_api_key=CM_KEY,
        account_id=ACCOUNT,
        ads_reporting_key=REPORT_KEY,
        max_report_key=MAX_REPORT_KEY,
        max_management_key=MGMT_KEY,
        ad_review_key=REVIEW_KEY,
        audit_log=tmp_audit_log,
        keys_file=tmp_path / "secrets" / "keys.json",
        upload_dir=uploads,
        timeout=5.0,
    )


@pytest.fixture
def audit(tmp_audit_log: Path) -> AuditLog:
    return AuditLog(tmp_audit_log)


@pytest.fixture
def client(config: Config, audit: AuditLog):
    from applovin_mcp.credentials import register_secret

    for key in (CM_KEY, REPORT_KEY, MAX_REPORT_KEY, MGMT_KEY, REVIEW_KEY):
        register_secret(key)
    c = AppLovinClient(config, audit)
    yield c
    c.close()


def audit_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def payload(result: Any) -> Any:
    """The JSON a tool returned through mcp.Client."""
    if result.structured_content is not None:
        sc = result.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(result.content[0].text)
