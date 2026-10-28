# AWS governance MCP server

Gives any MCP client (Claude Desktop, Claude Code, your own agent) three read-only tools over the
Brightway cost and governance data, with the same guardrails as the LangGraph agent in this repo.

| | Name | What it does |
|---|---|---|
| Tool | `policy_search(question, top_k)` | Best-matching passages from the policy library (S3 Vectors) |
| Tool | `cost_by_service(days, top_n)` | Live spend per service from Cost Explorer |
| Tool | `untagged_resources(missing_tag)` | Resources missing required tags; `missing_tag="owner"` filters in code, so the model never has to sift the list |
| Resource | `policy://index`, `policy://{name}` | The 15 policy documents, readable as files |
| Prompt | `governance_review(team)` | A ready-made review: untagged resources, policy, spend, three recommendations |

## Install and test (no Claude app needed)

```powershell
pip install mcp                          # works with mcp 1.x and 2.x
python eval\test_mcp_tools.py            # unit tests, no AWS, no SDK needed
python mcp_server\client_demo.py         # starts the server, talks to it like a client, prints what comes back
python mcp_server\client_demo.py --search   # also calls policy_search for real (needs S3 Vectors + Ollama)
```
The demo uses the sample data (`USE_FIXTURES=1`). Each call's audit line appears on the server's stderr.

## Use it from Claude Desktop

Find your Python: `where python`. Then edit `%APPDATA%\Claude\claude_desktop_config.json` (Settings > Developer > Edit Config) and restart Claude Desktop:

```json
{
  "mcpServers": {
    "aws-governance": {
      "command": "C:\\Path\\To\\python.exe",
      "args": ["C:\\projects\\langgraph-multi-llm-agent\\mcp_server\\server.py"],
      "env": { "USE_FIXTURES": "1" }
    }
  }
}
```
Remove the `env` block to use your real AWS account (it uses your normal AWS credentials; policy search also needs `VECTOR_BUCKET` in the repo's `.env` and Ollama running for embeddings). Then ask: *"Which resources are missing the owner tag, and what does policy say should happen to them?"* Claude will call the tools itself.

**Claude Code:** `claude mcp add aws-governance -e USE_FIXTURES=1 -- python C:\projects\langgraph-multi-llm-agent\mcp_server\server.py`

**HTTP instead of stdio:** `$env:MCP_TRANSPORT="streamable-http"; python mcp_server\server.py` serves `http://127.0.0.1:8000/mcp`. It has **no authentication**, so keep it on localhost; do not expose it.

## Safety design

- **Read-only.** There is no tool that changes AWS, and each tool is marked `readOnlyHint` for clients that show it.
- **The caller is a model you do not control, so inputs are untrusted.** Free-text arguments go through the same input guardrail as the agent (injection and credential requests are refused before any search, PII is replaced before it reaches the embedding model). Numeric arguments are clamped (`days` 1-365, `top_k` 1-8) and `missing_tag` must be one of the five required tags.
- **Tool results are untrusted too.** Instruction-like text in a policy chunk or tag value is removed and credentials are redacted before the client's model reads them.
- **Rate limit:** 60 calls a minute by default (`MCP_MAX_CALLS_PER_MIN`), because every Cost Explorer request is billed.
- **Audit trail:** one JSON line per call (tool, redacted arguments, outcome, milliseconds) on stderr, plus a file if `MCP_AUDIT_LOG` is set. Nothing is written to stdout, which carries the protocol.
- **Documents:** resource names must match `[a-z0-9-]+` and resolve inside `data/docs`; `../` and encoded variants are rejected (by the SDK and by the name check).

## Limits
- The guardrails are pattern rules, not a classifier (see the main README for what they miss).
- The rate limiter is per server process, not per client.
- `policy_search` is only as good as the index; it returns passages, it does not write answers. Answering is the client model's job.
