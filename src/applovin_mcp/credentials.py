"""API keys: where they live, how they are stored and how they are kept out of output.

AppLovin authenticates every API with a long-lived key (there is no OAuth
token to refresh). Keys come from environment variables or from the keys file
that `applovin-mcp setup` writes (default
`~/.applovin-mcp/secrets/keys.json`, folder 0700, file 0600). Environment
variables win over the file.

`redact()` hides every registered key, `api_key=` query values, signed
download URLs and secret-looking dictionary keys, so nothing that reaches a
log, an error message or the audit log carries a key.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from ._logging import get_logger

logger = get_logger("credentials")

# Environment variable names, in the order `applovin-mcp setup` asks for them.
CM_KEY_ENV = "APPLOVIN_CAMPAIGN_MANAGEMENT_API_KEY"
ACCOUNT_ID_ENV = "APPLOVIN_ACCOUNT_ID"
ADS_REPORTING_KEY_ENV = "APPLOVIN_REPORTING_API_KEY"
MAX_REPORT_KEY_ENV = "APPLOVIN_MAX_REPORT_KEY"
MAX_MANAGEMENT_KEY_ENV = "APPLOVIN_MAX_MANAGEMENT_KEY"
AD_REVIEW_KEY_ENV = "APPLOVIN_AD_REVIEW_KEY"

KEY_VARS: tuple[str, ...] = (
    CM_KEY_ENV,
    ADS_REPORTING_KEY_ENV,
    MAX_REPORT_KEY_ENV,
    MAX_MANAGEMENT_KEY_ENV,
    AD_REVIEW_KEY_ENV,
)
# Everything the keys file may hold (the account ID isn't secret, but lives with the keys).
FILE_VARS: tuple[str, ...] = (CM_KEY_ENV, ACCOUNT_ID_ENV, *KEY_VARS[1:])

_SECRET_KEYS = {
    "api_key",
    "apikey",
    "api-key",
    "authorization",
    "password",
    "secret",
    "token",
    "access_token",
    "x-amz-signature",
    "x-amz-credential",
    "x-amz-security-token",
    *(v.lower() for v in KEY_VARS),
}
_PATTERNS = (
    # api_key=... in a URL or form body
    re.compile(r"(?i)(api[_-]?key=)[^&\s\"'<>]+"),
    # pre-signed download links (S3 and similar) are credentials too
    re.compile(r"(?i)(x-amz-(?:signature|credential|security-token)=)[^&\s\"'<>]+"),
    re.compile(r"(?i)((?:authorization|api-key)\s*[:=]\s*)[^\s,\"'<>]+"),
)
_extra_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Remember a secret value so redact() hides it wherever it appears."""
    if value and len(value) >= 8:
        _extra_secrets.add(value)


def redact(value: Any) -> Any:
    """Return a copy of value with keys and other secrets replaced by '***'."""
    if isinstance(value, str):
        out = value
        for secret in _extra_secrets:
            if secret in out:
                out = out.replace(secret, "***")
        for pattern in _PATTERNS:
            out = pattern.sub(r"\1***", out)
        return out
    if isinstance(value, dict):
        return {
            k: ("***" if str(k).lower() in _SECRET_KEYS and v else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


def home_dir() -> Path:
    """The per-user folder for this server's files: ~/.applovin-mcp."""
    return Path.home() / ".applovin-mcp"


def ensure_private_dir(path: Path) -> Path:
    """Create a folder (and parents) readable only by the current user."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):  # best effort on Windows
        os.chmod(path, 0o700)
    return path


def write_private_file(path: Path, text: str) -> None:
    """Write text to a file with owner-only (0600) permissions."""
    ensure_private_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def read_keys_file(path: Path) -> dict[str, str]:
    """Read the keys file written by `applovin-mcp setup`. Missing file -> {}."""
    if not path.exists():
        return {}
    if os.name == "posix":
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            logger.warning("keys file %s was readable by others; restricting it to 0600", path)
            with contextlib.suppress(OSError):
                os.chmod(path, 0o600)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RuntimeError(f"Could not read the keys file {path}: {e}") from e
    if not isinstance(data, dict):
        raise RuntimeError(f"The keys file {path} must hold a JSON object.")
    out = {k: str(v).strip() for k, v in data.items() if k in FILE_VARS and v}
    for name in KEY_VARS:
        register_secret(out.get(name))
    return out


def save_keys_file(path: Path, values: dict[str, str]) -> None:
    """Write the keys file (only known names, blank values dropped), owner-only."""
    clean = {k: v for k, v in values.items() if k in FILE_VARS and v}
    write_private_file(path, json.dumps(clean, indent=2) + "\n")
