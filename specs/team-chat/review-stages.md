# Review-Stufen

Version: 0.2
Datum: 2026-10-20
Status: Entwurf
Owner: Jens Holze

Quellen: Auftrag Jens 2026-10-20 (Lead setzt den Modus, Prototyping nur Basisprüfungen, Feinschliff eigene Stufe, Erweiterungen nie Prototyping). Bestehende Regeln: Fork-Workflow `docs/fork-workflow.md`, Lead `agents/lead/config.yaml`, UX-Checkliste `docs/ux/chat-review-checklist.md`, Tasks `specs/omnigent-chat/07-tasks.md` v0.5 auf `epic/team-chat`. NFR-032 und die Tickets T-007b, T-132, T-133 stehen in den offenen PRs #51 und #53, nicht auf diesem Branch. [ANNAHME RS-A-005] [ANNAHME RS-A-008]

Die Kurzregel, die die Lead am Start liest, ist `review-mode.md`. Dieses Dokument begründet sie.

## Changelog

| Version | Datum | Änderung |
|---|---|---|
| 0.2 | 2026-10-20 | Tickets immer auf Englisch (RS-FR-013). Titel, Body und ACs. IDs, Modus-Werte, Links und wörtliche UI-Strings bleiben. Die Spec selbst bleibt Deutsch. |
| 0.1 | 2026-10-20 | Erste Fassung. Stufen, Ableitung, Umbau-Vorschlag, Simulation CLI-GUI und Team-Chat, RACI, Fragen an Jens. |

## 1. Wozu

Jens will nicht bei jedem Ticket den Modus von Hand setzen. Die Lead-Session liest die Spec und das Feld `Review-Modus` und legt die Tiefe fest, bevor der Coder startet.

Zwei Stufen:

- **Prototyping.** Die Funktion wird gebaut. Prüfung: Funktionalität (ACs und Tests grün) und Code (CI, Lint, Code-Review DeepSeek, danach Opus). Kein UX-Review.
- **Feinschliff (Polish).** Eigene Stufe. Sie beginnt, wenn die Basis-ACs grün sind und Prototyping aus ist. Zusätzlich UX-Review, NFR-032 (1280×800 und 390×844), Kontrast/NFR-009, Motion nur wo das Ticket Motion einführt.

Eine Erweiterung einer Funktion, deren Basis-ACs schon grün sind, läuft nie im Prototyping. Sie nimmt vom ersten Commit das volle Gate.

Beispiel von Jens: CLI-GUI (Tests, Lints und CI in einer Oberfläche) ist Prototyping, solange die Funktion entsteht. Sobald sie funktioniert und nur noch hübsch werden soll, ist es Feinschliff. Der Team-Chat ist schon zum Teil fertig. Nachzüge daran sind keine Prototypen.

## 2. Geltungsbereich

Fork `jholze/omnigent`. Nichts geht nach upstream. Task-PRs des Team-Chats zielen auf `epic/team-chat`. Nur Jens mergt `epic` nach `main`, macht Prod-Deploys und gibt Migrationen, Security und Auth frei. [ANNAHME RS-A-008]

Diese Spec ändert keine fachliche Anforderung in `specs/omnigent-chat/` außer der Ticket-Vorlage in `07-tasks.md` (Feld `Review-Modus`). Der Umbau in Abschnitt 6 ist ein Vorschlag.

## 3. Anforderungen

