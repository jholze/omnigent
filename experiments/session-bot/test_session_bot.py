"""Acceptance tests for bots/SPEC.md."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from bots.bot import REA_ARGV, BotError, Store

ROOT = Path(__file__).resolve().parents[1]
SESSION = "dfd76d2566ce40b3b0743a6346ee9cad"


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "bots", "--root", str(root), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class SessionBotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root)
        self.target = self.root / "sample-app"
        self.target.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_happy_path_stops_at_both_gates(self) -> None:
        bot = self.store.create(
            "rea-investigator",
            "Investigate the sample app and list its modules.",
        )
        self.assertEqual(bot.status, "idle")
        bot.open_session(SESSION)
        bot.prepare_rea(str(self.target))
        self.assertEqual(
            bot.rea_command,
            REA_ARGV.format(target=shlex.quote(str(self.target.resolve()))),
        )
        bot.request_approval("REA command is stored and not started")
        self.assertEqual(bot.status, "awaiting_approval")
        bot.decide(approve=True)
        self.assertEqual(bot.status, "implementing")
        with self.assertRaises(BotError):
            bot.request_review()
        bot.note("Modules: main. Evidence is the stored REA command.")
        bot.request_review()
        self.assertEqual(bot.status, "awaiting_review")
        bot.decide(approve=False)
        self.assertEqual(bot.status, "implementing")
        self.assertFalse(bot.result_fresh)
        with self.assertRaises(BotError):
            bot.request_review()
        bot.note("Revised note after the rejected review.")
        bot.request_review()
        bot.decide(approve=True)
        self.store.save(bot)
        reloaded = Store(self.root).load(bot.id)
        self.assertEqual(reloaded.status, "done")
        self.assertIn("Revised note", reloaded.memory)
        self.assertEqual(reloaded.session_id, SESSION)

    def test_approval_before_session_and_missing_target_fail(self) -> None:
        bot = self.store.create("rea-investigator", "Look at one directory.")
        self.store.save(bot)
        with self.assertRaises(BotError) as raised:
            bot.request_approval("too early")
        self.assertIn("session is open", raised.exception.message)
        self.assertEqual(bot.status, "idle")
        bot.open_session(SESSION)
        with self.assertRaises(BotError):
            bot.prepare_rea(str(self.root / "missing"))
        self.assertEqual(bot.rea_command, "")
        with self.assertRaises(BotError):
            bot.request_approval("no command yet")
        self.assertEqual(bot.status, "session_open")

    def test_close_keeps_memory_and_blocks_on_reject(self) -> None:
        bot = self.store.create("rea-investigator", "Keep the notes.")
        bot.open_session(SESSION)
        bot.note("seen before the session closed")
        bot.prepare_rea(str(self.target))
        command = bot.rea_command
        bot.close_session()
        self.assertEqual(bot.status, "idle")
        self.assertEqual(bot.session_id, "")
        self.assertEqual(bot.rea_command, command)
        self.assertIn("seen before", bot.memory)
        bot.open_session(SESSION)
        bot.request_approval("stop before implementation")
        bot.decide(approve=False)
        self.assertEqual(bot.status, "blocked")
        with self.assertRaises(BotError):
            bot.decide(approve=True)
        with self.assertRaises(BotError):
            bot.note("too late")

    def test_cli_persists_and_does_not_start_rea(self) -> None:
        created = _cli(
            self.root,
            "create",
            "--name",
            "rea-investigator",
            "--assignment",
            "CLI path for the sample app.",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        bot_id = json.loads(created.stdout)["id"]
        early = _cli(
            self.root,
            "request-approval",
            "--id",
            bot_id,
            "--reason",
            "too early",
        )
        self.assertEqual(early.returncode, 2)
        self.assertIn("session is open", early.stderr)
        opened = _cli(
            self.root,
            "open-session",
            "--id",
            bot_id,
            "--session",
            SESSION,
        )
        self.assertEqual(opened.returncode, 0, opened.stderr)
        prepared = _cli(
            self.root,
            "prepare-rea",
            "--id",
            bot_id,
            "--target",
            str(self.target),
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        record = json.loads(prepared.stdout)
        self.assertIn("analyze-javascript-application", record["rea_command"])
        self.assertNotIn("npx", prepared.stderr)
        shown = _cli(self.root, "show", "--id", bot_id)
        self.assertEqual(json.loads(shown.stdout)["status"], "session_open")


if __name__ == "__main__":
    unittest.main()
