---
title: Data Transfer and Networking Cost Guide
doc_type: guide
owner_team: platform-eng
last_reviewed: 2026-06-10
---
# Data Transfer and Networking Cost Guide

## NAT Gateways
NAT Gateways are charged per hour **and per GB processed**. Traffic from private subnets to AWS services (S3, DynamoDB, ECR, Bedrock) should not go through a NAT Gateway.

**Required:** use **VPC gateway endpoints** for S3 and DynamoDB (free) and **interface endpoints** for frequently used services such as ECR, Bedrock and CloudWatch Logs.

## Cross-AZ and cross-region traffic
- Cross-AZ traffic is charged in both directions. Keep chatty services (e.g. an application and its cache) in the same AZ where availability rules allow.
- Cross-region replication must be approved by the CCoE and justified by a DR requirement.

## Signs of a networking cost problem
- `NatGateway-Bytes` usage type rising in Cost Explorer.
- `DataTransfer-Regional-Bytes` (cross-AZ) growing faster than traffic.
