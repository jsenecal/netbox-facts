"""BigIDModel abstract model."""

from django.db import models
from netbox.models import RestrictedQuerySet


class BigIDModel(models.Model):
    id = models.BigAutoField(primary_key=True)

    objects = RestrictedQuerySet.as_manager()

    class Meta:
        abstract = True
