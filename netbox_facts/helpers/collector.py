"""Runner class for collection jobs."""

import ipaddress
import re
from itertools import groupby
from typing import TYPE_CHECKING, Generator, Tuple, Type, List, Dict, Any

from django.utils import timezone
from dcim.models.device_components import Interface
from dcim.models.devices import Device
from extras.choices import JournalEntryKindChoices
from extras.models.models import JournalEntry
from extras.plugins.utils import get_plugin_config
from ipam.models.ip import IPAddress, Prefix
from napalm.base import NetworkDriver
from napalm.base.exceptions import ConnectionException
from netbox_facts.exceptions import CollectionError
from netbox_facts.helpers.napalm import (
    get_network_instances_by_interface,
    parse_network_instances,
)
from netbox_facts.helpers.netbox import (
    get_absolute_url_markdown,
    get_primary_ip,
    resolve_napalm_interfaces_ip_addresses,
    resolve_napalm_network_instances,
)
from netbox_facts.models.mac import MACAddress
from netbox_facts.napalm.junos import EnhancedJunOSDriver

if TYPE_CHECKING:
    from netbox_facts.models.collection_plan import CollectionPlan

AUTO_D_TAG = "Automatically Discovered"


class NapalmCollector:
    """Class to run collection jobs."""

    # TODO Implement live status updates
    # https://github.com/netbox-community/netbox/compare/develop...JCWasmx86:netbox:progress_in_scripts

    def __init__(self, plan) -> None:
        self.plan: CollectionPlan = plan
        self._collector_type = plan.collector_type
        self._napalm_args = plan.get_napalm_args()
        self._napalm_driver: Type[NetworkDriver] | None = None
        self._napalm_username = get_plugin_config(
            "netbox_facts", "napalm_username", "netbox"
        )
        self._napalm_password = get_plugin_config(
            "netbox_facts", "napalm_password", "netbox"
        )
        self._interfaces_re = re.compile(
            get_plugin_config("netbox_facts", "valid_interfaces_re")
        )
        self._devices = Device.objects.none()
        self._current_device: Device | None = None
        self._log_prefix = ""
        self._now = timezone.now()

        # Get the NAPALM driver
        try:
            self._napalm_driver = plan.get_napalm_driver()
        except Exception as exc:  # pylint: disable=broad-except
            raise CollectionError(
                f"There was an error initializing the napalm driver: {exc}"
            ) from exc

        # Get the devices to collect from
        self._devices = plan.get_devices_queryset()

    def _get_network_instances(
        self, driver: NetworkDriver
    ) -> Generator[Tuple[str, dict], None, None]:
        """Get network instances organized by interface from a device."""
        return get_network_instances_by_interface(
            resolve_napalm_network_instances(
                parse_network_instances(driver.get_network_instances())
            )
        )

    def _ip_neighbors(
        self,
        driver: NetworkDriver | EnhancedJunOSDriver,
        table: Generator[Dict[str, Any], None, None],
    ):
        """Manage IPv4 and IPv6 neighbors from a device."""
        # Shuffles the network instances into a dict with interface names as keys
        network_instances = dict(self._get_network_instances(driver))

        interfaces_ip = dict(
            resolve_napalm_interfaces_ip_addresses(
                driver.get_interfaces_ip(), network_instances
            )
        )
        table_as_list = list(table)
        for interface_name, arp_data in groupby(
            table_as_list, key=lambda x: x["interface"]
        ):
            # Skip interfaces that don't match the configured regex
            if not self._interfaces_re.match(interface_name):
                continue

            # Get the matching interface from NetBox or skip this interface if it doesn't exist
            try:
                netbox_interface = self._current_device.vc_interfaces().get(  # pylint: disable=no-member # type: ignore
                    name=interface_name
                )
            except Interface.DoesNotExist:  # pylint: disable=no-member
                arp_data = list(arp_data)
                message = f"Could not find interface `{interface_name}` in NetBox for "

                if len(arp_data) > 5:
                    message += f"{len(list(arp_data))} ARP entries"

                else:
                    message += ", ".join(
                        f"`{arp_entry['ip']} ({arp_entry['mac']})`"
                        for arp_entry in arp_data
                    )
                self._log_warning(message)
                continue

            # Get the IP information for this interface
            interface_ip_data = interfaces_ip.get(interface_name, {})

            # Iterate over ARP entries for this interface
            for arp_entry in arp_data:
                if arp_entry["mac"] == "":
                    # Skip incomplete ARP entries
                    continue
                if arp_entry.get("state", "") == "unreachable":
                    # Skip unreachable ARP entries
                    continue

                # Build a proper ip_interface_object from the IP and prefix length
                ip_interface_object = None
                routing_instance = None
                netbox_prefix_qs = Prefix.objects.none()
                for data in interface_ip_data.values():
                    routing_instance = data.get("netbox_vrf", False)

                    if routing_instance is False:
                        self._log_warning(
                            f"Could not find a VRF named `{data.get('routing_instance_name')}`"
                            + f" in NetBox for interface `{interface_name}`."
                        )
                        continue

                    if arp_entry["ip"] in data["ip_interface_object"].network:
                        ip_interface_object = ipaddress.ip_interface(
                            f"{arp_entry['ip']}/{data['prefix_length']}"
                        )
                        routing_instance = data.get("netbox_vrf")
                        netbox_prefix_qs = data.get("netbox_prefixes")
                        break
                assert ip_interface_object is not None

                if not netbox_prefix_qs.exists():
                    message = (
                        f"Could not find a NetBox prefix for `{arp_entry['ip']}` "
                        + f"on interface `{interface_name}`"
                    )
                    self._log_warning(
                        message + "."
                        if routing_instance is None
                        else message + f" in VRF `{routing_instance}`."
                    )
                    continue

                netbox_mac, created = MACAddress.objects.get_or_create(
                    mac_address=arp_entry["mac"]
                )
                if created:
                    netbox_mac.tags.add(AUTO_D_TAG)
                    self._log_success(
                        f"Succesfully created MAC address {get_absolute_url_markdown(netbox_mac, bold=True)}."
                    )
                else:
                    self._log_info(
                        f"Found existing MAC address {get_absolute_url_markdown(netbox_mac, bold=True)}."
                    )

                netbox_mac.interfaces.add(netbox_interface)

                # Get or create an IPAddress for this entry

                (
                    netbox_address,
                    created,
                ) = IPAddress.objects.get_or_create(
                    vrf=routing_instance,
                    address=str(ip_interface_object),
                    defaults={
                        "description": f"Automatically discovered on {self._now}",
                    },
                )  # pylint: disable=no-member
                if created:
                    JournalEntry.objects.create(
                        created=self._now,
                        assigned_object=netbox_address,
                        kind=JournalEntryKindChoices.KIND_INFO,
                        comments=(
                            f"Dicovered by {self._current_device} with MAC"
                            + f" {get_absolute_url_markdown(netbox_mac, bold=True)}"
                            + f" on interface {get_absolute_url_markdown(netbox_interface, bold=True)} via"
                            + f" {self.plan.get_collector_type_display()} collection."  # type: ignore
                        ),
                    )
                    netbox_address.tags.add(AUTO_D_TAG)
                    self._log_success(
                        f"Succesfully created IP address {get_absolute_url_markdown(netbox_address, bold=True)}."
                    )
                else:
                    self._log_info(
                        f"Found existing IP address {get_absolute_url_markdown(netbox_address, bold=True)}."
                    )

                # Add the IPAddress to the MACAddress
                netbox_mac.ip_addresses.add(netbox_address)

                # Update the last seen timestamp
                netbox_mac.last_seen = self._now
                netbox_mac.save()
                self._log_success(
                    f"Succesfully updated {get_absolute_url_markdown(netbox_mac, bold=True)} found on "
                    + f"{get_absolute_url_markdown(netbox_interface, bold=True)} with IP address"
                    + get_absolute_url_markdown(netbox_address, bold=True)
                    + "."
                )
        # TODO Mark stale IP addresses as deprecated

    def arp(self, driver: NetworkDriver | EnhancedJunOSDriver):
        """Collect ARP table data from a device."""
        arp_table = driver.get_arp_table()

        self._ip_neighbors(driver, arp_table)  # type: ignore
        self._log_success("ARP collection completed")

    def ndp(self, driver: NetworkDriver | EnhancedJunOSDriver):
        """Collect NDP data from devices."""
        ndp_table = driver.get_ipv6_neighbors_table()

        self._ip_neighbors(driver, ndp_table)  # type: ignore
        self._log_success("IPv6 Neighbor Discovery collection completed")

    def inventory(self):
        """Collect inventory data from devices."""
        raise NotImplementedError()

    def interfaces(self):
        """Collect interface data from devices."""
        raise NotImplementedError()

    def lldp(self):
        """Collect LLDP data from devices."""
        raise NotImplementedError()

    def ethernet_switching(self):
        """Collect ethernet switching data from devices."""
        raise NotImplementedError()

    def l2_circuits(self):
        """Collect L2 circuit data from devices."""
        raise NotImplementedError()

    def evpn(self):
        """Collect EVPN data from devices."""
        raise NotImplementedError()

    def bgp(self):
        """Collect BGP data from devices."""
        raise NotImplementedError()

    def ospf(self):
        """Collect OSPF data from devices."""
        raise NotImplementedError()

    def execute(self):
        """Execute the collection job."""

        assert self._napalm_driver is not None

        for device in self._devices:
            self._current_device = device
            self._log_prefix = get_absolute_url_markdown(device, bold=True)

            self._log_info(
                f"Starting {self.plan.get_collector_type_display()} collection"  # type: ignore
            )

            try:
                primary_ip = get_primary_ip(self._current_device)
                with self._napalm_driver(
                    primary_ip,
                    self._napalm_username,
                    self._napalm_password,
                    optional_args=self._napalm_args,
                ) as driver:
                    # Lookup the collection method and call it
                    getattr(self, self._collector_type)(driver)
            except AttributeError as exc:
                raise NotImplementedError from exc
            except ConnectionException:
                self._log_failure("An error occurred while connecting to the device")

    def _log_debug(self, message):
        """Log a message at DEBUG level."""
        self.plan.log_debug(f"{self._log_prefix} {message}".strip())

    def _log_success(self, message):
        """Log a message at SUCCESS level."""
        self.plan.log_success(f"{self._log_prefix} {message}".strip())

    def _log_info(self, message):
        """Log a message at INFO level."""
        self.plan.log_info(f"{self._log_prefix} {message}".strip())

    def _log_warning(self, message):
        """Log a message at WARNING level."""
        self.plan.log_warning(f"{self._log_prefix} {message}".strip())

    def _log_failure(self, message):
        """Log a message at ERROR level."""
        self.plan.log_failure(f"{self._log_prefix} {message}".strip())
