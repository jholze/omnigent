# Review of Omnigent Chat spec v0.1 (Nachtrag v0.3) (03-domain, 04-requirements, 05-design, ADR-0001…0008) — 2026-10-16
Verdict (v0.1): **Revise** → nach Anwendung der Fixes #1–#13 in v0.2: **Approve with minor fixes**
Inventory v0.1: G 4 · ST 39 (R0 8 · R1 22 · R2 9, Epic-Skizze) · FR 84 · NFR 30 · AC 127 · open Q 10 · A 18
Inventory v0.2: G 4 · ST 39 · FR 87 (Must 70 · Should 14 · Could 3) · NFR 30 · AC 142 · open Q 10 (Q-3, 6, 7, 12–18) · A 22
Inventory v0.3 (nach Entscheidungen Jens 16.10. 23:25): G 4 · ST 39 · FR 88 (Must 69 · Should 16 · Could 3; davon 3 Should in R1.1) · NFR 30 · AC 144 · open Q 10 (Q-3, 6, 7, 12–17, 19; Q-18 entschieden) · A 23 · Tasks R0 10 · R1 23 · R1.1 2

Methode: ISO/IEC/IEEE 29148 (Einzel- und Mengenkriterien), INVEST, Scans auf Mehrdeutigkeit, Widersprüche, Testbarkeit, Traceability, Bestandssystem und Agent-Readiness. Code-Behauptungen in 05/ADRs wurden stichprobenartig erneut gegen `jholze/omnigent` (main `87f6f08` + PR-Branches) geprüft. Das Skill sieht Fixes erst nach OK des Owners vor; Jens hat für diesen Lauf ausdrücklich „Critical/Major beheben“ beauftragt.