| ID | Anforderung | Status |
|---|---|---|
| RS-FR-001 | When ein Ticket einen Coder bekommen soll, the Lead-Session shall den Review-Modus festlegen, bevor sie den Coder beauftragt. Jens setzt den Modus nicht pro Ticket. | new |
| RS-FR-002 | While der Modus `prototyping` ist, the Lead shall nur prüfen: ACs grün, die genannten Tests in diesem Lauf wirklich ausgeführt, CI grün, Lint grün, Code-Review DeepSeek danach Opus PASS, kein Blocker. Kein UX-Review. | new |
| RS-FR-003 | When die Basis-ACs grün sind und Prototyping ausgeschaltet ist, the Lead shall die Stufe `polish` starten. UI-Tickets prüfen dann zusätzlich UX-Review, NFR-032 und Kontrast/NFR-009. | new |
| RS-FR-004 | If das Ticket eine Erweiterung einer Funktion mit grünen Basis-ACs ist oder ein Gate-Ticket, the Lead shall `polish` verwenden, auch wenn das Feld `prototyping` sagt. Sie korrigiert das Feld. | new |
| RS-FR-005 | When die Abschaltkriterien in Abschnitt 5 erfüllt sind, the Lead shall Prototyping ausschalten und das Feld auf `polish` stellen, bevor Feinschliff-Arbeit beginnt. | new |
| RS-FR-006 | The Stufen shall das bestehende Merge-Gate nicht ersetzen. Prototyping erlaubt keinen Merge nach `main`, keine Migration, keine Security-, Auth- oder Prod-Änderung. | new |
| RS-FR-007 | The Beziehung zu NFR-032 shall so sein, dass die Viewports 1280×800 und 390×844 spätestens im Feinschliff grün sind, bevor der Epic nach `main` geht. Ob Prototyping sie im selben Ticket aufschieben darf, ist [OFFEN RS-Q-001]. | new |
| RS-FR-008 | While der Modus `prototyping` ist, the Lead shall NFR-009 (Kontrast, Tastatur, axe) nicht als Merge-Kriterium des Prototyps behandeln. Im Feinschliff einer UI-Fläche ist NFR-009 Pflicht. | new |
| RS-FR-009 | The Entscheidungen shall der RACI in Abschnitt 10 folgen. Die Lead mergt nicht. | new |
| RS-FR-010 | If ein Stop aus Abschnitt 11 greift, the Lead shall anhalten und nur den dort genannten Entscheider fragen. | new |
| RS-FR-011 | When eine Review denselben Fehlertyp zum zweiten Mal zeigt, the Lead shall eine Zeile an der Ticket-Vorlage ergänzen, kein neues System. | new |
| RS-FR-012 | If das Feld fehlt, the Lead shall den Modus aus der Tabelle in `review-mode.md` nehmen, sonst nach der Ableitregel in Abschnitt 4, und das Feld eintragen, bevor sie coden lässt. | new |
| RS-FR-013 | When ein GitHub-Issue für diesen Prozess angelegt oder fortgeschrieben wird, the title, the body and the acceptance criteria shall be English. Stabile IDs, die Werte `prototyping` und `polish`, Links und wörtliche Produkt-Strings bleiben unverändert. Die Spec-Dateien bleiben Deutsch. | new |

### Abnahmekriterien

**RS-AC-001 (RS-FR-001, RS-FR-004)**

```gherkin
Given das Ticket T-007b trägt Review-Modus prototyping
When die Lead-Session den Auftrag liest
Then setzt sie den Lauf auf polish
And sie schreibt die Korrektur in den PR
And sie beauftragt keinen Coder im Prototyping
```

**RS-AC-002 (RS-FR-012)**

```gherkin
Given das Feld fehlt und das Ticket steht in review-mode.md
When die Lead `scripts/fork/review_mode.py` mit der Ticket-ID ausführt
Then ist der gedruckte Modus der Wert aus der Tabelle
```

**RS-AC-003 (RS-FR-002)**

```gherkin
Given der Modus ist prototyping
When die Lead die Stufe abschließt
Then sind ACs, die genannten Tests, CI, Lint, DeepSeek und Opus PASS belegt
And es gibt keine UX-Review-Notiz und keinen NFR-032-Lauf als Pflicht
```

**RS-AC-004 (RS-FR-003, RS-FR-007)**

```gherkin
Given der Modus ist polish und das Ticket hat UI
When die Stufe exit erfüllt
Then liegt eine UX-Review-Notiz gegen docs/ux/chat-review-checklist.md vor
And die AC ist bei 1280×800 und bei 390×844 bedienbar, ohne horizontalen Scroll
And der Kontrast der neuen Texte erfüllt NFR-009
```

**RS-AC-005 (RS-FR-005)**

```gherkin
Given eine neue Funktion läuft im Prototyping und die Basis-ACs sind grün
And DeepSeek und danach Opus haben PASS gesagt und kein Blocker ist offen
When die Lead die Stufe schließt
Then stellt sie das Feld auf polish, bevor jemand Lesbarkeit, Viewport oder Bewegung ändert
```

**RS-AC-006 (RS-FR-006)**

```gherkin
Given der Diff enthält eine Migration, eine Security- oder Auth-Änderung oder einen Prod-Schritt
When die Lead das in irgendeiner Stufe sieht
Then stoppt sie und Jens entscheidet
And der Modus ändert daran nichts
```

**RS-AC-007 (RS-FR-007)**

```gherkin
Given ein UI-Ticket hat NFR-032 im Prototyping aufgeschoben
When der Epic nach main soll
Then ist die Polish-Stufe dieser Fläche grün, einschließlich 1280×800 und 390×844
```

**RS-AC-008 (RS-FR-013)**

```gherkin
Given a new GitHub issue for a team-chat ticket
When the lead or the requirements writer files it
Then the title, the body and the acceptance criteria are English
And the stable ID, the review mode and the links are unchanged
And a quoted product string such as "Direktnachricht" stays in the product language
```

