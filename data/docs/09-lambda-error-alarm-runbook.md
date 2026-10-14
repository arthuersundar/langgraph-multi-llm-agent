---
title: Lambda Error Alarm Runbook
doc_type: runbook
owner_team: platform-eng
severity: high
last_reviewed: 2026-07-18
---
# Lambda Error Alarm Runbook

Triggered by CloudWatch alarms named `<function>-errors-high` (error rate above 5% for 10 minutes) or `<function>-throttles`.

## Step 1: Assess impact
1. Open the function's CloudWatch metrics: Errors, Throttles, Duration, ConcurrentExecutions.
2. Check whether the function is customer-facing (tag `application`, see Team Ownership Directory). Customer-facing functions are Severity 2.

## Step 2: Common causes and fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| Errors spike right after a deploy | Bad release | Roll back to the previous alias/version |
| Timeouts, Duration near the limit | Slow downstream (database, external API) | Check downstream health; raise timeout only as a temporary measure |
| Throttles | Reserved concurrency too low or account limit reached | Increase reserved concurrency; check for runaway invocations |
| Errors and cost rising together | Retry storm from an event source (SQS, Kinesis) | Configure a dead-letter queue and maximum retry attempts |

## Step 3: Cost check
A Lambda retry storm can multiply cost quickly. If invocations are more than 3x normal, follow the Cost Anomaly Response Runbook as well.
