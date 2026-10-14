---
title: Untagged Resource Remediation Runbook
doc_type: runbook
owner_team: platform-eng
severity: low
last_reviewed: 2026-07-01
---
# Untagged Resource Remediation Runbook

## When to use
When the daily tagging report or an engineer finds resources missing any required tag from the Tagging Policy.

## Steps
1. **Find the creator.** Look up the resource's creation event in CloudTrail (`Create*` / `Run*` / `Put*` events) to identify the IAM user or role that created it.
2. **Map to a team.** Match the IAM role name or user to a team using the Team Ownership Directory. Roles follow the pattern `<team>-<application>-role`.
3. **Notify the owner** in the team's channel with the resource ARN and the missing tags. The grace period is 7 days.
4. **After 7 days without a fix:**
   - `dev`/`sandbox` (or unknown environment): stop the resource (do not delete) and tag it `quarantined=true`.
   - `prod`: escalate to the team lead and the CCoE. Never stop prod resources automatically.
5. **Deletion:** quarantined resources with no owner response after 30 days may be deleted, after taking a snapshot or backup where applicable.

## S3 buckets
Never delete an untagged S3 bucket without checking its contents and data classification. Buckets may hold customer data subject to retention rules.
