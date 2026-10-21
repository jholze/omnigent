# Agent-Provenance & Post-Merge-Verify – Requirements Spec

Version: 0.1 · Date: 2026-10-21 · Status: draft · Owner: Jens Holze

Quellen: `00-intake.md` v0.1. Code geprüft: `jholze/omnigent` `main` (Stand 2026-10-21). Status je Item: **exists** / **adapt** / **new** mit Pfad. Unsicherheit: `[ANNAHME A-n]`, `[OFFEN Q-n]`. Syntax: EARS (FR/NFR), Given-When-Then (AC).

## Changelog
| Version | Datum | Änderung |
|---|---|---|
| 0.1 | 2026-10-21 | Erster Entwurf (Quick Path), inkl. Quality Review §9 |

## 1. Kontext & Problem
Siehe `00-intake.md`. Agenten committen heute ohne Herkunftsangabe; Fork CI prüft nur PRs, nicht den Tip nach dem Merge.

## 2. Ziele
| ID | Ziel | Messung |
|---|---|---|
| G-1 | Jede Agent-Änderung ist Agent/Session/Modell zuordenbar | 100 % der Agent-Commits in gemergten PRs ab Fail-Modus mit gültigen Trailern (`git log --format='%(trailers)'`) |
| G-2 | Review-Matrix prüfbar | PR-Body listet Agenten + Modelle; Reviewer erkennt Vendor ohne Rückfrage |
| G-3 | Roter Tip wird automatisch eingegrenzt | Revert-PR + Notify bei jedem echten Rot; [ANNAHME A-1] < 60 min |
| G-4 | Kein Risiko für Prod/Shared Branches | 0 direkte Pushes auf `main`/`epic/**` durch den Workflow |

## 3. Non-Goals
- Kein direkter Push/Force-Push auf `main` oder `epic/**`; kein Auto-Merge des Revert-PR. [ANNAHME A-3]
- Kein Deploy, kein Aufruf von `scripts/fork/deploy-local.sh`, keine launchd-/Prod-Änderung. [ANNAHME A-5]
- Keine GitHub-hosted Runner (`ubuntu-latest` o. ä.). Nur `[self-hosted, macOS, ARM64, omnigent-ci]`.
- Keine Änderungen an Upstream (`omnigent-ai/omnigent`), kein Push dorthin; Upstream-Workflows (`ci.yml` etc.) bleiben unverändert.
- Keine kryptografische Signatur/Attestation der Trailer (nur Konvention + Check).
- Keine Attribution auf Zeilenebene (git-ai „blame per line“) – nur Commit/PR-Ebene.
- Kein UI in `web/`.
- Kein Bisect auf anderen Branches als `epic/team-chat` (bzw. `epic/**`, Q-1) und `main`. [ANNAHME A-4]
- Kein Code aus `feder-cr/git-ai` kopieren; nur Ideen.

## 4. Bestandsaufnahme: wie Agenten heute committen
| Akteur | Heute | Status für Feature |
|---|---|---|
| `coder`, `coder_deepseek`, `coder_grok` | committen/pushen nicht (Prompt + `blast_radius gate_pushes: true`) | exists – keine Änderung außer Session/Modell an Lead melden |
| `ux-builder` | committet lokal auf Feature-Branch | adapt – Helper nutzen |
| `devops` | Gate + Push Feature-Branch; Commit-Pfad nicht explizit | adapt – committet via Helper, prüft Trailer vor Push |
| Lead / Development-Session | Briefs mit Pinned SHA, RESULT | adapt – gibt Agent/Session/Modell im Brief mit, baut PR-Body |
| Cursor-Cloud-Agent | `Cursor Agent` + `Co-authored-by: Jens Holze` | adapt – zusätzlich Omnigent-Trailer per Hook/Template |
| `AGENTS.md` §Committing | nur pre-commit | adapt |
| `scripts/fork/` | kein Commit-Helper | new |
| `notify-spec.sh` | extern (nicht im Repo) [OFFEN Q-3] | exists (extern) / adapt |
| `.github/workflows/fork-ci.yml` | PR-CI self-hosted, Suites python-lint/pytest/web | exists – Suites wiederverwenden |

## 5. Funktionale Anforderungen

