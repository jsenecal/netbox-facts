from typing import Generator, Dict, Any
import napalm.base.helpers
from napalm.junos import JunOSDriver

# from .utils import junos_views
from napalm.junos.utils import junos_views
from .helpers import ip_object

__all__ = ("EnhancedJunOSDriver",)


class EnhancedJunOSDriver(JunOSDriver):  # pylint: disable=abstract-method
    """Enhanced JunOSDriver."""

    def get_arp_table(self, vrf="") -> Generator[Dict[str, Any], None, None]:
        """Return the ARP table with the ip address object."""
        if vrf:
            msg = "VRF support has not been added for this getter on this platform."
            raise NotImplementedError(msg)

        # arp_table = []

        arp_table_raw = junos_views.junos_arp_table(self.device)  # type: ignore # pylint: disable=no-member
        arp_table_raw.get()
        arp_table_items = arp_table_raw.items()

        for arp_table_entry in arp_table_items:
            arp_entry = {elem[0]: elem[1] for elem in arp_table_entry[1]}
            arp_entry["mac"] = napalm.base.helpers.mac(arp_entry.get("mac"))  # type: ignore
            arp_entry["ip"] = ip_object(arp_entry.get("ip"))  # type: ignore
            yield arp_entry

        # return arp_table

    def get_ipv6_neighbors_table(self) -> Generator[Dict[str, Any], None, None]:
        """Return the IPv6 neighbors table with the ip address object."""
        ipv6_neighbors_table_raw = junos_views.junos_ipv6_neighbors_table(self.device)  # type: ignore # pylint: disable=no-member
        ipv6_neighbors_table_raw.get()
        ipv6_neighbors_table_items = ipv6_neighbors_table_raw.items()

        for ipv6_table_entry in ipv6_neighbors_table_items:
            ipv6_entry = {elem[0]: elem[1] for elem in ipv6_table_entry[1]}
            ipv6_entry["mac"] = (
                ""
                if ipv6_entry.get("mac") == "none"
                else napalm.base.helpers.mac(ipv6_entry.get("mac"))  # type: ignore
            )
            ipv6_entry["ip"] = ip_object(ipv6_entry.get("ip"))  # type: ignore
            yield ipv6_entry
