---
title: S3 Storage Cost Optimization Guide
doc_type: guide
owner_team: data-eng
last_reviewed: 2026-05-30
---
# S3 Storage Cost Optimization Guide

## Storage classes
- **S3 Standard:** frequently accessed data (accessed at least monthly).
- **S3 Intelligent-Tiering:** default for any bucket with unknown or changing access patterns. Required for all new data-lake buckets.
- **S3 Glacier Instant Retrieval / Flexible Retrieval:** archives and compliance data accessed less than once a quarter.

## Mandatory lifecycle rules
Every bucket must have a lifecycle policy that:
1. Aborts incomplete multipart uploads after **7 days**.
2. Expires non-current object versions after **30 days** (versioned buckets).
3. For log buckets: moves objects to Glacier after **90 days** and deletes them after **400 days**, unless a legal hold applies.

## Common cost surprises
- Versioned buckets that never expire old versions can double storage costs.
- Requests from many small objects (millions of PUT/GET requests) can cost more than the storage itself; batch small files.
- Cross-region data transfer from S3 is charged per GB. Keep compute in the same region as the bucket.
