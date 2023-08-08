"""
Creates API endpoint URLs for the plugin.
"""

from netbox.api.routers import NetBoxRouter

from . import views

app_name = "netbox_facts"

router = NetBoxRouter()
router.register(
    "macaddresses",
    views.MACAddressViewSet,
)
router.register(
    "macvendors",
    views.MACVendorViewSet,
)

urlpatterns = router.urls
