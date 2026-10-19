# UX-Review-Checkliste Team Chat (`docs/ux/chat-review-checklist.md`)

T-000 Teil B · NFR-027 · gilt für jede UI-PR unter `web/src/chat/**` und angrenzende
Chat-Oberflächen. Nur Doku: keine Plugin-Hooks, keine Plugin-Skripte außer dem kopierten
`docs/ux/scripts/contrast.py`.

Quellen (Details und Lizenzen: `docs/ux/NOTICES.md`): plugin87/ux-ui-agent-skills
(`taste/motion-choreography.md`, `components/icon-system.md`, `accessibility/wcag-checklist.md`,
`workflows/design-review.md`, `taste/design-taste.md`), Emil Kowalski (`review-animations`,
`animation-performance`, `break-ui`), Vercel Web Interface Guidelines (`command.md`),
OneRedOak design-review (Phasen, Viewports, Triage). Bindend sind die Spec-Werte
(`specs/omnigent-chat/`, ADR-0008, NFR-006…011, NFR-030) und die Tokens in `docs/ux/system.md`.

## So wird geprüft
- Jede Zeile bekommt einen Status: `ok`, `n/a`, `Befund <ID>` oder `nicht geprüft (<Grund>)`.
- Schwere: **B** = Blocker (stoppt den Merge), **H** = High (vor Merge beheben), **M** = Medium,
  **N** = Nit. Die Spalte nennt die Standard-Schwere; der Reviewer darf mit Begründung abweichen.
- Belege: Screenshots unter `/tmp/omnigent-chat-ux/<task-id>/` (nie committen), Code als `file:line`.
- Viewports: 1440×900, 768×1024, 375×812, Reflow 320. Zusätzlich `reducedMotion: "reduce"` und
  Dark Mode, falls die App ihn bietet.
- `MOT-*`-Zeilen sind `n/a`, solange `web/src/chat/motion.ts` nicht existiert (T-117).
  Ausnahme MOT-00: Tasks mit „keine Animationen“ dürfen keine Motion einführen.
- Eine Review ohne Screenshots für die im Diff berührten Oberflächen ist kein Pass.

## 0 Prozess
| ID | Prüfpunkt | Schwere |
|---|---|---|
| PRO-01 | Diff bleibt im Scope des Tasks (07-tasks.md „Scope“/„Out of scope“). | H |
| PRO-02 | Spec-Texte wörtlich (z. B. Status-Labels FR-044, „nicht gesendet“/„erneut senden“ FR-084). | H |
| PRO-03 | Neue Design-Entscheidungen stehen in `docs/ux/system.md` (Status `proposed`/`decided`). | M |
| PRO-04 | Offene Pläne in `docs/ux/design-plans/` für diesen Task sind erledigt oder begründet abgelehnt. | H |
| PRO-05 | Tests zur Änderung vorhanden und mit echten Zahlen gelaufen (Vitest; `tests/e2e_ui` bei Journeys; kein zweiter Playwright-Baum unter `web/e2e`). | H |

## 1 Interaktion & Ablauf (OneRedOak Phase 1, NFR-030)
| ID | Prüfpunkt | Schwere |
|---|---|---|
| INT-01 | Primäre Journey des Tasks funktioniert ohne Erklärung im echten Browser. | B |
| INT-02 | Jede Klick-Aktion zeigt ≤ 100 ms sichtbares Feedback (Pressed-State, Spinner oder optimistische Änderung). | H |
| INT-03 | Alle interaktiven Elemente haben default, hover, active, focus-visible, disabled. | H |
| INT-04 | Destruktive oder irreversible Aktionen: AlertDialog oder Undo-Fenster. | B |
| INT-05 | Fehler erscheinen dort, wo die Aktion passierte (z. B. an der Nachricht), nicht nur als Toast. | H |
| INT-06 | Composer: Kartenoptik und Sendetaste wie `ChatComposer.tsx`/`isComposerSendKey` (NFR-011); Paste nie blockiert; bleibt während Senden/Streaming bedienbar. | H |
| INT-07 | Kein Überraschungs-Scroll: Anker unten nur, wenn der Nutzer unten war, sonst Pill „Neue Nachrichten“ (FR-080). Neue Agent-Ausgabe stiehlt nie den Fokus. | H |
| INT-08 | URL spiegelt Channel/Thread (`/chat/:channelId/t/:threadRootId`, FR-001); Links unterstützen Cmd/Ctrl-Klick. | M |

