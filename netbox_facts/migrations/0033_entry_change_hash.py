from django.db import migrations, models


class Migration(migrations.Migration):
    # The index spans the entry's device, so it needs the dcim app at the
    # migration that gave Device its current name. Pinned at that migration
    # rather than at whatever the installed NetBox happens to lead with, so
    # the graph does not shift under a NetBox upgrade.
    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("dcim", "0181_rename_device_role_device_role"),
        ("netbox_facts", "0032_optional_collectionplan_napalm_driver"),
    ]

    # Existing rows keep a blank hash on purpose: a hash is the identity of
    # what one run detected, and it cannot be reconstructed for a payload
    # that was recorded without one. A blank hash matches nothing, so a
    # legacy skipped entry suppresses nothing.
    operations = [
        migrations.AddField(
            model_name="factsreportentry",
            name="change_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddIndex(
            model_name="factsreportentry",
            index=models.Index(fields=["device", "entry_kind", "change_hash"], name="netbox_fact_device__37db07_idx"),
        ),
    ]
