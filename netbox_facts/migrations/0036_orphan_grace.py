import django.db.models.deletion
from django.db import migrations, models

from netbox_facts.constants import ORPHAN_TAG_NAME, ORPHAN_TAG_SLUG

#: The colour the visibility tag is created with. Orange reads as "on its
#: way out" alongside the colours NetBox gives healthy state, and an
#: operator is free to change it: nothing looks the tag up by colour.
ORPHAN_TAG_COLOR = "ff9800"

ORPHAN_TAG_DESCRIPTION = "Not seen by a netbox-facts collection run; removal pending the configured grace period."


def create_orphan_tag(apps, schema_editor):
    """Create the tag the stale grace period marks a missing object with.

    Created here rather than on first use, so that it can be filtered on
    before anything has been orphaned and so that an operator can recolour
    or re-describe it without the next run undoing their edit. Keyed on
    the slug, which is what every lookup in the plugin uses; a tag already
    carrying the slug is left exactly as it was found.
    """
    Tag = apps.get_model("extras", "Tag")
    Tag.objects.get_or_create(
        slug=ORPHAN_TAG_SLUG,
        defaults={
            "name": ORPHAN_TAG_NAME,
            "color": ORPHAN_TAG_COLOR,
            "description": ORPHAN_TAG_DESCRIPTION,
        },
    )


class Migration(migrations.Migration):
    # The cross-app dependencies are pinned down rather than at whatever
    # leaf the installed NetBox leads with: a dependency on a moving leaf
    # makes the plan unreproducible across NetBox releases. 0181 is the
    # same dcim floor the entry and outcome tables pin their device keys
    # to, and this table needs no more of dcim than Device existing; the
    # extras floor is the one the report tables already use, and the tag
    # data migration needs no more of extras than Tag.
    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("dcim", "0181_rename_device_role_device_role"),
        ("extras", "0001_squashed"),
        ("netbox_facts", "0034_device_outcomes"),
    ]

    operations = [
        migrations.AddField(
            model_name="collectionplan",
            name="stale_grace_days",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="OrphanCandidate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("object_id", models.PositiveBigIntegerField()),
                ("first_missing", models.DateTimeField()),
                ("last_missing", models.DateTimeField()),
                (
                    "content_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="contenttypes.contenttype",
                    ),
                ),
                (
                    "device",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="dcim.device",
                    ),
                ),
                (
                    "plan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orphan_candidates",
                        to="netbox_facts.collectionplan",
                    ),
                ),
            ],
            options={
                "verbose_name": "Orphan Candidate",
                "verbose_name_plural": "Orphan Candidates",
                "ordering": ["first_missing", "pk"],
                "indexes": [
                    models.Index(fields=["content_type", "object_id"], name="netbox_fact_content_e738c9_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("plan", "content_type", "object_id"),
                        name="netbox_facts_orphancandidate_unique",
                    ),
                ],
            },
        ),
        # Reversed as a no-op: dropping the tag would take its assignments
        # with it, and an operator rolling the schema back has not asked to
        # lose the record of which objects were marked orphaned.
        migrations.RunPython(create_orphan_tag, migrations.RunPython.noop),
    ]
