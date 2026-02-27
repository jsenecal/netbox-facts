"""Navigation menu for the netbox_facts plugin."""
from django.utils.translation import gettext_lazy as _
from netbox.plugins import PluginMenuButton, PluginMenuItem, get_plugin_config
from netbox.plugins.navigation import PluginMenu
from netbox.choices import ButtonColorChoices

macaddress_buttons = [
    PluginMenuButton(
        link="plugins:netbox_facts:macaddress_add",
        title="Add",
        icon_class="mdi mdi-plus-thick",
        color=ButtonColorChoices.GREEN,
        permissions=["netbox_facts.add_macaddress"],
    )
]
macvendor_buttons = [
    PluginMenuButton(
        link="plugins:netbox_facts:macvendor_add",
        title="Add",
        icon_class="mdi mdi-plus-thick",
        color=ButtonColorChoices.GREEN,
        permissions=["netbox_facts.add_macvendor"],
    )
]
collectionplan_buttons = [
    PluginMenuButton(
        link="plugins:netbox_facts:collectionplan_add",
        title="Add",
        icon_class="mdi mdi-plus-thick",
        color=ButtonColorChoices.GREEN,
        permissions=["netbox_facts.add_collectionplan"],
    )
]


networking_menu = (
    PluginMenuItem(
        link="plugins:netbox_facts:macaddress_list",
        link_text=_("MAC Addresses"),
        permissions=["netbox_facts.view_macaddress"],
        buttons=macaddress_buttons,
    ),
    PluginMenuItem(
        link="plugins:netbox_facts:macvendor_list",
        link_text=_("MAC Vendors"),
        permissions=["netbox_facts.view_macvendor"],
        buttons=macvendor_buttons,
    ),
)

facts_collection_menu = (
    PluginMenuItem(
        link="plugins:netbox_facts:collectionplan_list",
        link_text=_("Collection Plans"),
        permissions=["netbox_facts.view_collector"],
        buttons=collectionplan_buttons,
    ),
)

facts_reports_menu = (
    PluginMenuItem(
        link="plugins:netbox_facts:factsreport_list",
        link_text=_("Facts Reports"),
        permissions=["netbox_facts.view_factsreport"],
    ),
)

if get_plugin_config("netbox_facts", "top_level_menu"):
    # add a top level entry
    menu = PluginMenu(
        label=_("Operational Facts"),
        groups=(
            ("Networking", networking_menu),
            ("Facts Collection", facts_collection_menu),
            ("Facts Reports", facts_reports_menu),
        ),
        icon_class="mdi mdi-checkbox-multiple-marked-outline",
    )
else:
    # display under plugins
    menu_items = networking_menu + facts_collection_menu + facts_reports_menu
