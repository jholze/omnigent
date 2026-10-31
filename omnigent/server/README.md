# server

Hosts agents as a service. Accepts agent specs, stores them durably, and exposes APIs for managing and (eventually) invoking agents over HTTP.

The server is one way to deploy agents — the managed, multi-tenant, always-on way.

External MCP clients can drive a running server over `POST /mcp`: sessions,
projects, comments, policies, sharing, and the other `/v1` surfaces in
[`docs/mcp.md`](../../docs/mcp.md). `POST /mcp` requires a JSON content type
and the same trusted `Origin` check as `/v1`. Writes do nothing unless
`confirm` is boolean `true`. Deletes and revokes name what would be lost.
That doc also has the Claude Code and Cursor client config and how to get a
bearer token with `omnigent login`.

**Fork-only:** `/mcp`, agent accounts (`ogc_` tokens, `/v1/agent-accounts`),
and the `chat_*` MCP tools exist only in the `jholze/omnigent` fork, not
upstream. The full reference (auth, tool catalog, limits, troubleshooting) is
[`docs/mcp.md`](../../docs/mcp.md).
