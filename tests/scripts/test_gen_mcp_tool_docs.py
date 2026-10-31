"""``docs/mcp.md`` tool catalog matches ``tool_definitions()``."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "gen_mcp_tool_docs.py"


def _load():
    spec = importlib.util.spec_from_file_location("gen_mcp_tool_docs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_docs_catalog_is_current() -> None:
    assert _load().main(["--check"]) == 0


def test_every_tool_is_documented() -> None:
    from omnigent.server.mcp_chat import chat_tool_definitions
    from omnigent.server.mcp_endpoint import tool_definitions

    doc = (REPO_ROOT / "docs" / "mcp.md").read_text(encoding="utf-8")
    for tool in [*tool_definitions(), *chat_tool_definitions()]:
        assert f"#### `{tool['name']}`" in doc


def test_render_is_deterministic() -> None:
    module = _load()
    assert module.render() == module.render()
