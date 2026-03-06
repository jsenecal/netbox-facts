"""Runner class for collection jobs."""

from __future__ import annotations

import ipaddress
import re
from itertools import groupby
from typing import TYPE_CHECKING, Generator, Tuple, Type, List, Dict, Any

import django.core.exceptions
import django.db

from django.contrib.contenttypes.models import ContentType
from django.db.models import CharField, Func
from django.utils import timezone
from dcim.models.device_components import Interface, ModuleBay
from dcim.models.device_components import InventoryItem
from dcim.models.devices import Device
from dcim.models.modules import Module, ModuleType
from extras.choices import JournalEntryKindChoices
from extras.models.models import JournalEntry
from netbox.plugins.utils import get_plugin_config
from ipam.models.ip import IPAddress, Prefix
from ipam.models.vrfs import VRF
from napalm.base import NetworkDriver
from napalm.base.exceptions import (
    CommandErrorException,
    CommandTimeoutException,
    ConnectionException,
    ModuleImportError,
    NapalmException,
)
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
    create_module,
    detect_interface_type,
    duplicate_object_warning,
    get_absolute_url_markdown,
    get_connection_ips,
    get_or_create_ip,
    get_or_create_mac,
    resolve_device_by_name,
    resolve_napalm_interfaces_ip_addresses,
    resolve_napalm_network_instances,
    resolve_vrf,
)
from netbox_facts.models.mac import MACAddress
from netbox_facts.constants import AUTO_D_TAG
from netbox_facts.napalm.junos import EnhancedJunOSDriver

