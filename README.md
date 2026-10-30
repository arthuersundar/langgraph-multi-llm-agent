# langgraph-multi-llm-agent

An AWS cost and governance assistant. It answers from company policy documents (RAG on **S3 Vectors**) and from live AWS data (**Cost Explorer**, untagged resources), on swappable model backends: **Amazon Bedrock**, a **local model** (Ollama or vLLM), or either one through a deployed **API gateway** built with Terraform. A small **router** sends easy questions to a cheap model and hard ones to a stronger one, and an **eval harness** measures all of it.

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

`python eval/run_attacks.py --provider vllm --guardrails off` and `--guardrails on` run the same attacks through the whole agent and count how many succeed (a leaked system prompt, an obeyed instruction, a forbidden tool call, a runaway tool loop). `python eval/test_guardrails.py` runs the unit tests.

## MCP server

`mcp_server/` exposes the same three read-only tools (`policy_search`, `cost_by_service`, `untagged_resources`) plus the policy documents and a `governance_review` prompt to any MCP client (Claude Desktop, Claude Code, your own agent), with the same guardrails as the agent. `python eval/test_mcp_tools.py` runs the unit tests without AWS; `python mcp_server/client_demo.py` starts the server and talks to it like a client. Setup and the Claude Desktop config are in [`mcp_server/README.md`](mcp_server/README.md).

## Gateway (Terraform)

`gateway/` deploys an OpenAI-compatible endpoint: API Gateway REST API with a key and usage plan per team, in front of a dependency-free Lambda that translates OpenAI chat requests to Bedrock Converse (tool calls included) or passes them to a vLLM server. Guardrails come from an invented postmortem in the demo docs: capped `max_tokens`, no streaming, body size limit, per-team token and latency metrics (CloudWatch embedded metrics), backend throttles mapped to 429. Deploy, smoke tests, and the optional S3 state backend are in [`gateway/README.md`](gateway/README.md). Lambda translation tests run without AWS.

**Observability:** per-team requests, tokens, estimated cost (tokens times example prices, not the AWS bill) and p50/p95/p99 latency go to CloudWatch with a dashboard, alarms to an SNS topic, and X-Ray traces. On the deployed gateway, 40 requests with Bedrock quota at 0 all came back 429, and the dashboard showed each one waiting about 8 s (average 7,968 ms) because the SDK retried before giving up. Capping retries cut the wait to roughly 0.2 to 1.1 s. Details and the alarm behavior are in [`gateway/README.md`](gateway/README.md).

## Fine-tuning (LoRA)

`lora/` fine-tunes a small open model on the agent's tool-calling behavior and measures it against the untouched base model. Training data is synthetic: 573 trajectories (question, tool calls, tool results, answer) generated from the same invented documents and fixtures, with answers checked against the tool results; the 16 eval questions and the held-out ones are excluded. LoRA (rank 16) via Hugging Face PEFT, loss on assistant turns only. `base` and `tuned` are served side by side from one vLLM server and scored by the same eval.

Qwen2.5-3B-Instruct, bf16 on one rented RTX A5000 (training took about 20 minutes):

| | base | tuned |
|---|---|---|
| Main eval (16 questions), passed | 9 | 13 |
| Main eval, correct tools | 10 | 15 |
| Held-out documents (8 questions), passed | 3 | 7 |

The gain is in tool selection; facts in the answers were unchanged on the main eval (13 and 13), and two questions got worse. With 16 and 8 questions, treat this as an indication, not proof, and the training data is templated from the same document library, so it shows the behavior improved on this domain. These bf16 vLLM numbers are not comparable to the Ollama Q4 numbers above. Steps, settings and the full table are in [`lora/README.md`](lora/README.md). Llama-3.2-3B is not run yet.

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
            attacks.json | benign.json | run_attacks.py | attack_scoring.py | test_guardrails.py
gateway/    lambda/ (handler + tests) | terraform/ | bootstrap/ (optional state bucket)
mcp_server/ server.py | gov_tools.py | client_demo.py
lora/       data/ (synthetic trajectories) | train.py | serve_vllm.sh | compare.py | README.md
```

Stack: Python, LangGraph, LangChain, S3 Vectors, Ollama, Amazon Bedrock, API Gateway, Lambda, Terraform.
