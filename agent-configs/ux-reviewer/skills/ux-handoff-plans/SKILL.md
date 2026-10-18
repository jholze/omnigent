---
name: ux-handoff-plans
description: Read-only handoff from ux-reviewer to ux-builder - prove findings (contract, runtime, correction), vet them, and write self-contained design plans to docs/ux/design-plans/<task-id>-<slug>.md. Never modifies product source.
---

<!-- Adapted for Omnigent from ibelick/ui-skills improve-ui and its plan template (MIT,
(c) 2026 Julien Thibeaut); see docs/ux/NOTICES.md. -->

# Handoff plans (improve-ui mode)

## Boundaries
- Never modify product source. Write only `docs/ux/design-plans/*.md` (plans),
  `docs/ux/reviews/*.md` (review notes, e.g. T-122) and `/tmp/omnigent-chat-ux/**`
  (screenshots, notes). Policy-enforced.
- No installs, formatters, commits, pushes, PR comments. Do not edit `docs/ux/system.md`
  or the checklist; put proposed doc changes into the plan's "Design documentation" section.
- Each plan is self-contained: the builder has no context from your session.

## Prove before you plan
A finding is plan-worthy only with all three:
1. **Contract** – a binding rule for this property and surface: FR/NFR/AC, ADR-0008,
   `docs/ux/system.md` (`decided`), checklist ID, or a direct contradiction in the same view.
   "Prefer", taste, or repetition alone is not a contract.
2. **Runtime** – the owner/value really reaches the surface (import, props, resolved token,
   screenshot). Hierarchy, density, clarity claims need rendered evidence (screenshot).
3. **Correction** – exactly one change, naming the existing token/variant/primitive/exemplar.
   If the fix needs new product intent or has alternatives, it is a question for Jens, not a plan.
Then try to falsify each candidate (deliberate exception? same root cause as another?) and
delete what fails.

## When to write plans
- Always for open **Blocker** and **High** findings of the task under review.
- Medium/Nit only when the brief or Jens selects them. One plan per change, not per symptom;
  reconcile with an existing open plan instead of duplicating.

## File name and template
`docs/ux/design-plans/<task-id>-<kebab-outcome>.md`
```markdown
# <Outcome>
Status: open            <!-- builder sets: done (commit sha) | rejected (reason) -->
Task: <T-xxx>  Severity: Blocker|High|Medium  Checklist: <IDs>  Spec: <FR/NFR/ADR>
Written against: <branch> @ <sha>

## Evidence chain
- Surface: `<route / component / state>`
- Problem: <direct observation>
- Evidence: <screenshot path> · `<file:line>` · <rule cited>
- Owner: `<path>`
- Affected surfaces: `<paths>`
- Uncertainty: <none | what to validate>

## Design decision
<the scoped change and why it fixes the root problem>

## Reuse
- `<token / component / variant>` · Exemplar: `<path>`

## Changes
1. `<path>` – Change: <implementation-ready> · Preserve: <what stays> · Verify: <observable result>

## Validation
- Interface: <routes, states, worst-case data, widths 1440/768/375, reduced motion>
- Repository: `<exact command>` → <expected result>

## Stop conditions
- Stop if <assumption fails / scope must widen / spec conflict>.

## Design documentation
- After acceptance: <decision to record in docs/ux/system.md, or none>

## Resolution
<builder fills in>
```
