# Windows sign-in and AD groups

Shared Switcheroo hosts authenticate with **IIS Windows Authentication** (Kerberos / Negotiate) and map two AD security groups to the existing roles. The Python process never terminates Kerberos and never sees a password.

Lab first-run stays `SWITCHEROO_AUTH=local`.

## Flow

1. The browser on a domain-joined PC opens `https://switcheroo.internal.example`.
2. IIS requires Windows Authentication and terminates TLS.
3. IIS reverse-proxies HTTP to `127.0.0.1:8080` and sets `X-Remote-User` from `AUTH_USER` (`DOMAIN\sam`).
4. Switcheroo trusts that header **only** when the peer is loopback, or when `SWITCHEROO_TRUST_X_FORWARDED_FOR=true` behind an IIS site that overwrites both the identity header and `X-Forwarded-For`.
5. AD group membership is resolved (`win32net` on Windows, or LDAP if `SWITCHEROO_AD_LDAP_URL` is set).
6. `SWITCHEROO_AD_NETWORKS_GROUPS` → Networks. `SWITCHEROO_AD_CS_GROUPS` → Client Services. Membership in both is Networks.
7. A local `users` row is created or updated (JIT). The password hash is unusable. Offboarding in AD is picked up on the next group TTL (default 5 minutes).

People not in either group get no account and cannot sign in.

## IIS sketch

- Site HTTPS certificate on the Switcheroo hostname.
- Windows Authentication enabled; Anonymous Authentication off for the site.
- Reverse proxy (ARR / HttpPlatformHandler) to `http://127.0.0.1:8080`.
- Server variable: `HTTP_X_REMOTE_USER` = `{AUTH_USER}`.
- SPN on the IIS machine account: `HTTP/switcheroo.internal.example`.

Do not bind Switcheroo to `0.0.0.0` in Windows mode unless the host firewall only allows IIS and `TRUST_X_FORWARDED_FOR` is on. A LAN client that can reach uvicorn can send a fake `X-Remote-User`.

## Settings

```
SWITCHEROO_AUTH=windows
SWITCHEROO_AUTH_WINDOWS_HEADER=X-Remote-User
SWITCHEROO_AD_NETWORKS_GROUPS=CORP\Switcheroo-Networks
SWITCHEROO_AD_CS_GROUPS=CORP\Switcheroo-CS
SWITCHEROO_AD_GROUP_TTL_SECONDS=300
SWITCHEROO_ALLOW_LOCAL_LOGIN=false
SWITCHEROO_HOST=127.0.0.1
SWITCHEROO_REQUIRE_HARDENED=true
```

`SWITCHEROO_ALLOW_LOCAL_LOGIN=true` keeps `/login` as break-glass for the bootstrap Networks user. Leave it false on a hardened Windows host unless Cyber wants a local emergency account.

## Tickets

VLAN tickets store the **signed-in** `DOMAIN\sam`, not the WinSW service account.

## Sign out

Integrated Windows Authentication will send the user again on the next request. The Sign out button is hidden for Windows-JIT users. Close the browser (or use a private window) to switch accounts.

## Tests

Automated tests inject a fake group resolver. They never talk to a domain controller.
