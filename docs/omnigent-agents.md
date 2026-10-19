# Agent team — jholze/omnigent

Copied from ProductOS (`productOS/agents/lead`), with two changes
taken from Udo MCP / the ticket loop:

- `requirements` writes the issue contract.
- `test_author` writes tests; `tester` only runs them.

## Sessions

| Session | Id | Role |
|---|---|---|
| Omnigent PM Hub | `c750a1c6d0cf4152b15d3e74830d1fe1` | Plan, stop, `HAND TO LEAD` |
| Omnigent Core | `dfd76d2566ce40b3b0743a6346ee9cad` | Spawn workers (`--parent` Core) |
| ProductOS PM Hub | `2d377d57f8644772bca62ddf06b7ae66` | Ask about PIM process only |

Configs live in `agents/lead/`. Product source is a worktree under
`.work/`. Spawn:

```bash
python3 agents/lead/scripts/spawn.py \
  --agent <role> --title <slug> \
  --parent dfd76d2566ce40b3b0743a6346ee9cad \
  --message "<brief>"
```

GitHub file/comment/PR: `.grok/scripts/invoke-github.sh` (Qwen via
Ollama). `github_ops` session-create is PTY-blocked on this Mac.
