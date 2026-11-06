"""Tool behaviour through mcp.Client with mocked AppLovin APIs."""

from __future__ import annotations

import dataclasses
import json
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import pytest
import respx
from mcp import Client

from applovin_mcp import server
from applovin_mcp.config import Config
from applovin_mcp.tools._common import today_utc

from .conftest import ADS, MAX, MAX_REPORT_KEY, REVIEW, R, audit_entries, payload

CAMPAIGN = {
    "id": "101",
    "name": "UA US",
    "status": "LIVE",
    "type": "APP",
    "budget": {"daily_budget_for_all_countries": "100", "country_code_to_daily_budget": {}},
    "goal": {"goal_type": "CPI", "goal_value_for_all_countries": "2.0"},
}


def _day(days_ago: int) -> str:
    return (today_utc() - timedelta(days=days_ago)).isoformat()


async def _call(config: Config, tool: str, args: dict[str, Any]) -> Any:
    async with Client(server.build_server(config)) as c:
        result = await c.call_tool(tool, args)
    if result.is_error:
        raise AssertionError(" ".join(getattr(b, "text", "") for b in result.content))
    return payload(result)


async def _error(config: Config, tool: str, args: dict[str, Any]) -> str:
    async with Client(server.build_server(config)) as c:
        result = await c.call_tool(tool, args)
    assert result.is_error
    return " ".join(getattr(b, "text", "") for b in result.content)


def _q(call: Any) -> dict[str, list[str]]:
    return parse_qs(urlparse(str(call.request.url)).query)


# ---------------------------------------------------------------- campaigns


@respx.mock
async def test_budget_rise_is_refused_then_applied(config: Config, tmp_audit_log) -> None:
    respx.get(f"{ADS}/campaign/list").mock(return_value=httpx.Response(200, json=[CAMPAIGN]))
    update = respx.post(f"{ADS}/campaign/update").mock(
        return_value=httpx.Response(200, json={"id": "101"})
    )
    items = [{"id": "101", "budget": {"daily_budget_for_all_countries": "150"}}]
    refused = await _call(config, "applovin_ads_update_campaigns", {"items": items})
    assert refused["refused"] is True
    assert refused["required_confirm"] == "APPLY"
    assert refused["money_changes"][0]["change_pct"] == 50.0
    assert not update.called
    done = await _call(
        config, "applovin_ads_update_campaigns", {"items": items, "confirm": "APPLY"}
    )
    assert done["count"] == 1
    sent = json.loads(update.calls.last.request.content)
    assert sent == {
        "id": "101",
        "budget": {"daily_budget_for_all_countries": "150"},
        "type": "APP",
    }
    [entry] = audit_entries(tmp_audit_log)
    assert entry["before"]["budget"]["daily_budget_for_all_countries"] == "100"


async def test_forbidden_update_fields_never_reach_appLovin(config: Config) -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.route(host="api.ads.axon.ai")
        text = await _error(
            config,
            "applovin_ads_update_campaigns",
            {"items": [{"id": "101", "goal": {"goal_type": "CPP"}}]},
        )
    assert "goal.goal_type" in text
    assert not route.called


@respx.mock
async def test_create_campaign_is_paused_straight_away(config: Config) -> None:
    create = respx.post(f"{ADS}/campaign/create").mock(
        return_value=httpx.Response(200, json={"id": "555"})
    )
    update = respx.post(f"{ADS}/campaign/update").mock(
        return_value=httpx.Response(200, json={"id": "555"})
    )
    campaign = {
        "type": "WEB",
        "name": "Spring sale",
        "bidding_strategy": "auto_bidding_with_cpm_billing",
        "budget": {"daily_budget_for_all_countries": "200"},
        "goal": {"goal_type": "ROAS", "goal_value_for_all_countries": "1.2"},
        "start_date": "2026-12-01T00:00:00",
        "targeting": [{"country_code": "US"}],
        "website_url": "https://shop.example/?utm_source=applovin",
    }
    out = await _call(config, "applovin_ads_create_campaign", {"campaign": campaign})
    assert create.called
    assert json.loads(update.calls.last.request.content) == {
        "id": "555",
        "type": "WEB",
        "status": "PAUSED",
    }
    assert out["created"]["id"] == "555"


async def test_retired_targeting_name_is_refused(config: Config) -> None:
    text = await _error(
        config,
        "applovin_ads_update_campaigns",
        {"items": [{"id": "1", "targeting": [{"country_code": "US", "msa_names": ["AUSTIN"]}]}]},
    )
    assert "metro_names" in text


async def test_create_campaign_lists_missing_fields(config: Config) -> None:
    text = await _error(
        config, "applovin_ads_create_campaign", {"campaign": {"type": "APP", "name": "x"}}
    )
    assert "package_name" in text and "tracking" in text


