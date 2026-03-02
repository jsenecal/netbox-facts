"""Top-level package for NetBox Facts Plugin."""

__author__ = "Jonathan Senecal"
__email__ = "contact@jonathansenecal.com"
__version__ = "0.0.1"


from netbox.plugins import PluginConfig


class FactsConfig(PluginConfig):
    """Plugin configuration for the netbox_facts plugin."""

    name = "netbox_facts"
    verbose_name = "NetBox Facts Plugin"
    description = "Gather operational facts from supported NetBox Devices"
    version = __version__
    base_url = "facts"
    author = "Jonathan Senecal"
    author_email = "contact@jonathansenecal.com"
    default_settings = {
        "top_level_menu": True,
        "napalm_username": "",
        "napalm_password": "",
        "napalm_timeout": 60,
        "global_napalm_args": {},
        "valid_interfaces_re": ".*",
        "job_timeout": 1800,
    }

    def ready(self):
        super(FactsConfig, self).ready()
        from netbox_facts import (
            signals,
        )  # pylint: disable=import-outside-toplevel,unused-import


config = FactsConfig  # pylint: disable=invalid-name
print("🧩 netbox_facts plugin loaded.")
