"""Regression test for the omnigent.llms <-> omnigent.util.reasoning_effort cycle.

The eager top-level imports in ``omnigent/llms/__init__.py`` created
a circular load when any caller imported ``omnigent.llms.errors``
during the load of ``omnigent.util.reasoning_effort`` (which happens on
every server-routes import via ``server/routes/sessions.py``).

The fix in ``omnigent/llms/__init__.py`` switches to a
``__getattr__`` shim so ``Client`` and ``get_model_context_window``
are resolved lazily on first access. This test guards against
re-introducing the cycle by re-importing the affected modules
in a fresh interpreter-style namespace and asserting both the
short-form and long-form import paths work.
"""

from __future__ import annotations

import contextlib
import importlib
import sys
from collections.abc import Iterator
from typing import Any


def _purge(prefix: str) -> None:
    """Drop any already-loaded modules under ``prefix`` so a fresh
    ``import`` exercises the module-load order again."""
    for mod_name in list(sys.modules):
        if mod_name == prefix or mod_name.startswith(prefix + "."):
            sys.modules.pop(mod_name, None)


def _matches(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes)


def _parent_attrs(names: list[str]) -> list[tuple[Any, str, bool, Any]]:
    """Parent-package attributes for *names*.

    A reimport writes the new module onto its parent as well as
    ``sys.modules``. Restoring only ``sys.modules`` leaves
    ``from parent import child`` bound to that temporary copy.
    """
    snapshot: list[tuple[Any, str, bool, Any]] = []
    for name in names:
        parent_name, _, attr = name.rpartition(".")
        if not parent_name:
            continue
        parent = sys.modules.get(parent_name)
        if parent is None:
            continue
        had = attr in parent.__dict__
        snapshot.append((parent, attr, had, parent.__dict__.get(attr)))
    return snapshot


def _restore_parent_attrs(snapshot: list[tuple[Any, str, bool, Any]]) -> None:
    for parent, attr, had, old in snapshot:
        if had:
            setattr(parent, attr, old)
        else:
            parent.__dict__.pop(attr, None)


@contextlib.contextmanager
def _isolated_import(*prefixes: str) -> Iterator[None]:
    """Reimport *prefixes*, then restore the modules this worker already held.

    A purged ``omnigent.llms`` builds new classes while ``llm_retry`` and the
    usage recorder still hold the old ones, so later tests on this xdist
    worker fail ``isinstance`` or look up a bucket that was never written.
    """
    saved = {
        name: module for name, module in list(sys.modules.items()) if _matches(name, prefixes)
    }
    parent_attrs = _parent_attrs(list(saved))
    try:
        for prefix in prefixes:
            _purge(prefix)
        yield
    finally:
        for name in list(sys.modules):
            if _matches(name, prefixes):
                sys.modules.pop(name, None)
        sys.modules.update(saved)
        _restore_parent_attrs(parent_attrs)


def test_sessions_routes_import_does_not_trigger_cycle() -> None:
    """The original failure shape: importing the server routes module
    triggered ``reasoning_effort`` -> ``llms.errors`` -> ``llms.__init__``
    -> ``llms.client`` -> ``reasoning_effort`` re-entry."""
    import omnigent.server.routes.sessions as before

    with _isolated_import(
        "omnigent.llms",
        "omnigent.util.reasoning_effort",
        "omnigent.server.routes.sessions",
    ):
        importlib.import_module("omnigent.server.routes.sessions")
    import omnigent.server.routes.sessions as via_sysmodules
    from omnigent.server.routes import sessions as via_package_attr

    assert via_sysmodules is before
    assert via_package_attr is before


def test_short_form_import_still_works() -> None:
    """``from omnigent.llms import Client`` must keep working
    after the lazy-attribute switch."""
    with _isolated_import("omnigent.llms"):
        from omnigent.llms import Client, get_model_context_window

        assert Client is not None
        assert callable(get_model_context_window)


def test_module_only_import_does_not_load_client() -> None:
    """Importing ``omnigent.llms`` by itself should NOT eagerly pull
    in ``client.py`` -- that's the whole point of the lazy shim."""
    with _isolated_import("omnigent.llms"):
        importlib.import_module("omnigent.llms")
        assert "omnigent.llms.client" not in sys.modules, (
            "omnigent.llms.client was imported eagerly; lazy shim regressed"
        )


def test_unknown_attribute_raises_attribute_error() -> None:
    """The ``__getattr__`` shim should preserve normal AttributeError
    semantics for unknown names."""
    with _isolated_import("omnigent.llms"):
        import omnigent.llms as llms_pkg

        try:
            llms_pkg.does_not_exist  # noqa: B018
        except AttributeError as e:
            assert "does_not_exist" in str(e)
        else:
            raise AssertionError("expected AttributeError")
