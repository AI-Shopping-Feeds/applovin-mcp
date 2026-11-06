"""Logging for applovin-mcp.

Stays on stdlib and uses one named logger, 'applovin_mcp', which writes to
stderr so the stdio MCP transport's stdout stays clean for JSON-RPC. Nothing is
logged to disk. Log lines name the API and path of each call, never its query
string, headers or body, so keys don't reach the log.
"""

from __future__ import annotations

import logging
import os
import sys

LOGGER_NAME = "applovin_mcp"

_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the package logger or a sub-logger of it."""
    base = logging.getLogger(LOGGER_NAME)
    if not base.handlers:
        configure_default()
    if name and name != LOGGER_NAME:
        return base.getChild(name)
    return base


class _RedactFilter(logging.Filter):
    """Redact keys from records of third-party loggers (httpx logs full URLs)."""

    def filter(self, record: logging.LogRecord) -> bool:
        from .credentials import redact  # local: credentials imports this module

        try:
            record.msg = redact(record.getMessage())
            record.args = None
        except Exception:
            record.msg, record.args = "(log line hidden: could not redact it)", None
        return True


def quiet_http_loggers() -> None:
    """httpx logs every request URL at INFO, and reporting URLs carry api_key=.

    The MCP SDK sends the root logger to stderr, which clients often keep in
    log files, so these loggers stay at WARNING and are redacted regardless.
    """
    for name in ("httpx", "httpcore"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.WARNING)
        if not any(isinstance(f, _RedactFilter) for f in lg.filters):
            lg.addFilter(_RedactFilter())


def configure_default() -> None:
    """Idempotent default handler: stderr, level from APPLOVIN_LOG_LEVEL (default INFO)."""
    quiet_http_loggers()
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    logger.addHandler(handler)
    level_name = os.environ.get("APPLOVIN_LOG_LEVEL", "INFO").upper()
    logger.setLevel(_LEVELS.get(level_name, logging.INFO))
    logger.propagate = False
