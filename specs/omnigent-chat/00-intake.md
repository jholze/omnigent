# Intake: Omnigent Chat (Slack-artig, Menschen + Agenten) — v0.1 (2026-10-16, Status: Entwurf)

## Problem (zur Bestätigung)
Omnigent hat heute nur einen einfachen Menschen-Chat pro geteilter Session (PR #4); der Agent liest nicht mit, es gibt keine Channels, DMs, Threads oder Erwähnungen. Ziel ist, dass ein Team komplett in Omnigent im Chat arbeiten kann, mit Menschen und Agenten als echten Gesprächspartnern, und dass sich das richtig gut anfühlt.

## Gewünschtes Ergebnis / Erfolgssignale
- Das Team bei bloopark koordiniert die Arbeit in Omnigent statt in einem externen Chat. [OFFEN: messbares Signal, z. B. Anteil der Abstimmung im Omnigent-Chat]
- Agenten werden per @ angesprochen und arbeiten sichtbar im Thread.
- UX wird als herausragend wahrgenommen (Animationen, Icons, Mikrointeraktionen), harte Anforderung von Jens.

## Appetit / Termin
[OFFEN] Kein Zeitbudget genannt. Referenz: Die enge Session-Chat-Spec schätzt ca. 25 Tage für ihre Phasen 1–9.

## Rollen & Stakeholder
| Rolle | Ziel | Entscheidungsmacht | Kontakt |
|---|---|---|---|
| Jens (Owner) | Produktvision, UX-Anspruch | entscheidet | Jens Holze |
| Teammitglied (Omnigent-Nutzer) | eigene Sessions, teilen, chatten | – | bloopark-Team |
| Session-Teilnehmer (Grant Read/Edit/Manage/Owner) | in geteilter Session mitreden | Rechte je Stufe | – |
| Admin | Server betreiben, moderieren | Server-Einstellungen | [OFFEN] |
| Agent (Teilnehmer) | per @ Aufgaben übernehmen, im Thread antworten | keine; handelt mit Rechten des Aufrufers [ANNAHME A-3] | – |
| Besucher über öffentlichen Link | nur lesen | – | Nicht-Ziel für Chat |

## Bestand & Quellen
| Quelle | Typ | Zugang | Notizen |
|---|---|---|---|
| github.com/jholze/omnigent (Fork von omnigent-ai/omnigent v0.18.0) | Repo | öffentlich | FastAPI, SQLAlchemy/Alembic, React 18, Tailwind 4, shadcn, Tiptap, zustand |
| PR #4 feat/session-human-chat (616ea2f) | Code | öffentlich | session_chat_messages, GET/POST /v1/sessions/{id}/chat, Event session.human_chat, Chat-Spalte. exists |
| PR #5 Google-Login | Code | öffentlich | principals() mit verknüpften Mails; Voraussetzung |
| Issues #3, #7–#10 | Tickets | öffentlich | dürfen frei geschlossen/neu geschnitten werden (Jens) |
| docs/specs/chat-integration.md (Commit 14237d8, lokal) | Spec | Kopie in /workspace/omnigent-chat-session/ | enger Session-Chat, 22 Abschnitte, exists/adapt/new. Wird Bestand für unsere Spec |
| integrations/slack/ | Code | öffentlich | bestehende Slack-Brücke, bleibt unberührt |
| Upstream web/src/components/ai-elements | Code | öffentlich | AI Elements schon integriert |
| Slack Agent-Muster | Recherche | docs.slack.dev | Agent-Sessions, Streaming, Freigabe-Karten, Artefakte |

## Umfang (grob)
- Serverweite Channels und DMs für alle Nutzer eines Omnigent-Servers.
- Jede geteilte Session hat automatisch einen Chat mit ihren Teilnehmern (Sonderform eines Channels).
- Threads, @-Erwähnungen (Menschen und Agenten), serverseitiger Ungelesen-Status.
- Agenten als echte Teilnehmer: @ im Channel startet einen Thread, dahinter eine Omnigent-Session mit Status (arbeitet, wartet, fertig), Stop, Live-Streaming der Schritte und Freigabe-Karten; der Agent folgt dem Thread, bis man ihn entlässt.
- UX (Entscheidung Jens 16.10.): technische Basis AI Elements (exists upstream) + Motion; Muster gezielt aus agent-native (Streaming, Approval-Card, Guided Questions, Queue/Steer, Run-Stuck-Banner, Task-Cards), prompt-kit und Slack nachbauen, kein Code aus agent-native (Lizenz unklar, React 19).
- UX-Agent als Omnigent-Rolle mit Wissen aus plugin87/ux-ui-agent-skills (nur Doku-Dateien, ohne Hooks/Bash-Freigaben).

### Release 1 (von Jens bestätigt)
Channels, DMs, Threads, @-Erwähnungen, Ungelesen-Status, Agenten per @.

### Später
Reaktionen, Dateien, Suche, Mobile, Export.

## Nicht-Ziele
- Slack-Brücke anfassen oder erweitern (Datenmodell soll Andocken später erlauben).
- Mehrere Organisationen/Mandanten auf einem Server [ANNAHME A-1].
- Anrufe, Huddles, Föderation.
- Chat für Besucher über öffentlichen Link.

## Randbedingungen
- Stack wie Upstream; Änderungen upstream-fähig halten [ANNAHME A-2].
- Echtzeit heute im Prozess (SSE + WebSocket), ohne Broker, also eine Server-Instanz [ANNAHME A-4, im Design prüfen].
- Code-Lesen und Reviews mit Jens' lokalem Setup, keine kostenpflichtigen API-Modelle.
- Nichts pushen, keine Issues ändern ohne Jens' OK.

## Qualitätserwartungen (grob)
- UX: flüssige Animationen, Statuswechsel, Tipp-/Präsenz-Anzeige, kleine Icons, sofort spürbare Reaktion. [OFFEN: Zielwerte, z. B. Nachricht sichtbar < 300 ms]
- Sicherheit: Agent sieht nie mehr als der Aufrufer.

## Risiken
- Die enge Spec und ein bereits eingerichtetes Agenten-Team könnten jetzt auf dem engen Rahmen losbauen (Agent hört nicht mit, @agent erst Phase 10), der Jens' Ziel widerspricht.
- Rechte von Agenten in Threads mit mehreren Menschen.
- Echtzeit-Skalierung ohne Broker.
- UX-Anspruch ohne klares Designziel schwer abnehmbar.

## Annahmen
- [ANNAHME] A-1 Ein Workspace pro Installation, keine Mandantentrennung.
- [ANNAHME] A-2 Ergebnis soll upstream-fähig bleiben.
- [ANNAHME] A-3 Agent arbeitet mit den Rechten der Person, die ihn gerufen hat.
- [ANNAHME] A-4 Eine Server-Instanz reicht für Release 1.

## Aus der engen Spec übernommen (Empfehlungen dort, hier als Default)
| Nr. | Frage | Default |
|---|---|---|
| S-1 | Session-Chat existiert ab zwei direkten Grants, auch offline | übernehmen |
| S-2 | Owner liest Verlauf nach Unshare weiter, nur lesen | übernehmen |
| S-3 | Read-Recht darf im Chat schreiben | übernehmen |
| S-4 | Admin ohne eigene Zeile liest nur | übernehmen |
| S-5 | Feature-Flag, Standard aus | übernehmen |
| S-6 | PR #5 vor produktivem Chat | übernehmen |
| S-7 | Benachrichtigung nur generisch | übernehmen, für DMs offen |
| S-8 | Gelöscht = Tombstone ohne Text | übernehmen |
| S-11 | Account-Löschung in Account-Revocation klären | übernehmen |
| S-12 | @agent erst spät, nur per Button | ersetzt: Agenten sind Release 1 |

## Offene Fragen
| ID | Frage | Owner | Blockiert | Fällig |
|---|---|---|---|---|
| Q-1 | Soll das Agenten-Team auf PR #4/Issue #3 pausieren, bis die neue Spec steht? | Jens | Bau | sofort |
| Q-2 | Zeitbudget / Wunschtermin für Release 1? | Jens | Stufe 7 | Stufe 1 |
| Q-3 | Aufbewahrungsfrist für Channel- und DM-Nachrichten (enge Spec: keine, stirbt mit Session) | Jens | Stufe 4 | Stufe 4 |
| Q-4 | Uncommitteter people-Tab im Worktree: verwerfen oder liegen lassen (enge Spec widerspricht sich) | Jens | – | Stufe 5 |
| Q-6 | Erfolgssignal messbar machen | Jens | Stufe 1 | Stufe 1 |

## Glossar (Start)
| Begriff | Bedeutung |
|---|---|
| Session | Omnigent-Konversation mit Agent; Sub-Agents sind Kind-Konversationen |
| Grant | direkte Zeile in session_permissions (Read/Edit/Manage/Owner) |
| Session-Chat | Chat der Teilnehmer einer geteilten Session |
| Channel | serverweiter Chatraum, unabhängig von Sessions |
| Agent-Thread | Thread, in dem ein Agent arbeitet; dahinter eine Session |

## Changelog
- v0.2 (2026-10-16) UX-Basis und UX-Agent entschieden, Q-5 erledigt.
- v0.1 (2026-10-16) Erster Entwurf aus Intake-Gespräch, Code-Analyse, enger Session-Spec und Slack-/UI-Recherche.
