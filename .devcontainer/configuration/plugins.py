"""
Plugin related config
"""

PLUGINS = [
    "netbox_facts",
]

PLUGINS_CONFIG = {  # type: ignore
    "netbox_facts": {"top_level_menu": True},
}
