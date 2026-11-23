# CI-Status-Ansicht – Requirements Spec (Quick Path, Stufe 4)

Version: 0.2 · Datum: 2026-10-19 · Status: Entwurf · Owner: Jens Holze · Autor: Spec
Vorstufe: `intake.md` v0.2 · Visuelle Referenz: `mockups/` (siehe §5.1) · Design-Optionen: `05-design-notes.md` · Nächster Schritt: spec-quality-review

**Schreibkonventionen dieses Dokuments**
- Anforderungen in deutscher EARS-Form, durchgängig:
  - Ubiquitär: „Omnigent soll …"
  - Ereignis: „Wenn <Auslöser>, soll Omnigent …"
  - Zustand: „Während <Zustand>, soll Omnigent …"
  - Optional: „Wo <Feature aktiv ist>, soll Omnigent …"
  - Unerwünscht: „Falls <Fehlerfall>, dann soll Omnigent …"
- Akzeptanzkriterien in Gherkin mit deutschen Schlüsselwörtern (Angenommen / Wenn / Dann / Und).
- IDs sind stabil und werden nie wiederverwendet (G-, P-, ST-, FR-, NFR-, AC-, C-, A-, Q-, R-).
- Status je Anforderung: `exists` (vorhanden, Pfad genannt) · `adapt` (vorhanden, Änderung genannt) · `new`.
- Priorität: MoSCoW. Unsicherheit: `[ANNAHME A-n]`, `[OFFEN Q-n]`. Pfade beziehen sich auf das Repo `github.com/jholze/omnigent`, Stand `4b212fa` (main, „Merge pull request #34").
- Zahlen in den Mockups sind Beispieldaten (so steht es auch in den Mockups) und keine gemessenen Werte.

---

## 1. Kontext & Problem

Omnigent (Fork `github.com/jholze/omnigent`; Python-Server unter `omnigent/`, React-Web-UI unter `web/`) wird von Jens und von Agenten genutzt, die aus ihren Sessions heraus per `gh` Pull Requests pushen. Die CI läuft auf GitHub Actions, ausschließlich auf zwei self-hosted macOS-Runnern auf Jens' Mac (`actions-runner-omnigent`, `actions-runner-omnigent-2`; Verzeichnisse `~/actions-runner-omnigent*/`).

Pflicht-Checks (blockieren Merge):
- `Web (lint, typecheck, vitest, build)`
- `Python lint (ruff, pyrefly)`
- `Pytest (SQLite)`

Nicht blockierend: `E2E UI (Playwright, non-blocking)` darf rot sein.

**Anlass:** In der Nacht zum 18.10. lief der Mac mit Load ≈ 20 durch parallele Pytest-Läufe (PR #28–#30), während in Omnigent keine Session aktiv wirkte. Niemand konnte erkennen, warum der Mac ausgelastet war.

**Problem:** Omnigent zeigt CI-Status heute nur pro Session im PR-Tab (`web/src/shell/PullRequestPanel.tsx`) als Zähler „passing / pending / failing". Es gibt keine sessionübergreifende Sicht, keine Runner-Sicht, keine Warteschlange, keine Laufzeit/ETA und keinen Zusammenhang zwischen Mac-Last und CI-Jobs.

**Anforderung von Jens:** „geile UX". Gemeint ist eine echte GUI, keine Logliste. Die UI-Umsetzung läuft über **ux-builder** und wird von **ux-reviewer** abgenommen (Teamregel vom 18.10.).

### 1.1 Bestandsaufnahme im Code (read-only, `~/dev/omnigent-deploy` @ `4b212fa`)

| Bereich | Befund | Status für dieses Feature |
|---|---|---|
| Check-Status je PR | `omnigent/runner/github_resource.py`: `_PR_VIEW_FIELDS` holt `statusCheckRollup` über `gh pr view --json`. `_classify_check()` ordnet jeden Check in passing/failing/pending ein. `_summarize_checks()` liefert `{passing, failing, pending, total, runs[{name,bucket,url}]}` (Obergrenze 300 Runs). | adapt: Es fehlen queued vs. in_progress, `startedAt`/`completedAt`, Runner-Name und ein Pflicht-Flag. |
| Session-Route GitHub | Runner: `omnigent/runner/resource_routes.py:1317` `GET /v1/sessions/{id}/resources/github` → `pr_resource.pr_info`. Server-Proxy: `omnigent/server/routes/sessions/routes_resources.py:2924` (`get_session_github`). Dazu `…/github/changes`, `…/github/diff`, `POST …/github/prs`, `…/github/preferences`. | exists (pro Session). Eine sessionübergreifende Route fehlt → new. |
| MCP-Tool `session_github` | `omnigent/server/mcp_extensions.py:140` (Schema), `:704` `_session_github` mit den Actions info/changes/diff/attach_pr/preferences. Ruft die Session-Route oben auf. | adapt (Check-Details) bzw. neues Tool (siehe Q-13) |
| Session ↔ PR | `omnigent/runner/pr_observer.py` beobachtet Tool-Aufrufe (`gh pr create` u. a. über `omnigent/runner/git_providers/github_observer.py`) und schreibt in `SessionPrRegistry` (`omnigent/runner/session_prs.py`): Beziehungen `created/worked_on/attached/inferred`, gespeichert hostlokal als JSON unter `data_dir()/github/session-prs/<sha256(session_id)>.json`. | exists. Das ist die Grundlage für „welche Session hat gepusht“. |
| `git_branch` an Session | vorhanden: `omnigent/db/db_models.py:806`, `omnigent/server/schemas.py:2152`. Laut Docstring wird das Feld **nur** bei einer vom Server angelegten Worktree oder im Bind-Modus gesetzt (PR #34), sonst `None`. | exists, für die Zuordnung aber unzuverlässig (Branches, die Agenten selbst anlegen, fehlen). Nur als Fallback geeignet. |
| PR-Panel-UI | `web/src/shell/PullRequestPanel.tsx` (Check-Pills je Bucket mit Hover-Liste, im `WorkspacePanel.tsx:1539`). `web/src/hooks/usePullRequests.ts` pollt alle 5 s (`PULL_REQUEST_POLL_MS`), solange Checks pending sind; `staleTime` 30 s; kein Polling im Hintergrund-Tab. | exists (pro Session). Muster und Typen (`PullRequestChecks`) sind wiederverwendbar → adapt. |
| Sidebar | `web/src/shell/Sidebar.tsx`, `web/src/hooks/useSidebarData.tsx`, `web/src/lib/sidebarData.ts` | adapt (Indikator + Session-Badges) |
| Live-Updates | `web/src/lib/sessionUpdatesSocket.ts`: WebSocket `WS /v1/sessions/updates` mit snapshot/changed/removed/hosts_changed/projects_changed/heartbeat (Heartbeat 30 s). Laut Kommentar im PR-Panel gibt es zusätzlich ein „git-activity SSE signal“. | adapt (einen CI-Frame-Typ ergänzen) oder Polling – siehe Q-1 |
| Status-UI-Bausteine | `web/src/components/SessionStateBadge.tsx`, `RunningDot.tsx`, `components/ui/{badge,progress,tooltip,hover-card,tabs,collapsible,spinner,scroll-area}.tsx` | adapt (Basis für die Badges in Nr. 5) |
| Panels/Resize | `web/src/hooks/useResizablePanel.ts`, `useResizableInlinePanel.ts`, `shell/WorkspacePanel.tsx` | exists (Infrastruktur für das rechte Panel) |
| GitHub-Auth | `omnigent/server/github_app.py` + `github_app_client.py` + `routes/connections_github.py` (GitHub-App-User-Token für Sandboxes). `github_resource._account_token_for()` holt das `gh`-Konto-Token aus Config/Keyring. | exists. Kein neuer Token-Speicher nötig [ANNAHME A-9]. |
| GitHub Actions API / Webhooks | Kein Aufruf von `actions/runs`, `actions/jobs` oder `actions/runners` gefunden. Kein Webhook-Empfänger für GitHub (nur `routes_manager_webhook.py` = Outbox für Manager-Webhooks). `omnigent/policies/builtins/github.py:427` kennt `rerun_workflow_run` nur als Policy-Klassifikation. | new |
| Host-Last | `psutil` ist Abhängigkeit und wird in `omnigent/host/connect.py:31` für Kindprozesse genutzt. `omnigent/server/performance_metrics.py` misst nur Server-Requests. Eine CPU/RAM-Meldung des Hosts wurde nicht gefunden. | new |
| Feature-Flag Team-Chat | `omnigent/server/feature_flags.py:30` `TEAM_CHAT = "team_chat"` | exists. Bleibt aus (C-6). |
| Web-Stack | React 19.3.1, Tailwind CSS 4.3.1 (`@tailwindcss/vite`), shadcn CLI `0.0.0-beta.9d3f70e54` mit `web/components.json` (style `radix-nova`, Aliase `@/components`, `@/components/ui`, CSS-Variablen in `src/index.css`), radix-ui 1.6.3, lucide-react, tw-animate-css 1.4.0, Storybook-Stories (`*.stories.tsx`). **Motion (`motion`/`framer-motion`) ist nicht installiert.** | Grundlage für C-11/NFR-020 |

## 2. Ziele & Erfolgsmetriken

| ID | Ziel | Metrik / Zielwert |
|---|---|---|
| G-1 | Jens beantwortet „Was läuft gerade in der CI und warum ist der Mac ausgelastet?“ ohne Terminal und ohne GitHub-Website. | Zeit vom geöffneten Omnigent bis zur richtigen Antwort **≤ 10 s**, höchstens 2 Klicks, in 5 von 5 Testszenarien (moderierter Test mit Jens; Szenario A nachgestellt nach dem 18.10.: 2 Pytest-Läufe parallel, keine aktive Session). |
| G-2 | Der angezeigte Status ist aktuell. | Statuswechsel auf GitHub → Anzeige in Omnigent: **p95 ≤ 30 s** [ANNAHME A-1]. Veraltete Daten sind in 100 % der Fälle als veraltet gekennzeichnet. |
| G-3 | Jeder CI-Lauf ist seinem Ursprung zugeordnet. | 100 % der PRs, die per `gh pr create` aus einer Omnigent-Session entstanden sind, zeigen die verlinkte Session. 100 % der übrigen Läufe sind als „ohne Session“ bzw. „ohne PR“ sichtbar, keiner wird ausgeblendet. |
| G-4 | Merge-relevanter Status ist eindeutig. | In einem Test mit allen 5 Badge-Zuständen ordnet Jens jeden Zustand fehlerfrei „blockiert Merge / blockiert nicht“ zu (0 Fehlinterpretationen). |
| G-5 | Die Funktion verursacht keine Kosten und keine zusätzliche CI-Last. | 0 Minuten auf GitHub-hosted Runnern, 0 € GitHub-Kosten, 0 durch das Feature ausgelöste Workflow-Läufe. |
| G-6 | Lead-Agent (PM HUB) und Sub-Agenten fragen den CI-Status ohne UI ab. | Status eines PRs inkl. Pflicht-Check-Ergebnis mit **1 API- bzw. MCP-Aufruf** abrufbar. |
| G-7 | Die UX erfüllt Jens' Anspruch („geile UX“). | Abnahme durch ux-reviewer gegen die Mockups ohne offene Blocker + ausdrückliches OK von Jens. |

## 3. Non-Goals (R1)

- Jobs neu starten oder abbrechen (rerun/cancel), Merge auslösen.
- Workflows bearbeiten. Die 91 deaktivierten Workflows werden nicht angefasst (auch nicht gelesen, um sie zu aktivieren).
- GitHub-hosted Runner (weder nutzen noch anzeigen).
- Benachrichtigungen außerhalb von Omnigent (Mail, Push, Slack …).
- Team-Chat-Karte (Nr. 4). Kommt in R2, nur wenn `team_chat` per Entscheidung eingeschaltet wird.
- Runner-Zeitleiste (Nr. 3). Kommt in R2 als optionale Ansicht.
- CI-Throttling oder Steuerung der Runner-Parallelität.
- Weitere Repositories außer `jholze/omnigent` [ANNAHME A-8].

## 4. Rollen

| ID | Rolle | Nutzung | Zugriff |
|---|---|---|---|
| P-1 | Jens Holze – Owner/Operator | Hauptnutzer der GUI: Überblick, Ursachensuche bei Mac-Last, Sprung zu Session/Log | Vollzugriff (Owner) |
| P-2 | PM HUB / Lead-Agent | Liest den CI-Status über API/MCP, entscheidet über nächste Schritte. Merge nur nach grüner Pflicht-CI (C-4). | Lesend über API/MCP. Genauer Umfang [OFFEN Q-4] |
| P-3 | Sub-Agenten (z. B. omnigent-coder) | Pushen PRs aus ihrer Session; prüfen den Status ihres eigenen PRs | Lesend, mindestens eigene Session [OFFEN Q-4] |
| P-4 | Spätere Teammitglieder (z. B. Ranga) | Betrachter | [OFFEN Q-4] – Vorschlag: in R1 nicht freigeschaltet |

### 4.1 Story-Map-lite (jede Rolle einmal durchlaufen)

**P-1 Jens:** Omnigent öffnen → Sidebar-Indikator „CI 3 aktiv · 2 Runner · 2 wartend“ sehen (ST-1.1) → Panel öffnen, PRs nach Läuft/Wartet/Fertig sehen (ST-1.2) → im Runner-Abschnitt sehen, dass beide Runner Pytest für #28 und #30 fahren, und die Mac-Last ablesen (ST-4.1, ST-4.2) → Warteschlange prüfen (ST-4.3) → auf #30 klicken und in der pushenden Session landen (ST-1.3) → Karte aufklappen, Pytest-Log öffnen (ST-2.2). Session-Liste und PR-Header zeigen jederzeit Badges (ST-3.1).

**P-2 Lead-Agent:** Sub-Agent meldet Push → Lead fragt per MCP den CI-Status von PR #30 ab (ST-5.1) → wartet bis „Alle grün“ bzw. „Rot · nicht blockierend“ → meldet an Jens. Der Merge folgt der bestehenden Regel und ist nicht Teil dieses Features. (R2: Lead postet die Live-Karte in den Team-Chat und reagiert auf Rot, ST-7.1.)

**P-3 Sub-Agent:** führt `git push` / `gh pr create` aus → in seinem Transkript erscheint die CI-Karte für Menschen (ST-2.1) → er selbst fragt den Status über das MCP-Tool seiner Session ab (ST-5.2).

**P-4 Betrachter (später):** öffnet das Panel lesend (ST-6.2; in R1 nur als Option, siehe Q-4).

| Aktivität | Stories | Rollen |
|---|---|---|
| A1 Überblick verschaffen | ST-1.1 Indikator sehen · ST-1.2 alle PRs gruppiert sehen · ST-1.3 zu Session/GitHub springen | P-1 (P-4) |
| A2 PR in der pushenden Session verfolgen | ST-2.1 Karte unter dem Push sehen · ST-2.2 Details/Logs öffnen | P-1, P-3 |
| A3 Status kompakt erkennen | ST-3.1 Badges in Session-Liste und PR-Header | P-1 |
| A4 Runner & Mac-Last verstehen | ST-4.1 Runner-Belegung · ST-4.2 Mac-Last · ST-4.3 Warteschlange | P-1 |
| A5 Programmatisch abfragen | ST-5.1 Lead fragt CI-Status ab · ST-5.2 Sub-Agent fragt eigenen PR ab | P-2, P-3 |
| A6 Aktualität & Fehlerzustände | ST-6.1 dem Stand vertrauen können · ST-6.2 Zugriff passend zur Rolle | alle |
| A7 (R2) Team & Historie | ST-7.1 Live-Karte im Team-Chat · ST-7.2 Runner-Zeitleiste | P-1, P-2 |

## 5. Scope nach Release

| Release | Outcome | Stories | Mockup |
|---|---|---|---|
| R1 | Jens sieht sessionübergreifend, was in der CI läuft, auf welchem Runner, warum der Mac ausgelastet ist und welche Session den Lauf ausgelöst hat. Agenten fragen den Status per API/MCP ab. | ST-1.1–1.3, ST-2.1–2.2, ST-3.1, ST-4.1–4.3, ST-5.1–5.2, ST-6.1–6.2 | Nr. 1 `sidebar-panel`, Nr. 2 `session-card`, Nr. 5 `compact-badge` (gemeinsame Komponente) |
| R2 | Das Team sieht CI live im Team-Chat; der Lead reagiert auf Rot. Optional eine Runner-Zeitleiste. | ST-7.1 (nur wenn `team_chat` aktiviert wird), ST-7.2 | Nr. 4 `chat-card-live`, Nr. 3 `runner-timeline` |

### 5.1 UX-Referenzen (visuelle Source of Truth)

Die Mockups sind für Layout, Zustände, Texte und Hierarchie verbindlich. Bei Widerspruch zwischen Mockup und Textanforderung gilt die Textanforderung. Der Widerspruch wird als Q-n erfasst.

| Nr. | Datei (HTML) | Screenshot | Release |
|---|---|---|---|
| 1 | `mockups/sidebar-panel.html` | `mockups/png/sidebar-panel.png` | R1 |
| 2 | `mockups/session-card.html` (1a eingeklappt, 1b ausgeklappt, 1c abgeschlossen) | `mockups/png/session-card.png` | R1 |
| 5 | `mockups/compact-badge.html` (Zustandsgalerie + Kontext Session-Liste/PR-Header) | `mockups/png/compact-badge.png` | R1 |
| 4 | `mockups/chat-card-live.html` | `mockups/png/chat-card-live.png` | R2 |
| 3 | `mockups/runner-timeline.html` | `mockups/png/runner-timeline.png` | R2 (optional) |
| – | `mockups/index.html`, gemeinsames CSS `mockups/ci.css`, Generator `mockups/_src/` | `mockups/png/index.png` | – |

**Komponentenquelle für Micro-Interactions:** https://www.interior.dev/ ist Jens' bevorzugte Quelle. Laut Website (abgerufen 2026-10-19): 54 freie React-Komponenten, MIT-Lizenz, React + Tailwind CSS + Motion, TypeScript, Installation je Komponente mit `npx shadcn@latest add https://www.interior.dev/r/<name>.json`, Quellcode landet im Repo unter `components/interior/`. Auf der Startseite gesehen: Loading Button, Hold to Confirm, Tooltip Group, Slider Detents, Segmented Control, Like Burst, Copy Button. Regeln dazu: C-11, NFR-020.

**Prozess:** Alle UI-Arbeiten laufen über ux-builder, die Abnahme macht ux-reviewer (C-7).

## 6. Funktionale Anforderungen

### Aktivität A1: Überblick verschaffen (Mockup Nr. 1)

| ID | Anforderung (EARS) | Story | Prio | Status | Notizen |
|---|---|---|---|---|---|
| FR-001 | Omnigent soll in der Sidebar einen globalen CI-Indikator anzeigen, der die Anzahl aktiver Workflow-Läufe („CI n aktiv“) sowie die Anzahl belegter Runner und wartender Jobs („2 Runner · 2 wartend“) enthält. | ST-1.1 | Must | adapt (`web/src/shell/Sidebar.tsx`) | Mockup Nr. 1, unten links |
| FR-002 | Während kein Workflow-Lauf aktiv ist und kein Job wartet, soll Omnigent den CI-Indikator im Ruhezustand mit Text und Icon anzeigen (z. B. „CI ruhig“). | ST-1.1 | Should | new | Text [ANNAHME A-15], fehlt im Mockup |
| FR-003 | Wenn Jens den CI-Indikator aktiviert (Klick oder Enter), soll Omnigent das CI-Panel auf der rechten Seite öffnen. | ST-1.1 | Must | new (Panel-Infra `web/src/hooks/useResizablePanel.ts` exists) | Panel-Ort [OFFEN Q-11] |
| FR-004 | Omnigent soll im Tab „Pull Requests“ des CI-Panels alle offenen PRs von `jholze/omnigent` mit mindestens einem Workflow-Lauf in den Gruppen „Läuft“, „Wartet“ und „Fertig“ anzeigen, jeweils mit Anzahl. | ST-1.2 | Must | new | Gruppierung nach FR-035 |
| FR-005 | Omnigent soll pro PR im Panel Nummer, Titel und je Check eine Check-Pill (LINT, WEB, PY, E2E) mit Zustand (grün/läuft/wartet/rot) und Text anzeigen. | ST-1.2 | Must | adapt (Typ `PullRequestChecks` in `web/src/hooks/usePullRequests.ts`) | Kurzlabel-Zuordnung über Konfiguration (NFR-018) |
| FR-006 | Omnigent soll pro laufendem PR die verstrichene Zeit des längsten laufenden Checks, dessen typische Dauer und eine ETA („~8 Min · ETA 14:36“) anzeigen. | ST-1.2 | Should | new | typische Dauer [ANNAHME A-4] |
| FR-007 | Wenn Jens den Filter „Nur meine“ aktiviert, soll Omnigent nur PRs anzeigen, die Sessions von Jens zugeordnet sind. | ST-1.2 | Could | new | Bedeutung [OFFEN Q-8] |
| FR-008 | Wenn Jens einen PR im Panel aktiviert, der einer Session zugeordnet ist, soll Omnigent diese Session öffnen und zur zugehörigen CI-Karte scrollen. | ST-1.3 | Must | new (Zuordnung exists: `omnigent/runner/session_prs.py`) | |
| FR-009 | Falls ein PR keiner Omnigent-Session zugeordnet ist, dann soll Omnigent den PR trotzdem im Panel anzeigen, mit „ohne Session“ kennzeichnen und beim Aktivieren den PR auf GitHub in einem neuen Tab öffnen. | ST-1.3 | Must | new | Kernfall 18.10. |
| FR-010 | Omnigent soll in der Gruppe „Fertig“ nur Läufe anzeigen, die innerhalb der letzten 2 Stunden abgeschlossen wurden. | ST-1.2 | Should | new | Fenster [ANNAHME A-3] |
| FR-011 | Omnigent soll im CI-Panel zusätzlich einen Tab „Runner“ mit den Inhalten aus A4 und einen Tab „Verlauf“ anbieten. | ST-1.2 | Could | new | Inhalt „Verlauf“ [OFFEN Q-15] |

#### Akzeptanzkriterien A1

**AC-001 (FR-001) – Indikator mit laufender CI**
```gherkin
Angenommen PR #30 und PR #28 haben je einen laufenden Workflow-Lauf auf actions-runner-omnigent bzw. actions-runner-omnigent-2
Und PR #29 hat 2 Jobs in der Warteschlange
Wenn Jens Omnigent öffnet
Dann zeigt die Sidebar "CI 3 aktiv" und "2 Runner · 2 wartend"
```
**AC-002 (FR-001) – Indikator bei fehlenden Daten**
```gherkin
Angenommen Omnigent hat seit dem Serverstart noch keine CI-Daten erfolgreich geladen
Wenn Jens Omnigent öffnet
Dann zeigt der Indikator "CI-Status lädt …" und keine Zahl
Und es wird nie "CI 0 aktiv" angezeigt, solange keine Daten vorliegen
```
**AC-003 (FR-002)**
```gherkin
Angenommen kein Workflow-Lauf ist queued oder in_progress
Wenn die Sidebar sichtbar ist
Dann zeigt der Indikator den Ruhezustand mit Icon und Text statt einer Zahl
```
**AC-004 (FR-003) – Panel öffnen**
```gherkin
Angenommen das CI-Panel ist geschlossen
Wenn Jens den Indikator anklickt oder ihn per Tab fokussiert und Enter drückt
Dann öffnet sich rechts das CI-Panel mit dem Tab "Pull Requests"
Und der Fokus liegt im Panel
```
**AC-005 (FR-003) – Panel bei schmalem Fenster**
```gherkin
Angenommen das Browserfenster ist 1024 px breit
Wenn Jens das CI-Panel öffnet
Dann bleibt der Chat-Composer bedienbar und das Panel lässt sich per Schließen-Button oder Esc schließen
```
**AC-006 (FR-004) – Gruppierung**
```gherkin
Angenommen #30 und #28 haben laufende Checks, #29 hat nur wartende Jobs, #34 ist vor 1 h abgeschlossen
Wenn Jens das CI-Panel öffnet
Dann stehen #30 und #28 unter "Läuft 2", #29 unter "Wartet 1", #34 unter "Fertig 1"
```
**AC-007 (FR-004) – keine offenen PRs**
```gherkin
Angenommen es gibt keinen offenen PR mit Workflow-Lauf
Wenn Jens das CI-Panel öffnet
Dann zeigt der Tab einen Leerzustand "Keine laufenden PR-Checks" und den Runner-Abschnitt weiterhin an
```
**AC-008 (FR-005) – Check-Pills**
```gherkin
Angenommen bei PR #30 sind "Python lint (ruff, pyrefly)" und "Web (lint, typecheck, vitest, build)" erfolgreich, "Pytest (SQLite)" läuft und "E2E UI (Playwright, non-blocking)" wartet
Wenn Jens das CI-Panel ansieht
Dann zeigt PR #30 die Pills LINT (grün), WEB (grün), PY (läuft), E2E (wartet), jede mit Icon und Label
```
**AC-009 (FR-005) – unbekannter Check**
```gherkin
Angenommen PR #31 hat zusätzlich einen Check "CodeQL", für den kein Kurzlabel konfiguriert ist
Wenn Jens das CI-Panel ansieht
Dann erscheint für "CodeQL" eine Pill mit gekürztem Originalnamen und dem vollen Namen im Tooltip
Und der Check ist als nicht blockierend gekennzeichnet, weil er nicht in der Pflicht-Liste steht
```
**AC-010 (FR-006)**
```gherkin
Angenommen "Pytest (SQLite)" von PR #30 läuft seit 11:04 Min und die typische Dauer beträgt 19 Min
Wenn Jens das Panel ansieht
Dann zeigt PR #30 "~8 Min" und eine ETA als Uhrzeit (Europe/Berlin)
Und liegt keine typische Dauer vor, steht dort "ETA unbekannt" statt einer Zahl
```
**AC-011 (FR-008) – Sprung in Session**
```gherkin
Angenommen PR #30 wurde von Session "remove session human chat" per gh pr create erstellt
Wenn Jens PR #30 im Panel anklickt
Dann öffnet Omnigent diese Session und scrollt zur CI-Karte von #30
```
**AC-012 (FR-008) – Session archiviert oder gelöscht**
```gherkin
Angenommen die Session, die PR #27 erstellt hat, ist archiviert
Wenn Jens PR #27 im Panel anklickt
Dann öffnet Omnigent die archivierte Session lesend
Und ist die Session gelöscht, verhält sich der Eintrag wie "ohne Session" (FR-009)
```
**AC-013 (FR-009) – PR ohne Session**
```gherkin
Angenommen PR #35 wurde direkt auf github.com erstellt und keiner Session zugeordnet
Wenn Jens das CI-Panel öffnet
Dann erscheint #35 mit dem Kennzeichen "ohne Session"
Und ein Klick öffnet https://github.com/jholze/omnigent/pull/35 in einem neuen Tab
```
**AC-014 (FR-009) – mehrdeutige Zuordnung**
```gherkin
Angenommen PR #30 ist in der Registry zwei Sessions zugeordnet (A: created, B: worked_on)
Wenn Jens PR #30 anklickt
Dann öffnet Omnigent Session A (Beziehung "created" hat Vorrang)
```
**AC-015 (FR-010)**
```gherkin
Angenommen #34 wurde vor 1 h abgeschlossen, #33 vor 3 h
Wenn Jens das Panel öffnet
Dann zeigt "Fertig" nur #34
```

### Aktivität A2: PR in der pushenden Session verfolgen (Mockup Nr. 2)

| ID | Anforderung (EARS) | Story | Prio | Status | Notizen |
|---|---|---|---|---|---|
| FR-020 | Wenn eine Session einen PR erstellt oder auf den Branch eines ihr zugeordneten offenen PRs pusht, soll Omnigent im Transkript dieser Session direkt unter der Nachricht mit dem Push eine CI-Statuskarte für diesen PR einfügen. | ST-2.1 | Must | adapt (`omnigent/runner/pr_observer.py`, `session_prs.py` exist; Karte im Transkript new) | Anker [OFFEN Q-6] |
| FR-021 | Omnigent soll die CI-Karte im eingeklappten Zustand mit PR-Titel, PR-Nummer, Branch, Kurz-SHA, Startzeit, einem segmentierten Balken (ein gleich breites Segment je Check, Füllung = Laufzeit / typische Dauer), einer Gesamtstatus-Pill, „n/m Pflicht-Checks grün“ und Restzeit anzeigen. | ST-2.1 | Must | new | Mockup 1a, 1c |
| FR-022 | Wenn Jens „Details“ auf der Karte aktiviert, soll Omnigent je Check eine Zeile mit Name, Kennzeichen „Pflicht“ bzw. „nicht blockierend“, Runner-Name, Laufzeit, typischer Dauer und Log-Link anzeigen. | ST-2.2 | Must | new | Mockup 1b |
| FR-023 | Omnigent soll im ausgeklappten Zustand der Karte Workflow-Datei, Laufnummer und einen Link „Auf GitHub“ anzeigen. | ST-2.2 | Should | new | |
| FR-024 | Wenn auf einen PR mit bestehender Karte ein neuer Commit gepusht wird, soll Omnigent die ältere Karte als „überholt durch <Kurz-SHA>“ kennzeichnen und für den neuen Lauf eine neue Karte einfügen. | ST-2.1 | Should | new | [ANNAHME A-16] |
| FR-025 | Wenn ein Workflow-Lauf abgeschlossen ist, soll Omnigent die Karte mit Gesamtdauer und Endzustand einfrieren (Mockup 1c) und nicht weiter aktualisieren. | ST-2.1 | Must | new | |
| FR-026 | Wo das Feature `team_chat` aktiviert ist, soll Omnigent auf der Karte die Aktion „In Team-Chat teilen“ anzeigen. | ST-7.1 | Won't (R1) | new | R2. In R1 ausgeblendet (C-6) |

#### Akzeptanzkriterien A2

**AC-020 (FR-020) – Karte nach Push**
```gherkin
Angenommen Session "remove session human chat" führt "git push -u origin HEAD" aus und danach "gh pr create" für Branch cursor/remove-session-human-chat-f216 (PR #30)
Wenn der PR in der SessionPrRegistry mit Beziehung "created" erfasst ist
Dann erscheint unter der Agenten-Nachricht mit dem Push die CI-Karte für #30 mit Commit e752ad1
```
**AC-021 (FR-020) – Push ohne Workflow-Lauf**
```gherkin
Angenommen eine Session pusht auf einen PR-Branch, für den nach 120 s noch kein Workflow-Lauf existiert
Wenn die Karte angezeigt wird
Dann zeigt sie "Noch kein CI-Lauf gestartet" mit Zeitstempel des Pushs statt leerer Balken
```
**AC-022 (FR-020) – Push ohne PR**
```gherkin
Angenommen eine Session pusht einen Branch, zu dem es keinen PR gibt
Wenn der Push abgeschlossen ist
Dann fügt Omnigent keine CI-Karte ein
```
**AC-023 (FR-021) – eingeklappt, laufend**
```gherkin
Angenommen PR #30: Lint 24 s grün, Web 2:03 grün, Pytest läuft 11:04 bei typisch ~19 Min, E2E ausstehend
Wenn die Karte eingeklappt angezeigt wird
Dann zeigt der Balken 4 gleich breite Segmente: 2 voll grün, Pytest zu ~58 % gefüllt und als laufend markiert, E2E leer
Und der Text lautet "2/3 Pflicht-Checks grün · Pytest läuft 11:04 von typ. ~19 Min"
```
**AC-024 (FR-021) – Laufzeit über typischer Dauer**
```gherkin
Angenommen Pytest läuft 25 Min bei typischer Dauer von 19 Min
Wenn die Karte angezeigt wird
Dann ist das Pytest-Segment voll und als "länger als üblich" gekennzeichnet (Icon + Text, nicht nur Farbe)
Und die Restzeit zeigt "überfällig" statt eines negativen Werts
```
**AC-025 (FR-022) – Details**
```gherkin
Angenommen die Karte von PR #30 ist eingeklappt
Wenn Jens "Details" aktiviert
Dann sieht er 4 Zeilen; "Pytest (SQLite)" zeigt "Pflicht", "actions-runner-omnigent", "11:04 / ~19:00" und einen Log-Link
Und "E2E UI (Playwright, non-blocking)" zeigt "nicht blockierend"
```
**AC-026 (FR-022) – Runner unbekannt**
```gherkin
Angenommen ein Check wartet noch auf einen Runner
Wenn Jens die Details öffnet
Dann zeigt die Zeile "wartet auf Runner" statt eines Runner-Namens und keinen Log-Link
```
**AC-027 (FR-025) – abgeschlossen mit E2E rot**
```gherkin
Angenommen PR #34: alle 3 Pflicht-Checks grün, E2E nach 4:12 rot
Wenn der Lauf abgeschlossen ist
Dann zeigt die Karte "Pflicht grün", "E2E rot · nicht blockierend", "3/3 Pflicht-Checks grün · E2E rot, blockiert nicht" und die Gesamtdauer
```
**AC-028 (FR-025) – abgebrochener Lauf**
```gherkin
Angenommen der Lauf von PR #29 wurde auf GitHub abgebrochen (conclusion cancelled)
Wenn die Karte aktualisiert wird
Dann zeigt sie den Endzustand "abgebrochen" mit eigenem Icon und zählt den Lauf nicht als grün
```

### Aktivität A3: Status kompakt erkennen (Mockup Nr. 5)

| ID | Anforderung (EARS) | Story | Prio | Status | Notizen |
|---|---|---|---|---|---|
| FR-030 | Omnigent soll eine gemeinsame Badge-Komponente mit genau den Zuständen „Alle grün“, „Läuft“, „Wartet“, „Rot“ (Pflicht-Check fehlgeschlagen) und „Rot · nicht blockierend“ bereitstellen, jeweils mit eigenem Icon und Text. | ST-3.1 | Must | adapt (Basis `web/src/components/ui/badge.tsx`, Muster `SessionStateBadge.tsx`) | wird von Panel, Karte und R2-Chat genutzt |
| FR-031 | Omnigent soll die Badge-Komponente in den Varianten „Pill Standard (24 px)“ und „Pill Kompakt (20 px)“ bereitstellen. | ST-3.1 | Must | new | |
| FR-032 | Omnigent soll die Badge-Komponente zusätzlich in den Varianten „Punkt“, „Check-Chips“ und „Mini-Balken“ bereitstellen. | ST-3.1 | Could | new | Punkt nur mit Tooltip/aria-label |
| FR-033 | Omnigent soll in der Session-Liste der Sidebar für jede Session mit zugeordnetem offenen PR die kompakte Badge anzeigen. | ST-3.1 | Must | adapt (`web/src/shell/Sidebar.tsx`, `web/src/lib/sidebarData.ts`) | Darstellung ohne PR [OFFEN Q-9] |
| FR-034 | Omnigent soll im Session-Header eine Status-Pill (z. B. „CI läuft · 3/4“) und einen Link „PR #n ↗“ anzeigen, wenn der Session ein offener PR zugeordnet ist. | ST-3.1 | Should | adapt (Session-Header in `web/src/shell/`) | Mockup Nr. 1 oben rechts |
| FR-035 | Omnigent soll den Gesamtzustand eines PR-Laufs mit dieser Rangfolge bestimmen: Rot (mindestens ein Pflicht-Check fehlgeschlagen) > Läuft (mindestens ein Check in_progress) > Wartet (mindestens ein Check queued, keiner in_progress) > Rot · nicht blockierend (alle Pflicht-Checks erfolgreich, mindestens ein nicht-Pflicht-Check fehlgeschlagen) > Alle grün. | ST-3.1 | Must | adapt (`_classify_check` in `omnigent/runner/github_resource.py`) | Rangfolge [ANNAHME A-6] |
| FR-036 | Omnigent soll die Menge der Pflicht-Checks aus einer Konfiguration lesen, voreingestellt auf `Web (lint, typecheck, vitest, build)`, `Python lint (ruff, pyrefly)` und `Pytest (SQLite)`. | ST-3.1 | Must | new | Quelle [OFFEN Q-7] |

#### Akzeptanzkriterien A3

**AC-030 (FR-030) – fünf Zustände**
```gherkin
Angenommen die Storybook-Story der Badge-Komponente
Wenn ux-reviewer alle Zustände in jedem unterstützten Theme prüft
Dann hat jeder der 5 Zustände ein eigenes Icon und einen eigenen Text
Und alle Zustände sind in Graustufen unterscheidbar
```
**AC-031 (FR-030) – unbekannter GitHub-Zustand**
```gherkin
Angenommen GitHub liefert für einen Check einen unbekannten Status "waiting" oder conclusion "action_required"
Wenn die Badge berechnet wird
Dann zählt der Check als "Wartet" und der Fall wird ohne Token-Inhalte in die Server-Logs geschrieben
```
**AC-032 (FR-031)**
```gherkin
Angenommen PR #30 läuft mit 2 von 4 Checks fertig und ~8 Min Restzeit
Wenn die Standard-Pill gerendert wird
Dann lautet sie "Läuft · 2/4 · ~8 Min" bei 24 px Höhe
Und die kompakte Pill lautet "Läuft" bei 20 px Höhe
```
**AC-033 (FR-031) – zu wenig Platz**
```gherkin
Angenommen die Sidebar ist auf Minimalbreite gezogen
Wenn die Session-Liste rendert
Dann wird der Session-Titel gekürzt, die kompakte Pill bleibt vollständig sichtbar
```
**AC-034 (FR-033)**
```gherkin
Angenommen Session "docs: import omnigent specs" ist PR #28 zugeordnet, Pytest läuft, ~11 Min Rest
Wenn Jens die Session-Liste sieht
Dann steht neben der Session eine kompakte Badge "~11 Min" mit Läuft-Icon
```
**AC-035 (FR-033) – PR gemerged**
```gherkin
Angenommen der PR von Session "host heartbeat backoff" wurde gemerged
Wenn die Session-Liste aktualisiert wird
Dann zeigt die Session keine CI-Badge mehr
```
**AC-036 (FR-035) – Rangfolge**
```gherkin
Angenommen bei PR #29 ist "Python lint (ruff, pyrefly)" fehlgeschlagen und "Pytest (SQLite)" läuft noch
Wenn der Gesamtzustand berechnet wird
Dann ist er "Rot · Python lint" und nicht "Läuft"
```
**AC-037 (FR-035) – nur E2E rot**
```gherkin
Angenommen alle 3 Pflicht-Checks sind erfolgreich und "E2E UI (Playwright, non-blocking)" ist fehlgeschlagen
Wenn der Gesamtzustand berechnet wird
Dann ist er "Rot · nicht blockierend · E2E"
```
**AC-038 (FR-036) – Konfiguration**
```gherkin
Angenommen die Konfiguration nennt die 3 Standard-Pflicht-Checks
Wenn PR #30 die Checks Lint, Web, Pytest und E2E meldet
Dann sind genau Web, Python lint und Pytest als "Pflicht" markiert
```
**AC-039 (FR-036) – Pflicht-Check fehlt im Lauf**
```gherkin
Angenommen ein abgeschlossener PR-Lauf enthält "Pytest (SQLite)" nicht, z. B. weil der Job umbenannt wurde
Wenn der Gesamtzustand berechnet wird
Dann wird der PR nie als "Alle grün" angezeigt, sondern als "Pflicht-Check fehlt: Pytest (SQLite)"
```

### Aktivität A4: Runner & Mac-Last verstehen (Mockup Nr. 1, Abschnitte Runner/Mac-Last/Warteschlange)

| ID | Anforderung (EARS) | Story | Prio | Status | Notizen |
|---|---|---|---|---|---|
| FR-040 | Omnigent soll im CI-Panel für jeden self-hosted Runner (`actions-runner-omnigent`, `actions-runner-omnigent-2`) Name, Zustand (beschäftigt/frei/offline), aktuellen Job (Check + PR), verstrichene Zeit und Restzeit anzeigen. | ST-4.1 | Must | new | Datenquelle [OFFEN Q-3] |
| FR-041 | Falls ein Runner offline ist, dann soll Omnigent ihn im Runner-Abschnitt als „offline“ mit Zeitpunkt der letzten Aktivität anzeigen und den Sidebar-Indikator mit einem Warnhinweis versehen. | ST-4.1 | Must | new | |
| FR-042 | Omnigent soll im CI-Panel eine Warteschlange mit allen Jobs anzeigen, die auf einen Runner warten: Position, Kurzlabel, PR, Check-Name und Wartezeit. | ST-4.3 | Should | new | „nächster Start ca.“ [ANNAHME A-4] |
| FR-043 | Omnigent soll im Runner-Abschnitt die aktuelle CPU- und RAM-Auslastung (in %) des Hosts anzeigen, auf dem die Runner laufen. | ST-4.2 | Should | new (psutil vorhanden: `omnigent/host/connect.py`) | R1-Scope [OFFEN Q-2] |
| FR-044 | Falls die Mac-Last nicht ermittelt werden kann, dann soll Omnigent „Mac-Last nicht verfügbar“ anzeigen und keinen Wert 0 %. | ST-4.2 | Should | new | |
| FR-045 | Omnigent soll laufende oder wartende Jobs, die keinem offenen PR zugeordnet sind (z. B. Push auf `main`), im Runner-Abschnitt mit Workflow-Name, Branch und Kennzeichen „ohne PR“ anzeigen. | ST-4.1 | Must | new | Kernfall 18.10. |

#### Akzeptanzkriterien A4

**AC-040 (FR-040) – beide Runner beschäftigt**
```gherkin
Angenommen actions-runner-omnigent fährt "Pytest (SQLite)" für #30 seit 11:04 und actions-runner-omnigent-2 fährt "Pytest (SQLite)" für #28 seit 8:02
Wenn Jens den Runner-Abschnitt ansieht
Dann zeigt er "2/2 beschäftigt" und je Runner Check, PR, verstrichene Zeit und Restzeit
```
**AC-041 (FR-040) – Runner frei**
```gherkin
Angenommen actions-runner-omnigent-2 hat keinen Job
Wenn Jens den Runner-Abschnitt ansieht
Dann zeigt der Runner den Zustand "frei" mit Icon und Text und keinen Fortschrittsbalken
```
**AC-042 (FR-041) – Runner offline**
```gherkin
Angenommen actions-runner-omnigent-2 meldet sich seit 10 Minuten nicht mehr
Wenn Jens Omnigent ansieht
Dann zeigt der Runner-Abschnitt "offline · zuletzt aktiv 14:02"
Und der Sidebar-Indikator zeigt einen Warnhinweis mit Text "1 Runner offline"
```
**AC-043 (FR-041) – alle Runner offline, Jobs warten**
```gherkin
Angenommen beide Runner sind offline und 3 Jobs warten
Wenn Jens das Panel öffnet
Dann zeigt die Warteschlange 3 Jobs mit "kein Runner verfügbar"
Und es wird keine ETA angezeigt
```
**AC-044 (FR-042)**
```gherkin
Angenommen für #29 warten LINT und WEB seit 4 Min
Wenn Jens die Warteschlange ansieht
Dann zeigt sie "1 LINT #29 Python lint (ruff, pyrefly) wartet 4 Min" und "2 WEB #29 Web (lint, typecheck, vitest, build) wartet 4 Min"
```
**AC-045 (FR-043)**
```gherkin
Angenommen die Runner laufen auf dem Host "mac-studio" mit 92 % CPU und 55 % RAM
Wenn Jens den Runner-Abschnitt ansieht
Dann zeigt "Mac-Last" CPU 92 % und RAM 55 % mit Zeitstempel der Messung
```
**AC-046 (FR-044)**
```gherkin
Angenommen der Host der Runner ist nicht mit Omnigent verbunden
Wenn Jens den Runner-Abschnitt ansieht
Dann steht dort "Mac-Last nicht verfügbar" statt Prozentwerten
```
**AC-047 (FR-045) – Szenario 18.10.**
```gherkin
Angenommen keine Omnigent-Session ist aktiv und beide Runner fahren Pytest für PR #28 und #29
Wenn Jens das CI-Panel öffnet
Dann sieht er innerhalb von 10 s beide Pytest-Jobs, ihre PRs, die Runner und die Mac-Last
```
**AC-048 (FR-045) – Job ohne PR**
```gherkin
Angenommen actions-runner-omnigent fährt einen Lauf des Workflows ci.yml für einen Push auf main
Wenn Jens den Runner-Abschnitt ansieht
Dann zeigt der Runner "ci.yml · main · ohne PR" statt eines leeren Eintrags
```

### Aktivität A5: Programmatisch abfragen (API/MCP)

| ID | Anforderung (EARS) | Story | Prio | Status | Notizen |
|---|---|---|---|---|---|
| FR-050 | Omnigent soll einen authentifizierten, lesenden HTTP-Endpunkt bereitstellen, der den aggregierten CI-Status (PRs, Checks, Runner, Warteschlange, Mac-Last, Datenstand) als JSON liefert. | ST-5.1 | Must | new | Pfad [ANNAHME A-14], z. B. `GET /v1/ci/status` |
| FR-051 | Wenn ein Agent den CI-Status über MCP abfragt, soll Omnigent dieselben Daten wie der Endpunkt aus FR-050 liefern, optional gefiltert nach PR-Nummer oder Session-ID. | ST-5.1, ST-5.2 | Should | adapt (`omnigent/server/mcp_extensions.py` `session_github`) oder new | neue Action vs. neues Tool [OFFEN Q-13] |
| FR-052 | Omnigent soll im Endpunkt aus FR-050 je Check Name, Pflicht-Flag, Zustand, `started_at`, `completed_at`, Runner-Name und Log-URL liefern, je PR die zugeordnete Session-ID (oder `null`) und je Antwort den Zeitpunkt der letzten erfolgreichen Aktualisierung. | ST-5.1 | Must | adapt (`_summarize_checks` liefert heute nur name/bucket/url) | |
| FR-053 | Omnigent soll in der bestehenden `session.github.info`-Antwort (`GET /v1/sessions/{id}/resources/github`) je Check zusätzlich das Pflicht-Flag liefern. | ST-5.2 | Should | adapt (`omnigent/runner/github_resource.py` `_summarize_checks`) | abwärtskompatibel (NFR-019) |

#### Akzeptanzkriterien A5

**AC-050 (FR-050) – Abruf**
```gherkin
Angenommen PR #30 läuft wie in AC-008
Wenn der Lead-Agent GET auf den CI-Status-Endpunkt mit gültiger Authentifizierung ausführt
Dann erhält er HTTP 200 mit einem Eintrag für PR #30, Gesamtzustand "running" und "updated_at" nicht älter als 30 s
```
**AC-051 (FR-050) – nicht authentifiziert**
```gherkin
Angenommen ein Aufruf ohne gültige Omnigent-Authentifizierung
Wenn er den CI-Status-Endpunkt aufruft
Dann antwortet Omnigent wie andere geschützte /v1-Routen mit 401 bzw. 403 und liefert keine Daten
```
**AC-052 (FR-050) – GitHub nicht erreichbar**
```gherkin
Angenommen GitHub ist seit 5 Minuten nicht erreichbar
Wenn der Lead-Agent den Endpunkt aufruft
Dann erhält er HTTP 200 mit dem letzten bekannten Stand, "stale": true, "updated_at" und "error": "github_unreachable"
```
**AC-053 (FR-051)**
```gherkin
Angenommen der Lead-Agent hat Zugriff auf das Omnigent-MCP
Wenn er den CI-Status für PR 30 abfragt
Dann erhält er in einem Aufruf Gesamtzustand, Pflicht-Check-Ergebnis und Log-URLs
```
**AC-054 (FR-052) – Felder**
```gherkin
Angenommen "Pytest (SQLite)" von #30 läuft auf actions-runner-omnigent seit 14:12
Wenn der Endpunkt abgefragt wird
Dann enthält der Check required=true, state="in_progress", started_at mit Zeitzone, completed_at=null, runner="actions-runner-omnigent" und eine Log-URL
Und PR #30 enthält die Session-ID der erstellenden Session
```
**AC-055 (FR-052) – PR ohne Session, Runner unbekannt**
```gherkin
Angenommen PR #35 hat keine Session und sein Job wartet
Wenn der Endpunkt abgefragt wird
Dann ist session_id=null und runner=null, und die Antwort ist trotzdem gültig
```
**AC-056 (FR-053)**
```gherkin
Angenommen ein bestehender Client liest session.github.info
Wenn das Pflicht-Flag ergänzt wurde
Dann bleiben die bisherigen Felder passing/failing/pending/total/runs[name,bucket,url] unverändert
```

### Aktivität A6: Aktualität & Fehlerzustände (übergreifend für alle Ansichten)

| ID | Anforderung (EARS) | Story | Prio | Status | Notizen |
|---|---|---|---|---|---|
| FR-060 | Während eine CI-Ansicht (Indikator, Panel, Karte, Badge) sichtbar ist, soll Omnigent deren Daten ohne Neuladen der Seite automatisch aktualisieren. | ST-6.1 | Must | adapt (Muster `usePullRequests.ts`, `sessionUpdatesSocket.ts`) | Mechanismus [OFFEN Q-1] |
| FR-061 | Omnigent soll im CI-Panel anzeigen, ob die Daten aktuell sind („Live“) oder wann sie zuletzt erfolgreich aktualisiert wurden („Stand 14:31“). | ST-6.1 | Must | new | Mockup Nr. 1 „Live“ |
| FR-062 | Falls die letzte erfolgreiche Aktualisierung älter als 60 s ist, dann soll Omnigent alle CI-Ansichten als „veraltet“ kennzeichnen (Icon + Text + Uhrzeit) und laufende Animationen anhalten. | ST-6.1 | Must | new | Schwelle [ANNAHME A-2] |
| FR-063 | Falls GitHub nicht erreichbar ist, dann soll Omnigent im CI-Panel den Hinweis „GitHub nicht erreichbar – letzter Stand HH:MM“ anzeigen, den letzten bekannten Stand behalten und die Abfrage mit Backoff wiederholen. | ST-6.1 | Must | new | |
| FR-064 | Falls GitHub ein Rate-Limit meldet, dann soll Omnigent Abfragen bis zum gemeldeten Reset-Zeitpunkt aussetzen und „GitHub-Limit erreicht – weiter um HH:MM“ anzeigen. | ST-6.1 | Must | new | |
| FR-065 | Falls kein authentifiziertes GitHub-Konto für das Repo verfügbar ist, dann soll Omnigent „GitHub nicht verbunden“ mit Verweis auf die bestehende GitHub-Verbindung in den Einstellungen anzeigen. | ST-6.1 | Must | adapt (bestehend: `omnigent/server/routes/connections_github.py`, `github_resource._list_accounts`) | |
| FR-066 | Omnigent soll im Rahmen dieses Features ausschließlich lesende GitHub-Aufrufe ausführen (kein Rerun, Cancel, Merge, keine Workflow-Änderung). | ST-6.1 | Must | new | C-1, C-3, C-4, C-5 |
| FR-067 | Omnigent soll sessionübergreifende CI-Daten nur Nutzern bzw. Agenten anzeigen, die nach der bestehenden Omnigent-Autorisierung Zugriff haben. Sessions ohne Zugriff erscheinen als „ohne Session“. | ST-6.2 | Should | adapt (bestehende Auth, `omnigent/server/auth.py`) | Rollenmodell [OFFEN Q-4] |

#### Akzeptanzkriterien A6

**AC-060 (FR-060) – Auto-Update**
```gherkin
Angenommen das CI-Panel ist offen und "Pytest (SQLite)" von #30 läuft
Wenn der Check auf GitHub erfolgreich abschließt
Dann wechselt die Pill PY von #30 innerhalb von 30 s ohne Neuladen auf grün
```
**AC-061 (FR-060) – Hintergrund-Tab**
```gherkin
Angenommen der Omnigent-Tab ist 10 Minuten im Hintergrund
Wenn Jens zurück in den Tab wechselt
Dann zeigt die Ansicht innerhalb von 5 s den aktuellen Stand oder den Hinweis "veraltet"
```
**AC-062 (FR-061) – Live**
```gherkin
Angenommen die letzte erfolgreiche Aktualisierung war vor 8 s
Wenn Jens das Panel ansieht
Dann steht im Panel-Kopf "Live"
```
**AC-063 (FR-061, FR-062) – veraltet**
```gherkin
Angenommen die letzte erfolgreiche Aktualisierung war um 14:31 und es ist 14:33
Wenn Jens Panel, Karte oder Session-Liste ansieht
Dann steht statt "Live" in allen CI-Ansichten "Veraltet · Stand 14:31"
Und es läuft keine Animation (keine Streifen, kein Spinner)
```
**AC-064 (FR-062) – Server neu gestartet**
```gherkin
Angenommen der Omnigent-Server wurde neu gestartet und hat noch keine CI-Daten
Wenn Jens das Panel öffnet
Dann zeigt es "CI-Status lädt …" und nach höchstens 60 s den aktuellen Stand
```
**AC-065 (FR-063) – GitHub nicht erreichbar**
```gherkin
Angenommen github.com ist für den Omnigent-Server nicht erreichbar
Wenn das Panel offen ist
Dann zeigt es "GitHub nicht erreichbar – letzter Stand 14:31" sowie die zuletzt bekannten PRs
Und es erscheint kein Stacktrace und keine leere Liste
```
**AC-066 (FR-063) – Erholung**
```gherkin
Angenommen GitHub war 3 Minuten nicht erreichbar
Wenn GitHub wieder antwortet
Dann verschwindet der Hinweis spätestens 30 s danach ohne Neuladen
```
**AC-067 (FR-064) – Rate-Limit mit Reset**
```gherkin
Angenommen GitHub antwortet mit Rate-Limit und Reset-Zeitpunkt 15:00
Wenn der nächste Abfragezyklus ansteht
Dann sendet Omnigent bis 15:00 keine weiteren CI-Abfragen
Und das Panel zeigt "GitHub-Limit erreicht – weiter um 15:00"
```
**AC-068 (FR-064) – Rate-Limit ohne Reset-Angabe**
```gherkin
Angenommen GitHub meldet ein Secondary-Rate-Limit ohne Reset-Zeitpunkt
Wenn der nächste Abfragezyklus ansteht
Dann wartet Omnigent mit exponentiellem Backoff (Start 60 s, höchstens 15 Min)
Und das Panel zeigt "GitHub-Limit erreicht – neuer Versuch um HH:MM"
```
**AC-069 (FR-065) – nicht verbunden**
```gherkin
Angenommen gh auf dem Server ist nicht angemeldet und keine GitHub-Verbindung ist eingerichtet
Wenn Jens das Panel öffnet
Dann sieht er "GitHub nicht verbunden" mit Link zur GitHub-Verbindung in den Einstellungen
Und weder UI noch Logs enthalten Token-Fragmente
```
**AC-070 (FR-065) – Token wird ungültig**
```gherkin
Angenommen die CI-Daten wurden erfolgreich geladen und danach wird das Token widerrufen (HTTP 401)
Wenn der nächste Abfragezyklus läuft
Dann bleibt der letzte Stand sichtbar und als veraltet markiert, darüber steht "GitHub nicht verbunden"
```
**AC-071 (FR-066) – nur lesend**
```gherkin
Angenommen ein Testlauf zeichnet alle GitHub-Aufrufe des Features 24 h lang auf
Wenn die Aufzeichnung ausgewertet wird
Dann gibt es nur GET-Requests bzw. GraphQL-Queries und keine Mutations, POST, PATCH, PUT oder DELETE
```
**AC-072 (FR-066) – keine Aktionsbuttons**
```gherkin
Angenommen ein PR mit rotem Pflicht-Check
Wenn Jens Karte und Panel durchsucht
Dann gibt es keine Schaltflächen für Rerun, Cancel oder Merge, nur Links zu Log und GitHub
```
**AC-073 (FR-067)**
```gherkin
Angenommen ein Nutzer ohne Zugriff auf Session "remove session human chat"
Wenn er das CI-Panel öffnet
Dann sieht er PR #30 mit "ohne Session" und ohne Session-Titel
```

### Aktivität A7: Team & Historie (R2, nicht Teil von R1)

| ID | Anforderung (EARS) | Story | Prio | Status | Notizen |
|---|---|---|---|---|---|
| FR-070 | Wo das Feature `team_chat` aktiviert ist, soll Omnigent eine live aktualisierte CI-Karte (gleiche Komponente wie in FR-021) im Team-Chat anzeigen. | ST-7.1 | Won't (R1) / Should (R2) | adapt (`omnigent/server/feature_flags.py` TEAM_CHAT) | Mockup Nr. 4; Aktivierung offen (C-6) |
| FR-071 | Wo das Feature `team_chat` aktiviert ist und ein Pflicht-Check eines PRs fehlschlägt, soll der Lead-Agent im Team-Chat eine Nachricht mit fehlgeschlagenem Check und Log-Link posten. | ST-7.1 | Won't (R1) / Should (R2) | new | |
| FR-072 | Omnigent soll optional eine Runner-Zeitleiste anbieten, die Jobs je Runner über die Zeit zeigt. | ST-7.2 | Won't (R1) / Could (R2) | new | Mockup Nr. 3 |

R2-Anforderungen bekommen ihre Akzeptanzkriterien erst, wenn R2 eingeplant wird.

## 7. Nicht-funktionale Anforderungen (ISO/IEC 25010:2023)

| ID | Merkmal | Anforderung | Metrik & Schwelle | Verifikation |
|---|---|---|---|---|
| NFR-001 | Performance efficiency (Zeitverhalten) | Statuswechsel auf GitHub erscheinen in allen offenen CI-Ansichten. | p95 ≤ 30 s, p99 ≤ 60 s über 50 Statuswechsel [ANNAHME A-1] | Zeitstempelvergleich GitHub `completed_at`/`started_at` vs. UI-Render-Log |
| NFR-002 | Performance efficiency (Zeitverhalten) | Das CI-Panel ist nach dem Öffnen gefüllt. | p95 ≤ 1 s bei vorhandenem Server-Cache, 20 offene PRs, 2 Runner | Playwright-Messung, 20 Wiederholungen |
| NFR-003 | Performance efficiency (Ressourcen) | Die Datenerfassung belastet den Mac kaum. | Durchschnittliche CPU des Erfassungsprozesses < 1 % eines Kerns über 1 h bei 3 laufenden PRs. Keine zusätzlichen Runner-Jobs. | psutil-Messung über 1 h |
| NFR-004 | Performance efficiency (Kapazität) | GitHub-API-Verbrauch bleibt weit unter dem Limit. | ≤ 1.000 REST-Requests/h (20 % von 5.000/h für authentifizierte Nutzer [ANNAHME A-10]), unabhängig von der Zahl offener Browser-Tabs | Zähler im Server, Header `X-RateLimit-Remaining` |
| NFR-005 | Performance efficiency (Ressourcen) | Es gibt genau eine serverseitige Erfassung, egal wie viele Tabs, Sessions oder Agenten zusehen. | Anzahl GitHub-Requests bei 1 vs. 5 offenen Tabs unterscheidet sich um ≤ 10 % | Lasttest mit 1 und 5 Tabs, je 15 Min |
| NFR-006 | Reliability (Fehlertoleranz) | Ein GitHub-Ausfall beeinträchtigt keine anderen Omnigent-Funktionen. | Bei blockiertem github.com: p95 von `GET /v1/sessions` ändert sich um ≤ 10 %, 0 unbehandelte Exceptions | Fault-Injection-Test (DNS-Block) |
| NFR-007 | Reliability (Wiederherstellbarkeit) | Nach Ende eines GitHub-Ausfalls oder einem Server-Neustart ist der Status wieder aktuell. | ≤ 60 s nach Wiederverfügbarkeit bzw. Serverstart, ohne Neuladen im Browser | Fault-Injection- und Neustart-Test |
| NFR-008 | Functional suitability (Korrektheit) | Die Zustandsabbildung entspricht GitHub. | 100 % Übereinstimmung in Fixture-Tests für status queued/in_progress/completed × conclusion success/failure/cancelled/skipped/neutral/timed_out/action_required | Pytest (Fixtures) + Vitest (UI-Mapping) |
| NFR-009 | Security (Vertraulichkeit) | Keine Tokens in UI, API-Antworten, MCP-Ausgaben oder Logs. | 0 Treffer für Token-Muster (`gh[pousr]_`, `github_pat_`, `Bearer `) in Responses und Logs nach 24 h Testbetrieb | automatisierter Grep-Test + Code-Review |
| NFR-010 | Security (Autorisierung) | Der CI-Endpunkt und die MCP-Abfrage verwenden die bestehende Omnigent-Authentifizierung. | 100 % der Aufrufe ohne gültige Identität werden abgewiesen | API-Test (AC-051) |
| NFR-011 | Security (Least Privilege) | Für CI-Daten werden nur lesende Berechtigungen genutzt, kein neuer Secret-Speicher. | Falls ein neuer Token nötig ist: nur Leserechte (Actions, Checks, Pull Requests, Metadata) [ANNAHME A-11]. 0 neue Secret-Dateien. | Review der Token-Scopes |
| NFR-012 | Interaction capability (Barrierefreiheit) | Kontrast aller Texte und Statusinformationen. | Text ≥ 4,5:1; große Schrift und Nicht-Text-Elemente (Icons, Balken, Fokusring) ≥ 3:1; in jedem unterstützten Theme | ux-reviewer + axe-core in Playwright |
| NFR-013 | Interaction capability (Barrierefreiheit) | Status wird nie nur über Farbe vermittelt. | Jeder Zustand hat Icon + Text bzw. aria-label; Graustufen-Screenshot ohne Verwechslung | ux-reviewer, Storybook-Screenshot |
| NFR-014 | Interaction capability (Bedienbarkeit) | Vollständig per Tastatur bedienbar. | Indikator, Panel-Tabs, PR-Einträge, „Details“ und Log-Links per Tab/Enter/Esc erreichbar; sichtbarer Fokus; Wechsel des Gesamtstatus per `aria-live="polite"`, höchstens 1 Ansage je 10 s | Tastatur-Testprotokoll + axe |
| NFR-015 | Interaction capability (Bedienbarkeit) | Rücksicht auf reduzierte Bewegung. | Bei `prefers-reduced-motion: reduce` keine animierten Streifen/Spinner, sondern statische Darstellung wie im Mockup („gestreift = läuft“) | Playwright mit emuliertem Media-Feature |
| NFR-016 | Interaction capability (Erlernbarkeit / UX-Qualität) | Die GUI erfüllt G-1 und entspricht den Mockups. | 5/5 Testszenarien in ≤ 10 s; ux-reviewer-Abnahme gegen `mockups/*.html` ohne offene Blocker | moderierter Test mit Jens + ux-reviewer-Bericht |
| NFR-017 | Maintainability (Modularität/Wiederverwendbarkeit) | Badge, Check-Pill, Segmentbalken und CI-Karte sind gemeinsame Komponenten für Panel, Session-Karte und später den Team-Chat. | 1 Implementierung der Zustandsabbildung (Server) + 1 UI-Mapping; Storybook-Stories für alle 5 Zustände je Komponente | Code-Review, Storybook |
| NFR-018 | Flexibility (Anpassbarkeit) | Repo, Pflicht-Check-Namen, Kurzlabels und Runner-Namen sind konfigurierbar, nicht fest im Code. | Änderung eines Check-Namens wirkt ohne Code-Änderung | Konfigurationstest |
| NFR-019 | Compatibility (Interoperabilität) | Bestehende Schnittstellen bleiben abwärtskompatibel. | `session.github.info` und MCP `session_github` behalten alle bisherigen Felder; bestehende Tests in `tests/` und `web/` bleiben grün | Pytest + Vitest, Diff von `openapi.json` |
| NFR-020 | Maintainability (Wiederverwendbarkeit) | UI-Micro-Interactions (Ladezustände, Segmented Control für Panel-Tabs, Tooltips, Fortschritt) nutzen bevorzugt interior.dev-Komponenten, angepasst an Omnigents Design-Tokens (CSS-Variablen in `web/src/index.css`, shadcn-Style `radix-nova`). **Status: adapt.** Vorhanden: Tailwind 4.3.1, React 19.3.1, shadcn-CLI + `web/components.json`. **Fehlt: Motion (`motion`).** Zielpfad gemäß Alias: `web/src/components/interior/`. | Je übernommener Komponente: Herkunft + MIT-Hinweis im Dateikopf; 0 fest codierte Farben (nur Tokens); Eigenbau statt interior.dev im ux-builder-PR begründet | Code-Review, ux-reviewer |
| NFR-021 | Interaction capability (Bedienbarkeit) | Zeitangaben sind eindeutig. | Alle Uhrzeiten in Europe/Berlin; Dauern als m:ss bzw. „~n Min“; ETA als Uhrzeit | ux-reviewer, Vitest für Formatierung |

Safety (25010): keine Safety-Anforderungen identifiziert. Das Feature ist rein lesend (FR-066).

## 8. Constraints & Schnittstellen

| ID | Constraint |
|---|---|
| C-1 | Null GitHub-Kosten: nur die self-hosted Runner `actions-runner-omnigent` und `actions-runner-omnigent-2`, keine GitHub-hosted Runner. API-Aufrufe sind kostenlos und zulässig. |
| C-2 | Kein CI-Throttling: Das Feature verändert weder Parallelität noch Concurrency noch Runner-Konfiguration. |
| C-3 | Die 91 deaktivierten Workflows werden nicht angefasst (nicht aktivieren, nicht ändern, nicht auslösen). |
| C-4 | Kein Merge ohne grüne Pflicht-CI. Das Feature bietet keinen Merge an. „Rot · nicht blockierend“ darf nicht als Merge-Blocker dargestellt werden. |
| C-5 | Das Feature löst keine Workflow-Läufe aus (kein `workflow_dispatch`, kein Rerun). |
| C-6 | `team_chat` (`omnigent/server/feature_flags.py`) ist aus und bleibt aus, bis darüber entschieden ist. Keine Arbeit in R1 schaltet das Flag ein. |
| C-7 | Die UI-Umsetzung läuft über ux-builder, die Prüfung über ux-reviewer (Teamregel 18.10.). Die Mockups in `mockups/` sind die visuelle Source of Truth. |
| C-8 | Code-Basis: `github.com/jholze/omnigent`. Neue Server-Logik gehört zu den bestehenden GitHub-Modulen (`omnigent/runner/github_resource.py`, `omnigent/runner/git_providers/`), keine zweite, parallele GitHub-Integration. |
| C-9 | Keine Secrets lesen oder ausgeben. Agenten lesen bei der Umsetzung nicht `.env`, plists, `auth_tokens.json`, `encryption_key` oder `agent_swarm/secrets/`. Tokens kommen nur über bestehende Mechanismen (`gh`-Konto/Keyring, GitHub-App-Verbindung). |
| C-10 | Lokale Runner-Daten (falls genutzt, siehe Q-3) werden nur gelesen. Die Runner-Verzeichnisse `~/actions-runner-omnigent*/` werden nicht verändert. |
| C-11 | Bevorzugte Komponentenquelle für Micro-Interactions ist interior.dev (MIT; React + Tailwind + Motion; Installation per `npx shadcn@latest add https://www.interior.dev/r/<name>.json`, Quelle liegt im Repo). Eine neue Abhängigkeit `motion` braucht Jens' OK (Q-10). |

**Schnittstellen**
- GitHub: REST/GraphQL über `gh` bzw. das bestehende Konto-Token (`github_resource._gh`, `_account_token_for`). Mechanismus: Q-1.
- Omnigent intern: Session-PR-Registry (`omnigent/runner/session_prs.py`), Session-Route `GET /v1/sessions/{id}/resources/github`, WebSocket `WS /v1/sessions/updates` (`web/src/lib/sessionUpdatesSocket.ts`), MCP `session_github` (`omnigent/server/mcp_extensions.py`).
- Neu: aggregierter CI-Status-Endpunkt (FR-050), CI-Panel-UI, Session-Karte, Badge-Komponenten.

## 9. Daten & Migration

- **PR ↔ Session:** vorhanden in der hostlokalen `SessionPrRegistry` (JSON je Session). Für die sessionübergreifende Sicht braucht es einen Index PR → Session(s) [OFFEN Q-12]. `git_branch` (DB, `db_models.py:806`) dient nur als Fallback.
- **CI-Daten:** Vorschlag In-Memory-Cache auf dem Server, ohne DB-Migration [ANNAHME A-12]. Nach einem Neustart wird der Stand neu aufgebaut (NFR-007).
- **Typische Dauer:** Median der letzten 10 erfolgreichen Läufe desselben Checks [ANNAHME A-4]. Ob dafür Persistenz nötig ist, klärt das Design.
- **Mac-Last:** flüchtig, wird nicht gespeichert.
- Keine Migration bestehender Daten, keine Änderung bestehender Felder (NFR-019).

## 10. Traceability

| Goal | Story | FR/NFR | AC / Test | Task |
|---|---|---|---|---|
| G-1 | ST-1.1 | FR-001, FR-002, FR-003 | AC-001–005 | – (offen) |
| G-1, G-3 | ST-1.2 | FR-004, FR-005, FR-006, FR-007, FR-010, FR-011 | AC-006–010, AC-015 | – |
| G-3 | ST-1.3 | FR-008, FR-009 | AC-011–014 | – |
| G-3, G-7 | ST-2.1 | FR-020, FR-021, FR-024, FR-025 | AC-020–024, AC-027, AC-028 | – |
| G-7 | ST-2.2 | FR-022, FR-023 | AC-025, AC-026 | – |
| G-4 | ST-3.1 | FR-030–FR-036 | AC-030–039 | – |
| G-1 | ST-4.1 | FR-040, FR-041, FR-045 | AC-040–043, AC-047, AC-048 | – |
| G-1 | ST-4.2 | FR-043, FR-044 | AC-045, AC-046 | – |
| G-1 | ST-4.3 | FR-042 | AC-044 | – |
| G-6 | ST-5.1 | FR-050, FR-051, FR-052 | AC-050–055 | – |
| G-6 | ST-5.2 | FR-051, FR-053 | AC-053, AC-056 | – |
| G-2 | ST-6.1 | FR-060–FR-066, NFR-001, NFR-006, NFR-007 | AC-060–072 | – |
| G-6 | ST-6.2 | FR-067, NFR-010 | AC-073, AC-051 | – |
| G-5 | (übergreifend) | FR-066, NFR-003, NFR-004, NFR-005, C-1, C-2, C-5 | AC-071, Messungen NFR-003/004 | – |
| G-7 | (übergreifend) | NFR-012–NFR-017, NFR-020, NFR-021, C-7, C-11 | ux-reviewer-Bericht, axe, Storybook | – |
| (R2) | ST-7.1, ST-7.2 | FR-026, FR-070, FR-071, FR-072 | – (AC folgen mit R2-Planung) | – |

**Lückenprüfung**
- Ziele ohne Story: keine.
- Stories ohne FR: keine.
- Must-FRs ohne Happy- und Fehler-/Rand-AC: keine.
- Should/Could ohne AC: FR-007, FR-011, FR-023, FR-024. Für Should/Could akzeptabel, wird vor der Umsetzung nachgezogen.
- R2-FRs ohne AC: so gewollt.
- Tasks: noch nicht geschnitten (folgt nach Freigabe).

**Must-Liste R1 (27 FR):** FR-001, FR-003, FR-004, FR-005, FR-008, FR-009, FR-020, FR-021, FR-022, FR-025, FR-030, FR-031, FR-033, FR-035, FR-036, FR-040, FR-041, FR-045, FR-050, FR-052, FR-060, FR-061, FR-062, FR-063, FR-064, FR-065, FR-066.
- Aufwandsanteil: noch nicht geschätzt [ANNAHME A-13]. Die Grenze von ≤ 60 % wird nach dem Design geprüft.
- Kandidaten zum Herabstufen auf Should: FR-041 (Offline-Warnung im Indikator), FR-061, FR-064.

## 11. Annahmen

| ID | Annahme | Prüfen durch |
|---|---|---|
| A-1 | Das Latenzziel p95 ≤ 30 s reicht Jens. | Jens |
| A-2 | Schwelle „veraltet“ = 60 s ohne erfolgreiche Aktualisierung. | Jens / Design |
| A-3 | „Fertig“ zeigt Läufe der letzten 2 h (Mockup: „letzte 2 h“). | Jens |
| A-4 | Typische Dauer = Median der letzten 10 erfolgreichen Läufe desselben Checks. ETA und „nächster Start“ werden daraus abgeleitet. | Design |
| A-5 | Pflicht-Checks sind genau die drei genannten, E2E ist nicht blockierend (Angabe von Jens). Ob Branch Protection/Rulesets auf GitHub das technisch durchsetzen, ist nicht geprüft. | Q-7 |
| A-6 | Rangfolge des Gesamtzustands: Rot > Läuft > Wartet > Rot · nicht blockierend > Alle grün. | Jens |
| A-7 | Der Omnigent-Server läuft auf demselben Mac wie die Runner (Host „mac-studio“ im Mockup). Nur dann sind lokale Runner-Daten und Mac-Last direkt lesbar. | Jens / Design |
| A-8 | R1 betrifft nur das Repo `jholze/omnigent`. | Jens |
| A-9 | Das bestehende `gh`-Konto bzw. Token hat Lesezugriff auf die Actions-Läufe und Jobs von `jholze/omnigent`. | Design-Spike |
| A-10 | Das Limit für authentifizierte REST-Aufrufe beträgt 5.000/h (GitHub-Standard). Für den konkret genutzten Token-Typ nicht verifiziert. | Design-Spike |
| A-11 | Leserechte reichen für Workflow-Läufe und Jobs. Für die API-Liste der self-hosted Runner sind ggf. Admin-Rechte nötig; nicht verifiziert. | Q-3 |
| A-12 | Für R1 genügt ein In-Memory-Cache ohne DB-Migration. | Design |
| A-13 | Der Aufwand der Must-FRs liegt bei ≤ 60 %. Noch nicht geschätzt. | Design / Planung |
| A-14 | Der Endpunktpfad, z. B. `GET /v1/ci/status`, ist nur ein Platzhalter. | Design |
| A-15 | Text des Ruhezustands: „CI ruhig“ (nicht im Mockup). | ux-builder |
| A-16 | Ein neuer Push erzeugt eine neue Karte, die alte wird als überholt markiert (statt sie zu aktualisieren). | Jens / ux-builder |
| A-17 | Die GitHub-Jobs-API liefert je Job Runner-Name sowie Start- und Endzeit. Nicht verifiziert. | Design-Spike |
| A-18 | interior.dev bietet laut Startseite (19.10.) Segmented Control, Tooltip Group und Loading Button. Ob es eine passende Progress- oder Segmentbalken-Komponente gibt, ist nicht geprüft. | ux-builder |

## 12. Offene Fragen

| ID | Frage | Owner | Blockiert | Fällig | Vorschlag (Default, keine Entscheidung) | Antwort |
|---|---|---|---|---|---|---|
| Q-1 | Datenquelle und Aktualisierungsmechanismus für den CI-Status: (a) Polling GitHub REST/GraphQL über `gh` bzw. bestehendes Token, (b) GitHub-Webhooks (`workflow_run`/`workflow_job`/`check_run`), (c) lokaler Runner-Status? | Jens | FR-050, FR-060, NFR-001, NFR-004 | vor Design | (a) Ein zentraler Server-Poller mit adaptivem Intervall (kurz, solange etwas läuft; lang im Leerlauf) und Conditional Requests. Push an die UI über den bestehenden WebSocket. Webhooks erst, wenn Omnigent öffentlich erreichbar ist. API-Aufrufe sind kostenlos (C-1). | |
| Q-2 | Gehört die Mac-Last (CPU/RAM des Runner-Hosts) zu R1? | Jens | FR-043, FR-044 | vor Design | Ja, als Should (im Mockup enthalten, Kern des Problems vom 18.10.). Quelle: psutil auf dem Runner-Host. | |
| Q-3 | Datenquelle für die Runner-Sicht: GitHub API (Jobs mit Runner-Name; die Runner-Liste braucht ggf. Admin-Rechte) oder lokal (`_diag`-Logs, `Runner.Worker`-Prozesse in `~/actions-runner-omnigent*/`)? | Jens | FR-040, FR-041, FR-045 | vor Design | Kombination: Belegung aus den Jobs der laufenden Workflow-Läufe (GitHub API). Online/Offline zusätzlich lokal und nur lesend per Prozess-Check, falls A-7 zutrifft. | |
| Q-4 | Wer darf was sehen (Jens, Lead-Agent, Sub-Agenten, später Ranga)? Sehen Sub-Agenten fremde PRs? | Jens | FR-051, FR-067, NFR-010 | vor R1-Freigabe | R1: nur der Owner und die Agenten in Jens' Omnigent, alle lesend auf alles. Teammitglieder später read-only, Session-Titel nur bei Zugriff. | |
| Q-5 | Startzeitpunkt: PR #34 ist auf main gemerged (`4b212fa`). Erst nach der R0-Demo am 21.10. starten? | Jens | Planung | 2026-10-20 | Spec und Design jetzt, Umsetzung nach der R0-Demo am 21.10. | |
| Q-6 | Wo genau sitzt die Session-Karte: unter dem `git push`-Tool-Call oder unter `gh pr create`? Was passiert bei mehreren Pushes? | Jens / ux-builder | FR-020, FR-024 | vor UI-Build | Unter der ersten Agenten-Nachricht, nach der die Registry den PR kennt. Jeder weitere Push auf denselben PR erzeugt eine neue Karte (A-16). | |
| Q-7 | Woher kommt die Pflicht-Check-Liste: Konfiguration in Omnigent oder Branch Protection/Rulesets von GitHub? | Jens | FR-035, FR-036 | vor Design | Konfiguration mit den drei Namen als Default. Später optional Abgleich mit GitHub. | |
| Q-8 | Was bedeutet der Filter „Nur meine“ im Mockup? | Jens | FR-007 | vor UI-Build | PRs, die Sessions des angemeldeten Nutzers zugeordnet sind. | |
| Q-9 | Session-Liste ohne PR: Text „kein PR“ (Mockup) oder leer (weniger Rauschen)? | ux-builder / Jens | FR-033 | vor UI-Build | Leer lassen, „kein PR“ nur im Hover. | |
| Q-10 | Darf `motion` als neue Web-Abhängigkeit für interior.dev-Komponenten dazukommen? | Jens | NFR-020, C-11 | vor UI-Build | Ja, beschränkt auf CI-Komponenten; Bundle-Größe vorher/nachher im PR ausweisen. Alternative: Fallback mit CSS/tw-animate-css. | |
| Q-11 | Ort des CI-Panels: eigenes globales rechtes Panel oder Tab im bestehenden `WorkspacePanel` (dort liegt das PR-Panel pro Session)? | ux-builder / Jens | FR-003 | vor UI-Build | Eigenes globales Panel, weil die Sicht sessionübergreifend ist. Die Resize-Infrastruktur (`useResizablePanel`) wird wiederverwendet. | |
| Q-12 | Index PR → Session für die globale Sicht: Scan der Registry-Dateien oder neue DB-Tabelle? | Design | FR-008, FR-009, FR-052 | Design | In R1 Scan + Cache. DB nur, wenn der Scan zu langsam ist. | |
| Q-13 | MCP: neue Action in `session_github` oder ein neues Tool (z. B. `ci_status`)? | Jens / Design | FR-051 | Design | Neues, sessionunabhängiges Tool, weil die Sicht sessionübergreifend ist. `session_github info` bekommt nur das Pflicht-Flag (FR-053). | |
| Q-14 | Welche Themes müssen unterstützt werden? Die Mockups sind nur dunkel. | Jens / ux-builder | NFR-012 | vor UI-Build | Alle vorhandenen Omnigent-Themes über Tokens. ux-builder liefert eine helle Variante zur Abnahme. | |
| Q-15 | Was zeigt der Panel-Tab „Verlauf“? | Jens | FR-011 | vor UI-Build | Abgeschlossene Läufe der letzten 24 h als Liste mit Badges, oder den Tab in R1 weglassen. | |

## 13. Risiken

| ID | Risiko | Wahrscheinlichkeit | Auswirkung | Gegenmaßnahme |
|---|---|---|---|---|
| R-1 | Polling vieler PRs/Jobs erreicht das GitHub-Rate-Limit und stört andere `gh`-Nutzung (Agenten pushen ebenfalls per `gh`). | mittel | hoch | ein zentraler Poller (NFR-005), Budget ≤ 20 % (NFR-004), Conditional Requests, Backoff (FR-064) |
| R-2 | Die Session-Zuordnung ist lückenhaft: PR außerhalb von Omnigent erstellt, nur `git push` ohne `gh`, `git_branch` nur bei Server-Worktrees gesetzt. | hoch | mittel | Anzeige „ohne Session“ (FR-009), Registry-Beziehung `inferred`, Fallback `git_branch` |
| R-3 | Lokales Lesen von Runner-Daten koppelt Omnigent an das Mac-Layout. | mittel | mittel | konfigurierbare Pfade (NFR-018), nur als Ergänzung zur API (Q-3), nur lesend (C-10) |
| R-4 | Der UX-Anspruch bläht den Umfang auf. | mittel | mittel | Mockups als Source of Truth, ux-reviewer-Gate, Must-Grenze ≤ 60 % (A-13) |
| R-5 | Die Erfassung selbst erzeugt Last auf dem ohnehin ausgelasteten Mac. | niedrig | mittel | NFR-003 messen, adaptives Intervall |
| R-6 | Token-Leck über Logs: `github_resource._run` protokolliert bei Timeout `argv`. Ob Tokens per argv oder per Umgebung übergeben werden, ist nicht geprüft. | niedrig | hoch | NFR-009-Test, Review der Logging-Stellen |
| R-7 | Die neue Abhängigkeit `motion` vergrößert das Bundle und den Wartungsaufwand. | mittel | niedrig | Q-10, Bundle-Messung, CSS-Fallback |
| R-8 | „Rot · nicht blockierend“ wird als Merge-Blocker oder als grün missverstanden. | mittel | hoch | eigener Zustand mit Text (FR-030), Test zu G-4 |
| R-9 | R2-Arbeiten schalten `team_chat` versehentlich ein. | niedrig | mittel | C-6, Feature-Flag-Guard, Review |
| R-10 | Umbenannte Job-Namen in der Workflow-Datei lassen Pflicht-Checks „verschwinden“, und ein PR wirkt fälschlich grün. | mittel | hoch | AC-039 („Pflicht-Check fehlt“), Konfiguration (FR-036) |

## Changelog

| Version | Datum | Änderung |
|---|---|---|
| 0.1 | 2026-10-19 | Vorstufe: `intake.md` v0.1 (Idee, Anlass, Defaults, Einordnung). |
| 0.2 | 2026-10-19 | Erstfassung der Requirements Spec (Quick Path, Stufe 4). Inhalt: Ziele G-1–G-7, Rollen P-1–P-4, Story-Map-lite, 46 FR (27 Must; 42 FR für R1, 4 FR für R2: FR-026, FR-070–FR-072), 64 AC, 21 NFR, Constraints C-1–C-11, Bestandsaufnahme im Code (@`4b212fa`), Annahmen A-1–A-18, offene Fragen Q-1–Q-15, Risiken R-1–R-10. Release-Schnitt nach Jens' Entscheidung: R1 = Nr. 1 + Nr. 2 + Nr. 5, R2 = Nr. 4 (nur mit `team_chat`) + Nr. 3. interior.dev als bevorzugte Komponentenquelle (C-11, NFR-020). |