## 4. Stufen, Eintritt, Ausgang

### 4.1 Prototyping

**Eintritt.** Alle Punkte gelten:

- Die Basis-ACs dieser Funktion sind noch nicht grün. Die Funktion wird gebaut.
- Es ist keine Erweiterung einer schon fertigen Funktion. [ANNAHME RS-A-010] Fertig heißt: die Basis-ACs sind nach `main` oder `epic/team-chat` gemergt.
- Es ist kein Gate-Ticket (Tester-Suite, UX-Pass, Release-Runbook).
- Das Feld ist `prototyping`, oder es fehlt und die Ableitung ergibt `prototyping`.

**Prüfungen.** RS-FR-002. Der Tester führt die genannten Kommandos aus. „Nicht gelaufen“ ist kein Pass. DeepSeek zuerst, Opus hat das letzte Wort. Ein BLOCK stoppt. Nits nicht. Reihenfolge und Vendor-Matrix bleiben die aus `agents/lead/config.yaml` (DeepSeek reviewt keinen DeepSeek-Diff; ein großes Ticket kann `gpt_review` dazwischen haben). [ANNAHME RS-A-007]

**Nicht in dieser Stufe.** UX-Review, NFR-032, Kontrast/NFR-009, Motion-Review, Feinschliff der Checkliste (Hierarchie, Animation, 320-px-Reflow als Blocker).

**Ausgang.** Abschnitt 5. Danach ist die Stufe zu. Weiterarbeit an derselben Fläche ist Polish.

### 4.2 Feinschliff (Polish)

**Eintritt.** Eines gilt:

- Die Basis-ACs sind grün und Prototyping ist aus (RS-FR-005), oder
- das Ticket erweitert eine fertige Funktion, oder
- das Ticket ist selbst der Feinschliff oder ein Gate (Motion, a11y, UX-Pass, Release).

**Prüfungen.** Alles aus Prototyping, plus bei UI:

- UX-Review-Notiz gegen `docs/ux/chat-review-checklist.md`, ohne offenen Blocker und ohne offenes High.
- NFR-032: 1280×800 und 390×844. Die AC bleibt bedienbar, kein horizontaler Scroll.
- Kontrast NFR-009: Text mindestens 4,5:1. Die übrigen NFR-009-Punkte (Tastatur, `aria-live`, axe serious/critical) gehören zum Polish-Exit einer UI-Fläche, nicht zum Prototyp.

Ohne UI sind UX, NFR-032 und Kontrast n/a. Das Code-Gate bleibt. [ANNAHME RS-A-004] T-133 und T-123 sind dieser Fall.

Motion (NFR-006…008, Checkliste MOT-*) nur, wenn das Ticket Motion einführt. T-117 ist die Motion-Stufe. Ein Prototyp, der keine Animation anfasst, darf keine einführen.

**Ausgang.** Die Prüfungen sind grün, Opus PASS, kein Blocker. Erst dann darf die Fläche in den Epic-Release nach `main`.

## 5. Prototyping ausschalten

Alle Punkte:

1. Die Basis-ACs des Tickets sind als Tests grün.
2. Der Tester hat die genannten Kommandos in diesem Lauf ausgeführt. Drei grüne Läufe bleiben Pflicht, wo das Ticket sie schon verlangt (T-009, NFR-024).
3. DeepSeek-Review liegt vor, danach Opus PASS. Ein 402 von DeepSeek ist kein PASS. [OFFEN RS-Q-002]
4. Kein Blocker.

Die Lead stellt das Feld auf `polish` oder eröffnet das Feinschliff-Ticket und setzt das alte auf erledigt für die Funktion. Sie informiert Jens in der kurzen Statuszeile. Sie fragt ihn nicht, außer RS-Q-001 ist noch unbeantwortet und der nächste Diff ist UI. Bis zur Antwort gilt der Default aus RS-Q-001.

Prototyping bleibt aus für jede spätere Erweiterung dieser Funktion (RS-FR-004).

## 6. Wie die Lead den Modus ableitet

Reihenfolge:

1. Regel RS-FR-004 anwenden. Erweiterung oder Gate gewinnt gegen ein falsches Feld.
2. Feld `Review-Modus: prototyping | polish` im Ticketkopf und, auf GitHub, das Label `stage:prototyping` oder `stage:polish`. Feld und Label sagen dasselbe. Widersprechen sie sich, gewinnt die strengere Stufe (`polish`).
3. Fehlt beides, die Zeile in `review-mode.md`.
4. Fehlt das Ticket dort: neue Funktion ohne grüne Basis-ACs wird `prototyping`. Alles andere wird `polish`. Die Lead trägt Feld und Label ein, bevor sie den Coder beauftragt.

