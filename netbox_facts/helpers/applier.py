"""Apply/skip logic for FactsReport entries."""

import ipaddress
import logging

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from dcim.models.device_components import Interface
from dcim.models.devices import Device
from extras.choices import JournalEntryKindChoices
from extras.models.models import JournalEntry
from ipam.models.ip import IPAddress

from netbox_facts.choices import (
    CollectionTypeChoices,
    EntryActionChoices,
    EntryStatusChoices,
    ReportStatusChoices,
)
from netbox_facts.models.mac import MACAddress

logger = logging.getLogger("netbox_facts")

AUTO_D_TAG = "Automatically Discovered"


def apply_entries(report, entry_pks):
    """
    Apply selected pending entries in a report.
    Dispatches to per-collector-type handlers.
    Returns (applied_count, failed_count).
    """
    entries = report.entries.filter(pk__in=entry_pks, status=EntryStatusChoices.STATUS_PENDING)
    applied = 0
    failed = 0
    now = timezone.now()

    for entry in entries:
        handler = APPLY_HANDLERS.get(entry.collector_type)
        if handler is None:
            entry.status = EntryStatusChoices.STATUS_FAILED
            entry.error_message = f"No apply handler for collector type '{entry.collector_type}'"
            entry.save(update_fields=["status", "error_message"])
            failed += 1
            continue

        try:
            handler(entry, now)
            entry.status = EntryStatusChoices.STATUS_APPLIED
            entry.applied_at = now
            entry.save(update_fields=["status", "applied_at", "object_type", "object_id"])
            applied += 1
        except Exception as exc:
            entry.status = EntryStatusChoices.STATUS_FAILED
            entry.error_message = str(exc)[:1000]
            entry.save(update_fields=["status", "error_message"])
            failed += 1
            logger.warning("Failed to apply entry %s: %s", entry.pk, exc)

    _update_report_status(report)
    return applied, failed


def skip_entries(report, entry_pks):
    """Bulk-skip selected pending entries."""
    count = report.entries.filter(
        pk__in=entry_pks, status=EntryStatusChoices.STATUS_PENDING
    ).update(status=EntryStatusChoices.STATUS_SKIPPED)
    _update_report_status(report)
    return count


def _update_report_status(report):
    """Recompute report status from entry status distribution."""
    report.update_summary()

    statuses = set(
        report.entries.values_list("status", flat=True).distinct()
    )

    if not statuses or statuses == {EntryStatusChoices.STATUS_PENDING}:
        report.status = ReportStatusChoices.STATUS_PENDING
    elif statuses == {EntryStatusChoices.STATUS_APPLIED}:
        report.status = ReportStatusChoices.STATUS_APPLIED
        report.completed_at = timezone.now()
    elif statuses == {EntryStatusChoices.STATUS_FAILED}:
        report.status = ReportStatusChoices.STATUS_FAILED
        report.completed_at = timezone.now()
    elif EntryStatusChoices.STATUS_PENDING not in statuses:
        # All entries resolved (mix of applied/skipped/failed)
        if EntryStatusChoices.STATUS_APPLIED in statuses:
            report.status = ReportStatusChoices.STATUS_APPLIED
        else:
            report.status = ReportStatusChoices.STATUS_COMPLETED
        report.completed_at = timezone.now()
    else:
        report.status = ReportStatusChoices.STATUS_PARTIAL

    report.save(update_fields=["status", "completed_at"])


def _set_entry_object(entry, obj):
    """Set the GenericFK on an entry from an object instance."""
    if obj and hasattr(obj, "pk") and obj.pk:
        entry.object_type = ContentType.objects.get_for_model(obj)
        entry.object_id = obj.pk


# --- Per-collector apply handlers ---


