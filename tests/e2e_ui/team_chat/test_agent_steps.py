"""E2E: agent steps in an open Agent-Thread, in real browsers (T-115, T-121).

The agent's plan is seeded the way a native forwarder reports it: one
``external_session_todos`` event per change, carrying the whole todo list,
through the same ``POST /events`` path ``test_agent_thread.py`` uses for status.
The thread's session stream delivers it to the open page. Durations are
measured in the tab from the first in-progress to the first completed sighting,
so each test steps through the plan and waits for the browser to see each step.

- AC-048b: at "Tests ausführen" the expanded list shows checks with mono
  durations, a spinning arc with a shimmer label, and a dot.
- AC-090a: moving to step 3 announces "Tests ausführen, Schritt 3 von 4"
  within 600 ms, the row has ``aria-current="step"``, and the finished plan
  says "Lauf abgeschlossen".
- AC-090b: ``prefers-reduced-motion: reduce``, active to done runs no
  transform and nothing over 120 ms; the arc does not turn, no shimmer.
- AC-059a: an ``ExitPlanMode`` permission request, parked on the thread's
  session through the real hook route, shows ``plan-review`` in the thread;
  EDIT approves, READ sees it disabled.
- AC-060a/b: the PR card reads ``GET /v1/sessions/{id}/resources/github``,
  stubbed in the page (as ``tests/e2e_ui/gitlab/test_gitlab_panel.py`` does);
  the snapshot gets a workspace so the card asks. No GitHub call.

Each case runs at 1280x800 and 390x844 (NFR-032). Review screenshots go to
``/tmp/omnigent-chat-ux/T-115/`` as ``{surface}-{state}-{viewport}.png``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Locator, Page, Route, expect

from tests.e2e_ui.conftest import fetch_with_retry
from tests.e2e_ui.team_chat.conftest import ANNA, TeamChatServer
from tests.e2e_ui.team_chat.test_agent_thread import (
    _READ_ONLY_NOTE,
    BOB,
    AgentThread,
    _agent_channel,
    _agent_event,
    _open_channel,
    _open_live_thread,
    _parked_approval,
    _pending_ids,
    _set_status,
    _start_agent_thread,
    _thread_chip,
    _wait_until,
)
from tests.e2e_ui.team_chat.test_motion import _arm_probe, _assert_reduced, _probe_entries
from tests.e2e_ui.team_chat.test_viewports import (
    DESKTOP,
    PHONE,
    _assert_no_horizontal_scroll,
    _assert_touch_target,
    _assert_within_viewport,
)

_VIEWPORT_IDS = ["1280x800", "390x844"]
_VIEWPORTS = pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=_VIEWPORT_IDS)
_SCREENSHOT_DIR = Path("/tmp/omnigent-chat-ux/T-115")
_LOAD_TIMEOUT_MS = 30_000
_PLAN = ("Tests lesen", "Fix schreiben", "Tests ausführen", "PR öffnen")
_ANNOUNCE_MAX_MS = 600
_ANNOUNCE_DEBOUNCE_MS = 500
_REDUCED_MAX_MS = 120
_SPIN_MS = 800
_DURATION_TEXT = re.compile(r"^\d+(\.\d)?(ms|s)$")
_LONG_LABEL = (
    "Alle Integrationstests für die Abrechnung mit Rundung, Steuersätzen und "
    "Fremdwährungen ausführen und das Ergebnis zusammenfassen "
    "Donaudampfschifffahrtselektrizitaetenhauptbetriebswerkbauunterbeamtengesellschaft"
)

# Records when the step-3 row becomes current and when the region announces it.
_ARM_ANNOUNCE_PROBE = """([label, text]) => {
  window.__stepsProbe = {};
  const probe = window.__stepsProbe;
  const check = () => {
    const group = document.querySelector('[data-testid="steps-group"]');
    if (!group) return;
    const now = performance.now();
    const current = group.querySelector('[data-testid="step-row"][aria-current="step"]');
    if (probe.currentAt === undefined && current && current.textContent.includes(label)) {
      probe.currentAt = now;
    }
    const region = group.querySelector('[role="status"]');
    if (probe.announcedAt === undefined && region && region.textContent === text) {
      probe.announcedAt = now;
    }
  };
  new MutationObserver(check).observe(document.body, {
    subtree: true, childList: true, characterData: true, attributes: true,
  });
}"""


def _todos(done: int, labels: tuple[str, ...] = _PLAN) -> list[dict[str, str]]:
    """The plan with the first *done* todos completed and the next one in progress."""
    todos = []
    for index, label in enumerate(labels):
        status = "completed" if index < done else "in_progress" if index == done else "pending"
        todos.append({"content": label, "status": status, "activeForm": label})
    return todos


def _post_todos(server: TeamChatServer, thread: AgentThread, todos: list[dict[str, str]]) -> None:
    _agent_event(server, thread, "external_session_todos", todos=todos)


def _steps_group(pane: Locator) -> Locator:
    return pane.get_by_test_id("agent-thread-live").get_by_test_id("steps-group")


def _status_region(pane: Locator) -> Locator:
    return _steps_group(pane).get_by_role("status")


def _step_row(pane: Locator, label: str) -> Locator:
    return _steps_group(pane).get_by_test_id("step-row").filter(has_text=label)


def _advance_to(
    server: TeamChatServer, thread: AgentThread, pane: Locator, done: int, labels=_PLAN
) -> None:
    """Post the plan at step *done* and wait until the page announced it."""
    _post_todos(server, thread, _todos(done, labels))
    expected = f"{labels[done]}, Schritt {done + 1} von {len(labels)}"
    expect(_status_region(pane)).to_have_text(expected, timeout=_LOAD_TIMEOUT_MS)


def _running_thread_with_plan(
    server: TeamChatServer,
    page_as: Callable[..., Page],
    viewport: dict[str, int],
    reduced_motion: str,
    labels: tuple[str, ...] = _PLAN,
) -> tuple[Page, AgentThread, Locator]:
    """Anna's open thread, running, at step 2 of *labels*, with the steps expanded."""
    channel = _agent_channel(server)
    page = page_as(ANNA, viewport=viewport)
    page.emulate_media(reduced_motion=reduced_motion)
    _open_channel(page, server, channel["id"])
    thread = _start_agent_thread(server, channel["id"])
    pane = _open_live_thread(page, thread)
    _set_status(server, thread, "running", response_id="resp_turn")
    expect(pane.get_by_test_id("agent-thread-live")).to_be_visible(timeout=_LOAD_TIMEOUT_MS)

    _advance_to(server, thread, pane, 0, labels)
    _advance_to(server, thread, pane, 1, labels)
    summary = _steps_group(pane).get_by_test_id("steps-summary")
    expect(summary).to_have_text(f"{len(labels)} Schritte")
    summary.scroll_into_view_if_needed()
    summary.focus()
    summary.press("Enter")
    expect(summary).to_have_attribute("aria-expanded", "true")
    expect(_steps_group(pane).get_by_test_id("step-row")).to_have_count(len(labels))
    return page, thread, pane


