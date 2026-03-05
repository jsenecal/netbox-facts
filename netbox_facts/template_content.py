from netbox.plugins.templates import PluginTemplateExtension


class IPAddressMACAddresses(PluginTemplateExtension):
    models = ["ipam.ipaddress"]

    def right_page(self):
        obj = self.context["object"]
        mac_addresses = obj.mac_addresses.all()
        if not mac_addresses.exists():
            return ""
        return self.render(
            "netbox_facts/inc/ipaddress_mac_addresses.html",
            extra_context={"mac_addresses": mac_addresses},
        )


template_extensions = [IPAddressMACAddresses]
