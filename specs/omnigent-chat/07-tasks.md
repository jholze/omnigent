# Tasks: Omnigent Chat – R0, R1 (+ R1.1) · aus `04-requirements.md` v0.4 / `05-design.md` v0.4 · Tasks v0.3 (2026-10-16)

Status: Entwurf zur Freigabe durch Jens. Es werden **keine** Issues angelegt, bevor Jens zustimmt (Skill `spec-to-agent-tasks`, Schritt 9). Labels je Task: `spec:omnigent-chat`, `release:R0|R1|R1.1`, `type:feature|chore|test`, `agent-ready`.

## Arbeitsweise (gilt für jeden Task)
- **Laufende Omnigent-Sessions bleiben laufen** – keine Neustarts bestehender Sessions/Runner für diese Arbeit (Entscheidung Jens 16.10.2026).
- **Neue Sessions in `omnigent-src`** nutzen die Hindsight-Bank `coding-agent::omnigent`; **erster Schritt jeder neuen Session: `hindsight_diagnose`**, erst danach Task-Arbeit.
- **React 19 zuerst:** Issue #16 (Branch `feat/react-19`) wird gemergt, **bevor** die Chat-UI-Arbeit (T-006 ff.) beginnt. UI-Tasks tragen die Abhängigkeit „#16 React 19 gemergt“. Abhängigkeit D-1: `feat/react-19` ist ein lokaler Branch in eigenem Worktree auf Jens' Mac; die React-19-Session `6c30fca590d7445caa463df974cdc6e3` arbeitet noch, Push nur mit OK von Jens. Den Merge-Termin über diese Session verfolgen.
- Code-Reviews nur mit lokalen Werkzeugen (Claude Code + lokale Modelle), keine bezahlten Cursor-Modelle. PRs < 400 Zeilen anstreben (R-12).
- Voraussetzung für alles: PR #4, #5, #14, #15 auf `main` des Forks (C-3), danach T-000.
- Rollen: `coder_deepseek` = Store/Routen/Migrationen · `coder_grok` = UI + modulübergreifend · `ux` = Checkliste/Review · `test_author` = Playwright/pytest · `tester` = Gate (3 grüne Läufe) · `reviewer` = Code-Review jeder PR.
- Jeder UI-PR: UX-Review-Notiz gegen `docs/ux/chat-review-checklist.md` (NFR-027) und mindestens ein E2E-Test für die betroffene Story (NFR-023).

## Waves
| Release | Wave | Tasks | Ziel/Termin |
|---|---|---|---|
| R0 | 0 | T-000 | Sa 17.10. |
| R0 | 1 | T-001 [P], T-004 [P] | Sa 17.10. |
| R0 | 2 | T-002 | So 18.10. |
| R0 | 3 | T-003 [P], T-006 [P] | Mo 19.10. |
| R0 | 4 | T-005 [P], T-007 [P] | Mo 19.10. |
| R0 | 5 | T-008 | Di 20.10. |
| R0 | 6 | T-009 | Di 20.10. abends → Demo Mi 21.10. |
| R1 | 7 | T-101 [P], T-102 [P], T-105 [P] | Mi 21.10. |
| R1 | 8 | T-103 [P], T-104 [P], T-106 [P], T-110 [P], T-111 [P], T-117 [P] | Mi 21.10.–Do 22.10. |
| R1 | 9 | T-107 [P], T-109 [P], T-112 [P], T-113 [P], T-116 [P], T-118 [P] | Do 22.10. |
| R1 | 10 | T-114 [P], T-115 [P], T-119 [P], T-124 [P] | Do 22.10.–Fr 23.10. |
| R1 | 11 | T-120 [P], T-121 [P], T-122 [P] | Fr 23.10. |
| R1 | 12 | T-123 | Sa 24.10. (Launch) |
| R1.1 | 13 | T-108 [P], T-116b [P] | nach Launch (R1.1) |

**Aufwand (Schätzung, Agent-Stunden inkl. Review):** R0 82 h · R1 118 h · R1.1 11 h. Must-Anteil R0+R1 nach Aufwand ≈ 81 % (nur Feature-Tasks, anteilig nach Must-FRs je Task; Setup/Test/Release ausgenommen). Liegt über dem 60-%-Richtwert; Jens hat den Umfang mit Q-18 bewusst so entschieden und das Webhook-Wecken nach R1.1 geschoben. Puffer schafft nur die Cut-Line (05 §Rollout).

Anzahl: R0 10 Tasks · R1 23 Tasks · R1.1 2 Tasks · R2 Epic-Skizze.

## Tasks R0 – Walking Skeleton (Demo Mi 21.10.2026)

### T-000: Voraussetzungen zusammenführen: ein Alembic-Head, eine `principals.py`, UX-Checkliste, AGENTS.md-Ergänzungen
**Release:** R0   **Phase / wave:** Setup / 0   **Parallel:** nein   **Depends on:** –   **Rolle:** coder_deepseek (Teil A) · ux (Teil B) · reviewer   **Aufwand:** 6 h
**Implements:** NFR-022, NFR-027
**Labels:** `spec:omnigent-chat`, `release:R0`, `type:chore`, `agent-ready`

**Context**
PR #4 (`sc1a2b3c4d5e`), PR #5 (`nn1a2b3c4d5e`) und PR #15 (`mw1b2c3d4e5f`) setzen alle `down_revision="mm1a2b3c4d5e"` → drei Alembic-Heads; PR #4 und #5 legen beide `omnigent/server/principals.py` an. Jens merged #5 → #4 → #15 → #14 (Design §Rollout). Dieser Task macht danach den Baum wieder lauffähig und legt die Prozess-Artefakte an. Teil A darf erst starten, wenn die vier PRs auf `main` des Forks sind.

**Scope**
- `omnigent/db/migrations/versions/*` (adapt: `down_revision` der drei Revisionen linearisieren ODER Alembic-Merge-Revision, Entscheidung im PR begründen)
- `omnigent/server/principals.py` (adapt: eine Fassung)
- `docs/ux/chat-review-checklist.md` (new, Teil B: Punkte aus plugin87/ux-ui-agent-skills motion-choreography, icon-system, a11y, nur Doku)
- `AGENTS.md` (adapt: Abschnitt „Team Chat“ laut 07 §AGENTS.md)
- `.github/pull_request_template.md` (adapt: Checkbox „UX-Review-Notiz“)
- Out of scope / nicht anfassen: Keine Feature-Logik; keine Änderungen am Verhalten von PR #4/#5/#14/#15 außer Merge-Konflikten.

