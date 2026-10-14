---
title: "Postmortem: Runaway Bedrock Usage (June 2026)"
doc_type: postmortem
owner_team: ml-platform
date: 2026-06-24
cost_impact_usd: 3100
---
# Postmortem: Runaway Bedrock Usage (June 2026)

## Summary
A prototype support-summary agent in a sandbox account called Amazon Bedrock in a loop for 31 hours, costing **$3,100**.

## Root cause
The agent retried whenever the model's answer failed a JSON validation check, with no maximum retry count. A prompt change made most answers fail validation, so it retried indefinitely. The sandbox account called Bedrock directly instead of through the LLM gateway, so no per-team quota applied.

## Detection
Detected by the sandbox $300 cap terminating resources, then a manual review of the bill. Bedrock calls themselves were not stopped by the cap.

## Fix
- Blocked direct Bedrock access in sandbox accounts with a service control policy; all calls must use the gateway.
- Added a maximum of 3 retries and a per-run token budget to the agent framework.
- Added a CloudWatch alarm on gateway requests per key (150% of daily average for 2 hours).

## Lessons
Every agent loop needs a hard limit on iterations and spend. See the GenAI and Amazon Bedrock Usage Policy.
