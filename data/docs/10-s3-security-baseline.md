---
title: S3 Security Baseline
doc_type: standard
owner_team: security
applies_to: all S3 buckets
last_reviewed: 2026-08-01
---
# S3 Security Baseline

All S3 buckets must meet this baseline. The security team scans buckets daily.

1. **Block Public Access** must be enabled at the account level and on every bucket. Exceptions (e.g. a public static website) require Security approval and the tag `public-approved=true`.
2. **Encryption at rest** is mandatory: SSE-S3 for `internal` data, SSE-KMS with a customer-managed key for `confidential` and `restricted` data.
3. **TLS only:** bucket policies must deny requests where `aws:SecureTransport` is false.
4. **Versioning** is required for buckets holding `confidential` or `restricted` data.
5. **Access logging** or CloudTrail data events must be enabled for `restricted` buckets.
6. Buckets must carry the `data-classification` tag (see the Tagging Policy).

## Remediation timelines
- Public bucket without approval: **fix within 4 hours** (Severity 1).
- Missing encryption or TLS policy: within 5 business days.
- Missing tags or logging: within 10 business days.
