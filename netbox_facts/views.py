"""Views for the netbox_facts plugin."""
from django.http import Http404
from django.views import View
from core.choices import JobStatusChoices
from core.models.jobs import Job
from dcim.choices import DeviceStatusChoices
from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from ipam.filtersets import IPAddressFilterSet
from ipam.tables.ip import IPAddressTable
from utilities.htmx import is_htmx
from utilities.views import (
    ContentTypePermissionRequiredMixin,
    ViewTab,
    register_model_view,
)
from ipam.models import IPAddress
from django.contrib.contenttypes.models import ContentType

from netbox.views import generic
from netbox.views.generic.base import BaseObjectView

from . import filtersets, forms, models, tables


@register_model_view(models.MACAddress)
class MACAddressView(generic.ObjectView):
    """View for MACAddress instances."""

    queryset = models.MACAddress.objects.all()


@register_model_view(models.MACAddress, "ipaddresses")
class MACIPAddressesView(generic.ObjectChildrenView):
    """View for MACAddress instances, IP Addresses."""

    queryset = models.MACAddress.objects.all()
    template_name = "generic/object_children.html"
    child_model = IPAddress
    table = IPAddressTable
    filterset = IPAddressFilterSet
    tab = ViewTab(
        label=_("IP Addresses"),
        badge=lambda x: x.ip_addresses.all().count(),
        permission="ipam.view_ipaddress",
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
        if (
            "pk" in table.base_columns  # pylint: disable=no-member  # type: ignore
            and bulk_actions
        ):
            table.columns.show("pk")

        table.configure(request)
        return table

    def get_children(self, request, parent):
        if self.child_model is not None:
            return (
                self.child_model.objects.restrict(request.user, "view")  # type: ignore
                .filter(mac_addresses=parent)
                .prefetch_related("tags")
            )


class MACAddressListView(generic.ObjectListView):
    """List view for MACAddress instances."""

    queryset = models.MACAddress.objects.all().annotate(
        occurences=Count("interfaces"),
    )
    table = tables.MACAddressTable


@register_model_view(models.MACAddress, "edit")
class MACAddressEditView(generic.ObjectEditView):
    """Edit view for MACAddress instances."""

    queryset = models.MACAddress.objects.all()
    form = forms.MACAddressForm


@register_model_view(models.MACAddress, "delete")
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
        if (
            "pk" in table.base_columns and bulk_actions
        ):  # pylint: disable=no-member  # type: ignore
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


@register_model_view(models.MACVendor, "edit")
class MACVendorEditView(generic.ObjectEditView):
    """Edit view for MACVendor instances."""

    queryset = models.MACVendor.objects.all()
    form = forms.MACVendorForm


@register_model_view(models.MACVendor, "delete")
class MACVendorDeleteView(generic.ObjectDeleteView):
    """Delete view for MACVendor instances."""

    queryset = models.MACVendor.objects.all()


###
# Collector
###


class CollectionPlanListView(generic.ObjectListView):
    """List view for Collector instances."""

    queryset = models.CollectionPlan.objects.all()
    table = tables.CollectorTable


@register_model_view(models.CollectionPlan)
class CollectionPlanView(generic.ObjectView):
    """View for Collector instances."""

    queryset = models.CollectionPlan.objects.all()

    def get_extra_context(self, request, instance):
        # Gather assigned objects for parsing in the template
        assigned_objects = (
            ("Regions", instance.regions.all),
            ("Site Groups", instance.site_groups.all),
            ("Sites", instance.sites.all),
            ("Locations", instance.locations.all),
            ("Devices", instance.devices.all),
            ("Device Types", instance.device_types.all),
            (
                "Device Status",
                [
                    dict(DeviceStatusChoices)[status]
                    for status in instance.device_status
                ],
            ),
            ("Roles", instance.roles.all),
            ("Platforms", instance.platforms.all),
            ("Tenant Groups", instance.tenant_groups.all),
            ("Tenants", instance.tenants.all),
            ("Tags", instance.tags.all),
        )

        return {
            "assigned_objects": [
                (title, values, not isinstance(values, list))
                for title, values in assigned_objects
            ],
        }


@register_model_view(models.CollectionPlan, "edit")
class CollectorEditView(generic.ObjectEditView):
    """Edit view for Collector instances."""

    queryset = models.CollectionPlan.objects.all()
    form = forms.CollectorForm


@register_model_view(models.CollectionPlan, "delete")
class CollectorDeleteView(generic.ObjectDeleteView):
    """Delete view for Collector instances."""

    queryset = models.CollectionPlan.objects.all()


@register_model_view(models.CollectionPlan, "run")
class CollectorRunView(BaseObjectView):
    queryset = models.CollectionPlan.objects.all()

    def get_required_permission(self):
        return "netbox_facts.run_collector"

    def get(self, request, pk):
        # Redirect GET requests to the object view
        plan: models.CollectionPlan = get_object_or_404(self.queryset, pk=pk)
        return redirect(plan.get_absolute_url())

    def post(self, request, pk):
        plan: models.CollectionPlan = get_object_or_404(self.queryset, pk=pk)
        job = plan.enqueue_collection_job(request)

        messages.success(request, f"Queued job #{job.pk} to sync {plan}")
        return redirect("plugins:netbox_facts:collectionplan_results", pk=plan.pk)


@register_model_view(models.CollectionPlan, "results")
class CollectorResultsView(BaseObjectView):
    tab = ViewTab(
        label=_("Results"),
        permission="netbox_facts.view_collector_results",
        hide_if_empty=True,
        weight=5000,
    )

    queryset = models.CollectionPlan.objects.all()

    def get_required_permission(self):
        return "netbox_facts.view_collector_results"

    def get(self, request, **kwargs):
        """
        GET request handler. `*args` and `**kwargs` are passed to identify the object being queried.
        Args:
            request: The current request
        """
        instance = self.get_object(**kwargs)

        object_type = ContentType.objects.get_for_model(  # type: ignore
            instance, for_concrete_model=False
        )
        job = (
            Job.objects.filter(object_id=instance.pk, object_type=object_type)
            .order_by("-started")
            .first()
        )
        if job is None:
            raise Http404(f"No job found for {instance}")

        # If this is an HTMX request, return only the result HTML
        if is_htmx(request):
            response = render(
                request,
                "netbox_facts/htmx/collector_result.html",
                {
                    "object": instance,
                    "job": job,
                    "tab": self.tab,
                },
            )
            if job.completed or not job.started:
                response.status_code = 286
            return response

        return render(
            request,
            "netbox_facts/collector_result.html",
            {
                "object": instance,
                "job": job,
                "tab": self.tab,
            },
        )


# class ScriptResultView(ContentTypePermissionRequiredMixin, View):
#     def get_required_permission(self):
#         return "extras.view_script"

#     def get(self, request, job_pk):
#         object_type = ContentType.objects.get_by_natural_key(
#             app_label="netbox_facts", model="collectionplan"
#         )
#         job = get_object_or_404(Job.objects.all(), pk=job_pk, object_type=object_type)
#         collection_plan = job.object

#         # If this is an HTMX request, return only the result HTML
#         if is_htmx(request):
#             response = render(
#                 request,
#                 "extras/htmx/script_result.html",
#                 {
#                     "script": script,
#                     "job": job,
#                 },
#             )
#             if job.completed or not job.started:
#                 response.status_code = 286
#             return response

#         return render(
#             request,
#             "extras/script_result.html",
#             {
#                 "script": script,
#                 "job": job,
#             },
#         )


# class CollectorBulkImportView(generic.BulkImportView):
#     """Bulk import view for Collector instances."""

#     queryset = models.Collector.objects.all()
#     model_form = forms.CollectorImportForm


class CollectorBulkEditView(generic.BulkEditView):
    """Bulk edit view for Collector instances."""

    queryset = models.CollectionPlan.objects.all()
    filterset = filtersets.CollectorFilterSet
    table = tables.CollectorTable


class CollectorBulkDeleteView(generic.BulkDeleteView):
    """Bulk delete view for Collector instances."""

    queryset = models.CollectionPlan.objects.all()
    filterset = filtersets.CollectorFilterSet
    table = tables.CollectorTable
