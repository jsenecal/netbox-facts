# Code Review — 2026-02-28

Comprehensive analysis of the netbox-facts plugin codebase at v0.0.1.

---

## Critical

### 1. ~~`***REDACTED***` left as default settings~~ (Fixed)
`netbox_facts/__init__.py` — `git-filter-repo` replaced hardcoded credentials with `***REDACTED***` across all history, including the working tree defaults. These have been restored to empty strings `""`.

### 2. Potential infinite recursion in signals
`signals.py` — `post_save` on `MACVendor` updates all matching `MACAddress` objects. Each MAC save triggers `handle_mac_change()`, which may look up vendors again. No recursion guard exists.

### 3. No `@transaction.atomic` in applier
`helpers/applier.py` — `apply_entries()` processes entries individually. A failure partway through leaves the report in a corrupted partial state with no rollback.

### 4. API doesn't validate entry ownership
`api/views.py` — The apply/skip endpoints don't verify that the entry belongs to the report in the URL. A user could apply entries from any report.

### 5. ~~Typo in journal entries~~ (Fixed)
`helpers/collector.py:320` — `"Dicovered by"` → `"Discovered by"`.

---

## High

### 6. N+1 queries in `get_devices_queryset()`
`models/collection_plan.py:260-288` — Calls `.all()` on each M2M field separately, producing multiple subqueries. Should use Q objects in a single query.

### 7. Monolithic collector class
`helpers/collector.py` — `NapalmCollector` is 1,095 lines handling all 10 collector types. Should be refactored into per-collector-type strategy classes.

### 8. Broad `except Exception` throughout
Multiple locations catch all exceptions and either swallow them or wrap them generically, masking the real error (connection timeout vs auth failure vs driver bug).

### 9. No job timeout
NAPALM connections can hang indefinitely. No RQ timeout is configured, and no connection-level timeout is exposed to users.

### 10. Silent exception swallowing in applier
`helpers/applier.py` — `except Interface.DoesNotExist: pass` in multiple handlers with no logging.

### 11. Test coverage ~24%
Major gaps: no per-collector-type tests, no error path tests, no concurrency tests, no scale tests.

---

## Medium

### 12. Junos-centric interface regex
`__init__.py:26` — The default `valid_interfaces_re` only matches Junos naming (`ge-`, `xe-`, `et-`, `irb`, `ae`, etc.). Other vendors get zero results without reconfiguring.

### 13. Hard-coded "Automatically Discovered" tag
`helpers/collector.py:44` — Should be configurable or namespaced to the plugin.

### 14. N+1 queries in ARP/NDP processing
`helpers/collector.py:250-256` — `MACAddress.objects.filter()` and `IPAddress.objects.filter()` called per entry instead of batching.

### 15. Vendor lookup on every MAC save
`models/mac_address.py` — `MACAddress.save()` always calls `get_by_mac_address()` even if the MAC hasn't changed.

### 16. No rate limiting
Users can spam the "Run" button queuing many jobs, and API endpoints have no throttling.

### 17. Missing bulk import form
Referenced in `__all__` but not implemented.

### 18. Stale job recovery
If `plan.run()` crashes, status stays `WORKING` forever. `check_stalled()` only runs on plan init, not periodically.

---

## Low

### 19. No dry-run preview
`detect_only=True` creates a report but there's no way to preview what would change before committing.

### 20. No rollback capability
Applied entries are permanent, no undo.

### 21. No stale IP deprecation
Collector adds but never removes or deprecates old data.

### 22. CI missing linting/coverage
No flake8, mypy, bandit, or coverage reporting in the pipeline despite being configured in `pyproject.toml`.

### 23. Minimal documentation
No API docs, no collector-type docs, no troubleshooting guide.
