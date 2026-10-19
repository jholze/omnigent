"""RFC 8252 native sign-in shared by OIDC and accounts mode.

A native app opens the system browser at ``/auth/login`` with a loopback
(or allowlisted) redirect and a PKCE S256 challenge. After the user signs
in, the browser is sent back with a one-time code. The app exchanges that
code and its verifier at ``POST /auth/native-token``.

OIDC threads the parameters through its IdP state cookie. Accounts mode
stores them in a short-lived ``ap_native`` cookie so the password form and
Continue with Google can both finish the handoff. The code store, the
redirect checks, and the exchange rules are the same either way.
"""

from __future__ import annotations

import hmac
import re
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlencode, urlsplit

import jwt
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from omnigent.server.oidc import derive_code_challenge

# A native sign-in code is exchanged by the app right after the browser
# hands it over, so it only needs to outlive one redirect round trip.
NATIVE_CODE_TTL_SECONDS = 60
# How long the accounts-mode browser may sit on the login page before the
# native parameters must be presented again.
NATIVE_COOKIE_TTL_SECONDS = 600
NATIVE_COOKIE_NAME = "ap_native"
NATIVE_COOKIE_PATH = "/auth"

# RFC 8252 §7.3 loopback literals. ``localhost`` is excluded (§8.3): its
# resolution can be redirected away from this machine.
NATIVE_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})
# RFC 8252 §7.1 private-use-scheme redirects of first-party apps, matched
# exactly. The well-known manifest lists them so an app can tell whether
# this server accepts its redirect before opening the browser.
NATIVE_APP_REDIRECT_URIS: frozenset[str] = frozenset(
    {"ai.omnigent.android:/oauth/callback", "ai.omnigent.ios:/oauth/callback"}
)
NATIVE_PARAMS = ("native_redirect_uri", "native_state", "code_challenge", "code_challenge_method")
# RFC 7636: an S256 challenge is a 43-char base64url SHA-256 digest, and a
# verifier is 43-128 unreserved characters.
_S256_CHALLENGE_RE = re.compile(r"[A-Za-z0-9_-]{43}")
CODE_VERIFIER_RE = re.compile(r"[A-Za-z0-9._~-]{43,128}")
_NATIVE_STATE_RE = re.compile(r"[A-Za-z0-9._~-]{1,256}")

_NativeExchangeError = Literal["invalid_request", "invalid_grant"]


@dataclass
class NativeCode:
    """A one-time code issued to a native app's redirect.

    :param user_id: The authenticated user's id.
    :param code_challenge: The PKCE S256 challenge from ``/auth/login``.
    :param redirect_uri: The redirect the code was delivered to.
    :param created_at: Unix timestamp when the code was issued.
    """

    user_id: str
    code_challenge: str
    redirect_uri: str
    created_at: float


def evict_expired_native_codes(
    codes: dict[str, NativeCode],
    *,
    ttl_seconds: float = NATIVE_CODE_TTL_SECONDS,
) -> None:
    """Remove native sign-in codes past their TTL.

    :param codes: The mutable code dict to prune.
    :param ttl_seconds: Maximum age of a code, in seconds.
    """
    now = time.time()
    expired = [key for key, value in codes.items() if now - value.created_at > ttl_seconds]
    for key in expired:
        del codes[key]


def issue_native_code(
    codes: dict[str, NativeCode],
    *,
    user_id: str,
    code_challenge: str,
    redirect_uri: str,
    ttl_seconds: float = NATIVE_CODE_TTL_SECONDS,
) -> str:
    """Store a single-use code and return it.

    :param codes: The in-memory store shared with ``/auth/native-token``.
    :param user_id: The account the code will sign in.
    :param code_challenge: PKCE S256 challenge the exchange must meet.
    :param redirect_uri: Redirect the code is delivered to.
    :param ttl_seconds: Age after which :func:`evict_expired_native_codes`
        drops the code.
    :returns: The new code (``secrets.token_urlsafe``).
    """
    evict_expired_native_codes(codes, ttl_seconds=ttl_seconds)
    code = secrets.token_urlsafe(32)
    codes[code] = NativeCode(
        user_id=user_id,
        code_challenge=code_challenge,
        redirect_uri=redirect_uri,
        created_at=time.time(),
    )
    return code


