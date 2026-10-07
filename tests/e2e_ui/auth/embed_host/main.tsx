// Stand-in host page: renders the real embed island (`web/src/embed.tsx`) in its
// own React tree with a host fetcher the test can expire, like the Databricks
// monolith. Built with Vite from a copy under `web/.e2e-embed-host/`; see _embed_host.py.
import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { OmnigentApp, setOmnigentHostConfig } from "../src/embed";

// Keep in sync with EMBED_BASENAME in _embed_host.py and `base` in vite.config.ts.
const BASENAME = "/embed-host";
// Wording the embedding host uses when its user session has expired; the host
// fetcher rejects with it before any HTTP response reaches the web UI. Keep in
// sync with EXPIRED_SESSION_MESSAGE in _embed_host.py (the persist test asserts it).
const EXPIRED_SESSION_MESSAGE = "Fetch request failed due expired user session";
const EXPIRED_KEY = "omnigent-e2e:host-session-expired";
const PAGE_LOADS_KEY = "omnigent-e2e:host-page-loads";
const SESSION_CHANGE_EVENT = "embed-host-session-change";

interface EmbedHostControls {
  expireSession: (options?: { persist?: boolean }) => void;
  restoreSession: () => void;
  isExpired: () => boolean;
  pageLoads: () => number;
}

declare global {
  interface Window {
    omnigentEmbedHost: EmbedHostControls;
  }
}

const state = { expired: false };

function readStorage(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string | null): void {
  try {
    if (value === null) window.sessionStorage.removeItem(key);
    else window.sessionStorage.setItem(key, value);
  } catch {
    // Session storage unavailable; in-memory state still applies.
  }
}

function hostSessionExpired(): boolean {
  return state.expired || readStorage(EXPIRED_KEY) === "1";
}

// A persisted expiry survives the page reload, modelling a host whose
// re-authentication does not succeed; the in-memory flag resets on reload.
const controls: EmbedHostControls = {
  expireSession(options) {
    state.expired = true;
    if (options?.persist) writeStorage(EXPIRED_KEY, "1");
    window.dispatchEvent(new Event(SESSION_CHANGE_EVENT));
  },
  restoreSession() {
    state.expired = false;
    writeStorage(EXPIRED_KEY, null);
    window.dispatchEvent(new Event(SESSION_CHANGE_EVENT));
  },
  isExpired: hostSessionExpired,
  pageLoads: () => Number(readStorage(PAGE_LOADS_KEY) ?? "0"),
};
window.omnigentEmbedHost = controls;
writeStorage(PAGE_LOADS_KEY, String(controls.pageLoads() + 1));

async function hostFetcher(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  if (hostSessionExpired()) {
    throw new Error(EXPIRED_SESSION_MESSAGE);
  }
  // The host transport is the one place that legitimately calls the real fetch.
  // oxlint-disable-next-line no-restricted-globals
  return fetch(path, init);
}

const hostConfig = { fetcher: hostFetcher, serverIdentity: "e2e-embed-host" };
// Hosts install the transport eagerly, before the first render.
setOmnigentHostConfig(hostConfig);

function HostChrome() {
  const [expired, setExpired] = useState(hostSessionExpired);
  useEffect(() => {
    const update = () => setExpired(hostSessionExpired());
    window.addEventListener(SESSION_CHANGE_EVENT, update);
    return () => window.removeEventListener(SESSION_CHANGE_EVENT, update);
  }, []);
  return (
    <header
      data-testid="embed-host-chrome"
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        height: 40,
        padding: "0 16px",
        background: "#1b3139",
        color: "#fff",
        font: "13px system-ui, sans-serif",
      }}
    >
      <span>Host application (stand-in) · Omnigent embedded below</span>
      <span data-testid="embed-host-session-status">
        Host session: {expired ? "EXPIRED" : "valid"} · page loads:{" "}
        {controls.pageLoads()}
      </span>
    </header>
  );
}

function HostShell() {
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <HostChrome />
      <div style={{ flex: 1, minHeight: 0 }}>
        <OmnigentApp {...hostConfig} basename={BASENAME} isDarkMode={false} />
      </div>
    </div>
  );
}

createRoot(document.getElementById("host-root")!).render(
  <BrowserRouter>
    <HostShell />
  </BrowserRouter>,
);
