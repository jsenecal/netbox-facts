"""Top-level package for NetBox Facts Plugin."""

__author__ = """Jonathan Senecal"""
__email__ = 'contact@jonathansenecal.com'
__version__ = '0.0.1'


from extras.plugins import PluginConfig


class FactsConfig(PluginConfig):
    name = 'netbox_facts'
    verbose_name = 'NetBox Facts Plugin'
    description = 'Gather operational facts from supported NetBox Devices'
    version = 'version'
    base_url = 'facts'


config = FactsConfig
