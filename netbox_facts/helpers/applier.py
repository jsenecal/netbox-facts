"""Entry lifecycle logic (apply, skip, retry, un-skip) for FactsReport entries."""

import ipaddress
import logging

from dcim.models.device_components import Interface, InventoryItem, ModuleBay
from dcim.models.devices import Device
from dcim.models.modules import ModuleType
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from extras.choices import JournalEntryKindChoices
from extras.models.models import JournalEntry
from ipam.models.ip import IPAddress, Prefix
from ipam.models.vrfs import VRF
from rest_framework.exceptions import ValidationError as DRFValidationError

from netbox_facts.choices import (
    REVIEW_REPORT_STATUSES,
    CollectionTypeChoices,
    EntryActionChoices,
    EntryKindChoices,
    EntryStatusChoices,
    ReportStatusChoices,
)
from netbox_facts.constants import AUTO_D_TAG
from netbox_facts.helpers.netbox import (
    claim_device_interface,
    create_module,
    get_or_create_interface,
    get_or_create_ip,
    get_or_create_mac,
    resolve_device_by_name,
    resolve_vrf,
    resolve_vrf_or_fail,
    update_or_replace_module,
)
from netbox_facts.models.mac import MACAddress

logger = logging.getLogger("netbox_facts")

NO_RIR_MESSAGE = "No RIR exists in NetBox; cannot create ASN {as_number}"

ERROR_TYPE_VALIDATION = "validation"
ERROR_TYPE_ERROR = "error"
ERROR_KEY_ALL = "__all__"
ERROR_TYPE_KEY = "error_type"


def apply_entries(report, entry_pks):
    """
    Apply selected pending entries in a report.
    Dispatches to per-collector-type handlers.
    Returns (applied_count, failed_count).
    """
    entries = report.entries.pending().filter(pk__in=entry_pks)
    applied = 0
    failed = 0
    now = timezone.now()

    with transaction.atomic():
        for entry in entries:
            handler = APPLY_HANDLERS.get(entry.collector_type)
            if handler is None:
                _mark_entry_failed(
                    entry,
                    ValueError(f"No apply handler for collector type '{entry.collector_type}'"),
                )
                failed += 1
                continue

            entry.status = EntryStatusChoices.STATUS_APPLYING
            entry.save(update_fields=["status"])

            try:
                with transaction.atomic():
                    handler(entry, now)
                    entry.status = EntryStatusChoices.STATUS_APPLIED
                    entry.applied_at = now
                    entry.error_message = ""
                    entry.apply_error = None
                    entry.save(
                        update_fields=[
                            "status",
                            "applied_at",
                            "object_type",
                            "object_id",
                            "error_message",
                            "apply_error",
                        ]
                    )
                applied += 1
            except Exception as exc:
                _mark_entry_failed(entry, exc)
                failed += 1
                logger.warning("Failed to apply entry %s: %s", entry.pk, exc)

        _update_report_status(report)
    return applied, failed


def build_apply_error(exc):
    """Structure an apply failure so a reviewer can see what NetBox rejected.

    Validation failures keep their field addressing ({field: [messages]});
    anything else -- an unreachable device, a missing dependency -- is
    addressed to the entry as a whole under __all__. The error_type key
    tells the two apart.
    """
    if isinstance(exc, DjangoValidationError):
        messages = exc.message_dict if hasattr(exc, "error_dict") else {ERROR_KEY_ALL: exc.messages}
    elif isinstance(exc, DRFValidationError):
        detail = exc.detail
        messages = detail if isinstance(detail, dict) else {ERROR_KEY_ALL: detail}
    else:
        return {ERROR_TYPE_KEY: ERROR_TYPE_ERROR, ERROR_KEY_ALL: [str(exc)[:1000]]}

    structured = {field: normalize_error_messages(value) for field, value in messages.items()}
    return {ERROR_TYPE_KEY: ERROR_TYPE_VALIDATION, **structured}


def normalize_error_messages(value):
    """Normalize one field's messages to a list of plain strings.

    Public because the display side normalizes the same way when reading
    a stored payload back.
    """
    if isinstance(value, (list, tuple)):
        return [str(message) for message in value]
    return [str(value)]


def _error_summary(apply_error):
    """Flatten a structured apply error into a single log/detail line."""
    parts = []
    for field, messages in apply_error.items():
        if field == ERROR_TYPE_KEY:
            continue
        prefix = "" if field == ERROR_KEY_ALL else f"{field}: "
        parts.extend(f"{prefix}{message}" for message in messages)
    return "; ".join(parts)[:1000]