def _apply_arp_entry(entry, now):
    """Apply an ARP/NDP-discovered entry.

    The collector creates two entries per ARP/NDP hit: one for MAC (object_repr
    starts with "MAC") and one for IP (starts with "IP"). Each entry should
    only create/link to its respective object type.
    """
    dv = entry.detected_values
    mac_addr = dv.get("mac", "")
    ip_str = dv.get("ip", "")
    is_mac_entry = entry.object_repr.startswith("MAC")

    if is_mac_entry:
        # MAC entry: create/update MAC and link to interface
        if not mac_addr:
            return
        netbox_mac, created = MACAddress.objects.get_or_create(mac_address=mac_addr)
        if created:
            netbox_mac.tags.add(AUTO_D_TAG)
        netbox_mac.last_seen = now
        netbox_mac.save()

        iface_name = dv.get("interface", "")
        if iface_name:
            try:
                nb_iface = entry.device.vc_interfaces().get(name=iface_name)
                netbox_mac.interfaces.add(nb_iface)
            except Interface.DoesNotExist:
                pass
        _set_entry_object(entry, netbox_mac)
    else:
        # IP entry: create/update IP and associate with MAC
        if not ip_str:
            return

        from ipam.models.vrfs import VRF
        vrf = None
        vrf_name = dv.get("vrf")
        if vrf_name:
            try:
                vrf = VRF.objects.get(name=vrf_name)
            except VRF.DoesNotExist:
                pass

        nb_ip, created = IPAddress.objects.get_or_create(
            address=ip_str,
            vrf=vrf,
            defaults={"description": f"Automatically discovered on {now}"},
        )
        if created:
            nb_ip.tags.add(AUTO_D_TAG)

        # Associate IP with MAC if both exist
        if mac_addr:
            netbox_mac, _ = MACAddress.objects.get_or_create(mac_address=mac_addr)
            netbox_mac.ip_addresses.add(nb_ip)
        _set_entry_object(entry, nb_ip)


def _apply_ndp_entry(entry, now):
    """Apply an NDP entry (same logic as ARP)."""
    _apply_arp_entry(entry, now)


def _apply_inventory_entry(entry, now):
    """Apply an inventory entry (serial number update)."""
    dv = entry.detected_values
    new_serial = dv.get("serial_number", "")

    if new_serial and entry.action == EntryActionChoices.ACTION_CHANGED:
        Device.objects.filter(pk=entry.device.pk).update(serial=new_serial)
        entry.device.refresh_from_db()

    _set_entry_object(entry, entry.device)


def _apply_interfaces_entry(entry, now):
    """Apply an interface MAC entry."""
    dv = entry.detected_values
    mac_addr = dv.get("mac_address", "")
    iface_name = dv.get("interface", "")

    if not mac_addr:
        return

    netbox_mac, created = MACAddress.objects.get_or_create(mac_address=mac_addr)
    if created:
        netbox_mac.tags.add(AUTO_D_TAG)

    if iface_name:
        try:
            nb_iface = entry.device.vc_interfaces().get(name=iface_name)
            netbox_mac.device_interface = nb_iface
        except Interface.DoesNotExist:
            pass

    netbox_mac.discovery_method = CollectionTypeChoices.TYPE_INTERFACES
    netbox_mac.last_seen = now
    netbox_mac.save()
    _set_entry_object(entry, netbox_mac)


def _apply_lldp_entry(entry, now):
    """Apply an LLDP cable entry."""
    from dcim.choices import LinkStatusChoices
    from dcim.models.cables import Cable

    dv = entry.detected_values
    local_iface_name = dv.get("local_interface", "")
    remote_device_name = dv.get("remote_device", "")
    remote_iface_name = dv.get("remote_interface", "")

    if not all([local_iface_name, remote_device_name, remote_iface_name]):
        raise ValueError("Missing LLDP entry data")

    local_iface = entry.device.vc_interfaces().get(name=local_iface_name)
    remote_device = Device.objects.get(name=remote_device_name)
    remote_iface = remote_device.vc_interfaces().get(name=remote_iface_name)

    if local_iface.cable_id is not None or remote_iface.cable_id is not None:
        raise ValueError("Interface already has a cable")

    cable = Cable(
        a_terminations=[local_iface],
        b_terminations=[remote_iface],
        status=LinkStatusChoices.STATUS_CONNECTED,
    )
    cable.full_clean()
    cable.save()
    cable.tags.add(AUTO_D_TAG)
    _set_entry_object(entry, cable)


def _apply_ethernet_switching_entry(entry, now):
    """Apply an ethernet switching MAC entry."""
    dv = entry.detected_values
    mac_addr = dv.get("mac", "")
    iface_name = dv.get("interface", "")

    if not mac_addr:
        return

    netbox_mac, created = MACAddress.objects.get_or_create(mac_address=mac_addr)
    if created:
        netbox_mac.tags.add(AUTO_D_TAG)

    if iface_name:
        try:
            nb_iface = entry.device.vc_interfaces().get(name=iface_name)
            netbox_mac.interfaces.add(nb_iface)
        except Interface.DoesNotExist:
            pass

    netbox_mac.discovery_method = CollectionTypeChoices.TYPE_L2
    netbox_mac.last_seen = now
    netbox_mac.save()
    _set_entry_object(entry, netbox_mac)


