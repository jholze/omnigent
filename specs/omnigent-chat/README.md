# Spec: Omnigent Chat (Slack-artig, Menschen + Agenten + Grok Bots)

Status: **Stufen 0–7 als Entwurf fertig, wartet auf Freigabe durch Jens** (Stand 2026-10-17, 20:15 MESZ; Nachtrag MCP-Bot-Pfad freigegeben von Jens 17.10. 19:19; v0.6 „Bots dürfen alles, was der Owner darf“ entschieden von Jens 17.10. 20:11). Es wurden keine Issues, PRs oder Kommentare angelegt.
Repo: `jholze/omnigent` (Fork). Code-Stand geprüft: main `87f6f08` + PR #4 `616ea2f`, #5 `8a703a3`, #14 `a2fcbe1`, #15 `a3014eb`.
Termine: R0-Demo Mi 21.10.2026 · R1-Launch Sa/So 24./25.10.2026 · R1.1 kurz nach Launch · R2 später.

## Index
| Datei | Stufe | Version | Status | Inhalt |
|---|---|---|---|---|
| `00-intake.md` | 0 | v0.1 | freigegeben (Jens) | Problem, Rahmen, Annahmen |
| `01-goals.md` | 1 | v0.1 | freigegeben (Jens) | Vision, Ziele G-1…G-4 |
| `02-story-map.md` | 2 | v0.3 (Changelog; Kopfzeile noch „v0.1“) | freigegeben (Jens) | Story Map, Releases |
| `03-domain.md` | 3 | v0.5 | in Review | Event Storming, Kontexte, Glossar, Zustände, Example Maps |
| `04-requirements.md` | 4 | v0.6 | in Review | 104 FR (EARS; Must 81 · Should 20 · Could 3), 31 NFR (ISO 25010), 186 AC (GWT), Traceability, Annahmen, Fragen, Risiken |
| `05-design.md` | 5 | v0.6 | in Review | Architektur, Datenmodell, API/WS/MCP, Sicherheit, UX-Basis, Tests, Rollout |
| `adr/0001…0010` | 5 | accepted | – | Datenmodell, Session-Chat als Channel, Echtzeit über WS, Agentenkonten, Agent-Threads, ein Flag, Chat-Outbox (R1.1), Motion/AI Elements/interior.dev (amended), MCP-Bot-Pfad: Fehlercodes/Protokoll (Allowlist-Teil superseded), ADR-0010 Bots mit Owner-Rechten (neu; amends ADR-0004) |
| `06-review.md` | 6 | v1.4 | Review v0.6 ausstehend | 29148/INVEST-Review; Verdict „Approve with minor fixes“ nach Fixes |
| `07-tasks.md` | 7 | v0.5 | Entwurf | R0 10 · R1 30 · R1.1 2 Tasks, Block „MCP-Bot-Pfad“, Waves, Coverage, AGENTS.md-Vorschläge, Issue-Mapping, Platzhalter Playwright-Struktur |
| `ref-okr-pm-playwright.md` | – | – | Referenz | Antwort der OKR-PM-Session zu Frontend-Tests (Input für Jens' Nachtrag in 07) |

## Entscheidungen seit Freigabe der Stufen 0–2
- 17.10. 20:11 „Bots dürfen alles, was der Owner darf.“: Agentenkonto-Tokens (`ogc_`) erhalten über MCP die vollen Rechte ihres Owners (alle ~25 MCP-Tools + `chat_*`), Bot ⊆ Owner, Widerruf jederzeit; ersetzt die Allowlist (ADR-0010). FR-036/068/070/077/100 neu formuliert, neu FR-101…FR-104; Kostenbremse über Grenzen (Rate-Limit, `bot_streak_limit`, optionales Kontingent [OFFEN Q-28]); Q-25 bleibt offen (jetzt relevanter), neu Q-26 (Owner-Schalter für Hochrisiko-Tools, nicht entschieden), Q-27, Q-28.
- 16.10. 23:25 Q-18: Webhook-Wecken der Grok Bots → R1.1; in R1 Polling-Routine (≥ 5 min) über MCP `chat_unread` (FR-088).
- 16.10. 23:25 React 19 (Issue #16, `feat/react-19`) wird vor der Chat-UI gemergt (Branch liegt lokal in eigenem Worktree auf Jens' Mac, Session `6c30fca5…`, kein Push ohne OK → Abhängigkeit D-1).
- 17.10. 19:19 MCP-Bot-Pfad („Ok. Mach und umbauen.“): neue FR-091…FR-100, NFR-031; Bot-Tasks (T-126, T-105, T-101, T-106, T-125, T-109, T-107, T-127, T-128, T-131, T-119, T-129) direkt nach React 19 bzw. parallel zur R0-UI; MCP-Tasks-Extension, `subscriptions/listen`, OAuth 2.1/PRM/CIMD, Server Card, MCP Apps, A2A und Protokoll 2026-07-28 → Später/R2 (Q-21).
- 16.10. 23:25 Laufende Omnigent-Sessions bleiben laufen; neue Sessions in `omnigent-src` nutzen Hindsight `coding-agent::omnigent`, erster Schritt `hindsight_diagnose`.

## Changelog
- 2026-10-17 04/05 v0.6, 03 v0.5, 06 v1.4, 07 v0.5, ADR-0010 neu, ADR-0009/0004 amended: Bots mit Owner-Rechten (Entscheidung Jens 20:11). Stand-Kopie vorher: `/workspace/specs/.omnigent-chat.bak-20261017-2015` (Box). Paket: `/workspace/specs/omnigent-chat-2026-10-17-v0.6.tgz`.
- 2026-10-17 04/05 v0.5, 03 v0.4, 06 v1.3, 07 v0.4, ADR-0009 neu: MCP-Bot-Pfad nach Gap-Analyse des gemergten MCP-Endpoints (PR #14) und MCP-Spec 2026-07-28 – stabile Fehlercodes (`bot_streak_limit`, `rate_limited`), Annotationen/Structured Output, Untrusted-Markierung, Protokollversionen bis 2025-11-25, Allowlist, MCP-Audit/`last_seen_at`, Grok-Bot-Smoke; Q-21…Q-25; Tasks T-125…T-131. Stand-Kopie vorher: `/workspace/specs/.omnigent-chat.bak-20261017-1920` (Box).
- 2026-10-16 04/05 v0.4, 06 v1.2, 07 v0.3, ADR-0008 amended: interior.dev TaskSteps als Referenz für Agent-Schritte (FR-089/090, Q-20 Lizenz); Q-19 gelöst.
- 2026-10-16 v0.3 (03/04/05), 07 v0.2, 06 v1.1: Entscheidungen Jens 23:25 eingearbeitet.
- 2026-10-16 v0.2 (03/04/05): Fixes der Review-Findings #1–#13 (Critical: Session-Chat-Duplikat für Agent-Sessions, Pflicht-`host_id`).
- 2026-10-16 v0.1 (03/04/05, ADRs): erste Fassung der Stufen 3–5.
