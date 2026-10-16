"""Continue with Google on an accounts-mode server.

The button reuses the OIDC authorization-code and PKCE helpers and talks
only to ``https://accounts.google.com``. It does not select the OIDC auth
source. The session cookie it sets is the accounts cookie, with ``sub``
equal to the username.
"""

from __future__ import annotations

import hashlib
import html
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.responses import Response

from omnigent.db.account_authority import bind_account_authority
from omnigent.server.accounts_config import AccountsConfig
from omnigent.server.accounts_store import SqlAlchemyAccountStore
from omnigent.server.auth import UnifiedAuthProvider
from omnigent.server.login_lockout import record_failure
from omnigent.server.oidc import derive_code_challenge, generate_code_verifier

_logger = logging.getLogger(__name__)

GOOGLE_ISSUER = "https://accounts.google.com"
_GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
_GOOGLE_JWKS = "https://www.googleapis.com/oauth2/v3/certs"
_GOOGLE_SCOPES = "openid email profile"

_STATE_COOKIE = "ap_google_state"
_BIND_COOKIE = "ap_google_bind"
_BIND_MAX_AGE = 600
_STATE_MAX_AGE = 600

_INVALID_LOGIN = {"error": "invalid username or password"}


class GoogleExchangeError(Exception):
    """The token endpoint refused the authorization code."""


@dataclass(frozen=True)
class GoogleAccountsConfig:
    """The four values that show the button. Read live from the environment."""

    client_id: str
    client_secret: str
    redirect_uri: str
    allowed_domains: frozenset[str]


def google_accounts_config() -> GoogleAccountsConfig | None:
    """Return Google button settings, or ``None`` when the button stays hidden.

    Accounts mode is required. An empty allowlist does not mean every
    Google account. The issuer is not consulted: only these four variables
    matter, and setting them must not flip the server to OIDC.
    """
    from omnigent.server.auth import resolve_auth_source

    if resolve_auth_source() != "accounts":
        return None
    client_id = os.environ.get("OMNIGENT_OIDC_CLIENT_ID", "").strip()
    client_secret = os.environ.get("OMNIGENT_OIDC_CLIENT_SECRET", "").strip()
    redirect_uri = os.environ.get("OMNIGENT_OIDC_REDIRECT_URI", "").strip()
    raw_domains = os.environ.get("OMNIGENT_OIDC_ALLOWED_DOMAINS", "")
    domains = frozenset(part.strip().lower() for part in raw_domains.split(",") if part.strip())
    if not client_id or not client_secret or not redirect_uri or not domains:
        return None
    return GoogleAccountsConfig(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        allowed_domains=domains,
    )


def exchange_code(
    *,
    code: str,
    code_verifier: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> str:
    """Exchange an authorization code for an ``id_token``.

    Tests replace this function. The arguments are not logged.
    """
    if not code or not code_verifier:
        raise GoogleExchangeError("pkce")
    try:
        response = httpx.post(
            _GOOGLE_TOKEN,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            },
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise GoogleExchangeError("transport") from exc
    if response.status_code != 200:
        raise GoogleExchangeError("token")
    try:
        body = response.json()
    except ValueError as exc:
        raise GoogleExchangeError("token") from exc
    id_token = body.get("id_token") if isinstance(body, dict) else None
    if not isinstance(id_token, str) or not id_token:
        raise GoogleExchangeError("token")
    return id_token


def decode_id_token(id_token: str, *, client_id: str) -> dict[str, Any]:
    """Validate a Google ``id_token`` against Google's JWKS.

    Tests replace this function. The token is not logged.
    """
    signing_key = jwt.PyJWKClient(_GOOGLE_JWKS).get_signing_key_from_jwt(id_token)
    claims = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=client_id,
        issuer=GOOGLE_ISSUER,
    )
    if not isinstance(claims, dict):
        raise jwt.InvalidTokenError("id_token claims")
    return claims


