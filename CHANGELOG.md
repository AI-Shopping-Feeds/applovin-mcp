# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-06

First release.

### Added

- 42 tools, all prefixed `applovin_`, in eight groups split by side:
  - Ads Manager: `ads_campaigns`, `ads_creatives`, `ads_assets` (Campaign
    Management API v1, whitelist-only) and `ads_reporting` (app, web, asset and
    spend reports).
  - MAX: `max_reporting` (revenue, cohort, user-level), `max_ad_units` (ad units,
    networks, floors, waterfalls, experiments, test devices) and `max_ad_review`.
  - `request`: read and confirm-gated write escape hatch for any AppLovin API.
  - Meta tools: describe, health check, audit tail and lookup, dry-run.
- `APPLOVIN_TOOL_GROUPS` with `ads` and `max` shorthands, and `APPLOVIN_READ_ONLY`
  (also accepts AppLovin's `APPLOVIN_READONLY`).
- Safety: dry-run, confirm words (`DELETE`, `APPLY`), spend guards for budgets,
  cost and ROAS goals, floors and CPMs, optional caps, before-snapshots and an
  audit log with retention and `purge`.
- Guards for AppLovin's limits: client-side rate limits, no retries of
  Campaign Management 429s or of writes, batches that stop at the first failure,
  and a pause after repeated Campaign Management errors.
- `applovin-mcp setup` stores keys in `~/.applovin-mcp/secrets/keys.json` (0600).
- Key redaction in logs, errors and the audit log; masked identifiers in
  user-level revenue; capped downloads; uploads limited to `APPLOVIN_UPLOAD_DIR`.
- HTTP transports refuse non-loopback addresses unless `APPLOVIN_ALLOW_REMOTE=true`.