def _assert_done_with_mono_duration(row: Locator, label: str) -> None:
    expect(row).to_have_attribute("data-status", "done")
    expect(row.locator("svg.lucide-check")).to_be_visible()
    duration = row.locator(".font-mono")
    expect(duration).to_have_text(_DURATION_TEXT)
    font = duration.evaluate("el => getComputedStyle(el).fontFamily")
    assert "mono" in font.lower(), f"{label} duration font is not monospace: {font}"


def _spin_animations(arc: Locator) -> list[dict[str, float]]:
    """Running infinite animations on the arc, as ``{duration}`` per animation."""
    return arc.evaluate(
        """el => el.getAnimations()
          .map((a) => a.effect.getComputedTiming())
          .filter((t) => t.iterations === Infinity)
          .map((t) => ({ duration: Number(t.duration) }))"""
    )


@_VIEWPORTS
def test_ac_048b_expanded_steps_show_checks_durations_active_arc_and_pending_dot(
    team_chat_server: TeamChatServer, page_as: Callable[..., Page], viewport: dict[str, int]
) -> None:
    """At "Tests ausführen": two checks with mono durations, a turning arc with shimmer, a dot."""
    page, thread, pane = _running_thread_with_plan(
        team_chat_server, page_as, viewport, reduced_motion="no-preference"
    )
    _advance_to(team_chat_server, thread, pane, 2)

    for label in _PLAN[:2]:
        _assert_done_with_mono_duration(_step_row(pane, label), label)

    active = _step_row(pane, "Tests ausführen")
    expect(active).to_have_attribute("data-status", "active")
    active.scroll_into_view_if_needed()
    arc = active.locator("svg.lucide-loader-circle")
    expect(arc).to_have_attribute("data-spinning", "true")
    spins = _spin_animations(arc)
    assert [spin["duration"] for spin in spins] == [_SPIN_MS], f"arc animations: {spins}"
    shimmer = active.locator(".text-shimmer")
    expect(shimmer).to_have_text("Tests ausführen")
    name = shimmer.evaluate("el => getComputedStyle(el).animationName")
    assert name == "shimmer-sweep", f"active label animation is {name!r}"
    expect(active.locator(".font-mono")).to_have_count(0)

    pending = _step_row(pane, "PR öffnen")
    expect(pending).to_have_attribute("data-status", "pending")
    expect(pending.locator("svg")).to_have_count(0)
    expect(pending.locator(".rounded-full")).to_have_count(1)
    expect(pending.locator(".font-mono")).to_have_count(0)

    if viewport == PHONE:
        _assert_touch_target(_steps_group(pane).get_by_test_id("steps-summary"), "steps summary")
    _assert_no_horizontal_scroll(page)