## 2 Responsive (OneRedOak Phase 2, plugin87 Reflow)
| ID | Prüfpunkt | Schwere |
|---|---|---|
| RSP-01 | Screenshots 1440 / 768 / 375 ohne Überlappung oder abgeschnittene Aktionen. | H |
| RSP-02 | Reflow bei 320 px ohne horizontalen Scroll (WCAG 1.4.10). | B |
| RSP-03 | Touch-Ziele ≥ 24×24 px (WCAG 2.5.8), auf Touch 44 px empfohlen; keine überlappenden Trefferflächen. | H |
| RSP-04 | `h-dvh` statt `h-screen`; feste Leisten respektieren `env(safe-area-inset-*)`. | M |
| RSP-05 | 200 % Zoom: kein Inhaltsverlust (WCAG 1.4.4). | H |

## 3 Visueller Feinschliff (OneRedOak Phase 3, plugin87 design-taste)
| ID | Prüfpunkt | Schwere |
|---|---|---|
| VIS-01 | Ein klarer Fokuspunkt je Ansicht; Hierarchie über Größe, Gewicht, Kontrast, Raum (Squint-Test). | M |
| VIS-02 | Alle Werte aus Theme-Tokens bzw. `docs/ux/system.md`; kein Roh-Hex, keine Magic Numbers. | H |
| VIS-03 | Ein Akzent je Ansicht; Farbe kommuniziert Status/Aktion, nie Dekoration; keine Verläufe/Glow als Affordance (Ausnahme: bestehender Shimmer am aktiven Schritt). | M |
| VIS-04 | Eine Tiefenstrategie (Hairlines/Borders); Schatten nur für schwebende Ebenen. | M |
| VIS-05 | Anti-Defaults vermieden: Emoji als Icons, einseitig farbiger Rand an Alerts/Karten, identische Karten mit gleichem Grauschatten, ALL-CAPS-Eyebrows als Füller, `→` an jedem Button. | M |
| VIS-06 | `tabular-nums` für Zähler, Zeiten, Dauern; `text-balance`/`text-pretty`. | N |
| VIS-07 | Dark Mode (falls angeboten) gestaltet, nicht invertiert; Kontrast in beiden Modi. | M |

## 4 Icons (plugin87 icon-system, NFR-010)
| ID | Prüfpunkt | Schwere |
|---|---|---|
| ICO-01 | Nur `lucide-react`; keine Emoji, keine anderen Sets. | H |
| ICO-02 | Größen 16 px inline / 20 px Toolbar; eine Strichstärke je Oberfläche; `currentColor`. | M |
| ICO-03 | Icon-only-Controls haben `aria-label`; dekorative Icons `aria-hidden="true"`. | B |
| ICO-04 | Status nie nur über Icon oder Farbe (immer mit Text, z. B. Status-Chip FR-044). | H |
| ICO-05 | Icon-Zustände erben den Control-Zustand (hover/selected/disabled); Ladeicon mit `aria-busy`. | N |

