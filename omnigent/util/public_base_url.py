"""Resolve the external origin used for user-facing Omnigent links.

A server reached through a reverse proxy or tunnel is still addressed
internally on its loopback bind (``http://127.0.0.1:6767``). Links shown to a
person (CLI ``Web UI:`` lines, terminal status bars, notifications) should use
the externally reachable origin instead. Internal runner/server traffic never
goes through this module.
"""

from __future__ import annotations

import logging
import os
import urllib.parse

_log = logging.getLogger(__name__)

PUBLIC_BASE_URL_ENV_VAR = "OMNIGENT_PUBLIC_BASE_URL"
PUBLIC_BASE_URL_CONFIG_KEY = "public_base_url"

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_warned_invalid: set[str] = set()


def normalize_public_base_url(raw: object) -> str | None:
    """Validate and normalize a configured public base URL.

    :param raw: Configured value, e.g. ``"https://omnigent.example.com/"``.
    :returns: The URL without a trailing slash, e.g.
        ``"https://omnigent.example.com"``, or ``None`` when unset/blank.
    :raises ValueError: If the value is not an ``http``/``https`` URL with a
        host, embeds credentials, or carries a query/fragment.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(f"{PUBLIC_BASE_URL_CONFIG_KEY} must be a string")
    stripped = raw.strip().rstrip("/")
    if not stripped:
        return None
    parsed = urllib.parse.urlsplit(stripped)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"{PUBLIC_BASE_URL_CONFIG_KEY} must be an http(s) URL; got {stripped!r}")
    if parsed.username or parsed.password:
        raise ValueError(f"{PUBLIC_BASE_URL_CONFIG_KEY} must not embed credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{PUBLIC_BASE_URL_CONFIG_KEY} must not carry a query or fragment")
    return stripped


def configured_public_base_url() -> str | None:
    """Return the configured public base URL, or ``None`` when unset.

    ``OMNIGENT_PUBLIC_BASE_URL`` wins over the ``public_base_url`` key in the
    effective (global + project) config. An invalid value is logged once and
    ignored so link building never fails.

    :returns: Normalized URL, e.g. ``"https://omnigent.example.com"``, or ``None``.
    """
    raw: object = os.environ.get(PUBLIC_BASE_URL_ENV_VAR)
    if raw is None or not str(raw).strip():
        import yaml

        from omnigent.config import load_effective_config

        try:
            raw = load_effective_config().get(PUBLIC_BASE_URL_CONFIG_KEY)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            _log.debug("public_base_url: could not read config: %s", exc)
            return None
    try:
        return normalize_public_base_url(raw)
    except ValueError as exc:
        message = str(exc)
        if message not in _warned_invalid:
            _warned_invalid.add(message)
            _log.warning("Ignoring invalid public base URL: %s", message)
        return None


def public_link_base(local_base_url: str) -> str | None:
    """Return the public origin to use instead of a loopback server base.

    Only a loopback base is replaced: a remote ``--server`` is already the
    address the user reaches, and rewriting it would point at another server.

    :param local_base_url: Server base the caller would link to, e.g.
        ``"http://127.0.0.1:6767"``.
    :returns: The configured public base URL, or ``None`` to keep the
        caller's existing link unchanged.
    """
    host = urllib.parse.urlsplit(local_base_url.strip()).hostname
    if host not in _LOOPBACK_HOSTS:
        return None
    return configured_public_base_url()