def consume_native_code(
    codes: dict[str, NativeCode],
    *,
    code: str,
    verifier: object,
    redirect_uri: object,
    ttl_seconds: float = NATIVE_CODE_TTL_SECONDS,
) -> tuple[NativeCode | None, _NativeExchangeError | None]:
    """Pop ``code`` and check the PKCE exchange.

    The code is consumed before the verifier and redirect are checked, so
    a malformed retry cannot redeem it.

    :param codes: The in-memory store.
    :param code: The code from the form. Callers reject an empty code
        before calling, so this function always pops.
    :param verifier: Form ``code_verifier``. A non-string is
        ``invalid_request`` after the pop.
    :param redirect_uri: Form ``redirect_uri``. A non-string is
        ``invalid_request`` after the pop.
    :param ttl_seconds: Maximum age of the code.
    :returns: ``(pending, None)`` on success, otherwise
        ``(None, "invalid_request")`` or ``(None, "invalid_grant")``.
    """
    evict_expired_native_codes(codes, ttl_seconds=ttl_seconds)
    pending = codes.pop(code, None)
    if not isinstance(verifier, str) or not isinstance(redirect_uri, str):
        return None, "invalid_request"
    if (
        pending is None
        or time.time() - pending.created_at > ttl_seconds
        or redirect_uri != pending.redirect_uri
        or not CODE_VERIFIER_RE.fullmatch(verifier)
        or not hmac.compare_digest(derive_code_challenge(verifier), pending.code_challenge)
    ):
        return None, "invalid_grant"
    return pending, None


def is_loopback_redirect_uri(raw: str) -> bool:
    """Whether ``raw`` is an RFC 8252 loopback redirect a native app can listen on.

    Only ``http://127.0.0.1:<port>/...`` and ``http://[::1]:<port>/...``
    qualify: an explicit port, no userinfo, query, or fragment. Anything
    else could deliver the sign-in code off this machine.

    :param raw: The ``native_redirect_uri`` query parameter, e.g.
        ``"http://127.0.0.1:53682/callback"``.
    :returns: True when the URI is an acceptable loopback redirect.
    """
    if not raw or "?" in raw or "#" in raw or "\\" in raw or any(c.isspace() for c in raw):
        return False
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "http"
        and parts.hostname in NATIVE_LOOPBACK_HOSTS
        and port is not None
        and port > 0
        and parts.username is None
        and parts.password is None
    )


def parse_native_sign_in(params: Mapping[str, str]) -> dict[str, str] | None:
    """Read the native sign-in parameters of a ``/auth/login`` request.

    :param params: The request's query parameters.
    :returns: ``{"redirect_uri", "state", "code_challenge"}``, or ``None``
        when the request carries no native parameter.
    :raises ValueError: When any native parameter is present but the set
        is incomplete or invalid. Only S256 challenges are accepted.
    """
    if not any(name in params for name in NATIVE_PARAMS):
        return None
    redirect_uri = params.get("native_redirect_uri", "")
    native_state = params.get("native_state", "")
    challenge = params.get("code_challenge", "")
    if (
        params.get("code_challenge_method") != "S256"
        or not (redirect_uri in NATIVE_APP_REDIRECT_URIS or is_loopback_redirect_uri(redirect_uri))
        or not _NATIVE_STATE_RE.fullmatch(native_state)
        or not _S256_CHALLENGE_RE.fullmatch(challenge)
    ):
        raise ValueError("invalid native sign-in parameters")
    return {"redirect_uri": redirect_uri, "state": native_state, "code_challenge": challenge}


def native_from_state(state_payload: Mapping[str, object] | None) -> dict[str, str] | None:
    """Return the native sign-in recorded in a verified OIDC auth state, if any.

    :param state_payload: The decoded, signature-checked state cookie.
    :returns: The dict :func:`parse_native_sign_in` produced at login, or
        ``None`` for a browser or CLI-ticket sign-in.
    """
    native = state_payload.get("native") if state_payload else None
    if not isinstance(native, dict):
        return None
    fields = ("redirect_uri", "state", "code_challenge")
    if not all(isinstance(native.get(name), str) for name in fields):
        return None
    return {name: native[name] for name in fields}