def _mark_entry_failed(entry, exc):
    """Record a failed apply: a summary line plus the structured error."""
    entry.apply_error = build_apply_error(exc)
    entry.status = EntryStatusChoices.STATUS_FAILED
    entry.error_message = _error_summary(entry.apply_error)
    entry.save(update_fields=["status", "error_message", "apply_error"])


def _transition_entries(report, entry_pks, from_status, to_status, **reset_fields):
    """Move selected entries of a report from one status to another.

    Every transition is scoped the same two ways -- the report that owns
    the entries, and the status the transition is allowed to start from --
    so entries in another status, and entries belonging to another report,
    are ignored rather than moved. Extra keyword arguments reset fields
    that belong to the status being left behind. Returns the number of
    entries moved.
    """
    return report.entries.for_status(from_status).filter(pk__in=entry_pks).update(status=to_status, **reset_fields)


def skip_entries(report, entry_pks):
    """Bulk-skip selected pending entries."""
    count = _transition_entries(
        report,
        entry_pks,
        EntryStatusChoices.STATUS_PENDING,
        EntryStatusChoices.STATUS_SKIPPED,
    )
    _update_report_status(report)
    return count


def retry_entries(report, entry_pks):
    """Return selected failed entries to pending and re-apply them.

    A failed entry is a dead end otherwise: the apply path is pending-only
    by design, so retrying has to clear the previous failure and put the
    entry back in the pending state the applier accepts. The re-apply is
    immediate because the reviewer asking for a retry is asking for the
    outcome, not for the entry to reappear on the pending tab.

    Entries that did not fail, and entries belonging to another report,
    are ignored. Returns (applied_count, failed_count).
    """
    # The PKs are resolved before the transition because the apply path
    # that follows can no longer recognize these entries by status.
    failed_pks = list(
        report.entries.for_status(EntryStatusChoices.STATUS_FAILED)
        .filter(pk__in=entry_pks)
        .values_list("pk", flat=True)
    )
    if not failed_pks:
        return 0, 0

    _transition_entries(
        report,
        failed_pks,
        EntryStatusChoices.STATUS_FAILED,
        EntryStatusChoices.STATUS_PENDING,
        error_message="",
        apply_error=None,
    )
    return apply_entries(report, failed_pks)


def unskip_entries(report, entry_pks):
    """Return selected skipped entries to pending, without applying them.

    Un-skipping is a reconsideration, not an approval: the entry goes back
    on the pending tab so the reviewer can look at it again and decide.

    Entries in any other status, and entries belonging to another report,
    are ignored. Returns the number of entries returned to pending.
    """
    count = _transition_entries(
        report,
        entry_pks,
        EntryStatusChoices.STATUS_SKIPPED,
        EntryStatusChoices.STATUS_PENDING,
    )
    _update_report_status(report)
    return count


def _review_status(statuses):
    """Return the status of a report that still holds undecided entries.

    REVIEW_REPORT_STATUSES names the pair in review order -- untouched while
    nothing has been decided, then half-decided once something has -- and
    taking both from there is what keeps this transition and the review
    backlog the dashboard counts and links describing one set of statuses.
    """
    untouched, half_decided = REVIEW_REPORT_STATUSES
    return untouched if statuses <= {EntryStatusChoices.STATUS_PENDING} else half_decided


def _update_report_status(report):
    """Recompute report status from entry status distribution."""
    report.update_summary()

    statuses = set(report.entries.values_list("status", flat=True).distinct())

    if not statuses or EntryStatusChoices.STATUS_PENDING in statuses:
        # Still under review. completed_at is deliberately left as it stands:
        # a report reopened by an un-skip keeps the completion it recorded.
        report.status = _review_status(statuses)
    elif statuses == {EntryStatusChoices.STATUS_FAILED}:
        report.status = ReportStatusChoices.STATUS_FAILED
        report.completed_at = timezone.now()
    else:
        # Every entry resolved: applied, skipped, or a mix that includes a
        # failure. Anything applied makes the report an applied one.
        if EntryStatusChoices.STATUS_APPLIED in statuses:
            report.status = ReportStatusChoices.STATUS_APPLIED
        else:
            report.status = ReportStatusChoices.STATUS_COMPLETED
        report.completed_at = timezone.now()

    report.save(update_fields=["status", "completed_at"])


