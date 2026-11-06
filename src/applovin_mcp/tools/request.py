"""Escape hatch: call any endpoint on AppLovin's APIs that the other tools don't cover.

Only AppLovin's own API hosts can be reached (the target is picked by name, and
the path is checked to stay on that API). The right key, and the Ads Manager
account ID, are added by the server and can't be overridden.
"""

from __future__ import annotations

from typing import Any

from .. import safety
from ..client import AppLovinClient

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
APIS_HELP = (
    "api: 'ads' (Campaign Management, https://api.ads.axon.ai/manage/v1), "
    "'ads_reporting' (https://r.applovin.com with the Ads Manager Report Key), "
    "'max_reporting' (https://r.applovin.com with the MAX Report Key), "
    "'max' (Ad Unit Management, https://o.applovin.com/mediation/v1) or "
    "'ad_review' (https://api-safedk.applovin.com)."
)


def _check_api(client: AppLovinClient, api: str) -> None:
    target = client.target(api)
    if target.side not in client.enabled_sides:
        raise ValueError(
            f"The {target.side} side isn't enabled in APPLOVIN_TOOL_GROUPS, so '{api}' can't "
            "be called here."
        )


def register(mcp, client: AppLovinClient) -> None:
    @mcp.tool(
        description=(
            "Read (GET) any AppLovin API endpoint the other tools don't cover, e.g. "
            "the HTML Metrics API (api='ads_reporting', path='/playableMetrics').\n\n"
            + APIS_HELP
            + " path is relative to that base and starts with '/'. query holds the "
            "parameters; lists are sent comma-separated. The key (and account_id for "
            "'ads') are added for you: never pass them."
        )
    )
    def applovin_read_request(api: str, path: str, query: dict[str, Any] | None = None) -> Any:
        _check_api(client, api)
        return {
            "api": api,
            "path": path,
            "response": client.request(api, "GET", path, params=query),
        }

    @mcp.tool(
        description=(
            "Call any AppLovin API endpoint the other tools don't cover. "
            + APIS_HELP
            + " GET is a read. Every other method is a write: it follows dry-run, is "
            "audited, and needs confirm='APPLY' because this tool can't check what it "
            "changes. body is sent as JSON."
        )
    )
    def applovin_request(
        api: str,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: Any | None = None,
        confirm: str = "",
    ) -> Any:
        _check_api(client, api)
        method = method.upper()
        if method not in METHODS:
            raise ValueError(f"method must be one of {', '.join(METHODS)}.")
        if method == "GET":
            out = client.request(api, "GET", path, params=query)
            return {"api": api, "path": path, "response": out}
        decision = safety.Decision(
            required=safety.APPLY,
            reasons=["raw write through applovin_request; its effect isn't checked"],
        )
        refused = safety.refusal(
            client.config,
            decision,
            confirm,
            preview={"api": api, "method": method, "path": path, "query": query, "body": body},
        )
        if refused:
            return refused
        out = client.request(
            api,
            method,
            path,
            params=query,
            json_body=body,
            op=f"request {api} {method} {path}",
        )
        return {"api": api, "path": path, "method": method, "response": out}