@_VIEWPORTS
def test_ac_048b_long_step_label_wraps_inside_the_thread(
    team_chat_server: TeamChatServer, page_as: Callable[..., Page], viewport: dict[str, int]
) -> None:
    """A 200-character step label, with a long unbroken word, wraps; the page does not scroll."""
    labels = ("Tests lesen", "Fix schreiben", _LONG_LABEL, "PR öffnen")
    page, thread, pane = _running_thread_with_plan(
        team_chat_server, page_as, viewport, reduced_motion="no-preference", labels=labels
    )
    _advance_to(team_chat_server, thread, pane, 2, labels)

    long_row = _step_row(pane, "Donaudampfschifffahrt")
    short_row = _step_row(pane, "PR öffnen")
    expect(long_row).to_have_attribute("data-status", "active")
    long_box, short_box = long_row.bounding_box(), short_row.bounding_box()
    assert long_box is not None and short_box is not None
    assert long_box["height"] >= 2 * short_box["height"], (
        f"long label did not wrap: {long_box['height']}px vs {short_box['height']}px"
    )
    _assert_within_viewport(page, long_row, "long step row")
    _assert_no_horizontal_scroll(page)


@_VIEWPORTS
def test_ac_090a_step_change_announced_within_600ms_and_run_end_says_lauf_abgeschlossen(
    team_chat_server: TeamChatServer, page_as: Callable[..., Page], viewport: dict[str, int]
) -> None:
    """Step 2 to 3: the region says "Tests ausführen, Schritt 3 von 4" ≤ 600 ms after the row."""
    page, thread, pane = _running_thread_with_plan(
        team_chat_server, page_as, viewport, reduced_motion="no-preference"
    )
    region = _status_region(pane)
    expect(region).to_have_attribute("aria-live", "polite")
    expect(region).to_have_text("Fix schreiben, Schritt 2 von 4")
    announced = "Tests ausführen, Schritt 3 von 4"
    page.evaluate(_ARM_ANNOUNCE_PROBE, ["Tests ausführen", announced])

    _post_todos(team_chat_server, thread, _todos(2))
    page.wait_for_function(
        "() => window.__stepsProbe.currentAt !== undefined"
        " && window.__stepsProbe.announcedAt !== undefined",
        timeout=_LOAD_TIMEOUT_MS,
    )
    probe = page.evaluate("() => window.__stepsProbe")
    delay_ms = probe["announcedAt"] - probe["currentAt"]
    assert delay_ms <= _ANNOUNCE_MAX_MS, f"announced {delay_ms:.0f} ms after the step changed"
    # Debounced: the text waits for the step to settle, it does not follow every frame.
    assert delay_ms >= _ANNOUNCE_DEBOUNCE_MS - 20, f"announced after only {delay_ms:.0f} ms"
    expect(region).to_have_text(announced)
    expect(_step_row(pane, "Tests ausführen")).to_have_attribute("aria-current", "step")
    expect(_steps_group(pane).locator('[aria-current="step"]')).to_have_count(1)
    is_sr_only = region.evaluate(
        "el => { const s = getComputedStyle(el);"
        " return s.position === 'absolute' && el.getBoundingClientRect().width <= 1; }"
    )
    assert is_sr_only, "the status region is visible on screen"

    _post_todos(team_chat_server, thread, _todos(len(_PLAN)))
    expect(region).to_have_text("Lauf abgeschlossen", timeout=_LOAD_TIMEOUT_MS)
    expect(region).to_have_attribute("aria-live", "polite")
    expect(_steps_group(pane).locator('[aria-current="step"]')).to_have_count(0)
    _assert_no_horizontal_scroll(page)


