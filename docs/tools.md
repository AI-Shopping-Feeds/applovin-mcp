# All tools

**42 tools: 37 across 8 groups + 5 meta tools.**

Groups are the modules in `src/applovin_mcp/tools`; `APPLOVIN_TOOL_GROUPS`
selects some of them. `ads` is shorthand for every `ads_*` group (Ads Manager,
the advertiser side) and `max` for every `max_*` group (MAX, the publisher
side). `APPLOVIN_READ_ONLY=true` keeps only the tools marked *read* below. The
meta tools are always registered.

"Kind" comes from each tool's MCP annotations: *read* changes nothing in the
account, *write* creates or updates, *destructive* removes something from
serving (or, for `applovin_request`, may do anything).

## Confirm words

| Change | Needs |
|---|---|
| Removing creative sets or assets from campaigns or sets, removing a waterfall, discarding an experiment | `confirm="DELETE"` |
| More than `APPLOVIN_BULK_THRESHOLD` items (default 10) | `confirm="APPLY"` |
| A daily budget, CPI/CPE/CPP goal, bid floor or network CPM rising more than `APPLOVIN_MAX_INCREASE_PCT` (default 20%), or whose current value couldn't be read | `confirm="APPLY"` |
| A ROAS goal falling more than `APPLOVIN_MAX_INCREASE_PCT` (a lower target lets AppLovin spend more) | `confirm="APPLY"` |
| A budget above `APPLOVIN_MAX_DAILY_BUDGET` or a CPM above `APPLOVIN_MAX_CPM` (if set) | `confirm="APPLY"` |
| Disabling a MAX network or network line, or leaving lines out of a network's list | `confirm="APPLY"` |
| Promoting a MAX experiment | `confirm="APPLY"` |
| Any write through `applovin_request` | `confirm="APPLY"` |

Without the right word a guarded write returns `refused: true`, the reasons and
the money values it would change, and sends nothing. In dry-run mode nothing is
sent anyway, so guards don't block; the preview says which confirm a real run
would need.

## Meta (always on)

| Tool | Kind | What it does |
|---|---|---|
| `applovin_describe_server` | read (local) | Which keys are set (never their values), enabled sides and groups, safety settings, paths |
| `applovin_health_check` | read | One cheap read per configured API; shows a whitelist problem on the Campaign Management API |
| `applovin_audit_tail` | read (local) | Recent writes and dry-run rehearsals from the audit log |
| `applovin_audit_lookup` | read (local) | One audit entry: request, response and before-snapshot |
| `applovin_set_dry_run` | local setting | Turn dry-run on or off for this session |

## Ads Manager (`ads_*`)

The campaign, creative set and asset tools use the Campaign Management API
(`https://api.ads.axon.ai/manage/v1`), which is **whitelist-only** (limited
beta). Without access they fail with a 401/403 that says how to ask for it. The
reporting tools use the Report Key and work for every Ads Manager account.

### `ads_campaigns`

| Tool | Kind | What it does |
|---|---|---|
| `applovin_ads_list_campaigns` | read | Campaigns, by ID, external ID, status or name |
| `applovin_ads_create_campaign` | write | Create an APP or WEB campaign; paused straight after by default |
| `applovin_ads_update_campaigns` | write | Budgets, goal values, targeting, dates, name, status (up to 50, with progress) |
| `applovin_ads_set_campaign_status` | write | Pause (`PAUSED`) or resume (`LIVE`) campaigns |
| `applovin_ads_campaign_history` | read | Who changed what, with before/after values (last 30 days) |
| `applovin_ads_get_catalog_info` | read | Catalogs and variant sets for dynamic web ads |

### `ads_creatives`

| Tool | Kind | What it does |
|---|---|---|
| `applovin_ads_list_creative_sets` | read | Creative sets, by ID, external ID or campaign |
| `applovin_ads_create_creative_set` | write | Create a creative set from uploaded assets |
| `applovin_ads_update_creative_sets` | write | Status, countries, languages, assets (up to 50) |
| `applovin_ads_clone_creative_set` | write | Copy a set into another campaign (paused by default) |
| `applovin_ads_add_creative_sets_to_campaigns` | write | Add sets (up to 50) to campaigns (up to 20) |
| `applovin_ads_remove_creative_sets` | destructive | Take sets out of some or all campaigns (`DELETE`) |

