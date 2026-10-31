"""Continue with Google on an accounts-mode server, in a real browser.

A verified Google email that matches no username lands on the bind panel
and is not signed in. Entering an existing username and its password there
signs that same username in.

Google is never contacted. A Playwright route follows ``/auth/google/start``
and sends the browser straight to the callback, and the server process
starts with the code exchange and ``id_token`` check replaced by a decoder
of that test code.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Iterator
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from playwright.sync_api import Page, Route, expect

from tests.e2e_ui.auth._accounts_server import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    AccountsServer,
    spawn_accounts_server,
)

_GOOGLE_EMAIL = "jens@example.com"
_USERNAME = "jens"
_PASSWORD = "jens-e2e-password-1"

# Runs in the server process before main(): the test code is the id_token,
# and the id_token is base64 JSON claims. No request leaves the machine.
_STUB_GOOGLE = """
import base64 as _b64, json as _json
import omnigent.server.google_accounts as _g
def _exchange(*, code, **_kw):
    return code
def _decode(id_token, *, client_id):
    claims = _json.loads(_b64.urlsafe_b64decode(id_token.encode()))
    claims.update(iss=_g.GOOGLE_ISSUER, aud=client_id)
    return claims
_g.exchange_code = _exchange
_g.decode_id_token = _decode
"""


@pytest.fixture(scope="module")
def google_server(
    built_spa: None,
    mock_llm_server_url: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[AccountsServer]:
    """Accounts server with the four Google variables and a user ``jens``."""
    server_tmp = tmp_path_factory.mktemp("e2e_ui_google_bind")
    env = {
        "OMNIGENT_OIDC_CLIENT_ID": "e2e-google-client",
        "OMNIGENT_OIDC_CLIENT_SECRET": "e2e-google-secret",
        "OMNIGENT_OIDC_REDIRECT_URI": "{public_url}/auth/google/callback",
        "OMNIGENT_OIDC_ALLOWED_DOMAINS": "example.com",
        "OMNIGENT_OIDC_ISSUER": "",
    }
    for server in spawn_accounts_server(
        mock_llm_server_url, server_tmp, extra_env=env, preamble=_STUB_GOOGLE
    ):
        _register_user(server)
        yield server


def _register_user(server: AccountsServer) -> None:
    """Create ``jens`` with a password through an admin invite."""
    with httpx.Client(base_url=server.base_url, timeout=10.0) as admin:
        login = admin.post(
            "/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
        )
        assert login.status_code == 200, login.text
        invite = admin.post("/auth/invite", json={"is_admin": False})
        assert invite.status_code == 200, invite.text
        token = invite.json()["token"]
    with httpx.Client(base_url=server.base_url, timeout=10.0) as anon:
        created = anon.post(
            "/auth/register",
            json={"invite": token, "username": _USERNAME, "password": _PASSWORD},
        )
        assert created.status_code == 200, created.text


def _skip_google(route: Route, server: AccountsServer) -> None:
    """Follow ``/auth/google/start`` and return straight to the callback.

    The browser hits the public loopback alias. A fetch of the same path on
    ``127.0.0.1`` does not match that origin, so the server bounces back to
    the alias and does not set the state cookie. Sending the public ``Host``
    on the loopback fetch makes the start handler redirect to Google.
    The browser is then sent to the callback instead of Google's consent page.
    """
    parts = urlsplit(route.request.url)
    public_host = urlsplit(server.public_url).netloc
    start_url = f"{server.base_url}{parts.path}"
    if parts.query:
        start_url = f"{start_url}?{parts.query}"
    started = httpx.get(
        start_url,
        headers={"Host": public_host},
        follow_redirects=False,
        timeout=10.0,
    )
    location = started.headers["location"]
    assert location.startswith("https://accounts.google.com/"), location
    query = parse_qs(urlsplit(location).query)
    claims = {
        "email": _GOOGLE_EMAIL,
        "email_verified": True,
        "sub": "google-sub-e2e-jens",
        "hd": "example.com",
        "nonce": query["nonce"][0],
    }
    code = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode()
    target = f"{query['redirect_uri'][0]}?{urlencode({'code': code, 'state': query['state'][0]})}"
    headers = {"location": target}
    cookies = started.headers.get_list("set-cookie")
    if cookies:
        headers["set-cookie"] = cookies[0]
    route.fulfill(status=302, headers=headers)


def _me(page: Page) -> tuple[int, str | None]:
    """``/auth/me`` from inside the page, with the browser's own cookies."""
    status, user = page.evaluate(
        """async () => {
            const r = await fetch("/auth/me", { cache: "no-store" });
            return [r.status, r.ok ? (await r.json()).id : null];
        }"""
    )
    return status, user


def test_google_unmatched_email_binds_with_password(
    google_server: AccountsServer, page: Page
) -> None:
    """Continue with Google, stay on the bind panel, then sign in as ``jens``."""
    google_hits: list[str] = []
    page.route(
        re.compile(r"^https://([a-z0-9-]+\.)*(google|googleapis)\.com/"),
        lambda route: (google_hits.append(route.request.url), route.abort()),
    )
    page.route(
        re.compile(r"/auth/google/start(\?|$)"),
        lambda route: _skip_google(route, google_server),
    )
    page.goto(f"{google_server.public_url}/login")

    page.get_by_role("link", name="Continue with Google").click()

    expect(page).to_have_url(re.compile(r"/login\?google_email="), timeout=15_000)
    panel = page.locator("#root").get_by_text(f"Google confirmed {_GOOGLE_EMAIL}.")
    expect(panel).to_be_visible(timeout=15_000)
    assert _me(page)[0] == 401

    page.locator("#login-username").fill(_USERNAME)
    page.locator("#login-password").fill(_PASSWORD)
    page.get_by_role("button", name="Sign in").click()

    expect(page).not_to_have_url(re.compile(r"/login"), timeout=15_000)
    expect(page.locator('[data-testid="sidebar-brand"]')).to_be_visible(timeout=15_000)
    assert _me(page) == (200, _USERNAME)
    assert google_hits == []