@_VIEWPORTS
def test_ac_090b_reduced_motion_step_done_fades_without_scale_arc_still_no_shimmer(
    team_chat_server: TeamChatServer, page_as: Callable[..., Page], viewport: dict[str, int]
) -> None:
    """Reduced motion: active to done fades ≤ 120 ms with no transform; arc still, no shimmer."""
    page, thread, pane = _running_thread_with_plan(
        team_chat_server, page_as, viewport, reduced_motion="reduce"
    )
    active = _step_row(pane, "Fix schreiben")
    expect(active).to_have_attribute("aria-current", "step")
    active.scroll_into_view_if_needed()
    arc = active.locator("svg.lucide-loader-circle")
    expect(arc).to_be_visible()
    expect(arc).not_to_have_attribute("data-spinning", "true")
    assert _spin_animations(arc) == [], "the active arc turns under reduced motion"
    expect(active.locator(".text-shimmer")).to_have_count(0)
    chip_arc = _thread_chip(pane).locator("svg.lucide-loader-circle")
    expect(chip_arc).to_be_visible()
    expect(chip_arc).not_to_have_attribute("data-spinning", "true")

    _arm_probe(page)
    _post_todos(team_chat_server, thread, _todos(2))
    expect(_step_row(pane, "Fix schreiben").locator("svg.lucide-check")).to_be_visible(
        timeout=_LOAD_TIMEOUT_MS
    )
    entries = _probe_entries(page, "step-icon")
    assert entries, "the status change mounted no step icon"
    for entry in entries:
        assert entry["motion"] == "step-icon-reduced", entry
        assert entry["motionDuration"] <= _REDUCED_MAX_MS, entry
        _assert_reduced(entry)

    now_active = _step_row(pane, "Tests ausführen")
    expect(now_active).to_have_attribute("aria-current", "step")
    expect(now_active.locator(".text-shimmer")).to_have_count(0)
    expect(now_active.locator("svg.lucide-loader-circle")).not_to_have_attribute(
        "data-spinning", "true"
    )
    if viewport == PHONE:
        _assert_touch_target(_steps_group(pane).get_by_test_id("steps-summary"), "steps summary")
    _assert_no_horizontal_scroll(page)


def _viewport_id(viewport: dict[str, int]) -> str:
    return f"{viewport['width']}x{viewport['height']}"


def _screenshot(page: Page, surface: str, state: str, viewport: dict[str, int]) -> None:
    """Review evidence, not a baseline: ``{surface}-{state}-{viewport}.png``."""
    _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(_SCREENSHOT_DIR / f"{surface}-{state}-{_viewport_id(viewport)}.png"))


