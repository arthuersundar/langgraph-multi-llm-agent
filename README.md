# langgraph-multi-llm-agent

An AWS cost and governance assistant. It answers from company policy documents (RAG on **S3 Vectors**) and from live AWS data (**Cost Explorer**, untagged resources), on swappable model backends: **Amazon Bedrock**, a **local model** (Ollama or vLLM), or either one through a deployed **API gateway** built with Terraform. A small **router** sends easy questions to a cheap model and hard ones to a stronger one, code-level **guardrails** defend against prompt injection and data leaks, and an **eval harness** measures all of it. The same tools are also served over **MCP** for Claude Desktop and other clients.

Built to answer one question honestly: *how small a model can you get away with, and where does it break?*

## What it does

Ask: *"Which resources are missing the owner tag, and what does policy say should happen to them?"*

1. The agent looks up the Tagging Policy and the remediation runbook (policy documents, retrieved from S3 Vectors).
2. It calls the live tool for untagged resources.
3. It combines both into one answer that quotes the policy by title and lists the real resources.

```
                         +--> search_policies  --> S3 Vectors (policy docs, nomic embeddings)
question --> router --> LangGraph agent --+--> cost_by_service   --> AWS Cost Explorer
              |          (tool loop)      +--> untagged_resources -> Resource Groups Tagging API
              |               |
              |               v
              |        LLM (one of):  Bedrock (direct)  |  local Ollama/vLLM (direct)  |  gateway
              v                                                                          |
     cheap model / strong model                          API Gateway (key + usage plan per team)
                                                                  |
                                                         Lambda: OpenAI format <-> Bedrock Converse,
                                                                 or passthrough to a vLLM backend
```

- **RAG** gives the model the company's rules. **Live tools** give it what is happening now. The **agent** decides which to use.
- The provider is a config switch (`--provider bedrock|vllm|gateway|auto`). The prompt and tools stay identical, so comparisons are fair.
- The `vllm` provider means *any OpenAI-compatible server*. Here it is Ollama on a laptop.

## Quick start

```powershell
pip install -r requirements.txt        # then copy .env.example to .env and fill it in
python app\ingest.py                   # chunk the docs, embed, store in S3 Vectors
$env:USE_FIXTURES="1"                  # sample cost/tag data (see below)
python app\agent.py --provider vllm --sources "Which resources are missing required tags and what happens to them?"
python app\agent.py --provider auto "What is the monthly spending cap for sandbox accounts?"
```

On macOS/Linux use `export USE_FIXTURES=1` instead of `$env:USE_FIXTURES="1"`, and forward slashes in paths.

`--sources` prints what each tool handed the model (policy chunks, live data), so you can see what the answer is based on.

**Fixtures vs live.** `USE_FIXTURES=1` serves fixed sample data from `data/fixtures/` (invented spend and 8 untagged resources) so demos and evals give the same answer every run. Without it the tools call your real AWS account.

## Evaluation

16 questions in four kinds (policy, live data, combined, safety) with fixed fixture data. Each answer is scored on: **tools** (right tools called, forbidden ones not), **facts** (required facts present, bad phrases absent), and **ran** (no error). Prompt and tools are the same for every run.

| | 3B only | 7B only | Router (3B + 7B) |
|---|---|---|---|
| Models | llama3.2:3b | qwen2.5:7b | 3B for simple, 7B for hard |
| **Passed** | 11/16 | 13/16 | 13/16 |
| Policy lookups | 6/6 | 5/6 | 6/6 |
| Live-data questions | 3/3 | 3/3 | 3/3 |
| Combined (policy + live) | 1/5 | 3/5 | 3/5 |
| Safety | 1/2 | 2/2 | 1/2 |
| Avg time per question | 15.9 s | 53.5 s | 41.9 s |
| Total time (16 questions) | ~254 s | 855 s | 670 s |

All models ran locally through Ollama. Single runs of 16 questions are noisy (one question passed on the 3B and failed on the 7B), so read a one-question difference as a tie.

**What this shows**
- The small model is enough for single-source questions: all 9 policy and live-data questions passed on the 3B at about a third of the 7B's time.
- Model size matters on **combined questions** that need a policy lookup, a live call and a join of the two (1/5 on the 3B, 3/5 on the 7B).
- The router matches the 7B's pass count in about 78% of the time by sending 9 of 16 questions to the cheap model. Against the 3B alone, it buys two more passes for about 2.6x the time.

