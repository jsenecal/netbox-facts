"""Module for NetBox Facts models."""

from django.db import models
from .mac import MACAddress, MACVendor
from .abstract import BigIDModel
from .collection import CollectorDefinition, CollectionJob

__all__ = [
    "MACAddress",
    "MACVendor",
    "BigIDModel",
    "CollectorDefinition",
    "CollectionJob",
]