# Switcheroo runbook

Internal site. One Windows service, one SQLite file. If Launch Control health is red, start here — do not wait for a CS ticket.

## What is down?

| Check | Meaning |
| --- | --- |
| `GET /health` fails | Process is not serving HTTP. Service stopped, bind wrong, or crash loop. |
| `/health` ok, `/ready` not ok | App is up but SQLite or the poller heartbeat is unhealthy. |
| Site ok, polls stale | Driver/credentials/network to the Catalyst, not the website. |
| VLAN tickets missing in ServiceNow | Integration is dry-run or the SN account failed. Local request still exists. |

Launch Control (`scripts\Switcheroo-LaunchControl.cmd`) shows service state, PID, log tail, and `/health` latency.

## Process will not start

1. Read `data\switcheroo.log` and `data\Switcheroo.err.log`.
2. Hardened mode refuses lab secrets, missing `SWITCHEROO_DATA_KEY`, HTTP public URL, `OPEN_ACCESS=true`, or Windows auth without AD groups / a safe bind. The process exits with a one-line reason.
3. Confirm `.venv\Scripts\python.exe -m app` from `C:\Switcheroo` after `.env` is in place.
4. Elevated: `Restart-Service Switcheroo`.

## Restore from backup

RTO: minutes (stop service, restore file, start). RPO: last successful backup (run `scripts\Backup-Switcheroo.ps1` daily, plus before a deploy).

1. Elevated: `Stop-Service Switcheroo`.
2. `.\scripts\Restore-Switcheroo.ps1 -Path data\backups\switcheroo-YYYYMMDDThhmmssZ.db`
3. `Start-Service Switcheroo`
4. Confirm `/health` and `/ready`.

Never `Copy-Item` a live `data\switcheroo.db`. The backup script uses the SQLite backup API.

## Windows / IIS sign-in fails

- Browser must be domain-joined (or have a Kerberos ticket).
- IIS must set `X-Remote-User` from `AUTH_USER`.
- Uvicorn must stay on `127.0.0.1`. A spoofed header from the LAN is ignored.
- User must be in `SWITCHEROO_AD_NETWORKS_GROUPS` or `SWITCHEROO_AD_CS_GROUPS`.
- See [windows-auth.md](windows-auth.md).

## Integrations

ServiceNow and Teams default to dry-run. A live POST failure is logged and does **not** drop the local request. `/ready` reports `off` / `dry-run` / `live` without secrets.

Diagnostics: Launch Control → Diagnostics ON → reproduce → `data\diagnostics.log`. Turn it off afterwards.

## Rollback a bad deploy

1. Stop the service.
2. `git checkout` the last known tag or commit.
3. If that release migrated SQLite, restore the matching backup first.
4. Start the service. Confirm `/health` and a sign-in.

Lab vs shared is `.env` (`SWITCHEROO_REQUIRE_HARDENED`), not a second farm.

## Operator onboarding

1. Read [README.md](../README.md) Assumptions and [architecture.md](architecture.md).
2. Lab: `python -m app` on loopback; users `networks` / `cs`.
3. Shared host: [security.md](security.md) checklist, [windows-auth.md](windows-auth.md), BitLocker + NTFS ACLs on `.env` and `data\`.
4. Help → Security checklist in the running app (no secret values).
5. Do not put Switcheroo on the public internet.
