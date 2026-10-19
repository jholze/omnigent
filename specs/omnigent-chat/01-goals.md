# Ziele & Impact: Omnigent Chat — v0.1 (2026-10-16, Status: Entwurf)

## Vision
Ein Team arbeitet in Omnigent komplett im Chat: Menschen und Agenten reden in Channels, DMs und Threads miteinander, Agenten übernehmen Aufgaben direkt aus dem Gespräch, und das Ganze fühlt sich besser an als Slack.

## Business-Ziele
| ID | Ziel | Erfolgssignal | Status |
|---|---|---|---|
| G-1 | Abstimmung im Team findet in Omnigent statt, nicht in externen Chats | [ANNAHME] Nach 4 Wochen läuft die Mehrheit der projektbezogenen Abstimmung im Omnigent-Chat | Vorschlag |
| G-2 | Agenten-Arbeit startet aus dem Gespräch heraus | [ANNAHME] Mehrzahl neuer Agent-Sessions im Team wird per @ aus dem Chat gestartet | Vorschlag |
| G-3 | Herausragende UX | [OFFEN] Abnahme durch Jens anhand definierter UX-Kriterien und UX-Agent-Review | Vorschlag |
| G-4 | Upstream-fähige Umsetzung | [ANNAHME A-2] PR-fähig für omnigent-ai/omnigent | Vorschlag |

## Impact Map (Ziel → Akteur → Verhaltensänderung → Lieferung)
- **G-1** → Teammitglied → schreibt Kollegen in Channels/DMs statt extern → Channels, DMs, Threads, Ungelesen, Benachrichtigungen
- **G-1** → Session-Teilnehmer → stimmt sich neben der Agent-Arbeit ab, ohne den Agenten zu stören → Session-Chat als Sonderform eines Channels
- **G-2** → Teammitglied → ruft Agenten per @ statt neue Session per Hand → Agent-Threads mit Session dahinter, Status, Stop, Streaming, Freigaben
- **G-2** → Agent → arbeitet sichtbar und fragt bei Unklarheit nach → Thinking-Steps, Guided Questions, Approval-Cards, Artefakte
- **G-3** → alle → bleiben gern im Tool → AI-Elements-Basis, Motion-Animationen, Muster aus agent-native, UX-Agent als Gate
- **G-4** → Upstream-Maintainer → akzeptieren die Änderungen → Feature-Flag, saubere Migrationen, Tests

## Nicht-Ziele
- Slack-Brücke erweitern; Mandantenfähigkeit; Anrufe/Huddles; Föderation; Chat für Public-Link-Besucher.
- React-19-Upgrade ist eigenes Vorhaben (Issue #16), keine Voraussetzung.

## Annahmen
- [ANNAHME] Erfolgssignale G-1 und G-2 werden qualitativ von Jens bewertet, keine Telemetrie in Release 1.

## Offene Fragen
| ID | Frage | Owner | Blockiert |
|---|---|---|---|
| Q-2 | Zeitbudget / Wunschtermin für Release 1 | Jens | Stufe 7 |
| Q-6 | Erfolgssignale bestätigen oder ersetzen | Jens | Stufe 1 |
| Q-7 | Ist G-4 (upstream-fähig) ein Ziel oder nur nice-to-have? | Jens | Stufe 5 |

## Changelog
- v0.1 (2026-10-16) Erster Entwurf.
