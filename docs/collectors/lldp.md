# LLDP

The `lldp` collector inspects LLDP neighbors and creates `dcim.Cable`
rows between matching local and remote interfaces.

## NAPALM call

- `driver.get_lldp_neighbors_detail()`.

## Same-site rule

The collector deliberately restricts cable creation to neighbors in the
**same site** as the local device:

```python
if remote_device.site_id != device.site_id:
    self._log_info(...)
    continue
```

This prevents a misidentified hostname from creating an erroneous
intra-DC cable across sites. If you need cross-site cabling, the cables
must be added manually.

## Remote device resolution

`resolve_device_by_name()` matches the remote device by name with
progressive domain stripping: `switch.dc1.example.com` ->
`switch.dc1.example` -> `switch.dc1` -> `switch`, until a unique match is
found. `MultipleObjectsReturned` propagates and triggers a warning;
`DoesNotExist` results in a skipped neighbor.

## What it produces

Per matched neighbor:

```json
{
  "local_interface": "ge-0/0/0",
  "remote_device": "switch2.example.com",
  "remote_interface": "ge-0/0/4",
  "remote_chassis_id": "aa:bb:cc:11:22:33"
}
```

Action: always `new` (the collector only emits an entry when both
interfaces exist and neither is already cabled).

## Apply behavior

- Construct a `Cable` between `local_iface` and `remote_iface` with
  status `connected`.
- Validate, save, tag with **Automatically Discovered**.
- Record a `JournalEntry` on the local device summarizing the link.

The corresponding apply handler in
`netbox_facts/helpers/applier.py::_apply_lldp_entry` re-runs the same
checks in case the topology changed between detection and apply (one of
the interfaces having gained a cable in the interim raises
`ValueError("Interface already has a cable")`, marking the entry as
failed).

## Skip conditions

The collector silently skips a neighbor when any of the following hold:

- `remote_system_name` or `remote_port` is empty.
- The remote device cannot be resolved or is in a different site.
- The remote interface does not exist in NetBox.
- Either interface already has a cable.