@respx.mock
async def test_status_batch_stops_at_first_failure(config: Config) -> None:
    respx.get(f"{ADS}/campaign/list").mock(
        return_value=httpx.Response(200, json=[{**CAMPAIGN, "id": str(i)} for i in (1, 2, 3)])
    )
    respx.post(f"{ADS}/campaign/update").mock(
        side_effect=[httpx.Response(200, json={"id": "1"}), httpx.Response(400)]
    )
    out = await _call(
        config, "applovin_ads_set_campaign_status", {"ids": ["1", "2", "3"], "status": "paused"}
    )
    assert len(out["updated"]) == 1
    assert out["failed"]["id"] == "2"
    assert out["not_sent"] == ["3"]


@respx.mock
async def test_campaign_history_window(config: Config) -> None:
    route = respx.get(f"{ADS}/operation_logs/campaign").mock(
        return_value=httpx.Response(200, json=[{"operation_type": "UPDATE_CAMPAIGN"}])
    )
    out = await _call(
        config,
        "applovin_ads_campaign_history",
        {"campaign_id": "101", "start_date": _day(5), "end_date": _day(0)},
    )
    assert out["count"] == 1
    assert _q(route.calls.last)["id"] == ["101"]
    text = await _error(
        config, "applovin_ads_campaign_history", {"start_date": _day(40), "end_date": _day(0)}
    )
    assert "last 29 days" in text


# ---------------------------------------------------------------- creatives and assets


@respx.mock
async def test_removing_creative_sets_needs_delete(config: Config) -> None:
    respx.get(f"{ADS}/creative_set/list").mock(return_value=httpx.Response(200, json=[]))
    remove = respx.post(f"{ADS}/creative_set/remove-from-all-campaigns").mock(
        return_value=httpx.Response(200)
    )
    refused = await _call(config, "applovin_ads_remove_creative_sets", {"creative_set_ids": ["9"]})
    assert refused["required_confirm"] == "DELETE"
    assert not remove.called
    await _call(
        config,
        "applovin_ads_remove_creative_sets",
        {"creative_set_ids": ["9"], "confirm": "DELETE"},
    )
    assert json.loads(remove.calls.last.request.content) == {"creative_set_ids": [9]}


async def test_uploads_stay_inside_the_upload_folder(config: Config, tmp_path) -> None:
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"x")
    text = await _error(config, "applovin_ads_upload_assets", {"paths": [str(outside)]})
    assert "outside APPLOVIN_UPLOAD_DIR" in text
    no_dir = dataclasses.replace(config, upload_dir=None)
    assert "Uploads are off" in await _error(
        no_dir, "applovin_ads_upload_assets", {"paths": ["a.mp4"]}
    )
    assert config.upload_dir is not None
    (config.upload_dir / "notes.txt").write_text("x")
    assert "accepts" in await _error(config, "applovin_ads_upload_assets", {"paths": ["notes.txt"]})


@respx.mock
async def test_upload_streams_files_and_waits(config: Config) -> None:
    assert config.upload_dir is not None
    (config.upload_dir / "hero.mp4").write_bytes(b"\x00\x01video")
    upload = respx.post(f"{ADS}/asset/upload").mock(
        return_value=httpx.Response(200, json={"upload_id": "u1"})
    )
    respx.get(f"{ADS}/asset/upload_result").mock(
        side_effect=[
            httpx.Response(200, json={"upload_status": "PENDING", "summary": {"pending": 1}}),
            httpx.Response(200, json={"upload_status": "FINISHED", "summary": {"success": 1}}),
        ]
    )
    out = await _call(
        config, "applovin_ads_upload_assets", {"paths": ["hero.mp4"], "poll_seconds": 0}
    )
    assert out["result"]["upload_status"] == "FINISHED"
    body = upload.calls.last.request.content
    assert b'filename="hero.mp4"' in body
    assert b"video/mp4" in body


# ---------------------------------------------------------------- reporting


@respx.mock
async def test_ads_report_pages_with_limit_and_offset(config: Config) -> None:
    small = dataclasses.replace(config, report_page_size=2)
    route = respx.get(f"{R}/report").mock(
        side_effect=[
            httpx.Response(200, json={"code": 200, "results": [{"day": "a"}, {"day": "b"}]}),
            httpx.Response(200, json={"code": 200, "results": [{"day": "c"}]}),
        ]
    )
    out = await _call(
        small,
        "applovin_ads_report",
        {
            "start": _day(7),
            "end": "now",
            "columns": ["day", "campaign", "cost"],
            "filters": {"country": ["US", "GB"]},
            "sort": ["cost:DESC"],
            "cohort": True,
        },
    )
    assert [r["day"] for r in out["rows"]] == ["a", "b", "c"]
    assert out["truncated"] is False
    first, second = (_q(c) for c in route.calls)
    assert first["report_type"] == ["advertiser"]
    assert first["filter_country"] == ["US,GB"]
    assert first["sort_cost"] == ["DESC"]
    assert first["day_column"] == ["day"]
    assert (first["offset"], second["offset"]) == (["0"], ["2"])


