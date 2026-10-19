"""Command line for the session-bot prototype."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bots.bot import BotError, Store


def _print(bot) -> int:
    json.dump(bot.to_dict(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _mutate(store: Store, bot_id: str, action) -> int:
    bot = store.load(bot_id)
    action(bot)
    store.save(bot)
    return _print(bot)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bots")
    parser.add_argument("--root", default="bots/state")
    sub = parser.add_subparsers(dest="cmd", required=True)

    create = sub.add_parser("create")
    create.add_argument("--name", required=True)
    create.add_argument("--assignment", required=True)
    create.add_argument("--kind", default="rea")

    show = sub.add_parser("show")
    show.add_argument("--id", required=True)

    opened = sub.add_parser("open-session")
    opened.add_argument("--id", required=True)
    opened.add_argument("--session", required=True)

    closed = sub.add_parser("close-session")
    closed.add_argument("--id", required=True)

    rea = sub.add_parser("prepare-rea")
    rea.add_argument("--id", required=True)
    rea.add_argument("--target", required=True)

    note = sub.add_parser("note")
    note.add_argument("--id", required=True)
    note.add_argument("--text", required=True)

    approval = sub.add_parser("request-approval")
    approval.add_argument("--id", required=True)
    approval.add_argument("--reason", required=True)

    review = sub.add_parser("request-review")
    review.add_argument("--id", required=True)

    decide = sub.add_parser("decide")
    decide.add_argument("--id", required=True)
    group = decide.add_mutually_exclusive_group(required=True)
    group.add_argument("--approve", action="store_true")
    group.add_argument("--reject", action="store_true")

    args = parser.parse_args(argv)
    store = Store(Path(args.root))
    try:
        if args.cmd == "create":
            return _print(store.create(args.name, args.assignment, args.kind))
        if args.cmd == "show":
            return _print(store.load(args.id))
        if args.cmd == "open-session":
            return _mutate(store, args.id, lambda bot: bot.open_session(args.session))
        if args.cmd == "close-session":
            return _mutate(store, args.id, lambda bot: bot.close_session())
        if args.cmd == "prepare-rea":
            return _mutate(store, args.id, lambda bot: bot.prepare_rea(args.target))
        if args.cmd == "note":
            return _mutate(store, args.id, lambda bot: bot.note(args.text))
        if args.cmd == "request-approval":
            return _mutate(
                store, args.id, lambda bot: bot.request_approval(args.reason)
            )
        if args.cmd == "request-review":
            return _mutate(store, args.id, lambda bot: bot.request_review())
        if args.cmd == "decide":
            return _mutate(
                store, args.id, lambda bot: bot.decide(approve=args.approve)
            )
    except BotError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 2
    print("error: unknown command", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
