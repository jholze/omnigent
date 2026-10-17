"""Agent-account bearer tokens.

Tokens are ``ogc_`` plus 256 bits of randomness. Only
``HMAC-SHA256(OMNIGENT_CHAT_AGENT_SECRET, token)`` is stored. The raw token
is returned once, at create or rotate. Without the env secret, account
creation is refused and an ``ogc_`` bearer cannot be verified.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import secrets
import socket
from urllib.parse import urlsplit

_TOKEN_PREFIX = "ogc_"
_TOKEN_PREFIX_CHARS = 8
_SECRET_ENV = "OMNIGENT_CHAT_AGENT_SECRET"
_AGENT_PATH_PREFIXES = ("/mcp", "/v1/chat")


def agent_account_secret() -> str | None:
    """Return ``OMNIGENT_CHAT_AGENT_SECRET``, or ``None`` when it is unset."""
    raw = os.environ.get(_SECRET_ENV, "").strip()
    return raw or None


def agent_token_path_allowed(path: str) -> bool:
    """Whether an ``ogc_`` token may authenticate *path*.

    Allowed paths are ``/mcp`` and ``/v1/chat`` plus their subpaths. Every
    other path fails closed, including ``/v1/sessions``.
    """
    return any(path == prefix or path.startswith(prefix + "/") for prefix in _AGENT_PATH_PREFIXES)


def is_agent_token(token: str) -> bool:
    """Whether *token* uses the agent-account prefix."""
    return token.startswith(_TOKEN_PREFIX)


def hash_agent_token(token: str) -> str | None:
    """Return the HMAC-SHA256 hex digest stored for *token*.

    :returns: ``None`` when ``OMNIGENT_CHAT_AGENT_SECRET`` is unset, so an
        ``ogc_`` bearer fails closed instead of matching a weaker digest.
    """
    secret = agent_account_secret()
    if secret is None:
        return None
    digest = hmac.new(secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()


def derive_webhook_secret(account_id: str, secret_version: int) -> str:
    """Derive the webhook signing secret shown once at create or rotate.

    The value is not stored. It is ``HMAC-SHA256`` of
    ``chat-webhook:<account_id>:<secret_version>``.
    """
    secret = agent_account_secret()
    if secret is None:
        raise RuntimeError("OMNIGENT_CHAT_AGENT_SECRET is not set")
    material = f"chat-webhook:{account_id}:{secret_version}".encode()
    return hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()


def mint_agent_token() -> tuple[str, str, str]:
    """Mint a raw token and the values stored beside it.

    :returns: ``(raw_token, token_hash, token_prefix)``.
    :raises RuntimeError: When the HMAC secret is unset.
    """
    if agent_account_secret() is None:
        raise RuntimeError("OMNIGENT_CHAT_AGENT_SECRET is not set")
    raw = _TOKEN_PREFIX + secrets.token_urlsafe(32)
    prefix = raw[len(_TOKEN_PREFIX) : len(_TOKEN_PREFIX) + _TOKEN_PREFIX_CHARS]
    digest = hash_agent_token(raw)
    if digest is None:
        raise RuntimeError("OMNIGENT_CHAT_AGENT_SECRET is not set")
    return raw, digest, prefix


def webhook_url_rejected(url: str) -> bool:
    """Whether *url* fails the agent-account webhook rule.

    The scheme must be ``https``. A host that resolves to a loopback, private,
    link-local, or other non-public address is rejected. A hostname that does
    not resolve is not an internal address yet, so creation allows it.
    """
    if len(url) > 2048 or "\u0000" in url:
        return True
    parsed = urlsplit(url.strip())
    host = parsed.hostname
    if parsed.scheme != "https" or not host:
        return True
    from omnigent.util.ssrf import host_is_internal

    try:
        ipaddress.ip_address(host)
    except ValueError:
        literal = False
    else:
        literal = True
    if literal:
        return host_is_internal(host)
    try:
        socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    return host_is_internal(host)


def consume_mcp_request(principal_id: str, *, now: float | None = None) -> int | None:
    """Consume one combined agent-account request. See :mod:`chat_rate_limit`."""
    from omnigent.server.chat_rate_limit import consume_agent_request

    return consume_agent_request(principal_id, now=now)


def consume_chat_post(principal_id: str, *, now: float | None = None) -> int | None:
    """Consume one post for *principal_id* on the shared post window.

    Prefer :func:`omnigent.server.chat_rate_limit.consume_channel_post`, which
    keys the window by channel. This helper remains for callers that do not
    have a channel id.
    """
    from omnigent.server.chat_rate_limit import consume_channel_post

    return consume_channel_post(principal_id, "*", now=now)


def reset_agent_rate_limits() -> None:
    """Forget recorded hits. Test-only."""
    from omnigent.server.chat_rate_limit import reset_chat_rate_limits

    reset_chat_rate_limits()
