# EVPN

The `evpn` collector dispatches to a vendor-specific implementation. Only
`junos` is wired up today.

## Junos collection

- Runs `driver.cli(["show evpn mac-table"])`.
- Scrapes MAC addresses out of the raw output via a single regex:
  `([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})`.

## What it produces

Per MAC matched in the output:

```json
{"mac": "aa:bb:cc:11:22:33"}
```

Action: `confirmed` if a `MACAddress` already exists, else `new`.

## Apply behavior

- Get-or-create the `MACAddress`.
- Set `discovery_method = evpn` and `last_seen = now`.

A single `JournalEntry` is recorded on the device for the run, including
the first 2000 characters of the raw `show evpn mac-table` output.

## Limitations

- The collector does not currently parse interface, VTEP, ESI, or
  encapsulation context out of the EVPN output.
- Only Junos is supported. Adding another vendor requires implementing
  `_evpn_<vendor>(self, driver)` and registering the driver in the
  `vendor_map` inside `_get_vendor_method()`. See
  [Vendor Dispatch](../developer/vendor-dispatch.md).
