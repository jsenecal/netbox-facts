"""What NetBox holds right now for the subject of one report entry.

An entry stores the NetBox side of its comparison as it stood the moment
the device was polled. Minutes or days later a reviewer looks at it, and
NetBox may have moved on: somebody set the serial by hand, created the
interface, assigned the address. Re-reading that side costs no connection
to the device -- the device's own report is already stored -- so it is
done here, kind by kind.

Each reader answers two questions about one entry: what NetBox holds for
its subject now, and whether applying the entry would still change
anything. The second answer belongs with the first because only the kind
knows what its apply handler writes: an inventory item is satisfied by
three matching fields, a LAG membership by one parent, a missing VRF by
the VRF simply existing, and a stale entry by its object being gone.

Kinds absent from the table cannot be re-read without the device (a cable
needs both ends of a live topology, a BGP session the device's own view of
it), and snapshot_entry() says so by returning None rather than guessing.
"""

from dataclasses import dataclass
from typing import Any

from dcim.models.device_components import InventoryItem, ModuleBay
from django.core.exceptions import ValidationError as DjangoValidationError
from ipam.models.ip import IPAddress
from ipam.models.vrfs import VRF

from netbox_facts.choices import EntryActionChoices, EntryKindChoices
from netbox_facts.helpers.netbox import resolve_vrf
from netbox_facts.models.mac import MACAddress, MACAddressInterfaceRelation

__all__ = (
    "CURRENT_STATE_READERS",
    "EntryCurrentState",
    "snapshot_entry",
)


@dataclass(frozen=True)
class EntryCurrentState:
    """NetBox's side of one entry's comparison, read fresh.

    ``values`` replaces the entry's stored current_values and keeps the
    keys the collector records for that kind. ``instance`` is the live
    object the entry resolves to, or None when NetBox holds none.
    ``resolved`` is True when applying the entry would write nothing it
    has not already got.
    """

    values: dict
    instance: Any | None
    resolved: bool


def _is_removal(entry):
    """True when the entry proposes taking something out of NetBox."""
    return entry.action == EntryActionChoices.ACTION_STALE


def _absent(entry):
    """The state of an entry whose subject NetBox does not hold.

    There is nothing to snapshot and nothing to compare, so a removal is
    satisfied -- what it would have taken out is already gone -- and every
    other action is left for a reviewer.
    """
    return EntryCurrentState({}, None, _is_removal(entry))


def _subject_value(entry, key):
    """Read one key naming the entry's subject, from whichever side has it.

    A removal carries its subject in the snapshot NetBox gave at detect
    time; everything else carries it in what the device reported.
    """
    return (entry.detected_values or {}).get(key) or (entry.current_values or {}).get(key)


def _matches(entry, values, keys):
    """True when NetBox already holds what the device reported, key by key.

    Values are compared as strings because the two sides come from a JSON
    payload and from the ORM: a serial read back as an integer and the
    same serial read back as text are the same serial. A removal is never
    satisfied by a match -- it is satisfied by absence.
    """
    if _is_removal(entry):
        return False
    detected = entry.detected_values or {}
    return all(str(values.get(key, "")) == str(detected.get(key, "")) for key in keys)


def _device_interface(entry, name):
    """Resolve one of the entry device's interfaces by name, or None."""
    if not name:
        return None
    return entry.device.vc_interfaces().filter(name=name).first()


def _mac_address(address):
    """Resolve a MAC by address, tolerating a value NetBox cannot parse."""
    if not address:
        return None
    try:
        return MACAddress.objects.filter(mac_address=address).first()
    except (DjangoValidationError, ValueError):
        return None


def _device_state(entry):
    """A device entry proposes the serial the device reported.

    A payload with no serial proposes nothing, which the inventory apply
    handler treats the same way: it writes only on a serial change.
    """
    detected = (entry.detected_values or {}).get("serial_number", "")
    serial = entry.device.serial
    return EntryCurrentState(
        {"serial_number": serial},
        entry.device,
        not detected or serial == detected,
    )


