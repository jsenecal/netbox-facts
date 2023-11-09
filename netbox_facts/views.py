"""Views for the netbox_facts plugin."""
from django.db.models import Count
from django.utils.translation import gettext as _
from dcim.choices import DeviceStatusChoices
from utilities.views import ViewTab, register_model_view

from netbox.views import generic

from . import filtersets, forms, models, tables


class MACAddressView(generic.ObjectView):
    """View for MACAddress instances."""

    queryset = models.MACAddress.objects.all()


class MACAddressListView(generic.ObjectListView):
    """List view for MACAddress instances."""

    queryset = models.MACAddress.objects.all().annotate(
        occurences=Count("known_by"),
    )
    table = tables.MACAddressTable


class MACAddressEditView(generic.ObjectEditView):
    """Edit view for MACAddress instances."""

    queryset = models.MACAddress.objects.all()
    form = forms.MACAddressForm


class MACAddressDeleteView(generic.ObjectDeleteView):
    """Delete view for MACAddress instances."""

    queryset = models.MACAddress.objects.all()


class MACAddressBulkEditView(generic.BulkEditView):
    """Bulk edit view for MACAddress instances."""

    queryset = models.MACAddress.objects.all()
    filterset = filtersets.MACAddressFilterSet
    table = tables.MACAddressTable


class MACAddressBulkDeleteView(generic.BulkDeleteView):
    """Bulk delete view for MACAddress instances."""

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
        Return the django-tables2 Table instance to be used for rendering the objects list without the `vendor` field.

        Args:
            data: Queryset or iterable containing table data
            request: The current request
            bulk_actions: Render checkboxes for object selection
        """
        table = self.table(data, user=request.user)
        if "pk" in table.base_columns and bulk_actions:  # pylint: disable=no-member  # type: ignore
            table.columns.show("pk")

        table.columns.hide("vendor")

        table.configure(request)
        return table

    def get_children(self, request, parent):
        if self.child_model is not None:
            return (
                self.child_model.objects.restrict(request.user, "view")  # type: ignore
                .filter(vendor=parent)
                .prefetch_related("tags")
                .annotate(
                    occurences=Count("known_by"),
                )
            )


class MACVendorBulkEditView(generic.BulkEditView):
    """Bulk edit view for MACVendor instances."""

    queryset = models.MACVendor.objects.all()
    filterset = filtersets.MACVendorFilterSet
    table = tables.MACVendorTable


class MACVendorBulkDeleteView(generic.BulkDeleteView):
    """Bulk delete view for MACVendor instances."""

    queryset = models.MACVendor.objects.all()
    filterset = filtersets.MACVendorFilterSet
    table = tables.MACVendorTable


class MACVendorListView(generic.ObjectListView):
    """List view for MACVendor instances."""

    queryset = models.MACVendor.objects.all()
    table = tables.MACVendorTable
    # filterset = filtersets.MACVendorFilterSet
    # filterset_form = forms.MACVendorFilterForm


class MACVendorEditView(generic.ObjectEditView):
    """Edit view for MACVendor instances."""

    queryset = models.MACVendor.objects.all()
    form = forms.MACVendorForm


class MACVendorDeleteView(generic.ObjectDeleteView):
    """Delete view for MACVendor instances."""

    queryset = models.MACVendor.objects.all()

class CollectorDefinitionView(generic.ObjectView):
    """View for CollectorDefinition instances."""

    queryset = models.CollectorDefinition.objects.all()

    def get_extra_context(self, request, instance):
        # Gather assigned objects for parsing in the template
        assigned_objects = (
            ("Regions", instance.regions.all),
            ("Site Groups", instance.site_groups.all),
            ("Sites", instance.sites.all),
            ("Locations", instance.locations.all),
            ("Devices", instance.devices.all),
            ("Device Types", instance.device_types.all),
            ("Device Status", [dict(DeviceStatusChoices)[status] for status in instance.device_status]),
            ("Roles", instance.roles.all),
            ("Platforms", instance.platforms.all),
            ("Tenant Groups", instance.tenant_groups.all),
            ("Tenants", instance.tenants.all),
            ("Tags", instance.tags.all),
        )

        return {
            "assigned_objects": [(title, values, not isinstance(values, list)) for title, values in assigned_objects],
        }


class CollectorDefinitionListView(generic.ObjectListView):
    """List view for CollectorDefinition instances."""

    queryset = models.CollectorDefinition.objects.all().annotate(
        occurences=Count("jobs"),
    )
    table = tables.CollectorDefinitionTable


class CollectorDefinitionEditView(generic.ObjectEditView):
    """Edit view for CollectorDefinition instances."""

    queryset = models.CollectorDefinition.objects.all()
    form = forms.CollectorDefinitionForm


class CollectorDefinitionDeleteView(generic.ObjectDeleteView):
    """Delete view for CollectorDefinition instances."""

    queryset = models.CollectorDefinition.objects.all()


# class CollectorDefinitionBulkImportView(generic.BulkImportView):
#     """Bulk import view for CollectorDefinition instances."""

#     queryset = models.CollectorDefinition.objects.all()
#     model_form = forms.CollectorDefinitionImportForm


class CollectorDefinitionBulkEditView(generic.BulkEditView):
    """Bulk edit view for CollectorDefinition instances."""

    queryset = models.CollectorDefinition.objects.all()
    filterset = filtersets.CollectorDefinitionFilterSet
    table = tables.CollectorDefinitionTable


class CollectorDefinitionBulkDeleteView(generic.BulkDeleteView):
    """Bulk delete view for CollectorDefinition instances."""

    queryset = models.CollectorDefinition.objects.all()
    filterset = filtersets.CollectorDefinitionFilterSet
    table = tables.CollectorDefinitionTable


# @register_model_view(models.MACVendor)
# class MACVendorView(generic.ObjectView):
#     """View for MACVendor instances."""

#     queryset = models.MACVendor.objects.all()


class CollectionJobDeleteView(generic.ObjectDeleteView):
    """Delete view for CollectionJob instances."""

    queryset = models.CollectionJob.objects.all()

class CollectionJobBulkDeleteView(generic.BulkDeleteView):
    """Bulk delete view for CollectionJob instances."""

    queryset = models.CollectionJob.objects.all()
    table = tables.CollectionJobTable

@register_model_view(models.CollectorDefinition, "instances")
class CollectorDefinitionInstancesView(generic.ObjectChildrenView):
    """View for the instances of a CollectorDefinition instance."""

    queryset = models.CollectorDefinition.objects.all()
    template_name = "netbox_facts/collectordefinition_instances.html"
    child_model = models.CollectionJob
    table = tables.CollectionJobTable
    # filterset = filtersets.MACAddressFilterSet
    tab = ViewTab(
        label=_("Instances"),
        badge=lambda x: x.jobs.all().count(),
        permission="netbox_facts.view_collectionjob",
        weight=500,
    )
    actions = (
        "export",
        "delete",
        "bulk_delete",
    )

    def get_table(self, data, request, bulk_actions=True):
        """
        Return the django-tables2 Table instance to be used for rendering the objects list without the `vendor` field.

        Args:
            data: Queryset or iterable containing table data
            request: The current request
            bulk_actions: Render checkboxes for object selection
        """
        table = self.table(data, user=request.user)
        if "pk" in table.base_columns and bulk_actions:  # pylint: disable=no-member  # type: ignore
            table.columns.show("pk")

        table.columns.hide("job_definition")

        table.configure(request)
        return table

    def get_children(self, request, parent):
        if self.child_model is not None:
            return self.child_model.objects.restrict(request.user, "view").filter(  # type: ignore
                job_definition=parent
            )
