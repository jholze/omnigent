# T-007b – Offene Agent-Anfragen außerhalb der geladenen Seite fehlen in der Leiste

Status: Must · AC-107b (FR-107) · Quelle: UX-Review T-007b @ 7aff227; Entscheidung Jens 2026-10-20
Checkliste: INT-01, ROB-01 · Spec: T-007b AC-107b, FR-107 (Must), FR-047

## Problem
AC 5: Ungelöste Agent-Anfragen („braucht Antwort“) bleiben im Channel sichtbar, auch wenn die
Liste gescrollt wird. Die Leiste nimmt ihre Kandidaten aber nur aus der geladenen Tail-Seite:

- `web/src/chat/ChannelView.tsx` (neu, `requestRoots`): filtert `messages.data?.items`
- `fetchChannelMessages` (`web/src/chat/chatApi.ts:182`) lädt `?tail=true`
- Server-Standardwert `_DEFAULT_MESSAGES_LIMIT = 50` (`omnigent/server/routes/chat.py:53`)

## Laufzeit
Kommen nach der Wurzel eines Agent-Threads mehr als 50 Nachrichten (Wurzeln oder Folgenachrichten
im Channel), liegt die Wurzel nicht mehr in der Seite. Die Leiste fehlt dann, obwohl der Agent
weiter wartet. Gerade in aktiven Channels geht die Anfrage so verloren, also genau dort, wo AC 5
helfen soll.

## Korrektur (Anforderung)
Die Leiste zeigt jede Agent-Thread-Wurzel des Channels, deren Chip-Zustand `waiting` ist,
egal ob die Wurzel in der geladenen Seite liegt. Mögliche Quellen: der bereits geseedete
Agent-Thread-Store bzw. der Watch-Set (`useChannelAgentThreads`), eine serverseitige Liste
offener Agent-Threads je Channel oder `chat_threads`. Bleibt eine Lücke bewusst bestehen, muss
sie in AC/Spec stehen (Entscheidung Jens), nicht still im Code.
Für die Thread-Benennung ohne geladenen Wurzeltext gilt der vorhandene Fallback „Agent-Thread“.

## Abnahme
- E2E in `tests/e2e_ui/team_chat`: Agent-Thread anlegen, ≥ 51 weitere Nachrichten posten,
  Status auf waiting setzen, dann ist `chat-pending-request` bei 1280×800 und 390×844 sichtbar,
  und ein Klick öffnet den Thread.
- Nach Auflösung verschwindet die Leiste (bestehendes Verhalten).
