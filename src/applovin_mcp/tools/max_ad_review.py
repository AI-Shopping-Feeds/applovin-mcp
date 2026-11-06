"""MAX Ad Review competitor and risky-content rules (api-safedk.applovin.com).

Authenticated with the Ad Review Key in an Api-Key header.
"""

from __future__ import annotations

from typing import Any

from .. import safety
from ..client import AppLovinClient
from ._common import check_items

API = "ad_review"
KINDS = {"competitors": "/v1/rules/competitors", "risky": "/v1/rules/risky"}


def register(mcp, client: AppLovinClient) -> None:
    @mcp.tool()
    def applovin_max_list_ad_review_rules(kind: str = "competitors") -> Any:
        """List MAX Ad Review rules, active and paused. kind: 'competitors'
        (block or flag ads by title, store category or advertiser) or 'risky'
        (risky content rules)."""
        if kind not in KINDS:
            raise ValueError("kind must be competitors or risky.")
        return client.request(API, "GET", KINDS[kind])

    @mcp.tool()
    def applovin_max_create_ad_review_rules(
        kind: str, rules: list[dict[str, Any]], confirm: str = ""
    ) -> Any:
        """Create MAX Ad Review rules in one request (also re-activates matching
        paused rules).

        Competitor rules look like {"name": "android title rule", "platform":
        "android", "package_names": ["com.my.app"] (empty = all apps on that
        platform), "rule_type": "title|store_category|advertiser", "value":
        ["com.rival.game"]}; names are up to 35 characters. The response lists
        updated_rules, existing_rules and invalid_rules. More than
        APPLOVIN_BULK_THRESHOLD rules need confirm='APPLY'.
        """
        if kind not in KINDS:
            raise ValueError("kind must be competitors or risky.")
        check_items(rules, "rules", 500)
        for i, rule in enumerate(rules):
            if len(str(rule.get("name", ""))) > 35:
                raise ValueError(f"rules[{i}].name is over 35 characters.")
        body = {"rules": rules}
        decision = safety.assess(client.config, count=len(rules))
        refused = safety.refusal(client.config, decision, confirm, preview=body)
        if refused:
            return refused
        out = client.request(API, "POST", KINDS[kind], json_body=body, op=f"create_{kind}_rules")
        return safety.annotate(out, decision)
