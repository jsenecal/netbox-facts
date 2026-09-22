from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_facts", "0028_entry_kind_and_apply_error"),
    ]

    operations = [
        migrations.AddField(
            model_name="collectionplan",
            name="allow_unscoped",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Allow this plan to run without any scoping. A plan with no scope resolves to every device in "
                    "NetBox, so every run dials the whole fleet; enable this only for a deliberate fleet-wide plan."
                ),
                verbose_name="allow unscoped",
            ),
        ),
    ]
