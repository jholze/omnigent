"""E2E: the landing composer must settle while a host's Claude catalog probe hangs.

A real ``omnigent host`` daemon registers on the live server with a scripted
``claude`` first on PATH: it answers ``--version`` / ``auth status`` so
claude-native reads ready, and sleeps on the catalog probe, so every
``GET /v1/hosts/{id}/harnesses/claude-native/model-options`` fails (504 at the
server's 15 s budget, or 502 once the host's 20 s probe gives up). The composer
must still leave "Loading session configuration…" promptly, on a cold landing
and on a cached-pill landing alike. No browser request is intercepted.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page, Response, expect

_REPO_ROOT = Path(__file__).resolve().parents[3]

_HOST_READY_TIMEOUT_S = 150.0
# The composer settles on the first catalog answer, which the server bounds at
# 15 s; the rest is headroom for host registration and tunnel latency.
_SETTLE_BUDGET_S = 30.0
# Exceeds the reported ~2 minute stall.
_OBSERVE_CAP_S = 180.0
_LOADING_REASON = "Loading session configuration…"
_CATALOG_ROUTE = "/harnesses/claude-native/model-options"

# Scripted Claude Code double: ready to the daemon's readiness probes, silent
# on the control-protocol model-catalog probe.
_CLAUDE_STUB = """#!/usr/bin/env python3
import json
import sys
import time

ARGS = sys.argv[1:]

if "--version" in ARGS:
    print("2.1.268 (Claude Code)")
    raise SystemExit(0)

if ARGS[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "claudeai"}))
    raise SystemExit(0)

if "-p" in ARGS:
    # The catalog probe (stream-json initialize, legacy /model, --model alias
    # resolution) never answers; the host's probe budget kills this process.
    time.sleep(600)
    raise SystemExit(0)

