#!/usr/bin/env python3
"""Spawn an Omnigent-fork worker as a child of the Grok lead.

Unlike ~/.omnigent/bin/omnigent-spawn-child this is not bound to the
trading-bot lead. Parent defaults to OMNIGENT_PARENT_SESSION, else the
running grok session whose workspace is this repo.

Usage:
  python3 agents/lead/scripts/spawn.py --agent coder \\
      --title implement-brand-prefix --message "MODE: IMPLEMENT\\n..." \\
      --parent <lead-session-id>

Prints JSON {conversation_id, agent_name, parent_session_id, status, url}.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

# Cursor CLI: --yolo/--force = Run Everything (auto, no in-TUI approve).
# --approve-mcps skips MCP setup cards; --trust skips workspace trust;
# --sandbox disabled skips the sandbox gate. Pin the model so Auto/Fable
# is not used. Never copy these onto a prompting worker.
_CURSOR_UNATTENDED_ARGS = (
    "--yolo",
    "--force",
    "--approve-mcps",
    "--trust",
    "--sandbox",
    "disabled",
    "--model",
    "cursor-grok-4.6-high",
)
BASE = os.environ.get("OMNIGENT_URL", "http://127.0.0.1:6767")
STANDING = (
    "planner",
    "requirements",
    "coder",
    "coder_deepseek",
    "coder_grok",
    "test_author",
    "tester",
    "deepseek_review",
    "reviewer",
    "ux",
    "devops",
    "github_ops",
)
ROOT = Path(__file__).resolve().parents[3]
AGENTS_DIR = Path(__file__).resolve().parents[1] / "agents"


def _auth_headers() -> dict[str, str]:
    path = Path.home() / ".omnigent" / "auth_tokens.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    entry = data.get(BASE) or data.get(BASE.rstrip("/"))
    token = entry.get("token") if isinstance(entry, dict) else entry
    if isinstance(token, str) and token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _req(method: str, path: str, *, data: bytes | None = None, headers: dict | None = None):
    h = {"Accept": "application/json"}
    h.update(_auth_headers())
    if headers:
        h.update(headers)
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {e.code} {path}: {body[:800]}") from e


def _post_json(path: str, payload: dict):
    blob = json.dumps(payload).encode()
    return _req(
        "POST",
        path,
        data=blob,
        headers={"Content-Type": "application/json"},
    )


def _post_message(session_id: str, text: str) -> None:
    _post_json(
        f"/v1/sessions/{session_id}/events",
        {
            "type": "message",
            "data": {
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            },
        },
    )


def _bundle(source: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        if source.is_file():
            info = tarfile.TarInfo(name="config.yaml")
            data = source.read_bytes()
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        else:
            cfg = source / "config.yaml"
            if not cfg.is_file():
                raise SystemExit(f"no config.yaml in {source}")
            for file_path in sorted(source.rglob("*")):
                if file_path.is_file():
                    tf.add(str(file_path), arcname=str(file_path.relative_to(source)))
    return buf.getvalue()


def _identity(source: Path) -> tuple[str | None, str | None]:
    cfg = source if source.is_file() else source / "config.yaml"
    name = None
    harness = None
    for line in cfg.read_text().splitlines():
        s = line.strip()
        if s.startswith("#") or not s:
            continue
        if name is None and s.startswith("name:"):
            name = s.split(":", 1)[1].strip().strip("'\"")
        if harness is None and s.startswith("harness:"):
            harness = s.split(":", 1)[1].strip().strip("'\"")
        if name and harness:
            break
    return name, harness


def _multipart(metadata: dict, bundle: bytes, filename: str) -> tuple[bytes, str]:
    boundary = "----OmnigentSpawnBoundary"
    crlf = b"\r\n"
    parts = [
        f"--{boundary}".encode(),
        b'Content-Disposition: form-data; name="metadata"',
        b"Content-Type: application/json",
        b"",
        json.dumps(metadata).encode(),
        f"--{boundary}".encode(),
        f'Content-Disposition: form-data; name="bundle"; filename="{filename}"'.encode(),
        b"Content-Type: application/gzip",
        b"",
        bundle,
        f"--{boundary}--".encode(),
        b"",
    ]
    return crlf.join(parts), f"multipart/form-data; boundary={boundary}"


def _discover_parent() -> str:
    env = os.environ.get("OMNIGENT_PARENT_SESSION", "").strip()
    if env:
        return env
    _, listing = _req("GET", "/v1/sessions?limit=100&visibility=all")
    sessions = (
        listing.get("sessions")
        or listing.get("items")
        or listing.get("data")
        or listing
    )
    if not isinstance(sessions, list):
        raise SystemExit("could not list sessions; pass --parent")
    matches = []
    workspace = str(ROOT)
    for row in sessions:
        if not isinstance(row, dict):
            continue
        ws = str(row.get("workspace") or "")
        status = str(row.get("status") or "")
        name = str(row.get("agent_name") or "")
        if workspace in ws and status == "running" and name in {"grok", "omnigent_lead"}:
            sid = row.get("id") or row.get("session_id")
            if isinstance(sid, str):
                matches.append(sid)
    if len(matches) == 1:
        return matches[0]
    raise SystemExit(
        "pass --parent <lead-session-id> (sys_session_get_info) "
        f"or set OMNIGENT_PARENT_SESSION; candidates={matches!r}"
    )


def main() -> int:
    raise SystemExit("deprecated: use sys_session_create with agent_id (see agents/lead/config.yaml)")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--agent", choices=STANDING, help="Standing ProductOS worker")
    p.add_argument("--config", help="Path to a YAML or agent directory")
    p.add_argument("--title", required=True)
    p.add_argument("--message", default="")
    p.add_argument("--parent", default="")
    args = p.parse_args()
    if bool(args.agent) == bool(args.config):
        p.error("exactly one of --agent or --config")

    parent = args.parent.strip() or _discover_parent()
    source = (
        Path(args.config).expanduser().resolve()
        if args.config
        else (AGENTS_DIR / args.agent)
    )
    if not source.exists():
        raise SystemExit(f"config not found: {source}")

    name, harness = _identity(source)
    title = args.title
    if args.agent and not title.startswith(f"{args.agent}:"):
        title = f"{args.agent}:{args.title}"

    metadata: dict = {"parent_session_id": parent, "title": title}
    if harness == "cursor-native":
        metadata["terminal_launch_args"] = list(_CURSOR_UNATTENDED_ARGS)

    blob, ctype = _multipart(metadata, _bundle(source), f"{source.name}.tar.gz")
    _, created = _req("POST", "/v1/sessions", data=blob, headers={"Content-Type": ctype})
    child_id = created.get("session_id") or created.get("id")
    agent_name = created.get("agent_name") or name or args.agent
    if not isinstance(child_id, str) or not child_id:
        raise SystemExit(f"no child id in create response: {created!r}"[:500])

    if args.message:
        _post_message(child_id, args.message)

    watcher = Path("/Users/jholze/.omnigent/bin/watch-omnigent-session")
    if watcher.is_file():
        log = Path.home() / ".omnigent" / "logs" / f"watch-{child_id[:8]}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("ab") as fh:
            subprocess.Popen(
                [sys.executable, str(watcher), child_id, parent],
                stdout=fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

    snap = _req("GET", f"/v1/sessions/{child_id}")[1]
    out = {
        "conversation_id": child_id,
        "agent_name": snap.get("agent_name") or agent_name,
        "parent_session_id": snap.get("parent_session_id"),
        "status": snap.get("status"),
        "title": snap.get("title") or title,
        "url": f"{BASE}/c/{child_id}",
        "kind": snap.get("kind"),
    }
    print(json.dumps(out, indent=2))
    if out["parent_session_id"] != parent:
        raise SystemExit(
            f"spawn did not attach parent (got {out['parent_session_id']!r}); "
            "child will NOT wake the lead"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
