# Omnigent Chat – Requirements Spec
Version: 0.4 · Date: 2026-10-16 · Status: in review · Owner: Jens Holze

Quellen: `00-intake.md` v0.2, `01-goals.md` v0.1, `02-story-map.md` v0.3, `03-domain.md` v0.3, Bestand-Spec `omnigent-chat-session/chat-integration.md` („Bestand-Spec“). Code geprüft: `jholze/omnigent` main `87f6f08`, PR #4 `616ea2f`, PR #5 `8a703a3`, PR #14 `a2fcbe1`, PR #15 `a3014eb`.
Status je Item: **exists** / **adapt** / **new** mit Pfad. Unsicherheit: `[ANNAHME A-n]`, `[OFFEN Q-n]`.

## 1. Context & problem
Omnigent hat heute nur einen flachen Menschen-Chat pro geteilter Session (PR #4: `session_chat_messages`, `GET/POST /v1/sessions/{id}/chat`, bodyloses Event `session.human_chat`, Spalte `web/src/shell/SessionChatColumn.tsx`). Es gibt keine serverweiten Channels, keine DMs, keine Threads, keine Erwähnungen, keinen serverseitigen Lesestand, und Agenten nehmen am Gespräch nicht teil. Ziel: Ein Team arbeitet komplett im Omnigent-Chat, Menschen und Agenten (Omnigent-Agenten und externe Grok Bots) sind echte Gesprächspartner, und die UX ist herausragend (harte Anforderung).

## 2. Goals & success metrics
| ID | Goal | Metric / target |
|---|---|---|
| G-1 | Abstimmung findet in Omnigent statt | [ANNAHME] Nach 4 Wochen Mehrheit der projektbezogenen Abstimmung im Omnigent-Chat, qualitativ durch Jens bewertet [OFFEN Q-6] |
| G-2 | Agenten-Arbeit startet aus dem Gespräch | [ANNAHME] Mehrzahl neuer Agent-Sessions des Teams per @ aus dem Chat |
| G-3 | Herausragende UX | Abnahme durch Jens anhand NFR-006…NFR-011, NFR-030 und UX-Agent-Review (NFR-027) ohne offene Blocker |
| G-4 | Upstream-fähig | [ANNAHME A-2] Feature-Flag, saubere Migrationen, Tests; [OFFEN Q-7] Ziel oder nice-to-have |

## 3. Non-goals
- Slack-Brücke (`integrations/slack/`) anfassen oder erweitern. Datenmodell darf späteres Andocken nicht verhindern.
- Mandantenfähigkeit (A-1), Anrufe/Huddles, Föderation, Chat für Public-Link-Besucher.
- (geändert 16.10. 23:25, Jens) React-19-Upgrade (Issue #16, Branch `feat/react-19`) ist jetzt **Voraussetzung**: Merge vor Beginn der Chat-UI-Arbeit (T-006 ff.). Die Chat-UI zielt auf React 19. Abhängigkeit D-1: Branch liegt lokal in eigenem Worktree auf Jens' Mac (Session `6c30fca5…`), Push nur mit OK.
- In R0/R1: Reaktionen, Bearbeiten/Löschen, Dateien, Suche, Export, Mobile-Layout, TTS, private Channels (R2), GitHub-Issue-Diskussion (R2), Board-Widget (R2), serverseitige Agent-Queue (R2), Worker-Übersicht (R2).
- Kein Code aus BuilderIO/agent-native kopieren (Lizenz unklar, React 19); Muster werden nachgebaut.
- Keine Plugin-Hooks aus plugin87/ux-ui-agent-skills; nur deren Doku als Prüfgrundlage.

## 4. Roles
| Rolle | Beschreibung | Rechte im Chat |
|---|---|---|
| Entwickler (Mensch) | Angemeldeter Nutzer | Channels lesen/beitreten/schreiben, DMs, Threads, Agenten starten |
| Session-Owner | Aufrufer eines Agent-Threads (A-5) oder Owner einer geteilten Session | OWNER auf der Session: Stop, alle Freigaben |
| Bearbeiter | Principal mit EDIT/MANAGE auf der Agent-Session | Freigaben, Steuern, Weiterleitung an Agent |
| Channel-Mitglied | Mitglied ohne eigenen Grant | READ auf Agent-Sessions des Channels (Q-9) |
| Admin | `permission_store.is_admin` | Flag (Deployment), Agentenkonten widerrufen; Session-Chat nur lesen (Bestand-Spec S-4) |
| Omnigent-Agent | Läuft als Session mit Rechten des Aufrufers (A-3) | antwortet im Thread, fragt per Karte |
| Agentenkonto (Grok Bot) | Externer Teilnehmer, Principal `agent:<uuid>`, gehört einem Menschen | nur Chat-Rechte, Teilmenge der Owner-Rechte |
| UX-Agent | Bau-Rolle | prüft UI-PRs (NFR-027) |

## 5. Scope by release (from story map)
| Release | Outcome | Stories |
|---|---|---|
| R0 (Demo intern Mi 21.10.2026) | Entwickler schreibt im Channel, ruft `@agent`, sieht ihn im Thread arbeiten, gibt Freigabe im Thread | ST-1.1.1, ST-2.1.1, ST-2.1.2, ST-2.2.1, ST-3.1.1, ST-3.2.1, ST-4.1.1, ST-6.1.1 |
| R1 (Launch 24./25.10.2026) | Team verbringt einen Arbeitstag im Chat inkl. Agenten-Steuerung und Grok Bots; sieht richtig gut aus | ST-1.2.1, ST-1.2.2, ST-1.2.3, ST-2.3.1, ST-2.1.3, ST-2.1.4, ST-3.1.2, ST-3.1.3, ST-3.2.2, ST-3.3.1, ST-3.3.2, ST-3.4.1, ST-4.2.1, ST-5.1.1, ST-7.1.1, ST-7.1.2, ST-7.1.3, ST-7.1.4, ST-7.1.5, ST-7.1.6, ST-UX-1, ST-UX-2 |
| R1.1 (kurz nach Launch, Q-18) | Grok Bots werden per Webhook sofort geweckt statt per 5-min-Polling | ST-7.1.2 (Teil Webhook: FR-071…FR-073, NFR-019) |
| R2 | GitHub-Arbeit und Team-Organisation im Chat | ST-5.2.1, ST-5.2.2, ST-5.3.1, ST-3.2.3, ST-6.1.2, ST-6.2.1, ST-5.4.1, ST-5.4.2, ST-3.3.3 (nur Epic-Skizze in `07-tasks.md`) |

## 6. Functional requirements
Begriffe laut Glossar `03-domain.md`. „Server“ = Omnigent-Server, „Web-App“ = `web/`. Alle Chat-Routen liegen unter `/v1/chat/` (new, `omnigent/server/routes/chat.py`).

### Activity A1: Ankommen & Überblick
| ID | Requirement (EARS) | Story | Prio | Rel | Status | Notes |
|---|---|---|---|---|---|---|
| FR-001 | Where `team_chat` is enabled, the Web-App shall provide the routes `/chat`, `/chat/:channelId` and `/chat/:channelId/t/:threadRootId` with a sidebar listing joined Channels and DMs, and an entry „Chat“ in the main sidebar. | ST-1.1.1 | Must | R0 | new (`web/src/chat/ChatPage.tsx`; adapt `web/src/App.tsx`, `web/src/shell/Sidebar.tsx`) | |
| FR-002 | The Server shall define feature flag `team_chat` (default off, `frontend_visible`) and report it in `GET /v1/info` `features`. | ST-6.1.1 | Must | R0 | adapt (`omnigent/server/feature_flags.py`, `designs/FEATURE_FLAGS.md`) | ersetzt `session_human_chat` der Bestand-Spec (ADR-0006) |
| FR-003 | If `team_chat` is disabled, then the Server shall answer every `/v1/chat/*` request with 404 before reading or writing any chat table, omit `chat` frames from WS `/v1/sessions/updates`, and omit `chat_*` tools from `/mcp`. | ST-6.1.1 | Must | R0 | new | fehlendes `app.state.feature_flags` = aus (Bestand-Spec §17.2) |
| FR-004 | When an authenticated principal calls `GET /v1/chat/bootstrap`, the Server shall ensure the Channel `general` exists exactly once per workspace, add a human caller as member, and return joined Channels/DMs, directory entries and a `changes_cursor`. | ST-6.1.1 | Must | R0 | new | Agentenkonten werden nicht automatisch Mitglied |
| FR-005 | When a human calls `POST /v1/chat/channels` with a name matching `^[a-z0-9][a-z0-9-]{0,39}$` that is unused in the workspace, the Server shall create a public Channel and make the caller a member. | ST-1.1.1 | Should | R0 | new | [OFFEN Q-16] nur Admin? |
| FR-006 | When a human calls `POST /v1/chat/channels/{id}/join` or `/leave` on a public Channel, the Server shall add or remove the membership; leaving `general` shall be refused with 409. | ST-1.1.1 | Must | R0 | new | |
| FR-007 | When a principal calls `POST /v1/chat/channels/{id}/read` with `{created_at, id[, thread_root_id]}` naming an existing message of that Channel (or Thread), the Server shall move the principal's read cursor forward and ignore a cursor that is not newer. | ST-1.2.1 | Must | R1 | new (Tabelle `chat_reads`) | Muster Bestand-Spec §8.3 |
| FR-008 | The Server shall return per joined Channel `unread_count` and `mention_count` (messages after the caller's cursor, excluding the caller's own) and per followed Thread `unread_count` in `GET /v1/chat/bootstrap` and `GET /v1/chat/channels`. | ST-1.2.1 | Must | R1 | new | Folgen = gestartet, gepostet oder erwähnt |
| FR-009 | When a read cursor moves forward, the Server shall push a `chat` frame `read.updated` to every WS connection of the same principal. | ST-1.2.1 | Must | R1 | new | Geräte-Sync |
| FR-010 | The Web-App shall show a „Braucht dich“ view listing (a) unread messages mentioning the caller and (b) pending elicitations of Agent-Thread sessions on which the caller has level ≥ EDIT, each linking to its Thread. | ST-1.2.2 | Must | R1 | adapt (`web/src/pages/InboxPage.tsx`, `pending_elicitations_count` in `SessionListItem`) | |
| FR-011 | When a mention of the caller, a DM message or an elicitation from an Agent-Thread the caller may answer arrives while the window is unfocused or the Channel is not open, the Web-App shall show an OS notification whose body contains no message text („Neue Erwähnung in #billing“, „Neue Direktnachricht von Jens“, „Agent braucht eine Antwort“). | ST-1.2.3 | Should | R1 | adapt (`web/src/lib/browserNotifications.ts`, `web/src/hooks/useIdleNotifications.ts`) | Bestand-Spec S-7 |
| FR-012 | If the browser notification permission is denied, then the Web-App shall not ask again and shall only update in-app badges. | ST-1.2.3 | Should | R1 | exists (Verhalten in `browserNotifications.ts`) | |

### Activity A2: Mit Menschen sprechen
| ID | Requirement (EARS) | Story | Prio | Rel | Status | Notes |
|---|---|---|---|---|---|---|
| FR-013 | When a member posts `{body, client_id[, thread_root_id]}` to `POST /v1/chat/channels/{id}/messages`, the Server shall store the trimmed body with server time `created_at` (epoch µs) and server-set author, and return 201 with the message. | ST-2.1.1 | Must | R0 | new (Muster `omnigent/stores/session_chat_store/`, PR #4) | |
| FR-014 | When a message is stored, the Server shall publish a `chat` frame `message.created` containing `channel_id`, `message_id`, `thread_root_id`, `created_at`, `author_id` and no body to the WS connections of every member of the Channel. | ST-2.1.1 | Must | R0 | adapt (`omnigent/runtime/user_session_stream.py`, `routes_core.py:1828`) | Frames laufen durch `telemetry.record_message_payload` → kein Body |
| FR-015 | If the body is empty after trim, longer than 4000 characters, or contains U+0000, then the Server shall return 422 and store nothing. | ST-2.1.1 | Must | R0 | adapt (Bestand: 2000 Zeichen) | [ANNAHME A-9] |
| FR-016 | If the caller is not a member of the Channel, then the Server shall return 403 `not_a_member` for a public Channel and 404 for a DM or Session-Chat, and store nothing. | ST-2.1.1 | Must | R0 | new | |
| FR-017 | If a principal exceeds 30 post attempts within 60 s in one Channel, then the Server shall return 429 with `Retry-After` and store nothing. | ST-2.1.1 | Must | R1 | adapt (Bestand-Spec §11.1) | in-process |
| FR-018 | When a post repeats a `client_id` already stored for the same author and Channel, the Server shall return 200 with the original message; if the body differs, then it shall return 409. | ST-2.1.1 | Must | R0 | adapt (Bestand-Spec §11.3) | `client_id` 1–64 `[A-Za-z0-9_-]` |
| FR-019 | When a member calls `GET /v1/chat/channels/{id}/messages` with `tail=1`, `before=<µs>:<id>` or `after=<µs>:<id>` and `limit` (default 50, max 200), the Server shall return top-level messages (or, with `thread_root_id`, the Thread's replies) in ascending `(created_at, id)` order with `has_older`/`has_more`. | ST-2.1.1 | Must | R0 | adapt (Bestand-Spec §10.3) | |
| FR-020 | The Web-App shall render bodies of human messages as text nodes, turn `http(s)` URLs into links with `rel="noopener noreferrer"`, and render mention tokens as name chips; it shall not interpret HTML or Markdown in human messages. | ST-2.1.1 | Must | R0 | new | [ANNAHME A-15] |
| FR-021 | When a principal calls `POST /v1/chat/dms` with 1–8 other principals, the Server shall return the existing DM for exactly that participant set including the caller, or create it; if the set contains an Agentenkonto whose owner is not in the set, then the Server shall return 403 `agent_owner_required` [OFFEN Q-13]. | ST-2.1.2 | Must | R0 | new (`dm_key`) | Bot-Regel ab R1 |
| FR-022 | If a principal who is not a DM participant requests the DM or its messages, then the Server shall return 404. | ST-2.1.2 | Must | R0 | new | |
| FR-023 | When a member posts with `thread_root_id` naming a top-level message of the same Channel, the Server shall store the message as a Thread reply. | ST-2.2.1 | Must | R0 | new | |
| FR-024 | If `thread_root_id` names a reply, a message of another Channel or no message, then the Server shall return 422 (`nested_thread` / `thread_root_not_found`) and store nothing. | ST-2.2.1 | Must | R0 | new | |
| FR-025 | When a Thread reply is stored, the Server shall update the root's `reply_count`, `last_reply_at` and up to 5 latest participant ids and publish `thread.updated`. | ST-2.2.1 | Must | R0 | new (`chat_threads`) | |
| FR-026 | When a user types `@` in a chat composer, the Web-App shall show within 100 ms an autocomplete of Channel members, other humans from the directory, Agentenkonten („Name (Owner)“) and Omnigent-Agenten, filtered by prefix and operable with ↑/↓/Enter/Esc. | ST-2.3.1 | Must | R1 | new (`@tiptap/extension-mention`, `@tiptap/suggestion` 3.30.5) | R0: nur Agent-Handles |
| FR-027 | When a stored body contains tokens `<@user:ID>`, `<@agent_account:ID>` or `<@omnigent_agent:HANDLE>`, the Server shall validate each against existing principals/handles, record one `chat_mentions` row per valid target, and leave invalid tokens as literal text. | ST-2.3.1, ST-3.1.1 | Must | R0 | new | |
| FR-028 | Where dictation is configured, the chat composer shall offer `ComposerMicButton` that inserts transcribed text without sending. | ST-2.1.3 | Could | R1 | exists (`web/src/components/ComposerMicButton.tsx`, `WS /v1/dictation/stream`) | |
| FR-029 | The Server shall treat every root session with direct grants for ≥ 2 distinct principals (ignoring `__public__`) as a Session-Chat (`kind=session`) whose members are those principals, with admins as read-only, except sessions carrying the label `omnigent.chat.v1` (Agent-Thread-Sessions, deren Gespräch der Thread selbst ist); Session-Chats erscheinen in der `/chat`-Sidebar im Abschnitt „Sessions“, sobald sie ≥ 1 Nachricht haben. | ST-2.1.4 | Must | R1 | adapt (PR #4 `omnigent/server/session_chat.py`) | Bestand-Spec S-1, S-4 |
| FR-030 | The Server shall never turn a Session-Chat message into a conversation item or `/events` call. | ST-2.1.4 | Must | R1 | exists (PR #4 Test „POST writes no conversation item“) | |
| FR-031 | The Server shall migrate rows of `session_chat_messages` into `chat_messages` of the corresponding Session-Chat and keep `GET/POST /v1/sessions/{id}/chat` as adapters on the chat store with unchanged response shape. | ST-2.1.4 | Must | R1 | adapt (`omnigent/server/routes/session_chat.py`, PR #4) | ADR-0002 |
| FR-032 | While a member types in a composer, the Web-App shall send at most one `typing` frame per 3 s and other open clients shall show „Anna schreibt…“ until 4 s without refresh. | ST-2.1.1 | Could | R1 | new | Bestand-Spec §8.2, ohne Persistenz |

### Activity A3: Agenten beauftragen
| ID | Requirement (EARS) | Story | Prio | Rel | Status | Notes |
|---|---|---|---|---|---|---|
| FR-033 | When a human posts a message containing an `<@omnigent_agent:HANDLE>` token in a Channel or DM (top-level or in a Thread without active agent), the Server shall create a session as the caller via the existing create path, send the Thread context as first `message` event, and link the Thread (`chat_threads.session_id`, `agent_state=active`). | ST-3.1.1 | Must | R0 | new (`omnigent/server/chat_agent_threads.py`; nutzt Create-Pfad von `POST /v1/sessions` und `POST /v1/sessions/{id}/events`, exists) | A-5, A-8; `host_id` optional (ohne: vom Aufrufer verwalteter Runner wie heute) |
| FR-034 | When an Agent-Thread session is created, the Server shall grant READ on it to every other human member of the Channel; if the sharing mode forbids new grants, then the Server shall skip the grants and post the system line „Live-Ansicht nur für {Aufrufer} (Freigabe am Server deaktiviert)“. | ST-3.1.1 | Must | R0 | adapt (Permission-Store, `omnigent/server/sharing_settings.py`) | Q-9; [OFFEN Q-17] spätere Mitglieder |
| FR-035 | If session creation or the first message fails, then the Server shall set `agent_state=start_failed` and post a system line naming the reason class (`no_project`, `host_offline`, `agent_not_found`, `forbidden`, `timeout`, `internal`) without stack traces. | ST-3.1.1 | Must | R0 | new | |
| FR-036 | If the author of an `<@omnigent_agent:…>` mention is an Agentenkonto, then the Server shall store the message, create no session and post the system line „Nur Menschen können Omnigent-Agenten starten“. | ST-3.1.1, ST-7.1.5 | Must | R1 | new | Kosten-/Loop-Schutz |
| FR-037 | The Server shall build the first agent message from the triggering message and up to 20 preceding Thread messages (max 12 000 characters, newest kept), each prefixed with the author's display name. | ST-3.1.1 | Must | R0 | new | [ANNAHME A-6, A-10] |
| FR-038 | The Server shall resolve the handle `agent` to the `agent_id` of the caller's resolved project (A-8). | ST-3.1.1 | Must | R0 | new | R0-Handle |
| FR-039 | The Server shall expose `GET /v1/chat/agents` returning, for the caller, every agent from the existing agent listing with a unique handle (slugified name, collision suffix `-2`, `-3` by `created_at`), plus all active Agentenkonten. | ST-3.1.2 | Must | R1 | new (liest Agent-Store wie `GET /v1/agents`) | |
| FR-040 | While a Thread has `agent_state=active`, when a human with level ≥ EDIT on its session posts a reply, the Server shall forward it as `message` event (`[Name] text`) to the session, which steers if the session is running. | ST-3.1.3 | Must | R1 | new (Steer exists, `tests/e2e_ui/chat/test_always_steer.py`) | [ANNAHME A-17] |
| FR-041 | If the author of a reply in an active Agent-Thread has level < EDIT on the session, then the Server shall store but not forward the reply and mark it `forward_state=not_forwarded`, and the Web-App shall label it „nicht an Agent“. | ST-3.1.3 | Must | R1 | new | folgt aus Q-8/Q-9 |
| FR-042 | When the Aufrufer or a human with level ≥ EDIT triggers „Entlassen“ (button or `/dismiss`), the Server shall set `agent_state=dismissed` and stop forwarding without stopping the session. | ST-3.1.3 | Must | R1 | new | |
| FR-043 | When a human with level ≥ EDIT mentions the Thread's agent handle in a dismissed Thread, the Server shall set `agent_state=active` again and forward that message to the same session. | ST-3.1.3 | Should | R1 | new | |
| FR-044 | While an Agent-Thread is open, the Web-App shall show the session status chip (`launching`→„startet…“, `running`→„arbeitet“, `waiting`→„braucht Antwort“, `idle`→„fertig“, `failed`→„Fehler“, dismissed→„entlassen“) updated from the session stream. | ST-3.2.1 | Must | R0 | adapt (`web/src/components/SessionStateBadge.tsx`) | Visuelle Sprache der Status-Icons wie TaskSteps (Bogen = arbeitet, Häkchen = fertig, Kreuz = Fehler), ADR-0008 |
| FR-045 | While an Agent-Thread is open and the viewer has READ on the session, the Web-App shall render the session's assistant output streaming via the existing conversation registry (`web/src/store/conversationRegistry.ts`) and AI-Elements `message`/`reasoning`. | ST-3.2.1 | Must | R0 | adapt (`web/src/components/ai-elements/*` exists) | |
| FR-046 | When an Agent-Thread session reaches `idle` after a turn, the Server shall store the turn's final assistant text (max 32 000 characters, else truncated with „… vollständig in der Session“) as a Thread reply with `author_kind=omnigent_agent`. | ST-3.2.1 | Must | R0 | new | [ANNAHME A-11] |
| FR-047 | The Web-App shall show a compact status chip on every Agent-Thread root in the Channel view, fed by the WS watch-set of `/v1/sessions/updates` (`SessionListItem.status`, `pending_elicitations_count`). | ST-3.2.1 | Must | R0 | adapt (WS exists; Rescan 4 s, `_SESSION_UPDATES_RESCAN_INTERVAL_S` in `_sessions/common.py`) | |
| FR-048 | While an Agent-Thread is open, the Web-App shall show tool calls and todos of the current turn collapsed into one row („3 Schritte“) that expands on click or Enter into an ordered step list. | ST-3.2.2 | Should | R1 | new (Referenz: interior.dev TaskSteps, ADR-0008; Komponente `web/src/chat/agent/TaskSteps.tsx`) | |
| FR-089 | The step list shall render each step with status `pending` (Punkt), `active` (drehender Bogen + Shimmer auf dem Label), `done` (Häkchen mit Spring-Pop, Dauer als Mono-Meta rechts) or `error` (Kreuz mit Spring-Pop; folgende Schritte bleiben `pending`). | ST-3.2.2 | Should | R1 | new (Muster interior.dev TaskSteps; Icons lucide `Check`/`X`/`LoaderCircle`, NFR-010) | [OFFEN Q-20] vor Code-Übernahme |
| FR-090 | Where the step list is shown, the Web-App shall announce via an sr-only `role="status"` region „{Label}, Schritt {n} von {m}“ (entprellt 500 ms) and on completion „Lauf abgeschlossen“ or „Lauf fehlgeschlagen bei {Label}“ (polite), mark the active row with `aria-current="step"`, and under reduced motion show status changes without scale, rotation or shimmer. | ST-3.2.2, ST-UX-1 | Must | R1 | new (Muster interior.dev `useReducedMotion`, aria-live) | bedingt: gilt nur, wenn FR-048 umgesetzt ist |
| FR-049 | The Thread composer shall offer buttons and slash commands `/interrupt`, `/retry`, `/stop`, `/dismiss` that map to events `interrupt`, `retry_session`, `stop_session` and FR-042. | ST-3.3.1 | Must | R1 | adapt (`POST /v1/sessions/{id}/events` exists) | |
| FR-050 | If the viewer lacks the level required by a control (EDIT for interrupt/retry/dismiss/steer, OWNER for stop), then the Web-App shall render the control disabled with a tooltip naming the required role, and a slash command shall show that hint and send nothing. | ST-3.3.1, ST-3.3.2 | Must | R1 | new | |
| FR-051 | The stop control shall send `stop_session` only for the session OWNER. | ST-3.3.2 | Must | R1 | exists (Owner-Gate serverseitig) | |
| FR-052 | The Thread header shall contain „Session öffnen“ linking to `/c/{session_id}`. | ST-3.4.1 | Must | R1 | new | |
| FR-053 | While a session is `running` and no stream event arrived for 120 s, the Web-App shall show a run-stuck banner in the Thread with „Unterbrechen“ and „Session öffnen“. | ST-3.3.1 | Should | R1 | new (Muster agent-native) | |
| FR-054 | If forwarding a Thread reply to the session fails, then the Server shall set `forward_state=failed`, and the Web-App shall show „nicht zugestellt – erneut senden“, which retries via `POST /v1/chat/messages/{id}/forward`. | ST-3.1.3 | Should | R1 | new | |

### Activity A4: Auf Agenten reagieren
| ID | Requirement (EARS) | Story | Prio | Rel | Status | Notes |
|---|---|---|---|---|---|---|
| FR-055 | When an Agent-Thread session emits `response.elicitation_request`, the open Thread shall render the matching card (`ApprovalCard`/`ElicitationCard`, `AskUserQuestionForm`) inline at the position of the request. | ST-4.1.1 | Must | R0 | adapt (`web/src/components/blocks/ApprovalCard.tsx`, `AskUserQuestionForm.tsx`) | |
| FR-056 | The card shall be actionable only for viewers with level ≥ EDIT (`ELICITATION_RESOLVE_LEVEL`), and read-only with „Nur Aufrufer und Bearbeiter können antworten“ for others. | ST-4.1.1 | Must | R0 | exists serverseitig (`routes_elicitations.py:60`), new UI-Zustand | Q-8 |
| FR-057 | When an elicitation is resolved anywhere (Thread, `InboxPage`, `ApprovePage`, MCP), the Thread card shall switch to the resolved state with verdict and resolver within 2 s. | ST-4.1.1 | Must | R0 | adapt (`response.elicitation_resolved` exists; Muster `tests/e2e_ui/approvals/test_resolved_elsewhere_shows_verdict.py`) | |
| FR-058 | If resolving returns 403 or reports the elicitation already resolved, then the card shall show the server's verdict and no error toast. | ST-4.1.1 | Must | R0 | adapt | |
| FR-059 | When the session requests plan approval, the Thread shall render `ExitPlanModeReview` with the same EDIT rule. | ST-4.2.1 | Should | R1 | adapt (`web/src/components/blocks/ExitPlanModeReview.tsx`) | |

### Activity A5: Code & Tickets
| ID | Requirement (EARS) | Story | Prio | Rel | Status | Notes |
|---|---|---|---|---|---|---|
| FR-060 | When an Agent-Thread session has an attached pull request, the Thread shall show a read-only PR card (title, number, state, CI status, link). | ST-5.1.1 | Should | R1 | adapt (`web/src/shell/PullRequestPanel.tsx`, `…/resources/github/prs` exists, `routes_resources.py:2979`) | ohne GitHub-App: keine Karte |

### Activity A7: Externe Agenten (Grok Bots)
| ID | Requirement (EARS) | Story | Prio | Rel | Status | Notes |
|---|---|---|---|---|---|---|
| FR-061 | When a human calls `POST /v1/chat/agent-accounts` with `display_name` (1–40 chars), `handle` (`^[a-z0-9][a-z0-9-]{1,39}$`, unique among handles) and `webhook_url`, the Server shall create an Agentenkonto with principal `agent:<uuid>` owned by the caller and return a bearer token (prefix `ogc_`, ≥ 256 bit) and the webhook secret exactly once. | ST-7.1.4 | Must | R1 | new | ADR-0004 |
| FR-062 | The Web-App shall display every Agentenkonto as „{display_name} ({Owner-Anzeigename})“ with a „Bot“ badge in messages, member list and autocomplete. | ST-7.1.4 | Must | R1 | new | |
| FR-063 | If a human already owns 5 non-revoked Agentenkonten, then the Server shall reject creation with 409 `agent_account_limit`. | ST-7.1.4 | Should | R1 | new | [ANNAHME A-16] |
| FR-064 | When the owner or an admin calls `POST /v1/chat/agent-accounts/{id}/rotate` or `DELETE /v1/chat/agent-accounts/{id}`, the Server shall invalidate the previous token for all subsequent requests (rotate returns a new token once; delete sets `revoked_at` and removes all memberships). | ST-7.1.4 | Must | R1 | new | |
| FR-065 | If `webhook_url` is not `https`, or its host resolves to a loopback, private, link-local or otherwise internal address, then the Server shall reject it with 422 at creation and skip delivery at send time. | ST-7.1.4 | Must | R1 | adapt (`omnigent/util/ssrf.py` `host_is_internal`) | Test-Ausnahme analog PR #15 `allow_insecure_localhost` |
| FR-066 | When a request carries an Agentenkonto token, the Server shall authenticate it only for `/mcp` and `/v1/chat/*` and treat every other path as unauthenticated (401). | ST-7.1.1 | Must | R1 | adapt (`omnigent/server/auth.py`) | eigener Zweig, `_DELEGATED_ALLOWED_PREFIXES` bleibt unverändert |
| FR-067 | The Server shall allow an Agentenkonto to read or post in a Channel only while it is a member and its owner is currently a member; in a DM only while its owner is a participant [OFFEN Q-13]. | ST-7.1.1 | Must | R1 | new | „sieht nie mehr als Owner“ |
| FR-068 | If an Agentenkonto attempts to create a Channel, join a Channel its owner is not in, access a Session-Chat, read a session, resolve an elicitation, or create/rotate Agentenkonten, then the Server shall return 403 (401 for non-chat paths) and change nothing. | ST-7.1.1 | Must | R1 | new | |
| FR-069 | Where `team_chat` is enabled, `/mcp` shall offer tools `chat_list_channels`, `chat_read`, `chat_post`, `chat_open_dm`, `chat_list_members`, `chat_unread`, `chat_mark_read` that call the `/v1/chat/*` routes in-process; write tools shall require `confirm: true` as all PR #14 writes. | ST-7.1.6 | Must | R1 | adapt (`omnigent/server/mcp_endpoint.py`, PR #14) | auch für User-JWT |
| FR-070 | When `/mcp` is called with an Agentenkonto token, `tools/list` shall return only `chat_*` tools and `tools/call` of any other tool shall return a tool error without calling `/v1`. | ST-7.1.6 | Must | R1 | adapt (PR #14) | |
| FR-071 | When a stored message mentions an Agentenkonto or is posted in a DM with an Agentenkonto, the Server shall insert one `chat.mention` outbox row per Agentenkonto in the same transaction, unless the Bot-Bremse is active or the author is that Agentenkonto. | ST-7.1.2, ST-7.1.6 | Should | R1.1 | new (Muster PR #15 `omnigent/server/session_outbox.py`) | verschoben (Q-18 entschieden); R1 nutzt FR-088 |
| FR-072 | The Server shall deliver each outbox row as HTTPS POST with the PR #15 envelope fields and `X-Omnigent-Signature: v1=<hmac>` (via `manager_webhook_signing.sign` with the account secret), retry non-2xx with exponential backoff (≈ 5 s·2^n, max 15 min, jitter), mark `dead_letter` after 10 attempts, and keep order per Agentenkonto. | ST-7.1.2 | Should | R1.1 | adapt (PR #15 `manager_webhook_signing.py`, `manager_webhook_dispatcher.py`) | Payload ohne Body [ANNAHME A-14] |
| FR-073 | While an Agentenkonto has dead-lettered outbox rows, the Web-App shall show „Webhook fehlerhaft“ on the account in settings and in the member list, with an „Erneut zustellen“ action for the owner. | ST-7.1.2, ST-7.1.3 | Should | R1.1 | new | |
| FR-074 | If a Thread's `bot_streak` is 5, then the Server shall reject posts by Agentenkonten in that Thread with 409 `bot_streak_limit`, enqueue no `chat.mention` rows for it, and post the system line „Bots warten auf einen Menschen“ once. | ST-7.1.5 | Must | R1 | new | Streak zählt Agentenkonto- und gespiegelte Agent-Nachrichten |
| FR-075 | When a human posts in a Thread, the Server shall reset that Thread's `bot_streak` to 0. | ST-7.1.5 | Must | R1 | new | |
| FR-076 | The member list and autocomplete shall show for each Agentenkonto a status: „aktiv“ (authenticated request in the last 10 min), „inaktiv“, „Webhook fehlerhaft“; revoked accounts shall not appear in autocomplete. | ST-7.1.3 | Should | R1 | new | |
| FR-077 | The Server shall never forward messages authored by an Agentenkonto to an Omnigent session. | ST-7.1.5 | Must | R1 | new | Agentenkonto hat keinen Session-Grant |

### Activity UX: Erlebnis
| ID | Requirement (EARS) | Story | Prio | Rel | Status | Notes |
|---|---|---|---|---|---|---|
| FR-078 | The Web-App shall animate with Motion (`motion` package, `motion/react`): message enter (opacity 0→1, y 8→0 px, 180 ms), status-chip change (crossfade 200 ms), card mount (spring, settle ≤ 300 ms), unread badge change (scale pulse 150 ms), thread pane open (slide 220 ms). | ST-UX-1 | Must | R1 | new (Dependency `motion`) | Werte = NFR-007 |
| FR-079 | While the OS reports `prefers-reduced-motion: reduce`, the Web-App shall replace all transform animations of FR-078 by opacity changes ≤ 120 ms or none. | ST-UX-1 | Must | R1 | new (`MotionConfig reducedMotion="user"`) | |
| FR-080 | While assistant text streams, the Web-App shall smooth rendering (buffered character release, ≤ 1 visual update per animation frame) and keep the view anchored at the bottom only when the user was at the bottom; otherwise it shall show a „Neue Nachrichten“ pill. | ST-UX-1, ST-3.2.1 | Must | R1 | new (Muster agent-native/prompt-kit) | R0: Anker ohne Glättung |
| FR-081 | When an Agent-Thread is empty or a Channel has no messages, the Web-App shall show up to 3 suggested prompts that insert text into the composer on click. | ST-UX-1 | Could | R1 | new (Slack-Muster) | |

### Activity Zuverlässigkeit (querliegend)
| ID | Requirement (EARS) | Story | Prio | Rel | Status | Notes |
|---|---|---|---|---|---|---|
| FR-082 | When the WS connection reconnects, the Web-App shall call `GET /v1/chat/changes?after=<changes_cursor>` and merge returned messages, thread and read updates deduplicated by id before applying new frames. | ST-2.1.1 | Must | R0 | new | |
| FR-083 | If the `changes` cursor is older than 7 days or more than 1000 changes are pending, then the Server shall return `reset: true` and the Web-App shall reload bootstrap and open views. | ST-2.1.1 | Should | R1 | new | |
| FR-084 | If sending fails (network, 429, 5xx), then the Web-App shall keep the message as „nicht gesendet“ with „erneut senden“ reusing the same `client_id`, and keep the composer usable. | ST-2.1.1 | Must | R0 | new | |
| FR-085 | The Web-App shall hide sessions labelled `omnigent.chat.v1` from the main session sidebar for every viewer who is not the session owner, and list them in the owning Thread instead. | ST-3.1.1 | Must | R1 | adapt (`web/src/shell/Sidebar.tsx`, `web/src/hooks/useConversations.ts`; `SessionListItem.labels` exists) | verhindert Sidebar-Flut durch READ-Grants (ADR-0005) |
| FR-086 | When a human joins a Channel, the Server shall grant READ on the sessions of all Agent-Threads of that Channel (unless the sharing mode forbids new grants). | ST-3.1.1 | Should | R1 | new | Default zu [OFFEN Q-17] |
| FR-088 | When an Agentenkonto calls `chat_unread` (MCP) or `GET /v1/chat/unread`, the Server shall return its unread messages that mention it or were posted in its DMs (oldest first, at most 50, each with `channel_id`, `thread_root_id`, `message_id`, author, body), and `chat_mark_read` shall advance the account's read cursor past them. | ST-7.1.2 | Must | R1 | new (`chat_reads` je Principal, FR-007) | Wecken per Polling-Routine des Grok Bots, Intervall ≥ 5 min [ANNAHME A-23]; Webhook-Wecken R1.1 |
| FR-087 | If a Thread stays in `agent_state=starting` for more than 120 s (z. B. Server-Neustart während des Starts), then the Server shall set `start_failed` with reason `timeout` and post the system line. | ST-3.1.1 | Must | R0 | new | Zustand liegt nur in `chat_threads` |

#### Acceptance criteria
Testdaten für alle ACs: Workspace 0; Menschen `anna@bloopark.de` (Anna), `jens@bloopark.de` (Jens), `bob@bloopark.de` (Bob), `carla@bloopark.de` (Carla); Channels `#general`, `#billing` (Mitglieder Anna, Jens, Bob); Agent „coder“; Annas Projekt „billing“ mit `agent_id=coder`, `host_id=h-anna` (online); Agentenkonto „Grok“ `grok-jens` (Owner Jens). Mit „E2E“ markierte ACs werden als Playwright-Test in `tests/e2e_ui/team_chat/` umgesetzt (Mock-LLM); alle übrigen als pytest/Vitest.

**AC-001a (FR-001) – E2E**
```gherkin
Given team_chat ist aktiv und Anna ist angemeldet
When Anna /chat öffnet
Then sieht sie in der Chat-Sidebar "#general" unter "Channels" und einen leeren Abschnitt "Direktnachrichten"
```
**AC-001b (FR-001, FR-003) – E2E**
```gherkin
Given team_chat ist nicht aktiv
When Anna /chat öffnet
Then zeigt die Web-App "Chat ist auf diesem Server nicht aktiviert" und die Hauptsidebar enthält keinen Eintrag "Chat"
```
**AC-002a (FR-002)**
```gherkin
Given der Server läuft mit OMNIGENT_FEATURES=team_chat
When GET /v1/info aufgerufen wird
Then enthält features den Schlüssel "team_chat" mit true
```
**AC-003a (FR-003)**
```gherkin
Given team_chat ist nicht aktiv
When Anna POST /v1/chat/channels/{general}/messages mit body "Hallo" sendet
Then antwortet der Server 404 und chat_messages enthält keine neue Zeile
```
**AC-003b (FR-003)**
```gherkin
Given team_chat ist nicht aktiv
When ein User-JWT tools/list auf /mcp aufruft
Then enthält die Liste kein Tool mit Präfix "chat_"
```
**AC-004a (FR-004)**
```gherkin
Given es existiert kein Channel "general"
When Anna und Jens gleichzeitig GET /v1/chat/bootstrap aufrufen
Then existiert genau ein Channel "general" und beide sind Mitglied
```
**AC-004b (FR-004)**
```gherkin
When das Agentenkonto grok-jens GET /v1/chat/bootstrap aufruft
Then wird grok-jens nicht Mitglied von "general"
```
**AC-005a (FR-005)**
```gherkin
When Anna POST /v1/chat/channels mit name "billing-ops" sendet
Then antwortet der Server 201 und Anna ist Mitglied von "#billing-ops"
```
**AC-005b (FR-005)**
```gherkin
When Anna POST /v1/chat/channels mit name "Billing Ops!" oder dem bestehenden Namen "billing" sendet
Then antwortet der Server 422 bzw. 409 und es wird kein Channel angelegt
```
**AC-006a (FR-006)**
```gherkin
Given Carla ist nicht Mitglied von "#billing"
When Carla POST /v1/chat/channels/{billing}/join sendet
Then ist Carla Mitglied und "#billing" erscheint in ihrer Sidebar
```
**AC-006b (FR-006)**
```gherkin
When Anna POST /v1/chat/channels/{general}/leave sendet
Then antwortet der Server 409 und Anna bleibt Mitglied
```
**AC-007a (FR-007)**
```gherkin
Given Annas Cursor in "#billing" steht auf m10 und m11, m12 existieren
When Anna POST .../read mit dem Cursor von m12 sendet
Then steht ihr Cursor auf m12
```
**AC-007b (FR-007)**
```gherkin
Given Annas Cursor steht auf m12
When ein zweiter Tab POST .../read mit dem Cursor von m11 sendet
Then bleibt der Cursor auf m12 und der Server antwortet 204
```
**AC-007c (FR-007)**
```gherkin
When Anna POST .../read mit einer id sendet, die in "#billing" nicht existiert
Then antwortet der Server 422 und der Cursor bleibt unverändert
```
**AC-008a (FR-008)**
```gherkin
Given Annas Cursor steht auf m10, danach folgen m11 von Jens mit "<@user:anna@bloopark.de>" und m12 von Anna
When Anna GET /v1/chat/bootstrap aufruft
Then ist für "#billing" unread_count 1 und mention_count 1
```
**AC-008b (FR-008)**
```gherkin
Given "#general" hat 0 Nachrichten und Anna hat nie gelesen
Then ist für "#general" unread_count 0 und mention_count 0
```
**AC-009a (FR-009) – E2E**
```gherkin
Given Anna hat /chat in zwei Browser-Kontexten offen und "#billing" zeigt in beiden Badge "2"
When sie "#billing" in Kontext A öffnet
Then zeigt Kontext B binnen 1 s kein Badge mehr für "#billing"
```
**AC-009b (FR-009)**
```gherkin
Given Jens hat eine WS-Verbindung offen
When Anna ihren Cursor bewegt
Then erhält Jens keinen Frame read.updated
```
**AC-010a (FR-010) – E2E**
```gherkin
Given Jens schreibt in "#billing" "<@user:anna@bloopark.de> schau mal" und Annas Agent-Thread-Session wartet auf Freigabe
When Anna die Braucht-dich-Ansicht öffnet
Then sieht sie beide Einträge und ein Klick öffnet den jeweiligen Thread
```
**AC-010b (FR-010)**
```gherkin
Given Bob hat nur READ auf Annas wartender Agent-Thread-Session
When Bob die Braucht-dich-Ansicht öffnet
Then enthält sie diese Freigabe nicht
```
**AC-011a (FR-011)**
```gherkin
Given Anna hat Benachrichtigungen erlaubt und das Fenster ist nicht fokussiert
When Jens Anna eine DM "Kurz Zeit?" schickt
Then erscheint eine OS-Benachrichtigung "Neue Direktnachricht von Jens" ohne den Text "Kurz Zeit?"
```
**AC-012a (FR-012)**
```gherkin
Given die Berechtigung ist "denied"
When eine Erwähnung eintrifft
Then wird Notification.requestPermission nicht aufgerufen und das Badge zählt hoch
```
**AC-013a (FR-013, FR-014) – E2E**
```gherkin
Given Anna und Jens haben "#general" offen
When Anna "Deploy heute 16 Uhr?" sendet
Then sieht Jens die Nachricht ohne Reload binnen 1 s mit Autor "Anna" und Uhrzeit
```
**AC-013b (FR-013)**
```gherkin
When Anna POST mit body "  Hallo  ", client_id "c-1" und einem zusätzlichen Feld created_by "jens@bloopark.de" sendet
Then antwortet der Server 201, der gespeicherte body ist "Hallo" und der Autor ist "anna@bloopark.de"
```
**AC-014a (FR-014)**
```gherkin
When Anna eine Nachricht in "#billing" speichert
Then erhält jede WS-Verbindung von Jens und Bob einen Frame {type:"chat", kind:"message.created"} ohne Feld body
```
**AC-014b (FR-014)**
```gherkin
Given Carla ist nicht Mitglied von "#billing"
When Anna in "#billing" postet
Then erhält Carla keinen Frame
```
**AC-015a (FR-015)**
```gherkin
When Anna body "   " sendet
Then antwortet der Server 422 und es wird nichts gespeichert
```
**AC-015b (FR-015)**
```gherkin
When Anna einen body mit 4001 Zeichen oder "a\u0000b" sendet
Then antwortet der Server 422 und es wird nichts gespeichert
```
**AC-015c (FR-015)**
```gherkin
When Anna einen body mit genau 4000 Zeichen sendet
Then antwortet der Server 201
```
**AC-016a (FR-016)**
```gherkin
Given Carla ist nicht Mitglied von "#billing"
When Carla dort "Hi" postet
Then antwortet der Server 403 mit code "not_a_member" und speichert nichts
```
**AC-016b (FR-016)**
```gherkin
Given Bob ist nicht Teilnehmer der DM Anna–Jens
When Bob in diese DM postet
Then antwortet der Server 404
```
**AC-017a (FR-017)**
```gherkin
Given Anna hat in den letzten 60 s 30 Posts in "#general" versucht
When sie den 31. Post sendet
Then antwortet der Server 429 mit Retry-After und speichert nichts
```
**AC-017b (FR-017)**
```gherkin
Given Anna hat 30 Posts in "#general" versucht
When sie in "#billing" postet
Then antwortet der Server 201
```
**AC-018a (FR-018)**
```gherkin
Given Anna hat "Hallo" mit client_id "c-1" gespeichert (id m1)
When sie denselben Request erneut sendet
Then antwortet der Server 200 mit id m1 und es gibt genau eine Zeile
```
**AC-018b (FR-018)**
```gherkin
When Anna client_id "c-1" mit body "Anders" sendet
Then antwortet der Server 409 und m1 bleibt "Hallo"
```
**AC-019a (FR-019)**
```gherkin
Given "#general" hat 120 Top-Level-Nachrichten
When Anna GET .../messages?tail=1 aufruft
Then erhält sie die neuesten 50 aufsteigend sortiert mit has_older true
```
**AC-019b (FR-019)**
```gherkin
When Anna before und after zusammen oder limit 500 sendet
Then antwortet der Server 422
```
**AC-020a (FR-020) – E2E**
```gherkin
When Anna "<script>alert(1)</script> **fett** https://example.com" sendet
Then zeigt Jens' Ansicht "<script>alert(1)</script> **fett**" als Text und einen Link auf https://example.com, und es wird kein Script ausgeführt
```
**AC-021a (FR-021) – E2E**
```gherkin
When Anna zweimal eine DM mit Jens öffnet
Then erhält sie beide Male dieselbe Channel-ID und Jens sieht die DM in "Direktnachrichten" nach Annas erster Nachricht
```
**AC-021b (FR-021)**
```gherkin
When Anna POST /v1/chat/dms mit 9 anderen Principals sendet
Then antwortet der Server 422
```
**AC-022a (FR-022)**
```gherkin
Given die DM Anna–Jens existiert
When Bob GET .../messages dieser DM aufruft
Then antwortet der Server 404
```
**AC-023a (FR-023) – E2E**
```gherkin
Given Annas Top-Level-Nachricht m1 in "#general"
When Jens im Thread von m1 "Passt" antwortet
Then zeigt m1 bei Anna "1 Antwort" und der Thread zeigt "Passt"
```
**AC-024a (FR-024)**
```gherkin
Given r1 ist eine Antwort im Thread von m1
When Jens mit thread_root_id r1 postet
Then antwortet der Server 422 mit code "nested_thread"
```
**AC-024b (FR-024)**
```gherkin
When Jens in "#general" mit thread_root_id einer Nachricht aus "#billing" postet
Then antwortet der Server 422 mit code "thread_root_not_found"
```
**AC-025a (FR-025)**
```gherkin
When Jens und danach Bob je eine Antwort auf m1 posten
Then hat m1 reply_count 2, last_reply_at der Antwort von Bob und Teilnehmer [Bob, Jens]
```
**AC-026a (FR-026) – E2E**
```gherkin
Given Anna tippt "@je" im Composer von "#general"
Then erscheinen binnen 100 ms "Jens" und "Grok (Jens Holze)" mit Badge "Bot", und Enter fügt den markierten Eintrag als Chip ein
```
**AC-026b (FR-026)**
```gherkin
Given Anna tippt "@zz"
Then zeigt die Liste "Niemand gefunden" und es wird kein Chip eingefügt
```
**AC-027a (FR-027)**
```gherkin
When Jens "<@user:anna@bloopark.de> bitte prüfen" postet
Then existiert ein chat_mentions-Eintrag für anna@bloopark.de
```
**AC-027b (FR-027)**
```gherkin
When Jens "<@user:nobody@x.de> hallo" postet
Then wird kein chat_mentions-Eintrag erzeugt und der Text bleibt wörtlich erhalten
```
**AC-028a (FR-028)**
```gherkin
Given Diktat ist konfiguriert
When Anna im Chat-Composer "Bin gleich da" diktiert
Then steht "Bin gleich da" im Composer und es wurde nichts gesendet
```
**AC-029a (FR-029) – E2E**
```gherkin
Given Jens teilt Session S1 mit Anna (EDIT)
When Anna S1 öffnet
Then ist im ChatHeader der Session-Chat erreichbar und zeigt die bisherigen Nachrichten
```
**AC-029b (FR-029)**
```gherkin
Given S1 hat nur Jens als direkten Grant und einen "__public__"-Grant
When ein Public-Link-Besucher GET /v1/sessions/S1/chat aufruft
Then antwortet der Server 403
```
**AC-030a (FR-030)**
```gherkin
When Anna im Session-Chat von S1 "Nur für Menschen" postet
Then entsteht kein Conversation-Item in S1 und kein /events-Aufruf
```
**AC-031a (FR-031)**
```gherkin
Given session_chat_messages enthält 3 Zeilen für Root S1
When die Migration läuft
Then enthält chat_messages 3 Zeilen im Session-Chat von S1 mit gleichen id, created_by, created_at und body
```
**AC-031b (FR-031)**
```gherkin
When GET /v1/sessions/S1/chat nach der Migration aufgerufen wird
Then entspricht die Antwortform der bisherigen und tests/server/test_session_chat.py bleibt grün
```
**AC-032a (FR-032)**
```gherkin
Given Anna tippt in "#general"
Then sieht Jens "Anna schreibt…" und es verschwindet 4 s nach Annas letztem Tastendruck
```
**AC-033a (FR-033, FR-038) – E2E**
```gherkin
Given Anna ist in "#billing"
When sie "<@omnigent_agent:agent> bitte Tests für billing fixen" sendet
Then entsteht ein Thread an der Nachricht mit Chip "startet…" oder "arbeitet", die Session gehört Anna, liegt im Projekt "billing" mit agent_id "coder" und erhält als erste Nachricht den Text mit Präfix "[Anna]"
```
**AC-033b (FR-033)**
```gherkin
Given der Thread von m1 hat bereits agent_state "active"
When Anna darin erneut den Agenten erwähnt
Then wird keine zweite Session angelegt
```
**AC-034a (FR-034)**
```gherkin
When Anna in "#billing" einen Agent-Thread startet
Then haben Jens und Bob READ (1) auf der Session und Anna OWNER (4)
```
**AC-034b (FR-034)**
```gherkin
Given OMNIGENT_SHARING_MODE=off
When Anna einen Agent-Thread startet
Then werden keine Grants angelegt und im Thread steht "Live-Ansicht nur für Anna (Freigabe am Server deaktiviert)"
```
**AC-035a (FR-035)**
```gherkin
Given Anna hat kein Projekt "billing" und kein Chat-Standardprojekt
When sie in "#billing" den Agenten erwähnt
Then wird keine Session angelegt, agent_state ist "start_failed" und der Thread zeigt "Kein Projekt für Agenten-Start – Standard in Einstellungen setzen"
```
**AC-035b (FR-035)**
```gherkin
Given Annas Host h-anna ist offline
When sie den Agenten erwähnt
Then zeigt der Thread eine Systemzeile mit Grund "host_offline" und keinen Stacktrace
```
**AC-036a (FR-036)**
```gherkin
When grok-jens in "#general" "<@omnigent_agent:coder> mach was" postet
Then wird die Nachricht gespeichert, keine Session angelegt und die Systemzeile "Nur Menschen können Omnigent-Agenten starten" erscheint
```
**AC-037a (FR-037)**
```gherkin
Given der Thread von m1 hat 25 Nachrichten
When Anna darin den Agenten erwähnt
Then enthält die erste Session-Nachricht höchstens die 20 vorhergehenden Nachrichten plus die auslösende, höchstens 12000 Zeichen, jeweils mit "[Name]"
```
**AC-038a (FR-038)**
```gherkin
Given Annas Projekt "billing" hat keinen agent_id
When Anna "@agent" erwähnt
Then zeigt der Thread den Grund "agent_not_found" und es entsteht keine Session
```
**AC-039a (FR-039)**
```gherkin
Given die Agenten "Coder DeepSeek" (älter) und "coder deepseek" (neuer) sind für Anna sichtbar
When Anna GET /v1/chat/agents aufruft
Then erhält sie die Handles "coder-deepseek" und "coder-deepseek-2"
```
**AC-039b (FR-039)**
```gherkin
Given ein Agent ist für Anna nicht sichtbar
Then fehlt er in GET /v1/chat/agents und eine Erwähnung seines Handles startet keine Session (agent_not_found)
```
**AC-040a (FR-040) – E2E**
```gherkin
Given der Agent-Thread ist active und die Session läuft
When Anna im Thread "bitte auch Linter" antwortet
Then erhält die Session die Nachricht "[Anna] bitte auch Linter" und die Antwort trägt keine Markierung "nicht an Agent"
```
**AC-041a (FR-041) – E2E**
```gherkin
Given Bob hat READ auf der Session
When Bob im aktiven Agent-Thread antwortet
Then wird seine Antwort gespeichert, nicht weitergeleitet und mit "nicht an Agent" markiert
```
**AC-042a (FR-042)**
```gherkin
When Anna im Thread "/dismiss" sendet
Then ist agent_state "dismissed", der Chip zeigt "entlassen" und Annas nächste Antwort wird nicht weitergeleitet
```
**AC-042b (FR-042)**
```gherkin
When Bob (READ) "Entlassen" auslöst
Then antwortet der Server 403 und agent_state bleibt "active"
```
**AC-043a (FR-043)**
```gherkin
Given der Thread ist dismissed
When Anna "<@omnigent_agent:agent> weiter bitte" antwortet
Then ist agent_state "active" und dieselbe Session erhält die Nachricht
```
**AC-044a (FR-044) – E2E**
```gherkin
Given Anna hat den Agent-Thread offen
When die Session nach waiting wechselt
Then zeigt der Chip binnen 1 s "braucht Antwort"
```
**AC-045a (FR-045) – E2E**
```gherkin
Given das Mock-LLM streamt "Tests sind grün" in 5 Teilen
When Jens (READ) den Thread öffnet
Then sieht er den Text fortlaufend erscheinen, ohne Reload
```
**AC-045b (FR-045)**
```gherkin
Given Bob hat keinen Grant (Sharing-Modus off)
When Bob den Thread öffnet
Then sieht er statt der Live-Ansicht den Hinweis "Live-Ansicht nur für Anna" und die gespiegelten Antworten
```
**AC-046a (FR-046) – E2E**
```gherkin
When der Turn mit Assistant-Text "Tests grün, PR #42 offen" idle wird
Then enthält der Thread eine Nachricht mit author_kind "omnigent_agent" und genau diesem Text, auch nach Reload
```
**AC-046b (FR-046)**
```gherkin
Given der finale Assistant-Text hat 50000 Zeichen
Then enthält die gespiegelte Nachricht 32000 Zeichen gefolgt von "… vollständig in der Session"
```
**AC-047a (FR-047) – E2E**
```gherkin
Given Jens sieht "#billing" ohne offenen Thread
When Annas Agent-Session von running nach waiting wechselt
Then zeigt der Chip an der Thread-Wurzel binnen 5 s "braucht Antwort"
```
**AC-048a (FR-048)**
```gherkin
Given der Agent ruft im Turn 3 Tools auf
Then zeigt der offene Thread eine Zeile "3 Schritte" und Enter darauf klappt die Tool-Liste auf
```
**AC-048b (FR-048, FR-089) – E2E**
```gherkin
Given der Agent arbeitet die Todos "Tests lesen", "Fix schreiben", "Tests ausführen", "PR öffnen" ab und steht bei "Tests ausführen"
When Anna die Schritte aufklappt
Then zeigen "Tests lesen" und "Fix schreiben" ein Häkchen mit Dauer (z. B. "3.4s") in Monospace, "Tests ausführen" einen drehenden Bogen mit Shimmer-Label und "PR öffnen" einen Punkt
```
**AC-089a (FR-089)**
```gherkin
Given der Schritt "Tests ausführen" schlägt fehl
Then zeigt er ein rotes Kreuz, "PR öffnen" bleibt pending ohne Dauer, und kein späterer Schritt wird als erledigt dargestellt
```
**AC-090a (FR-090) – E2E**
```gherkin
Given Anna nutzt einen Screenreader und die Schritte sind aufgeklappt
When der Agent von Schritt 2 zu Schritt 3 "Tests ausführen" wechselt
Then enthält die role="status"-Region nach höchstens 600 ms "Tests ausführen, Schritt 3 von 4", die Zeile hat aria-current="step"
And nach dem letzten Schritt meldet die polite-Region "Lauf abgeschlossen"
```
**AC-090b (FR-090) – E2E**
```gherkin
Given der Browser emuliert prefers-reduced-motion: reduce
When ein Schritt von active zu done wechselt
Then erscheint das Häkchen ohne Skalierung (nur Opacity ≤ 120 ms), der Bogen dreht sich nicht und das aktive Label hat keinen Shimmer
```
**AC-049a (FR-049) – E2E**
```gherkin
Given die Session läuft
When Anna "/interrupt" sendet
Then wird ein Event type "interrupt" gesendet und der Chip wechselt binnen 1 s auf "fertig"
```
**AC-049b (FR-049)**
```gherkin
When Anna "/unbekannt" sendet
Then wird "/unbekannt" als normale Thread-Nachricht behandelt
```
**AC-050a (FR-050) – E2E**
```gherkin
Given Bob hat READ
When Bob "/retry" eingibt
Then zeigt der Composer "Keine Berechtigung: nur Aufrufer und Bearbeiter" und es wird nichts gesendet
```
**AC-051a (FR-051)**
```gherkin
Given Jens hat EDIT auf Annas Session
Then ist der Stop-Button für Jens deaktiviert mit Tooltip "Nur Anna (Owner) kann stoppen"
```
**AC-051b (FR-051)**
```gherkin
When Anna "Stop" klickt
Then wird stop_session gesendet
```
**AC-052a (FR-052) – E2E**
```gherkin
When Jens "Session öffnen" klickt
Then navigiert die Web-App zu /c/{session_id} und zeigt das Transkript lesend
```
**AC-053a (FR-053)**
```gherkin
Given die Session ist running und seit 120 s kam kein Stream-Event
Then zeigt der Thread das Banner "Agent reagiert nicht" mit "Unterbrechen" und "Session öffnen"
```
**AC-054a (FR-054)**
```gherkin
Given der Runner ist offline und Anna antwortet im aktiven Thread
Then zeigt ihre Nachricht "nicht zugestellt – erneut senden" und ein Klick nach Wiederverbindung leitet sie genau einmal weiter
```
**AC-055a (FR-055) – E2E**
```gherkin
Given das Mock-LLM fordert eine Freigabe "Migration anwenden?" an
When Anna den Thread offen hat
Then erscheint im Thread eine Freigabe-Karte mit "Genehmigen" und "Ablehnen"
```
**AC-056a (FR-056) – E2E**
```gherkin
When Anna "Genehmigen" klickt
Then antwortet resolve 202 und die Karte zeigt "Genehmigt von Anna"
```
**AC-056b (FR-056) – E2E**
```gherkin
Given Bob hat READ
Then zeigt Bobs Karte keine Buttons und den Text "Nur Aufrufer und Bearbeiter können antworten"
```
**AC-057a (FR-057) – E2E**
```gherkin
Given Annas Thread zeigt eine offene Freigabe
When Jens (EDIT) sie in der InboxPage genehmigt
Then zeigt Annas Karte binnen 2 s "Genehmigt von Jens"
```
**AC-058a (FR-058)**
```gherkin
Given die Freigabe ist bereits beantwortet und Annas Karte ist veraltet
When Anna "Ablehnen" klickt
Then zeigt die Karte das gespeicherte Ergebnis "Genehmigt von Jens" ohne Fehler-Toast
```
**AC-059a (FR-059)**
```gherkin
When der Agent einen Plan zur Freigabe vorlegt
Then zeigt der Thread ExitPlanModeReview und Annas Freigabe setzt die Session fort
```
**AC-060a (FR-060)**
```gherkin
Given die Session hat PR #42 (open, CI grün) verknüpft
Then zeigt der Thread eine PR-Karte "#42" mit Status "open", CI "grün" und Link
```
**AC-060b (FR-060)**
```gherkin
Given die GitHub-App ist nicht verbunden
Then zeigt der Thread keine PR-Karte und keine Fehlermeldung
```
**AC-061a (FR-061) – E2E**
```gherkin
When Jens in den Einstellungen ein Agentenkonto "Grok", Handle "grok-jens", Webhook "https://bot.example.com/hook" anlegt
Then werden ein Token mit Präfix "ogc_" und ein Secret genau einmal angezeigt und nach Schließen des Dialogs nicht mehr
```
**AC-061b (FR-061)**
```gherkin
Given Handle "grok-jens" existiert
When Anna ein Konto mit Handle "grok-jens" anlegt
Then antwortet der Server 409
```
**AC-062a (FR-062)**
```gherkin
When grok-jens in "#general" postet
Then zeigt die Nachricht den Autor "Grok (Jens Holze)" mit Badge "Bot"
```
**AC-063a (FR-063)**
```gherkin
Given Jens besitzt 5 aktive Agentenkonten
When er ein sechstes anlegt
Then antwortet der Server 409 mit code "agent_account_limit"
```
**AC-064a (FR-064)**
```gherkin
When Jens das Token von grok-jens rotiert
Then liefert der alte Token auf /mcp 401 und der neue Token funktioniert
```
**AC-064b (FR-064)**
```gherkin
When Anna (kein Admin) DELETE auf Jens' Agentenkonto aufruft
Then antwortet der Server 404 und das Konto bleibt aktiv
```
**AC-065a (FR-065)**
```gherkin
When Jens Webhook "http://bot.example.com/hook" oder "https://10.0.0.5/hook" angibt
Then antwortet der Server 422
```
**AC-066a (FR-066)**
```gherkin
When grok-jens GET /v1/sessions mit seinem Token aufruft
Then antwortet der Server 401
```
**AC-066b (FR-066)**
```gherkin
When grok-jens GET /v1/chat/channels aufruft
Then antwortet der Server 200 mit genau den Channels, in denen grok-jens und Jens Mitglied sind
```
**AC-067a (FR-067)**
```gherkin
Given grok-jens und Jens sind Mitglied in "#billing"
When Jens "#billing" verlässt
Then liefert der nächste chat_read von grok-jens auf "#billing" 403
```
**AC-068a (FR-068)**
```gherkin
When grok-jens POST /v1/chat/channels oder POST /v1/sessions/S1/elicitations/e1/resolve aufruft
Then antwortet der Server 403 bzw. 401 und ändert nichts
```
**AC-069a (FR-069)**
```gherkin
Given Claude Code ist mit Annas User-JWT an /mcp verbunden
When es chat_post mit channel "general", body "Build läuft" und confirm true aufruft
Then erscheint "Build läuft" von Anna in "#general"
```
**AC-069b (FR-069)**
```gherkin
When chat_post ohne confirm true aufgerufen wird
Then liefert das Tool eine Vorschau des Requests und es wird nichts gespeichert
```
**AC-070a (FR-070)**
```gherkin
When grok-jens tools/list aufruft
Then enthält die Liste nur Tools mit Präfix "chat_"
```
**AC-070b (FR-070)**
```gherkin
When grok-jens tools/call "create_session" aufruft
Then liefert das Tool einen Fehler und es erfolgt kein /v1-Aufruf
```
**AC-071a (FR-071)**
```gherkin
When Anna "<@agent_account:{grok-jens-id}> fass zusammen" in "#general" postet
Then existiert genau eine Outbox-Zeile chat.mention für grok-jens im selben Commit wie die Nachricht
```
**AC-071b (FR-071)**
```gherkin
When grok-jens sich selbst erwähnt
Then entsteht keine Outbox-Zeile
```
**AC-072a (FR-072)**
```gherkin
Given eine Outbox-Zeile für grok-jens und ein Test-Receiver auf localhost (Test-Ausnahme aktiv)
Then erhält der Receiver binnen 2 s einen POST mit event_type "chat.mention", gültiger Signatur v1 und data ohne Feld body
```
**AC-072b (FR-072)**
```gherkin
Given der Receiver antwortet 500
Then wird mit steigendem Abstand erneut zugestellt und nach 10 Versuchen ist die Zeile dead_letter
```
**AC-073a (FR-073)**
```gherkin
Given grok-jens hat eine dead_letter-Zeile
Then zeigt Jens' Einstellungsseite "Webhook fehlerhaft" und "Erneut zustellen" setzt die Zeile auf pending
```
**AC-074a (FR-074)**
```gherkin
Given im Thread von m1 haben grok-jens und grok-anna abwechselnd 5 Nachrichten gepostet
When grok-jens eine 6. Nachricht postet
Then antwortet der Server 409 "bot_streak_limit", es entsteht keine Outbox-Zeile und die Systemzeile "Bots warten auf einen Menschen" steht genau einmal im Thread
```
**AC-075a (FR-075)**
```gherkin
Given die Bot-Bremse ist aktiv
When Anna im Thread "Weiter" postet
Then ist bot_streak 0 und grok-jens darf wieder posten
```
**AC-076a (FR-076)**
```gherkin
Given grok-jens hat sich vor 3 min authentifiziert
Then zeigt die Mitgliederliste "aktiv"; nach Widerruf fehlt grok-jens in der @-Liste
```
**AC-077a (FR-077)**
```gherkin
Given ein aktiver Agent-Thread
When grok-jens darin postet
Then wird die Nachricht nicht an die Session weitergeleitet
```
**AC-078a (FR-078) – E2E**
```gherkin
Given reducedMotion "no-preference"
When eine neue Nachricht erscheint
Then hat das Nachrichten-Element beim Mount eine Transform-Animation mit Dauer 180 ms ± 20 ms (gemessen über element.getAnimations() bzw. data-motion-Attribute)
```
**AC-079a (FR-079) – E2E**
```gherkin
Given Playwright emuliert reducedMotion "reduce"
When eine neue Nachricht erscheint und der Status wechselt
Then läuft keine transform-Animation und keine Animation dauert länger als 120 ms
```
**AC-080a (FR-080) – E2E**
```gherkin
Given Jens hat im Thread nach oben gescrollt
When der Agent weiter streamt
Then bleibt die Scroll-Position stabil (± 2 px) und eine Pille "Neue Nachrichten" erscheint
```
**AC-080b (FR-080)**
```gherkin
Given Jens ist am Ende des Threads
When der Agent streamt
Then bleibt die letzte Zeile sichtbar
```
**AC-081a (FR-081)**
```gherkin
Given ein neuer Agent-Thread ohne Antworten
Then zeigt er höchstens 3 Vorschläge und ein Klick fügt den Text in den Composer ein
```
**AC-082a (FR-082) – E2E**
```gherkin
Given Jens' Browser ist 30 s offline (Playwright context.set_offline) und Anna postet 3 Nachrichten
When die Verbindung zurückkehrt
Then sieht Jens binnen 3 s alle 3 Nachrichten genau einmal in richtiger Reihenfolge
```
**AC-083a (FR-083)**
```gherkin
Given Jens' changes_cursor ist 8 Tage alt
When GET /v1/chat/changes aufgerufen wird
Then antwortet der Server mit reset true und die Web-App lädt bootstrap neu
```
**AC-084a (FR-084) – E2E**
```gherkin
Given der Server antwortet auf POST .../messages mit 503 (Playwright route)
When Anna "Hallo" sendet
Then zeigt die Nachricht "nicht gesendet" mit "erneut senden", und nach Erholung erzeugt ein Klick genau eine gespeicherte Nachricht
```

**AC-002b (FR-002)**
```gherkin
Given OMNIGENT_FEATURES ist leer
When GET /v1/info aufgerufen wird
Then ist features "team_chat" false
```
**AC-021c (FR-021)**
```gherkin
When Anna POST /v1/chat/dms mit [grok-jens] (Owner Jens nicht enthalten) sendet
Then antwortet der Server 403 mit code "agent_owner_required" und es entsteht keine DM
```
**AC-025b (FR-025)**
```gherkin
When 7 verschiedene Personen auf m1 antworten
Then enthält participants genau die 5 zuletzt antwortenden Personen
```
**AC-043b (FR-043)**
```gherkin
Given der Thread ist dismissed und Bob hat READ
When Bob den Agenten im Thread erwähnt
Then bleibt agent_state "dismissed" und die Nachricht wird mit "nicht an Agent" markiert
```
**AC-044b (FR-044) – E2E**
```gherkin
Given das Mock-LLM liefert einen Fehler
When Anna den Agent-Thread offen hat
Then zeigt der Chip "Fehler" und der Thread eine Systemzeile ohne Rohtext des Fehlers
```
**AC-047b (FR-047)**
```gherkin
Given Jens hat keinen Grant auf die Agent-Thread-Session (Sharing-Modus off)
Then zeigt die Thread-Wurzel bei Jens den letzten bekannten Thread-Zustand aus chat_threads statt eines Session-Status
```
**AC-052b (FR-052)**
```gherkin
Given Bob hat keinen Grant auf die Session
Then ist "Session öffnen" für Bob nicht sichtbar
```
**AC-055b (FR-055)**
```gherkin
Given die Freigabe wurde angefordert, während Anna den Thread geschlossen hatte
When Anna den Thread später öffnet
Then erscheint die offene Freigabe-Karte an ihrer Position
```
**AC-062b (FR-062)**
```gherkin
Given Jens' Anzeigename ist nicht gesetzt
Then zeigt die Nachricht "Grok (jens@bloopark.de)"
```
**AC-082b (FR-082)**
```gherkin
Given der Backfill liefert eine Nachricht, die per Frame bereits angezeigt wurde
Then erscheint sie genau einmal
```
**AC-085a (FR-085) – E2E**
```gherkin
Given Anna hat in "#billing" einen Agent-Thread gestartet und Jens hat READ
When Jens die Hauptsidebar ansieht
Then erscheint die Agent-Thread-Session dort nicht, wohl aber im Thread unter "Session öffnen"
```
**AC-085b (FR-085)**
```gherkin
When Anna (Owner) die Hauptsidebar ansieht
Then erscheint die Session mit Chat-Symbol in ihrer Liste
```
**AC-086a (FR-086)**
```gherkin
Given "#billing" hat einen Agent-Thread mit Session S7
When Carla "#billing" beitritt
Then hat Carla READ auf S7
```
**AC-087a (FR-087)**
```gherkin
Given ein Thread steht seit 121 s auf agent_state "starting"
When der Server den Thread liest oder der periodische Abgleich läuft
Then ist agent_state "start_failed" und die Systemzeile nennt Grund "timeout"
```
**AC-029c (FR-029)**
```gherkin
Given Annas Agent-Thread-Session S7 trägt das Label omnigent.chat.v1 und hat Grants für Anna, Jens, Bob
Then existiert für S7 kein Session-Chat-Channel
```

**AC-088a (FR-088)**
```gherkin
Given grok-jens ist Mitglied in "#billing" und Anna schrieb "<@agent_account:grok-jens> fass zusammen"
When die Grok-Bot-Routine chat_unread mit dem Token von grok-jens aufruft
Then enthält das Ergebnis genau diese Nachricht mit channel_id, thread_root_id und message_id
And nach chat_mark_read mit confirm true liefert ein erneuter Aufruf sie nicht mehr
```
**AC-088b (FR-088)**
```gherkin
Given Jens hat "#billing" verlassen
When grok-jens chat_unread aufruft
Then enthält das Ergebnis keine Nachrichten aus "#billing"
```

## 7. Non-functional requirements
Referenzumgebung „REF“: eine Server-Instanz (Docker, 2 vCPU, 4 GB RAM, SQLite oder Postgres), LAN-Latenz ≤ 10 ms, Browser Chrome stable auf Laptop mit ≥ 4 Kernen.

| ID | Characteristic (25010) | Requirement | Metric & threshold | Verification | Status |
|---|---|---|---|---|---|
| NFR-001 | Performance efficiency | Zustell-Latenz: POST-Commit bis Sichtbarkeit beim anderen Mitglied im offenen Channel | p50 ≤ 250 ms, p95 ≤ 500 ms bei 20 gleichzeitigen Nutzern und 1 Nachricht/s gesamt, REF | Playwright-Messtest `tests/e2e_ui/team_chat/test_latency.py` (2 Kontexte, 50 Nachrichten) | new |
| NFR-002 | Performance efficiency | Optimistische Anzeige der eigenen Nachricht | p95 ≤ 50 ms nach Enter | Playwright Performance-Marks | new |
| NFR-003 | Performance efficiency | Channel öffnen (tail 50) | Server p95 ≤ 150 ms; sichtbar p95 ≤ 800 ms bei 100 000 Nachrichten in DB | pytest-Benchmark `tests/benchmarks/` + Playwright | new |
| NFR-004 | Performance efficiency | Agent-Thread-Start: Enter bis Thread mit Status-Chip sichtbar (ohne Runner-Startzeit) | p95 ≤ 2 s | E2E mit Mock-LLM | new |
| NFR-005 | Performance efficiency | Statusaktualität Agent-Thread | offener Thread ≤ 1 s; Channel-Ansicht ≤ 5 s | E2E AC-044a, AC-047a | adapt (WS-Rescan 4 s exists) |
| NFR-006 | Interaction capability | Flüssigkeit der Animationen | Ziel 60 fps; bei 500 geladenen Nachrichten und laufendem Streaming < 5 % verworfene Frames und keine Long Task > 50 ms während einer Enter-Animation | Playwright mit CDP-Trace `test_motion_perf.py` | new |
| NFR-007 | Interaction capability | Animationsdauern | Mikro 120–200 ms, Enter 160–240 ms, Layout ≤ 300 ms, keine Animation > 400 ms außer Endlos-Indikatoren; Springs ohne Überschwingen > 4 px. Schritte/Status (Referenz interior.dev TaskSteps): Pop-Spring stiffness 640 / damping 22 / mass 0.7 (Start scale 0.4), Zellwechsel-Spring 520 / 34 / 0.45, Bogen 0,8 s je Umdrehung linear, Shimmer 1,6 s linear, Farb-/Meta-Übergang 200 ms, Ansage entprellt 500 ms | Vitest auf Motion-Tokens `web/src/chat/motion.ts` + UX-Review | new |
| NFR-008 | Interaction capability | Reduzierte Bewegung | unter `prefers-reduced-motion: reduce` 0 Transform-Animationen, Opacity ≤ 120 ms | Playwright `reduced_motion="reduce"` (AC-079a) | new |
| NFR-009 | Interaction capability | Barrierefreiheit WCAG 2.2 AA | R0-Journey vollständig per Tastatur; neue Nachrichten im offenen Channel über `aria-live="polite"` (gebündelt ≤ 1 Ansage / 2 s); Statuswechsel angesagt; Kontrast ≥ 4.5:1; 0 axe-Verstöße „serious“/„critical“ auf `/chat`-Ansichten | Playwright + `axe-playwright-python` (neue Test-Dependency) | new |
| NFR-010 | Interaction capability | Icon-System | nur `lucide-react`; Größen 16 px (inline) / 20 px (Toolbar); einheitliche Strichstärke; jedes Icon-only-Control mit `aria-label` | Vitest-Prüfung der Chat-Komponenten + UX-Review (icon-system) | new |
| NFR-011 | Interaction capability | Konsistenz mit Agent-Composer | Chat-Composer nutzt die Kartenoptik von `web/src/components/composer/ChatComposer.tsx` und `isComposerSendKey` | Vitest | adapt (Issue #7) |
| NFR-012 | Security | Agentenkonto sieht nie mehr als sein Owner | 100 % der Fälle der Rechte-Matrix (Design §Sicherheit) grün | `tests/server/test_chat_agent_account_permissions.py` | new |
| NFR-013 | Security | Least privilege Agentenkonto | Token gültig nur für `/mcp` (nur `chat_*`) und `/v1/chat/*`; gespeichert nur als HMAC-SHA256; ≥ 256 bit Entropie; einmalige Anzeige | pytest + Review | new |
| NFR-014 | Security | Keine Nachrichtentexte außerhalb der DB | 0 Vorkommen eines Marker-Bodys in WS-Frames, SSE, Webhook-Payloads, Logs ≥ INFO, Audit-Attributen | pytest mit Marker-String + `caplog` | adapt (Bestand-Spec §14) |
| NFR-015 | Security | XSS | Menschen-Bodies nur als Text; Agent-/Bot-Markdown nur über `web/src/components/ai-elements/streamdown-security.ts` | Vitest + AC-020a | adapt |
| NFR-016 | Security | SSRF bei Webhooks | Zustellung nur an HTTPS-Hosts, für die `omnigent/util/ssrf.py:host_is_internal` false liefert; Prüfung bei Anlage und vor jedem Versand; Redirects nicht folgen | pytest | adapt |
| NFR-017 | Security | Missbrauchsgrenzen | 30 Posts/min je (Principal, Channel); Agentenkonten zusätzlich 120 Requests/min auf `/v1/chat` und `/mcp` | pytest | new |
| NFR-018 | Reliability | Reconnect & Backfill | nach WS-Abbruch ≤ 60 s sind 100 % der Nachrichten binnen 3 s nach Reconnect sichtbar, 0 Duplikate | E2E AC-082a | new |
| NFR-019 | Reliability | Webhook-Zustellung (R1.1) | at-least-once, Reihenfolge je Agentenkonto, übersteht Server-Neustart (Lease 60 s) | pytest-Integration analog `tests/integration/test_manager_webhook_delivery.py` (PR #15) | adapt |
| NFR-020 | Reliability | Agent-Thread nach Server-Neustart | Status-Chip und offene Karten nach Reload korrekt aus Session-Zustand | E2E `test_agent_thread_reload.py` | new |
| NFR-021 | Compatibility | Browser & Shell | Chrome/Edge/Firefox/Safari je aktuelle und Vorversion, Electron-Shell; Desktop ≥ 1024 px; React 19 (nach Merge #16) | Playwright chromium (CI) + manuelle Checkliste je Release | adapt |
| NFR-022 | Compatibility | Datenbanken & Migration | Migrationen laufen auf SQLite, Postgres und CockroachDB; genau ein Alembic-Head | `tests/db/test_migration_chat.py` + `just crdb-test` | new |
| NFR-023 | Maintainability | Testabdeckung | jede Must-FR ≥ 1 automatisierter Test; jede R0/R1-UI-Story ≥ 1 Playwright-E2E in `tests/e2e_ui/team_chat/`; Lint und Typecheck grün | Traceability §10 + CI | new |
| NFR-024 | Maintainability | Playwright-Suite `team_chat` | deterministisch mit Mock-LLM; Laufzeit ≤ 10 min auf einem CI-Shard; 0 Flakes in 3 aufeinanderfolgenden Läufen (`.github/workflows/flake-stress-ui.yml`) | CI | new |
| NFR-025 | Flexibility | Rollout/Rollback | Flag aus → keine Datenverluste; Flag wieder an → Daten wieder sichtbar | pytest + Betriebsnotiz | new |
| NFR-026 | Performance efficiency | Kapazität | 50 Nutzer, 200 Channels, 100 000 Nachrichten, 50 offene WS auf einer Instanz ohne Fehler; RSS-Zuwachs ≤ 200 MB | Loadtest unter `tests/loadtest/` | new |
| NFR-027 | Maintainability | UX-Agent-Review (Prozess, ST-UX-2) | 100 % der UI-PRs mit Review-Notiz gegen die plugin87-Doku (motion-choreography, icon-system, a11y) ohne offene Blocker | PR-Checkliste | new |
| NFR-028 | Security | Audit ohne Inhalt | Post, Agent-Thread-Start, Agentenkonto anlegen/rotieren/widerrufen erzeugen Audit-Attribute (IDs, Aktion) via `add_audit_attrs` | pytest | adapt |
| NFR-029 | Safety | Kostenbremse Bots | Agentenkonten starten keine Sessions; Bot-Streak ≤ 5 je Thread | pytest (FR-036, FR-074) | new |
| NFR-030 | Interaction capability | Wahrgenommene Reaktion | jede Klick-Aktion zeigt ≤ 100 ms sichtbares Feedback (Pressed-State, Spinner oder optimistische Änderung) | UX-Review + Playwright-Stichprobe | new |

## 8. Constraints & interfaces
- C-1 Stack unverändert: FastAPI, SQLAlchemy 2/Alembic, React 19 (Issue #16 vor UI-Arbeit gemergt), Vite, Tailwind 4, shadcn/Radix, zustand, TanStack Query, Tiptap 3.30.5, lucide. Neue Web-Dependencies nur `motion`, `@tiptap/extension-mention`, `@tiptap/suggestion` (3.30.5). Neue Test-Dependency `axe-playwright-python`.
- C-2 Eine Server-Instanz; Echtzeit im Prozess (A-4/A-7). Kein Broker.
- C-3 Voraussetzungen: PR #4, #5, #14, #15 gemergt und Server aus dem Fork neu installiert vor Launch.
- C-4 Nicht anfassen: `integrations/slack/`; das Agent-Transkript in `ChatPage` (außer Wiederverwendung von Komponenten); `_DELEGATED_ALLOWED_PREFIXES` in `omnigent/server/auth.py` (kein Erweitern für Chat).
- C-5 Code-Reviews nur mit lokalen Werkzeugen (Claude Code + lokale Modelle), keine kostenpflichtigen Cursor-Modelle.
- C-6 Kein Code aus agent-native kopieren; Muster nachbauen.
- C-7 Alembic: genau ein Head nach jedem Merge (Merge-Revision falls nötig).
- C-8 Upstream-Fähigkeit (A-2): alles hinter Flag, keine Fork-Hardcodes.
- C-9 Repo-Konventionen: benannte DB-Sessions (`make_named_managed_session_maker`, AGENTS.md „Database query names“), kurze Kommentare ohne Issue-Nummern, `pre-commit` grün, `openapi.json` via `scripts/dump_openapi.py` aktuell (`tests/server/test_openapi_drift.py`).
- Schnittstellen: REST `/v1/chat/*`; WS-Frame-Typ `chat` auf `/v1/sessions/updates`; MCP `chat_*`; Webhook `chat.mention` (Envelope wie PR #15). Details in `05-design.md`.

## 9. Data & migration
- Neue Tabellen: `chat_channels`, `chat_members`, `chat_messages`, `chat_threads`, `chat_mentions`, `chat_reads`, `chat_agent_accounts`, `chat_agent_outbox`, `chat_user_prefs` (Design §Datenmodell).
- Migration kopiert `session_chat_messages` (PR #4, Revision `sc1a2b3c4d5e`) in `chat_messages`; alte Tabelle bleibt bis R2 lesbar, wird nicht mehr beschrieben.
- Aufbewahrung: unbegrenzt bis Löschung [OFFEN Q-3]. Session-Chat-Zeilen werden mit der Root-Session gelöscht (Bestand-Spec §3.5).
- Account-Löschung: Autor bleibt opaker Principal-String; Scrubbing gehört zu `designs/ACCOUNT_REVOCATION.md` (Bestand-Spec S-11).

## 10. Traceability
| Goal | Story | FR/NFR | AC / test | Task |
|---|---|---|---|---|
| G-1 | ST-1.1.1 | FR-001, FR-005, FR-006 | AC-001a/b, AC-005a/b, AC-006a/b; E2E `test_chat_shell.py` | T-003, T-006, T-009 |
| G-1 | ST-6.1.1 | FR-002, FR-003, FR-004, NFR-025 | AC-002a, AC-003a/b, AC-004a/b | T-001, T-003, T-006 |
| G-1 | ST-2.1.1 | FR-013–FR-020, FR-032, FR-082–FR-084, NFR-001/002/014/018 | AC-013a…AC-020a, AC-032a, AC-082a–AC-084a; E2E `test_channel_messaging.py`, `test_reconnect_backfill.py` | T-002, T-003, T-006, T-007, T-009, T-111, T-119, T-124 |
| G-1 | ST-2.1.2 | FR-021, FR-022 | AC-021a/b, AC-022a; E2E `test_dm.py` | T-002, T-003, T-007, T-009 |
| G-1 | ST-2.2.1 | FR-023–FR-025 | AC-023a–AC-025a; E2E `test_threads.py` | T-002, T-003, T-007, T-009 |
| G-2 | ST-3.1.1 | FR-027, FR-033–FR-038, FR-085–FR-087 | AC-027a/b, AC-033a…AC-038a, AC-085a/b, AC-086a, AC-087a; E2E `test_agent_thread.py` | T-002, T-005, T-008, T-009, T-103, T-109, T-113 |
| G-2 | ST-3.2.1 | FR-044–FR-047, NFR-004/005 | AC-044a…AC-047a; E2E `test_agent_thread.py` | T-005, T-008, T-009, T-118 |
| G-2 | ST-4.1.1 | FR-055–FR-058 | AC-055a…AC-058a; E2E `test_agent_thread_approval.py` | T-008, T-009 |
| G-1 | ST-1.2.1 | FR-007–FR-009 | AC-007a–AC-009b; E2E `test_unread.py` | T-101, T-111, T-120 |
| G-1 | ST-1.2.2 | FR-010 | AC-010a/b; E2E `test_needs_you.py` | T-112, T-120 |
| G-1 | ST-1.2.3 | FR-011, FR-012 | AC-011a, AC-012a; Vitest | T-112 |
| G-1 | ST-2.3.1 | FR-026, FR-027 | AC-026a/b; E2E `test_mentions.py` | T-002, T-110, T-120 |
| G-1 | ST-2.1.3 | FR-028 | AC-028a; Vitest | T-110 |
| G-1 | ST-2.1.4 | FR-029–FR-031 | AC-029a…AC-031b; E2E `test_session_chat_channel.py` (ersetzt `tests/e2e_ui/collaboration/test_session_human_chat.py`) | T-102, T-113, T-120 |
| G-2 | ST-3.1.2 | FR-039 | AC-039a/b | T-103, T-110 |
| G-2 | ST-3.1.3 | FR-040–FR-043, FR-054 | AC-040a…AC-043a, AC-054a; E2E `test_agent_follow.py` | T-104, T-114, T-121 |
| G-2 | ST-3.2.2 | FR-048, FR-089, FR-090, NFR-007 | AC-048a/b, AC-089a, AC-090a/b; E2E `test_agent_steps.py` | T-115, T-121 |
| G-2 | ST-3.3.1 | FR-049, FR-050, FR-053 | AC-049a/b, AC-050a, AC-053a; E2E `test_agent_controls.py` | T-114, T-121 |
| G-2 | ST-3.3.2 | FR-050, FR-051 | AC-051a/b | T-114, T-121 |
| G-2 | ST-3.4.1 | FR-052 | AC-052a; E2E | T-114, T-121 |
| G-2 | ST-4.2.1 | FR-059 | AC-059a | T-115 |
| G-2 | ST-5.1.1 | FR-060 | AC-060a/b | T-115 |
| G-2 | ST-7.1.1 | FR-066–FR-068, NFR-012/013/017 | AC-066a…AC-068a | T-105, T-106 |
| G-2 | ST-7.1.2 | FR-088 (R1); FR-071–FR-073, NFR-016/019 (R1.1) | AC-088a/b; AC-071a…AC-073a (R1.1) | T-107; R1.1: T-108, T-116b |
| G-2 | ST-7.1.3 | FR-076 | AC-076a; E2E `test_agent_accounts_ui.py` | T-116, T-121 |
| G-2 | ST-7.1.4 | FR-061–FR-065 | AC-061a…AC-065a; E2E `test_agent_accounts_ui.py` | T-105, T-116, T-121 |
| G-2 | ST-7.1.5 | FR-036, FR-074, FR-075, FR-077, NFR-029 | AC-036a, AC-074a, AC-075a, AC-077a | T-109 |
| G-2 | ST-7.1.6 | FR-069, FR-070 | AC-069a…AC-070b | T-107 |
| G-3 | ST-UX-1 | FR-078–FR-081, NFR-006–NFR-011, NFR-030 | AC-078a…AC-081a; E2E `test_motion.py`, `test_a11y.py` | T-117, T-118, T-120 |
| G-3 | ST-UX-2 | NFR-027 | PR-Checkliste | T-000, T-122 |
| G-4 | (quer) | NFR-022–NFR-025, C-7, C-8 | CI | T-000, T-001, T-119 |

Lücken: Erfolgsmessung G-1/G-2 ohne Telemetrie (bewusst, Q-6). Keine Story ohne FR, keine Must-FR ohne AC.

## 11. Assumptions
| ID | Annahme | Bestätigen durch |
|---|---|---|
| A-1 | Ein Workspace pro Installation | Jens |
| A-2 | Ergebnis bleibt upstream-fähig | Jens (Q-7) |
| A-3 | Omnigent-Agent arbeitet mit den Rechten des Aufrufers | Jens |
| A-4/A-7 | Eine Server-Instanz reicht für den Launch | Jens |
| A-5 | Aufrufer wird OWNER der Agent-Thread-Session | Jens |
| A-6 | Agent-Kontext = auslösende Nachricht + Thread, nicht ganzer Channel | Jens |
| A-8 | Projekt/Host für Agent-Start: eigenes Projekt des Aufrufers mit dem Namen aus der Channel-Einstellung `project_name`, sonst Chat-Standardprojekt des Aufrufers (`chat_user_prefs`); `agent_id`/`workspace`/`git` über `resolve_project_session_create`, `host_id` aus `project.config.host_id` falls gesetzt (sonst ohne `host_id`, d. h. vom Aufrufer verwalteter Runner wie beim heutigen Create). Grund: Hosts müssen dem Session-Owner gehören (`omnigent/server/routes/_host_launch.py:147`), Projekte sind owner-privat (`omnigent/entities/project.py`) | Jens (Q-12) |
| A-9 | Body-Limit 4000 Zeichen (PR #4: 2000) | Jens |
| A-10 | Kontextgrenze 20 Nachrichten / 12 000 Zeichen | Jens |
| A-11 | Spiegel-Limit 32 000 Zeichen | Jens |
| A-12 | Agentenkonten haben keinen Zugang zu Session-Chats | Jens |
| A-13 | Server-Secret `OMNIGENT_CHAT_AGENT_SECRET` (Env) für Token-Hashes und Ableitung der Webhook-Secrets; ohne Secret antworten Agentenkonto-Routen 503 | Jens |
| A-14 | Webhook-Payload enthält keinen Nachrichtentext; Bot holt Inhalt über MCP `chat_read` | Jens (Q-14) |
| A-15 | Menschen-Nachrichten: Text + Links; Agent-/Bot-Nachrichten: Markdown über bestehende Streamdown-Sicherheit | Jens |
| A-16 | Max. 5 Agentenkonten je Owner | Jens |
| A-17 | Weiterleitung an Agent mit Präfix „[Name] “ | Jens |
| A-18 | Grok Bot kann einen Bearer-Token für `/mcp` halten und eine HTTPS-Webhook-URL bereitstellen | Jens (Q-14) |
| A-19 | Nachrichtenliste ab 200 geladenen Nachrichten einfach gefenstert, ohne neue Virtualisierungs-Dependency | Tech Lead |
| A-20 | Verlässt ein Mitglied einen Channel, bleiben seine READ-Grants auf Agent-Thread-Sessions bestehen (wie Verlauf) | Jens |
| A-21 | Verzeichnis (Directory) für DMs/Autocomplete = Mitglieder von `#general` (jeder Mensch tritt per Bootstrap bei) plus aktive Agentenkonten; funktioniert in allen Auth-Modi ohne Nutzerliste | Tech Lead |
| A-22 | Grok Bots werden nur bei expliziter Erwähnung oder DM geweckt, nicht durch weitere Thread-Antworten | Jens |
| A-23 | Die Grok-Bot-Routine pollt `chat_unread` mit Intervall ≥ 5 min und markiert Gelesenes per `chat_mark_read`; die Routine selbst ist nicht Teil dieses Repos | Jens |

## 12. Open questions
| ID | Question | Owner | Blocks | Due | Answer |
|---|---|---|---|---|---|
| Q-3 | Aufbewahrungsfrist Channel-/DM-Nachrichten? | Jens | nichts (Default unbegrenzt) | R2 | |
| Q-6 | Erfolgssignale messbar machen? | Jens | nichts | nach R1 | |
| Q-7 | Upstream-Fähigkeit Ziel oder nice-to-have? | Jens | nichts (Default: Ziel) | R1 | |
| Q-10 | GitHub-Diskussion in R1? | Jens | – | – | Entschieden: R2 |
| Q-11 | R0 als interne Demo Mitte der Woche? | Jens | – | – | Entschieden: Mi 21.10.2026 |
| Q-12 | Projekt/Host-Auflösung für Agent-Start gemäß A-8 ok? | Jens | T-005 | Mo 19.10. | |
| Q-13 | Darf ein Agentenkonto in einer DM ohne seinen Owner sein (z. B. Anna ↔ Grok (Jens))? Default: nein | Jens | T-106 | Mi 21.10. | |
| Q-14 | Format und Auth des Grok-Bot-Routine-Webhooks (HMAC-Header + JSON-Envelope ok? Größenlimit? eigener Auth-Header?) | Jens | T-108 (R1.1) | vor R1.1 | nicht mehr launch-kritisch (Q-18) |
| Q-15 | Verhältnis zu Issue #12 (Grok-Bot-Teammate-Prototyp): schließen, später oder Teil von R2? | Jens | nichts | R1 | |
| Q-16 | Dürfen alle Menschen Channels anlegen (Default) oder nur Admins? | Jens | nichts | R0 | |
| Q-17 | Bekommen später beitretende Mitglieder READ auf bestehende Agent-Thread-Sessions? Default: ja, beim Beitritt für Threads mit Session | Jens | T-103 | Do 22.10. | |
| Q-18 | Must-Anteil über Richtwert (Review-Schätzung ≈ 68 %, Task-Schätzung in `07-tasks.md` ≈ 81 % der Feature-Aufwände): akzeptieren oder Webhook-Wecken verschieben? | Jens | – | – | **ENTSCHIEDEN** 16.10.2026 23:25: Webhook-Wecken (FR-071…073, NFR-019, T-108, Q-14) → R1.1 kurz nach Launch; in R1 wecken sich Grok Bots per Polling-Routine (≥ 5 min) über MCP-`chat_unread` (FR-088). |
| Q-19 | Wo liegt `feat/react-19`? | – | – | – | **GELÖST** (16.10. 23:28): lokaler Branch in eigenem Worktree auf Jens' Mac; React-19-Session `6c30fca590d7445caa463df974cdc6e3` arbeitet noch, kein Push ohne OK. Keine Frage an Jens, sondern Abhängigkeit D-1 (R-15) für T-006…T-008. |
| Q-20 | Lizenz von interior.dev (TaskSteps, shadcn-Registry `https://www.interior.dev/r/task-steps.json`) erlaubt Übernahme des Codes in `jholze/omnigent` (und ggf. upstream)? Bis zur Klärung: nur Muster nachbauen, kein Copy-in | Jens / Lead | T-115 | vor T-115 (Do 22.10.) | |

## 13. Risks
| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R-1 | Termin 24./25.10.: 33 Tasks (R0 10 + R1 23) in 6 Arbeitstagen mit lokalen Reviews | hoch | hoch | R0 strikt; R1-Cut-Line (Design §Rollout) für Should/Could; tägliche Merge-Züge; Wellen parallel |
| R-2 | Merge-Konflikte der Voraussetzungen: PR #4, #5 und #15 setzen alle `down_revision="mm1a2b3c4d5e"` → drei Alembic-Heads; PR #4 und #5 legen beide `omnigent/server/principals.py` an; alle ändern `omnigent/db/db_models.py`, `omnigent/server/app.py`, `omnigent/server/routes/_sessions/helpers.py` | sicher | hoch | T-000: Merge-Reihenfolge #5 → #4 → #15 → #14, Re-Chaining bzw. Merge-Revision, Test „genau ein Head“ |
| R-3 | PR #14 (Draft, ~5,5k Zeilen) und #15 (~5k Zeilen) noch ohne Review; Fehler vererben sich in Chat-MCP/Webhook | mittel | hoch | Sicherheits-Review vor Merge (lokal); Chat nutzt nur `sign()`, Envelope-Form und das `_Api`-Muster |
| R-4 | Server-Neuinstallation 0.17 → Fork 0.18 migriert Produktivdaten | mittel | hoch | Backup + Probe-Upgrade auf Kopie am 22.10.; Rollback-Plan (T-123) |
| R-5 | E2E-Infrastruktur: geteilter `live_server` aktiviert keine Flags; Multi-User-Helper bindet keinen Runner; Nightly-E2E auf main rot (Issue #2) | hoch | mittel | eigene Fixture `team_chat_server` (Runner + Mock-LLM + Flag; Header-Identitäten wie `tests/e2e_ui/collaboration/test_author_label.py`); Suite separat lauffähig (T-004) |
| R-6 | HTTP/1.1-Verbindungslimit: jede offene Session-SSE belegt eine Verbindung (`web/src/store/conversationRegistry.ts`) | mittel | mittel | Live-Stream nur für den geöffneten Thread; Channel-Status über WS-Watch |
| R-7 | In-Process-Bus verliert Frames bei mehreren Workern | niedrig | mittel | C-2; Backfill über `/v1/chat/changes` heilt |
| R-8 | SSRF über nutzerdefinierte Webhook-URLs | mittel | hoch | FR-065, NFR-016 |
| R-9 | Bot-Schleifen / Kosten | mittel | mittel | FR-036, FR-074, FR-077, NFR-017 |
| R-10 | UX-Abnahme subjektiv | mittel | hoch | messbare NFR-006…011/030, UX-Agent-Checkliste, Demo 21.10. |
| R-11 | Format des Grok-Bot-Webhooks unbekannt (Q-14) – nur noch R1.1 | niedrig | mittel | Envelope wie PR #15 (dort bereits für Grok Bot gedacht, Beispiel `key_id: grok-bot`); Fallback: Bot pollt `chat_unread` |
| R-12 | Lokale Review-Kapazität wird Engpass | hoch | mittel | PRs < 400 Zeilen, Reviewer parallel, Tester-Gate automatisiert |
| R-13 | Motion erhöht Bundle/Rendering-Kosten | niedrig | niedrig | `LazyMotion` + `domAnimation`; Messung NFR-006 |
| R-15 | Abhängigkeit D-1: React-19-Upgrade (#16) muss vor der Chat-UI gemergt sein; Branch `feat/react-19` liegt nur lokal (Worktree auf Jens' Mac, Session `6c30fca5…`, kein Push ohne OK); Upgrade kann Regressionen in bestehender UI, Tiptap 3 oder Radix auslösen und die UI-Wellen verzögern | hoch | hoch | Merge spätestens Sa 17.10.; `pnpm --filter web run test` + volle `tests/e2e_ui` vor Chat-UI-Start; Fallback: Chat-UI auf React 18 beginnen und vor R0-Demo rebasen (Jens entscheidet) |
| R-16 | Polling-Wecken: Bots reagieren erst nach ≤ 5 min (+ Laufzeit) | sicher | niedrig | erwartetes Verhalten im UI-Hinweis „Bots antworten zeitversetzt“; Webhook in R1.1 |
| R-14 | AI-Elements-Komponenten für Tool/Task/Confirmation fehlen im Repo (vorhanden: `conversation`, `message`, `reasoning`, `shimmer`, `code-block`) | sicher | mittel | schlanke eigene Komponenten nach AI-Elements-Muster (T-115) |

## Changelog
| Version | Date | Change |
|---|---|---|
| 0.1 | 2026-10-16 | Erste Fassung: FR-001…FR-084, NFR-001…NFR-030, 127 ACs, Traceability, Risiken. |
| 0.4 | 2026-10-16 | interior.dev TaskSteps als Referenz für Agent-Schritte: FR-048 präzisiert, neu FR-089 (Should), FR-090 (Must, a11y/Reduced Motion), AC-048b, AC-089a, AC-090a/b; NFR-007 um TaskSteps-Werte ergänzt; Q-20 (Lizenz) neu; Q-19 gelöst → Abhängigkeit D-1. |
| 0.3 | 2026-10-16 | Entscheidungen Jens 23:25: Q-18 entschieden (Webhook-Wecken FR-071…073/NFR-019 → Should/R1.1; neu FR-088 Polling über `chat_unread`, AC-088a/b, A-23); React 19 (#16) als Voraussetzung der Chat-UI (C-1, NFR-021, Non-goal geändert, R-15, Q-19); R-16. |
| 0.2 | 2026-10-16 | Review-Fixes (06-review.md #1–#9): FR-029 schließt Agent-Thread-Sessions aus + Sidebar-Abschnitt; FR-021 Bot-Owner-Regel; Host optional (A-8, FR-035); FR-071 ohne Thread-Folgen (A-22); neue FR-085…FR-087; Downgrades FR-043/054/063/083 auf Should; 15 neue ACs; A-19…A-22, Q-18. |
