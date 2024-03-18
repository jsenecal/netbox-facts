"""Helpers module for netbox-facts."""
from .collector import NapalmCollector
from .netbox import get_absolute_url_markdown

__all__ = ("NapalmCollector", "get_absolute_url_markdown")