def _apply_bgp_entry(entry, now):
    """Apply a BGP peer IP/ASN entry."""
    from ipam.models import ASN, RIR
    from ipam.models.vrfs import VRF

    dv = entry.detected_values
    remote_address = dv.get("remote_address", "")
    as_number = dv.get("remote_as")
    vrf_name = dv.get("vrf")

    if not remote_address:
        return

    nb_vrf = None
    if vrf_name:
        try:
            nb_vrf = VRF.objects.get(name=vrf_name)
        except VRF.DoesNotExist:
            pass

    try:
        ip_obj = ipaddress.ip_address(remote_address)
        prefix_len = 32 if ip_obj.version == 4 else 128
        ip_str = f"{remote_address}/{prefix_len}"
    except ValueError as exc:
        raise ValueError(f"Invalid IP: {remote_address}") from exc

    nb_ip, created = IPAddress.objects.get_or_create(
        address=ip_str,
        vrf=nb_vrf,
        defaults={"description": f"BGP peer AS{as_number} discovered on {now}"},
    )
    if created:
        nb_ip.tags.add(AUTO_D_TAG)

    if as_number is not None:
        try:
            ASN.objects.get_or_create(
                asn=int(as_number),
                defaults={"rir": RIR.objects.first()},
            )
        except (RIR.DoesNotExist, TypeError):
            pass

    _set_entry_object(entry, nb_ip)


def _apply_ospf_entry(entry, now):
    """Apply an OSPF neighbor IP entry."""
    dv = entry.detected_values
    address = dv.get("address", "")

    if not address:
        return

    ip_obj, created = IPAddress.objects.get_or_create(
        address=f"{address}/32",
        defaults={
            "description": (
                f"OSPF neighbor (Router ID: {dv.get('router_id', '')}) "
                f"discovered on {entry.device} ({now.date()})"
            ),
        },
    )
    if created:
        ip_obj.tags.add(AUTO_D_TAG)
    _set_entry_object(entry, ip_obj)


def _apply_evpn_entry(entry, now):
    """Apply an EVPN MAC entry."""
    dv = entry.detected_values
    mac_addr = dv.get("mac", "")

    if not mac_addr:
        return

    netbox_mac, created = MACAddress.objects.get_or_create(mac_address=mac_addr)
    if created:
        netbox_mac.tags.add(AUTO_D_TAG)
    netbox_mac.discovery_method = CollectionTypeChoices.TYPE_EVPN
    netbox_mac.last_seen = now
    netbox_mac.save()
    _set_entry_object(entry, netbox_mac)


def _apply_l2_circuits_entry(entry, now):
    """Apply an L2 circuits entry (journal entry creation)."""
    dv = entry.detected_values
    raw_output = dv.get("raw_output", "")
    if raw_output:
        JournalEntry.objects.create(
            created=now,
            assigned_object=entry.device,
            kind=JournalEntryKindChoices.KIND_INFO,
            comments=f"L2 circuit data collected:\n```\n{raw_output[:2000]}\n```",
        )
    _set_entry_object(entry, entry.device)


APPLY_HANDLERS = {
    CollectionTypeChoices.TYPE_ARP: _apply_arp_entry,
    CollectionTypeChoices.TYPE_NDP: _apply_ndp_entry,
    CollectionTypeChoices.TYPE_INVENTORY: _apply_inventory_entry,
    CollectionTypeChoices.TYPE_INTERFACES: _apply_interfaces_entry,
    CollectionTypeChoices.TYPE_LLDP: _apply_lldp_entry,
    CollectionTypeChoices.TYPE_L2: _apply_ethernet_switching_entry,
    CollectionTypeChoices.TYPE_BGP: _apply_bgp_entry,
    CollectionTypeChoices.TYPE_OSPF: _apply_ospf_entry,
    CollectionTypeChoices.TYPE_EVPN: _apply_evpn_entry,
    CollectionTypeChoices.TYPE_L2CIRCTUITS: _apply_l2_circuits_entry,
}
