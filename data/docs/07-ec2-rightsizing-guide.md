---
title: EC2 Rightsizing Guide
doc_type: guide
owner_team: platform-eng
last_reviewed: 2026-06-10
---
# EC2 Rightsizing Guide

## When an instance is oversized
An instance is a rightsizing candidate when, over 14 days, **peak CPU stays below 40%** and memory stays below 50%.

## Rules
- Downsize one size at a time (e.g. `m6i.2xlarge` to `m6i.xlarge`) and observe for 7 days.
- Prefer **Graviton** (`m7g`, `c7g`, `r7g`) for Linux workloads; typically 20% cheaper for similar performance. Test before switching.
- Production changes go through a change request; non-prod can be changed directly by the owning team.
- Burstable instances (`t3`/`t4g`) are allowed only for dev, sandbox and low-traffic internal tools.

## GPU instances
GPU instances (`g5`, `g6`, `p4`, `p5`) require CCoE approval, must be tagged `gpu-justification`, and must be stopped when not in use. Idle GPU instances are the single most expensive waste pattern in our accounts.
