# Spec: Omnigent Chat (Slack-artig, Menschen + Agenten + Grok Bots)

Status: **Stufen 0–7 als Entwurf fertig, wartet auf Freigabe durch Jens** (Stand 2026-10-16, 23:40 MESZ). Es wurden keine Issues, PRs oder Kommentare angelegt.
Repo: `jholze/omnigent` (Fork). Code-Stand geprüft: main `87f6f08` + PR #4 `616ea2f`, #5 `8a703a3`, #14 `a2fcbe1`, #15 `a3014eb`.
Termine: R0-Demo Mi 21.10.2026 · R1-Launch Sa/So 24./25.10.2026 · R1.1 kurz nach Launch · R2 später.

## Index
| Datei | Stufe | Version | Status | Inhalt |
|---|---|---|---|---|
| `00-intake.md` | 0 | v0.1 | freigegeben (Jens) | Problem, Rahmen, Annahmen |
| `01-goals.md` | 1 | v0.1 | freigegeben (Jens) | Vision, Ziele G-1…G-4 |
| `02-story-map.md` | 2 | v0.3 (Changelog; Kopfzeile noch „v0.1“) | freigegeben (Jens) | Story Map, Releases |
| `03-domain.md` | 3 | v0.3 | in Review | Event Storming, Kontexte, Glossar, Zustände, Example Maps |
| `04-requirements.md` | 4 | v0.4 | in Review | 90 FR (EARS), 30 NFR (ISO 25010), 148 AC (GWT), Traceability, Annahmen, Fragen, Risiken |
| `05-design.md` | 5 | v0.4 | in Review | Architektur, Datenmodell, API/WS/MCP, Sicherheit, UX-Basis, Tests, Rollout |
| `adr/0001…0008` | 5 | accepted | – | Datenmodell, Session-Chat als Channel, Echtzeit über WS, Agentenkonten, Agent-Threads, ein Flag, Chat-Outbox (R1.1), Motion/AI Elements/interior.dev (amended) |
| `06-review.md` | 6 | v1.2 | – | 29148/INVEST-Review; Verdict „Approve with minor fixes“ nach Fixes |
| `07-tasks.md` | 7 | v0.3 | Entwurf | R0 10 · R1 23 · R1.1 2 Tasks, Waves, Coverage, AGENTS.md-Vorschläge, Issue-Mapping, Platzhalter Playwright-Struktur |
| `ref-okr-pm-playwright.md` | – | – | Referenz | Antwort der OKR-PM-Session zu Frontend-Tests (Input für Jens' Nachtrag in 07) |

## Entscheidungen seit Freigabe der Stufen 0–2
- 16.10. 23:25 Q-18: Webhook-Wecken der Grok Bots → R1.1; in R1 Polling-Routine (≥ 5 min) über MCP `chat_unread` (FR-088).
- 16.10. 23:25 React 19 (Issue #16, `feat/react-19`) wird vor der Chat-UI gemergt (Branch liegt lokal in eigenem Worktree auf Jens' Mac, Session `6c30fca5…`, kein Push ohne OK → Abhängigkeit D-1).
- 16.10. 23:25 Laufende Omnigent-Sessions bleiben laufen; neue Sessions in `omnigent-src` nutzen Hindsight `coding-agent::omnigent`, erster Schritt `hindsight_diagnose`.

## Changelog
- 2026-10-16 04/05 v0.4, 06 v1.2, 07 v0.3, ADR-0008 amended: interior.dev TaskSteps als Referenz für Agent-Schritte (FR-089/090, Q-20 Lizenz); Q-19 gelöst.
- 2026-10-16 v0.3 (03/04/05), 07 v0.2, 06 v1.1: Entscheidungen Jens 23:25 eingearbeitet.
- 2026-10-16 v0.2 (03/04/05): Fixes der Review-Findings #1–#13 (Critical: Session-Chat-Duplikat für Agent-Sessions, Pflicht-`host_id`).
- 2026-10-16 v0.1 (03/04/05, ADRs): erste Fassung der Stufen 3–5.
