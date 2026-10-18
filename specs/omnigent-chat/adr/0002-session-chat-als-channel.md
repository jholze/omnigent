# ADR-0002: Session-Chat als Channel `kind=session`, Migration von `session_chat_messages`
Status: accepted · Date: 2026-10-16
## Context
PR #4 liefert `session_chat_messages` (Revision `sc1a2b3c4d5e`), Routen `GET/POST /v1/sessions/{id}/chat`, Event `session.human_chat` und Tests (`tests/server/test_session_chat.py`, `tests/e2e_ui/collaboration/test_session_human_chat.py`). Jens: Jede geteilte Session hat einen Session-Chat als Sonderfall eines Channels (FR-029…FR-031).
## Decision
Wir legen je Root-Session mit Chat einen `chat_channels`-Eintrag `kind=session` (`session_root_id`) an, kopieren die PR-#4-Zeilen mit gleicher ID in `chat_messages` und stellen die PR-#4-Routen auf Adapter über den Chat-Store um (gleiche Antwortform, Event `session.human_chat` bleibt). Zugriffsregel bleibt die PR-#4-Regel (direkte Grants, `__public__` ausgeschlossen, Admin nur lesen; Bestand-Spec S-1…S-4). Die alte Tabelle wird nicht mehr beschrieben und in R2 entfernt.
## Consequences
+ Session-Chat erhält Threads, Erwähnungen, Lesestand ohne Zusatzcode.
+ Bestandstests bleiben Vertrag für die Adapter.
− Migration muss idempotent sein und auf SQLite/Postgres/CRDB laufen (NFR-022).
− Löschen einer Root-Session muss auch den `kind=session`-Channel samt Nachrichten löschen (Bestand-Spec §3.5).
