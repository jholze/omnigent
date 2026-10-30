# Bei 768 px zeigt der Chat eine nutzbare Spalte statt drei schmaler
Status: open
Task: T-122 (Fix gehört an den Layout-Task des Chat-Shells, T-007b-Linie)  Severity: High  Checklist: RSP-01, ROB-02  Spec: NFR-032, FR-001
Written against: feat/team-chat-t122 @ 6d180bcbfdaf3c11b7ff185df470f71d84f10be4

## Evidence chain
- Surface: `/chat/:channelId` und `/chat/:channelId/t/:threadRootId` bei 768×1024 (frischer Kontext, App-Navigation standardmäßig angedockt)
- Problem: Die App-Navigation (~280 px) bleibt angedockt. Ohne Thread teilen sich Chat-Sidebar (~210 px) und Channel-Spalte den Rest. Die Channel-Spalte ist ~185 px breit, die Leiste „Agent braucht Antwort“ bricht silbenweise („Ag / ent / bra / uch …“) und ist rund 300 px hoch. Mit offenem Thread blendet `md:max-lg:hidden` die Chat-Sidebar aus, das Thread-Pane nimmt 320 px. Die Channel-Spalte schrumpft auf ~110 px: Leiste und Nachrichten stehen dann buchstabenweise, der Composer-Platzhalter ist abgeschnitten („Nachricht an“).
- Evidence: `/tmp/omnigent-chat-ux/t122/2-channel-768.png`, `/tmp/omnigent-chat-ux/t122/2-thread-768.png`; Code `web/src/chat/ChatPage.tsx:264` (Sidebar `md:max-lg:hidden` nur bei offenem Thread), `:271-273` (Channel-Spalte nur `max-md:hidden`), `web/src/chat/ThreadPane.tsx:304` (`md:w-80`)
- Owner: `web/src/chat/ChatPage.tsx`
- Affected surfaces: Channel, DM, Thread im Bereich `md` bis unter `lg` (768–1023 px), z. B. iPad hochkant und halbe Laptop-Fenster
- Uncertainty: Ob die App-Navigation bei `md` angedockt sein soll, entscheidet die Shell (vorbestehend, nicht Chat-Scope). Der Plan ändert nur die Chat-Spalten.

## Design decision
Zwischen `md` und `lg` fehlt neben der angedockten App-Navigation der Platz für mehr als eine Chat-Spalte. Dort gilt deshalb dieselbe Spaltenlogik wie auf dem Phone (`max-md`): Es ist genau eine Chat-Spalte sichtbar, nämlich Channel-Liste, Channel oder Thread. Den Weg zurück bieten die vorhandenen Zurück-Links. Das ist eine Breakpoint-Verschiebung bestehender Regeln, kein neues Muster.

## Reuse
- Bestehende Phone-Regeln in `web/src/chat/ChatPage.tsx:264-281` (`max-md:hidden`, Zurück-Link `md:hidden`) und `web/src/chat/ThreadPane.tsx:317-323` (Zurück-Link `md:hidden`, `max-md:w-full max-md:flex-1`)

## Changes
1. `web/src/chat/ChatPage.tsx:264-281` und `web/src/chat/ThreadPane.tsx:304,322,330` – Change: Die Schwelle der Ein-Spalten-Logik von `md` auf `lg` heben (`max-md:` → `max-lg:`, `md:hidden` → `lg:hidden` für Zurück-Links, Thread-Pane `lg:w-96` erst ab `lg` als Seitenleiste). · Preserve: Phone-Verhalten unverändert, ab 1024 px dreispaltig wie heute, URL-Routing FR-001, Escape schließt den Thread. · Verify: Bei 768×1024 ist die sichtbare Chat-Spalte ≥ 440 px breit; „Agent braucht Antwort“ steht in höchstens 2 Zeilen; kein Text bricht zeichenweise.

## Validation
- Interface: 768×1024 und 1023×768 mit offener Leiste „Agent braucht Antwort“, langem Channel-Namen und offenem Thread; Zurück-Link führt vom Thread zum Channel und vom Channel zur Liste; 1280 und 390 unverändert.
- Repository: `uv run --group test pytest tests/e2e_ui/team_chat/test_viewports.py -q` → grün, ergänzt um 768×1024 (Channel und Thread: keine Spalte < 320 px).

## Stop conditions
- Stop, wenn Jens bei `md` lieber die App-Navigation einklappt, statt Chat-Spalten zu stapeln. Das ist eine Shell-Entscheidung außerhalb von `web/src/chat`.

## Design documentation
- Nach Abnahme in `docs/ux/system.md` unter Layout: „Chat zeigt unter `lg` genau eine Chat-Spalte.“

## Resolution
