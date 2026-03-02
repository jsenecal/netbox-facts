# Code Review — 2026-02-28

Comprehensive analysis of the netbox-facts plugin codebase at v0.0.1.

---

## Critical

### 1. ~~`***REDACTED***` left as default settings~~ (Fixed)
`netbox_facts/__init__.py` — `git-filter-repo` replaced hardcoded credentials with `***REDACTED***` across all history, including the working tree defaults. These have been restored to empty strings `""`.

### 2. ~~Potential infinite recursion in signals~~ (Fixed)
`signals.py` — `post_save` on `MACVendor` updates all matching `MACAddress` objects. Each MAC save triggers `handle_mac_change()`, which may look up vendors again. Fixed by using `MACAddress.objects.filter().update()` instead of `instance.save()` to avoid re-triggering signals.

### 3. ~~No `@transaction.atomic` in applier~~ (Fixed)
`helpers/applier.py` — `apply_entries()` now uses `transaction.atomic()` with per-entry savepoints so individual failures don't corrupt the report.

### 4. ~~API doesn't validate entry ownership~~ (Fixed)
`api/views.py` — The apply/skip endpoints now validate that all entry PKs belong to the report before processing.

### 5. ~~Typo in journal entries~~ (Fixed)
`helpers/collector.py:320` — `"Dicovered by"` → `"Discovered by"`.

---

## High

### 6. ~~N+1 queries in `get_devices_queryset()`~~ (Fixed)
Refactored from 12 individual `.exists()`/`.filter()` calls to a single Q-object chain.

### 7. Monolithic collector class
`helpers/collector.py` — `NapalmCollector` is 1,095 lines handling all 10 collector types. Should be refactored into per-collector-type strategy classes.

**Effort:** xl (multi-day refactor). Deferred.

### 8. ~~Broad `except Exception` throughout~~ (Fixed)
Narrowed to specific NAPALM exceptions (`ConnectionException`, `CommandErrorException`, `CommandTimeoutException`, `NapalmException`, `ModuleImportError`) and Django exceptions (`IntegrityError`, `ValidationError`, `DatabaseError`).

### 9. ~~No job timeout~~ (Fixed)
Added 30-minute default RQ job timeout (configurable via `job_timeout` setting) and 60-second NAPALM connection timeout (configurable via `napalm_timeout` setting).

### 10. ~~Silent exception swallowing in applier~~ (Fixed)
Added `logger.warning()` to 6 silent `except: pass` blocks.

### 11. ~~Test coverage ~24%~~ (Improved to 81%)
Added 9 new tests covering rate limiting guard, `recover_stale_jobs` management command, and bulk import forms. Total: 173 tests, 81% coverage.

---

## Medium

### 12. ~~Junos-centric interface regex~~ (Fixed)
Default widened from Junos-specific patterns to `.*`. Junos regex documented as configuration example.

### 13. ~~Hard-coded "Automatically Discovered" tag~~ (Fixed)
Extracted to shared `constants.py`, imported in both `collector.py` and `applier.py`.

### 14. ~~N+1 queries in ARP/NDP processing~~ (Fixed)
Pre-fetches all MACs with a single `filter(mac_address__in=...)` query and all IPs via PostgreSQL `HOST()` function before entering the ARP/NDP loop. Uses in-memory dict lookups instead of per-entry DB queries.

### 15. ~~Vendor lookup on every MAC save~~ (Fixed)
Guarded to only run when `vendor is None`.

### 16. ~~No rate limiting~~ (Fixed)
Added duplicate job guard in `enqueue_collection_job()` (rejects when QUEUED/WORKING), `FactsMutationThrottle` (30/min) on API apply/skip/run actions, and API `run` endpoint on `CollectorViewSet`.

### 17. ~~Missing bulk import form~~ (Fixed)
Added `MACAddressImportForm`, `MACVendorImportForm`, `CollectionPlanImportForm` and corresponding `BulkImportView` views with `@register_model_view`.

### 18. ~~Stale job recovery~~ (Fixed)
Added `recover_stale_jobs` management command that finds plans stuck in WORKING/QUEUED with no active job and marks them STALLED.

---

## Low

### 19. No dry-run preview
`detect_only=True` creates a report but there is no way to preview without persisting to the database. In practice, `detect_only` serves as the preview mechanism.

**Effort:** large (full day+). Deferred — requires new API surface and UI.

### 20. No rollback capability
Applied entries are permanent, no undo. Each collector type would need a reverse handler.

**Effort:** xl (multi-day). Deferred.

### 21. ~~No stale IP deprecation~~ (Fixed)
Added stale IP detection at the end of `_ip_neighbors()`. After processing the ARP/NDP table, queries previously discovered IPs on the device's interfaces and records `ACTION_STALE` entries for IPs no longer present.

### 22. ~~CI missing linting/coverage~~ (Fixed)
Added ruff lint step and coverage reporting (fail-under=20%) to GitHub Actions workflow.

### 23. ~~Minimal documentation~~ (Partially fixed)
README rewritten with features, config reference, and dev setup. CONTRIBUTING updated. CHANGELOG added. Still missing: dedicated API docs, per-collector-type docs, troubleshooting guide.

---

## Implementation Plan

### Batch 1: Quick wins (no migrations, no risk)
- [x] #1 — Credentials removed
- [x] #2 — Signal recursion fixed
- [x] #3 — Transaction safety added
- [x] #4 — Entry ownership validation
- [x] #5 — Typo fixed
- [x] #6 — Q-object refactor of `get_devices_queryset()`
- [x] #10 — Add logging to silent `except: pass` blocks
- [x] #12 — Widen default interface regex
- [x] #13 — Extract `AUTO_D_TAG` to shared `constants.py`
- [x] #15 — Guard vendor lookup on MAC save
- [x] #22 — Add ruff/coverage to CI

### Batch 2: Exception handling & timeouts
- [x] #8 — Narrow `except Exception` to specific exceptions
- [x] #9 — Add RQ job timeout + NAPALM connection timeout

### Batch 3: Performance
- [x] #14 — Batch MAC/IP lookups in ARP/NDP loop (pre-fetch MACs and IPs with bulk queries)

### Batch 4: API & form improvements
- [x] #16 — Duplicate job guard + DRF throttling on mutating actions + API run endpoint
- [x] #17 — Bulk import forms for MACAddress, MACVendor, CollectionPlan

### Batch 5: Operational robustness
- [x] #18 — `recover_stale_jobs` management command
- [x] #21 — Stale IP detection in ARP/NDP collector

### Batch 6: Test coverage
- [x] #11 — 9 new tests: enqueue guard, management command, import forms (173 total, 81% coverage)

### Deferred (multi-day refactors)
- #7 — Monolithic collector refactor
- #19 — Dry-run preview
- #20 — Rollback capability
