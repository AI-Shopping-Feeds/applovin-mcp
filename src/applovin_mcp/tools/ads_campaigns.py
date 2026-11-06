"""Ads Manager campaigns through the Campaign Management API (api.ads.axon.ai/manage/v1).

The API is whitelist-only (limited beta). Without access every call here fails
with a 401/403 whose message says how to request it; reporting tools keep working.

Each call creates or updates one campaign. AppLovin blocks the API for 24 hours
after 100 failed requests in five minutes, so these tools check arguments
locally first, stop a batch at its first failure, and the client pauses
Campaign Management calls if errors pile up.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context

from .. import safety
from ..client import AppLovinClient
from ..config import Config
from ._common import check_dates, check_ids, check_items, chunks
from ._progress import report, run_blocking

API = "ads"
STATUSES = ("LIVE", "PAUSED")
COST_GOALS = {"CPI", "CPE", "CPP"}
ROAS_GOALS = {"AD_ROAS", "CHK_ROAS", "BLD_ROAS", "ROAS"}
# Fields the API rejects on update.
FORBIDDEN_ON_UPDATE = ("bidding_strategy", "platform", "start_date")
FORBIDDEN_GOAL_ON_UPDATE = ("goal_type", "event_target", "roas_day_target")
REQUIRED_ON_CREATE = {
    "APP": (
        "bidding_strategy",
        "budget",
        "goal",
        "name",
        "package_name",
        "platform",
        "start_date",
        "targeting",
        "tracking",
    ),
    "WEB": ("bidding_strategy", "budget", "goal", "name", "start_date", "targeting", "website_url"),
}
MAX_UPDATES = 50


def fetch_campaigns(
    client: AppLovinClient, ids: list[str] | None = None, *, max_results: int = 200
) -> tuple[list[Any], bool]:
    """Campaigns by ID (any number, 100 per call) or every campaign (paged)."""
    if not ids:
        return client.paginate_pages(API, "/campaign/list", max_results=max_results)
    found: list[Any] = []
    for chunk in chunks(ids, 100):
        page, _ = client.paginate_pages(
            API, "/campaign/list", {"ids": ",".join(chunk)}, max_results=len(chunk)
        )
        found.extend(page)
    return found[:max_results], len(found) > max_results


def _money_map(obj: Any, all_key: str, per_country_key: str) -> dict[str, float] | None:
    if not isinstance(obj, dict):
        return None
    out: dict[str, float] = {}
    value = safety.num(obj.get(all_key))
    if value is not None:
        out["*"] = value
    for cc, raw in (obj.get(per_country_key) or {}).items():
        n = safety.num(raw)
        if n is not None:
            out[str(cc).upper()] = n
    return out


def campaign_money_changes(
    item: dict[str, Any], current: dict[str, Any] | None, config: Config
) -> list[safety.MoneyChange]:
    """Budget and goal values an item sets, paired with the campaign's current ones.

    A budget or cost goal (CPI/CPE/CPP) rising is risky; a ROAS goal falling is
    risky (a lower target lets AppLovin spend more). Switching between a global
    value and per-country values counts as 'current value unknown'.
    """
    cid = str(item.get("id") or "new")
    out: list[safety.MoneyChange] = []
    new_budget = (
        _money_map(
            item.get("budget"), "daily_budget_for_all_countries", "country_code_to_daily_budget"
        )
        or {}
    )
    old_budget = (
        _money_map(
            (current or {}).get("budget"),
            "daily_budget_for_all_countries",
            "country_code_to_daily_budget",
        )
        if current
        else None
    )
    for key, new in new_budget.items():
        old = old_budget.get(key) if old_budget is not None else None
        label = cid if key == "*" else f"{cid}:{key}"
        out.append(
            safety.MoneyChange(label, "daily budget", old, new, True, config.max_daily_budget)
        )
    goal_raw = item.get("goal")
    goal: dict[str, Any] = goal_raw if isinstance(goal_raw, dict) else {}
    new_goal = _money_map(goal, "goal_value_for_all_countries", "country_code_to_goal_value") or {}
    current_goal = (current or {}).get("goal") or {}
    goal_type = str(goal.get("goal_type") or current_goal.get("goal_type") or "").upper()
    old_goal = (
        _money_map(current_goal, "goal_value_for_all_countries", "country_code_to_goal_value")
        if current
        else None
    )
    for key, new in new_goal.items():
        old = old_goal.get(key) if old_goal is not None else None
        label = cid if key == "*" else f"{cid}:{key}"
        if goal_type in ROAS_GOALS:
            out.append(safety.MoneyChange(label, f"{goal_type} goal", old, new, False))
        elif goal_type in COST_GOALS:
            out.append(safety.MoneyChange(label, f"{goal_type} goal", old, new, True))
        else:
            out.append(safety.MoneyChange(label, "goal (type unknown)", None, new, True))
    return out


def check_targeting(body: dict[str, Any], where: str) -> None:
    """Refuse msa_names, which AppLovin renamed to metro_names and will retire."""
    targeting = body.get("targeting")
    if isinstance(targeting, list) and any(
        isinstance(t, dict) and "msa_names" in t for t in targeting
    ):
        raise ValueError(f"{where}: use metro_names instead of msa_names (being retired).")


def _check_update_item(item: dict[str, Any], index: int) -> None:
    if not item.get("id"):
        raise ValueError(f"items[{index}] needs the campaign id.")
    bad = [f for f in FORBIDDEN_ON_UPDATE if f in item]
    goal = item.get("goal")
    if isinstance(goal, dict):
        bad += [f"goal.{f}" for f in FORBIDDEN_GOAL_ON_UPDATE if f in goal]
    if bad:
        raise ValueError(
            f"items[{index}] sets {', '.join(bad)}, which AppLovin only accepts when a campaign "
            "is created. Leave them out (create a new campaign to change them)."
        )
    check_targeting(item, f"items[{index}]")
    status = item.get("status")
    if status is not None and str(status).upper() not in STATUSES:
        raise ValueError(f"items[{index}].status must be LIVE or PAUSED.")


async def apply_campaign_updates(
    client: AppLovinClient,
    items: list[dict[str, Any]],
    confirm: str,
    ctx: Context | None,
    op: str,
) -> dict[str, Any]:
    for i, item in enumerate(items):
        _check_update_item(item, i)
    ids = [str(it["id"]) for it in items]
    before = await run_blocking(client.snapshot, fetch_campaigns, client, ids, max_results=len(ids))
    current = {str(c.get("id")): c for c in (before[0] if before else []) if isinstance(c, dict)}
    bodies: list[dict[str, Any]] = []
    changes: list[safety.MoneyChange] = []
    for item in items:
        cur = current.get(str(item["id"]))
        body = dict(item)
        if "status" in body:
            body["status"] = str(body["status"]).upper()
        body.setdefault("type", (cur or {}).get("type") or client.config.ads_account_type.upper())
        bodies.append(body)
        changes.extend(campaign_money_changes(body, cur, client.config))
    missing = [i for i in ids if i not in current] if before else []
    extra = (
        [f"campaign(s) {', '.join(missing[:10])} weren't found by /campaign/list"]
        if missing
        else []
    )
    decision = safety.assess(client.config, count=len(items), changes=changes, extra_reasons=extra)
    refused = safety.refusal(client.config, decision, confirm, preview=bodies)
    if refused:
        return refused
    results: list[Any] = []
    total = len(bodies)
    for done, body in enumerate(bodies, start=1):
        try:
            out = await run_blocking(
                client.request,
                API,
                "POST",
                "/campaign/update",
                json_body=body,
                op=op,
                before=current.get(str(body["id"])),
            )
        except Exception as e:
            return safety.annotate(
                {
                    "updated": results,
                    "failed": {"id": body["id"], "reason": str(e)[:500]},
                    "not_sent": [b["id"] for b in bodies[done:]],
                    "message": "Stopped at the first failure to protect AppLovin's error budget.",
                },
                decision,
            )
        results.append(out)
        await report(ctx, done, total, f"Updated campaign {body['id']} ({done} of {total})")
    return safety.annotate({"updated": results, "count": len(results)}, decision)


def register(mcp, client: AppLovinClient) -> None:
    @mcp.tool()
    def applovin_ads_list_campaigns(
        ids: list[str] | None = None,
        hashed_ids: list[str] | None = None,
        statuses: list[str] | None = None,
        name_contains: str | None = None,
        max_results: int = 200,
    ) -> dict[str, Any]:
        """List Ads Manager campaigns (archived campaigns aren't returned).

        ids filters by campaign ID; hashed_ids by external ID (the
        campaign_id_external column in reports). statuses (LIVE, PAUSED) and
        name_contains filter the results locally. Budget and goal values are
        strings of US dollars or ROAS fractions (1 = 100%).
        """
        if ids and hashed_ids:
            raise ValueError("Pass ids or hashed_ids, not both.")
        if hashed_ids:
            hashed = check_ids(hashed_ids, "hashed_ids")
            items: list[Any] = []
            for chunk in chunks(hashed, 100):
                page, _ = client.paginate_pages(
                    API, "/campaign/list", {"hashed_ids": ",".join(chunk)}, max_results=len(chunk)
                )
                items.extend(page)
            truncated = False
        else:
            items, truncated = fetch_campaigns(
                client, check_ids(ids, "ids") if ids else None, max_results=max_results
            )
        wanted = {s.upper() for s in statuses or []}
        if wanted:
            items = [c for c in items if str(c.get("status", "")).upper() in wanted]
        if name_contains:
            needle = name_contains.lower()
            items = [c for c in items if needle in str(c.get("name", "")).lower()]
        return {"campaigns": items, "count": len(items), "truncated": truncated}

    @mcp.tool()
    def applovin_ads_create_campaign(
        campaign: dict[str, Any], start_paused: bool = True, confirm: str = ""
    ) -> dict[str, Any]:
        """Create one Ads Manager campaign. Changes the live account and can spend money.

        campaign: the Campaign object as AppLovin documents it. type is APP or WEB.
        APP needs bidding_strategy, budget, goal, name, package_name, platform,
        start_date (today or later), targeting, tracking (and itunes_id on iOS);
        WEB needs bidding_strategy, budget, goal, name, start_date, targeting,
        website_url. Example budget {"daily_budget_for_all_countries": "500"};
        goal {"goal_type": "CHK_ROAS", "roas_day_target": "DAY7",
        "goal_value_for_all_countries": "0.5"}; targeting [{"country_code": "US"}].
        Goal type, bidding strategy, platform and start date can't be changed later.

        AppLovin ignores status on create, so with start_paused (default) the
        server pauses the new campaign straight away; turn it on later with
        applovin_ads_set_campaign_status. Budgets over APPLOVIN_MAX_DAILY_BUDGET
        need confirm='APPLY'. Rehearse with dry-run first.
        """
        if not isinstance(campaign, dict):
            raise ValueError("campaign must be an object.")
        ctype = str(campaign.get("type") or client.config.ads_account_type).upper()
        if ctype not in REQUIRED_ON_CREATE:
            raise ValueError("campaign.type must be APP or WEB.")
        body = {**campaign, "type": ctype}
        missing = [f for f in REQUIRED_ON_CREATE[ctype] if not body.get(f)]
        if (
            ctype == "APP"
            and str(body.get("platform", "")).lower() == "ios"
            and not body.get("itunes_id")
        ):
            missing.append("itunes_id")
        if missing:
            raise ValueError(f"A {ctype} campaign also needs: {', '.join(missing)}.")
        if "id" in body:
            raise ValueError("Don't pass id when creating a campaign.")
        check_targeting(body, "campaign")
        decision = safety.assess(
            client.config,
            count=1,
            changes=campaign_money_changes(body, None, client.config),
            creating=True,
        )
        refused = safety.refusal(client.config, decision, confirm, preview=body)
        if refused:
            return refused
        created = client.request(
            API, "POST", "/campaign/create", json_body=body, op="create_campaign"
        )
        out: dict[str, Any] = {"created": created}
        new_id = created.get("id") if isinstance(created, dict) else None
        if start_paused and new_id is None and not client.config.dry_run:
            out["warning"] = (
                "AppLovin returned no campaign id, so the server couldn't pause the new "
                "campaign. Find it with applovin_ads_list_campaigns and pause it if needed."
            )
        elif start_paused:
            pause = {"id": new_id or "<new campaign id>", "type": ctype, "status": "PAUSED"}
            try:
                out["paused"] = client.request(
                    API, "POST", "/campaign/update", json_body=pause, op="pause_new_campaign"
                )
            except Exception as e:
                out["pause_failed"] = str(e)[:500]
                out["warning"] = (
                    "The campaign was created but couldn't be paused. Pause it in Ads Manager "
                    "or with applovin_ads_set_campaign_status now if it shouldn't run."
                )
        return safety.annotate(out, decision)

    @mcp.tool()
    async def applovin_ads_update_campaigns(
        items: list[dict[str, Any]], confirm: str = "", ctx: Context | None = None
    ) -> dict[str, Any]:
        """Update Ads Manager campaigns, one request each with progress (up to 50).

        items: objects with id plus only the fields to change, e.g.
        {"id": "123", "budget": {"daily_budget_for_all_countries": "800"}},
        {"id": "123", "goal": {"goal_value_for_all_countries": "0.6"}},
        {"id": "123", "targeting": [{"country_code": "US"}, {"country_code": "CA"}]},
        {"id": "123", "end_date": "2026-12-31T00:00:00"}. type is filled in from the
        campaign. bidding_strategy, platform, start_date and the goal's
        goal_type/event_target/roas_day_target can't be changed.

        Current values are read first and kept in the audit log. Budget or
        CPI/CPE/CPP goal rises, and ROAS goal cuts, of more than
        APPLOVIN_MAX_INCREASE_PCT (or where the current value can't be read), and
        more than APPLOVIN_BULK_THRESHOLD campaigns, need confirm='APPLY'. A batch
        stops at its first failure.
        """
        check_items(items, "items", MAX_UPDATES)
        return await apply_campaign_updates(client, items, confirm, ctx, "update_campaign")

    @mcp.tool()
    async def applovin_ads_set_campaign_status(
        ids: list[str], status: str, confirm: str = "", ctx: Context | None = None
    ) -> dict[str, Any]:
        """Pause (PAUSED) or resume (LIVE) Ads Manager campaigns, up to 50.

        Resumed campaigns start spending again. More than
        APPLOVIN_BULK_THRESHOLD campaigns need confirm='APPLY'.
        """
        status = status.upper()
        if status not in STATUSES:
            raise ValueError("status must be LIVE or PAUSED.")
        id_list = check_ids(ids, "ids", MAX_UPDATES)
        items = [{"id": i, "status": status} for i in id_list]
        return await apply_campaign_updates(client, items, confirm, ctx, f"set_status_{status}")

    @mcp.tool()
    def applovin_ads_campaign_history(
        campaign_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        max_results: int = 200,
    ) -> dict[str, Any]:
        """Show who changed which campaigns and how (creates, updates, status changes,
        archives), newest first, with before/after values for updates.

        Dates are YYYY-MM-DD (UTC) within the last 30 days; pass both or neither
        (default: the last 7 days). campaign_id narrows it to one campaign.
        Archived campaigns still appear here.
        """
        params: dict[str, Any] = {}
        if campaign_id:
            params["id"] = check_ids([campaign_id], "campaign_id", 1)[0]
        if bool(start_date) != bool(end_date):
            raise ValueError("Pass both start_date and end_date, or neither.")
        if start_date and end_date:
            check_dates(start_date, end_date, window_days=29, allow_now=False)
            params.update(start_date=start_date, end_date=end_date)
        items, truncated = client.paginate_pages(
            API, "/operation_logs/campaign", params, max_results=max_results
        )
        return {"operations": items, "count": len(items), "truncated": truncated}

    @mcp.tool()
    def applovin_ads_get_catalog_info() -> dict[str, Any]:
        """List the product catalogs and variant sets available for dynamic ads
        (web and e-commerce accounts only). Use the IDs as catalog_id and
        variant_set_id on a WEB campaign."""
        return {"catalog_info": client.request(API, "GET", "/campaign/catalog_info")}
