---
title: Cloud Resource Tagging Policy
doc_type: policy
owner_team: platform-eng
applies_to: all AWS accounts
last_reviewed: 2026-07-01
---
# Cloud Resource Tagging Policy

## Purpose
Every cloud resource must be traceable to a team, an application and a cost center. Tags are how Brightway Retail allocates cloud spend and finds owners during incidents.

## Required tags
Every taggable AWS resource (EC2, EBS, S3, RDS, Lambda, EKS, Load Balancers, NAT Gateways) must carry these tags:

| Tag key | Example | Rules |
|---|---|---|
| `owner` | `jane.doe@brightway.example` | A person, not a distribution list |
| `team` | `data-eng` | Must match a team in the Team Ownership Directory |
| `cost-center` | `CC-4100` | Must match the team's cost center |
| `environment` | `prod`, `staging`, `dev`, `sandbox` | Lowercase only |
| `application` | `loyalty-api` | Short application name |

Optional: `data-classification` (`public`, `internal`, `confidential`, `restricted`). Required for S3 buckets and databases.

## Enforcement
- New resources without required tags are flagged by the daily tagging report.
- **Grace period:** 7 days after first flag. After that, `dev` and `sandbox` resources are stopped; `prod` resources are escalated to the team lead (see Untagged Resource Remediation Runbook).
- Untagged spend is reported monthly to Finance as "unallocated" and charged to the Platform Engineering cost center until fixed.

## Exceptions
Exceptions require approval from the Cloud Center of Excellence (CCoE) and expire after 90 days.
