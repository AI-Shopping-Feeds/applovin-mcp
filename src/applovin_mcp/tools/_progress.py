"""Helpers for async tools that loop over many blocking AppLovin API calls."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, TypeVar

import anyio.to_thread
from mcp.server.mcpserver import Context

T = TypeVar("T")


async def run_blocking(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run a blocking call (an HTTP request, file I/O) in a worker thread."""
    return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))


async def report(ctx: Context | None, done: float, total: float | None, message: str) -> None:
    """Send a progress notification when called through MCP; a no-op otherwise.

    Callers must pass a strictly increasing `done`.
    """
    if ctx is not None:
        await ctx.report_progress(done, total, message)