### `ads_assets`

| Tool | Kind | What it does |
|---|---|---|
| `applovin_ads_list_assets` | read | Uploaded assets with review status and rejection reasons |
| `applovin_ads_upload_assets` | write | Upload files from `APPLOVIN_UPLOAD_DIR`; waits for processing with progress |
| `applovin_ads_get_upload_result` | read | Status of an upload, per file |
| `applovin_ads_add_assets_to_creative_sets` | write | Add active assets (up to 10) to sets (up to 50) |
| `applovin_ads_remove_assets` | destructive | Take assets out of some or all sets (`DELETE`) |

### `ads_reporting`

| Tool | Kind | What it does |
|---|---|---|
| `applovin_ads_report` | read | App (`/report`, 45 days) or web (`/webReport`, 90 days) performance; pages with progress |
| `applovin_ads_asset_report` | read | Asset-level impressions, clicks and cost |
| `applovin_ads_spend_report` | read | Daily spend by US state or metro area (365 days) |

## MAX (`max_*`)

### `max_reporting`

| Tool | Kind | What it does |
|---|---|---|
| `applovin_max_revenue_report` | read | Estimated MAX revenue, impressions, eCPM, fill by app, ad unit, network, country... |
| `applovin_max_cohort_report` | read | Revenue, impressions or sessions/retention by install day |
| `applovin_max_user_revenue` | read | User- or impression-level revenue for one app and day, summarised; device IDs masked |

### `max_ad_units`

| Tool | Kind | What it does |
|---|---|---|
| `applovin_max_list_ad_units` | read | Active ad units, optionally with networks and floors |
| `applovin_max_get_ad_unit` | read | One ad unit, segment waterfall or experiment in full |
| `applovin_max_create_ad_unit` | write | Create an ad unit |
| `applovin_max_update_ad_unit` | write | Networks and CPMs, bid floors, frequency caps, refresh (guarded) |
| `applovin_max_create_waterfall` | write | Add a segment waterfall |
| `applovin_max_remove_waterfall` | destructive | Remove a segment waterfall (`DELETE`) |
| `applovin_max_create_experiment` | write | Start an A/B test on an ad unit |
| `applovin_max_end_experiment` | destructive | Promote (`APPLY`) or discard (`DELETE`) an experiment |
| `applovin_max_list_test_devices` | read | Test devices |
| `applovin_max_set_test_device` | write | Add or change a test device |

### `max_ad_review`

| Tool | Kind | What it does |
|---|---|---|
| `applovin_max_list_ad_review_rules` | read | Competitor or risky-content rules |
| `applovin_max_create_ad_review_rules` | write | Create rules in one request |

## Escape hatch (`request`)

| Tool | Kind | What it does |
|---|---|---|
| `applovin_read_request` | read | GET any endpoint on an AppLovin API (for example the HTML Metrics API) |
| `applovin_request` | destructive | Any method on an AppLovin API; writes need `APPLY` |

The target is picked by name (`ads`, `ads_reporting`, `max_reporting`, `max`,
`ad_review`), so only AppLovin's own hosts can be reached. The key and the Ads
Manager account ID are added by the server and can't be overridden. A side
that isn't enabled in `APPLOVIN_TOOL_GROUPS` can't be reached here either.

## Keys each tool needs

| Groups | Key (environment variable) |
|---|---|
| `ads_campaigns`, `ads_creatives`, `ads_assets` | `APPLOVIN_CAMPAIGN_MANAGEMENT_API_KEY` and `APPLOVIN_ACCOUNT_ID` |
| `ads_reporting` | `APPLOVIN_REPORTING_API_KEY` |
| `max_reporting` | `APPLOVIN_MAX_REPORT_KEY` |
| `max_ad_units` | `APPLOVIN_MAX_MANAGEMENT_KEY` |
| `max_ad_review` | `APPLOVIN_AD_REVIEW_KEY` |
