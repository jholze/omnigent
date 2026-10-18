# Omnigent Chat – Design · v0.4 (2026-10-16, Status: in Review)

Bezug: `04-requirements.md` v0.4 (FR/NFR-IDs), `03-domain.md` v0.3, ADRs in `adr/`. Code-Stand: main `87f6f08` + PR #4/#5/#14/#15 (gemergt vorausgesetzt, C-3). Alle Pfade relativ zum Repo-Root `jholze/omnigent`.

## Context & scope
Wir bauen serverweite Channels, DMs, Threads, Erwähnungen, Lesestand und Agenten als Teilnehmer (Omnigent-Agenten als Session-gestützte Agent-Threads, Grok Bots als Agentenkonten über `/mcp` + Webhook). Der Session-Chat aus PR #4 wird zur Sonderform eines Channels. Scope R0+R1; R2 nur dort berücksichtigt, wo das Datenmodell es nicht verbauen darf (private Channels, Board-Zeilen, GitHub-Karten, Slack-Brücke).

## Goals / non-goals
- Goals: G-1…G-4; FR-001…FR-084; NFR-001…NFR-030.
- Non-goals: siehe `04-requirements.md` §3; zusätzlich kein Cross-Process-Bus (C-2), keine Änderung an `_DELEGATED_ALLOWED_PREFIXES` (C-4), keine Kopie von agent-native-Code (C-6).

## Overview

```mermaid
flowchart LR
  subgraph Browser
    CP[ChatPage /chat] --> CS[chatStore zustand]
    CS <-- frames --> WS[(WS /v1/sessions/updates)]
    CP --> AT[AgentThreadView]
    AT <-- SSE --> SS[(GET /v1/sessions/:id/stream)]
  end
  subgraph Server
    R[routes/chat.py /v1/chat/*] --> ST[chat_store]
    R --> ACC[chat_access]
    R --> RT[chat_realtime -> user_session_stream]
    R --> ORC[chat_agent_threads]
    ORC --> SESS[Session create + /events exists]
    SESS --> PS[_publish_status hook]
    PS --> ORC
    R --> OB[chat_outbox]
    OB --> DISP[chat_webhook_dispatcher]
    MCP[/mcp chat_* tools/] --> R
    AUTH[auth.py agent token branch] --> R
  end
  DISP -->|HMAC POST| GB[Grok Bot Routine-Webhook]
  GB -->|Bearer ogc_| MCP
```

## Components & responsibilities

