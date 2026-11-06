"""Build the MCP server. Stays import-light so tests can patch pieces individually."""

from __future__ import annotations

import functools
import inspect
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__, tools
from ._logging import configure_default, get_logger
from .audit import AuditLog
from .client import TARGETS, AppLovinAPIError, AppLovinClient
from .config import Config, load_config
from .credentials import redact
from .tool_catalog import ALWAYS_REGISTERED, TOOL_SPECS
from .tools._common import today_utc

logger = get_logger("server")

INSTRUCTIONS = """\
AppLovin Ads Manager (applovin_ads_*: campaigns, creative sets, assets, reports) and MAX (applovin_max_*: revenue, cohort and user-level reports, ad units, waterfalls).
Start with applovin_describe_server and applovin_health_check: they show which keys are set and whether the whitelist-only Campaign Management API accepts yours.
Writes change live campaigns, spend and ad revenue. Rehearse in dry-run (applovin_set_dry_run) and show the user what will change first.
Removals need confirm='DELETE'; big batches, budget, goal or CPM changes beyond the limit, promotions and raw writes need confirm='APPLY'.
Every write is in the audit log (applovin_audit_tail) with a before-snapshot where available. Failed Campaign Management calls count towards a 24-hour AppLovin block, so fix arguments rather than retrying.
AppLovin treats report statistics and pricing as confidential: don't share them outside the user's organisation, or merge user-level data with other personal data, unless the user confirms it's allowed.
Tools can be limited with APPLOVIN_TOOL_GROUPS ('ads', 'max' or group names) or APPLOVIN_READ_ONLY=true."""

# Errors whose message helps the agent fix the call (bad arguments, a refusal,
# AppLovin API errors, missing keys, permissions or config problems).
_EXPECTED_ERRORS = (
    ValueError,
    RuntimeError,
    PermissionError,
    FileNotFoundError,
    AppLovinAPIError,
    httpx.HTTPError,
)


def _as_tool_error(fn):
    """Re-raise expected errors as ToolError so MCP SDK 2 shows their message.

    SDK 2 hides the text of any other exception from the client. Messages are
    redacted so keys never reach the client.
    """
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except _EXPECTED_ERRORS as exc:
                raise ToolError(redact(str(exc))) from exc

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except _EXPECTED_ERRORS as exc:
            raise ToolError(redact(str(exc))) from exc

    return wrapper


