# AppLovin MCP Server (read + write)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) [![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

An [MCP](https://modelcontextprotocol.io) server for AppLovin, covering both sides:
**Ads Manager** (AppLovin's advertiser platform, also called Axon) through the
[Campaign Management API](https://support.applovin.com/en/growth/promoting-your-apps/api/axon-campaign-management-api)
and [Reporting API](https://support.applovin.com/en/growth/promoting-your-apps/api/reporting-api),
and **MAX** (mediation and monetisation) through the
[Revenue Reporting](https://support.applovin.com/en/max/reporting-apis/revenue-reporting-api),
[Cohort](https://support.applovin.com/en/max/reporting-apis/cohort-api),
[User-Level Ad Revenue](https://support.applovin.com/en/max/reporting-apis/user-level-ad-revenue-api)
and [Ad Unit Management](https://support.applovin.com/en/max/advanced-features/ad-unit-management-api) APIs.
Use it from Claude, ChatGPT, Cursor, Codex or any MCP client to report on spend,
ROAS and ad revenue, change budgets and goals, manage creative sets and uploads,
and tune MAX ad units, floors and waterfalls.

It's maintained by [AI Shopping Feeds](https://github.com/AI-Shopping-Feeds). It offers:

- **both sides of AppLovin in one server:** Ads Manager campaigns, creative sets, assets and reports (`applovin_ads_*`), and MAX revenue, cohort and user-level reports, ad units, waterfalls, experiments and Ad Review rules (`applovin_max_*`); turn on one side with `APPLOVIN_TOOL_GROUPS=ads` or `=max`
- **safety:** dry-run, confirm words for removals, big batches and budget, goal or CPM jumps, a local audit log with before-snapshots, read-only mode, and guards that keep you clear of AppLovin's 24-hour API block
- **an escape hatch** for any other AppLovin endpoint, reads open and writes behind `confirm="APPLY"`

AppLovin publishes its own [AppLovin MCP](https://support.applovin.com/en/growth/promoting-your-websites/api/applovin-mcp)
(Node.js, limited beta, Ads Manager campaigns and reporting). This one is Python, adds MAX,
and puts dry-run, confirm words and an audit log in front of every write; see
[the comparison](docs/terms-and-data.md#how-this-differs-from-applovins-own-mcp). Not affiliated with or endorsed by AppLovin.

Part of the AI Shopping Feeds MCP family: [Google Ads](https://github.com/AI-Shopping-Feeds/google-ads-mcp) · [Google Merchant Centre](https://github.com/AI-Shopping-Feeds/google-merchant-centre-mcp) · [Google Analytics](https://github.com/AI-Shopping-Feeds/google-analytics-mcp) · [YouTube Studio](https://github.com/AI-Shopping-Feeds/youtube-studio-mcp) · [Amazon Ads](https://github.com/AI-Shopping-Feeds/amazon-ads-mcp) · [Apple Ads](https://github.com/AI-Shopping-Feeds/apple-ads-mcp) · [Microsoft Ads](https://github.com/AI-Shopping-Feeds/microsoft-ads-mcp) · [TikTok Ads](https://github.com/AI-Shopping-Feeds/tiktok-ads-mcp) · [Pinterest Ads](https://github.com/AI-Shopping-Feeds/pinterest-ads-mcp) · [Snapchat Ads](https://github.com/AI-Shopping-Feeds/snapchat-ads-mcp) · [OpenAI Ads](https://github.com/AI-Shopping-Feeds/openai-ads-mcp) · **[AppLovin](https://github.com/AI-Shopping-Feeds/applovin-mcp)**.

## Let your agent set it up

Paste this into Claude Code, Codex, Cursor or any agent that can read web pages and edit files:

```text
Set up the AppLovin MCP server for me by following https://raw.githubusercontent.com/AI-Shopping-Feeds/applovin-mcp/main/skills/applovin-mcp/SKILL.md
```

The agent checks whether it's already connected, installs the runner if you
agree, tells you which AppLovin keys to copy for the side you use, has you
store them with `applovin-mcp setup` (so they never pass through chat), adds
the server to your client's config without touching your other servers, and
checks it works by calling `applovin_health_check`.

The same file is an [Agent Skill](https://agentskills.io), so your agent can keep it as a guide to using the tools well. For Claude Code, copy the folder into your skills:

```bash
git clone --depth 1 https://github.com/AI-Shopping-Feeds/applovin-mcp.git /tmp/applovin-mcp
cp -r /tmp/applovin-mcp/skills/applovin-mcp ~/.claude/skills/
```

Other agents that support skills load the same `skills/applovin-mcp` folder from their own skills directory.

## What it can do

42 tools, all prefixed `applovin_`. The full list is in [docs/tools.md](docs/tools.md).

### Read

| Area | Tools |
|---|---|
| Ads Manager campaigns | `ads_list_campaigns`, `ads_campaign_history` (who changed what), `ads_get_catalog_info` |
| Creative sets and assets | `ads_list_creative_sets`, `ads_list_assets` (review status, rejection reasons), `ads_get_upload_result` |
| Ads Manager reports | `ads_report` (app or web, cohort or real time), `ads_asset_report`, `ads_spend_report` (US state or metro) |
| MAX reports | `max_revenue_report`, `max_cohort_report` (revenue, impressions, sessions and retention), `max_user_revenue` |
| MAX setup | `max_list_ad_units`, `max_get_ad_unit` (networks, CPMs, floors, waterfalls, experiments), `max_list_test_devices`, `max_list_ad_review_rules` |
| Anything else | `read_request` |

### Write

| Area | Tools |
|---|---|
| Campaigns | `ads_create_campaign` (paused straight after by default), `ads_update_campaigns` (budgets, goals, targeting, dates), `ads_set_campaign_status` |
| Creative sets | `ads_create_creative_set`, `ads_update_creative_sets`, `ads_clone_creative_set`, `ads_add_creative_sets_to_campaigns`, `ads_remove_creative_sets` |
| Assets | `ads_upload_assets` (videos, images, playables from one folder), `ads_add_assets_to_creative_sets`, `ads_remove_assets` |
| MAX ad units | `max_create_ad_unit`, `max_update_ad_unit` (networks, CPMs, floors, caps), `max_create_waterfall`, `max_remove_waterfall` |
| MAX tests | `max_create_experiment`, `max_end_experiment`, `max_set_test_device`, `max_create_ad_review_rules` |
| Any endpoint | `request` |

### Safety

- **Dry-run** (`APPLOVIN_DRY_RUN=true` or `applovin_set_dry_run`) records writes and shows what would be sent, without sending.
- **Confirm words.** Removals need `confirm="DELETE"`. More than 10 items, daily budget, CPI/CPE/CPP goal, floor or CPM rises above 20%, ROAS goal cuts above 20%, values whose current figure can't be read, disabled networks, experiment promotions and raw writes need `confirm="APPLY"`. Optional caps: `APPLOVIN_MAX_DAILY_BUDGET`, `APPLOVIN_MAX_CPM`.
- **Audit log.** Every write goes to `~/.applovin-mcp/logs/audit.jsonl` (0600) with a before-snapshot, and is pruned after `APPLOVIN_AUDIT_RETENTION_DAYS` (30). `applovin-mcp purge` deletes it and the stored keys. Report rows stay in memory.
- **Read-only mode** (`APPLOVIN_READ_ONLY=true`, or AppLovin's `APPLOVIN_READONLY`) and **tool groups** (`APPLOVIN_TOOL_GROUPS=ads`, `max` or group names).
- **AppLovin's limits.** The Campaign Management API allows 1000 requests a minute and blocks an account for 24 hours after 100 failed requests in five minutes; the server checks arguments first, stops batches at the first failure and pauses those calls if errors pile up.
- **Privacy.** User-level revenue is summarised in memory with IDFA, IDFV and user IDs masked unless you ask for them. Uploads only read from `APPLOVIN_UPLOAD_DIR`.
- The HTTP transports refuse a non-loopback address unless `APPLOVIN_ALLOW_REMOTE=true`.
- What AppLovin's terms allow, and how this server follows them: [docs/terms-and-data.md](docs/terms-and-data.md).

## Supported clients

| Client | Works? | How |
|---|---|---|
| Claude Code | ✅ | Local server, `claude mcp add` (below) |
| Claude Desktop | ✅ | Local server, `claude_desktop_config.json` |
| Cursor | ✅ | Local server, `~/.cursor/mcp.json` |
| VS Code (Copilot) | ✅ | Local server, `.vscode/mcp.json` (uses `servers` instead of `mcpServers`) |
| Codex | ✅ | Local server, `~/.codex/config.toml` |
| Gemini CLI | ✅ | Local server, `~/.gemini/settings.json` |
| Windsurf and other local clients | ✅ | Local server, same JSON entry |
| Claude.ai and ChatGPT | ❌ not yet | These need a remote server with sign-in. This server's HTTP transports have no sign-in, so don't expose them publicly. Use a local client. |

## Setup

### 1. Install uv

The server runs with `uvx` from [uv](https://docs.astral.sh/uv/getting-started/installation/)
(`brew install uv` on macOS) and needs Python 3.11 or later. It installs straight
from this repository:

```shell
uvx --from git+https://github.com/AI-Shopping-Feeds/applovin-mcp.git applovin-mcp
```

Pin a release by adding `@vX.Y.Z` to the URL.

### 2. Get credentials

AppLovin uses long-lived keys, one per API. You only need the side you use.
Step by step, including who can see the keys and how to get whitelisted:
[docs/setup-applovin-api.md](docs/setup-applovin-api.md).

1. **Ads Manager (advertisers):** an Admin opens Ads Manager → account menu →
   **Keys** and copies the **Report Key** (reports) and, if AppLovin has
   whitelisted the account, the **Campaign Management API key**. The **account
   ID** is under account menu → **Go to settings**.
2. **MAX (publishers):** in the MAX dashboard, **Account → General → Keys**:
   the **Report Key** (reports), the **Management Key** (ad units) and, for Ad
   Review rules, the **Ad Review Key**.
3. **Store them** without pasting them into chat:

   ```shell
   uvx --from git+https://github.com/AI-Shopping-Feeds/applovin-mcp.git applovin-mcp setup
   ```

   This asks for each key without echoing it and saves them to
   `~/.applovin-mcp/secrets/keys.json` (owner-only). You can use environment
   variables instead; they win over the file.

### 3. Add it to your client

Replace the placeholders. Keep your other servers in each file. With keys stored
by `setup`, only the options you want need setting; this example limits the
server to MAX.

**Claude Code**

```bash
claude mcp add --scope user applovin \
  --env APPLOVIN_TOOL_GROUPS=max \
  --env APPLOVIN_DRY_RUN=true \
  -- uvx --from git+https://github.com/AI-Shopping-Feeds/applovin-mcp.git applovin-mcp
```

**Claude Desktop, Cursor, Gemini CLI, Windsurf** (and VS Code under `servers`)

```json
{
  "mcpServers": {
    "applovin": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/AI-Shopping-Feeds/applovin-mcp.git", "applovin-mcp"],
      "env": {
        "APPLOVIN_ACCOUNT_ID": "123456789",
        "APPLOVIN_UPLOAD_DIR": "/abs/path/creatives",
        "APPLOVIN_DRY_RUN": "true"
      }
    }
  }
}
```

Desktop apps often can't find `uvx` on the PATH; if the server doesn't start, use
the full path from `which uvx` as the `command`.

**Codex** (`~/.codex/config.toml`)

```toml
[mcp_servers.applovin]
command = "uvx"
args = ["--from", "git+https://github.com/AI-Shopping-Feeds/applovin-mcp.git", "applovin-mcp"]
env = { APPLOVIN_ACCOUNT_ID = "123456789", APPLOVIN_DRY_RUN = "true" }
```

### 4. Check it works

Restart or reload the client, then ask it to "check my AppLovin connection". It
should call `applovin_health_check` and show one line per key: a 401/403 on the
Campaign Management API means AppLovin hasn't whitelisted the account yet, while
reports still work. Turn dry-run off when you're ready to make changes.

## Configuration

| Variable | Required | What it does |
|---|---|---|
| `APPLOVIN_CAMPAIGN_MANAGEMENT_API_KEY` | for Ads Manager writes | Campaign Management API key (whitelist-only) |
| `APPLOVIN_ACCOUNT_ID` | with the key above | Ads Manager account ID |
| `APPLOVIN_REPORTING_API_KEY` | for Ads Manager reports | Ads Manager Report Key |
| `APPLOVIN_ADS_ACCOUNT_TYPE` | no | `app` (default) or `web`: which report endpoint `applovin_ads_report` uses by default |
| `APPLOVIN_MAX_REPORT_KEY` | for MAX reports | MAX Report Key |
| `APPLOVIN_MAX_MANAGEMENT_KEY` | for MAX ad units | MAX Management Key |
| `APPLOVIN_AD_REVIEW_KEY` | for Ad Review rules | MAX Ad Review Key |
| `APPLOVIN_KEYS_FILE` | no | Where `setup` saves keys (default `~/.applovin-mcp/secrets/keys.json`) |
| `APPLOVIN_UPLOAD_DIR` | for uploads | The only folder creative files may be uploaded from |
| `APPLOVIN_DRY_RUN` | no | `true` records writes without sending them |
| `APPLOVIN_READ_ONLY` (or `APPLOVIN_READONLY`) | no | `true` registers only read-only tools |
| `APPLOVIN_TOOL_GROUPS` | no | `ads`, `max`, or groups: `ads_campaigns`, `ads_creatives`, `ads_assets`, `ads_reporting`, `max_reporting`, `max_ad_units`, `max_ad_review`, `request`. Meta tools are always on |
| `APPLOVIN_BULK_THRESHOLD`, `APPLOVIN_MAX_INCREASE_PCT` | no | Batch size (10) and percentage change (20) above which writes need `APPLY` |
| `APPLOVIN_MAX_DAILY_BUDGET`, `APPLOVIN_MAX_CPM` | no | Optional caps in US dollars |
| `APPLOVIN_AUDIT_LOG`, `APPLOVIN_AUDIT_RETENTION_DAYS` | no | Audit log path (default `~/.applovin-mcp/logs/audit.jsonl`) and days to keep entries (30; 0 keeps them) |
| `APPLOVIN_MAX_DOWNLOAD_MB` | no | Largest response or report file held in memory (200) |
| `APPLOVIN_PAGE_SIZE`, `APPLOVIN_REPORT_PAGE_SIZE`, `APPLOVIN_TIMEOUT`, `APPLOVIN_LOG_LEVEL` | no | List page size (100), report rows per request (5000), timeout in seconds (60) and log level |
| `APPLOVIN_TRANSPORT`, `APPLOVIN_HOST`, `APPLOVIN_PORT` | no | `stdio` (default), `streamable-http` or `sse` (deprecated), and its bind address (default `127.0.0.1:8000`) |
| `APPLOVIN_ALLOW_REMOTE` | no | `true` (or `--allow-remote`) lets HTTP bind to a non-loopback address. The server has no sign-in |

Settings are read from the environment, then `./.env` (or `--env FILE`), then
`~/.applovin-mcp/.env`. A copy of every variable is in [.env.example](.env.example).

## Example prompts

```text
Show last week's spend, installs and 7-day ROAS by campaign in Ads Manager, worst first.
```

```text
Raise the daily budget of every campaign with 7-day ROAS above target by 10%. Rehearse it in dry-run first and show me the changes.
```

```text
Which MAX networks earned the most on my rewarded ad units yesterday, and how did fill rate compare by country?
```

```text
Compare day-7 retention and revenue per install for installs from the last two weeks, by country.
```

## More docs

- [All tools](docs/tools.md)
- [Getting AppLovin API keys](docs/setup-applovin-api.md)
- [Terms, data handling and the official AppLovin MCP](docs/terms-and-data.md)
- [Agent skill](skills/applovin-mcp/SKILL.md): setup and usage guide for agents
- [Changelog](CHANGELOG.md) and [security policy](SECURITY.md)

## Contributing and licence

Issues and pull requests are welcome on [this repository](https://github.com/AI-Shopping-Feeds/applovin-mcp). See
[CONTRIBUTING.md](CONTRIBUTING.md).

Licensed under the [MIT License](LICENSE).

---

Try out [AI Shopping Feeds](https://aishoppingfeeds.com/), our shopping feed management tool (including Google Shopping feeds).