| Komponente | Pfad | Status | Verantwortung | FR/NFR |
|---|---|---|---|---|
| Feature-Flag | `omnigent/server/feature_flags.py`, `designs/FEATURE_FLAGS.md` | adapt | `Feature.TEAM_CHAT = "team_chat"`, owner `web`, review_by `0.20.0`, `frontend_visible=True` | FR-002, FR-003 |
| DB-Modelle | `omnigent/db/db_models.py` | adapt | `SqlChatChannel`, `SqlChatMember`, `SqlChatMessage`, `SqlChatThread`, `SqlChatMention`, `SqlChatRead`, `SqlChatAgentAccount`, `SqlChatAgentOutbox`, `SqlChatUserPref` | §Datenmodell |
| Migration | `omnigent/db/migrations/versions/tc1a2b3c4d5e_add_team_chat.py` | new | Tabellen + Indizes; Kopie `session_chat_messages` → `chat_messages`; `down_revision` = Head nach T-000 | FR-031, NFR-022 |
| Chat-Store | `omnigent/stores/chat_store/__init__.py` (ABC), `sqlalchemy_store.py` | new | CRUD, Cursor-Paging, DM-Key, Thread-Zähler, Mentions, Reads, Changes-Feed; benannte Sessions (`make_named_managed_session_maker`) | FR-004…FR-027 |
| Zugriffspolitik | `omnigent/server/chat_access.py` | new | `principal_kind()`, `can_read/can_post(channel, principal)`, Session-Chat-Regel (übernimmt `may_read_human_chat` aus PR #4 `omnigent/server/session_chat.py`), Agentenkonto-Teilmengenregel | FR-016, FR-022, FR-029, FR-067, FR-068, NFR-012 |
| REST-Routen | `omnigent/server/routes/chat.py` (`create_chat_router(...)`) | new | `/v1/chat/*` (§APIs), Flag-Gate, Rate-Limit, Validierung, Audit | FR-003…FR-027, FR-039, FR-042, FR-054, FR-061…FR-064, FR-083 |
| Echtzeit | `omnigent/server/chat_realtime.py`; `omnigent/server/routes/sessions/routes_core.py` (WS, Zweig `elif evt_type == "chat"`) | new / adapt | `publish_chat(principals, frame)` → `user_session_stream.publish(_discovery_key(p), {"type":"chat", ...})`; WS leitet als Frame `{"type":"chat", ...}` weiter | FR-009, FR-014, FR-025 |
| Agent-Thread-Orchestrator | `omnigent/server/chat_agent_threads.py` | new | Start (Create-Pfad + Grants + erste Nachricht), Weiterleitung, Entlassen/Reaktivieren, Spiegeln bei `idle`, Fehlerklassen | FR-033…FR-043, FR-046, FR-054, FR-077 |
| Status-Hook | `omnigent/server/routes/_sessions/helpers.py` `_publish_status` | adapt | ruft `chat_agent_threads.on_session_status(session_id, status, response_id)` in `try/except` (Muster `_record_manager_webhook_status` aus PR #15) | FR-046 |
| Agentenkonten | `omnigent/server/chat_agent_accounts.py` | new | Token erzeugen (`ogc_` + 43 Zeichen urlsafe = 256 bit), HMAC-Hash, Secret-Ableitung, Limit | FR-061…FR-065, NFR-013 |
| Auth-Zweig | `omnigent/server/auth.py` | adapt | Bearer `ogc_…` → Lookup per Hash → Principal `agent:<uuid>`, nur für `/mcp` und `/v1/chat/*`; `request.state.chat_agent_account` setzen | FR-066 |
| MCP-Chat-Tools | `omnigent/server/mcp_endpoint.py` (PR #14) | adapt | `chat_*` in `tool_definitions()` und `_call_tool`; Filter bei Agentenkonto; `confirm` für Writes | FR-069, FR-070 |
| Outbox + Dispatcher (R1.1) | `omnigent/server/chat_outbox.py`, `omnigent/server/chat_webhook_dispatcher.py` | new (adapt PR #15) | Insert in Post-Transaktion; Zustellung mit `manager_webhook_signing.sign(secret=…)`; Backoff/Lease/Dead-Letter wie PR #15 | FR-071…FR-073, NFR-016, NFR-019 |
| Session-Chat-Adapter | `omnigent/server/routes/session_chat.py`, `omnigent/server/session_chat.py` (PR #4) | adapt | lesen/schreiben über Chat-Store (`kind=session`); Event `session.human_chat` bleibt für alte Clients; Sessions mit Label `omnigent.chat.v1` → 404 `no_session_chat` | FR-029…FR-031 |
| Sidebar-Filter | adapt `web/src/shell/Sidebar.tsx` (Liste aus `useConversations`) | adapt | Sessions mit Label `omnigent.chat.v1` und Owner ≠ ich ausblenden; Owner sieht sie mit Chat-Symbol | FR-085 |
| App-Verdrahtung | `omnigent/server/app.py`, `omnigent/cli.py`, `deploy/docker/entrypoint.py`, `deploy/databricks/src/app.py`, `scripts/dump_openapi.py` | adapt | `chat_store` konstruieren und übergeben (Muster PR #4), Dispatcher starten | – |
| Web: Seite & Navigation | `web/src/chat/ChatPage.tsx`, `ChatSidebar.tsx`; adapt `web/src/App.tsx`, `web/src/shell/Sidebar.tsx` | new/adapt | Routen, Liste, Badges | FR-001, FR-008 |
| Web: Daten | `web/src/chat/chatApi.ts`, `web/src/chat/teamChatStore.ts` (zustand), `web/src/chat/useChatFrames.ts`; adapt `web/src/lib/sessionUpdatesSocket.ts` (Frame-Typ `chat`), `web/src/hooks/SessionUpdatesProvider.tsx` (zusätzliche Watch-IDs) | new/adapt | Bootstrap, Paging, Optimistik, Dedupe, Backfill, Watch der Agent-Thread-Sessions | FR-014, FR-047, FR-082…FR-084 |
| Web: Ansicht | `web/src/chat/ChannelView.tsx`, `MessageList.tsx`, `MessageItem.tsx`, `ThreadPane.tsx`, `ChatComposerCard.tsx` (nutzt `ChatComposer`-Optik + `isComposerSendKey`) | new | Nachrichten, Threads, Composer, Fehlerzustände | FR-013…FR-025, NFR-011 |
| Web: Agent-Thread | `web/src/chat/agent/AgentThreadView.tsx`, `AgentStatusChip.tsx`, `AgentControls.tsx`, `StepsGroup.tsx` + `TaskSteps.tsx` (FR-048/089/090), `RunStuckBanner.tsx`, `PrCard.tsx` | new | Live-Ansicht über `conversationRegistry`, Karten, Steuerung | FR-044…FR-060 |
| Web: Erwähnungen | `web/src/chat/mentions/MentionExtension.ts`, `MentionList.tsx` | new | Tiptap-Mention + Suggestion | FR-026 |
| Web: Agentenkonten | `web/src/chat/settings/AgentAccountsSection.tsx`; adapt `web/src/pages/SettingsPage.tsx` (Abschnitt „Agenten“) | new/adapt | Anlegen/Rotieren/Widerrufen, Status | FR-061…FR-065, FR-073, FR-076 |
| Web: Motion | `web/src/chat/motion.ts` (Tokens), `web/src/chat/MotionRoot.tsx` (`LazyMotion`+`MotionConfig reducedMotion="user"`) | new | Animationen, Reduced Motion | FR-078…FR-080, NFR-006…NFR-008 |
| Web: Braucht dich / Notifications | adapt `web/src/pages/InboxPage.tsx`, `web/src/lib/browserNotifications.ts` | adapt | Erwähnungen + Freigaben, OS-Hinweise | FR-010…FR-012 |
| Web: Session-Chat | adapt `web/src/shell/SessionChatColumn.tsx`, `web/src/shell/ChatHeader.tsx` | adapt | Spalte rendert `ChannelView` des `kind=session`-Channels | FR-029 |
| E2E | `tests/e2e_ui/team_chat/` (new), Fixture `tests/e2e_ui/team_chat/conftest.py` | new | Playwright-Suiten je Story | NFR-023, NFR-024 |

## Data model
Alle Tabellen mit `workspace_id BigInteger` (default 0, Teil des PK wie in PR #4), IDs `Uuid16` (bare hex), Zeiten epoch µs (`BigInteger`, `now_epoch_us`), keine Foreign Keys (Repo-Konvention seit `p1a2b3c4d5e6_remove_all_fks`), Löschung durch die Anwendung.

| Tabelle | Spalten (Auszug) | Schlüssel / Indizes | Status |
|---|---|---|---|
| `chat_channels` | `id`, `kind` (`channel`/`dm`/`session`), `name` (nur `channel`), `topic`, `project_name` (A-8), `visibility` (`public`; `private` ab R2), `dm_key` (sha256 der sortierten Principals, nur `dm`), `session_root_id` (nur `session`), `created_by`, `created_at`, `archived_at` | PK (`workspace_id`,`id`); unique (`workspace_id`,`name`) where kind=channel; unique (`workspace_id`,`dm_key`); unique (`workspace_id`,`session_root_id`) | new |
| `chat_members` | `channel_id`, `principal_id` (String 160; `user@…` oder `agent:<uuid>`), `role` (`member`/`admin`), `joined_at` | PK (`workspace_id`,`channel_id`,`principal_id`); idx (`workspace_id`,`principal_id`) | new (für `kind=session` nicht genutzt: Mitgliedschaft = Grants) |
| `chat_messages` | `id`, `channel_id`, `thread_root_id` (null = Top-Level), `author_id`, `author_kind` (`user`/`agent_account`/`omnigent_agent`/`system`), `created_at`, `body` (Text), `client_id` (String 64, null), `forward_state` (null/`forwarded`/`not_forwarded`/`failed`), `session_ref` (Session-ID bei gespiegelten Antworten), `response_ref`, `system_code` (bei `system`), `edited_at`, `deleted_at`, `deleted_by` (ab „Später“) | PK (`workspace_id`,`id`); idx (`workspace_id`,`channel_id`,`thread_root_id`,`created_at`,`id`); unique (`workspace_id`,`channel_id`,`author_id`,`client_id`) where client_id not null; idx (`workspace_id`,`created_at`,`id`) für Changes-Feed | new |
| `chat_threads` | `root_message_id`, `channel_id`, `reply_count`, `last_reply_at`, `participants` (JSON, ≤ 5), `bot_streak`, `brake_notice_at`, `session_id` (null), `agent_handle`, `agent_id`, `agent_state` (`none`/`starting`/`active`/`dismissed`/`start_failed`/`closed`), `caller_id`, `start_requested_at` (epoch µs, für FR-087), `updated_at` | PK (`workspace_id`,`root_message_id`); unique (`workspace_id`,`session_id`) where not null; idx (`workspace_id`,`channel_id`,`updated_at`) | new |
| `chat_mentions` | `message_id`, `channel_id`, `target_kind`, `target_id`, `created_at` | PK (`workspace_id`,`message_id`,`target_id`); idx (`workspace_id`,`target_id`,`created_at`) | new |
| `chat_reads` | `channel_id`, `principal_id`, `thread_root_id` ('' = Channel-Ebene), `last_read_at`, `last_read_id`, `updated_at` | PK (`workspace_id`,`channel_id`,`principal_id`,`thread_root_id`) | new |
| `chat_agent_accounts` | `id`, `principal_id` (`agent:<id>`), `owner_id`, `display_name`, `handle`, `token_hash` (hex), `token_prefix` (8 Zeichen für Anzeige), `secret_version` (int), `webhook_url`, `created_at`, `last_seen_at`, `revoked_at` | PK (`workspace_id`,`id`); unique (`workspace_id`,`handle`); unique (`token_hash`) | new |
| `chat_agent_outbox` | `event_id`, `account_id`, `sequence` (monoton je Konto), `event_type` (`chat.mention`), `payload` (JSON ohne Body), `status` (`pending`/`leased`/`delivered`/`dead_letter`), `attempts`, `next_attempt_at`, `lease_until`, `last_error_code`, `created_at` | PK (`workspace_id`,`event_id`); idx (`status`,`next_attempt_at`); idx (`workspace_id`,`account_id`,`sequence`) | new |
| `chat_user_prefs` | `principal_id`, `default_project_id`, `updated_at` | PK (`workspace_id`,`principal_id`) | new (A-8) |
| `session_chat_messages` | unverändert | – | exists (PR #4), ab Migration nur noch gelesen von nichts; Drop in R2 |

**Handles:** Omnigent-Agent-Handles werden nicht gespeichert, sondern pro Anfrage aus der Agentenliste des Aufrufers abgeleitet (FR-039). Kollisionsregel: Agentenkonto-Handles sind global eindeutig; ein Agent-Slug, der einem Agentenkonto-Handle gleicht, bekommt Suffix `-agent`. Im Body steht immer das Token mit ID (`<@agent_account:ID>`) bzw. Handle (`<@omnigent_agent:HANDLE>`); die Anzeige wird beim Rendern aufgelöst.

**Changes-Feed (`GET /v1/chat/changes`):** Cursor = `(ts_us, kind, id)` als opaque base64; liefert vereinigt neue/aktualisierte Nachrichten (nach `created_at`), Threads (nach `updated_at`) und eigene Reads (nach `updated_at`) in Channels, in denen der Aufrufer Mitglied ist bzw. Leserecht hat; max. 1000 Einträge, sonst `reset: true` (FR-083).

**Migration der Session-Chat-Daten (FR-031):** pro distinct `(workspace_id, root_conversation_id)` in `session_chat_messages` ein `chat_channels`-Eintrag `kind=session`, dann `INSERT … SELECT` mit gleicher `id`, `created_by → author_id`, `author_kind='user'`. Idempotent (Insert nur, wenn ID fehlt). Downgrade löscht nur die neuen Tabellen.

## APIs / interfaces

### REST `/v1/chat/*` (new, `omnigent/server/routes/chat.py`)
Alle Routen: Flag-Gate (404), Auth wie `/v1` (User) bzw. Agentenkonto-Token (§Sicherheit), Fehler via `OmnigentError`. IDs als bare hex.

| Methode & Pfad | Body / Query | Antwort | FR | Rel |
|---|---|---|---|---|
| `GET /v1/chat/bootstrap` | – | `{me, channels[], dms[], directory[], agents_hint, changes_cursor}` mit `unread_count`, `mention_count` (R1) | FR-004, FR-008 | R0 |
| `GET /v1/chat/channels` | `joined=1|0` | Liste öffentlicher Channels mit Zählern | FR-005, FR-008 | R0 |
| `POST /v1/chat/channels` | `{name, topic?, project_name?}` | 201 Channel; 409 Name belegt; 422 | FR-005 | R0 |
| `PATCH /v1/chat/channels/{id}` | `{topic?, project_name?}` | 200 (Mitglied) | A-8 | R0 |
| `POST /v1/chat/channels/{id}/join` · `/leave` | – | 204; 409 bei `general` | FR-006 | R0 |
| `GET /v1/chat/channels/{id}/members` | – | Mitglieder inkl. Agentenkonten mit Status | FR-076 | R1 |
| `GET /v1/chat/channels/{id}/messages` | `tail`, `before`, `after`, `limit`, `thread_root_id` | `{items[], has_older, has_more, next_after, threads{}}` | FR-019 | R0 |
| `POST /v1/chat/channels/{id}/messages` | `{body, client_id, thread_root_id?}` | 201/200 Message (+`agent_thread` bei Start) | FR-013…FR-018, FR-023, FR-027, FR-033 | R0 |
| `POST /v1/chat/channels/{id}/read` | `{created_at, id, thread_root_id?}` | 204 | FR-007 | R1 |
| `POST /v1/chat/channels/{id}/typing` | `{active}` | 204, kein Schreibzugriff | FR-032 | R1 (Could) |
| `POST /v1/chat/dms` | `{principals: [..]}` | 200/201 DM | FR-021 | R0 |
| `GET /v1/chat/changes` | `after` | `{items[], next, reset}` | FR-082, FR-083 | R0 |
| `GET /v1/chat/agents` | – | `{omnigent_agents:[{handle, agent_id, name}], agent_accounts:[…]}` | FR-039 | R1 (R0: nur `agent`) |
| `POST /v1/chat/threads/{root_id}/dismiss` | – | 200 Thread | FR-042 | R1 |
| `POST /v1/chat/messages/{id}/forward` | – | 202 | FR-054 | R1 |
| `GET /v1/chat/needs-you` | – | `{mentions[], elicitations[]}` | FR-010 | R1 |
| `GET /v1/chat/unread` | `?limit≤50` | Agentenkonto: `{items:[{channel_id, thread_root_id, message_id, author, body, created_at}], next_cursor}`; Mensch: 403 (nutzt bootstrap/needs-you) | FR-088 | R1 |
| `GET/PUT /v1/chat/prefs` | `{default_project_id}` | 200 | A-8 | R0 |
| `POST /v1/chat/agent-accounts` | `{display_name, handle, webhook_url}` | 201 `{account, token, webhook_secret}` (einmalig) | FR-061, FR-063, FR-065 | R1 |
| `GET /v1/chat/agent-accounts` | – | eigene Konten (Admin: alle) mit Status | FR-073, FR-076 | R1 |
| `POST /v1/chat/agent-accounts/{id}/rotate` | `{rotate_secret?: bool}` | 200 `{token, webhook_secret?}` | FR-064 | R1 |
| `DELETE /v1/chat/agent-accounts/{id}` | – | 204 | FR-064 | R1 |
| `POST /v1/chat/agent-accounts/{id}/redeliver` | – | 202 (dead_letter → pending) | FR-073 | R1.1 |

Bestehend (adapt): `GET/POST /v1/sessions/{id}/chat` (PR #4) als Adapter; `GET /v1/sessions/{id}/stream`, `POST /v1/sessions/{id}/events`, `…/elicitations/{eid}/resolve` unverändert genutzt.

### WS-Frames (adapt `WS /v1/sessions/updates`)
Server → Client, zusätzlich zu `snapshot/changed/removed/hosts_changed/projects_changed/heartbeat`:

```json
{"type":"chat","kind":"message.created","channel_id":"…","message_id":"…","thread_root_id":null,"created_at":1760630400000000,"author_id":"anna@bloopark.de"}
{"type":"chat","kind":"thread.updated","channel_id":"…","root_message_id":"…","reply_count":3,"agent_state":"active","session_id":"…","bot_streak":0}
{"type":"chat","kind":"read.updated","channel_id":"…","thread_root_id":"","created_at":…,"id":"…"}
{"type":"chat","kind":"membership.changed","channel_id":"…"}
{"type":"chat","kind":"typing","channel_id":"…","principal_id":"…","active":true}
```
Kein Body (NFR-014; `_send` ruft `telemetry.record_message_payload(frame)`). Client lädt Inhalte per `GET …/messages?after=`. Unbekannte `kind` werden ignoriert. Client → Server: unverändert (`watch`), Chat-Session-IDs werden in den Watch-Set aufgenommen (Cap 500, `_SESSION_UPDATES_MAX_WATCHED`).

### MCP-Tools (adapt PR #14 `mcp_endpoint.py`)
| Tool | Ruft | Write/confirm |
|---|---|---|
| `chat_list_channels` | `GET /v1/chat/bootstrap` | nein |
| `chat_read` | `GET /v1/chat/channels/{id}/messages` (`channel` = ID oder `#name`, `thread_root_id`, `after`, `limit` ≤ 100; Bodies auf 4000 Zeichen) | nein |
| `chat_post` | `POST …/messages` mit generierter `client_id` | ja |
| `chat_open_dm` | `POST /v1/chat/dms` | ja |
| `chat_list_members` | `GET …/members` | nein |
| `chat_unread` | Mensch: `GET /v1/chat/bootstrap` gefiltert auf `unread_count>0` + `GET /v1/chat/needs-you` (nur Erwähnungen). Agentenkonto: `GET /v1/chat/unread` (FR-088: Erwähnungen + DM-Nachrichten nach Cursor, max 50, älteste zuerst) – Weckweg der Grok-Bot-Polling-Routine in R1 | nein |
| `chat_mark_read` | `POST …/read` | ja |
Bei Agentenkonto-Token liefert `tools/list` nur diese Tools; andere Tool-Namen → `ToolFailure("tool not available for agent accounts")` vor jedem `/v1`-Aufruf (FR-070).

### Webhook `chat.mention` (new)
Envelope wie PR #15 (`event_id`, `event_version=1`, `event_type`, `workspace_id`, `sequence` je Konto, `occurred_at`, `data`); Feld `session_id` entfällt, dafür `account_id`. Header `X-Omnigent-Event-Id`, `-Event-Type`, `-Delivery-Attempt`, `-Timestamp`, `-Signature: v1=<hex>`, `X-Omnigent-Key-Id: <handle>`.
```json
{"event_id":"…","event_version":1,"event_type":"chat.mention","workspace_id":0,"account_id":"…","sequence":12,"occurred_at":1760630400,
 "data":{"reason":"mention|dm","channel_id":"…","channel_name":"billing","channel_kind":"channel","thread_root_id":"…","message_id":"…",
 "author":{"id":"anna@bloopark.de","kind":"user","display":"Anna"},
 "fetch":{"mcp_tool":"chat_read","args":{"channel":"…","thread_root_id":"…","after":"…"}},"web_url":"https://<public_base_url>/chat/…/t/…"}}
```
Secret je Konto: `HMAC_SHA256(OMNIGENT_CHAT_AGENT_SECRET, "chat-webhook:" + account_id + ":" + secret_version)` (hex) – wird nicht gespeichert, nur bei Anlage/Rotation angezeigt (ADR-0004).

## Agent-Thread-Orchestrierung (FR-033…FR-047, FR-054)

```mermaid
sequenceDiagram
  participant A as Anna (Browser)
  participant C as routes/chat.py
  participant O as chat_agent_threads
  participant S as Sessions (exists)
  participant W as WS/SSE
  A->>C: POST messages "<@omnigent_agent:agent> Tests fixen"
  C->>C: speichern, Mentions, Thread anlegen (agent_state=starting)
  C-->>A: 201 {message, agent_thread:{state:starting}}
  C->>O: start(thread, caller=Anna) (Hintergrund-Task)
  O->>O: Projekt/Host auflösen (A-8), Agent auflösen
  O->>S: create session (as Anna, project_id, agent_id, host_id, labels omnigent.chat.v1)
  O->>S: grant READ für Mitglieder (wenn SharingMode erlaubt)
  O->>S: POST /events message "[Anna] …" (Kontext FR-037)
  O->>C: thread.session_id, agent_state=active → frame thread.updated
  S-->>W: session.status running / Stream / elicitation
  S->>O: _publish_status(idle) Hook
  O->>C: Spiegel-Nachricht (author_kind=omnigent_agent) → frame message.created
```

- **Session-Erzeugung:** Der Orchestrator ruft dieselbe interne Funktion wie `POST /v1/sessions` (`_create_session_from_existing_agent`, `omnigent/server/routes/_sessions/orchestration.py:10365`) nach `resolve_project_session_create` (`routes/_session_create_validation.py`), mit `user_id=caller`. Kein HTTP-Selbstaufruf. Fehler werden in Klassen gemappt: Projekt fehlt → `no_project`; `host_id` fehlt → Create ohne Host-Bindung (vom Aufrufer verwalteter Runner, wie heute in `POST /v1/sessions`; so läuft auch der E2E-Runner); Host offline / Runner nicht erreichbar → `host_offline`; Agent-ID unbekannt → `agent_not_found`; `OmnigentError` 403/404 → `forbidden`; sonst `internal` (Log mit Exception, Thread ohne Details) (FR-035).
- **Labels:** `omnigent.chat.v1 = {"channel_id":…, "thread_root_id":…}` an der Session, damit Sidebar/Inbox Chat-Sessions erkennen.
- **Grants:** über den Permission-Store mit `LEVEL_READ` für jedes andere menschliche Mitglied (keine Agentenkonten); SharingMode (`omnigent/server/auth.py:128`, Laufzeitwert über `omnigent/server/sharing_settings.py`) prüfen; bei Verbot Systemzeile (FR-034). Beim Join (FR-086) dieselbe Grant-Funktion für alle `chat_threads` des Channels mit `session_id`; Leave entzieht nichts (A-20).
- **Start-Timeout (FR-087):** `start_requested_at` wird beim Übergang nach `starting` gesetzt; ein Abgleich im Dispatcher-Loop (alle 30 s) und jeder Thread-Lesezugriff setzen Threads mit `starting` und Alter > 120 s auf `start_failed`/`timeout`. Da Starten in-process läuft (C-2), deckt das Neustarts ab.
- **Verzeichnis (A-21):** `directory` im Bootstrap = Mitglieder von `#general` (jeder Mensch tritt beim ersten Bootstrap bei) + aktive Agentenkonten; keine Abhängigkeit vom Accounts-Store, funktioniert in Header-/OIDC-/Single-User-Modus.
- **Bot-DM-Regel (FR-021):** DM-Teilnehmermenge mit Agentenkonto ohne dessen Owner → 403 `agent_owner_required` (Default zu Q-13).
- **Weiterleitung (R1):** Bei Thread-Antwort eines Menschen mit `level ≥ EDIT` (Permission-Store `check_access`) → `POST /v1/sessions/{id}/events` intern mit `type: message` und Text `[Name] body`; Ergebnis setzt `forward_state`. READ-Autoren → `not_forwarded`. Agentenkonten → nie (FR-077).
- **Spiegeln:** `on_session_status(session_id, "idle", response_id)` → wenn `chat_threads.session_id == session_id`: letzten Assistant-Text über den `summary_lookup` aus `omnigent/server/manager_webhook_context.py` (PR #15, `build_lookups`) ohne Kürzung auf 500, sondern auf 32 000 Zeichen (geprüft: liefert den Text der neuesten Assistant-Message unter den letzten 8 Items; synchroner Store-Aufruf → `asyncio.to_thread`); Idempotenz über `(session_ref, response_ref)` (keine Doppelspiegelung bei wiederholtem idle). `failed` → Systemzeile „Agent-Fehler: {Fehlerklasse}“ ohne Rohtext.
- **Status im Thread:** offen → Session-SSE über `conversationRegistry` (Status ≤ 1 s); Channel-Ansicht → WS-Watch (`SessionListItem.status`, ≤ 5 s).
- **Neustart:** Orchestrator ist zustandslos; Zustand liegt in `chat_threads`. Spiegelung wird beim nächsten idle-Edge nachgeholt; beim Öffnen eines Threads gleicht der Client den Status aus der Session ab (NFR-020).

## Error handling
| Fall | Verhalten | FR |
|---|---|---|
| Flag aus | 404 vor Store-Zugriff; WS ohne `chat`-Frames; MCP ohne Tools | FR-003 |
| Validierung | 422 mit `code` (`empty_body`, `body_too_long`, `nul_in_body`, `nested_thread`, `thread_root_not_found`, `invalid_client_id`, `invalid_cursor`) | FR-015, FR-024 |
| Nicht Mitglied | 403 `not_a_member` (öffentlich) / 404 (DM, Session-Chat) | FR-016, FR-022 |
| Rate-Limit | 429 + `Retry-After`; in-process Sliding Window je (Principal, Channel) | FR-017 |
| Idempotenz | 200 Original / 409 `client_id_conflict` | FR-018 |
| Bot-Bremse | 409 `bot_streak_limit` | FR-074 |
| Agentenkonten deaktiviert (kein Secret) | 503 `agent_accounts_unconfigured` | A-13 |
| Agent-Start | `start_failed` + Systemzeile mit Klasse | FR-035 |
| Weiterleitung | `forward_state=failed`, Retry-Route | FR-054 |
| Webhook | Backoff, `dead_letter`, Konto-Status „Webhook fehlerhaft“ | FR-072, FR-073 |
| Changes zu alt | `reset: true` | FR-083 |
| Client-Senden | „nicht gesendet“, Retry mit gleicher `client_id` | FR-084 |

## Cross-cutting

### Sicherheit
**Rechte-Matrix** (Grundlage für NFR-012-Tests; „—“ = verboten):

| Aktion | Mensch Mitglied | Mensch Nicht-Mitglied | Admin | Agentenkonto (Owner Mitglied) | Agentenkonto (Owner nicht Mitglied) | Public-Besucher |
|---|---|---|---|---|---|---|
| Öffentlichen Channel lesen | ja | nur Metadaten + Beitreten | wie Mensch | ja, wenn selbst Mitglied | — (403) | — |
| In Channel posten | ja | — (403) | wie Mensch | ja, wenn Mitglied, Bremse beachten | — | — |
| Channel anlegen | ja (Q-16) | ja | ja | — | — | — |
| Channel beitreten | ja | ja | ja | nur wenn Owner Mitglied; nur durch Owner hinzufügbar oder via `chat_join` [s. Q-13] | — | — |
| DM lesen/posten | Teilnehmer | — (404) | — (404) | nur wenn Owner Teilnehmer (Q-13) | — | — |
| Session-Chat | direkte Grants (PR #4-Regel) | — | nur lesen | — (A-12) | — | — (403) |
| Agent-Thread starten | ja | — | wie Mensch | — (Systemzeile) | — | — |
| Agent-Live-Ansicht | READ-Grant | — | Admin-Regeln exists | — (kein Session-Zugriff) | — | — |
| Freigabe beantworten | EDIT+ | — | Admin-Regeln exists | — | — | — |
| Agentenkonto verwalten | eigene | eigene | alle widerrufen | — | — | — |

**Agentenkonto-Auth (ADR-0004):** In `AuthProvider` vor Cookie/JWT-Prüfung: Header `Authorization: Bearer ogc_…` → `hmac_sha256(secret, token)` → Lookup `chat_agent_accounts.token_hash` (nicht widerrufen). Treffer nur, wenn Pfad `== /mcp` oder beginnt mit `/v1/chat/`; sonst `None` (401). `last_seen_at` höchstens einmal pro Minute aktualisieren. In-process-Aufrufe aus `/mcp` (`_Api.call`) leiten den Header weiter (exists, `_forwarded_headers`) und landen auf `/v1/chat/*` – erlaubt. `/mcp` muss Agentenkonto-Tokens akzeptieren, obwohl es delegierte Scope-Tokens ablehnt: Der neue Zweig ist kein Scope-Token, sondern eigener Principal-Typ.
**Sichtbarkeit:** `chat_access.can_read(agent)` prüft bei jedem Request Mitgliedschaft des Agentenkontos **und** des Owners (FR-067) – keine Zwischenspeicherung.
**Inhalte:** Bodies nie in Frames, Webhooks, Logs, Audit (NFR-014); Rendering laut FR-020/A-15.
**SSRF:** `host_is_internal` bei Anlage und vor jedem Versand; `follow_redirects=False`; Timeout 10 s (NFR-016).

### Privacy
Agentenkonto-Owner kann über das Token alles lesen, was das Konto lesen darf; deshalb Teilmengenregel (FR-067) und Hinweis im DM-/Mitglieder-UI „Grok (Jens Holze) – Nachrichten sind für Jens über seinen Bot lesbar“. Keine Nachrichtentexte in OS-Notifications (FR-011).

### Observability
Audit via `add_audit_attrs` (Aktion, Channel-ID, Message-ID, Account-ID; kein Body) (NFR-028). Logger `omnigent.chat` mit `debug_event`-Muster; Zähler: Posts, Frames, Agent-Starts je Fehlerklasse, Webhook-Zustellungen/Dead-Letters.

### Performance
Paging über zusammengesetzte Indizes; Unread-Zählung per `COUNT` mit Cursor-Bedingung je Channel (≤ 200 Channels → eine gruppierte Abfrage); Fan-out an Mitglieder über In-Memory-Set der `user_session_stream`-Subscriber (no-op ohne Verbindung). Web: Liste ab 200 geladenen Nachrichten virtualisiert [ANNAHME A-19: ohne neue Dependency, einfache Fensterung], Motion über `LazyMotion`.

### UX-Grundlage (ADR-0008)
- **Basis:** AI Elements (`web/src/components/ai-elements/*`: `conversation`, `message`, `reasoning`, `shimmer`, `code-block` vorhanden) + Motion. Fehlende Bausteine (Tool-Gruppe, Task-Liste, Bestätigungskarte-Hülle, Prompt-Vorschläge) werden nach AI-Elements-/prompt-kit-Muster in `web/src/chat/` neu gebaut.
- **Agent-Schritte und Status (Referenz interior.dev TaskSteps, https://www.interior.dev/docs/task-steps):** `web/src/chat/agent/TaskSteps.tsx` mit Hook `useTaskSteps({steps, current, failed})` → Zeilen mit Status `pending`/`active`/`done`/`error`; Mapping aus Session-Todos bzw. Tool-Calls des aktuellen Turns (`StepsGroup` klappt sie als „n Schritte“ ein). Visuell: Punkt (pending), drehender Bogen + Shimmer-Label (active), Häkchen mit Pop-Spring 640/22/0.7 (done, Dauer als Mono-Meta `tabular-nums` rechts), Kreuz mit Pop-Spring (error, Folgeschritte bleiben pending). `useReducedMotion()` → nur Opacity, kein Drehen/Shimmer. a11y: `<ol aria-label>`, `aria-current="step"`, sr-only `role="status"` mit „{Label}, Schritt n von m“ (500 ms entprellt), polite-Region „Lauf abgeschlossen/fehlgeschlagen“. Icons über lucide (`Check`, `X`, `LoaderCircle`) statt der Original-SVGs (NFR-010). Dieselbe Bildsprache für `AgentStatusChip` (FR-044). Code-Übernahme per `bunx shadcn add https://www.interior.dev/r/task-steps.json` nur nach Lizenzklärung (Q-20); bis dahin Nachbau. Einzige Dependency `motion` (ohnehin T-117).
- **Muster (nachgebaut, kein Code):** geglättetes Streaming, Freigabe-Karte, geführte Fragen, Queue/Steer, Run-Stuck-Banner, Task-Karten (agent-native); Statusbegriffe processing/active/suspended/closed, Stop-Button, Thinking-Steps, Suggested Prompts (Slack).
- **Motion-Tokens** (`web/src/chat/motion.ts`): `durations = {micro:150, enter:180, chip:200, pane:220, layout:280}`, `easeOut = [0.2, 0, 0, 1]`, `spring = {type:"spring", stiffness:500, damping:40}`, `pop = {type:"spring", stiffness:640, damping:22, mass:0.7}`, `cell = {type:"spring", stiffness:520, damping:34, mass:0.45}`, `spin = 0.8 s linear`, `shimmer = 1.6 s linear`; ausschließlich `transform`/`opacity` animieren.
- **Icons:** lucide; Mapping Status → `Loader2` (dreht, bei Reduced Motion statisch), `CircleHelp`/`MessageCircleQuestion` (braucht Antwort), `CheckCircle2` (fertig), `XCircle` (Fehler), `MoonStar`/`LogOut` (entlassen), `Bot` (Agentenkonto-Badge).
- **UX-Agent:** prüft jede UI-PR gegen plugin87/ux-ui-agent-skills-Doku (motion-choreography, icon-system, a11y) als Checkliste `docs/ux/chat-review-checklist.md` (new, T-000); nur Dokumente, keine Hooks.

## Alternatives considered
| Entscheidung | Alternative | Warum verworfen |
|---|---|---|
| Ein Messages-Table mit `thread_root_id` (ADR-0001) | getrennte Tabellen je Kind (channel/dm/session) | dreifache Store-/Routenlogik, schlechter für spätere Slack-Brücke/Suche |
| Session-Chat als `kind=session` (ADR-0002) | PR #4-Tabelle parallel behalten | zwei Chat-Systeme, doppelte Unread-/Mention-Logik |
| User-Stream über bestehenden WS (ADR-0003) | neue SSE `/v1/chat/stream` | zweite Dauerverbindung je Tab (HTTP/1.1-Limit, R-6); WS hat bereits Auth, Reconnect, `user_session_stream` |
| Frames ohne Body + Refetch | Body im Frame | Telemetrie zeichnet Frames auf (`record_message_payload`); Bestand-Spec-Prinzip; +1 RTT akzeptabel (NFR-001) |
| Agent-Thread = echte Session (ADR-0005) | Agent liest Channel als „Listener“ ohne Session | keine Freigaben/Status/Streaming-Wiederverwendung; widerspricht Jens' Entscheidung |
| Weiterleitung nur EDIT+ | alle Mitglieder weiterleiten | READ-Mitglieder könnten Agent steuern → verletzt Q-9/Rechte-Modell |
| Agentenkonto-Token eigener Zweig (ADR-0004) | Device-Grant/Client-Credentials wiederverwenden | Client-Credentials = ein Env-Client; Device-Grant = Mensch delegiert alles; beide über `_DELEGATED_ALLOWED_PREFIXES`, das Chat-Pfade für alle Scope-Tokens öffnen würde |
| Separate Chat-Outbox (ADR-0007) | PR #15 `session_lifecycle_outbox` erweitern | dort Ordnung je `session_id` und globaler Endpoint aus Config; Chat braucht Ordnung je Agentenkonto und URL je Konto |
| Ein Flag `team_chat` (ADR-0006) | `session_human_chat` + `team_chat` | zwei Flags für ein Datenmodell; PR #4 hat noch kein Flag |
| Motion | nur CSS/`tw-animate-css` (exists) | Jens' Vorgabe; Layout-/Presence-Animationen und Reduced-Motion-Steuerung zentral |

## Test strategy
- **Backend (pytest):** `tests/server/test_chat_routes.py`, `test_chat_store.py` (bzw. `tests/stores/test_chat_store.py`), `test_chat_agent_threads.py`, `test_chat_agent_accounts.py`, `test_chat_agent_account_permissions.py` (Matrix NFR-012), `test_chat_mcp_tools.py`, `test_chat_outbox.py`, `tests/integration/test_chat_webhook_delivery.py`, `tests/db/test_migration_chat.py`, `test_chat_no_body_leak.py` (NFR-014). Lauf: `uv run --no-sync pytest tests/server/test_chat_*.py tests/stores/test_chat_store.py tests/db/test_migration_chat.py -q`.
- **Bestand grün halten:** `tests/server/test_session_chat.py`, `tests/server/test_feature_flags.py`, `tests/server/test_openapi_drift.py`, `tests/server/test_mcp_endpoint.py`, `tests/server/test_session_outbox.py`.
- **Web (Vitest):** `web/src/chat/**/*.test.tsx`; Lauf `pnpm --filter web run test -- src/chat`, plus `pnpm --filter web run lint` und `pnpm --filter web run type-check`.
- **E2E (Playwright, Pflicht):** neue Suite `tests/e2e_ui/team_chat/` im bestehenden pytest-playwright-Gerüst (`tests/e2e_ui/conftest.py`, Mock-LLM `tests/server/integration/mock_llm_server.py`). Eigene Fixture `team_chat_server` (Modul-Scope): spawnt `omnigent server` mit `OMNIGENT_FEATURES=team_chat`, Runner + Mock-LLM wie `live_server`, Identitäten über `X-Forwarded-Email`-Header je Browser-Kontext (Muster `tests/e2e_ui/collaboration/test_author_label.py`); der Aufrufer von Agent-Threads ist der Runner-Owner. Je R0/R1-UI-Story mindestens eine Datei (Liste in `07-tasks.md`). Selektoren über `data-testid` (`chat-sidebar`, `chat-message`, `chat-composer`, `thread-pane`, `agent-status-chip`, `approval-card`, …). Reduced Motion via `browser.new_context(reduced_motion="reduce")`, Offline via `context.set_offline(True)`, Fehler via `page.route`. axe über `axe-playwright-python`. Lauf: `uv run --no-sync pytest tests/e2e_ui/team_chat -v` (einmalig `uv run --no-sync playwright install --with-deps chromium`).
- **Ersetzt:** `tests/e2e_ui/collaboration/test_session_human_chat.py` (PR #4) wird auf das neue Session-Chat-UI angepasst (T-113), nicht gelöscht.
- **Tester-Gate:** pro PR Backend-Tests + Vitest + betroffene E2E-Dateien; vor Release komplette `team_chat`-Suite 3× (NFR-024).

## Rollout & migration
1. **T-000 (vor allem anderen):** Merge-Zug der Voraussetzungen #5 → #4 → #15 → #14 auf `main`, außerdem React 19 (#16, `feat/react-19`) vor dem ersten UI-Task T-006 (Entscheidung Jens 16.10. 23:25) des Forks mit Re-Chaining der Revisionen (`nn1a…` → `sc1a…` → `mw1b…` oder Merge-Revision), ein `principals.py`, Tests grün. Danach setzt die Chat-Migration auf diesen einzigen Head.
2. Chat-Migration läuft unabhängig vom Flag (leere Tabellen harmlos).
3. R0 (Mi 21.10.): Deploy auf Testinstanz mit `OMNIGENT_FEATURES=team_chat`; Demo.
4. R1 (Fr 23.10. Code-Freeze 18:00, Sa 24.10. Deploy): Backup Produktiv-DB → Probe-Upgrade auf Kopie → Neuinstallation aus Fork → `GET /v1/info` prüfen → Flag setzen → Smoke (T-123) → Team einladen.
5. **Cut-Line R1** (bei Verzug in dieser Reihenfolge streichen, ohne Must zu verletzen): FR-081 (Vorschläge), FR-032 (Typing), FR-060 (PR-Karte), FR-053 (Run-Stuck), FR-048 (Schritte), FR-059 (Plan-Review im Thread → Link „in Session prüfen“), FR-076 (Bot-Status). (FR-071…073 Webhook-Wecken bereits nach R1.1 verschoben, Q-18.)
6. Rollback: Flag entfernen; Daten bleiben (NFR-025). Downgrade der Migration nur, wenn nie produktiv genutzt.

## Open questions
Siehe `04-requirements.md` §12 (Q-12…Q-18). Design-Annahmen A-19…A-21 stehen in `04-requirements.md` §11.

## Changelog
- v0.1 (2026-10-16) Erste Fassung.
- v0.4 (2026-10-16) interior.dev TaskSteps als Referenz für Agent-Schritte/Status (FR-048/089/090), Motion-Tokens `pop`/`cell`/`spin`/`shimmer`, Q-20; D-1 (React 19 lokal, Session `6c30fca5…`).
- v0.3 (2026-10-16) Q-18: Webhook-Wecken (Outbox/Dispatcher, ADR-0007) erst R1.1; R1-Weckweg `GET /v1/chat/unread` + MCP `chat_unread` (FR-088). React 19 (#16) Voraussetzung der Chat-UI; Motion/Tiptap/Radix unter React 19 im T-006-PR verifizieren.
- v0.2 (2026-10-16) Review-Fixes (06-review.md): Host optional; Start-Timeout; Join-Grants; Sidebar-Filter; Session-Chat ohne Agent-Thread-Sessions; Verzeichnis-Definition; Bot-DM-Regel; Webhook-Grund ohne `thread_follow`; `followers`-Spalte entfernt; Orchestrator-Pfad korrigiert (`_sessions/orchestration.py`).