## 5 Barrierefreiheit (plugin87 wcag-checklist P0, Vercel, NFR-009)
| ID | Prüfpunkt | Schwere |
|---|---|---|
| A11Y-01 | Gesamte R0-Journey per Tastatur, logische Reihenfolge, kein Keyboard-Trap (2.1.1, 2.1.2, 2.4.3). | B |
| A11Y-02 | Sichtbarer `focus-visible`-Ring überall; nie `outline-none` ohne Ersatz; Fokus nicht von Sticky-Leisten verdeckt (2.4.7, 2.4.11). | B |
| A11Y-03 | Dialoge: Fokus rein, gefangen, beim Schließen zurück zum Auslöser; Escape schließt. | B |
| A11Y-04 | Jedes Control mit zugänglichem Namen; sichtbares Label im Namen enthalten (4.1.2, 2.5.3). | B |
| A11Y-05 | Kontrast Text ≥ 4,5:1, UI/Icons/Fokusring ≥ 3:1 in allen Zuständen (`python3 docs/ux/scripts/contrast.py FG BG`). | B |
| A11Y-06 | Neue Nachrichten im offenen Channel über `aria-live="polite"`, gebündelt ≤ 1 Ansage / 2 s; Statuswechsel angesagt (4.1.3, NFR-009). | H |
| A11Y-07 | Semantik: native Elemente vor ARIA, Listen als `ul/ol`, Landmarks (`main`, `nav`), Überschriften ohne Sprung. | H |
| A11Y-08 | Aufklappbares mit `aria-expanded`/`aria-controls`; Laden mit `aria-busy` oder Statustext. | M |
| A11Y-09 | axe auf `/chat`-Ansichten: 0 Verstöße „serious“/„critical“ (NFR-009), sobald der Test existiert. | B |

## 6 Motion (plugin87 motion-choreography, Emil review-animations, NFR-006…008)
| ID | Prüfpunkt | Schwere |
|---|---|---|
| MOT-00 | Task verbietet Animationen → keine Motion im Diff. | H |
| MOT-01 | Jede Animation hat einen Zweck (Feedback, räumlicher Zusammenhang, Zustand, harte Wechsel abfedern). | M |
| MOT-02 | Keine Animation auf Tastatur-Aktionen oder 100+/Tag (Senden per Enter, Channel-Shortcut, Palette). | B |
| MOT-03 | Easing und Dauer nur aus den Motion-Tokens; nie `ease-in` auf UI; Exits schneller als Enter. | H |
| MOT-04 | Dauern: Mikro 120–200 ms, Enter 160–240 ms, Layout ≤ 300 ms, nichts > 400 ms außer Endlos-Indikatoren; Springs ohne Überschwingen > 4 px (NFR-007). | H |
| MOT-05 | FR-078-Werte: Message-Enter 180 ms (opacity, y 8→0), Chip-Crossfade 200 ms, Karten-Spring ≤ 300 ms, Badge-Puls 150 ms, Thread-Pane 220 ms. Enter nicht beim Backfill/Zurückscrollen. | H |
| MOT-06 | TaskSteps-Werte (NFR-007): Pop 640/22/0.7 ab scale 0.4, Zellwechsel 520/34/0.45, Bogen 0,8 s linear, Shimmer 1,6 s linear, Farbe/Meta 200 ms, Ansage entprellt 500 ms. | M |
| MOT-07 | Nur `transform`/`opacity` (+`clip-path`); kein `transition: all`; keine Layout-Properties (Akkordeon-Höhe ausgenommen). | H |
| MOT-08 | Popover/Menüs/Tooltips aus dem Auslöser (`--radix-*-transform-origin`), Dialoge zentriert; nie aus `scale(0)`. | M |
| MOT-09 | Schnell wiederholte Auslöser (Toast, Badge, Toggle) unterbrechbar: Transitions/Springs statt Keyframes. | M |
| MOT-10 | Reduced Motion: 0 Transform-Animationen, Opacity ≤ 120 ms (NFR-008, FR-079); Hover-Motion nur unter `(hover: hover) and (pointer: fine)`; Endlos-Loops stoppen. | B |
| MOT-11 | Performance: 60 fps, < 5 % verworfene Frames bei 500 Nachrichten + Streaming, keine Long Task > 50 ms während Enter (NFR-006, `test_motion_perf.py`); kein `setState` pro Frame; beim Streaming `transform`-Strings statt Motion-`x/y`. | H |
| MOT-12 | Zurückhaltung: eine Signatur-Bewegung je Ansicht; Stagger 30–80 ms, gesamt < 400 ms, blockiert nie Interaktion. | N |

