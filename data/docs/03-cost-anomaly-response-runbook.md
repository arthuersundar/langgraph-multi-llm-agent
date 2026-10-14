---
title: Cost Anomaly Response Runbook
doc_type: runbook
owner_team: finance-ops
severity: medium
last_reviewed: 2026-08-15
---
# Cost Anomaly Response Runbook

Use this runbook when daily spend for any service rises more than 30% above its 7-day average, or when AWS Cost Anomaly Detection raises an alert.

## Step 1: Identify the driver (15 minutes)
1. In Cost Explorer, group the last 14 days of cost by **Service**, then by **Usage type**.
2. Identify the service and usage type with the largest increase.
3. Group by the `team` and `application` tags to find the owner. If the cost is untagged, follow the Untagged Resource Remediation Runbook in parallel.

## Step 2: Classify the anomaly
- **Expected:** a planned launch, load test or seasonal peak (e.g. holiday promotions). Record it in #cloud-cost and close.
- **Misconfiguration:** e.g. a NAT Gateway carrying traffic that should use a VPC endpoint, debug logging left on, an autoscaling group stuck at maximum.
- **Runaway usage:** e.g. a GenAI workload calling Bedrock in a loop, a Lambda retry storm.

## Step 3: Contain
- Misconfiguration or runaway usage in non-prod: stop or scale down immediately.
- In prod: page the owning team; do not stop prod resources without the owner's agreement.
- For Bedrock or other GenAI spend, see the GenAI Usage Policy for per-team quotas and the API gateway kill switch.

## Step 4: Close out
Post a short summary in #cloud-cost: root cause, cost impact, fix, and prevention. Anomalies over $1,000 require a postmortem.
