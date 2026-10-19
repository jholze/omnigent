"""Desktop native sign-in on an accounts-mode server with Google.

The desktop app opens the system browser at ``GET /auth/login`` and
exchanges the loopback code at ``POST /auth/native-token``. These tests
cover that handoff, the public-host bounce, and the manifest the shell
reads before it chooses the flow.
"""

from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from fastapi.testclient import TestClient

from omnigent.server.auth import UnifiedAuthProvider
from omnigent.server.native_signin import NATIVE_APP_REDIRECT_URIS
from omnigent.server.oidc import derive_code_challenge
from tests.server.integration.test_well_known_auth import _app, _oidc_config
from tests.server.test_accounts import accounts_app as accounts_app
from tests.server.test_accounts_google import (
    _add_user,
    _callback,
    _Identity,
    _install_google,
    _secret,
    _start,
)
from tests.server.test_accounts_google import google_app as google_app

_VERIFIER = "v" * 64
_STATE = "desktop-state-1"
_REDIRECT = "http://127.0.0.1:53682/callback"
_PUBLIC = "https://pub.example/auth/google/callback"


def _native_params(**overrides: str) -> dict[str, str]:
    params = {
        "native_redirect_uri": _REDIRECT,
        "native_state": _STATE,
        "code_challenge": derive_code_challenge(_VERIFIER),
        "code_challenge_method": "S256",
    }
    params.update(overrides)
    return params


def _set_cookie(response: object, name: str) -> str:
    raw = response.headers.get("set-cookie", "")  # type: ignore[attr-defined]
    assert name in raw, raw
    return raw


def _exchange(client: TestClient, **form: str) -> object:
    return client.post("/auth/native-token", data=form)


