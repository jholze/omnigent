# T-115 · Thread-Kopf bei 1280 px: Channel-Name bricht zeichenweise

Status: offen · Schwere: High · Reviewer: ux-reviewer · Branch feat/team-chat-t115 @ c337294756ca3b85f1fd85ff37b43fe6a5c43793

## Problem
Der Fehler tritt in allen sechs Desktop-PNGs (1280×800) auf. Im Thread-Pane steht der Channel-Name (z. B. „#e2e-81bff234“) senkrecht, ein Zeichen pro Zeile, zwischen Status-Chip und „Session öffnen“. Dadurch wird der Kopf etwa 270 px hoch (Bildpixel y≈50–320). Er schneidet den Anfang des Thread-Inhalts ab: Root-Nachricht und Plan-Codeblock sind oben angeschnitten, etwa in `steps-collapsed`, `steps-error` und `plan-pending`. Die Status-Chips „braucht …“ werden gekürzt. Bei 390 px tritt der Fehler nicht auf, dort steht der Name in der Zurück-Zeile.

## Vertrag
- RSP-01 (H): keine Überlappung, nichts abgeschnitten
- ROB-02 / ROB-06: Langer Text wird lesbar gekürzt (truncate + title), nicht zeichenweise umbrochen.
- FR-044: Status-Chip lesbar

## Herkunft
Mit diesem Bild-Review nicht geprüft, ob die Zeile im T-115-Diff liegt oder schon vorher bestand (hier kein Shell und kein Diff erlaubt). Falls sie vorher bestand: PRO-01 beachten und als eigenen Task führen, nicht in T-115 beheben.

## Korrektur (Anforderungen)
- Der Channel-Name im Thread-Kopf steht in einer Zeile mit Ellipsis und vollem Namen als `title`, oder er entfällt bei Platzmangel.
- Die Kopfhöhe bleibt bei 1280 und 768 auf eine Zeile beschränkt. Der Thread-Inhalt beginnt unter dem Kopf und wird nicht verdeckt.

## Abnahme
Neue Desktop-PNGs zeigen einen einzeiligen Kopf und eine vollständig sichtbare Root-Nachricht.