class AppLovinMCPServer(MCPServer):
    """MCPServer whose tools report expected errors to the client.

    Each tool gets its title and annotations from `tool_catalog.TOOL_SPECS`
    unless the decorator passes them. With `read_only=True`, tools that are not
    annotated read-only are skipped (the meta tools are always registered).
    """

    def __init__(self, *args: Any, read_only: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.read_only = read_only

    def tool(  # type: ignore[override]
        self,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        annotations: ToolAnnotations | None = None,
        **kwargs: Any,
    ):
        def decorator(fn):
            tool_name = name or fn.__name__
            spec = TOOL_SPECS.get(tool_name)
            tool_title = title or (spec.title if spec else None)
            tool_annotations = annotations or (spec.annotations if spec else None)
            is_read_only = bool(tool_annotations and tool_annotations.read_only_hint)
            if self.read_only and not is_read_only and tool_name not in ALWAYS_REGISTERED:
                return fn
            register = super(AppLovinMCPServer, self).tool(
                name=tool_name,
                title=tool_title,
                description=description,
                annotations=tool_annotations,
                **kwargs,
            )
            register(_as_tool_error(fn))
            return fn

        return decorator


def _health_probe(client: AppLovinClient, api: str) -> tuple[str, dict[str, Any]]:
    """The cheapest read on each API, and what it proves."""
    day = (today_utc() - timedelta(days=1)).isoformat()
    if api == "ads":
        return "/campaign/list", {"page": 1, "size": 1}
    if api == "ads_reporting":
        if client.config.ads_account_type == "web":
            return "/webReport", {
                "report_type": "advertiser",
                "start": day,
                "end": day,
                "columns": "day,cost",
                "attribution_mode": "click",
                "format": "json",
                "limit": 1,
            }
        return "/report", {
            "report_type": "advertiser",
            "start": day,
            "end": day,
            "columns": "day,cost",
            "format": "json",
            "limit": 1,
        }
    if api == "max_reporting":
        return "/maxReport", {
            "start": day,
            "end": day,
            "columns": "day,impressions",
            "format": "json",
            "limit": 1,
        }
    if api == "max":
        return "/ad_units", {"limit": 1, "offset": 0}
    return "/v1/rules/competitors", {}


def build_server(config: Config | None = None, env_file: Path | None = None) -> MCPServer:
    """Construct an MCPServer instance with the configured applovin_* tools."""
    configure_default()
    if config is None:
        config = load_config(env_file=env_file)
    groups = tools.resolve_tool_groups(config.tool_groups)  # fail fast on unknown groups

    audit = AuditLog(config.audit_log, retention_days=config.audit_retention_days)
    pruned = audit.prune()
    if pruned:
        logger.info(
            "pruned %d audit entries older than %d days", pruned, config.audit_retention_days
        )
    client = AppLovinClient(config, audit)
    sides = {s for s in (tools.side_of(g) for g in groups) if s}
    client.enabled_sides = sides or {"ads", "max"}

    logger.info(
        "starting applovin-mcp sides=%s dry_run=%s read_only=%s keys=%s",
        ",".join(sorted(client.enabled_sides)),
        config.dry_run,
        config.read_only,
        ",".join(name for name, ok in config.key_status().items() if ok) or "none",
    )

    mcp = AppLovinMCPServer(
        "applovin-mcp",
        title="AppLovin",
        version=__version__,
        instructions=INSTRUCTIONS,
        read_only=config.read_only,
    )

    @mcp.tool()
    def applovin_describe_server() -> dict[str, Any]:
        """Show which AppLovin keys are set (never their values), which side (Ads
        Manager, MAX) and tool groups are enabled, and the safety settings."""
        return {
            "version": __version__,
            "sides": sorted(client.enabled_sides),
            "tool_groups": config.tool_groups or "all",
            "keys_set": config.key_status(),
            "account_id": config.account_id,
            "ads_account_type": config.ads_account_type,
            "dry_run": config.dry_run,
            "read_only": config.read_only,
            "bulk_threshold": config.bulk_threshold,
            "max_increase_pct": config.max_increase_pct,
            "max_daily_budget": config.max_daily_budget,
            "max_cpm": config.max_cpm,
            "upload_dir": str(config.upload_dir) if config.upload_dir else None,
            "audit_log": str(config.audit_log),
            "audit_retention_days": config.audit_retention_days,
            "keys_file": str(config.keys_file),
            "max_download_mb": config.max_download_mb,
            "timeout": config.timeout,
            "transport": config.transport,
            "api_hosts": sorted({t.base_url for t in TARGETS.values()}),
        }

    @mcp.tool()
    def applovin_health_check() -> dict[str, Any]:
        """Check every configured key with one cheap read per AppLovin API.

        A 401/403 from the Campaign Management API usually means the account
        isn't on AppLovin's whitelist yet; the reporting keys work without it.
        """
        checks: list[dict[str, Any]] = []
        for api, target in TARGETS.items():
            if target.side not in client.enabled_sides:
                continue
            if not client.has_key(api):
                checks.append({"check": api, "ok": None, "skipped": f"{target.key_env} isn't set"})
                continue
            path, params = _health_probe(client, api)
            t0 = time.time()
            try:
                client.request(api, "GET", path, params=params)
                checks.append({"check": api, "ok": True, "ms": int((time.time() - t0) * 1000)})
            except Exception as e:
                checks.append({"check": api, "ok": False, "error": redact(str(e))[:600]})
        ran = [c for c in checks if c["ok"] is not None]
        notes = []
        if not ran:
            notes.append(
                "No AppLovin keys are set. Run `applovin-mcp setup`, or set the keys in the "
                "client config; see docs/setup-applovin-api.md."
            )
        return {
            "ok": bool(ran) and all(c["ok"] for c in ran),
            "checks": checks,
            "notes": notes,
        }

    @mcp.tool()
    def applovin_audit_lookup(audit_id: str) -> dict[str, Any]:
        """Look up an audit log entry by ID: the request, response and before-snapshot of a write."""
        entry = audit.find(audit_id)
        if not entry:
            return {"found": False, "audit_id": audit_id}
        return {"found": True, "entry": entry}

    @mcp.tool()
    def applovin_audit_tail(limit: int = 20) -> dict[str, Any]:
        """Show the most recent audit log entries (writes and dry-run rehearsals)."""
        entries = audit.tail(limit)
        return {"count": len(entries), "entries": entries}

    @mcp.tool()
    def applovin_set_dry_run(enabled: bool) -> dict[str, Any]:
        """Turn dry-run on or off for this session. In dry-run, writes are recorded in
        the audit log and returned as a preview but NOT sent to AppLovin; reads still work."""
        client.config.dry_run = enabled
        logger.info("dry_run=%s (set via tool)", enabled)
        return {"dry_run": enabled}

    for group in groups:
        getattr(tools, group).register(mcp, client)

    logger.info(
        "registered %d tools (groups=%s read_only=%s)",
        len(mcp._tool_manager.list_tools()),
        ",".join(config.tool_groups) or "all",
        config.read_only,
    )
    return mcp
