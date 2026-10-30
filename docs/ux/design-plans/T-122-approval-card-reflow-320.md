# Freigabe-Optionen bleiben bei 320 px vollständig lesbar
Status: open
Task: T-122 (Fix gehört an den Task der Freigabe-Karte im Thread, T-008/T-115-Linie)  Severity: Blocker  Checklist: RSP-02, RSP-01, ROB-06  Spec: WCAG 1.4.10 (NFR-009), FR-055, NFR-032
Written against: feat/team-chat-t122 @ 6d180bcbfdaf3c11b7ff185df470f71d84f10be4

## Evidence chain
- Surface: Freigabe-Karte (`approval-card`) im Agent-Thread, Claude-Code-`Bash`-Anfrage, 320 px
- Problem: Die Buttons „Approve & switch to auto mode“ und „Approve & don't ask again for Bash“ brechen nicht um. Sie ragen über den Kartenrand und werden am Viewport-Rand abgeschnitten („… again for Ba“). Die Seite scrollt nicht horizontal (`scrollWidth = clientWidth = 320`), der Text ist also verloren, nicht nur verschoben. Das ist genau die Option, die eine Dauerfreigabe erteilt: Nutzer sehen nicht, wofür sie zustimmen.
- Evidence: `/tmp/omnigent-chat-ux/t122/2-thread-320.png`; bei 390 passt der Text knapp (`2-thread-390.png`). Code `web/src/components/blocks/ApprovalCard.tsx:515-524` (Auto-Mode-Button) und `:538-548` (Remember-Button) nutzen den `Button`-Default `whitespace-nowrap`. Dieselbe Datei bricht andere Optionen schon korrekt um: `:459` und `:477` (`h-auto max-w-full whitespace-normal text-left`).
- Owner: `web/src/components/blocks/ApprovalCard.tsx`
- Affected surfaces: Freigabe-Karte im Chat-Thread und in der Session-Ansicht (gleiche Komponente); alle Optionen mit langem Label (`Approve for this session`, `Always allow`, `Accept & allow all edits`, Codex-Aktionen ab `:563`)
- Uncertainty: keine für das Abschneiden. Die englischen Labels sind ein eigener Medium-Befund im Review (COPY-01) und nicht Teil dieses Plans.

## Design decision
Jede Option in der Aktionszeile darf auf mehrere Zeilen umbrechen und nie breiter als die Karte werden. Die Datei hat dafür schon ein Muster. Es wird auf alle Options-Buttons angewendet, statt nur auf die beiden bestehenden.

## Reuse
- Exemplar: `web/src/components/blocks/ApprovalCard.tsx:459` (`className="h-auto max-w-full whitespace-normal text-left"`, Icon `shrink-0`, Text in `<span className="min-w-0">`)

## Changes
1. `web/src/components/blocks/ApprovalCard.tsx:494-560` und `:563-590` – Change: Alle Options-`Button`s der Aktionszeilen bekommen `className="h-auto max-w-full whitespace-normal text-left"`, das Check-/X-Icon `shrink-0`. · Preserve: Reihenfolge, `componentId`, `title`, `data-testid`, Varianten (`default` für Genehmigen, `outline` sonst), `flex flex-wrap gap-2`. · Verify: Bei 320 px liegt jeder Button innerhalb der Karte (`button.right ≤ card.right`), das Label ist vollständig sichtbar, die Trefferfläche bleibt ≥ 44 px hoch auf Touch.

## Validation
- Interface: Thread mit Claude-Code-Freigabe (Bash, Remember-Ziel lang, z. B. ein MCP-Toolname mit 40 Zeichen), Codex-Freigabe mit allen Persist-Modi; 320, 375, 390, 768, 1440; hell und dunkel.
- Repository: `uv run --group test pytest tests/e2e_ui/team_chat/test_agent_thread.py -q -k "phone_390 or nfr032"` → grün; neuer Fall 320 px: jede `approval-card`-Schaltfläche liegt innerhalb der Kartenbox. Vitest der Karte: `pnpm --filter web exec vitest run src/components/blocks` → grün.

## Stop conditions
- Stop, wenn die Session-Ansicht außerhalb des Chats ein festes einzeiliges Layout verlangt (vorhandene Snapshot-Tests). Dann die Änderung auf den Chat-Kontext begrenzen und Jens fragen.

## Design documentation
- keine (Muster existiert bereits in derselben Datei)

## Resolution
