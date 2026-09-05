from collections.abc import Generator
from typing import Any

from netbox.constants import CENSOR_TOKEN

NAPALM_SENSITIVE_KEYS = ("username", "password", "secret")


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
