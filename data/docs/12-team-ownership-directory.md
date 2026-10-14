---
title: Team Ownership Directory
doc_type: directory
owner_team: platform-eng
last_reviewed: 2026-09-01
---
# Team Ownership Directory

| Team (`team` tag) | Cost center | Team lead | Slack channel | Monthly AWS budget | Main applications |
|---|---|---|---|---|---|
| platform-eng | CC-1000 | Priya Raman | #platform-eng | $18,000 | eks-shared, ci-runners, nat-egress |
| data-eng | CC-4100 | Marcus Webb | #data-eng | $25,000 | datalake, airflow, sales-etl |
| ml-platform | CC-4200 | Elena Torres | #ml-platform | $15,000 | llm-gateway, vllm-serving, feature-store |
| ecommerce | CC-5300 | David Kim | #ecommerce | $12,000 | storefront-web, cart-api, checkout-api |
| store-systems | CC-6100 | Aisha Bello | #store-systems | $9,000 | pos-sync, inventory-api |
| loyalty | CC-5400 | Tom Nguyen | #loyalty | $6,000 | loyalty-api, offers-engine |
| security | CC-1100 | Grace Liu | #security | $4,000 | log-archive, scanners |
| finance-ops | CC-9000 | Robert Hale | #cloud-cost | $1,000 | cost-reports |

All people and channels above are fictional, for this demo company.

## Escalation
- Cost issues: team lead, then #cloud-cost (Finance Ops).
- Security issues: #security, and page the on-call security engineer for Severity 1.
- Ownership unclear: Platform Engineering owns triage.