def login_page_html(*, bind_email: str | None) -> str:
    """Password form, and the Google button when the four variables are set."""
    configured = google_accounts_config() is not None
    button = (
        '<p><a href="/auth/google/start">Continue with Google</a></p>' if configured else ""
    )
    bind = ""
    if bind_email:
        safe = html.escape(bind_email, quote=True)
        bind = (
            '<section id="google-bind">'
            f"<p>Google confirmed {safe}.</p>"
            "<p>Sessions owned by another username will not appear.</p>"
            '<form id="google-bind-password" action="/auth/google/bind" method="post">'
            '<input name="username" autocomplete="username">'
            '<input name="password" type="password" autocomplete="current-password">'
            '<button type="submit">Sign in</button>'
            "</form>"
            '<form id="google-bind-create" action="/auth/google/create" method="post">'
            '<button type="submit">Create account</button>'
            "</form>"
            '<form id="google-bind-cancel" action="/auth/google/cancel" method="post">'
            '<button type="submit">Cancel</button>'
            "</form>"
            "</section>"
        )
    return (
        "<!DOCTYPE html><html><body>"
        f"{button}"
        '<form id="password-login" action="/auth/login" method="post">'
        '<input name="username" autocomplete="username">'
        '<input name="password" type="password" autocomplete="current-password">'
        '<button type="submit">Sign in</button>'
        "</form>"
        f"{bind}"
        "</body></html>"
    )


def bind_email_for_display(request: Request, cookie_secret: bytes) -> str | None:
    """Email to show on the login page. The bind cookie wins over the query."""
    payload = _decode_bind_cookie(request, cookie_secret)
    if payload is not None:
        email = payload.get("email")
        if isinstance(email, str) and email:
            return email
    raw = request.query_params.get("google_email", "").strip().lower()
    if raw and "@" in raw:
        return raw
    return None


def inject_login_shell(response: Response, scope: dict[str, Any]) -> Response:
    """Append the accounts login fragment to the SPA shell for ``/login``."""
    body = getattr(response, "body", None)
    if not isinstance(body, (bytes, bytearray)) or b"</body>" not in body:
        return response
    query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
    email = (query.get("google_email") or [""])[0].strip().lower()
    fragment = login_page_html(bind_email=email or None)
    hidden = f'<div hidden id="accounts-google-shell">{fragment}</div>'.encode()
    html_body = bytes(body).replace(b"</body>", hidden + b"</body>", 1)
    headers = {key: value for key, value in response.headers.items() if key.lower() != "content-length"}
    return Response(html_body, status_code=response.status_code, media_type="text/html", headers=headers)


