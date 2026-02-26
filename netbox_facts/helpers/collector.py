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
from netbox.plugins.utils import get_plugin_config
from ipam.models.ip import IPAddress, Prefix
from napalm.base import NetworkDriver
from napalm.base.exceptions import ConnectionException
from netbox_facts.choices import CollectionTypeChoices
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
                if ip_interface_object is None:
                    self._log_warning(
                        f"Could not determine prefix length for `{arp_entry['ip']}` "
                        f"on interface `{interface_name}`. Skipping."
                    )
                    continue

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

    def inventory(self, driver: NetworkDriver):
        """Collect inventory data from a device using get_facts()."""
        facts = driver.get_facts()
        device = self._current_device
        changes = []
        serial_changed = False

        new_serial = facts.get("serial_number", "")
        if new_serial and device.serial != new_serial:
            changes.append(f"Serial: `{device.serial}` → `{new_serial}`")
            Device.objects.filter(pk=device.pk).update(serial=new_serial)
            serial_changed = True

        os_version = facts.get("os_version", "")
        if os_version:
            changes.append(f"OS version: `{os_version}`")

        hostname = facts.get("hostname", "")
        fqdn = facts.get("fqdn", "")
        if hostname:
            changes.append(f"Hostname: `{hostname}`" + (f" (FQDN: `{fqdn}`)" if fqdn else ""))

        if serial_changed:
            JournalEntry.objects.create(
                created=self._now,
                assigned_object=device,
                kind=JournalEntryKindChoices.KIND_INFO,
                comments=f"Inventory facts collected:\n" + "\n".join(f"- {c}" for c in changes),
            )

        self._log_success("Inventory collection completed")

    def interfaces(self, driver: NetworkDriver):
        """Collect interface data from a device using get_interfaces()."""
        ifaces = driver.get_interfaces()
        device = self._current_device

        for iface_name, iface_data in ifaces.items():
            # Skip interfaces that don't match the configured regex
            if not self._interfaces_re.match(iface_name):
                continue

            mac_addr = iface_data.get("mac_address", "")
            if not mac_addr:
                continue

            # Get the matching interface from NetBox or skip
            try:
                nb_iface = device.vc_interfaces().get(name=iface_name)
            except Interface.DoesNotExist:
                self._log_warning(
                    f"Could not find interface `{iface_name}` in NetBox. Skipping."
                )
                continue

            netbox_mac, created = MACAddress.objects.get_or_create(
                mac_address=mac_addr
            )
            if created:
                netbox_mac.tags.add(AUTO_D_TAG)
                self._log_success(
                    f"Created MAC address {get_absolute_url_markdown(netbox_mac, bold=True)}."
                )

            netbox_mac.device_interface = nb_iface
            netbox_mac.discovery_method = CollectionTypeChoices.TYPE_INTERFACES
            netbox_mac.last_seen = self._now
            netbox_mac.save()

        self._log_success("Interface collection completed")

    def lldp(self, driver: NetworkDriver):
        """Collect LLDP data from a device using get_lldp_neighbors_detail()."""
        from dcim.choices import LinkStatusChoices
        from dcim.models.cables import Cable

        lldp_data = driver.get_lldp_neighbors_detail()
        device = self._current_device

        for local_iface_name, neighbors in lldp_data.items():
            # Get local interface from NetBox
            try:
                local_iface = device.vc_interfaces().get(name=local_iface_name)
            except Interface.DoesNotExist:
                self._log_warning(
                    f"Could not find local interface `{local_iface_name}` in NetBox. Skipping."
                )
                continue

            for neighbor in neighbors:
                remote_system_name = neighbor.get("remote_system_name", "")
                remote_port = neighbor.get("remote_port", "")

                if not remote_system_name or not remote_port:
                    continue

                # Look up the remote device in NetBox
                try:
                    remote_device = Device.objects.get(name=remote_system_name)
                except Device.DoesNotExist:
                    self._log_info(
                        f"Remote device `{remote_system_name}` not found in NetBox. Skipping cable creation."
                    )
                    continue

                # Only create cables between devices in the same site
                if remote_device.site_id != device.site_id:
                    self._log_info(
                        f"Remote device `{remote_system_name}` is in a different site. Skipping cable creation."
                    )
                    continue

                # Look up the remote interface
                try:
                    remote_iface = remote_device.vc_interfaces().get(name=remote_port)
                except Interface.DoesNotExist:
                    self._log_warning(
                        f"Could not find remote interface `{remote_port}` on `{remote_system_name}`. Skipping."
                    )
                    continue

                # Check that neither interface already has a cable
                if local_iface.cable_id is not None:
                    self._log_info(
                        f"Local interface `{local_iface_name}` already has a cable. Skipping."
                    )
                    continue
                if remote_iface.cable_id is not None:
                    self._log_info(
                        f"Remote interface `{remote_port}` on `{remote_system_name}` already has a cable. Skipping."
                    )
                    continue

                # Create the cable
                try:
                    cable = Cable(
                        a_terminations=[local_iface],
                        b_terminations=[remote_iface],
                        status=LinkStatusChoices.STATUS_CONNECTED,
                    )
                    cable.full_clean()
                    cable.save()

                    JournalEntry.objects.create(
                        created=self._now,
                        assigned_object=device,
                        kind=JournalEntryKindChoices.KIND_INFO,
                        comments=(
                            f"LLDP: Created cable between `{local_iface_name}` and "
                            f"`{remote_system_name}:{remote_port}`."
                        ),
                    )
                    self._log_success(
                        f"Created cable between `{local_iface_name}` and `{remote_system_name}:{remote_port}`."
                    )
                except Exception as exc:
                    self._log_warning(
                        f"Could not create cable between `{local_iface_name}` and "
                        f"`{remote_system_name}:{remote_port}`: {exc}"
                    )

        self._log_success("LLDP collection completed")

    def ethernet_switching(self, driver: NetworkDriver):
        """Collect ethernet switching data from a device using get_mac_address_table()."""
        mac_table = driver.get_mac_address_table()
        device = self._current_device

        for entry in mac_table:
            mac_addr = entry.get("mac", "")
            if not mac_addr:
                continue

            iface_name = entry.get("interface", "")
            if not iface_name:
                continue

            # Get the matching interface from NetBox or skip
            try:
                nb_iface = device.vc_interfaces().get(name=iface_name)
            except Interface.DoesNotExist:
                self._log_warning(
                    f"Could not find interface `{iface_name}` in NetBox. Skipping."
                )
                continue

            netbox_mac, created = MACAddress.objects.get_or_create(
                mac_address=mac_addr
            )
            if created:
                netbox_mac.tags.add(AUTO_D_TAG)
                self._log_success(
                    f"Created MAC address {get_absolute_url_markdown(netbox_mac, bold=True)}."
                )

            netbox_mac.interfaces.add(nb_iface)
            netbox_mac.discovery_method = CollectionTypeChoices.TYPE_L2
            netbox_mac.last_seen = self._now
            netbox_mac.save()

        self._log_success("Ethernet switching collection completed")

    def l2_circuits(self):
        """Collect L2 circuit data from devices."""
        raise NotImplementedError()

    def evpn(self):
        """Collect EVPN data from devices."""
        raise NotImplementedError()

    def bgp(self, driver: NetworkDriver):
        """Collect BGP data from a device using get_bgp_neighbors_detail()."""
        from ipam.models import ASN, RIR
        from ipam.models.vrfs import VRF

        bgp_data = driver.get_bgp_neighbors_detail()
        device = self._current_device

        for vrf_name, peers_by_as in bgp_data.items():
            # Resolve VRF (empty string or "global" means no VRF)
            nb_vrf = None
            if vrf_name and vrf_name.lower() not in ("global", "default"):
                try:
                    nb_vrf = VRF.objects.get(name=vrf_name)
                except VRF.DoesNotExist:
                    self._log_warning(
                        f"Could not find VRF `{vrf_name}` in NetBox. "
                        "Peers will be created in the global table."
                    )

            for as_number, peers in peers_by_as.items():
                # Try to get or create ASN (requires an RIR)
                nb_asn = None
                try:
                    nb_asn, _ = ASN.objects.get_or_create(
                        asn=int(as_number),
                        defaults={"rir": RIR.objects.first()},
                    )
                except (RIR.DoesNotExist, TypeError):
                    self._log_warning(
                        f"No RIR exists in NetBox. Cannot create ASN {as_number}."
                    )

                for peer in peers:
                    remote_address = peer.get("remote_address", "")
                    if not remote_address:
                        continue

                    # Create IP address as /32 (IPv4) or /128 (IPv6)
                    try:
                        ip_obj = ipaddress.ip_address(remote_address)
                        prefix_len = 32 if ip_obj.version == 4 else 128
                        ip_str = f"{remote_address}/{prefix_len}"
                    except ValueError:
                        self._log_warning(
                            f"Invalid IP address `{remote_address}`. Skipping."
                        )
                        continue

                    nb_ip, created = IPAddress.objects.get_or_create(
                        address=ip_str,
                        vrf=nb_vrf,
                        defaults={
                            "description": f"BGP peer AS{as_number} discovered on {self._now}",
                        },
                    )
                    if created:
                        nb_ip.tags.add(AUTO_D_TAG)
                        JournalEntry.objects.create(
                            created=self._now,
                            assigned_object=nb_ip,
                            kind=JournalEntryKindChoices.KIND_INFO,
                            comments=(
                                f"BGP peer discovered by {get_absolute_url_markdown(device, bold=True)}: "
                                f"AS{as_number} remote address `{remote_address}`"
                                + (f" in VRF `{vrf_name}`" if nb_vrf else "")
                                + "."
                            ),
                        )
                        self._log_success(
                            f"Created peer IP {get_absolute_url_markdown(nb_ip, bold=True)} (AS{as_number})."
                        )
                    else:
                        self._log_info(
                            f"Found existing peer IP {get_absolute_url_markdown(nb_ip, bold=True)}."
                        )

        self._bgp_routing_integration()
        self._log_success("BGP collection completed")

    def _bgp_routing_integration(self):
        """Stub for future netbox-routing BGPSession integration (Task 11)."""
        pass

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
            except ValueError:
                self._log_warning(
                    "Device has no primary IP address configured. Skipping."
                )
                continue

            try:
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
