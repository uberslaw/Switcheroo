# Teams channel alerts (VLAN-change pending)

Switcheroo is where Networks click **Approve** or **Reject**. A Microsoft Teams incoming webhook (Workflows, or the older Incoming Webhook connector) is the **pager**: when a VLAN change stays on the queue, the Networks channel gets a card with a link to the Requests page. Nobody has to keep the Switcheroo console open.

Bounce, refresh, and troubleshooting **do not** post to Teams. Auto-approved VLAN changes also skip the alert (there is nothing to accept or reject).

This is the same dry-run-first pattern as ServiceNow. Defaults never open a socket to Microsoft.

## What to request / create

| Item | Value |
| --- | --- |
| Destination | A Networks-owned Teams **channel** (not a personal chat) |
| Auth | Channel webhook URL. Treat it as a **secret** (the `sig` query parameter is a credential). Never commit it. |
| Protocol | HTTPS POST of a JSON card |
| Preferred | **Workflows**: channel → Workflows → *Post to a channel when a webhook request is received* |
| Fallback | Classic Incoming Webhook connector, if your tenant still has it (`TEAMS_WEBHOOK_FORMAT=messagecard`) |

The Switcheroo host must be able to reach the webhook hostname (typically `*.logic.azure.com`, `*.environment.api.powerplatform.com`, or `*.webhook.office.com`).

## Card contents

Title: **A VLAN change request has been generated**

Body: **Open this link to accept or reject. Click I'm on it first so nobody doubles up.**

Buttons:

- **I'm on it** → `{SWITCHEROO_PUBLIC_URL}/requests/{id}/ack`
- **Open request page** → `{SWITCHEROO_PUBLIC_URL}/requests?status=pending#request-{id}`

Facts include request id, requester, switch, office, port, and from → to VLAN. Networks can Approve & run / Reject on that page (same actions as the approval queue).

## Acknowledgement (so nobody doubles up)

Incoming webhooks **cannot rewrite** the original channel card after it is posted (that needs a Teams bot). The pattern that still works inside the channel:

1. First person clicks **I'm on it** on the card (or on `/requests`).
2. After sign-in they confirm. Switcheroo records who claimed it.
3. A **follow-up card** posts to the same channel: *VLAN request #N acknowledged — {name} is handling this — no need to pick it up.*
4. The Requests page and approval queue show **On it: {username}**.
5. If they cannot finish, **Release** clears the claim and posts that the request is available again.

A second Networks user who tries to acknowledge gets *Already acknowledged by …*. Approve/reject still work if someone has to finish another person's claim.

The ack page is `/requests/{id}/ack`. Unauthenticated clicks from Teams are sent to login and then back to that page.

## Going live

1. Create the Workflows webhook in the Networks channel and copy the URL into `.env` on the Switcheroo host only.
2. Set `SWITCHEROO_PUBLIC_URL` to the URL people can open from Teams (internal hostname or reverse-proxy URL — not `127.0.0.1` unless that is actually how they reach it).
3. `TEAMS_ENABLED=true`
4. `TEAMS_DRY_RUN=false`
5. Leave `TEAMS_WEBHOOK_FORMAT=adaptive` for Workflows. Use `messagecard` only for a classic Incoming Webhook.
6. Restart Switcheroo. Startup **fails fast** if live mode is on and the webhook URL or public URL is missing, or if the webhook host is not a Teams/Power Automate endpoint.
7. Create one lab VLAN request (with auto-approve off) and confirm the channel card appears; click **I'm on it**, then confirm a follow-up "acknowledged" card; approve or reject.

Dry-run payloads (no HTTP) land in `data/teams-dryrun/` and `data/switcheroo.log`. The webhook URL is never written to those files — only the hostname.

A Teams outage **does not** block the local VLAN request; the failure is logged and Networks can still use `/requests`.
