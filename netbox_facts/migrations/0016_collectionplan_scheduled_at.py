# Generated by Django 4.2.7 on 2023-11-27 20:51

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_facts', '0015_rename_collector_collectionplan_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='collectionplan',
            name='scheduled_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]