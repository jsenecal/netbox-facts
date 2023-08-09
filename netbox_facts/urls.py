from django.urls import path

from netbox.views.generic import ObjectChangeLogView, ObjectJournalView
from . import models, views


urlpatterns = (
    path("mac-addresses/", views.MACAddressListView.as_view(), name="macaddress_list"),
    path("mac-addresses/edit/", views.MACAddressBulkEditView.as_view(), name="macaddress_bulk_edit"),
    path("mac-addresses/delete/", views.MACAddressBulkDeleteView.as_view(), name="macaddress_bulk_delete"),
    path("mac-address/add/", views.MACAddressEditView.as_view(), name="macaddress_add"),
    path("mac-address/<int:pk>/", views.MACAddressView.as_view(), name="macaddress_detail"),
    path("mac-address/<int:pk>/edit/", views.MACAddressEditView.as_view(), name="macaddress_edit"),
    path("mac-address/<int:pk>/delete/", views.MACAddressDeleteView.as_view(), name="macaddress_delete"),
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
    path("mac-vendor/add/", views.MACVendorEditView.as_view(), name="macvendor_add"),
    path("mac-vendor/edit/", views.MACVendorBulkEditView.as_view(), name="macvendor_bulk_edit"),
    path("mac-vendor/delete/", views.MACVendorBulkDeleteView.as_view(), name="macvendor_bulk_delete"),
    path("mac-vendor/<int:pk>/", views.MACVendorView.as_view(), name="macvendor_detail"),
    path("mac-vendor/<int:pk>/edit/", views.MACVendorEditView.as_view(), name="macvendor_edit"),
    path("mac-vendor/<int:pk>/delete/", views.MACVendorDeleteView.as_view(), name="macvendor_delete"),
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
)
