from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_facts", "0020_netbox_facts"),
    ]

    operations = [
        migrations.AddField(
            model_name="macaddress",
            name="last_seen",
            field=models.DateTimeField(
                blank=True,
                editable=False,
                null=True,
                verbose_name="Last Seen",
            ),
        ),
    ]
