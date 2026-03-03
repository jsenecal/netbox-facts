"""NetBox helper functions"""


import ipaddress
from typing import Any, Dict, Generator, List, Tuple
from dcim.models.devices import Device
from ipam.models import IPAddress
from ipam.models.ip import Prefix

from ipam.models.vrfs import VRF


def get_absolute_url_markdown(instance: Any, code=False, bold=False) -> str:
    """Get a markdown link to an object's absolute URL."""
    if hasattr(instance, "get_absolute_url") is False:
        raise ValueError(
            f"Object {instance} does not have a get_absolute_url() method."
        )

    link_text = str(instance)
    if code:
        link_text = f"`{link_text}`"
    url = instance.get_absolute_url()
    md = f"[{link_text}]({url})"
    if bold:
        return f"**{md}**"
    return md


def get_primary_ip(instance: Device) -> str:
    """Get the primary IP address for a device."""
    if instance.primary_ip is not None:
        ip_address: IPAddress = instance.primary_ip
        return str(ip_address.address.ip)
    raise ValueError(f"Device {instance} does not have a primary IP address.")


def get_connection_ips(instance: Device, target: str) -> list[tuple[str, str]]:
    """Return a list of (ip, label) tuples to try for connecting to a device.

    ``target`` is a ConnectionTargetChoices value.
    """
    from netbox_facts.choices import ConnectionTargetChoices

    primary = None
    if instance.primary_ip is not None:
        primary = (str(instance.primary_ip.address.ip), "primary")

    oob = None
    if instance.oob_ip is not None:
        oob = (str(instance.oob_ip.address.ip), "oob")

    if target == ConnectionTargetChoices.TARGET_PRIMARY:
        candidates = [primary]
    elif target == ConnectionTargetChoices.TARGET_OOB:
        candidates = [oob]
    elif target == ConnectionTargetChoices.TARGET_PRIMARY_THEN_OOB:
        candidates = [primary, oob]
    elif target == ConnectionTargetChoices.TARGET_OOB_THEN_PRIMARY:
        candidates = [oob, primary]
    else:
        candidates = [primary]

    result = [c for c in candidates if c is not None]
    if not result:
        raise ValueError(f"Device {instance} has no usable IP address for target '{target}'.")
    return result


def resolve_napalm_network_instances(
    instances,
) -> Generator[Tuple[str, Dict[str, str | List[str]]], Any, Any]:
    """Parse network instances and resolve VRFs in NetBox.
    Returns a generator of instance_name, data pairs where the netbox_vrf key is either missing, None or a VRF object.
    """
    instances_by_name = {}
    for instance_name, data in instances.items():
        if data["instance_type"] == "L3VRF":
            try:
                # Try to find an existing VRF object and cache it
                data["netbox_vrf"] = instances_by_name.get(
                    instance_name, VRF.objects.get(name=instance_name)
                )
                instances_by_name[instance_name] = data["netbox_vrf"]
            except VRF.DoesNotExist:  # pylint: disable=no-member
                pass
        else:
            data["netbox_vrf"] = None
        yield instance_name, data


def resolve_napalm_interfaces_ip_addresses(interfaces, network_instances=None):
    """Parse interfaces and resolve IP addresses in NetBox."""

    if network_instances is None:
        network_instances = {}

    # Iterate over each interface
    for interface_name, interface_data in interfaces.items():
        network_instance_data = network_instances.get(interface_name, {})
        new_data = {}
        # Iterate over each IP address family (IPv4, IPv6) and extract the IP addresses and metadata
        for data in interface_data.values():
            for ip_address, metadata in data.items():
                # Get the prefix length from the metadata
                prefix_length = str(metadata.get("prefix_length", 32))
                address_with_length = ip_address + "/" + prefix_length

                # Try to find an existing IPAddress object
                ipa_kwargs = {"address": address_with_length}
                if network_instance_data.get("netbox_vrf"):
                    ipa_kwargs["vrf"] = network_instance_data.get("netbox_vrf")
                ipa_qs = IPAddress.objects.filter(**ipa_kwargs)

                # Try to find an existing Prefix object
                prefix_kwargs = {"prefix__net_contains_or_equals": address_with_length}
                if network_instance_data.get("netbox_vrf"):
                    prefix_kwargs["vrf"] = network_instance_data.get("netbox_vrf")
                prefix_qs = Prefix.objects.filter(**prefix_kwargs)

                ip_interface = ipaddress.ip_interface(address_with_length)
                new_data[ip_address] = {
                    "netbox_ip_addresses": ipa_qs,
                    "netbox_prefixes": prefix_qs,
                    "netbox_vrf": network_instance_data.get("netbox_vrf"),
                    "routing_instance_name": network_instance_data.get("name"),
                    "ip_address": ip_address,
                    "prefix_length": prefix_length,
                    "ip_interface_object": ip_interface,
                    "version": ip_interface.version,
                }

        yield interface_name, new_data