@_VIEWPORTS
def test_ac_048b_steps_collapse_and_expand_inside_the_viewport(
    team_chat_server: TeamChatServer, page_as: Callable[..., Page], viewport: dict[str, int]
) -> None:
    """The steps row collapses and expands the list; neither state scrolls the page."""
    page, _thread, pane = _running_thread_with_plan(
        team_chat_server, page_as, viewport, reduced_motion="no-preference"
    )
    summary = _steps_group(pane).get_by_test_id("steps-summary")
    _assert_no_horizontal_scroll(page)
    _screenshot(page, "steps", "expanded", viewport)

    summary.press("Enter")
    expect(summary).to_have_attribute("aria-expanded", "false")
    expect(_steps_group(pane).get_by_test_id("step-row")).to_have_count(0)
    _assert_within_viewport(page, summary, "steps summary")
    _assert_no_horizontal_scroll(page)
    _screenshot(page, "steps", "collapsed", viewport)


def test_ac_090b_reduced_motion_expanded_steps_screenshot(
    team_chat_server: TeamChatServer, page_as: Callable[..., Page]
) -> None:
    """Reduced motion, expanded steps at 1280x800: the active arc is still."""
    page, _thread, pane = _running_thread_with_plan(
        team_chat_server, page_as, DESKTOP, reduced_motion="reduce"
    )
    arc = _step_row(pane, "Fix schreiben").locator("svg.lucide-loader-circle")
    expect(arc).not_to_have_attribute("data-spinning", "true")
    _screenshot(page, "steps", "expanded-reduced-motion", DESKTOP)


@_VIEWPORTS
def test_fr_089_failed_turn_marks_the_running_tool_step_as_error(
    team_chat_server: TeamChatServer, page_as: Callable[..., Page], viewport: dict[str, int]
) -> None:
    """A turn that fails during a tool call shows that step as an error row."""
    channel = _agent_channel(team_chat_server)
    page = page_as(ANNA, viewport=viewport)
    _open_channel(page, team_chat_server, channel["id"])
    thread = _start_agent_thread(team_chat_server, channel["id"])
    pane = _open_live_thread(page, thread)
    _set_status(team_chat_server, thread, "running", response_id="resp_turn")
    _agent_event(
        team_chat_server,
        thread,
        "external_conversation_item",
        item_type="function_call",
        item_data={
            "agent": "chat_agent",
            "name": "shell",
            "arguments": json.dumps({"command": "pytest tests/billing"}),
            "call_id": "call_t115_error",
        },
        response_id="resp_turn",
    )
    summary = _steps_group(pane).get_by_test_id("steps-summary")
    expect(summary).to_have_text("1 Schritt", timeout=_LOAD_TIMEOUT_MS)
    summary.click()
    expect(summary).to_have_attribute("aria-expanded", "true")

    _set_status(team_chat_server, thread, "failed", response_id="resp_turn")
    expect(_steps_group(pane).get_by_test_id("step-row")).to_have_attribute(
        "data-status", "error", timeout=_LOAD_TIMEOUT_MS
    )
    icon = (
        _steps_group(pane).get_by_test_id("step-row").get_by_test_id("step-status").locator("svg")
    )
    _settle_mount_animation(icon)
    _screenshot(page, "steps", "error", viewport)
    _assert_no_horizontal_scroll(page)


# --- AC-059a: plan review ---------------------------------------------------

_PLAN_LONG_WORD = "Rechnungsrundungsdifferenzausgleichsbuchungsprotokollierungsverfahren" * 2
_PLAN_CODE_LINE = (
    "uv run --group test pytest tests/billing/test_rounding.py::"
    "test_invoice_total_rounds_half_even_for_every_supported_currency_and_tax_rate -q -x"
)
_PLAN_TEXT = (
    "# Plan\n\n"
    "1. Rundung in `billing/rounding.py` auf Banker's Rounding umstellen.\n"
    f"2. {_PLAN_LONG_WORD} ergänzen.\n\n"
    f"```bash\n{_PLAN_CODE_LINE}\n```\n"
)
_PLAN_BUTTONS = ("Yes, and use auto mode", "Yes, manually approve edits", "Reject with feedback")


def _plan_review(pane: Locator) -> Locator:
    return pane.get_by_test_id("agent-thread-live").get_by_test_id("plan-review")


