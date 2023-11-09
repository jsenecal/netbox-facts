"""Exceptions for the netbox_facts module."""


class CollectionError(Exception):
    """Base exception for all collection errors."""


class CollectionTimeout(CollectionError):
    """Raised when the collection times out."""


class CollectionFailed(CollectionError):
    """Raised when the collection fails."""


class OperationNotSupported(CollectionError):
    """Raised when the collection operation is not supported."""
