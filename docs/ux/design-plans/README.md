# Design plans (ux-reviewer → ux-builder handoff)

Self-contained change plans written by **ux-reviewer** (read-only on product code) for
**ux-builder**. Mode adapted from ibelick/ui-skills `improve-ui` (MIT, see `../NOTICES.md`).

- File: `<task-id>-<kebab-outcome>.md`, template in `docs/ux/skills/ux-handoff-plans/SKILL.md`.
- `Status: open` → the builder implements it on the task's feature branch and sets
  `Status: done (<sha>)`, or `Status: rejected (<reason>)` and asks Jens if the plan
  conflicts with the spec.
- Plans are documentation: the builder commits the updated plan together with the fix, so
  the review trail lives in the PR. Plans never change the spec; spec conflicts go to Jens.
- Review notes for whole passes (e.g. T-122) live in `docs/ux/reviews/`.
