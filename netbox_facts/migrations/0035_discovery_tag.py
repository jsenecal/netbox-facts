from django.db import migrations

# Frozen copy of the tag's identity, deliberately not imported from
# netbox_facts.constants: a data migration describes a one-time
# transformation as it stood when written, so it must not change meaning
# if the constants module is edited later. This mirrors the frozen-copy
# convention 0028_entry_kind_and_apply_error.py uses for the same reason.
AUTO_D_TAG_NAME = "Automatically Discovered"
AUTO_D_TAG_SLUG = "automatically-discovered"
# Kept within extras.Tag.description's 200-character max_length.
AUTO_D_TAG_DESCRIPTION = (
    "Marks objects netbox_facts created automatically. Stale handling and "
    "ownership checks match by slug; renaming the slug or deleting the tag "
    "breaks them. Name, color, and this text are safe to edit."
)


def get_or_create_discovery_tag(apps, schema_editor):
    """Ensure the discovery tag exists at its stable slug.

    Three cases, in order:

    1. A tag already carries this slug -- nothing to do; the tag was
       created by a previous run of this migration, or by hand with the
       same slug.
    2. No tag carries this slug, but one already carries the display
       name -- adopt it in place by setting its slug, rather than
       creating a second tag the UI would show twice. This is the path a
       pre-existing install takes: every tag.add("Automatically
       Discovered") call before this migration existed created the tag by
       name alone, with whatever slug TagBase.slugify() produced for it at
       the time.
    3. Neither exists -- create the tag fresh.
    """
    Tag = apps.get_model("extras", "Tag")

    if Tag.objects.filter(slug=AUTO_D_TAG_SLUG).exists():
        return

    existing = Tag.objects.filter(name=AUTO_D_TAG_NAME).first()
    if existing is not None:
        existing.slug = AUTO_D_TAG_SLUG
        if not existing.description:
            existing.description = AUTO_D_TAG_DESCRIPTION
        existing.save()
        return

    Tag.objects.create(
        name=AUTO_D_TAG_NAME,
        slug=AUTO_D_TAG_SLUG,
        description=AUTO_D_TAG_DESCRIPTION,
    )


class Migration(migrations.Migration):
    # extras is pinned to its squashed floor rather than whatever leaf the
    # installed NetBox happens to lead with, matching the convention
    # 0023_factsreport_factsreportentry_collectionplan_detect_only.py set
    # for this same cross-app dependency: this migration only needs
    # extras.Tag to exist with its name/slug/description fields, which the
    # squashed migration already provides.
    dependencies = [
        ("extras", "0001_squashed"),
        ("netbox_facts", "0034_device_outcomes"),
    ]

    operations = [
        migrations.RunPython(get_or_create_discovery_tag, migrations.RunPython.noop),
    ]
