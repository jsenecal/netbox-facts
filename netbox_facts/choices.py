from utilities.choices import ChoiceSet
from django.utils.translation import gettext_lazy as _


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
    STATUS_APPLIED = "applied"
    STATUS_SKIPPED = "skipped"
    STATUS_FAILED = "failed"

    CHOICES = (
        (STATUS_PENDING, _("Pending"), "cyan"),
        (STATUS_APPLIED, _("Applied"), "green"),
        (STATUS_SKIPPED, _("Skipped"), "gray"),
        (STATUS_FAILED, _("Failed"), "red"),
    )
