# Session-Bot, Prototyp

## Stack

Fünf Objekte und die Tore, aus dem Grok-Bot-Client 0.71.2, den REA gelesen hat. Ein Werkzeug, aus REA. Der Chat eines Bots ist eine Session in der Sessionliste des Projekts.

| Objekt | In diesem Prototyp | Später |
|---|---|---|
| Bot | Ein Datensatz: Name, Auftrag, Gedächtnis, Zustand | Mehrere Bots, ein gemeinsamer Rechner pro Konto |
| Chat | `session_id` der Omnigent-Session. Liegt das Original in einem Projekt, erscheint der Side-Chat in der Sessionliste dieses Projekts | Der Bot öffnet die Session selbst |
| Prompt | Das Feld `assignment`, plus Notizen | Ein gespeicherter Prompt wird ein Skill. Ein ausgelöster Prompt wird eine Routine |
| Werkzeug | Ein Befehl: REA auf ein lokales Verzeichnis. Er wird gespeichert und nicht gestartet | REA als MCP in der Session. Der Rechner der Session ist der Arbeitsplatz, kein zweiter VM |
| Artefakt | Die Notiz `result` | Der Diff, den die Session ablegt |

Der Client liefert, was der Datensatz schon erzwingt: der Bot überlebt die Session, riskante Schritte warten auf eine Entscheidung, das Gedächtnis ist Text. Eine Automation ist dort eine Routine, MCP hat eine Erlaubnis pro Werkzeug, die Freigabe kennt `needs-approval` und `auto-review`. Nicht übernommen werden die gehostete SandBox, VNC, Stimme und der Integrationskatalog.

REA ist das einzige Untersuchungswerkzeug. Der erste Lauf liest ein Verzeichnis, das euch gehört oder offen liegt, und schreibt Belege in die Notiz. Hopper, Ghidra und IDA sind kein Teil dieses Prototyps.

## Verhalten

Ein Bot ist ein Datensatz auf der Platte. Er überlebt das Ende einer Session. Er besitzt einen Auftrag, ein Gedächtnis und eine Session-ID. Er setzt keinen Produktcode um und startet REA nicht. Er hält den REA-Befehl fest und bleibt an zwei Toren stehen, bis ein Mensch entscheidet.

Der nächste Schritt, nicht dieser Prototyp, hängt `session_id` an `agents/lead/scripts/spawn.py` und führt den gespeicherten Befehl aus.

## Datensatz

Eine Datei `bots/state/<id>.json`. `id` ist `bot_` plus acht Hex-Zeichen.

| Feld | Bedeutung |
|---|---|
| `name` | `rea-investigator` und andere Namen der Form `^[a-z][a-z0-9-]{1,40}$` |
| `kind` | Nur `rea` |
| `assignment` | Ein Satz, 1 bis 500 Zeichen |
| `status` | `idle`, `session_open`, `awaiting_approval`, `implementing`, `awaiting_review`, `done`, `blocked` |
| `memory` | Angehängte Notizen |
| `session_id` | Omnigent-Session, 8 bis 64 Zeichen aus Buchstaben, Ziffern, `_` und `-`. Leer, solange keine Session offen ist |
| `rea_target` | Absoluter Pfad eines vorhandenen Verzeichnisses |
| `rea_command` | `npx -y rea-agents@latest analyze-javascript-application <target> --json` |
| `approval_reason` | Der Satz, mit dem das erste Tor geöffnet wurde |
| `result` | Die letzte Notiz aus der laufenden Umsetzungsrunde |
| `result_fresh` | `true`, wenn diese Runde eine neue Notiz hat |
| `history` | `{at, event, detail}` in Reihenfolge |

## Übergänge

`idle` öffnet eine Session und wird `session_open`. Schließen aus `session_open` löscht nur `session_id` und setzt `idle`. Gedächtnis und REA-Befehl bleiben.

`prepare-rea` gilt nur in `session_open`. Das Ziel muss ein vorhandenes Verzeichnis sein. Der Befehl wird gespeichert und nicht ausgeführt.

`request-approval` gilt nur in `session_open`, mit Session, vorbereitetem REA-Befehl und einem Grund. Status wird `awaiting_approval`.

`decide --approve` auf diesem Tor setzt `implementing` und leert das Ergebnis der Runde. `decide --reject` setzt `blocked`. Aus `blocked` und `done` führt kein Übergang heraus.

`note` gilt in `session_open` und in `implementing`. In `implementing` wird der Text das Ergebnis und `result_fresh` wird `true`.

`request-review` gilt nur in `implementing` mit `result_fresh`. Status wird `awaiting_review`.

`decide --reject` auf dem Review-Tor setzt `implementing` und `result_fresh` auf `false`. Die alte Notiz bleibt sichtbar. Eine neue Notiz ist nötig, bevor das Tor wieder offen ist. `decide --approve` setzt `done`.

Jeder andere Übergang endet mit Exit 2 und einer Zeile auf stderr. Ein gültiger Aufruf schreibt den Datensatz und druckt ihn als JSON auf stdout.

## Abnahme

```bash
python3 -m unittest bots.test_session_bot
```

Das Ergebnis ist `OK`. Die Tests prüfen die Übergänge, das Liegenbleiben über einen neuen Prozess, die Ablehnung eines fehlenden REA-Ziels und dass dabei kein Analysebefehl gestartet wird.
