"""MAX ad units, waterfalls, experiments and test devices (Ad Unit Management API).

Base URL https://o.applovin.com/mediation/v1, authenticated with the MAX
Management Key in an Api-Key header; AppLovin allows 2000 requests an hour.
A POST to an ad unit changes only the fields it includes; the server reads the
ad unit first, keeps it in the audit log, and checks CPM and floor changes.
"""

from __future__ import annotations

import re
from typing import Any

from .. import safety
from ..client import AppLovinClient
from ..config import Config

API = "max"
AD_UNIT_FIELDS = (
    "ad_network_settings,disabled_ad_network_settings,frequency_capping_settings,"
    "bid_floors,segments,banner_refresh_settings,mrec_refresh_settings"
)
EXPERIMENT_FIELDS = "ad_network_settings,frequency_capping_settings,bid_floors"
CORE_FIELDS = ("id", "name", "platform", "ad_format", "package_name")
_AD_UNIT_ID = re.compile(r"^[A-Za-z0-9]{4,64}$")
_SEGMENT_ID = re.compile(r"^\d{1,15}$")
_DEVICE_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
ALLOCATIONS = (50, 25, 10, 5)


def check_ad_unit_id(value: str) -> str:
    value = str(value).strip()
    if not _AD_UNIT_ID.match(value):
        raise ValueError(
            f"ad_unit_id must be a MAX ad unit ID like 1234567890abcdef, got '{value}'."
        )
    return value


def unit_path(
    ad_unit_id: str, segment_id: str | int | None = None, experiment: bool = False
) -> str:
    base = "/ad_unit_experiment" if experiment else "/ad_unit"
    path = f"{base}/{check_ad_unit_id(ad_unit_id)}"
    if segment_id not in (None, ""):
        seg = str(segment_id).strip()
        if not _SEGMENT_ID.match(seg):
            raise ValueError(f"segment_id must be numeric, got '{seg}'.")
        path += f"/{seg}"
    return path


def fetch_ad_unit(
    client: AppLovinClient,
    ad_unit_id: str,
    segment_id: str | int | None = None,
    experiment: bool = False,
    fields: str | None = None,
) -> Any:
    return client.request(
        API,
        "GET",
        unit_path(ad_unit_id, segment_id, experiment),
        params={"fields": fields or (EXPERIMENT_FIELDS if experiment else AD_UNIT_FIELDS)},
    )


def _networks(unit: Any) -> dict[str, dict[str, Any]]:
    """{NETWORK_API_NAME: settings} from ad_network_settings (+ disabled ones)."""
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(unit, dict):
        return out
    for key in ("disabled_ad_network_settings", "ad_network_settings"):
        for entry in unit.get(key) or []:
            if isinstance(entry, dict):
                for name, settings in entry.items():
                    if isinstance(settings, dict):
                        out[str(name)] = settings
    return out


