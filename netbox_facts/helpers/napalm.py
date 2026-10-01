from collections.abc import Generator
from typing import Any

from netbox.constants import CENSOR_TOKEN
from netbox.plugins.utils import get_plugin_config

NAPALM_SENSITIVE_KEYS = ("username", "password", "secret")

# The two arguments NAPALM takes positionally. Every other key, the enable
# secret included, is handed to the driver as optional_args.
NAPALM_CREDENTIAL_KEYS = ("username", "password")


def mask_napalm_credentials(napalm_args: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of napalm_args with credential values censored."""
    masked = dict(napalm_args)
    for key in NAPALM_SENSITIVE_KEYS:
        if masked.get(key):
            masked[key] = CENSOR_TOKEN
    return masked


def restore_masked_credentials(incoming: dict[str, Any], stored: dict[str, Any] | None) -> dict[str, Any]:
    """Return a copy of incoming args with censored values restored from stored ones.

    A censored value round-tripped by a client means "keep the current
    credential"; it must never overwrite the stored real value.
    """
    restored = dict(incoming)
    stored = stored if isinstance(stored, dict) else {}
    for key in NAPALM_SENSITIVE_KEYS:
        if restored.get(key) == CENSOR_TOKEN and key in stored:
            restored[key] = stored[key]
    return restored


def resolve_napalm_credentials(napalm_args: dict[str, Any] | None) -> tuple[str, str]:
    """Return the username and password a plan connects with.

    A credential carried by the plan's own NAPALM arguments wins; one that
    is absent or empty falls back to the plugin-level setting of the same
    name. Both the pre-run check and the collector resolve through here,
    so a plan that passes the check is one the collector can authenticate
    as, and neither side can drift from the other.
    """
    args = napalm_args if isinstance(napalm_args, dict) else {}
    username = args.get("username") or get_plugin_config("netbox_facts", "napalm_username", "")
    password = args.get("password") or get_plugin_config("netbox_facts", "napalm_password", "")
    return username or "", password or ""


def strip_napalm_credentials(
    napalm_args: dict[str, Any],
    keys: tuple[str, ...] = NAPALM_CREDENTIAL_KEYS,
) -> dict[str, Any]:
    """Return a copy of napalm_args without the given credential keys.

    The default drops the two credentials NAPALM takes positionally, which
    is what the driver call needs: everything left over is passed as
    optional_args, the enable secret included, because NAPALM reads the
    secret from there even though the read paths censor it like a
    password. A caller that must not carry a secret at all -- cloning a
    plan, for one -- passes NAPALM_SENSITIVE_KEYS instead.
    """
    return {key: value for key, value in napalm_args.items() if key not in keys}


def parse_network_instances(instances) -> dict[str, dict[str, str | list[str] | None]]:
    """Parse network instances"""

    return {
        instance["name"]: {
            "instance_type": instance["type"],
            "route_distinguisher": instance["state"].get("route_distinguisher")
            if instance["state"].get("route_distinguisher")
            else None,
            "interfaces": list(instance["interfaces"]["interface"].keys()),
        }
        for instance in instances.values()
    }


def get_network_instances_by_interface(
    instances,
) -> Generator[tuple[str, dict[str, str]], Any, Any]:
    """Get network instances by interface"""
    for instance_name, instance_data in instances:
        instance_data["name"] = instance_name
        for interface in instance_data["interfaces"]:
            yield interface, {key: value for key, value in instance_data.items() if key != "interfaces"}
