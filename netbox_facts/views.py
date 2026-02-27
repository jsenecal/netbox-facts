"""Views for the netbox_facts plugin."""

import logging

from core.choices import JobStatusChoices
from core.models.jobs import Job
from dcim.choices import DeviceStatusChoices
from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from ipam.filtersets import IPAddressFilterSet
from ipam.models import IPAddress
from ipam.tables.ip import IPAddressTable
from netbox.views import generic
from netbox.views.generic.base import BaseObjectView
from extras.views import ScriptResultView
from utilities.htmx import htmx_partial
from utilities.views import (
    ViewTab,
    register_model_view,
)

from . import filtersets, forms, models, tables
from .choices import EntryActionChoices, EntryStatusChoices


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
        table = self.table(data, user=request.user)
        if (
            "pk" in table.base_columns
            and bulk_actions
        ):
            table.columns.show("pk")

        table.configure(request)
        return table

    def get_children(self, request, parent):
        if self.child_model is not None:
            return (
                self.child_model.objects.restrict(request.user, "view")
                .filter(mac_addresses=parent)
                .prefetch_related("tags")
            )


class MACAddressListView(generic.ObjectListView):
    """List view for MACAddress instances."""

    queryset = models.MACAddress.objects.all().annotate(
        occurences=Count("interfaces"),
    )
    table = tables.MACAddressTable
    filterset = filtersets.MACAddressFilterSet
    filterset_form = forms.MACAddressFilterForm


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
    form = forms.MACAddressBulkEditForm


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
        table = self.table(data, user=request.user)
        if "pk" in table.base_columns and bulk_actions:
            table.columns.show("pk")

        table.columns.hide("vendor")

        table.configure(request)
        return table

    def get_children(self, request, parent):
        if self.child_model is not None:
            return (
                self.child_model.objects.restrict(request.user, "view")
                .filter(vendor=parent)
                .prefetch_related("tags")
                .annotate(
                    occurences=Count("interfaces"),
                )
            )


class MACVendorBulkEditView(generic.BulkEditView):
    """Bulk edit view for MACVendor instances."""

    queryset = models.MACVendor.objects.all()
    filterset = filtersets.MACVendorFilterSet
    table = tables.MACVendorTable
    form = forms.MACVendorBulkEditForm


class MACVendorBulkDeleteView(generic.BulkDeleteView):
    """Bulk delete view for MACVendor instances."""

    queryset = models.MACVendor.objects.all()
    filterset = filtersets.MACVendorFilterSet
    table = tables.MACVendorTable


class MACVendorListView(generic.ObjectListView):
    """List view for MACVendor instances."""

    queryset = models.MACVendor.objects.all()
    table = tables.MACVendorTable
    filterset = filtersets.MACVendorFilterSet
    filterset_form = forms.MACVendorFilterForm


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
# CollectionPlan
###


class CollectionPlanListView(generic.ObjectListView):
    """List view for CollectionPlan instances."""

    queryset = models.CollectionPlan.objects.all()
    table = tables.CollectorTable
    filterset = filtersets.CollectorFilterSet
    filterset_form = forms.CollectionPlanFilterForm


@register_model_view(models.CollectionPlan)
class CollectionPlanView(generic.ObjectView):
    """View for CollectionPlan instances."""

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
    """Edit view for CollectionPlan instances."""

    queryset = models.CollectionPlan.objects.all()
    form = forms.CollectorForm


@register_model_view(models.CollectionPlan, "delete")
class CollectorDeleteView(generic.ObjectDeleteView):
    """Delete view for CollectionPlan instances."""

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
class CollectorResultsView(ScriptResultView):

    tab = ViewTab(
        label=_("Results"),
        permission="netbox_facts.view_collector_results",
        badge=lambda x: (
            x.result.get_status_display() if x.result is not None else False
        ),
        hide_if_empty=True,
        weight=5000,
    )

    queryset = models.CollectionPlan.objects.all()

    def get_required_permission(self):
        return "netbox_facts.view_collector_results"

    def get(self, request, **kwargs):
        table = None
        instance = self.get_object(**kwargs)

        object_type = ContentType.objects.get_for_model(
            instance, for_concrete_model=False
        )
        job: Job | None = (
            Job.objects.filter(object_id=instance.pk, object_type=object_type)
            .order_by("-started")
            .first()
        )
        if job is None:
            raise Http404(f"No job found for {instance}")

        if job.completed:
            table = self.get_table(job, request, bulk_actions=False)

        context = {
            "collection_plan": job.object,
            "job": job,
            "table": table,
        }

        if job.data and "log" in job.data:
            context["tests"] = job.data.get("tests", {})
        elif job.data:
            context["tests"] = {
                name: data
                for name, data in job.data.items()
                if name.startswith("test_")
            }

        # If this is an HTMX request, return only the result HTML
        if htmx_partial(request):
            response = render(request, "extras/htmx/script_result.html", context)
            if job.completed or not job.started:
                response.status_code = 286
            return response

        return render(request, "netbox_facts/collector_result.html", context)


class CollectorBulkEditView(generic.BulkEditView):
    """Bulk edit view for CollectionPlan instances."""

    queryset = models.CollectionPlan.objects.all()
    filterset = filtersets.CollectorFilterSet
    table = tables.CollectorTable
    form = forms.CollectionPlanBulkEditForm


class CollectorBulkDeleteView(generic.BulkDeleteView):
    """Bulk delete view for CollectionPlan instances."""

    queryset = models.CollectionPlan.objects.all()
    filterset = filtersets.CollectorFilterSet
    table = tables.CollectorTable


###
# FactsReport
###


