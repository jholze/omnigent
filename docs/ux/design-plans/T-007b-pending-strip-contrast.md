# T-007b – Kontrast der Leiste „Agent braucht Antwort“ (helles Theme)

Status: offen · Schwere: Blocker · Quelle: UX-Review T-007b @ 7aff227 (ungestagter Diff)
Checkliste: A11Y-05 · Spec: NFR-009 (WCAG 2.2 AA, 1.4.3)

## Problem
Die Leiste mit offenen Agent-Anfragen (`web/src/chat/agent/PendingAgentRequests.tsx`) zeichnet
ihren Hintergrund mit `bg-warning/15` und zwei Textteile in `text-muted-foreground`:
- das Bindewort „ in “ (Z. 79)
- das sichtbare Label „Thread öffnen“ (Z. 82, ab md sichtbar, durch `chatScale.css` 14 px)

## Beleg (Contract)
Token aus `web/src/index.css:234` (`--warning: #d4972a`) und
`web/src/themePalettes.generated.css:4/20` (`--background: #ffffff`, `--muted-foreground: #71717a`).
15 % Warnfarbe auf Weiß ergibt etwa `#f9efdf`.

    python3 docs/ux/scripts/contrast.py 71717a f9efdf  → 4.24:1  Normal text AA FAIL

Beim Hover kommt `hover:bg-warning/10` dazu, der Wert sinkt also weiter. Das dunkle Theme
besteht (`92a4b3` auf `2c2416` → 5.97:1). Die Zahlen sind aus den Tokens gerechnet, live
gemessen ist noch nichts. Bei der Abnahme mit dem berechneten Stil im Browser gegenprüfen.

## Laufzeit
Sobald eine Anfrage offen ist, sehen alle Personen im Channel die Leiste, oben am Channel
angeheftet. Bei 1280×800 ist „Thread öffnen“ sichtbar. Auf dem Phone ist es `sr-only`, dort
bleibt nur „ in “ betroffen.

## Korrektur (Anforderung, keine Stilvorgabe)
Jeder Text in der Leiste braucht im hellen und im dunklen Theme, im Ruhe- und im Hover-Zustand
mindestens 4,5:1 gegen den tatsächlich gerenderten Hintergrund. Nur vorhandene Theme-Tokens
verwenden (VIS-02), keine Roh-Hex-Werte. Möglich sind zum Beispiel Vordergrund-Token statt
muted auf der Tönung oder eine schwächere Tönung. Die Wahl liegt beim Builder.

## Abnahme
- `contrast.py` für alle Textfarben × {Ruhe, Hover} × {hell, dunkel} ≥ 4.5:1, Werte im PR notieren.
- Screenshot 1280×800 hell und dunkel mit sichtbarer Leiste unter
  `/tmp/omnigent-chat-ux/T-007b/`.