def register_google_routes(
    router: APIRouter,
    auth_provider: UnifiedAuthProvider,
    account_store: SqlAlchemyAccountStore,
    accounts: AccountsConfig,
) -> None:
    """Mount ``/google/*`` on the accounts router."""
    secure = accounts.secure_cookies
    session_cookie = accounts.session_cookie_name
    session_max_age = accounts.session_ttl_hours * 3600
    secret = accounts.cookie_secret

    def _origin_ok(request: Request) -> bool:
        origin = request.headers.get("origin")
        parts = urlsplit(accounts.base_url)
        return origin == f"{parts.scheme}://{parts.netloc}"

    def _clear_state(response: Response) -> None:
        response.delete_cookie(
            key=_STATE_COOKIE,
            path="/",
            secure=secure,
            httponly=True,
            samesite="lax",
        )

    def _clear_bind(response: Response) -> None:
        response.delete_cookie(
            key=_BIND_COOKIE,
            path="/auth",
            secure=secure,
            httponly=True,
            samesite="strict",
        )

    def _set_session(response: Response, user_id: str) -> bool:
        generation = account_store.ensure_account_generation(user_id)
        if not isinstance(generation, str):
            return False
        bind_account_authority(user_id, generation)
        now = int(time.time())
        token = jwt.encode(
            {
                "sub": user_id,
                "iat": now,
                "exp": now + session_max_age,
                "provider": "accounts",
                "sid": uuid.uuid4().hex,
                "account_generation": generation,
            },
            secret,
            algorithm="HS256",
        )
        account_store.mark_logged_in(user_id, now)
        response.set_cookie(
            key=session_cookie,
            value=token,
            max_age=session_max_age,
            httponly=True,
            secure=secure,
            samesite="lax",
            path="/",
        )
        return True

    def _logged_in(user_id: str) -> Response:
        response = RedirectResponse("/", status_code=302)
        if not _set_session(response, user_id):
            failed = JSONResponse(status_code=401, content=_INVALID_LOGIN)
            _clear_state(failed)
            return failed
        _clear_state(response)
        _clear_bind(response)
        return response

    def _json_session(user_id: str) -> Response:
        generation = account_store.ensure_account_generation(user_id)
        user = account_store.get_user(user_id)
        if user is None or not isinstance(generation, str):
            return JSONResponse(status_code=401, content=_INVALID_LOGIN)
        response = JSONResponse(
            status_code=200,
            content={
                "user": {"id": user.id, "is_admin": user.is_admin},
                "expires_in": session_max_age,
            },
        )
        if not _set_session(response, user_id):
            return JSONResponse(status_code=401, content=_INVALID_LOGIN)
        _clear_bind(response)
        return response

    def _actor(request: Request) -> dict[str, str]:
        user_id = auth_provider.get_user_id(request)
        raw = request.cookies.get(session_cookie)
        if user_id and raw:
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            return {"kind": "user", "sub": user_id, "sid_hash": digest}
        return {"kind": "anonymous"}

    def _state_matches(actor: object, request: Request, user_id: str) -> bool:
        if not isinstance(actor, dict) or actor.get("kind") != "user":
            return False
        if actor.get("sub") != user_id:
            return False
        raw = request.cookies.get(session_cookie)
        if not raw or not isinstance(actor.get("sid_hash"), str):
            return False
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return actor.get("sid_hash") == digest

    @router.get("/google/status")
    async def google_status(request: Request) -> JSONResponse:
        configured = google_accounts_config() is not None
        payload = _decode_bind_cookie(request, secret)
        email = payload.get("email") if isinstance(payload, dict) else None
        bind_email = email if configured and isinstance(email, str) else None
        return JSONResponse({"configured": configured, "bind_email": bind_email})

    @router.get("/google/start")
    async def google_start(request: Request) -> Response:
        cfg = google_accounts_config()
        if cfg is None:
            return JSONResponse(status_code=404, content={"error": "not configured"})
        verifier = generate_code_verifier()
        challenge = derive_code_challenge(verifier)
        state = uuid.uuid4().hex
        nonce = uuid.uuid4().hex
        now = int(time.time())
        token = jwt.encode(
            {
                "state": state,
                "code_verifier": verifier,
                "nonce": nonce,
                "actor": _actor(request),
                "iat": now,
                "exp": now + _STATE_MAX_AGE,
            },
            secret,
            algorithm="HS256",
        )
        query = urlencode(
            {
                "client_id": cfg.client_id,
                "redirect_uri": cfg.redirect_uri,
                "response_type": "code",
                "scope": _GOOGLE_SCOPES,
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        response = RedirectResponse(f"{_GOOGLE_AUTH}?{query}", status_code=302)
        response.set_cookie(
            key=_STATE_COOKIE,
            value=token,
            max_age=_STATE_MAX_AGE,
            httponly=True,
            secure=secure,
            samesite="lax",
            path="/",
        )
        return response

    @router.get("/google/callback")
    async def google_callback(request: Request) -> Response:
        cfg = google_accounts_config()
        if cfg is None:
            return JSONResponse(status_code=404, content={"error": "not configured"})
        raw_state = request.cookies.get(_STATE_COOKIE)
        if not raw_state:
            _logger.info("auth/google: state missing")
            return _reject(400, secure)
        try:
            state_payload = jwt.decode(raw_state, secret, algorithms=["HS256"])
        except jwt.InvalidTokenError:
            _logger.info("auth/google: state invalid")
            return _reject(400, secure)
        if not isinstance(state_payload, dict):
            return _reject(400, secure)
        if request.query_params.get("state") != state_payload.get("state"):
            _logger.info("auth/google: state mismatch")
            return _reject(400, secure)
        verifier = state_payload.get("code_verifier")
        expected_nonce = state_payload.get("nonce")
        code = request.query_params.get("code", "")
        if not isinstance(verifier, str) or not isinstance(expected_nonce, str) or not code:
            return _reject(400, secure)
        try:
            id_token = exchange_code(
                code=code,
                code_verifier=verifier,
                client_id=cfg.client_id,
                client_secret=cfg.client_secret,
                redirect_uri=cfg.redirect_uri,
            )
            claims = decode_id_token(id_token, client_id=cfg.client_id)
        except (GoogleExchangeError, jwt.InvalidTokenError, Exception):
            _logger.info("auth/google: pkce or token rejected")
            return _reject(400, secure)
        if claims.get("nonce") != expected_nonce or claims.get("iss") != GOOGLE_ISSUER:
            return _reject(400, secure)
        audience = claims.get("aud")
        if audience != cfg.client_id and not (
            isinstance(audience, list) and cfg.client_id in audience
        ):
            return _reject(400, secure)
        identity = _verified_identity(claims, cfg.allowed_domains)
        if identity is None:
            _logger.info("auth/google: identity rejected")
            failed = JSONResponse(status_code=403, content={"error": "forbidden"})
            _clear_named(failed, _STATE_COOKIE, "/", secure, "lax")
            return failed
        email, google_sub = identity

        linked = account_store.google_user_for_subject(GOOGLE_ISSUER, google_sub)
        if linked is not None:
            return _logged_in(linked)

        password_hash = account_store.get_password_hash(email)
        email_user = account_store.get_user(email)
        if email_user is not None and password_hash is None:
            result = account_store.link_google(GOOGLE_ISSUER, google_sub, email, email)
            if result != "ok":
                failed = JSONResponse(status_code=409, content={"error": "already linked"})
                _clear_named(failed, _STATE_COOKIE, "/", secure, "lax")
                return failed
            return _logged_in(email)

        if account_store.google_user_for_email(email) is not None:
            failed = JSONResponse(status_code=409, content={"error": "already linked"})
            _clear_named(failed, _STATE_COOKIE, "/", secure, "lax")
            return failed

        current = auth_provider.get_user_id(request)
        if current and _state_matches(state_payload.get("actor"), request, current):
            result = account_store.link_google(GOOGLE_ISSUER, google_sub, current, email)
            if result != "ok":
                failed = JSONResponse(status_code=409, content={"error": "already linked"})
                _clear_named(failed, _STATE_COOKIE, "/", secure, "lax")
                return failed
            return _logged_in(current)

        return _issue_bind(email, google_sub, secure, secret, account_store)

    @router.post("/google/bind")
    async def google_bind(request: Request) -> Response:
        if not _origin_ok(request):
            return JSONResponse(status_code=403, content={"error": "forbidden"})
        payload = _decode_bind_cookie(request, secret)
        if payload is None:
            return JSONResponse(status_code=400, content={"error": "invalid bind"})
        username, password = await _credentials(request)
        username = username.strip().lower()
        if not username or not password:
            return JSONResponse(status_code=400, content={"error": "invalid bind"})
        if not _password_ok(account_store, username, password):
            record_failure(username)
            return JSONResponse(status_code=401, content=_INVALID_LOGIN)
        result = account_store.consume_bind_and_link(
            jti=str(payload["jti"]),
            issuer=str(payload["iss"]),
            subject=str(payload["sub"]),
            email=str(payload["email"]),
            user_id=username,
            now=int(time.time()),
        )
        if result == "replay":
            failed = JSONResponse(status_code=400, content={"error": "invalid bind"})
            _clear_bind(failed)
            return failed
        if result != "ok":
            return JSONResponse(status_code=409, content={"error": "already linked"})
        return _json_session(username)

    @router.post("/google/create")
    async def google_create(request: Request) -> Response:
        if not _origin_ok(request):
            return JSONResponse(status_code=403, content={"error": "forbidden"})
        payload = _decode_bind_cookie(request, secret)
        if payload is None:
            return JSONResponse(status_code=400, content={"error": "invalid bind"})
        result = account_store.consume_bind_and_create(
            jti=str(payload["jti"]),
            issuer=str(payload["iss"]),
            subject=str(payload["sub"]),
            email=str(payload["email"]),
            now=int(time.time()),
        )
        if result == "replay":
            failed = JSONResponse(status_code=400, content={"error": "invalid bind"})
            _clear_bind(failed)
            return failed
        if result != "ok":
            return JSONResponse(status_code=409, content={"error": "already linked"})
        return _json_session(str(payload["email"]))

    @router.post("/google/cancel")
    async def google_cancel(request: Request) -> Response:
        if not _origin_ok(request):
            return JSONResponse(status_code=403, content={"error": "forbidden"})
        payload = _decode_bind_cookie(request, secret)
        if payload is not None and isinstance(payload.get("jti"), str):
            account_store.consume_bind_nonce(payload["jti"], now=int(time.time()))
        response = RedirectResponse("/login", status_code=302)
        _clear_bind(response)
        return response

    @router.delete("/google/link")
    async def google_unlink(request: Request) -> Response:
        user_id = auth_provider.get_user_id(request)
        if user_id is None:
            return JSONResponse(status_code=401, content={"error": "not authenticated"})
        result = account_store.unlink_google(user_id)
        if result == "passwordless_identity":
            return JSONResponse(status_code=409, content={"error": "cannot unlink this account"})
        if result == "missing":
            return JSONResponse(status_code=404, content={"error": "not linked"})
        return Response(status_code=204)


def _reject(status: int, secure: bool) -> JSONResponse:
    response = JSONResponse(status_code=status, content={"error": "invalid request"})
    _clear_named(response, _STATE_COOKIE, "/", secure, "lax")
    return response


def _clear_named(response: Response, key: str, path: str, secure: bool, samesite: str) -> None:
    response.delete_cookie(key=key, path=path, secure=secure, httponly=True, samesite=samesite)


def _issue_bind(
    email: str,
    google_sub: str,
    secure: bool,
    secret: bytes,
    account_store: SqlAlchemyAccountStore,
) -> Response:
    now = int(time.time())
    jti = uuid.uuid4().hex
    expires = now + _BIND_MAX_AGE
    account_store.issue_bind_nonce(
        jti,
        issuer=GOOGLE_ISSUER,
        subject=google_sub,
        email=email,
        expires_at=expires,
    )
    token = jwt.encode(
        {
            "email": email,
            "sub": google_sub,
            "iss": GOOGLE_ISSUER,
            "jti": jti,
            "iat": now,
            "exp": expires,
        },
        secret,
        algorithm="HS256",
    )
    response = RedirectResponse("/login?" + urlencode({"google_email": email}), status_code=302)
    _clear_named(response, _STATE_COOKIE, "/", secure, "lax")
    response.set_cookie(
        key=_BIND_COOKIE,
        value=token,
        max_age=_BIND_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/auth",
    )
    return response


def _decode_bind_cookie(request: Request, secret: bytes) -> dict[str, Any] | None:
    raw = request.cookies.get(_BIND_COOKIE)
    if not raw:
        return None
    try:
        payload = jwt.decode(raw, secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    if not isinstance(payload, dict):
        return None
    email = payload.get("email")
    subject = payload.get("sub")
    issuer = payload.get("iss")
    jti = payload.get("jti")
    if (
        not isinstance(email, str)
        or not isinstance(subject, str)
        or issuer != GOOGLE_ISSUER
        or not isinstance(jti, str)
    ):
        return None
    return payload


def _verified_identity(
    claims: dict[str, Any],
    allowed: frozenset[str],
) -> tuple[str, str] | None:
    """Return ``(email, sub)`` or ``None`` when the claim must be rejected."""
    if not _verified_true(claims.get("email_verified")):
        return None
    email = claims.get("email")
    subject = claims.get("sub")
    if not isinstance(email, str) or "@" not in email or not isinstance(subject, str) or not subject:
        return None
    email = email.strip().lower()
    domain = email.split("@", 1)[1]
    if not domain or domain not in allowed:
        return None
    hosted = claims.get("hd")
    if isinstance(hosted, str) and hosted.strip():
        hosted_domain = hosted.strip().lower()
        if hosted_domain not in allowed or hosted_domain != domain:
            return None
    return email, subject


def _verified_true(value: object) -> bool:
    if value is True:
        return True
    return isinstance(value, str) and value.strip().lower() == "true"


async def _credentials(request: Request) -> tuple[str, str]:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        body = await request.json()
        if not isinstance(body, dict):
            return "", ""
        username = body.get("username", "")
        password = body.get("password", "")
        return (username if isinstance(username, str) else "", password if isinstance(password, str) else "")
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")
    return (
        username if isinstance(username, str) else "",
        password if isinstance(password, str) else "",
    )


def _password_ok(account_store: SqlAlchemyAccountStore, username: str, password: str) -> bool:
    """True only for an existing user with a matching password.

    Unknown users, wrong passwords, and passwordless usernames all fail
    after one password verify, so the three responses take similar time.
    """
    from omnigent.server.passwords import InvalidPasswordError, verify_password
    from omnigent.server.routes.accounts_auth import _DUMMY_HASH

    password_hash, _generation = account_store.login_snapshot(username)
    try:
        if password_hash is None:
            verify_password(password, _DUMMY_HASH)
            return False
        verify_password(password, password_hash)
        return True
    except InvalidPasswordError:
        return False
