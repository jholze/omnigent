# T-115 · Fehlerzustand der Schritte

Status: PASS nach Neubau (12:40). Die Bilder vom 12:33 sind kein Beleg für den Fix.

## Verdikt über die Bilder von 12:33

Verdikt: BLOCK
In steps-error-390x844.png ist die Zeile bei y 382–391 ein Geisterbild (#F7F7F7 auf #FFFFFF, 1,07:1). In steps-error-1280x800.png ist das einzige deckende Rot #c8324c ein Icon bei x 289–302, y 311–324, nicht ein Kreuz vor dem Schritt.

## Warum diese Bilder den Fix nicht zeigen

`storedAgentReplyCount` liegt in `AgentThreadView.tsx` und `ThreadPane.tsx` (mtime 12:31). Das ausgelieferte SPA unter `omnigent/server/static/web-ui/` ist von 12:19. Der Screenshot-Lauf nutzte `--ui-skip-build` und hat deshalb die alte Oberfläche fotografiert. Die Systemzeile nimmt dort die Live-Ansicht weiter nach wenigen Millisekunden zurück. Ein neues Bild braucht einen Build ohne `--ui-skip-build`.

## Soll

Die fehlgeschlagene Schrittzeile bleibt neben `Agent-Fehler: …` stehen. Das Kreuz ist im Endzustand deckend, Farbe `--destructive` (#c8324c hell), Kontrast mindestens 3:1. Eine gespeicherte Agent-Antwort ersetzt die Live-Ansicht weiter.

## Nachprüfung B-1 · Bilder nach dem Neubau (12:40, `?rebuild=1240`)

Gemessen pixelgenau auf einem Canvas aus den PNGs von 127.0.0.1:8765. Gezählt wurde nur das deckende Rot, der Antialiasing-Rand nicht.
- steps-error-1280x800.png: Das Kreuz bei x 628–635, y 209–220 hat deckende #c8324c-Pixel. Der Text daneben bei x 643–679 auch. Insgesamt 54 Pixel #c8324c auf #FFFFFF, 5,23:1. Die zweite Fehlerzeile bei x 1037–1115, y 522–536 hat 149 Pixel #c8324c, 5,23:1. Das Seitenleisten-Icon bei x 290, y 312 ist nicht gewertet.
- steps-error-390x844.png: Das Kreuz bei x 66–73, y 495–502 hat 14 Pixel #c8324c, 5,23:1, mindestens 3:1 (NFR-008, 1.4.11). Der Text bei x 140–218, y 496–510 hat 149 Pixel #c8324c, 5,23:1, mindestens 4,5:1 (1.4.3). Die Zeilen y 378–386 enthalten kein #F7F7F7 mehr, nur #FFFFFF und Text in #71717A. Es gibt keine Geisterzeile.
- H-1, englische Plan-Buttons, lange Titel und Reduced Motion auf dem Phone wurden auftragsgemäß nicht bewertet.

Verdikt: PASS
