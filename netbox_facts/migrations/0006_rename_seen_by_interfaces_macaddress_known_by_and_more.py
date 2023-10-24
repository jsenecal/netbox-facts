# Generated by Django 4.1.10 on 2023-08-08 19:56

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_facts', '0005_macaddress_description_and_more'),
    ]

    operations = [
        migrations.RenameField(
            model_name='macaddress',
            old_name='seen_by_interfaces',
            new_name='known_by',
        ),
        migrations.AlterField(
            model_name='macaddress',
            name='description',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AlterField(
            model_name='macvendor',
            name='name',
            field=models.CharField(max_length=100),
        ),
    ]