### A. Agent-Attribution
| ID | Anforderung (EARS) | Status / Pfad |
|---|---|---|
| FR-001 | Das System SOLL eine Trailer-Konvention definieren: `Omnigent-Agent: <role>` (z. B. `coder_deepseek`), `Omnigent-Session: <32-hex session id>`, `Model: <model id>` (z. B. `deepseek-v4-pro`, `claude-opus-5-5`); optional `Omnigent-Task: <task-id>`. | new `docs/fork/agent-provenance.md` |
| FR-002 | WENN ein Agent über `scripts/fork/agent-commit.sh` committet, DANN SOLL der Helper die drei Pflicht-Trailer aus `OMNIGENT_AGENT`, `OMNIGENT_SESSION_ID`, `OMNIGENT_MODEL` (oder Flags) per `git interpret-trailers` anhängen. | new `scripts/fork/agent-commit.sh` |
| FR-003 | WENN eine Pflicht-Variable fehlt oder die Session-ID nicht `^[0-9a-f]{32}$` entspricht, DANN SOLL der Helper mit Exit ≠ 0 abbrechen und keinen Commit erzeugen. | new |
| FR-004 | Der Lead SOLL in jedem HANDOFF/Brief `Agent`, `Session`, `Model` der ausführenden Rolle mitgeben; `devops` und `ux-builder` SOLLEN ausschließlich über den Helper committen. | adapt `agents/lead/config.yaml`, `agents/lead/agents/devops/config.yaml` |
| FR-005 | `AGENTS.md` §Committing SOLL die Trailer-Regel für Agent-Commits (inkl. Cursor-Cloud-Agent) nennen; menschliche Commits sind ausgenommen. | adapt `AGENTS.md` (Fork-Abschnitt) |
| FR-006 | WENN ein PR erstellt/aktualisiert wird, DANN SOLL `scripts/fork/pr-agents.sh <base> <head>` aus den Trailern einen Markdown-Abschnitt „Contributing agents“ (Agent, Model, Session, Anzahl Commits) erzeugen, den Lead/devops in den PR-Body setzen. | new `scripts/fork/pr-agents.sh` |
| FR-007 | WENN ein PR gegen `main` oder `epic/**` von einem Agent-Branch [ANNAHME A-6] geöffnet/synchronisiert wird, DANN SOLL der Job `agent-provenance` jeden Nicht-Merge-Commit `base..head` auf Pflicht-Trailer prüfen und fehlende als Annotation melden. | new Job in `.github/workflows/fork-ci.yml` (adapt) |
| FR-008 | SOLANGE `AGENT_PROVENANCE_MODE=warn` (Default Phase 1), SOLL der Job grün enden; WENN `fail`, SOLL er bei fehlenden Trailern rot enden. | new |
| FR-009 | Der Check SOLL Upstream-Sync-Branches (`sync/upstream-*`), Merge-Commits und Commits mit Autor `jholze`/`Jens Holze` ohne Agent-Trailer überspringen. | new |

### B. Post-Merge-Verify mit Culprit-Revert
| ID | Anforderung (EARS) | Status / Pfad |
|---|---|---|
| FR-020 | WENN ein Push (Merge) auf `epic/team-chat` oder `main` erfolgt, DANN SOLL `post-merge-verify.yml` auf `[self-hosted, macOS, ARM64, omnigent-ci]` die Pflicht-Suites auf dem neuen Tip ausführen. | new `.github/workflows/post-merge-verify.yml` |
| FR-021 | Die Pflicht-Suites SOLLEN dieselben blockierenden Befehle wie `fork-ci.yml` sein (python-lint, pytest, web test+build), extrahiert in ein gemeinsames Skript; e2e-ui bleibt ausgeschlossen (non-blocking). | new `scripts/fork/verify-suites.sh`, adapt `fork-ci.yml` |
| FR-022 | WENN die Suites rot sind, DANN SOLL der Workflow die roten Suites genau einmal erneut ausführen; WENN der Rerun grün ist, DANN SOLL er als „flaky“ enden (Notify, kein Bisect, kein Revert). | new |
| FR-023 | Das System SOLL den letzten grünen Tip je Branch speichern (Git-Ref `refs/verify/<branch>/last-green`, [OFFEN Q-2]). | new |
| FR-024 | WENN der Rerun rot bleibt, DANN SOLL der Workflow über die First-Parent-Merges `last-green..tip` bisecten (`git bisect run` mit `--first-parent`), nur mit den roten Suites, und den ersten roten Merge als Culprit bestimmen. | new `scripts/fork/verify-bisect.sh` |
| FR-025 | WENN kein `last-green` existiert, DANN SOLL der Workflow höchstens die letzten N=10 First-Parent-Merges betrachten. | new |
| FR-026 | WENN ein Culprit bestimmt ist, DANN SOLL der Workflow einen Branch `revert/<branch-slug>/<short-sha>` mit `git revert -m 1 <culprit>` erstellen, nur diesen Branch pushen und einen **Revert-PR** gegen den betroffenen Branch öffnen (Body: Culprit, Original-PR, rote Tests, Bisect-Log, Agent-Trailer des Culprits). | new |
| FR-027 | Der Workflow SOLL nie auf `main`/`epic/**` pushen, nie mergen, nie Deploy-Skripte aufrufen. | new (Guard im Skript + Tests) |
| FR-028 | WENN Ergebnis grün, flaky, Culprit gefunden oder Bisect unklar ist, DANN SOLL der Workflow über das bestehende notify-spec-Webhook-Skript benachrichtigen (Status, Branch, Tip, Culprit, PR-URL, Run-URL). | adapt `notify-spec.sh` [OFFEN Q-3] |
| FR-029 | WENN der Bisect kein eindeutiges Ergebnis liefert (z. B. Revert konfliktbehaftet, alle Kandidaten grün), DANN SOLL kein Revert-PR entstehen, sondern eine „needs human“-Notify. | new |
| FR-030 | WENN für denselben Culprit schon ein offener Revert-PR existiert, DANN SOLL kein zweiter entstehen. | new |
| FR-031 | Verify-Läufe je Branch SOLLEN serialisiert sein (`concurrency` ohne cancel), damit Bisect nicht parallel läuft. | new |

