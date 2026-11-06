from __future__ import annotations

import dataclasses

from applovin_mcp import safety
from applovin_mcp.config import Config
from applovin_mcp.tools.ads_campaigns import campaign_money_changes
from applovin_mcp.tools.max_ad_units import ad_unit_guards


def test_small_change_needs_nothing(config: Config) -> None:
    d = safety.assess(config, count=3)
    assert d.required is None
    assert safety.refusal(config, d, "") is None


def test_removal_needs_delete(config: Config) -> None:
    d = safety.assess(config, count=1, destructive=safety.DELETE)
    assert d.required == "DELETE"
    refused = safety.refusal(config, d, "APPLY")
    assert refused and refused["refused"] is True
    assert safety.refusal(config, d, "DELETE") is None


def test_bulk_needs_apply(config: Config) -> None:
    assert safety.assess(config, count=config.bulk_threshold + 1).required == "APPLY"


def test_budget_rise_above_limit_needs_apply(config: Config) -> None:
    small = safety.assess(config, count=1, changes=[safety.MoneyChange("1", "budget", 100, 115)])
    assert small.required is None
    big = safety.assess(config, count=1, changes=[safety.MoneyChange("1", "budget", 100, 130)])
    assert big.required == "APPLY"
    assert big.changes[0]["change_pct"] == 30.0
    cut = safety.assess(config, count=1, changes=[safety.MoneyChange("1", "budget", 100, 10)])
    assert cut.required is None


def test_roas_goal_cut_is_the_risky_direction(config: Config) -> None:
    lower = safety.MoneyChange("1", "ROAS goal", 1.0, 0.5, rising_is_risky=False)
    higher = safety.MoneyChange("1", "ROAS goal", 1.0, 2.0, rising_is_risky=False)
    assert safety.assess(config, count=1, changes=[lower]).required == "APPLY"
    assert safety.assess(config, count=1, changes=[higher]).required is None


def test_unknown_current_value_needs_apply(config: Config) -> None:
    d = safety.assess(config, count=1, changes=[safety.MoneyChange("1", "budget", None, 10)])
    assert d.required == "APPLY"


def test_new_values_only_check_the_cap(config: Config) -> None:
    capped = dataclasses.replace(config, max_daily_budget=500.0)
    new = safety.MoneyChange("x", "daily budget", None, 300, cap=500.0, is_new=True)
    assert safety.assess(capped, count=1, changes=[new]).required is None
    over = safety.MoneyChange("x", "daily budget", None, 900, cap=500.0, is_new=True)
    assert safety.assess(capped, count=1, changes=[over]).required == "APPLY"


def test_dry_run_never_refuses(config: Config) -> None:
    d = safety.assess(config, count=1, destructive=safety.DELETE)
    assert safety.refusal(dataclasses.replace(config, dry_run=True), d, "") is None


def test_campaign_changes_pair_with_current(config: Config) -> None:
    current = {
        "id": "7",
        "budget": {"daily_budget_for_all_countries": "100", "country_code_to_daily_budget": {}},
        "goal": {"goal_type": "CHK_ROAS", "goal_value_for_all_countries": "0.8"},
    }
    item = {
        "id": "7",
        "budget": {"daily_budget_for_all_countries": "150"},
        "goal": {"goal_value_for_all_countries": "0.4"},
    }
    changes = campaign_money_changes(item, current, config)
    assert [(c.kind, c.old, c.new, c.rising_is_risky) for c in changes] == [
        ("daily budget", 100.0, 150.0, True),
        ("CHK_ROAS goal", 0.8, 0.4, False),
    ]
    assert safety.assess(config, count=1, changes=changes).required == "APPLY"


def test_switching_to_country_budgets_is_unchecked(config: Config) -> None:
    current = {"id": "7", "budget": {"daily_budget_for_all_countries": "100"}}
    item = {"id": "7", "budget": {"country_code_to_daily_budget": {"US": "90"}}}
    [change] = campaign_money_changes(item, current, config)
    assert change.old is None


def test_ad_unit_guards(config: Config) -> None:
    current = {
        "bid_floors": [{"country_group_name": "t1", "cpm": "10.00"}],
        "ad_network_settings": [
            {
                "ADMOB_NETWORK": {
                    "disabled": False,
                    "ad_network_ad_units": [
                        {"ad_network_ad_unit_id": "a", "cpm": "5.00", "disabled": False},
                        {"ad_network_ad_unit_id": "b", "cpm": "3.00", "disabled": False},
                    ],
                }
            }
        ],
    }
    changes = {
        "bid_floors": [{"country_group_name": "t1", "cpm": "15.00"}],
        "ad_network_settings": [
            {
                "ADMOB_NETWORK": {
                    "ad_network_ad_units": [
                        {"ad_network_ad_unit_id": "a", "cpm": "5.50", "disabled": True},
                        {"ad_network_ad_unit_id": "c", "cpm": "8.00"},
                    ]
                }
            }
        ],
    }
    money, reasons = ad_unit_guards(config, "abc123", current, changes)
    by_id = {m.entity_id: m for m in money}
    assert by_id["abc123:floor:t1"].old == 10.0
    assert by_id["abc123:ADMOB_NETWORK:c"].is_new is True
    assert any("leaves out ADMOB_NETWORK line(s) b" in r for r in reasons)
    assert any("disables ADMOB_NETWORK line a" in r for r in reasons)
    d = safety.assess(config, count=1, changes=money, extra_reasons=reasons)
    assert d.required == "APPLY"
