"""E2E: chat motion with and without reduced motion, and the scroll pill (T-120).

A probe watches every element that mounts with ``data-motion`` after it is
armed. For about 600 ms it samples ``getAnimations()`` on the element (for a
status chip, on the chip) and its computed ``transform`` each frame. The
probe stores preset names, timings and transforms only, never message text.

- AC-078a: no preference, a new message mounts with a 180 ms ± 20 ms
  transform animation.
- AC-079a: ``prefers-reduced-motion: reduce``, a new message and a status
  change run no transform and nothing longer than 120 ms.
- AC-080a: Jens scrolled up in an agent thread; streamed output keeps his
  position (± 2 px) and shows the "Neue Nachrichten" pill.

Agent status and stream come from the external-harness events in
``test_agent_thread.py``. Each case runs at 1280x800 and 390x844 (NFR-032).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.e2e_ui.team_chat.conftest import (
    ANNA,
    CHAT_TEST_IDS,
    JENS,
    TeamChatServer,
    seed_message,
)
from tests.e2e_ui.team_chat.test_agent_thread import (
    BOB,
    _agent_channel,
    _agent_event,
    _open_channel,
    _open_live_thread,
    _root_chip,
    _set_status,
    _start_agent_thread,
)
from tests.e2e_ui.team_chat.test_viewports import (
    DESKTOP,
    PHONE,
    _assert_no_horizontal_scroll,
    _assert_touch_target,
)

_VIEWPORTS = pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1280x800", "390x844"])
_LOAD_TIMEOUT_MS = 30_000
_ENTER_MS = 180
_ENTER_TOLERANCE_MS = 20
_REDUCED_MAX_MS = 120
_SCROLL_TOLERANCE_PX = 2
_SAMPLE_MS = 600
_IDENTITY_TRANSFORMS = {"none", "matrix(1, 0, 0, 1, 0, 0)"}

_ARM_MOTION_PROBE = """([sampleMs, chipTestId]) => {
  window.__motionProbe = [];
  const watch = (element) => {
    const chip = element.closest(`[data-testid="${chipTestId}"]`);
    const target = chip ?? element;
    const entry = {
      motion: element.getAttribute("data-motion"),
      motionDuration: Number(element.getAttribute("data-motion-duration")),
      testId: (element.closest("[data-testid]") || element).getAttribute("data-testid"),
      animations: [],
      transforms: [],
      done: false,
    };
    window.__motionProbe.push(entry);
    const seen = new Set();
    const started = performance.now();
    const sample = () => {
      for (const animation of target.getAnimations({ subtree: true })) {
        if (seen.has(animation)) continue;
        seen.add(animation);
        const keyframes = animation.effect?.getKeyframes?.() ?? [];
        const properties = new Set();
        for (const frame of keyframes) {
          for (const key of Object.keys(frame)) {
            if (!["offset", "computedOffset", "easing", "composite"].includes(key)) {
              properties.add(key);
            }
          }
        }
        if (animation.transitionProperty) properties.add(animation.transitionProperty);
        if (animation.animationName) properties.add(`@${animation.animationName}`);
        const timing = animation.effect?.getComputedTiming?.() ?? {};
        entry.animations.push({
          properties: [...properties],
          duration: Number(timing.duration) || 0,
          iterations: timing.iterations === Infinity ? -1 : Number(timing.iterations ?? 1),
        });
      }
      entry.transforms.push(getComputedStyle(target).transform);
      if (performance.now() - started < sampleMs) requestAnimationFrame(sample);
      else entry.done = true;
    };
    sample();
  };
  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      for (const node of mutation.addedNodes) {
        if (!(node instanceof Element)) continue;
        if (node.hasAttribute("data-motion")) watch(node);
        node.querySelectorAll("[data-motion]").forEach(watch);
      }
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
}"""


def _arm_probe(page: Page) -> None:
    page.evaluate(_ARM_MOTION_PROBE, [_SAMPLE_MS, CHAT_TEST_IDS["agentStatusChip"]])


def _probe_entries(page: Page, motion_prefix: str) -> list[dict[str, Any]]:
    """Finished probe entries whose preset name starts with *motion_prefix*."""
    page.wait_for_function(
        "(prefix) => window.__motionProbe.some((e) => e.motion.startsWith(prefix))"
        " && window.__motionProbe.every((e) => e.done)",
        arg=motion_prefix,
        timeout=_LOAD_TIMEOUT_MS,
    )
    entries: list[dict[str, Any]] = page.evaluate("() => window.__motionProbe")
    return [entry for entry in entries if entry["motion"].startswith(motion_prefix)]


def _transform_animations(entry: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        animation
        for animation in entry["animations"]
        if any(
            "transform" in prop or prop in ("translate", "scale", "rotate")
            for prop in animation["properties"]
        )
    ]


def _message_row(page: Page, token: str) -> Locator:
    return (
        page.get_by_test_id(CHAT_TEST_IDS["messageList"])
        .first.get_by_test_id(CHAT_TEST_IDS["message"])
        .filter(has_text=token)
    )


@_VIEWPORTS
def test_ac_078a_new_message_mounts_with_180ms_transform_animation(
    team_chat_server: TeamChatServer, page_as: Callable[..., Page], viewport: dict[str, int]
) -> None:
    """No motion preference: Jens's new message slides in over 180 ms ± 20 ms."""
    channel = _agent_channel(team_chat_server)
    page = page_as(ANNA, viewport=viewport)
    page.emulate_media(reduced_motion="no-preference")
    _open_channel(page, team_chat_server, channel["id"])
    _arm_probe(page)

    token = f"m078-{uuid.uuid4().hex[:8]}"
    seed_message(team_chat_server, as_user=JENS, channel_id=channel["id"], body=f"Neu {token}")
    expect(_message_row(page, token)).to_be_visible(timeout=_LOAD_TIMEOUT_MS)

    [entry] = _probe_entries(page, "message-enter")
    assert entry["testId"] == CHAT_TEST_IDS["message"], entry
    assert entry["motion"] == "message-enter", entry
    transforms = _transform_animations(entry)
    assert transforms, f"no transform animation on the new message: {entry['animations']}"
    for animation in transforms:
        assert abs(animation["duration"] - _ENTER_MS) <= _ENTER_TOLERANCE_MS, animation
    assert any(value not in _IDENTITY_TRANSFORMS for value in entry["transforms"]), (
        "the row never left its resting position"
    )
    assert entry["transforms"][-1] in _IDENTITY_TRANSFORMS, entry["transforms"][-1]
    _assert_no_horizontal_scroll(page)


