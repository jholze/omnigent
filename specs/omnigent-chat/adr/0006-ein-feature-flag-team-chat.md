# ADR-0006: Ein Feature-Flag `team_chat` ersetzt `session_human_chat`
Status: accepted · Date: 2026-10-16
## Context
Die Bestand-Spec plante `session_human_chat` (default aus) für den Session-Chat. PR #4 hat noch kein Flag; der Session-Chat ist jetzt Teil des einheitlichen Chats (ADR-0001/0002).
## Decision
Wir führen genau ein Flag `team_chat` in `omnigent/server/feature_flags.py` (owner web, review_by 0.20.0, frontend_visible) ein. Es gated `/v1/chat/*`, die Session-Chat-Adapter `/v1/sessions/{id}/chat`, `chat`-Frames, `session.human_chat`-Events, MCP-`chat_*`-Tools, Outbox-Inserts und die Chat-UI. Flag aus = 404 vor Store-Zugriff (Bestand-Spec §17.2-Muster); fehlendes `app.state.feature_flags` gilt als aus.
## Consequences
+ Ein Schalter für Rollout/Rollback (NFR-025).
− PR #4-Verhalten (immer an) ändert sich: Session-Chat ist ohne Flag aus; Tests in `tests/server/test_session_chat.py` müssen das Flag an ihrer App aktivieren.
