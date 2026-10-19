# Third-party notices: UX agent skills

The files under `docs/ux/` and the skill copies under `agent-configs/ux-builder/skills/`
and `agent-configs/ux-reviewer/skills/` are **condensed adaptations** of the open-source
sources below. Text was shortened, rearranged, merged and changed to fit Omnigent's
stack (React 19, Vite, Tailwind 4, shadcn-style components on `radix-ui`, `motion/react`,
`lucide-react`) and the team-chat spec (`specs/omnigent-chat/`, ADR-0008). Nothing here is
a verbatim copy of a whole file except `docs/ux/scripts/contrast.py`. Changes are ours;
mistakes in the adaptation are ours, not the original authors'.

Fetched on 2026-10-18 from the default branch (`main`) at the commit noted per source.

| # | Source | License | Copyright | Commit | Adapted from | Used in |
|---|---|---|---|---|---|---|
| 1 | https://github.com/Dammyjay93/interface-design | MIT | Copyright (c) 2026 Damola Akinleye | `2f9be3206855` | `.claude/skills/interface-design/SKILL.md`, `reference/system-template.md` | `skills/ux-build-workflow`, `system.md` |
| 2 | https://github.com/ibelick/ui-skills | MIT | Copyright (c) 2026 Julien Thibeaut | `747a47fdb6b4` | `skills/baseline-ui/SKILL.md`, `skills/fixing-motion-performance/SKILL.md`, `skills/fixing-accessibility/SKILL.md`, `skills/improve-ui/SKILL.md`, `skills/improve-ui/references/plan-template.md` | `skills/ux-stack-rules`, `skills/ux-handoff-plans`, `design-plans/README.md` |
| 3 | https://github.com/emilkowalski/skills | MIT | Copyright (c) 2026 Emil Kowalski | `2d228630ba14` | `skills/emil-design-eng`, `skills/animate`, `skills/find-animation-opportunities`, `skills/review-animations` (+`STANDARDS.md`), `skills/animation-performance`, `skills/break-ui` | `skills/ux-motion`, `skills/ux-motion-review`, `skills/ux-break-ui`, `chat-review-checklist.md` |
| 4 | https://github.com/anthropics/skills | Apache-2.0 (per `skills/frontend-design/LICENSE.txt`; the repository has no top-level SPDX license) | Anthropic, PBC (the `LICENSE.txt` carries no copyright line) | `dbd4588f9e10` | `skills/frontend-design/SKILL.md` (process "plan → review against the brief → build → critique", AI-default list, UX-writing rules) | `skills/ux-build-workflow` |
| 5 | https://github.com/vercel/ai-elements | Apache-2.0 | Copyright 2023 Vercel, Inc. | `ca8be600bfd0` | `skills/ai-elements/SKILL.md`, `skills/ai-elements/references/{conversation,message,tool,task,queue,plan,reasoning,chain-of-thought,confirmation,shimmer,prompt-input}.md` (component vocabulary only; Next.js, AI SDK and CLI-install parts removed) | `skills/ux-agent-chat-patterns` |
| 6 | https://github.com/ibelick/prompt-kit | MIT (`LICENCE.md`) | Copyright (c) 2025 Julien Thibeaut | `5a9496635cc0` | `llms.txt` (component list as context; no code) | `skills/ux-agent-chat-patterns` |
| 7 | https://github.com/OneRedOak/claude-code-workflows | MIT | Copyright (c) 2025 Patrick Ellis | `29a34c725313` | `design-review/design-review-agent.md` (phases 0–7, viewports, triage matrix, report structure) | `skills/ux-design-review`, `chat-review-checklist.md`, ux-reviewer prompt |
| 8 | https://github.com/plugin87/ux-ui-agent-skills (XIAS) | MIT | Copyright (c) 2026 Thientan Soparat | `62092bd83bc3` | `workflows/design-review.md`, `taste/design-taste.md`, `taste/motion-choreography.md`, `components/icon-system.md`, `accessibility/wcag-checklist.md`, `scripts/contrast.py` (file copied unchanged apart from a header comment) | `skills/ux-design-review`, `chat-review-checklist.md`, `scripts/contrast.py` |
| 9 | https://github.com/vercel-labs/web-interface-guidelines | MIT | Copyright (c) 2025 Vercel Labs | `434b7f913646` | `command.md` | `skills/ux-static-pass`, `chat-review-checklist.md` |

Referenced but not copied: interior.dev TaskSteps (pattern reference in ADR-0008; license
open as Q-20, so only behaviour is described, no code), microsoft/playwright-mcp
(`@playwright/mcp`, Apache-2.0, run via `npx`, not vendored).

## MIT License (applies to sources 1, 2, 3, 6, 7, 8, 9)

Each MIT source is used under the following terms, with the copyright line from the table
above:

```
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Apache License 2.0 (applies to sources 4 and 5)

Sources 4 and 5 are licensed under the Apache License, Version 2.0. You may not use
those files except in compliance with the License. You may obtain a copy of the License
at http://www.apache.org/licenses/LICENSE-2.0 (the repository root `LICENSE` of Omnigent
is also Apache-2.0). Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
CONDITIONS OF ANY KIND, either express or implied. See the License for the specific
language governing permissions and limitations under the License.

Notice for source 5, reproduced from its `LICENSE`: "Copyright 2023 Vercel, Inc. Licensed
under the Apache License, Version 2.0." Source 4 ships no NOTICE file; attribution is to
Anthropic, PBC.

Modification statement (Apache-2.0 §4(b)): the adapted files `skills/ux-build-workflow/SKILL.md`
and `skills/ux-agent-chat-patterns/SKILL.md` (and their copies under `agent-configs/`) were
modified by the Omnigent fork maintainers on 2026-10-18: condensed, merged with other
sources, Next.js / AI SDK / landing-page content removed, rules rewritten for Omnigent.
Each of these files states this in its header.