if TYPE_CHECKING:
    from netbox_facts.models.collection_plan import CollectionPlan
    from netbox_facts.models.facts_report import FactsReport, FactsReportEntry


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
        # Per-plan username/password override global defaults
        self._napalm_username = self._napalm_args.pop(
            "username",
            get_plugin_config("netbox_facts", "napalm_username", "netbox"),
        )
        self._napalm_password = self._napalm_args.pop(
            "password",
            get_plugin_config("netbox_facts", "napalm_password", "netbox"),
        )
        self._interfaces_re = re.compile(
            get_plugin_config("netbox_facts", "valid_interfaces_re")
        )
        # Inject NAPALM connection timeout into optional_args
        napalm_timeout = get_plugin_config("netbox_facts", "napalm_timeout", 60)
        if napalm_timeout and "timeout" not in self._napalm_args:
            self._napalm_args["timeout"] = napalm_timeout
        self._devices = Device.objects.none()
        self._current_device: Device | None = None
        self._log_prefix = ""
        self._now = timezone.now()
        self._report: FactsReport | None = None
        self._detect_only: bool = getattr(plan, "detect_only", False)
        self._seen_ips: set = set()

        # Get the NAPALM driver
        try:
            self._napalm_driver = plan.get_napalm_driver()
        except (ModuleImportError, ModuleNotFoundError) as exc:
            raise CollectionError(
                f"There was an error initializing the napalm driver: {exc}"
            ) from exc

        # Get the devices to collect from
        self._devices = plan.get_devices_queryset()

    def _should_apply(self) -> bool:
        """Return True if mutations should be performed (detect_only is False)."""
        return not self._detect_only

    @staticmethod
    def _object_repr(*parts):
        """Build an object_repr string from model instances and/or plain strings.

        Model instances become "ModelName [str](url)", plain strings pass through.
        Parts are joined with " on ".
        """
        rendered = []
        for part in parts:
            if hasattr(part, "get_absolute_url"):
                name = type(part).__name__
                rendered.append(f"{name} {get_absolute_url_markdown(part)}")
            else:
                rendered.append(str(part))
        return " on ".join(rendered)

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

    def _mark_entry_applied(self, entry, object_instance=None, object_repr=None):
        """Mark an entry as applied, optionally updating its GenericFK and repr."""
        if entry is None:
            return
        entry.status = EntryStatusChoices.STATUS_APPLIED
        entry.applied_at = timezone.now()
        update_fields = ["status", "applied_at"]
        if object_instance is not None and hasattr(object_instance, "pk") and object_instance.pk:
            entry.object_type = ContentType.objects.get_for_model(object_instance)
            entry.object_id = object_instance.pk
            update_fields.extend(["object_type", "object_id"])
        if object_repr is not None:
            entry.object_repr = object_repr
            update_fields.append("object_repr")
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

        # Pre-fetch existing MACs in bulk to avoid N+1 queries
        all_macs = {
            entry["mac"]
            for entry in table_as_list
            if entry["mac"] and entry.get("state", "") != "unreachable"
        }
        existing_macs_by_addr = {}
        if all_macs:
            for mac_obj in MACAddress.objects.filter(mac_address__in=all_macs):
                key = str(mac_obj.mac_address).replace(":", "").upper()
                existing_macs_by_addr[key] = mac_obj

        # Pre-fetch existing IPs in bulk to avoid N+1 queries
        all_raw_ips = list(
            {
                entry["ip"]
                for entry in table_as_list
                if entry["ip"] and entry["mac"]
                and entry.get("state", "") != "unreachable"
            }
        )
        existing_ips_map = {}  # (cidr_str, vrf_id) -> IPAddress
        if all_raw_ips:
            for ip_obj in (
                IPAddress.objects.annotate(
                    _host=Func(
                        "address", function="HOST",
                        output_field=CharField(),
                    )
                )
                .filter(_host__in=all_raw_ips)
                .select_related("vrf")
            ):
                key = (str(ip_obj.address), ip_obj.vrf_id)
                existing_ips_map[key] = ip_obj

        seen_ips = set()  # Track (cidr_str, vrf_id) for stale detection

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

                # Determine action for MAC (using pre-fetched bulk data)
                mac_cache_key = arp_entry["mac"].replace(":", "").upper()
                existing_mac = existing_macs_by_addr.get(mac_cache_key)
                mac_action = EntryActionChoices.ACTION_CONFIRMED if existing_mac else EntryActionChoices.ACTION_NEW

                # Determine action for IP (using pre-fetched bulk data)
                ip_cache_key = (
                    str(ip_interface_object),
                    routing_instance.pk if routing_instance else None,
                )
                existing_ip = existing_ips_map.get(ip_cache_key)
                ip_action = EntryActionChoices.ACTION_CONFIRMED if existing_ip else EntryActionChoices.ACTION_NEW
                seen_ips.add(ip_cache_key)

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
                    object_repr=f"MACAddress {arp_entry['mac']}",
                )

                # Record IP entry
                ip_entry = self._record_entry(
                    action=ip_action,
                    collector_type=self._collector_type,
                    device=self._current_device,
                    detected_values=detected,
                    object_instance=existing_ip,
                    object_repr=self._object_repr(existing_ip) if existing_ip else f"IPAddress {ip_interface_object}",
                )

                if self._should_apply():
                    try:
                        netbox_mac, created = get_or_create_mac(arp_entry["mac"])
                    except MACAddress.MultipleObjectsReturned:
                        self._log_warning(
                            duplicate_object_warning("MAC", arp_entry["mac"])
                        )
                        continue
                    if created:
                        self._log_success(
                            f"Succesfully created MAC address {get_absolute_url_markdown(netbox_mac, bold=True)}."
                        )
                    else:
                        self._log_info(
                            f"Found existing MAC address {get_absolute_url_markdown(netbox_mac, bold=True)}."
                        )

                    netbox_mac.interfaces.add(netbox_interface)

                    # Get or create an IPAddress for this entry
                    try:
                        netbox_address, created = get_or_create_ip(
                            str(ip_interface_object),
                            vrf=routing_instance,
                            description=f"Automatically discovered on {self._now}",
                        )
                    except IPAddress.MultipleObjectsReturned:
                        self._log_warning(
                            duplicate_object_warning("IP", ip_interface_object)
                        )
                        continue
                    if created:
                        JournalEntry.objects.create(
                            created=self._now,
                            assigned_object=netbox_address,
                            kind=JournalEntryKindChoices.KIND_INFO,
                            comments=(
                                f"Discovered by {self._current_device} with MAC"
                                + f" {get_absolute_url_markdown(netbox_mac, bold=True)}"
                                + f" on interface {get_absolute_url_markdown(netbox_interface, bold=True)} via"
                                + f" {self.plan.get_collector_type_display()} collection."  # type: ignore
                            ),
                        )
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
                    self._mark_entry_applied(mac_entry, netbox_mac, object_repr=self._object_repr(netbox_mac))
                    self._mark_entry_applied(ip_entry, netbox_address, object_repr=self._object_repr(netbox_address))
        # Detect stale IPs: previously discovered IPs on this device
        # that are no longer present in the current ARP/NDP table.
        # Filter by IP family so ARP only flags v4, NDP only flags v6.
        if self._current_device and seen_ips:
            ip_family = 6 if self._collector_type == CollectionTypeChoices.TYPE_NDP else 4
            device_macs = MACAddress.objects.filter(
                interfaces__in=self._current_device.vc_interfaces()
            ).distinct()
            known_ips = (
                IPAddress.objects.filter(mac_addresses__in=device_macs)
                .filter(tags__name=AUTO_D_TAG, address__family=ip_family)
                .select_related("vrf")
                .distinct()
            )
            for ip_obj in known_ips:
                key = (str(ip_obj.address), ip_obj.vrf_id)
                if key not in seen_ips:
                    self._record_entry(
                        action=EntryActionChoices.ACTION_STALE,
                        collector_type=self._collector_type,
                        device=self._current_device,
                        detected_values={},
                        current_values={
                            "ip": str(ip_obj.address),
                            "vrf": str(ip_obj.vrf) if ip_obj.vrf else None,
                        },
                        object_instance=ip_obj,
                        object_repr=self._object_repr(ip_obj),
                    )
                    self._log_info(
                        f"IP {ip_obj.address} not seen in current table — flagged as stale."
                    )

    def arp(self, driver: NetworkDriver | EnhancedJunOSDriver):
        """Collect ARP table data from a device."""
        arp_table = self._napalm_rpc(driver.get_arp_table, "ARP data")
        if arp_table is None:
            return

        self._ip_neighbors(driver, arp_table)  # type: ignore
        self._log_success("ARP collection completed")

    def ndp(self, driver: NetworkDriver | EnhancedJunOSDriver):
        """Collect NDP data from devices."""
        ndp_table = self._napalm_rpc(driver.get_ipv6_neighbors_table, "NDP data")
        if ndp_table is None:
            return

        self._ip_neighbors(driver, ndp_table)  # type: ignore
        self._log_success("IPv6 Neighbor Discovery collection completed")

    def inventory(self, driver: NetworkDriver):
        """Collect inventory data from a device using get_facts()."""
        facts = self._napalm_rpc(driver.get_facts, "inventory data")
        if facts is None:
            return
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
            object_repr=self._object_repr(device),
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

        # Chassis module inventory (Junos-specific)
        if hasattr(driver, "get_chassis_inventory"):
            self._collect_chassis_inventory(driver, device)

        self._log_success("Inventory collection completed")

    def _collect_chassis_inventory(self, driver, device):
        """Collect chassis hardware modules and reconcile with InventoryItems and Modules."""
        modules = self._napalm_rpc(driver.get_chassis_inventory, "chassis inventory")
        if modules is None:
            return
        modules = list(modules)

        manufacturer = device.device_type.manufacturer

        # Pre-fetch existing discovered InventoryItems for this device
        existing_items = {
            item.name: item
            for item in InventoryItem.objects.filter(device=device)
        }
        # Track newly created items so children can find their parent
        created_items = {}
        seen_names = set()

        # Track Modules by name for sub-module parent bay lookups
        modules_by_name = {}
        seen_module_bay_ids = set()

        for mod in modules:
            name = mod["name"]
            component_name = mod.get("component_name") or name
            parent_name = mod.get("parent_name")
            part_id = mod.get("part_id") or ""
            serial = mod.get("serial") or ""
            description = mod.get("description") or ""

            # Skip BUILTIN modules and modules with no part_id
            if part_id == "BUILTIN" or not part_id:
                continue

            # Skip Routing Engine modules — serial handled by get_facts
            if name.startswith("Routing Engine"):
                continue

            seen_names.add(name)
            existing = existing_items.get(name)

            detected = {
                "name": name,
                "parent_name": parent_name,
                "serial": serial,
                "part_id": part_id,
                "description": description,
            }

            if existing is None:
                action = EntryActionChoices.ACTION_NEW
                current = {}
            elif (
                existing.serial != serial
                or existing.part_id != part_id
                or existing.description != description
            ):
                action = EntryActionChoices.ACTION_CHANGED
                current = {
                    "serial": existing.serial,
                    "part_id": existing.part_id,
                    "description": existing.description,
                }
            else:
                action = EntryActionChoices.ACTION_CONFIRMED
                current = {
                    "serial": existing.serial,
                    "part_id": existing.part_id,
                    "description": existing.description,
                }

            object_repr = f"InventoryItem {name}"
            entry = self._record_entry(
                action=action,
                collector_type=self._collector_type,
                device=device,
                detected_values=detected,
                current_values=current,
                object_instance=existing,
                object_repr=object_repr,
            )

            if self._should_apply():
                # Resolve parent from already-created or pre-existing items
                parent_item = None
                if parent_name:
                    parent_item = created_items.get(parent_name) or existing_items.get(parent_name)

                if action == EntryActionChoices.ACTION_NEW:
                    item = InventoryItem.objects.create(
                        device=device,
                        name=name,
                        parent=parent_item,
                        serial=serial,
                        part_id=part_id,
                        description=description,
                        discovered=True,
                    )
                    item.tags.add(AUTO_D_TAG)
                    created_items[name] = item
                    self._mark_entry_applied(entry, item, object_repr=self._object_repr(item))
                elif action == EntryActionChoices.ACTION_CHANGED:
                    existing.serial = serial
                    existing.part_id = part_id
                    existing.description = description
                    existing.save(update_fields=["serial", "part_id", "description"])
                    self._mark_entry_applied(entry, existing, object_repr=self._object_repr(existing))
                else:
                    self._mark_entry_applied(entry, existing)

            # --- Module creation logic ---
            self._collect_chassis_module(
                device, manufacturer, name, component_name, parent_name,
                serial, part_id, description,
                modules_by_name, seen_module_bay_ids,
            )

        # Detect stale discovered items (hardware no longer present)
        stale_items = InventoryItem.objects.filter(
            device=device, discovered=True,
        ).exclude(name__in=seen_names)

        for stale_item in stale_items:
            stale_entry = self._record_entry(
                action=EntryActionChoices.ACTION_STALE,
                collector_type=self._collector_type,
                device=device,
                detected_values={},
                current_values={
                    "name": stale_item.name,
                    "serial": stale_item.serial,
                    "part_id": stale_item.part_id,
                    "description": stale_item.description,
                },
                object_instance=stale_item,
                object_repr=f"InventoryItem {stale_item.name}",
            )
            if self._should_apply():
                stale_item.delete()
                self._mark_entry_applied(stale_entry, device)

        # Detect stale auto-discovered Modules
        stale_modules = Module.objects.filter(
            device=device, tags__name=AUTO_D_TAG,
        ).exclude(module_bay_id__in=seen_module_bay_ids)

        for stale_mod in stale_modules:
            bay_name = stale_mod.module_bay.name
            stale_entry = self._record_entry(
                action=EntryActionChoices.ACTION_STALE,
                collector_type=self._collector_type,
                device=device,
                detected_values={},
                current_values={
                    "module_bay_id": stale_mod.module_bay_id,
                    "module_type_id": stale_mod.module_type_id,
                    "serial": stale_mod.serial,
                },
                object_instance=stale_mod,
                object_repr=f"Module {bay_name}",
            )
            if self._should_apply():
                stale_mod.delete()
                self._mark_entry_applied(stale_entry, device)

    def _collect_chassis_module(
        self, device, manufacturer, name, component_name, parent_name,
        serial, part_id, description, modules_by_name, seen_module_bay_ids,
    ):
        """Try to create/confirm a Module for a chassis hardware module.

        Called after the InventoryItem entry for each module. Only creates a
        Module when a matching ModuleBay and ModuleType exist in NetBox.
        """
        # Resolve parent Module for sub-module bay lookups
        parent_module = None
        if parent_name:
            parent_module = modules_by_name.get(parent_name)
            if parent_module is None:
                # Try DB lookup for previously created modules
                parent_bay = ModuleBay.objects.filter(
                    device=device, name=parent_name.rsplit("/", 1)[-1] if "/" in parent_name else parent_name,
                    module=None,
                ).first()
                if parent_bay:
                    parent_module = getattr(parent_bay, "installed_module", None)

        # Find ModuleBay
        bay = ModuleBay.objects.filter(
            device=device,
            name=component_name,
            module=parent_module,
        ).first()

        if bay is None:
            self._log_warning(f"No ModuleBay found for {component_name}")
            return

        # Find ModuleType — part_number takes precedence over model
        module_type = ModuleType.objects.filter(
            manufacturer=manufacturer, part_number=part_id,
        ).first()
        if module_type is None:
            module_type = ModuleType.objects.filter(
                manufacturer=manufacturer, model=part_id,
            ).first()

        if module_type is None:
            self._log_warning(f"No ModuleType found for part {part_id}")
            return

        seen_module_bay_ids.add(bay.pk)

        # Check existing module in bay
        installed = getattr(bay, "installed_module", None)

        if installed is None:
            action = EntryActionChoices.ACTION_NEW
            current = {}
        elif installed.serial == serial:
            action = EntryActionChoices.ACTION_CONFIRMED
            current = {"serial": installed.serial}
        else:
            action = EntryActionChoices.ACTION_CHANGED
            current = {"serial": installed.serial}

        mod_detected = {
            "name": name,
            "component_name": component_name,
            "parent_name": parent_name,
            "serial": serial,
            "part_id": part_id,
            "description": description,
            "module_bay_id": bay.pk,
            "module_type_id": module_type.pk,
        }

        mod_entry = self._record_entry(
            action=action,
            collector_type=self._collector_type,
            device=device,
            detected_values=mod_detected,
            current_values=current,
            object_instance=installed,
            object_repr=f"Module {component_name}",
        )

        if self._should_apply():
            if action == EntryActionChoices.ACTION_NEW:
                mod_obj = create_module(device, bay, module_type, serial)
                modules_by_name[name] = mod_obj
                self._mark_entry_applied(mod_entry, mod_obj, object_repr=self._object_repr(mod_obj))
            elif action == EntryActionChoices.ACTION_CHANGED:
                installed.serial = serial
                installed.save(update_fields=["serial"])
                modules_by_name[name] = installed
                self._mark_entry_applied(mod_entry, installed, object_repr=self._object_repr(installed))
            else:
                modules_by_name[name] = installed
                self._mark_entry_applied(mod_entry, installed)
        else:
            # Track for stale detection even in detect-only mode
            if installed is not None:
                modules_by_name[name] = installed

    def _get_or_create_interface(self, device, name, iface_data=None):
        """Look up an interface on a device, creating it if missing.

        Uses detect_interface_type() to infer the type and optionally
        populates description/enabled/mtu from iface_data.
        Sub-interfaces (containing '.') get their parent set to the physical interface.
        """
        try:
            return device.vc_interfaces().get(name=name)
        except Interface.DoesNotExist:
            iface_type = detect_interface_type(name)
            kwargs = {"device": device, "name": name, "type": iface_type}
            if "." in name:
                parent_name = name.rsplit(".", 1)[0]
                try:
                    kwargs["parent"] = device.vc_interfaces().get(name=parent_name)
                except Interface.DoesNotExist:
                    pass
            if iface_data:
                if iface_data.get("description"):
                    kwargs["description"] = iface_data["description"]
                if iface_data.get("is_enabled") is not None:
                    kwargs["enabled"] = iface_data["is_enabled"]
                if iface_data.get("mtu"):
                    kwargs["mtu"] = iface_data["mtu"]
            nb_iface = Interface.objects.create(**kwargs)
            nb_iface.tags.add(AUTO_D_TAG)
            self._log_success(
                f"Auto-created interface `{name}` (type={iface_type}) on {device}."
            )
            return nb_iface

    def interfaces(self, driver: NetworkDriver):
        """Collect interface data from a device using get_interfaces()."""
        self._seen_ips = set()
        # Pass the interface regex to enhanced drivers that support server-side filtering
        try:
            try:
                ifaces = driver.get_interfaces(
                    interface_name=self._interfaces_re.pattern,
                )
            except TypeError:
                ifaces = driver.get_interfaces()
        except (CommandErrorException, CommandTimeoutException, ConnectionException) as exc:
            self._log_failure(f"Failed to retrieve interface data: {exc}")
            return
        device = self._current_device

        for iface_name, iface_data in ifaces.items():
            # Skip interfaces that don't match the configured regex
            if not self._interfaces_re.match(iface_name):
                continue

            mac_addr = iface_data.get("mac_address") or ""
            if not mac_addr or mac_addr.lower() in ("none", "n/a", ""):
                continue

            nb_iface = self._get_or_create_interface(device, iface_name, iface_data)

            # Validate MAC address format before querying
            try:
                existing_mac = MACAddress.objects.filter(mac_address=mac_addr).first()
            except (django.core.exceptions.ValidationError, ValueError):
                self._log_warning(
                    f"Invalid MAC address `{mac_addr}` on interface `{iface_name}`. Skipping."
                )
                continue

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
                object_repr=self._object_repr(existing_mac or nb_iface) + f" MAC {mac_addr}",
            )

            if self._should_apply():
                try:
                    netbox_mac, created = get_or_create_mac(mac_addr)
                except MACAddress.MultipleObjectsReturned:
                    self._log_warning(
                        duplicate_object_warning("MAC", mac_addr)
                    )
                    continue
                except (django.core.exceptions.ValidationError, ValueError) as exc:
                    self._log_warning(
                        f"Could not create MAC `{mac_addr}` for `{iface_name}`: {exc}"
                    )
                    continue

                if created:
                    self._log_success(
                        f"Created MAC address {get_absolute_url_markdown(netbox_mac, bold=True)}."
                    )

                netbox_mac.device_interface = nb_iface
                netbox_mac.discovery_method = CollectionTypeChoices.TYPE_INTERFACES
                netbox_mac.last_seen = self._now
                netbox_mac.save()
                self._mark_entry_applied(iface_entry, netbox_mac, object_repr=self._object_repr(netbox_mac))

        # --- Process logical interfaces (LAG, IPs, VRFs) ---
        has_logical = any(
            iface_data.get("logical_interfaces")
            for iface_data in ifaces.values()
        )
        if has_logical:
            self._interfaces_logical(device, ifaces)
        else:
            self._interfaces_ip_generic(device, driver)

        self._detect_stale_ips(device)
        self._log_success("Interface collection completed")

    def _interfaces_logical(self, device, ifaces):
        """Process logical interfaces from enhanced driver data (LAG, IPs, VRFs)."""
        for iface_name, iface_data in ifaces.items():
            if not self._interfaces_re.match(iface_name):
                continue

            logical_interfaces = iface_data.get("logical_interfaces", {})
            if not logical_interfaces:
                continue

            nb_iface = self._get_or_create_interface(device, iface_name, iface_data)

            # --- LAG membership ---
            # Check the first logical interface's first family for "aenet"
            is_lag_member = False
            for li_name, li_data in logical_interfaces.items():
                families = li_data.get("families", {})
                first_family_name = next(iter(families), None) if families else None
                if first_family_name == "aenet":
                    is_lag_member = True
                    ae_bundle = families["aenet"].get("ae_bundle", "")
                    if ae_bundle:
                        ae_name = ae_bundle.split(".")[0]  # "ae0.0" -> "ae0"
                        detected = {"interface": iface_name, "lag_parent": ae_name}
                        current_lag = nb_iface.lag.name if nb_iface.lag else None
                        if current_lag == ae_name:
                            action = EntryActionChoices.ACTION_CONFIRMED
                        else:
                            action = (
                                EntryActionChoices.ACTION_CHANGED
                                if current_lag
                                else EntryActionChoices.ACTION_NEW
                            )
                        entry = self._record_entry(
                            action=action,
                            collector_type=self._collector_type,
                            device=device,
                            detected_values=detected,
                            current_values={"lag_parent": current_lag},
                            object_instance=nb_iface,
                            object_repr=f"LAG {get_absolute_url_markdown(nb_iface)} -> {ae_name}",
                        )
                        if self._should_apply():
                            ae_iface = self._get_or_create_interface(device, ae_name)
                            nb_iface.lag = ae_iface
                            nb_iface.save()
                            self._mark_entry_applied(entry, nb_iface)
                            self._log_success(
                                f"Set LAG membership: `{iface_name}` -> `{ae_name}`"
                            )
                break  # Only check the first logical interface for LAG

            # LAG members don't have their own IPs; skip to next physical
            if is_lag_member:
                continue

            # --- IPs and VRFs ---
            for li_name, li_data in logical_interfaces.items():
                families = li_data.get("families", {})
                if "aenet" in families:
                    continue

                vrf_name = li_data.get("vrf") or ""
                netbox_vrf = None
                try:
                    netbox_vrf = resolve_vrf(vrf_name)
                except VRF.DoesNotExist:
                    self._log_warning(
                        f"VRF `{vrf_name}` not found in NetBox. "
                        f"Skipping IPs on `{li_name}`."
                    )
                    self._record_entry(
                        action=EntryActionChoices.ACTION_NEW,
                        collector_type=self._collector_type,
                        device=device,
                        detected_values={"name": vrf_name},
                        object_repr=f"VRF {vrf_name}",
                    )
                    continue
                except VRF.MultipleObjectsReturned:
                    self._log_warning(
                        duplicate_object_warning("VRF", vrf_name)
                        + f" Skipping IPs on `{li_name}`."
                    )
                    continue

                nb_li = self._get_or_create_interface(device, li_name, li_data)

                for fam_name, fam_data in families.items():
                    if fam_name not in ("inet", "inet6"):
                        continue
                    addresses = fam_data.get("addresses", {})
                    for dest, addr_data in addresses.items():
                        local_ip = addr_data.get("local")
                        if not local_ip:
                            continue
                        # Skip non-preferred when multiple addresses (VRRP)
                        if len(addresses) > 1 and not addr_data.get("preferred"):
                            continue

                        # Build CIDR
                        try:
                            if dest:
                                # Handle incomplete inet destinations (3 octets)
                                if fam_name == "inet" and dest.count(".") == 2:
                                    net_part, prefix_len = dest.split("/")
                                    net = ipaddress.ip_network(
                                        f"{net_part}.0/{prefix_len}"
                                    )
                                else:
                                    net = ipaddress.ip_network(dest, strict=False)
                            else:
                                # Loopback: no destination
                                net = ipaddress.ip_network(local_ip, strict=False)
                        except ValueError:
                            ip_obj = ipaddress.ip_address(local_ip)
                            net = ipaddress.ip_network(
                                f"{local_ip}/{ip_obj.max_prefixlen}",
                                strict=False,
                            )
                        cidr = f"{local_ip}/{net.prefixlen}"

                        self._record_ip_entry(device, nb_li, cidr, net, netbox_vrf)

    def _interfaces_ip_generic(self, device, driver):
        """Collect IPs/VRFs using standard NAPALM get_interfaces_ip()."""
        interfaces_ip = self._napalm_rpc(driver.get_interfaces_ip, "interface IP data")
        if interfaces_ip is None:
            return

        network_instances = dict(self._get_network_instances(driver))

        for iface_name, family_data in interfaces_ip.items():
            nb_li = self._get_or_create_interface(device, iface_name)

            # Resolve VRF from network instances
            ni_data = network_instances.get(iface_name, {})
            netbox_vrf = ni_data.get("netbox_vrf")

            for family_name, addresses in family_data.items():
                if family_name not in ("ipv4", "ipv6"):
                    continue
                for ip_str, meta in addresses.items():
                    prefix_len = meta.get("prefix_length", 32)
                    cidr = f"{ip_str}/{prefix_len}"
                    try:
                        net = ipaddress.ip_network(cidr, strict=False)
                    except ValueError:
                        continue

                    self._record_ip_entry(device, nb_li, cidr, net, netbox_vrf)

    def _detect_stale_ips(self, device):
        """Detect auto-discovered IPs on a device that weren't seen in this run."""
        iface_ct = ContentType.objects.get_for_model(Interface)
        device_iface_ids = device.vc_interfaces().values_list("pk", flat=True)
        stale_ips = IPAddress.objects.filter(
            assigned_object_type=iface_ct,
            assigned_object_id__in=device_iface_ids,
            tags__name=AUTO_D_TAG,
        )
        for ip in stale_ips:
            vrf_id = ip.vrf_id
            key = (str(ip.address), vrf_id)
            if key in self._seen_ips:
                continue
            current_values = {
                "ip_address": str(ip.address),
                "vrf": ip.vrf.name if ip.vrf else None,
                "assigned_object": str(ip.assigned_object),
            }
            entry = self._record_entry(
                action=EntryActionChoices.ACTION_STALE,
                collector_type=self._collector_type,
                device=device,
                detected_values={},
                current_values=current_values,
                object_instance=ip,
                object_repr=self._object_repr(ip, ip.assigned_object),
            )
            if self._should_apply():
                ip.assigned_object = None
                ip.save()
                self._mark_entry_applied(entry, ip)

    def _record_ip_entry(self, device, nb_li, cidr, net, netbox_vrf):
        """Record and optionally apply a single IP address entry."""
        li_name = nb_li.name
        vrf_name = netbox_vrf.name if netbox_vrf else None

        existing_ip = IPAddress.objects.filter(
            address=cidr, vrf=netbox_vrf
        ).first()
        if not existing_ip:
            action = EntryActionChoices.ACTION_NEW
        elif (
            existing_ip.assigned_object is not None
            and existing_ip.assigned_object != nb_li
            and existing_ip.tags.filter(name=AUTO_D_TAG).exists()
        ):
            action = EntryActionChoices.ACTION_CHANGED
        else:
            action = EntryActionChoices.ACTION_CONFIRMED
        detected = {
            "logical_interface": li_name,
            "ip_address": cidr,
            "vrf": vrf_name,
            "prefix": str(net),
        }
        current_values = None
        if action == EntryActionChoices.ACTION_CHANGED and existing_ip:
            current_values = {
                "assigned_object": str(existing_ip.assigned_object),
            }
        entry = self._record_entry(
            action=action,
            collector_type=self._collector_type,
            device=device,
            detected_values=detected,
            current_values=current_values,
            object_instance=existing_ip or nb_li,
            object_repr=self._object_repr(existing_ip, nb_li) if existing_ip else f"IPAddress {cidr} on {get_absolute_url_markdown(nb_li)}",
        )

        vrf_id = netbox_vrf.pk if netbox_vrf else None
        self._seen_ips.add((cidr, vrf_id))

        if self._should_apply():
            # Create prefix (skip host routes)
            if net.num_addresses > 1:
                try:
                    nb_prefix, prefix_created = Prefix.objects.get_or_create(
                        prefix=str(net),
                        vrf=netbox_vrf,
                        defaults={
                            "description": (
                                f"Discovered on {device} "
                                f"({self._now.date()})"
                            ),
                        },
                    )
                except Prefix.MultipleObjectsReturned:
                    self._log_warning(
                        duplicate_object_warning("Prefix", net)
                    )
                    return
                if prefix_created:
                    nb_prefix.tags.add(AUTO_D_TAG)
            # Create/get IPAddress
            try:
                nb_ip, created = get_or_create_ip(
                    cidr, vrf=netbox_vrf,
                    assigned_object=nb_li,
                    description=f"Discovered on {device} ({self._now.date()})",
                )
            except IPAddress.MultipleObjectsReturned:
                self._log_warning(
                    duplicate_object_warning("IP", cidr)
                )
                return
            if created:
                self._log_success(
                    f"Created IP `{cidr}` on `{li_name}`"
                )
            elif nb_ip.assigned_object is None:
                nb_ip.assigned_object = nb_li
                nb_ip.save()
            elif nb_ip.assigned_object != nb_li and nb_ip.tags.filter(name=AUTO_D_TAG).exists():
                nb_ip.assigned_object = nb_li
                nb_ip.save()
            self._mark_entry_applied(entry, nb_ip, object_repr=self._object_repr(nb_ip, nb_li))

    def lldp(self, driver: NetworkDriver):
        """Collect LLDP data from a device using get_lldp_neighbors_detail()."""
        from dcim.choices import LinkStatusChoices
        from dcim.models.cables import Cable

        lldp_data = self._napalm_rpc(driver.get_lldp_neighbors_detail, "LLDP data")
        if lldp_data is None:
            return
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
            except Interface.MultipleObjectsReturned:
                self._log_warning(
                    duplicate_object_warning("interface", local_iface_name)
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
                    remote_device = resolve_device_by_name(remote_system_name)
                except Device.DoesNotExist:
                    self._log_info(
                        f"Remote device `{remote_system_name}` not found in NetBox. Skipping cable creation."
                    )
                    continue
                except Device.MultipleObjectsReturned:
                    self._log_warning(
                        duplicate_object_warning("device", remote_system_name)
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
                except Interface.MultipleObjectsReturned:
                    self._log_warning(
                        duplicate_object_warning("interface", f"{remote_port}` on `{remote_system_name}")
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
                    object_repr=f"Cable {get_absolute_url_markdown(local_iface)} ↔ {remote_system_name}:{remote_port}",
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
                        self._mark_entry_applied(lldp_entry, cable, object_repr=self._object_repr(cable))
                    except (Device.DoesNotExist, Interface.DoesNotExist, ValueError,
                            django.core.exceptions.ValidationError,
                            django.db.IntegrityError) as exc:
                        self._log_warning(
                            f"Could not create cable between `{local_iface_name}` and "
                            f"`{remote_system_name}:{remote_port}`: {exc}"
                        )

        self._log_success("LLDP collection completed")

    def ethernet_switching(self, driver: NetworkDriver):
        """Collect ethernet switching data from a device using get_mac_address_table()."""
        mac_table = self._napalm_rpc(driver.get_mac_address_table, "MAC address table")
        if mac_table is None:
            return
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
            except Interface.MultipleObjectsReturned:
                self._log_warning(
                    duplicate_object_warning("interface", iface_name)
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
                object_repr=f"MACAddress {mac_addr} on {get_absolute_url_markdown(nb_iface)}",
            )

            if self._should_apply():
                try:
                    netbox_mac, created = get_or_create_mac(mac_addr)
                except MACAddress.MultipleObjectsReturned:
                    self._log_warning(
                        duplicate_object_warning("MAC", mac_addr)
                    )
                    continue
                if created:
                    self._log_success(
                        f"Created MAC address {get_absolute_url_markdown(netbox_mac, bold=True)}."
                    )

                netbox_mac.interfaces.add(nb_iface)
                netbox_mac.discovery_method = CollectionTypeChoices.TYPE_L2
                netbox_mac.last_seen = self._now
                netbox_mac.save()
                self._mark_entry_applied(l2_entry, netbox_mac, object_repr=self._object_repr(netbox_mac))

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
        output = self._napalm_rpc(driver.cli, "L2 circuit data", ["show l2circuit connections"])
        if output is None:
            return
        raw = output.get("show l2circuit connections", "")

        if not raw.strip():
            self._log_info("No L2 circuit data found.")
            return

        detected = {"raw_output": raw[:2000]}
        l2c_entry = self._record_entry(
            action=EntryActionChoices.ACTION_CONFIRMED,
            collector_type=self._collector_type,
            device=self._current_device,
            detected_values=detected,
            object_repr=f"L2 circuit data on {get_absolute_url_markdown(self._current_device)}",
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
        output = self._napalm_rpc(driver.cli, "EVPN data", ["show evpn mac-table"])
        if output is None:
            return
        raw = output.get("show evpn mac-table", "")

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
                    object_repr=self._object_repr(existing_mac) if existing_mac else f"MACAddress {mac_str}",
                )

                if self._should_apply():
                    try:
                        netbox_mac, created = get_or_create_mac(mac_str)
                    except MACAddress.MultipleObjectsReturned:
                        self._log_warning(
                            duplicate_object_warning("MAC", mac_str)
                        )
                        continue
                    netbox_mac.discovery_method = CollectionTypeChoices.TYPE_EVPN
                    netbox_mac.last_seen = self._now
                    netbox_mac.save()

                    if created:
                        self._log_success(
                            f"Created EVPN MAC {get_absolute_url_markdown(netbox_mac, bold=True)}."
                        )
                    self._mark_entry_applied(evpn_entry, netbox_mac, object_repr=self._object_repr(netbox_mac))

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

        bgp_data = self._napalm_rpc(driver.get_bgp_neighbors_detail, "BGP data")
        if bgp_data is None:
            return
        device = self._current_device
        self._bgp_routing_data = {"local_as": None, "vrfs": {}}

        for vrf_name, peers_by_as in bgp_data.items():
            # Resolve VRF (empty string or "global" means no VRF)
            nb_vrf = None
            try:
                nb_vrf = resolve_vrf(vrf_name)
            except VRF.DoesNotExist:
                self._log_warning(
                    f"Could not find VRF `{vrf_name}` in NetBox. "
                    "Skipping peers in this VRF."
                )
                self._record_entry(
                    action=EntryActionChoices.ACTION_NEW,
                    collector_type=self._collector_type,
                    device=device,
                    detected_values={"name": vrf_name},
                    object_repr=f"VRF {vrf_name}",
                )
                continue
            except VRF.MultipleObjectsReturned:
                self._log_warning(
                    duplicate_object_warning("VRF", vrf_name)
                    + " Skipping peers in this VRF."
                )
                continue

            for as_number, peers in peers_by_as.items():
                for peer in peers:
                    remote_address = peer.get("remote_address", "")
                    if not remote_address:
                        continue

                    if self._bgp_routing_data["local_as"] is None:
                        local_as_val = peer.get("local_as")
                        if local_as_val is not None:
                            self._bgp_routing_data["local_as"] = int(local_as_val)

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
                        object_repr=f"BGP peer {get_absolute_url_markdown(existing_ip) if existing_ip else remote_address} AS{as_number}",
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
                        except ASN.MultipleObjectsReturned:
                            self._log_warning(
                                duplicate_object_warning("ASN", as_number)
                            )

                        try:
                            nb_ip, created = get_or_create_ip(
                                ip_str, vrf=nb_vrf,
                                description=f"BGP peer AS{as_number} discovered on {self._now}",
                            )
                        except IPAddress.MultipleObjectsReturned:
                            self._log_warning(
                                duplicate_object_warning("IP", ip_str)
                            )
                            continue
                        if created:
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
                        self._mark_entry_applied(bgp_entry, nb_ip, object_repr=f"BGP peer {get_absolute_url_markdown(nb_ip)} AS{as_number}")
                        self._bgp_routing_data["vrfs"].setdefault(vrf_name, []).append({
                            "remote_address": remote_address,
                            "as_number": int(as_number),
                            "nb_vrf": nb_vrf,
                            "nb_ip": nb_ip,
                            "nb_asn": nb_asn,
                        })

        self._bgp_routing_integration()
        self._log_success("BGP collection completed")

    def _bgp_routing_integration(self):
        """Create BGPRouter/BGPScope/BGPPeer in netbox-routing if available."""
        if not HAS_NETBOX_ROUTING:
            return

        data = getattr(self, "_bgp_routing_data", None)
        if not data or data["local_as"] is None:
            self._log_info("No local AS found in BGP data; skipping routing integration.")
            return

        try:
            from netbox_routing.models import BGPPeer, BGPRouter, BGPScope
        except (ImportError, RuntimeError):
            self._log_warning("netbox-routing models could not be loaded; skipping routing integration.")
            return

        from django.contrib.contenttypes.models import ContentType
        from ipam.models import ASN, RIR

        device = self._current_device
        device_ct = ContentType.objects.get_for_model(device)

        # Get-or-create local ASN
        try:
            local_asn, _ = ASN.objects.get_or_create(
                asn=data["local_as"],
                defaults={"rir": RIR.objects.first()},
            )
        except (RIR.DoesNotExist, TypeError):
            self._log_warning(f"No RIR in NetBox. Cannot create local ASN {data['local_as']}.")
            return

        # BGPRouter
        if self._should_apply():
            bgp_router, router_created = BGPRouter.objects.get_or_create(
                assigned_object_type=device_ct,
                assigned_object_id=device.pk,
                asn=local_asn,
            )
            if router_created:
                bgp_router.tags.add(AUTO_D_TAG)
        else:
            bgp_router = BGPRouter.objects.filter(
                assigned_object_type=device_ct,
                assigned_object_id=device.pk,
                asn=local_asn,
            ).first()
            router_created = bgp_router is None

        router_action = EntryActionChoices.ACTION_NEW if router_created else EntryActionChoices.ACTION_CONFIRMED
        self._record_entry(
            action=router_action,
            collector_type=self._collector_type,
            device=device,
            detected_values={"local_as": data["local_as"]},
            object_instance=bgp_router,
            object_repr=f"BGPRouter {device}",
        )

        if router_created and self._should_apply():
            self._log_success(f"Created BGPRouter for {device} (AS{data['local_as']}).")
        elif bgp_router:
            self._log_info(f"Found existing BGPRouter for {device}.")

        if not self._should_apply() and bgp_router is None:
            # detect-only and router doesn't exist yet — can't create scope/peer
            return

        # Per-VRF scopes and peers
        for vrf_name, peers in data["vrfs"].items():
            nb_vrf = peers[0]["nb_vrf"] if peers else None

            if self._should_apply():
                bgp_scope, scope_created = BGPScope.objects.get_or_create(
                    router=bgp_router,
                    vrf=nb_vrf,
                )
                if scope_created:
                    bgp_scope.tags.add(AUTO_D_TAG)
            else:
                bgp_scope = BGPScope.objects.filter(
                    router=bgp_router, vrf=nb_vrf,
                ).first()
                scope_created = bgp_scope is None

            scope_action = EntryActionChoices.ACTION_NEW if scope_created else EntryActionChoices.ACTION_CONFIRMED
            scope_label = vrf_name if nb_vrf else "global"
            self._record_entry(
                action=scope_action,
                collector_type=self._collector_type,
                device=device,
                detected_values={"local_as": data["local_as"], "vrf": vrf_name if nb_vrf else None},
                object_instance=bgp_scope,
                object_repr=f"BGPScope {device} {scope_label}",
            )

            if not self._should_apply() and bgp_scope is None:
                continue

            for peer_data in peers:
                nb_ip = peer_data.get("nb_ip")
                nb_asn = peer_data.get("nb_asn")
                if nb_ip is None:
                    continue

                if self._should_apply():
                    bgp_peer, peer_created = BGPPeer.objects.get_or_create(
                        scope=bgp_scope,
                        peer=nb_ip,
                        defaults={"remote_as": nb_asn},
                    )
                    if peer_created:
                        bgp_peer.tags.add(AUTO_D_TAG)
                else:
                    bgp_peer = BGPPeer.objects.filter(
                        scope=bgp_scope, peer=nb_ip,
                    ).first()
                    peer_created = bgp_peer is None

                peer_action = EntryActionChoices.ACTION_NEW if peer_created else EntryActionChoices.ACTION_CONFIRMED
                self._record_entry(
                    action=peer_action,
                    collector_type=self._collector_type,
                    device=device,
                    detected_values={
                        "remote_address": peer_data["remote_address"],
                        "remote_as": peer_data["as_number"],
                        "local_as": data["local_as"],
                        "vrf": vrf_name if nb_vrf else None,
                    },
                    object_instance=bgp_peer,
                    object_repr=f"BGPPeer {peer_data['remote_address']} AS{peer_data['as_number']}",
                )

    def ospf(self, driver: NetworkDriver):
        """Collect OSPF data. Dispatches to vendor-specific implementation."""
        impl = self._get_vendor_method("ospf")
        impl(driver)

    def _ospf_junos(self, driver):
        """Junos OSPF collection via CLI."""
        output = self._napalm_rpc(driver.cli, "OSPF data", ["show ospf neighbor"])
        if output is None:
            return
        raw = output.get("show ospf neighbor", "")

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
                object_repr=f"OSPF neighbor {get_absolute_url_markdown(existing_ip) if existing_ip else neighbor_ip} (RID: {router_id})",
            )

            if self._should_apply():
                try:
                    ip_obj, created = get_or_create_ip(
                        f"{neighbor_ip}/32",
                        description=(
                            f"OSPF neighbor (Router ID: {router_id}) discovered on "
                            f"{self._current_device} ({self._now.date()})"
                        ),
                    )
                except IPAddress.MultipleObjectsReturned:
                    self._log_warning(
                        duplicate_object_warning("IP", f"{neighbor_ip}/32")
                    )
                    continue
                if created:
                    self._log_success(
                        f"Created OSPF neighbor IP {get_absolute_url_markdown(ip_obj, bold=True)} "
                        f"(Router ID: {router_id})."
                    )
                self._mark_entry_applied(ospf_entry, ip_obj, object_repr=f"OSPF neighbor {get_absolute_url_markdown(ip_obj)} (RID: {router_id})")

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
        except (ImportError, RuntimeError):
            self._log_warning("netbox-routing models could not be loaded; skipping OSPF integration.")
            return

        try:
            instance = OSPFInstance.objects.filter(
                device=self._current_device,
            ).first()
            if instance:
                self._log_info(
                    f"Found OSPF instance `{instance}` for {self._current_device} "
                    f"in netbox-routing. Neighbor: {neighbor_data['address']} "
                    f"(State: {neighbor_data['state']})"
                )
        except (NapalmException, django.db.IntegrityError, ValueError, AttributeError) as exc:
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
                    connection_ips = get_connection_ips(
                        self._current_device,
                        self.plan.connection_target,
                    )
                except ValueError:
                    self._log_warning(
                        "Device has no usable IP address configured. Skipping."
                    )
                    continue

                connected = False
                for ip, label in connection_ips:
                    self._log_info(f"Connecting via {label} IP `{ip}`")
                    try:
                        with self._napalm_driver(
                            ip,
                            self._napalm_username,
                            self._napalm_password,
                            optional_args=self._napalm_args,
                        ) as driver:
                            # Lookup the collection method and call it
                            getattr(self, self._collector_type)(driver)
                        connected = True
                        break
                    except AttributeError as exc:
                        raise NotImplementedError from exc
                    except ConnectionException as exc:
                        detail = exc.__cause__ or exc
                        self._log_warning(f"Connection failed via {label} IP `{ip}`: {detail}")

                if not connected:
                    self._log_failure("All connection attempts failed.")
        except Exception as exc:
            # Safety net: mark the report as failed on unhandled exceptions
            self._report.update_summary()
            self._report.completed_at = timezone.now()
            self._report.status = ReportStatusChoices.STATUS_FAILED
            self._report.error_message = str(exc)[:2000]
            self._report.save(update_fields=["completed_at", "status", "error_message"])
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

    def _napalm_rpc(self, call, label, *args, **kwargs):
        """Execute a NAPALM RPC call with standard error handling.

        Returns the call result, or None if the call failed.
        """
        try:
            return call(*args, **kwargs)
        except (CommandErrorException, CommandTimeoutException, ConnectionException) as exc:
            self._log_failure(f"Failed to retrieve {label}: {exc}")
            return None
        except NotImplementedError:
            self._log_info(f"Driver does not support {label}, skipping.")
            return None

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
