"""Tests for the export_templates model feature on FactsReportEntry.

Regression coverage for a wave-2 follow-up: the entry export path renders
ExportTemplates correctly, but FactsReportEntry never registered the
`export_templates` model feature, so the template-creation form's
object-type picker (ObjectType.objects.with_feature('export_templates'))
silently excluded it.
"""

from core.models import ObjectType
from django.test import TestCase
from extras.forms.model_forms import ExportTemplateForm
from netbox.models.features import get_model_features

from netbox_facts.models import FactsReportEntry


class ExportTemplateFeatureTest(TestCase):
    """FactsReportEntry must advertise the export_templates feature."""

    def setUp(self):
        # post_migrate stamps the model's feature list onto its ObjectType row,
        # and the test database is reused across runs, so re-sync it here the
        # same way a migration would.
        object_type = ObjectType.objects.get_for_model(FactsReportEntry)
        object_type.features = get_model_features(FactsReportEntry)
        object_type.save()

    def test_facts_report_entry_supports_export_templates(self):
        """FactsReportEntry advertises the export_templates feature to NetBox."""
        self.assertIn("export_templates", get_model_features(FactsReportEntry))

    def test_object_type_picker_offers_facts_report_entry(self):
        """The ExportTemplate creation form's object-type picker includes the entry type."""
        object_type = ObjectType.objects.get_for_model(FactsReportEntry)

        queryset = ExportTemplateForm().fields["object_types"].queryset

        self.assertIn(object_type, queryset)
