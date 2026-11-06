"""Confirmation guards for writes that change live campaigns, spend or ad revenue.

A write tool asks `assess()` what its change needs, then `refusal()` whether to
stop. A guarded write is refused (with a summary of what it would do) unless the
caller passes the required `confirm` word:

* removals (creative sets or assets taken out of campaigns, waterfalls removed,
  experiments discarded) need confirm='DELETE';
* writes touching more than APPLOVIN_BULK_THRESHOLD items, budget or CPM rises
  above APPLOVIN_MAX_INCREASE_PCT, ROAS-goal cuts above it, changes whose
  current value couldn't be read, values over APPLOVIN_MAX_DAILY_BUDGET /
  APPLOVIN_MAX_CPM, experiment promotions, disabled networks and raw writes
  need confirm='APPLY'.

In dry-run mode nothing is sent, so guards don't block; the result says which
confirm a real run would need.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .config import Config

APPLY = "APPLY"
DELETE = "DELETE"


@dataclass
class MoneyChange:
    """A money value a write sets. `old` is None if it couldn't be read.

    rising_is_risky: True for budgets, cost goals and CPMs (higher = more spend
    or a stricter floor); False for ROAS goals (a lower target spends more).
    cap: an absolute ceiling for this kind of value, if configured.
    """

    entity_id: str
    kind: str
    old: float | None
    new: float
    rising_is_risky: bool = True
    cap: float | None = None
    # A value on something new (a new network line, floor group or campaign):
    # only the cap applies.
    is_new: bool = False


@dataclass
class Decision:
    required: str | None = None
    reasons: list[str] = field(default_factory=list)
    changes: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "required_confirm": self.required,
            "reasons": self.reasons,
            "money_changes": self.changes[:50],
        }


def num(value: Any) -> float | None:
    """A float from an API number or numeric string; None if it isn't one."""
    if isinstance(value, bool):
        return None
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def assess(
    config: Config,
    *,
    count: int,
    destructive: str | None = None,
    changes: Sequence[MoneyChange] = (),
    creating: bool = False,
    extra_reasons: Iterable[str] = (),
) -> Decision:
    """Work out which confirm word, if any, a write needs."""
    d = Decision()
    if destructive:
        d.required = destructive
        d.reasons.append(f"removes or discards {count} item(s); this server can't undo it")
    if count > config.bulk_threshold:
        d.reasons.append(
            f"{count} items is more than APPLOVIN_BULK_THRESHOLD ({config.bulk_threshold})"
        )
    d.reasons.extend(extra_reasons)
    limit = config.max_increase_pct
    for c in changes:
        info: dict[str, Any] = {"id": c.entity_id, "kind": c.kind, "old": c.old, "new": c.new}
        if c.old is not None and c.old > 0:
            info["change_pct"] = round((c.new - c.old) / c.old * 100, 1)
        d.changes.append(info)
        if c.cap is not None and c.new > c.cap:
            d.reasons.append(f"{c.kind} {c.new:g} for {c.entity_id} is above the cap of {c.cap:g}")
        if creating or c.is_new:
            continue
        if c.old is None:
            d.reasons.append(
                f"current {c.kind} for {c.entity_id} is unknown, so the change is unchecked"
            )
        elif c.rising_is_risky and (
            c.old <= 0 < c.new or (c.old > 0 and (c.new - c.old) / c.old * 100 > limit)
        ):
            d.reasons.append(
                f"{c.kind} for {c.entity_id} rises from {c.old:g} to {c.new:g} "
                f"(more than APPLOVIN_MAX_INCREASE_PCT={limit:g}%)"
            )
        elif not c.rising_is_risky and c.old > 0 and (c.old - c.new) / c.old * 100 > limit:
            d.reasons.append(
                f"{c.kind} for {c.entity_id} drops from {c.old:g} to {c.new:g}, which lets "
                f"AppLovin spend more (more than APPLOVIN_MAX_INCREASE_PCT={limit:g}%)"
            )
    if d.reasons and d.required is None:
        d.required = APPLY
    return d


def refusal(
    config: Config, decision: Decision, confirm: str, *, preview: Any = None
) -> dict[str, Any] | None:
    """Return a refusal payload if the write must not go ahead, else None."""
    if decision.required is None or config.dry_run or confirm == decision.required:
        return None
    out: dict[str, Any] = {
        "refused": True,
        "error": (
            f"Not sent. Show the user what will change, and once they agree call again "
            f"with confirm='{decision.required}' (case-sensitive)."
        ),
        **decision.as_dict(),
    }
    if preview is not None:
        out["would_send"] = preview
    return out


def annotate(result: Any, decision: Decision) -> Any:
    """Attach the guard summary to a result (useful in dry-run)."""
    if decision.required is None:
        return result
    if isinstance(result, dict):
        return {**result, "safety": decision.as_dict()}
    return {"result": result, "safety": decision.as_dict()}
