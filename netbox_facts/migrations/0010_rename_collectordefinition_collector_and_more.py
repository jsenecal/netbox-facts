# Generated by Django 4.2.6 on 2023-11-09 17:43

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('extras', '0098_webhook_custom_field_data_webhook_tags'),
        ('dcim', '0181_rename_device_role_device_role'),
        ('tenancy', '0011_contactassignment_tags'),
        ('netbox_facts', '0009_collectionjob_result'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='CollectorDefinition',
            new_name='Collector',
        ),
        migrations.DeleteModel(
            name='CollectionJob',
        ),
    ]
