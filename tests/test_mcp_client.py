"""End-to-end tests through `mcp.Client` against the real server (fake keys, mocked HTTP)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.types import Tool

from applovin_mcp import __version__, server, tools
from applovin_mcp.client import AppLovinClient
from applovin_mcp.config import Config
from applovin_mcp.tool_catalog import ALWAYS_REGISTERED, TOOL_SPECS

from .conftest import ADS, CM_KEY, MAX, REVIEW, R, payload

META_TOOLS = [
    "applovin_describe_server",
    "applovin_health_check",
    "applovin_audit_lookup",
    "applovin_audit_tail",
    "applovin_set_dry_run",
]
EXPECTED_TOOL_COUNT = 42


def _build(config: Config, **changes: Any) -> MCPServer:
    return server.build_server(dataclasses.replace(config, **changes))


async def _tools(mcp: MCPServer) -> list[Tool]:
    async with Client(mcp) as c:
        return (await c.list_tools()).tools


def _group_tool_names(client: AppLovinClient) -> dict[str, list[str]]:
    """Tool names per group, by registering each module on its own server."""
    out: dict[str, list[str]] = {}
    for group in tools.TOOL_GROUPS:
        mcp = MCPServer("probe")
        getattr(tools, group).register(mcp, client)
        out[group] = list(mcp._tool_manager._tools)
    return out


# ---------------------------------------------------------------- metadata


async def test_server_reports_version_and_instructions(config: Config) -> None:
    async with Client(_build(config)) as c:
        assert c.server_info is not None
        assert c.server_info.name == "applovin-mcp"
        assert c.server_info.version == __version__
        assert c.instructions
        assert "applovin_describe_server" in c.instructions
        assert "dry-run" in c.instructions
        assert "APPLOVIN_TOOL_GROUPS" in c.instructions
        assert "confidential" in c.instructions
        assert 4 <= len(c.instructions.splitlines()) <= 8


async def test_every_tool_has_a_catalog_entry(config: Config) -> None:
    listed = await _tools(_build(config))
    assert len(listed) == EXPECTED_TOOL_COUNT
    for tool in listed:
        assert tool.name.startswith("applovin_"), tool.name
        assert tool.name in TOOL_SPECS, f"{tool.name} has no tool_catalog entry"
        assert tool.title, tool.name
        assert tool.title[0].isupper(), tool.name
        assert tool.description, tool.name
        a = tool.annotations
        assert a is not None, tool.name
        assert a.read_only_hint is not None, tool.name
        assert a.destructive_hint is not None, tool.name
        assert a.idempotent_hint is not None, tool.name
        assert a.open_world_hint is not None, tool.name
    # The catalog has no stale entries either.
    assert {t.name for t in listed} == set(TOOL_SPECS)


async def test_annotations_are_plausible(config: Config) -> None:
    by_name = {t.name: t.annotations for t in await _tools(_build(config))}
    read_only = {n for n, a in by_name.items() if a and a.read_only_hint}
    destructive = {n for n, a in by_name.items() if a and a.destructive_hint}
    assert not read_only & destructive
    for name in by_name:
        verb = (
            name.removeprefix("applovin_").removeprefix("ads_").removeprefix("max_").split("_")[0]
        )
        if verb in {"list", "get", "report", "read"} or name.endswith("_report"):
            assert name in read_only, name
        if verb in {"remove", "end"}:
            assert name in destructive, name
        if verb in {"create", "update", "set", "add", "upload", "clone"} and name != (
            "applovin_set_dry_run"
        ):
            assert name not in read_only, name
    assert "applovin_request" in destructive
    assert {"applovin_max_user_revenue", "applovin_read_request"} <= read_only
    closed = {n for n, a in by_name.items() if a and a.open_world_hint is False}
    assert closed == set(META_TOOLS) - {"applovin_health_check"}


async def test_tools_list_order_is_deterministic(config: Config, client: AppLovinClient) -> None:
    first = [t.name for t in await _tools(_build(config))]
    second = [t.name for t in await _tools(_build(config))]
    assert first == second
    per_group = _group_tool_names(client)
    expected = META_TOOLS + [n for g in tools.TOOL_GROUPS for n in per_group[g]]
    assert first == expected


def test_groups_are_one_sided(client: AppLovinClient) -> None:
    for group, names in _group_tool_names(client).items():
        side = tools.side_of(group)
        for name in names:
            if side == "ads":
                assert name.startswith("applovin_ads_"), name
            elif side == "max":
                assert name.startswith("applovin_max_"), name


# ---------------------------------------------------------------- filtering


async def test_tool_groups_limit_registration(config: Config, client: AppLovinClient) -> None:
    names = [
        t.name for t in await _tools(_build(config, tool_groups=["max_reporting", "ads_reporting"]))
    ]
    per_group = _group_tool_names(client)
    assert names == META_TOOLS + per_group["ads_reporting"] + per_group["max_reporting"]


async def test_max_side_only(config: Config) -> None:
    groups = tools.resolve_tool_groups(["max"])
    names = {t.name for t in await _tools(_build(config, tool_groups=groups))}
    assert names - set(META_TOOLS)
    assert all(n.startswith("applovin_max_") for n in names - set(META_TOOLS))


async def test_read_only_registers_only_read_tools(config: Config) -> None:
    listed = await _tools(_build(config, read_only=True))
    names = {t.name for t in listed}
    assert set(META_TOOLS) <= names
    for tool in listed:
        if tool.name not in ALWAYS_REGISTERED:
            assert tool.annotations and tool.annotations.read_only_hint, tool.name
    assert "applovin_ads_update_campaigns" not in names
    assert "applovin_request" not in names
    assert "applovin_ads_report" in names
    expected = {n for n, s in TOOL_SPECS.items() if s.annotations.read_only_hint}
    assert names == expected | set(META_TOOLS)


async def test_env_vars_filter_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPLOVIN_TOOL_GROUPS", "max_ad_review,ads_reporting")
    monkeypatch.setenv("APPLOVIN_READONLY", "1")
    names = {t.name for t in await _tools(server.build_server(env_file=Path("missing.env")))}
    assert names == set(META_TOOLS) | {
        "applovin_ads_report",
        "applovin_ads_asset_report",
        "applovin_ads_spend_report",
        "applovin_max_list_ad_review_rules",
    }


def test_unknown_group_fails_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPLOVIN_TOOL_GROUPS", "ads,nope")
    with pytest.raises(RuntimeError) as exc_info:
        server.build_server(env_file=Path("missing.env"))
    message = str(exc_info.value)
    assert "nope" in message
    for group in tools.TOOL_GROUPS:
        assert group in message


# ---------------------------------------------------------------- calls


@respx.mock
async def test_api_error_reaches_the_client(config: Config) -> None:
    respx.get(f"{ADS}/campaign/list").mock(
        return_value=httpx.Response(401, headers={"x-al-error-message": "Unauthorized"})
    )
    async with Client(_build(config)) as c:
        result = await c.call_tool("applovin_ads_list_campaigns", {})
    assert result.is_error
    text = " ".join(getattr(block, "text", "") for block in result.content)
    assert "Campaign Management API error 401" in text
    assert "whitelist-only" in text
    assert CM_KEY not in text


async def test_missing_key_is_explained(config: Config) -> None:
    async with Client(_build(config, max_report_key=None)) as c:
        result = await c.call_tool(
            "applovin_max_revenue_report",
            {"start": "2026-11-01", "end": "2026-11-02", "columns": ["day"]},
        )
    assert result.is_error
    assert "APPLOVIN_MAX_REPORT_KEY" in result.content[0].text


async def test_describe_server_hides_secrets(config: Config) -> None:
    async with Client(_build(config, tool_groups=["max_reporting"], read_only=True)) as c:
        result = await c.call_tool("applovin_describe_server", {})
    out = payload(result)
    assert out["sides"] == ["max"]
    assert out["read_only"] is True
    assert out["keys_set"]["APPLOVIN_MAX_REPORT_KEY"] is True
    assert out["version"] == __version__
    assert "fake-" not in json.dumps(out)


@respx.mock
async def test_health_check_probes_each_configured_api(config: Config) -> None:
    respx.get(f"{ADS}/campaign/list").mock(return_value=httpx.Response(403))
    respx.get(f"{R}/report").mock(return_value=httpx.Response(200, json={"results": []}))
    respx.get(f"{R}/maxReport").mock(return_value=httpx.Response(200, json={"results": []}))
    respx.get(f"{MAX}/ad_units").mock(return_value=httpx.Response(200, json=[]))
    async with Client(_build(config, ad_review_key=None)) as c:
        out = payload(await c.call_tool("applovin_health_check", {}))
    by_check = {c["check"]: c for c in out["checks"]}
    assert by_check["ads"]["ok"] is False
    assert "whitelist-only" in by_check["ads"]["error"]
    assert by_check["ads_reporting"]["ok"] is True
    assert by_check["max_reporting"]["ok"] is True
    assert by_check["max"]["ok"] is True
    assert by_check["ad_review"]["ok"] is None
    assert out["ok"] is False
    assert all(REVIEW not in str(call.request.url) for call in respx.calls)


async def test_set_dry_run_and_audit_tail(config: Config) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{MAX}/ad_unit/abcdef1234567890").mock(
            return_value=httpx.Response(200, json={"id": "abcdef1234567890", "name": "x"})
        )
        post = router.post(f"{MAX}/ad_unit/abcdef1234567890")
        async with Client(_build(config)) as c:
            await c.call_tool("applovin_set_dry_run", {"enabled": True})
            changed = payload(
                await c.call_tool(
                    "applovin_max_update_ad_unit",
                    {
                        "ad_unit_id": "abcdef1234567890",
                        "changes": {"banner_refresh_settings": {"interval": 30}},
                    },
                )
            )
            tail = payload(await c.call_tool("applovin_audit_tail", {"limit": 5}))
            found = payload(
                await c.call_tool("applovin_audit_lookup", {"audit_id": changed["audit_id"]})
            )
    assert changed["_dry_run"] is True
    assert not post.called
    assert tail["count"] == 1
    assert found["found"] is True


def test_version_matches_pyproject() -> None:
    root = Path(__file__).resolve().parent.parent
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert f'\nversion = "{__version__}"\n' in pyproject


def test_docs_list_every_tool() -> None:
    root = Path(__file__).resolve().parent.parent
    docs = (root / "docs" / "tools.md").read_text(encoding="utf-8")
    missing = [name for name in TOOL_SPECS if f"`{name}`" not in docs]
    assert not missing, f"docs/tools.md is missing {missing}"
    assert f"**{len(TOOL_SPECS)} tools" in docs
