# Installation

## Requirements

- NetBox 4.5.x
- Python 3.12, 3.13, or 3.14
- A reachable PostgreSQL database and Redis (the standard NetBox stack)
- NAPALM-compatible network devices

## Install from PyPI

```bash
pip install netbox-facts
```

To enable the optional [netbox-routing](https://github.com/netbox-community/netbox-routing)
integration (required for the BGP collector to populate `BGPRouter`,
`BGPScope`, and `BGPPeer`), install the `routing` extra:

```bash
pip install "netbox-facts[routing]"
```

## Install from source

```bash
pip install git+https://github.com/jsenecal/netbox-facts
```

Or pin via your `local_requirements.txt` / `plugin_requirements.txt`:

```text
git+https://github.com/jsenecal/netbox-facts
```

## Enable the plugin

Edit `/opt/netbox/netbox/netbox/configuration.py` (or, for netbox-docker,
`/configuration/plugins.py`) and add `netbox_facts` to `PLUGINS`:

```python
PLUGINS = [
    "netbox_facts",
]
```

A minimal `PLUGINS_CONFIG` block:

```python
PLUGINS_CONFIG = {
    "netbox_facts": {
        "top_level_menu": True,
        "napalm_username": "",
        "napalm_password": "",
        "global_napalm_args": {},
        "valid_interfaces_re": r".*",
    },
}
```

See [Configuration](configuration.md) for every available setting.

## Run migrations

```bash
python manage.py migrate netbox_facts
```

Restart NetBox (and the RQ workers) so the plugin's `JobRunner` is
registered.

## Verify the install

After restart, the navigation menu should show an **Operational Facts**
top-level entry (or, if `top_level_menu` is `False`, entries under the
**Plugins** menu). The REST API root at `/api/plugins/facts/` should list
the four resources:

- `macaddresses/`
- `macvendors/`
- `collectionplans/`
- `factsreports/`
