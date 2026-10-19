# UX skills (canonical copies)

Condensed, attributed adaptations used by the Omnigent agents **ux-builder** and
**ux-reviewer** (sources and licenses: `../NOTICES.md`). The agent bundles under
`agent-configs/ux-builder/skills/` and `agent-configs/ux-reviewer/skills/` carry copies
because a bundle must be self-contained. Edit here, then sync and re-register:

```bash
for s in ux-build-workflow ux-stack-rules ux-motion ux-agent-chat-patterns; do
  rsync -a --delete docs/ux/skills/$s/ agent-configs/ux-builder/skills/$s/; done
for s in ux-design-review ux-motion-review ux-break-ui ux-static-pass ux-handoff-plans ux-stack-rules; do
  rsync -a --delete docs/ux/skills/$s/ agent-configs/ux-reviewer/skills/$s/; done
omnigent agent add agent-configs/ux-builder --server http://127.0.0.1:6767
omnigent agent add agent-configs/ux-reviewer --server http://127.0.0.1:6767
```

| Skill | Agent | Built from |
|---|---|---|
| `ux-build-workflow` | builder | Dammyjay93/interface-design + anthropics frontend-design (process) |
| `ux-stack-rules` | builder, reviewer | ibelick baseline-ui + fixing-motion-performance + fixing-accessibility |
| `ux-motion` | builder | Emil Kowalski emil-design-eng + animate + find-animation-opportunities, project tokens |
| `ux-agent-chat-patterns` | builder | vercel/ai-elements skill (no Next.js/AI SDK) + prompt-kit llms.txt + interior.dev TaskSteps (behaviour only) |
| `ux-design-review` | reviewer | OneRedOak design-review agent + plugin87 design-review / design-taste / wcag-checklist |
| `ux-motion-review` | reviewer | Emil Kowalski review-animations + animation-performance |
| `ux-break-ui` | reviewer | Emil Kowalski break-ui |
| `ux-static-pass` | reviewer | vercel-labs web-interface-guidelines command.md |
| `ux-handoff-plans` | reviewer | ibelick improve-ui + plan template |
