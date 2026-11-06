"""Configuration from environment variables, .env files and the keys file."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from .credentials import (
    ACCOUNT_ID_ENV,
    AD_REVIEW_KEY_ENV,
    ADS_REPORTING_KEY_ENV,
    CM_KEY_ENV,
    KEY_VARS,
    MAX_MANAGEMENT_KEY_ENV,
    MAX_REPORT_KEY_ENV,
    home_dir,
    read_keys_file,
    register_secret,
)

# "sse" (HTTP+SSE) is deprecated by the MCP spec; prefer "streamable-http".
TRANSPORTS = ("stdio", "streamable-http", "sse")
ACCOUNT_TYPES = ("app", "web")


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_loopback(host: str) -> bool:
    """True for localhost and loopback addresses (127.0.0.0/8, ::1)."""
    host = host.strip().strip("[]")
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_tool_groups(raw: str | None) -> list[str]:
    """Split a comma-separated APPLOVIN_TOOL_GROUPS value and check every name.

    Names are case-insensitive and may use '-' for '_'. 'ads' and 'max' expand to
    every group on that side. Empty means all groups.
    """
    from .tools import resolve_tool_groups  # local: tools imports this module

    names = [p.strip().lower().replace("-", "_") for p in (raw or "").split(",")]
    names = [n for n in names if n]
    return resolve_tool_groups(names) if names else []


@dataclass
class Config:
    # Ads Manager (Axon) Campaign Management API key and account ID.
    cm_api_key: str | None = None
    account_id: str | None = None
    # Ads Manager Reporting API key ("Report Key" in the Ads Manager account).
    ads_reporting_key: str | None = None
    # MAX keys from the MAX dashboard (Account > General > Keys).
    max_report_key: str | None = None
    max_management_key: str | None = None
    ad_review_key: str | None = None
    # APP or WEB Ads Manager account; picks the default report endpoint.
    ads_account_type: str = "app"
    audit_log: Path = field(default_factory=lambda: home_dir() / "logs" / "audit.jsonl")
    keys_file: Path = field(default_factory=lambda: home_dir() / "secrets" / "keys.json")
    # Folder that local asset files may be uploaded from (None = uploads off).
    upload_dir: Path | None = None
    page_size: int = 100
    report_page_size: int = 5000
    timeout: float = 60.0
    dry_run: bool = False
    log_level: str = "INFO"
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    # Serve HTTP on a non-loopback address. Off by default: the server has no sign-in.
    allow_remote: bool = False
    # Tool modules to register; empty means all (see applovin_mcp.tools.TOOL_GROUPS).
    tool_groups: list[str] = field(default_factory=list)
    # Register only tools annotated read-only (plus the meta tools).
    read_only: bool = False
    # Writes touching more than this many items need confirm='APPLY'.
    bulk_threshold: int = 10
    # Budget/bid-like rises (or ROAS-goal drops) beyond this percentage need confirm='APPLY'.
    max_increase_pct: float = 20.0
    # Optional absolute caps; values above them need confirm='APPLY'.
    max_daily_budget: float | None = None
    max_cpm: float | None = None
    # Audit entries older than this are pruned at startup; 0 keeps them.
    audit_retention_days: int = 30
    # Largest API response or report file the server will hold in memory.
    max_download_mb: int = 200

    def key_status(self) -> dict[str, bool]:
        """Which credentials are set (never the values)."""
        return {
            CM_KEY_ENV: bool(self.cm_api_key),
            ACCOUNT_ID_ENV: bool(self.account_id),
            ADS_REPORTING_KEY_ENV: bool(self.ads_reporting_key),
            MAX_REPORT_KEY_ENV: bool(self.max_report_key),
            MAX_MANAGEMENT_KEY_ENV: bool(self.max_management_key),
            AD_REVIEW_KEY_ENV: bool(self.ad_review_key),
        }


def _path_or_none(raw: str | None) -> Path | None:
    if not raw or not raw.strip():
        return None
    return Path(raw).expanduser().resolve()


def _float_or_none(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as e:
        raise RuntimeError(f"{name} must be a number, got '{raw}'") from e


def keys_file_path() -> Path:
    """APPLOVIN_KEYS_FILE, default ~/.applovin-mcp/secrets/keys.json."""
    return _path_or_none(os.environ.get("APPLOVIN_KEYS_FILE")) or (
        home_dir() / "secrets" / "keys.json"
    )


def audit_log_path() -> Path:
    """APPLOVIN_AUDIT_LOG, default ~/.applovin-mcp/logs/audit.jsonl."""
    return _path_or_none(os.environ.get("APPLOVIN_AUDIT_LOG")) or (
        home_dir() / "logs" / "audit.jsonl"
    )


def load_env_files(env_file: Path | None = None) -> None:
    """Load --env (or ./.env), then ~/.applovin-mcp/.env. Real env vars always win."""
    if env_file is not None:
        load_dotenv(dotenv_path=env_file, override=False)
    else:
        found = find_dotenv(usecwd=True)
        if found:
            load_dotenv(dotenv_path=found, override=False)
    user_env = home_dir() / ".env"
    if user_env.is_file():
        load_dotenv(dotenv_path=user_env, override=False)


def load_config(env_file: Path | None = None) -> Config:
    """Read configuration from the environment, .env files and the keys file.

    Missing keys are allowed: tools that need them explain what to set.
    Raises RuntimeError with an actionable message on invalid values."""
    load_env_files(env_file)
    keys_file = keys_file_path()
    stored = read_keys_file(keys_file)

    def cred(name: str) -> str | None:
        return (os.environ.get(name, "").strip() or stored.get(name) or "").strip() or None

    values = {name: cred(name) for name in (*KEY_VARS, ACCOUNT_ID_ENV)}
    for name in KEY_VARS:
        register_secret(values[name])

    account_id = values[ACCOUNT_ID_ENV]
    if account_id and not account_id.isdigit():
        raise RuntimeError(
            f"{ACCOUNT_ID_ENV} must be the numeric account ID shown in Ads Manager "
            f"(account menu > Go to settings), got '{account_id}'."
        )

    account_type = os.environ.get("APPLOVIN_ADS_ACCOUNT_TYPE", "app").strip().lower() or "app"
    if account_type not in ACCOUNT_TYPES:
        raise RuntimeError(f"APPLOVIN_ADS_ACCOUNT_TYPE must be app or web, got '{account_type}'.")

    transport = os.environ.get("APPLOVIN_TRANSPORT", "stdio").strip().lower()
    if transport not in TRANSPORTS:
        raise RuntimeError(
            f"APPLOVIN_TRANSPORT must be one of {', '.join(TRANSPORTS)}, got '{transport}'."
        )

    upload_dir = _path_or_none(os.environ.get("APPLOVIN_UPLOAD_DIR"))
    if upload_dir is not None and not upload_dir.is_dir():
        raise RuntimeError(f"APPLOVIN_UPLOAD_DIR is not a folder: {upload_dir}")

    def _int(name: str, default: str) -> int:
        try:
            return int(os.environ.get(name, default))
        except ValueError as e:
            raise RuntimeError(f"{name} must be an integer") from e

    def _float(name: str, default: str) -> float:
        try:
            return float(os.environ.get(name, default))
        except ValueError as e:
            raise RuntimeError(f"{name} must be a number") from e

    # APPLOVIN_READONLY is the official AppLovin MCP's name; accept it too.
    read_only = _truthy(os.environ.get("APPLOVIN_READ_ONLY")) or _truthy(
        os.environ.get("APPLOVIN_READONLY")
    )

    return Config(
        cm_api_key=values[CM_KEY_ENV],
        account_id=account_id,
        ads_reporting_key=values[ADS_REPORTING_KEY_ENV],
        max_report_key=values[MAX_REPORT_KEY_ENV],
        max_management_key=values[MAX_MANAGEMENT_KEY_ENV],
        ad_review_key=values[AD_REVIEW_KEY_ENV],
        ads_account_type=account_type,
        audit_log=audit_log_path(),
        keys_file=keys_file,
        upload_dir=upload_dir,
        page_size=max(1, min(100, _int("APPLOVIN_PAGE_SIZE", "100"))),
        report_page_size=max(1, _int("APPLOVIN_REPORT_PAGE_SIZE", "5000")),
        timeout=_float("APPLOVIN_TIMEOUT", "60"),
        dry_run=_truthy(os.environ.get("APPLOVIN_DRY_RUN")),
        log_level=os.environ.get("APPLOVIN_LOG_LEVEL", "INFO").upper(),
        transport=transport,
        host=os.environ.get("APPLOVIN_HOST", "127.0.0.1"),
        port=_int("APPLOVIN_PORT", "8000"),
        allow_remote=_truthy(os.environ.get("APPLOVIN_ALLOW_REMOTE")),
        tool_groups=parse_tool_groups(os.environ.get("APPLOVIN_TOOL_GROUPS")),
        read_only=read_only,
        bulk_threshold=_int("APPLOVIN_BULK_THRESHOLD", "10"),
        max_increase_pct=_float("APPLOVIN_MAX_INCREASE_PCT", "20"),
        max_daily_budget=_float_or_none("APPLOVIN_MAX_DAILY_BUDGET"),
        max_cpm=_float_or_none("APPLOVIN_MAX_CPM"),
        audit_retention_days=_int("APPLOVIN_AUDIT_RETENTION_DAYS", "30"),
        max_download_mb=max(1, _int("APPLOVIN_MAX_DOWNLOAD_MB", "200")),
    )
