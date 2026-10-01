import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    # The dcim dependency is pinned to the migration that gave Device its
    # current role field rather than to whatever the installed NetBox
    # happens to lead with: a cross-app dependency on a moving leaf makes
    # the plan unreproducible across NetBox releases, and this table only
    # needs dcim.Device to exist. 0181 is the same floor the entry table's
    # device FK is pinned to.
    dependencies = [
        ("dcim", "0181_rename_device_role_device_role"),
        ("netbox_facts", "0032_optional_collectionplan_napalm_driver"),
    ]

    operations = [
        migrations.CreateModel(
            name="FactsReportDeviceOutcome",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("outcome", models.CharField(max_length=50)),
                ("duration", models.FloatField(blank=True, null=True)),
                ("entry_count", models.PositiveIntegerField(default=0)),
                ("message", models.CharField(blank=True, default="", max_length=500)),
                (
                    "device",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="dcim.device",
                    ),
                ),
                (
                    "report",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="device_outcomes",
                        to="netbox_facts.factsreport",
                    ),
                ),
            ],
            options={
                "verbose_name": "Facts Report Device Outcome",
                "verbose_name_plural": "Facts Report Device Outcomes",
                "ordering": ["pk"],
                "indexes": [
                    models.Index(fields=["report", "outcome"], name="netbox_fact_report__69e374_idx"),
                ],
            },
        ),
    ]
