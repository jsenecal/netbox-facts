ARG NETBOX_VARIANT=v3.7

FROM netboxcommunity/netbox:${NETBOX_VARIANT}

RUN mkdir -pv /plugins/netbox-facts
COPY . /plugins/netbox-facts

RUN /opt/netbox/venv/bin/python3 /plugins/netbox-facts/setup.py develop && \
    cp -rf /plugins/netbox-facts/netbox_facts/ /opt/netbox/venv/lib/python3.10/site-packages/netbox_facts