async def test_ads_report_refuses_deprecated_and_old(config: Config) -> None:
    text = await _error(
        config, "applovin_ads_report", {"start": _day(3), "end": _day(1), "columns": ["day", "ad"]}
    )
    assert "ad_creative_type" in text
    text = await _error(
        config, "applovin_ads_report", {"start": _day(60), "end": _day(1), "columns": ["day"]}
    )
    assert "last 45 days" in text


@respx.mock
async def test_web_report_uses_web_endpoint(config: Config) -> None:
    route = respx.get(f"{R}/webReport").mock(return_value=httpx.Response(200, json={"results": []}))
    await _call(
        config,
        "applovin_ads_report",
        {
            "kind": "web",
            "start": _day(80),
            "end": _day(1),
            "columns": ["day", "cost"],
            "attribution_mode": "click_and_view",
        },
    )
    assert _q(route.calls.last)["attribution_mode"] == ["click_and_view"]


@respx.mock
async def test_asset_names_are_double_encoded(config: Config) -> None:
    route = respx.get(f"{R}/assetReport").mock(return_value=httpx.Response(200, json=[]))
    await _call(
        config,
        "applovin_ads_asset_report",
        {
            "columns": ["asset_id", "cost"],
            "range": "yesterday",
            "filters": {"asset_name": ["html+3"]},
        },
    )
    raw = str(route.calls.last.request.url)
    assert "filter_asset_name=html%252B3" in raw


@respx.mock
async def test_max_revenue_report(config: Config) -> None:
    route = respx.get(f"{R}/maxReport").mock(
        return_value=httpx.Response(200, json={"code": 200, "results": [{"impressions": "5"}]})
    )
    out = await _call(
        config,
        "applovin_max_revenue_report",
        {"start": _day(2), "end": _day(1), "columns": ["day", "impressions"]},
    )
    assert out["row_count"] == 1
    assert _q(route.calls.last)["api_key"] == [MAX_REPORT_KEY]


async def test_cohort_days_are_checked(config: Config) -> None:
    text = await _error(
        config,
        "applovin_max_cohort_report",
        {"kind": "revenue", "start": _day(9), "end": _day(8), "columns": ["day", "pub_revenue_8"]},
    )
    assert "cohort days" in text


@respx.mock
async def test_user_revenue_masks_identifiers(config: Config) -> None:
    signed = "https://applovin-externalreports.s3.amazonaws.com/user.csv?X-Amz-Signature=abc"
    respx.get(f"{R}/max/userAdRevenueReport").mock(
        return_value=httpx.Response(200, json={"status": 200, "ad_revenue_report_url": signed})
    )
    csv_text = (
        "Ad Unit ID,Placement,IDFA,IDFV,User Id,Impressions,Revenue\n"
        "u1,home,AAAA-1,BBBB-1,user-1,3,0.5\n"
        "u1,level,AAAA-2,BBBB-2,,2,0.25\n"
        "u2,home,AAAA-3,BBBB-3,user-3,1,1.0\n"
    )
    download = respx.get(signed).mock(return_value=httpx.Response(200, text=csv_text))
    out = await _call(
        config,
        "applovin_max_user_revenue",
        {"date": _day(2), "platform": "ios", "application": "com.example.game"},
    )
    assert out["total_revenue_usd"] == 1.75
    assert out["total_impressions"] == 6
    assert out["by_group"][0] == {"Ad Unit ID": "u2", "revenue_usd": 1.0, "impressions": 1}
    assert {r["IDFA"] for r in out["rows"]} == {"***"}
    assert "AAAA" not in json.dumps(out)
    assert "X-Amz-Signature" not in json.dumps(out)
    assert "api_key" not in str(download.calls.last.request.url)


# ---------------------------------------------------------------- MAX ad units

AD_UNIT = {
    "id": "abcdef1234567890",
    "name": "Rewarded",
    "platform": "ios",
    "ad_format": "REWARD",
    "package_name": "com.example.game",
    "bid_floors": [{"country_group_name": "t1", "cpm": "10.00", "countries": {}}],
    "ad_network_settings": [],
}


