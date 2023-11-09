# Generated by Django 4.2.6 on 2023-10-20 19:53

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('dcim', '0181_rename_device_role_device_role'),
        ('netbox_facts', '0002_alter_macvendor_options_macvendor_vendor_name'),
    ]

    operations = [
        migrations.AlterField(
            model_name='macvendor',
            name='manufacturer',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='mac_prefixes', to='dcim.manufacturer'),
        ),
    ]
