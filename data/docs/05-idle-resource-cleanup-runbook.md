---
title: Idle Resource Cleanup Runbook
doc_type: runbook
owner_team: platform-eng
severity: low
last_reviewed: 2026-06-20
---
# Idle Resource Cleanup Runbook

Idle resources are the most common source of avoidable spend. Run this check weekly.

## Definitions of idle

| Resource | Idle when |
|---|---|
| EC2 instance | Average CPU < 5% and network < 5 MB/day for 14 days |
| EBS volume | Unattached for more than 7 days |
| Elastic IP | Not associated with a running instance |
| Load Balancer | Zero healthy targets or < 100 requests/day for 14 days |
| RDS instance | Zero connections for 7 days |
| NAT Gateway | < 1 GB processed per day for 14 days |
| Lambda function | Zero invocations for 60 days (no direct cost, but remove for hygiene) |

## Actions
1. Confirm the owner via the `owner` tag. If untagged, use the Untagged Resource Remediation Runbook.
2. Ask the owner to confirm the resource can be removed. Allow 5 business days.
3. For EBS volumes and RDS instances, **take a snapshot before deleting**.
4. Release unused Elastic IPs immediately in non-prod accounts; they are billed even when idle.
