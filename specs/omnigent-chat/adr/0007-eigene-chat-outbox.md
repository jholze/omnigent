# ADR-0007: Eigene Chat-Outbox je Agentenkonto, Signatur und Envelope aus PR #15 wiederverwendet
Status: accepted (Umsetzung R1.1, Q-18 entschieden 16.10. 23:25; R1 weckt per Polling über `chat_unread`) · Date: 2026-10-16
## Context
@-Erwähnung soll Grok Bots aktiv wecken (ST-7.1.2). PR #15 bringt `session_lifecycle_outbox` mit Ordnung je `session_id`, einen global konfigurierten Endpoint (`manager_webhook.endpoint`) und ein globales Secret, plus `manager_webhook_signing.sign(secret=…)`, Backoff, Lease, Dead-Letter. Chat-Events sind nicht sessiongebunden; jedes Konto hat eigene URL und eigenes Secret.
## Decision
Wir bauen `chat_agent_outbox` (Ordnung je `account_id`) und `chat_webhook_dispatcher` nach dem PR-#15-Muster (Insert in der Post-Transaktion, sofortiges Wecken, 2-s-Poll als Fallback, Backoff ≈ 5 s·2^n bis 15 min mit Jitter, 10 Versuche → `dead_letter`, Lease 60 s) und verwenden `sign()` sowie Header-Namen und Envelope-Felder aus PR #15. Payload ohne Nachrichtentext.
## Consequences
+ Grok Bot kann dieselbe Verifikation wie für Manager-Webhooks nutzen.
+ Keine Kopplung an Session-Sequenzen.
− Zweiter Dispatcher-Task im Server; gemeinsame Hilfsfunktionen sollten extrahiert werden statt kopiert (T-108).
