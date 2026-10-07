"""Serve the real embed island inside a stand-in host page on the test server's origin.

The Databricks monolith is not available to the e2e suite, so ``embed_host/``
holds a minimal host page that renders ``OmnigentApp`` from ``web/src/embed.tsx``
with a host fetcher the test can expire. The sources are copied under
``web/.e2e-embed-host/`` and built with Vite there, so they resolve ``web``'s
dependencies; the output is then served through a Playwright route under
``/embed-host/`` on the live server's origin, which lets the fetcher call the
Omnigent API same-origin like a real host transport would.
"""

from __future__ import annotations

import mimetypes
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import filelock
from playwright.sync_api import Page, Route

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WEB_DIR = _REPO_ROOT / "web"
_HARNESS_SRC = Path(__file__).with_name("embed_host")
_HARNESS_BUILD_DIR = _WEB_DIR / ".e2e-embed-host"
_DIST_DIR = _HARNESS_BUILD_DIR / "dist"
_DIST_INDEX = _DIST_DIR / ".e2e-embed-host" / "index.html"
_VITE_BIN = _WEB_DIR / "node_modules" / ".bin" / "vite"

EMBED_BASENAME = "/embed-host"
EXPIRED_SESSION_MESSAGE = "Fetch request failed due expired user session"


def _newest_mtime(paths: list[Path]) -> float:
    newest = 0.0
    for root in paths:
        for candidate in root.rglob("*") if root.is_dir() else [root]:
            if candidate.is_file():
                newest = max(newest, candidate.stat().st_mtime)
    return newest


def _build_is_current() -> bool:
    if not _DIST_INDEX.exists():
        return False
    built_at = _DIST_INDEX.stat().st_mtime
    return _newest_mtime([_HARNESS_SRC, _WEB_DIR / "src"]) <= built_at


def build_embed_host() -> Path:
    """Build the host page when its sources or ``web/src`` changed; return the dist dir."""
    with filelock.FileLock(str(_WEB_DIR / ".e2e-embed-host.lock"), timeout=1800):
        if _build_is_current():
            return _DIST_DIR
        _HARNESS_BUILD_DIR.mkdir(exist_ok=True)
        for source in _HARNESS_SRC.iterdir():
            if source.is_file():
                shutil.copy2(source, _HARNESS_BUILD_DIR / source.name)
        # Call the installed binary directly: `pnpm exec` re-checks dependency
        # status and may try to reinstall node_modules.
        subprocess.run(
            [str(_VITE_BIN), "build", "--config", ".e2e-embed-host/vite.config.ts"],
            cwd=_WEB_DIR,
            check=True,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0"},
        )
        if not _DIST_INDEX.exists():
            raise RuntimeError(f"embed host build produced no {_DIST_INDEX}")
        return _DIST_DIR


def serve_embed_host(page: Page, base_url: str) -> None:
    """Fulfil ``<base_url>/embed-host/**`` from the built host page for this page."""
    dist_dir = build_embed_host()
    index_html = _DIST_INDEX.read_bytes()

    def handler(route: Route) -> None:
        relative = urlparse(route.request.url).path[len(EMBED_BASENAME) :].lstrip("/")
        asset = dist_dir / relative
        if relative.startswith("assets/") and asset.is_file():
            content_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
            route.fulfill(status=200, body=asset.read_bytes(), content_type=content_type)
            return
        route.fulfill(status=200, body=index_html, content_type="text/html; charset=utf-8")

    page.route(f"{base_url}{EMBED_BASENAME}", handler)
    page.route(f"{base_url}{EMBED_BASENAME}/**", handler)


def embedded_url(base_url: str, path: str) -> str:
    """Absolute URL of an app route inside the stand-in host, e.g. ``/c/<id>``."""
    return f"{base_url}{EMBED_BASENAME}{path}"
