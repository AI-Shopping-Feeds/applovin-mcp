"""MAX (publisher) reporting on r.applovin.com with the MAX Report Key.

Revenue and cohort reports are paged GET requests. The user-level report is a
signed link to a CSV that the server downloads into memory (capped by
APPLOVIN_MAX_DOWNLOAD_MB), summarises and drops; device and user identifiers
are masked unless the caller asks for them.
"""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from typing import Any

from mcp.server.mcpserver import Context

from .. import safety
from ..client import AppLovinClient
from ._common import check_dates, fetch_report, report_query
from ._progress import report, run_blocking

API = "max_reporting"
COHORT_PATHS = {
    "revenue": "/maxCohort",
    "impressions": "/maxCohort/imp",
    "sessions": "/maxCohort/session",
}
COHORT_DAYS = {0, 1, 2, 3, 4, 5, 6, 7, 10, 14, 18, 21, 24, 27, 30, 45}
PLATFORMS = ("android", "fireos", "ios")
# Columns in the user-level CSV that identify a device or person.
IDENTIFIER_COLUMNS = {"idfa", "idfv", "user id", "userid", "user_id", "custom data"}
_DAY_SUFFIX = re.compile(r"_(\d+)$")

REVENUE_COLUMNS_HINT = """\
Columns: day, hour (last 30 days), application, package_name, platform,
store_id, country, device_type, has_idfa, ad_format, max_ad_unit,
max_ad_unit_id, max_ad_unit_test, max_placement, ad_unit_waterfall_name,
network, network_placement, custom_network_name, impressions, estimated_revenue,
ecpm, requests (not with network columns), attempts, responses and fill_rate
(only with network or network_placement, not with max_placement)."""

COHORT_COLUMNS_HINT = """\
Columns by kind (X is one of 0-7, 10, 14, 18, 21, 24, 27, 30, 45 days since install):
  revenue: day, application, package_name, platform, country, installs,
    pub_revenue_X, rpi_X, ads_pub_revenue_X, ads_rpi_X, iap_pub_revenue_X,
    iap_rpi_X, banner_/inter_/mrec_/reward_ + pub_revenue_X or rpi_X
  impressions: ... installs, imp_X, imp_per_user_X, user_count_X,
    banner_/inter_/mrec_/reward_ + imp_X or imp_per_user_X
  sessions: ... installs, daily_usage_X, session_count_X, session_length_X,
    user_count_X, retention_X"""


def _norm(name: str) -> str:
    return name.strip().lower()


def summarise_user_rows(
    rows: list[dict[str, str]],
    *,
    aggregated: bool,
    group_by: list[str],
    include_identifiers: bool,
    max_rows: int,
) -> dict[str, Any]:
    header = list(rows[0].keys()) if rows else []
    by_norm = {_norm(h): h for h in header}
    missing = [g for g in group_by if _norm(g) not in by_norm]
    if rows and missing:
        raise ValueError(f"group_by columns not in the report: {missing}. Columns: {header}")
    revenue_col = by_norm.get("revenue")
    imp_col = by_norm.get("impressions")
    total_revenue = 0.0
    total_impressions = 0
    groups: dict[tuple[str, ...], dict[str, float]] = defaultdict(
        lambda: {"revenue": 0.0, "impressions": 0.0}
    )
    for row in rows:
        rev = safety.num(row.get(revenue_col or "")) or 0.0
        imps = (safety.num(row.get(imp_col or "")) or 0.0) if aggregated else 1.0
        total_revenue += rev
        total_impressions += int(imps)
        key = tuple(row.get(by_norm[_norm(g)], "") for g in group_by)
        groups[key]["revenue"] += rev
        groups[key]["impressions"] += imps
    top = sorted(groups.items(), key=lambda kv: kv[1]["revenue"], reverse=True)[:50]
    masked = [h for h in header if _norm(h) in IDENTIFIER_COLUMNS]
    sample: list[dict[str, str]] = []
    for row in rows[: max(0, max_rows)]:
        if include_identifiers:
            sample.append(dict(row))
        else:
            sample.append({k: ("***" if k in masked and v else v) for k, v in row.items()})
    return {
        "row_count": len(rows),
        "columns": header,
        "total_revenue_usd": round(total_revenue, 6),
        "total_impressions": total_impressions,
        "by_group": [
            {
                **dict(zip(group_by, key, strict=True)),
                "revenue_usd": round(v["revenue"], 6),
                "impressions": int(v["impressions"]),
            }
            for key, v in top
        ],
        "rows": sample,
        "identifiers_masked": [] if include_identifiers else masked,
    }


