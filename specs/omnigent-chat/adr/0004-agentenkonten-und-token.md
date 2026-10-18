# ADR-0004: Agentenkonten als eigener Principal-Typ mit gehashtem Bearer-Token und Pfad-Allowlist
Status: accepted, amended by ADR-0010 (2026-10-17: Pfadgrenze `/mcp` + `/v1/chat/*` bleibt nach außen; In-Process-Aufrufe aus `/mcp` handeln mit den Rechten des Owners; „nur Chat-Rechte“ entfällt) · Date: 2026-10-16
## Context
Grok Bots sollen in R1 als eigene Teilnehmer lesen/schreiben (ST-7.1.1, ST-7.1.4), jeder gehört einem Menschen, nur Chat-Rechte, nie mehr sehen als der Owner (NFR-012/013). PR #14 `/mcp` akzeptiert nur User-JWT/Cookie und lehnt Scope-Tokens ab; `_DELEGATED_ALLOWED_PREFIXES` (`omnigent/server/auth.py:70`) gilt für alle Scope-Tokens (Device-Grant, Client-Credentials). Client-Credentials kennt genau einen Env-konfigurierten Client.
## Decision
Wir führen `chat_agent_accounts` mit Principal `agent:<uuid>` ein. Token: `ogc_` + 32 Byte urlsafe, gespeichert nur als `HMAC_SHA256(OMNIGENT_CHAT_AGENT_SECRET, token)`; Anzeige einmalig. Ein neuer Zweig im `AuthProvider` erkennt `Bearer ogc_…` und authentifiziert nur auf `/mcp` und `/v1/chat/*`. Webhook-Secret je Konto wird aus demselben Server-Secret abgeleitet (`"chat-webhook:" + account_id + ":" + secret_version`) und nicht gespeichert. Ohne `OMNIGENT_CHAT_AGENT_SECRET` sind Agentenkonten deaktiviert (503). `chat_access` erzwingt bei jedem Request die Owner-Teilmengenregel.
## Consequences
+ Kein Eingriff in bestehende Delegationspfade; Widerruf/Rotation sofort wirksam (DB-Lookup).
+ Keine Klartext-Secrets in der DB; Rotation per `secret_version`.
− Rotation des Server-Secrets invalidiert alle Agentenkonto-Tokens und Webhook-Secrets (dokumentieren).
− Ein DB-Lookup pro Bot-Request (Index auf `token_hash`; Bots sind wenige).
