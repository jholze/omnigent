"""Account principals for a signed-in username.

A session sees one cookie subject. When that username has a linked
Google email, grants stored under the email authorize the same person.
The resolver is process-global and is set only for an accounts-mode app.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

_logger = logging.getLogger(__name__)

_resolver: Callable[[str], frozenset[str]] | None = None


def set_principal_resolver(resolver: Callable[[str], frozenset[str]] | None) -> None:
    """Install or clear the lookup that adds a linked email to a username."""
    global _resolver
    _resolver = resolver


def principals(sub: str) -> frozenset[str]:
    """Return ``sub`` plus a linked email, when one is installed.

    The cookie subject is always included. A resolver failure leaves the
    subject alone so a closed database cannot widen or drop access.
    """
    found = {sub}
    resolver = _resolver
    if resolver is None or not sub:
        return frozenset(found)
    try:
        extra = resolver(sub)
    except Exception:
        _logger.warning("principal lookup failed")
        return frozenset(found)
    for item in extra:
        if isinstance(item, str) and item:
            found.add(item)
    return frozenset(found)


def same_account(user_id: str | None, owner: str | None) -> bool:
    """Whether ``owner`` is this signed-in user or their linked email."""
    if user_id is None or owner is None:
        return False
    return owner in principals(user_id)