def native_redirect_url(native: Mapping[str, str], params: Mapping[str, str]) -> str:
    """Build the URL a native app's redirect listener receives.

    :param native: The sign-in from :func:`parse_native_sign_in`.
    :param params: Query parameters to deliver, e.g. ``{"code": ...}``.
        The app's ``state`` is appended.
    :returns: ``redirect_uri`` with those query parameters.
    """
    query = urlencode({**params, "state": native["state"]})
    return f"{native['redirect_uri']}?{query}"


def native_redirect(native: Mapping[str, str], params: dict[str, str]) -> RedirectResponse:
    """Redirect the browser to a native app's redirect URI.

    :param native: The native sign-in from :func:`native_from_state`.
    :param params: Query parameters to deliver, e.g. ``{"code": ...}``;
        the app's ``state`` is appended.
    :returns: A 302 to the native redirect URI.
    """
    return RedirectResponse(url=native_redirect_url(native, params), status_code=302)


def origin_of(url: str) -> str:
    """Return ``scheme://host[:port]`` for ``url``.

    :param url: An absolute URL or an origin, e.g.
        ``"https://pub.example/auth/google/callback"``.
    :returns: The origin, or ``""`` when ``url`` has no scheme or host.
    """
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


def _origin_key(origin: str) -> tuple[str, str, int | None] | None:
    """Normalize an origin so default ports and host case do not look different."""
    parts = urlsplit(origin)
    if not parts.scheme or parts.hostname is None:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port is None:
        port = 443 if parts.scheme == "https" else 80 if parts.scheme == "http" else None
    return parts.scheme.lower(), parts.hostname.lower(), port


def origins_match(left: str, right: str) -> bool:
    """Whether two origins are the same scheme, host, and port.

    :param left: An origin or absolute URL.
    :param right: An origin or absolute URL.
    :returns: True when both parse and name the same public origin.
    """
    left_key = _origin_key(left if "://" in left else origin_of(left))
    right_key = _origin_key(right if "://" in right else origin_of(right))
    return left_key is not None and left_key == right_key


def request_public_origin(request: Request) -> str:
    """Return the scheme and host the client used to reach this request.

    ``X-Forwarded-Proto`` and ``X-Forwarded-Host`` win when a reverse proxy
    (ngrok, Caddy) set them — the same headers the server already trusts
    for the public request URL. Otherwise the ``Host`` header, then the
    ASGI URL, so a direct ``http://127.0.0.1`` request stays on loopback.

    :param request: The incoming request.
    :returns: ``scheme://host[:port]``.
    """
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")
    host_header = request.headers.get("host")
    if forwarded_proto and forwarded_proto.strip():
        scheme = forwarded_proto.split(",")[0].strip()
    else:
        scheme = request.url.scheme
    raw_host = forwarded_host or host_header
    if raw_host and raw_host.strip():
        host = raw_host.split(",")[0].strip()
    else:
        host = request.url.netloc
    return f"{scheme}://{host}"


def request_is_https(request: Request) -> bool:
    """Whether the client used HTTPS, honoring ``X-Forwarded-Proto``.

    :param request: The incoming request.
    :returns: True for an https request or a proxy that says the client did.
    """
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_proto and forwarded_proto.strip():
        return forwarded_proto.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def public_redirect(origin: str, path: str, query: str) -> str:
    """Return ``origin + path`` with ``query`` preserved exactly.

    :param origin: ``scheme://host[:port]`` of the public host.
    :param path: Absolute path, e.g. ``"/auth/google/start"``.
    :param query: The raw query string without ``?``, or ``""``.
    :returns: The absolute URL.
    """
    if query:
        return f"{origin}{path}?{query}"
    return f"{origin}{path}"


def encode_native_cookie(secret: bytes, native: Mapping[str, str]) -> str:
    """Sign the native parameters into an ``ap_native`` cookie value.

    :param secret: Accounts cookie HMAC key.
    :param native: ``redirect_uri``, ``state``, and ``code_challenge``.
    :returns: An HS256 JWT that expires in :data:`NATIVE_COOKIE_TTL_SECONDS`.
    """
    now = int(time.time())
    token = jwt.encode(
        {
            "redirect_uri": native["redirect_uri"],
            "state": native["state"],
            "code_challenge": native["code_challenge"],
            "iat": now,
            "exp": now + NATIVE_COOKIE_TTL_SECONDS,
        },
        secret,
        algorithm="HS256",
    )
    return token if isinstance(token, str) else token.decode("ascii")


