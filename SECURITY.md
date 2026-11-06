# Security

This server can change live AppLovin campaigns and spend (budgets, goals,
status, creatives), change MAX ad units, floors and waterfalls (and so ad
revenue), and read reports, including user-level revenue with device
identifiers. It acts with the AppLovin keys you give it.

## Protect your keys

- Keys live in environment variables or in `~/.applovin-mcp/secrets/keys.json`,
  which `applovin-mcp setup` writes with owner-only permissions (folder 0700,
  file 0600). Keep it that way, and never commit keys or paste them into chat.
- The audit log (`~/.applovin-mcp/logs/audit.jsonl`, 0600) holds writes, never
  keys. `applovin-mcp purge` deletes it and the stored keys.
- To revoke a key, regenerate it on the **Keys** page in Ads Manager or the MAX
  dashboard; the old key stops working at once. Do this if a machine or a key
  may be compromised.
- Give the server only the keys it needs: report keys alone for reporting, and
  `APPLOVIN_READ_ONLY=true` when nobody should make changes.
- Set `APPLOVIN_UPLOAD_DIR` to a folder that holds only creatives.

## Never expose the HTTP transport

The `streamable-http` and `sse` transports have no sign-in: anyone who can
reach the port can use your keys. The server refuses to bind to anything but
a loopback address unless you set `APPLOVIN_ALLOW_REMOTE=true` (or pass
`--allow-remote`). Only do that behind your own authentication.

## Reporting a vulnerability

Report security problems privately through a
[GitHub private security advisory](https://github.com/AI-Shopping-Feeds/applovin-mcp/security/advisories/new),
or contact the maintainers at [AI Shopping Feeds](https://github.com/AI-Shopping-Feeds).
Don't post working exploits, keys or account data in a public issue.

## Suspected misuse of AppLovin data

If you think this server exposed or misused AppLovin data (for example report
statistics or user-level identifiers), report it the same private way. If your
own keys or data were involved, also regenerate the keys and tell your AppLovin
account team.
