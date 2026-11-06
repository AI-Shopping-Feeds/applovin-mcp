from . import (
    ads_assets,
    ads_campaigns,
    ads_creatives,
    ads_reporting,
    max_ad_review,
    max_ad_units,
    max_reporting,
    request,
)

__all__ = [
    "ads_assets",
    "ads_campaigns",
    "ads_creatives",
    "ads_reporting",
    "max_ad_review",
    "max_ad_units",
    "max_reporting",
    "request",
]

# Tool groups (one per module) in registration order, which is also the order of
# tools/list. APPLOVIN_TOOL_GROUPS selects a subset by these names.
TOOL_GROUPS: tuple[str, ...] = (
    "ads_campaigns",
    "ads_creatives",
    "ads_assets",
    "ads_reporting",
    "max_reporting",
    "max_ad_units",
    "max_ad_review",
    "request",
)

# Shorthands: one side of AppLovin at a time.
GROUP_ALIASES: dict[str, tuple[str, ...]] = {
    "ads": tuple(g for g in TOOL_GROUPS if g.startswith("ads_")),
    "max": tuple(g for g in TOOL_GROUPS if g.startswith("max_")),
}


def side_of(group: str) -> str | None:
    """'ads' or 'max' for a group on one side; None for the shared request group."""
    if group.startswith("ads_"):
        return "ads"
    if group.startswith("max_"):
        return "max"
    return None


def resolve_tool_groups(requested: list[str] | tuple[str, ...]) -> list[str]:
    """Validate group names (and the 'ads'/'max' shorthands); return them in order.

    An empty selection means every group. Raises RuntimeError naming the
    unknown groups and listing the valid ones.
    """
    if not requested:
        return list(TOOL_GROUPS)
    wanted: set[str] = set()
    unknown: list[str] = []
    for name in requested:
        if name in GROUP_ALIASES:
            wanted.update(GROUP_ALIASES[name])
        elif name in TOOL_GROUPS:
            wanted.add(name)
        else:
            unknown.append(name)
    if unknown:
        raise RuntimeError(
            f"APPLOVIN_TOOL_GROUPS has unknown group(s): {', '.join(unknown)}.\n"
            f"Valid groups: {', '.join(TOOL_GROUPS)} (or 'ads' / 'max' for one side)."
        )
    return [g for g in TOOL_GROUPS if g in wanted]
