"""E2E: Settings → Agenten issues an agent account's credentials once (T-121, FR-061).

Jens creates "Grok" with a unique ``grok-jens-…`` handle. The dialog shows
an ``ogc_`` token and a secret once; after "Schließen" neither is in the DOM,
also not after a reload. Credential values stay in Python memory and are
checked by predicates, so no assertion message or locator error prints them.
The account is revoked at teardown.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import httpx
import pytest
from playwright.sync_api import Page, expect

from tests.e2e_ui.team_chat.conftest import (
    JENS,
    TeamChatServer,
    fetch_bootstrap,
    identity_headers,
    revoke_agent_account,
)
from tests.e2e_ui.team_chat.test_viewports import (
    DESKTOP,
    PHONE,
    _assert_no_horizontal_scroll,
    _assert_touch_target,
)

_VIEWPORTS = pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1280x800", "390x844"])
_SECTION = "agent-accounts-section"
_TOKEN = "agent-account-token"
_SECRET = "agent-account-secret"
_ROW = "agent-account-row"
_LOAD_TIMEOUT_MS = 30_000
_IN_DOM = "value => document.documentElement.outerHTML.includes(value)"


@pytest.fixture
def grok_handle(team_chat_server: TeamChatServer) -> Iterator[str]:
    """A fresh ``grok-jens-…`` handle; any account Jens made with it is revoked after."""
    fetch_bootstrap(team_chat_server, as_user=JENS)
    handle = f"grok-jens-{uuid.uuid4().hex[:6]}"
    yield handle
    resp = httpx.get(
        f"{team_chat_server.base_url}/v1/chat/agent-accounts",
        headers=identity_headers(JENS),
        timeout=10.0,
        trust_env=False,
    )
    resp.raise_for_status()
    for account in resp.json():
        if account["handle"] == handle:
            revoke_agent_account(team_chat_server, as_user=JENS, account_id=account["id"])


def _open_agent_settings(page: Page, server: TeamChatServer) -> None:
    page.goto(f"{server.base_url}/settings/agent-accounts")
    expect(page.get_by_test_id(_SECTION)).to_be_visible(timeout=_LOAD_TIMEOUT_MS)


@_VIEWPORTS
def test_ac_061a_create_grok_shows_ogc_token_and_secret_once(
    team_chat_server: TeamChatServer, page_as, grok_handle: str, viewport: dict[str, int]
) -> None:
    """Jens creates "Grok": ogc_ token and secret show once and are gone after closing."""
    page = page_as(JENS, viewport=viewport)
    _open_agent_settings(page, team_chat_server)
    section = page.get_by_test_id(_SECTION)
    section.get_by_label("Anzeigename").fill("Grok")
    section.get_by_label("Handle").fill(grok_handle)
    section.get_by_role("button", name="Anlegen").click()

    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    token_el, secret_el = page.get_by_test_id(_TOKEN), page.get_by_test_id(_SECRET)
    expect(token_el).to_have_count(1)
    expect(secret_el).to_have_count(1)
    token, secret = token_el.inner_text().strip(), secret_el.inner_text().strip()
    # Booleans first: pytest's assertion rewrite would print the values.
    token_has_prefix = token.startswith("ogc_")
    secret_present = len(secret) >= 16
    assert token_has_prefix, "the token does not start with ogc_"
    assert secret_present, "the secret is missing or too short"
    row = section.get_by_test_id(_ROW).filter(has_text=f"@{grok_handle}")
    expect(row).to_have_count(1)
    _assert_no_horizontal_scroll(page)

    dialog.get_by_role("button", name="Schließen").first.click()
    expect(dialog).to_have_count(0, timeout=_LOAD_TIMEOUT_MS)
    expect(page.get_by_test_id(_TOKEN)).to_have_count(0)
    expect(page.get_by_test_id(_SECRET)).to_have_count(0)
    token_left = page.evaluate(_IN_DOM, token)
    secret_left = page.evaluate(_IN_DOM, secret)
    assert not token_left, "the token is still in the page after closing"
    assert not secret_left, "the secret is still in the page after closing"

    page.reload()
    reloaded_row = page.get_by_test_id(_SECTION).get_by_test_id(_ROW)
    expect(reloaded_row.filter(has_text=f"@{grok_handle}")).to_have_count(
        1, timeout=_LOAD_TIMEOUT_MS
    )
    expect(page.get_by_test_id(_TOKEN)).to_have_count(0)
    token_left = page.evaluate(_IN_DOM, token)
    secret_left = page.evaluate(_IN_DOM, secret)
    assert not token_left, "the token is in the page after a reload"
    assert not secret_left, "the secret is in the page after a reload"


@pytest.mark.xfail(
    strict=True,
    reason="Settings → Agenten buttons use size sm (32px) on phones; NFR-032 needs 44px",
)
def test_nfr032_phone_390_agent_accounts_controls_are_44px(
    team_chat_server: TeamChatServer, page_as, grok_handle: str
) -> None:
    """At 390x844 "Anlegen", the dialog's "Schließen" and the row actions are 44px."""
    page = page_as(JENS, viewport=PHONE)
    _open_agent_settings(page, team_chat_server)
    section = page.get_by_test_id(_SECTION)
    section.get_by_label("Anzeigename").fill("Grok")
    section.get_by_label("Handle").fill(grok_handle)
    create = section.get_by_role("button", name="Anlegen")
    _assert_touch_target(create, "Anlegen")
    create.click()
    dialog = page.get_by_role("dialog")
    close = dialog.get_by_role("button", name="Schließen").first
    _assert_touch_target(close, "Schließen")
    close.click()
    row = section.get_by_test_id(_ROW).filter(has_text=f"@{grok_handle}")
    _assert_touch_target(row.get_by_role("button", name="Token rotieren"), "Token rotieren")
    _assert_touch_target(row.get_by_role("button", name="Widerrufen"), "Widerrufen")
    _assert_no_horizontal_scroll(page)
