# Design-Plan T-124 – Tipp-Indikator (FR-032, AC-032a)

Quelle: UX-Review T-124, feat/team-chat-t124 @ 40aac62f (gestagter Diff `web/src/chat`), 29.10.2026.
Belege: `/tmp/omnigent-chat-ux/t124/` (Screenshots, `results.json`), lokal, nicht committet.
Für: ux-builder. Dieser Plan beschreibt Probleme und Abnahmekriterien, keine fertige Implementierung.

## B1 – Zeile verschwindet zu früh (AC-032a)

**Vertrag.** AC-032a: „Given Anna tippt in "#general" Then sieht Jens "Anna schreibt…" und es
verschwindet 4 s nach Annas letztem Tastendruck.“ FR-032: höchstens ein `typing`-Frame je 3 s,
Anzeige bis 4 s ohne Auffrischung. Checkliste INT-01 (B), Nielsen H1 (Sichtbarkeit des Status).

**Laufzeit (gemessen, Chromium, team_chat-Fixture, Build des gestagten Stands).**
- Kurzer Burst („Hallo zusammen“, ca. 0,6 s): Zeile weg nach 3,8 s nach dem letzten Tastendruck (ok).
- Durchgehend ca. 2,5 s tippen, dann aufhören: Zeile weg nach **1,79 s** (zwei Läufe, beide 1,79 s).
- Tippen mit Pausen (Bursts bei 0 s / 2,9 s / 4,7 s): Zeile weg ca. **2,3 s** nach dem letzten Tastendruck.
- `results.json` Schlüssel `timing`, `flicker-probe`.

**Ursache.** `web/src/chat/ChatComposerCard.tsx:124-130` drosselt nur an der Vorderkante: ein
`active: true` geht beim ersten Tastendruck raus, weitere Tastendrücke innerhalb von 3 s werden
verworfen, ohne nachträglichen Frame. `web/src/chat/typingStore.ts:64-67` blendet 4 s nach dem
letzten *Frame* aus. Damit endet die Anzeige je nach Tipp-Rhythmus 1–4 s nach dem letzten
Tastendruck, nie „4 s danach“. Zwischen zwei Frames kann die Zeile zudem bei Tipp-Pausen kurz
verschwinden und wiederkommen, obwohl Anna weiterschreibt.

**Korrektur (Problem, nicht Geschmack).**
- Nach dem letzten Tastendruck muss die Anzeige bei Jens mindestens 4 s stehen bleiben.
- Die 3-s-Grenze (Client und Server, `routes/chat.py` `_typing_allowed`) bleibt bindend: ein
  nachlaufender `active: true` am Ende des 3-s-Fensters, wenn seit dem letzten gesendeten Frame
  getippt wurde, erfüllt beides (Server nimmt ihn an, weil ≥ 3 s vergangen sind).
- Kein neues Feld im Frame (NFR-014: nur `channel_id`, `principal_id`, `active`).
- Leeren/Senden/Blur beenden weiterhin sofort (`active: false`), der nachlaufende Frame wird dann
  verworfen.

**Abnahme.**
- E2E (`tests/e2e_ui/team_chat`): Anna tippt 2,5 s durchgehend, hört auf → Zeile bei Jens ist
  3,9 s nach dem letzten Tastendruck noch sichtbar und spätestens nach 7,5 s weg.
- Bursts bei 0 / 2,9 / 4,7 s: Zeile bleibt durchgehend sichtbar (kein Aus/An), Abfrage alle 150 ms.
- Senden per Enter: Zeile < 1 s weg (heute 0,03 s, bleibt so).
- Keine Animation hinzufügen (MOT-00).

## H1 – Zeile doppelt und im falschen Kontext bei offenem Thread

**Vertrag.** 07-tasks.md T-124 Scope: `ChatComposerCard.tsx`, `ChannelView.tsx` – ThreadPane steht
nicht im Scope (PRO-01, H). A11Y-06 (H): Live-Ansagen gebündelt, ≤ 1 Ansage / 2 s. FR-032 kennt
nur Channel-Ebene; der Frame trägt keinen Thread.

**Laufzeit.** 1440×900, Thread offen, Carla tippt im Channel: zwei sichtbare Zeilen „Carla schreibt…“,
eine unter dem Channel (x 580, 456 px) und eine im Thread-Pane (x 1077, 343 px), 6 `aria-live`-Regionen
auf der Seite statt 4 – Beleg `/tmp/omnigent-chat-ux/t124/1-thread-open-typing-1440.png`,
`results.json` → `thread-1440`. Das Thread-Pane behauptet damit, dass jemand im Thread antwortet,
obwohl sie im Channel schreibt; Screenreader hören dieselbe Ansage zweimal.
Ursache: `web/src/chat/ThreadPane.tsx:354` rendert denselben channelweiten Indikator.

**Korrektur.** Pro Ansicht nur eine Zeile und nur eine Live-Region für dieselbe Information. Solange
der Frame keinen Thread kennt, darf das Thread-Pane keine channelweite Tipp-Zeile zeigen (oder die
Entscheidung, Thread-Tippen anzuzeigen, wird mit Spec-Änderung getroffen, nicht im UI geraten).

**Abnahme.** 1440×900 mit offenem Thread: genau ein `chat-typing-indicator` sichtbar, unter dem
Channel. 375×812 Thread-Vollbild: keine irreführende Zeile im Thread (Entscheidung dokumentiert in
`docs/ux/system.md`, PRO-03). Anzahl `[aria-live]` steigt durch T-124 um höchstens 1.