def register(mcp, client: AppLovinClient) -> None:
    @mcp.tool(
        description=(
            "Run a MAX revenue report (estimated, aggregated mediation statistics in "
            "UTC) and return its rows, paging with progress. Data from the last 1-2 "
            "hours may be incomplete.\n\n"
            "Dates are YYYY-MM-DD within the last 45 days. filters "
            '{"platform": "ios", "max_ad_unit_id": ["abc"]}, sort ["estimated_revenue:DESC"] '
            "(applied in order), not_zero drops all-zero rows.\n\n" + REVENUE_COLUMNS_HINT
        )
    )
    async def applovin_max_revenue_report(
        start: str,
        end: str,
        columns: list[str],
        filters: dict[str, Any] | None = None,
        sort: list[str] | None = None,
        not_zero: bool = False,
        max_rows: int = 1000,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        check_dates(start, end, window_days=45, allow_now=False)
        params = report_query(columns=columns, filters=filters, sort=sort, not_zero=not_zero)
        params[:0] = [("start", start), ("end", end)]
        out = await fetch_report(
            client,
            API,
            "/maxReport",
            params,
            max_rows=max_rows,
            ctx=ctx,
            label="MAX revenue report",
        )
        return {"start": start, "end": end, **out}

    @mcp.tool(
        description=(
            "Run a MAX cohort report: what users who installed on each day went on "
            "to earn (kind='revenue'), see (kind='impressions') or do "
            "(kind='sessions', including retention_X). Install dates are "
            "YYYY-MM-DD within the last 45 days (UTC). Returns rows, paging with "
            "progress.\n\n" + COHORT_COLUMNS_HINT
        )
    )
    async def applovin_max_cohort_report(
        kind: str,
        start: str,
        end: str,
        columns: list[str],
        filters: dict[str, Any] | None = None,
        sort: list[str] | None = None,
        not_zero: bool = False,
        max_rows: int = 1000,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        if kind not in COHORT_PATHS:
            raise ValueError(f"kind must be one of {', '.join(COHORT_PATHS)}.")
        check_dates(start, end, window_days=45, allow_now=False)
        for col in columns:
            m = _DAY_SUFFIX.search(col)
            if m and int(m.group(1)) not in COHORT_DAYS:
                raise ValueError(
                    f"{col}: cohort days must be one of {', '.join(map(str, sorted(COHORT_DAYS)))}."
                )
        params = report_query(columns=columns, filters=filters, sort=sort, not_zero=not_zero)
        params[:0] = [("start", start), ("end", end)]
        out = await fetch_report(
            client,
            API,
            COHORT_PATHS[kind],
            params,
            max_rows=max_rows,
            ctx=ctx,
            label=f"MAX {kind} cohort",
        )
        return {"kind": kind, "start": start, "end": end, **out}

    @mcp.tool()
    async def applovin_max_user_revenue(
        date: str,
        platform: str,
        application: str | None = None,
        store_id: str | None = None,
        aggregated: bool = True,
        group_by: list[str] | None = None,
        include_identifiers: bool = False,
        max_rows: int = 20,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Summarise MAX user-level ad revenue for one app and one UTC day.

        Pass application (package name / bundle ID) or store_id (numeric iTunes ID
        or Android package name), and platform (android, fireos or ios). Data for a
        day is ready 8 hours after it ends (UTC); the last 45 days are available.
        aggregated=true gives one row per user; false one row per impression.

        Returns totals, revenue and impressions by group_by columns (default
        'Ad Unit ID'; e.g. ['Placement'], ['Network', 'Country'] for impression
        rows) and up to max_rows sample rows. IDFA, IDFV, User ID and Custom Data
        are masked unless include_identifiers=true; only ask for them when the
        user needs them, and don't merge them with other personal data unless the
        user confirms their end users consented. Nothing is saved to disk.
        """
        if platform not in PLATFORMS:
            raise ValueError("platform must be android, fireos or ios.")
        if bool(application) == bool(store_id):
            raise ValueError("Pass application or store_id (one of them).")
        check_dates(date, date, window_days=45, allow_now=False)
        params: dict[str, Any] = {"date": date, "platform": platform, "aggregated": aggregated}
        if application:
            params["application"] = application
        else:
            params["store_id"] = store_id
        await report(ctx, 1, None, "Requesting the user-level report link")
        out = await run_blocking(
            client.request, API, "GET", "/max/userAdRevenueReport", params=params
        )
        url = out.get("ad_revenue_report_url") if isinstance(out, dict) else None
        if not url:
            status = out.get("status") if isinstance(out, dict) else None
            raise RuntimeError(
                f"AppLovin returned no report link (status {status}). Data for a day is ready "
                "8 hours after it ends (UTC); check the app identifier and platform."
            )
        await report(ctx, 2, None, "Downloading the report into memory")
        raw = await run_blocking(client.download, str(url))
        text = raw.decode("utf-8-sig", errors="replace")
        rows = list(csv.DictReader(io.StringIO(text)))
        await report(ctx, 3, None, f"Summarising {len(rows)} rows")
        summary = summarise_user_rows(
            rows,
            aggregated=aggregated,
            group_by=group_by or ["Ad Unit ID"],
            include_identifiers=include_identifiers,
            max_rows=max_rows,
        )
        return {"date": date, "platform": platform, "aggregated": aggregated, **summary}
