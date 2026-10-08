"""A long model catalog keeps the composer's Models flyout compact and scrollable."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Locator, Page, Route, async_playwright, expect

from tests._helpers.async_thread import run_in_fresh_loop
from tests.e2e_ui.start_session.test_start_session import (
    _HOST_ID,
    _codex_native_agents_body,
    _open_entry_models,
    _register_common_routes,
)

# The flyout caps at 24rem like the task popovers, or at the viewport when shorter.
_COMPACT_CAP_PX = 384
_MODEL_OPTIONS = [
    {
        "id": f"catalog-model-{index:02d}",
        "model": f"system.ai.catalog-model-{index:02d}",
        "displayName": f"Catalog Model {index:02d}",
        "isDefault": index == 1,
    }
    for index in range(1, 33)
]
_LAST_MODEL = _MODEL_OPTIONS[-1]


@pytest.mark.parametrize("viewport_height", [600, 340], ids=["desktop", "short"])
@pytest.mark.parametrize("surface", ["new-session", "existing-session"])
def test_long_catalog_models_flyout_stays_compact_and_scrollable(
    seeded_session: tuple[str, str], surface: str, viewport_height: int
) -> None:
    run_in_fresh_loop(_drive(*seeded_session, surface, viewport_height))


async def _drive(base_url: str, session_id: str, surface: str, viewport_height: int) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        viewport = {"width": 1280, "height": viewport_height}
        recording = bool(os.environ.get("OMNIGENT_E2E_RECORD_DIR"))
        context = await browser.new_context(
            viewport=viewport, **({"record_video_size": viewport} if recording else {})
        )
        page = await context.new_page()
        patch_bodies: list[dict[str, Any]] = []
        try:
            await _register_common_routes(
                page,
                created_session_id=session_id,
                create_bodies=[],
                agents_body=_codex_native_agents_body(),
            )
            await page.route(
                re.compile(r"/v1/sessions\?(?!.*pinned=).*visibility=mine"),
                lambda route: route.fulfill(json={"data": []}),
            )
            await page.route(
                f"**/v1/hosts/{_HOST_ID}/harnesses/codex-native/model-options",
                lambda route: route.fulfill(json={"models": _MODEL_OPTIONS}),
            )
            await page.route(
                re.compile(rf"/v1/sessions/{session_id}(?:\?.*)?$"),
                _codex_native_snapshot(patch_bodies),
            )
            await page.add_init_script(
                "localStorage.setItem('omnigent:recent-workspaces', "
                f"JSON.stringify({{{_HOST_ID}: ['/work/repo']}}))"
            )
            if surface == "new-session":
                await page.goto(base_url)
                await _open_entry_models(page, "ag_codex_e2e")
                models = page.get_by_test_id("new-chat-landing-agent-models")
            else:
                await page.goto(f"{base_url}/c/{session_id}")
                await page.get_by_test_id("composer-config-gear").click()
                await page.get_by_test_id("composer-agent-edit").click()
                models = page.get_by_test_id("composer-agent-models")
            await expect(models).to_be_visible()
            flyout = models.locator('xpath=ancestor::*[@role="menu"]')
            # Rejects if a late re-layout retargets the open animation or a transition.
            await flyout.evaluate(
                "el => Promise.all(el.getAnimations().map(animation => animation.finished))"
            )
            box = await flyout.bounding_box()
            assert box is not None
            await _capture(page, f"{surface}-{viewport_height}-flyout-open", hold=recording)
            assert box["y"] >= 0 and box["y"] + box["height"] <= viewport_height, box

            last = flyout.get_by_role(
                "menuitemcheckbox", name=_LAST_MODEL["displayName"], exact=True
            )
            await _wheel_until_visible(page, flyout, last)
            if recording:
                await page.wait_for_timeout(800)
            await last.click()
            if surface == "new-session":
                await expect(last).to_have_attribute("aria-checked", "true")
            else:
                # Codex sessions stay pending until the harness reports the switch,
                # so the accepted pick is the PATCH the composer sent.
                for _ in range(50):
                    if patch_bodies:
                        break
                    await page.wait_for_timeout(100)
                assert patch_bodies[-1:] == [{"model_override": _LAST_MODEL["id"]}], patch_bodies
            await _capture(
                page, f"{surface}-{viewport_height}-last-model-selected", hold=recording
            )

            assert box["height"] <= min(_COMPACT_CAP_PX, viewport_height), (
                f"{surface} Models flyout is {box['height']:.0f}px tall in a "
                f"{viewport_height}px viewport ({box['height'] / viewport_height:.0%}); "
                f"expected a compact cap of at most {_COMPACT_CAP_PX}px"
            )
        finally:
            await page.unroute_all(behavior="wait")
            await context.close()
            await browser.close()


async def _capture(page: Page, name: str, *, hold: bool = False) -> None:
    """Save optional local captures outside the tracked source tree; hold for video."""
    if directory := os.environ.get("E2E_SCREENSHOT_DIR"):
        await page.screenshot(path=Path(directory) / f"{name}.png", animations="disabled")
    if hold:
        await page.wait_for_timeout(1500)


async def _wheel_until_visible(page: Page, flyout: Locator, row: Locator) -> None:
    """Mouse-wheel inside the flyout until ``row`` is fully inside its scroll box."""
    await flyout.hover()
    for _ in range(40):
        if await row.evaluate(
            "el => { const r = el.getBoundingClientRect();"
            " const p = el.closest('[role=\"menu\"]').getBoundingClientRect();"
            " return r.top >= p.top && r.bottom <= p.bottom; }"
        ):
            return
        await page.mouse.wheel(0, 120)
        await page.wait_for_timeout(50)
    await expect(row).to_be_in_viewport()


def _codex_native_snapshot(patch_bodies: list[dict[str, Any]]):
    """Patch the seeded session's snapshot into a codex-native session with the long catalog."""
    wire_by_id = {model["id"]: model["model"] for model in _MODEL_OPTIONS}
    state: dict[str, Any] = {"model_override": _MODEL_OPTIONS[0]["id"], "latest": {}}

    async def handle(route: Route) -> None:
        request = route.request
        if request.method == "GET":
            response = await route.fetch()
            body = await response.json()
        elif request.method == "PATCH":
            response = None
            patch = json.loads(request.post_data or "{}")
            patch_bodies.append(patch)
            if patch.get("model_override") in wire_by_id:
                state["model_override"] = patch["model_override"]
            body = dict(state["latest"])
        else:
            await route.fallback()
            return
        body.update(
            harness="codex-native",
            model_override=state["model_override"],
            llm_model=wire_by_id[state["model_override"]],
            model_options=_MODEL_OPTIONS,
        )
        body["labels"] = {**body.get("labels", {}), "omnigent.wrapper": "codex-native-ui"}
        state["latest"] = body
        if response is None:
            await route.fulfill(json=body)
        else:
            await route.fulfill(response=response, json=body)

    return handle
