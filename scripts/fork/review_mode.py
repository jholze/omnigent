#!/usr/bin/env python3
"""Print the review depth for one team-chat ticket.

The lead session runs this before the first dispatch:

    python3 scripts/fork/review_mode.py T-007b
    python3 scripts/fork/review_mode.py T-007b --field prototyping

The table in specs/team-chat/review-mode.md is the source. An extension or a
gate ticket is never prototyping, even when the field says so.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

LOCKED = frozenset({"erweiterung", "gate"})
MODES = frozenset({"prototyping", "polish"})


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def table_path() -> Path:
    return repo_root() / "specs" / "team-chat" / "review-mode.md"


def parse_table(text: str) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    started = False
    for line in text.splitlines():
        if line.startswith("| Ticket |"):
            started = True
            continue
        if not started:
            continue
        if line.startswith("|---"):
            continue
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        ticket, mode, klasse, ui, grund = cells[:5]
        rows[ticket] = {
            "mode": mode,
            "klasse": klasse,
            "ui": ui,
            "grund": grund,
        }
    return rows


def load_table(path: Path | None = None) -> dict[str, dict[str, str]]:
    source = path or table_path()
    return parse_table(source.read_text(encoding="utf-8"))


def resolve(
    ticket: str,
    field: str | None,
    row: dict[str, str] | None,
) -> tuple[str, str]:
    cleaned = (field or "").strip()
    if cleaned and cleaned not in MODES:
        raise ValueError(cleaned)
    chosen = cleaned or (row["mode"] if row else "")
    if row and row["klasse"] in LOCKED and chosen == "prototyping":
        return (
            "polish",
            "korrigiert: Erweiterung oder Gate ist nie prototyping",
        )
    if cleaned in MODES:
        return cleaned, "feld"
    if row:
        return row["mode"], "tabelle"
    raise KeyError(ticket)


def checks(mode: str, ui: str) -> list[str]:
    lines = [
        "ACs grün; die genannten Tests sind in diesem Lauf wirklich gelaufen",
        "CI grün, inklusive der relevanten Tests",
        "Lint grün",
        "Code-Review: DeepSeek, danach Opus PASS",
        "kein Blocker",
    ]
    if mode == "prototyping":
        lines.extend(
            [
                "kein UX-Review",
                "kein NFR-032",
                "kein Kontrast-Lauf",
            ]
        )
        return lines
    if ui == "ja":
        lines.extend(
            [
                "UX-Review gegen docs/ux/chat-review-checklist.md",
                "NFR-032: 1280x800 und 390x844, bedienbar, kein horizontaler Scroll",
                "Kontrast NFR-009, Text mindestens 4.5:1",
            ]
        )
        return lines
    lines.append("UX, NFR-032 und Kontrast: n/a (kein UI)")
    return lines


def render(ticket: str, field: str | None, table: dict[str, dict[str, str]]) -> str:
    row = table.get(ticket)
    mode, source = resolve(ticket, field, row)
    ui = row["ui"] if row else "nein"
    body = [
        f"Ticket: {ticket}",
        f"Modus: {mode}",
        f"Quelle: {source}",
    ]
    if row:
        body.append(f"Klasse: {row['klasse']}")
        body.append(f"UI: {row['ui']}")
        body.append(f"Grund: {row['grund']}")
    body.append("Prüfungen:")
    body.extend(f"- {line}" for line in checks(mode, ui))
    return "\n".join(body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review-Modus eines Tickets")
    parser.add_argument("ticket", help="Stabile ID, z. B. T-007b")
    parser.add_argument("--field", default="", help="Review-Modus aus dem Ticket")
    args = parser.parse_args(argv)
    table = load_table()
    try:
        print(render(args.ticket, args.field, table))
    except KeyError:
        print(
            f"Ticket {args.ticket} steht nicht in specs/team-chat/review-mode.md.",
            file=sys.stderr,
        )
        print(
            "Lead leitet ab: Erweiterung einer fertigen Funktion oder Gate = polish; "
            "neue Funktion ohne grüne Basis-ACs = prototyping. Feld eintragen, dann erneut.",
            file=sys.stderr,
        )
        return 2
    except ValueError as exc:
        print(f"Ungültiger Review-Modus: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