def _set_entry_object(entry, obj):
    """Set the GenericFK on an entry from an object instance."""
    if obj and hasattr(obj, "pk") and obj.pk:
        entry.object_type = ContentType.objects.get_for_model(obj)
        entry.object_id = obj.pk


def get_or_create_asn(as_number):
    """Get or create an ipam ASN, returning None when it cannot be created.

    ipam.ASN.rir is a non-nullable foreign key, so creating an ASN requires
    at least one RIR in NetBox. When the ASN does not already exist and no
    RIR is available, return None instead of letting the insert fail with an
    IntegrityError; callers decide whether a missing ASN is a warning or an
    error.
    """
    from ipam.models import ASN, RIR

    as_number = int(as_number)
    nb_asn = ASN.objects.filter(asn=as_number).first()
    if nb_asn is not None:
        return nb_asn
    rir = RIR.objects.first()
    if rir is None:
        return None
    nb_asn, _ = ASN.objects.get_or_create(asn=as_number, defaults={"rir": rir})
    return nb_asn


# --- Per-collector apply handlers ---


def _apply_arp_entry(entry, now):
    """Apply an ARP/NDP-discovered entry.

    The collector creates two entries per ARP/NDP hit, one of kind
    mac_address and one of kind ip_address. Each entry only creates or links
    to its own object type.
    """
    dv = entry.detected_values
    mac_addr = dv.get("mac", "")
    ip_str = dv.get("ip", "")

    if entry.entry_kind == EntryKindChoices.KIND_MAC_ADDRESS:
        # MAC entry: create/update MAC and link to interface
        if not mac_addr:
            return
        netbox_mac, created = get_or_create_mac(mac_addr)
        netbox_mac.last_seen = now
        netbox_mac.save()

        iface_name = dv.get("interface", "")
        if iface_name:
            try:
                nb_iface = entry.device.vc_interfaces().get(name=iface_name)
                netbox_mac.interfaces.add(nb_iface)
            except Interface.DoesNotExist:
                logger.warning(
                    "Interface %s not found on device %s for ARP entry %s", iface_name, entry.device, entry.pk
                )
        _set_entry_object(entry, netbox_mac)
    else:
        # IP entry: create/update IP and associate with MAC
        if not ip_str:
            return

        vrf = resolve_vrf_or_fail(dv.get("vrf"))

        nb_ip, created = get_or_create_ip(
            ip_str,
            vrf=vrf,
            description=f"Automatically discovered on {now}",
        )

        # Associate IP with MAC if both exist
        if mac_addr:
            netbox_mac, _ = MACAddress.objects.get_or_create(mac_address=mac_addr)
            netbox_mac.ip_addresses.add(nb_ip)
        _set_entry_object(entry, nb_ip)


def _apply_ndp_entry(entry, now):
    """Apply an NDP entry (same logic as ARP)."""
    _apply_arp_entry(entry, now)


def _apply_inventory_entry(entry, now):
    """Apply an inventory entry (serial number update, InventoryItem, or Module)."""
    if entry.entry_kind == EntryKindChoices.KIND_MODULE:
        if entry.action == EntryActionChoices.ACTION_STALE:
            _apply_stale_module(entry)
        else:
            _apply_module(entry)
        return

    if entry.entry_kind == EntryKindChoices.KIND_INVENTORY_ITEM:
        if entry.action == EntryActionChoices.ACTION_STALE:
            _apply_stale_inventory_item(entry)
        else:
            _apply_inventory_item(entry)
        return

    # Legacy device serial update
    dv = entry.detected_values
    new_serial = dv.get("serial_number", "")

    if new_serial and entry.action == EntryActionChoices.ACTION_CHANGED:
        Device.objects.filter(pk=entry.device.pk).update(serial=new_serial)
        entry.device.refresh_from_db()

    _set_entry_object(entry, entry.device)


