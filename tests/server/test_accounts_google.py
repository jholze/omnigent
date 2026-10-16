"""Accounts-mode Continue with Google.

The button reuses Google's authorization-code flow. It does not select
the OIDC auth source, and the session cookie it sets is the accounts
cookie. These tests name the acceptance cases from issue #1.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from omnigent.server.accounts_store import SqlAlchemyAccountStore
from omnigent.server.auth import (
    LEVEL_EDIT,
    LEVEL_OWNER,
    LEVEL_READ,
    RESERVED_USER_PUBLIC,
    UnifiedAuthProvider,
    create_auth_provider,
    resolve_auth_source,
)
from omnigent.server.google_accounts import GOOGLE_ISSUER, GoogleExchangeError
from omnigent.server.google_accounts import exchange_code as _real_exchange_code
from omnigent.server.login_lockout import failure_count
from omnigent.server.passwords import hash_password
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from tests.server.test_accounts import _build_accounts_app
from tests.server.test_oidc import _make_oidc_config

_SECRET = "google-client-secret-do-not-leak"
_ORIGIN = {"origin": "http://localhost:8000"}
_JENS_PASSWORD = "jens-password"
# Distinct values so a leak in any log line names which credential escaped.
_AUTH_CODE = "authcode-leakcheck-4f1d9a"
_ID_TOKEN = "idtoken-leakcheck-8c2e7b"
_ACCESS_TOKEN = "accesstoken-leakcheck-3a6f0e"
_VERIFIER = "pkceverifier-leakcheck-" + "v" * 40


def _configure_google(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNIGENT_OIDC_CLIENT_ID", "google-client")
    monkeypatch.setenv("OMNIGENT_OIDC_CLIENT_SECRET", _SECRET)
    monkeypatch.setenv("OMNIGENT_OIDC_REDIRECT_URI", "http://localhost:8000/auth/google/callback")
    monkeypatch.setenv("OMNIGENT_OIDC_ALLOWED_DOMAINS", "example.com")
    monkeypatch.delenv("OMNIGENT_OIDC_ISSUER", raising=False)


@pytest.fixture
def google_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Accounts app with the four Google variables set and no OIDC issuer."""
    _configure_google(monkeypatch)
    monkeypatch.setattr("omnigent.server.google_accounts.generate_code_verifier", lambda: _VERIFIER)
    yield from _build_accounts_app(tmp_path, monkeypatch, init_admin_password="admin-pw-12345")


class _Identity:
    """Claims returned by the patched id_token decoder."""

    def __init__(self) -> None:
        self.email: str | None = "jens@example.com"
        self.sub = "google-sub-jens"
        self.nonce = ""
        self.verified: object = True
        self.hd: str | None = "example.com"
        self.include_hd = True
        self.state = ""


def _install_google(monkeypatch: pytest.MonkeyPatch, ident: _Identity) -> None:
    def token_post(url: str, *, data: dict[str, str], timeout: float) -> httpx.Response:
        del timeout
        assert data["code"] == _AUTH_CODE
        assert data["code_verifier"] == _VERIFIER
        body = {"id_token": _ID_TOKEN, "access_token": _ACCESS_TOKEN, "token_type": "Bearer"}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    def decode(id_token: str, *, client_id: str) -> dict[str, object]:
        assert id_token == _ID_TOKEN
        claims: dict[str, object] = {
            "iss": GOOGLE_ISSUER,
            "aud": client_id,
            "nonce": ident.nonce,
            "email_verified": ident.verified,
            "sub": ident.sub,
        }
        if ident.email is not None:
            claims["email"] = ident.email
        if ident.include_hd and ident.hd is not None:
            claims["hd"] = ident.hd
        return claims

    monkeypatch.setattr("omnigent.server.google_accounts.exchange_code", _real_exchange_code)
    monkeypatch.setattr("omnigent.server.google_accounts.httpx.post", token_post)
    monkeypatch.setattr("omnigent.server.google_accounts.decode_id_token", decode)


def _store(tmp_path: Path) -> SqlAlchemyAccountStore:
    return SqlAlchemyAccountStore(f"sqlite:///{tmp_path}/test.db")


def _permissions(tmp_path: Path) -> SqlAlchemyPermissionStore:
    return SqlAlchemyPermissionStore(f"sqlite:///{tmp_path}/test.db")


def _conversations(tmp_path: Path) -> SqlAlchemyConversationStore:
    return SqlAlchemyConversationStore(f"sqlite:///{tmp_path}/test.db")


def _add_user(tmp_path: Path, user_id: str, password: str | None) -> None:
    store = _store(tmp_path)
    if password is None:
        store.create_passwordless_user(user_id)
        return
    store.create_user_with_password(user_id, hash_password(password))


def _secret() -> bytes:
    return bytes.fromhex(os.environ["OMNIGENT_ACCOUNTS_COOKIE_SECRET"])


