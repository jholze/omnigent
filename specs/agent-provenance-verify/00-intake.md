# Intake: Agent-Provenance & Post-Merge-Verify — v0.1

Version: 0.1 · Date: 2026-10-21 · Status: draft · Owner: Jens Holze · Pfad: Quick Path (Intake → Requirements + ACs → Quality Review → Agent-Tasks)

## Changelog
| Version | Datum | Änderung |
|---|---|---|
| 0.1 | 2026-10-21 | Erster Entwurf nach Ideen aus `feder-cr/git-ai` |

## Problem (zur Bestätigung)
1. **Herkunft unklar:** In `jholze/omnigent` schreiben mehrere Agenten (Claude Opus, DeepSeek V4 Pro, Grok, Cursor-Cloud-Agent) Code. Aus Commits und PRs ist nicht ablesbar, welcher Agent, welche Session und welches Modell eine Änderung gemacht hat. Heute steht dort entweder `Jens Holze` oder `Cursor Agent` mit `Co-authored-by: Jens Holze` – Rolle, Session-ID und Modell fehlen. Das Review-Matrix-Prinzip („nie derselbe Vendor reviewt“, `agents/lead/config.yaml`) ist damit nicht prüfbar.
2. **Rote Tips nach Merge:** Fork CI läuft nur auf PRs (`.github/workflows/fork-ci.yml`, `on: pull_request` für `main` und `epic/**`). Nach einem Merge – besonders wenn mehrere PRs kurz nacheinander in `epic/team-chat` landen – prüft niemand den neuen Tip. Ein roter Tip wird spät bemerkt, der Schuldige muss von Hand gesucht werden.

## Gewünschtes Ergebnis / Erfolgssignale
- Jeder Agent-Commit trägt Trailer `Omnigent-Agent`, `Omnigent-Session`, `Model`; jeder Agent-PR listet die beteiligten Agenten.
- Ein roter Tip auf `epic/team-chat` oder `main` führt ohne Zutun zu einem **Revert-PR** gegen den schuldigen Merge und einer Benachrichtigung.
- [ANNAHME A-1] Zeit von rotem Tip bis Revert-PR < 60 min auf dem Mac-Runner.

## Appetit
[ANNAHME A-2] Klein: ca. 2–3 Agent-Tage, 9 Tasks, kein UI.

## Rollen & Stakeholder
| Rolle | Ziel | Entscheidung |
|---|---|---|
| Jens (Owner) | Nachvollziehbarkeit, stabile Epic-Branches | entscheidet, mergt Revert-PRs |
| Lead / PM HUB (`agents/lead/config.yaml`) | Briefs, Handoffs, PR-Text | – |
| `devops` | Gate + Push des Feature-Branches | – |
| Coder-/UX-Agenten | committen lokal (ux-builder) bzw. liefern Diffs | – |
| Self-hosted Runner `omnigent-ci` (Mac, 2 Instanzen) | führt Verify aus | – |

## Bestand & Quellen
| Quelle | Befund | Status |
|---|---|---|
| `agents/lead/config.yaml` | Rollen, Modelle je Rolle, Handoff-/RESULT-Format, Session-IDs (`c750a1c6…` PM HUB). Kein Commit-Format. | adapt |
| `agents/lead/agents/coder*/config.yaml` | Coder committen/pushen **nicht** („Do not `git commit`“), `gate_pushes: true` | exists |
| `agents/lead/agents/devops/config.yaml` | einziger Push-Pfad (Feature-Branch, `--force-with-lease` nur dort); kein Trailer | adapt |
| `ux-builder` (Prompt in lead config) | „commits locally on the feature branch“ | adapt |
| `AGENTS.md` §Committing | nur pre-commit; keine Trailer-Regel | adapt |
| `git log main` | Cursor-Cloud-Commits nutzen `Co-authored-by: Jens Holze`; sonst keine Trailer | exists (Muster) |
| `scripts/fork/` | `deploy-local.sh`, `upstream-sync.sh`; kein Commit-Helper | new |
| `notify-spec.sh` | **nicht im Repo** und nicht auf der Box gefunden; liegt vermutlich lokal auf Jens' Mac [OFFEN Q-3] | exists (extern) |
| `.github/workflows/fork-ci.yml` | PR-CI auf `[self-hosted, macOS, ARM64, omnigent-ci]`, Gate auf jholze, `contents: read`; Jobs python-lint, pytest, web, e2e-ui (non-blocking) | adapt (Basis für Suites) |
| `specs/team-chat/`, Branch `epic/team-chat` | auf `origin` **nicht vorhanden** (Stand 2026-10-21 08:30 MESZ); Stilvorlage daher `specs/omnigent-chat/` | – |
| `feder-cr/git-ai` | Ideengeber: Agent-Attribution je Commit, Verifikation nach Merge | Referenz, kein Code übernommen |

## Umfang (grob)
- Trailer-Konvention + Commit-Helper `scripts/fork/agent-commit.sh` + Prompt-Anpassungen.
- PR-Body-Abschnitt „Contributing agents“ (generiert aus Trailern).
- CI-Check `agent-provenance` (warn → fail).
- Workflow `post-merge-verify.yml` auf self-hosted: Tests auf neuem Tip, 1× Rerun, Bisect über Merges seit letztem Grün, Revert-PR, Notify.

## Nicht-Ziele (früh)
Siehe `04-requirements.md` §3. Kurz: kein Direkt-Push, kein Prod/Deploy, keine GitHub-hosted Runner, keine Upstream-Änderung.

## Annahmen
- [ANNAHME A-3] Revert immer als PR, nie direkter Push.
- [ANNAHME A-4] Scope nur `epic/team-chat` (bzw. `epic/**`) und `main`.
- [ANNAHME A-5] Keine Berührung von Prod/`deploy-local.sh`/launchd.
- [ANNAHME A-6] „Agent-Branch“ = Head-Branch mit Präfix `feat/`, `fix/`, `chore/`, `test/`, `cursor/`, `agent/` aus PRs, deren Commits nicht von einem Menschen stammen; Merge- und Upstream-Sync-Commits sind ausgenommen.