def _assert_reduced(entry: dict[str, Any]) -> None:
    """No transform animation, nothing over 120 ms, and the element never moved."""
    assert not _transform_animations(entry), f"transform under reduced motion: {entry}"
    for animation in entry["animations"]:
        assert animation["iterations"] != -1, f"endless animation under reduced motion: {entry}"
        assert animation["duration"] <= _REDUCED_MAX_MS, animation
    moved = [value for value in entry["transforms"] if value not in _IDENTITY_TRANSFORMS]
    assert not moved, f"{entry['motion']} moved under reduced motion: {moved[:3]}"


@_VIEWPORTS
def test_ac_079a_reduced_motion_message_and_status_change_have_no_transform(
    team_chat_server: TeamChatServer, page_as: Callable[..., Page], viewport: dict[str, int]
) -> None:
    """Reduced motion: a new message and a chip change fade in ≤ 120 ms without transform."""
    channel = _agent_channel(team_chat_server)
    page = page_as(ANNA, viewport=viewport)
    page.emulate_media(reduced_motion="reduce")
    _open_channel(page, team_chat_server, channel["id"])
    thread = _start_agent_thread(team_chat_server, channel["id"])
    chip = _root_chip(page, thread.token)
    expect(chip).to_have_text("fertig", timeout=_LOAD_TIMEOUT_MS)
    _arm_probe(page)

    token = f"m079-{uuid.uuid4().hex[:8]}"
    seed_message(team_chat_server, as_user=JENS, channel_id=channel["id"], body=f"Neu {token}")
    expect(_message_row(page, token)).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    _set_status(team_chat_server, thread, "running", response_id="resp_turn")
    expect(chip).to_have_text("arbeitet", timeout=_LOAD_TIMEOUT_MS)

    [message] = _probe_entries(page, "message-enter")
    assert message["motion"] == "message-enter-reduced", message
    _assert_reduced(message)
    chip_entries = _probe_entries(page, "chip-crossfade")
    assert chip_entries, "the status change mounted no chip label"
    for entry in chip_entries:
        assert entry["motion"] == "chip-crossfade-reduced", entry
        _assert_reduced(entry)
    _assert_no_horizontal_scroll(page)


