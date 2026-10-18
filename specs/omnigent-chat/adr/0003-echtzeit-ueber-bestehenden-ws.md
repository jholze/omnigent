# ADR-0003: Echtzeit über den bestehenden WS `/v1/sessions/updates` mit bodylosen `chat`-Frames und Changes-Backfill
Status: accepted · Date: 2026-10-16
## Context
Chat braucht einen nutzerbezogenen Strom (FR-009, FR-014, FR-025) mit Lücken-Heilung (FR-082, NFR-018). Vorhanden: per-User-Fan-out `omnigent/runtime/user_session_stream.py` (keine Replay-Puffer), WS `/v1/sessions/updates` (`routes_core.py:1828`) mit Event-Dispatch (`session_added`, `hosts_changed`, `projects_changed`), Client `web/src/lib/sessionUpdatesSocket.ts` mit Reconnect. Session-SSE belegt je Stream eine Verbindung (HTTP/1.1-Limit, `conversationRegistry.ts`). WS-Frames laufen durch `telemetry.record_message_payload`.
## Decision
Wir publizieren Chat-Ereignisse über `user_session_stream.publish` an die Discovery-Keys aller berechtigten Principals und leiten sie im WS als Frame `{"type":"chat","kind":…}` ohne Nachrichtentext weiter. Clients laden Inhalte per Paging-API und heilen Lücken nach Reconnect über `GET /v1/chat/changes`. Live-Agent-Ausgabe kommt weiter über die Session-SSE, aber nur für den geöffneten Thread; Status in Listen über den WS-Watch-Set.
## Consequences
+ Keine zweite Dauerverbindung je Tab; Auth und Reconnect existieren.
+ Kein Body in Telemetrie/Logs.
− +1 Roundtrip pro Nachricht (Budget NFR-001 hält).
− In-Process: nur eine Server-Instanz (C-2); Backfill heilt verlorene Frames.
