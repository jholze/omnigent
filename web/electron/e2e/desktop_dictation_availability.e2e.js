// Desktop-shell journey: the composer mic follows the connected server's
// dictation capability.
//
// Electron's Chromium exposes SpeechRecognition but has no speech backend, so
// the only working dictation path in the desktop app is the server fallback
// advertised by GET /v1/info `dictation_available`. Without it the shell offers
// no mic (one could only dead-end in "Voice input isn't available on this
// device."); with it, clicking the mic starts a server take whose transcript
// lands in the composer.
//
// Headless CI has no microphone, and Chromium reports a missing audio input as
// a `not-allowed` speech error even when the permission is granted; a fake audio
// device stands in for the user's microphone.
//
// Run from web/electron after building the SPA:
//   OMNIGENT_PW_NO_SANDBOX=1 OMNIGENT_PYTHON=../../.venv/bin/python \
//     xvfb-run -a node --test e2e/desktop_dictation_availability.e2e.js

"use strict";

const { describe, it, before, after } = require("node:test");
const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const {
  REPO_ROOT,
  desktopDepsAvailable,
  spawnServer,
  launchDesktop,
  saveRecording,
} = require("./desktopHarness");

const deps = desktopDepsAvailable();
const RECORD_DIR =
  process.env.OMNIGENT_DESKTOP_RECORD_DIR ||
  path.join(__dirname, "recordings", "desktop-dictation");
const PYTHON = process.env.OMNIGENT_PYTHON || "python3";

const COMPOSER_LABEL = "Describe a task to start a new session…";
const MIC_NAME = "Voice dictation";
// Same handshake budget the button gives a server take (a cold model load).
const TAKE_TIMEOUT_MS = 40_000;
const TRANSCRIPT_TIMEOUT_MS = 15_000;

/** The sentence the server's fake engine transcribes. */
function fakeScript() {
  const result = spawnSync(
    PYTHON,
    ["-c", "from omnigent.server.dictation import FAKE_SCRIPT; print(FAKE_SCRIPT)"],
    { encoding: "utf8", env: { ...process.env, PYTHONPATH: REPO_ROOT } },
  );
  assert.equal(result.status, 0, `could not read FAKE_SCRIPT: ${result.stderr}`);
  return result.stdout.trim();
}

async function serverInfo(serverUrl) {
  return (await fetch(`${serverUrl}/v1/info`)).json();
}

/** Poll `probe` until it is truthy or the deadline passes; returns the last result. */
async function pollUntil(window, probe, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    // oxlint-disable-next-line no-await-in-loop -- Sequential polling by design.
    const result = await probe();
    if (result || Date.now() >= deadline) return result;
    // oxlint-disable-next-line no-await-in-loop -- Sequential polling by design.
    await window.waitForTimeout(250);
  }
}

/** Boot the shell straight into the connected home composer. */
async function openHomeComposer(serverUrl, fakeMicPreload) {
  const launched = await launchDesktop({
    recordDir: RECORD_DIR,
    serverUrl,
    preload: [fakeMicPreload],
  });
  const composer = launched.window.getByLabel(COMPOSER_LABEL).first();
  await composer.waitFor({ state: "visible", timeout: 45_000 });
  return { ...launched, composer };
}

/** Name the clips once the shell closes (per-page video is only flushed then). */
async function closeDesktop({ electronApp, stopDisplayCapture, userDataDir }, clipName) {
  // Stop filming the display first so the clip ends on the asserted state
  // rather than on the window tearing down.
  await stopDisplayCapture();
  await electronApp.close();
  const saved = saveRecording(RECORD_DIR, clipName);
  fs.rmSync(userDataDir, { recursive: true, force: true });
  return saved;
}

describe(
  "desktop shell — composer mic follows server dictation",
  { skip: deps.ok ? false : `missing deps: ${deps.missing.join(", ")}` },
  () => {
    let tmpDir;
    let fakeMicPreload;

    before(() => {
      tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "omni-desktop-dictation-"));
      fakeMicPreload = path.join(tmpDir, "fake-mic.cjs");
      fs.writeFileSync(
        fakeMicPreload,
        'require("electron").app.commandLine.appendSwitch("use-fake-device-for-media-stream");\n',
      );
    });

    after(() => {
      if (tmpDir) fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("offers no mic when the server has no dictation", async () => {
      // Default dictation config: no omnigent[dictation] extra, no engine override.
      const server = await spawnServer(tmpDir, { env: () => ({ OMNIGENT_DICTATION_ENGINE: "" }) });
      let saved;
      try {
        const info = await serverInfo(server.serverUrl);
        assert.equal(info.dictation_available, false, "precondition: server offers no dictation");

        const desktop = await openHomeComposer(server.serverUrl, fakeMicPreload);
        try {
          const { window } = desktop;
          // Let the capability probe settle, then hold the state for the clip.
          await window.waitForTimeout(3000);
          const micCount = await window.getByRole("button", { name: MIC_NAME }).count();
          assert.equal(micCount, 0, "a mic was offered although no dictation path can work");
          await window.waitForTimeout(2000);
        } finally {
          saved = await closeDesktop(desktop, "mic-hidden-without-server-dictation");
        }
      } finally {
        await server.close();
      }
      assert.ok(saved && saved.length > 0, "no desktop recording was produced");
    });

    it("dictates through the server when it advertises dictation", async () => {
      const script = fakeScript();
      const server = await spawnServer(tmpDir, {
        env: () => ({ OMNIGENT_DICTATION_ENGINE: "fake" }),
      });
      let saved;
      try {
        const info = await serverInfo(server.serverUrl);
        assert.equal(info.dictation_available, true, "precondition: server offers dictation");

        const desktop = await openHomeComposer(server.serverUrl, fakeMicPreload);
        try {
          const { window, composer } = desktop;
          const mic = window.getByRole("button", { name: MIC_NAME }).first();
          await mic.waitFor({ state: "visible", timeout: 15_000 });
          await mic.click();

          const listening = await pollUntil(
            window,
            async () => (await mic.getAttribute("aria-pressed")) === "true",
            TAKE_TIMEOUT_MS,
          );
          assert.ok(
            listening,
            `the take never started (title: ${await mic.getAttribute("title")})`,
          );

          // The fake engine finalizes its script after ~0.5 s of audio.
          const transcribed = await pollUntil(
            window,
            async () => (await composer.inputValue()).includes(script),
            TRANSCRIPT_TIMEOUT_MS,
          );
          assert.ok(
            transcribed,
            `transcript did not land; composer: ${await composer.inputValue()}`,
          );

          await mic.click();
          const stopped = await pollUntil(
            window,
            async () => (await mic.getAttribute("aria-pressed")) === "false",
            TAKE_TIMEOUT_MS,
          );
          assert.ok(stopped, "the take did not stop");
          assert.ok((await composer.inputValue()).includes(script), "stopping clobbered the text");
          await window.waitForTimeout(2000);
        } finally {
          saved = await closeDesktop(desktop, "mic-dictates-through-server");
        }
      } finally {
        await server.close();
      }
      assert.ok(saved && saved.length > 0, "no desktop recording was produced");
    });
  },
);