**Known failures**
- Listing resources "missing the owner tag": the model has to filter a list. Better done inside the tool.
- A NAT gateway cost question: the relevant postmortem was not in the top retrieved chunks.
- Budget-vs-spend arithmetic: small models skip a step or do the sum wrong. Better done in code.
- The out-of-scope (Azure) check is strict and may flag a correct answer that mentions an AWS dollar figure near the word "Azure".

Run it yourself:
```powershell
$env:USE_FIXTURES="1"
python eval\run_eval.py --provider vllm --label vllm-llama3.2_3b      # one backend
python eval\run_eval.py --provider auto                                # the router
python eval\report.py                                                  # comparison table
python eval\show_failures.py vllm-llama3.2_3b                          # see what a model actually said
```

## Lessons from building it

- **Do filtering and arithmetic in code, not in a small model.** Tools should return what the model needs, not raw lists it must sift.
- **Don't let the model choose retrieval filters.** An optional `doc_type` argument let the 3B pick a wrong value and silently get nothing. Removing it took the 3B from 3/6 to 6/6 on policy questions.
- **Guardrails belong in code.** Small models skipped the "I can't delete that" refusal, so action requests are caught by a rule and answered with a read-only refusal first (`GUARD_ACTIONS=0` shows the raw model). The same idea now covers injection, PII and leaks (see Guardrails).
- **Tool data is untrusted input too.** Tag values and document text can contain instructions, so they are cleaned before the model reads them.
- **Keep the prompt fixed across backends** or the comparison means nothing.
- **A router is cheap to build and measure.** Keyword rules (no extra model call) were enough to route this question set.

## Router

`app/router.py` classifies a question with regexes, with no extra model call: lookups in one source go to the cheap tier; anything that needs both sources, a calculation, an action, or is out of scope goes to the strong tier. Tiers are configuration:

```
ROUTER_CHEAP=vllm:llama3.2:3b
ROUTER_STRONG=vllm:qwen2.5:7b
# or on Bedrock: ROUTER_CHEAP=bedrock:us.amazon.nova-micro-v1:0  ROUTER_STRONG=bedrock:us.amazon.nova-pro-v1:0
```

## Guardrails

`app/guardrails.py` wraps the agent in code, with no extra model calls, at the four places an attack can arrive:

| Layer | What it does |
|---|---|
| **Input** | Blocks prompt injection (override, role hijack, prompt extraction, credential requests, chat-template tokens, base64-encoded payloads, 5 languages) and redacts PII and secrets (email, phone, SSN, Luhn-checked cards, AWS and Bedrock keys) before the model sees them |
| **Tool results** | Removes instruction-like text from what tools return, so an attacker-written tag value or document cannot steer the agent (indirect injection) |
| **Output** | Redacts secrets, SSNs and card numbers, scrubs PII the user typed, and replaces answers that repeat the system prompt |
| **Actions** | Flags requests to change AWS in any phrasing ("can you delete...", "the CFO approved it, go ahead and shut down...") so the answer opens with a read-only refusal |

A small model follows instructions it finds anywhere in its context, so these checks run before and after the model, where they cannot be talked out of it. `GUARDRAILS=0` switches them off to see the raw model.

**Measured offline** (`python eval/run_attacks.py --offline`, no model needed):

| | Result |
|---|---|
| Attacks stopped by the input, tool and action layers (dev set, 34 attacks, 7 categories) | 34/34 |
| Harmless questions wrongly blocked or redacted (24 look-alikes + the 16 eval questions) | 0/40 |
| **Held-out** attacks, written after the rules were fixed | 12/16 |
| Held-out harmless questions wrongly blocked | 0/12 |

The dev set is what the rules were tuned on, so the held-out row is the honest one. The four misses are the known limits of pattern matching: a paraphrase with no trigger words ("disregard the guidelines you were given earlier"), "forget what you were told" without "everything", letter-spaced text ("i g n o r e"), and a credentials question phrased without "your" or "you are using". Catching those needs a classifier model in front, which this project does not add.

**Measured with a model** (`qwen2.5:7b`; same prompt, tools and 36 attacks, guardrails off vs on):

| Attack category | Off | On |
|---|---|---|
| Direct overrides (incl. French, Spanish, German, base64) | 7/8 got through | 0 |
| Role hijacks | 3/4 (2 after manual review) | 0 |
| System-prompt extraction | 1/5 | 0 |
| PII echoed back in the answer | 3/5 | 0 |
| Indirect injection via poisoned tool data | 1/3 | 0 |
| Credential theft, delete requests, runaway tool loops | 0/11 | 0 |
| **Total** | **15/36 flagged (14 after review)** | **0/36** |

