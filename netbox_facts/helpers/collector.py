"""Runner class for collection jobs."""

from __future__ import annotations

import ipaddress
import re
from itertools import groupby
from typing import TYPE_CHECKING, Generator, Tuple, Type, List, Dict, Any

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from dcim.models.device_components import Interface
from dcim.models.devices import Device
from extras.choices import JournalEntryKindChoices
from extras.models.models import JournalEntry
from netbox.plugins.utils import get_plugin_config
from ipam.models.ip import IPAddress, Prefix
from napalm.base import NetworkDriver
from napalm.base.exceptions import ConnectionException
from netbox_facts.choices import (
    CollectionTypeChoices,
    EntryActionChoices,
    EntryStatusChoices,
    ReportStatusChoices,
)
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
    from netbox_facts.models.facts_report import FactsReport, FactsReportEntry

AUTO_D_TAG = "Automatically Discovered"


try:
    import netbox_routing  # noqa: F401
    HAS_NETBOX_ROUTING = True
except ImportError:
    HAS_NETBOX_ROUTING = False


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
        self._report: FactsReport | None = None
        self._detect_only: bool = getattr(plan, "detect_only", False)

        # Get the NAPALM driver
        try:
            self._napalm_driver = plan.get_napalm_driver()
        except Exception as exc:  # pylint: disable=broad-except
            raise CollectionError(
                f"There was an error initializing the napalm driver: {exc}"
            ) from exc

        # Get the devices to collect from
        self._devices = plan.get_devices_queryset()

    def _should_apply(self) -> bool:
        """Return True if mutations should be performed (detect_only is False)."""
        return not self._detect_only

    def _record_entry(
        self,
        action: str,
        collector_type: str,
        device: Device,
        detected_values: dict,
        current_values: dict | None = None,
        object_instance=None,
        object_repr: str = "",
    ) -> "FactsReportEntry | None":
        """Create a FactsReportEntry. Returns the entry or None if no report."""
        if self._report is None:
            return None

        from netbox_facts.models.facts_report import FactsReportEntry

        ct = None
        obj_id = None
        if object_instance is not None and hasattr(object_instance, "pk") and object_instance.pk:
            ct = ContentType.objects.get_for_model(object_instance)
            obj_id = object_instance.pk

        entry = FactsReportEntry.objects.create(
            report=self._report,
            action=action,
            status=EntryStatusChoices.STATUS_PENDING,
            collector_type=collector_type,
            device=device,
            object_type=ct,
            object_id=obj_id,
            object_repr=object_repr,
            detected_values=detected_values,
            current_values=current_values or {},
        )
        return entry

    def _mark_entry_applied(self, entry, object_instance=None):
        """Mark an entry as applied, optionally updating its GenericFK."""
        if entry is None:
            return
        entry.status = EntryStatusChoices.STATUS_APPLIED
        entry.applied_at = timezone.now()
        update_fields = ["status", "applied_at"]
        if object_instance is not None and hasattr(object_instance, "pk") and object_instance.pk:
            entry.object_type = ContentType.objects.get_for_model(object_instance)
            entry.object_id = object_instance.pk
            update_fields.extend(["object_type", "object_id"])
        entry.save(update_fields=update_fields)

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

                # Determine action for MAC
                existing_mac = MACAddress.objects.filter(mac_address=arp_entry["mac"]).first()
                mac_action = EntryActionChoices.ACTION_CONFIRMED if existing_mac else EntryActionChoices.ACTION_NEW

                # Determine action for IP
                existing_ip = IPAddress.objects.filter(
                    address=str(ip_interface_object), vrf=routing_instance
                ).first()
                ip_action = EntryActionChoices.ACTION_CONFIRMED if existing_ip else EntryActionChoices.ACTION_NEW

                vrf_name = str(routing_instance) if routing_instance else None
                detected = {
                    "mac": arp_entry["mac"],
                    "ip": str(ip_interface_object),
                    "interface": interface_name,
                    "vrf": vrf_name,
                }

                # Record MAC entry
                mac_entry = self._record_entry(
                    action=mac_action,
                    collector_type=self._collector_type,
                    device=self._current_device,
                    detected_values=detected,
                    object_instance=existing_mac,
                    object_repr=f"MAC {arp_entry['mac']}",
                )

                # Record IP entry
                ip_entry = self._record_entry(
                    action=ip_action,
                    collector_type=self._collector_type,
                    device=self._current_device,
                    detected_values=detected,
                    object_instance=existing_ip,
                    object_repr=f"IP {ip_interface_object}",
                )

                if self._should_apply():
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

                    # Mark entries as applied with correct object references
                    self._mark_entry_applied(mac_entry, netbox_mac)
                    self._mark_entry_applied(ip_entry, netbox_address)
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
        detected = {
            "serial_number": new_serial,
            "os_version": facts.get("os_version", ""),
            "hostname": facts.get("hostname", ""),
            "fqdn": facts.get("fqdn", ""),
        }
        current = {
            "serial_number": device.serial,
        }

        if new_serial and device.serial != new_serial:
            action = EntryActionChoices.ACTION_CHANGED
            changes.append(f"Serial: `{device.serial}` → `{new_serial}`")
            serial_changed = True
        elif new_serial:
            action = EntryActionChoices.ACTION_CONFIRMED
        else:
            action = EntryActionChoices.ACTION_CONFIRMED

        inv_entry = self._record_entry(
            action=action,
            collector_type=self._collector_type,
            device=device,
            detected_values=detected,
            current_values=current,
            object_instance=device,
            object_repr=f"Device {device.name}",
        )

        if self._should_apply():
            if serial_changed:
                Device.objects.filter(pk=device.pk).update(serial=new_serial)
            self._mark_entry_applied(inv_entry, device)

        os_version = facts.get("os_version", "")
        if os_version:
            changes.append(f"OS version: `{os_version}`")

        hostname = facts.get("hostname", "")
        fqdn = facts.get("fqdn", "")
        if hostname:
            changes.append(f"Hostname: `{hostname}`" + (f" (FQDN: `{fqdn}`)" if fqdn else ""))

        if serial_changed and self._should_apply():
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

            existing_mac = MACAddress.objects.filter(mac_address=mac_addr).first()
            action = EntryActionChoices.ACTION_CONFIRMED if existing_mac else EntryActionChoices.ACTION_NEW
            detected = {
                "interface": iface_name,
                "mac_address": mac_addr,
                "is_enabled": iface_data.get("is_enabled"),
                "speed": iface_data.get("speed"),
                "mtu": iface_data.get("mtu"),
                "is_up": iface_data.get("is_up"),
            }

            iface_entry = self._record_entry(
                action=action,
                collector_type=self._collector_type,
                device=device,
                detected_values=detected,
                object_instance=existing_mac or nb_iface,
                object_repr=f"Interface {iface_name} MAC {mac_addr}",
            )

            if self._should_apply():
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
                self._mark_entry_applied(iface_entry, netbox_mac)

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
                remote_chassis_id = neighbor.get("remote_chassis_id", "")

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

                detected = {
                    "local_interface": local_iface_name,
                    "remote_device": remote_system_name,
                    "remote_interface": remote_port,
                    "remote_chassis_id": remote_chassis_id,
                }

                lldp_entry = self._record_entry(
                    action=EntryActionChoices.ACTION_NEW,
                    collector_type=self._collector_type,
                    device=device,
                    detected_values=detected,
                    object_repr=f"Cable {local_iface_name} ↔ {remote_system_name}:{remote_port}",
                )

                if self._should_apply():
                    # Create the cable
                    try:
                        cable = Cable(
                            a_terminations=[local_iface],
                            b_terminations=[remote_iface],
                            status=LinkStatusChoices.STATUS_CONNECTED,
                        )
                        cable.full_clean()
                        cable.save()
                        cable.tags.add(AUTO_D_TAG)

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
                        self._mark_entry_applied(lldp_entry, cable)
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

            if not self._interfaces_re.match(iface_name):
                continue

            # Get the matching interface from NetBox or skip
            try:
                nb_iface = device.vc_interfaces().get(name=iface_name)
            except Interface.DoesNotExist:
                self._log_warning(
                    f"Could not find interface `{iface_name}` in NetBox. Skipping."
                )
                continue

            existing_mac = MACAddress.objects.filter(mac_address=mac_addr).first()
            action = EntryActionChoices.ACTION_CONFIRMED if existing_mac else EntryActionChoices.ACTION_NEW
            detected = {
                "mac": mac_addr,
                "interface": iface_name,
                "vlan": entry.get("vlan"),
            }

            l2_entry = self._record_entry(
                action=action,
                collector_type=self._collector_type,
                device=device,
                detected_values=detected,
                object_instance=existing_mac,
                object_repr=f"MAC {mac_addr} on {iface_name}",
            )

            if self._should_apply():
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
                self._mark_entry_applied(l2_entry, netbox_mac)

        self._log_success("Ethernet switching collection completed")

    def _get_vendor_method(self, method_name):
        """
        Get vendor-specific implementation based on NAPALM driver.

        To add support for a new vendor:
        1. Implement a method named _{method_name}_{vendor}(self, driver)
        2. Add the driver name to the vendor_map below

        Example for adding EOS support for l2_circuits:
            def _l2_circuits_eos(self, driver):
                ...
            # Then add to vendor_map: 'eos': f'_{method_name}_eos'
        """
        vendor_map = {
            "junos": f"_{method_name}_junos",
            "netbox_facts.napalm.junos": f"_{method_name}_junos",
        }
        driver_name = self.plan.napalm_driver
        impl_name = vendor_map.get(driver_name)
        if impl_name and hasattr(self, impl_name):
            return getattr(self, impl_name)
        supported = [k for k, v in vendor_map.items() if hasattr(self, v)]
        raise NotImplementedError(
            f"{method_name} is not implemented for driver '{driver_name}'. "
            f"Supported drivers: {supported}"
        )

    def l2_circuits(self, driver: NetworkDriver):
        """Collect L2 circuit data. Dispatches to vendor-specific implementation."""
        impl = self._get_vendor_method("l2_circuits")
        impl(driver)

    def _l2_circuits_junos(self, driver):
        """Junos L2 circuit collection via CLI."""
        try:
            output = driver.cli(["show l2circuit connections"])
            raw = output.get("show l2circuit connections", "")
        except Exception as exc:
            self._log_failure(f"Failed to retrieve L2 circuit data: {exc}")
            return

        if not raw.strip():
            self._log_info("No L2 circuit data found.")
            return

        detected = {"raw_output": raw[:2000]}
        l2c_entry = self._record_entry(
            action=EntryActionChoices.ACTION_CONFIRMED,
            collector_type=self._collector_type,
            device=self._current_device,
            detected_values=detected,
            object_repr=f"L2 circuit data on {self._current_device}",
        )

        if self._should_apply():
            JournalEntry.objects.create(
                created=self._now,
                assigned_object=self._current_device,
                kind=JournalEntryKindChoices.KIND_INFO,
                comments=f"L2 circuit data collected:\n```\n{raw[:2000]}\n```",
            )
            self._mark_entry_applied(l2c_entry, self._current_device)
        self._log_success("L2 circuit collection completed")

    def evpn(self, driver: NetworkDriver):
        """Collect EVPN data. Dispatches to vendor-specific implementation."""
        impl = self._get_vendor_method("evpn")
        impl(driver)

    def _evpn_junos(self, driver):
        """Junos EVPN collection via CLI."""
        try:
            output = driver.cli(["show evpn mac-table"])
            raw = output.get("show evpn mac-table", "")
        except Exception as exc:
            self._log_failure(f"Failed to retrieve EVPN data: {exc}")
            return

        if not raw.strip():
            self._log_info("No EVPN data found.")
            return

        mac_pattern = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")
        for line in raw.strip().split("\n"):
            match = mac_pattern.search(line)
            if match:
                mac_str = match.group(1)
                existing_mac = MACAddress.objects.filter(mac_address=mac_str).first()
                action = EntryActionChoices.ACTION_CONFIRMED if existing_mac else EntryActionChoices.ACTION_NEW
                detected = {"mac": mac_str}

                evpn_entry = self._record_entry(
                    action=action,
                    collector_type=self._collector_type,
                    device=self._current_device,
                    detected_values=detected,
                    object_instance=existing_mac,
                    object_repr=f"EVPN MAC {mac_str}",
                )

                if self._should_apply():
                    netbox_mac, created = MACAddress.objects.get_or_create(
                        mac_address=mac_str
                    )
                    netbox_mac.discovery_method = CollectionTypeChoices.TYPE_EVPN
                    netbox_mac.last_seen = self._now
                    netbox_mac.save()

                    if created:
                        netbox_mac.tags.add(AUTO_D_TAG)
                        self._log_success(
                            f"Created EVPN MAC {get_absolute_url_markdown(netbox_mac, bold=True)}."
                        )
                    self._mark_entry_applied(evpn_entry, netbox_mac)

        if self._should_apply():
            JournalEntry.objects.create(
                created=self._now,
                assigned_object=self._current_device,
                kind=JournalEntryKindChoices.KIND_INFO,
                comments=f"EVPN data collected:\n```\n{raw[:2000]}\n```",
            )
        self._log_success("EVPN collection completed")

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

                    existing_ip = IPAddress.objects.filter(address=ip_str, vrf=nb_vrf).first()
                    ip_action = EntryActionChoices.ACTION_CONFIRMED if existing_ip else EntryActionChoices.ACTION_NEW
                    detected = {
                        "remote_address": remote_address,
                        "remote_as": int(as_number),
                        "vrf": vrf_name if nb_vrf else None,
                        "state": "up" if peer.get("up") else "down",
                    }

                    bgp_entry = self._record_entry(
                        action=ip_action,
                        collector_type=self._collector_type,
                        device=device,
                        detected_values=detected,
                        object_instance=existing_ip,
                        object_repr=f"BGP peer {remote_address} AS{as_number}",
                    )

                    if self._should_apply():
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
                        self._mark_entry_applied(bgp_entry, nb_ip)

        self._bgp_routing_integration()
        self._log_success("BGP collection completed")

    def _bgp_routing_integration(self):
        """Create/update BGP session in netbox-routing if available."""
        if not HAS_NETBOX_ROUTING:
            return

        try:
            from netbox_routing.models import BGPPeer, BGPRouter, BGPScope  # noqa: F401

            router = BGPRouter.objects.filter(
                assigned_object_id=self._current_device.pk,
            ).first()
            if not router:
                self._log_info(
                    f"No BGPRouter found for {self._current_device} in netbox-routing. "
                    f"Skipping BGP session creation."
                )
                return

            self._log_info(
                f"Found BGPRouter for {self._current_device} in netbox-routing."
            )
        except Exception as exc:
            self._log_warning(f"netbox-routing BGP integration error: {exc}")

    def ospf(self, driver: NetworkDriver):
        """Collect OSPF data. Dispatches to vendor-specific implementation."""
        impl = self._get_vendor_method("ospf")
        impl(driver)

    def _ospf_junos(self, driver):
        """Junos OSPF collection via CLI."""
        try:
            output = driver.cli(["show ospf neighbor"])
            raw = output.get("show ospf neighbor", "")
        except Exception as exc:
            self._log_failure(f"Failed to retrieve OSPF data: {exc}")
            return

        if not raw.strip():
            self._log_info("No OSPF neighbor data found.")
            return

        ip_pattern = re.compile(
            r"^(\d+\.\d+\.\d+\.\d+)\s+(\S+)\s+(\S+)\s+(\d+\.\d+\.\d+\.\d+)",
            re.MULTILINE,
        )
        neighbors = []
        for match in ip_pattern.finditer(raw):
            neighbor_ip = match.group(1)
            iface_name = match.group(2)
            state = match.group(3)
            router_id = match.group(4)
            neighbors.append({
                "address": neighbor_ip,
                "interface": iface_name,
                "state": state,
                "router_id": router_id,
            })

            existing_ip = IPAddress.objects.filter(address=f"{neighbor_ip}/32").first()
            ip_action = EntryActionChoices.ACTION_CONFIRMED if existing_ip else EntryActionChoices.ACTION_NEW
            detected = {
                "address": neighbor_ip,
                "interface": iface_name,
                "state": state,
                "router_id": router_id,
            }

            ospf_entry = self._record_entry(
                action=ip_action,
                collector_type=self._collector_type,
                device=self._current_device,
                detected_values=detected,
                object_instance=existing_ip,
                object_repr=f"OSPF neighbor {neighbor_ip} (RID: {router_id})",
            )

            if self._should_apply():
                ip_obj, created = IPAddress.objects.get_or_create(
                    address=f"{neighbor_ip}/32",
                    defaults={
                        "description": (
                            f"OSPF neighbor (Router ID: {router_id}) discovered on "
                            f"{self._current_device} ({self._now.date()})"
                        ),
                    },
                )
                if created:
                    ip_obj.tags.add(AUTO_D_TAG)
                    self._log_success(
                        f"Created OSPF neighbor IP {get_absolute_url_markdown(ip_obj, bold=True)} "
                        f"(Router ID: {router_id})."
                    )
                self._mark_entry_applied(ospf_entry, ip_obj)

                self._ospf_routing_integration(ip_obj, neighbors[-1])

        if neighbors and self._should_apply():
            neighbor_lines = "\n".join(
                f"- `{n['address']}` on `{n['interface']}` (State: {n['state']}, "
                f"Router ID: {n['router_id']})"
                for n in neighbors
            )
            JournalEntry.objects.create(
                created=self._now,
                assigned_object=self._current_device,
                kind=JournalEntryKindChoices.KIND_INFO,
                comments=f"OSPF neighbors discovered:\n{neighbor_lines}",
            )

        self._log_success("OSPF collection completed")

    def _ospf_routing_integration(self, ip_obj, neighbor_data):
        """Create/update OSPF data in netbox-routing if available."""
        if not HAS_NETBOX_ROUTING:
            return

        try:
            from netbox_routing.models import OSPFInstance  # noqa: F401

            instance = OSPFInstance.objects.filter(
                device=self._current_device,
            ).first()
            if instance:
                self._log_info(
                    f"Found OSPF instance `{instance}` for {self._current_device} "
                    f"in netbox-routing. Neighbor: {neighbor_data['address']} "
                    f"(State: {neighbor_data['state']})"
                )
        except Exception as exc:
            self._log_warning(f"netbox-routing OSPF integration error: {exc}")

    def execute(self):
        """Execute the collection job."""
        from netbox_facts.models.facts_report import FactsReport

        assert self._napalm_driver is not None

        # Create a report for this run
        self._report = FactsReport.objects.create(
            collection_plan=self.plan,
            status=ReportStatusChoices.STATUS_PENDING,
        )

        try:
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
        except Exception:
            # Mark the report as failed on unhandled exceptions
            self._report.update_summary()
            self._report.completed_at = timezone.now()
            self._report.status = ReportStatusChoices.STATUS_FAILED
            self._report.save(update_fields=["completed_at", "status"])
            raise
        else:
            # Finalize report on success
            self._report.update_summary()
            self._report.completed_at = timezone.now()
            self._report.status = (
                ReportStatusChoices.STATUS_APPLIED
                if self._should_apply()
                else ReportStatusChoices.STATUS_PENDING
            )
            self._report.save(update_fields=["completed_at", "status"])

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
