"""The lead session derives review depth from the team-chat table."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "fork" / "review_mode.py"
TABLE = REPO_ROOT / "specs" / "team-chat" / "review-mode.md"

OPEN_TICKETS = {
    "T-005-AC-033c",
    "T-007b",
    "T-009",
    "T-101",
    "T-102",
    "T-103",
    "T-104",
    "T-105",
    "T-106",
    "T-107",
    "T-108",
    "T-109",
    "T-110",
    "T-111",
    "T-112",
    "T-113",
    "T-114",
    "T-115",
    "T-116",
    "T-116b",
    "T-117",
    "T-118",
    "T-119",
    "T-120",
    "T-121",
    "T-122",
    "T-123",
    "T-124",
    "T-125",
    "T-126",
    "T-127",
    "T-128",
    "T-129",
    "T-130",
    "T-131",
    "T-132",
    "T-133",
}


def _load():
    spec = importlib.util.spec_from_file_location("review_mode", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_table_covers_the_open_tickets():
    table = _load().load_table(TABLE)
    assert set(table) == OPEN_TICKETS
    for ticket, row in table.items():
        assert row["mode"] in {"prototyping", "polish"}, ticket
        assert row["klasse"] in {"erweiterung", "neu", "gate"}, ticket
        assert row["ui"] in {"ja", "nein"}, ticket
        if row["klasse"] in {"erweiterung", "gate"}:
            assert row["mode"] == "polish", ticket


def test_extension_field_is_corrected_to_polish():
    module = _load()
    table = module.load_table(TABLE)
    mode, source = module.resolve("T-007b", "prototyping", table["T-007b"])
    assert mode == "polish"
    assert source.startswith("korrigiert")


def test_field_wins_for_a_new_ticket():
    module = _load()
    table = module.load_table(TABLE)
    mode, source = module.resolve("T-101", "polish", table["T-101"])
    assert mode == "polish"
    assert source == "feld"
    mode, source = module.resolve("T-101", "", table["T-101"])
    assert mode == "prototyping"
    assert source == "tabelle"


def test_unknown_ticket_is_not_invented():
    module = _load()
    try:
        module.resolve("T-999", "", None)
    except KeyError as exc:
        assert exc.args == ("T-999",)
    else:
        raise AssertionError("expected KeyError")


def test_prototyping_skips_ux_and_polish_ui_includes_viewports():
    module = _load()
    prototyping = module.checks("prototyping", "ja")
    assert any("kein UX-Review" in line for line in prototyping)
    assert all("1280x800" not in line for line in prototyping)
    polish = module.checks("polish", "ja")
    assert any("1280x800" in line and "390x844" in line for line in polish)
    assert any("UX-Review" in line for line in polish)
    quiet = module.checks("polish", "nein")
    assert any("n/a" in line for line in quiet)


def test_cli_prints_correction_and_rejects_unknown():
    known = subprocess.run(
        [sys.executable, str(SCRIPT), "T-007b", "--field", "prototyping"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert known.returncode == 0
    assert "Modus: polish" in known.stdout
    assert "korrigiert" in known.stdout

    missing = subprocess.run(
        [sys.executable, str(SCRIPT), "T-999"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert missing.returncode == 2
    assert "nicht in" in missing.stderr


def test_lead_prompt_and_ticket_template_point_at_the_rule():
    lead = (REPO_ROOT / "agents" / "lead" / "config.yaml").read_text(encoding="utf-8")
    tasks = (REPO_ROOT / "specs" / "omnigent-chat" / "07-tasks.md").read_text(encoding="utf-8")
    form = (REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "team_chat_task.yml").read_text(
        encoding="utf-8"
    )
    assert "specs/team-chat/review-mode.md" in lead
    assert "Review-Modus" in tasks
    assert "prototyping" in form and "polish" in form
