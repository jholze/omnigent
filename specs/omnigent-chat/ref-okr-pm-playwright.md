# Referenz: Frontend-Tests im Projekt OKR Odoo Module (PM-Hub-Session)

Quelle: Omnigent-Session `d97dd2f6ae984261af0287ccb6c96661` „OKR Odoo Module PM Hub“, Workspace `/Users/jholze/Documents/scripts/okr_odoo`, Antwort-Item `508609685922480bb0d5f271fdca375b` (16.10.2026, 23:12 MESZ). Gefragt hat Spec im Auftrag von Jens.

## Kurzfassung (Deutsch)

- **Im Projekt gibt es kein Playwright.** Es gibt keine `playwright.config`, keine Page Objects, keine Traces oder Screenshots und keine GitHub Actions.
- Die Frontend-Tests haben drei Schichten:
  1. **HOOT-Unit-Tests** (`bloopark_okr/static/tests/*.test.js`, Bundle `web.assets_unit_tests`). Sie prüfen reine Funktionen und laufen im Browser über `/web/tests?headless&filter=bloopark_okr` (`scripts/run_hoot_tests.sh`).
  2. **Odoo-Tours** (`bloopark_okr/static/tests/tours/okr_tours.js` mit dem Runner `bloopark_okr/tests/test_okr_tour.py`, Tag `okr_tour`). Sie decken die UI-Integration ab und starten über `TEST_TAGS=okr_tour ./scripts/run_odoo_tests.sh` (odoo-bin `--test-enable`). Die Testdaten legt die Testmethode selbst an (`_ensure_tour_cycle`), Login ist `admin`.
  3. **Puppeteer-Smoke** (`scripts/odoo_sh_verify.mjs`, `puppeteer-core`). Er hängt sich an ein bestehendes Chrome auf Port 9222 mit SSO-Session an, wartet auf den Odoo.sh-Build und prüft dann die Instanz: Konsole leer, Test-IDs vorhanden, Features. Das Ergebnis lautet `RESULT: PASS` oder `RESULT: FAIL` (optional `--json`). Er startet keinen Server und speichert kein Passwort.
- **Selektoren:** `data-testid` mit dem Präfix `okr-` in kebab-case, gesetzt im OWL-XML. Geteilte Widgets bekommen die ID als Prop `testId`. Die Liste der IDs steht in `docs/TEST_PLAN.md`.
- **CI:** Nur Odoo.sh auf Development-Branches (Python-Suite inklusive Tours). HOOT und Smoke laufen außerhalb davon. Mehr dazu in `docs/ODOO_SH_TESTS.md`.
- **Rollen** (`Agents.md`, `agents/lead/agents/`): Der `tester` schreibt und führt HOOT und Tours aus („Not run“ ist kein Pass). `devops` führt nach dem Push den Smoke aus. Der `coder` setzt die `data-testid`.
- **Was sich bewährt hat:** stabile Test-IDs, die alle Schichten gemeinsam nutzen, Testdaten aus dem Test selbst statt aus Demo-Daten, eine klare Trennung der drei Schichten, ein eindeutiges PASS-Kriterium und ein Verhalten pro Test.
- Für Omnigent-Chat lassen sich die Muster übernehmen (Test-ID-Konvention, Seeding im Test, Gates, Rollen). Die Playwright-Infrastruktur selbst gibt es hier nicht als Vorlage.

---

## Antwort der Session (wörtlich)

In diesem Repo gibt es kein Playwright. Kein `playwright.config`, keine Page Objects, keine Traces oder Screenshots, keine GitHub Actions. Die Frontend-Prüfung sitzt in drei Schichten: HOOT-Unit-Tests im Browser, Odoo-Tours und ein Puppeteer-Smoke gegen die laufende Odoo.sh-Instanz.