def _apply_inventory_item(entry):
    """Apply a chassis InventoryItem entry (NEW or CHANGED)."""
    dv = entry.detected_values
    name = dv.get("name", "")
    parent_name = dv.get("parent_name")
    serial = dv.get("serial", "")
    part_id = dv.get("part_id", "")
    description = dv.get("description", "")

    # Resolve parent InventoryItem if this is a sub-module
    parent = None
    if parent_name:
        parent = InventoryItem.objects.filter(
            device=entry.device,
            name=parent_name,
        ).first()

    item, created = InventoryItem.objects.get_or_create(
        device=entry.device,
        name=name,
        defaults={
            "parent": parent,
            "serial": serial,
            "part_id": part_id,
            "description": description,
            "discovered": True,
        },
    )
    if created:
        item.tags.add(AUTO_D_TAG)
    elif entry.action == EntryActionChoices.ACTION_CHANGED:
        item.serial = serial
        item.part_id = part_id
        item.description = description
        item.save(update_fields=["serial", "part_id", "description"])

    _set_entry_object(entry, item)


def _apply_stale_inventory_item(entry):
    """Delete a stale discovered InventoryItem."""
    cv = entry.current_values
    name = cv.get("name", "")
    try:
        item = InventoryItem.objects.get(
            device=entry.device,
            name=name,
            discovered=True,
        )
        item.delete()
    except InventoryItem.DoesNotExist:
        logger.warning("InventoryItem %s not found for stale entry %s", name, entry.pk)
    _set_entry_object(entry, entry.device)


def _apply_module(entry):
    """Apply a chassis Module entry (NEW or CHANGED)."""
    dv = entry.detected_values
    module_bay_id = dv.get("module_bay_id")
    module_type_id = dv.get("module_type_id")
    serial = dv.get("serial", "")

    bay = ModuleBay.objects.get(pk=module_bay_id)
    mod_type = ModuleType.objects.get(pk=module_type_id)

    if entry.action == EntryActionChoices.ACTION_NEW:
        mod = create_module(entry.device, bay, mod_type, serial)
    elif entry.action == EntryActionChoices.ACTION_CHANGED:
        mod = getattr(bay, "installed_module", None)
        if mod is None:
            raise ValueError(f"No installed module in bay {bay.name} to update")
        mod = update_or_replace_module(entry.device, bay, mod, mod_type, serial)

    _set_entry_object(entry, mod)


def _apply_stale_module(entry):
    """Delete a stale auto-discovered Module."""
    cv = entry.current_values
    module_bay_id = cv.get("module_bay_id")

    try:
        bay = ModuleBay.objects.get(pk=module_bay_id)
        mod = getattr(bay, "installed_module", None)
    except ModuleBay.DoesNotExist:
        logger.warning("ModuleBay %s not found for stale module entry %s", module_bay_id, entry.pk)
        _set_entry_object(entry, entry.device)
        return

    if mod is not None and mod.tags.filter(name=AUTO_D_TAG).exists():
        mod.delete()

    _set_entry_object(entry, entry.device)


def _apply_interfaces_entry(entry, now):
    """Apply an interface entry (MAC, LAG membership, IP address, VRF, or stale IP)."""
    dv = entry.detected_values

    if entry.entry_kind == EntryKindChoices.KIND_VRF:
        _apply_vrf_entry(entry)
    elif entry.action == EntryActionChoices.ACTION_STALE:
        _apply_stale_interfaces_ip(entry)
    elif entry.entry_kind == EntryKindChoices.KIND_LAG:
        _apply_interfaces_lag(entry, dv)
    elif entry.entry_kind == EntryKindChoices.KIND_IP_ADDRESS:
        _apply_interfaces_ip(entry, dv, now)
    else:
        _apply_interfaces_mac(entry, dv, now)


def _apply_interfaces_mac(entry, dv, now):
    """Apply an interface MAC entry, creating the interface if missing."""
    mac_addr = dv.get("mac_address", "")
    iface_name = dv.get("interface", "")

    nb_iface = None
    if iface_name:
        nb_iface = get_or_create_interface(entry.device, iface_name)

    if not mac_addr:
        # MAC-less interface entry (detect-only run against a missing
        # interface, or a logical carrier): creating the interface is
        # the whole change.
        if nb_iface is not None:
            _set_entry_object(entry, nb_iface)
        return

    netbox_mac, created = get_or_create_mac(mac_addr)

    if nb_iface is not None:
        claim_device_interface(netbox_mac, nb_iface)

    netbox_mac.discovery_method = CollectionTypeChoices.TYPE_INTERFACES
    netbox_mac.last_seen = now
    netbox_mac.save()
    _set_entry_object(entry, netbox_mac)


def _apply_interfaces_lag(entry, dv):
    """Apply a LAG membership entry."""
    iface_name = dv["interface"]
    ae_name = dv["lag_parent"]
    nb_iface = get_or_create_interface(entry.device, iface_name)
    ae_iface = get_or_create_interface(entry.device, ae_name)
    nb_iface.lag = ae_iface
    nb_iface.save()
    _set_entry_object(entry, nb_iface)


