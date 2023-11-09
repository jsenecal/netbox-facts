from utilities.choices import ChoiceSet
from django.utils.translation import gettext_lazy as _


class CollectionTypeChoices(ChoiceSet):
    key = "Collection.Type"

    TYPE_ARP = "arp"
    TYPE_NDP = "ndp"
    TYPE_INVENTORY = "inventory"
    TYPE_INTERFACES = "interfaces"
    TYPE_L2 = "ethernet_switching"
    TYPE_L2CIRCTUITS = "l2_circuits"
    TYPE_EVPN = "evpn"
    TYPE_BGP = "bgp"

    CHOICES = [
        (TYPE_ARP, "ARP", "gray"),
        (TYPE_NDP, _("IPv6 Neighbor Discovery"), "gray"),
        (TYPE_INVENTORY, _("Inventory"), "blue"),
        (TYPE_INTERFACES, _("Interfaces"), "purple"),
        (TYPE_L2, _("Ethernet Switching Tables"), "black"),
        (TYPE_L2CIRCTUITS, _("L2 Circuits"), "orange"),
        (TYPE_EVPN, "EVPN", "red"),
        (TYPE_BGP, "BGP", "green"),
    ]


class CollectorStatusChoices(ChoiceSet):
    key = "CollectorDefinition.status"

    STATUS_ENABLED = "enabled"
    STATUS_PAUSED = "paused"
    STATUS_FAILED = "failed"
    STATUS_DISABLED = "disabled"

    CHOICES = [
        (STATUS_ENABLED, _("Enabled"), "green"),
        (STATUS_PAUSED, _("Paused"), "cyan"),
        (STATUS_FAILED, _("Failed"), "red"),
        (STATUS_DISABLED, _("Disabled"), "yellow"),
    ]


class CollectorPriorityChoices(ChoiceSet):
    key = "CollectorDefinition.priority"

    PRIORITY_HIGH = "high"
    PRIORITY_DEFAULT = "default"
    PRIORITY_LOW = "low"

    CHOICES = [
        (PRIORITY_HIGH, _("High"), "red"),
        (PRIORITY_DEFAULT, _("Default"), "purple"),
        (PRIORITY_LOW, _("Low"), "blue"),
    ]
