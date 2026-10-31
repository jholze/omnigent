# T-114 · „erneut senden“ an nicht zugestellter Thread-Antwort: sichtbares Label im zugänglichen Namen

Status: erfüllt (UX-Recheck PASS, 2026-10-28) · Schwere: Blocker (Checkliste A11Y-04, Standard B) · Reviewer: ux-reviewer · 2026-10-28
Basis: feat/team-chat-t114 @ 281bd791 plus uncommitteter Arbeitsbaum

## Vertrag
- FR-054 / AC-054a: Die Nachricht zeigt „nicht zugestellt – erneut senden“, und ein Klick leitet sie genau einmal weiter.
- NFR-009: WCAG 2.2 AA. Erfolgskriterium 2.5.3 „Label in Name“ (Stufe A): Bei einem Control mit sichtbarem Textlabel muss der zugängliche Name diesen Text enthalten, am besten am Anfang.
- Checkliste `docs/ux/chat-review-checklist.md` A11Y-04: „Jedes Control mit zugänglichem Namen; sichtbares Label im Namen enthalten (4.1.2, 2.5.3)“, Schwere B.

## Laufzeit-Befund (statisch belegt, live nicht geprüft, siehe Review)
`web/src/chat/MessageItem.tsx:405-418` (`ForwardStatus`, Zweig `failed`):
- sichtbarer Text des Buttons: „erneut senden“
- `aria-label="Nicht zugestellt: erneut an den Agenten senden"` überschreibt den Namen.
- Der Name enthält die Wortfolge „erneut senden“ nicht. Sprachsteuerung („klicke erneut senden“) und Screenreader-Nutzer, die sich am sichtbaren Text orientieren, finden das Control nicht unter dem Namen, den sie sehen. Das ist ein Verstoß gegen 2.5.3.

## Problem (nicht die Lösung)
Der zugängliche Name des Retry-Buttons muss den sichtbaren Text „erneut senden“ wörtlich und zusammenhängend enthalten, am besten am Anfang. Der Kontext („nicht zugestellt“, an wen) darf zusätzlich vorhanden sein, aber nicht so, dass er die sichtbare Wortfolge aufbricht.

## Korrektur-Kriterien (Abnahme)
1. Accessibility-Tree (`browser_snapshot` oder `getByRole('button', { name: /erneut senden/ })`): Der Button ist über einen Namen auffindbar, der „erneut senden“ zusammenhängend enthält.
2. Der Kontext „nicht zugestellt“ bleibt für Screenreader erreichbar, z. B. über den sichtbaren Nachbartext oder eine Beschreibung. Wie, entscheidet der Builder (Stack-Regeln `ux-stack-rules`).
3. Der sichtbare Text bleibt wörtlich „nicht zugestellt – erneut senden“ (FR-054, PRO-02).
4. Ein Vitest in `web/src/chat/forwardStatus.test.tsx` prüft den Namen per Rolle.
5. Nebenbei, ohne Blocker-Rang: Der Zweig `forwarding` (`MessageItem.tsx:387`) nutzt die Test-ID `forwardFailed`. Für „wird zugestellt…“ braucht es eine eigene Test-ID, damit E2E-Tests „failed“ und „forwarding“ unterscheiden können.

## Nicht Teil dieses Plans
Layout, Farbe (#c8324c auf #fff = 5,23:1, besteht) und Touch-Ziel (min-h-11 auf dem Phone) sind in Ordnung.