def _start(client: TestClient) -> tuple[str, str]:
    response = client.get("/auth/google/start", follow_redirects=False)
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert _SECRET not in location
    query = parse_qs(urlsplit(location).query)
    assert query["scope"] == ["openid email profile"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["response_type"] == ["code"]
    return query["state"][0], query["nonce"][0]


def _callback(client: TestClient, state: str) -> object:
    return client.get(
        "/auth/google/callback",
        params={"code": _AUTH_CODE, "state": state},
        follow_redirects=False,
    )


def _prepare(client: TestClient, monkeypatch: pytest.MonkeyPatch, ident: _Identity) -> object:
    state, nonce = _start(client)
    ident.nonce = nonce
    ident.state = state
    _install_google(monkeypatch, ident)
    return _callback(client, state)


def _set_cookie_headers(response: object) -> list[str]:
    return response.headers.get_list("set-cookie")  # type: ignore[attr-defined]


def _issued(response: object, name: str) -> str | None:
    """Return a newly set cookie value, ignoring a deletion."""
    prefix = name + "="
    for raw in _set_cookie_headers(response):
        if not raw.startswith(prefix) or "Max-Age=0" in raw:
            continue
        value = raw.split(";", 1)[0][len(prefix) :].strip('"')
        if value:
            return value
    return None


def _assert_no_new_session_or_bind(response: object) -> None:
    assert _issued(response, "ap_session") is None
    assert _issued(response, "ap_google_bind") is None


def _server_log(caplog: pytest.LogCaptureFixture) -> str:
    """Captured log text without the test client's own request lines."""
    return "\n".join(
        record.getMessage() for record in caplog.records if not record.name.startswith("httpx")
    )


def _assert_clean(
    response: object,
    caplog: pytest.LogCaptureFixture,
    ident: _Identity | None = None,
) -> None:
    text = response.text  # type: ignore[attr-defined]
    assert _SECRET not in text
    assert "id-token" not in text
    assert _ID_TOKEN not in text
    assert _ACCESS_TOKEN not in text
    assert _SECRET not in caplog.text
    for raw in _set_cookie_headers(response):
        assert _SECRET not in raw
    logged = _server_log(caplog)
    secrets = {
        "authorization code": _AUTH_CODE,
        "id_token": _ID_TOKEN,
        "access_token": _ACCESS_TOKEN,
        "PKCE verifier": _VERIFIER,
    }
    if ident is not None and ident.state:
        secrets["state"] = ident.state
    bind = _issued(response, "ap_google_bind")
    if bind:
        secrets["bind cookie"] = bind
    for label, value in secrets.items():
        assert value not in logged, f"{label} leaked into the log"


def _bind(client: TestClient, username: str, password: str) -> object:
    return client.post(
        "/auth/google/bind",
        json={"username": username, "password": password},
        headers=_ORIGIN,
    )


def _session_ids(client: TestClient) -> list[str]:
    response = client.get("/v1/sessions")
    assert response.status_code == 200, response.text
    return [row["id"] for row in response.json()["data"]]


def _seed_session(tmp_path: Path, grantee: str, level: int, *, share_files: bool = False) -> str:
    conversations = _conversations(tmp_path)
    permissions = _permissions(tmp_path)
    conversation = conversations.create_conversation(agent_id=uuid.uuid4().hex, title="shared")
    permissions.grant("admin", conversation.id, LEVEL_OWNER)
    permissions.grant(grantee, conversation.id, level)
    if share_files:
        conversations.update_conversation(conversation.id, share_workspace_files=True)
    return conversation.id


def _cookie_value(client: TestClient, name: str) -> str | None:
    found = None
    for cookie in client.cookies.jar:
        if cookie.name == name and cookie.value.count(".") >= 2:
            found = cookie.value
    return found


def _assert_foreign_has_no_button(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    """A header or OIDC server does not serve the accounts Google button."""
    monkeypatch.setenv("OMNIGENT_AUTH_PROVIDER", source)
    root = tmp_path / source
    root.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(root / "home"))
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(root / "data"))
    db_url = f"sqlite:///{root}/test.db"
    from omnigent.db.utils import get_or_create_engine
    from omnigent.runtime import init as init_runtime
    from omnigent.runtime import telemetry
    from omnigent.runtime.agent_cache import AgentCache
    from omnigent.runtime.caps import RuntimeCaps
    from omnigent.server.app import create_app
    from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
    from omnigent.stores.artifact_store.local import LocalArtifactStore
    from omnigent.stores.comment_store.sqlalchemy_store import SqlAlchemyCommentStore
    from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
    from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
    from omnigent.stores.host_store import HostStore
    from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore

    get_or_create_engine(db_url)
    telemetry.init()
    artifact_store = LocalArtifactStore(str(root / "artifacts"))
    agent_cache = AgentCache(artifact_store=artifact_store, cache_dir=root / "cache")
    init_runtime(
        agent_cache=agent_cache,
        caps=RuntimeCaps(),
        agent_store=SqlAlchemyAgentStore(db_url),
        file_store=SqlAlchemyFileStore(db_url),
        conversation_store=SqlAlchemyConversationStore(db_url),
        artifact_store=artifact_store,
        comment_store=SqlAlchemyCommentStore(db_url),
    )
    if source == "oidc":
        provider = UnifiedAuthProvider(source="oidc", oidc_config=_make_oidc_config())
    else:
        provider = create_auth_provider()
    app = create_app(
        agent_store=SqlAlchemyAgentStore(db_url),
        file_store=SqlAlchemyFileStore(db_url),
        conversation_store=SqlAlchemyConversationStore(db_url),
        artifact_store=artifact_store,
        agent_cache=agent_cache,
        comment_store=SqlAlchemyCommentStore(db_url),
        permission_store=SqlAlchemyPermissionStore(db_url),
        host_store=HostStore(db_url),
        auth_provider=provider,
        account_store=None,
    )
    with TestClient(app) as client:
        page = client.get("/login")
        assert "Continue with Google" not in page.text
        assert client.get("/auth/google/start").status_code == 404