| Angefragt | Was hier steht |
|---|---|
| Ordner | `bloopark_okr/static/tests/*.test.js`, `bloopark_okr/static/tests/tours/okr_tours.js`, `bloopark_okr/tests/test_okr_tour.py`, `scripts/odoo_sh_verify.mjs` |
| Config | Asset-Bundles in `bloopark_okr/__manifest__.py`, Smoke-Config in `.odoo-sh.json` |
| Fixtures | Tour-Helfer in `okr_tours.js`, Seed in der `HttpCase`-Methode. Keine Page-Object-Klassen |
| Daten und Auth | XML `noupdate="1"` plus Seed in der Testmethode. Tour-Login `admin`. Smoke nutzt die Chrome-SSO-Session auf Port 9222 |
| Server | Odoo läuft schon. Tours startet `odoo-bin` selbst. Der Smoke startet keinen Server |
| CI | Odoo.sh-Build nur auf Development-Branches. HOOT und Smoke laufen außerhalb dieses Builds |
| Report | Odoo-Testlog, HOOT-UI, Smoke-Text `RESULT: PASS` oder `--json` |
| Selektoren | `data-testid`, Präfix `okr-`, kebab-case |

## Schichten

`docs/TEST_PLAN.md` beschreibt die Pyramide. Der Plan listet bei HOOT nur `okr_utils.test.js`. Im Baum liegen zusätzlich `okr_dropdown.test.js`, `alignment_mindmap_layout.test.js`, `alignment_tree_utils.test.js` und `alignment_viewport.test.js`.

1. **HOOT** prüft reine Funktionen ohne Klickstrecke. Bundle `web.assets_unit_tests` lädt `bloopark_okr/static/tests/**/*.test.js`. Start ist `./scripts/run_hoot_tests.sh`. Das Skript druckt nur die URL. Odoo muss schon laufen:

```bash
http://localhost:8069/web/tests?headless&filter=bloopark_okr
```

Ein Test importiert die Funktion direkt und setzt den Tag `okr`:

```javascript
import { describe, expect, test } from "@odoo/hoot";
import { progressBar } from "@bloopark_okr/components/okr_utils";

describe.current.tags("okr");

describe("OKR utils", () => {
    test("progressBar renders filled blocks and percentage", () => {
        expect(progressBar(42)).toBe("████░░░░░░ 42%");
    });
});
```

2. **Tours** sind die UI-Integration. `web.assets_tests` lädt `bloopark_okr/static/tests/tours/**/*.js`. Der Python-Runner steht in `bloopark_okr/tests/test_okr_tour.py`, Tag `okr_tour`. Start:

```bash
TEST_TAGS=okr_tour ./scripts/run_odoo_tests.sh
```

`scripts/run_odoo_tests.sh` verlangt `ODOO_BIN` und `ODOO_DB`. Die Datenbank muss das Modul schon installiert haben. Das Skript ruft `odoo-bin --stop-after-init --test-enable --test-tags` auf. `UPGRADE=1` hängt `-u bloopark_okr` an.

3. **Smoke** ist `scripts/odoo_sh_verify.mjs` mit `puppeteer-core` (`scripts/package.json`). Er hängt sich an Chrome auf `127.0.0.1:9222`, wartet den Odoo.sh-Build ab und öffnet die Dev-Instanz über den CONNECT-Link. Er startet weder Odoo noch Chrome.

## Daten und Auth

Tours hängen nicht an Demo-XML. `test_okr_tour.py` sagt das ausdrücklich: Odoo.sh-Testdatenbanken installieren ohne Demo. `_ensure_tour_cycle()` sucht den Zyklus `Q4 2026` und das Objective `OKR Tour Draft Objective` und legt sie an, wenn sie fehlen. Danach:

```python
self.start_tour('/odoo/okr', 'okr_my_okrs_smoke_tour', login='admin')
```

Dieselben Datensätze stehen zusätzlich in `bloopark_okr/data/okr_tour_data.xml` mit `noupdate="1"`. Der Smoke auf der Dev-Instanz nutzt `bloopark_okr/data/okr_smoke_seed.xml` (Zyklus Q2 2026, Company- und Team-Objective, Key Results). Demo bleibt in `bloopark_okr/demo/okr_demo_data.xml` und ist für die Tests nicht die Quelle.

Der Smoke speichert kein Passwort. Voraussetzung ist eine bestehende odoo.sh-SSO-Session in dem Chrome mit `--remote-debugging-port=9222`. `pageLooksLoggedOut` bricht ab, wenn die Seite auf `/web/login` landet. Skill: `.grok/skills/odoo-sh-verify/SKILL.md`.