def _apply_interfaces_ip(entry, dv, now):
    """Apply an IP address assignment entry."""
    cidr = dv["ip_address"]
    li_name = dv["logical_interface"]
    vrf_name = dv.get("vrf")
    prefix_str = dv.get("prefix")

    vrf = resolve_vrf_or_fail(vrf_name)

    nb_li = get_or_create_interface(entry.device, li_name)

    # Create prefix if non-host-route
    if prefix_str:
        net = ipaddress.ip_network(prefix_str, strict=False)
        if net.num_addresses > 1:
            nb_prefix, prefix_created = Prefix.objects.get_or_create(
                prefix=prefix_str,
                vrf=vrf,
                defaults={
                    "description": f"Discovered on {entry.device} ({now.date()})",
                },
            )
            if prefix_created:
                nb_prefix.tags.add(AUTO_D_TAG)

    # Create/get IPAddress
    nb_ip, created = get_or_create_ip(
        cidr,
        vrf=vrf,
        assigned_object=nb_li,
        description=f"Discovered on {entry.device} ({now.date()})",
    )
    if not created and nb_ip.assigned_object is None:
        nb_ip.assigned_object = nb_li
        nb_ip.save()
    elif nb_ip.assigned_object != nb_li and nb_ip.tags.filter(name=AUTO_D_TAG).exists():
        nb_ip.assigned_object = nb_li
        nb_ip.save()
    _set_entry_object(entry, nb_ip)


def _apply_stale_interfaces_ip(entry):
    """Unassign a stale auto-discovered IP address."""
    cv = entry.current_values
    cidr = cv.get("ip_address", "")
    vrf_name = cv.get("vrf")

    vrf = None
    if vrf_name:
        try:
            vrf = resolve_vrf(vrf_name)
        except VRF.DoesNotExist:
            logger.warning("VRF %s not found for stale IP entry %s", vrf_name, entry.pk)

    try:
        nb_ip = IPAddress.objects.get(address=cidr, vrf=vrf)
    except IPAddress.DoesNotExist:
        logger.warning("IP %s not found for stale entry %s", cidr, entry.pk)
        return

    if not nb_ip.tags.filter(name=AUTO_D_TAG).exists():
        return

    nb_ip.assigned_object = None
    nb_ip.save()
    _set_entry_object(entry, nb_ip)


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
    remote_device = resolve_device_by_name(remote_device_name)
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

    netbox_mac, created = get_or_create_mac(mac_addr)

    if iface_name:
        try:
            nb_iface = entry.device.vc_interfaces().get(name=iface_name)
            netbox_mac.interfaces.add(nb_iface)
        except Interface.DoesNotExist:
            logger.warning(
                "Interface %s not found on device %s for ethernet switching entry %s",
                iface_name,
                entry.device,
                entry.pk,
            )

    netbox_mac.discovery_method = CollectionTypeChoices.TYPE_L2
    netbox_mac.last_seen = now
    netbox_mac.save()
    _set_entry_object(entry, netbox_mac)


def _apply_vrf_entry(entry):
    """Apply a missing-VRF entry by creating the VRF."""
    name = entry.detected_values.get("name", "")
    if not name:
        raise ValueError("VRF entry has no name")
    vrf, created = VRF.objects.get_or_create(name=name)
    _set_entry_object(entry, vrf)


def _apply_bgp_entry(entry, now):
    """Apply a BGP peer IP/ASN entry."""
    if entry.entry_kind == EntryKindChoices.KIND_VRF:
        return _apply_vrf_entry(entry)
    if entry.entry_kind == EntryKindChoices.KIND_BGP_ROUTER:
        return _apply_bgp_router_entry(entry)
    if entry.entry_kind == EntryKindChoices.KIND_BGP_SCOPE:
        return _apply_bgp_scope_entry(entry)
    if entry.entry_kind == EntryKindChoices.KIND_BGP_PEER:
        return _apply_bgp_peer_routing_entry(entry)

    dv = entry.detected_values
    remote_address = dv.get("remote_address", "")
    as_number = dv.get("remote_as")
    vrf_name = dv.get("vrf")

    if not remote_address:
        return

    nb_vrf = resolve_vrf_or_fail(vrf_name)

    try:
        ip_obj = ipaddress.ip_address(remote_address)
        prefix_len = 32 if ip_obj.version == 4 else 128
        ip_str = f"{remote_address}/{prefix_len}"
    except ValueError as exc:
        raise ValueError(f"Invalid IP: {remote_address}") from exc

    nb_ip, created = get_or_create_ip(
        ip_str,
        vrf=nb_vrf,
        description=f"BGP peer AS{as_number} discovered on {now}",
    )

    if as_number is not None and get_or_create_asn(as_number) is None:
        logger.warning("%s (BGP entry %s)", NO_RIR_MESSAGE.format(as_number=as_number), entry.pk)

    _set_entry_object(entry, nb_ip)


