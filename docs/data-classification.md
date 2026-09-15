# Data classification

Switcheroo is an internal operations tool. This is not a consumer product and it does not implement a public “download my data” portal.

| Class | Examples | Handling |
| --- | --- | --- |
| Workplace identity | Local username, Windows `DOMAIN\sam`, display name, role | Needed to sign in and to attribute VLAN tickets. Disable the user on offboarding. Do not erase change-request history. |
| Workplace telemetry | Switch port MAC, IP, ISE state, connected uptime | Operations data for the desk. Not a DSAR product. Keep while the switch row exists. |
| Change records | VLAN/bounce requests, reasons, ServiceNow RITM/REQ, approver | Audit. Retain with the host (audit log rotation, 90-day policy for rotated files). |
| Secrets | TACACS/device password, session key, data key, SN password, Teams webhook URL | Fernet in SQLite or `.env`. Never in audit/logs. ACL `.env` and `data\`. |
| Lab only | Seed users `networks`/`cs`, simulator IPs in TEST-NET-1 | Must not remain on a shared database with README passwords. |

## Residency

The SQLite file, logs, and `data\backups\` live on the Switcheroo host. Keep that host (and backups) in the intended AU / internal location. This app does not replicate to a public cloud region.

## Access and deletion

- Networks can **disable** a user under Access. That is the offboarding control. The last active Networks user cannot be disabled.
- Disabled users cannot sign in. Change requests they already created stay in the database.
- Windows-JIT users are revoked when they leave both AD groups (re-checked on the group TTL).

## Privacy Act / GDPR

Treat usernames, Windows accounts, and port MAC/IP as workplace personal information if your org classifies them that way. Fulfil an access/delete request by exporting or disabling the local user and redacting ticket text if Legal asks — there is no automated consumer workflow.
