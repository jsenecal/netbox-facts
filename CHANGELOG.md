# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Releases prior to 1.0.x use the legacy `## VERSION (DATE)` heading style.



## [0.1.1] - 2026-05-01

### Fixed

* `MACIPAddressesView.get_table` and `MACVendorInstancesView.get_table` no longer pass the `user` kwarg to `BaseTable.__init__`. NetBox 4.5 removed that kwarg, so the "IP Addresses" tab on the MAC address detail and the "Instances" tab on the MAC vendor detail crashed with `TypeError: Table.__init__() got an unexpected keyword argument 'user'`. User-specific column/ordering preferences are now applied entirely via `table.configure(request)`, which both overrides already invoke. (#4)

### Changed

* Removed the redundant `MACIPAddressesView.get_table` override; the view now inherits upstream `TableMixin.get_table`, which adds support for persisting saved `TableConfig` selections via the `tableconfig_id` query parameter.

### Tests

* Added a regression test pinning the post-fix Table init contract for `MACAddressTable` and the upstream `IPAddressTable` mounted in the IP-addresses child view.

## [0.1.0] - 2026-04-28

### Breaking Changes

* Migrated from setuptools to hatchling build backend with `pyproject.toml`.
* Removed hardcoded default NAPALM credentials from plugin settings; `napalm_username` and `napalm_password` now default to empty strings and must be configured explicitly.

### Added (toolkit normalization)

* Canonical 5 GHA workflows (ci.yml, publish.yml, docs.yml, release-drafter.yml, pr-title.yml) + `.github/release-drafter.yml`. Replaces the previous `tests.yml` + `mkdocs.yml` workflow setup. CI now matrixes Python 3.12-3.14 x NetBox 4.5.3/4.5.8 with full migrate / pytest / makemigrations check / system check / build steps and OIDC codecov upload.
* `.pre-commit-config.yaml` with ruff hooks + standard pre-commit-hooks + commit-msg AI-attribution rejecter.
* `.git-template/hooks/commit-msg` (canonical hook tracked in-tree).
* `docs/zensical.toml` (replaces root `mkdocs.yml`; same nav).
* `uv.lock` committed.
* Empty `tests/` directory and pytest config (was using `manage.py test netbox_facts`).

### Removed (toolkit normalization)

* `tests.yml` workflow (replaced by `ci.yml`).
* `mkdocs.yml` workflow (replaced by `docs.yml`).
* Root `mkdocs.yml` config (replaced by `docs/zensical.toml`).
* Dev deps no longer needed: `autoflake`, `autopep8`, `bandit`, `black`, `bump2version`, `debugpy`, `flake8`, `ipython`, `isort`, `mypy`, `mypy-extensions`, `pycodestyle`, `pydocstyle`, `pylint`, `pylint-django`, `rich`, `sourcery-analytics`, `mkdocs`, `mkdocs-material`, `mkdocs-include-markdown-plugin`, `mkdocstrings[python]`, `twine`, `watchdog`, `wheel`, `wily`, `yapf`. Kept: `pre-commit`, `pytest`, `ruff`. Added: `pytest-django`, `pytest-cov`, `bumpver`. `zensical` lives in a separate `[docs]` extra.
* `[tool.isort]`, `[tool.mypy]`, `[tool.bumpversion]` sections.

### Changed (toolkit normalization)

* Build is now `uv build` (was `python -m build`).
* `[tool.bumpversion]` -> `[tool.bumpver]` with the canonical file_patterns including a `CHANGELOG.md` promotion pattern. Tag pattern `vMAJOR.MINOR.PATCH`.
* Ruff selectors expanded from `["E", "F", "W"]` to the canonical set (`E, F, W, I, N, UP, S, B, A, C4, DJ, PIE`); ignore `N806` globally for the Django `User = get_user_model()` idiom; per-file ignores added for migrations and tests.
* CHANGELOG converted to Keep-a-Changelog format with bracketed `[Unreleased]` heading. Pre-1.0.x entries kept in their existing `## VERSION (DATE)` style.

### Added

* **NetBox 4.5.x compatibility** — updated models, views, and APIs for the NetBox 4.x plugin framework
* **10 collector types**: ARP, NDP, Inventory, Interfaces, LLDP, Ethernet Switching, L2 Circuits, EVPN, BGP, OSPF
* **Detect-only mode** (`detect_only` flag on CollectionPlan) — collection runs produce a `FactsReport` without modifying NetBox objects; changes can be reviewed and selectively applied or skipped
* **FactsReport / FactsReportEntry models** — track detected facts with action types (new/changed/confirmed/stale) and apply status (pending/applied/skipped/failed)
* **Auto-scheduling** — `CollectionPlan` with an interval automatically schedules recurring jobs via `CollectionJobRunner.enqueue_once()`, mirroring NetBox's DataSource sync pattern
* **JobRunner integration** — `CollectionJobRunner` extends NetBox's `JobRunner` with job log persistence and report linking
* **Vendor dispatch framework** — extensible per-vendor collector methods with Junos-specific L2 circuits, EVPN, and OSPF collectors
* **LLDP collector** with same-site cable auto-creation
* **BGP collector** with ASN and VRF support
* **Optional netbox-routing integration** — BGP and OSPF collectors use `netbox-routing` plugin models when installed
* **REST API** — full CRUD endpoints for MAC addresses, MAC vendors, collection plans, and facts reports
* **CI test workflow** — GitHub Actions running tests inside the NetBox container with PostgreSQL and Redis services
* **Dev container improvements** — updated to NetBox 4.5.3, migrated dependency management to uv

### Fixed

* MAC prefix handling and OUI vendor lookup
* Infinite recursion risk in MAC signal handlers
* UI forms with proper selectors, device field filtering, and driver selection
* Stalled job detection and status management
* Entry ownership validation in apply/skip API endpoints
* Transaction safety in applier with per-entry savepoints

## 0.0.1 (2023-08-02)

* First release on PyPI.
