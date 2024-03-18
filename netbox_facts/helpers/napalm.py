from typing import Any, Dict, Generator, List, Tuple


def parse_network_instances(instances) -> Dict[str, Dict[str, str | List[str] | None]]:
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
) -> Generator[Tuple[str, Dict[str, str]], Any, Any]:
    """Get network instances by interface"""
    for instance_name, instance_data in instances:
        instance_data["name"] = instance_name
        for interface in instance_data["interfaces"]:
            yield interface, {
                key: value
                for key, value in instance_data.items()
                if key != "interfaces"
            }