@respx.mock
async def test_floor_rise_needs_apply_and_fills_core_fields(config: Config) -> None:
    respx.get(f"{MAX}/ad_unit/abcdef1234567890").mock(
        return_value=httpx.Response(200, json=AD_UNIT)
    )
    post = respx.post(f"{MAX}/ad_unit/abcdef1234567890").mock(
        return_value=httpx.Response(200, json=AD_UNIT)
    )
    floors = [
        {
            "country_group_name": "t1",
            "cpm": "14.00",
            "countries": {"type": "INCLUDE", "values": ["us"]},
        }
    ]
    args = {"ad_unit_id": "abcdef1234567890", "changes": {"bid_floors": floors}}
    refused = await _call(config, "applovin_max_update_ad_unit", args)
    assert refused["required_confirm"] == "APPLY"
    assert not post.called
    await _call(config, "applovin_max_update_ad_unit", {**args, "confirm": "APPLY"})
    sent = json.loads(post.calls.last.request.content)
    assert sent["name"] == "Rewarded" and sent["ad_format"] == "REWARD"
    assert sent["bid_floors"] == floors


async def test_update_ad_unit_points_to_dedicated_tools(config: Config) -> None:
    text = await _error(
        config,
        "applovin_max_update_ad_unit",
        {"ad_unit_id": "abcdef1234567890", "changes": {"promote": True}},
    )
    assert "applovin_max_end_experiment" in text


@respx.mock
async def test_end_experiment_words(config: Config) -> None:
    respx.get(f"{MAX}/ad_unit_experiment/abcdef1234567890").mock(
        return_value=httpx.Response(200, json={"id": "abcdef1234567890", "experiment_name": "t"})
    )
    respx.get(f"{MAX}/ad_unit/abcdef1234567890").mock(
        return_value=httpx.Response(200, json=AD_UNIT)
    )
    post = respx.post(f"{MAX}/ad_unit_experiment/abcdef1234567890").mock(
        return_value=httpx.Response(200, json={"message": "ok"})
    )
    promote = await _call(
        config,
        "applovin_max_end_experiment",
        {"ad_unit_id": "abcdef1234567890", "action": "promote"},
    )
    assert promote["required_confirm"] == "APPLY"
    discard = await _call(
        config,
        "applovin_max_end_experiment",
        {"ad_unit_id": "abcdef1234567890", "action": "deprecate"},
    )
    assert discard["required_confirm"] == "DELETE"
    await _call(
        config,
        "applovin_max_end_experiment",
        {"ad_unit_id": "abcdef1234567890", "action": "deprecate", "confirm": "DELETE"},
    )
    assert json.loads(post.calls.last.request.content) == {
        "id": "abcdef1234567890",
        "experiment_name": "t",
        "promote": False,
        "deprecate": True,
    }


@respx.mock
async def test_ad_review_rules_bulk_guard(config: Config) -> None:
    route = respx.post(f"{REVIEW}/v1/rules/competitors").mock(
        return_value=httpx.Response(200, json={"updated_rules": {"total": 11}})
    )
    rules = [
        {"name": f"r{i}", "platform": "ios", "rule_type": "title", "value": ["x"]}
        for i in range(11)
    ]
    refused = await _call(
        config, "applovin_max_create_ad_review_rules", {"kind": "competitors", "rules": rules}
    )
    assert refused["required_confirm"] == "APPLY"
    assert not route.called


# ---------------------------------------------------------------- escape hatch


@respx.mock
async def test_raw_writes_need_apply_and_reads_pass(config: Config) -> None:
    get = respx.get(f"{R}/playableMetrics").mock(return_value=httpx.Response(200, json=[]))
    post = respx.post(f"{MAX}/ad_unit").mock(return_value=httpx.Response(200, json={"id": "n"}))
    await _call(
        config,
        "applovin_read_request",
        {
            "api": "ads_reporting",
            "path": "/playableMetrics",
            "query": {"columns": ["day", "spend"]},
        },
    )
    assert unquote(str(get.calls.last.request.url)).count("day,spend") == 1
    refused = await _call(
        config, "applovin_request", {"api": "max", "method": "POST", "path": "/ad_unit", "body": {}}
    )
    assert refused["required_confirm"] == "APPLY"
    assert not post.called


async def test_escape_hatch_respects_the_enabled_side(config: Config) -> None:
    max_only = dataclasses.replace(config, tool_groups=["max_reporting", "request"])
    text = await _error(max_only, "applovin_read_request", {"api": "ads", "path": "/campaign/list"})
    assert "ads side isn't enabled" in text


@pytest.mark.parametrize("path", ["/../x", "//evil.example/x", "/x?api_key=1"])
async def test_escape_hatch_paths_are_checked(config: Config, path: str) -> None:
    assert await _error(config, "applovin_read_request", {"api": "max", "path": path})
