"""Title and MCP annotations for every applovin_* tool.

`AppLovinMCPServer.tool()` looks each tool up here by name, so the tool modules
stay free of metadata. Every tool must have an entry: tests/test_mcp_client.py
fails if a registered tool is missing or an entry has no tool.

Annotation kinds (all API tools are open-world: they call AppLovin's APIs):

* READ: changes nothing in the account, including reports.
* WRITE: creates something; repeating it creates it again.
* UPSERT: an update or state change; repeating it changes nothing more.
* DESTRUCTIVE: removes creative sets or assets from campaigns, removes a
  waterfall or ends an experiment.
* DESTRUCTIVE_ONCE: the raw request tool, which may do anything.
* LOCAL_*: touches only this server (audit log, session settings).
"""

from __future__ import annotations

from typing import NamedTuple

from mcp.types import ToolAnnotations


def _ann(
    *, read_only: bool, destructive: bool, idempotent: bool, open_world: bool = True
) -> ToolAnnotations:
    return ToolAnnotations(
        read_only_hint=read_only,
        destructive_hint=destructive,
        idempotent_hint=idempotent,
        open_world_hint=open_world,
    )


READ = _ann(read_only=True, destructive=False, idempotent=True)
WRITE = _ann(read_only=False, destructive=False, idempotent=False)
UPSERT = _ann(read_only=False, destructive=False, idempotent=True)
DESTRUCTIVE = _ann(read_only=False, destructive=True, idempotent=True)
DESTRUCTIVE_ONCE = _ann(read_only=False, destructive=True, idempotent=False)
LOCAL_READ = _ann(read_only=True, destructive=False, idempotent=True, open_world=False)
LOCAL_SETTING = _ann(read_only=False, destructive=False, idempotent=True, open_world=False)


class ToolSpec(NamedTuple):
    title: str
    annotations: ToolAnnotations


# Registered regardless of APPLOVIN_TOOL_GROUPS and APPLOVIN_READ_ONLY.
ALWAYS_REGISTERED = frozenset(
    {
        "applovin_describe_server",
        "applovin_health_check",
        "applovin_audit_lookup",
        "applovin_audit_tail",
        "applovin_set_dry_run",
    }
)

_CATALOG: dict[str, tuple[str, ToolAnnotations]] = {
    # server (meta)
    "applovin_describe_server": ("Describe the server configuration", LOCAL_READ),
    "applovin_health_check": ("Check each configured AppLovin key", READ),
    "applovin_audit_lookup": ("Look up an audit log entry", LOCAL_READ),
    "applovin_audit_tail": ("Show recent audit log entries", LOCAL_READ),
    "applovin_set_dry_run": ("Turn dry-run mode on or off", LOCAL_SETTING),
    # ads_campaigns
    "applovin_ads_list_campaigns": ("List Ads Manager campaigns", READ),
    "applovin_ads_create_campaign": ("Create an Ads Manager campaign", WRITE),
    "applovin_ads_update_campaigns": ("Update Ads Manager campaigns", UPSERT),
    "applovin_ads_set_campaign_status": ("Pause or resume Ads Manager campaigns", UPSERT),
    "applovin_ads_campaign_history": ("Show the campaign change history", READ),
    "applovin_ads_get_catalog_info": ("Get catalogs for dynamic web ads", READ),
    # ads_creatives
    "applovin_ads_list_creative_sets": ("List Ads Manager creative sets", READ),
    "applovin_ads_create_creative_set": ("Create a creative set", WRITE),
    "applovin_ads_update_creative_sets": ("Update creative sets", UPSERT),
    "applovin_ads_clone_creative_set": ("Clone a creative set into a campaign", WRITE),
    "applovin_ads_add_creative_sets_to_campaigns": ("Add creative sets to campaigns", UPSERT),
    "applovin_ads_remove_creative_sets": ("Remove creative sets from campaigns", DESTRUCTIVE),
    # ads_assets
    "applovin_ads_list_assets": ("List creative assets", READ),
    "applovin_ads_upload_assets": ("Upload creative assets", WRITE),
    "applovin_ads_get_upload_result": ("Check an asset upload", READ),
    "applovin_ads_add_assets_to_creative_sets": ("Add assets to creative sets", UPSERT),
    "applovin_ads_remove_assets": ("Remove assets from creative sets", DESTRUCTIVE),
    # ads_reporting
    "applovin_ads_report": ("Run an Ads Manager performance report", READ),
    "applovin_ads_asset_report": ("Run an asset performance report", READ),
    "applovin_ads_spend_report": ("Run a US state or metro spend report", READ),
    # max_reporting
    "applovin_max_revenue_report": ("Run a MAX revenue report", READ),
    "applovin_max_cohort_report": ("Run a MAX cohort report", READ),
    "applovin_max_user_revenue": ("Summarise MAX user-level ad revenue", READ),
    # max_ad_units
    "applovin_max_list_ad_units": ("List MAX ad units", READ),
    "applovin_max_get_ad_unit": ("Get a MAX ad unit, waterfall or experiment", READ),
    "applovin_max_create_ad_unit": ("Create a MAX ad unit", WRITE),
    "applovin_max_update_ad_unit": ("Update a MAX ad unit or waterfall", UPSERT),
    "applovin_max_create_waterfall": ("Create a segment waterfall", WRITE),
    "applovin_max_remove_waterfall": ("Remove a segment waterfall", DESTRUCTIVE),
    "applovin_max_create_experiment": ("Start a MAX ad unit experiment", WRITE),
    "applovin_max_end_experiment": ("Promote or discard a MAX experiment", DESTRUCTIVE),
    "applovin_max_list_test_devices": ("List MAX test devices", READ),
    "applovin_max_set_test_device": ("Add or change a MAX test device", UPSERT),
    # max_ad_review
    "applovin_max_list_ad_review_rules": ("List MAX Ad Review rules", READ),
    "applovin_max_create_ad_review_rules": ("Create MAX Ad Review rules", WRITE),
    # request
    "applovin_read_request": ("Read from any AppLovin API endpoint", READ),
    "applovin_request": ("Call any AppLovin API endpoint", DESTRUCTIVE_ONCE),
}

TOOL_SPECS: dict[str, ToolSpec] = {
    name: ToolSpec(title, annotations) for name, (title, annotations) in _CATALOG.items()
}
