"""Ads Manager creative sets through the Campaign Management API (whitelist-only)."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context

from .. import safety
from ..client import AppLovinClient
from ._common import check_ids, check_items, check_numeric_ids, chunks
from ._progress import report, run_blocking

API = "ads"
STATUSES = ("LIVE", "PAUSED")
MAX_UPDATES = 50


def fetch_creative_sets(
    client: AppLovinClient, ids: list[str] | None = None, *, max_results: int = 200
) -> tuple[list[Any], bool]:
    if not ids:
        return client.paginate_pages(API, "/creative_set/list", max_results=max_results)
    found: list[Any] = []
    for chunk in chunks(ids, 100):
        page, _ = client.paginate_pages(
            API, "/creative_set/list", {"ids": ",".join(chunk)}, max_results=len(chunk)
        )
        found.extend(page)
    return found[:max_results], len(found) > max_results


def fetch_by_campaign(
    client: AppLovinClient, campaign_ids: list[str], *, max_results: int = 500
) -> tuple[dict[str, list[Any]], int, bool]:
    """Creative sets grouped by campaign ID (merges every page)."""
    merged: dict[str, list[Any]] = {}
    total = 0
    for chunk in chunks(campaign_ids, 100):
        page_no = 1
        while True:
            out = client.request(
                API,
                "GET",
                "/creative_set/list_by_campaign_id",
                params={"ids": ",".join(chunk), "page": page_no, "size": 100},
            )
            campaigns = out.get("campaigns") if isinstance(out, dict) else None
            count = 0
            for cid, sets in (campaigns or {}).items():
                merged.setdefault(str(cid), []).extend(sets or [])
                count += len(sets or [])
            total += count
            if count < 100 or total >= max_results:
                break
            page_no += 1
        if total >= max_results:
            return merged, total, True
    return merged, total, False


def register(mcp, client: AppLovinClient) -> None:
    @mcp.tool()
    def applovin_ads_list_creative_sets(
        ids: list[str] | None = None,
        hashed_ids: list[str] | None = None,
        campaign_ids: list[str] | None = None,
        max_results: int = 200,
    ) -> dict[str, Any]:
        """List creative sets, with their assets, countries and campaign IDs.

        Filter by ids, by hashed_ids (the creative_set_id column in reports), or
        by campaign_ids (returns the sets grouped by campaign). Pass at most one filter.
        """
        if sum(bool(x) for x in (ids, hashed_ids, campaign_ids)) > 1:
            raise ValueError("Pass only one of ids, hashed_ids or campaign_ids.")
        if campaign_ids:
            grouped, total, truncated = fetch_by_campaign(
                client, check_ids(campaign_ids, "campaign_ids"), max_results=max_results
            )
            return {"campaigns": grouped, "creative_set_count": total, "truncated": truncated}
        if hashed_ids:
            items: list[Any] = []
            for chunk in chunks(check_ids(hashed_ids, "hashed_ids"), 100):
                page, _ = client.paginate_pages(
                    API,
                    "/creative_set/list",
                    {"hashed_ids": ",".join(chunk)},
                    max_results=len(chunk),
                )
                items.extend(page)
            return {"creative_sets": items, "count": len(items), "truncated": False}
        items, truncated = fetch_creative_sets(
            client, check_ids(ids, "ids") if ids else None, max_results=max_results
        )
        return {"creative_sets": items, "count": len(items), "truncated": truncated}

    @mcp.tool()
    def applovin_ads_create_creative_set(creative_set: dict[str, Any]) -> dict[str, Any]:
        """Create a creative set. Changes the live account.

        creative_set: type (APP or WEB), name and assets ([{"id": "<asset id>"}]
        from applovin_ads_list_assets; either a HOSTED_HTML asset, or an
        IMG_INTER_P image plus a VID_LONG_P or VID_SHORT_P video), and optionally
        campaign_id, countries, languages, status (APP only), product_page (APP)
        or creative_set_url / brand_info (WEB). Without campaign_id the set is
        standalone; add it to campaigns with applovin_ads_add_creative_sets_to_campaigns.
        """
        if not isinstance(creative_set, dict) or "id" in creative_set:
            raise ValueError("creative_set must be an object without an id.")
        body = dict(creative_set)
        body["type"] = str(body.get("type") or client.config.ads_account_type).upper()
        missing = [f for f in ("name", "assets") if not body.get(f)]
        if missing:
            raise ValueError(f"creative_set also needs: {', '.join(missing)}.")
        return client.request(
            API, "POST", "/creative_set/create", json_body=body, op="create_creative_set"
        )

    @mcp.tool()
    async def applovin_ads_update_creative_sets(
        items: list[dict[str, Any]], confirm: str = "", ctx: Context | None = None
    ) -> dict[str, Any]:
        """Update creative sets, one request each with progress (up to 50).

        items: objects with id plus the fields to change, e.g. {"id": "56789",
        "status": "PAUSED"} or {"id": "56789", "countries": ["US", "CA"]}. Setting
        assets replaces the set's asset list. type is filled in from the set.
        Current values are kept in the audit log. More than
        APPLOVIN_BULK_THRESHOLD sets need confirm='APPLY'. Stops at the first failure.
        """
        check_items(items, "items", MAX_UPDATES)
        for i, item in enumerate(items):
            if not item.get("id"):
                raise ValueError(f"items[{i}] needs the creative set id.")
            if item.get("status") is not None and str(item["status"]).upper() not in STATUSES:
                raise ValueError(f"items[{i}].status must be LIVE or PAUSED.")
        ids = [str(it["id"]) for it in items]
        before = await run_blocking(
            client.snapshot, fetch_creative_sets, client, ids, max_results=len(ids)
        )
        current = {
            str(c.get("id")): c for c in (before[0] if before else []) if isinstance(c, dict)
        }
        bodies = []
        for item in items:
            body = dict(item)
            if "status" in body:
                body["status"] = str(body["status"]).upper()
            body.setdefault(
                "type",
                (current.get(str(item["id"])) or {}).get("type")
                or client.config.ads_account_type.upper(),
            )
            bodies.append(body)
        decision = safety.assess(client.config, count=len(bodies))
        refused = safety.refusal(client.config, decision, confirm, preview=bodies)
        if refused:
            return refused
        results: list[Any] = []
        for done, body in enumerate(bodies, start=1):
            try:
                out = await run_blocking(
                    client.request,
                    API,
                    "POST",
                    "/creative_set/update",
                    json_body=body,
                    op="update_creative_set",
                    before=current.get(str(body["id"])),
                )
            except Exception as e:
                return safety.annotate(
                    {
                        "updated": results,
                        "failed": {"id": body["id"], "reason": str(e)[:500]},
                        "not_sent": [b["id"] for b in bodies[done:]],
                    },
                    decision,
                )
            results.append(out)
            await report(ctx, done, len(bodies), f"Updated creative set {body['id']}")
        return safety.annotate({"updated": results, "count": len(results)}, decision)

    @mcp.tool()
    def applovin_ads_clone_creative_set(
        creative_set_id: str, campaign_id: str, status: str = "PAUSED"
    ) -> dict[str, Any]:
        """Copy a creative set into another campaign. The copy is PAUSED unless you
        pass status='LIVE'. The source set must already belong to a campaign."""
        status = status.upper()
        if status not in STATUSES:
            raise ValueError("status must be LIVE or PAUSED.")
        body = {
            "creative_set_id": check_numeric_ids([creative_set_id], "creative_set_id", 1)[0],
            "campaign_id": check_numeric_ids([campaign_id], "campaign_id", 1)[0],
            "status": status,
        }
        return client.request(
            API, "POST", "/creative_set/clone", json_body=body, op="clone_creative_set"
        )

    @mcp.tool()
    def applovin_ads_add_creative_sets_to_campaigns(
        creative_set_ids: list[str], campaign_ids: list[str], confirm: str = ""
    ) -> dict[str, Any]:
        """Add existing creative sets (up to 50) to campaigns (up to 20). Live
        campaigns start serving them. More than APPLOVIN_BULK_THRESHOLD
        set-campaign pairs need confirm='APPLY'."""
        sets = check_numeric_ids(creative_set_ids, "creative_set_ids", 50)
        camps = check_numeric_ids(campaign_ids, "campaign_ids", 20)
        body = {"campaign_ids": camps, "creative_set_ids": sets}
        decision = safety.assess(client.config, count=len(sets) * len(camps))
        refused = safety.refusal(client.config, decision, confirm, preview=body)
        if refused:
            return refused
        out = client.request(
            API, "POST", "/creative_set/add-to-campaigns", json_body=body, op="add_creative_sets"
        )
        return safety.annotate(out, decision)

    @mcp.tool()
    async def applovin_ads_remove_creative_sets(
        creative_set_ids: list[str],
        campaign_ids: list[str] | None = None,
        confirm: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Take creative sets out of campaigns so they stop serving there. Needs
        confirm='DELETE' after the user agrees.

        With campaign_ids (up to 20), each set is removed from those campaigns;
        without, from every campaign. The sets themselves remain and can be added
        back with applovin_ads_add_creative_sets_to_campaigns; their campaign
        lists are kept in the audit log. To stop a set temporarily, pause it instead.
        """
        sets = check_numeric_ids(creative_set_ids, "creative_set_ids", 50)
        camps = check_numeric_ids(campaign_ids, "campaign_ids", 20) if campaign_ids else []
        decision = safety.assess(
            client.config,
            count=len(sets) * (len(camps) or 1),
            destructive=safety.DELETE,
        )
        preview: dict[str, Any] = {"creative_set_ids": sets, "campaign_ids": camps or "all"}
        refused = safety.refusal(client.config, decision, confirm, preview=preview)
        if refused:
            return refused
        before = await run_blocking(
            client.snapshot,
            fetch_creative_sets,
            client,
            [str(s) for s in sets],
            max_results=len(sets),
        )
        snapshot = before[0] if before else None
        if not camps:
            out = await run_blocking(
                client.request,
                API,
                "POST",
                "/creative_set/remove-from-all-campaigns",
                json_body={"creative_set_ids": sets},
                op="remove_creative_sets_from_all",
                before=snapshot,
            )
            return safety.annotate(out, decision)
        results: list[Any] = []
        for done, sid in enumerate(sets, start=1):
            body = {"campaign_ids": camps, "creative_set_id": sid}
            try:
                out = await run_blocking(
                    client.request,
                    API,
                    "POST",
                    "/creative_set/remove-from-campaigns",
                    json_body=body,
                    op="remove_creative_set_from_campaigns",
                    before=snapshot,
                )
            except Exception as e:
                return safety.annotate(
                    {
                        "removed": results,
                        "failed": {"creative_set_id": sid, "reason": str(e)[:500]},
                        "not_sent": sets[done:],
                    },
                    decision,
                )
            results.append(out)
            await report(ctx, done, len(sets), f"Removed creative set {sid}")
        return safety.annotate({"removed": results, "count": len(results)}, decision)
