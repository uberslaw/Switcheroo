# ISO 27001 Statement of Applicability (mapping only)

This is **not** an ISO 27001 certificate and not an ISMS. It maps Annex A themes to what Switcheroo actually does so Cyber can see coverage, host work, and residual risk. Continual improvement is the in-app **Help → Security checklist** plus [security.md](security.md) staying in sync with code.

## Information assets

- SQLite database (`data\switcheroo.db`) — inventory, users, requests, rack layout
- `.env` — session key, data key, integration credentials
- `data\audit.log` and `data\switcheroo.log`
- Device/TACACS secrets (Fernet blobs)
- WinSW service configuration and Launch Control

## Annex A mapping (summary)

| Theme | In the app | On the host | Residual |
| --- | --- | --- | --- |
| A.5 Policies | [security.md](security.md), this SoA, runbook | Org acceptable-use / backup policy | Org must own the paper policy |
| A.8 Assets | Classification in [data-classification.md](data-classification.md) | Inventory the Windows host | — |
| A.8.10 Deletion | User disable; no silent wipe of tickets | Secure delete of retired disks | No automated retention job |
| A.8.13 Backup | `scripts\Backup-Switcheroo.ps1` (SQLite backup API) | Schedule + off-box copy if Cyber requires | Operator must run and test restore |
| A.8.24 Crypto | scrypt passwords; Fernet device secrets; TLS at IIS | BitLocker | No HSM / in-process TLS |
| A.5.15 / A.5.17 Access | Roles CS/Networks; grants server-side; Windows/AD groups; disable user | IIS Windows Auth; NTFS ACLs | Entra OIDC not implemented |
| A.5.16 Identity | Local users or JIT from `DOMAIN\sam` | AD as source of truth when `AUTH=windows` | Break-glass local login is optional |
| A.8.15 Logging | JSON-lines audit; optional JSON app log; `/ready` | Central log ship if the org has one | No SIEM connector |
| A.5.24 Incident | [runbook.md](runbook.md) | Org incident process | No 24/7 on-call product |
| A.5.19 / A.5.21 Vendors | ServiceNow, Teams, Cisco RESTCONF, WinSW, pinned PyPI | Vendor reviews | Dry-run until dedicated accounts exist |
| A.8.8 Tech vulns | `pip-audit` in GitHub Actions; Dependabot; pinned `requirements.txt` | OS patching | Operator applies Windows updates |
| A.8.9 Config | `SWITCHEROO_REQUIRE_HARDENED` fail-fast | IIS / firewall | Lab mode is not production |
| A.8.31 Dev/test | pytest in CI; lab vs hardened `.env` | — | No separate staging farm |
| A.5.33 Records | Audit rotation; 90-day note | Host retention | — |
| A.8.11 / residency | On-host SQLite and backups | Keep host/backups in AU/internal | No cloud region |

## Vendors and libraries

- **ServiceNow** — Table API / catalog; dedicated integration user; dry-run default
- **Microsoft Teams** — webhook URL is a secret; host allowlist
- **Cisco IOS-XE** — RESTCONF/SSH; dedicated TACACS user
- **WinSW** — MIT; service wrapper
- **PyPI pins** — FastAPI, SQLAlchemy, cryptography, httpx, Jinja2, openpyxl, netmiko, … (see `requirements.txt`)

## Risk notes (honest)

Single Windows host and single SQLite file are the architecture. Disk/OS compromise yields the database; `SWITCHEROO_DATA_KEY` still protects device passwords. Networks can set any management IP (trusted role). The app must not sit on the public internet.

Management review and corrective actions are an organisational process, not a feature in this repository.
