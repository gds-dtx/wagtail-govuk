"""Database connections in the service, where no request cycle closes them."""

from django.db import connections


def release_connections() -> None:
    """Close stale connections, as Django would at a request's end.

    Connections inside a transaction (tests) are kept.
    """
    for connection in connections.all(initialized_only=True):
        if not connection.in_atomic_block:
            connection.close_if_unusable_or_obsolete()
