# Inventory

The `inventory` collector keeps device-level facts and (on Junos) chassis
hardware in sync with NetBox.

## NAPALM calls

- `driver.get_facts()` for serial / OS version / hostname / fqdn.
- `driver.get_chassis_inventory()` if available (the bundled enhanced
  Junos driver provides one). Used to reconcile `dcim.InventoryItem` and
  `dcim.Module` rows.

## Device-level entry

For every device, the collector emits a single entry whose
`object_repr` is the device markdown link.

`detected_values`:

```json
{
  "serial_number": "JN12345678AB",
  "os_version": "21.4R3.10",
  "hostname": "core-01",
  "fqdn": "core-01.example.com"
}
```

`current_values`: `{"serial_number": "<existing>"}`.

Action:

- `changed` if the serial differs from `Device.serial`.
- `confirmed` otherwise.

Apply behavior writes the new serial to `Device.serial` and records a
`JournalEntry` summarizing OS version, hostname, and FQDN.

## Chassis inventory (Junos)

When `driver.get_chassis_inventory()` exists, the collector also walks
hardware modules.

For each chassis module:

- Skip `BUILTIN` modules and Routing Engines (the latter are covered by
  `get_facts`).
- Compare to the existing `InventoryItem` for the device by name. Action
  is `new`, `changed` (serial / part_id / description differ), or
  `confirmed`.
- If a matching `dcim.ModuleBay` and `dcim.ModuleType` exist for the
  device's manufacturer (matched on `part_number`, then `model`), an
  additional Module entry is emitted with `object_repr = "Module <bay>"`.

`detected_values` for an inventory item:

```json
{
  "name": "FPC 0",
  "parent_name": null,
  "serial": "AB1234567",
  "part_id": "MX-MPC2E-3D",
  "description": "Modular Port Concentrator"
}
```

For a chassis module:

```json
{
  "name": "FPC 0/PIC 0",
  "component_name": "PIC 0",
  "parent_name": "FPC 0",
  "serial": "BB9876543",
  "part_id": "MIC-3D-4XGE-XFP",
  "description": "...",
  "module_bay_id": 42,
  "module_type_id": 7
}
```

## Stale detection

- `InventoryItem` rows on this device with `discovered=True` whose names
  were not seen this run are flagged `stale`. Apply deletes them.
- `Module` rows on this device tagged **Automatically Discovered** whose
  bays were not visited are flagged `stale`. Apply deletes them.

## Apply specifics

- Newly created `InventoryItem` rows are saved with `discovered=True` and
  tagged auto-discovered.
- Module creation goes through `create_module()` which sets
  `_adopt_components=True` and `_disable_replication=True` so existing
  child components are adopted instead of being duplicated.
- Parent / child relationships are resolved within the same run by
  remembering newly-created items in `created_items` and modules in
  `modules_by_name`.
