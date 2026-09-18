from django.db import migrations, models
from django.db.models import Q


def backfill_entry_kind(apps, schema_editor):
    """Resolve a kind for entries recorded before the field existed.

    Rows are matched on the same leading type token the applier used to
    dispatch on, in the same order. Anything unrecognized becomes "other",
    which dispatches exactly as the unprefixed fall-through did, so a label
    that matches nothing can never make the backfill fail.
    """
    from netbox_facts.choices import ENTRY_KIND_REPR_PREFIXES, EntryKindChoices

    entries = apps.get_model("netbox_facts", "FactsReportEntry").objects
    for prefix, kind in ENTRY_KIND_REPR_PREFIXES:
        entries.filter(entry_kind="").filter(Q(object_repr=prefix) | Q(object_repr__startswith=f"{prefix} ")).update(
            entry_kind=kind
        )
    entries.filter(entry_kind="").update(entry_kind=EntryKindChoices.KIND_OTHER)


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_facts", "0027_alter_collectionplan_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="factsreportentry",
            name="apply_error",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="factsreportentry",
            name="entry_kind",
            field=models.CharField(blank=True, default="", max_length=50),
        ),
        migrations.AddIndex(
            model_name="factsreportentry",
            index=models.Index(fields=["report", "entry_kind"], name="netbox_fact_report__eeb488_idx"),
        ),
        migrations.RunPython(backfill_entry_kind, migrations.RunPython.noop),
    ]
