# Generated by Django 4.2.6 on 2023-10-25 02:36

import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_facts', '0003_alter_macvendor_manufacturer'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='collectordefinition',
            name='enabled',
        ),
        migrations.AlterField(
            model_name='collectordefinition',
            name='device_status',
            field=django.contrib.postgres.fields.ArrayField(base_field=models.CharField(default='active', max_length=50), blank=True, size=None),
        ),
    ]