def _inventory_item_state(entry):
    """A chassis item is satisfied by its serial, part and description."""
    name = _subject_value(entry, "name")
    item = InventoryItem.objects.filter(device=entry.device, name=name).first() if name else None
    if item is None:
        return _absent(entry)

    values = {
        "name": item.name,
        "serial": item.serial,
        "part_id": item.part_id,
        "description": item.description,
    }
    return EntryCurrentState(values, item, _matches(entry, values, ("serial", "part_id", "description")))


def _module_state(entry):
    """A module is satisfied by the right part, with the right serial, in its bay."""
    bay_id = _subject_value(entry, "module_bay_id")
    bay = ModuleBay.objects.filter(pk=bay_id).first() if bay_id else None
    installed = getattr(bay, "installed_module", None) if bay is not None else None
    if installed is None:
        return _absent(entry)

    values = {
        "module_bay_id": bay.pk,
        "module_type_id": installed.module_type_id,
        "serial": installed.serial,
    }
    return EntryCurrentState(values, installed, _matches(entry, values, ("module_type_id", "serial")))


def _lag_state(entry):
    """A LAG entry is satisfied by the member pointing at the right bundle."""
    detected = entry.detected_values or {}
    interface = _device_interface(entry, detected.get("interface"))
    if interface is None:
        # The member is the subject here, not the thing being removed, so
        # its absence leaves the membership unjudged rather than satisfied.
        return EntryCurrentState({}, None, False)

    parent = interface.lag.name if interface.lag else None
    return EntryCurrentState({"lag_parent": parent}, interface, parent == detected.get("lag_parent"))


def _claimed_mac(interface, mac_address):
    """Return (mac, claimed) for a MAC an interface is meant to be wearing.

    The interfaces apply handler claims the interface through
    MACAddress's one-to-one device_interface, so that claim -- not mere
    existence of the MAC -- is what makes such an entry a no-op. A MAC
    the entry does not name is claimed by definition.
    """
    if not mac_address:
        return None, True
    mac = _mac_address(mac_address)
    claimed = mac is not None and interface is not None and mac.device_interface_id == interface.pk
    return mac, claimed


def _interface_state(entry):
    """An interface entry is satisfied by the interface existing.

    A detect-only run records a missing interface with the whole payload
    it was about to use, MAC included, and the apply handler creates both
    the interface and that MAC claim -- so when the payload names a MAC,
    both halves have to be in place before the entry is a no-op.
    """
    detected = entry.detected_values or {}
    interface = _device_interface(entry, detected.get("interface"))
    if interface is None:
        return _absent(entry)

    values = {"interface": interface.name}
    mac_address = detected.get("mac_address") or ""
    if not mac_address:
        return EntryCurrentState(values, interface, not _is_removal(entry))

    mac, claimed = _claimed_mac(interface, mac_address)
    values["mac_address"] = str(mac.mac_address) if mac is not None else None
    return EntryCurrentState(values, mac or interface, claimed and not _is_removal(entry))


def _interface_mac_state(entry):
    """An interface MAC is satisfied once that interface wears it."""
    detected = entry.detected_values or {}
    mac_address = detected.get("mac_address") or ""
    interface = _device_interface(entry, detected.get("interface"))
    mac, claimed = _claimed_mac(interface, mac_address)
    if mac is None:
        return _absent(entry)

    values = {"mac_address": str(mac.mac_address), "interface": interface.name if interface else None}
    return EntryCurrentState(values, mac, claimed and not _is_removal(entry))


def _mac_seen_on_interface(mac, interface):
    """True when a MAC-to-interface sighting is already recorded.

    Crossed through MACAddressInterfaceRelation rather than through
    MACAddress.interfaces: NetBox's own dcim.MACAddress claims the
    `mac_addresses` accessor this relation asks for on Interface, which
    leaves the plugin's forward manager resolving to the wrong relation
    and answering empty. The through model is the only reliable way over.
    """
    return MACAddressInterfaceRelation.objects.filter(mac_address=mac, interface=interface).exists()