def _settle_mount_animation(element: Locator) -> None:
    """Wait for the card's entry animation; it scales an ancestor while it runs."""
    element.evaluate(
        """async (el) => {
          for (let node = el; node; node = node.parentElement) {
            const finite = node.getAnimations()
              .filter((a) => a.effect.getComputedTiming().iterations !== Infinity);
            await Promise.all(finite.map((a) => a.finished));
          }
        }"""
    )


def _thread_waiting_for_plan(
    server: TeamChatServer,
    page_as: Callable[..., Page],
    viewport: dict[str, int],
    viewer: str,
) -> tuple[Page, AgentThread, Locator]:
    """*viewer*'s open thread on a running session, before the plan is asked."""
    channel = _agent_channel(server)
    page = page_as(viewer, viewport=viewport)
    _open_channel(page, server, channel["id"])
    thread = _start_agent_thread(server, channel["id"])
    pane = _open_live_thread(page, thread)
    _set_status(server, thread, "running", response_id="resp_turn")
    expect(pane.get_by_test_id("agent-thread-live")).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    return page, thread, pane


@_VIEWPORTS
def test_ac_059a_plan_review_pending_long_plan_stays_inside_the_viewport(
    team_chat_server: TeamChatServer, page_as: Callable[..., Page], viewport: dict[str, int]
) -> None:
    """Anna (EDIT): the pending plan with a long word and code line fits; phone buttons 44px."""
    page, thread, pane = _thread_waiting_for_plan(team_chat_server, page_as, viewport, ANNA)

    with _parked_approval(
        team_chat_server,
        thread.session_id,
        tool_name="ExitPlanMode",
        tool_input={"plan": _PLAN_TEXT},
    ):
        review = pane.get_by_test_id("agent-thread-live").locator(
            '[data-testid="plan-review"][data-state="pending"]'
        )
        expect(review).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
        expect(review).to_contain_text(_PLAN_LONG_WORD)
        expect(review.locator("pre")).to_contain_text(_PLAN_CODE_LINE)
        _settle_mount_animation(review)
        if viewport == PHONE:
            for name in _PLAN_BUTTONS:
                _assert_touch_target(review.get_by_role("button", name=name), name)
        review.scroll_into_view_if_needed()
        _assert_within_viewport(page, review, "plan review")
        _assert_no_horizontal_scroll(page)
        _screenshot(page, "plan", "pending", viewport)


def test_ac_059a_edit_viewer_manual_approve_submits_accept(
    team_chat_server: TeamChatServer, page_as: Callable[..., Page]
) -> None:
    """Anna (EDIT) clicks "Yes, manually approve edits": the parked hook returns allow."""
    page, thread, pane = _thread_waiting_for_plan(team_chat_server, page_as, DESKTOP, ANNA)

    with _parked_approval(
        team_chat_server,
        thread.session_id,
        tool_name="ExitPlanMode",
        tool_input={"plan": _PLAN_TEXT},
    ) as approval:
        review = _plan_review(pane)
        expect(review).to_have_attribute("data-state", "pending", timeout=_LOAD_TIMEOUT_MS)
        with page.expect_request(
            lambda request: (
                request.method == "POST"
                and request.url.endswith(f"/elicitations/{approval.elicitation_id}/resolve")
            )
        ) as sent:
            review.get_by_role("button", name="Yes, manually approve edits").click()
        assert sent.value.post_data_json["action"] == "accept", sent.value.post_data
        assert approval.thread is not None
        approval.thread.join(timeout=30.0)
        assert not approval.thread.is_alive(), "the parked plan hook did not return"
        assert approval.verdict.get("status") == 200, approval.verdict
        assert "allow" in approval.verdict.get("body", ""), approval.verdict
        expect(review).to_have_attribute("data-state", "resolved", timeout=_LOAD_TIMEOUT_MS)


