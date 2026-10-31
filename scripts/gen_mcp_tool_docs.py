"""Generate the MCP tool catalog in ``docs/mcp.md`` from the code.

Fork-only (jholze/omnigent). The catalog is rendered from
:func:`omnigent.server.mcp_endpoint.tool_definitions` and
:func:`omnigent.server.mcp_chat.chat_tool_definitions` and written between
the ``BEGIN``/``END`` markers in ``docs/mcp.md``. Output is deterministic:
tools keep the order ``tools/list`` returns them in, parameters keep schema
order.

Run with no arguments to rewrite the block. Pass ``--check`` to exit
non-zero when the block is out of date (``tests/scripts`` runs this).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = REPO_ROOT / "docs" / "mcp.md"
BEGIN = "<!-- BEGIN GENERATED MCP TOOLS (scripts/gen_mcp_tool_docs.py) -->"
END = "<!-- END GENERATED MCP TOOLS -->"


def _cell(text: str) -> str:
    """Make *text* safe for one Markdown table cell."""
    return " ".join(text.split()).replace("|", "\\|")


def _type(schema: dict[str, Any]) -> str:
    """Short type label for a JSON-schema property."""
    if "enum" in schema:
        return " \\| ".join(f"`{value}`" for value in schema["enum"])
    kind = schema.get("type", "any")
    if isinstance(kind, list):
        kind = " \\| ".join(str(item) for item in kind)
    if kind == "array" and isinstance(schema.get("items"), dict):
        return f"array of {_type(schema['items'])}"
    return str(kind)


def _render_tool(tool: dict[str, Any]) -> list[str]:
    schema = tool.get("inputSchema", {})
    props: dict[str, Any] = schema.get("properties", {})
    required = set(schema.get("required", []))
    lines = [f"#### `{tool['name']}`", "", " ".join(tool.get("description", "").split()), ""]
    if not props:
        lines += ["No parameters.", ""]
        return lines
    lines += ["| Parameter | Type | Required | Description |", "| --- | --- | --- | --- |"]
    for name, prop in props.items():
        mark = "yes" if name in required else "no"
        lines.append(
            f"| `{name}` | {_type(prop)} | {mark} | {_cell(prop.get('description', ''))} |"
        )
    lines.append("")
    return lines


def render() -> str:
    """Render the generated block, markers included."""
    from omnigent.server.mcp_chat import chat_tool_definitions
    from omnigent.server.mcp_endpoint import tool_definitions

    base = tool_definitions()
    chat = chat_tool_definitions()
    lines = [
        BEGIN,
        "",
        f"`tools/list` returns {len(base)} tools, or {len(base) + len(chat)} when the "
        f"`team_chat` flag is on ({len(chat)} `chat_*` tools).",
        "",
        "### Base tools",
        "",
    ]
    for tool in base:
        lines += _render_tool(tool)
    lines += ["### Chat tools (`team_chat` only)", ""]
    for tool in chat:
        lines += _render_tool(tool)
    lines.append(END)
    return "\n".join(lines)


def _replace(text: str, block: str) -> str:
    start = text.index(BEGIN)
    end = text.index(END) + len(END)
    return text[:start] + block + text[end:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when docs/mcp.md is stale")
    args = parser.parse_args(argv)
    current = DOC.read_text(encoding="utf-8")
    updated = _replace(current, render())
    if args.check:
        if updated != current:
            print("docs/mcp.md is stale; run scripts/gen_mcp_tool_docs.py", file=sys.stderr)
            return 1
        return 0
    if updated != current:
        DOC.write_text(updated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(REPO_ROOT))
    raise SystemExit(main())
