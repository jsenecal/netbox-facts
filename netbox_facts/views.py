"""Views for the netbox_facts plugin."""
from django.db.models import Count
from django.utils.translation import gettext as _
from utilities.views import ViewTab, register_model_view

from netbox.views import generic

from . import filtersets, forms, models, tables


class MACAddressView(generic.ObjectView):
    queryset = models.MACAddress.objects.all()


class MACAddressListView(generic.ObjectListView):
    queryset = models.MACAddress.objects.all().annotate(
        occurences=Count("seen_by_interfaces"),
    )
    table = tables.MACAddressTable


class MACAddressEditView(generic.ObjectEditView):
    queryset = models.MACAddress.objects.all()
    form = forms.MACAddressForm


class MACAddressDeleteView(generic.ObjectDeleteView):
    queryset = models.MACAddress.objects.all()


class MACAddressBulkEditView(generic.BulkEditView):
    queryset = models.MACAddress.objects.all()
    filterset = filtersets.MACAddressFilterSet
    table = tables.MACAddressTable


class MACAddressBulkDeleteView(generic.BulkDeleteView):
    queryset = models.MACAddress.objects.all()
    filterset = filtersets.MACAddressFilterSet
    table = tables.MACAddressTable


@register_model_view(models.MACVendor)
class MACVendorView(generic.ObjectView):
    """View for MACVendor instances."""

    queryset = models.MACVendor.objects.all()


@register_model_view(models.MACVendor, "instances")
class MACVendorInstancesView(generic.ObjectChildrenView):
    """View for MACVendor instances, instances."""

    queryset = models.MACVendor.objects.all()
    template_name = "netbox_facts/macvendor_instances.html"
    child_model = models.MACAddress
    table = tables.MACAddressTable
    filterset = filtersets.MACAddressFilterSet
    tab = ViewTab(
        label=_("Instances"),
        badge=lambda x: x.instances.all().count(),
        permission="netbox_facts.view_macaddress",
        weight=500,
    )

    def get_table(self, data, request, bulk_actions=True):
        """
        Return the django-tables2 Table instance to be used for rendering the objects list.

        Args:
            data: Queryset or iterable containing table data
            request: The current request
            bulk_actions: Render checkboxes for object selection
        """
        table = self.table(data, user=request.user)
        if "pk" in table.base_columns and bulk_actions:  # type: ignore
            table.columns.show("pk")

        table.columns.hide("vendor")

        table.configure(request)
        return table

    def get_children(self, request, parent):
        if self.child_model is not None:
            return (
                self.child_model.objects.restrict(request.user, "view").filter(vendor=parent).prefetch_related("tags")  # type: ignore
            )


class MACVendorListView(generic.ObjectListView):
    queryset = models.MACVendor.objects.all()
    table = tables.MACVendorTable


class MACVendorEditView(generic.ObjectEditView):
    queryset = models.MACVendor.objects.all()
    form = forms.MACVendorForm


class MACVendorDeleteView(generic.ObjectDeleteView):
    queryset = models.MACVendor.objects.all()