Werkzeug: `python3 scripts/fork/review_mode.py <ticket-id>`. Ein unbekannter Name endet mit Exit 2 und erfindet keinen Modus.

[ANNAHME RS-A-003] Omnigent Boss ist die Lead aus `agents/lead/config.yaml` (PM HUB). Hat der Epic eine eigene Session, setzt diese den Modus. Boss prüft das Ergebnis und mergt nicht.

## 7. Verhältnis zu den bestehenden Regeln

| Regel | Was bleibt | Was die Stufe ändert |
|---|---|---|
| Merge-Gate | CI grün, die relevanten Tests wirklich gelaufen, DeepSeek dann Opus PASS, ACs geprüft, kein Blocker, keine Migration/Security/Prod ohne Jens | Prototyping lässt UX und NFR-032 weg. Polish holt sie nach. Beides ersetzt das Gate nicht. |
| Epic-Branch | Task-PRs nach `epic/team-chat`. Nur Jens mergt Epic nach `main`. | Unverändert. Prototyping ist kein Freibrief, am Epic vorbei nach `main` zu mergen. |
| NFR-032 | Viewports 1280×800 und 390×844 für Chat-UI. Wortlaut in PR #53, noch nicht auf dem Branch. [ANNAHME RS-A-005] | Im Prototyping nicht Pflicht. Vor Epic-Release Pflicht im Polish. Konflikt mit „im selben Ticket“: [OFFEN RS-Q-001]. |
| NFR-009 | Kontrast ≥ 4,5:1, Tastatur, Live-Region, axe | Polish einer UI-Fläche. Nicht Prototyping (RS-FR-008). |
| UX-Checkliste | `docs/ux/chat-review-checklist.md`, NFR-027 | Pflicht im Polish und bei jeder Erweiterung einer fertigen UI. Nicht im Prototyping. |
| Mobile im selben Ticket | PR #51, Jens 2026-10-20: jede neue oder geänderte UI prüft beide Viewports im selben Ticket. [ANNAHME RS-A-008] | Gilt für Polish und für jede Erweiterung. Für echtes Prototyping widerspricht es RS-FR-002. [OFFEN RS-Q-001] |
| Sonderfälle Jens | Prod, Migration, Security, Auth, Epic nach `main` | In beiden Stufen Stop (RS-AC-006). |

Breiten 1440, 768, 375 und 320 aus der Checkliste und aus PR #51 bleiben im Polish optional. Blocker sind die zwei Gate-Größen. [OFFEN RS-Q-007]

## 8. Umbau, nur Vorschlag

Diese Datei ändert die anderen Specs nicht, außer der Vorlage in `07-tasks.md`. Die Zeilen dort sind der Kopf für neue Tickets. Die bestehenden Task-Körper bleiben, damit PR #53 (Demo-Feedback, v0.6) nicht kollidiert.

### 8.1 Basis gegen Feinschliff im Team-Chat

