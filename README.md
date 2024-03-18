# NetBox Facts Plugin

Gather operational facts from supported NetBox Devices


* Free software: Apache-2.0
* Documentation: https://jsenecal.github.io/netbox-facts


## Features

This plugin leverages [NAPALM](https://napalm.readthedocs.io/en/latest/) to gather and document operational information about NetBox devices and various models.

## Compatibility

| NetBox Version | Plugin Version |
|----------------|----------------|
|     3.5        |      0.0.1     |

## Installing

For adding to a NetBox Docker setup see
[the general instructions for using netbox-docker with plugins](https://github.com/netbox-community/netbox-docker/wiki/Using-Netbox-Plugins).

While this is still in development and not yet on pypi you can install with pip:

```bash
pip install git+https://github.com/jsenecal/netbox-facts
```

or by adding to your `local_requirements.txt` or `plugin_requirements.txt` (netbox-docker):

```bash
git+https://github.com/jsenecal/netbox-facts
```

Enable the plugin in `/opt/netbox/netbox/netbox/configuration.py`,
 or if you use netbox-docker, your `/configuration/plugins.py` file :

```python
PLUGINS = [
    'netbox_facts'
]

PLUGINS_CONFIG = {
    "netbox_facts": {},
}
```

## Credits

This package was created with [Cookiecutter](https://github.com/audreyr/cookiecutter) and the [`netbox-community/cookiecutter-netbox-plugin`](https://github.com/netbox-community/cookiecutter-netbox-plugin) project template.
