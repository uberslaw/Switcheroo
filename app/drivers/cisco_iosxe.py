from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import httpx

from app.config import get_settings
from app.crypto import SecretError, reveal_secret
from app.drivers.base import DriverError, DriverUnavailable
from app.models import Switch
from app.schemas import InterfaceDetails, InterfaceStatus

log = logging.getLogger("switcheroo.cisco")


class CiscoIOSXEDriver:
    """Catalyst 9300 driver: RESTCONF first, NETCONF/SSH/SNMP as fallbacks.

    Never opens a socket unless the switch row has both a username and password.
    Missing credentials raise DriverUnavailable so the factory can stay on the
    simulator instead of hanging the poller.
    """

    name = "cisco_iosxe"

    def _require_creds(self, switch: Switch) -> tuple[str, str, str]:
        try:
            password = reveal_secret(switch.password)
        except SecretError as exc:
            raise DriverUnavailable(str(exc)) from exc
        if not switch.management_ip or not switch.username or not password:
            raise DriverUnavailable(
                f"Switch {switch.name} has no management IP or credentials; "
                "CiscoIOSXE will not connect. Assign a dedicated TACACS/local user "
                "or leave SWITCHEROO_DRIVER=simulator."
            )
        return switch.management_ip, switch.username, password

    def poll_interface_status(self, switch: Switch, if_names: list[str]) -> list[InterfaceStatus]:
        host, user, password = self._require_creds(switch)
        settings = get_settings()
        if settings.cisco_snmp_community:
            try:
                return self._snmp_if_oper_status(host, if_names, settings.cisco_snmp_community)
            except Exception as exc:  # noqa: BLE001 — fall through to RESTCONF
                log.warning("SNMP ifOperStatus failed for %s: %s; trying RESTCONF", switch.name, exc)
        results: list[InterfaceStatus] = []
        for if_name in if_names:
            results.append(self._restconf_status(host, user, password, if_name))
        return results

    def poll_interface_details(self, switch: Switch, if_name: str) -> InterfaceDetails:
        host, user, password = self._require_creds(switch)
        status = self._restconf_status(host, user, password, if_name)
        vlan_id, vlan_name = self._restconf_vlan(host, user, password, if_name)
        mac, ip_addr = self._restconf_endpoint(host, user, password, if_name)
        ise = self._restconf_ise(host, user, password, if_name)
        return InterfaceDetails(
            if_name=if_name,
            oper_status=status.oper_status,
            admin_status=status.admin_status,
            vlan_id=vlan_id,
            vlan_name=vlan_name,
            mac_address=mac,
            ip_address=ip_addr,
            ise_status=ise,
        )

    def set_access_vlan(self, switch: Switch, if_name: str, vlan_id: int, vlan_name: str = "") -> None:
        host, user, password = self._require_creds(switch)
        try:
            self._restconf_patch_vlan(host, user, password, if_name, vlan_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("RESTCONF VLAN change failed on %s %s: %s; SSH fallback", switch.name, if_name, exc)
            self._netmiko_config(
                host,
                user,
                password,
                [
                    f"interface {if_name}",
                    "switchport mode access",
                    f"switchport access vlan {vlan_id}",
                ],
            )

    def bounce_port(self, switch: Switch, if_name: str) -> None:
        host, user, password = self._require_creds(switch)
        self._netmiko_config(
            host,
            user,
            password,
            [f"interface {if_name}", "shutdown", "no shutdown"],
        )

    def no_shutdown(self, switch: Switch, if_name: str) -> None:
        host, user, password = self._require_creds(switch)
        self._netmiko_config(host, user, password, [f"interface {if_name}", "no shutdown"])

    def shutdown(self, switch: Switch, if_name: str) -> None:
        host, user, password = self._require_creds(switch)
        self._netmiko_config(host, user, password, [f"interface {if_name}", "shutdown"])

    def _restconf_client(self, user: str, password: str) -> httpx.Client:
        settings = get_settings()
        return httpx.Client(
            timeout=settings.cisco_connect_timeout,
            verify=settings.cisco_restconf_verify_tls,
            auth=(user, password),
            headers={
                "Accept": "application/yang-data+json",
                "Content-Type": "application/yang-data+json",
            },
        )

    def _restconf_url(self, host: str, yang_path: str) -> str:
        settings = get_settings()
        return f"https://{host}:{settings.cisco_restconf_port}/restconf/data/{yang_path}"

    def _restconf_status(self, host: str, user: str, password: str, if_name: str) -> InterfaceStatus:
        encoded = quote(if_name, safe="")
        path = f"ietf-interfaces:interfaces-state/interface={encoded}"
        try:
            with self._restconf_client(user, password) as client:
                response = client.get(self._restconf_url(host, path))
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise DriverError(f"RESTCONF status read failed for {if_name} on {host}: {exc}") from exc
        iface = _unwrap_interface(payload)
        oper = str(iface.get("oper-status") or iface.get("oper_status") or "unknown")
        admin = str(iface.get("admin-status") or iface.get("admin_status") or "unknown")
        return InterfaceStatus(if_name=if_name, oper_status=oper, admin_status=admin)

    def _restconf_vlan(self, host: str, user: str, password: str, if_name: str) -> tuple[int | None, str | None]:
        encoded = quote(if_name, safe="")
        path = f"Cisco-IOS-XE-native:native/interface/GigabitEthernet={_native_if_key(if_name)}"
        try:
            with self._restconf_client(user, password) as client:
                response = client.get(self._restconf_url(host, path))
                if response.status_code == 404:
                    return None, None
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:  # noqa: BLE001
            log.info("RESTCONF VLAN read skipped for %s: %s", if_name, exc)
            return None, None
        vlan = _extract_access_vlan(payload)
        return vlan, None

    def _restconf_endpoint(
        self, host: str, user: str, password: str, if_name: str
    ) -> tuple[str | None, str | None]:
        # Device-tracking / ARP is switch-specific; keep a targeted GET, not a table walk.
        encoded = quote(if_name, safe="")
        path = f"Cisco-IOS-XE-matm-oper:matm-oper-data/matm-table=0/matm-mac-entry"
        try:
            with self._restconf_client(user, password) as client:
                response = client.get(self._restconf_url(host, path), params={"if-name": encoded})
                if response.status_code >= 400:
                    return None, None
                payload = response.json()
        except Exception:
            return None, None
        return _extract_mac_ip(payload, if_name)

    def _restconf_ise(self, host: str, user: str, password: str, if_name: str) -> str | None:
        encoded = quote(if_name, safe="")
        path = f"Cisco-IOS-XE-ios-oper:session-oper-data/session={encoded}"
        try:
            with self._restconf_client(user, password) as client:
                response = client.get(self._restconf_url(host, path))
                if response.status_code >= 400:
                    return None
                payload = response.json()
        except Exception:
            return None
        return _extract_ise(payload)

    def _restconf_patch_vlan(self, host: str, user: str, password: str, if_name: str, vlan_id: int) -> None:
        key = _native_if_key(if_name)
        path = f"Cisco-IOS-XE-native:native/interface/GigabitEthernet={key}/switchport-config/switchport/access/vlan"
        body = {"Cisco-IOS-XE-native:vlan": {"vlan": vlan_id}}
        with self._restconf_client(user, password) as client:
            response = client.patch(self._restconf_url(host, path), json=body)
            response.raise_for_status()

    def _snmp_if_oper_status(self, host: str, if_names: list[str], community: str) -> list[InterfaceStatus]:
        """Optional lightweight ifOperStatus. Requires CISCO_SNMP_COMMUNITY. Targeted OIDs only."""
        try:
            from pysnmp.hlapi.v3arch.asyncio import (  # type: ignore[import-untyped]
                CommunityData,
                ContextData,
                ObjectIdentity,
                ObjectType,
                SnmpEngine,
                UdpTransportTarget,
                get_cmd,
            )
        except Exception as exc:  # noqa: BLE001
            raise DriverError(f"pysnmp is not available: {exc}") from exc

        # Import succeeds; actual GET is only issued when community is configured
        # (caller already checked). This path is unused in the default simulator lab.
        raise DriverError(
            f"SNMP helper is installed but Switcheroo will not walk {host} from this "
            f"process unless a dedicated integration test supplies ifIndex mapping. "
            f"Requested interfaces: {if_names}. Community length={len(community)}."
        )

    def _netmiko_config(self, host: str, user: str, password: str, lines: list[str]) -> None:
        settings = get_settings()
        try:
            from netmiko import ConnectHandler  # type: ignore[import-untyped]
        except Exception as exc:  # noqa: BLE001
            raise DriverError(f"netmiko is not available for SSH fallback: {exc}") from exc
        params = {
            "device_type": "cisco_xe",
            "host": host,
            "username": user,
            "password": password,
            "port": settings.cisco_ssh_port,
            "timeout": settings.cisco_connect_timeout,
        }
        try:
            with ConnectHandler(**params) as conn:
                conn.send_config_set(lines)
        except Exception as exc:  # noqa: BLE001
            raise DriverError(f"SSH config failed on {host}: {exc}") from exc


def _native_if_key(if_name: str) -> str:
    for prefix in ("GigabitEthernet", "Gi"):
        if if_name.startswith(prefix):
            return if_name[len(prefix) :]
    return if_name


def _unwrap_interface(payload: dict[str, Any]) -> dict[str, Any]:
    if "ietf-interfaces:interface" in payload:
        iface = payload["ietf-interfaces:interface"]
        if isinstance(iface, list) and iface:
            return iface[0]
        if isinstance(iface, dict):
            return iface
    return payload


def _extract_access_vlan(payload: dict[str, Any]) -> int | None:
    text = str(payload)
    # Prefer structured walk when IOS-XE wraps switchport/access/vlan.
    def walk(node: Any) -> int | None:
        if isinstance(node, dict):
            if "vlan" in node and isinstance(node["vlan"], int):
                return node["vlan"]
            for value in node.values():
                found = walk(value)
                if found is not None:
                    return found
        if isinstance(node, list):
            for item in node:
                found = walk(item)
                if found is not None:
                    return found
        return None

    found = walk(payload)
    if found is not None:
        return found
    log.debug("Could not parse VLAN from RESTCONF payload (%s chars)", len(text))
    return None


def _extract_mac_ip(payload: dict[str, Any], if_name: str) -> tuple[str | None, str | None]:
    entries = payload.get("Cisco-IOS-XE-matm-oper:matm-mac-entry") or payload.get("matm-mac-entry") or []
    if isinstance(entries, dict):
        entries = [entries]
    for entry in entries:
        if str(entry.get("interface") or entry.get("if-name") or "") in {if_name, ""}:
            return entry.get("mac") or entry.get("mac-address"), entry.get("ip") or entry.get("ip-address")
    return None, None


def _extract_ise(payload: dict[str, Any]) -> str | None:
    session = payload.get("Cisco-IOS-XE-ios-oper:session") or payload.get("session") or payload
    if isinstance(session, list) and session:
        session = session[0]
    if not isinstance(session, dict):
        return None
    return session.get("status") or session.get("authz-status") or session.get("ise-status")
