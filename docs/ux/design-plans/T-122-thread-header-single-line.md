# Thread-Kopf bleibt einzeilig lesbar, Status-Chip wird nie gequetscht
Status: resolved
Task: T-122 (Fix gehört an den UI-Task des Thread-Panes, z. B. Folge zu T-115)  Severity: Blocker  Checklist: INT-01, RSP-01, ROB-02, ROB-06, ICO-04  Spec: FR-044, FR-055, NFR-032
Written against: feat/team-chat-t122 @ 6d180bcbfdaf3c11b7ff185df470f71d84f10be4
Ersetzt: `docs/ux/design-plans/T-115-thread-header-reflow.md` (High, gleiche Ursache, dort nur mit kurzem Channel-Namen belegt). Der Builder setzt den alten Plan auf `rejected (ersetzt durch T-122-thread-header-single-line)`.

## Evidence chain
- Surface: `/chat/:channelId/t/:threadRootId`, Thread-Pane ab `md` (768, 1280, 1440), Agent-Thread mit offener Freigabe
- Problem: Der Channel-Name im Thread-Kopf wird mit Breite 0 px gerendert und bricht zeichenweise um. Bei einem gültigen Namen mit 39 Zeichen (`qualitaetssicherung-donaudampfschiff-26`, Regex erlaubt 40) ist der Kopf 638 px (1440×900), 538 px (1280×800) und 762 px (768×1024) hoch. Von Root-Nachricht, Agent-Antwort und Freigabe-Karte ist nichts zu sehen. Die Buchstaben ragen oben aus dem Pane und liegen über den Agent-Steuerknöpfen („Wiederholen“). Der Status-Chip wird auf 69 px (1440), 5 px (768) bzw. 14 px (320) gequetscht und zeigt „braucht …“, „b.“ oder nur das Icon. Gleiches Bild für Anna (EDIT) und Jens (READ).
- Evidence: `/tmp/omnigent-chat-ux/t122/2-thread-1440.png`, `2-thread-1280.png`, `2-thread-768.png`, `1-jens-readonly-thread-1440.png`, `2-thread-320.png`, `2-thread-390.png` (Chip „braucht A…“); Messwerte in `/tmp/omnigent-chat-ux/t122/notes.json` → `metrics.threadheader-*` (`headerH` 638/538/762, Label-Span `w: 0, h: 800`); Code `web/src/chat/ThreadPane.tsx:324` (Header `flex` ohne Umbruch), `:329` (`min-w-0 flex-1 break-words`), Chip-Wrapper `web/src/chat/agent/AgentStatusChip.tsx:156` (`max-w-full min-w-0`, schrumpft mit)
- Owner: `web/src/chat/ThreadPane.tsx` (Header-Zeile)
- Affected surfaces: Thread-Pane in Channel und DM, alle Breiten ab 768; Chip-Kürzung auch auf 320–390
- Uncertainty: keine. Bei kurzen Namen (`e2e-xxxxxxxx`) ist der Fehler schon im T-115-Review belegt (Kopf ≈ 270 px).

## Design decision
Der Kopf ist eine Werkzeugzeile. Der Channel-Name ist Kontext, kein Inhalt. Er wird einzeilig mit Ellipsis gekürzt, der volle Name steht im `title`. Der Status-Chip darf nicht unter die Breite seines sichtbaren Labels schrumpfen (FR-044: das Label muss lesbar sein). Reicht die Zeile nicht, umbrechen Chip und „Session öffnen“ in eine zweite Zeile, statt den Chip zu kürzen. Zwei Zeilen (Kopf ≤ 96 px) sind an allen genannten Breiten spec-konform. Der Thread-Inhalt beginnt immer direkt darunter.

## Reuse
- Tailwind `truncate` wie am Chip-Label `web/src/chat/agent/AgentStatusChip.tsx:140`; `title` für den vollen Text wie am Schließen-Knopf `web/src/chat/ThreadPane.tsx:339`; `flex-wrap` wie in `web/src/chat/MessageItem.tsx:540` (`flex min-w-0 flex-wrap items-center gap-x-2`)

## Changes
1. `web/src/chat/ThreadPane.tsx:329` – Change: Channel-Label `break-words` durch `truncate` ersetzen, `title={channelLabel}` setzen. · Preserve: `max-md:hidden` (auf dem Phone steht der Name im Zurück-Link), `text-xs text-muted-foreground`. · Verify: Label-Span hat genau eine Zeile (Höhe ≤ 20 px) und Breite > 0 oder ist ausgeblendet.
2. `web/src/chat/ThreadPane.tsx:324` – Change: Header auf `flex-wrap` mit `gap-y-1` umstellen. Der Status-Wrapper (`AgentThreadStatus`) bekommt `shrink-0`, damit der Chip nicht unter sein Label schrumpft. · Preserve: `min-h-11`, Schließen-Knopf rechts (`ml-auto`, 44 px auf Touch), Escape schließt. · Verify: Bei 1440, 768, 390 und 320 Kopfhöhe ≤ 96 px, Chip-Text vollständig („braucht Antwort“, „arbeitet im Hintergrund“).

## Validation
- Interface: Agent-Thread mit offener Freigabe in einem Channel mit 40-Zeichen-Namen und mit kurzem Namen; 1440×900, 1280×800, 768×1024, 390×844, 375×812, 320; Chip-Zustände „startet…“, „braucht Antwort“, „arbeitet im Hintergrund“; reduced motion. Root-Nachricht und Freigabe-Karte sind ohne Scrollen des Kopfes sichtbar.
- Repository: `uv run --group test pytest tests/e2e_ui/team_chat/test_agent_thread.py -q -k "nfr032"` → grün; `test_nfr032_long_channel_thread_header_is_one_row` prüft Kopf ≤ 96 px, Chip ungekürzt, Channel-Name mit `title` gekürzt, Freigabe-Karte sichtbar, bei 1440, 768, 390 und 320 px. Kein xfail für dieses Kriterium.

## Stop conditions
- Stop, wenn der Chip in `AgentStatusChip.tsx` dafür seine reservierte Breite (`AGENT_CHIP_WIDEST_LABEL`) aufgeben müsste. Das ist eine T-117-Motion-Entscheidung (kein Springen bei Statuswechsel) und gehört zu Jens.

## Design documentation
- Nach Abnahme in `docs/ux/system.md` festhalten: „Kontext-Labels in Kopfzeilen: einzeilig, `truncate` + `title`. Status-Chips schrumpfen nie unter ihr Label.“

## Resolution
zwei Zeilen erlaubt, 48-px-Grenze gestrichen (Spec-Entscheidung 30.10.)
Der Schließen-Knopf bleibt 44 px: `web/src/chat/chatScale.css` setzt das absichtlich über die Utility-Klasse.