def test_google_start_bounces_off_loopback_without_a_state_cookie(
    google_app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A start on 127.0.0.1 must land on the host Google redirects to."""
    monkeypatch.setenv("OMNIGENT_OIDC_REDIRECT_URI", _PUBLIC)
    response = google_app.get(
        "/auth/google/start",
        params={"from": "desktop"},
        headers={
            "host": "127.0.0.1:6767",
            "x-forwarded-proto": "",
            "x-forwarded-host": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302, response.text
    assert response.headers["location"] == "https://pub.example/auth/google/start?from=desktop"
    assert "ap_google_state" not in response.headers.get("set-cookie", "")


def test_google_start_on_the_redirect_host_sets_the_state_cookie(google_app: TestClient) -> None:
    state, _nonce = _start(google_app)
    assert state
    assert "ap_google_state" in google_app.cookies


def test_google_start_trusts_forwarded_public_origin(
    google_app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ngrok hop is the redirect host even when the socket is loopback."""
    monkeypatch.setenv("OMNIGENT_OIDC_REDIRECT_URI", _PUBLIC)
    response = google_app.get(
        "/auth/google/start",
        headers={"x-forwarded-proto": "https", "x-forwarded-host": "pub.example"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "ap_google_state" in response.headers.get("set-cookie", "")


def test_get_login_sets_native_cookie_and_shows_the_login_page(google_app: TestClient) -> None:
    response = google_app.get("/auth/login", params=_native_params(), follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login?native=1"
    raw = _set_cookie(response, "ap_native")
    assert "httponly" in raw.lower()
    assert "samesite=lax" in raw.lower()
    assert "path=/auth" in raw.lower()
    assert "secure" not in raw.lower()
    payload = jwt.decode(google_app.cookies["ap_native"], _secret(), algorithms=["HS256"])
    assert payload["redirect_uri"] == _REDIRECT
    assert payload["state"] == _STATE
    assert payload["exp"] - payload["iat"] == 600


def test_get_login_bounces_to_the_redirect_origin(
    google_app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNIGENT_OIDC_REDIRECT_URI", _PUBLIC)
    params = _native_params()
    response = google_app.get(
        "/auth/login",
        params=params,
        headers={
            "host": "127.0.0.1:6767",
            "x-forwarded-proto": "",
            "x-forwarded-host": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    location = urlsplit(response.headers["location"])
    assert (
        f"{location.scheme}://{location.netloc}{location.path}" == "https://pub.example/auth/login"
    )
    assert parse_qs(location.query) == {key: [value] for key, value in params.items()}
    assert "ap_native" not in response.headers.get("set-cookie", "")


def test_get_login_without_native_params_redirects_to_the_spa(google_app: TestClient) -> None:
    response = google_app.get("/auth/login", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"
    assert "ap_native" not in response.headers.get("set-cookie", "")


@pytest.mark.parametrize(
    "override",
    [
        {"native_redirect_uri": "http://evil.com/callback"},
        {"native_redirect_uri": "https://127.0.0.1:53682/callback"},
        {"native_redirect_uri": "http://127.0.0.1:53682/callback?x=1"},
        {"native_redirect_uri": "ai.omnigent.ios:/oauth/wrong"},
        {"native_state": "has space"},
        {"native_state": ""},
        {"code_challenge_method": "plain"},
        {"code_challenge": "too-short"},
    ],
)
def test_get_login_rejects_invalid_native_parameters(
    google_app: TestClient, override: dict[str, str]
) -> None:
    response = google_app.get("/auth/login", params=_native_params(**override))
    assert response.status_code == 400
    assert response.json() == {"error": "Invalid native sign-in parameters"}


def test_google_callback_issues_a_native_code(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _add_user(tmp_path, "jens@example.com", None)
    login = google_app.get("/auth/login", params=_native_params(), follow_redirects=False)
    assert login.status_code == 302
    state, nonce = _start(google_app)
    ident = _Identity()
    ident.nonce = nonce
    _install_google(monkeypatch, ident)
    response = _callback(google_app, state)
    assert response.status_code == 302, response.text
    location = urlsplit(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == _REDIRECT
    query = parse_qs(location.query)
    assert query["state"] == [_STATE]
    assert query["code"][0]
    assert "ap_native=" in response.headers.get("set-cookie", "")
    assert "ap_session=" in response.headers.get("set-cookie", "")

    google_app.cookies.clear()
    exchanged = _exchange(
        google_app,
        code=query["code"][0],
        code_verifier=_VERIFIER,
        redirect_uri=_REDIRECT,
    )
    assert exchanged.status_code == 200, exchanged.text  # type: ignore[attr-defined]
    body = exchanged.json()  # type: ignore[attr-defined]
    assert body["user_id"] == "jens@example.com"
    assert body["expires_in"] > 0
    assert isinstance(body["refresh_token"], str)
    assert exchanged.headers["cache-control"] == "no-store"  # type: ignore[attr-defined]
    token = body["token"]
    claims = jwt.decode(token, _secret(), algorithms=["HS256"])
    assert claims["sub"] == "jens@example.com"
    assert claims["provider"] == "accounts"
    assert claims["sid"]
    assert isinstance(claims["account_generation"], str)

    google_app.cookies.clear()
    bearer = google_app.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert bearer.status_code == 200
    assert bearer.json()["id"] == "jens@example.com"
    info = google_app.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert info.status_code == 200
    assert info.json()["user_id"] == "jens@example.com"
    google_app.cookies.set("ap_session", token)
    cookie = google_app.get("/auth/me")
    assert cookie.status_code == 200
    assert cookie.json()["id"] == "jens@example.com"

    replay = _exchange(
        google_app,
        code=query["code"][0],
        code_verifier=_VERIFIER,
        redirect_uri=_REDIRECT,
    )
    assert replay.status_code == 400  # type: ignore[attr-defined]
    assert replay.json() == {"error": "invalid_grant"}  # type: ignore[attr-defined]


def test_native_exchange_rejects_a_wrong_verifier_and_burns_the_code(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code = _native_code(google_app, tmp_path, monkeypatch)
    bad = _exchange(google_app, code=code, code_verifier="w" * 64, redirect_uri=_REDIRECT)
    assert bad.status_code == 400  # type: ignore[attr-defined]
    assert bad.json() == {"error": "invalid_grant"}  # type: ignore[attr-defined]
    retry = _exchange(google_app, code=code, code_verifier=_VERIFIER, redirect_uri=_REDIRECT)
    assert retry.json() == {"error": "invalid_grant"}  # type: ignore[attr-defined]


def test_native_exchange_rejects_a_redirect_mismatch(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code = _native_code(google_app, tmp_path, monkeypatch)
    mismatch = _exchange(
        google_app,
        code=code,
        code_verifier=_VERIFIER,
        redirect_uri="http://127.0.0.1:53683/callback",
    )
    assert mismatch.status_code == 400  # type: ignore[attr-defined]
    assert mismatch.json() == {"error": "invalid_grant"}  # type: ignore[attr-defined]
    retry = _exchange(google_app, code=code, code_verifier=_VERIFIER, redirect_uri=_REDIRECT)
    assert retry.json() == {"error": "invalid_grant"}  # type: ignore[attr-defined]


def test_native_code_expires(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": time.time()}
    monkeypatch.setattr("omnigent.server.native_signin.time.time", lambda: clock["now"])
    code = _native_code(google_app, tmp_path, monkeypatch)
    clock["now"] += 61
    expired = _exchange(google_app, code=code, code_verifier=_VERIFIER, redirect_uri=_REDIRECT)
    assert expired.status_code == 400  # type: ignore[attr-defined]
    assert expired.json() == {"error": "invalid_grant"}  # type: ignore[attr-defined]


def test_password_login_includes_native_redirect_only_with_the_cookie(
    accounts_app: TestClient,
) -> None:
    plain = accounts_app.post(
        "/auth/login", json={"username": "admin", "password": "admin-pw-12345"}
    )
    assert plain.status_code == 200, plain.text
    assert "native_redirect" not in plain.json()
    assert "token" in plain.json()
    assert "ap_session" in plain.headers.get("set-cookie", "")
    token = plain.json()["token"]
    accounts_app.cookies.clear()
    bearer = accounts_app.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert bearer.status_code == 200
    assert bearer.json()["user_id"] == "admin"

    accounts_app.cookies.clear()
    started = accounts_app.get("/auth/login", params=_native_params(), follow_redirects=False)
    assert started.status_code == 302
    signed_in = accounts_app.post(
        "/auth/login", json={"username": "admin", "password": "admin-pw-12345"}
    )
    assert signed_in.status_code == 200, signed_in.text
    native = signed_in.json()["native_redirect"]
    query = parse_qs(urlsplit(native).query)
    assert urlsplit(native).path == "/callback"
    assert query["state"] == [_STATE]
    assert "ap_native=" in signed_in.headers.get("set-cookie", "")


def test_refresh_token_from_native_sign_in_redeems_at_oauth_token(
    google_app: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code = _native_code(google_app, tmp_path, monkeypatch)
    google_app.cookies.clear()
    exchanged = _exchange(google_app, code=code, code_verifier=_VERIFIER, redirect_uri=_REDIRECT)
    refresh = exchanged.json()["refresh_token"]  # type: ignore[attr-defined]
    redeemed = google_app.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": refresh},
    )
    assert redeemed.status_code == 200, redeemed.text
    access = redeemed.json()["access_token"]
    assert redeemed.json()["refresh_token"] == refresh
    google_app.cookies.clear()
    me = google_app.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    assert me.json()["id"] == "jens@example.com"
    revoked = google_app.post("/oauth/revoke", data={"token": refresh})
    assert revoked.status_code == 200


def test_manifest_accounts_with_google_advertises_the_native_handoff(
    google_app: TestClient,
) -> None:
    manifest = google_app.get("/.well-known/omnigent.json")
    assert manifest.status_code == 200
    auth = manifest.json()["auth"]
    assert auth["mode"] == "oidc"
    assert auth["session_cookie"] == "ap_session"
    assert auth["native_redirect_uris"] == sorted(NATIVE_APP_REDIRECT_URIS)
    info = google_app.get("/v1/info")
    assert info.json()["accounts_enabled"] is True


def test_manifest_accounts_without_google_stays_accounts(accounts_app: TestClient) -> None:
    started = accounts_app.get("/auth/login", follow_redirects=False)
    assert started.status_code == 302
    assert started.headers["location"] == "/login"
    auth = accounts_app.get("/.well-known/omnigent.json").json()["auth"]
    assert auth["mode"] == "accounts"
    assert auth["session_cookie"] == "ap_session"
    assert auth["native_redirect_uris"] is None


def test_oidc_manifest_and_login_are_unchanged(
    runtime_init: None, db_uri: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNIGENT_OIDC_ISSUER", "https://github.com")
    monkeypatch.setenv("OMNIGENT_OIDC_CLIENT_ID", "cid")
    monkeypatch.setenv("OMNIGENT_OIDC_CLIENT_SECRET", "secret")
    monkeypatch.setenv("OMNIGENT_OIDC_REDIRECT_URI", "http://localhost:8000/auth/callback")
    monkeypatch.setenv("OMNIGENT_OIDC_ALLOWED_DOMAINS", "example.com")
    app = _app(
        db_uri,
        tmp_path,
        UnifiedAuthProvider(
            source="oidc", oidc_config=_oidc_config("http://localhost:8000/auth/callback")
        ),
    )
    with TestClient(app) as client:
        auth = client.get("/.well-known/omnigent.json").json()["auth"]
        assert auth == {
            "mode": "oidc",
            "session_cookie": "ap_session",
            "native_redirect_uris": [
                "ai.omnigent.android:/oauth/callback",
                "ai.omnigent.ios:/oauth/callback",
            ],
        }
        login = client.get("/auth/login", follow_redirects=False)
        assert login.status_code == 302
        assert "github.com/login/oauth/authorize" in login.headers["location"]


def test_bind_and_create_accept_the_redirect_origin(
    google_app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNIGENT_OIDC_REDIRECT_URI", _PUBLIC)
    for path in ("/auth/google/bind", "/auth/google/create", "/auth/google/cancel"):
        allowed = google_app.post(path, headers={"origin": "https://pub.example"})
        assert allowed.status_code != 403, path
        foreign = google_app.post(path, headers={"origin": "https://evil.example"})
        assert foreign.status_code == 403, path


def _native_code(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    _add_user(tmp_path, "jens@example.com", None)
    started = client.get("/auth/login", params=_native_params(), follow_redirects=False)
    assert started.status_code == 302
    state, nonce = _start(client)
    ident = _Identity()
    ident.nonce = nonce
    _install_google(monkeypatch, ident)
    response = _callback(client, state)
    assert response.status_code == 302, response.text
    return parse_qs(urlsplit(response.headers["location"]).query)["code"][0]
