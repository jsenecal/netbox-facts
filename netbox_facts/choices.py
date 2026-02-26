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