## 6. Nicht-funktionale Anforderungen
| ID | Anforderung | Status |
|---|---|---|
| NFR-001 | Nur self-hosted Runner `omnigent-ci`; jeder Job mit Gate `github.repository == 'jholze/omnigent'`. | adapt (Muster `fork-ci.yml`) |
| NFR-002 | `permissions` minimal: `contents: write` nur für Revert-Branch-Push, `pull-requests: write`; sonst `contents: read`. Keine Secrets in Logs; Webhook-URL nur aus Secret/Runner-Env, maskiert. | new |
| NFR-003 | Timeout Verify-Job ≤ 180 min; Bisect ≤ ⌈log2(N)⌉+1 Suite-Läufe. | new |
| NFR-004 | Upstream-Sync-sicher: alle Änderungen in fork-only Dateien oder fork-only Abschnitten. | new |
| NFR-005 | Helper/Skripte bash + `shellcheck`-sauber, getestet mit `bats` oder pytest-Fixtures (temporäres Git-Repo). | new |
| NFR-006 | Trailer-Check läuft < 30 s. | new |
| NFR-007 | Workflow-Logs und PR-Bodies enthalten keine Tokens. | new |

## 7. Akzeptanzkriterien (Given-When-Then)
| ID | Bezug | Kriterium |
|---|---|---|
| AC-001 | FR-002 | Given gesetzte `OMNIGENT_AGENT/SESSION_ID/MODEL`, When `agent-commit.sh -m "x"`, Then enthält `git log -1 --format=%(trailers)` alle drei Trailer. |
| AC-002 | FR-003 | Given fehlendes `OMNIGENT_MODEL` oder Session-ID `abc`, When Helper läuft, Then Exit ≠ 0 und `git rev-parse HEAD` unverändert. |
| AC-003 | FR-006 | Given 3 Commits von 2 Agenten, When `pr-agents.sh base head`, Then Tabelle mit 2 Zeilen inkl. Model und Commit-Anzahl. |
| AC-004 | FR-007/008 | Given Agent-PR mit einem Commit ohne Trailer und Mode `warn`, When Fork CI läuft, Then Job grün mit `::warning::` für diesen SHA; Given Mode `fail`, Then Job rot. |
| AC-005 | FR-009 | Given `sync/upstream-*`-PR oder Merge-Commit oder menschlicher Commit, When Check läuft, Then keine Warnung. |
| AC-006 | FR-020/NFR-001 | Given ein Merge in `epic/team-chat`, When Push-Event, Then Run startet nur auf Label `omnigent-ci`; `rg 'runs-on' post-merge-verify.yml` zeigt nur self-hosted. |
| AC-007 | FR-022 | Given eine Suite schlägt einmal fehl und im Rerun nicht, When Verify läuft, Then Status `flaky`, kein Bisect, kein PR, eine Notify. |
| AC-008 | FR-024 | Given Fixture-Repo mit 5 Merges seit `last-green`, Merge 3 bricht Test, When `verify-bisect.sh`, Then Culprit = Merge 3 in ≤ 4 Suite-Läufen. |
| AC-009 | FR-026/027 | Given Culprit, When Workflow fertig, Then genau ein Revert-PR `revert/...` gegen den Branch existiert, und der Ziel-Branch-Tip ist unverändert. |
| AC-010 | FR-023 | Given grüner Lauf, When fertig, Then `refs/verify/<branch>/last-green` = Tip. |
| AC-011 | FR-028 | Given jeder Endstatus, When Workflow endet, Then genau ein Notify-Aufruf mit Status-Feld; Log enthält keine Webhook-URL. |
| AC-012 | FR-029/030 | Given Revert-Konflikt oder bestehender offener Revert-PR für denselben Culprit, When Workflow, Then kein neuer PR, Notify `needs-human` bzw. `duplicate`. |
| AC-013 | FR-004/005 | Given aktualisierte Prompts, When `rg -n 'agent-commit.sh' AGENTS.md agents/lead`, Then Treffer in AGENTS.md, lead- und devops-Config. |

