"""Ads Manager reporting (r.applovin.com) with the Ads Manager Report Key.

Reports are synchronous GET requests. These tools page through them with
limit/offset, send progress, and return rows held in memory only; rows are
never written to disk or to the audit log.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from mcp.server.mcpserver import Context

from ..client import AppLovinClient
from ._common import check_dates, fetch_report, report_query

API = "ads_reporting"
# Replaced by ad_creative_type, creative_set and placement_type
# (AppLovin's "Deprecation of ad and ad_id" notice for app campaigns).
DEPRECATED_APP_COLUMNS = {"ad", "ad_id"}
ASSET_RANGES = ("yesterday", "last_7d", "last_month")
ATTRIBUTION_MODES = ("click", "click_and_view")

APP_COLUMNS_HINT = """\
Useful columns (app campaigns): day, hour, campaign, campaign_id_external,
campaign_type, country, platform, creative_set, creative_set_id,
ad_creative_type, placement_type, impressions, clicks, ctr, conversions, cost,
average_cpa, sales, roas_7d, ad_roas_7d, total_rev_7d, ret_1d, target_event_count.
Time-span columns end in _0d, _1d, _3d, _7d, _14d, _30d, _90d or _1y."""

WEB_COLUMNS_HINT = """\
Useful columns (web campaigns): day, hour, campaign, campaign_id_external,
country, creative_set, creative_set_id, impressions, clicks, ctr, cost,
average_cpc, sales, sales_7d, roas_7d, chka_usd_7d, cpp_7d, nc_d7_roas,
generate_lead_7d, cost_per_lead_7d. Windows: _0d, _1d, _3d, _7d, _14d, _28d."""


def register(mcp, client: AppLovinClient) -> None:
    @mcp.tool(
        description=(
            "Run an Ads Manager performance report and return its rows, paging with "
            "progress (data is UTC).\n\n"
            "kind: 'app' (/report, last 45 days) or 'web' (/webReport, last 90 days); "
            "default APPLOVIN_ADS_ACCOUNT_TYPE. Dates are YYYY-MM-DD; end may be 'now'. "
            "cohort=true attributes metrics back to the serve day (day_column=day). "
            "attribution_mode (web only): 'click' or 'click_and_view'. filters "
            '{"country": ["US", "GB"]}, exclude (filter_not_), sort ["cost:DESC"], '
            "having e.g. 'impressions > 0 AND cost > 0' (slow), not_zero drops all-zero "
            "rows. The deprecated app columns ad and ad_id are refused; use "
            "ad_creative_type, creative_set and placement_type.\n\n"
            + APP_COLUMNS_HINT
            + "\n"
            + WEB_COLUMNS_HINT
        )
    )
    async def applovin_ads_report(
        start: str,
        end: str,
        columns: list[str],
        kind: str | None = None,
        cohort: bool = False,
        attribution_mode: str = "click",
        filters: dict[str, Any] | None = None,
        exclude: dict[str, Any] | None = None,
        sort: list[str] | None = None,
        having: str | None = None,
        not_zero: bool = False,
        max_rows: int = 1000,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        kind = (kind or client.config.ads_account_type).lower()
        if kind not in ("app", "web"):
            raise ValueError("kind must be app or web.")
        warnings: list[str] = []
        used = {c.strip() for c in columns}
        if kind == "app" and used & DEPRECATED_APP_COLUMNS:
            raise ValueError(
                "AppLovin deprecated the ad and ad_id columns. Use ad_creative_type, "
                "creative_set and placement_type instead."
            )
        if kind == "web" and "ad_id" in used:
            warnings.append(
                "ad_id is being replaced by creative-set columns; prefer creative_set_id."
            )
        check_dates(start, end, window_days=45 if kind == "app" else 90)
        params = report_query(
            columns=columns,
            filters=filters,
            exclude=exclude,
            sort=sort,
            having=having,
            not_zero=not_zero,
        )
        params = [("report_type", "advertiser"), ("start", start), ("end", end), *params]
        if cohort:
            params.append(("day_column", "day"))
        path = "/report"
        if kind == "web":
            if attribution_mode not in ATTRIBUTION_MODES:
                raise ValueError("attribution_mode must be click or click_and_view.")
            params.append(("attribution_mode", attribution_mode))
            path = "/webReport"
        out = await fetch_report(
            client, API, path, params, max_rows=max_rows, ctx=ctx, label="Ads report"
        )
        if warnings:
            out["warnings"] = warnings
        return {"kind": kind, "start": start, "end": end, **out}

    @mcp.tool()
    async def applovin_ads_asset_report(
        columns: list[str],
        range: str | None = None,
        start: str | None = None,
        end: str | None = None,
        filters: dict[str, Any] | None = None,
        exclude: dict[str, Any] | None = None,
        greater_than: dict[str, float] | None = None,
        less_than: dict[str, float] | None = None,
        sort: list[str] | None = None,
        not_zero: bool = False,
        max_rows: int = 1000,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Asset-level performance (impressions, clicks, ctr, cost per asset).

        Pass range ('yesterday', 'last_7d' or 'last_month'), or start and end
        (YYYY-MM-DD, last 45 days). Columns: asset_id, asset_name, asset_url,
        campaign, campaign_id, campaign_package_name, creative_set,
        creative_set_id, impressions, clicks, ctr, cost. filters/exclude work on
        the ID and name columns; greater_than/less_than on metrics, e.g.
        {"impressions": 100}. Yesterday's data is final after 06:00 UTC.
        """
        if bool(range) == bool(start or end):
            raise ValueError("Pass either range, or start and end.")
        if range and range not in ASSET_RANGES:
            raise ValueError(f"range must be one of {', '.join(ASSET_RANGES)}.")
        if filters and "asset_name" in filters:
            # AppLovin expects asset names URL-encoded twice; httpx adds the second pass.
            raw = filters["asset_name"]
            names = raw if isinstance(raw, list | tuple) else [raw]
            filters = {**filters, "asset_name": [quote(str(n), safe="") for n in names]}
        params = report_query(
            columns=columns, filters=filters, exclude=exclude, sort=sort, not_zero=not_zero
        )
        for prefix, mapping in (
            ("filter_greater_than_", greater_than),
            ("filter_less_than_", less_than),
        ):
            for col, value in (mapping or {}).items():
                report_query(columns=[col])  # validates the name
                params.append((f"{prefix}{col}", value))
        if range:
            params.insert(0, ("range", range))
            path = "/assetReport"
        else:
            if not (start and end):
                raise ValueError("Pass both start and end.")
            check_dates(start, end, window_days=45, allow_now=False)
            params[:0] = [("start", start), ("end", end)]
            path = "/assetAnalyticsReport"
        out = await fetch_report(
            client, API, path, params, max_rows=max_rows, ctx=ctx, label="Asset report"
        )
        return {"range": range, "start": start, "end": end, **out}

    @mcp.tool()
    async def applovin_ads_spend_report(
        start: str,
        end: str,
        geo_type: str,
        columns: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        exclude: dict[str, Any] | None = None,
        sort: list[str] | None = None,
        max_rows: int = 1000,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Daily spend in USD by US state or metro area (DMA), per campaign.

        geo_type: 'state' or 'metro'. Dates YYYY-MM-DD within the last 365 days;
        end may be 'now'. Default columns: day, campaign_id_external, campaign,
        state or metro_code + metro_name, cost. AppLovin documents this endpoint on
        a page that isn't in its navigation yet, so it may change.
        """
        if geo_type not in ("state", "metro"):
            raise ValueError("geo_type must be state or metro.")
        check_dates(start, end, window_days=365)
        if not columns:
            geo = ["state"] if geo_type == "state" else ["metro_code", "metro_name"]
            columns = ["day", "campaign_id_external", "campaign", *geo, "cost"]
        params = report_query(columns=columns, filters=filters, exclude=exclude, sort=sort)
        params[:0] = [
            ("report_type", "advertiser"),
            ("start", start),
            ("end", end),
            ("geo_type", geo_type),
        ]
        out = await fetch_report(
            client, API, "/spend-report", params, max_rows=max_rows, ctx=ctx, label="Spend report"
        )
        return {"geo_type": geo_type, "start": start, "end": end, **out}
