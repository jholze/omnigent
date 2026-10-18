# experiments

Prototypen aus dem alten Ordner `~/Documents/scripts/omnigent` (ohne Git), am 2026-10-18 beim Mac-Umzug hierher übernommen. Sie sind **nicht Teil des Produkt-Builds**: keine Tests unter `tests/`, von ruff ausgenommen, nicht im pyrefly-Scope. Der Code ist unverändert; Imports zeigen noch auf die alte Struktur (`from bots.bot import …`).

| Pfad | Herkunft | Inhalt |
|------|----------|--------|
| `session-bot/` | `bots/` | Session-Bot-Prototyp (Datensatz, Tore, CLI), Spezifikation in `SPEC.md`, Test `test_session_bot.py` |

Aus demselben Ordner stammen außerdem:

- `agents/lead/` (vorher `agents/lead/`): das Lead-Team für diesen Fork (Grok-Lead plus 12 Worker-Konfigurationen, `scripts/spawn.py`). Es wurde nicht verworfen, weil es keine neuere Fassung gibt (`~/.omnigent/agent-configs` enthält nur einen älteren `reviewer.yaml`, im Repo gab es keine Agent-Konfigurationen) und die Lead-Session es noch nutzt. Es liegt am alten relativen Pfad, damit `python3 agents/lead/scripts/spawn.py` aus dem Repo-Root weiter funktioniert. Die Worker pinnen ihre Modelle absichtlich; `agents/lead/` ist deshalb vom Lint `no-hardcoded-models` ausgenommen.
- `.grok/scripts/invoke-github.sh`: lokaler Qwen/Ollama-Aufruf für GitHub-Routinearbeit, von `agents/lead` referenziert.
- `docs/omnigent-agents.md`, `docs/omnigent-testing.md`.

Nicht übernommen: `.cursor/hooks.json` (automatisch erzeugter, sitzungsspezifischer Usage-Hook) und `.cursor/mcp.json`. Der Odoo-Teil des alten Ordners liegt im Repo `jholze/odoo-dev`.