**Acceptance criteria**
```gherkin
Given die PRs #4, #5, #14 und #15 sind gemergt
When `test_single_alembic_head` läuft
Then gibt es genau einen Alembic-Head und `upgrade head` läuft auf SQLite und CockroachDB
```
```gherkin
Given eine UI-PR
When der Reviewer das PR-Template öffnet
Then enthält es die Checkbox "UX-Review-Notiz gegen docs/ux/chat-review-checklist.md"
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: `tests/db/test_migration_connections.py::test_single_alembic_head` (exists, muss grün sein)
- Run: `uv run --no-sync pytest tests/db/test_migration_connections.py::test_single_alembic_head -q` → grün
- Run: `uv run --no-sync pytest tests/server/test_session_chat.py -q` → grün
- Run: `just crdb-test` → grün
- Run: `pre-commit run --all-files` → grün
- Run: `just lint` → grün
- Run: `just typecheck-python` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-001: Flag `team_chat`, SQLAlchemy-Modelle und Migration der Chat-Tabellen
**Release:** R0   **Phase / wave:** Foundational / 1   **Parallel:** [P] ja   **Depends on:** T-000   **Rolle:** coder_deepseek · reviewer   **Aufwand:** 6 h
**Implements:** FR-002, FR-003, NFR-022, NFR-025; ACs: AC-002a, AC-002b, AC-003a, AC-003b
**Labels:** `spec:omnigent-chat`, `release:R0`, `type:feature`, `agent-ready`

**Context**
Grundlage für alle weiteren Tasks: ein Feature-Flag (ADR-0006) und das einheitliche Datenmodell (ADR-0001). Tabellen gemäß 05 §Datenmodell: `chat_channels`, `chat_members`, `chat_messages`, `chat_threads` (inkl. `start_requested_at`), `chat_mentions`, `chat_reads`, `chat_agent_accounts`, `chat_agent_outbox`, `chat_user_prefs`. Keine Foreign Keys, Zeiten epoch µs (`omnigent/db/utils.py:now_epoch_us`). R1-Tabellen werden schon jetzt angelegt, damit es nur eine Migration gibt.

**Requirements (zitiert)**
- FR-002 (Must, R0): The Server shall define feature flag `team_chat` (default off, `frontend_visible`) and report it in `GET /v1/info` `features`.
- FR-003 (Must, R0): If `team_chat` is disabled, then the Server shall answer every `/v1/chat/*` request with 404 before reading or writing any chat table, omit `chat` frames from WS `/v1/sessions/updates`, and omit `chat_*` tools from `/mcp`.

**Scope**
- `omnigent/server/feature_flags.py` (adapt: `Feature.TEAM_CHAT`, Definition owner `web`, `review_by_release` `0.20.0`, `frontend_visible`)
- `designs/FEATURE_FLAGS.md` (adapt)
- `omnigent/db/db_models.py` (adapt: `SqlChat*`)
- `omnigent/db/migrations/versions/tc1a2b3c4d5e_add_team_chat.py` (new, `down_revision` = Head nach T-000)
- `tests/db/test_migration_team_chat.py` (new)
- `tests/server/test_feature_flags_team_chat.py` (new)
- Out of scope / nicht anfassen: Kein Store, keine Routen. `session_chat_messages` wird hier NICHT migriert (T-102).

**Acceptance criteria**
*AC-002a (FR-002)*
```gherkin
Given der Server läuft mit OMNIGENT_FEATURES=team_chat
When GET /v1/info aufgerufen wird
Then enthält features den Schlüssel "team_chat" mit true
```
*AC-002b (FR-002)*
```gherkin
Given OMNIGENT_FEATURES ist leer
When GET /v1/info aufgerufen wird
Then ist features "team_chat" false
```
*AC-003a (FR-003)*
```gherkin
Given team_chat ist nicht aktiv
When Anna POST /v1/chat/channels/{general}/messages mit body "Hallo" sendet
Then antwortet der Server 404 und chat_messages enthält keine neue Zeile
```
*AC-003b (FR-003)*
```gherkin
Given team_chat ist nicht aktiv
When ein User-JWT tools/list auf /mcp aufruft
Then enthält die Liste kein Tool mit Präfix "chat_"
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: `tests/db/test_migration_team_chat.py`: upgrade/downgrade auf SQLite, Unique-Constraints (`dm_key`, `client_id`), Single-Head
- Add tests: `tests/server/test_feature_flags_team_chat.py`: Flag default aus, `GET /v1/info`
- Run: `uv run --no-sync pytest tests/db/test_migration_team_chat.py tests/server/test_feature_flags_team_chat.py tests/db/test_migration_connections.py -q` → grün
- Run: `just crdb-test` → grün
- Run: `just lint` → grün
- Run: `just typecheck-python` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-004: Playwright-Gerüst `tests/e2e_ui/team_chat/` mit Fixture `team_chat_server` (Flag an, Mehrbenutzer, Runner, Mock-LLM)
**Release:** R0   **Phase / wave:** Foundational / 1   **Parallel:** [P] ja   **Depends on:** T-000   **Rolle:** test_author · reviewer   **Aufwand:** 6 h
**Implements:** NFR-023, NFR-024
**Labels:** `spec:omnigent-chat`, `release:R0`, `type:test`, `agent-ready`

**Context**
Der geteilte `live_server` in `tests/e2e_ui/conftest.py` ist single-user ohne Flags; `tests/e2e_ui/collaboration/_multi_user_server.py` bindet keinen Runner (R-5). Für Chat brauchen wir einen eigenen Server mit `OMNIGENT_FEATURES=team_chat`, Header-Identitäten (Muster `X-Forwarded-Email` aus `test_author_label.py`) UND einem Runner mit Mock-LLM (`tests/server/integration/mock_llm_server.py`), dessen Owner der Agent-Aufrufer ist (Anna = Runner-Owner). Muster für einen eigens gespawnten Flag-Server: `test_harness_credential_old_host.py`. Neue UI-Testbereiche müssen in `feature-map/` eingetragen werden, sonst schlägt `tests/dev/test_verify_omnigent_feature_map.py` fehl. Abschnitt „Playwright-Struktur (Referenz OKR PM)“ in 07 beachten, sobald Jens ihn nachgetragen hat.

**Scope**
- `tests/e2e_ui/team_chat/__init__.py`, `conftest.py` (new: Fixture `team_chat_server`, `page_as(email)`, Seed-Helfer `seed_channel`, `seed_message` über REST)
- `tests/e2e_ui/team_chat/test_smoke.py` (new: Flag an → `/chat` lädt; Flag aus → kein Eintrag)
- `feature-map/README.md` (adapt: Bereich `team_chat` unter „Not yet mapped“ oder eigene Feature-Datei)
- `web/src/chat/testids.ts` (new: zentrale `data-testid`-Konstanten aus 05 §Teststrategie)
- Out of scope / nicht anfassen: Keine Änderungen am geteilten `live_server`; kein Fix für Issue #2 (Nightly).

**Acceptance criteria**
```gherkin
Given der Server `team_chat_server` läuft mit OMNIGENT_FEATURES=team_chat und einem Mock-LLM-Runner von Anna
When Anna und Jens je einen eigenen Browser-Kontext öffnen
Then sehen beide den Eintrag "Chat" und der Server meldet zwei verschiedene Principals
```
```gherkin
Given der Server läuft ohne Flag
When Anna die App öffnet
Then gibt es keinen Eintrag "Chat" und /v1/chat/bootstrap antwortet 404
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: `tests/e2e_ui/team_chat/test_smoke.py` (2 Fälle; der Flag-an-Fall wird bis T-006 mit `xfail(strict=True)` markiert)
- Run: `uv run --no-sync playwright install --with-deps chromium` → grün
- Run: `uv run --no-sync pytest tests/e2e_ui/team_chat -v --tracing=retain-on-failure` → grün
- Run: `uv run --no-sync pytest tests/dev/test_verify_omnigent_feature_map.py -q` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-002: Chat-Store (`omnigent/stores/chat_store`) für Channels, DMs, Nachrichten, Threads, Mentions
**Release:** R0   **Phase / wave:** Foundational / 2   **Parallel:** nein   **Depends on:** T-001   **Rolle:** coder_deepseek · reviewer   **Aufwand:** 8 h
**Implements:** FR-018, FR-023, FR-024, FR-025, FR-027, NFR-003; ACs: AC-018a, AC-018b, AC-023a, AC-024a, AC-024b, AC-025a, AC-025b, AC-027a, AC-027b
**Labels:** `spec:omnigent-chat`, `release:R0`, `type:feature`, `agent-ready`

**Context**
Persistenz-Schicht ohne HTTP. Muster: bestehende Stores unter `omnigent/stores/*` mit benannten DB-Sessions (`make_named_managed_session_maker`, AGENTS.md). Cursor `(created_at, id)`, Idempotenz über `client_id`, Thread eine Ebene, Zähler/Teilnehmer in `chat_threads`, Mention-Tokens `<@user:ID>`, `<@agent_account:ID>`, `<@omnigent_agent:HANDLE>` werden geparst und in `chat_mentions` gespeichert. Bootstrap `#general` per Unique-Konflikt idempotent.

**Requirements (zitiert)**
- FR-018 (Must, R0): When a post repeats a `client_id` already stored for the same author and Channel, the Server shall return 200 with the original message; if the body differs, then it shall return 409.
- FR-023 (Must, R0): When a member posts with `thread_root_id` naming a top-level message of the same Channel, the Server shall store the message as a Thread reply.
- FR-024 (Must, R0): If `thread_root_id` names a reply, a message of another Channel or no message, then the Server shall return 422 (`nested_thread` / `thread_root_not_found`) and store nothing.
- FR-025 (Must, R0): When a Thread reply is stored, the Server shall update the root's `reply_count`, `last_reply_at` and up to 5 latest participant ids and publish `thread.updated`.
- FR-027 (Must, R0): When a stored body contains tokens `<@user:ID>`, `<@agent_account:ID>` or `<@omnigent_agent:HANDLE>`, the Server shall validate each against existing principals/handles, record one `chat_mentions` row per valid target, and leave invalid tokens as literal text.

**Scope**
- `omnigent/stores/chat_store/__init__.py`, `sqlalchemy_store.py` (new)
- `omnigent/server/chat_mentions.py` (new: Token-Parser, rein funktional)
- `tests/stores/test_chat_store.py`, `tests/server/test_chat_mentions.py` (new)
- Out of scope / nicht anfassen: Keine Routen, kein Rechte-Check (T-003), keine Echtzeit.

**Acceptance criteria**
*AC-018a (FR-018)*
```gherkin
Given Anna hat "Hallo" mit client_id "c-1" gespeichert (id m1)
When sie denselben Request erneut sendet
Then antwortet der Server 200 mit id m1 und es gibt genau eine Zeile
```
*AC-018b (FR-018)*
```gherkin
When Anna client_id "c-1" mit body "Anders" sendet
Then antwortet der Server 409 und m1 bleibt "Hallo"
```
*AC-023a (FR-023) – E2E*
```gherkin
Given Annas Top-Level-Nachricht m1 in "#general"
When Jens im Thread von m1 "Passt" antwortet
Then zeigt m1 bei Anna "1 Antwort" und der Thread zeigt "Passt"
```
*AC-024a (FR-024)*
```gherkin
Given r1 ist eine Antwort im Thread von m1
When Jens mit thread_root_id r1 postet
Then antwortet der Server 422 mit code "nested_thread"
```
*AC-024b (FR-024)*
```gherkin
When Jens in "#general" mit thread_root_id einer Nachricht aus "#billing" postet
Then antwortet der Server 422 mit code "thread_root_not_found"
```
*AC-025a (FR-025)*
```gherkin
When Jens und danach Bob je eine Antwort auf m1 posten
Then hat m1 reply_count 2, last_reply_at der Antwort von Bob und Teilnehmer [Bob, Jens]
```
*AC-025b (FR-025)*
```gherkin
When 7 verschiedene Personen auf m1 antworten
Then enthält participants genau die 5 zuletzt antwortenden Personen
```
*AC-027a (FR-027)*
```gherkin
When Jens "<@user:anna@bloopark.de> bitte prüfen" postet
Then existiert ein chat_mentions-Eintrag für anna@bloopark.de
```
*AC-027b (FR-027)*
```gherkin
When Jens "<@user:nobody@x.de> hallo" postet
Then wird kein chat_mentions-Eintrag erzeugt und der Text bleibt wörtlich erhalten
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: `tests/stores/test_chat_store.py` (Paging, Idempotenz, Thread-Regeln, Zähler, gleichzeitiges `#general`)
- Add tests: `tests/server/test_chat_mentions.py`
- Run: `uv run --no-sync pytest tests/stores/test_chat_store.py tests/server/test_chat_mentions.py -q` → grün
- Run: `just crdb-test` → grün
- Run: `just lint` → grün
- Run: `just typecheck-python` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-003: REST-Routen R0, Zugriffsregeln, `chat`-WS-Frames und Changes-Feed
**Release:** R0   **Phase / wave:** Story ST-2.1.1/2.1.2/2.2.1/6.1.1 / 3   **Parallel:** [P] ja   **Depends on:** T-002   **Rolle:** coder_deepseek · reviewer   **Aufwand:** 10 h
**Implements:** FR-003, FR-004, FR-005, FR-006, FR-013, FR-014, FR-015, FR-016, FR-019, FR-021, FR-022, NFR-001, NFR-014; ACs: AC-003a, AC-003b, AC-004a, AC-004b, AC-005a, AC-005b, AC-006a, AC-006b, AC-013a, AC-013b, AC-014a, AC-014b, AC-015a, AC-015b, AC-015c, AC-016a, AC-016b, AC-019a, AC-019b, AC-021a, AC-021b, AC-021c, AC-022a
**Labels:** `spec:omnigent-chat`, `release:R0`, `type:feature`, `agent-ready`

**Context**
Router `create_chat_router` unter `/v1/chat` (05 §API). Flag aus → 404 vor jedem Store-Zugriff. Rechte in `omnigent/server/chat_access.py` (Mitgliedschaft; DM nur Teilnehmer). Nach Commit `user_session_stream.publish` an alle berechtigten Principals; im WS `/v1/sessions/updates` (`omnigent/server/routes/sessions/routes_core.py`, Dispatch nach `evt_type`) neuer Zweig `elif evt_type == "chat"` → Frame `{type:"chat", kind, …}` OHNE Body (ADR-0003). `GET /v1/chat/changes?after=` liefert IDs für Backfill. Verzeichnis = Mitglieder `#general` + Agentenkonten (A-21).

**Requirements (zitiert)**
- FR-003 (Must, R0): If `team_chat` is disabled, then the Server shall answer every `/v1/chat/*` request with 404 before reading or writing any chat table, omit `chat` frames from WS `/v1/sessions/updates`, and omit `chat_*` tools from `/mcp`.
- FR-004 (Must, R0): When an authenticated principal calls `GET /v1/chat/bootstrap`, the Server shall ensure the Channel `general` exists exactly once per workspace, add a human caller as member, and return joined Channels/DMs, directory entries and a `changes_cursor`.
- FR-005 (Should, R0): When a human calls `POST /v1/chat/channels` with a name matching `^[a-z0-9][a-z0-9-]{0,39}$` that is unused in the workspace, the Server shall create a public Channel and make the caller a member.
- FR-006 (Must, R0): When a human calls `POST /v1/chat/channels/{id}/join` or `/leave` on a public Channel, the Server shall add or remove the membership; leaving `general` shall be refused with 409.
- FR-013 (Must, R0): When a member posts `{body, client_id[, thread_root_id]}` to `POST /v1/chat/channels/{id}/messages`, the Server shall store the trimmed body with server time `created_at` (epoch µs) and server-set author, and return 201 with the message.
- FR-014 (Must, R0): When a message is stored, the Server shall publish a `chat` frame `message.created` containing `channel_id`, `message_id`, `thread_root_id`, `created_at`, `author_id` and no body to the WS connections of every member of the Channel.
- FR-015 (Must, R0): If the body is empty after trim, longer than 4000 characters, or contains U+0000, then the Server shall return 422 and store nothing.
- FR-016 (Must, R0): If the caller is not a member of the Channel, then the Server shall return 403 `not_a_member` for a public Channel and 404 for a DM or Session-Chat, and store nothing.
- FR-019 (Must, R0): When a member calls `GET /v1/chat/channels/{id}/messages` with `tail=1`, `before=<µs>:<id>` or `after=<µs>:<id>` and `limit` (default 50, max 200), the Server shall return top-level messages (or, with `thread_root_id`, the Thread's replies) in ascending `(created_at, id)` order with `has_older`/`has_more`.
- FR-021 (Must, R0): When a principal calls `POST /v1/chat/dms` with 1–8 other principals, the Server shall return the existing DM for exactly that participant set including the caller, or create it; if the set contains an Agentenkonto whose owner is not in the set, then the Server shall return 403 `agent_owner_required` [OFFEN Q-13].
- FR-022 (Must, R0): If a principal who is not a DM participant requests the DM or its messages, then the Server shall return 404.

**Scope**
- `omnigent/server/routes/chat.py` (new)
- `omnigent/server/chat_access.py` (new)
- `omnigent/server/chat_realtime.py` (new)
- `omnigent/server/routes/sessions/routes_core.py` (adapt: WS-Zweig)
- `omnigent/server/app.py` (adapt: Router registrieren)
- `openapi.json` (regeneriert)
- `tests/server/test_chat_routes.py`, `tests/server/test_chat_realtime.py` (new)
- Out of scope / nicht anfassen: Kein Unread (T-101), keine Agent-Logik (T-005), kein Rate-Limit (T-119).

**Acceptance criteria**
*AC-003a (FR-003)*
```gherkin
Given team_chat ist nicht aktiv
When Anna POST /v1/chat/channels/{general}/messages mit body "Hallo" sendet
Then antwortet der Server 404 und chat_messages enthält keine neue Zeile
```
*AC-003b (FR-003)*
```gherkin
Given team_chat ist nicht aktiv
When ein User-JWT tools/list auf /mcp aufruft
Then enthält die Liste kein Tool mit Präfix "chat_"
```
*AC-004a (FR-004)*
```gherkin
Given es existiert kein Channel "general"
When Anna und Jens gleichzeitig GET /v1/chat/bootstrap aufrufen
Then existiert genau ein Channel "general" und beide sind Mitglied
```
*AC-004b (FR-004)*
```gherkin
When das Agentenkonto grok-jens GET /v1/chat/bootstrap aufruft
Then wird grok-jens nicht Mitglied von "general"
```
*AC-005a (FR-005)*
```gherkin
When Anna POST /v1/chat/channels mit name "billing-ops" sendet
Then antwortet der Server 201 und Anna ist Mitglied von "#billing-ops"
```
*AC-005b (FR-005)*
```gherkin
When Anna POST /v1/chat/channels mit name "Billing Ops!" oder dem bestehenden Namen "billing" sendet
Then antwortet der Server 422 bzw. 409 und es wird kein Channel angelegt
```
*AC-006a (FR-006)*
```gherkin
Given Carla ist nicht Mitglied von "#billing"
When Carla POST /v1/chat/channels/{billing}/join sendet
Then ist Carla Mitglied und "#billing" erscheint in ihrer Sidebar
```
*AC-006b (FR-006)*
```gherkin
When Anna POST /v1/chat/channels/{general}/leave sendet
Then antwortet der Server 409 und Anna bleibt Mitglied
```
*AC-013a (FR-013, FR-014) – E2E*
```gherkin
Given Anna und Jens haben "#general" offen
When Anna "Deploy heute 16 Uhr?" sendet
Then sieht Jens die Nachricht ohne Reload binnen 1 s mit Autor "Anna" und Uhrzeit
```
*AC-013b (FR-013)*
```gherkin
When Anna POST mit body "  Hallo  ", client_id "c-1" und einem zusätzlichen Feld created_by "jens@bloopark.de" sendet
Then antwortet der Server 201, der gespeicherte body ist "Hallo" und der Autor ist "anna@bloopark.de"
```
*AC-014a (FR-014)*
```gherkin
When Anna eine Nachricht in "#billing" speichert
Then erhält jede WS-Verbindung von Jens und Bob einen Frame {type:"chat", kind:"message.created"} ohne Feld body
```
*AC-014b (FR-014)*
```gherkin
Given Carla ist nicht Mitglied von "#billing"
When Anna in "#billing" postet
Then erhält Carla keinen Frame
```
*AC-015a (FR-015)*
```gherkin
When Anna body "   " sendet
Then antwortet der Server 422 und es wird nichts gespeichert
```
*AC-015b (FR-015)*
```gherkin
When Anna einen body mit 4001 Zeichen oder "a\u0000b" sendet
Then antwortet der Server 422 und es wird nichts gespeichert
```
*AC-015c (FR-015)*
```gherkin
When Anna einen body mit genau 4000 Zeichen sendet
Then antwortet der Server 201
```
*AC-016a (FR-016)*
```gherkin
Given Carla ist nicht Mitglied von "#billing"
When Carla dort "Hi" postet
Then antwortet der Server 403 mit code "not_a_member" und speichert nichts
```
*AC-016b (FR-016)*
```gherkin
Given Bob ist nicht Teilnehmer der DM Anna–Jens
When Bob in diese DM postet
Then antwortet der Server 404
```
*AC-019a (FR-019)*
```gherkin
Given "#general" hat 120 Top-Level-Nachrichten
When Anna GET .../messages?tail=1 aufruft
Then erhält sie die neuesten 50 aufsteigend sortiert mit has_older true
```
*AC-019b (FR-019)*
```gherkin
When Anna before und after zusammen oder limit 500 sendet
Then antwortet der Server 422
```
*AC-021a (FR-021) – E2E*
```gherkin
When Anna zweimal eine DM mit Jens öffnet
Then erhält sie beide Male dieselbe Channel-ID und Jens sieht die DM in "Direktnachrichten" nach Annas erster Nachricht
```
*AC-021b (FR-021)*
```gherkin
When Anna POST /v1/chat/dms mit 9 anderen Principals sendet
Then antwortet der Server 422
```
*AC-021c (FR-021)*
```gherkin
When Anna POST /v1/chat/dms mit [grok-jens] (Owner Jens nicht enthalten) sendet
Then antwortet der Server 403 mit code "agent_owner_required" und es entsteht keine DM
```
*AC-022a (FR-022)*
```gherkin
Given die DM Anna–Jens existiert
When Bob GET .../messages dieser DM aufruft
Then antwortet der Server 404
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: `tests/server/test_chat_routes.py` (je AC ein Test, Header-Identitäten Anna/Jens/Bob)
- Add tests: `tests/server/test_chat_realtime.py` (Frame ohne Body; Marker-Body taucht in keinem Frame auf)
- Run: `uv run --no-sync pytest tests/server/test_chat_routes.py tests/server/test_chat_realtime.py -q` → grün
- Run: `uv run --no-sync python scripts/dump_openapi.py` → grün
- Run: `uv run --no-sync pytest tests/server/test_openapi_drift.py -q` → grün
- Run: `just lint` → grün
- Run: `just typecheck-python` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] `openapi.json` regeneriert, Drift-Test grün
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-006: Web: Chat-Shell, Routen, Datenlayer (TanStack Query + zustand), WS-Frames und Reconnect-Backfill
**Release:** R0   **Phase / wave:** Story ST-1.1.1 / 3   **Parallel:** [P] ja   **Depends on:** T-001, T-004, #16 React 19 gemergt   **Rolle:** coder_grok · ux · reviewer   **Aufwand:** 8 h
**Implements:** FR-001, FR-082, NFR-018, NFR-021; ACs: AC-001a, AC-001b, AC-082a, AC-082b
**Labels:** `spec:omnigent-chat`, `release:R0`, `type:feature`, `agent-ready`

**Context**
Neue Seite `/chat` mit Sidebar (Channels, DMs). Läuft auf React 19 (Issue #16, Entscheidung Jens 16.10.); im PR kurz bestätigen, dass Tiptap 3, Radix und TanStack Query unter React 19 laufen (`pnpm --filter web run test` grün). Datenzugriff gegen den API-Vertrag aus 05 §API (bis T-003 gemergt ist mit Vitest-Mocks). Frames kommen über den bestehenden Socket (`web/src/lib/sessionUpdatesSocket.ts`, Frame-Union erweitern; `web/src/hooks/SessionUpdatesProvider.tsx`). Nach Reconnect `GET /v1/chat/changes` und Cache-Merge ohne Duplikate.

**Requirements (zitiert)**
- FR-001 (Must, R0): Where `team_chat` is enabled, the Web-App shall provide the routes `/chat`, `/chat/:channelId` and `/chat/:channelId/t/:threadRootId` with a sidebar listing joined Channels and DMs, and an entry „Chat“ in the main sidebar.
- FR-082 (Must, R0): When the WS connection reconnects, the Web-App shall call `GET /v1/chat/changes?after=<changes_cursor>` and merge returned messages, thread and read updates deduplicated by id before applying new frames.

**Scope**
- `web/src/chat/ChatPage.tsx`, `ChatSidebar.tsx`, `chatApi.ts`, `teamChatStore.ts`, `useChatFrames.ts` (new)
- `web/src/App.tsx` (adapt: Routen `/chat`, `/chat/:channelId`, `/chat/:channelId/t/:threadRootId`)
- `web/src/shell/Sidebar.tsx` (adapt: Eintrag „Chat“ nur bei Flag)
- `web/src/lib/sessionUpdatesSocket.ts`, `web/src/hooks/SessionUpdatesProvider.tsx` (adapt)
- `web/src/chat/*.test.ts(x)` (new)
- Out of scope / nicht anfassen: Keine Nachrichtenliste/Composer (T-007); das React-19-Upgrade selbst ist NICHT Teil dieses Tasks (Issue #16 muss vorher gemergt sein; ist es das nicht, Task nicht starten und melden); `motion` noch nicht (T-117).

**Acceptance criteria**
*AC-001a (FR-001) – E2E*
```gherkin
Given team_chat ist aktiv und Anna ist angemeldet
When Anna /chat öffnet
Then sieht sie in der Chat-Sidebar "#general" unter "Channels" und einen leeren Abschnitt "Direktnachrichten"
```
*AC-001b (FR-001, FR-003) – E2E*
```gherkin
Given team_chat ist nicht aktiv
When Anna /chat öffnet
Then zeigt die Web-App "Chat ist auf diesem Server nicht aktiviert" und die Hauptsidebar enthält keinen Eintrag "Chat"
```
*AC-082a (FR-082) – E2E*
```gherkin
Given Jens' Browser ist 30 s offline (Playwright context.set_offline) und Anna postet 3 Nachrichten
When die Verbindung zurückkehrt
Then sieht Jens binnen 3 s alle 3 Nachrichten genau einmal in richtiger Reihenfolge
```
*AC-082b (FR-082)*
```gherkin
Given der Backfill liefert eine Nachricht, die per Frame bereits angezeigt wurde
Then erscheint sie genau einmal
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: Vitest: Frame-Parsing, Backfill-Merge dedupliziert, Routen-Guard bei Flag aus
- Run: `pnpm --filter web run test` → grün
- Run: `pnpm --filter web run lint` → grün
- Run: `pnpm --filter web run type-check` → grün
- Run: `uv run --no-sync pytest tests/e2e_ui/team_chat -v --tracing=retain-on-failure -k smoke` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] UX-Review-Notiz gegen `docs/ux/chat-review-checklist.md` im PR; E2E für die Story existiert oder ist in T-009/T-120/T-121 eingeplant
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-005: Agent-Thread-Orchestrator: Session als Aufrufer starten, READ-Grants, Kontext, Spiegeln, Start-Timeout
**Release:** R0   **Phase / wave:** Story ST-3.1.1/3.2.1 / 4   **Parallel:** [P] ja   **Depends on:** T-003   **Rolle:** coder_grok (modulübergreifend) · reviewer   **Aufwand:** 10 h
**Implements:** FR-033, FR-034, FR-035, FR-037, FR-038, FR-046, FR-087, NFR-004, NFR-020, NFR-028; ACs: AC-033a, AC-033b, AC-034a, AC-034b, AC-035a, AC-035b, AC-037a, AC-038a, AC-046a, AC-046b, AC-087a
**Labels:** `spec:omnigent-chat`, `release:R0`, `type:feature`, `agent-ready`

**Context**
Kern von G-2 (ADR-0005). Bei `<@omnigent_agent:agent>` von einem Menschen: Projekt nach A-8 auflösen (Channel-`project_name` → Projekt des Aufrufers, sonst `chat_user_prefs.default_project_id`), `resolve_project_session_create` (`omnigent/server/routes/_session_create_validation.py:39`) + `_create_session_from_existing_agent` (`omnigent/server/routes/_sessions/orchestration.py:10365`) mit `user_id=Aufrufer`; `host_id` nur wenn in der Projekt-Config gesetzt. Label `omnigent.chat.v1`. READ-Grants über den Permission-Store, SharingMode beachten. Hook in `_publish_status` (`omnigent/server/routes/_sessions/helpers.py`, dort hängt PR #15 seinen Hook an) → bei `idle` letzten Assistant-Text via `summary_lookup` (`omnigent/server/manager_webhook_context.py`, PR #15) als Thread-Nachricht spiegeln (max 32 000 Zeichen, idempotent über `session_ref`/`response_ref`). Threads in `starting` > 120 s → `start_failed/timeout`.

**Requirements (zitiert)**
- FR-033 (Must, R0): When a human posts a message containing an `<@omnigent_agent:HANDLE>` token in a Channel or DM (top-level or in a Thread without active agent), the Server shall create a session as the caller via the existing create path, send the Thread context as first `message` event, and link the Thread (`chat_threads.session_id`, `agent_state=active`).
- FR-034 (Must, R0): When an Agent-Thread session is created, the Server shall grant READ on it to every other human member of the Channel; if the sharing mode forbids new grants, then the Server shall skip the grants and post the system line „Live-Ansicht nur für {Aufrufer} (Freigabe am Server deaktiviert)“.
- FR-035 (Must, R0): If session creation or the first message fails, then the Server shall set `agent_state=start_failed` and post a system line naming the reason class (`no_project`, `host_offline`, `agent_not_found`, `forbidden`, `timeout`, `internal`) without stack traces.
- FR-037 (Must, R0): The Server shall build the first agent message from the triggering message and up to 20 preceding Thread messages (max 12 000 characters, newest kept), each prefixed with the author's display name.
- FR-038 (Must, R0): The Server shall resolve the handle `agent` to the `agent_id` of the caller's resolved project (A-8).
- FR-046 (Must, R0): When an Agent-Thread session reaches `idle` after a turn, the Server shall store the turn's final assistant text (max 32 000 characters, else truncated with „… vollständig in der Session“) as a Thread reply with `author_kind=omnigent_agent`.
- FR-087 (Must, R0): If a Thread stays in `agent_state=starting` for more than 120 s (z. B. Server-Neustart während des Starts), then the Server shall set `start_failed` with reason `timeout` and post the system line.

**Scope**
- `omnigent/server/chat_agent_threads.py` (new)
- `omnigent/server/routes/_sessions/helpers.py` (adapt: ein Aufruf in `_publish_status`)
- `omnigent/server/routes/chat.py` (adapt: Hook nach Post)
- `tests/server/test_chat_agent_threads.py` (new, mit Mock-LLM-Runner aus `tests/server/integration/`)
- Out of scope / nicht anfassen: Keine Weiterleitung von Thread-Antworten (T-104); keine Handles außer `agent` (T-103); keine Bots (T-109).

**Acceptance criteria**
*AC-033a (FR-033, FR-038) – E2E*
```gherkin
Given Anna ist in "#billing"
When sie "<@omnigent_agent:agent> bitte Tests für billing fixen" sendet
Then entsteht ein Thread an der Nachricht mit Chip "startet…" oder "arbeitet", die Session gehört Anna, liegt im Projekt "billing" mit agent_id "coder" und erhält als erste Nachricht den Text mit Präfix "[Anna]"
```
*AC-033b (FR-033)*
```gherkin
Given der Thread von m1 hat bereits agent_state "active"
When Anna darin erneut den Agenten erwähnt
Then wird keine zweite Session angelegt
```
*AC-034a (FR-034)*
```gherkin
When Anna in "#billing" einen Agent-Thread startet
Then haben Jens und Bob READ (1) auf der Session und Anna OWNER (4)
```
*AC-034b (FR-034)*
```gherkin
Given OMNIGENT_SHARING_MODE=off
When Anna einen Agent-Thread startet
Then werden keine Grants angelegt und im Thread steht "Live-Ansicht nur für Anna (Freigabe am Server deaktiviert)"
```
*AC-035a (FR-035)*
```gherkin
Given Anna hat kein Projekt "billing" und kein Chat-Standardprojekt
When sie in "#billing" den Agenten erwähnt
Then wird keine Session angelegt, agent_state ist "start_failed" und der Thread zeigt "Kein Projekt für Agenten-Start – Standard in Einstellungen setzen"
```
*AC-035b (FR-035)*
```gherkin
Given Annas Host h-anna ist offline
When sie den Agenten erwähnt
Then zeigt der Thread eine Systemzeile mit Grund "host_offline" und keinen Stacktrace
```
*AC-037a (FR-037)*
```gherkin
Given der Thread von m1 hat 25 Nachrichten
When Anna darin den Agenten erwähnt
Then enthält die erste Session-Nachricht höchstens die 20 vorhergehenden Nachrichten plus die auslösende, höchstens 12000 Zeichen, jeweils mit "[Name]"
```
*AC-038a (FR-038)*
```gherkin
Given Annas Projekt "billing" hat keinen agent_id
When Anna "@agent" erwähnt
Then zeigt der Thread den Grund "agent_not_found" und es entsteht keine Session
```
*AC-046a (FR-046) – E2E*
```gherkin
When der Turn mit Assistant-Text "Tests grün, PR #42 offen" idle wird
Then enthält der Thread eine Nachricht mit author_kind "omnigent_agent" und genau diesem Text, auch nach Reload
```
*AC-046b (FR-046)*
```gherkin
Given der finale Assistant-Text hat 50000 Zeichen
Then enthält die gespiegelte Nachricht 32000 Zeichen gefolgt von "… vollständig in der Session"
```
*AC-087a (FR-087)*
```gherkin
Given ein Thread steht seit 121 s auf agent_state "starting"
When der Server den Thread liest oder der periodische Abgleich läuft
Then ist agent_state "start_failed" und die Systemzeile nennt Grund "timeout"
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: `tests/server/test_chat_agent_threads.py`: Start, Grants, Sharing off, jede Fehlerklasse, Kontextgrenzen, Spiegeln einmalig, Timeout
- Run: `uv run --no-sync pytest tests/server/test_chat_agent_threads.py -q` → grün
- Run: `uv run --no-sync pytest tests/server -q -k "publish_status or manager_webhook"` → grün
- Run: `just lint` → grün
- Run: `just typecheck-python` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-007: Web: Channel-Ansicht, Nachrichtenliste, Composer-Karte, Thread-Pane, DMs, Sende-Retry
**Release:** R0   **Phase / wave:** Story ST-2.1.1/2.1.2/2.2.1 / 4   **Parallel:** [P] ja   **Depends on:** T-003, T-006, #16 React 19 gemergt   **Rolle:** coder_grok · ux · reviewer   **Aufwand:** 10 h
**Implements:** FR-020, FR-084, NFR-002, NFR-009, NFR-010, NFR-011, NFR-015, NFR-030; ACs: AC-013a, AC-014a, AC-015a, AC-019a, AC-020a, AC-021a, AC-023a, AC-084a
**Labels:** `spec:omnigent-chat`, `release:R0`, `type:feature`, `agent-ready`

**Context**
UI für Schreiben/Lesen. Composer-Optik und Sendetaste wie Agent-Composer (`web/src/components/composer/ChatComposer.tsx`, `web/src/lib/composerSendShortcutPreferences.ts:isComposerSendKey`), löst damit Issue #7 ab. Menschen-Text nur als Textknoten; Agent-/Bot-Markdown nur über `web/src/components/ai-elements/` mit `streamdown-security.ts`. Optimistische Anzeige mit `client_id`, bei Fehler „nicht gesendet“ + Erneut senden. Thread-Pane rechts (Slack-Muster nachgebaut, kein Code kopiert). Leerer Channel „Noch keine Nachrichten“.

**Requirements (zitiert)**
- FR-020 (Must, R0): The Web-App shall render bodies of human messages as text nodes, turn `http(s)` URLs into links with `rel="noopener noreferrer"`, and render mention tokens as name chips; it shall not interpret HTML or Markdown in human messages.
- FR-084 (Must, R0): If sending fails (network, 429, 5xx), then the Web-App shall keep the message as „nicht gesendet“ with „erneut senden“ reusing the same `client_id`, and keep the composer usable.

**Scope**
- `web/src/chat/ChannelView.tsx`, `MessageList.tsx`, `MessageItem.tsx`, `ThreadPane.tsx`, `ChatComposerCard.tsx`, `NewDmDialog.tsx` (new)
- `web/src/chat/*.test.tsx` (new)
- Out of scope / nicht anfassen: Keine Animationen (T-117), kein Autocomplete (T-110), keine Agent-Ansicht (T-008).

**Acceptance criteria**
*AC-013a (FR-013, FR-014) – E2E*
```gherkin
Given Anna und Jens haben "#general" offen
When Anna "Deploy heute 16 Uhr?" sendet
Then sieht Jens die Nachricht ohne Reload binnen 1 s mit Autor "Anna" und Uhrzeit
```
*AC-014a (FR-014)*
```gherkin
When Anna eine Nachricht in "#billing" speichert
Then erhält jede WS-Verbindung von Jens und Bob einen Frame {type:"chat", kind:"message.created"} ohne Feld body
```
*AC-015a (FR-015)*
```gherkin
When Anna body "   " sendet
Then antwortet der Server 422 und es wird nichts gespeichert
```
*AC-019a (FR-019)*
```gherkin
Given "#general" hat 120 Top-Level-Nachrichten
When Anna GET .../messages?tail=1 aufruft
Then erhält sie die neuesten 50 aufsteigend sortiert mit has_older true
```
*AC-020a (FR-020) – E2E*
```gherkin
When Anna "<script>alert(1)</script> **fett** https://example.com" sendet
Then zeigt Jens' Ansicht "<script>alert(1)</script> **fett**" als Text und einen Link auf https://example.com, und es wird kein Script ausgeführt
```
*AC-021a (FR-021) – E2E*
```gherkin
When Anna zweimal eine DM mit Jens öffnet
Then erhält sie beide Male dieselbe Channel-ID und Jens sieht die DM in "Direktnachrichten" nach Annas erster Nachricht
```
*AC-023a (FR-023) – E2E*
```gherkin
Given Annas Top-Level-Nachricht m1 in "#general"
When Jens im Thread von m1 "Passt" antwortet
Then zeigt m1 bei Anna "1 Antwort" und der Thread zeigt "Passt"
```
*AC-084a (FR-084) – E2E*
```gherkin
Given der Server antwortet auf POST .../messages mit 503 (Playwright route)
When Anna "Hallo" sendet
Then zeigt die Nachricht "nicht gesendet" mit "erneut senden", und nach Erholung erzeugt ein Klick genau eine gespeicherte Nachricht
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: Vitest: XSS-Fälle (`<script>`, `javascript:`-Link), Retry mit gleicher `client_id`, Tastatur Enter/Shift+Enter
- Run: `pnpm --filter web run test` → grün
- Run: `pnpm --filter web run lint` → grün
- Run: `pnpm --filter web run type-check` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] UX-Review-Notiz gegen `docs/ux/chat-review-checklist.md` im PR; E2E für die Story existiert oder ist in T-009/T-120/T-121 eingeplant
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-008: Web: Agent-Thread-Ansicht mit Status-Chip, Live-Streaming und Freigabe-Karten
**Release:** R0   **Phase / wave:** Story ST-3.2.1/4.1.1 / 5   **Parallel:** nein   **Depends on:** T-005, T-007, #16 React 19 gemergt   **Rolle:** coder_grok · ux · reviewer   **Aufwand:** 10 h
**Implements:** FR-044, FR-045, FR-047, FR-055, FR-056, FR-057, FR-058, NFR-005, NFR-006; ACs: AC-044a, AC-044b, AC-045a, AC-045b, AC-047a, AC-047b, AC-055a, AC-055b, AC-056a, AC-056b, AC-057a, AC-058a
**Labels:** `spec:omnigent-chat`, `release:R0`, `type:feature`, `agent-ready`

**Context**
Im offenen Thread: Status-Chip aus Session-Status (`SessionStatusEvent` idle/launching/running/waiting/failed), Streaming über die bestehende Konversations-Registry (`web/src/store/conversationRegistry.ts`, nur für den offenen Thread wegen HTTP/1.1-Limit, R-6). In der Channel-Liste Chip über den WS-Watch (`SessionListItem.status`, Watch-Set in `SessionUpdatesProvider.tsx` um Thread-Sessions erweitern). Freigaben mit `ApprovalCard`/`ElicitationCard` (`web/src/components/blocks/ApprovalCard.tsx`) und `AskUserQuestionForm.tsx`; aktiv nur ab EDIT (`ELICITATION_RESOLVE_LEVEL`, `routes_elicitations.py:60`).

**Requirements (zitiert)**
- FR-044 (Must, R0): While an Agent-Thread is open, the Web-App shall show the session status chip (`launching`→„startet…“, `running`→„arbeitet“, `waiting`→„braucht Antwort“, `idle`→„fertig“, `failed`→„Fehler“, dismissed→„entlassen“) updated from the session stream.
- FR-045 (Must, R0): While an Agent-Thread is open and the viewer has READ on the session, the Web-App shall render the session's assistant output streaming via the existing conversation registry (`web/src/store/conversationRegistry.ts`) and AI-Elements `message`/`reasoning`.
- FR-047 (Must, R0): The Web-App shall show a compact status chip on every Agent-Thread root in the Channel view, fed by the WS watch-set of `/v1/sessions/updates` (`SessionListItem.status`, `pending_elicitations_count`).
- FR-055 (Must, R0): When an Agent-Thread session emits `response.elicitation_request`, the open Thread shall render the matching card (`ApprovalCard`/`ElicitationCard`, `AskUserQuestionForm`) inline at the position of the request.
- FR-056 (Must, R0): The card shall be actionable only for viewers with level ≥ EDIT (`ELICITATION_RESOLVE_LEVEL`), and read-only with „Nur Aufrufer und Bearbeiter können antworten“ for others.
- FR-057 (Must, R0): When an elicitation is resolved anywhere (Thread, `InboxPage`, `ApprovePage`, MCP), the Thread card shall switch to the resolved state with verdict and resolver within 2 s.
- FR-058 (Must, R0): If resolving returns 403 or reports the elicitation already resolved, then the card shall show the server's verdict and no error toast.

**Scope**
- `web/src/chat/agent/AgentThreadView.tsx`, `AgentStatusChip.tsx` (new)
- `web/src/hooks/SessionUpdatesProvider.tsx` (adapt: Watch-Set)
- `web/src/components/blocks/ApprovalCard.tsx` (adapt nur falls Props fehlen)
- `web/src/chat/agent/*.test.tsx` (new)
- Out of scope / nicht anfassen: Keine Steuerung/Slash (T-114), keine Schritte/Plan/PR (T-115).

**Acceptance criteria**
*AC-044a (FR-044) – E2E*
```gherkin
Given Anna hat den Agent-Thread offen
When die Session nach waiting wechselt
Then zeigt der Chip binnen 1 s "braucht Antwort"
```
*AC-044b (FR-044) – E2E*
```gherkin
Given das Mock-LLM liefert einen Fehler
When Anna den Agent-Thread offen hat
Then zeigt der Chip "Fehler" und der Thread eine Systemzeile ohne Rohtext des Fehlers
```
*AC-045a (FR-045) – E2E*
```gherkin
Given das Mock-LLM streamt "Tests sind grün" in 5 Teilen
When Jens (READ) den Thread öffnet
Then sieht er den Text fortlaufend erscheinen, ohne Reload
```
*AC-045b (FR-045)*
```gherkin
Given Bob hat keinen Grant (Sharing-Modus off)
When Bob den Thread öffnet
Then sieht er statt der Live-Ansicht den Hinweis "Live-Ansicht nur für Anna" und die gespiegelten Antworten
```
*AC-047a (FR-047) – E2E*
```gherkin
Given Jens sieht "#billing" ohne offenen Thread
When Annas Agent-Session von running nach waiting wechselt
Then zeigt der Chip an der Thread-Wurzel binnen 5 s "braucht Antwort"
```
*AC-047b (FR-047)*
```gherkin
Given Jens hat keinen Grant auf die Agent-Thread-Session (Sharing-Modus off)
Then zeigt die Thread-Wurzel bei Jens den letzten bekannten Thread-Zustand aus chat_threads statt eines Session-Status
```
*AC-055a (FR-055) – E2E*
```gherkin
Given das Mock-LLM fordert eine Freigabe "Migration anwenden?" an
When Anna den Thread offen hat
Then erscheint im Thread eine Freigabe-Karte mit "Genehmigen" und "Ablehnen"
```
*AC-055b (FR-055)*
```gherkin
Given die Freigabe wurde angefordert, während Anna den Thread geschlossen hatte
When Anna den Thread später öffnet
Then erscheint die offene Freigabe-Karte an ihrer Position
```
*AC-056a (FR-056) – E2E*
```gherkin
When Anna "Genehmigen" klickt
Then antwortet resolve 202 und die Karte zeigt "Genehmigt von Anna"
```
*AC-056b (FR-056) – E2E*
```gherkin
Given Bob hat READ
Then zeigt Bobs Karte keine Buttons und den Text "Nur Aufrufer und Bearbeiter können antworten"
```
*AC-057a (FR-057) – E2E*
```gherkin
Given Annas Thread zeigt eine offene Freigabe
When Jens (EDIT) sie in der InboxPage genehmigt
Then zeigt Annas Karte binnen 2 s "Genehmigt von Jens"
```
*AC-058a (FR-058)*
```gherkin
Given die Freigabe ist bereits beantwortet und Annas Karte ist veraltet
When Anna "Ablehnen" klickt
Then zeigt die Karte das gespeicherte Ergebnis "Genehmigt von Jens" ohne Fehler-Toast
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: Vitest: Status-Mapping-Tabelle (03), Karte read-only für READ, 409 → Serverstand
- Run: `pnpm --filter web run test` → grün
- Run: `pnpm --filter web run lint` → grün
- Run: `pnpm --filter web run type-check` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] UX-Review-Notiz gegen `docs/ux/chat-review-checklist.md` im PR; E2E für die Story existiert oder ist in T-009/T-120/T-121 eingeplant
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-009: R0-Playwright-Suite + Tester-Gate für die Demo am Mi 21.10.
**Release:** R0   **Phase / wave:** Polish / Gate / 6   **Parallel:** nein   **Depends on:** T-008   **Rolle:** test_author · tester (Gate) · reviewer   **Aufwand:** 8 h
**Implements:** NFR-023, NFR-024, NFR-009; ACs: AC-001a, AC-001b, AC-013a, AC-020a, AC-021a, AC-023a, AC-033a, AC-044a, AC-044b, AC-045a, AC-046a, AC-047a, AC-055a, AC-056a, AC-056b, AC-057a, AC-082a, AC-084a
**Labels:** `spec:omnigent-chat`, `release:R0`, `type:test`, `agent-ready`

**Context**
Jede R0-UI-Story bekommt mindestens einen E2E-Test (NFR-023). Tests nutzen `team_chat_server` aus T-004, zwei bis drei Browser-Kontexte (Anna, Jens, Bob), Mock-LLM mit Elicitation-Szenario. Der Tester führt die Suite 3× hintereinander aus (0 Flakes, NFR-024) und gibt das Demo-Go.

**Scope**
- `tests/e2e_ui/team_chat/test_chat_shell.py`, `test_channel_messaging.py`, `test_dm.py`, `test_threads.py`, `test_agent_thread.py`, `test_agent_thread_approval.py`, `test_reconnect_backfill.py` (new)
- Out of scope / nicht anfassen: Keine Produktänderungen; gefundene Fehler als Befund an den zuständigen Task zurück.

**Acceptance criteria**
*AC-001a (FR-001) – E2E*
```gherkin
Given team_chat ist aktiv und Anna ist angemeldet
When Anna /chat öffnet
Then sieht sie in der Chat-Sidebar "#general" unter "Channels" und einen leeren Abschnitt "Direktnachrichten"
```
*AC-001b (FR-001, FR-003) – E2E*
```gherkin
Given team_chat ist nicht aktiv
When Anna /chat öffnet
Then zeigt die Web-App "Chat ist auf diesem Server nicht aktiviert" und die Hauptsidebar enthält keinen Eintrag "Chat"
```
*AC-013a (FR-013, FR-014) – E2E*
```gherkin
Given Anna und Jens haben "#general" offen
When Anna "Deploy heute 16 Uhr?" sendet
Then sieht Jens die Nachricht ohne Reload binnen 1 s mit Autor "Anna" und Uhrzeit
```
*AC-020a (FR-020) – E2E*
```gherkin
When Anna "<script>alert(1)</script> **fett** https://example.com" sendet
Then zeigt Jens' Ansicht "<script>alert(1)</script> **fett**" als Text und einen Link auf https://example.com, und es wird kein Script ausgeführt
```
*AC-021a (FR-021) – E2E*
```gherkin
When Anna zweimal eine DM mit Jens öffnet
Then erhält sie beide Male dieselbe Channel-ID und Jens sieht die DM in "Direktnachrichten" nach Annas erster Nachricht
```
*AC-023a (FR-023) – E2E*
```gherkin
Given Annas Top-Level-Nachricht m1 in "#general"
When Jens im Thread von m1 "Passt" antwortet
Then zeigt m1 bei Anna "1 Antwort" und der Thread zeigt "Passt"
```
*AC-033a (FR-033, FR-038) – E2E*
```gherkin
Given Anna ist in "#billing"
When sie "<@omnigent_agent:agent> bitte Tests für billing fixen" sendet
Then entsteht ein Thread an der Nachricht mit Chip "startet…" oder "arbeitet", die Session gehört Anna, liegt im Projekt "billing" mit agent_id "coder" und erhält als erste Nachricht den Text mit Präfix "[Anna]"
```
*AC-044a (FR-044) – E2E*
```gherkin
Given Anna hat den Agent-Thread offen
When die Session nach waiting wechselt
Then zeigt der Chip binnen 1 s "braucht Antwort"
```
*AC-044b (FR-044) – E2E*
```gherkin
Given das Mock-LLM liefert einen Fehler
When Anna den Agent-Thread offen hat
Then zeigt der Chip "Fehler" und der Thread eine Systemzeile ohne Rohtext des Fehlers
```
*AC-045a (FR-045) – E2E*
```gherkin
Given das Mock-LLM streamt "Tests sind grün" in 5 Teilen
When Jens (READ) den Thread öffnet
Then sieht er den Text fortlaufend erscheinen, ohne Reload
```
*AC-046a (FR-046) – E2E*
```gherkin
When der Turn mit Assistant-Text "Tests grün, PR #42 offen" idle wird
Then enthält der Thread eine Nachricht mit author_kind "omnigent_agent" und genau diesem Text, auch nach Reload
```
*AC-047a (FR-047) – E2E*
```gherkin
Given Jens sieht "#billing" ohne offenen Thread
When Annas Agent-Session von running nach waiting wechselt
Then zeigt der Chip an der Thread-Wurzel binnen 5 s "braucht Antwort"
```
*AC-055a (FR-055) – E2E*
```gherkin
Given das Mock-LLM fordert eine Freigabe "Migration anwenden?" an
When Anna den Thread offen hat
Then erscheint im Thread eine Freigabe-Karte mit "Genehmigen" und "Ablehnen"
```
*AC-056a (FR-056) – E2E*
```gherkin
When Anna "Genehmigen" klickt
Then antwortet resolve 202 und die Karte zeigt "Genehmigt von Anna"
```
*AC-056b (FR-056) – E2E*
```gherkin
Given Bob hat READ
Then zeigt Bobs Karte keine Buttons und den Text "Nur Aufrufer und Bearbeiter können antworten"
```
*AC-057a (FR-057) – E2E*
```gherkin
Given Annas Thread zeigt eine offene Freigabe
When Jens (EDIT) sie in der InboxPage genehmigt
Then zeigt Annas Karte binnen 2 s "Genehmigt von Jens"
```
*AC-082a (FR-082) – E2E*
```gherkin
Given Jens' Browser ist 30 s offline (Playwright context.set_offline) und Anna postet 3 Nachrichten
When die Verbindung zurückkehrt
Then sieht Jens binnen 3 s alle 3 Nachrichten genau einmal in richtiger Reihenfolge
```
*AC-084a (FR-084) – E2E*
```gherkin
Given der Server antwortet auf POST .../messages mit 503 (Playwright route)
When Anna "Hallo" sendet
Then zeigt die Nachricht "nicht gesendet" mit "erneut senden", und nach Erholung erzeugt ein Klick genau eine gespeicherte Nachricht
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: siehe Dateien; jede Testfunktion nennt die AC-ID im Namen, z. B. `test_ac_033a_agent_thread_starts`
- Run: `for i in 1 2 3; do uv run --no-sync pytest tests/e2e_ui/team_chat -v --tracing=retain-on-failure || exit 1; done` → grün
- Run: `uv run --no-sync pytest -q` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

## Tasks R1 – Launch (Sa/So 24./25.10.2026)

### T-101: Lesestand und Ungelesen-Zähler serverseitig (`chat_reads`, `read.updated`)
**Release:** R1   **Phase / wave:** Story ST-1.2.1 / 7   **Parallel:** [P] ja   **Depends on:** T-009   **Rolle:** coder_deepseek · reviewer   **Aufwand:** 5 h
**Implements:** FR-007, FR-008, FR-009; ACs: AC-007a, AC-007b, AC-007c, AC-008a, AC-008b, AC-009a, AC-009b
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:feature`, `agent-ready`

**Context**
Ersetzt Issue #8. Cursor nur vorwärts, je Channel und je abonniertem Thread; Zähler im Bootstrap und `GET /v1/chat/channels`; Frame `read.updated` an alle Verbindungen desselben Principals (Geräte-Sync).

**Requirements (zitiert)**
- FR-007 (Must, R1): When a principal calls `POST /v1/chat/channels/{id}/read` with `{created_at, id[, thread_root_id]}` naming an existing message of that Channel (or Thread), the Server shall move the principal's read cursor forward and ignore a cursor that is not newer.
- FR-008 (Must, R1): The Server shall return per joined Channel `unread_count` and `mention_count` (messages after the caller's cursor, excluding the caller's own) and per followed Thread `unread_count` in `GET /v1/chat/bootstrap` and `GET /v1/chat/channels`.
- FR-009 (Must, R1): When a read cursor moves forward, the Server shall push a `chat` frame `read.updated` to every WS connection of the same principal.

**Scope**
- `omnigent/stores/chat_store/sqlalchemy_store.py` (adapt)
- `omnigent/server/routes/chat.py` (adapt: `POST …/read`)
- `tests/server/test_chat_reads.py` (new)
- Out of scope / nicht anfassen: Keine UI (T-111).

**Acceptance criteria**
*AC-007a (FR-007)*
```gherkin
Given Annas Cursor in "#billing" steht auf m10 und m11, m12 existieren
When Anna POST .../read mit dem Cursor von m12 sendet
Then steht ihr Cursor auf m12
```
*AC-007b (FR-007)*
```gherkin
Given Annas Cursor steht auf m12
When ein zweiter Tab POST .../read mit dem Cursor von m11 sendet
Then bleibt der Cursor auf m12 und der Server antwortet 204
```
*AC-007c (FR-007)*
```gherkin
When Anna POST .../read mit einer id sendet, die in "#billing" nicht existiert
Then antwortet der Server 422 und der Cursor bleibt unverändert
```
*AC-008a (FR-008)*
```gherkin
Given Annas Cursor steht auf m10, danach folgen m11 von Jens mit "<@user:anna@bloopark.de>" und m12 von Anna
When Anna GET /v1/chat/bootstrap aufruft
Then ist für "#billing" unread_count 1 und mention_count 1
```
*AC-008b (FR-008)*
```gherkin
Given "#general" hat 0 Nachrichten und Anna hat nie gelesen
Then ist für "#general" unread_count 0 und mention_count 0
```
*AC-009a (FR-009) – E2E*
```gherkin
Given Anna hat /chat in zwei Browser-Kontexten offen und "#billing" zeigt in beiden Badge "2"
When sie "#billing" in Kontext A öffnet
Then zeigt Kontext B binnen 1 s kein Badge mehr für "#billing"
```
*AC-009b (FR-009)*
```gherkin
Given Jens hat eine WS-Verbindung offen
When Anna ihren Cursor bewegt
Then erhält Jens keinen Frame read.updated
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: `tests/server/test_chat_reads.py`
- Run: `uv run --no-sync pytest tests/server/test_chat_reads.py -q` → grün
- Run: `uv run --no-sync python scripts/dump_openapi.py` → grün
- Run: `uv run --no-sync pytest tests/server/test_openapi_drift.py -q` → grün
- Run: `just lint` → grün
- Run: `just typecheck-python` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] `openapi.json` regeneriert, Drift-Test grün
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-102: Session-Chat als Channel `kind=session`: Migration von `session_chat_messages`, Adapter der PR-#4-Routen
**Release:** R1   **Phase / wave:** Story ST-2.1.4 / 7   **Parallel:** [P] ja   **Depends on:** T-009   **Rolle:** coder_deepseek · reviewer   **Aufwand:** 6 h
**Implements:** FR-029, FR-030, FR-031, NFR-022; ACs: AC-029a, AC-029b, AC-029c, AC-030a, AC-031a, AC-031b
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:feature`, `agent-ready`

**Context**
ADR-0002. Jede Root-Session mit direkten Grants für ≥ 2 Principals (ohne `__public__`, ohne Label `omnigent.chat.v1`) bekommt einen Session-Chat. Zeilen aus `session_chat_messages` werden mit gleicher ID nach `chat_messages` kopiert (idempotent). Die Routen `GET/POST /v1/sessions/{id}/chat` (`omnigent/server/routes/session_chat.py`) werden Adapter über den Chat-Store; Event `session.human_chat` bleibt. Die Bestandstests `tests/server/test_session_chat.py` laufen ab jetzt mit aktivem Flag (ADR-0006).

**Requirements (zitiert)**
- FR-029 (Must, R1): The Server shall treat every root session with direct grants for ≥ 2 distinct principals (ignoring `__public__`) as a Session-Chat (`kind=session`) whose members are those principals, with admins as read-only, except sessions carrying the label `omnigent.chat.v1` (Agent-Thread-Sessions, deren Gespräch der Thread selbst ist); Session-Chats erscheinen in der `/chat`-Sidebar im Abschnitt „Sessions“, sobald sie ≥ 1 Nachricht haben.
- FR-030 (Must, R1): The Server shall never turn a Session-Chat message into a conversation item or `/events` call.
- FR-031 (Must, R1): The Server shall migrate rows of `session_chat_messages` into `chat_messages` of the corresponding Session-Chat and keep `GET/POST /v1/sessions/{id}/chat` as adapters on the chat store with unchanged response shape.

**Scope**
- `omnigent/server/session_chat.py`, `omnigent/server/routes/session_chat.py` (adapt)
- `omnigent/db/migrations/versions/tc2…_migrate_session_chat.py` (new, Datenmigration)
- `tests/server/test_session_chat.py` (adapt: Flag), `tests/db/test_migration_session_chat_to_chat.py` (new)
- Out of scope / nicht anfassen: Alte Tabelle nicht löschen (R2). Keine UI (T-113).

**Acceptance criteria**
*AC-029a (FR-029) – E2E*
```gherkin
Given Jens teilt Session S1 mit Anna (EDIT)
When Anna S1 öffnet
Then ist im ChatHeader der Session-Chat erreichbar und zeigt die bisherigen Nachrichten
```
*AC-029b (FR-029)*
```gherkin
Given S1 hat nur Jens als direkten Grant und einen "__public__"-Grant
When ein Public-Link-Besucher GET /v1/sessions/S1/chat aufruft
Then antwortet der Server 403
```
*AC-029c (FR-029)*
```gherkin
Given Annas Agent-Thread-Session S7 trägt das Label omnigent.chat.v1 und hat Grants für Anna, Jens, Bob
Then existiert für S7 kein Session-Chat-Channel
```
*AC-030a (FR-030)*
```gherkin
When Anna im Session-Chat von S1 "Nur für Menschen" postet
Then entsteht kein Conversation-Item in S1 und kein /events-Aufruf
```
*AC-031a (FR-031)*
```gherkin
Given session_chat_messages enthält 3 Zeilen für Root S1
When die Migration läuft
Then enthält chat_messages 3 Zeilen im Session-Chat von S1 mit gleichen id, created_by, created_at und body
```
*AC-031b (FR-031)*
```gherkin
When GET /v1/sessions/S1/chat nach der Migration aufgerufen wird
Then entspricht die Antwortform der bisherigen und tests/server/test_session_chat.py bleibt grün
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: `tests/server/test_session_chat.py` (bestehend, grün)
- Add tests: `tests/db/test_migration_session_chat_to_chat.py`
- Run: `uv run --no-sync pytest tests/server/test_session_chat.py tests/db/test_migration_session_chat_to_chat.py tests/db/test_migration_connections.py -q` → grün
- Run: `just crdb-test` → grün
- Run: `just lint` → grün
- Run: `just typecheck-python` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-105: Agentenkonten: Anlegen, Rotieren, Widerrufen, Token-Hash und eigener Auth-Zweig
**Release:** R1   **Phase / wave:** Story ST-7.1.4 / 7   **Parallel:** [P] ja   **Depends on:** T-009   **Rolle:** coder_deepseek · reviewer (Security)   **Aufwand:** 8 h
**Implements:** FR-061, FR-063, FR-064, FR-065, FR-066, NFR-013, NFR-016, NFR-028; ACs: AC-061a, AC-061b, AC-063a, AC-064a, AC-064b, AC-065a, AC-066a, AC-066b
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:feature`, `agent-ready`

**Context**
ADR-0004. Token `ogc_` + 32 Byte, gespeichert nur als `HMAC_SHA256(OMNIGENT_CHAT_AGENT_SECRET, token)`, einmalig angezeigt. Ohne Env-Secret → 503. Neuer Zweig im Auth-Provider (`omnigent/server/auth.py`) für `Bearer ogc_…`, gültig nur für `/mcp` und `/v1/chat/*` (NICHT über `_DELEGATED_ALLOWED_PREFIXES`). Webhook-URL nur HTTPS und nicht intern (`omnigent/util/ssrf.py:host_is_internal`). Audit über `omnigent/debug_logging.py:add_audit_attrs` ohne Inhalte.

**Requirements (zitiert)**
- FR-061 (Must, R1): When a human calls `POST /v1/chat/agent-accounts` with `display_name` (1–40 chars), `handle` (`^[a-z0-9][a-z0-9-]{1,39}$`, unique among handles) and `webhook_url`, the Server shall create an Agentenkonto with principal `agent:<uuid>` owned by the caller and return a bearer token (prefix `ogc_`, ≥ 256 bit) and the webhook secret exactly once.
- FR-063 (Should, R1): If a human already owns 5 non-revoked Agentenkonten, then the Server shall reject creation with 409 `agent_account_limit`.
- FR-064 (Must, R1): When the owner or an admin calls `POST /v1/chat/agent-accounts/{id}/rotate` or `DELETE /v1/chat/agent-accounts/{id}`, the Server shall invalidate the previous token for all subsequent requests (rotate returns a new token once; delete sets `revoked_at` and removes all memberships).
- FR-065 (Must, R1): If `webhook_url` is not `https`, or its host resolves to a loopback, private, link-local or otherwise internal address, then the Server shall reject it with 422 at creation and skip delivery at send time.
- FR-066 (Must, R1): When a request carries an Agentenkonto token, the Server shall authenticate it only for `/mcp` and `/v1/chat/*` and treat every other path as unauthenticated (401).

**Scope**
- `omnigent/server/chat_agent_accounts.py` (new)
- `omnigent/server/auth.py` (adapt: Zweig)
- `omnigent/server/routes/chat.py` (adapt: `agent-accounts`)
- `tests/server/test_chat_agent_accounts.py`, `tests/server/test_chat_agent_auth.py` (new)
- Out of scope / nicht anfassen: Keine Rechte-Politik in Channels (T-106), kein MCP (T-107), keine Zustellung (T-108).

**Acceptance criteria**
*AC-061a (FR-061) – E2E*
```gherkin
When Jens in den Einstellungen ein Agentenkonto "Grok", Handle "grok-jens", Webhook "https://bot.example.com/hook" anlegt
Then werden ein Token mit Präfix "ogc_" und ein Secret genau einmal angezeigt und nach Schließen des Dialogs nicht mehr
```
*AC-061b (FR-061)*
```gherkin
Given Handle "grok-jens" existiert
When Anna ein Konto mit Handle "grok-jens" anlegt
Then antwortet der Server 409
```
*AC-063a (FR-063)*
```gherkin
Given Jens besitzt 5 aktive Agentenkonten
When er ein sechstes anlegt
Then antwortet der Server 409 mit code "agent_account_limit"
```
*AC-064a (FR-064)*
```gherkin
When Jens das Token von grok-jens rotiert
Then liefert der alte Token auf /mcp 401 und der neue Token funktioniert
```
*AC-064b (FR-064)*
```gherkin
When Anna (kein Admin) DELETE auf Jens' Agentenkonto aufruft
Then antwortet der Server 404 und das Konto bleibt aktiv
```
*AC-065a (FR-065)*
```gherkin
When Jens Webhook "http://bot.example.com/hook" oder "https://10.0.0.5/hook" angibt
Then antwortet der Server 422
```
*AC-066a (FR-066)*
```gherkin
When grok-jens GET /v1/sessions mit seinem Token aufruft
Then antwortet der Server 401
```
*AC-066b (FR-066)*
```gherkin
When grok-jens GET /v1/chat/channels aufruft
Then antwortet der Server 200 mit genau den Channels, in denen grok-jens und Jens Mitglied sind
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: `tests/server/test_chat_agent_accounts.py`
- Add tests: `tests/server/test_chat_agent_auth.py` (Token auf `/v1/sessions` → 401; widerrufen → 401 sofort)
- Run: `uv run --no-sync pytest tests/server/test_chat_agent_accounts.py tests/server/test_chat_agent_auth.py -q` → grün
- Run: `uv run --no-sync python scripts/dump_openapi.py` → grün
- Run: `uv run --no-sync pytest tests/server/test_openapi_drift.py -q` → grün
- Run: `just lint` → grün
- Run: `just typecheck-python` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] `openapi.json` regeneriert, Drift-Test grün
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-103: Agent-Handles aus der Agent-Liste (`GET /v1/chat/agents`) und READ-Grants für später Beitretende
**Release:** R1   **Phase / wave:** Story ST-3.1.2 / 8   **Parallel:** [P] ja   **Depends on:** T-101   **Rolle:** coder_deepseek · reviewer   **Aufwand:** 4 h
**Implements:** FR-039, FR-086; ACs: AC-039a, AC-039b, AC-086a
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:feature`, `agent-ready`

**Context**
Handle = Slug des Agent-Namens aus der Agent-Liste des Aufrufers, Kollision → Suffix `-2` nach `created_at`; `agent` bleibt Alias für den Projekt-Agenten. Beim Join eines Menschen READ auf alle Agent-Thread-Sessions des Channels (Default zu Q-17), Leave entzieht nichts (A-20).

**Requirements (zitiert)**
- FR-039 (Must, R1): The Server shall expose `GET /v1/chat/agents` returning, for the caller, every agent from the existing agent listing with a unique handle (slugified name, collision suffix `-2`, `-3` by `created_at`), plus all active Agentenkonten.
- FR-086 (Should, R1): When a human joins a Channel, the Server shall grant READ on the sessions of all Agent-Threads of that Channel (unless the sharing mode forbids new grants).

**Scope**
- `omnigent/server/chat_agent_threads.py` (adapt)
- `omnigent/server/routes/chat.py` (adapt)
- `tests/server/test_chat_agent_handles.py` (new)
- Out of scope / nicht anfassen: Keine UI (T-110).

**Acceptance criteria**
*AC-039a (FR-039)*
```gherkin
Given die Agenten "Coder DeepSeek" (älter) und "coder deepseek" (neuer) sind für Anna sichtbar
When Anna GET /v1/chat/agents aufruft
Then erhält sie die Handles "coder-deepseek" und "coder-deepseek-2"
```
*AC-039b (FR-039)*
```gherkin
Given ein Agent ist für Anna nicht sichtbar
Then fehlt er in GET /v1/chat/agents und eine Erwähnung seines Handles startet keine Session (agent_not_found)
```
*AC-086a (FR-086)*
```gherkin
Given "#billing" hat einen Agent-Thread mit Session S7
When Carla "#billing" beitritt
Then hat Carla READ auf S7
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: `tests/server/test_chat_agent_handles.py`
- Run: `uv run --no-sync pytest tests/server/test_chat_agent_handles.py -q` → grün
- Run: `uv run --no-sync python scripts/dump_openapi.py` → grün
- Run: `uv run --no-sync pytest tests/server/test_openapi_drift.py -q` → grün
- Run: `just lint` → grün
- Run: `just typecheck-python` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] `openapi.json` regeneriert, Drift-Test grün
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-104: Agent folgt dem Thread: Weiterleitung (EDIT+), `not_forwarded`, Entlassen/Reaktivieren, Forward-Retry
**Release:** R1   **Phase / wave:** Story ST-3.1.3 / 8   **Parallel:** [P] ja   **Depends on:** T-101   **Rolle:** coder_grok · reviewer   **Aufwand:** 6 h
**Implements:** FR-040, FR-041, FR-042, FR-043, FR-054; ACs: AC-040a, AC-041a, AC-042a, AC-042b, AC-043a, AC-043b, AC-054a
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:feature`, `agent-ready`

**Context**
Solange `agent_state=active`, gehen Antworten von Autoren mit Level ≥ EDIT als `[Name] Text` über `POST /v1/sessions/{id}/events` an die Session (Steer bei `running` ist bestehendes Verhalten, `test_always_steer.py`). READ-Autoren: `forward_state=not_forwarded`. Entlassen per Button/`/dismiss`; erneute Erwähnung reaktiviert dieselbe Session. Fehler → `forward_state=failed`, Retry-Endpoint.

**Requirements (zitiert)**
- FR-040 (Must, R1): While a Thread has `agent_state=active`, when a human with level ≥ EDIT on its session posts a reply, the Server shall forward it as `message` event (`[Name] text`) to the session, which steers if the session is running.
- FR-041 (Must, R1): If the author of a reply in an active Agent-Thread has level < EDIT on the session, then the Server shall store but not forward the reply and mark it `forward_state=not_forwarded`, and the Web-App shall label it „nicht an Agent“.
- FR-042 (Must, R1): When the Aufrufer or a human with level ≥ EDIT triggers „Entlassen“ (button or `/dismiss`), the Server shall set `agent_state=dismissed` and stop forwarding without stopping the session.
- FR-043 (Should, R1): When a human with level ≥ EDIT mentions the Thread's agent handle in a dismissed Thread, the Server shall set `agent_state=active` again and forward that message to the same session.
- FR-054 (Should, R1): If forwarding a Thread reply to the session fails, then the Server shall set `forward_state=failed`, and the Web-App shall show „nicht zugestellt – erneut senden“, which retries via `POST /v1/chat/messages/{id}/forward`.

**Scope**
- `omnigent/server/chat_agent_threads.py` (adapt)
- `omnigent/server/routes/chat.py` (adapt: `threads/{id}/dismiss`, `messages/{id}/forward`)
- `tests/server/test_chat_agent_follow.py` (new)
- Out of scope / nicht anfassen: Keine UI (T-114).

**Acceptance criteria**
*AC-040a (FR-040) – E2E*
```gherkin
Given der Agent-Thread ist active und die Session läuft
When Anna im Thread "bitte auch Linter" antwortet
Then erhält die Session die Nachricht "[Anna] bitte auch Linter" und die Antwort trägt keine Markierung "nicht an Agent"
```
*AC-041a (FR-041) – E2E*
```gherkin
Given Bob hat READ auf der Session
When Bob im aktiven Agent-Thread antwortet
Then wird seine Antwort gespeichert, nicht weitergeleitet und mit "nicht an Agent" markiert
```
*AC-042a (FR-042)*
```gherkin
When Anna im Thread "/dismiss" sendet
Then ist agent_state "dismissed", der Chip zeigt "entlassen" und Annas nächste Antwort wird nicht weitergeleitet
```
*AC-042b (FR-042)*
```gherkin
When Bob (READ) "Entlassen" auslöst
Then antwortet der Server 403 und agent_state bleibt "active"
```
*AC-043a (FR-043)*
```gherkin
Given der Thread ist dismissed
When Anna "<@omnigent_agent:agent> weiter bitte" antwortet
Then ist agent_state "active" und dieselbe Session erhält die Nachricht
```
*AC-043b (FR-043)*
```gherkin
Given der Thread ist dismissed und Bob hat READ
When Bob den Agenten im Thread erwähnt
Then bleibt agent_state "dismissed" und die Nachricht wird mit "nicht an Agent" markiert
```
*AC-054a (FR-054)*
```gherkin
Given der Runner ist offline und Anna antwortet im aktiven Thread
Then zeigt ihre Nachricht "nicht zugestellt – erneut senden" und ein Klick nach Wiederverbindung leitet sie genau einmal weiter
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: `tests/server/test_chat_agent_follow.py`
- Run: `uv run --no-sync pytest tests/server/test_chat_agent_follow.py -q` → grün
- Run: `uv run --no-sync python scripts/dump_openapi.py` → grün
- Run: `uv run --no-sync pytest tests/server/test_openapi_drift.py -q` → grün
- Run: `just lint` → grün
- Run: `just typecheck-python` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] `openapi.json` regeneriert, Drift-Test grün
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-106: Zugriffspolitik für Agentenkonten (Bot ⊆ Owner) inklusive Rechte-Matrix-Tests
**Release:** R1   **Phase / wave:** Story ST-7.1.1 / 8   **Parallel:** [P] ja   **Depends on:** T-105   **Rolle:** coder_deepseek · reviewer (Security)   **Aufwand:** 5 h
**Implements:** FR-067, FR-068, NFR-012, NFR-017; ACs: AC-021c, AC-067a, AC-068a
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:feature`, `agent-ready`

**Context**
`chat_access` prüft bei jedem Bot-Request: Bot ist Mitglied UND Owner ist Mitglied; Verbote (Channel anlegen, fremde Channels, Session-Chats, Agent-Starts, Agentenkonten verwalten). DM mit Bot ohne Owner → 403 `agent_owner_required` (Default Q-13). Rechte-Matrix aus 05 §Sicherheit vollständig als parametrisierter Test.

**Requirements (zitiert)**
- FR-067 (Must, R1): The Server shall allow an Agentenkonto to read or post in a Channel only while it is a member and its owner is currently a member; in a DM only while its owner is a participant [OFFEN Q-13].
- FR-068 (Must, R1): If an Agentenkonto attempts to create a Channel, join a Channel its owner is not in, access a Session-Chat, read a session, resolve an elicitation, or create/rotate Agentenkonten, then the Server shall return 403 (401 for non-chat paths) and change nothing.

**Scope**
- `omnigent/server/chat_access.py` (adapt)
- `tests/server/test_chat_agent_access_matrix.py` (new)
- Out of scope / nicht anfassen: Keine neuen Endpoints.

**Acceptance criteria**
*AC-021c (FR-021)*
```gherkin
When Anna POST /v1/chat/dms mit [grok-jens] (Owner Jens nicht enthalten) sendet
Then antwortet der Server 403 mit code "agent_owner_required" und es entsteht keine DM
```
*AC-067a (FR-067)*
```gherkin
Given grok-jens und Jens sind Mitglied in "#billing"
When Jens "#billing" verlässt
Then liefert der nächste chat_read von grok-jens auf "#billing" 403
```
*AC-068a (FR-068)*
```gherkin
When grok-jens POST /v1/chat/channels oder POST /v1/sessions/S1/elicitations/e1/resolve aufruft
Then antwortet der Server 403 bzw. 401 und ändert nichts
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: `tests/server/test_chat_agent_access_matrix.py` (100 % der Matrixzellen)
- Run: `uv run --no-sync pytest tests/server/test_chat_agent_access_matrix.py -q` → grün
- Run: `just lint` → grün
- Run: `just typecheck-python` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-110: Web: @-Autocomplete (Menschen, Agentenkonten, Omnigent-Agenten) und Diktat im Chat-Composer
**Release:** R1   **Phase / wave:** Story ST-2.3.1/2.1.3 / 8   **Parallel:** [P] ja   **Depends on:** T-103   **Rolle:** coder_grok · ux · reviewer   **Aufwand:** 6 h
**Implements:** FR-026, FR-028, NFR-009; ACs: AC-026a, AC-026b, AC-028a, AC-039a
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:feature`, `agent-ready`

**Context**
Löst den Mention-Teil von Issue #10 ab. Autocomplete ≤ 100 ms aus Verzeichnis (A-21) + `GET /v1/chat/agents`; gespeichert werden Tokens (`<@user:ID>` usw.), angezeigt aktuelle Namen; Agentenkonten als „Name (Owner)“. Umsetzung mit `@tiptap/extension-mention` (neue Dependency laut C-1) oder schlankem eigenem Popover, im PR begründen. Diktat über bestehenden `web/src/components/ComposerMicButton.tsx`.

**Requirements (zitiert)**
- FR-026 (Must, R1): When a user types `@` in a chat composer, the Web-App shall show within 100 ms an autocomplete of Channel members, other humans from the directory, Agentenkonten („Name (Owner)“) and Omnigent-Agenten, filtered by prefix and operable with ↑/↓/Enter/Esc.
- FR-028 (Could, R1): Where dictation is configured, the chat composer shall offer `ComposerMicButton` that inserts transcribed text without sending.

**Scope**
- `web/src/chat/mentions/*` (new)
- `web/src/chat/ChatComposerCard.tsx` (adapt)
- `web/package.json` (adapt, falls Tiptap-Mention)
- Out of scope / nicht anfassen: Keine Datei-Uploads, keine Suche (neues Ticket aus #10).

**Acceptance criteria**
*AC-026a (FR-026) – E2E*
```gherkin
Given Anna tippt "@je" im Composer von "#general"
Then erscheinen binnen 100 ms "Jens" und "Grok (Jens Holze)" mit Badge "Bot", und Enter fügt den markierten Eintrag als Chip ein
```
*AC-026b (FR-026)*
```gherkin
Given Anna tippt "@zz"
Then zeigt die Liste "Niemand gefunden" und es wird kein Chip eingefügt
```
*AC-028a (FR-028)*
```gherkin
Given Diktat ist konfiguriert
When Anna im Chat-Composer "Bin gleich da" diktiert
Then steht "Bin gleich da" im Composer und es wurde nichts gesendet
```
*AC-039a (FR-039)*
```gherkin
Given die Agenten "Coder DeepSeek" (älter) und "coder deepseek" (neuer) sind für Anna sichtbar
When Anna GET /v1/chat/agents aufruft
Then erhält sie die Handles "coder-deepseek" und "coder-deepseek-2"
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: Vitest: Präfix-Filter, Tastatur ↑/↓/Enter/Esc, „Niemand gefunden“
- Run: `pnpm --filter web run test` → grün
- Run: `pnpm --filter web run lint` → grün
- Run: `pnpm --filter web run type-check` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] UX-Review-Notiz gegen `docs/ux/chat-review-checklist.md` im PR; E2E für die Story existiert oder ist in T-009/T-120/T-121 eingeplant
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-111: Web: Ungelesen-Badges, Lesestand-Sync, Changes-Reset und 429-Anzeige
**Release:** R1   **Phase / wave:** Story ST-1.2.1 / 8   **Parallel:** [P] ja   **Depends on:** T-101   **Rolle:** coder_grok · reviewer   **Aufwand:** 5 h
**Implements:** FR-083, NFR-018; ACs: AC-007a, AC-008a, AC-009a, AC-017a, AC-083a
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:feature`, `agent-ready`

**Context**
Badges in `ChatSidebar` aus Bootstrap/`read.updated`; Lesestand wird beim Sichtbarwerden der letzten Nachricht gesetzt (Intersection Observer, gedrosselt). Bei `reset_required` (FR-083, Server in T-119) Bootstrap neu laden. 429 → Hinweis mit `Retry-After`.

**Requirements (zitiert)**
- FR-083 (Should, R1): If the `changes` cursor is older than 7 days or more than 1000 changes are pending, then the Server shall return `reset: true` and the Web-App shall reload bootstrap and open views.

**Scope**
- `web/src/chat/ChatSidebar.tsx`, `ChannelView.tsx`, `useChatFrames.ts` (adapt)
- Out of scope / nicht anfassen: Kein Server-Code.

**Acceptance criteria**
*AC-007a (FR-007)*
```gherkin
Given Annas Cursor in "#billing" steht auf m10 und m11, m12 existieren
When Anna POST .../read mit dem Cursor von m12 sendet
Then steht ihr Cursor auf m12
```
*AC-008a (FR-008)*
```gherkin
Given Annas Cursor steht auf m10, danach folgen m11 von Jens mit "<@user:anna@bloopark.de>" und m12 von Anna
When Anna GET /v1/chat/bootstrap aufruft
Then ist für "#billing" unread_count 1 und mention_count 1
```
*AC-009a (FR-009) – E2E*
```gherkin
Given Anna hat /chat in zwei Browser-Kontexten offen und "#billing" zeigt in beiden Badge "2"
When sie "#billing" in Kontext A öffnet
Then zeigt Kontext B binnen 1 s kein Badge mehr für "#billing"
```
*AC-017a (FR-017)*
```gherkin
Given Anna hat in den letzten 60 s 30 Posts in "#general" versucht
When sie den 31. Post sendet
Then antwortet der Server 429 mit Retry-After und speichert nichts
```
*AC-083a (FR-083)*
```gherkin
Given Jens' changes_cursor ist 8 Tage alt
When GET /v1/chat/changes aufgerufen wird
Then antwortet der Server mit reset true und die Web-App lädt bootstrap neu
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: Vitest: Cursor nur vorwärts, Reset lädt neu
- Run: `pnpm --filter web run test` → grün
- Run: `pnpm --filter web run lint` → grün
- Run: `pnpm --filter web run type-check` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] UX-Review-Notiz gegen `docs/ux/chat-review-checklist.md` im PR; E2E für die Story existiert oder ist in T-009/T-120/T-121 eingeplant
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-117: Motion-Basis (`motion`, Tokens, MotionRoot, Reduced Motion) und Chat-Animationen
**Release:** R1   **Phase / wave:** Story ST-UX-1 / 8   **Parallel:** [P] ja   **Depends on:** T-009   **Rolle:** coder_grok · ux · reviewer   **Aufwand:** 6 h
**Implements:** FR-078, FR-079, NFR-006, NFR-007, NFR-008, NFR-010; ACs: AC-078a, AC-079a
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:feature`, `agent-ready`

**Context**
ADR-0008. `motion` hinzufügen (`motion/react`, `LazyMotion` + `domAnimation`, `MotionConfig reducedMotion="user"`); Tokens in `web/src/chat/motion.ts` (micro 150, enter 180, chip 200, pane 220, layout 280 ms; easeOut [0.2,0,0,1]; spring 500/40). Animationen: Nachricht-Enter, Chip-Crossfade, Karten-Spring, Badge, Thread-Pane.

**Requirements (zitiert)**
- FR-078 (Must, R1): The Web-App shall animate with Motion (`motion` package, `motion/react`): message enter (opacity 0→1, y 8→0 px, 180 ms), status-chip change (crossfade 200 ms), card mount (spring, settle ≤ 300 ms), unread badge change (scale pulse 150 ms), thread pane open (slide 220 ms).
- FR-079 (Must, R1): While the OS reports `prefers-reduced-motion: reduce`, the Web-App shall replace all transform animations of FR-078 by opacity changes ≤ 120 ms or none.

**Scope**
- `web/package.json` (adapt)
- `web/src/chat/motion.ts`, `MotionRoot.tsx` (new)
- `web/src/chat/MessageItem.tsx`, `agent/AgentStatusChip.tsx`, `ThreadPane.tsx` (adapt)
- Out of scope / nicht anfassen: Keine Animationen außerhalb von `web/src/chat/`.

**Acceptance criteria**
*AC-078a (FR-078) – E2E*
```gherkin
Given reducedMotion "no-preference"
When eine neue Nachricht erscheint
Then hat das Nachrichten-Element beim Mount eine Transform-Animation mit Dauer 180 ms ± 20 ms (gemessen über element.getAnimations() bzw. data-motion-Attribute)
```
*AC-079a (FR-079) – E2E*
```gherkin
Given Playwright emuliert reducedMotion "reduce"
When eine neue Nachricht erscheint und der Status wechselt
Then läuft keine transform-Animation und keine Animation dauert länger als 120 ms
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: Vitest: Reduced-Motion schaltet Transforms ab
- Run: `pnpm --filter web run test` → grün
- Run: `pnpm --filter web run lint` → grün
- Run: `pnpm --filter web run type-check` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] UX-Review-Notiz gegen `docs/ux/chat-review-checklist.md` im PR; E2E für die Story existiert oder ist in T-009/T-120/T-121 eingeplant
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-107: MCP-Chat-Tools `chat_*` in `/mcp` (User-JWT und Agentenkonto-Token) und Polling-Weckweg `chat_unread`
**Release:** R1   **Phase / wave:** Story ST-7.1.6 / 9   **Parallel:** [P] ja   **Depends on:** T-106   **Rolle:** coder_deepseek · reviewer   **Aufwand:** 5 h
**Implements:** FR-069, FR-070, FR-088; ACs: AC-069a, AC-069b, AC-070a, AC-070b, AC-088a, AC-088b
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:feature`, `agent-ready`

**Context**
In R1 werden Grok Bots NICHT per Webhook geweckt (Q-18 entschieden 16.10. 23:25), sondern ihre Routine pollt alle ≥ 5 min `chat_unread` (A-23): für Agentenkonten liefert `GET /v1/chat/unread` ungelesene Erwähnungen + DM-Nachrichten nach Cursor (max 50, älteste zuerst), `chat_mark_read` schiebt den Cursor. PR #14 liefert `omnigent/server/mcp_endpoint.py` (`tool_definitions`, `_call_tool`, `_Api.call` als In-Process-ASGI mit weitergereichtem `authorization`). Neue Tools `chat_list_channels`, `chat_read`, `chat_post`, `chat_open_dm`, `chat_list_members`, `chat_unread`, `chat_mark_read`; Schreib-Tools brauchen `confirm:true`. Mit Agentenkonto-Token liefert `tools/list` nur `chat_*`.

**Requirements (zitiert)**
- FR-069 (Must, R1): Where `team_chat` is enabled, `/mcp` shall offer tools `chat_list_channels`, `chat_read`, `chat_post`, `chat_open_dm`, `chat_list_members`, `chat_unread`, `chat_mark_read` that call the `/v1/chat/*` routes in-process; write tools shall require `confirm: true` as all PR #14 writes.
- FR-070 (Must, R1): When `/mcp` is called with an Agentenkonto token, `tools/list` shall return only `chat_*` tools and `tools/call` of any other tool shall return a tool error without calling `/v1`.
- FR-088 (Must, R1): When an Agentenkonto calls `chat_unread` (MCP) or `GET /v1/chat/unread`, the Server shall return its unread messages that mention it or were posted in its DMs (oldest first, at most 50, each with `channel_id`, `thread_root_id`, `message_id`, author, body), and `chat_mark_read` shall advance the account's read cursor past them.

**Scope**
- `omnigent/server/mcp_endpoint.py` (adapt)
- `omnigent/server/routes/chat.py` (adapt: `GET /v1/chat/unread`)
- `docs/mcp.md` (adapt, inkl. Abschnitt „Grok-Bot-Routine: Polling ≥ 5 min“)
- `tests/server/test_mcp_chat_tools.py` (new)
- Out of scope / nicht anfassen: Bestehende MCP-Tools nicht ändern.

**Acceptance criteria**
*AC-069a (FR-069)*
```gherkin
Given Claude Code ist mit Annas User-JWT an /mcp verbunden
When es chat_post mit channel "general", body "Build läuft" und confirm true aufruft
Then erscheint "Build läuft" von Anna in "#general"
```
*AC-069b (FR-069)*
```gherkin
When chat_post ohne confirm true aufgerufen wird
Then liefert das Tool eine Vorschau des Requests und es wird nichts gespeichert
```
*AC-070a (FR-070)*
```gherkin
When grok-jens tools/list aufruft
Then enthält die Liste nur Tools mit Präfix "chat_"
```
*AC-070b (FR-070)*
```gherkin
When grok-jens tools/call "create_session" aufruft
Then liefert das Tool einen Fehler und es erfolgt kein /v1-Aufruf
```
*AC-088a (FR-088)*
```gherkin
Given grok-jens ist Mitglied in "#billing" und Anna schrieb "<@agent_account:grok-jens> fass zusammen"
When die Grok-Bot-Routine chat_unread mit dem Token von grok-jens aufruft
Then enthält das Ergebnis genau diese Nachricht mit channel_id, thread_root_id und message_id
And nach chat_mark_read mit confirm true liefert ein erneuter Aufruf sie nicht mehr
```
*AC-088b (FR-088)*
```gherkin
Given Jens hat "#billing" verlassen
When grok-jens chat_unread aufruft
Then enthält das Ergebnis keine Nachrichten aus "#billing"
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: `tests/server/test_mcp_chat_tools.py`
- Run: `uv run --no-sync pytest tests/server/test_mcp_chat_tools.py tests/server -q -k mcp` → grün
- Run: `just lint` → grün
- Run: `just typecheck-python` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-109: Bot-Bremse und Schutzregeln: Streak 5, kein Agent-Start durch Bots, keine Weiterleitung von Bot-Nachrichten
**Release:** R1   **Phase / wave:** Story ST-7.1.5 / 9   **Parallel:** [P] ja   **Depends on:** T-104, T-106   **Rolle:** coder_grok · reviewer   **Aufwand:** 4 h
**Implements:** FR-036, FR-074, FR-075, FR-077, NFR-029; ACs: AC-036a, AC-074a, AC-075a, AC-077a
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:feature`, `agent-ready`

**Context**
Schleifen- und Kostenschutz. `bot_streak` in `chat_threads`; 6. Bot-Post → 409 `bot_streak_limit` + einmalige Systemzeile; menschliche Antwort setzt zurück. Bot-Erwähnung eines Omnigent-Agenten → Systemzeile statt Session. Bot-Nachrichten werden nie weitergeleitet.

**Requirements (zitiert)**
- FR-036 (Must, R1): If the author of an `<@omnigent_agent:…>` mention is an Agentenkonto, then the Server shall store the message, create no session and post the system line „Nur Menschen können Omnigent-Agenten starten“.
- FR-074 (Must, R1): If a Thread's `bot_streak` is 5, then the Server shall reject posts by Agentenkonten in that Thread with 409 `bot_streak_limit`, enqueue no `chat.mention` rows for it, and post the system line „Bots warten auf einen Menschen“ once.
- FR-075 (Must, R1): When a human posts in a Thread, the Server shall reset that Thread's `bot_streak` to 0.
- FR-077 (Must, R1): The Server shall never forward messages authored by an Agentenkonto to an Omnigent session.

**Scope**
- `omnigent/server/chat_agent_threads.py`, `omnigent/server/routes/chat.py` (adapt)
- `tests/server/test_chat_bot_brake.py` (new)
- Out of scope / nicht anfassen: Keine UI-Änderung außer Systemzeilen-Codes.

**Acceptance criteria**
*AC-036a (FR-036)*
```gherkin
When grok-jens in "#general" "<@omnigent_agent:coder> mach was" postet
Then wird die Nachricht gespeichert, keine Session angelegt und die Systemzeile "Nur Menschen können Omnigent-Agenten starten" erscheint
```
*AC-074a (FR-074)*
```gherkin
Given im Thread von m1 haben grok-jens und grok-anna abwechselnd 5 Nachrichten gepostet
When grok-jens eine 6. Nachricht postet
Then antwortet der Server 409 "bot_streak_limit", es entsteht keine Outbox-Zeile und die Systemzeile "Bots warten auf einen Menschen" steht genau einmal im Thread
```
*AC-075a (FR-075)*
```gherkin
Given die Bot-Bremse ist aktiv
When Anna im Thread "Weiter" postet
Then ist bot_streak 0 und grok-jens darf wieder posten
```
*AC-077a (FR-077)*
```gherkin
Given ein aktiver Agent-Thread
When grok-jens darin postet
Then wird die Nachricht nicht an die Session weitergeleitet
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: `tests/server/test_chat_bot_brake.py`
- Run: `uv run --no-sync pytest tests/server/test_chat_bot_brake.py -q` → grün
- Run: `just lint` → grün
- Run: `just typecheck-python` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-112: „Braucht dich“-Ansicht und Desktop-Benachrichtigungen
**Release:** R1   **Phase / wave:** Story ST-1.2.2/1.2.3 / 9   **Parallel:** [P] ja   **Depends on:** T-111   **Rolle:** coder_grok · ux · reviewer   **Aufwand:** 5 h
**Implements:** FR-010, FR-011, FR-012; ACs: AC-010a, AC-010b, AC-011a, AC-012a
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:feature`, `agent-ready`

**Context**
`GET /v1/chat/needs-you` (in T-101 mitliefern, falls fehlt: hier im Server ergänzen) + Anzeige im Inbox-Muster (`web/src/pages/InboxPage.tsx`, `pending_elicitations_count`). Benachrichtigungen über `web/src/lib/browserNotifications.ts` mit generischem Text ohne Nachrichteninhalt.

**Requirements (zitiert)**
- FR-010 (Must, R1): The Web-App shall show a „Braucht dich“ view listing (a) unread messages mentioning the caller and (b) pending elicitations of Agent-Thread sessions on which the caller has level ≥ EDIT, each linking to its Thread.
- FR-011 (Should, R1): When a mention of the caller, a DM message or an elicitation from an Agent-Thread the caller may answer arrives while the window is unfocused or the Channel is not open, the Web-App shall show an OS notification whose body contains no message text („Neue Erwähnung in #billing“, „Neue Direktnachricht von Jens“, „Agent braucht eine Antwort“).
- FR-012 (Should, R1): If the browser notification permission is denied, then the Web-App shall not ask again and shall only update in-app badges.

**Scope**
- `web/src/chat/NeedsYouView.tsx` (new)
- `web/src/pages/InboxPage.tsx`, `web/src/lib/browserNotifications.ts` (adapt)
- `omnigent/server/routes/chat.py` (adapt nur falls `needs-you` fehlt)
- Out of scope / nicht anfassen: Keine Push-Benachrichtigungen auf Mobilgeräte.

**Acceptance criteria**
*AC-010a (FR-010) – E2E*
```gherkin
Given Jens schreibt in "#billing" "<@user:anna@bloopark.de> schau mal" und Annas Agent-Thread-Session wartet auf Freigabe
When Anna die Braucht-dich-Ansicht öffnet
Then sieht sie beide Einträge und ein Klick öffnet den jeweiligen Thread
```
*AC-010b (FR-010)*
```gherkin
Given Bob hat nur READ auf Annas wartender Agent-Thread-Session
When Bob die Braucht-dich-Ansicht öffnet
Then enthält sie diese Freigabe nicht
```
*AC-011a (FR-011)*
```gherkin
Given Anna hat Benachrichtigungen erlaubt und das Fenster ist nicht fokussiert
When Jens Anna eine DM "Kurz Zeit?" schickt
Then erscheint eine OS-Benachrichtigung "Neue Direktnachricht von Jens" ohne den Text "Kurz Zeit?"
```
*AC-012a (FR-012)*
```gherkin
Given die Berechtigung ist "denied"
When eine Erwähnung eintrifft
Then wird Notification.requestPermission nicht aufgerufen und das Badge zählt hoch
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: Vitest: Notification-Text ohne Body; verweigerte Berechtigung fragt nicht erneut
- Run: `pnpm --filter web run test` → grün
- Run: `pnpm --filter web run lint` → grün
- Run: `pnpm --filter web run type-check` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] UX-Review-Notiz gegen `docs/ux/chat-review-checklist.md` im PR; E2E für die Story existiert oder ist in T-009/T-120/T-121 eingeplant
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-113: Web: Session-Chat in Chat-Sidebar und Session-Spalte; Agent-Thread-Sessions aus der Haupt-Sidebar ausblenden
**Release:** R1   **Phase / wave:** Story ST-2.1.4 / ST-3.1.1 / 9   **Parallel:** [P] ja   **Depends on:** T-102, T-110   **Rolle:** coder_grok · ux · reviewer   **Aufwand:** 5 h
**Implements:** FR-085, NFR-011; ACs: AC-029a, AC-029c, AC-085a, AC-085b
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:feature`, `agent-ready`

**Context**
Session-Chats erscheinen in `/chat` im Abschnitt „Sessions“, sobald sie ≥ 1 Nachricht haben. Die Session-Spalte aus PR #4 (`web/src/shell/SessionChatColumn.tsx`) nutzt dieselben Komponenten wie Channels (MessageList, ChatComposerCard) und löst damit Issue #3/#7 ab. Haupt-Sidebar (`web/src/shell/Sidebar.tsx`, Daten aus `web/src/hooks/useConversations.ts`) blendet Sessions mit Label `omnigent.chat.v1` für Nicht-Owner aus.

**Requirements (zitiert)**
- FR-085 (Must, R1): The Web-App shall hide sessions labelled `omnigent.chat.v1` from the main session sidebar for every viewer who is not the session owner, and list them in the owning Thread instead.

**Scope**
- `web/src/shell/SessionChatColumn.tsx`, `web/src/shell/Sidebar.tsx`, `web/src/chat/ChatSidebar.tsx` (adapt)
- `web/src/lib/humanChat.ts` (adapt/entfernen, PR #4)
- Out of scope / nicht anfassen: Keine Server-Änderung.

**Acceptance criteria**
*AC-029a (FR-029) – E2E*
```gherkin
Given Jens teilt Session S1 mit Anna (EDIT)
When Anna S1 öffnet
Then ist im ChatHeader der Session-Chat erreichbar und zeigt die bisherigen Nachrichten
```
*AC-029c (FR-029)*
```gherkin
Given Annas Agent-Thread-Session S7 trägt das Label omnigent.chat.v1 und hat Grants für Anna, Jens, Bob
Then existiert für S7 kein Session-Chat-Channel
```
*AC-085a (FR-085) – E2E*
```gherkin
Given Anna hat in "#billing" einen Agent-Thread gestartet und Jens hat READ
When Jens die Hauptsidebar ansieht
Then erscheint die Agent-Thread-Session dort nicht, wohl aber im Thread unter "Session öffnen"
```
*AC-085b (FR-085)*
```gherkin
When Anna (Owner) die Hauptsidebar ansieht
Then erscheint die Session mit Chat-Symbol in ihrer Liste
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: Vitest: Sidebar-Filter (Owner sieht, Nicht-Owner nicht)
- Add tests: `web/src/shell/SessionChatColumn.test.tsx` angepasst
- Run: `pnpm --filter web run test` → grün
- Run: `pnpm --filter web run lint` → grün
- Run: `pnpm --filter web run type-check` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] UX-Review-Notiz gegen `docs/ux/chat-review-checklist.md` im PR; E2E für die Story existiert oder ist in T-009/T-120/T-121 eingeplant
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-116: Web: Einstellungen „Agenten“ (Agentenkonten) und Bot-Anzeige/Status
**Release:** R1   **Phase / wave:** Story ST-7.1.3/7.1.4 / 9   **Parallel:** [P] ja   **Depends on:** T-105   **Rolle:** coder_grok · ux · reviewer   **Aufwand:** 5 h
**Implements:** FR-062, FR-076, NFR-013; ACs: AC-061a, AC-062a, AC-062b, AC-064a, AC-076a
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:feature`, `agent-ready`

**Context**
Abschnitt in `web/src/pages/SettingsPage.tsx`: Anlegen (Token + Hinweis „nur einmal sichtbar“, kopieren), Rotieren, Widerrufen. Hinweis für die Grok-Bot-Routine: Polling ≥ 5 min über `chat_unread` (R1); Webhook-Felder erst R1.1 sichtbar. Anzeige „Name (Owner)“ mit „Bot“-Badge überall; Status „aktiv“ aus `last_seen_at`.

**Requirements (zitiert)**
- FR-062 (Must, R1): The Web-App shall display every Agentenkonto as „{display_name} ({Owner-Anzeigename})“ with a „Bot“ badge in messages, member list and autocomplete.
- FR-076 (Should, R1): The member list and autocomplete shall show for each Agentenkonto a status: „aktiv“ (authenticated request in the last 10 min), „inaktiv“, „Webhook fehlerhaft“; revoked accounts shall not appear in autocomplete.

**Scope**
- `web/src/chat/settings/AgentAccountsSection.tsx` (new)
- `web/src/pages/SettingsPage.tsx`, `web/src/chat/MessageItem.tsx` (adapt)
- Out of scope / nicht anfassen: Webhook-Status-UI (FR-073) → T-116b in R1.1.

**Acceptance criteria**
*AC-061a (FR-061) – E2E*
```gherkin
When Jens in den Einstellungen ein Agentenkonto "Grok", Handle "grok-jens", Webhook "https://bot.example.com/hook" anlegt
Then werden ein Token mit Präfix "ogc_" und ein Secret genau einmal angezeigt und nach Schließen des Dialogs nicht mehr
```
*AC-062a (FR-062)*
```gherkin
When grok-jens in "#general" postet
Then zeigt die Nachricht den Autor "Grok (Jens Holze)" mit Badge "Bot"
```
*AC-062b (FR-062)*
```gherkin
Given Jens' Anzeigename ist nicht gesetzt
Then zeigt die Nachricht "Grok (jens@bloopark.de)"
```
*AC-064a (FR-064)*
```gherkin
When Jens das Token von grok-jens rotiert
Then liefert der alte Token auf /mcp 401 und der neue Token funktioniert
```
*AC-076a (FR-076)*
```gherkin
Given grok-jens hat sich vor 3 min authentifiziert
Then zeigt die Mitgliederliste "aktiv"; nach Widerruf fehlt grok-jens in der @-Liste
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: Vitest: Token wird nach Schließen nicht erneut angezeigt
- Run: `pnpm --filter web run test` → grün
- Run: `pnpm --filter web run lint` → grün
- Run: `pnpm --filter web run type-check` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] UX-Review-Notiz gegen `docs/ux/chat-review-checklist.md` im PR; E2E für die Story existiert oder ist in T-009/T-120/T-121 eingeplant
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-118: Geglättetes Streaming, Scroll-Pille „Neue Nachrichten“ und Prompt-Vorschläge
**Release:** R1   **Phase / wave:** Story ST-UX-1 / 9   **Parallel:** [P] ja   **Depends on:** T-117   **Rolle:** coder_grok · ux · reviewer   **Aufwand:** 4 h
**Implements:** FR-080, FR-081, NFR-006; ACs: AC-080a, AC-080b, AC-081a
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:feature`, `agent-ready`

**Context**
Gepufferte Zeichenausgabe (≤ 1 Update pro Frame), Auto-Scroll nur am Ende, sonst Pille. Vorschläge (Could) für leere Threads/Channels. Benchmark-Muster: `web/src/loadtest/streamRenderBench.ts`.

**Requirements (zitiert)**
- FR-080 (Must, R1): While assistant text streams, the Web-App shall smooth rendering (buffered character release, ≤ 1 visual update per animation frame) and keep the view anchored at the bottom only when the user was at the bottom; otherwise it shall show a „Neue Nachrichten“ pill.
- FR-081 (Could, R1): When an Agent-Thread is empty or a Channel has no messages, the Web-App shall show up to 3 suggested prompts that insert text into the composer on click.

**Scope**
- `web/src/chat/agent/useSmoothedStream.ts`, `NewMessagesPill.tsx`, `Suggestions.tsx` (new)

**Acceptance criteria**
*AC-080a (FR-080) – E2E*
```gherkin
Given Jens hat im Thread nach oben gescrollt
When der Agent weiter streamt
Then bleibt die Scroll-Position stabil (± 2 px) und eine Pille "Neue Nachrichten" erscheint
```
*AC-080b (FR-080)*
```gherkin
Given Jens ist am Ende des Threads
When der Agent streamt
Then bleibt die letzte Zeile sichtbar
```
*AC-081a (FR-081)*
```gherkin
Given ein neuer Agent-Thread ohne Antworten
Then zeigt er höchstens 3 Vorschläge und ein Klick fügt den Text in den Composer ein
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: Vitest: Puffer-Logik mit Fake-Timern
- Run: `pnpm --filter web run test` → grün
- Run: `pnpm --filter web run lint` → grün
- Run: `pnpm --filter web run type-check` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] UX-Review-Notiz gegen `docs/ux/chat-review-checklist.md` im PR; E2E für die Story existiert oder ist in T-009/T-120/T-121 eingeplant
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-114: Web: Agent-Steuerung im Thread (Buttons + Slash), Rechte-Zustände, „Session öffnen“, Run-Stuck, Forward-Status
**Release:** R1   **Phase / wave:** Story ST-3.1.3/3.3.1/3.3.2/3.4.1 / 10   **Parallel:** [P] ja   **Depends on:** T-104, T-113   **Rolle:** coder_grok · ux · reviewer   **Aufwand:** 6 h
**Implements:** FR-049, FR-050, FR-051, FR-052, FR-053, NFR-030; ACs: AC-041a, AC-042a, AC-049a, AC-049b, AC-050a, AC-051a, AC-051b, AC-052a, AC-052b, AC-053a, AC-054a
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:feature`, `agent-ready`

**Context**
`/interrupt`, `/retry`, `/stop`, `/dismiss` und gleichwertige Buttons nutzen bestehende Session-Endpunkte; fehlende Rechte → deaktiviert mit Tooltip (Stop nur OWNER). Markierungen „nicht an Agent“ und „nicht zugestellt – erneut senden“. Run-Stuck-Banner nach 120 s ohne Stream-Event.

**Requirements (zitiert)**
- FR-049 (Must, R1): The Thread composer shall offer buttons and slash commands `/interrupt`, `/retry`, `/stop`, `/dismiss` that map to events `interrupt`, `retry_session`, `stop_session` and FR-042.
- FR-050 (Must, R1): If the viewer lacks the level required by a control (EDIT for interrupt/retry/dismiss/steer, OWNER for stop), then the Web-App shall render the control disabled with a tooltip naming the required role, and a slash command shall show that hint and send nothing.
- FR-051 (Must, R1): The stop control shall send `stop_session` only for the session OWNER.
- FR-052 (Must, R1): The Thread header shall contain „Session öffnen“ linking to `/c/{session_id}`.
- FR-053 (Should, R1): While a session is `running` and no stream event arrived for 120 s, the Web-App shall show a run-stuck banner in the Thread with „Unterbrechen“ and „Session öffnen“.

**Scope**
- `web/src/chat/agent/AgentControls.tsx`, `RunStuckBanner.tsx` (new)
- `web/src/chat/ChatComposerCard.tsx`, `agent/AgentThreadView.tsx` (adapt)
- Out of scope / nicht anfassen: Keine neuen Server-Endpunkte.

**Acceptance criteria**
*AC-041a (FR-041) – E2E*
```gherkin
Given Bob hat READ auf der Session
When Bob im aktiven Agent-Thread antwortet
Then wird seine Antwort gespeichert, nicht weitergeleitet und mit "nicht an Agent" markiert
```
*AC-042a (FR-042)*
```gherkin
When Anna im Thread "/dismiss" sendet
Then ist agent_state "dismissed", der Chip zeigt "entlassen" und Annas nächste Antwort wird nicht weitergeleitet
```
*AC-049a (FR-049) – E2E*
```gherkin
Given die Session läuft
When Anna "/interrupt" sendet
Then wird ein Event type "interrupt" gesendet und der Chip wechselt binnen 1 s auf "fertig"
```
*AC-049b (FR-049)*
```gherkin
When Anna "/unbekannt" sendet
Then wird "/unbekannt" als normale Thread-Nachricht behandelt
```
*AC-050a (FR-050) – E2E*
```gherkin
Given Bob hat READ
When Bob "/retry" eingibt
Then zeigt der Composer "Keine Berechtigung: nur Aufrufer und Bearbeiter" und es wird nichts gesendet
```
*AC-051a (FR-051)*
```gherkin
Given Jens hat EDIT auf Annas Session
Then ist der Stop-Button für Jens deaktiviert mit Tooltip "Nur Anna (Owner) kann stoppen"
```
*AC-051b (FR-051)*
```gherkin
When Anna "Stop" klickt
Then wird stop_session gesendet
```
*AC-052a (FR-052) – E2E*
```gherkin
When Jens "Session öffnen" klickt
Then navigiert die Web-App zu /c/{session_id} und zeigt das Transkript lesend
```
*AC-052b (FR-052)*
```gherkin
Given Bob hat keinen Grant auf die Session
Then ist "Session öffnen" für Bob nicht sichtbar
```
*AC-053a (FR-053)*
```gherkin
Given die Session ist running und seit 120 s kam kein Stream-Event
Then zeigt der Thread das Banner "Agent reagiert nicht" mit "Unterbrechen" und "Session öffnen"
```
*AC-054a (FR-054)*
```gherkin
Given der Runner ist offline und Anna antwortet im aktiven Thread
Then zeigt ihre Nachricht "nicht zugestellt – erneut senden" und ein Klick nach Wiederverbindung leitet sie genau einmal weiter
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: Vitest: Rechte-Matrix der Controls
- Run: `pnpm --filter web run test` → grün
- Run: `pnpm --filter web run lint` → grün
- Run: `pnpm --filter web run type-check` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] UX-Review-Notiz gegen `docs/ux/chat-review-checklist.md` im PR; E2E für die Story existiert oder ist in T-009/T-120/T-121 eingeplant
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-115: Web: Agent-Schritte nach interior.dev TaskSteps (eingeklappt, Status, a11y), Plan-Review und PR-Karte
**Release:** R1   **Phase / wave:** Story ST-3.2.2/4.2.1/5.1.1 / 10   **Parallel:** [P] ja   **Depends on:** T-008, T-117   **Rolle:** coder_grok · ux · reviewer   **Aufwand:** 6 h
**Implements:** FR-048, FR-089, FR-090, FR-059, FR-060, NFR-007, NFR-008, NFR-009; ACs: AC-048a, AC-048b, AC-059a, AC-060a, AC-060b, AC-089a, AC-090a, AC-090b
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:feature`, `agent-ready`

**Context**
Schritte sind Should (Cut-Line), aber wenn sie gebaut werden, ist FR-090 (a11y/Reduced Motion) Pflicht. Referenz ist interior.dev TaskSteps (https://www.interior.dev/docs/task-steps, ADR-0008): `useTaskSteps({steps, current, failed})` → Status pending/active/done/error; Punkt, drehender Bogen (0,8 s) + Shimmer-Label (1,6 s), Häkchen/Kreuz mit Pop-Spring 640/22/0.7, Dauer als Mono-Meta bei done; `useReducedMotion` → nur Opacity; sr-only `role=status` „{Label}, Schritt n von m“ (500 ms entprellt), `aria-current=step`, polite „Lauf abgeschlossen/fehlgeschlagen“. Icons über lucide (`Check`, `X`, `LoaderCircle`). **Kein Copy-in** per `bunx shadcn add https://www.interior.dev/r/task-steps.json`, solange Q-20 (Lizenz) offen ist – bis dahin Nachbau. Schritte kommen aus Session-Todos bzw. Tool-Calls des aktuellen Turns. Plan-Review mit `web/src/components/blocks/ExitPlanModeReview.tsx`. PR-Karte read-only aus den Session-Ressourcen (Muster `web/src/shell/PullRequestPanel.tsx`).

**Requirements (zitiert)**
- FR-048 (Should, R1): While an Agent-Thread is open, the Web-App shall show tool calls and todos of the current turn collapsed into one row („3 Schritte“) that expands on click or Enter into an ordered step list.
- FR-089 (Should, R1): The step list shall render each step with status `pending` (Punkt), `active` (drehender Bogen + Shimmer auf dem Label), `done` (Häkchen mit Spring-Pop, Dauer als Mono-Meta rechts) or `error` (Kreuz mit Spring-Pop; folgende Schritte bleiben `pending`).
- FR-090 (Must, R1): Where the step list is shown, the Web-App shall announce via an sr-only `role="status"` region „{Label}, Schritt {n} von {m}“ (entprellt 500 ms) and on completion „Lauf abgeschlossen“ or „Lauf fehlgeschlagen bei {Label}“ (polite), mark the active row with `aria-current="step"`, and under reduced motion show status changes without scale, rotation or shimmer.
- FR-059 (Should, R1): When the session requests plan approval, the Thread shall render `ExitPlanModeReview` with the same EDIT rule.
- FR-060 (Should, R1): When an Agent-Thread session has an attached pull request, the Thread shall show a read-only PR card (title, number, state, CI status, link).

**Scope**
- `web/src/chat/agent/TaskSteps.tsx`, `StepsGroup.tsx`, `PrCard.tsx` (new)
- `web/src/chat/motion.ts` (adapt: Tokens `pop`, `cell`, `spin`, `shimmer`)
- `web/src/chat/agent/AgentThreadView.tsx`, `AgentStatusChip.tsx` (adapt: gleiche Bildsprache)
- Out of scope / nicht anfassen: Keine GitHub-Schreibaktionen; kein Copy-in von interior.dev-Code vor Q-20.

**Acceptance criteria**
*AC-048a (FR-048)*
```gherkin
Given der Agent ruft im Turn 3 Tools auf
Then zeigt der offene Thread eine Zeile "3 Schritte" und Enter darauf klappt die Tool-Liste auf
```
*AC-048b (FR-048, FR-089) – E2E*
```gherkin
Given der Agent arbeitet die Todos "Tests lesen", "Fix schreiben", "Tests ausführen", "PR öffnen" ab und steht bei "Tests ausführen"
When Anna die Schritte aufklappt
Then zeigen "Tests lesen" und "Fix schreiben" ein Häkchen mit Dauer (z. B. "3.4s") in Monospace, "Tests ausführen" einen drehenden Bogen mit Shimmer-Label und "PR öffnen" einen Punkt
```
*AC-059a (FR-059)*
```gherkin
When der Agent einen Plan zur Freigabe vorlegt
Then zeigt der Thread ExitPlanModeReview und Annas Freigabe setzt die Session fort
```
*AC-060a (FR-060)*
```gherkin
Given die Session hat PR #42 (open, CI grün) verknüpft
Then zeigt der Thread eine PR-Karte "#42" mit Status "open", CI "grün" und Link
```
*AC-060b (FR-060)*
```gherkin
Given die GitHub-App ist nicht verbunden
Then zeigt der Thread keine PR-Karte und keine Fehlermeldung
```
*AC-089a (FR-089)*
```gherkin
Given der Schritt "Tests ausführen" schlägt fehl
Then zeigt er ein rotes Kreuz, "PR öffnen" bleibt pending ohne Dauer, und kein späterer Schritt wird als erledigt dargestellt
```
*AC-090a (FR-090) – E2E*
```gherkin
Given Anna nutzt einen Screenreader und die Schritte sind aufgeklappt
When der Agent von Schritt 2 zu Schritt 3 "Tests ausführen" wechselt
Then enthält die role="status"-Region nach höchstens 600 ms "Tests ausführen, Schritt 3 von 4", die Zeile hat aria-current="step"
And nach dem letzten Schritt meldet die polite-Region "Lauf abgeschlossen"
```
*AC-090b (FR-090) – E2E*
```gherkin
Given der Browser emuliert prefers-reduced-motion: reduce
When ein Schritt von active zu done wechselt
Then erscheint das Häkchen ohne Skalierung (nur Opacity ≤ 120 ms), der Bogen dreht sich nicht und das aktive Label hat keinen Shimmer
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: Vitest: `useTaskSteps`-Statuslogik (done/active/error/complete, Ansagetext), Reduced Motion ohne Transform
- Add tests: E2E in T-121: `tests/e2e_ui/team_chat/test_agent_steps.py`
- Run: `pnpm --filter web run test` → grün
- Run: `pnpm --filter web run lint` → grün
- Run: `pnpm --filter web run type-check` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] UX-Review-Notiz gegen `docs/ux/chat-review-checklist.md` im PR; E2E für die Story existiert oder ist in T-009/T-120/T-121 eingeplant
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-119: Härtung: Rate-Limit, Changes-Reset, Audit, No-Body-Garantie, Latenz-/Kapazitätsmessung, OpenAPI
**Release:** R1   **Phase / wave:** Polish / 10   **Parallel:** [P] ja   **Depends on:** T-107, T-109   **Rolle:** coder_deepseek · reviewer (Security)   **Aufwand:** 6 h
**Implements:** FR-017, NFR-001, NFR-003, NFR-014, NFR-017, NFR-026, NFR-028; ACs: AC-017a, AC-017b, AC-083a
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:feature`, `agent-ready`

**Context**
30 Posts/min je (Principal, Channel), Bots zusätzlich 120 Requests/min; Changes älter 7 Tage oder > 1000 → `reset_required`. Ein Test postet einen Marker-Body und prüft, dass er in keinem WS-Frame, SSE-Event, Log und Audit-Attribut auftaucht. Latenz/Kapazität mit neuem Skript.

**Requirements (zitiert)**
- FR-017 (Must, R1): If a principal exceeds 30 post attempts within 60 s in one Channel, then the Server shall return 429 with `Retry-After` and store nothing.

**Scope**
- `omnigent/server/routes/chat.py`, `chat_realtime.py` (adapt)
- `tests/server/test_chat_hardening.py` (new)
- `tests/loadtest/test_chat_latency.py` (new)

**Acceptance criteria**
*AC-017a (FR-017)*
```gherkin
Given Anna hat in den letzten 60 s 30 Posts in "#general" versucht
When sie den 31. Post sendet
Then antwortet der Server 429 mit Retry-After und speichert nichts
```
*AC-017b (FR-017)*
```gherkin
Given Anna hat 30 Posts in "#general" versucht
When sie in "#billing" postet
Then antwortet der Server 201
```
*AC-083a (FR-083)*
```gherkin
Given Jens' changes_cursor ist 8 Tage alt
When GET /v1/chat/changes aufgerufen wird
Then antwortet der Server mit reset true und die Web-App lädt bootstrap neu
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: `tests/server/test_chat_hardening.py`
- Add tests: `tests/loadtest/test_chat_latency.py` (p50/p95 gegen NFR-001)
- Run: `uv run --no-sync pytest tests/server/test_chat_hardening.py tests/loadtest/test_chat_latency.py -q` → grün
- Run: `uv run --no-sync python scripts/dump_openapi.py` → grün
- Run: `uv run --no-sync pytest tests/server/test_openapi_drift.py -q` → grün
- Run: `just lint` → grün
- Run: `just typecheck-python` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] `openapi.json` regeneriert, Drift-Test grün
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-124: Tipp-Indikator (Could)
**Release:** R1   **Phase / wave:** Story ST-2.1.1 / 10   **Parallel:** [P] ja   **Depends on:** T-111   **Rolle:** coder_grok · reviewer   **Aufwand:** 2 h
**Implements:** FR-032; ACs: AC-032a
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:feature`, `agent-ready`

**Context**
Erster Kandidat für die Cut-Line. Höchstens ein `typing`-Frame je 3 s.

**Requirements (zitiert)**
- FR-032 (Could, R1): While a member types in a composer, the Web-App shall send at most one `typing` frame per 3 s and other open clients shall show „Anna schreibt…“ until 4 s without refresh.

**Scope**
- `web/src/chat/ChatComposerCard.tsx`, `ChannelView.tsx` (adapt)
- `omnigent/server/routes/chat.py` (adapt: `POST …/typing`)

**Acceptance criteria**
*AC-032a (FR-032)*
```gherkin
Given Anna tippt in "#general"
Then sieht Jens "Anna schreibt…" und es verschwindet 4 s nach Annas letztem Tastendruck
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: Vitest + `tests/server/test_chat_routes.py::test_typing_throttle`
- Run: `pnpm --filter web run test` → grün
- Run: `pnpm --filter web run lint` → grün
- Run: `pnpm --filter web run type-check` → grün
- Run: `just lint` → grün
- Run: `just typecheck-python` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] UX-Review-Notiz gegen `docs/ux/chat-review-checklist.md` im PR; E2E für die Story existiert oder ist in T-009/T-120/T-121 eingeplant
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-120: R1-Playwright: Menschen-Chat (Unread, Mentions, Braucht-dich, Session-Chat, Reconnect, Motion, a11y)
**Release:** R1   **Phase / wave:** Test / 11   **Parallel:** [P] ja   **Depends on:** T-111, T-112, T-113, T-117, T-118   **Rolle:** test_author · tester (Gate) · reviewer   **Aufwand:** 6 h
**Implements:** NFR-009, NFR-023, NFR-024; ACs: AC-009a, AC-010a, AC-026a, AC-029a, AC-078a, AC-079a, AC-080a
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:test`, `agent-ready`

**Context**
E2E für alle R1-UI-Stories der Menschen-Seite in `tests/e2e_ui/team_chat/`. a11y mit `axe-playwright-python` (neue Test-Dependency, NFR-009): 0 Verstöße der Stufen serious/critical auf `/chat`, Thread-Pane, Einstellungen. Motion-Test emuliert `prefers-reduced-motion`.

**Scope**
- `tests/e2e_ui/team_chat/test_unread.py`, `test_mentions.py`, `test_needs_you.py`, `test_session_chat_channel.py`, `test_motion.py`, `test_a11y.py` (new)
- `tests/e2e_ui/collaboration/test_session_human_chat.py` (entfernen; ersetzt durch `test_session_chat_channel.py`)
- `pyproject.toml` (adapt: Test-Dependency)
- Out of scope / nicht anfassen: Keine Produktänderungen.

**Acceptance criteria**
*AC-009a (FR-009) – E2E*
```gherkin
Given Anna hat /chat in zwei Browser-Kontexten offen und "#billing" zeigt in beiden Badge "2"
When sie "#billing" in Kontext A öffnet
Then zeigt Kontext B binnen 1 s kein Badge mehr für "#billing"
```
*AC-010a (FR-010) – E2E*
```gherkin
Given Jens schreibt in "#billing" "<@user:anna@bloopark.de> schau mal" und Annas Agent-Thread-Session wartet auf Freigabe
When Anna die Braucht-dich-Ansicht öffnet
Then sieht sie beide Einträge und ein Klick öffnet den jeweiligen Thread
```
*AC-026a (FR-026) – E2E*
```gherkin
Given Anna tippt "@je" im Composer von "#general"
Then erscheinen binnen 100 ms "Jens" und "Grok (Jens Holze)" mit Badge "Bot", und Enter fügt den markierten Eintrag als Chip ein
```
*AC-029a (FR-029) – E2E*
```gherkin
Given Jens teilt Session S1 mit Anna (EDIT)
When Anna S1 öffnet
Then ist im ChatHeader der Session-Chat erreichbar und zeigt die bisherigen Nachrichten
```
*AC-078a (FR-078) – E2E*
```gherkin
Given reducedMotion "no-preference"
When eine neue Nachricht erscheint
Then hat das Nachrichten-Element beim Mount eine Transform-Animation mit Dauer 180 ms ± 20 ms (gemessen über element.getAnimations() bzw. data-motion-Attribute)
```
*AC-079a (FR-079) – E2E*
```gherkin
Given Playwright emuliert reducedMotion "reduce"
When eine neue Nachricht erscheint und der Status wechselt
Then läuft keine transform-Animation und keine Animation dauert länger als 120 ms
```
*AC-080a (FR-080) – E2E*
```gherkin
Given Jens hat im Thread nach oben gescrollt
When der Agent weiter streamt
Then bleibt die Scroll-Position stabil (± 2 px) und eine Pille "Neue Nachrichten" erscheint
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: siehe Dateien; Testnamen enthalten AC-IDs
- Run: `uv run --no-sync pytest tests/e2e_ui/team_chat -v --tracing=retain-on-failure -k "unread or mentions or needs_you or session_chat or motion or a11y"` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-121: R1-Playwright: Agenten und Bots (Folgen, Steuerung, Agentenkonten-UI, Bot-Polling über MCP)
**Release:** R1   **Phase / wave:** Test / 11   **Parallel:** [P] ja   **Depends on:** T-114, T-115, T-116, T-107, T-109   **Rolle:** test_author · tester (Gate) · reviewer   **Aufwand:** 6 h
**Implements:** NFR-023, NFR-024; ACs: AC-040a, AC-041a, AC-048b, AC-049a, AC-050a, AC-052a, AC-061a, AC-085a, AC-090a, AC-090b
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:test`, `agent-ready`

**Context**
E2E für Agent-Folgen, Steuerung, Session öffnen, Agentenkonten-UI. Bot-Pfad: Test legt Agentenkonto an, ruft `/mcp` `chat_unread`/`chat_post` mit dem Token direkt (simuliert die Grok-Bot-Polling-Routine) und prüft die Anzeige „Grok (Jens Holze)“ im Browser.

**Scope**
- `tests/e2e_ui/team_chat/test_agent_follow.py`, `test_agent_controls.py`, `test_agent_accounts_ui.py`, `test_bot_polling.py`, `test_agent_steps.py` (new; Schritte, Live-Ansage, Reduced Motion)
- Out of scope / nicht anfassen: Kein Webhook-Empfänger (R1.1).

**Acceptance criteria**
*AC-040a (FR-040) – E2E*
```gherkin
Given der Agent-Thread ist active und die Session läuft
When Anna im Thread "bitte auch Linter" antwortet
Then erhält die Session die Nachricht "[Anna] bitte auch Linter" und die Antwort trägt keine Markierung "nicht an Agent"
```
*AC-041a (FR-041) – E2E*
```gherkin
Given Bob hat READ auf der Session
When Bob im aktiven Agent-Thread antwortet
Then wird seine Antwort gespeichert, nicht weitergeleitet und mit "nicht an Agent" markiert
```
*AC-048b (FR-048, FR-089) – E2E*
```gherkin
Given der Agent arbeitet die Todos "Tests lesen", "Fix schreiben", "Tests ausführen", "PR öffnen" ab und steht bei "Tests ausführen"
When Anna die Schritte aufklappt
Then zeigen "Tests lesen" und "Fix schreiben" ein Häkchen mit Dauer (z. B. "3.4s") in Monospace, "Tests ausführen" einen drehenden Bogen mit Shimmer-Label und "PR öffnen" einen Punkt
```
*AC-049a (FR-049) – E2E*
```gherkin
Given die Session läuft
When Anna "/interrupt" sendet
Then wird ein Event type "interrupt" gesendet und der Chip wechselt binnen 1 s auf "fertig"
```
*AC-050a (FR-050) – E2E*
```gherkin
Given Bob hat READ
When Bob "/retry" eingibt
Then zeigt der Composer "Keine Berechtigung: nur Aufrufer und Bearbeiter" und es wird nichts gesendet
```
*AC-052a (FR-052) – E2E*
```gherkin
When Jens "Session öffnen" klickt
Then navigiert die Web-App zu /c/{session_id} und zeigt das Transkript lesend
```
*AC-061a (FR-061) – E2E*
```gherkin
When Jens in den Einstellungen ein Agentenkonto "Grok", Handle "grok-jens", Webhook "https://bot.example.com/hook" anlegt
Then werden ein Token mit Präfix "ogc_" und ein Secret genau einmal angezeigt und nach Schließen des Dialogs nicht mehr
```
*AC-085a (FR-085) – E2E*
```gherkin
Given Anna hat in "#billing" einen Agent-Thread gestartet und Jens hat READ
When Jens die Hauptsidebar ansieht
Then erscheint die Agent-Thread-Session dort nicht, wohl aber im Thread unter "Session öffnen"
```
*AC-090a (FR-090) – E2E*
```gherkin
Given Anna nutzt einen Screenreader und die Schritte sind aufgeklappt
When der Agent von Schritt 2 zu Schritt 3 "Tests ausführen" wechselt
Then enthält die role="status"-Region nach höchstens 600 ms "Tests ausführen, Schritt 3 von 4", die Zeile hat aria-current="step"
And nach dem letzten Schritt meldet die polite-Region "Lauf abgeschlossen"
```
*AC-090b (FR-090) – E2E*
```gherkin
Given der Browser emuliert prefers-reduced-motion: reduce
When ein Schritt von active zu done wechselt
Then erscheint das Häkchen ohne Skalierung (nur Opacity ≤ 120 ms), der Bogen dreht sich nicht und das aktive Label hat keinen Shimmer
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: siehe Dateien
- Run: `uv run --no-sync pytest tests/e2e_ui/team_chat -v --tracing=retain-on-failure -k "agent_follow or agent_controls or agent_accounts or bot_polling or agent_steps"` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-122: UX-Agent-Review-Pass über die gesamte Chat-UI
**Release:** R1   **Phase / wave:** Polish / 11   **Parallel:** [P] ja   **Depends on:** T-114, T-115, T-116, T-118   **Rolle:** ux · reviewer   **Aufwand:** 4 h
**Implements:** NFR-027, NFR-006, NFR-007, NFR-010, NFR-030
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:chore`, `agent-ready`

**Context**
Prüfung gegen `docs/ux/chat-review-checklist.md` (aus plugin87/ux-ui-agent-skills: motion-choreography, icon-system, a11y; nur Doku, keine Hooks). Befunde als Liste mit Screenshot-Pfaden aus Playwright; Fixes gehen an die UI-Tasks zurück. Performance-Trace (Chrome DevTools via Playwright) für NFR-006.

**Scope**
- `docs/ux/reviews/2026-10-team-chat.md` (new)
- Out of scope / nicht anfassen: Keine Code-Änderungen durch den UX-Agenten selbst.

**Acceptance criteria**
```gherkin
Given alle R1-UI-Tasks sind gemergt
When der UX-Agent die Checkliste abarbeitet
Then hat jeder Punkt den Status ok oder einen verlinkten Befund, und kein Befund mit Schwere hoch ist offen
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Run: `uv run --no-sync pytest tests/e2e_ui/team_chat -v --tracing=retain-on-failure -k "motion or a11y"` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-123: Release-Runbook R1: Backup, Neuinstallation aus dem Fork, Flag an, Smoke-Test, Rollback
**Release:** R1   **Phase / wave:** Release / 12   **Parallel:** nein   **Depends on:** T-120, T-121, T-122   **Rolle:** tester (Gate) · reviewer   **Aufwand:** 3 h
**Implements:** NFR-025, NFR-022
**Labels:** `spec:omnigent-chat`, `release:R1`, `type:chore`, `agent-ready`

**Context**
Server wird vor dem Launch aus dem Fork neu installiert (0.17 → 0.18, R-4). Probe-Upgrade auf einer Kopie der Produktivdaten am Do 22.10. Laufende Omnigent-Sessions werden nicht neu gestartet (siehe §Arbeitsweise). Jens führt die Produktiv-Schritte selbst aus.

**Scope**
- `docs/runbooks/team-chat-r1.md` (new)
- Out of scope / nicht anfassen: Kein Deployment durch Agenten.

**Acceptance criteria**
```gherkin
Given eine Kopie der Produktiv-DB
When das Runbook Schritt für Schritt ausgeführt wird
Then läuft `alembic upgrade head` fehlerfrei, `team_chat` ist an und der Smoke-Test (Post in #general, @agent-Thread, Freigabe) ist grün
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Run: `uv run --no-sync pytest tests/db -q` → grün
- Run: `uv run --no-sync pytest tests/e2e_ui/team_chat -v --tracing=retain-on-failure -k smoke` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

## R1.1 – kurz nach Launch (Q-18 entschieden 16.10.2026 23:25)

In R1 wecken sich Grok Bots über ihre eigene Polling-Routine (Intervall ≥ 5 min) per MCP `chat_unread` → lesen → `chat_post` → `chat_mark_read` (FR-088, T-107). Das sofortige Wecken per Webhook (FR-071…FR-073, NFR-019, ADR-0007) und die Klärung des Webhook-Formats (Q-14) folgen in R1.1. Tabellen `chat_agent_outbox` existieren bereits seit T-001 (keine zweite Migration nötig).

### T-108: Chat-Outbox `chat.mention` und Webhook-Dispatcher je Agentenkonto
**Release:** R1.1   **Phase / wave:** Story ST-7.1.2 (R1.1) / 13   **Parallel:** [P] ja   **Depends on:** T-123, Q-14 beantwortet   **Rolle:** coder_deepseek · reviewer (Security)   **Aufwand:** 8 h
**Implements:** FR-071, FR-072, NFR-019, NFR-016, NFR-014; ACs: AC-071a, AC-071b, AC-072a, AC-072b
**Labels:** `spec:omnigent-chat`, `release:R1.1`, `type:feature`, `agent-ready`

**Context**
ADR-0007. Insert in der Post-Transaktion bei Erwähnung eines Agentenkontos oder DM mit Agentenkonto (nicht durch Thread-Folgen, A-22). Dispatcher nach PR-#15-Muster (`omnigent/server/manager_webhook_dispatcher.py`): Ordnung je `account_id`, Backoff ≈ 5 s·2^n bis 15 min mit Jitter, 10 Versuche → `dead_letter`, Lease 60 s. Signatur über `omnigent/server/manager_webhook_signing.py:sign`, Secret je Konto abgeleitet. Payload ohne Nachrichtentext. Format unter Vorbehalt Q-14.

**Requirements (zitiert)**
- FR-071 (Should, R1.1): When a stored message mentions an Agentenkonto or is posted in a DM with an Agentenkonto, the Server shall insert one `chat.mention` outbox row per Agentenkonto in the same transaction, unless the Bot-Bremse is active or the author is that Agentenkonto.
- FR-072 (Should, R1.1): The Server shall deliver each outbox row as HTTPS POST with the PR #15 envelope fields and `X-Omnigent-Signature: v1=<hmac>` (via `manager_webhook_signing.sign` with the account secret), retry non-2xx with exponential backoff (≈ 5 s·2^n, max 15 min, jitter), mark `dead_letter` after 10 attempts, and keep order per Agentenkonto.

**Scope**
- `omnigent/server/chat_outbox.py`, `omnigent/server/chat_webhook_dispatcher.py` (new)
- `omnigent/server/app.py` (adapt: Dispatcher-Lifecycle)
- `tests/server/test_chat_webhooks.py` (new, lokaler HTTPS-Testempfänger)
- Out of scope / nicht anfassen: Manager-Webhook aus PR #15 nicht verändern; gemeinsame Helfer extrahieren statt kopieren.

**Acceptance criteria**
*AC-071a (FR-071)*
```gherkin
When Anna "<@agent_account:{grok-jens-id}> fass zusammen" in "#general" postet
Then existiert genau eine Outbox-Zeile chat.mention für grok-jens im selben Commit wie die Nachricht
```
*AC-071b (FR-071)*
```gherkin
When grok-jens sich selbst erwähnt
Then entsteht keine Outbox-Zeile
```
*AC-072a (FR-072)*
```gherkin
Given eine Outbox-Zeile für grok-jens und ein Test-Receiver auf localhost (Test-Ausnahme aktiv)
Then erhält der Receiver binnen 2 s einen POST mit event_type "chat.mention", gültiger Signatur v1 und data ohne Feld body
```
*AC-072b (FR-072)*
```gherkin
Given der Receiver antwortet 500
Then wird mit steigendem Abstand erneut zugestellt und nach 10 Versuchen ist die Zeile dead_letter
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: `tests/server/test_chat_webhooks.py` (Signatur prüfbar, Retry, Dead-Letter, Neustart mit Lease, kein Body)
- Run: `uv run --no-sync pytest tests/server/test_chat_webhooks.py tests/server -q -k manager_webhook` → grün
- Run: `just lint` → grün
- Run: `just typecheck-python` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde

### T-116b: Web: Webhook-Felder und Status „Webhook fehlerhaft“ / „Erneut zustellen“
**Release:** R1.1   **Phase / wave:** Story ST-7.1.2 (R1.1) / 13   **Parallel:** [P] ja   **Depends on:** T-108   **Rolle:** coder_grok · ux · reviewer   **Aufwand:** 3 h
**Implements:** FR-073; ACs: AC-073a
**Labels:** `spec:omnigent-chat`, `release:R1.1`, `type:feature`, `agent-ready`

**Context**
Ergänzt T-116 um Webhook-URL (HTTPS), Secret-Version und Dead-Letter-Status, sobald T-108 gemergt ist.

**Requirements (zitiert)**
- FR-073 (Should, R1.1): While an Agentenkonto has dead-lettered outbox rows, the Web-App shall show „Webhook fehlerhaft“ on the account in settings and in the member list, with an „Erneut zustellen“ action for the owner.

**Scope**
- `web/src/chat/settings/AgentAccountsSection.tsx` (adapt)
- `tests/e2e_ui/team_chat/test_agent_webhook_ui.py` (new, mit lokalem Webhook-Empfänger)

**Acceptance criteria**
*AC-073a (FR-073)*
```gherkin
Given grok-jens hat eine dead_letter-Zeile
Then zeigt Jens' Einstellungsseite "Webhook fehlerhaft" und "Erneut zustellen" setzt die Zeile auf pending
```

**Verification**
- Session-Start: `hindsight_diagnose` (Bank `coding-agent::omnigent`)
- Add tests: E2E mit Testempfänger
- Run: `pnpm --filter web run test` → grün
- Run: `pnpm --filter web run lint` → grün
- Run: `pnpm --filter web run type-check` → grün
- Run: `uv run --no-sync pytest tests/e2e_ui/team_chat -v --tracing=retain-on-failure -k webhook` → grün

**Definition of done**
- [ ] ACs laufen als automatisierte Tests grün (Testname enthält AC-ID)
- [ ] UX-Review-Notiz gegen `docs/ux/chat-review-checklist.md` im PR; E2E für die Story existiert oder ist in T-009/T-120/T-121 eingeplant
- [ ] Keine Änderungen außerhalb des Scopes; keine Nachrichtentexte in Logs/Frames
- [ ] Spec/Traceability aktualisiert, falls Verhalten abweicht (melden, nicht selbst entscheiden)
- [ ] Lokales Review (Claude Code + lokale Modelle) ohne offene Major-Befunde
## R2 – Epic-Skizze (keine Tasks, eigener Spec-Durchlauf vor Start)
| Epic | Stories | Inhalt | Abhängigkeiten |
|---|---|---|---|
| E-R2-1 GitHub-Diskussion im Chat | ST-5.2.1, ST-5.2.2 | PR-/Issue-Kommentare als Thread spiegeln, Antworten zurück | GitHub-App, `routes_resources.py` (POST …/resources/github/prs) |
| E-R2-2 /board-Kanban | ST-5.3.1 | Board-Zeilen als `author_kind=system`-Nachrichten, Statuswechsel im Channel | ADR-0001 (Systemnachrichten) |
| E-R2-3 Worker-Übersicht | ST-3.2.3 | alle laufenden Agent-Threads eines Teams mit Status | WS-Watch, FR-085-Label |
| E-R2-4 Private Channels | ST-6.1.2 | `visibility=private`, Einladungen | `chat_channels.visibility` existiert ab T-001 |
| E-R2-5 Serverseitige Queue | ST-6.2.1, ST-3.3.3 | Nachrichten an beschäftigte Agenten einreihen | Session-Events |
| E-R2-6 Aufräumen | – | `session_chat_messages` löschen, Edit/Reaktionen (aus #9), Dateien/Suche (aus #10), Aufbewahrung (Q-3) | T-102 |
| E-R2-7 Mehr-Instanz-Betrieb | ST-5.4.1, ST-5.4.2 | Bus statt In-Process (C-2) | – |

## Coverage
Automatisch geprüft: jede FR (R0/R1/R1.1), jede NFR und jede AC aus `04-requirements.md` v0.4 ist mindestens einem Task zugeordnet. Lücken: keine.

| FR | Prio | Rel | Tasks |
|---|---|---|---|
| FR-001 | Must | R0 | T-006 |
| FR-002 | Must | R0 | T-001 |
| FR-003 | Must | R0 | T-001, T-003 |
| FR-004 | Must | R0 | T-003 |
| FR-005 | Should | R0 | T-003 |
| FR-006 | Must | R0 | T-003 |
| FR-007 | Must | R1 | T-101 |
| FR-008 | Must | R1 | T-101 |
| FR-009 | Must | R1 | T-101 |
| FR-010 | Must | R1 | T-112 |
| FR-011 | Should | R1 | T-112 |
| FR-012 | Should | R1 | T-112 |
| FR-013 | Must | R0 | T-003 |
| FR-014 | Must | R0 | T-003 |
| FR-015 | Must | R0 | T-003 |
| FR-016 | Must | R0 | T-003 |
| FR-017 | Must | R1 | T-119 |
| FR-018 | Must | R0 | T-002 |
| FR-019 | Must | R0 | T-003 |
| FR-020 | Must | R0 | T-007 |
| FR-021 | Must | R0 | T-003 |
| FR-022 | Must | R0 | T-003 |
| FR-023 | Must | R0 | T-002 |
| FR-024 | Must | R0 | T-002 |
| FR-025 | Must | R0 | T-002 |
| FR-026 | Must | R1 | T-110 |
| FR-027 | Must | R0 | T-002 |
| FR-028 | Could | R1 | T-110 |
| FR-029 | Must | R1 | T-102 |
| FR-030 | Must | R1 | T-102 |
| FR-031 | Must | R1 | T-102 |
| FR-032 | Could | R1 | T-124 |
| FR-033 | Must | R0 | T-005 |
| FR-034 | Must | R0 | T-005 |
| FR-035 | Must | R0 | T-005 |
| FR-036 | Must | R1 | T-109 |
| FR-037 | Must | R0 | T-005 |
| FR-038 | Must | R0 | T-005 |
| FR-039 | Must | R1 | T-103 |
| FR-040 | Must | R1 | T-104 |
| FR-041 | Must | R1 | T-104 |
| FR-042 | Must | R1 | T-104 |
| FR-043 | Should | R1 | T-104 |
| FR-044 | Must | R0 | T-008 |
| FR-045 | Must | R0 | T-008 |
| FR-046 | Must | R0 | T-005 |
| FR-047 | Must | R0 | T-008 |
| FR-048 | Should | R1 | T-115 |
| FR-049 | Must | R1 | T-114 |
| FR-050 | Must | R1 | T-114 |
| FR-051 | Must | R1 | T-114 |
| FR-052 | Must | R1 | T-114 |
| FR-053 | Should | R1 | T-114 |
| FR-054 | Should | R1 | T-104 |
| FR-055 | Must | R0 | T-008 |
| FR-056 | Must | R0 | T-008 |
| FR-057 | Must | R0 | T-008 |
| FR-058 | Must | R0 | T-008 |
| FR-059 | Should | R1 | T-115 |
| FR-060 | Should | R1 | T-115 |
| FR-061 | Must | R1 | T-105 |
| FR-062 | Must | R1 | T-116 |
| FR-063 | Should | R1 | T-105 |
| FR-064 | Must | R1 | T-105 |
| FR-065 | Must | R1 | T-105 |
| FR-066 | Must | R1 | T-105 |
| FR-067 | Must | R1 | T-106 |
| FR-068 | Must | R1 | T-106 |
| FR-069 | Must | R1 | T-107 |
| FR-070 | Must | R1 | T-107 |
| FR-071 | Should | R1.1 | T-108 |
| FR-072 | Should | R1.1 | T-108 |
| FR-073 | Should | R1.1 | T-116b |
| FR-074 | Must | R1 | T-109 |
| FR-075 | Must | R1 | T-109 |
| FR-076 | Should | R1 | T-116 |
| FR-077 | Must | R1 | T-109 |
| FR-078 | Must | R1 | T-117 |
| FR-079 | Must | R1 | T-117 |
| FR-080 | Must | R1 | T-118 |
| FR-081 | Could | R1 | T-118 |
| FR-082 | Must | R0 | T-006 |
| FR-083 | Should | R1 | T-111 |
| FR-084 | Must | R0 | T-007 |
| FR-085 | Must | R1 | T-113 |
| FR-086 | Should | R1 | T-103 |
| FR-087 | Must | R0 | T-005 |
| FR-088 | Must | R1 | T-107 |
| FR-089 | Should | R1 | T-115 |
| FR-090 | Must | R1 | T-115 |

| NFR | Tasks |
|---|---|
| NFR-001 | T-003, T-119 |
| NFR-002 | T-007 |
| NFR-003 | T-002, T-119 |
| NFR-004 | T-005 |
| NFR-005 | T-008 |
| NFR-006 | T-008, T-117, T-118, T-122 |
| NFR-007 | T-115, T-117, T-122 |
| NFR-008 | T-115, T-117 |
| NFR-009 | T-007, T-009, T-110, T-115, T-120 |
| NFR-010 | T-007, T-117, T-122 |
| NFR-011 | T-007, T-113 |
| NFR-012 | T-106 |
| NFR-013 | T-105, T-116 |
| NFR-014 | T-003, T-108, T-119 |
| NFR-015 | T-007 |
| NFR-016 | T-105, T-108 |
| NFR-017 | T-106, T-119 |
| NFR-018 | T-006, T-111 |
| NFR-019 | T-108 |
| NFR-020 | T-005 |
| NFR-021 | T-006 |
| NFR-022 | T-000, T-001, T-102, T-123 |
| NFR-023 | T-004, T-009, T-120, T-121 |
| NFR-024 | T-004, T-009, T-120, T-121 |
| NFR-025 | T-001, T-123 |
| NFR-026 | T-119 |
| NFR-027 | T-000, T-122 |
| NFR-028 | T-005, T-105, T-119 |
| NFR-029 | T-109 |
| NFR-030 | T-007, T-114, T-122 |

AC → Task: jede AC ist im Abschnitt „Acceptance criteria“ des zuständigen Tasks wörtlich zitiert; E2E-markierte ACs zusätzlich in T-009 (R0), T-120 (R1 Menschen) oder T-121 (R1 Agenten/Bots).

## Playwright-Struktur (Referenz OKR PM)

> **Platzhalter – Jens trägt das Referenzmuster aus dem OKR-PM-Projekt nach.** Bis dahin gilt die Struktur aus T-004: `tests/e2e_ui/team_chat/` mit Fixture `team_chat_server`, `page_as(email)`, Seed-Helfern per REST, `data-testid`-Konstanten in `web/src/chat/testids.ts`, Testnamen mit AC-ID, Mock-LLM, `--tracing=retain-on-failure`. Nach dem Nachtrag: T-004, T-009, T-120, T-121 an das Muster anpassen.

Hinweis (Stand 16.10. 23:30): Die Rohantwort aus der OKR-PM-Session liegt in `ref-okr-pm-playwright.md`. Dort gibt es **kein** Playwright; übertragbar sind laut Datei: stabile `data-testid` mit Feature-Präfix (Vorschlag hier: `chat-`), Seeding im Test statt Demo-Daten, klare Schichten (Vitest · pytest · Playwright), eindeutiges PASS-Kriterium, „Not run“ ist kein Pass für den `tester`. Die verbindliche Fassung trägt Jens hier ein.

<!-- OKR-PM-Referenz hier einfügen -->

## Vorschläge für AGENTS.md (Abschnitt „Team Chat“, Umsetzung in T-000)
- Feature-Flag: `OMNIGENT_FEATURES=team_chat`; jeder neue `/v1/chat/*`-Endpoint prüft das Flag vor jedem Store-Zugriff (404).
- Keine Nachrichtentexte in WS-Frames, SSE, Webhooks, Logs, Telemetrie, Audit (NFR-014). Frames tragen nur IDs.
- Chat-Code lebt in `omnigent/server/chat_*.py`, `omnigent/server/routes/chat.py`, `omnigent/stores/chat_store/`, `web/src/chat/`. Nicht in `web/src/store/chatStore.ts` (Agent-Chat, anderes Feature).
- Rechte immer über `omnigent/server/chat_access.py`; Agentenkonten nie mehr als ihr Owner.
- Neue Migrationen: genau ein Alembic-Head (`tests/db/test_migration_connections.py::test_single_alembic_head`), SQLite/Postgres/CRDB (`just crdb-test`).
- Playwright: Tests in `tests/e2e_ui/team_chat/`, Fixture `team_chat_server`, Testname enthält AC-ID; neue UI-Testbereiche in `feature-map/README.md` eintragen.
- UI-PRs: UX-Review-Notiz gegen `docs/ux/chat-review-checklist.md`; Animationen nur über Tokens in `web/src/chat/motion.ts`; Icons nur `lucide-react`.
- Neue Sessions in `omnigent-src`: Hindsight-Bank `coding-agent::omnigent`, erster Schritt `hindsight_diagnose`. Laufende Sessions nicht neu starten.
- Reviews nur lokal (Claude Code + lokale Modelle).

## Mapping bestehender Issues (Vorschlag, nichts wird ohne OK von Jens geändert)
| Issue | Inhalt | Vorschlag | abgelöst durch |
|---|---|---|---|
| #3 | Menschen-Chat-Spalte in der Session | nach R1-Launch schließen als „abgelöst“ | T-102, T-113 (Session-Chat als Channel) |
| #7 | Session-Chat nutzt ChatComposer (`isComposerSendKey`, „No messages yet.“) | schließen nach T-007/T-113 | T-007, T-113, NFR-011 |
| #8 | Serverseitiges Unread | schließen nach T-101/T-111 | T-101, T-111 |
| #9 | Reply/Edit/React | Threads abgedeckt (T-002/T-007); Edit/Reaktionen in neues Ticket „Chat: Bearbeiten und Reaktionen (später)“; #9 schließen | T-002, T-007; Rest E-R2-6 |
| #10 | Mention/Datei/Suche | Mentions abgedeckt (T-002/T-110); Dateien/Suche in neues Ticket; #10 schließen | T-002, T-110; Rest E-R2-6 |
| #12 | Grok-Bot-Teammate-Prototyp | offen lassen bis Q-15 entschieden | Agentenkonten T-105–T-107 überschneiden sich |
| #16 | React 19 | **Voraussetzung** vor T-006 (Entscheidung Jens); Abhängigkeit D-1: lokaler Branch `feat/react-19` auf Jens' Mac, Session `6c30fca5…` | – |
| #2 | Nightly-E2E rot | nicht Teil dieser Spec; blockiert die Chat-Suite nicht, weil `team_chat_server` eigenständig ist | – |

## Changelog
- v0.3 (2026-10-16) T-115 auf interior.dev TaskSteps ausgerichtet (FR-089/090, Q-20: kein Copy-in vor Lizenzklärung); T-121 um `test_agent_steps.py`; Q-19 gelöst → D-1.
- v0.2 (2026-10-16) Q-18 entschieden: T-108 (Outbox/Webhook) und neue T-116b nach R1.1; T-107 um Polling-Weckweg `chat_unread` (FR-088) erweitert; React 19 (#16) als Abhängigkeit von T-006/T-007/T-008; Abschnitt „Arbeitsweise“ (laufende Sessions bleiben, Hindsight `coding-agent::omnigent` + `hindsight_diagnose`); FR-085 nach T-113.
- v0.1 (2026-10-16) Erste Fassung (intern, nicht veröffentlicht).