## 7 Robustheit & Inhalte (OneRedOak Phase 5, Emil break-ui, Vercel content)
| ID | Prüfpunkt | Schwere |
|---|---|---|
| ROB-01 | Zustände vorhanden: Laden (Skeleton passend zum Layout), leer (genau eine nächste Aktion, FR-081), Fehler (mit nächstem Schritt), offline/Reconnect (FR-082). | H |
| ROB-02 | Worst Case hält: lange deutsche Namen, ungebrochene URL/E-Mail, 1-Zeichen-Text, Emoji, RTL, fehlendes Avatar. | H |
| ROB-03 | Zähler mit `Intl.PluralRules`/`Intl.NumberFormat` („1 Nachricht“, „1.284“), Zeiten mit `Intl.DateTimeFormat`. | M |
| ROB-04 | Lange Listen virtualisiert (500 Nachrichten), kein Ruckeln beim Scrollen. | H |
| ROB-05 | Menschen-Text bleibt Textknoten (FR-020); Agent-Markdown nur über ai-elements + `streamdown-security.ts`. | B |
| ROB-06 | Gekürzter Text ist lesbar (Tooltip/`title`/Detail); Zahlen und Zeiten nie gekürzt. | M |
| ROB-07 | Freigabe-Karte: nur EDIT+ bedienbar, sonst Lesetext (FR-056); anderswo gelöst → Status binnen 2 s (FR-057); 403/schon gelöst → Server-Verdikt, kein Fehler-Toast (FR-058). | B |

## 8 Code & Konsole (OneRedOak Phase 6–7, Vercel)
| ID | Prüfpunkt | Schwere |
|---|---|---|
| CODE-01 | Bestehende Primitives (`components/ui`, `ai-elements`) wiederverwendet; kein zweites Primitive-System; nichts von Hand nachgebaut, was Radix kann. | H |
| CODE-02 | Statischer Pass ohne Anti-Patterns (`div onClick`, `outline-none` ohne Ersatz, `transition: all`, Bilder ohne Maße, `dangerouslySetInnerHTML` auf Nutzerinhalt). | H |
| CODE-03 | `cn` für Klassenlogik; gleiche lange className mehrfach → Komponente extrahieren. | N |
| CODE-04 | Browser-Konsole ohne neue Errors/Warnings. | H |

## 9 Texte (plugin87 design-taste, Vercel copy)
| ID | Prüfpunkt | Schwere |
|---|---|---|
| COPY-01 | Nutzersprache statt Systembegriffe; aktive, konkrete Verben („Freigeben“, „Erneut senden“). | M |
| COPY-02 | Fehlermeldungen sagen, was passiert ist und was zu tun ist; keine Entschuldigungen, nichts Vages. | M |
| COPY-03 | `…` statt `...`, Ladetexte enden mit `…`; keine Gedankenstrich-Füller, keine Marketingwörter. | N |

## Report-Block (in die PR-Notiz bzw. `docs/ux/reviews/…` übernehmen)
```markdown
### UX-Review <task-id> – <branch> @ <sha>
Screenshots: /tmp/omnigent-chat-ux/<task-id>/
| Bereich | ok | n/a | Befunde | nicht geprüft |
|---|---|---|---|---|
| PRO / INT / RSP / VIS / ICO / A11Y / MOT / ROB / CODE / COPY | … | … | … | … |
Offene Blocker: <IDs oder „keine“>
Offene High: <IDs oder „keine“>
Pläne: docs/ux/design-plans/<files>
Verdikt: BLOCK | PASS
```
Ein offener Blocker stoppt den Merge (NFR-027). Befunde werden nicht abgeschwächt.
