---
title: Cloud Cost Guardrails Policy
doc_type: policy
owner_team: finance-ops
applies_to: all AWS accounts
last_reviewed: 2026-08-15
---
# Cloud Cost Guardrails Policy

## Monthly budgets
Each team has a monthly AWS budget agreed with Finance (see Team Ownership Directory). AWS Budgets alerts are configured at **50%, 80% and 100%** of the monthly budget.

## Thresholds and required actions

| Situation | Required action | Timeline |
|---|---|---|
| Spend reaches 80% of budget before day 20 of the month | Team lead reviews top 5 cost drivers and posts a summary in #cloud-cost | Within 2 business days |
| Daily spend for a service rises more than **30%** above its 7-day average | Treat as a cost anomaly; follow the Cost Anomaly Response Runbook | Same day |
| Spend exceeds 100% of budget | Freeze non-prod scale-ups; Finance and team lead approve any new resources | Immediately |
| Single new resource expected to cost > $2,000/month | Pre-approval from CCoE before launch | Before provisioning |

## Non-production rules
- `dev` and `sandbox` resources must be stopped outside 07:00–20:00 local time unless tagged `always-on=true` with CCoE approval.
- Sandbox accounts have a hard cap of **$300/month**; resources are terminated automatically above it.

## Savings commitments
Savings Plans and Reserved Instances are purchased centrally by Finance Ops. Teams must not buy commitments directly.
