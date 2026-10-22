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

On macOS/Linux use `export USE_FIXTURES=1` instead of ` $env:USE_FIXTURES="1"` ` and forward slashes in paths.

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
- **Guardrails belong in code.** Small models skipped the "I can't delete that" refusal, so action requests are caught by a rule and answered with a read-only refusal first (`GUARD_ACTIONS=0` shows the raw model).
- **Keep the prompt fixed across backends** or the comparison means nothing.
- **A router is cheap to build and measure.** Keyword rules (no extra model call) were enough to route this question set.

## Router

`app/router.py` classifies a question with regexes, with no extra model call: lookups in one source go to the cheap tier; anything that needs both sources, a calculation, an action, or is out of scope goes to the strong tier. Tiers are configuration:

```
ROUTER_CHEAP=vllm:llama3.2:3b
ROUTER_STRONG=vllm:qwen2.5:7b
# or on Bedrock: ROUTER_CHEAP=bedrock:us.amazon.nova-micro-v1:0  ROUTER_STRONG=bedrock:us.amazon.nova-pro-v1:0
```

## Gateway (Terraform)

`gateway/` deploys an OpenAI-compatible endpoint: API Gateway REST API with a key and usage plan per team, in front of a dependency-free Lambda that translates OpenAI chat requests to Bedrock Converse (tool calls included) or passes them to a vLLM server. Guardrails come from an invented postmortem in the demo docs: capped `max_tokens`, no streaming, body size limit, per-team token and latency metrics (CloudWatch embedded metrics), backend throttles mapped to 429. Deploy, smoke tests, and the optional S3 state backend are in [`gateway/README.md`](gateway/README.md). Lambda translation tests run without AWS.

## Status and limits

- **Bedrock:** the code path (direct and through the gateway) is built and unit-tested, but the end-to-end Bedrock evaluation is not run: the AWS account's Bedrock token quotas were applied at 0 and a support case is open. Direct API calls with a Bedrock API key work. Bedrock numbers will be added when the quotas are restored.
- **Gateway to local model:** the gateway's vLLM backend needs a URL it can reach, so it cannot call a laptop's Ollama. Smoke tests (403 without a key, 400 on bad input) pass against the deployed gateway.
- **Data:** all company documents, names and figures under `data/` are invented. The fixture spend is sample data, not a real bill.
- **Read-only:** the agent has no permission to change AWS resources.

## Layout

```
app/        agent.py (graph, tools, guard) | router.py | llm.py (provider switch) | aws_tools.py
            search.py, ingest.py, embeddings.py (S3 Vectors RAG)
data/       docs/ (15 invented policy documents) | fixtures/ (sample cost and tag data)
eval/       questions.json | run_eval.py | scoring.py | report.py | show_failures.py | test_router.py
gateway/    lambda/ (handler + tests) | terraform/ | bootstrap/ (optional state bucket)
```

Stack: Python, LangGraph, LangChain, S3 Vectors, Ollama, Amazon Bedrock, API Gateway, Lambda, Terraform.
