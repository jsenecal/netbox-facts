"""Top-level package for NetBox Facts Plugin."""

__author__ = """Jonathan Senecal"""
__email__ = 'contact@jonathansenecal.com'
__version__ = '0.0.1'


from extras.plugins import PluginConfig


class FactsConfig(PluginConfig):
    name = 'netbox_facts_plugin'
    verbose_name = 'NetBox Facts Plugin'
    description = 'Gather operational facts ab'
    version = 'version'
    base_url = 'netbox_facts_plugin'


config = FactsConfig