def _mac_address_state(entry):
    """A neighbor MAC is satisfied once it is linked to the interface it was seen on."""
    detected = entry.detected_values or {}
    mac = _mac_address(detected.get("mac") or detected.get("mac_address"))
    if mac is None:
        return _absent(entry)

    name = detected.get("interface") or ""
    interface = _device_interface(entry, name)
    linked = (interface is not None and _mac_seen_on_interface(mac, interface)) if name else True
    values = {"mac": str(mac.mac_address), "interface": interface.name if interface else None}
    return EntryCurrentState(values, mac, linked and not _is_removal(entry))


def _ip_mac_link(mac_address, nb_ip):
    """True when the MAC an address was seen with already holds it."""
    if not mac_address:
        return True
    mac = _mac_address(mac_address)
    return mac is not None and mac.ip_addresses.filter(pk=nb_ip.pk).exists()


def _ip_address_state(entry):
    """An address is satisfied by its assignment, or by the neighbor link.

    The interfaces collector proposes assigning the address to a logical
    interface, so that assignment is the no-op test. The neighbor
    collectors propose the address itself and its link to the MAC it
    answered for, which is what their apply handler writes.
    """
    detected = entry.detected_values or {}
    current = entry.current_values or {}
    # The interfaces collector names the address under "ip_address" and the
    # neighbor collectors under "ip". Keeping whichever key the entry
    # already uses makes a refreshed snapshot read like a detected one.
    key = "ip_address" if "ip_address" in detected or "ip_address" in current else "ip"
    address = _subject_value(entry, key)
    try:
        vrf = resolve_vrf(_subject_value(entry, "vrf"))
    except (VRF.DoesNotExist, VRF.MultipleObjectsReturned):
        # Without the VRF the address cannot be identified at all, so
        # nothing can be said about it beyond leaving it for review.
        return EntryCurrentState({}, None, False)

    nb_ip = IPAddress.objects.filter(address=address, vrf=vrf).first() if address else None
    if nb_ip is None:
        return _absent(entry)

    assigned = nb_ip.assigned_object
    values = {
        key: str(nb_ip.address),
        "vrf": nb_ip.vrf.name if nb_ip.vrf else None,
        "assigned_object": str(assigned) if assigned is not None else None,
    }

    target_name = detected.get("logical_interface")
    if target_name:
        target = _device_interface(entry, target_name)
        satisfied = target is not None and assigned == target
    else:
        satisfied = _ip_mac_link(detected.get("mac"), nb_ip)
    return EntryCurrentState(values, nb_ip, satisfied and not _is_removal(entry))


def _vrf_state(entry):
    """A VRF entry is satisfied by the VRF existing."""
    name = _subject_value(entry, "name")
    vrf = VRF.objects.filter(name=name).first() if name else None
    if vrf is None:
        return _absent(entry)
    return EntryCurrentState({"name": vrf.name}, vrf, not _is_removal(entry))


CURRENT_STATE_READERS = {
    EntryKindChoices.KIND_DEVICE: _device_state,
    EntryKindChoices.KIND_INVENTORY_ITEM: _inventory_item_state,
    EntryKindChoices.KIND_MODULE: _module_state,
    EntryKindChoices.KIND_LAG: _lag_state,
    EntryKindChoices.KIND_INTERFACE: _interface_state,
    EntryKindChoices.KIND_INTERFACE_MAC: _interface_mac_state,
    EntryKindChoices.KIND_MAC_ADDRESS: _mac_address_state,
    EntryKindChoices.KIND_IP_ADDRESS: _ip_address_state,
    EntryKindChoices.KIND_VRF: _vrf_state,
}


def snapshot_entry(entry):
    """Read NetBox's side of one entry's comparison as it stands now.

    Returns None for a kind whose current state cannot be established
    without collecting from the device again, so a caller can leave those
    entries exactly as they are instead of recording a guess.
    """
    reader = CURRENT_STATE_READERS.get(entry.entry_kind)
    return reader(entry) if reader is not None else None
