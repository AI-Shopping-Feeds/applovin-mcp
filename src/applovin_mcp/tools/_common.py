"""Shared building blocks for the tool modules."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from mcp.server.mcpserver import Context

from ..client import AppLovinClient
from ._progress import report, run_blocking

_COLUMN = re.compile(r"^[a-z][a-z0-9_]*$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def ids_to_strings(ids: Iterable[Any] | None) -> list[str]:
    return [str(i).strip() for i in (ids or []) if str(i).strip()]


def check_ids(ids: Iterable[Any] | None, what: str, max_items: int | None = None) -> list[str]:
    out = ids_to_strings(ids)
    if not out:
        raise ValueError(f"Pass at least one {what}.")
    bad = [i for i in out if not _ID.match(i)]
    if bad:
        raise ValueError(f"{what} must be plain IDs; got {bad[:5]}.")
    if max_items is not None and len(out) > max_items:
        raise ValueError(f"At most {max_items} {what} per call; got {len(out)}.")
    return out


def check_id(value: Any, what: str) -> str:
    return check_ids([value], what, 1)[0]


def check_numeric_ids(
    ids: Iterable[Any] | None, what: str, max_items: int | None = None
) -> list[int]:
    out = check_ids(ids, what, max_items)
    bad = [i for i in out if not i.isdigit()]
    if bad:
        raise ValueError(f"{what} must be numeric IDs; got {bad[:5]}.")
    return [int(i) for i in out]


def chunks(items: Sequence[Any], size: int) -> list[list[Any]]:
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def check_items(items: list[dict[str, Any]], what: str, max_items: int) -> None:
    if not items:
        raise ValueError(f"Pass at least one {what}.")
    if len(items) > max_items:
        raise ValueError(f"At most {max_items} {what} per call; got {len(items)}.")
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"{what}[{i}] must be an object.")


def today_utc() -> date:
    return datetime.now(UTC).date()


def check_dates(start: str, end: str, *, window_days: int, allow_now: bool = True) -> None:
    """Check YYYY-MM-DD dates (end may be 'now') and AppLovin's look-back window."""
    if not _DAY.match(start):
        raise ValueError(f"start must be YYYY-MM-DD, got '{start}'.")
    if not (_DAY.match(end) or (allow_now and end == "now")):
        raise ValueError(f"end must be YYYY-MM-DD{' or now' if allow_now else ''}, got '{end}'.")
    try:
        first = date.fromisoformat(start)
        last = today_utc() if end == "now" else date.fromisoformat(end)
    except ValueError as e:
        raise ValueError(f"Invalid date: {e}") from e
    if last < first:
        raise ValueError("end is before start.")
    oldest = today_utc() - timedelta(days=window_days)
    if first < oldest:
        raise ValueError(
            f"This API only covers the last {window_days} days (UTC): start must be "
            f"{oldest.isoformat()} or later."
        )


def check_columns(columns: Sequence[str], what: str = "columns") -> list[str]:
    cols = [c.strip() for c in columns if c and c.strip()]
    if not cols:
        raise ValueError(f"Pass at least one of {what}.")
    bad = [c for c in cols if not _COLUMN.match(c)]
    if bad:
        raise ValueError(f"Column names are lower_snake_case; got {bad[:5]}.")
    return cols


def report_query(
    *,
    columns: Sequence[str],
    filters: dict[str, Any] | None = None,
    exclude: dict[str, Any] | None = None,
    sort: Sequence[str] | None = None,
    having: str | None = None,
    not_zero: bool = False,
) -> list[tuple[str, Any]]:
    """Build the shared query parameters of AppLovin's GET reporting endpoints.

    filters: {"column": "value" or ["v1", "v2"]} -> filter_column=v1,v2
    exclude: the same, sent as filter_not_column (where the API supports it)
    sort: ["cost:DESC", "day:ASC"], applied in order
    """
    params: list[tuple[str, Any]] = [("columns", ",".join(check_columns(columns)))]
    for prefix, mapping in (("filter_", filters), ("filter_not_", exclude)):
        for col, values in (mapping or {}).items():
            check_columns([col], "filter columns")
            vals = values if isinstance(values, list | tuple) else [values]
            params.append((f"{prefix}{col}", ",".join(str(v) for v in vals)))
    for item in sort or []:
        col, _, direction = item.partition(":")
        direction = (direction or "ASC").upper()
        if direction not in {"ASC", "DESC"}:
            raise ValueError(f"Sort direction must be ASC or DESC, got '{item}'.")
        check_columns([col], "sort columns")
        params.append((f"sort_{col.strip()}", direction))
    if having:
        params.append(("having", having))
    if not_zero:
        params.append(("not_zero", 1))
    return params


def rows_from(payload: Any) -> list[Any]:
    """Rows from a reporting response: {'results': [...]} or a bare list."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("results", "rows", "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


async def fetch_report(
    client: AppLovinClient,
    api: str,
    path: str,
    params: list[tuple[str, Any]],
    *,
    max_rows: int,
    ctx: Context | None,
    label: str,
) -> dict[str, Any]:
    """Page through a GET report with limit/offset and progress; rows stay in memory."""
    max_rows = max(1, max_rows)
    page_size = max(1, min(client.config.report_page_size, max_rows))
    rows: list[Any] = []
    offset = 0
    page = 0
    more = False
    while True:
        page += 1
        query = [*params, ("format", "json"), ("limit", page_size), ("offset", offset)]
        payload = await run_blocking(client.request, api, "GET", path, params=query)
        batch = rows_from(payload)
        rows.extend(batch)
        await report(ctx, page, None, f"{label}: {len(rows)} rows so far")
        if len(batch) < page_size:
            break
        if len(rows) >= max_rows:
            more = True
            break
        offset += len(batch)
    return {
        "row_count": min(len(rows), max_rows),
        "rows": rows[:max_rows],
        "truncated": more or len(rows) > max_rows,
        "pages": page,
    }
