"""CLI entrypoint exposed as the `applovin-mcp` console script.

Subcommands:

* `applovin-mcp run`       (default) start the MCP server.
* `applovin-mcp setup`     store AppLovin keys in ~/.applovin-mcp/secrets/keys.json (0600).
* `applovin-mcp purge`     delete the audit log and the stored keys.
* `applovin-mcp describe`  print the effective configuration (no secrets).
* `applovin-mcp version`   print the package version.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from . import __version__
from ._logging import configure_default, get_logger
from .config import (
    TRANSPORTS,
    audit_log_path,
    is_loopback,
    keys_file_path,
    load_config,
    load_env_files,
)
from .credentials import (
    ACCOUNT_ID_ENV,
    AD_REVIEW_KEY_ENV,
    ADS_REPORTING_KEY_ENV,
    CM_KEY_ENV,
    MAX_MANAGEMENT_KEY_ENV,
    MAX_REPORT_KEY_ENV,
    read_keys_file,
    redact,
    register_secret,
    save_keys_file,
)
from .server import build_server

logger = get_logger("cli")

# What `setup` asks for, with where to find it.
SETUP_PROMPTS: tuple[tuple[str, str, bool], ...] = (
    (
        CM_KEY_ENV,
        "Ads Manager Campaign Management API key (account menu > Keys; Admins only; "
        "whitelist-only beta)",
        True,
    ),
    (ACCOUNT_ID_ENV, "Ads Manager account ID (account menu > Go to settings)", False),
    (ADS_REPORTING_KEY_ENV, "Ads Manager Report Key (account menu > Keys)", True),
    (MAX_REPORT_KEY_ENV, "MAX Report Key (MAX dashboard > Account > General > Keys)", True),
    (MAX_MANAGEMENT_KEY_ENV, "MAX Management Key (same page)", True),
    (AD_REVIEW_KEY_ENV, "MAX Ad Review Key (same page; only for Ad Review rules)", True),
)


@click.group(invoke_without_command=True)
@click.option(
    "--env",
    "env_file",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Path to a .env file. Defaults to ./.env, then ~/.applovin-mcp/.env.",
)
@click.option(
    "--transport",
    type=click.Choice(TRANSPORTS),
    default=None,
    help="Override APPLOVIN_TRANSPORT. 'sse' is deprecated; use 'streamable-http'.",
)
@click.option("--host", default=None, help="Bind host for HTTP transports (default 127.0.0.1).")
@click.option(
    "--port", type=int, default=None, help="Bind port for HTTP transports (default 8000)."
)
@click.option(
    "--allow-remote",
    is_flag=True,
    default=False,
    help="Allow binding HTTP to a non-loopback address (same as APPLOVIN_ALLOW_REMOTE=true). "
    "The server has no sign-in: only do this behind your own authentication.",
)
@click.pass_context
def cli(
    ctx: click.Context,
    env_file: Path | None,
    transport: str | None,
    host: str | None,
    port: int | None,
    allow_remote: bool,
) -> None:
    """AppLovin MCP server (Ads Manager and MAX)."""
    ctx.ensure_object(dict)
    ctx.obj.update(
        env_file=env_file, transport=transport, host=host, port=port, allow_remote=allow_remote
    )
    if ctx.invoked_subcommand is None:
        ctx.invoke(run)


@cli.command()
@click.pass_context
def run(ctx: click.Context) -> None:
    """Start the MCP server (default subcommand)."""
    configure_default()
    config = load_config(env_file=ctx.obj.get("env_file"))
    if ctx.obj.get("transport"):
        config.transport = ctx.obj["transport"]
    if ctx.obj.get("host"):
        config.host = ctx.obj["host"]
    if ctx.obj.get("port"):
        config.port = ctx.obj["port"]
    if ctx.obj.get("allow_remote"):
        config.allow_remote = True

    if config.transport != "stdio" and not is_loopback(config.host):
        if not config.allow_remote:
            raise click.ClickException(
                f"Refusing to serve on {config.host}: the server has no sign-in, so anyone who "
                "can reach it could use your AppLovin keys. Use 127.0.0.1, or set "
                "APPLOVIN_ALLOW_REMOTE=true (or --allow-remote) only behind your own "
                "authentication."
            )
        logger.warning(
            "binding to %s: the server has no authentication, so anyone who can reach "
            "this address can use your AppLovin keys",
            config.host,
        )

    mcp = build_server(config=config)
    if config.transport == "stdio":
        mcp.run("stdio")
    elif config.transport == "streamable-http":
        logger.info("serving streamable HTTP on http://%s:%d/mcp", config.host, config.port)
        mcp.run("streamable-http", host=config.host, port=config.port)
    elif config.transport == "sse":
        logger.warning(
            "the sse transport (HTTP+SSE) is deprecated by the MCP spec; "
            "use --transport streamable-http instead"
        )
        mcp.run("sse", host=config.host, port=config.port)
    else:
        raise click.ClickException(f"Unknown transport: {config.transport}")


@cli.command()
@click.option(
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Where to save the keys (default APPLOVIN_KEYS_FILE or ~/.applovin-mcp/secrets/keys.json).",
)
@click.pass_context
def setup(ctx: click.Context, output_path: Path | None) -> None:
    """Store your AppLovin keys for this server, owner-only (0600).

    Press Enter to keep a stored value (or skip one you don't have), or type '-'
    to remove it. Keys are read without echoing and are never printed.
    """
    configure_default()
    load_env_files(ctx.obj.get("env_file"))
    target = output_path or keys_file_path()
    stored = read_keys_file(target)
    click.echo(f"Saving to {target}")
    click.echo(
        "You only need the keys for the side you use: Ads Manager (advertising) or MAX "
        "(monetisation). See docs/setup-applovin-api.md."
    )
    values = dict(stored)
    for name, label, secret in SETUP_PROMPTS:
        state = "stored" if stored.get(name) else "not set"
        answer = click.prompt(
            f"{label} [{state}]", default="", show_default=False, hide_input=secret
        ).strip()
        if answer == "-":
            values.pop(name, None)
        elif answer:
            if name == ACCOUNT_ID_ENV and not answer.isdigit():
                raise click.ClickException("The account ID is a number.")
            if secret:
                register_secret(answer)
            values[name] = answer
    save_keys_file(target, values)
    click.echo(f"Saved {len(values)} value(s) to {target} (owner-only permissions).")
    if target.resolve() != keys_file_path().resolve():
        click.echo(f"Set APPLOVIN_KEYS_FILE={target.resolve()} so the server finds it.")
    click.echo(
        "Next: run `applovin-mcp describe`, then add the server to your MCP client. "
        "Never paste keys into chat or commit them."
    )


@cli.command()
@click.option("--yes", is_flag=True, help="Don't ask for confirmation.")
@click.option("--keep-keys", is_flag=True, help="Delete only the audit log.")
@click.pass_context
def purge(ctx: click.Context, yes: bool, keep_keys: bool) -> None:
    """Delete locally stored AppLovin data: the audit log and the stored keys."""
    load_env_files(ctx.obj.get("env_file"))
    audit = audit_log_path()
    targets = [audit, audit.with_suffix(audit.suffix + ".tmp")]
    if not keep_keys:
        targets.append(keys_file_path())
    existing = [p for p in targets if p.exists()]
    if not existing:
        click.echo("Nothing to delete.")
        return
    click.echo("Will delete:\n" + "\n".join(f"  {p}" for p in existing))
    if not yes and not click.confirm("Delete these files?", default=False):
        click.echo("Cancelled.")
        return
    for p in existing:
        p.unlink()
    click.echo(f"Deleted {len(existing)} file(s).")
    if not keep_keys:
        click.echo(
            "If you also set keys in a client config, remove them there. To revoke a key, "
            "regenerate it on the Keys page in Ads Manager or the MAX dashboard."
        )


@cli.command()
def version() -> None:
    """Print the applovin-mcp version."""
    click.echo(__version__)


@cli.command("describe")
@click.pass_context
def describe(ctx: click.Context) -> None:
    """Print the effective configuration and exit. No secrets."""
    configure_default()
    config = load_config(env_file=ctx.obj.get("env_file"))
    info = {
        "version": __version__,
        "keys_set": config.key_status(),
        "keys_file": str(config.keys_file),
        "account_id": config.account_id,
        "ads_account_type": config.ads_account_type,
        "audit_log": str(config.audit_log),
        "audit_retention_days": config.audit_retention_days,
        "dry_run": config.dry_run,
        "read_only": config.read_only,
        "tool_groups": config.tool_groups or "all",
        "bulk_threshold": config.bulk_threshold,
        "max_increase_pct": config.max_increase_pct,
        "max_daily_budget": config.max_daily_budget,
        "max_cpm": config.max_cpm,
        "upload_dir": str(config.upload_dir) if config.upload_dir else None,
        "transport": config.transport,
        "host": config.host,
        "port": config.port,
        "allow_remote": config.allow_remote,
    }
    click.echo(json.dumps(info, indent=2, ensure_ascii=False))


def main() -> None:
    try:
        cli(obj={})
    except RuntimeError as e:
        # Surface config errors cleanly instead of a Python traceback.
        click.echo(f"Error: {redact(str(e))}", err=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