## Tour-Form und Selektoren

Eine Tour ist ein Eintrag in `registry.category("web_tour.tours")`. Jeder Step hat `content` und `trigger`. Wiederholte Klicks sitzen in einer kleinen Funktion, nicht in einer Page-Object-Klasse:

```javascript
registry.category("web_tour.tours").add("okr_my_okrs_smoke_tour", {
    url: "/odoo/okr",
    steps: () => [
        { content: "OKR app loads", trigger: ".o_okr_app" },
        { content: "Cycle selector is available", trigger: '[data-testid="okr-cycle-select"]' },
        { content: "New objective button is available", trigger: '[data-testid="okr-new-objective"]' },
    ],
});
```

`data-testid` steht im OWL-XML der Komponente, zum Beispiel `okr-summary`, `okr-body`, `okr-objective-row` in `bloopark_okr/static/src/client_actions/my_okrs/my_okrs.xml`. Die Liste der vereinbarten IDs steht in `docs/TEST_PLAN.md`. Konvention: Präfix `okr-`, kebab-case, ein Widget ein Name (`okr-cycle-select`, `okr-kr-checkin`, `okr-checkin-value`). Geteilte Widgets bekommen die ID als Prop (`testId`, `groupTestId` in `okr_dropdown.xml` und `okr_segmented_control.xml`). Der Smoke liest dieselben IDs in `page.evaluate` (`readOkrTestIds`). CSS-Klassen wie `.o_okr_app` bleiben die Ausnahme für die App-Hülle.

## CI, Rollen, Report

Es gibt kein `.github/`. `docs/TEST_PLAN.md` sagt „No GitHub Actions“. Odoo.sh führt auf Development-Branches die Python-Suite inklusive Tours aus, Test-Tag `/bloopark_okr`. HOOT und `odoo_sh_verify.mjs` laufen dort nicht. Staging und Production testen beim Build nicht. Details: `docs/ODOO_SH_TESTS.md`.

Agent-Schnitt in `Agents.md` und `agents/lead/agents/`:

| Rolle | Frontend-Anteil |
|---|---|
| `tester` | Schreibt HOOT und Tours, führt sie aus, wenn `ODOO_BIN` gesetzt ist. „Not run“ gilt nicht als bestanden |
| `devops` | Nach Push nur `node scripts/odoo_sh_verify.mjs --wait --commit HEAD`. Gesund erst bei `RESULT: PASS` |
| `coder` | Setzt `data-testid` im XML mit. Kein eigener Testlauf |

Der Smoke hat zwei Phasen. Phase 1 wartet auf der Branch-Seite (`--wait`, Poll aus `.odoo-sh.json`, Default 30 s, Timeout 45 min) auf `Test: Success`. Bei diesem Label überspringt er den LOGS-Tab. Phase 2 öffnet die Instanz, sammelt `console` und `pageerror`, prüft die Test-IDs, Menüs, Check-in-Popover, Dashboards und Scroll-Zustand. Fehler in der Konsole lassen `result.ok` auf false. Ausgabe ist Text mit `RESULT: PASS` oder `RESULT: FAIL`, optional `--json`. Es werden keine Screenshots, Videos oder Trace-Dateien geschrieben.

## Was sich bewährt hat

- Stabile `data-testid` in der Komponente, von Tour und Smoke gemeinsam gelesen. Kein Text-Selektor für Struktur, Text nur für den konkreten Datensatz (`:contains("OKR Tour Draft Objective")`).
- Tour-Daten in der Testmethode anlegen. Dieselbe Datenbank kann die Tour zweimal laufen lassen, und ein Build ohne Demo bleibt grün.
- Reine Logik in HOOT, Klickstrecken in Tours, der laufende Server nur im Smoke. Der Smoke startet nichts und kennt kein Passwort.
- Ein klares Ende: `RESULT: PASS` verlangt Build, leere Konsole, geöffnete App, Test-IDs, Features und Dashboards. `Test: Success` allein reicht nicht.
- Ein Verhalten pro Test. Der Tester ändert eine bestehende Assertion nur, wenn der Auftrag genau dieses Verhalten ändert.
