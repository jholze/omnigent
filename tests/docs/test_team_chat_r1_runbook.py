"""Text checks for the Team Chat R1 launch runbook."""

from __future__ import annotations

import re
from pathlib import Path

RUNBOOK_PATH = Path(__file__).resolve().parents[2] / "docs" / "runbooks" / "team-chat-r1.md"

EXPECTED_HEADINGS_IN_ORDER = [
    "Backup",
    "Install from the jholze/omnigent fork",
    "One Alembic head",
    "alembic upgrade head",
    "Flag `team_chat` on",
    "Smoke",
    "Rollback",
    "Bot smoke",
]


def _normalized(text: str) -> str:
    """Lowercase, drop bold markers, and collapse whitespace so wrapped prose matches."""
    return re.sub(r"\s+", " ", text.replace("**", "")).lower()


def _section_body(markdown: str, heading_fragment: str) -> str:
    """Return the text under the first `## ` heading containing the fragment."""
    sections = re.split(r"^## ", markdown, flags=re.MULTILINE)
    for section in sections[1:]:
        heading_line, _, body = section.partition("\n")
        if heading_fragment in heading_line:
            return body
    raise AssertionError(f"no '## ' heading contains {heading_fragment!r}")


def test_nfr_025_team_chat_r1_runbook_launch_and_rollback_steps() -> None:
    markdown = RUNBOOK_PATH.read_text(encoding="utf-8")
    level_two_headings = [line[3:] for line in markdown.splitlines() if line.startswith("## ")]

    search_from = 0
    for expected in EXPECTED_HEADINGS_IN_ORDER:
        match_index = next(
            (
                index
                for index in range(search_from, len(level_two_headings))
                if expected in level_two_headings[index]
            ),
            None,
        )
        assert match_index is not None, (
            f"heading {expected!r} missing or out of order in {level_two_headings}"
        )
        search_from = match_index + 1

    assert "(team-chat-r1-bot-smoke.md)" in markdown

    prose = _normalized(markdown)
    assert "if any smoke check fails, the launch stops" in prose
    assert "without a green bot smoke there is no launch go for bots" in prose

    upgrade_section = _normalized(_section_body(markdown, "alembic upgrade head"))
    assert "on a copy of the backup" in upgrade_section
    assert "never on `<home>/.omnigent/chat.db`" in upgrade_section

    db_test_command = "uv run --no-sync pytest tests/db -q"
    unmarked_db_test_lines = [
        line for line in markdown.splitlines() if line.strip().strip("`") == db_test_command
    ]
    assert f'{db_test_command} -m "not databricks"' in markdown and not unmarked_db_test_lines, (
        f"tests/db step must carry -m 'not databricks'; unmarked: {unmarked_db_test_lines}"
    )