@_VIEWPORTS
def test_ac_059a_read_viewer_sees_plan_review_read_only_and_cannot_resolve(
    team_chat_server: TeamChatServer, page_as: Callable[..., Page], viewport: dict[str, int]
) -> None:
    """Bob (READ): the plan is read-only, every button disabled, the note shown; no resolve."""
    page, thread, pane = _thread_waiting_for_plan(team_chat_server, page_as, viewport, BOB)

    with _parked_approval(
        team_chat_server,
        thread.session_id,
        tool_name="ExitPlanMode",
        tool_input={"plan": _PLAN_TEXT},
    ) as approval:
        review = _plan_review(pane)
        expect(review).to_have_attribute("data-state", "read-only", timeout=_LOAD_TIMEOUT_MS)
        expect(review).to_contain_text(_READ_ONLY_NOTE)
        for name in _PLAN_BUTTONS:
            expect(review.get_by_role("button", name=name)).to_be_disabled()
        # The plan's code block adds its own copy controls; they are disabled too.
        buttons = review.get_by_role("button")
        for index in range(buttons.count()):
            expect(buttons.nth(index)).to_be_disabled()
        review.scroll_into_view_if_needed()
        _assert_no_horizontal_scroll(page)
        _screenshot(page, "plan", "read-only", viewport)

        resolves: list[str] = []
        page.on(
            "request",
            lambda request: resolves.append(request.url) if "/resolve" in request.url else None,
        )
        review.get_by_role("button", name="Yes, manually approve edits").click(force=True)
        page.wait_for_timeout(1_000)
        assert resolves == [], f"a READ click sent {resolves}"
        assert approval.elicitation_id in _pending_ids(team_chat_server, thread.session_id)
        assert approval.thread is not None and approval.thread.is_alive(), approval.verdict
        expect(review).to_have_attribute("data-state", "read-only")


# --- AC-060a/b: PR card -----------------------------------------------------

_PR_URL = "https://github.com/acme/billing/pull/42"
_PR_LONG_WORD = "Donaudampfschifffahrtselektrizitaetenhauptbetriebswerkbauunterbeamtengesellschaft"
_PR_LONG_TITLE = (
    "Rundung der Rechnungssummen für alle Währungen und Steuersätze korrigieren, "
    f"Tests ergänzen und {_PR_LONG_WORD} dokumentieren"
).ljust(200, ".")
_FAKE_WORKSPACE = "/tmp/t115-billing"


def _github_info(*, authenticated: bool, title: str = "Rundung korrigieren") -> dict[str, Any]:
    """``session.github.info`` as the runner reports it; a PR only when signed in."""
    pr = {
        "number": 42,
        "url": _PR_URL,
        "title": title,
        "state": "OPEN",
        "is_draft": False,
        "author": "anna",
        "author_id": "1",
        "base_ref": "main",
        "head_ref": "fix/rounding",
        "head_sha": "a" * 40,
        "base_sha": "b" * 40,
        "body": "",
        "comments": [],
        "checks": {"passing": 3, "failing": 0, "pending": 0, "total": 3, "runs": []},
    }
    return {
        "object": "session.github.info",
        "available": True,
        "provider": "github",
        "branch": "fix/rounding",
        "base_ref": "main",
        "repo": {"name_with_owner": "acme/billing"} if authenticated else None,
        "auth": {
            "authenticated": authenticated,
            "hint": None if authenticated else "Run gh auth login",
            "cli": {"name": "gh", "available": True},
            "accounts": None,
            "selected_account": None,
        },
        "warnings": [],
        "pr": pr if authenticated else None,
    }


def _stub_github(page: Page, info: dict[str, Any]) -> list[str]:
    """Give every session snapshot a workspace and answer the github resource with *info*."""
    requested: list[str] = []

    def _snapshot(route: Route) -> None:
        if route.request.method != "GET":
            route.continue_()
            return
        response = fetch_with_retry(route)
        payload = response.json()
        if isinstance(payload, dict) and not payload.get("workspace"):
            payload["workspace"] = _FAKE_WORKSPACE
        route.fulfill(
            status=response.status,
            headers={**response.headers, "content-type": "application/json"},
            body=json.dumps(payload),
        )

    def _github(route: Route) -> None:
        requested.append(urlsplit(route.request.url).path)
        route.fulfill(json=info)

    page.route(re.compile(r"/v1/sessions/[^/?]+(\?|$)"), _snapshot)
    page.route(re.compile(r"/v1/sessions/[^/]+/resources/github(\?|$)"), _github)
    return requested


