"""Pure parsing of team-chat mention tokens.

A mention is a token ``<@kind:target>`` with one of three kinds —
``user`` (a principal id), ``agent_account`` (an agent-account
principal id) or ``omnigent_agent`` (an agent handle). This module only
extracts tokens; whether a target actually exists is decided by the
chat store, which leaves unknown targets as literal text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_MENTION_RE = re.compile(r"<@(user|agent_account|omnigent_agent):([^>]+)>")


@dataclass(frozen=True)
class MentionToken:
    """One mention token found in a message body.

    :param kind: ``"user"``, ``"agent_account"`` or ``"omnigent_agent"``.
    :param target_id: The principal id or agent handle the token names.
    """

    kind: str
    target_id: str


def parse_mentions(body: str) -> list[MentionToken]:
    """Return the mention tokens in *body*, in order of appearance.

    Anything that is not exactly ``<@kind:target>`` with a known kind is
    ignored here and stays literal text in the stored body.

    :param body: The message text to scan.
    :returns: The recognised tokens, possibly with duplicate targets.
    """
    return [
        MentionToken(kind=match.group(1), target_id=match.group(2))
        for match in _MENTION_RE.finditer(body)
    ]
