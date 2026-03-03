import re
from typing import Generator, Dict, Any

import napalm.base.helpers
from napalm.junos import JunOSDriver

from .utils import junos_views
from .helpers import ip_object

__all__ = ("EnhancedJunOSDriver",)


class EnhancedJunOSDriver(JunOSDriver):  # pylint: disable=abstract-method
    """Enhanced JunOSDriver with richer interface data and regex filtering."""

    def get_arp_table(self, vrf="") -> Generator[Dict[str, Any], None, None]:
        """Return the ARP table with the ip address object."""
        if vrf:
            msg = "VRF support has not been added for this getter on this platform."
            raise NotImplementedError(msg)

        arp_table_raw = junos_views.junos_arp_table(self.device)
        arp_table_raw.get()
        arp_table_items = arp_table_raw.items()

        for arp_table_entry in arp_table_items:
            arp_entry = {elem[0]: elem[1] for elem in arp_table_entry[1]}
            arp_entry["mac"] = napalm.base.helpers.mac(arp_entry.get("mac"))
            arp_entry["ip"] = ip_object(arp_entry.get("ip"))
            yield arp_entry

    def get_ipv6_neighbors_table(self) -> Generator[Dict[str, Any], None, None]:
        """Return the IPv6 neighbors table with the ip address object."""
        ipv6_neighbors_table_raw = junos_views.junos_ipv6_neighbors_table(self.device)
        ipv6_neighbors_table_raw.get()
        ipv6_neighbors_table_items = ipv6_neighbors_table_raw.items()

        for ipv6_table_entry in ipv6_neighbors_table_items:
            ipv6_entry = {elem[0]: elem[1] for elem in ipv6_table_entry[1]}
            ipv6_entry["mac"] = (
                ""
                if ipv6_entry.get("mac") == "none"
                else napalm.base.helpers.mac(ipv6_entry.get("mac"))
            )
            ipv6_entry["ip"] = ip_object(ipv6_entry.get("ip"))
            yield ipv6_entry

    def get_interfaces(self, interface_name=None):
        """Return interfaces details, optionally filtered by interface regex.

        When *interface_name* is provided it is passed directly to the Junos
        RPC as ``interface_name`` which accepts shell-style globs and Junos
        regex (e.g. ``'[riafgxel][reto!im]*'``).

        Returns the standard NAPALM dict keyed by interface name, but each
        entry is enriched with ``link_mode``, ``source_filtering``, ESI fields,
        and a ``logical_interfaces`` sub-dict.
        """
        result = {}

        physical = junos_views.junos_iface_table(self.device)
        if interface_name:
            physical.get(interface_name=interface_name)
        else:
            physical.get()

        for iface_entry in physical.items():
            iface = iface_entry[0]
            iface_data = {elem[0]: elem[1] for elem in iface_entry[1]}

            mac_raw = iface_data.get("mac_address")
            mac = napalm.base.helpers.convert(
                napalm.base.helpers.mac, mac_raw, ""
            )

            match_mtu = re.search(r"(\w+)", str(iface_data.get("mtu") or ""))
            mtu = napalm.base.helpers.convert(int, match_mtu.group(0), 0) if match_mtu else 0

            speed = -1.0
            match = re.search(r"(\d+|[Aa]uto)(\w*)", iface_data.get("speed") or "")
            if match and match.group(1).lower() == "auto":
                match = re.search(r"(\d+)(\w*)", iface_data.get("negotiated_speed") or "")
            if match:
                speed_value = napalm.base.helpers.convert(float, match.group(1), -1.0)
                if speed_value != -1.0:
                    if match.group(2).lower() == "gbps":
                        speed_value *= 1000.0
                    speed = speed_value

            entry = {
                "is_up": iface_data.get("is_up", False),
                "is_enabled": True if iface_data.get("is_enabled") is None else iface_data["is_enabled"],
                "description": iface_data.get("description") or "",
                "last_flapped": float(iface_data.get("last_flapped") or -1),
                "mac_address": mac,
                "speed": speed,
                "mtu": mtu,
                "link_mode": iface_data.get("link_mode") or "",
                "source_filtering": iface_data.get("source_filtering") or "",
                "esi_value": iface_data.get("esi_value") or "",
                "esi_mode": iface_data.get("esi_mode") or "",
            }

            # Parse nested logical interfaces
            logical_raw = iface_data.get("logical_interfaces")
            if logical_raw:
                entry["logical_interfaces"] = self._parse_logical_interfaces(logical_raw)

            result[iface] = entry

        return result

    @staticmethod
    def _parse_logical_interfaces(logical_raw):
        """Parse logical interface table data into a dict.

        *logical_raw* is a PyEZ nested Table instance.  We iterate via
        ``table.items()`` which yields ``(key, [(field, val), ...])``
        tuples — the same pattern used by the ARP / NDP getters.
        """
        logical = {}
        for li_entry in logical_raw.items():
            liface = li_entry[0]
            liface_data = {elem[0]: elem[1] for elem in li_entry[1]}

            lentry = {
                "description": liface_data.get("description") or "",
                "encapsulation": liface_data.get("encapsulation") or "",
                "vrf": liface_data.get("vrf") or "",
                "esi_value": liface_data.get("esi_value") or "",
                "esi_mode": liface_data.get("esi_mode") or "",
            }

            # Parse address families
            family_raw = liface_data.get("family")
            if family_raw:
                families = {}
                for fam_entry_raw in family_raw.items():
                    fname = fam_entry_raw[0]
                    fdata = {elem[0]: elem[1] for elem in fam_entry_raw[1]}
                    fam_entry = {
                        "mtu": fdata.get("mtu"),
                        "ae_bundle": fdata.get("ae_bundle") or "",
                    }
                    addr_raw = fdata.get("addresses")
                    if addr_raw:
                        addresses = {}
                        for addr_entry_raw in addr_raw.items():
                            adest = addr_entry_raw[0]
                            adata = {elem[0]: elem[1] for elem in addr_entry_raw[1]}
                            addresses[adest] = {
                                "local": adata.get("local") or "",
                                "broadcast": adata.get("broadcast") or "",
                                "preferred": bool(adata.get("preferred")),
                                "primary": bool(adata.get("primary")),
                            }
                        fam_entry["addresses"] = addresses
                    families[fname] = fam_entry
                lentry["families"] = families

            logical[liface] = lentry
        return logical
