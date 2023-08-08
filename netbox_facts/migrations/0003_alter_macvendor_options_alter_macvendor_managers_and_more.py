# Generated by Django 4.1.10 on 2023-08-07 18:45

import dcim.fields
from django.db import migrations
import netbox_facts.models


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_facts', '0002_macvendor_comments'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='macvendor',
            options={'ordering': ('name',), 'verbose_name': 'MAC Vendor', 'verbose_name_plural': 'MAC Vendors'},
        ),
        migrations.AlterModelManagers(
            name='macvendor',
            managers=[
                ('objects', netbox_facts.models.MACVendorManager()),
            ],
        ),
        migrations.AlterField(
            model_name='macvendor',
            name='mac_prefix',
            field=dcim.fields.MACAddressField(max_length=8, unique=True),
        ),
    ]