def _floors(unit: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    if not isinstance(unit, dict):
        return out
    for floor in unit.get("bid_floors") or []:
        if isinstance(floor, dict):
            cpm = safety.num(floor.get("cpm"))
            if cpm is not None:
                out[str(floor.get("country_group_name", ""))] = cpm
    return out


def ad_unit_guards(
    config: Config, ad_unit_id: str, current: Any, changes: dict[str, Any]
) -> tuple[list[safety.MoneyChange], list[str]]:
    """CPM and floor changes, disabled networks and dropped network ad units."""
    known = isinstance(current, dict)
    money: list[safety.MoneyChange] = []
    reasons: list[str] = []
    if isinstance(changes.get("bid_floors"), list):
        old_floors = _floors(current) if known else None
        for group, cpm in _floors(changes).items():
            old = old_floors.get(group, 0.0) if old_floors is not None else None
            money.append(
                safety.MoneyChange(
                    f"{ad_unit_id}:floor:{group}", "bid floor CPM", old, cpm, True, config.max_cpm
                )
            )
    if isinstance(changes.get("ad_network_settings"), list):
        old_nets = _networks(current) if known else {}
        for name, settings in _networks(
            {"ad_network_settings": changes["ad_network_settings"]}
        ).items():
            cur = old_nets.get(name)
            if settings.get("disabled") is True and (cur is None or not cur.get("disabled")):
                reasons.append(f"disables {name} on ad unit {ad_unit_id}")
            cur_units = {
                str(u.get("ad_network_ad_unit_id")): u
                for u in (cur or {}).get("ad_network_ad_units") or []
                if isinstance(u, dict)
            }
            new_units = settings.get("ad_network_ad_units")
            if isinstance(new_units, list):
                new_ids = {
                    str(u.get("ad_network_ad_unit_id")) for u in new_units if isinstance(u, dict)
                }
                dropped = sorted(set(cur_units) - new_ids)
                if dropped:
                    reasons.append(
                        f"leaves out {name} line(s) {', '.join(dropped[:5])}; AppLovin replaces the "
                        "network's list, so include every line you want to keep"
                    )
                for u in new_units:
                    if not isinstance(u, dict):
                        continue
                    uid = str(u.get("ad_network_ad_unit_id"))
                    old_u = cur_units.get(uid)
                    if (
                        u.get("disabled") is True
                        and old_u is not None
                        and not old_u.get("disabled")
                    ):
                        reasons.append(f"disables {name} line {uid}")
                    line_cpm = safety.num(u.get("cpm"))
                    if line_cpm is None:
                        continue
                    old_cpm = safety.num(old_u.get("cpm")) if old_u else None
                    money.append(
                        safety.MoneyChange(
                            f"{ad_unit_id}:{name}:{uid}",
                            "network CPM",
                            old_cpm if known else None,
                            line_cpm,
                            True,
                            config.max_cpm,
                            is_new=known and old_u is None,
                        )
                    )
    return money, reasons


def _core(current: Any, ad_unit_id: str) -> dict[str, Any]:
    body = {k: current[k] for k in CORE_FIELDS if isinstance(current, dict) and k in current}
    body["id"] = ad_unit_id
    return body


def register(mcp, client: AppLovinClient) -> None:
    @mcp.tool()
    def applovin_max_list_ad_units(
        fields: list[str] | None = None,
        platform: str | None = None,
        ad_format: str | None = None,
        package_name: str | None = None,
        max_results: int = 500,
    ) -> dict[str, Any]:
        """List active MAX ad units (id, name, platform, package_name, ad_format,
        has_active_experiment).

        fields adds detail for every unit: ad_network_settings,
        frequency_capping_settings, bid_floors (slower; may time out on big
        accounts, so prefer applovin_max_get_ad_unit for one unit). platform,
        ad_format and package_name filter the results locally.
        """
        allowed = {"ad_network_settings", "frequency_capping_settings", "bid_floors"}
        bad = [f for f in fields or [] if f not in allowed]
        if bad:
            raise ValueError(f"fields can include {', '.join(sorted(allowed))}; got {bad}.")
        items: list[Any] = []
        offset = 0
        page = 100
        truncated = False
        while True:
            params: dict[str, Any] = {"limit": page, "offset": offset}
            if fields:
                params["fields"] = ",".join(fields)
            batch = client.request(API, "GET", "/ad_units", params=params)
            batch = batch if isinstance(batch, list) else []
            items.extend(batch)
            if len(batch) < page:
                break
            if len(items) >= max_results:
                truncated = True
                break
            offset += len(batch)

        def keep(u: dict[str, Any]) -> bool:
            return (
                (not platform or str(u.get("platform", "")).lower() == platform.lower())
                and (not ad_format or str(u.get("ad_format", "")).upper() == ad_format.upper())
                and (not package_name or u.get("package_name") == package_name)
            )

        items = [u for u in items if isinstance(u, dict) and keep(u)][:max_results]
        return {"ad_units": items, "count": len(items), "truncated": truncated}

    @mcp.tool()
    def applovin_max_get_ad_unit(
        ad_unit_id: str,
        segment_id: str | None = None,
        experiment: bool = False,
        fields: list[str] | None = None,
    ) -> Any:
        """Get one MAX ad unit with its networks and CPMs, bid floors, frequency
        caps, refresh settings and segments (waterfalls).

        segment_id: a waterfall's ID (from segments) to get that waterfall.
        experiment=true gets the ad unit's experiment instead. fields overrides
        the detail returned.
        """
        return fetch_ad_unit(
            client, ad_unit_id, segment_id, experiment, ",".join(fields) if fields else None
        )

    @mcp.tool()
    def applovin_max_create_ad_unit(
        name: str,
        platform: str,
        package_name: str,
        ad_format: str,
        template_size: str | None = None,
    ) -> Any:
        """Create a MAX ad unit. Changes the live account.

        platform: ios or android. ad_format: e.g. INTER, REWARD, BANNER, MREC,
        NATIVE. template_size is for native units only (small_template_1,
        medium_template_1 or custom_template_1). AppLovin refuses a second active
        ad unit for the same app, platform and format through the API; use the
        dashboard for that.
        """
        platform = platform.lower()
        if platform not in ("ios", "android"):
            raise ValueError("platform must be ios or android.")
        body: dict[str, Any] = {
            "name": name,
            "platform": platform,
            "package_name": package_name,
            "ad_format": ad_format.upper(),
        }
        if template_size:
            body["template_size"] = template_size
        return client.request(API, "POST", "/ad_unit", json_body=body, op="create_ad_unit")

    @mcp.tool()
    def applovin_max_update_ad_unit(
        ad_unit_id: str,
        changes: dict[str, Any],
        segment_id: str | None = None,
        experiment: bool = False,
        confirm: str = "",
    ) -> Any:
        """Change a MAX ad unit, one of its waterfalls (segment_id) or its
        experiment (experiment=true). Changes live ad serving and revenue.

        changes: only the fields to change, e.g. {"bid_floors": [...]} (send the
        full list of floors), {"frequency_capping_settings": [...]},
        {"banner_refresh_settings": {"interval": 30}}, or {"ad_network_settings":
        [{"ADMOB_NETWORK": {"ad_network_app_id": "...", "ad_network_ad_units":
        [...]}}]}. For a network, include every line you want to keep: AppLovin
        replaces that network's list. Other networks are untouched. id, name,
        platform, format and package are filled in from the current ad unit.

        The current settings are read first and kept in the audit log. Floor or
        CPM rises above APPLOVIN_MAX_INCREASE_PCT, values over APPLOVIN_MAX_CPM,
        disabled networks or lines, dropped lines, and changes whose current
        value can't be read need confirm='APPLY'. Use applovin_max_create_waterfall,
        applovin_max_remove_waterfall and applovin_max_end_experiment for those actions.
        """
        if not isinstance(changes, dict) or not changes:
            raise ValueError("changes must be a non-empty object.")
        blocked = {
            "id": "it's taken from ad_unit_id",
            "promote": "use applovin_max_end_experiment",
            "deprecate": "use applovin_max_end_experiment",
            "segment": "use applovin_max_create_waterfall",
            "segments": "it's read-only",
            "disabled": "use applovin_max_remove_waterfall (ad units can't be disabled through the API)",
        }
        bad = [f"{k} ({why})" for k, why in blocked.items() if k in changes]
        if bad:
            raise ValueError(f"Don't set {'; '.join(bad)}.")
        ad_unit_id = check_ad_unit_id(ad_unit_id)
        path = unit_path(ad_unit_id, segment_id, experiment)
        current = client.snapshot(fetch_ad_unit, client, ad_unit_id, segment_id, experiment)
        body = _core(current, ad_unit_id)
        if experiment:
            name = changes.get("experiment_name") or (current or {}).get("experiment_name")
            if not name:
                raise ValueError(
                    "Couldn't read the experiment's name; pass experiment_name in changes."
                )
            body["experiment_name"] = name
        body.update(changes)
        money, reasons = ad_unit_guards(client.config, ad_unit_id, current, changes)
        decision = safety.assess(client.config, count=1, changes=money, extra_reasons=reasons)
        refused = safety.refusal(client.config, decision, confirm, preview=body)
        if refused:
            return refused
        out = client.request(API, "POST", path, json_body=body, op="update_ad_unit", before=current)
        return safety.annotate(out, decision)

    @mcp.tool()
    def applovin_max_create_waterfall(
        ad_unit_id: str,
        segment: dict[str, Any],
        settings: dict[str, Any] | None = None,
        confirm: str = "",
    ) -> Any:
        """Add a waterfall for a user segment to a MAX ad unit.

        segment: {"name": "No-ID iPhones", "id_type": "all|id_only|no_id",
        "device_type": "all|phones|tablets", "segment_keys": [["+1:2"]]} (keys
        from data and keyword passing). A segment can't be changed later; remove
        the waterfall and create a new one instead. settings: optional
        ad_network_settings, bid_floors and other ad unit fields for the new
        waterfall. Values over APPLOVIN_MAX_CPM need confirm='APPLY'.
        """
        if not isinstance(segment, dict) or not segment.get("name"):
            raise ValueError("segment needs at least a name.")
        if segment.get("id_type", "all") not in ("all", "id_only", "no_id"):
            raise ValueError("segment.id_type must be all, id_only or no_id.")
        if segment.get("device_type", "all") not in ("all", "phones", "tablets"):
            raise ValueError("segment.device_type must be all, phones or tablets.")
        if "id" in segment:
            raise ValueError("Leave segment.id out; AppLovin assigns it.")
        ad_unit_id = check_ad_unit_id(ad_unit_id)
        current = client.snapshot(fetch_ad_unit, client, ad_unit_id)
        body = _core(current, ad_unit_id)
        body.update(settings or {})
        body["segment"] = segment
        money, reasons = ad_unit_guards(client.config, ad_unit_id, None, settings or {})
        for m in money:
            m.is_new = True
        decision = safety.assess(client.config, count=1, changes=money, extra_reasons=reasons)
        refused = safety.refusal(client.config, decision, confirm, preview=body)
        if refused:
            return refused
        out = client.request(
            API, "POST", unit_path(ad_unit_id), json_body=body, op="create_waterfall"
        )
        return safety.annotate(out, decision)

    @mcp.tool()
    def applovin_max_remove_waterfall(ad_unit_id: str, segment_id: str, confirm: str = "") -> Any:
        """Remove a segment waterfall from a MAX ad unit; its users fall back to the
        default waterfall. Needs confirm='DELETE' after the user agrees. The
        waterfall's settings are kept in the audit log."""
        ad_unit_id = check_ad_unit_id(ad_unit_id)
        path = unit_path(ad_unit_id, segment_id)
        decision = safety.assess(client.config, count=1, destructive=safety.DELETE)
        current = client.snapshot(fetch_ad_unit, client, ad_unit_id, segment_id)
        body = {**_core(current, ad_unit_id), "disabled": True}
        refused = safety.refusal(client.config, decision, confirm, preview=body)
        if refused:
            return refused
        out = client.request(
            API, "POST", path, json_body=body, op="remove_waterfall", before=current
        )
        return safety.annotate(out, decision)

    @mcp.tool()
    def applovin_max_create_experiment(
        ad_unit_id: str,
        experiment_name: str,
        changes: dict[str, Any] | None = None,
        segment_id: str | None = None,
        test_group_allocation: int | None = None,
        confirm: str = "",
    ) -> Any:
        """Start an A/B experiment on a MAX ad unit (or one of its waterfalls).

        The experiment copies the ad unit; changes sets what differs (for example
        bid_floors or ad_network_settings). test_group_allocation: 50, 25, 10 or
        5 (% of users). Only one active experiment per ad unit. Floor or CPM
        rises above the limit compared with the live ad unit need confirm='APPLY'.
        """
        ad_unit_id = check_ad_unit_id(ad_unit_id)
        changes = dict(changes or {})
        if "id" in changes or "promote" in changes or "deprecate" in changes:
            raise ValueError("Leave id, promote and deprecate out of changes.")
        if test_group_allocation is not None and test_group_allocation not in ALLOCATIONS:
            raise ValueError("test_group_allocation must be 50, 25, 10 or 5.")
        body: dict[str, Any] = {"experiment_name": experiment_name, **changes}
        if test_group_allocation is not None:
            body["test_group_allocation"] = test_group_allocation
        parent = client.snapshot(fetch_ad_unit, client, ad_unit_id, segment_id)
        money, reasons = ad_unit_guards(client.config, ad_unit_id, parent, changes)
        decision = safety.assess(client.config, count=1, changes=money, extra_reasons=reasons)
        refused = safety.refusal(client.config, decision, confirm, preview=body)
        if refused:
            return refused
        out = client.request(
            API,
            "POST",
            unit_path(ad_unit_id, segment_id, experiment=True),
            json_body=body,
            op="create_experiment",
            before=parent,
        )
        return safety.annotate(out, decision)

    @mcp.tool()
    def applovin_max_end_experiment(
        ad_unit_id: str,
        action: str,
        segment_id: str | None = None,
        experiment_name: str | None = None,
        confirm: str = "",
    ) -> Any:
        """End a MAX ad unit experiment.

        action='promote' makes the experiment's settings the live settings for
        all users (confirm='APPLY'); action='deprecate' discards the experiment
        (confirm='DELETE'). Look at its results first (applovin_max_revenue_report
        with the max_ad_unit_test column). Both settings are kept in the audit log.
        """
        action = action.lower()
        if action not in ("promote", "deprecate"):
            raise ValueError("action must be promote or deprecate.")
        ad_unit_id = check_ad_unit_id(ad_unit_id)
        path = unit_path(ad_unit_id, segment_id, experiment=True)
        current = client.snapshot(fetch_ad_unit, client, ad_unit_id, segment_id, True)
        name = experiment_name or (current or {}).get("experiment_name")
        if not name:
            raise ValueError("Couldn't read the experiment's name; pass experiment_name.")
        body = {
            "id": ad_unit_id,
            "experiment_name": name,
            "promote": action == "promote",
            "deprecate": action == "deprecate",
        }
        if action == "promote":
            decision = safety.assess(
                client.config,
                count=1,
                extra_reasons=["promoting replaces the live ad unit settings for every user"],
            )
        else:
            decision = safety.assess(client.config, count=1, destructive=safety.DELETE)
        refused = safety.refusal(client.config, decision, confirm, preview=body)
        if refused:
            return refused
        parent = client.snapshot(fetch_ad_unit, client, ad_unit_id, segment_id)
        out = client.request(
            API,
            "POST",
            path,
            json_body=body,
            op=f"{action}_experiment",
            before={"experiment": current, "ad_unit": parent},
        )
        return safety.annotate(out, decision)

    @mcp.tool()
    def applovin_max_list_test_devices() -> dict[str, Any]:
        """List MAX test devices (enabled and disabled) with their network."""
        items = client.request(API, "GET", "/test_devices")
        items = items if isinstance(items, list) else []
        return {"test_devices": items, "count": len(items)}

    @mcp.tool()
    def applovin_max_set_test_device(
        device_id: str,
        name: str | None = None,
        network: str | None = None,
        disabled: bool | None = None,
    ) -> Any:
        """Add a test device, or change an existing one's name, network or disabled
        flag. device_id is the device's advertising ID (IDFA/GAID). network is a
        Network API name such as APPLOVIN_NETWORK (default for new devices)."""
        device_id = device_id.strip()
        if not _DEVICE_ID.match(device_id):
            raise ValueError("device_id must be an advertising ID (letters, digits and dashes).")
        listed = client.request(API, "GET", "/test_devices")
        current = (
            next(
                (d for d in listed if isinstance(d, dict) and d.get("device_id") == device_id),
                None,
            )
            if isinstance(listed, list)
            else None
        )
        if current is None:
            if not name:
                raise ValueError("A new test device needs a name.")
            body = {
                "name": name,
                "device_id": device_id,
                "disabled": bool(disabled),
                "network": network or "APPLOVIN_NETWORK",
            }
            return client.request(
                API, "POST", "/test_device", json_body=body, op="create_test_device"
            )
        body = {
            "name": name or current.get("name"),
            "device_id": device_id,
            "disabled": current.get("disabled") if disabled is None else disabled,
            "network": network or current.get("network"),
        }
        return client.request(
            API,
            "POST",
            f"/test_device/{device_id}",
            json_body=body,
            op="update_test_device",
            before=current,
        )
