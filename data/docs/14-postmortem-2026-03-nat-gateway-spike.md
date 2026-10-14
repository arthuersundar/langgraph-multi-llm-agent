---
title: "Postmortem: NAT Gateway Cost Spike (March 2026)"
doc_type: postmortem
owner_team: data-eng
date: 2026-03-18
cost_impact_usd: 7400
---
# Postmortem: NAT Gateway Cost Spike (March 2026)

## Summary
Over 9 days, NAT Gateway data processing charges in the data platform account rose from about $40/day to over $850/day, an extra **$7,400**.

## Root cause
A new Spark job (`sales-etl` v2) read ~40 TB from S3 through a NAT Gateway because the new VPC had no S3 gateway endpoint. The previous VPC had one; the Terraform module for the new VPC omitted it.

## Detection
Detected by the monthly budget alert at 80%, not by anomaly detection, which had not been enabled in that account. Detection took 9 days.

## Fix
- Added the S3 gateway endpoint; NAT charges returned to normal within an hour.
- Made the S3 and DynamoDB gateway endpoints mandatory in the shared VPC Terraform module.
- Enabled Cost Anomaly Detection in all accounts.

## Lessons
Check `NatGateway-Bytes` first when networking costs rise. See the Data Transfer and Networking Cost Guide.