Gemergt, damit fertig im Sinn von RS-A-010: T-001…T-008 auf `main` oder `epic/team-chat` (Store, REST, Gerüst, Orchestrator, Shell, Channel/DM/Thread, Agent-Thread). T-000 ist über die UX-Checkliste (PR #33) und die frühen Chat-PRs abgedeckt. Dafür gibt es keine neuen Issues.

Offen und **Polish**, weil die Fläche schon steht oder das Ticket ein Gate ist:

- T-007b Demo-Feedback an Sidebar, Thread, DMs, Lesbarkeit. Ein Ticket, nicht nach T-007c gesplittet (PR #53). PR #52 gehört hierher. Nicht Prototyping.
- T-009 R0-Playwright. Die Phase in `07-tasks.md` heißt schon „Polish / Gate“.
- T-110…T-116, T-116b, T-117, T-118, T-124: UI auf der gebauten Shell.
- T-111 hängt am Lesecursor. Der Server T-101 darf Prototyping sein. Die Badge-UI ist Polish.
- T-114 ebenso gegenüber T-104, T-116 gegenüber T-105.
- T-120, T-121, T-122, T-123: Gate und Release.
- T-132 Channel-Verwaltung. Die API zum Anlegen und join/leave ist mit T-003 fertig. Die Oberfläche ist die Erweiterung. Berechtigungen bleiben [OFFEN] in der Chat-Spec (Q-16, Q-29…Q-32), das entscheidet diese Spec nicht.
- T-133 Demo-Seed. PR #49. Kein UI-Produkt, volles Code-Gate, UX n/a.
- T-102 Session-Chat als Channel. Session-Chat existiert (PR #4). Adaption, volles Gate, UI liegt bei T-113.
- T-005-AC-033c. Host-Start nicht `internal`. Nachzug an gemergtem T-005 (PR #53). Eigenes Issue, T-005 nicht wieder aufmachen. [OFFEN RS-Q-005]

Offen und **Prototyping**, weil die Funktion noch nicht gebaut ist und kein UI der fertigen Shell ist: T-101, T-103, T-104, T-105, T-106, T-107, T-108, T-109, T-119, T-125, T-126, T-127, T-128, T-129, T-130, T-131. Sobald ihre Basis-ACs grün sind, schaltet die Lead auf Polish, bevor eine zugehörige UI (T-111, T-114, T-116) mehr als die Funktion zeigt.

### 8.2 Feld in der Vorlage

Erledigt in diesem PR, als Vorlage, nicht in jedem alten Task-Körper:

- `specs/omnigent-chat/07-tasks.md`, Abschnitt Ticket-Vorlage: `Review-Modus: prototyping | polish`
- `.github/ISSUE_TEMPLATE/team_chat_task.yml`: dasselbe Feld, plus Marker `exists | adapt | new` und die ACs

Vorschlag für später, nicht in diesem PR: das Feld in die bestehenden Köpfe schreiben, wenn PR #53 gemergt ist. Vorher erzeugen zwei Branches an derselben Datei nur Konflikte.

### 8.3 Wohin das UX-Review wandert

Heute: `07-tasks.md` verlangt die UX-Notiz an jedem UI-PR, das PR-Template hat die Checkbox für `web/src/chat`.

Vorschlag:

- Prototyping: Checkbox bleibt leer und ist kein Blocker. Die PR-Beschreibung sagt `Review-Modus: prototyping` und nennt die Basis-ACs.
- Polish und jede Erweiterung einer fertigen UI: Checkbox Pflicht, wie heute.
- Das PR-Template selbst fasst dieser PR nicht an.

NFR-032 hängt in PR #53 nur an T-007b. Vorschlag: jede Polish-UI-Zeile in der Coverage nennt NFR-032. Die zwei Viewports sind eine Prüfung, kein eigenes Mobile-Produkt (so steht es in PR #53, A-33).

### 8.4 Was bewusst nicht umgebaut wird

- Keine neue Orchestrator-App. Ein Feld, eine Tabelle, ein kurzes Skript.
- Issue #12 (Grok-Bot-Prototyp) bleibt offen bis Q-15. T-105 verweist darauf und dupliziert es nicht.
- Issues #3, #7, #8, #10 bleiben, bis Jens sie schließt. Die neuen Sub-Issues verweisen nur.
- Die alte Zeile in der Lead, PRs gingen nach `main`, lässt PR #51. Dieser PR hängt nur den Hinweis auf `review-mode.md` an den Prompt.

## 9. Simulation CLI-GUI

[ANNAHME RS-A-001] Im Repo gibt es kein Epic „CLI-GUI“. Die Simulation baut keine Software. Sie spielt nur die Stufen durch. Die Funktion: eine Ansicht zeigt den letzten Testlauf und den Lint eines Branches.

Tickets der Simulation, keine echten IDs: SIM-CLI-1 (Funktion), SIM-CLI-2 (Feinschliff), SIM-CLI-3 (spätere Erweiterung).

| Schritt | Stufe | Prüfungen | Entscheidung | Wer | Jens |
|---|---|---|---|---|---|
| 0. Spec | keine | Lead liest: keine grünen Basis-ACs, keine fertige Fläche | Modus von SIM-CLI-1 = prototyping, Feld eintragen | Lead der Epic-Session. Boss sieht den Plan. | Einmal die ACs, das ist der bestehende Stop. Nicht den Modus. |
| 1. SIM-CLI-1 bauen | prototyping | AC: ein roter Pytest erscheint mit Datei und Zahl. Tests wirklich gelaufen. CI, Lint. DeepSeek, dann Opus PASS. | Exit nur bei Abschnitt 5 | Lead | Nur bei Migration, Security, Auth, Prod. Hier keines. |
| 1b. Was nicht läuft | prototyping | Kein UX-Review, kein 390×844, kein Kontrast, keine Animation | Lead weist einen UX-Auftrag zurück | Lead | Nein |
| 2. Abschalten | Wechsel | Basis-ACs grün, Reviews PASS, kein Blocker | Feld von SIM-CLI-1 auf polish, SIM-CLI-2 aufmachen | Lead | Informiert. Gefragt nur, solange RS-Q-001 offen ist und SIM-CLI-2 UI ist. Default: nicht fragen, Viewports liegen in SIM-CLI-2, vor dem Epic-Release. |
| 3. SIM-CLI-2 | polish | Alles aus Schritt 1, plus UX-Checkliste, 1280×800 und 390×844, Kontrast | Exit wenn RS-AC-004 grün ist | Lead. UX-Reviewer schreibt die Notiz. Opus das Code-PASS. | Epic nach `main`, sonst nichts |
| 4. SIM-CLI-3 Filter | polish | Voller Gate ab dem ersten Commit. `review_mode.py SIM-CLI-3 --field prototyping` würde korrigieren, sobald die Zeile Klasse `erweiterung` hat. | Lead korrigiert ein falsches Feld | Lead | Nein, außer die Sonderfälle |

Der Modus kommt aus der Spec. Jens steigt in Schritt 0 für die ACs ein, in Schritt 3 für den Epic-Release, und dazwischen nur bei den Sonderfällen oder bei RS-Q-001, solange es offen ist.

## 10. Simulation Team-Chat

Ausgang: T-006, T-007, T-008 sind auf `epic/team-chat` gemergt. Die Demo am 20.10. lief auf dieser Fläche. Erweiterungen sind Polish. [ANNAHME RS-A-002]

| Schritt | Stufe | Prüfungen | Entscheidung | Wer | Jens |
|---|---|---|---|---|---|
| 1. T-007b | polish | Acht ACs aus PR #53, beide Viewports, UX-Notiz, DeepSeek dann Opus. PR #52 hat UX und 390×844 schon gezeigt und DeepSeek wegen HTTP 402 nicht laufen lassen. | 402 ist kein PASS. Retry, sonst Waiver. [OFFEN RS-Q-002] Der Modus bleibt polish. | Lead korrigiert, falls jemand Prototyping einträgt. | Waiver nur für den 402, nicht für den Modus. Q-34 (Wortlaut Idle) ist eine Produktfrage der Chat-Spec, kein Stufenentscheid. |
| 2. T-009 | polish / gate | R0-Suite, drei Läufe, NFR-009 soweit die Suite sie schon zitiert. NFR-032 an jeder UI-AC dieser Suite vor dem Epic-Release nachziehen. | Nicht Prototyping, die UI steht. | Lead | Nein |
| 3. T-101 | prototyping | Server-ACs des Lesecursors, pytest, CI, DeepSeek, Opus. Kein UX. | Exit nach Abschnitt 5, dann Feld auf polish, bevor T-111 mehr als den Zähler zeigt. | Lead | Nein |
| 4. T-111 | polish | Badge an der fertigen Sidebar, NFR-032, Kontrast. Wartet auf grüne Basis von T-101, ist aber selbst nie Prototyping. | Lead | Lead | Nein |
| 5. T-132 | polish | UI auf der fertigen Channel-API. UX, sobald die Oberfläche existiert. | Start blockiert fachlich durch Q-16 und Q-29, nicht durch die Stufe. | Lead setzt polish. | Die offenen Berechtigungen, nicht den Modus. |
| 6. T-133 | polish, UI n/a | AC-133a/b, zweiter Lauf ohne Doppelte. PR #49. Kein Kontrast, kein Viewport als Produktprüfung. | Voller Code-Gate, weil der Epic schon steht. | Lead | Nein |
| 7. T-105…T-107 | prototyping | Neue Serverfunktion. Issue #12 nicht zweit anlegen. | Polish erst, wenn die Basis-ACs grün sind. T-116 ist von Anfang an polish. | Lead | Security- und Auth-Diffs inside dieser Tickets: Stop, Jens. Die Stufe hebt das nicht auf. |
| 8. T-005-AC-033c | polish | Ein AC, Server, kein UX. | Eigenes Issue, T-005 bleibt zu. | Lead | Nur wenn der Fix Auth oder eine Migration anfasst. |
| 9. Epic nach main | polish-Exit jeder UI-Fläche | NFR-032 auch dort, wo ein Prototyp sie aufgeschoben hat (RS-AC-007). Kein Blocker. | Merge | Niemand aus der Lead | Jens. Prod, Migration, Security, Auth ebenfalls nur Jens. |

## 11. RACI

R = macht es, A = verantwortlich, C = wird gefragt, I = wird informiert. Eine Zeile hat ein A.

| Aktivität | Lead (Epic-Session) | Omnigent Boss | Jens | DeepSeek | Opus | UX |
|---|---|---|---|---|---|---|
| Modus aus Feld und Tabelle setzen | R/A | I. A nur, wenn es keine Epic-Session gibt. | I | | | |
| Falsches Prototyping an einer Erweiterung korrigieren | R/A | I | I | | | |
| Prototyping-Prüfungen | A | I | I | R Review und Tester | R PASS | |
| Prototyping ausschalten | R/A | I | I | | | |
| UX, NFR-032, Kontrast | A | I | I | | C am Code | R Notiz |
| Task-PR nach `epic/team-chat` mergen | I | I | A [ANNAHME RS-A-009] | | | |
| Epic nach `main`, Prod, Migration, Security, Auth | I | I | A | | | |
| Waiver DeepSeek 402 | C | I | A | | | |
| RS-Q-001 beantworten | C | I | A | | | |

[ANNAHME RS-A-009] Wer eine grüne Task-PR in den Epic mergt, ist heute Jens, weil die Lead nicht mergt (`agents/lead/config.yaml`, „You do not merge“). Diese Spec ändert das nicht.

## 12. Eskalation

| Lage | Wohin | Nicht |
|---|---|---|
| Opus BLOCK oder Test rot | Dieselbe Lead, derselbe Coder, derselbe Branch, dann Tester und Review erneut | Nicht Jens, nicht die Stufe wechseln |
| DeepSeek HTTP 402 | Ein Retry. Bleibt es 402, Jens nur für den Waiver dieses Laufs. Opus bleibt Pflicht. | Nicht still mit Opus allein schließen. [OFFEN RS-Q-002] |
| Feld sagt prototyping, Klasse ist Erweiterung oder Gate | Lead korrigiert auf polish | Nicht Jens fragen |
| Ticket fehlt in der Tabelle | Lead leitet ab, trägt das Feld ein | Nicht raten und loscodieren |
| Diff ist Migration, Security, Auth oder Prod | Stop, Jens | Der Modus ändert nichts |
| NFR-032 gegen Prototyping, RS-Q-001 offen | Default aus RS-Q-001, im PR als offen markieren | Nicht bei jedem Ticket neu fragen |
| Produktfrage der Chat-Spec (Q-16, Q-29, Q-34, …) | Bleibt dort. Die Stufe startet nicht, wenn die AC die offene Regel braucht. | Diese Spec beantwortet sie nicht |

## 13. Was in die Ticket-Vorlage wandert

Die Vorlage trägt ab diesem PR:

- `Review-Modus: prototyping | polish`
- Marker `exists | adapt | new`
- ACs in EARS oder Given/When/Then
- einen Satz, dass eine Erweiterung immer `polish` ist
- die Regel „Tickets in English“ (RS-FR-013): Titel, Body und ACs auf Englisch; IDs, Modus-Werte, Links und wörtliche UI-Strings bleiben

RS-FR-011: wiederholt sich ein Review-Befund, kommt eine Zeile an diese Vorlage, kein zweites Werkzeug. Der erste Kandidat, noch nicht eingetragen, weil RS-Q-002 offen ist: „DeepSeek 402 ist kein PASS.“

Die UX-Zeile der Vorlage gilt für Polish und für Erweiterungen. Im Prototyping steht dort „UX: n/a, Stufe prototyping“.

## 14. Open-Questions-Log

Jede Annahme und jeder offene Punkt aus diesem Dokument.

| ID | Art | Punkt | Empfohlener Default |
|---|---|---|---|
| RS-A-001 | [ANNAHME] | CLI-GUI existiert im Repo nicht. Abschnitt 9 ist nur der Ablauf. | So lassen, bis jemand das Epic wirklich aufmacht. |
| RS-A-002 | [ANNAHME] | T-001…T-008 sind fertig, weil sie gemergt sind. Ausnahme AC-033c. | So lassen. |
| RS-A-003 | [ANNAHME] | Omnigent Boss ist die Lead aus `agents/lead/config.yaml`. | So lassen. |
| RS-A-004 | [ANNAHME] | Polish ohne UI: UX, NFR-032 und Kontrast sind n/a. | So lassen. T-133, T-123, T-005-AC-033c. |
| RS-A-005 | [ANNAHME] | NFR-032 heißt 1280×800 und 390×844 und steht in PR #53, nicht auf dem Branch. | Den Wortlaut aus PR #53 übernehmen, wenn es mergt. |
| RS-A-006 | [ANNAHME] | `07-tasks.md` sagte, Issues kommen erst nach Jens' Ja. Dieser Auftrag hebt das für das Epic Team-Chat auf. | Nur dieses Epic. Weitere Epics wieder fragen. |
| RS-A-007 | [ANNAHME] | DeepSeek-dann-Opus und die Vendor-Matrix gelten in beiden Stufen. | So lassen. |
| RS-A-008 | [ANNAHME] | Epic-Branch und „Mobile im selben Ticket“ stammen aus PR #51 und gelten als Jens' Regel, auch ungemergt. | PR #51 mergen, dann den Satz hier streichen. |
| RS-A-009 | [ANNAHME] | Task-PRs in den Epic mergt Jens, weil die Lead nicht mergt. | So lassen. |
| RS-A-010 | [ANNAHME] | Fertig heißt gemergt nach `main` oder `epic/team-chat`. | So lassen. Ein offener PR ist nicht fertig. |
| RS-Q-001 | [OFFEN] | NFR-032 und „Mobile im selben Ticket“ gegen Prototyping, das die Viewports auf den Feinschliff verschiebt. | Prototyping darf die Viewports nur aufschieben, solange die Funktion neu ist und noch keine fertige Fläche erweitert. Polish holt sie vor dem Epic-Release nach. Erweiterungen prüfen sie im selben Ticket. |
| RS-Q-002 | [OFFEN] | PR #52 lief ohne DeepSeek (HTTP 402) und mit Opus PASS. | 402 ist kein PASS. Ein Retry. Danach darf nur Jens diesen einen Lauf waiven. Opus bleibt. Der Modus bleibt. |
| RS-Q-003 | [OFFEN] | Schaltet die Lead Prototyping allein aus? | Ja. Jens wird informiert. Er bestätigt ACs einmal, Epic nach `main`, und die Sonderfälle. Nicht den Modus. |
| RS-Q-004 | [OFFEN] | Ist jede restliche Team-Chat-UI Polish, auch eine noch nie gebaute Ansicht wie T-112? | Ja. Die Shell ist in der Demo. Neue Serverfunktion ohne UI darf Prototyping bleiben (Tabelle). |
| RS-Q-005 | [OFFEN] | AC-033c an gemergtem T-005: Ticket wieder auf oder Nachzug? | Nachzug `T-005-AC-033c`, Polish, T-005 bleibt zu. Dieselbe Regel für später nachgetragene ACs. |
| RS-Q-007 | [OFFEN] | Checkliste 1440/768/375/320 gegen Gate 1280×800 und 390×844. | Gate sind die zwei Größen und blocken. Die anderen sind im Polish optional und blocken nicht. |

RS-Q-006 gibt es nicht, damit die Nummern der fünf Fragen an Jens nicht springen. Die UX-Checkbox ist in Abschnitt 8.3 vorgeschlagen und folgt RS-Q-004.

## 15. Simulationsergebnis

Die Lead kann den Modus ohne Jens setzen. CLI-GUI läuft als Prototyping, bis die Basis-ACs grün sind, und wechselt dann in den Feinschliff mit UX, 1280×800, 390×844 und Kontrast. Eine spätere Filter-Erweiterung startet im vollen Gate. Der Team-Chat ist über T-006…T-008 fertig, deshalb sind T-007b, T-009, T-132, T-133 und die übrige Chat-UI Polish. Neu und ohne UI, vor allem der MCP-Bot-Pfad, darf Prototyping bleiben, bis die Basis-ACs grün sind. Jens kommt für die einmalige AC-Abnahme, für Epic nach `main`, für Prod, Migration, Security und Auth, und für den DeepSeek-Waiver. Der Konflikt NFR-032 gegen aufgeschobenes Mobile bleibt offen, mit dem Default aus RS-Q-001.

## 16. Fragen an Jens

Höchste Priorität zuerst. Je ein empfohlener Default.

1. **RS-Q-001.** Darf Prototyping die Viewports 1280×800 und 390×844 auf den Feinschliff verschieben, obwohl PR #51 Mobile im selben Ticket verlangt? Default: nur bei einer neuen Funktion, die noch keine fertige Fläche erweitert. Vor Epic nach `main` ist Polish inklusive NFR-032 grün. Erweiterungen prüfen die Viewports im selben Ticket.
2. **RS-Q-002.** Ist ein DeepSeek-Lauf mit HTTP 402 ein durchgelassenes Review, wie in PR #52? Default: nein. Ein Retry, danach nur ein schriftlicher Waiver von Jens für diesen Lauf. Opus PASS bleibt. Der Modus ändert sich nicht.
3. **RS-Q-003.** Schaltet die Lead Prototyping allein aus, wenn Abschnitt 5 grün ist? Default: ja. Jens wird informiert und nicht gefragt. Er bestätigt weiter die ACs einmal und jeden Epic-Release nach `main`.
4. **RS-Q-004.** Ist die restliche Team-Chat-UI, einschließlich noch nicht gebauter Ansichten auf der Shell, durchgehend Polish? Default: ja, siehe Tabelle in `review-mode.md`. Prototyping nur für neue Serverfunktion ohne UI.
5. **RS-Q-005.** Bekommt AC-033c ein eigenes Issue, statt T-005 wieder zu öffnen? Default: ja, ID `T-005-AC-033c`, Stufe Polish.
