# L2 Circuits

The `l2_circuits` collector dispatches to a vendor-specific implementation.
Only `junos` is wired up today.

## Junos collection

- Runs `driver.cli(["show l2circuit connections"])`.
- The truncated raw output (first 2000 characters) is captured verbatim.

## What it produces

A single `FactsReportEntry` per device:

```json
{"raw_output": "..."}
```

Action: `confirmed` (the collector does not currently parse circuit
state, so it simply records that data was collected).

## Apply behavior

Apply creates a `JournalEntry` on the device whose body is a fenced code
block containing the raw output:

````markdown
L2 circuit data collected:
```
<truncated output>
```
````

## Limitations

- No structured circuit objects are created (NetBox core does not yet
  ship a generic L2 circuit model that this plugin can target).
- Only Junos is supported. See
  [Vendor Dispatch](../developer/vendor-dispatch.md) for adding a vendor.
