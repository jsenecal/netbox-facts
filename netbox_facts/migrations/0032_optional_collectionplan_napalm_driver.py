from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_facts", "0030_alter_factsreportentry_device"),
    ]

    operations = [
        migrations.AlterField(
            model_name="collectionplan",
            name="napalm_driver",
            field=models.CharField(
                blank=True,
                help_text=(
                    "The NAPALM driver to use for every device this plan targets. Leave blank to resolve the "
                    "driver per device from its platform, which lets one plan span several vendors."
                ),
                max_length=50,
                verbose_name="NAPALM driver",
            ),
        ),
    ]
