"""Top-level package for NetBox Facts Plugin."""

import logging

__author__ = "Jonathan Senecal"
__email__ = "contact@jonathansenecal.com"
__version__ = "0.1.1"


from netbox.plugins import PluginConfig

logger = logging.getLogger(__name__)


class FactsConfig(PluginConfig):
    """Plugin configuration for the netbox_facts plugin."""

    name = "netbox_facts"
    verbose_name = "NetBox Facts Plugin"
    description = "Gather operational facts from supported NetBox Devices"
    version = __version__
    base_url = "facts"
    author = "Jonathan Senecal"
    author_email = "contact@jonathansenecal.com"
    min_version = "4.5.0"
    max_version = "4.7.99"
    default_settings = {
        "top_level_menu": True,
        "napalm_username": "",
        "napalm_password": "",
        "napalm_timeout": 60,
        "global_napalm_args": {},
        "valid_interfaces_re": ".*",
        "job_timeout": 1800,
        "report_retention_days": 0,
        "scope_warning_threshold": 500,
    }

    def ready(self):
        super().ready()
        # These modules are imported for their side effects: signals connects the
        # model receivers, retention registers the report pruning job as a NetBox
        # system job, dashboard registers the pending-changes widget, and
        # device_views attaches the Facts tab to dcim.Device. The last two
        # register into NetBox's registries, which dcim's URLConf and the
        # dashboard widget picker read once at startup; ready() is the only hook
        # guaranteed to run before either, so they cannot be left to urls.py.
        # events is called into explicitly, just below.
        from netbox_facts import (  # pylint: disable=import-outside-toplevel,unused-import # noqa: F401
            dashboard,
            device_views,
            events,
            retention,
            signals,
        )

        events.register_event_types()

        logger.info("%s plugin loaded", self.name)


config = FactsConfig  # pylint: disable=invalid-name
