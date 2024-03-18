from django.urls import include, path

from netbox.views.generic import ObjectChangeLogView, ObjectJournalView
from utilities.urls import get_model_urls
from . import models, views


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
    path(
        "mac-address/<int:pk>/changelog/",
        ObjectChangeLogView.as_view(),
        name="macaddress_changelog",
        kwargs={"model": models.MACAddress},
    ),
    path(
        "mac-address/<int:pk>/journal/",
        ObjectJournalView.as_view(),
        name="macaddress_journal",
        kwargs={"model": models.MACAddress},
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
        "mac-vendor/<int:pk>/", views.MACVendorView.as_view(), name="macvendor_detail"
    ),
    path(
        "mac-vendor/<int:pk>/edit/",
        views.MACVendorEditView.as_view(),
        name="macvendor_edit",
    ),
    path(
        "mac-vendor/<int:pk>/delete/",
        views.MACVendorDeleteView.as_view(),
        name="macvendor_delete",
    ),
    path(
        "mac-vendor/<int:pk>/instances/",
        views.MACVendorInstancesView.as_view(),
        name="macvendor_instances",
    ),
    path(
        "mac-vendor/<int:pk>/changelog/",
        ObjectChangeLogView.as_view(),
        name="macvendor_changelog",
        kwargs={"model": models.MACVendor},
    ),
    path(
        "mac-vendor/<int:pk>/journal/",
        ObjectJournalView.as_view(),
        name="macvendor_journal",
        kwargs={"model": models.MACVendor},
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
    # path(
    #     "collector/<int:pk>/run/",
    #     views.CollectorRunView.as_view(),
    #     name="collectionplan_run",
    # ),
    # path(
    #     "collector/<int:pk>/edit/",
    #     views.CollectorEditView.as_view(),
    #     name="collectionplan_edit",
    # ),
    # path(
    #     "collector/<int:pk>/delete/",
    #     views.CollectorDeleteView.as_view(),
    #     name="collectionplan_delete",
    # ),
    path(
        "collection-plans/edit/",
        views.CollectorBulkEditView.as_view(),
        name="collectionplan_bulk_edit",
    ),
    # path(
    #     "collector/<int:pk>/instances/",
    #     views.CollectorInstancesView.as_view(),
    #     name="collectionplan_instances",
    # ),
    # path(
    #     "collector/<int:pk>/changelog/",
    #     ObjectChangeLogView.as_view(),
    #     name="collectionplan_changelog",
    #     kwargs={"model": models.Collector},
    # ),
    # path(
    #     "collector/<int:pk>/journal/",
    #     ObjectJournalView.as_view(),
    #     name="collectionplan_journal",
    #     kwargs={"model": models.Collector},
    # ),
)