def _thread_with_github(
    server: TeamChatServer,
    page_as: Callable[..., Page],
    viewport: dict[str, int],
    info: dict[str, Any],
) -> tuple[Page, Locator, list[str]]:
    channel = _agent_channel(server)
    page = page_as(ANNA, viewport=viewport)
    requested = _stub_github(page, info)
    _open_channel(page, server, channel["id"])
    thread = _start_agent_thread(server, channel["id"])
    pane = _open_live_thread(page, thread)
    _set_status(server, thread, "running", response_id="resp_turn")
    expect(pane.get_by_test_id("agent-thread-live")).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    return page, pane, requested


@_VIEWPORTS
def test_ac_060a_pr_card_shows_42_open_ci_gruen_and_new_tab_link(
    team_chat_server: TeamChatServer, page_as: Callable[..., Page], viewport: dict[str, int]
) -> None:
    """With a PR on the session: "#42", "open", CI "grün", a link that opens a new tab."""
    page, pane, requested = _thread_with_github(
        team_chat_server, page_as, viewport, _github_info(authenticated=True)
    )
    card = pane.get_by_test_id("pr-card")
    expect(card).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    assert requested, "the card did not read /resources/github"
    expect(card).to_contain_text("#42")
    expect(card).to_have_attribute("data-state", "open")
    expect(card).to_contain_text("Status open")
    expect(card).to_contain_text("CI grün")
    link = card.get_by_role("link")
    expect(link).to_have_attribute("href", _PR_URL)
    expect(link).to_have_attribute("target", "_blank")
    if viewport == PHONE:
        _assert_touch_target(link, "PR link")
    card.scroll_into_view_if_needed()
    _assert_no_horizontal_scroll(page)
    _screenshot(page, "pr", "card", viewport)


@_VIEWPORTS
def test_ac_060a_long_pr_title_wraps_without_horizontal_scroll(
    team_chat_server: TeamChatServer, page_as: Callable[..., Page], viewport: dict[str, int]
) -> None:
    """A 200-character title with a long unbroken word wraps inside the card."""
    assert len(_PR_LONG_TITLE) == 200
    page, pane, _requested = _thread_with_github(
        team_chat_server,
        page_as,
        viewport,
        _github_info(authenticated=True, title=_PR_LONG_TITLE),
    )
    card = pane.get_by_test_id("pr-card")
    title = card.get_by_role("link").locator("span").first
    expect(title).to_have_text(_PR_LONG_TITLE, timeout=_LOAD_TIMEOUT_MS)
    card.scroll_into_view_if_needed()
    height, line_height = title.evaluate(
        "el => [el.getBoundingClientRect().height, parseFloat(getComputedStyle(el).lineHeight)]"
    )
    assert height >= 2 * line_height, f"title did not wrap: {height}px, line {line_height}px"
    _assert_within_viewport(page, card, "PR card")
    _assert_no_horizontal_scroll(page)


@_VIEWPORTS
def test_ac_060b_github_not_connected_shows_no_pr_card_and_no_error(
    team_chat_server: TeamChatServer, page_as: Callable[..., Page], viewport: dict[str, int]
) -> None:
    """GitHub signed out: no PR card and no error text in the thread."""
    page, pane, requested = _thread_with_github(
        team_chat_server, page_as, viewport, _github_info(authenticated=False)
    )
    _wait_until(lambda: bool(requested), timeout_s=30.0, what="the card's /resources/github read")
    page.wait_for_timeout(500)
    expect(pane.get_by_test_id("pr-card")).to_have_count(0)
    expect(pane.get_by_role("alert")).to_have_count(0)
    for text in ("Fehler", "error", "gh auth login", "GitHub"):
        expect(pane.get_by_text(text)).to_have_count(0)
    _assert_no_horizontal_scroll(page)
    _screenshot(page, "pr", "absent", viewport)