def _seed_thread_height(server: TeamChatServer, channel_id: str, root_id: str) -> None:
    """Thread replies from three members, enough to scroll the pane at 1280x800."""
    authors = (JENS, BOB, ANNA)
    for index in range(36):
        seed_message(
            server,
            as_user=authors[index % len(authors)],
            channel_id=channel_id,
            thread_root_id=root_id,
            body=f"Zwischenstand {index}: Build und Tests laufen weiter.",
        )


def _wait_for_stable_height(scroller: Locator) -> None:
    """Wait until the list's scrollHeight holds for 500 ms (the live footer settled)."""
    scroller.evaluate(
        """(el) => new Promise((resolve) => {
          let last = el.scrollHeight;
          let since = performance.now();
          const tick = () => {
            if (el.scrollHeight !== last) { last = el.scrollHeight; since = performance.now(); }
            if (performance.now() - since >= 500) resolve(null);
            else requestAnimationFrame(tick);
          };
          requestAnimationFrame(tick);
        })"""
    )


@_VIEWPORTS
def test_ac_080a_scrolled_up_thread_keeps_position_and_shows_new_messages_pill(
    team_chat_server: TeamChatServer, page_as: Callable[..., Page], viewport: dict[str, int]
) -> None:
    """Jens scrolled up while the agent streams: scrollTop holds ± 2 px; the pill appears."""
    channel = _agent_channel(team_chat_server)
    page = page_as(JENS, viewport=viewport)
    _open_channel(page, team_chat_server, channel["id"])
    thread = _start_agent_thread(team_chat_server, channel["id"])
    _seed_thread_height(team_chat_server, channel["id"], thread.root_id)
    # The turn is already running when Jens opens the thread; only text streams after.
    _set_status(team_chat_server, thread, "running", response_id="resp_turn")
    pane = _open_live_thread(page, thread)
    scroller = pane.get_by_test_id(CHAT_TEST_IDS["messageList"])
    expect(
        scroller.get_by_test_id(CHAT_TEST_IDS["message"]).filter(has_text="Zwischenstand 35:")
    ).to_have_count(1, timeout=_LOAD_TIMEOUT_MS)
    expect(pane.get_by_test_id("agent-thread-live")).to_be_visible(timeout=_LOAD_TIMEOUT_MS)

    _wait_for_stable_height(scroller)
    pill = pane.get_by_test_id(CHAT_TEST_IDS["newMessages"])
    expect(pill).to_have_count(0)
    scroll_room = scroller.evaluate("el => el.scrollHeight - el.clientHeight")
    assert scroll_room > 200, f"thread is too short to scroll up: {scroll_room}px"
    scroller.evaluate("el => { el.scrollTop = Math.floor(el.scrollTop / 2); }")
    page.wait_for_timeout(100)
    before = scroller.evaluate("el => el.scrollTop")
    expect(pill).to_have_count(0)

    shown = ""
    for index, part in enumerate(
        ["Schritt eins erledigt. ", "Schritt zwei läuft. ", "Fast fertig."]
    ):
        _agent_event(
            team_chat_server,
            thread,
            "external_output_text_delta",
            delta=part,
            message_id="msg_turn",
            index=index,
        )
        shown += part
        expect(pane.get_by_test_id("agent-thread-live")).to_contain_text(
            shown.strip(), timeout=5_000
        )

    expect(pill).to_be_visible(timeout=5_000)
    expect(pill).to_contain_text("Neue Nachrichten")
    after = scroller.evaluate("el => el.scrollTop")
    assert abs(after - before) <= _SCROLL_TOLERANCE_PX, f"scrollTop moved {before} -> {after}"
    if viewport == PHONE:
        _assert_touch_target(pill, "Neue Nachrichten pill")
    _assert_no_horizontal_scroll(page)