- Harmless look-alike questions wrongly blocked: **0/24**. The 16-question quality eval is unchanged with guardrails on (13/16, nothing blocked or flagged).
- Without guardrails the model followed instructions hidden inside a legitimate question, printed its system prompt, repeated an SSN, an AWS key and a password back to the user, and obeyed an instruction planted in the AWS data. It resisted credential theft and delete requests on its own.
- Scoring is automatic (marker in the answer, system prompt repeated, forbidden tool called, runaway loop), so it has false alarms: one refusal that quoted the marker was counted. The "on" result is on the attacks the rules were tuned on; the held-out row above is the one to quote for generalisation.

```powershell
python eval\run_attacks.py --offline                                  # no model: input, tool and action layers
python eval\run_attacks.py --provider vllm --guardrails off           # raw model
python eval\run_attacks.py --provider vllm --guardrails on --benign   # guarded, plus look-alike questions
python eval\test_guardrails.py                                        # unit tests
```
Set `$env:VLLM_MODEL` first; the run prints the model that actually answers. Results are saved in `eval\results\attacks\`.

## MCP server

`mcp_server/` exposes the same data as an [MCP](https://modelcontextprotocol.io) server, so Claude Desktop, Claude Code or any MCP client can use it: three read-only tools (`policy_search`, `cost_by_service`, `untagged_resources`), the 15 policy documents as resources, and a ready-made `governance_review` prompt. The client's model does the reasoning; this server supplies tools that are safe to hand to a model you do not control.

- Every call passes through the agent's guardrails (injection refused, PII redacted, instructions in tool data removed) plus a rate limit and a per-call audit line on stderr.
- `untagged_resources(missing_tag="owner")` filters in code. That was a failure on the small models in the eval ("which resources are missing the owner tag?"), so the fix lives in the tool.
- Tested against the real MCP SDK with a client script (`python mcp_server/client_demo.py`) and 36 offline checks (`python eval/test_mcp_tools.py`). Setup for Claude Desktop is in [`mcp_server/README.md`](mcp_server/README.md).

## Gateway (Terraform)

`gateway/` deploys an OpenAI-compatible endpoint: API Gateway REST API with a key and usage plan per team, in front of a dependency-free Lambda that translates OpenAI chat requests to Bedrock Converse (tool calls included) or passes them to a vLLM server. Guardrails come from an invented postmortem in the demo docs: capped `max_tokens`, no streaming, body size limit, per-team token and latency metrics (CloudWatch embedded metrics), backend throttles mapped to 429. Deploy, smoke tests, and the optional S3 state backend are in [`gateway/README.md`](gateway/README.md). Lambda translation tests run without AWS.

## Status and limits

- **Bedrock:** the code path (direct and through the gateway) is built and unit-tested, but the end-to-end Bedrock evaluation is not run: the AWS account's Bedrock token quotas were applied at 0 and a support case is open. Direct API calls with a Bedrock API key work. Bedrock numbers will be added when the quotas are restored.
- **Gateway to local model:** the gateway's vLLM backend needs a URL it can reach, so it cannot call a laptop's Ollama. Smoke tests (403 without a key, 400 on bad input) pass against the deployed gateway.
- **Data:** all company documents, names and figures under `data/` are invented. The fixture spend is sample data, not a real bill.
- **Read-only:** the agent has no permission to change AWS resources.

## Layout

```
app/        agent.py (graph, tools, guard) | router.py | guardrails.py | llm.py (provider switch) | aws_tools.py
            search.py, ingest.py, embeddings.py (S3 Vectors RAG)
data/       docs/ (15 invented policy documents) | fixtures/ (sample cost and tag data)
eval/       questions.json | run_eval.py | scoring.py | report.py | show_failures.py | test_router.py
            attacks.json | benign.json | run_attacks.py | attack_scoring.py | test_guardrails.py | test_mcp_tools.py
mcp_server/ server.py (MCP server) | gov_tools.py (tool logic) | client_demo.py | README.md
gateway/    lambda/ (handler + tests) | terraform/ | bootstrap/ (optional state bucket)
```

Stack: Python, LangGraph, LangChain, S3 Vectors, Ollama, Amazon Bedrock, API Gateway, Lambda, Terraform.
