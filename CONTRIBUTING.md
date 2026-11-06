# Contributing

Thanks for helping improve the AppLovin MCP server.

## Development setup

```bash
git clone https://github.com/AI-Shopping-Feeds/applovin-mcp.git
cd applovin-mcp
uv venv && uv pip install -e ".[test,dev]"
```

Or with a plain virtual environment: `python -m venv .venv`, activate it, then
`pip install -e ".[test,dev]"`.

To try it against a real account, run `.venv/bin/applovin-mcp setup` and keep
`APPLOVIN_DRY_RUN=true` until you're sure.

## Checks

```bash
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src/applovin_mcp
.venv/bin/pytest
```

Tests run offline: HTTP is mocked with `respx` and every key is fake. They never
touch a real AppLovin account. `pre-commit install` runs the same checks on commit.

## Pull requests

- Keep keys, account IDs, report data and personal paths out of commits and
  issues. Use placeholders such as `123456789` and `/abs/path/...`.
- New tools go in the matching module under `src/applovin_mcp/tools/`, use the
  `applovin_ads_` or `applovin_max_` prefix (by side), and need an entry in
  `tool_catalog.py` (title and annotations) and a row in `docs/tools.md`. The
  tests fail otherwise.
- Every request goes through `AppLovinClient.request()`, so dry-run, the audit
  log, rate limits and redaction apply. Writes that remove things need
  `confirm="DELETE"`; money changes go through `safety.assess()`.
- Never paste AppLovin's documentation, API specs, sample code or SDK code into
  the repository; their EULA restricts copying it. Write short summaries in
  your own words and link to the page.
- Update the README and CHANGELOG when tools or setup change.
