"""Tool errors must reach the client: MCP SDK 2 hides unexpected exception text."""

from __future__ import annotations

import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from applovin_mcp.client import AppLovinAPIError
from applovin_mcp.credentials import register_secret
from applovin_mcp.server import AppLovinMCPServer


def _server() -> AppLovinMCPServer:
    mcp = AppLovinMCPServer("test")
    register_secret("leaky-key-value-123")

    @mcp.tool()
    def applovin_refuses(confirm: str = "") -> dict:
        """Refuses unless confirmed."""
        raise ValueError("Pass confirm='DELETE' to proceed.")

    @mcp.tool()
    def applovin_api_fails() -> dict:
        """Fails like an AppLovin API."""
        raise AppLovinAPIError(403, {"message": "denied"}, api="max")

    @mcp.tool()
    def applovin_leaks() -> dict:
        """Fails with a key in the message."""
        raise RuntimeError("GET /report?api_key=abc123 with leaky-key-value-123")

    @mcp.tool()
    def applovin_crashes() -> dict:
        """Crashes with an unexpected error."""
        raise KeyError("internal detail")

    return mcp


@pytest.mark.parametrize(
    ("tool", "message"),
    [
        ("applovin_refuses", "Pass confirm='DELETE' to proceed."),
        ("applovin_api_fails", "MAX Ad Unit Management API error 403"),
    ],
)
def test_expected_errors_keep_their_message(tool: str, message: str) -> None:
    with pytest.raises(ToolError, match=message.split(":")[0]):
        asyncio.run(_server().call_tool(tool, {}))


def test_error_messages_are_redacted() -> None:
    with pytest.raises(ToolError) as exc_info:
        asyncio.run(_server().call_tool("applovin_leaks", {}))
    text = str(exc_info.value)
    assert "abc123" not in text
    assert "leaky-key-value-123" not in text


def test_unexpected_errors_stay_hidden() -> None:
    with pytest.raises(Exception) as exc_info:
        asyncio.run(_server().call_tool("applovin_crashes", {}))
    assert "internal detail" not in str(exc_info.value)


def test_wrapper_keeps_the_tool_schema() -> None:
    tools = {t.name: t for t in asyncio.run(_server().list_tools())}
    assert "confirm" in tools["applovin_refuses"].input_schema["properties"]
