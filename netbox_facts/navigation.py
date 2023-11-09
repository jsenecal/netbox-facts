"""Navigation menu for the netbox_facts plugin."""
from django.utils.translation import gettext_lazy as _
from extras.plugins import PluginMenuButton, PluginMenuItem, get_plugin_config
from extras.plugins.navigation import PluginMenu
from utilities.choices import ButtonColorChoices

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
collector_definition_buttons = [
    PluginMenuButton(
        link="plugins:netbox_facts:collectordefinition_add",
        title="Add",
        icon_class="mdi mdi-plus-thick",
        color=ButtonColorChoices.GREEN,
        permissions=["netbox_facts.add_collectordefinition"],
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
        link="plugins:netbox_facts:collectordefinition_list",
        link_text=_("Definitions"),
        permissions=["netbox_facts.view_collectordefinition"],
        buttons=collector_definition_buttons,
    ),
)

if get_plugin_config("netbox_facts", "top_level_menu"):
    # add a top level entry
    menu = PluginMenu(
        label=_("Operational Facts"),
        groups=(("Networking", networking_menu), ("Facts Collection", facts_collection_menu)),
        icon_class="mdi mdi-checkbox-multiple-marked-outline",
    )
else:
    # display under plugins
    menu_items = networking_menu + facts_collection_menu
