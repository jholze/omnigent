# CI-Status-Ansicht in Omnigent – Intake

Version 0.2 · 2026-10-19 · Status: Entwurf (Quick Path; Stufe 4 Requirements: `04-requirements.md` v0.2)

## Idee (Jens, 19.10. 00:09)
Grafische Ansicht (GUI) in Omnigent, die zeigt, welche automatischen Tests (CI, GitHub Actions auf den self-hosted Mac-Runnern) gerade laufen. Anforderung von Jens: "geile UX" – visuell, keine Logliste.

## Anlass
Mac unter Volllast durch parallele Pytest-Läufe für PR #28–#30, ohne dass in Omnigent eine aktive Session sichtbar war. Ursache war nicht erkennbar.

## Vorschläge (Defaults aus v0.1; Status siehe „Entscheidungen“)
- [ÜBERNOMMEN in Spec, FR-005/FR-022/FR-040] Inhalt: pro offenem PR die Pflicht-Checks (Web, ruff/pyrefly, Pytest) plus E2E mit Status, Laufzeit, Log-Link; welcher Runner (actions-runner-omnigent, -2) gerade was macht.
- [ENTSCHIEDEN → Nr. 1 + Nr. 2] Ort: eigenes Panel + Status-Badge in der Session, die den PR gepusht hat.
- [ENTSCHIEDEN] Scope v1: nur anzeigen, kein Rerun/Cancel.
- Umsetzung UI über ux-builder, Prüfung über ux-reviewer (Regel vom 18.10.).

## Rahmen
- Null GitHub-Kosten, nur self-hosted Runner; kein CI-Throttling; deaktivierte Workflows nicht anfassen.

## Open Questions
- [OFFEN → Spec Q-1/Q-3] Datenquelle: GitHub API (Polling/Webhook) vs. lokaler Runner-Status.
- [OFFEN → Spec Q-2, Vorschlag Should] Auch Mac-Last (CPU) anzeigen?
- [OFFEN → Spec Q-5] Start erst nach PR #34 / R0-Demo 21.10.?

## Einordnung (Vorschlag 19.10. 00:12)
1. Statuskarte in der pushenden Session (inline unter dem Push).
2. Globaler Indikator in der Sidebar + Panel (alle offenen PRs, beide Runner).
3. Später: Karte im Team-Chat, live aktualisiert, Lead reagiert auf Rot.
Start mit 1+2; Karte als wiederverwendbare Komponente, damit 3 ohne Umbau geht.

## Entscheidungen (Jens, 19.10.)
- [ENTSCHIEDEN] Mockup Nr. 1 `sidebar-panel` (Sidebar-Indikator „CI n aktiv“ + rechtes Panel: PRs gruppiert Läuft/Wartet/Fertig mit Check-Pills + ETA; Runner-Abschnitt; Mac-Last CPU/RAM; Warteschlange). Ersetzt die [ANNAHME] zum Ort (Panel).
- [ENTSCHIEDEN] Mockup Nr. 2 `session-card` (Inline-Karte unter der Push-Nachricht der pushenden Session; ein-/ausgeklappt; Segmentbalken je Check, Laufzeit vs. typisch, ETA, Log-Links). Ersetzt die [ANNAHME] „Status-Badge in der Session“.
- [ENTSCHIEDEN] Release 1 = Nr. 1 + Nr. 2 + Nr. 5 (Badges als gemeinsame Komponente).
- [ENTSCHIEDEN] Release 2 = Nr. 4 Live-Karte im Team-Chat (Lead postet und reagiert auf Rot; `team_chat` ist aus und bleibt aus, bis entschieden) + Nr. 3 Runner-Zeitleiste als optionale Ansicht.
- [ENTSCHIEDEN] Scope R1 nur anzeigen. Non-Goals: Rerun/Cancel, Workflows bearbeiten, GitHub-hosted Runner, Benachrichtigungen außerhalb von Omnigent, Team-Chat-Karte.
- [ENTSCHIEDEN] UX: „geile UX“, echte GUI statt Logliste. UI über ux-builder, Prüfung durch ux-reviewer. Die Mockups (`mockups/*.html`, `mockups/png/`) sind die visuelle Source of Truth.
- [ENTSCHIEDEN] Bevorzugte Komponentenquelle für Micro-Interactions: https://www.interior.dev/ (MIT; React + Tailwind + Motion). Im Web-Stack fehlt Motion → Q-10 in der Spec.
- Weiterhin offen (jetzt in `04-requirements.md` §12): Datenquelle (Q-1), Mac-Last in R1 (Q-2, Vorschlag Should), Runner-Datenquelle (Q-3), Rollenzugriff (Q-4), Start nach R0-Demo 21.10. (Q-5; PR #34 ist inzwischen auf main gemerged, `4b212fa`).

## Changelog
- 0.1 (2026-10-19): Erstfassung aus Chat.
- 0.1+ (2026-10-19 00:12): Abschnitt „Einordnung“ ergänzt.
- 0.2 (2026-10-19): Entscheidungen von Jens zu Mockups und Release-Schnitt eingetragen, interior.dev als Komponentenquelle, offene Fragen in die Requirements Spec v0.2 übertragen.
