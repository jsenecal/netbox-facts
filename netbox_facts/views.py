"""Views for the netbox_facts plugin."""

import json

from core.models.jobs import Job
from dcim.choices import DeviceStatusChoices
from dcim.filtersets import InterfaceFilterSet
from dcim.models import Interface
from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, OuterRef, Q, Subquery
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext as _
from extras.choices import LogLevelChoices
from extras.utils import filename_from_model
from extras.views import ScriptResultView
from ipam.filtersets import IPAddressFilterSet
from ipam.models import IPAddress
from ipam.tables.ip import IPAddressTable
from netbox import object_actions
from netbox.views import generic
from netbox.views.generic.base import BaseObjectView
from utilities.htmx import htmx_partial
from utilities.views import (
    ViewTab,
    register_model_view,
)

from . import filtersets, forms, models, tables
from .choices import EntryActionChoices, EntryStatusChoices
from .helpers.entry_display import build_apply_error_display, build_entry_diff
from .models.collection_plan import SCOPE_DIMENSIONS


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

    def get_children(self, request, parent):
        if self.child_model is not None:
            return (
                self.child_model.objects.restrict(request.user, "view")
                .filter(mac_addresses=parent)
                .prefetch_related("tags")
            )


def _annotate_interface_last_seen(queryset, mac_address):
    """Annotate an Interface queryset with the MAC-to-interface link timestamp.

    MACAddressInterfaceRelation rows are inserted once per (mac_address,
    interface) pair and never touched again on rediscovery, so this
    timestamp reflects when the pairing was first observed rather than a
    continuously refreshed heartbeat -- it is still the closest available
    signal for "when was this MAC last seen on this interface".
    """
    last_seen = models.MACAddressInterfaceRelation.objects.filter(
        mac_address=mac_address,
        interface=OuterRef("pk"),
    ).order_by("-last_updated")
    return queryset.annotate(last_seen=Subquery(last_seen.values("last_updated")[:1]))


@register_model_view(models.MACAddress, "interfaces")
class MACInterfacesView(generic.ObjectChildrenView):
    """View for MACAddress instances, Interfaces."""

    queryset = models.MACAddress.objects.all()
    template_name = "generic/object_children.html"
    child_model = Interface
    table = tables.MACInterfaceTable
    filterset = InterfaceFilterSet
    tab = ViewTab(
        label=_("Interfaces"),
        badge=lambda x: x.interfaces.all().count(),
        permission="dcim.view_interface",
        weight=490,
    )

    def get_children(self, request, parent):
        if self.child_model is not None:
            # NetBox's own dcim.MACAddress model claims the "mac_addresses" reverse
            # accessor on Interface (its GenericRelation to native MAC objects), so
            # filtering on that name here would resolve to the wrong model entirely.
            # Cross the plugin's own through model instead.
            queryset = self.child_model.objects.restrict(request.user, "view").filter(
                macaddressinterfacerelation__mac_address=parent
            )
            return _annotate_interface_last_seen(queryset, parent).prefetch_related("tags", "device")


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


@register_model_view(models.MACAddress, "bulk_import", path="import", detail=False)
class MACAddressBulkImportView(generic.BulkImportView):
    queryset = models.MACAddress.objects.all()
    model_form = forms.MACAddressImportForm


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
        table = self.table(data)
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