def _apply_ospf_entry(entry, now):
    """Apply an OSPF neighbor IP entry."""
    dv = entry.detected_values
    address = dv.get("address", "")

    if not address:
        return

    ip_obj, created = get_or_create_ip(
        f"{address}/32",
        description=(
            f"OSPF neighbor (Router ID: {dv.get('router_id', '')}) discovered on {entry.device} ({now.date()})"
        ),
    )
    _set_entry_object(entry, ip_obj)


def _apply_evpn_entry(entry, now):
    """Apply an EVPN MAC entry."""
    dv = entry.detected_values
    mac_addr = dv.get("mac", "")

    if not mac_addr:
        return

    netbox_mac, created = get_or_create_mac(mac_addr)
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


def _get_local_bgp_router(entry, local_as):
    """Get or create the netbox-routing BGPRouter for an entry's device.

    Resolves the device's local ASN first; ASN creation requires an RIR,
    so an empty RIR table fails with a clear ValueError instead of an
    IntegrityError. A router created here is tagged as auto-discovered.
    """
    from netbox_routing.models import BGPRouter

    asn_obj = get_or_create_asn(local_as)
    if asn_obj is None:
        raise ValueError(NO_RIR_MESSAGE.format(as_number=local_as))

    device = entry.device
    router, created = BGPRouter.objects.get_or_create(
        assigned_object_type=ContentType.objects.get_for_model(device),
        assigned_object_id=device.pk,
        asn=asn_obj,
    )
    if created:
        router.tags.add(AUTO_D_TAG)
    return router


def _apply_bgp_router_entry(entry):
    """Create a BGPRouter from a detect-only report entry."""
    local_as = entry.detected_values.get("local_as")
    if local_as is None:
        raise ValueError("BGPRouter entry has no local_as")

    router = _get_local_bgp_router(entry, local_as)
    _set_entry_object(entry, router)


def _apply_bgp_scope_entry(entry):
    """Create a BGPScope from a detect-only report entry."""
    from netbox_routing.models import BGPScope

    dv = entry.detected_values
    router = _get_local_bgp_router(entry, dv.get("local_as"))
    nb_vrf = resolve_vrf_or_fail(dv.get("vrf"))

    scope, created = BGPScope.objects.get_or_create(
        router=router,
        vrf=nb_vrf,
    )
    if created:
        scope.tags.add(AUTO_D_TAG)
    _set_entry_object(entry, scope)


def _apply_bgp_peer_routing_entry(entry):
    """Create a BGPPeer (netbox-routing) from a detect-only report entry."""
    from netbox_routing.models import BGPPeer, BGPScope

    dv = entry.detected_values
    remote_address = dv.get("remote_address", "")
    as_number = dv.get("remote_as")

    # Build chain: Router -> Scope
    router = _get_local_bgp_router(entry, dv.get("local_as"))
    nb_vrf = resolve_vrf_or_fail(dv.get("vrf"))

    scope, _ = BGPScope.objects.get_or_create(
        router=router,
        vrf=nb_vrf,
    )

    # IP + remote ASN
    ip_obj = ipaddress.ip_address(remote_address)
    prefix_len = 32 if ip_obj.version == 4 else 128
    ip_str = f"{remote_address}/{prefix_len}"
    nb_ip, _ = get_or_create_ip(ip_str, vrf=nb_vrf)

    nb_remote_asn = None
    if as_number is not None:
        nb_remote_asn = get_or_create_asn(as_number)

    peer, created = BGPPeer.objects.get_or_create(
        scope=scope,
        peer=nb_ip,
        defaults={"remote_as": nb_remote_asn},
    )
    if created:
        peer.tags.add(AUTO_D_TAG)
    _set_entry_object(entry, peer)


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
