# ADR-0001: Einheitliches Chat-Datenmodell (Channels mit `kind`, ein Messages-Table mit `thread_root_id`)
Status: accepted · Date: 2026-10-16
## Context
Channels, DMs und Session-Chats (FR-005, FR-021, FR-029) brauchen dieselben Fähigkeiten: Nachrichten, Threads (FR-023), Erwähnungen (FR-027), Lesestand (FR-007), Echtzeit (FR-014). PR #4 hat nur `session_chat_messages` (flach, Key Root-Session). Spätere Slack-Brücke, Suche und Board-Zeilen (R2) sollen andocken können.
## Decision
Wir modellieren jede Unterhaltung als `chat_channels`-Zeile mit `kind ∈ {channel, dm, session}` und speichern alle Nachrichten in `chat_messages` mit optionalem `thread_root_id` (eine Ebene). Thread-Metadaten (Zähler, Agent-Zustand, Bot-Streak) liegen in `chat_threads`. Keine Foreign Keys (Repo-Konvention), Zeiten in epoch µs, Cursor `(created_at, id)`.
## Consequences
+ Eine Store-/Routen-/UI-Implementierung für alle Arten; Unread/Mentions einheitlich.
+ Neue Arten (private Channels R2, Board-Zeilen als `author_kind=system`) ohne Schemabruch.
− Session-Chat-Mitgliedschaft ist abgeleitet (Grants) statt gespeichert → Sonderfall in `chat_access`.
· `session_chat_messages` wird migriert (ADR-0002).
