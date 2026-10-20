# Review-Modus

Version: 0.1
Datum: 2026-10-20
Status: Entwurf
Owner: Jens Holze

Die Lead-Session liest diese Datei am Start, bevor sie den ersten Coder beauftragt.
Die Begründung, die Simulation und die offenen Fragen stehen in `review-stages.md`.

## Regel

1. Steht `Review-Modus` im Ticket, gilt das Feld. Ausnahme: `prototyping` an einer Erweiterung oder einem Gate-Ticket ist ungültig. Die Lead korrigiert auf `polish` und schreibt das in den PR. Die Verbotsregel schlägt das Feld (RS-FR-004).
2. Fehlt das Feld, gilt die Tabelle. Dieselbe Zuordnung tragen die Labels `stage:prototyping` und `stage:polish`. Am 2026-10-20 ließen sich diese Labels mit dem Token dieses Laufs nicht anlegen (HTTP 403). Bis sie existieren, steht der Modus im Issue-Titel als `[prototyping]` oder `[polish]`.
3. Fehlt das Ticket in der Tabelle, leitet die Lead nach `review-stages.md` RS-FR-012 ab und trägt das Feld ein, bevor sie coden lässt. Eine Erweiterung einer fertigen Funktion wird dabei nie `prototyping`.

## Prüfungen

Beide Stufen: die ACs des Tickets sind grün, die genannten Tests sind in diesem Lauf wirklich gelaufen, CI und Lint sind grün, Code-Review ist DeepSeek und danach Opus PASS, kein Blocker. Nicht gelaufen ist kein Pass.

`prototyping` macht sonst nichts. Kein UX-Review, kein NFR-032, kein Kontrast.

`polish` ist das volle Gate. Hat das Ticket UI, kommen dazu: UX-Review gegen `docs/ux/chat-review-checklist.md`, NFR-032 bei 1280×800 und 390×844, Kontrast nach NFR-009. Hat es keine UI, sind UX, NFR-032 und Kontrast n/a. Das Code-Gate bleibt.

Motion prüft die Lead nur, wenn das Ticket Motion einführt (T-117 und Folgetickets, die `web/src/chat/motion.ts` anfassen).

## Abschalten

`prototyping` geht aus, wenn die Basis-ACs grün sind, der Tester sie wirklich laufen ließ, DeepSeek und danach Opus PASS sagen und kein Blocker offen ist. Die Lead stellt das Feld auf `polish`, bevor jemand an Lesbarkeit, Viewport oder Bewegung geht. Jens wird informiert. Er wird dafür nicht gefragt, außer RS-Q-001 ist noch offen und der Diff ist UI. Bis dahin gilt der empfohlene Default in `review-stages.md`.

## Tabelle

Klasse `erweiterung` oder `gate` ist nie `prototyping`. Klasse `neu` ist die Funktion, die noch gebaut wird. UI `ja` zieht im Polish die Viewport- und Kontrastprüfung.

| Ticket | Modus | Klasse | UI | Grund |
|---|---|---|---|---|
| T-005-AC-033c | polish | erweiterung | nein | AC-033c an gemergtem T-005, Server |
| T-007b | polish | erweiterung | ja | Demo-Feedback an T-007/T-008, PR #52 |
| T-009 | polish | gate | ja | R0-Suite und Tester-Gate der gebauten UI |
| T-101 | prototyping | neu | nein | Lesecursor, Funktion wird gebaut |
| T-102 | polish | erweiterung | nein | Session-Chat existiert, Adaption |
| T-103 | prototyping | neu | nein | Agent-Handles, neu |
| T-104 | prototyping | neu | nein | Agent folgt dem Thread, neu |
| T-105 | prototyping | neu | nein | Agentenkonten, neu; Issue #12 nicht duplizieren |
| T-106 | prototyping | neu | nein | Zugriffspolitik, neu |
| T-107 | prototyping | neu | nein | MCP-Chat-Tools, neu |
| T-108 | prototyping | neu | nein | Outbox, R1.1, neu |
| T-109 | prototyping | neu | nein | Bot-Bremse, neu |
| T-110 | polish | erweiterung | ja | @-Autocomplete an der gebauten Composer-Fläche |
| T-111 | polish | erweiterung | ja | Badge an der gebauten Sidebar; Server bleibt T-101 |
| T-112 | polish | erweiterung | ja | Ansicht auf der gebauten Shell |
| T-113 | polish | erweiterung | ja | Session-Chat in der gebauten Sidebar |
| T-114 | polish | erweiterung | ja | Steuerung an der gebauten Thread-Fläche |
| T-115 | polish | erweiterung | ja | Schritte an der gebauten Thread-Fläche |
| T-116 | polish | erweiterung | ja | Einstellungen an der gebauten Shell |
| T-116b | polish | erweiterung | ja | Webhook-Felder an T-116 |
| T-117 | polish | erweiterung | ja | Motion ist Feinschliff |
| T-118 | polish | erweiterung | ja | Streaming und Scroll an der gebauten Liste |
| T-119 | prototyping | neu | nein | Rate-Limit, neu |
| T-120 | polish | gate | ja | R1-Playwright Menschen, a11y |
| T-121 | polish | gate | ja | R1-Playwright Agenten |
| T-122 | polish | gate | ja | UX-Pass |
| T-123 | polish | gate | nein | Release-Runbook; Prod macht Jens |
| T-124 | polish | erweiterung | ja | Tipp-Indikator an der gebauten Fläche |
| T-125 | prototyping | neu | nein | Unread für Agentenkonten, neu |
| T-126 | prototyping | neu | nein | MCP-Protokollhygiene, neu |
| T-127 | prototyping | neu | nein | Strukturierte Chat-Tools, neu |
| T-128 | prototyping | neu | nein | Sicherheitsgrenzen, neu |
| T-129 | prototyping | neu | nein | Grok-Bot-Smoke, kein UI-Produkt |
| T-130 | prototyping | neu | nein | Messung, neu |
| T-131 | prototyping | neu | nein | Audit, neu |
| T-132 | polish | erweiterung | ja | Channel-Verwaltung auf fertiger API, PR #53 |
| T-133 | polish | erweiterung | nein | Demo-Seed der fertigen Fläche, PR #49 |

## Changelog

| Version | Datum | Änderung |
|---|---|---|
| 0.1 | 2026-10-20 | Erste Fassung. Tabelle der offenen Team-Chat-Tickets. Gemergte T-000…T-008 ohne AC-033c stehen nicht drin. Labels `stage:*` sind vorgesehen; der Lauf konnte sie nicht anlegen (HTTP 403), der Issue-Titel trägt den Modus bis dahin. |
