# CI-Status-Ansicht – Design-Notizen (Optionen je offener Frage)

Version: 0.1 · Datum: 2026-10-19 · Status: Entwurf · Owner: Jens Holze · Autor: Spec
Bezug: `04-requirements.md` v0.2, §12

> Diese Datei sammelt nur **Kandidaten-Optionen mit Abwägungen**. Hier fällt **keine Entscheidung** und es gibt kein ADR. Entscheidungen trifft Jens. Danach folgen `05-design.md` und die ADRs.

## Q-1 Datenquelle & Aktualisierung CI-Status
| Option | Beschreibung | Plus | Minus |
|---|---|---|---|
| 1a Polling über `gh`/bestehendes Token (REST) | zentraler Server-Poller: offene PRs → Workflow-Läufe → Jobs (`/repos/{o}/{r}/actions/runs`, `/actions/runs/{id}/jobs`) | baut auf `github_resource._gh`/`_account_token_for` auf; keine öffentliche URL nötig; API-Aufrufe kostenlos | Latenz = Intervall; Verbrauch am Rate-Limit (R-1) |
| 1b Polling GraphQL (`statusCheckRollup` + Check-Runs) | erweitert das bestehende `gh pr view --json statusCheckRollup` | nah am bestehenden Code (`_PR_VIEW_FIELDS`) | Runner-Name und Warteschlange stehen nicht im Rollup → zusätzlich REST nötig [ANNAHME A-17] |
| 1c GitHub-Webhooks (`workflow_run`, `workflow_job`, `check_run`) | GitHub pusht Ereignisse an einen neuen Omnigent-Endpunkt | minimale Latenz, kaum API-Verbrauch | braucht eine von außen erreichbare URL bzw. einen Tunnel und ein Webhook-Secret; neuer Angriffsvektor; heute gibt es keinen Empfänger |
| 1d Hybrid | Polling als Basis, Webhooks später als Beschleuniger | robuster Start, später schneller | zwei Pfade testen |
| UI-Transport A | neuer Frame-Typ auf `WS /v1/sessions/updates` | eine Verbindung, Live-Push | Protokoll-Erweiterung in `sessionUpdatesSocket.ts` |
| UI-Transport B | TanStack-Query-Polling gegen den neuen Endpunkt (wie `usePullRequests.ts`) | einfach, bekanntes Muster | mehr Requests je Tab (nur gegen Omnigent, nicht gegen GitHub) |

## Q-2 Mac-Last in R1
| Option | Plus | Minus |
|---|---|---|
| 2a Should in R1 via psutil auf dem Runner-Host | beantwortet direkt „warum ist der Mac voll“ (G-1) | braucht einen Meldeweg vom Host (A-7) |
| 2b Erst R2 | weniger Umfang | Kernfrage vom 18.10. bleibt halb offen |
| 2c Nur Load-Average statt CPU/RAM | trivial (`os.getloadavg`) | weniger anschaulich als das Mockup |

## Q-3 Runner-Sicht
| Option | Plus | Minus |
|---|---|---|
| 3a GitHub Jobs-API (`runner_name` je Job) | ohne lokalen Zugriff; zeigt Job ↔ Runner | freie oder offline Runner nur über die Runner-Liste-API, ggf. Admin-Rechte (A-11) |
| 3b GitHub Runner-Liste-API (`/actions/runners`) | offizieller Online-/Busy-Status | Berechtigung unklar |
| 3c Lokal: `Runner.Worker`-Prozesse | sehr direkt, keine API-Kosten | nur, wenn Omnigent auf dem Runner-Mac läuft (A-7); Kopplung (R-3) |
| 3d Lokal: `_diag`-Logs in `~/actions-runner-omnigent*/` | Detail-Infos | fragiles Logformat, Parsing-Aufwand |
| 3e Kombination 3a + 3c | Belegung aus der API, Online/Offline lokal | zwei Quellen abgleichen |

## Q-4 Rollen & Zugriff
- 4a R1 nur Owner + eigene Agenten, alles lesend.
- 4b Die bestehende Session-Autorisierung (`omnigent/server/auth.py`) je PR anwenden: Session-Titel nur bei Zugriff.
- 4c Eigene Rolle „CI-Viewer“ für Teammitglieder (z. B. Ranga).

## Q-5 Startzeitpunkt
- 5a Sofort nach Spec-Freigabe.
- 5b Nach der R0-Demo am 21.10.
- 5c Zuerst ein Spike (A-9, A-10, A-11, A-17) vor dem Design, dann der Build.

## Q-6 Anker der Session-Karte
- 6a Unter dem `git push`-Tool-Call.
- 6b Unter dem `gh pr create`-Tool-Call.
- 6c Unter der ersten Nachricht, nach der `SessionPrRegistry` den PR kennt (`pr_observer.observe_tool_completion`).
- Mehrere Pushes: neue Karte je Push (alte als überholt) **oder** eine Karte, die sich aktualisiert und eine Historie zeigt.

## Q-7 Quelle der Pflicht-Checks
- 7a Omnigent-Konfiguration (Default: die drei Namen).
- 7b GitHub Branch Protection/Rulesets lesen (`required_status_checks`).
- 7c Konfiguration + Warnung, wenn sie von GitHub abweicht.

## Q-8 Filter „Nur meine“
- 8a PRs aus Sessions des angemeldeten Nutzers.
- 8b PRs mit GitHub-Autor = verbundenes Konto.
- 8c Filter weglassen.

## Q-9 Session-Liste ohne PR
- 9a „kein PR“ als Text (Mockup).
- 9b Leer, Info nur im Hover.

## Q-10 Motion-Abhängigkeit für interior.dev
- 10a `motion` aufnehmen; interior.dev-Komponenten per `npx shadcn@latest add https://www.interior.dev/r/<name>.json` nach `web/src/components/interior/`, danach auf Tokens anpassen.
- 10b Ohne Motion: interior.dev-Verhalten mit CSS/`tw-animate-css` (bereits installiert, 1.4.0) nachbauen.
- 10c Nur einzelne Komponenten mit Motion (z. B. Segmented Control für die Panel-Tabs), Rest per CSS.
- Stack-Abgleich: React 19.3.1 ✓, Tailwind 4.3.1 ✓, shadcn-CLI + `web/components.json` (style `radix-nova`) ✓, Motion ✗.

## Q-11 Panel-Ort
- 11a Eigenes globales rechtes Panel (Mockup Nr. 1).
- 11b Neuer Tab im `WorkspacePanel` (pro Session) und global nur der Indikator.
- 11c Eigene Seite/Route „CI“.

## Q-12 Index PR → Session
- 12a Scan aller `data_dir()/github/session-prs/*.json` + In-Memory-Index.
- 12b Neue DB-Tabelle, die bei `SessionPrRegistry.record()` mitgeschrieben wird.
- 12c Fallback über `conversation.git_branch` ↔ PR-`headRefName`.

## Q-13 MCP-Schnittstelle
- 13a Neue Action in `session_github` (braucht `session_id`, passt schlecht zur globalen Sicht).
- 13b Neues Tool `ci_status` (global, optional Filter PR/Session).
- 13c Beides: `session_github info` mit Pflicht-Flag (FR-053) + neues globales Tool.

## Q-14 Themes
- 14a Alle Omnigent-Themes über Tokens.
- 14b R1 nur Dark, Light in R2.

## Q-15 Tab „Verlauf“
- 15a Abgeschlossene Läufe der letzten 24 h.
- 15b Weglassen in R1, Platz für die Runner-Zeitleiste (Nr. 3) in R2.
- 15c Statistiken (Ø Dauer je Check, Rot-Quote).
