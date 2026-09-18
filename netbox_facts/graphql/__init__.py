"""GraphQL support for the netbox_facts plugin."""

from .schema import FactsQuery

# NetBox loads a plugin's GraphQL schema from the `schema` attribute of this
# package, so the assignment must follow the import above to take precedence
# over the schema submodule of the same name.
schema = [FactsQuery]

__all__ = ("FactsQuery", "schema")