def test_login_page_hides_google_until_configured(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The button needs all four variables, and only on an accounts server."""
    caplog.set_level("INFO")
    assert resolve_auth_source() == "accounts"
    page = google_app.get("/login")
    assert page.status_code == 200
    assert "Continue with Google" in page.text
    assert 'id="password-login"' in page.text
    assert 'action="/auth/login"' in page.text
    assert _SECRET not in page.text

    required = (
        "OMNIGENT_OIDC_CLIENT_ID",
        "OMNIGENT_OIDC_CLIENT_SECRET",
        "OMNIGENT_OIDC_REDIRECT_URI",
        "OMNIGENT_OIDC_ALLOWED_DOMAINS",
    )
    for name in required:
        saved = os.environ[name]
        monkeypatch.delenv(name)
        hidden = google_app.get("/login")
        assert "Continue with Google" not in hidden.text
        assert 'action="/auth/login"' in hidden.text
        monkeypatch.setenv(name, saved)

    monkeypatch.setenv("OMNIGENT_OIDC_ALLOWED_DOMAINS", " , ")
    empty = google_app.get("/login")
    assert "Continue with Google" not in empty.text
    monkeypatch.setenv("OMNIGENT_OIDC_ALLOWED_DOMAINS", "example.com")

    for name in required:
        monkeypatch.setenv(name, os.environ.get(name, "example.com"))
    monkeypatch.setenv("OMNIGENT_OIDC_CLIENT_ID", "google-client")
    monkeypatch.setenv("OMNIGENT_OIDC_CLIENT_SECRET", _SECRET)
    monkeypatch.setenv("OMNIGENT_OIDC_REDIRECT_URI", "http://localhost:8000/auth/google/callback")
    _assert_foreign_has_no_button(tmp_path, monkeypatch, "header")
    _assert_foreign_has_no_button(tmp_path, monkeypatch, "oidc")
    assert _SECRET not in caplog.text


def test_google_callback_rejects_unverified_and_foreign_domains(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unverified, foreign, missing, or hosted-domain-mismatched mail is a 403."""
    caplog.set_level("INFO")
    ident = _Identity()
    cases = (
        {"verified": False},
        {"email": "ada@other.test", "hd": "other.test"},
        {"email": None},
        {"hd": "other.test"},
        {"email": "ada@other.test", "include_hd": False},
    )
    for override in cases:
        ident = _Identity()
        for key, value in override.items():
            setattr(ident, key, value)
        response = _prepare(google_app, monkeypatch, ident)
        assert response.status_code == 403, response.text
        assert response.json() == {"error": "forbidden"}
        _assert_no_new_session_or_bind(response)
        _assert_clean(response, caplog, ident)
        assert _store(tmp_path).get_user("jens@example.com") is None
        assert _store(tmp_path).google_user_for_email("jens@example.com") is None

    ident = _Identity()
    ident.include_hd = False
    accepted = _prepare(google_app, monkeypatch, ident)
    assert accepted.status_code == 302
    assert "google_email=jens%40example.com" in accepted.headers["location"]
    assert _issued(accepted, "ap_google_bind") is not None
    assert _issued(accepted, "ap_session") is None
    _assert_clean(accepted, caplog, ident)


def test_google_start_state_required(
    google_app: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A callback without a matching state and PKCE proof is a 400."""
    caplog.set_level("INFO")
    state, nonce = _start(google_app)
    ident = _Identity()
    ident.nonce = nonce
    _install_google(monkeypatch, ident)

    google_app.cookies.delete("ap_google_state")
    missing = _callback(google_app, state)
    assert missing.status_code == 400
    assert missing.json() == {"error": "invalid request"}
    _assert_no_new_session_or_bind(missing)

    state, nonce = _start(google_app)
    ident.nonce = nonce
    mismatched = _callback(google_app, "not-the-state")
    assert mismatched.status_code == 400
    _assert_no_new_session_or_bind(mismatched)

    def fail_exchange(**kwargs: object) -> str:
        del kwargs
        raise GoogleExchangeError("pkce")

    monkeypatch.setattr("omnigent.server.google_accounts.exchange_code", fail_exchange)
    state, _nonce = _start(google_app)
    failed = _callback(google_app, state)
    assert failed.status_code == 400
    _assert_no_new_session_or_bind(failed)

    state, nonce = _start(google_app)
    ident.nonce = "different-nonce"
    ident.state = state
    _install_google(monkeypatch, ident)
    bad_nonce = _callback(google_app, state)
    assert bad_nonce.status_code == 400
    _assert_no_new_session_or_bind(bad_nonce)
    _assert_clean(bad_nonce, caplog, ident)


def test_passwordless_email_username_auto_matches(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A passwordless user whose id is the verified email is signed in."""
    _add_user(tmp_path, "jens@example.com", None)
    conversation = _conversations(tmp_path).create_conversation(
        agent_id=uuid.uuid4().hex, title="owned by email"
    )
    _permissions(tmp_path).grant("jens@example.com", conversation.id, LEVEL_OWNER)
    users_before = len(_store(tmp_path).list_users())
    response = _prepare(google_app, monkeypatch, _Identity())
    assert response.status_code == 302
    assert response.headers["location"].endswith("/")
    token = _issued(response, "ap_session")
    assert token is not None
    claims = jwt.decode(token, _secret(), algorithms=["HS256"])
    assert claims["sub"] == "jens@example.com"
    assert _issued(response, "ap_google_bind") is None
    assert _store(tmp_path).google_user_for_subject(GOOGLE_ISSUER, "google-sub-jens") == (
        "jens@example.com"
    )
    me = google_app.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["id"] == "jens@example.com"
    assert conversation.id in _session_ids(google_app)
    assert len(_store(tmp_path).list_users()) == users_before


def test_password_email_username_uses_bind_panel(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An email-shaped username that already has a password is not auto-matched."""
    _add_user(tmp_path, "jens@example.com", _JENS_PASSWORD)
    response = _prepare(google_app, monkeypatch, _Identity())
    assert response.status_code == 302
    assert response.headers["location"].startswith("/login?google_email=")
    assert _issued(response, "ap_session") is None
    assert _issued(response, "ap_google_bind") is not None
    panel = google_app.get(response.headers["location"])
    assert "Google confirmed jens@example.com." in panel.text
    assert "Sessions owned by another username will not appear." in panel.text
    assert _store(tmp_path).google_user_for_email("jens@example.com") is None

    bound = _bind(google_app, "jens@example.com", _JENS_PASSWORD)
    assert bound.status_code == 200, bound.text
    assert bound.json()["user"]["id"] == "jens@example.com"
    assert _store(tmp_path).google_user_for_subject(GOOGLE_ISSUER, "google-sub-jens") == (
        "jens@example.com"
    )


def test_squatted_email_username_does_not_capture(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A password user who registered the email as a username does not inherit the login."""
    _add_user(tmp_path, "jens@example.com", "squatter-password")
    _add_user(tmp_path, "jens", _JENS_PASSWORD)
    response = _prepare(google_app, monkeypatch, _Identity())
    assert response.status_code == 302
    assert _issued(response, "ap_session") is None
    assert google_app.get("/auth/me").status_code == 401

    stolen = _bind(google_app, "jens", _JENS_PASSWORD)
    assert stolen.status_code == 409
    assert stolen.json() == {"error": "already linked"}
    assert "squatter" not in stolen.text
    assert _store(tmp_path).linked_email("jens") is None

    taken = _bind(google_app, "jens@example.com", "squatter-password")
    assert taken.status_code == 200, taken.text
    assert taken.json()["user"]["id"] == "jens@example.com"
    assert _store(tmp_path).linked_email("jens") is None


def test_unmatched_email_sets_bind_cookie_only(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown address sets the bind cookie and creates no user."""
    response = _prepare(google_app, monkeypatch, _Identity())
    assert response.status_code == 302
    assert _issued(response, "ap_google_bind") is not None
    assert _issued(response, "ap_session") is None
    raw = _issued(response, "ap_google_bind")
    assert raw is not None
    header = next(item for item in _set_cookie_headers(response) if item.startswith("ap_google_bind="))
    assert "HttpOnly" in header
    assert "SameSite=strict" in header or "SameSite=Strict" in header
    assert "Path=/auth" in header
    assert "Max-Age=600" in header
    assert _store(tmp_path).get_user("jens@example.com") is None
    assert _store(tmp_path).google_user_for_email("jens@example.com") is None
    me = google_app.get("/auth/me")
    assert me.status_code == 401


def test_bind_password_keeps_owner_and_grants(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Binding keeps the username as owner and honors both grant principals."""
    _add_user(tmp_path, "jens", _JENS_PASSWORD)
    owned = _seed_session(tmp_path, "jens", LEVEL_OWNER)
    shared = _seed_session(tmp_path, "jens@example.com", LEVEL_READ)
    response = _prepare(google_app, monkeypatch, _Identity())
    assert _issued(response, "ap_session") is None
    listed = google_app.get("/v1/sessions")
    assert listed.status_code == 401

    bound = _bind(google_app, "Jens", _JENS_PASSWORD)
    assert bound.status_code == 200, bound.text
    token = _issued(bound, "ap_session")
    assert token is not None
    claims = jwt.decode(token, _secret(), algorithms=["HS256"])
    assert claims["sub"] == "jens"
    assert "sid" in claims
    assert _store(tmp_path).linked_email("jens") == "jens@example.com"
    visible = set(_session_ids(google_app))
    assert owned in visible
    assert shared in visible
    owner = _permissions(tmp_path).get("jens", owned)
    assert owner is not None and owner.level == LEVEL_OWNER


def test_bind_second_principal_rejected(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One email maps to one user, and one user maps to one email."""
    _add_user(tmp_path, "jens", _JENS_PASSWORD)
    _add_user(tmp_path, "ada", "ada-password")
    _prepare(google_app, monkeypatch, _Identity())
    first = _bind(google_app, "jens", _JENS_PASSWORD)
    assert first.status_code == 200, first.text
    google_app.cookies.clear()

    other = _Identity()
    other.email = "ada@example.com"
    other.sub = "google-sub-ada"
    second = _prepare(google_app, monkeypatch, other)
    assert second.status_code == 302
    rejected = _bind(google_app, "jens", _JENS_PASSWORD)
    assert rejected.status_code == 409
    assert rejected.json() == {"error": "already linked"}
    assert "ada@example.com" not in rejected.text
    assert _store(tmp_path).linked_email("jens") == "jens@example.com"

    store = _store(tmp_path)
    store.issue_bind_nonce(
        "second-jti",
        issuer=GOOGLE_ISSUER,
        subject="google-sub-other",
        email="jens@example.com",
        expires_at=2_000_000_000,
    )
    forged = jwt.encode(
        {
            "email": "jens@example.com",
            "sub": "google-sub-other",
            "iss": GOOGLE_ISSUER,
            "jti": "second-jti",
            "exp": 2_000_000_000,
        },
        _secret(),
        algorithm="HS256",
    )
    google_app.cookies.set("ap_google_bind", forged, path="/auth")
    other_user = _bind(google_app, "ada", "ada-password")
    assert other_user.status_code == 409
    assert other_user.json() == {"error": "already linked"}
    assert "jens" not in other_user.json()["error"]
    assert _store(tmp_path).linked_email("ada") is None
    assert _store(tmp_path).google_user_for_email("jens@example.com") == "jens"


def test_bind_wrong_password_is_uniform(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown, wrong, and passwordless targets share one 401 and keep the bind."""
    _add_user(tmp_path, "jens", _JENS_PASSWORD)
    _add_user(tmp_path, "nopw", None)
    _prepare(google_app, monkeypatch, _Identity())
    before_login = failure_count("nobody")
    login = google_app.post("/auth/login", json={"username": "nobody", "password": "wrong-pass"})
    assert login.status_code == 401
    assert login.json() == {"error": "invalid username or password"}
    assert failure_count("nobody") == before_login + 1

    bodies = []
    for username, password in (
        ("nobody", "wrong-pass"),
        ("jens", "not-the-password"),
        ("nopw", "whatever-pass"),
    ):
        before = failure_count(username)
        response = _bind(google_app, username, password)
        assert response.status_code == 401
        bodies.append(response.json())
        assert failure_count(username) == before + 1
        assert _cookie_value(google_app, "ap_google_bind") is not None
    assert bodies[0] == bodies[1] == bodies[2] == {"error": "invalid username or password"}

    success = _bind(google_app, "jens", _JENS_PASSWORD)
    assert success.status_code == 200, success.text
    assert _store(tmp_path).linked_email("jens") == "jens@example.com"


def test_bind_expired_replayed_and_tampered(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired, replayed, re-signed, or doubled bind creates one link at most."""
    _add_user(tmp_path, "jens", _JENS_PASSWORD)
    expired = jwt.encode(
        {
            "email": "jens@example.com",
            "sub": "google-sub-jens",
            "iss": GOOGLE_ISSUER,
            "jti": "expired-jti",
            "exp": 1,
        },
        _secret(),
        algorithm="HS256",
    )
    google_app.cookies.set("ap_google_bind", expired, path="/auth")
    stale = _bind(google_app, "jens", _JENS_PASSWORD)
    assert stale.status_code == 400
    assert _store(tmp_path).linked_email("jens") is None

    google_app.cookies.set("ap_google_bind", "not-a-token", path="/auth")
    tampered = _bind(google_app, "jens", _JENS_PASSWORD)
    assert tampered.status_code == 400
    assert _store(tmp_path).linked_email("jens") is None

    google_app.cookies.clear()
    _prepare(google_app, monkeypatch, _Identity())
    saved = _cookie_value(google_app, "ap_google_bind")
    assert saved is not None
    payload = jwt.decode(saved, _secret(), algorithms=["HS256"])
    swapped = jwt.encode(
        {**payload, "email": "ada@example.com"},
        _secret(),
        algorithm="HS256",
    )
    google_app.cookies.set("ap_google_bind", swapped, path="/auth")
    swapped_response = _bind(google_app, "jens", _JENS_PASSWORD)
    assert swapped_response.status_code == 400
    assert _store(tmp_path).linked_email("jens") is None

    google_app.cookies.set("ap_google_bind", saved, path="/auth")
    first = _bind(google_app, "jens", _JENS_PASSWORD)
    assert first.status_code == 200, first.text
    google_app.cookies.set("ap_google_bind", saved, path="/auth")
    replay = _bind(google_app, "jens", _JENS_PASSWORD)
    assert replay.status_code == 400
    assert _store(tmp_path).google_user_for_email("jens@example.com") == "jens"

    store = _store(tmp_path)
    _add_user(tmp_path, "ada", "ada-password")
    store.issue_bind_nonce(
        "race-jti",
        issuer=GOOGLE_ISSUER,
        subject="google-sub-race",
        email="ada@example.com",
        expires_at=2_000_000_000,
    )

    def once() -> str:
        return store.consume_bind_and_link(
            jti="race-jti",
            issuer=GOOGLE_ISSUER,
            subject="google-sub-race",
            email="ada@example.com",
            user_id="ada",
            now=1_900_000_000,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: once(), (0, 1)))
    assert results.count("ok") == 1
    assert _store(tmp_path).google_user_for_subject(GOOGLE_ISSUER, "google-sub-race") == "ada"


def test_explicit_new_google_account(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create makes a passwordless member. Cancel and a taken id change nothing."""
    response = _prepare(google_app, monkeypatch, _Identity())
    assert response.status_code == 302
    missing_origin = google_app.post("/auth/google/create")
    assert missing_origin.status_code == 403
    assert _store(tmp_path).get_user("jens@example.com") is None

    created = google_app.post("/auth/google/create", headers=_ORIGIN)
    assert created.status_code == 200, created.text
    token = _issued(created, "ap_session")
    assert token is not None
    assert jwt.decode(token, _secret(), algorithms=["HS256"])["sub"] == "jens@example.com"
    user = _store(tmp_path).get_user("jens@example.com")
    assert user is not None and user.has_password is False

    google_app.cookies.clear()
    again = _Identity()
    again.sub = "google-sub-taken"
    taken = _prepare(google_app, monkeypatch, again)
    assert taken.status_code == 409

    fresh = _Identity()
    fresh.email = "new@example.com"
    fresh.sub = "google-sub-new"
    fresh.hd = "example.com"
    pending = _prepare(google_app, monkeypatch, fresh)
    assert pending.status_code == 302
    cancelled = google_app.post("/auth/google/cancel", headers=_ORIGIN, follow_redirects=False)
    assert cancelled.status_code == 302
    assert _store(tmp_path).get_user("new@example.com") is None
    assert _store(tmp_path).google_user_for_email("new@example.com") is None
    google_app.cookies.clear()
    replay = _prepare(google_app, monkeypatch, fresh)
    assert replay.status_code == 302
    assert _issued(replay, "ap_google_bind") is not None


def test_callback_links_only_the_user_who_started(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A logged-in callback links only when that same user started Google."""
    _add_user(tmp_path, "jens", _JENS_PASSWORD)
    _add_user(tmp_path, "ada", "ada-password")
    google_app.post("/auth/login", json={"username": "jens", "password": _JENS_PASSWORD})
    linked = _prepare(google_app, monkeypatch, _Identity())
    assert linked.status_code == 302
    assert linked.headers["location"].endswith("/")
    assert _store(tmp_path).linked_email("jens") == "jens@example.com"
    me = google_app.get("/auth/me")
    assert me.json()["id"] == "jens"
    assert me.json()["linked_email"] == "jens@example.com"

    google_app.post("/auth/logout")
    assert _store(tmp_path).get_user("jens") is not None
    _store(tmp_path).unlink_google("jens")
    google_app.cookies.clear()
    state, nonce = _start(google_app)
    google_app.post("/auth/login", json={"username": "jens", "password": _JENS_PASSWORD})
    ident = _Identity()
    ident.nonce = nonce
    _install_google(monkeypatch, ident)
    anonymous = _callback(google_app, state)
    assert anonymous.status_code == 302
    assert anonymous.headers["location"].startswith("/login?")
    assert _store(tmp_path).linked_email("jens") is None

    google_app.cookies.clear()
    google_app.post("/auth/login", json={"username": "admin", "password": "admin-pw-12345"})
    admin_state, admin_nonce = _start(google_app)
    admin_cookie = _cookie_value(google_app, "ap_google_state")
    google_app.post("/auth/logout")
    google_app.post("/auth/login", json={"username": "jens", "password": _JENS_PASSWORD})
    google_app.cookies.set("ap_google_state", admin_cookie or "", path="/")
    ident.nonce = admin_nonce
    foreign = _callback(google_app, admin_state)
    assert foreign.status_code == 302
    assert foreign.headers["location"].startswith("/login?")
    assert _store(tmp_path).linked_email("jens") is None
    assert _store(tmp_path).linked_email("admin") is None

    google_app.post("/auth/logout")
    google_app.cookies.clear()
    pending = _prepare(google_app, monkeypatch, _Identity())
    assert pending.status_code == 302
    assert _bind(google_app, "ada", "ada-password").status_code == 200
    google_app.cookies.clear()
    google_app.post("/auth/login", json={"username": "jens", "password": _JENS_PASSWORD})
    occupied = _Identity()
    occupied.sub = "google-sub-other"
    conflict = _prepare(google_app, monkeypatch, occupied)
    assert conflict.status_code == 409
    assert conflict.json() == {"error": "already linked"}
    assert _store(tmp_path).google_user_for_email("jens@example.com") == "ada"
    assert google_app.get("/auth/me").json()["id"] == "jens"


def test_share_grant_follows_google_sign_in(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A grant to the email is invisible until bind, then both principals apply."""
    _add_user(tmp_path, "jens", _JENS_PASSWORD)
    email_only = _seed_session(tmp_path, "jens@example.com", LEVEL_READ)
    email_device = _seed_session(tmp_path, "jens@example.com", LEVEL_READ)
    owned = _seed_session(tmp_path, "jens", LEVEL_READ)
    public_id = _seed_session(tmp_path, RESERVED_USER_PUBLIC, LEVEL_READ)
    edit_id = _seed_session(tmp_path, "jens@example.com", LEVEL_EDIT, share_files=False)
    view_id = _seed_session(tmp_path, "jens@example.com", LEVEL_READ)

    pending = _prepare(google_app, monkeypatch, _Identity())
    assert pending.status_code == 302
    assert google_app.get("/v1/sessions").status_code == 401
    assert email_only not in _session_ids_as_other(google_app, tmp_path, monkeypatch)

    bound = _bind(google_app, "jens", _JENS_PASSWORD)
    assert bound.status_code == 200, bound.text
    visible = set(_session_ids(google_app))
    assert email_only in visible
    assert owned in visible

    blocked = google_app.get(
        f"/v1/sessions/{view_id}/resources/environments/default/search",
        params={"q": "a"},
    )
    assert blocked.status_code == 403
    _conversations(tmp_path).update_conversation(view_id, share_workspace_files=True)
    opened = google_app.get(
        f"/v1/sessions/{view_id}/resources/environments/default/search",
        params={"q": "a"},
    )
    assert opened.status_code != 403
    editable = google_app.get(
        f"/v1/sessions/{edit_id}/resources/environments/default/search",
        params={"q": "a"},
    )
    assert editable.status_code != 403

    left = google_app.delete(f"/v1/sessions/{email_only}/permissions/jens@example.com")
    assert left.status_code == 204, left.text
    assert _permissions(tmp_path).get("jens@example.com", email_only) is None
    assert _permissions(tmp_path).get("admin", email_only) is not None
    assert _permissions(tmp_path).get("jens@example.com", email_device) is not None
    after_leave = set(_session_ids(google_app))
    assert email_only not in after_leave
    assert email_device in after_leave
    assert owned in after_leave
    assert _permissions(tmp_path).get(RESERVED_USER_PUBLIC, public_id) is not None
    assert _permissions(tmp_path).check_access("bob", public_id, LEVEL_READ) is True

    google_app.cookies.clear()
    mixed = _Identity()
    mixed.email = "Jens@Example.com"
    mixed.sub = "google-sub-jens"
    mixed.hd = "example.com"
    repeat = _prepare(google_app, monkeypatch, mixed)
    assert repeat.status_code == 302
    assert jwt.decode(_issued(repeat, "ap_session") or "", _secret(), algorithms=["HS256"])[
        "sub"
    ] == "jens"

    refresh = google_app.post(
        "/auth/login",
        json={"username": "jens", "password": _JENS_PASSWORD, "issue_refresh": True},
    )
    assert refresh.status_code == 200, refresh.text
    token_response = google_app.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": refresh.json()["refresh_token"]},
    )
    assert token_response.status_code == 200, token_response.text
    access = token_response.json()["access_token"]
    assert jwt.decode(access, options={"verify_signature": False})["sub"] == "jens"
    google_app.cookies.clear()
    listed = google_app.get("/v1/sessions", headers={"authorization": f"Bearer {access}"})
    assert listed.status_code == 200, listed.text
    visible = {row["id"] for row in listed.json()["data"]}
    assert owned in visible
    assert email_device in visible


def test_leave_email_grant_keeps_username_grant(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Access is the higher of the two grants; leaving the email keeps the username's."""
    _add_user(tmp_path, "jens", _JENS_PASSWORD)
    session_id = _seed_session(tmp_path, "jens", LEVEL_READ)
    _permissions(tmp_path).grant("jens@example.com", session_id, LEVEL_EDIT)
    _prepare(google_app, monkeypatch, _Identity())
    assert _bind(google_app, "jens", _JENS_PASSWORD).status_code == 200

    search = f"/v1/sessions/{session_id}/resources/environments/default/search"
    assert google_app.get(search, params={"q": "a"}).status_code != 403

    left = google_app.delete(f"/v1/sessions/{session_id}/permissions/jens@example.com")
    assert left.status_code == 204, left.text
    assert _permissions(tmp_path).get("jens@example.com", session_id) is None
    remaining = _permissions(tmp_path).get("jens", session_id)
    assert remaining is not None and remaining.level == LEVEL_READ
    assert session_id in _session_ids(google_app)
    assert google_app.get(search, params={"q": "a"}).status_code == 403


def _session_ids_as_other(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> set[str]:
    """Sign in a different Google subject and return the sessions it can see."""
    _add_user(tmp_path, "ada", "ada-password")
    saved = list(client.cookies.jar)
    client.cookies.clear()
    other = _Identity()
    other.email = "ada@example.com"
    other.sub = "google-sub-ada-view"
    response = _prepare(client, monkeypatch, other)
    assert response.status_code == 302
    bound = _bind(client, "ada", "ada-password")
    assert bound.status_code == 200, bound.text
    visible = set(_session_ids(client))
    client.cookies.clear()
    for cookie in saved:
        client.cookies.set(cookie.name, cookie.value, path=cookie.path or "/")
    return visible


def test_unlink_removes_only_the_link(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unlink deletes the link row and leaves the user, password, and owner grant."""
    _add_user(tmp_path, "jens", _JENS_PASSWORD)
    owned = _seed_session(tmp_path, "jens", LEVEL_OWNER)
    shared = _seed_session(tmp_path, "jens@example.com", LEVEL_READ)
    _prepare(google_app, monkeypatch, _Identity())
    assert _bind(google_app, "jens", _JENS_PASSWORD).status_code == 200
    assert shared in _session_ids(google_app)

    removed = google_app.delete("/auth/google/link")
    assert removed.status_code == 204
    assert _store(tmp_path).get_user("jens") is not None
    assert _store(tmp_path).linked_email("jens") is None
    assert _permissions(tmp_path).get("jens", owned) is not None
    still = google_app.post("/auth/login", json={"username": "jens", "password": _JENS_PASSWORD})
    assert still.status_code == 200
    assert shared not in _session_ids(google_app)
    assert owned in _session_ids(google_app)

    google_app.cookies.clear()
    again = _prepare(google_app, monkeypatch, _Identity())
    assert again.status_code == 302
    assert again.headers["location"].startswith("/login?")

    google_app.cookies.clear()
    created = _Identity()
    created.email = "solo@example.com"
    created.sub = "google-sub-solo"
    assert _prepare(google_app, monkeypatch, created).status_code == 302
    assert google_app.post("/auth/google/create", headers=_ORIGIN).status_code == 200
    denied = google_app.delete("/auth/google/link")
    assert denied.status_code == 409
    assert _store(tmp_path).linked_email("solo@example.com") == "solo@example.com"

    google_app.cookies.clear()
    _add_user(tmp_path, "self@example.com", "self-password")
    named = _Identity()
    named.email = "self@example.com"
    named.sub = "google-sub-self"
    assert _prepare(google_app, monkeypatch, named).status_code == 302
    assert _bind(google_app, "self@example.com", "self-password").status_code == 200
    allowed = google_app.delete("/auth/google/link")
    assert allowed.status_code == 204
    assert _store(tmp_path).get_user("self@example.com") is not None
    assert _store(tmp_path).linked_email("self@example.com") is None

    missing = google_app.delete("/auth/google/link")
    assert missing.status_code == 404
    google_app.cookies.clear()
    assert google_app.delete("/auth/google/link").status_code == 401


def test_username_cannot_collide_with_linked_email(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A linked email cannot be registered or renamed onto another account."""
    _add_user(tmp_path, "jens", _JENS_PASSWORD)
    _prepare(google_app, monkeypatch, _Identity())
    assert _bind(google_app, "jens", _JENS_PASSWORD).status_code == 200

    google_app.post("/auth/logout")
    google_app.post("/auth/login", json={"username": "admin", "password": "admin-pw-12345"})
    invite = google_app.post("/auth/invite", json={"is_admin": False})
    assert invite.status_code == 200, invite.text
    token = invite.json()["token"]
    google_app.cookies.clear()
    collided = google_app.post(
        "/auth/register",
        json={"invite": token, "username": "jens@example.com", "password": "register-pass"},
    )
    assert collided.status_code == 409
    assert collided.json() == {"error": "username already taken"}
    created = google_app.post(
        "/auth/register",
        json={"invite": token, "username": "bob", "password": "register-pass"},
    )
    assert created.status_code == 200, created.text

    store = _store(tmp_path)
    with pytest.raises(ValueError, match="linked email"):
        store.rename_user("jens", "jens@example.com")
    with pytest.raises(ValueError, match="linked email"):
        store.create_user_with_password("jens@example.com", hash_password("another-password"))


def test_password_login_and_oidc_header_modes_unchanged(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Password login still signs the username in. OIDC and header hide the button."""
    assert resolve_auth_source() == "accounts"
    login = google_app.post(
        "/auth/login",
        json={"username": "admin", "password": "admin-pw-12345"},
    )
    assert login.status_code == 200, login.text
    me = google_app.get("/auth/me")
    assert me.json()["id"] == "admin"
    logout = google_app.post("/auth/logout", follow_redirects=False)
    assert logout.status_code in (200, 204, 302)
    assert google_app.get("/auth/me").status_code == 401

    for name in (
        "OMNIGENT_OIDC_CLIENT_ID",
        "OMNIGENT_OIDC_CLIENT_SECRET",
        "OMNIGENT_OIDC_REDIRECT_URI",
        "OMNIGENT_OIDC_ALLOWED_DOMAINS",
    ):
        monkeypatch.setenv(name, os.environ.get(name) or "example.com")
    monkeypatch.setenv("OMNIGENT_OIDC_CLIENT_SECRET", _SECRET)
    _assert_foreign_has_no_button(tmp_path, monkeypatch, "header")
    _assert_foreign_has_no_button(tmp_path, monkeypatch, "oidc")


def _finite_stream(seen: list[str | None]) -> object:
    """Stand-in for the live tail so an authorized SSE request returns."""

    async def stream(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        del args
        seen.append(kwargs.get("viewer_user_id"))  # type: ignore[arg-type]
        yield "data: [DONE]\n\n"

    return stream


def _watched_ids(client: TestClient, session_id: str) -> set[str]:
    with client.websocket_connect("/v1/sessions/updates") as ws:
        ws.send_json({"type": "watch", "session_ids": [session_id]})
        frame = ws.receive_json()
    assert frame["type"] == "snapshot"
    return {item["id"] for item in frame["items"]}


def test_linked_email_grant_authorizes_stream_and_updates_socket(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A grant on the linked email opens the SSE stream and the updates socket."""
    from omnigent.server.routes.sessions import routes_events

    seen: list[str | None] = []
    monkeypatch.setattr(routes_events, "_stream_live_events", _finite_stream(seen))
    _add_user(tmp_path, "jens", _JENS_PASSWORD)
    email_only = _seed_session(tmp_path, "jens@example.com", LEVEL_READ)
    assert _permissions(tmp_path).get("jens", email_only) is None

    _prepare(google_app, monkeypatch, _Identity())
    assert _bind(google_app, "jens", _JENS_PASSWORD).status_code == 200
    assert google_app.get("/auth/me").json()["id"] == "jens"

    stream = google_app.get(f"/v1/sessions/{email_only}/stream")
    assert stream.status_code == 200, stream.text
    assert seen == ["jens"]
    assert email_only in _watched_ids(google_app, email_only)

    google_app.cookies.clear()
    _add_user(tmp_path, "ada", "ada-password")
    other = _Identity()
    other.email = "ada@example.com"
    other.sub = "google-sub-ada"
    assert _prepare(google_app, monkeypatch, other).status_code == 302
    assert _bind(google_app, "ada", "ada-password").status_code == 200
    assert google_app.get("/auth/me").json()["id"] == "ada"

    denied = google_app.get(f"/v1/sessions/{email_only}/stream")
    assert denied.status_code in (403, 404)
    assert seen == ["jens"]
    assert email_only not in _watched_ids(google_app, email_only)


def test_accounts_logout_clears_cookie_without_google(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Logout is local: 204, the cookie is cleared, and Google is never called."""
    import httpx

    caplog.set_level("DEBUG")
    _add_user(tmp_path, "jens@example.com", None)
    ident = _Identity()
    signed_in = _prepare(google_app, monkeypatch, ident)
    assert signed_in.status_code == 302
    assert _issued(signed_in, "ap_session") is not None
    assert google_app.get("/auth/me").json()["id"] == "jens@example.com"

    outbound: list[str] = []

    def no_google(*args: object, **kwargs: object) -> str:
        del args, kwargs
        outbound.append("google_accounts")
        raise AssertionError("logout must not call Google")

    def no_sync(self: object, request: httpx.Request) -> httpx.Response:
        del self
        outbound.append(str(request.url))
        raise AssertionError(f"unexpected outbound request {request.url}")

    async def no_async(self: object, request: httpx.Request) -> httpx.Response:
        del self
        outbound.append(str(request.url))
        raise AssertionError(f"unexpected outbound request {request.url}")

    monkeypatch.setattr("omnigent.server.google_accounts.exchange_code", no_google)
    monkeypatch.setattr("omnigent.server.google_accounts.decode_id_token", no_google)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_sync)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_async)

    response = google_app.post("/auth/logout", headers=_ORIGIN, follow_redirects=False)
    assert response.status_code == 204
    cleared = [raw for raw in _set_cookie_headers(response) if "ap_session=" in raw]
    assert cleared, _set_cookie_headers(response)
    assert all("Max-Age=0" in raw or "expires=Thu, 01 Jan 1970" in raw for raw in cleared)
    assert _issued(response, "ap_session") is None
    assert google_app.get("/auth/me").status_code == 401
    assert outbound == []
    assert not any("accounts.google.com" in url for url in outbound)
    _assert_clean(signed_in, caplog, ident)
    _assert_clean(response, caplog, ident)
