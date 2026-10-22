# ADR-0011: Projekt-Allowlist für Agentenkonten
Status: accepted · Date: 2026-10-22 · Entscheider: Jens Holze (Priorität direkt nach T-103/T-104, Issue #114) · Amends: ADR-0010
## Context
ADR-0010 lässt ein `ogc_`-Token auf `/mcp` im Prozess mit den vollen Rechten des Owners handeln (`mcp_effective_principal = owner_id`). Damit sieht der Bot jedes Projekt, jede Session und jeden Host, den der Owner besitzt. Der Bot **PM - FT Max** soll nur das Projekt **Fishing Tackle Max** (`ad2bc03dae8f47b98ce1bf5197881f7a`) sehen. Der Bot **ProductOS PM Bot** soll nur das Projekt **ProductOS** sehen. Ein Ersatz-Account ist keine Lösung. Die Konten werden erst nach Merge und Deploy von Jens angelegt, nicht von diesem Ticket.
## Decision
Wir werden
1. einem Agentenkonto eine optionale, nicht-leere Projekt-Allowlist geben. `NULL` bleibt unscoped und behält ADR-0010. Eine leere Liste ist ungültig (400). Scope entfernen ist ein ausdrücklicher Schritt zurück auf unscoped, mit Bestätigung.
2. die Rechte eines scoped Accounts als Schnittmenge aus den Owner-Rechten und den Sessions bilden, deren `project_id` in der Allowlist liegt (FR-105). Sub-Agent-Sessions erben die Entscheidung der Root-Session (FR-107).
3. die Prüfung bei jedem Request neu auswerten, ohne Cache über den Request hinaus (FR-108). Eine Session, die das erlaubte Projekt verlässt, ist beim nächsten Request unsichtbar. Widerruf und Token-Rotation bleiben 401.
4. Sessions nur mit einer `project_id` aus der Allowlist anlegen lassen. Ohne Projekt oder mit einem anderen Projekt wird nichts angelegt (FR-106).
5. scoped Accounts auf Admin, Projektverwaltung, Host-Verwaltung, Sharing, globale Policies und die Agentenkonto-Verwaltung mit 403 `out_of_scope` ablehnen. Metadaten der erlaubten Projekte sind lesbar. Host-Nutzung nur, um Sessions in erlaubten Projekten zu starten oder zu treiben (FR-110). `session_workspace` Shell bleibt owner-only.
6. Sessions und Projekte außerhalb des Scopes mit 404 verbergen. Verbotene Endpoint-Klassen antworten 403 `out_of_scope`. Jede Ablehnung wird mit Account-Id, Route und Grund auditiert, nie mit Token, `token_hash` oder Webhook-Secret (NFR-033, NFR-034).
7. die Allowlist in Settings → Agenten pflegen (FR-111). Ein Projekt, das der Owner nicht besitzt, ist 404 und ändert das Konto nicht. Löschen eines Projekts entfernt es aus jeder Liste. Wird eine Liste dadurch leer, fail closed: das Konto wird nicht unscoped.
8. die Migration umkehrbar halten. Bestehende Konten bleiben unscoped (NFR-035). Der Alembic-Parent auf `epic/team-chat` bei Aufnahme ist `ar1b2c3d4e5f`. Genau ein Head.
## Consequences
+ PM - FT Max und ProductOS PM Bot können mit Owner-Rechten arbeiten, ohne die anderen Projekte des Owners zu sehen.
+ Unscoped Konten und die T-103-Tests bleiben unverändert (FR-109).
− Die Owner-Abbildung aus ADR-0010 ist nicht mehr die ganze Geschichte. Ein fehlender Scope-Check neben `mcp_effective_principal` wäre eine Rechte-Erweiterung. Security-Review ist Pflicht. Epic nach `main` und jedes Deploy brauchen Jens' ausdrückliches OK.
− Projekte haben keine eigene Zugriffsliste. Der Scope hängt am Konto, nicht am Projekt.
