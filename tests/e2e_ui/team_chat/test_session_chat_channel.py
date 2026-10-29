"""E2E: the session chat column, under the name T-120 gives it (AC-029a).

The journey lives in ``test_session_chat_ui.py``; this module collects the
same test so the ``session_chat`` selection names the AC-029a proof here too.
"""

from __future__ import annotations

from tests.e2e_ui.team_chat.test_session_chat_ui import (
    test_ac_029a_header_toggle_opens_session_chat_with_prior_lines as test_ac_029a_session_chat_channel_shows_prior_lines,  # noqa: E501
)

__all__ = ["test_ac_029a_session_chat_channel_shows_prior_lines"]
