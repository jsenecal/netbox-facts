# BGP netbox-routing Integration — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the BGP collector populate netbox-routing's BGPRouter, BGPScope, and BGPPeer models from NAPALM data.

**Architecture:** The existing `_bgp_routing_integration()` stub in the collector gets fleshed out to create the full BGPRouter -> BGPScope -> BGPPeer chain. The collector stores `bgp_data` during the main BGP loop so the integration method can iterate it. Report entries are created for each new routing object. The applier dispatches on `object_repr` prefix (`BGPRouter`, `BGPScope`, `BGPPeer`). The form hides BGP/OSPF collector types when netbox-routing is absent.

**Tech Stack:** Django ORM, netbox-routing 0.4.1, NAPALM, NetBox plugin API

**Design doc:** `docs/plans/2026-03-06-bgp-routing-integration-design.md`

---

### Task 1: Hide BGP/OSPF collector types when netbox-routing is absent

**Files:**
- Modify: `netbox_facts/forms.py:285-291` (CollectionPlanForm `__init__`)
- Modify: `netbox_facts/forms.py:356-358` (CollectionPlanFilterForm `collector_type`)
- Test: `netbox_facts/tests/test_forms.py` (new file or add to existing)

**Step 1: Write the failing test**

In `netbox_facts/tests/test_helpers.py` (where `HAS_NETBOX_ROUTING` tests already live), add:

```python
class HideCollectorTypeTests(TestCase):
    """Form should hide BGP/OSPF when netbox-routing is absent."""

    @patch("netbox_facts.forms.HAS_NETBOX_ROUTING", False)
    def test_form_hides_bgp_ospf_when_no_routing(self):
        from netbox_facts.forms import CollectionPlanForm
        form = CollectionPlanForm()
        choices = [c[0] for c in form.fields["collector_type"].choices]
        self.assertNotIn("bgp", choices)
        self.assertNotIn("ospf", choices)

    @patch("netbox_facts.forms.HAS_NETBOX_ROUTING", True)
    def test_form_shows_bgp_ospf_when_routing_present(self):
        from netbox_facts.forms import CollectionPlanForm
        form = CollectionPlanForm()
        choices = [c[0] for c in form.fields["collector_type"].choices]
        self.assertIn("bgp", choices)
        self.assertIn("ospf", choices)
```

**Step 2: Run test to verify it fails**

Run: `make test TEST_ARGS="-k HideCollectorType"`
Expected: FAIL — `HAS_NETBOX_ROUTING` not yet imported in forms.py

**Step 3: Write minimal implementation**

In `netbox_facts/forms.py`, add the import near the top:

```python
from netbox_facts.helpers.collector import HAS_NETBOX_ROUTING
```

In `CollectionPlanForm.__init__`, after `super().__init__()`, add:

```python
if not HAS_NETBOX_ROUTING:
    routing_types = {
        CollectionTypeChoices.TYPE_BGP,
        CollectionTypeChoices.TYPE_OSPF,
    }
    self.fields["collector_type"].choices = [
        c for c in self.fields["collector_type"].choices
        if c[0] not in routing_types
    ]
```