## 8. Traceability (Ziel → Anforderung → Test → Task)
| Ziel | Anforderungen | ACs | Tasks |
|---|---|---|---|
| G-1 | FR-001, FR-002, FR-003, FR-004, FR-005, FR-009 | AC-001, AC-002, AC-005, AC-013 | T-001, T-002, T-003 |
| G-2 | FR-006, FR-007, FR-008 | AC-003, AC-004 | T-004, T-005 |
| G-3 | FR-020…FR-026, FR-028…FR-031 | AC-006…AC-012 | T-006, T-007, T-008, T-009 |
| G-4 | FR-027, NFR-001, NFR-002, NFR-007 | AC-006, AC-009, AC-011 | T-008, T-009 |

## 9. Quality Review (Quick Path)
| Prüfpunkt | Ergebnis |
|---|---|
| Jede FR hat ≥ 1 AC | ja (FR-001 über AC-001/AC-013 indirekt) |
| Testbar / eindeutig | ja; Ausnahme Q-1 (Branch-Scope) und Q-3 (Notify-Schnittstelle) offen |
| Konflikte mit Bestand | `fork-ci.yml` erlaubt nur `contents: read` – Verify braucht eigenen Workflow mit eigener `permissions`-Sektion (gelöst durch separate Datei) |
| Sicherheit | Push-Recht nur auf `revert/**`; Guard + Test (AC-009). Public Repo: Push-Trigger kann von Fremden nicht ausgelöst werden (nur Merges durch Jens) |
| Risiko | Bisect auf Mac dauert lange (pytest-Suite); Mitigation: nur rote Suite, First-Parent, N≤10 |
| Risiko | Zwei Runner teilen Maschine; Verify + PR-CI parallel → `concurrency` + Timeout |
| Vollständigkeit Non-Goals | ja |

## 10. Annahmen
- A-1 < 60 min bis Revert-PR. A-3 Revert nur als PR. A-4 Scope `epic/team-chat` + `main`. A-5 kein Prod. A-6 Definition Agent-Branch (00-intake).
- A-7 Der Workflow nutzt `GITHUB_TOKEN` (kein PAT) für Revert-PR; PRs mit `GITHUB_TOKEN` triggern keine Fork CI → Revert-PR braucht manuellen Re-Run oder `workflow_dispatch` [OFFEN Q-4].

## 11. Open Questions
| ID | Frage | Empfohlener Default | Status |
|---|---|---|---|
| Q-1 | Nur `epic/team-chat` oder alle `epic/**` (wie `fork-ci.yml`)? | `epic/**` + `main`, Filter als Liste im Workflow | offen |
| Q-2 | Wo „last green“ speichern: Git-Ref, Tag, oder Abfrage letzter grüner Run per API? | API (`gh run list --status success`) als Quelle, kein Push zusätzlicher Refs | offen |
| Q-3 | Wo liegt `notify-spec.sh` und welche Schnittstelle (Args/Env, Secret-Name)? Nicht im Repo gefunden. | Skript nach `scripts/fork/notify-spec.sh` übernehmen, Webhook-URL als Repo-Secret `NOTIFY_SPEC_WEBHOOK` | offen |
| Q-4 | Wann vom Trailer-Check `warn` auf `fail` umschalten? | nach 2 Wochen bzw. wenn 10 Agent-PRs in Folge sauber | offen |