@register_model_view(models.MACVendor, "bulk_import", path="import", detail=False)
class MACVendorBulkImportView(generic.BulkImportView):
    queryset = models.MACVendor.objects.all()
    model_form = forms.MACVendorImportForm


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

    queryset = models.MACVendor.objects.all().annotate(
        instance_count=Count("instances"),
    )
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

    #: How many objects of one scoping dimension are listed before truncating.
    scope_list_limit = 10
    #: How many unreachable devices are named in the readiness tooltip.
    readiness_sample_limit = 5

    def get_extra_context(self, request, instance):
        return {
            "assigned_objects": self.get_assigned_objects(instance),
            "scope": self.get_scope_preview(instance),
        }

    def get_assigned_objects(self, instance):
        """Return the per-dimension assignments, truncated to a readable size.

        A plan may pin thousands of devices, so each dimension is capped and
        the overflow reported as a count rather than rendered row by row.
        Rows are derived from SCOPE_DIMENSIONS so a dimension added there
        automatically gains a row here.
        """
        assigned_objects = [
            (dimension.label, getattr(instance, dimension.field).all()) for dimension in SCOPE_DIMENSIONS
        ]

        rows = []
        for title, queryset in assigned_objects:
            values = list(queryset[: self.scope_list_limit + 1])
            remainder = queryset.count() - self.scope_list_limit if len(values) > self.scope_list_limit else 0
            rows.append(
                {
                    "title": title,
                    "values": values[: self.scope_list_limit],
                    "remainder": remainder,
                    "linkify": True,
                }
            )
        rows.append(
            {
                "title": "Device Status",
                "values": [dict(DeviceStatusChoices)[status] for status in instance.device_status],
                "remainder": 0,
                "linkify": False,
            }
        )
        return rows

    def get_scope_preview(self, instance):
        """Return the resolved device count and connection readiness of the plan."""
        unready = instance.get_unready_devices()
        unready_count = unready.count()
        sample = [str(device) for device in unready[: self.readiness_sample_limit]]
        if unready_count > len(sample):
            sample.append(_("and {count} more").format(count=unready_count - len(sample)))

        return {
            "matched_count": instance.get_matched_device_count(),
            "devices_url": instance.get_devices_list_url(),
            "unready_count": unready_count,
            "unready_names": ", ".join(sample),
            "warning": instance.get_scope_warning(),
            "unscoped": not instance.has_scope(),
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
        from .exceptions import OperationNotSupported

        plan: models.CollectionPlan = get_object_or_404(self.queryset, pk=pk)
        try:
            job = plan.enqueue_collection_job(request)
        except OperationNotSupported as exc:
            messages.warning(request, str(exc))
            return redirect(plan.get_absolute_url())

        messages.success(request, f"Queued job #{job.pk} to sync {plan}")
        return redirect("plugins:netbox_facts:collectionplan_results", pk=plan.pk)


@register_model_view(models.CollectionPlan, "results")
class CollectorResultsView(ScriptResultView):
    tab = ViewTab(
        label=_("Results"),
        permission="netbox_facts.view_collector_results",
        badge=lambda x: x.result.get_status_display() if x.result is not None else False,
        hide_if_empty=True,
        weight=5000,
    )

    queryset = models.CollectionPlan.objects.all()

    def get_required_permission(self):
        return "netbox_facts.view_collector_results"

    def get(self, request, **kwargs):
        table = None
        instance = self.get_object(**kwargs)

        object_type = ContentType.objects.get_for_model(instance, for_concrete_model=False)
        job: Job | None = (
            Job.objects.filter(object_id=instance.pk, object_type=object_type).order_by("-started").first()
        )
        if job is None:
            raise Http404(f"No job found for {instance}")

        if job.completed:
            table = self.get_table(job, request, bulk_actions=False)

        log_threshold = request.GET.get("log_threshold", LogLevelChoices.LOG_INFO)
        context = {
            "object": instance,
            "tab": self.tab,
            "collection_plan": job.object,
            "job": job,
            "table": table,
            "log_levels": dict(LogLevelChoices),
            "log_threshold": log_threshold,
        }

        if job.data and "log" in job.data:
            context["tests"] = job.data.get("tests", {})
        elif job.data:
            context["tests"] = {name: data for name, data in job.data.items() if name.startswith("test_")}

        # If this is an HTMX request, return only the result HTML
        if htmx_partial(request):
            if request.GET.get("log"):
                return render(request, "htmx/table.html", context)
            response = render(request, "extras/htmx/script_result.html", context)
            if job.completed or not job.started:
                response.status_code = 286
            return response

        return render(request, "netbox_facts/collector_result.html", context)


@register_model_view(models.CollectionPlan, "bulk_import", path="import", detail=False)
class CollectionPlanBulkImportView(generic.BulkImportView):
    queryset = models.CollectionPlan.objects.all()
    model_form = forms.CollectionPlanImportForm


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
    actions = (object_actions.BulkExport,)


@register_model_view(models.FactsReport)
class FactsReportView(generic.ObjectView):
    """Detail view for FactsReport instances."""

    queryset = models.FactsReport.objects.all()
    actions = (object_actions.DeleteObject,)

    def get_extra_context(self, request, instance):
        entries = instance.entries.all()
        pending_count = instance.pending_entries.count()
        applied_count = entries.for_status(EntryStatusChoices.STATUS_APPLIED).count()
        skipped_count = entries.for_status(EntryStatusChoices.STATUS_SKIPPED).count()
        failed_count = entries.for_status(EntryStatusChoices.STATUS_FAILED).count()

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


class EntryBulkExport(object_actions.BulkExport):
    """Export action for the entry tabs, bound to the entries themselves.

    The button is rendered from the report's detail template, which hands
    every action the report it is showing. Resolving the export against the
    entry model instead keeps the offered export templates and data format
    those of the rows actually being exported.
    """

    @classmethod
    def get_context(cls, context, obj):
        return super().get_context(context, models.FactsReportEntry)


class _EntryExportView(generic.ObjectListView):
    """NetBox's list-export machinery, pointed at one report's entries.

    Child views render tables but carry no export handling; all of it lives
    on ObjectListView.get() -- the current-view column set, export
    templates, the requesting user's CSV delimiter preference, the
    STREAMING_EXPORTS response and the table prefetching that goes with it.
    The entry tabs hand their `export` requests to an instance of this view,
    with its queryset replaced by the tab's entries, rather than restating
    any of that. Its own dispatch never runs: the tab has already checked
    the permissions and resolved the report.
    """

    queryset = models.FactsReportEntry.objects.all()
    table = tables.FactsReportEntryTable
    filterset = filtersets.FactsReportEntryFilterSet
    actions = (EntryBulkExport,)

    def export_table(self, table, columns=None, filename=None, delimiter=None):
        """Name the attachment the way NetBox names export-template output.

        The inherited default is `netbox_{verbose_name_plural}`, which for
        this model yields a title-cased name with spaces in it.
        """
        filename = filename or f"{filename_from_model(self.queryset.model)}.csv"
        return super().export_table(table, columns, filename, delimiter)


def _status_entries_view(status_value, status_label, weight):
    """Factory for per-status entry tab views."""

    @register_model_view(models.FactsReport, f"entries_{status_value}")
    class _View(generic.ObjectChildrenView):
        queryset = models.FactsReport.objects.all()
        child_model = models.FactsReportEntry
        table = tables.FactsReportEntryTable
        filterset = filtersets.FactsReportEntryFilterSet
        filterset_form = forms.FactsReportEntryFilterForm
        actions = (EntryBulkExport,)
        template_name = "netbox_facts/factsreport_entries.html"
        tab = ViewTab(
            label=_(status_label),
            badge=lambda x, s=status_value: x.entries.for_status(s).count(),
            permission="netbox_facts.view_factsreport",
            weight=weight,
        )

        def get_children(self, request, parent):
            return parent.entries.for_status(status_value)

        def get_extra_context(self, request, instance):
            # The tab's status drives which lifecycle controls the template
            # renders, and rides along in the POST so a select-all can be
            # resolved back to this tab's entries server side.
            return {"entry_status": status_value}

        def get(self, request, *args, **kwargs):
            """Answer the Export button's links, else render the tab.

            The export itself is the list view's, run against this tab's
            entries; the filterset is applied there, as it is for any list.
            """
            if "export" in request.GET and EntryBulkExport in self.get_permitted_actions(
                request.user, model=self.child_model
            ):
                export_view = _EntryExportView()
                export_view.setup(request)
                export_view.queryset = self.get_children(request, self.get_object(**kwargs))
                return export_view.get(request)
            return super().get(request, *args, **kwargs)

    _View.__name__ = f"FactsReport{status_label}EntriesView"
    _View.__qualname__ = _View.__name__
    return _View


_status_entries_view(EntryStatusChoices.STATUS_PENDING, "Pending", 510)
_status_entries_view(EntryStatusChoices.STATUS_APPLIED, "Applied", 520)
_status_entries_view(EntryStatusChoices.STATUS_SKIPPED, "Skipped", 530)
_status_entries_view(EntryStatusChoices.STATUS_FAILED, "Failed", 540)


class FactsReportEntryActionView(BaseObjectView):
    """Shared plumbing for the POST-only entry lifecycle views.

    Every lifecycle action selects entries the same three ways, in
    precedence order: a single per-row button, every entry matching the
    tab a select-all was ticked on, or the ticked checkboxes. Subclasses
    only implement the transition itself.
    """

    queryset = models.FactsReport.objects.all()

    def get_required_permission(self):
        return "netbox_facts.apply_factsreport"

    def get(self, request, pk):
        return redirect("plugins:netbox_facts:factsreport", pk=pk)

    def post(self, request, pk):
        report = get_object_or_404(self.queryset, pk=pk)
        entry_pks = self.get_entry_pks(request, report)

        if not entry_pks:
            messages.warning(request, _("No entries selected."))
            return redirect("plugins:netbox_facts:factsreport", pk=pk)

        self.perform(request, report, entry_pks)
        return redirect("plugins:netbox_facts:factsreport", pk=pk)

    def get_entry_pks(self, request, report):
        """Resolve the entries this POST targets."""
        row_pk = request.POST.get("row_pk")
        if row_pk:
            # A per-row button submits the whole bulk form, so its own PK
            # travels under a name the checkboxes do not use; acting on it
            # alone is what the reviewer clicked.
            return [row_pk]
        if request.POST.get("_all"):
            return self.resolve_all_entry_pks(request, report)
        return request.POST.getlist("pk")

    def resolve_all_entry_pks(self, request, report):
        """Resolve a cross-page "select all matching" selection server side.

        Only the flag, the tab's status, and the tab's filters cross the
        wire; the entries themselves are re-derived here, scoped to this
        report, so the selection can never reach another report's entries
        and never grows with the size of the page.
        """
        entries = report.entries.all()

        entry_status = request.POST.get("entry_status")
        if entry_status in EntryStatusChoices.values():
            entries = entries.for_status(entry_status)

        entries = filtersets.FactsReportEntryFilterSet(request.GET, entries, request=request).qs
        return list(entries.values_list("pk", flat=True))

    def perform(self, request, report, entry_pks):
        """Run the transition on the resolved entries and report the outcome."""
        raise NotImplementedError

    def message_apply_result(self, request, applied, failed):
        """Report the outcome of a path that applies entries."""
        if applied:
            messages.success(request, _("Applied {count} entries.").format(count=applied))
        if failed:
            messages.warning(request, _("{count} entries failed to apply.").format(count=failed))


@register_model_view(models.FactsReport, "apply")
class FactsReportApplyView(FactsReportEntryActionView):
    """POST-only view to apply selected entries, or every pending entry in the background."""

    template_name = "netbox_facts/factsreport_apply_confirm.html"

    def post(self, request, pk):
        if request.POST.get("apply_all"):
            report = get_object_or_404(self.queryset, pk=pk)
            return self.apply_all(request, report)

        return super().post(request, pk)

    def perform(self, request, report, entry_pks):
        from .helpers.applier import apply_entries

        self.message_apply_result(request, *apply_entries(report, entry_pks))

    def apply_all(self, request, report):
        """Confirm, then hand every pending entry of the report to a background job."""
        from django.utils.html import format_html
        from utilities.forms import ConfirmationForm
        from utilities.request import copy_safe_request

        from .jobs import ApplyEntriesJobRunner

        pending_count = report.pending_entries.count()
        if not pending_count:
            messages.warning(request, _("No pending entries to apply."))
            return redirect("plugins:netbox_facts:factsreport", pk=report.pk)

        # The entry set is resolved here rather than posted by the browser, so the
        # confirmation round trip carries only the flag and the CSRF token.
        if not ConfirmationForm(request.POST).is_valid():
            return render(
                request,
                self.template_name,
                {
                    "object": report,
                    "pending_count": pending_count,
                    "form": ConfirmationForm(),
                    "return_url": report.get_absolute_url(),
                },
            )

        if ApplyEntriesJobRunner.get_active_jobs(report).exists():
            messages.warning(request, _("An apply job is already queued or running for this report."))
            return redirect("plugins:netbox_facts:factsreport", pk=report.pk)

        job = ApplyEntriesJobRunner.enqueue(
            instance=report,
            user=request.user,
            request=copy_safe_request(request),
        )
        messages.success(
            request,
            format_html(
                _('Queued job <a href="{url}">#{job_id}</a> to apply {count} pending entries.'),
                url=job.get_absolute_url(),
                job_id=job.pk,
                count=pending_count,
            ),
        )

        return redirect("plugins:netbox_facts:factsreport", pk=report.pk)


@register_model_view(models.FactsReport, "skip")
class FactsReportSkipView(FactsReportEntryActionView):
    """POST-only view to skip selected entries."""

    def perform(self, request, report, entry_pks):
        from .helpers.applier import skip_entries

        count = skip_entries(report, entry_pks)
        messages.success(request, _("Skipped {count} entries.").format(count=count))


@register_model_view(models.FactsReport, "retry")
class FactsReportRetryView(FactsReportEntryActionView):
    """POST-only view to retry selected failed entries."""

    def perform(self, request, report, entry_pks):
        from .helpers.applier import retry_entries

        self.message_apply_result(request, *retry_entries(report, entry_pks))


@register_model_view(models.FactsReport, "unskip")
class FactsReportUnskipView(FactsReportEntryActionView):
    """POST-only view to return selected skipped entries to pending."""

    def perform(self, request, report, entry_pks):
        from .helpers.applier import unskip_entries

        count = unskip_entries(report, entry_pks)
        messages.success(request, _("Returned {count} entries to pending.").format(count=count))


###
# FactsReportEntry
###


def _entry_tab_url(entry):
    """Return the report tab the entry is listed on, or the report itself.

    Entries are reached through a per-status tab on their report, so that
    tab is where a reviewer came from and where a back link belongs. A
    status with no tab of its own (an entry mid-apply) falls back to the
    report.
    """
    try:
        return reverse(f"plugins:netbox_facts:factsreport_entries_{entry.status}", args=[entry.report_id])
    except NoReverseMatch:
        return entry.report.get_absolute_url()


def _pretty_json(payload):
    """Render a captured payload as the raw evidence block shows it."""
    return json.dumps(payload or {}, indent=2, sort_keys=True, default=str)


@register_model_view(models.FactsReportEntry)
class FactsReportEntryView(generic.ObjectView):
    """Detail view for a single report entry.

    Shows the comparison key by key, the payloads it was derived from,
    and -- for a failed entry -- what NetBox rejected on apply.
    """

    queryset = models.FactsReportEntry.objects.all()
    template_name = "netbox_facts/factsreportentry.html"
    actions = ()

    def get_required_permission(self):
        return "netbox_facts.view_factsreport"

    def has_permission(self):
        """Gate an entry on its report rather than on the entry itself.

        Entries hold no permissions of their own: they are rows of a
        report and are visible exactly when it is. Restricting the report
        queryset rather than the entry one keeps object-level constraints
        granted on reports (a tenant's plans, say) in force here, which
        the default model-level check on FactsReportEntry would miss.
        """
        user = self.request.user
        if not user.has_perms((self.get_required_permission(), *self.additional_permissions)):
            return False

        self.queryset = self.queryset.filter(report__in=models.FactsReport.objects.restrict(user, "view"))
        return True

    def get_extra_context(self, request, instance):
        apply_error = None
        if instance.status == EntryStatusChoices.STATUS_FAILED:
            apply_error = build_apply_error_display(instance.apply_error)

        return {
            "diff_rows": build_entry_diff(instance),
            "apply_error": apply_error,
            "detected_json": _pretty_json(instance.detected_values),
            "current_json": _pretty_json(instance.current_values),
            "parent_tab_url": _entry_tab_url(instance),
        }


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