Do the same for `CollectionPlanFilterForm.__init__` (create one if it doesn't exist):

```python
def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    if not HAS_NETBOX_ROUTING:
        routing_types = {
            CollectionTypeChoices.TYPE_BGP,
            CollectionTypeChoices.TYPE_OSPF,
        }
        self.fields["collector_type"].choices = [
            c for c in self.fields["collector_type"].choices
            if c[0] not in routing_types
        ]
```

**Step 4: Run test to verify it passes**

Run: `make test TEST_ARGS="-k HideCollectorType"`
Expected: PASS

**Step 5: Commit**

```bash
git add netbox_facts/forms.py netbox_facts/tests/test_helpers.py
git commit -m "Hide BGP/OSPF collector types when netbox-routing is absent"
```

---

### Task 2: Store BGP data for routing integration

The main `bgp()` method needs to accumulate `local_as` and per-VRF peer data so `_bgp_routing_integration()` can use it. Currently, data flows through the loop but isn't stored.

**Files:**
- Modify: `netbox_facts/helpers/collector.py:1547-1665` (bgp method)

**Step 1: Write the failing test**

```python
class BGPRoutingDataAccumulationTest(CollectorTestBase):
    """bgp() should store _bgp_routing_data for the integration method."""

    def test_bgp_stores_routing_data(self):
        plan = self._create_plan(collector_type=CollectionTypeChoices.TYPE_BGP)
        collector = self._create_collector(plan)
        collector._current_device = self.device

        mock_driver = MagicMock()
        mock_driver.get_bgp_neighbors_detail.return_value = {
            "global": {
                65001: [{"remote_address": "10.0.0.1", "up": True, "local_as": 65000}],
            }
        }

        collector.bgp(mock_driver)
        self.assertIsNotNone(getattr(collector, "_bgp_routing_data", None))
        self.assertEqual(collector._bgp_routing_data["local_as"], 65000)
```

**Step 2: Run test to verify it fails**

Run: `make test TEST_ARGS="-k test_bgp_stores_routing_data"`
Expected: FAIL — `_bgp_routing_data` doesn't exist

**Step 3: Write minimal implementation**

At the start of `bgp()`, after `device = self._current_device`, add:

```python
# Accumulate data for netbox-routing integration
self._bgp_routing_data = {
    "local_as": None,
    "vrfs": {},  # vrf_name -> list of (remote_address, as_number, nb_vrf, nb_ip, nb_asn)
}
```

Inside the peer loop (after `remote_address = peer.get("remote_address", "")`), capture `local_as`:

```python
if self._bgp_routing_data["local_as"] is None:
    local_as = peer.get("local_as")
    if local_as is not None:
        self._bgp_routing_data["local_as"] = int(local_as)
```

At the end of each peer's apply block (after `_mark_entry_applied` for the IP), record the peer data:

```python
self._bgp_routing_data["vrfs"].setdefault(vrf_name, []).append({
    "remote_address": remote_address,
    "as_number": int(as_number),
    "nb_vrf": nb_vrf,
    "nb_ip": nb_ip,
    "nb_asn": nb_asn,
})
```

**Step 4: Run test to verify it passes**

Run: `make test TEST_ARGS="-k test_bgp_stores_routing_data"`
Expected: PASS

**Step 5: Commit**

```bash
git add netbox_facts/helpers/collector.py netbox_facts/tests/test_helpers.py
git commit -m "Accumulate BGP peer data for netbox-routing integration"
```

---

### Task 3: Implement `_bgp_routing_integration()` — BGPRouter creation

**Files:**
- Modify: `netbox_facts/helpers/collector.py:1667-1690` (_bgp_routing_integration)
- Test: `netbox_facts/tests/test_helpers.py`

**Key model details:**
- `BGPRouter` uses a GFK: `assigned_object_type` (FK to ContentType) + `assigned_object_id`
- Unique constraint: `(assigned_object_type, assigned_object_id, asn)`
- Required: `asn` (FK to `ipam.ASN`)

**Step 1: Write the failing test**

```python
from django.contrib.contenttypes.models import ContentType

class BGPRoutingIntegrationTest(CollectorTestBase):
    """_bgp_routing_integration creates BGPRouter/BGPScope/BGPPeer."""

    def test_creates_bgp_router(self):
        from ipam.models import ASN, RIR
        from netbox_routing.models import BGPRouter

        plan = self._create_plan(collector_type=CollectionTypeChoices.TYPE_BGP)
        collector = self._create_collector(plan)
        collector._current_device = self.device

        rir = RIR.objects.first() or RIR.objects.create(name="Test RIR", slug="test-rir")
        local_asn, _ = ASN.objects.get_or_create(asn=65000, defaults={"rir": rir})

        collector._bgp_routing_data = {
            "local_as": 65000,
            "vrfs": {
                "global": [{
                    "remote_address": "10.0.0.1",
                    "as_number": 65001,
                    "nb_vrf": None,
                    "nb_ip": IPAddress.objects.create(address="10.0.0.1/32"),
                    "nb_asn": ASN.objects.get_or_create(asn=65001, defaults={"rir": rir})[0],
                }],
            },
        }

        collector._bgp_routing_integration()

        device_ct = ContentType.objects.get_for_model(self.device)
        router = BGPRouter.objects.filter(
            assigned_object_type=device_ct,
            assigned_object_id=self.device.pk,
            asn=local_asn,
        ).first()
        self.assertIsNotNone(router)
        self.assertTrue(router.tags.filter(name="Automatically Discovered").exists())
```

**Step 2: Run test to verify it fails**

Run: `make test TEST_ARGS="-k test_creates_bgp_router"`
Expected: FAIL — stub doesn't create anything

**Step 3: Write minimal implementation**

Replace the `_bgp_routing_integration` body:

```python
def _bgp_routing_integration(self):
    """Create BGPRouter/BGPScope/BGPPeer in netbox-routing if available."""
    if not HAS_NETBOX_ROUTING:
        return

    data = getattr(self, "_bgp_routing_data", None)
    if not data or data["local_as"] is None:
        self._log_info("No local AS found in BGP data; skipping netbox-routing integration.")
        return

    from django.contrib.contenttypes.models import ContentType
    from ipam.models import ASN, RIR
    from netbox_routing.models import BGPPeer, BGPRouter, BGPScope

    device = self._current_device
    device_ct = ContentType.objects.get_for_model(device)

    # Get-or-create local ASN
    try:
        local_asn, _ = ASN.objects.get_or_create(
            asn=data["local_as"],
            defaults={"rir": RIR.objects.first()},
        )
    except (RIR.DoesNotExist, TypeError):
        self._log_warning(f"No RIR in NetBox. Cannot create local ASN {data['local_as']}.")
        return

    # Get-or-create BGPRouter
    bgp_router, router_created = BGPRouter.objects.get_or_create(
        assigned_object_type=device_ct,
        assigned_object_id=device.pk,
        asn=local_asn,
    )
    if router_created:
        bgp_router.tags.add(AUTO_D_TAG)

    router_action = EntryActionChoices.ACTION_NEW if router_created else EntryActionChoices.ACTION_CONFIRMED
    self._record_entry(
        action=router_action,
        collector_type=self._collector_type,
        device=device,
        detected_values={"local_as": data["local_as"]},
        object_instance=bgp_router,
        object_repr=f"BGPRouter {device}",
    )

    if router_created:
        self._log_success(f"Created BGPRouter for {device} (AS{data['local_as']}).")
    else:
        self._log_info(f"Found existing BGPRouter for {device}.")

    # Per-VRF scopes and peers
    for vrf_name, peers in data["vrfs"].items():
        nb_vrf = peers[0]["nb_vrf"] if peers else None

        bgp_scope, scope_created = BGPScope.objects.get_or_create(
            router=bgp_router,
            vrf=nb_vrf,
        )
        if scope_created:
            bgp_scope.tags.add(AUTO_D_TAG)

        scope_action = EntryActionChoices.ACTION_NEW if scope_created else EntryActionChoices.ACTION_CONFIRMED
        scope_label = vrf_name if nb_vrf else "global"
        self._record_entry(
            action=scope_action,
            collector_type=self._collector_type,
            device=device,
            detected_values={"vrf": vrf_name if nb_vrf else None},
            object_instance=bgp_scope,
            object_repr=f"BGPScope {device} {scope_label}",
        )

        for peer_data in peers:
            nb_ip = peer_data.get("nb_ip")
            nb_asn = peer_data.get("nb_asn")
            if nb_ip is None:
                continue

            bgp_peer, peer_created = BGPPeer.objects.get_or_create(
                scope=bgp_scope,
                peer=nb_ip,
                defaults={"remote_as": nb_asn},
            )
            if peer_created:
                bgp_peer.tags.add(AUTO_D_TAG)

            peer_action = EntryActionChoices.ACTION_NEW if peer_created else EntryActionChoices.ACTION_CONFIRMED
            self._record_entry(
                action=peer_action,
                collector_type=self._collector_type,
                device=device,
                detected_values={
                    "remote_address": peer_data["remote_address"],
                    "remote_as": peer_data["as_number"],
                },
                object_instance=bgp_peer,
                object_repr=f"BGPPeer {peer_data['remote_address']} AS{peer_data['as_number']}",
            )
```

**Important:** The `get_or_create` + `tags.add(AUTO_D_TAG)` calls should only execute when `self._should_apply()`. When detect-only, only create report entries. Wrap the ORM calls:

```python
if self._should_apply():
    bgp_router, router_created = BGPRouter.objects.get_or_create(...)
    if router_created:
        bgp_router.tags.add(AUTO_D_TAG)
else:
    bgp_router = BGPRouter.objects.filter(...).first()
    router_created = bgp_router is None
```

Apply this pattern to BGPScope and BGPPeer creation as well. When detect-only and objects don't exist, set `object_instance=None` in `_record_entry`.

**Step 4: Run test to verify it passes**

Run: `make test TEST_ARGS="-k test_creates_bgp_router"`
Expected: PASS

**Step 5: Commit**

```bash
git add netbox_facts/helpers/collector.py netbox_facts/tests/test_helpers.py
git commit -m "Implement BGPRouter creation in _bgp_routing_integration"
```

---

### Task 4: Test BGPScope and BGPPeer creation

**Files:**
- Test: `netbox_facts/tests/test_helpers.py`

**Step 1: Write tests for scope and peer creation**

```python
def test_creates_bgp_scope_per_vrf(self):
    """Integration should create a BGPScope per VRF."""
    from netbox_routing.models import BGPScope
    # ... setup similar to test_creates_bgp_router ...
    collector._bgp_routing_integration()
    scope = BGPScope.objects.filter(router__asn__asn=65000, vrf=None).first()
    self.assertIsNotNone(scope)

def test_creates_bgp_peer(self):
    """Integration should create a BGPPeer for each neighbor."""
    from netbox_routing.models import BGPPeer
    # ... setup similar to test_creates_bgp_router ...
    collector._bgp_routing_integration()
    peer = BGPPeer.objects.filter(peer__address="10.0.0.1/32").first()
    self.assertIsNotNone(peer)
    self.assertEqual(peer.remote_as.asn, 65001)

def test_detect_only_does_not_create_routing_objects(self):
    """When detect_only=True, no BGPRouter/Scope/Peer should be created."""
    from netbox_routing.models import BGPRouter
    plan = self._create_plan(collector_type=CollectionTypeChoices.TYPE_BGP)
    plan.detect_only = True
    plan.save()
    collector = self._create_collector(plan)
    # ... setup _bgp_routing_data ...
    collector._bgp_routing_integration()
    self.assertFalse(BGPRouter.objects.exists())
    # But report entries should still be created
    entries = FactsReportEntry.objects.filter(object_repr__startswith="BGPRouter")
    self.assertTrue(entries.exists())

def test_idempotent_does_not_duplicate(self):
    """Running integration twice should not duplicate objects."""
    from netbox_routing.models import BGPRouter
    # ... setup ...
    collector._bgp_routing_integration()
    collector._bgp_routing_integration()
    ct = ContentType.objects.get_for_model(self.device)
    self.assertEqual(
        BGPRouter.objects.filter(
            assigned_object_type=ct,
            assigned_object_id=self.device.pk,
        ).count(),
        1,
    )
```

**Step 2: Run tests**

Run: `make test TEST_ARGS="-k BGPRoutingIntegration"`
Expected: PASS (implementation was done in Task 3)

**Step 3: Commit**

```bash
git add netbox_facts/tests/test_helpers.py
git commit -m "Add tests for BGPScope/BGPPeer creation and detect-only mode"
```

---

### Task 5: Applier — handle BGPRouter/BGPScope/BGPPeer entries

When a user clicks "Apply" on a detect-only report, the applier needs to create the netbox-routing objects.

**Files:**
- Modify: `netbox_facts/helpers/applier.py:489-532` (_apply_bgp_entry)
- Test: `netbox_facts/tests/test_applier.py`

**Step 1: Write the failing test**

```python
class ApplyBGPRoutingEntryTest(TestCase):
    """Applier should create BGPRouter/BGPScope/BGPPeer from entries."""

    def test_apply_bgp_router_entry(self):
        from netbox_routing.models import BGPRouter
        # Create a report entry with object_repr="BGPRouter {device}"
        # and detected_values={"local_as": 65000}
        entry = FactsReportEntry.objects.create(
            report=report,
            collector_type=CollectionTypeChoices.TYPE_BGP,
            device=device,
            action=EntryActionChoices.ACTION_NEW,
            detected_values={"local_as": 65000},
            object_repr=f"BGPRouter {device}",
        )
        _apply_bgp_entry(entry, now)
        self.assertTrue(BGPRouter.objects.filter(asn__asn=65000).exists())

    def test_apply_bgp_scope_entry(self):
        from netbox_routing.models import BGPScope
        # ... create BGPRouter first, then entry with "BGPScope {device} global"
        entry = FactsReportEntry.objects.create(
            ...,
            detected_values={"local_as": 65000, "vrf": None},
            object_repr=f"BGPScope {device} global",
        )
        _apply_bgp_entry(entry, now)
        self.assertTrue(BGPScope.objects.exists())

    def test_apply_bgp_peer_entry(self):
        from netbox_routing.models import BGPPeer
        # ... create BGPRouter + BGPScope first, then entry with "BGPPeer 10.0.0.1 AS65001"
        entry = FactsReportEntry.objects.create(
            ...,
            detected_values={"remote_address": "10.0.0.1", "remote_as": 65001, "local_as": 65000, "vrf": None},
            object_repr="BGPPeer 10.0.0.1 AS65001",
        )
        _apply_bgp_entry(entry, now)
        self.assertTrue(BGPPeer.objects.filter(peer__address="10.0.0.1/32").exists())
```

**Step 2: Run test to verify it fails**

Run: `make test TEST_ARGS="-k test_apply_bgp_router_entry"`
Expected: FAIL — no dispatch for `BGPRouter` prefix

**Step 3: Write minimal implementation**

In `_apply_bgp_entry`, add dispatches at the top (before the existing VRF dispatch):

```python
def _apply_bgp_entry(entry, now):
    """Apply a BGP peer IP/ASN entry, or a routing-model entry."""
    from ipam.models import ASN, RIR

    if entry.object_repr.startswith("VRF "):
        return _apply_vrf_entry(entry)

    if entry.object_repr.startswith("BGPRouter "):
        return _apply_bgp_router_entry(entry)

    if entry.object_repr.startswith("BGPScope "):
        return _apply_bgp_scope_entry(entry)

    if entry.object_repr.startswith("BGPPeer "):
        return _apply_bgp_peer_entry(entry)

    # ... existing IP/ASN logic below ...
```

Then add the three helper functions:

```python
def _apply_bgp_router_entry(entry):
    """Create a BGPRouter from a report entry."""
    from django.contrib.contenttypes.models import ContentType
    from ipam.models import ASN, RIR
    from netbox_routing.models import BGPRouter

    local_as = entry.detected_values.get("local_as")
    if local_as is None:
        raise ValueError("BGPRouter entry has no local_as")

    device = entry.device
    device_ct = ContentType.objects.get_for_model(device)

    asn_obj, _ = ASN.objects.get_or_create(
        asn=int(local_as),
        defaults={"rir": RIR.objects.first()},
    )
    router, created = BGPRouter.objects.get_or_create(
        assigned_object_type=device_ct,
        assigned_object_id=device.pk,
        asn=asn_obj,
    )
    if created:
        router.tags.add(AUTO_D_TAG)
    _set_entry_object(entry, router)


def _apply_bgp_scope_entry(entry):
    """Create a BGPScope from a report entry."""
    from django.contrib.contenttypes.models import ContentType
    from ipam.models import ASN, RIR
    from netbox_routing.models import BGPRouter, BGPScope

    local_as = entry.detected_values.get("local_as")
    vrf_name = entry.detected_values.get("vrf")
    device = entry.device
    device_ct = ContentType.objects.get_for_model(device)

    # Find or create the router first
    asn_obj, _ = ASN.objects.get_or_create(
        asn=int(local_as),
        defaults={"rir": RIR.objects.first()},
    )
    router, _ = BGPRouter.objects.get_or_create(
        assigned_object_type=device_ct,
        assigned_object_id=device.pk,
        asn=asn_obj,
    )

    nb_vrf = None
    if vrf_name:
        nb_vrf = resolve_vrf(vrf_name)

    scope, created = BGPScope.objects.get_or_create(
        router=router,
        vrf=nb_vrf,
    )
    if created:
        scope.tags.add(AUTO_D_TAG)
    _set_entry_object(entry, scope)


def _apply_bgp_peer_entry(entry):
    """Create a BGPPeer from a report entry."""
    import ipaddress as ipaddress_mod
    from django.contrib.contenttypes.models import ContentType
    from ipam.models import ASN, RIR
    from netbox_routing.models import BGPPeer, BGPRouter, BGPScope

    dv = entry.detected_values
    remote_address = dv.get("remote_address", "")
    as_number = dv.get("remote_as")
    local_as = dv.get("local_as")
    vrf_name = dv.get("vrf")
    device = entry.device
    device_ct = ContentType.objects.get_for_model(device)

    # Build the chain: Router -> Scope
    asn_obj, _ = ASN.objects.get_or_create(
        asn=int(local_as),
        defaults={"rir": RIR.objects.first()},
    )
    router, _ = BGPRouter.objects.get_or_create(
        assigned_object_type=device_ct,
        assigned_object_id=device.pk,
        asn=asn_obj,
    )

    nb_vrf = None
    if vrf_name:
        nb_vrf = resolve_vrf(vrf_name)

    scope, _ = BGPScope.objects.get_or_create(
        router=router,
        vrf=nb_vrf,
    )

    # IP + remote ASN
    ip_obj = ipaddress_mod.ip_address(remote_address)
    prefix_len = 32 if ip_obj.version == 4 else 128
    ip_str = f"{remote_address}/{prefix_len}"
    nb_ip, _ = get_or_create_ip(ip_str, vrf=nb_vrf)

    nb_remote_asn = None
    if as_number is not None:
        nb_remote_asn, _ = ASN.objects.get_or_create(
            asn=int(as_number),
            defaults={"rir": RIR.objects.first()},
        )

    peer, created = BGPPeer.objects.get_or_create(
        scope=scope,
        peer=nb_ip,
        defaults={"remote_as": nb_remote_asn},
    )
    if created:
        peer.tags.add(AUTO_D_TAG)
    _set_entry_object(entry, peer)
```

**Important:** The applier must also store `local_as` in `detected_values` for BGPPeer entries (needed to reconstruct the Router chain). The collector already stores `remote_as` and `remote_address`. We need to add `local_as` to the BGPPeer detected_values in the collector's `_bgp_routing_integration` method (Task 3 code):

```python
detected_values={
    "remote_address": peer_data["remote_address"],
    "remote_as": peer_data["as_number"],
    "local_as": data["local_as"],       # <-- add this
    "vrf": vrf_name if nb_vrf else None, # <-- add this
},
```

Similarly for BGPScope entries, add `local_as` to detected_values.

**Step 4: Run test to verify it passes**

Run: `make test TEST_ARGS="-k test_apply_bgp"`
Expected: PASS

**Step 5: Commit**

```bash
git add netbox_facts/helpers/applier.py netbox_facts/helpers/collector.py netbox_facts/tests/test_applier.py
git commit -m "Applier: handle BGPRouter/BGPScope/BGPPeer report entries"
```

---

### Task 6: Full integration test — end-to-end BGP collection

**Files:**
- Test: `netbox_facts/tests/test_helpers.py`

**Step 1: Write the end-to-end test**

```python
class BGPEndToEndTest(CollectorTestBase):
    """Full bgp() call should create IPs, ASNs, and routing objects."""

    def test_full_bgp_collection_creates_routing_chain(self):
        from ipam.models import ASN, RIR
        from netbox_routing.models import BGPPeer, BGPRouter, BGPScope

        rir = RIR.objects.first() or RIR.objects.create(name="Test RIR", slug="test-rir")
        plan = self._create_plan(collector_type=CollectionTypeChoices.TYPE_BGP)
        collector = self._create_collector(plan)
        collector._current_device = self.device

        mock_driver = MagicMock()
        mock_driver.get_bgp_neighbors_detail.return_value = {
            "global": {
                65001: [
                    {"remote_address": "10.0.0.1", "up": True, "local_as": 65000},
                    {"remote_address": "10.0.0.2", "up": False, "local_as": 65000},
                ],
                65002: [
                    {"remote_address": "10.0.0.3", "up": True, "local_as": 65000},
                ],
            },
        }

        collector.bgp(mock_driver)

        # Verify routing chain
        device_ct = ContentType.objects.get_for_model(self.device)
        router = BGPRouter.objects.get(
            assigned_object_type=device_ct,
            assigned_object_id=self.device.pk,
        )
        self.assertEqual(router.asn.asn, 65000)

        scope = BGPScope.objects.get(router=router, vrf=None)
        self.assertIsNotNone(scope)

        peers = BGPPeer.objects.filter(scope=scope)
        self.assertEqual(peers.count(), 3)

        # Verify report entries exist for routing objects
        entries = FactsReportEntry.objects.filter(
            report=collector._report,
            object_repr__startswith="BGPRouter",
        )
        self.assertEqual(entries.count(), 1)
```

**Step 2: Run test**

Run: `make test TEST_ARGS="-k test_full_bgp_collection_creates_routing_chain"`
Expected: PASS

**Step 3: Commit**

```bash
git add netbox_facts/tests/test_helpers.py
git commit -m "Add end-to-end BGP collection with netbox-routing test"
```

---

### Task 7: Run full test suite and fix regressions

**Step 1: Run all tests**

Run: `make test`
Expected: All 193+ tests pass

**Step 2: Fix any failures**

Common issues to watch for:
- Import errors if `netbox_routing` models have different field names than expected
- Existing BGP tests that mock `_bgp_routing_integration` may need updating
- `FactsReportEntry` queries in tests may need to account for new routing entries

**Step 3: Final commit**

```bash
git add -u
git commit -m "Fix test regressions from BGP routing integration"
```

---

## Summary of Changes

| File | Change |
|------|--------|
| `netbox_facts/forms.py` | Hide BGP/OSPF choices when no netbox-routing |
| `netbox_facts/helpers/collector.py` | Accumulate `_bgp_routing_data`; flesh out `_bgp_routing_integration()` |
| `netbox_facts/helpers/applier.py` | Add `_apply_bgp_router_entry`, `_apply_bgp_scope_entry`, `_apply_bgp_peer_entry` |
| `netbox_facts/tests/test_helpers.py` | Tests for form hiding, data accumulation, routing integration |
| `netbox_facts/tests/test_applier.py` | Tests for applier routing entry handlers |
| `.devcontainer/Dockerfile-plugin_dev` | Add `netbox-routing` to pip install (already done) |

## Not Changing

- Existing IP/ASN creation logic in `bgp()`
- `FactsReportEntry` model (GFK, no new fields)
- Migration files (no FK to netbox-routing)
- OSPF routing integration (separate future task)
