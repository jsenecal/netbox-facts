from django.utils.translation import gettext_lazy as _
from utilities.choices import ChoiceSet


class CollectionTypeChoices(ChoiceSet):
    key = "Collection.Type"

    TYPE_ARP = "arp"
    TYPE_NDP = "ndp"
    TYPE_INVENTORY = "inventory"
    TYPE_INTERFACES = "interfaces"
    TYPE_LLDP = "lldp"
    TYPE_L2 = "ethernet_switching"
    TYPE_L2CIRCTUITS = "l2_circuits"
    TYPE_EVPN = "evpn"
    TYPE_BGP = "bgp"
    TYPE_OSPF = "ospf"

    CHOICES = [
        (TYPE_ARP, "ARP", "gray"),
        (TYPE_NDP, _("IPv6 Neighbor Discovery"), "gray"),
        (TYPE_INVENTORY, _("Inventory"), "blue"),
        (TYPE_INTERFACES, _("Interfaces"), "purple"),
        (TYPE_LLDP, _("LLDP"), "cyan"),
        (TYPE_L2, _("Ethernet Switching Tables"), "black"),
        (TYPE_L2CIRCTUITS, _("L2 Circuits"), "orange"),
        (TYPE_EVPN, "EVPN", "red"),
        (TYPE_BGP, "BGP", "green"),
        (TYPE_OSPF, "OSPF", "teal"),
    ]


class ConnectionTargetChoices(ChoiceSet):
    TARGET_PRIMARY = "primary"
    TARGET_OOB = "oob"
    TARGET_PRIMARY_THEN_OOB = "primary_then_oob"
    TARGET_OOB_THEN_PRIMARY = "oob_then_primary"

    CHOICES = [
        (TARGET_PRIMARY, _("Primary IP"), "blue"),
        (TARGET_OOB, _("OOB IP"), "purple"),
        (TARGET_PRIMARY_THEN_OOB, _("Primary IP, then OOB"), "cyan"),
        (TARGET_OOB_THEN_PRIMARY, _("OOB IP, then Primary"), "teal"),
    ]


class CollectorStatusChoices(ChoiceSet):
    NEW = "new"
    QUEUED = "queued"
    WORKING = "working"
    COMPLETED = "completed"
    SCHEDULED = "scheduled"
    FAILED = "failed"
    STALLED = "stalled"

    CHOICES = (
        (NEW, _("New"), "blue"),
        (QUEUED, _("Queued"), "orange"),
        (WORKING, _("Working"), "cyan"),
        (COMPLETED, _("Completed"), "green"),
        (SCHEDULED, _("Scheduled"), "purple"),
        (FAILED, _("Failed"), "red"),
        (STALLED, _("Stalled"), "gray"),
    )


class CollectorPriorityChoices(ChoiceSet):
    key = "Collector.priority"

    PRIORITY_HIGH = "high"
    PRIORITY_DEFAULT = "default"
    PRIORITY_LOW = "low"

    CHOICES = [
        (PRIORITY_HIGH, _("High"), "red"),
        (PRIORITY_DEFAULT, _("Default"), "purple"),
        (PRIORITY_LOW, _("Low"), "blue"),
    ]


class ReportStatusChoices(ChoiceSet):
    STATUS_PENDING = "pending"
    STATUS_COMPLETED = "completed"
    STATUS_PARTIAL = "partial"
    STATUS_APPLIED = "applied"
    STATUS_FAILED = "failed"

    CHOICES = (
        (STATUS_PENDING, _("Pending"), "cyan"),
        (STATUS_COMPLETED, _("Completed"), "blue"),
        (STATUS_PARTIAL, _("Partial"), "orange"),
        (STATUS_APPLIED, _("Applied"), "green"),
        (STATUS_FAILED, _("Failed"), "red"),
    )


class EntryActionChoices(ChoiceSet):
    ACTION_NEW = "new"
    ACTION_CHANGED = "changed"
    ACTION_CONFIRMED = "confirmed"
    ACTION_STALE = "stale"

    CHOICES = (
        (ACTION_NEW, _("New"), "green"),
        (ACTION_CHANGED, _("Changed"), "orange"),
        (ACTION_CONFIRMED, _("Confirmed"), "blue"),
        (ACTION_STALE, _("Stale"), "gray"),
    )


class EntryStatusChoices(ChoiceSet):
    STATUS_PENDING = "pending"
    STATUS_APPLYING = "applying"
    STATUS_APPLIED = "applied"
    STATUS_SKIPPED = "skipped"
    STATUS_FAILED = "failed"

    CHOICES = (
        (STATUS_PENDING, _("Pending"), "cyan"),
        (STATUS_APPLYING, _("Applying"), "blue"),
        (STATUS_APPLIED, _("Applied"), "green"),
        (STATUS_SKIPPED, _("Skipped"), "gray"),
        (STATUS_FAILED, _("Failed"), "red"),
    )


