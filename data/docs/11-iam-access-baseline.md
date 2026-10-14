---
title: IAM Access Baseline
doc_type: standard
owner_team: security
last_reviewed: 2026-08-01
---
# IAM Access Baseline

1. **No long-lived access keys** for human users in production accounts. Use SSO (IAM Identity Center). Access keys older than **90 days** must be rotated; keys unused for 45 days are disabled.
2. **Root account:** MFA required; root must not be used for daily work. Any root login triggers a security alert.
3. **Least privilege:** `AdministratorAccess` is limited to the platform break-glass role and sandbox accounts. Application roles follow the naming pattern `<team>-<application>-role` and grant only the actions they need.
4. **MFA** is required for all console users.
5. **External access:** IAM Access Analyzer findings for resources shared outside the organization must be reviewed within 5 business days.
6. GenAI and agent workloads must use **read-only** roles unless an action is explicitly approved; agents must never hold permissions to delete resources.
