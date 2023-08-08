"""Navigation menu for the netbox_facts plugin."""
from django.utils.translation import gettext_lazy as _
from extras.plugins import PluginMenuButton, PluginMenuItem
from utilities.choices import ButtonColorChoices

macaddress_buttons = [
    PluginMenuButton(
        link="plugins:netbox_facts:macaddress_add",
        title="Add",
        icon_class="mdi mdi-plus-thick",
        color=ButtonColorChoices.GREEN,
    )
]
macvendor_buttons = [
    PluginMenuButton(
        link="plugins:netbox_facts:macvendor_add",
        title="Add",
        icon_class="mdi mdi-plus-thick",
        color=ButtonColorChoices.GREEN,
    )
]

menu_items = (
    PluginMenuItem(
        link="plugins:netbox_facts:macaddress_list", link_text=_("MAC Addresses"), buttons=macaddress_buttons
    ),
    PluginMenuItem(link="plugins:netbox_facts:macvendor_list", link_text=_("MAC Vendors"), buttons=macvendor_buttons),
)
