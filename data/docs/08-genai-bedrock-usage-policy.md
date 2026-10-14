---
title: GenAI and Amazon Bedrock Usage Policy
doc_type: policy
owner_team: ml-platform
applies_to: all GenAI workloads
last_reviewed: 2026-09-01
---
# GenAI and Amazon Bedrock Usage Policy

## Access
- All application calls to foundation models must go through the **central LLM API gateway** (API Gateway + Lambda). Direct calls to Bedrock from applications are not allowed in production.
- Each team receives its own gateway API key with a usage plan.

## Approved models

| Use case | Approved models |
|---|---|
| General chat, summarization | Amazon Nova Lite, Amazon Nova Pro |
| High-volume classification, extraction | Amazon Nova Micro |
| Complex reasoning (with justification) | Anthropic Claude models on Bedrock |
| Self-hosted (sensitive data, high volume) | Approved open-weight models on the internal vLLM platform |

## Quotas and budgets
- Default usage plan per team: **10,000 requests/day, 20 requests/second burst**.
- Default GenAI budget: **$500/month per team**. Increases need ML Platform and Finance approval.
- If a team exceeds 150% of its daily average for 2 hours, the gateway throttles that key and alerts the team. ML Platform can disable a key immediately (the "kill switch").

## Data rules
- No `restricted` data may be sent to any model. `confidential` data only to Bedrock (data stays in our AWS account) or the internal vLLM platform, never to external APIs.
- Prompts and responses are logged for 30 days, with customer PII masked by the gateway before logging.
