# ADR-0009: MCP-Bot-Pfad: exakte Tool-Allowlist, Fehler als Tool-Ergebnis mit stabilem Code, Protokollbasis bis 2025-11-25
Status: accepted (Freigabe Jens 2026-10-17 19:19, „Ok. Mach und umbauen.“); **Entscheidung 1 (Allowlist) superseded by ADR-0010** (Jens 2026-10-17 20:11, „Bots dürfen alles, was der Owner darf.“) – Punkte 2–5 gelten weiter · Date: 2026-10-17
## Context
Die Gap-Analyse des gemergten MCP-Endpoints (PR #14: `omnigent/server/mcp_endpoint.py`, `mcp_extensions.py`, `docs/mcp.md`) zeigt: ~25 Tools inkl. Hochrisiko-Aktionen (`session_workspace` mit Shell für den Owner ohne Freigabe-Gate, `host_fs`, `policy`, `access`, Elicitation-Antworten), nur Protokollversionen 2024-11-05/2025-03-26, Ergebnisse als JSON in einem Textblock ohne `structuredContent`/Annotationen, kein Rate-Limit, kein Audit, Auth nur User-JWT. Grok Bots (Agentenkonten, ADR-0004) sollen in R1 über `/mcp` lesen/schreiben (ST-7.1.1, ST-7.1.6) und reagieren auf Fehler wie die Bot-Bremse (FR-074) nur, wenn sie maschinenlesbar sind. Die aktuelle MCP-Spezifikation ist 2026-07-28 (zustandslos ohne `initialize`, `server/discover`, Tasks-Extension, `subscriptions/listen`); MCP Security Best Practices und OWASP MCP Cheat Sheet empfehlen: Tool-Output als nicht vertrauenswürdig behandeln, Tool-Allowlists, menschliche Freigabe für destruktive Aktionen. Linked: FR-070, FR-091…FR-100, NFR-013, NFR-017, NFR-028, NFR-031.
## Decision
Wir werden
1. ~~für Agentenkonto-Tokens eine **exakte Allowlist** der sieben `chat_*`-Tools erzwingen (fail-closed, kein Präfix-Match), geprüft vor `confirm` und vor jedem `/v1`-Aufruf;~~ → ersetzt durch ADR-0010 (Owner-Rechte, keine Allowlist);
2. Fehler der `/v1/chat/*`-Routen als **Tool-Ergebnis mit `isError: true`** und stabilem Code (`<code>: …` im Text, `structuredContent.error.code`) liefern, nicht als JSON-RPC-Fehler (Ausnahme: Rate-Limit außerhalb von `tools/call` → HTTP 429 + `-32000`);
3. Chat-Inhalte in Tool-Ergebnissen mit `author.kind` und `content_trust: "untrusted"` kennzeichnen und im Textblock in eine nicht schließbare Hülle legen;
4. die Protokollversionen **2025-06-18 und 2025-11-25** zusätzlich aushandeln, `MCP-Protocol-Version` validieren und `structuredContent`/`outputSchema` ab 2025-06-18 senden (Textblock bleibt);
5. Protokoll 2026-07-28, Tasks-Extension, `subscriptions/listen`, OAuth 2.1/PRM/CIMD, Server Card, MCP Apps und A2A **nicht in R1** umsetzen (R2, Q-21).
## Consequences
+ ~~Bots erreichen nie Shell-, Dateisystem-, Policy-, Sharing- oder Freigabe-Tools, auch nicht künftig hinzukommende.~~ (entfällt mit ADR-0010)
+ Bot-Routinen können auf `bot_streak_limit` (warten auf Menschen) und `rate_limited` (`retry_after_s`) gezielt reagieren.
+ Neuere Clients (z. B. Grok Bot) bekommen strukturierte Ergebnisse; ältere Clients funktionieren unverändert.
− Zusätzlicher Code im PR-#14-Endpoint (Versionsermittlung je Request, Fehlerabbildung, Hülle); Annotationen nur für `chat_*` (Q-24).
− Ohne 2026-07-28-Unterstützung muss in R2 nachgezogen werden, sobald Clients nur noch die neue Version sprechen.
· Die Shell ohne Freigabe-Gate im User-JWT-Pfad bleibt außerhalb dieses Specs (Q-25).