class FactsReportListView(generic.ObjectListView):
    """List view for FactsReport instances."""

    queryset = models.FactsReport.objects.annotate(
        entry_count=Count("entries"),
        new_count=Count("entries", filter=Q(entries__action=EntryActionChoices.ACTION_NEW)),
        changed_count=Count("entries", filter=Q(entries__action=EntryActionChoices.ACTION_CHANGED)),
        stale_count=Count("entries", filter=Q(entries__action=EntryActionChoices.ACTION_STALE)),
    )
    table = tables.FactsReportTable
    filterset = filtersets.FactsReportFilterSet
    filterset_form = forms.FactsReportFilterForm


@register_model_view(models.FactsReport)
class FactsReportView(generic.ObjectView):
    """Detail view for FactsReport instances."""

    queryset = models.FactsReport.objects.all()

    def get_extra_context(self, request, instance):
        entries = instance.entries.all()
        pending_count = entries.filter(status=EntryStatusChoices.STATUS_PENDING).count()
        applied_count = entries.filter(status=EntryStatusChoices.STATUS_APPLIED).count()
        skipped_count = entries.filter(status=EntryStatusChoices.STATUS_SKIPPED).count()
        failed_count = entries.filter(status=EntryStatusChoices.STATUS_FAILED).count()

        return {
            "entry_stats": {
                "pending": pending_count,
                "applied": applied_count,
                "skipped": skipped_count,
                "failed": failed_count,
                "total": entries.count(),
            },
        }


@register_model_view(models.FactsReport, "delete")
class FactsReportDeleteView(generic.ObjectDeleteView):
    """Delete view for FactsReport instances."""

    queryset = models.FactsReport.objects.all()


class FactsReportBulkDeleteView(generic.BulkDeleteView):
    """Bulk delete view for FactsReport instances."""

    queryset = models.FactsReport.objects.all()
    filterset = filtersets.FactsReportFilterSet
    table = tables.FactsReportTable


@register_model_view(models.FactsReport, "entries")
class FactsReportEntriesView(generic.ObjectChildrenView):
    """Entries tab for a FactsReport."""

    queryset = models.FactsReport.objects.all()
    child_model = models.FactsReportEntry
    table = tables.FactsReportEntryTable
    filterset = filtersets.FactsReportEntryFilterSet
    template_name = "netbox_facts/factsreport_entries.html"
    tab = ViewTab(
        label=_("Entries"),
        badge=lambda x: x.entries.count(),
        permission="netbox_facts.view_factsreport",
        weight=500,
    )

    def get_children(self, request, parent):
        return parent.entries.all()

    def get_extra_context(self, request, instance):
        has_pending = instance.entries.filter(
            status=EntryStatusChoices.STATUS_PENDING
        ).exists()
        return {"has_pending": has_pending}


@register_model_view(models.FactsReport, "apply")
class FactsReportApplyView(BaseObjectView):
    """POST-only view to apply selected entries."""

    queryset = models.FactsReport.objects.all()

    def get_required_permission(self):
        return "netbox_facts.apply_factsreport"

    def get(self, request, pk):
        return redirect("plugins:netbox_facts:factsreport", pk=pk)

    def post(self, request, pk):
        from .helpers.applier import apply_entries

        report = get_object_or_404(self.queryset, pk=pk)
        entry_pks = request.POST.getlist("pk")

        if not entry_pks:
            messages.warning(request, _("No entries selected."))
            return redirect("plugins:netbox_facts:factsreport_entries", pk=pk)

        applied, failed = apply_entries(report, entry_pks)
        if applied:
            messages.success(request, _("Applied {count} entries.").format(count=applied))
        if failed:
            messages.warning(request, _("{count} entries failed to apply.").format(count=failed))

        return redirect("plugins:netbox_facts:factsreport_entries", pk=pk)


@register_model_view(models.FactsReport, "skip")
class FactsReportSkipView(BaseObjectView):
    """POST-only view to skip selected entries."""

    queryset = models.FactsReport.objects.all()

    def get_required_permission(self):
        return "netbox_facts.apply_factsreport"

    def get(self, request, pk):
        return redirect("plugins:netbox_facts:factsreport", pk=pk)

    def post(self, request, pk):
        from .helpers.applier import skip_entries

        report = get_object_or_404(self.queryset, pk=pk)
        entry_pks = request.POST.getlist("pk")

        if not entry_pks:
            messages.warning(request, _("No entries selected."))
            return redirect("plugins:netbox_facts:factsreport_entries", pk=pk)

        count = skip_entries(report, entry_pks)
        messages.success(request, _("Skipped {count} entries.").format(count=count))

        return redirect("plugins:netbox_facts:factsreport_entries", pk=pk)


@register_model_view(models.CollectionPlan, "reports")
class CollectionPlanReportsView(generic.ObjectChildrenView):
    """Reports tab on CollectionPlan detail."""

    queryset = models.CollectionPlan.objects.all()
    child_model = models.FactsReport
    table = tables.FactsReportTable
    filterset = filtersets.FactsReportFilterSet
    template_name = "generic/object_children.html"
    tab = ViewTab(
        label=_("Reports"),
        badge=lambda x: x.reports.count(),
        permission="netbox_facts.view_factsreport",
        weight=600,
    )

    def get_children(self, request, parent):
        return models.FactsReport.objects.filter(
            collection_plan=parent,
        ).annotate(
            entry_count=Count("entries"),
            new_count=Count("entries", filter=Q(entries__action=EntryActionChoices.ACTION_NEW)),
            changed_count=Count("entries", filter=Q(entries__action=EntryActionChoices.ACTION_CHANGED)),
            stale_count=Count("entries", filter=Q(entries__action=EntryActionChoices.ACTION_STALE)),
        )