## Findings
| # | Severity | Item | Criterion | Finding | Proposed fix | Status |
|---|---|---|---|---|---|---|
| 1 | Critical | FR-029 ↔ FR-034, ADR-0005 | Consistent / Correct | Agent-Thread-Sessions bekommen READ-Grants für alle Channel-Mitglieder → erfüllen die Session-Chat-Regel S-1 (≥ 2 direkte Grants) → jede Agent-Session erzeugt einen zweiten, doppelten Chat neben dem Thread. | Sessions mit Label `omnigent.chat.v1` von FR-029 ausnehmen; AC-029c; Adapter antwortet 404 `no_session_chat`. | behoben (04/03/05 v0.2) |
| 2 | Critical | FR-035, A-8, 05 §Orchestrierung | Feasible / Correct | `host_id` war Pflicht (`no_host`). Der heutige Create-Pfad erlaubt Sessions ohne `host_id` (vom Aufrufer verwalteter Runner); der E2E-`live_server` läuft genau so. Mit der Pflicht wäre jede R0-E2E und jedes Projekt ohne Host-Bindung `start_failed`. | `host_id` optional; Klasse `no_host` entfernt; Ex 5b in 03. | behoben |
| 3 | Major | ADR-0005, Sidebar | Complete | READ-Grants lösen `session_added` aus → Agent-Sessions fluten die Haupt-Sidebar aller Channel-Mitglieder (UX ist harte Anforderung). Kein FR dazu. | Neue FR-085 (Must R1) + AC-085a/b; Komponente „Sidebar-Filter“ in 05. | behoben |
| 4 | Major | `chat_threads.agent_state=starting` | Complete (Fehlerpfade) | Server-Neustart während des Starts lässt den Thread für immer auf „startet…“. | Neue FR-087 (Must R0, Timeout 120 s → `start_failed/timeout`) + AC-087a; Spalte `start_requested_at`. | behoben |
| 5 | Major | FR-021 ↔ FR-067 ↔ Q-13 | Consistent | FR-067 verbietet Bots in DMs ohne Owner (Default Q-13), FR-021 legte solche DMs trotzdem an; FR-071 hätte sie geweckt. | FR-021 um 403 `agent_owner_required` ergänzt; AC-021c. | behoben |
| 6 | Major | FR-071, `chat_threads.followers` | Unambiguous | „Thread-Folgen“ für Bots ohne definiertes Ende (kein Entlassen für Bots) → unklare Weckregel, Kostenrisiko. | Weckregel auf Mention/DM reduziert (A-22); `followers` entfernt; Webhook-Grund `mention|dm`. | behoben |
| 7 | Major | FR-004 `directory` | Unambiguous / Feasible | Herkunft des Verzeichnisses war offen; im Header-/OIDC-Modus gibt es keine Nutzerliste. DMs (FR-021) und Autocomplete (FR-026) hängen davon ab. | A-21: Mitglieder von `#general` + aktive Agentenkonten; in 05 beschrieben. | behoben |
| 8 | Major | FR-002, 025, 043, 044, 047, 052, 055, 062, 082 | Verifiable (Must ⇒ ≥ 1 Fehler-/Randfall) | Diese Must-FRs hatten nur einen Happy-Path-AC. | AC-002b, 025b, 043b, 044b, 047b, 052b, 055b, 062b, 082b ergänzt. | behoben |
| 9 | Major | Gesamtmenge | Feasible as a whole (Must ≤ ~60 %) | 74 von 84 FR waren Must; geschätzt ≈ 75 % des Aufwands bei 6 Arbeitstagen bis Launch. | FR-043, 054, 063, 083 → Should; Cut-Line in 05; Rest als Q-18 an Jens. | **Q-18 ENTSCHIEDEN** (16.10. 23:25): Webhook-Wecken (FR-071…073, NFR-019, T-108) → Should/R1.1; R1 weckt per Polling ≥ 5 min über `chat_unread` (neu FR-088). Restrisiko: Must-Anteil nach Task-Schätzung ≈ 81 % der Feature-Aufwände (07); von Jens bewusst akzeptiert, Puffer nur über Cut-Line. |
| 10 | Major | Q-17 | Complete | Default „spätere Mitglieder bekommen READ“ ohne FR. | FR-086 (Should R1) + AC-086a; Leave entzieht nichts (A-20). | behoben |
| 11 | Major | 05 §Orchestrierung | Correct (Bestandssystem) | `_create_session_from_existing_agent` war `routes_core.py` zugeordnet; tatsächlich `omnigent/server/routes/_sessions/orchestration.py:10365`. | Pfad korrigiert; zusätzlich `summary_lookup` aus PR #15 geprüft (neueste Assistant-Message unter den letzten 8 Items, synchron → `asyncio.to_thread`). | behoben |
| 12 | Major | 03 Hotspots | Consistent | Hotspot-Zuordnung verwies für H3/H4 auf falsche FRs (FR-033/FR-036 statt FR-034/FR-041). | Zuordnung H1…H5 einzeln korrigiert. | behoben |
| 13 | Major | A-19 | Conforming | Annahme in 05 eingeführt, fehlte in der Annahmenliste von 04. | A-19 in 04 §11. | behoben |
| 14 | Minor | FR-078 | Singular | Ein FR bündelt fünf Animationen; Werte stehen in NFR-007. | Bei v1.0 in FR-078a…e splitten oder FR auf „gemäß NFR-007-Tabelle“ reduzieren. | offen |
| 15 | Minor | FR-011 | Singular | Drei Auslöser (Mention, DM, Agent-Frage) in einem FR. | Splitten, falls einzeln testbar gewünscht. | offen |
| 16 | Minor | FR-019 | Singular | Paging-Modi `tail`/`before`/`after` in einem FR. | Akzeptabel (eine API); ggf. splitten. | offen |
| 17 | Minor | NFR-001 | Verifiable | Messverfahren „Loadtest-Skript“ existiert noch nicht (`tests/loadtest/` enthält nur Helfer). | In T-119 als Skript `tests/loadtest/test_chat_latency.py` festgelegt. | über Task gelöst |
| 18 | Minor | FR-005 | Correct | Should, aber Demo braucht `#billing` → Demo-Daten per API-Seed statt UI möglich. | Seed im E2E-Fixture; FR-005 bleibt Should. | offen (OK) |
| 19 | Minor | FR-031 / ADR-0006 | Consistent | PR-#4-Tests laufen ohne Flag; nach ADR-0006 ist der Session-Chat ohne `team_chat` aus. | In T-102 Tests auf Flag-App umstellen (im Task festgehalten). | über Task gelöst |
| 20 | Minor | Glossar | Consistent | „Folgen“ meint bei Threads (Unread, FR-008) etwas anderes als „Agent folgt“ (FR-040). | Begriff „Thread abonniert“ für FR-008 erwägen. | offen |
| 21 | Minor | `chat_members.principal_id` | Consistent | Länge 160 vs. `created_by` 128 in PR #4. | Einheitlich 160 bei der Migration. | offen |
| 22 | Minor | Single-User-Modus | Complete | Verhalten ohne Auth (ein Principal `local`) nicht explizit; funktioniert, aber DMs sind sinnlos. | Hinweis in 05: DM-Button verborgen, wenn Verzeichnis nur aus mir besteht. | offen |
| 24 | Minor | T-003, T-114 (07) | Small (Task ≤ ~5 Checks) | T-003 zitiert 21 ACs, T-114 11 ACs. | Bei Bedarf T-003 in Channels/DMs und Messages/Frames teilen. | offen |
| 25 | Major | C-1, NFR-021, Non-goals | Correct (Owner-Entscheidung) | Jens hat React 19 (#16) zur Voraussetzung der Chat-UI gemacht; Spec ging von React 18 aus. Branch `feat/react-19` auf `jholze/omnigent` nicht auffindbar. | C-1/NFR-021/Non-goal angepasst, R-15, Q-19, Abhängigkeit an T-006/T-007/T-008. | behoben (v0.3); Q-19 gelöst in v0.4 (D-1) |
| 23 | Minor | R2-Stories | Estimable | Nur Epic-Skizzen, absichtlich. | Vor R2 eigener Spec-Durchlauf. | akzeptiert |

INVEST-Prüfung: Alle R0-Stories sind unabhängig genug für vertikale Slices; ST-3.1.1 ist die größte (Orchestrator + UI). Sie wurde in T-005 (Server) und T-008 (UI) geteilt. ST-7.1.x ist klein geschnitten. ST-UX-2 ist eine Prozess-Story ohne Nutzerfunktion, wird als NFR-027 + Review-Task geführt.

## Traceability gaps
- v0.1: FR-085…087 fehlten (siehe #3, #4, #10). Behoben, Matrix in 04 §10 ergänzt.
- Keine verwaisten Tasks: jeder Task in `07-tasks.md` verweist auf FR/NFR/AC, außer Setup (T-000) und Release (T-123).
- R2-Stories haben bewusst keine FRs.

## Blocking open questions
| Q | Frage | blockiert | fällig |
|---|---|---|---|
| Q-12 | Projekt-/Host-Auflösung für Agent-Start (A-8, Host jetzt optional) ok? | T-005 | Mo 19.10. |
| Q-13 | Bot in DM ohne Owner (Default nein) | T-106 | Mi 21.10. |
| Q-17 | Spätere Mitglieder READ (Default ja, FR-086) | T-103 | Do 22.10. |
Gelöst: Q-19 (Branch lokal auf Jens' Mac, Session `6c30fca5…`) → Abhängigkeit D-1. Neu, nicht launch-blockierend: Q-20 (Lizenz interior.dev; ohne Klärung wird nachgebaut). Nicht blockierend: Q-3, Q-6, Q-7, Q-15, Q-16 (Defaults gelten); Q-14 nur noch für R1.1. Q-18 entschieden.

## Strengths (keep)
- Jede exists/adapt-Angabe nennt Datei/Zeile; Bestands-Patterns (WS-Watch, `_publish_status`, Outbox, Signatur) werden wiederverwendet statt neu erfunden.
- Konkrete Testdaten (Anna/Jens/Bob/Carla, `#billing`, `grok-jens`) und E2E-Markierung je UI-AC; Playwright-Pflicht als NFR-023/024 verankert.
- Sicherheitsmodell klar: Bot ⊆ Owner, Token gehasht, keine Bodies in Frames/Logs/Webhooks, SSRF-Prüfung über `omnigent/util/ssrf.py`.
- Risiken mit Gegenmaßnahmen und ehrlicher Termineinschätzung (R-1, R-2, R-5).

## Nachtrag 3 (2026-10-17): MCP-Bot-Pfad – Selbstcheck
Anlass: Gap-Analyse des gemergten MCP-Endpoints (PR #14) und MCP-Spec 2026-07-28; Änderung freigegeben von Jens 17.10.2026 19:19. Kein vollständiger 29148-Review, sondern Selbstcheck der geänderten Teile (vollständiger Review-Pass empfohlen vor Freigabe 04/05 v0.5).

| Prüfung | Ergebnis |
|---|---|
| IDs eindeutig (FR, NFR, AC, T, Q, A, R, C, ADR) | ok – keine Doppelvergabe; neu FR-091…FR-100, NFR-031, AC-069c/070c/070d/074b/076b/088c/091a/092a–c/093a/094a/b/095a/b/096a/097a/b/098a/b/099a/100a/b, T-125…T-131, Q-21…Q-25, A-24…A-28, R-17…R-20, C-10/C-11, ADR-0009 |
| Jede neue FR ≥ 1 AC | ok (FR-091 → AC-091a, AC-074b; FR-092 → AC-092a–c; FR-093 → AC-093a; FR-094 → AC-094a/b; FR-095 → AC-095a/b; FR-096 → AC-096a; FR-097 → AC-097a/b; FR-098 → AC-098a/b; FR-099 → AC-099a; FR-100 → AC-100a/b) |
| Jede Must-FR ≥ 1 Fehler-/Randfall | ok (FR-091 Fehler per Definition; FR-092 AC-092b/c; FR-095 AC-095b; FR-097 AC-097b; FR-098 AC-098a; FR-099 Fehler per Definition; FR-096 nur Happy Path – Beschreibungstext ist statisch, akzeptiert) |
| Jede neue FR/AC ≥ 1 Task | ok (Coverage `07-tasks.md` v0.4) |
| Traceability Goal → Story → FR → AC → Task | ok für ST-7.1.1/7.1.2/7.1.3/7.1.5/7.1.6 (04 §10) |
| Präzisierungen statt Duplikate | FR-070 (Allowlist), FR-074 (MCP-Code), FR-076 (`last_seen_at`), NFR-013/017/028 präzisiert; FR-092 konkretisiert NFR-017 als Verhalten, FR-100 konkretisiert NFR-028 |
| Unsicherheiten markiert | A-24…A-28 als [ANNAHME]; Q-21…Q-25 mit empfohlenem Default |

Inventory v0.5: G 4 · ST 39 · FR 100 (Must 77 · Should 20 · Could 3) · NFR 31 · AC 171 · open Q 15 (Q-3, 6, 7, 12–17, 20–25) · A 28 · Tasks R0 10 · R1 30 · R1.1 2.

Hinweis: Der Must-Anteil steigt weiter (07 Schätzung ≈ 80 % nach Aufwand); Cut-Line in 05 §Rollout um FR-094, FR-100, FR-093 ergänzt.

## Nachtrag 4 (2026-10-17): Bots mit Owner-Rechten – Selbstcheck
Anlass: Entscheidung Jens Sa 17.10.2026 20:11 „Bots dürfen alles, was der Owner darf.“ (ADR-0010 ersetzt ADR-0009 Punkt 1). Kein unabhängiger 29148-Review, nur Selbstcheck der geänderten Teile; **spec-quality-review für 04/05 v0.6 steht aus** (Schwerpunkt Security: R-21, Q-25, Q-26).

| Prüfung | Ergebnis |
|---|---|
| IDs eindeutig | ok – neu FR-101…FR-104, AC-036b, AC-068b–e, AC-070e, AC-077b, AC-100c, AC-101a/b, AC-102a, AC-103a/b, AC-104a/b, A-29…A-32, Q-26…Q-28, R-21/R-22, ADR-0010; keine ID wiederverwendet (FR-036/068/070/077 behalten ihre IDs mit neuem Inhalt, Changelog 04 v0.6) |
| Jede neue/geänderte FR ≥ 1 AC | ok (FR-036 → AC-036a/b; FR-068 → AC-068a–e; FR-070 → AC-070a–e, AC-069c; FR-077 → AC-077a/b; FR-100 → AC-100a–c; FR-101 → AC-101a/b, AC-070c/d; FR-102 → AC-102a; FR-103 → AC-103a/b; FR-104 → AC-104a/b) |
| Jede Must-FR ≥ 1 Fehler-/Randfall | ok (AC-036b, AC-068b/c/d, AC-070c/e, AC-077b, AC-100c, AC-101b, AC-102a, AC-103a Marker) |
| ACs 04 = 07 | ok – alle 186 ACs wortgleich in genau einem Task zitiert (automatischer Abgleich) |
| Jede FR/NFR ≥ 1 Task | ok (Coverage `07-tasks.md` v0.5) |
| Verbotene Wörter / Mehrdeutigkeit | FR-068 ist eine „iff“-Formulierung (erlaubt + Ablehnungsfall) – akzeptiert, weil symmetrisch und testbar (AC-068a/b); FR-104 Grenzwerte bewusst [OFFEN Q-28] |
| Unsicherheiten markiert | A-29…A-32 als [ANNAHME]; Q-26 (Owner-Schalter) bewusst nicht entschieden; Q-24/Q-25 offen und als „relevanter“ markiert |

Inventory v0.6: G 4 · ST 39 · FR 104 (Must 81 · Should 20 · Could 3) · NFR 31 · AC 186 · open Q 18 (Q-3, 6, 7, 12–17, 20–28) · A 32 · Tasks R0 10 · R1 30 · R1.1 2 (R1 141 h).

## Changelog
- v1.4 (2026-10-17) Nachtrag 4: Selbstcheck „Bots mit Owner-Rechten“ (04/05 v0.6, 03 v0.5, 07 v0.5, ADR-0010); Review ausstehend.
- v1.3 (2026-10-17) Nachtrag 3: Selbstcheck MCP-Bot-Pfad (04/05 v0.5, 07 v0.4, ADR-0009).
- v1.2 (2026-10-16) Nachtrag 2: interior.dev TaskSteps (FR-089/090, 4 ACs, NFR-007-Werte, Q-20), Q-19 gelöst; Inventory v0.4: FR 90 (Must 70 · Should 17 · Could 3) · NFR 30 · AC 148 · open Q 10 (Q-3, 6, 7, 12–17, 20).
- v1.1 (2026-10-16) Nachtrag: Entscheidungen Jens 23:25 (Q-18 → R1.1/Polling, React 19 als Voraussetzung) in 03/04/05 v0.3 und 07 eingearbeitet; Findings #24, #25.
- v1 (2026-10-16) Review von v0.1; Fixes #1–#13 in 03/04/05 v0.2 angewandt; #14–#23 Minor offen bzw. über Tasks gelöst.
