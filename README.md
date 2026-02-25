# NetBox Facts Plugin

Gather operational facts from supported NetBox Devices


* Free software: Apache-2.0
* Documentation: https://jsenecal.github.io/netbox-facts


## Features

This plugin leverages [NAPALM](https://napalm.readthedocs.io/en/latest/) to gather and document operational information about NetBox devices and various models.

## Compatibility

| NetBox Version | Plugin Version |
|----------------|----------------|
|     3.7        |      0.0.1     |

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


## Developing

### VSCode + Docker + Dev Containers

To develop this plugin further one can use the included .devcontainer configuration. This configuration creates a docker container which includes a fully working netbox installation. Currently it should work when using WSL 2. For this to work make sure you have Docker Desktop installed and the WSL 2 integrations activated.

1. In the WSL terminal, enter `code` to run Visual studio code.
2. Install the devcontainer extension "ms-vscode-remote.remote-containers"
3. Press Ctrl+Shift+P and use the "Dev Container: Clone Repository in Container Volume" function to clone this repository. This will take a while depending on your computer
4. Start the netbox instance using `make all`

Your netbox instance will be served under 0.0.0.0:8008, so it should now be available under localhost:8008.


## Credits

This package was created with [Cookiecutter](https://github.com/audreyr/cookiecutter) and the [`netbox-community/cookiecutter-netbox-plugin`](https://github.com/netbox-community/cookiecutter-netbox-plugin) project template.
