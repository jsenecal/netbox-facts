# Generated by Django 4.2.6 on 2023-11-02 18:37

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_facts', '0006_alter_collectordefinition_options_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='collectordefinition',
            name='priority',
            field=models.CharField(default='low'),
        ),
    ]
