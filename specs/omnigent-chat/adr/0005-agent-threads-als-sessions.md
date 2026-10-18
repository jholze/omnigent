# ADR-0005: Agent-Threads sind echte Omnigent-Sessions des Aufrufers; Weiterleitung nur für EDIT+; Spiegeln bei `idle`
Status: accepted · Date: 2026-10-16
## Context
Jens: @agent startet einen Thread mit echter Session, Status live, Streaming, Freigaben im Thread, Agent folgt bis Entlassen (ST-3.1.1, ST-3.1.3). Q-8: Freigaben durch Aufrufer + EDIT. Q-9: Mitglieder bekommen READ. Code: Hosts müssen dem Session-Owner gehören (`_host_launch.py:147`); Projekte sind owner-privat; `_publish_status` (`_sessions/helpers.py`) ist die zentrale Status-Kante (PR #15 hängt dort den Webhook an).
## Decision
Der Orchestrator `omnigent/server/chat_agent_threads.py` erstellt die Session intern über `resolve_project_session_create` + `_create_session_from_existing_agent` als Aufrufer (OWNER), mit Projekt/Host nach A-8, Label `omnigent.chat.v1`, READ-Grants für Mitglieder, und sendet den Thread-Kontext als erste Nachricht. Thread-Antworten werden nur für Autoren mit EDIT+ weitergeleitet (`[Name] text`), nie für Agentenkonten. Ein Hook in `_publish_status` meldet `idle`/`failed`; bei `idle` wird der letzte Assistant-Text (via `summary_lookup` aus PR #15) als Thread-Nachricht gespiegelt.
## Consequences
+ Volle Wiederverwendung von Status, Streaming, Elicitations, Stop/Retry/Steer, Inbox.
+ Rechte bleiben im bestehenden Permission-Modell.
− Agent-Sessions erscheinen bei Mitgliedern als geteilte Sessions in der Sidebar → Filter über Label nötig.
− Agent-Start schlägt fehl, wenn der Aufrufer keinen eigenen Host/Projekt hat (Systemzeile, Q-12).
