# NetBox Facts Plugin

Gather operational facts from supported NetBox Devices using [NAPALM](https://napalm.readthedocs.io/en/latest/) and store them in NetBox.

* Free software: Apache-2.0
* Documentation: https://jsenecal.github.io/netbox-facts

## Features

- **10 collector types**: ARP, IPv6 Neighbor Discovery (NDP), Inventory, Interfaces, LLDP, Ethernet Switching Tables, L2 Circuits, EVPN, BGP, and OSPF
- **Detect-only mode**: Collection plans can produce a report without modifying NetBox objects — changes can be reviewed and selectively applied or skipped
- **Auto-scheduling**: Interval-based recurring collection via NetBox's JobRunner framework, with priority queues (high/default/low)
- **MAC address tracking**: Discovered MAC addresses linked to interfaces and IP addresses, with automatic OUI vendor lookup
- **REST API**: Full CRUD endpoints for MAC addresses, MAC vendors, collection plans, and facts reports
- **Optional BGP integration**: Works with the [netbox-routing](https://github.com/netbox-community/netbox-routing) plugin for BGP session data
- **Vendor-specific NAPALM drivers**: Extended Junos driver with enhanced ARP/NDP collection

## Compatibility

| NetBox Version | Plugin Version |
|----------------|----------------|
|     4.5.x      |      0.0.1     |

## Installing

For adding to a NetBox Docker setup see
[the general instructions for using netbox-docker with plugins](https://github.com/netbox-community/netbox-docker/wiki/Using-Netbox-Plugins).

Install with pip:

```bash
pip install git+https://github.com/jsenecal/netbox-facts
```

or by adding to your `local_requirements.txt` or `plugin_requirements.txt` (netbox-docker):

```bash
git+https://github.com/jsenecal/netbox-facts
```

For BGP collector support, install with the optional `routing` extra:

```bash
pip install "netbox-facts[routing] @ git+https://github.com/jsenecal/netbox-facts"
```

Enable the plugin in `/opt/netbox/netbox/netbox/configuration.py`,
or if you use netbox-docker, your `/configuration/plugins.py` file:

```python
PLUGINS = [
    "netbox_facts",
]

PLUGINS_CONFIG = {
    "netbox_facts": {
        "top_level_menu": True,
        "napalm_username": "",
        "napalm_password": "",
        "global_napalm_args": {},
        "valid_interfaces_re": r"<your-interface-regex>",
    },
}
```

### Configuration Options

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `top_level_menu` | bool | `True` | Show plugin as a top-level menu item instead of under Plugins |
| `napalm_username` | str | `""` | Default NAPALM username for device connections |
| `napalm_password` | str | `""` | Default NAPALM password for device connections |
| `global_napalm_args` | dict | `{}` | Additional arguments passed to all NAPALM driver instances |
| `valid_interfaces_re` | str | `".*"` | Regex to filter which interfaces are processed |
| `job_timeout` | int | `1800` | Maximum RQ job runtime in seconds (30 min default) |
| `napalm_timeout` | int | `60` | NAPALM connection timeout in seconds |

### Per-Plan Credentials

Each Collection Plan has a **NAPALM arguments** JSON field that is merged on top of `global_napalm_args`. You can override the username and password for a specific plan by including `username` and `password` keys:

```json
{
    "username": "collector-user",
    "password": "collector-pass"
}
```

These keys are extracted before the remaining args are passed through to NAPALM's `optional_args`, so they won't interfere with driver options.

### Connection Target

Each Collection Plan has a **Connection target** setting that controls which device IP address is used to connect:

| Option | Behavior |
|--------|----------|
| **Primary IP** *(default)* | Connect using the device's primary IP address |
| **OOB IP** | Connect using the device's out-of-band management IP |
| **Primary IP, then OOB** | Try primary IP first; on connection failure, fall back to OOB IP |
| **OOB IP, then Primary** | Try OOB IP first; on connection failure, fall back to primary IP |

The "both" options are useful when devices are reachable via either path depending on network conditions. Each connection attempt is logged with the IP address and type being used.

## Developing

### VSCode + Docker + Dev Containers

To develop this plugin further one can use the included `.devcontainer` configuration. This creates a Docker container with a fully working NetBox installation.

1. Install the Dev Containers extension (`ms-vscode-remote.remote-containers`) in VS Code
2. Use **Dev Container: Clone Repository in Container Volume** to clone this repository
3. Inside the container, run `make all` to set up and launch the NetBox instance

Your NetBox instance will be served at `0.0.0.0:8008` (accessible via `localhost:8008`).

### Key Commands

```bash
make all          # Full setup (install + migrations + static files + sample data + launch)
make rebuild      # Rebuild without reinitializing sample data
make setup        # Install plugin in editable mode
make migrations   # Generate new migrations
make migrate      # Apply migrations
make test         # Run test suite (migration check + Django tests)
make runserver    # Start dev server
make nbshell      # Interactive NetBox shell
```

## Credits

This package was created with [Cookiecutter](https://github.com/audreyr/cookiecutter) and the [`netbox-community/cookiecutter-netbox-plugin`](https://github.com/netbox-community/cookiecutter-netbox-plugin) project template.
