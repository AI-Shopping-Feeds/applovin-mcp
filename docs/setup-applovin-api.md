# Getting AppLovin API keys

AppLovin has no developer app to register and no OAuth sign-in for these APIs.
Each API uses a long-lived **key** copied from the AppLovin dashboard. This page
explains which keys exist, who can see them, and how to store them for this
server. It takes about ten minutes, plus however long AppLovin takes to
whitelist an account for the Campaign Management API.

You only need the keys for the side you use:

| You want to... | Side | Keys |
|---|---|---|
| Report on Ads Manager campaigns (spend, installs, ROAS) | Ads Manager | Report Key |
| Change Ads Manager campaigns, creative sets and assets | Ads Manager | Campaign Management API key + account ID (whitelist-only) |
| Report on MAX revenue, cohorts, user-level revenue | MAX | Report Key (MAX dashboard) |
| Manage MAX ad units, floors, waterfalls, experiments | MAX | Management Key |
| Manage MAX Ad Review rules | MAX | Ad Review Key |

Ads Manager and the MAX dashboard can be separate AppLovin accounts with their
own keys, which is why the server keeps them in separate settings.

## Ads Manager (advertisers)

### 1. Make sure you can see the keys

Sign in to Ads Manager. The **Keys** page is only visible to account
**Admins**. If you aren't one, ask an Admin to give you the role or to copy
the keys for you. AppLovin's guide:
[Adding users](https://support.applovin.com/en/growth/introduction/adding-users).

### 2. Copy the Report Key

Account menu (top right) → **Keys**. The **Report Key** works with the
Reporting API for every account. Set it as `APPLOVIN_REPORTING_API_KEY`.

If your campaigns promote websites rather than apps, also set
`APPLOVIN_ADS_ACCOUNT_TYPE=web` so `applovin_ads_report` uses the web report.

### 3. Campaign Management API key (whitelist-only)

The Campaign Management API is in **limited beta and whitelist-only**. To use
it, contact your AppLovin account team and ask for Campaign Management API
access for your account. AppLovin doesn't publish how long this takes. Once
enabled, the **Campaign Management API key** appears on the same **Keys** page.
It is different from MAX's Management Key.

Set it as `APPLOVIN_CAMPAIGN_MANAGEMENT_API_KEY`.

Until the account is whitelisted, the campaign, creative set and asset tools
answer with a 401/403 that says so; `applovin_health_check` shows it too.
Reports keep working.

### 4. Find the account ID

Account menu → **Go to settings**. The account ID is the number after the
account name. Set it as `APPLOVIN_ACCOUNT_ID` (it isn't a secret).

## MAX (publishers)

In the MAX dashboard, open **Account → General → Keys**:

- **Report Key** → `APPLOVIN_MAX_REPORT_KEY` (revenue, cohort and user-level reports)
- **Management Key** → `APPLOVIN_MAX_MANAGEMENT_KEY` (ad units, waterfalls, experiments, test devices)
- **Ad Review Key** → `APPLOVIN_AD_REVIEW_KEY` (only for Ad Review rules)

If your MAX account and Ads Manager account are the same AppLovin login and
show one Report Key, you can use the same value for both report settings.

## Store the keys

Run this once and paste each key when asked. Input isn't echoed, and you can
press Enter to skip a key you don't have:

```shell
uvx --from git+https://github.com/AI-Shopping-Feeds/applovin-mcp.git applovin-mcp setup
```

The keys are saved to `~/.applovin-mcp/secrets/keys.json`, readable only by
you (folder 0700, file 0600). Run `applovin-mcp describe` to see which keys
the server found (it never prints them).

Alternatives: set the same names as environment variables in your MCP client's
config, or in `~/.applovin-mcp/.env`. Environment variables win over the keys
file.

Never paste keys into chat, a ticket or a git repository. Anyone with a key can
read, and with the management keys change, your account.

## Check it works

Ask your agent to "check my AppLovin connection", or call
`applovin_health_check`. It makes one cheap read per configured key:

- `ok: true`: the key works.
- `ok: false` on `ads` with 401/403: not whitelisted yet, wrong key, or the
  account ID doesn't match the key.
- `ok: false` on a report key with 401: copy the key again.
- `ok: null`: that key isn't set (fine if you don't use that side).

## Revoking a key

Regenerate it on the same **Keys** page in Ads Manager or the MAX dashboard;
the old value stops working. Then run `applovin-mcp setup` again, or
`applovin-mcp purge` to delete the stored keys and the audit log.

## Limits to know

- Campaign Management API: 1000 requests a minute; going over blocks requests
  for ten minutes. 100 failed requests in five minutes blocks the API for **24
  hours** (each rejected asset in an upload counts). The server stops batches
  at the first failure and pauses these calls after 40 failures in five minutes.
- Ad Unit Management API: 2000 requests an hour.
- Report windows: Ads Manager app reports and MAX reports cover the last 45
  days, web reports 90 days, the spend report 365 days, campaign history 30 days.
  User-level revenue for a day is ready 8 hours after the day ends (UTC).

## Agencies

Keys belong to one AppLovin account. If you manage several clients, keep one
keys file per client (`APPLOVIN_KEYS_FILE`) and one server entry per client,
and get each client's authorisation first. See
[terms-and-data.md](terms-and-data.md#agencies-and-acting-for-clients).
