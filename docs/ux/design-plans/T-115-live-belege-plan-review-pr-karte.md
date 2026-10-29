# T-115 – Live-Belege für Plan-Review und PR-Karte (Blocker B1)

Status: offen · Quelle: UX-Review T-115, feat/team-chat-t115 @ c337294756ca (Working Tree, uncommitted)
Checkliste: RSP-01 (H), RSP-02 (B), RSP-03 (H), PRO-05 (H) · Spec: NFR-032, FR-059/AC-059a, FR-060/AC-060a/b

## Problem
T-115 liefert drei Oberflächen im offenen Agent-Thread: Schritte (StepsGroup/TaskSteps), Plan-Review
(`PlanReview` in `web/src/chat/agent/AgentThreadView.tsx`) und PR-Karte (`web/src/chat/agent/PrCard.tsx`).
Nur die Schritte sind im echten Browser bei 1280×800 und 390×844 belegt
(`tests/e2e_ui/team_chat/test_agent_steps.py`, 8 passed, vom Reviewer nachgelaufen).
Für Plan-Review und PR-Karte gibt es nur Vitest (jsdom, ohne Layout):
- kein Test bei 1280×800 und bei 390×844,
- kein Beleg für 44 px Touch-Ziele auf dem Phone (die Plan-Buttons bekommen 44 px nur über
  `max-md:[&_button]:min-h-11` am `CardMount`, der PR-Link über `min-h-11 … md:min-h-0`),
- kein Beleg, dass ein langer PR-Titel oder ein langer Plan (lange Wörter, Codeblock, Tabelle) umbricht
  und die Seite nicht horizontal scrollt,
- keine Screenshots einer der drei Oberflächen.
NFR-032 und der Auftrag sagen: ein fehlender Viewport ist kein Pass. Die Checkliste sagt: eine Review ohne
Screenshots für die berührten Oberflächen ist kein Pass.

## Vertrag
- NFR-032: 1280×800 und 390×844, kein horizontaler Scroll, Touch-Ziele ≥ 44 px auf dem Phone, lange Texte brechen um.
- AC-059a: Thread zeigt `ExitPlanModeReview`; EDIT-Viewer kann freigeben; READ-Viewer sieht dieselbe Review mit deaktivierten Controls (FR-056/059).
- AC-060a: PR-Karte „#42“, Status „open“, CI „grün“, Link. AC-060b: ohne GitHub-Verbindung keine Karte, keine Fehlermeldung.

## Korrektur (Verhalten, nicht Geschmack)
1. E2E in `tests/e2e_ui/team_chat/` (bestehender Baum, kein `web/e2e`), parametrisiert mit `DESKTOP`/`PHONE`
   aus `test_viewports.py`, Testnamen mit AC-ID:
   - `test_ac_059a_plan_review_*`: Elicitation mit `exitPlanMode.plan` über den vorhandenen Seam
     (wie die Approval-Card-Tests in `test_agent_thread.py`). Prüfen: `plan-review[data-state=pending]` sichtbar;
     auf dem Phone jeder Button `_assert_touch_target`; Plan mit langem ungebrochenem Wort + Codezeile ⇒
     `_assert_within_viewport` und `_assert_no_horizontal_scroll`; Freigabe-Klick sendet `accept`.
     Zweite Variante als READ-Viewer: `data-state=read-only`, alle Buttons `disabled`, Lesetext sichtbar.
   - `test_ac_060a_pr_card_*`: PR-Info über den bestehenden Test-Seam der Ressourcenroute
     (`…/resources/github/prs`) bzw. das Muster der PullRequestPanel-Tests stubben, nicht echtes GitHub.
     Prüfen: „#42“, „open“, „grün“, Link mit `target=_blank`; Phone: Link ≥ 44 px hoch; 200-Zeichen-Titel
     mit langem Wort umbricht, kein horizontaler Scroll.
   - `test_ac_060b_*`: Provider nicht verbunden ⇒ `pr-card` count 0 und keine Fehlermeldung im Thread, beide Viewports.
2. Screenshots dieser Zustände (Schritte zu/auf/Fehler, Plan pending/read-only, PR-Karte, je 1280×800 und
   390×844, plus `reducedMotion: reduce`) unter `/tmp/omnigent-chat-ux/T-115/` für den Re-Review bereitstellen
   oder dem UX-Reviewer einen laufenden Wegwerf-Server mit `team_chat` nennen.

## Re-Review 2026-10-29 (gleicher Pin c337294756ca)
- Korrektur 1 ist im Code erledigt: `tests/e2e_ui/team_chat/test_agent_steps.py` enthält AC-059a (pending, EDIT-Accept, READ),
  AC-060a (Karte, langer Titel) und AC-060b, je 1280×800 und 390×844. Ein Lauf mit echten Zahlen liegt dem Reviewer nicht vor.
- Korrektur 2 ist offen. Die 15 PNGs unter `.ux-shots/` existieren (Größe > 0). Das Datei-Lesetool des Reviewers
  gibt bei Binärdateien aber nur „Binary file not inlined“ zurück. Shell und file:// sind laut Auftrag gesperrt.
  Der Reviewer hat die Oberflächen also nicht gesehen.
- Was B1 schließt: eines davon. (a) Der Re-Review läuft in einer Sitzung, deren Lesetool PNGs als Bild ausgibt.
  (b) Der Auftrag erlaubt `browser_navigate` auf einen Wegwerf-Server mit `team_chat` oder auf die Bilder.
  (c) Der Auftrag nennt die Testausgabe mit echten Zahlen (`N passed`) und erlaubt dem Reviewer, eigene Screenshots per Playwright MCP zu machen.
- Lücke im Beleg: Der lange 200-Zeichen-PR-Titel wird nur per Assertion geprüft. Davon gibt es keinen Screenshot
  (`pr-card-*` zeigt den kurzen Titel). Für den Re-Review bitte `pr-card-long-title-{viewport}.png` ergänzen.

## Abnahme
- Neue E2E grün mit echten Zahlen (Tester-Gate), beide Viewports je Test.
- Re-Review sieht Screenshots aller drei Oberflächen in beiden Viewports; B1 geschlossen.