class EntryKindChoices(ChoiceSet):
    """What kind of object a report entry concerns.

    The kind is the apply-dispatch key within a collector type and the
    filtering key for clients; the entry's object_repr stays a pure display
    value.
    """

    KIND_DEVICE = "device"
    KIND_INTERFACE = "interface"
    KIND_INTERFACE_MAC = "interface_mac"
    KIND_LAG = "lag"
    KIND_IP_ADDRESS = "ip_address"
    KIND_MAC_ADDRESS = "mac_address"
    KIND_VRF = "vrf"
    KIND_INVENTORY_ITEM = "inventory_item"
    KIND_MODULE = "module"
    KIND_CABLE = "cable"
    KIND_L2_CIRCUIT = "l2_circuit"
    KIND_BGP_PEER_IP = "bgp_peer_ip"
    KIND_BGP_ROUTER = "bgp_router"
    KIND_BGP_SCOPE = "bgp_scope"
    KIND_BGP_PEER = "bgp_peer"
    KIND_OSPF_NEIGHBOR = "ospf_neighbor"
    KIND_OTHER = "other"

    CHOICES = (
        (KIND_DEVICE, _("Device")),
        (KIND_INTERFACE, _("Interface")),
        (KIND_INTERFACE_MAC, _("Interface MAC")),
        (KIND_LAG, _("LAG")),
        (KIND_IP_ADDRESS, _("IP address")),
        (KIND_MAC_ADDRESS, _("MAC address")),
        (KIND_VRF, _("VRF")),
        (KIND_INVENTORY_ITEM, _("Inventory item")),
        (KIND_MODULE, _("Module")),
        (KIND_CABLE, _("Cable")),
        (KIND_L2_CIRCUIT, _("L2 circuit")),
        (KIND_BGP_PEER_IP, _("BGP peer address")),
        (KIND_BGP_ROUTER, _("BGP router")),
        (KIND_BGP_SCOPE, _("BGP scope")),
        (KIND_BGP_PEER, _("BGP peer")),
        (KIND_OSPF_NEIGHBOR, _("OSPF neighbor")),
        (KIND_OTHER, _("Other")),
    )


# The object_repr prefixes the applier dispatched on before entries carried a
# kind of their own. Ordered, and matched on a whole leading token, so that
# rows written before the field can be resolved to the kind they were applied
# as; anything else is KIND_OTHER, which dispatches exactly as the unprefixed
# fall-through did. Also used to drop the duplicated type token from an
# entry's display title.
ENTRY_KIND_REPR_PREFIXES = (
    ("MACAddress", EntryKindChoices.KIND_MAC_ADDRESS),
    ("IPAddress", EntryKindChoices.KIND_IP_ADDRESS),
    ("InventoryItem", EntryKindChoices.KIND_INVENTORY_ITEM),
    ("Module", EntryKindChoices.KIND_MODULE),
    ("Interface", EntryKindChoices.KIND_INTERFACE),
    ("VRF", EntryKindChoices.KIND_VRF),
    ("LAG", EntryKindChoices.KIND_LAG),
    ("Cable", EntryKindChoices.KIND_CABLE),
    ("Device", EntryKindChoices.KIND_DEVICE),
    ("BGPRouter", EntryKindChoices.KIND_BGP_ROUTER),
    ("BGPScope", EntryKindChoices.KIND_BGP_SCOPE),
    ("BGPPeer", EntryKindChoices.KIND_BGP_PEER),
    ("BGP peer", EntryKindChoices.KIND_BGP_PEER_IP),
    ("OSPF neighbor", EntryKindChoices.KIND_OSPF_NEIGHBOR),
    ("L2 circuit data", EntryKindChoices.KIND_L2_CIRCUIT),
)

# Prefixes a title strips but derivation must not use: an "Interface ..."
# label alone cannot tell an interface entry from an interface-MAC one, so
# the kind decides and only the display side drops the token.
ENTRY_KIND_TITLE_PREFIXES = {
    EntryKindChoices.KIND_INTERFACE_MAC: ("Interface", "MACAddress"),
}

# The verb each action reads as in a one-line entry title.
ENTRY_ACTION_VERBS = {
    EntryActionChoices.ACTION_NEW: _("discovered"),
    EntryActionChoices.ACTION_CHANGED: _("changed"),
    EntryActionChoices.ACTION_CONFIRMED: _("confirmed"),
    EntryActionChoices.ACTION_STALE: _("stale"),
}


def _has_prefix(text, prefix):
    """Return True when text leads with prefix as a whole token."""
    return text == prefix or text.startswith(f"{prefix} ")


def entry_kind_from_object_repr(object_repr):
    """Resolve an entry kind from a display label's leading type token.

    Returns KIND_OTHER for anything unrecognized: a label is free text and
    must never make resolution fail.
    """
    text = (object_repr or "").strip()
    for prefix, kind in ENTRY_KIND_REPR_PREFIXES:
        if _has_prefix(text, prefix):
            return kind
    return EntryKindChoices.KIND_OTHER


def strip_entry_kind_prefix(entry_kind, object_repr):
    """Drop a leading type token that repeats what the kind already says."""
    text = (object_repr or "").strip()
    prefixes = [prefix for prefix, kind in ENTRY_KIND_REPR_PREFIXES if kind == entry_kind]
    prefixes.extend(ENTRY_KIND_TITLE_PREFIXES.get(entry_kind, ()))
    for prefix in prefixes:
        if text == prefix:
            return ""
        if text.startswith(f"{prefix} "):
            return text[len(prefix) + 1 :]
    return text