print("stub claude: unsupported invocation: " + " ".join(ARGS), file=sys.stderr)
raise SystemExit(1)
"""


@dataclass
class SlowCatalogHost:
    """A real host daemon whose Claude catalog probe never answers."""

    host_id: str
    name: str
    log_path: Path
    stub_path: Path

    def log_tail(self) -> str:
        if not self.log_path.exists():
            return ""
        return self.log_path.read_text(errors="replace")[-4000:]


def host_daemon_env(home: Path, stub_bin: Path, host_id: str, name: str) -> dict[str, str]:
    """Isolated environment for the host daemon with the stub ``claude`` first on PATH."""
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("OMNIGENT_RUNNER", "OMNIGENT_PROCESS", "ANTHROPIC_", "OPENAI_")):
            env.pop(key)
    for key in ("CLAUDECODE", "RUNNER_SERVER_URL", "OMNIGENT", "CODEX_HOME"):
        env.pop(key, None)
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{stub_bin}{os.pathsep}{os.environ['PATH']}",
            "PYTHONPATH": os.pathsep.join(
                [
                    str(_REPO_ROOT),
                    str(_REPO_ROOT / "sdks" / "python-client"),
                    str(_REPO_ROOT / "sdks" / "ui"),
                ]
            ),
            "OMNIGENT_CONFIG_HOME": str(home / "omnigent-config"),
            "OMNIGENT_DATA_DIR": str(home / "omnigent-data"),
            "OMNIGENT_SKIP_ONBOARD": "1",
            "OMNIGENT_HOST_ID": host_id,
            "OMNIGENT_HOST_NAME": name,
        }
    )
    return env


def fetch_host_row(base_url: str, host_id: str) -> dict[str, object] | None:
    rows = httpx.get(f"{base_url}/v1/hosts", timeout=10.0).json().get("hosts", [])
    return next((row for row in rows if row.get("host_id") == host_id), None)


def start_slow_catalog_host(
    base_url: str, root: Path
) -> tuple[subprocess.Popen[bytes], SlowCatalogHost]:
    """Spawn the daemon and wait until the server lists it online with claude-native ready.

    :param base_url: Live server the host registers on.
    :param root: Directory for the isolated home, stub binary and host log.
    :returns: The daemon process and its description.
    """
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    stub_bin = root / "stub-bin"
    stub_bin.mkdir(exist_ok=True)
    stub = stub_bin / "claude"
    stub.write_text(_CLAUDE_STUB)
    stub.chmod(0o755)
    host_id = uuid.uuid4().hex
    name = f"slow-catalog-{host_id[:8]}"
    log_path = root / "host.log"
    env = host_daemon_env(home, stub_bin, host_id, name)
    with log_path.open("w") as log_handle:
        proc = subprocess.Popen(
            [sys.executable, "-m", "omnigent.host._daemon_entry", "--server", base_url],
            env=env,
            cwd=str(_REPO_ROOT),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
    host = SlowCatalogHost(host_id=host_id, name=name, log_path=log_path, stub_path=stub)
    deadline = time.monotonic() + _HOST_READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"host daemon exited early ({proc.returncode}):\n{host.log_tail()}")
        try:
            row = fetch_host_row(base_url, host_id)
        except httpx.HTTPError:
            row = None
        if (
            row is not None
            and row.get("status") == "online"
            and (row.get("configured_harnesses") or {}).get("claude-native") is True
        ):
            return proc, host
        time.sleep(1.0)
    stop_process(proc)
    raise RuntimeError(
        f"host {name} never reported online with claude-native ready:\n{host.log_tail()}"
    )


def stop_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


@pytest.fixture
def slow_catalog_host(live_server: str, output_path: str) -> Iterator[SlowCatalogHost]:
    proc, host = start_slow_catalog_host(live_server, Path(output_path) / "slow_catalog_host")
    try:
        yield host
    finally:
        stop_process(proc)


@dataclass
class StallObservation:
    """What the composer showed from the start of a visit until it settled."""

    variant: str
    settled_after_s: float | None = None
    samples: list[dict[str, object]] = field(default_factory=list)
    responses: list[dict[str, object]] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    video: str | None = None
    setup_dialogs_confirmed_at: list[float] = field(default_factory=list)


def _config_loading(page: Page) -> bool:
    if page.get_by_test_id("new-chat-landing-picker-loading").count() > 0:
        return True
    picker = page.get_by_test_id("new-chat-landing-agent-select")
    return picker.count() > 0 and picker.first.get_attribute("aria-busy", timeout=2_000) == "true"


def _confirm_setup_dialog(page: Page) -> bool:
    """Confirm the host-onboarding "Your setup is ready" dialog when it pops up."""
    dialog = page.get_by_role("dialog").filter(has_text="Your setup is ready")
    if dialog.count() == 0 or not dialog.first.is_visible():
        return False
    dialog.first.get_by_role("button", name="Confirm").click(timeout=5_000)
    expect(dialog).to_have_count(0, timeout=10_000)
    return True


def _ensure_claude_code_selected(page: Page) -> None:
    """Pick Claude Code when the landing offers a choice; a loading landing keeps its pick."""
    picker = page.get_by_test_id("new-chat-landing-agent-select")
    placeholder = page.get_by_test_id("new-chat-landing-picker-loading")
    expect(picker.or_(placeholder).first).to_be_visible(timeout=30_000)
    if picker.count() == 0 or not picker.is_enabled():
        return
    if "Claude Code" in (picker.get_attribute("aria-label") or ""):
        return
    picker.click()
    row = page.get_by_role("menuitem", name=re.compile(r"^Claude Code")).first
    expect(row).to_be_visible(timeout=30_000)
    row.click()
    expect(picker).to_have_attribute("aria-label", re.compile("Claude Code"), timeout=30_000)
    if page.get_by_role("menu").count() > 0:
        page.keyboard.press("Escape")


def observe_landing_stall(
    page: Page, base_url: str, variant: str, output: Path, *, navigate: Callable[[], object]
) -> StallObservation:
    """Drive one landing visit and sample the composer until it settles or the cap elapses."""
    observation = StallObservation(variant=variant)
    started = time.monotonic()

    def _on_response(response: Response) -> None:
        path = response.url.removeprefix(base_url)
        if _CATALOG_ROUTE in path or re.match(r"^/v1/(hosts|agents|info)(\?|$)", path):
            observation.responses.append(
                {
                    "t": round(time.monotonic() - started, 1),
                    "status": response.status,
                    "path": path,
                }
            )

    page.on("response", _on_response)
    try:
        navigate()
        observation.video = page.video.path() if page.video else None
        composer = page.get_by_test_id("new-chat-landing-input")
        expect(composer).to_be_visible(timeout=30_000)
        if _confirm_setup_dialog(page):
            observation.setup_dialogs_confirmed_at.append(round(time.monotonic() - started, 1))
        _ensure_claude_code_selected(page)
        composer.fill("Summarize this repository's README.")
        submit = page.get_by_test_id("new-chat-landing-submit")
        # The disabled button takes no pointer events; its tooltip wrapper does.
        hover_target = submit.locator("xpath=..")
        hover_target.hover()
        tooltip = page.get_by_test_id("new-chat-landing-submit-error-tooltip")
        marks = [30.0, 60.0, 120.0]
        deadline = started + _OBSERVE_CAP_S
        while True:
            elapsed = time.monotonic() - started
            if _confirm_setup_dialog(page):
                observation.setup_dialogs_confirmed_at.append(round(elapsed, 1))
                hover_target.hover()
            loading = _config_loading(page)
            enabled = submit.is_enabled()
            reason = tooltip.first.inner_text() if tooltip.count() > 0 else ""
            observation.samples.append(
                {
                    "t": round(elapsed, 1),
                    "loading": loading,
                    "send_enabled": enabled,
                    "reason": reason,
                }
            )
            if not loading and reason != _LOADING_REASON:
                observation.settled_after_s = round(elapsed, 1)
                break
            if marks and elapsed >= marks[0]:
                shot = output / f"{variant}-stall-{int(marks.pop(0))}s.png"
                page.screenshot(path=shot)
                observation.screenshots.append(str(shot))
            if time.monotonic() >= deadline:
                break
            page.wait_for_timeout(1_000)
        shot = output / f"{variant}-settled.png"
        page.screenshot(path=shot)
        observation.screenshots.append(str(shot))
    finally:
        page.remove_listener("response", _on_response)
    return observation


@pytest.mark.timeout(900)
def test_landing_composer_settles_while_claude_catalog_probe_hangs(
    request: pytest.FixtureRequest,
    live_server: str,
    slow_catalog_host: SlowCatalogHost,
    output_path: str,
) -> None:
    """Send must not stay behind "Loading session configuration…" for minutes.

    First visit: cold landing (no picker cache). Second visit, on a new page of
    the same browser context: the cached-pill landing the report's screenshot
    shows. Both are measured before asserting so a failure still reports both
    stalls.
    """
    output = Path(output_path)
    output.mkdir(parents=True, exist_ok=True)
    page: Page = request.getfixturevalue("page")
    page.set_viewport_size({"width": 1440, "height": 900})
    base_url = live_server
    context = page.context
    fresh = observe_landing_stall(
        page, base_url, "fresh", output, navigate=lambda: page.goto(f"{base_url}/")
    )
    page.close()
    cached_page = context.new_page()
    try:
        cached_page.set_viewport_size({"width": 1440, "height": 900})
        cached = observe_landing_stall(
            cached_page,
            base_url,
            "cached",
            output,
            navigate=lambda: cached_page.goto(f"{base_url}/"),
        )
    finally:
        cached_page.close()

    evidence = {
        "host": {"id": slow_catalog_host.host_id, "name": slow_catalog_host.name},
        "visits": [fresh.__dict__, cached.__dict__],
    }
    evidence_path = output / "stall-observations.json"
    evidence_path.write_text(json.dumps(evidence, indent=2))
    print(f"stall observations: {evidence_path}")

    for visit in (fresh, cached):
        catalog_failures = [
            r for r in visit.responses if _CATALOG_ROUTE in str(r["path"]) and r["status"] >= 500
        ]
        assert catalog_failures, (
            f"[{visit.variant}] premise: the host's catalog probe must be failing; "
            f"responses={visit.responses}"
        )
        stalled_for = visit.settled_after_s or f">{_OBSERVE_CAP_S:.0f}"
        assert visit.settled_after_s is not None and visit.settled_after_s <= _SETTLE_BUDGET_S, (
            f"[{visit.variant}] the composer kept reporting {_LOADING_REASON!r} for "
            f"{stalled_for} s (budget {_SETTLE_BUDGET_S:.0f} s); "
            f"catalog responses={catalog_failures}; samples={visit.samples[-3:]}"
        )
