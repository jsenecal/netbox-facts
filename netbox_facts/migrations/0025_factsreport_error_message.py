from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_facts", "0024_netbox_facts"),
    ]

    operations = [
        migrations.AddField(
            model_name="factsreport",
            name="error_message",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Error details when the collection failed.",
            ),
        ),
    ]
