# Terms, data handling and the official AppLovin MCP

This page summarises what AppLovin's terms say about using its APIs through a
tool like this one, how the server follows them, and what is still open. It is
a reading of public documents, not legal advice. Read the documents yourself,
and ask AppLovin or a lawyer if something matters to your business.

Checked on 6 November 2026.

## The documents

AppLovin has no separate API licence. API use is covered by:

| Document | Version seen | Applies to |
|---|---|---|
| [Terms of Use Agreement](https://legal.applovin.com/terms/) | revision of 18 June 2026, effective 14 July 2026 | Everyone using the platform, Ads Manager (axon.ai, ads.axon.ai) and the MAX dashboard |
| [End User License Agreement — Software](https://legal.applovin.com/eula/) | effective 18 December 2025 | SDKs, **APIs**, documentation and sample code |
| [Policies for Demand Partners](https://legal.applovin.com/policies-demand-partners/) | updated 18 June 2026 | Advertisers and agencies (Ads Manager) |
| [Policies for Publishers](https://legal.applovin.com/policies-publishers/) | updated 22 September 2026 | App publishers (MAX) |
| [Data Sharing Requirements](https://legal.applovin.com/data-sharing-requirements/) | current | Anyone sharing user data with AppLovin |
| [AppLovin MCP page](https://support.applovin.com/en/growth/promoting-your-websites/api/applovin-mcp) | current | AppLovin's own MCP server (context for AI use) |

## What they require, and what this server does

| Term | What it says | How this server follows it |
|---|---|---|
| Keys are credentials | You "should not disclose your account credentials to anyone" (Terms §1). The official MCP page says to revoke a leaked key at once. | Keys are read from env vars or a 0600 file in a 0700 folder, sent only to AppLovin's hosts, registered for redaction, and never logged, returned or written to the audit log. `applovin-mcp setup` reads them without echoing. `purge` deletes them. |
| No sharing your account | "You may not authorize others to use your account" (Terms §1). | Documented for agencies below. The HTTP transports refuse non-loopback addresses unless you opt in, so a server can't quietly become a shared endpoint. |
| Confidential information | AppLovin Confidential Information includes "any statistics provided by AppLovin concerning your use of the Platform", "the existence of, and information about, beta features" and pricing (Terms §7). You may not disclose it without consent, except to legal or financial advisers. | Report rows are kept in memory only, never written to disk or the audit log. The server instructions tell the agent not to share AppLovin statistics or pricing outside the user's organisation unless the user confirms it's allowed. |
| Information derived from the platform | You may not make available "any information derived from the Platform or the Services" to third parties (Terms §3). | Same as above. See flag (a) about AI providers. |
| Pricing | "All pricing information, including bidding-related information" is confidential (Demand Partner and Publisher Policies). | Same. CPMs, floors, bids and revenue share are covered by the instruction line. |
| Data use limits | You "must not collect or use any data received through the Services" beyond permitted purposes (Demand Partner Policies, Privacy). | The server only reads what the user asks for, for their own account. |
| Impression-level data | Publishers "must obtain legally sufficient consent from your end users before combining" impression-level data with personal data (Publisher Policies, Privacy). | `applovin_max_user_revenue` summarises in memory, masks IDFA, IDFV, user IDs and custom data unless asked, never writes the file to disk, and never returns the signed download link. The tool description and the skill say not to merge it with other personal data without consent. |
| Software and documentation | The EULA's "Software" includes APIs, "documentation, tools and assemblies, libraries, scripts, object code, sample source code"; no copying or derivative works without authorisation. | The repo contains no AppLovin documentation text, specs, sample code or SDK code; only endpoint paths, parameter names and short summaries needed to call the APIs, written in our own words. AppLovin's official MCP package was looked at only to compare features, never copied. |
| Brand | The Terms (§3) give no rights in AppLovin's "trademarks, logos, and other intellectual property"; the [brand guidelines](https://www.applovin.com/en/brand) cover logo use. | No logos. The name is used only to say what the server works with, and the README says "Not affiliated with or endorsed by AppLovin." |
| Responsibility | On the official MCP page: "You’re responsible for all actions taken against your account through this tool." | Dry-run, confirm words, spend guards, before-snapshots and the audit log exist so a person approves every change. |
| Rate limits | Campaign Management API: 1000 requests per 60 s; 100 errors in five minutes blocks the account for 24 hours. Ad Unit Management API: 2000 requests an hour. | Client-side limits below those numbers, no retries of 429s on the Campaign Management API, writes never retried, local argument checks, batches stop at the first failure, and calls pause after 40 failures in five minutes. |
| Data retention | No specific API retention rule found. | The audit log holds writes only, is pruned after `APPLOVIN_AUDIT_RETENTION_DAYS` (default 30), and `applovin-mcp purge` deletes it. |
| Hosts | Not addressed. | Only `api.ads.axon.ai`, `r.applovin.com`, `o.applovin.com`, `api-safedk.applovin.com` and, for user-level report files, the signed AppLovin storage link (HTTPS, AppLovin or Amazon S3 hosts only, size-capped). |

## Flags

### (a) Using it with AI clients such as Claude

**Complicates, doesn't clearly block.** The Terms treat AppLovin's statistics
about your account, and pricing, as AppLovin Confidential Information, and bar
making information derived from the platform available to third parties. When
report rows go into a hosted AI model, the model provider receives them. The
terms don't mention AI tools either way.

On the other side, AppLovin itself publishes the AppLovin MCP for "Claude
Desktop, Claude Code, Cursor" and suggests prompts like pulling last week's
ROAS, so AppLovin evidently expects report data to reach AI assistants through
its own tool. It hasn't said the same about third-party tools.

What to do: use an AI client under business terms that don't train on your
inputs, keep outputs inside your organisation, and if it matters, ask your
AppLovin account team to confirm in writing. User-level data is the most
sensitive part, so it is masked by default.

### (b) A public repository with "AppLovin" in its name

**Complicates; the maintainer should decide.**

- The Terms grant no trademark rights, and the brand guidelines only cover
  logos. Using the name to describe compatibility ("an MCP server for AppLovin")
  is the usual nominative use, and the README carries a non-affiliation line.
- **Name clash:** AppLovin's official server is published on npm as
  `applovin-mcp`, with a command also called `applovin-mcp`. This repository,
  its Python package and its command use the same name. Users could confuse the
  two, and AppLovin could object to the overlap more readily than to the brand
  name alone. Before making this public, consider a distinct name (for example
  `applovin-ads-max-mcp`, or "AI Shopping Feeds AppLovin MCP" as the display
  name) or ask AppLovin.
- The Campaign Management API pages are public but not linked from AppLovin's
  documentation menu, and the API is described as a whitelist-only beta.
  "Information about beta features" counts as confidential under the Terms,
  but AppLovin has made this API public itself (its official MCP page and npm
  package describe it), so the docs here only repeat what AppLovin publishes.
  If AppLovin withdraws those pages, review this.

### (c) Agencies using it for clients

**Complicates.** The Terms count "ad agencies" as Advertisers, require
Advertisers to have "the legal authority and authorization to act on behalf of
the owner" (§12), say you may not authorize others to use your account, and say not
to disclose credentials. Keys are per account and only Admins can see them.

## Agencies and acting for clients

- Get each client's written authorisation before managing their AppLovin account.
- Prefer being added as a user on the client's account (AppLovin's
  [Adding users](https://support.applovin.com/en/growth/introduction/adding-users))
  over receiving keys by email or chat.
- Run one server entry per client, each with its own keys file
  (`APPLOVIN_KEYS_FILE`) and audit log (`APPLOVIN_AUDIT_LOG`), so data and keys
  never mix.
- Don't combine one client's AppLovin statistics with another's, or share them
  outside the client, without permission.

## How this differs from AppLovin's own MCP

AppLovin publishes the **AppLovin MCP** ([setup page](https://support.applovin.com/en/growth/promoting-your-websites/api/applovin-mcp),
npm package `applovin-mcp`, version 0.0.8 on 13 August 2026, MIT licence; the
GitHub repository named in the package isn't public). It's the
vendor-supported choice if you only need Ads Manager.

| | This server | AppLovin MCP |
|---|---|---|
| Runs | Locally, Python 3.11+ via `uvx` | Locally, Node.js 22+ via `npx` |
| Status | Independent, open source (MIT) | AppLovin's own, limited beta |
| Ads Manager campaigns, creative sets, assets | Yes (needs the whitelisted Campaign Management API) | Yes (same API, same whitelist) |
| Ads Manager reports | App, web, asset and spend reports | Advertiser, asset and playable reports |
| MAX revenue, cohort, user-level reports | Yes | No |
| MAX ad units, waterfalls, experiments, Ad Review | Yes | No |
| Read-only switch | `APPLOVIN_READ_ONLY` (also accepts `APPLOVIN_READONLY`) | `APPLOVIN_READONLY` |
| Dry-run | Yes | No |
| Confirm words and spend guards | Yes | No |
| Audit log with before-snapshots | Local file, pruned after 30 days | Upload attempts logged to stderr |
| Tool groups | Yes (`ads`, `max`, per group) | Picks APP or WEB tools automatically |
| Upload safety | Only from one folder you choose | Blocks common secret locations |
| Escape hatch for other endpoints | Yes | No |

## API versions and deprecations

| API | Base URL | Version |
|---|---|---|
| Campaign Management | `https://api.ads.axon.ai/manage/v1` | v1 (whitelist-only beta) |
| Ads Manager reporting | `https://r.applovin.com` (`/report`, `/webReport`, `/assetReport`, `/assetAnalyticsReport`, `/spend-report`) | unversioned |
| MAX reporting | `https://r.applovin.com` (`/maxReport`, `/maxCohort`, `/max/userAdRevenueReport`) | unversioned |
| MAX Ad Unit Management | `https://o.applovin.com/mediation/v1` | v1 |
| MAX Ad Review rules | `https://api-safedk.applovin.com/v1/rules/...` | v1 |

No sunset dates were announced. Deprecations designed around:

- **`ad` and `ad_id` report columns** are deprecated for app campaigns in favour
  of `ad_creative_type`, `creative_set` and `placement_type`.
  `applovin_ads_report` refuses them for app reports.
- **`msa_names` targeting** was renamed `metro_names` and "will soon be
  deprecated"; the campaign tools refuse `msa_names`.
- **`frequency_cap_settings`** on MAX networks is deprecated; use
  `frequency_capping_settings`.
- The **Spend Reporting API** page isn't in AppLovin's menu yet; the tool notes
  that it may change.

## Community reference

[FurkanEmiroglu/Applovin-Max-MCP-Server](https://github.com/FurkanEmiroglu/Applovin-Max-MCP-Server)
(Go, MIT, described by its author as a proof of concept) covers MAX revenue and
cohort reports. It was used only as a pointer to which endpoints exist; no code
was taken. This server checks every endpoint against AppLovin's documentation,
sends management keys in headers, and redacts the query-string report key from
logs and errors (AppLovin's reporting endpoints only accept it as `api_key=`).

## What isn't verified

No live AppLovin keys were used to build this server. Everything was built from
AppLovin's public documentation and tested against mocked responses. In
particular, the exact error AppLovin returns to a non-whitelisted account, the
JSON shape of the Ads Manager report responses, and how the Campaign Management
API treats `status` on create (the docs say it's ignored, so the server pauses
new campaigns straight after) should be checked with a real account.
