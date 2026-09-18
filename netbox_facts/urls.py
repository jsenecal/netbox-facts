from django.urls import include, path
from utilities.urls import get_model_urls

from . import views

urlpatterns = (
    path("mac-addresses/", views.MACAddressListView.as_view(), name="macaddress_list"),
    path(
        "mac-address/add/",
        views.MACAddressEditView.as_view(),
        name="macaddress_add",
    ),
    path(
        "mac-addresses/edit/",
        views.MACAddressBulkEditView.as_view(),
        name="macaddress_bulk_edit",
    ),
    path(
        "mac-addresses/delete/",
        views.MACAddressBulkDeleteView.as_view(),
        name="macaddress_bulk_delete",
    ),
    path(
        "mac-address/<int:pk>/",
        include(get_model_urls("netbox_facts", "macaddress")),
    ),
    path("mac-vendors/", views.MACVendorListView.as_view(), name="macvendor_list"),
    path(
        "mac-vendors/edit/",
        views.MACVendorBulkEditView.as_view(),
        name="macvendor_bulk_edit",
    ),
    path(
        "mac-vendors/delete/",
        views.MACVendorBulkDeleteView.as_view(),
        name="macvendor_bulk_delete",
    ),
    path("mac-vendor/add/", views.MACVendorEditView.as_view(), name="macvendor_add"),
    path(
        "mac-vendor/<int:pk>/",
        include(get_model_urls("netbox_facts", "macvendor")),
    ),
    path(
        "collection-plans/",
        views.CollectionPlanListView.as_view(),
        name="collectionplan_list",
    ),
    path(
        "collection-plan/add/",
        views.CollectorEditView.as_view(),
        name="collectionplan_add",
    ),
    path(
        "collection-plans/delete/",
        views.CollectorBulkDeleteView.as_view(),
        name="collectionplan_bulk_delete",
    ),
    path(
        "collection-plan/<int:pk>/",
        include(get_model_urls("netbox_facts", "collectionplan")),
    ),
    path(
        "collection-plans/edit/",
        views.CollectorBulkEditView.as_view(),
        name="collectionplan_bulk_edit",
    ),
    # Facts Reports
    path(
        "facts-reports/",
        views.FactsReportListView.as_view(),
        name="factsreport_list",
    ),
    path(
        "facts-reports/delete/",
        views.FactsReportBulkDeleteView.as_view(),
        name="factsreport_bulk_delete",
    ),
    path(
        "facts-report/<int:pk>/",
        include(get_model_urls("netbox_facts", "factsreport")),
    ),
)