def decode_native_cookie(raw: str, secret: bytes) -> dict[str, str] | None:
    """Return the native parameters in a valid ``ap_native`` cookie.

    Signature, expiry, and the same redirect/challenge checks as
    :func:`parse_native_sign_in` all have to hold.

    :param raw: The cookie value.
    :param secret: Accounts cookie HMAC key.
    :returns: ``{"redirect_uri", "state", "code_challenge"}``, or ``None``.
    """
    try:
        payload = jwt.decode(raw, secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    if not isinstance(payload, dict):
        return None
    redirect_uri = payload.get("redirect_uri")
    native_state = payload.get("state")
    challenge = payload.get("code_challenge")
    if (
        not isinstance(redirect_uri, str)
        or not isinstance(native_state, str)
        or not isinstance(challenge, str)
    ):
        return None
    try:
        return parse_native_sign_in(
            {
                "native_redirect_uri": redirect_uri,
                "native_state": native_state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    except ValueError:
        return None


def read_native_cookie(request: Request, secret: bytes) -> dict[str, str] | None:
    """Read ``ap_native`` from ``request``, or ``None`` when it is absent or bad.

    :param request: The incoming request.
    :param secret: Accounts cookie HMAC key.
    """
    raw = request.cookies.get(NATIVE_COOKIE_NAME)
    if not raw:
        return None
    return decode_native_cookie(raw, secret)


def native_cookie_secure(request: Request, *, config_secure: bool) -> bool:
    """Whether ``ap_native`` should set ``Secure``.

    Accounts base URL on the local daemon is plain HTTP, so
    ``config_secure`` is false there. A browser that reached the public
    HTTPS host still needs ``Secure`` or the cookie would be sent back to
    loopback.

    :param request: The request that sets or clears the cookie.
    :param config_secure: ``AccountsConfig.secure_cookies``.
    """
    return config_secure or request_is_https(request)


def apply_native_cookie(response: Response, token: str, *, secure: bool) -> None:
    """Set ``ap_native`` on ``response``.

    Path is ``/auth`` so ``/auth/google/*`` and ``POST /auth/login`` see it
    and the SPA document at ``/login`` does not.

    :param response: The redirect that carries the cookie.
    :param token: The signed cookie value.
    :param secure: The ``Secure`` attribute.
    """
    response.set_cookie(
        key=NATIVE_COOKIE_NAME,
        value=token,
        max_age=NATIVE_COOKIE_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path=NATIVE_COOKIE_PATH,
    )


def clear_native_cookie(response: Response, *, secure: bool) -> None:
    """Delete ``ap_native``. Attributes match :func:`apply_native_cookie`.

    :param response: The response that finishes the native handoff.
    :param secure: The ``Secure`` attribute the cookie was set with.
    """
    response.delete_cookie(
        key=NATIVE_COOKIE_NAME,
        path=NATIVE_COOKIE_PATH,
        secure=secure,
        httponly=True,
        samesite="lax",
    )


def take_native_redirect(
    request: Request,
    *,
    secret: bytes,
    user_id: str,
    codes: dict[str, NativeCode],
) -> str | None:
    """Issue a one-time code when ``ap_native`` is valid.

    The caller still sets the browser session cookie. The desktop exchanges
    the one-time code; a person who finishes in this same tab stays signed
    in there too. The caller clears ``ap_native`` when this returns a URL.

    :param request: The login or Google callback request.
    :param secret: Accounts cookie HMAC key.
    :param user_id: The account that just authenticated.
    :param codes: The store shared with ``POST /auth/native-token``.
    :returns: The loopback (or app-scheme) URL, or ``None`` when this
        browser is not in a native sign-in.
    """
    native = read_native_cookie(request, secret)
    if native is None:
        return None
    code = issue_native_code(
        codes,
        user_id=user_id,
        code_challenge=native["code_challenge"],
        redirect_uri=native["redirect_uri"],
    )
    return native_redirect_url(native, {"code": code})
