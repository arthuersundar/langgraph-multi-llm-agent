"""Policy question/answer cards, written by hand from the documents in data/docs/.

Each card = one section of one document (the unit the real retriever returns) plus a few
question/answer pairs that the section fully answers. Answers quote the document title and
use only numbers and names that appear in the section.

Held out on purpose (never used as a training target or distractor, so the eval measures
transfer, not recall): three whole documents, and the sections the 16 eval questions depend on.
"""
HELD_OUT_DOCS = {"07", "10", "11"}          # EC2 rightsizing, S3 security baseline, IAM baseline
HELD_OUT_SECTIONS = {
    ("01", "Enforcement"), ("02", "Monthly budgets"), ("02", "Non-production rules"),
    ("02", "Savings commitments"), ("04", "Steps"), ("08", "Access"), ("12", "Overview"),
    ("13", "NAT Gateways"), ("14", "Summary"), ("14", "Root cause"), ("14", "Lessons"),
    ("15", "Summary"), ("15", "Root cause"),
}

TAG = "Cloud Resource Tagging Policy"
GUARD = "Cloud Cost Guardrails Policy"
ANOM = "Cost Anomaly Response Runbook"
REM = "Untagged Resource Remediation Runbook"
IDLE = "Idle Resource Cleanup Runbook"
S3G = "S3 Storage Cost Optimization Guide"
GEN = "GenAI and Amazon Bedrock Usage Policy"
LAM = "Lambda Error Alarm Runbook"
DIR = "Team Ownership Directory"
DTC = "Data Transfer and Networking Cost Guide"
PM14 = "Postmortem: NAT Gateway Cost Spike (March 2026)"
PM15 = "Postmortem: Runaway Bedrock Usage (June 2026)"

# (doc prefix, section) -> list of (question, answer)
CARDS = {
    ("01", "Purpose"): [
        ("Why does Brightway make us tag cloud resources?",
         f"According to the {TAG} (Purpose), every cloud resource must be traceable to a team, an application and a cost center. Tags are how Brightway Retail allocates cloud spend and finds owners during incidents."),
        ("What are tags used for in our AWS accounts?",
         f"Per the {TAG}: allocating cloud spend to teams, applications and cost centers, and finding the owner of a resource during an incident."),
    ],
    ("01", "Required tags"): [
        ("Which tags does every AWS resource need?",
         f"The {TAG} requires five tags on every taggable AWS resource (EC2, EBS, S3, RDS, Lambda, EKS, load balancers, NAT gateways): `owner`, `team`, `cost-center`, `environment` and `application`."),
        ("What values are allowed for the environment tag?",
         f"The {TAG} (Required tags) allows `prod`, `staging`, `dev` or `sandbox`, lowercase only."),
        ("Can the owner tag be a team mailing list?",
         f"No. The {TAG} says the `owner` tag must be a person, not a distribution list (for example `jane.doe@brightway.example`)."),
        ("Is the data-classification tag mandatory?",
         f"It is optional in general, but the {TAG} says `data-classification` (`public`, `internal`, `confidential` or `restricted`) is required for S3 buckets and databases."),
    ],
    ("01", "Exceptions"): [
        ("Can a team get an exception to the tagging rules, and for how long?",
         f"Yes. The {TAG} (Exceptions) says exceptions need approval from the Cloud Center of Excellence (CCoE) and expire after 90 days."),
        ("Who approves a tagging exception?",
         f"The Cloud Center of Excellence (CCoE), according to the {TAG}. An approved exception expires after 90 days."),
    ],
    ("02", "Thresholds and required actions"): [
        ("When does a daily cost increase count as an anomaly?",
         f"The {GUARD} treats it as a cost anomaly when a service's daily spend rises more than 30% above its 7-day average. The response is the {ANOM}, the same day."),
        ("What must happen if a team goes over 100% of its monthly budget?",
         f"Per the {GUARD}: non-prod scale-ups are frozen, and Finance and the team lead must approve any new resources, immediately."),
        ("Do I need approval before launching an expensive resource?",
         f"Yes, if a single new resource is expected to cost more than $2,000/month. The {GUARD} requires CCoE pre-approval before provisioning."),
        ("What should a team lead do when spend hits 80% of budget early in the month?",
         f"If spend reaches 80% of budget before day 20 of the month, the {GUARD} says the team lead reviews the top 5 cost drivers and posts a summary in #cloud-cost within 2 business days."),
    ],
    ("03", "Overview"): [
        ("When should the cost anomaly runbook be used?",
         f"The {ANOM} is for when daily spend for any service rises more than 30% above its 7-day average, or when AWS Cost Anomaly Detection raises an alert."),
    ],
    ("03", "Step 1: Identify the driver (15 minutes)"): [
        ("How do I find what caused a cost spike?",
         f"Step 1 of the {ANOM}: in Cost Explorer, group the last 14 days of cost by Service, then by Usage type, and find the largest increase. Then group by the `team` and `application` tags to find the owner. If the cost is untagged, follow the {REM} in parallel."),
    ],
    ("03", "Step 2: Classify the anomaly"): [
        ("What kinds of cost anomaly are there?",
         f"The {ANOM} (Step 2) has three: expected (a planned launch, load test or seasonal peak; record it in #cloud-cost and close), misconfiguration (for example a NAT Gateway carrying traffic that should use a VPC endpoint, debug logging left on, or an autoscaling group stuck at maximum) and runaway usage (for example a GenAI workload calling Bedrock in a loop or a Lambda retry storm)."),
    ],
    ("03", "Step 3: Contain"): [
        ("Can I just stop a production resource that is driving a cost anomaly?",
         f"No. Per the {ANOM} (Step 3), in prod you page the owning team and do not stop prod resources without the owner's agreement. In non-prod you can stop or scale down immediately."),
    ],
    ("03", "Step 4: Close out"): [
        ("What is required after a cost anomaly is resolved?",
         f"Step 4 of the {ANOM}: post a short summary in #cloud-cost covering root cause, cost impact, fix and prevention. Anomalies over $1,000 require a postmortem."),
    ],
    ("04", "When to use"): [
        ("When is the untagged-resource runbook used?",
         f"The {REM} is used when the daily tagging report or an engineer finds resources missing any required tag from the {TAG}."),
    ],
    ("04", "S3 buckets"): [
        ("Can I delete an untagged S3 bucket?",
         f"Not without checking first. The {REM} (S3 buckets) says never delete an untagged S3 bucket without checking its contents and data classification, because buckets may hold customer data subject to retention rules."),
    ],
    ("05", "Overview"): [
        ("How often should idle resources be checked?",
         f"Weekly, per the {IDLE}. It calls idle resources the most common source of avoidable spend."),
    ],
    ("05", "Definitions of idle"): [
        ("When is an EBS volume considered idle?",
         f"Per the {IDLE}, an EBS volume is idle when it has been unattached for more than 7 days."),
        ("What counts as an idle EC2 instance?",
         f"The {IDLE} defines it as average CPU below 5% and network below 5 MB/day for 14 days."),
        ("When is an RDS instance idle?",
         f"According to the {IDLE}, when it has had zero connections for 7 days."),
        ("How do we decide a NAT gateway or load balancer is idle?",
         f"Per the {IDLE}: a NAT Gateway is idle when it processes less than 1 GB per day for 14 days; a load balancer is idle with zero healthy targets or fewer than 100 requests/day for 14 days."),
    ],
    ("05", "Actions"): [
        ("What do I do once I find an idle resource?",
         f"The {IDLE} (Actions): confirm the owner via the `owner` tag (use the {REM} if untagged), ask the owner to confirm removal and allow 5 business days, and take a snapshot before deleting EBS volumes or RDS instances. Unused Elastic IPs in non-prod accounts are released immediately because they are billed even when idle."),
    ],
    ("06", "Storage classes"): [
        ("Which S3 storage class should a bucket with unpredictable access use?",
         f"S3 Intelligent-Tiering, per the {S3G}. It is the default for any bucket with unknown or changing access patterns and is required for all new data-lake buckets."),
        ("When should data go to Glacier?",
         f"The {S3G} says S3 Glacier Instant Retrieval or Flexible Retrieval is for archives and compliance data accessed less than once a quarter."),
    ],
    ("06", "Mandatory lifecycle rules"): [
        ("What lifecycle rules must every S3 bucket have?",
         f"The {S3G} requires a lifecycle policy that aborts incomplete multipart uploads after 7 days and expires non-current object versions after 30 days (versioned buckets). Log buckets must also move objects to Glacier after 90 days and delete them after 400 days, unless a legal hold applies."),
        ("How long do we keep log bucket data?",
         f"Per the {S3G}: move to Glacier after 90 days and delete after 400 days, unless a legal hold applies."),
    ],
    ("06", "Common cost surprises"): [
        ("What commonly makes S3 bills surprisingly high?",
         f"The {S3G} lists: versioned buckets that never expire old versions (can double storage cost), millions of small PUT/GET requests (can cost more than the storage itself, so batch small files), and cross-region data transfer, which is charged per GB, so keep compute in the same region as the bucket."),
    ],
    ("08", "Approved models"): [
        ("Which models are approved for high-volume classification?",
         f"Amazon Nova Micro, per the {GEN}. General chat and summarization use Nova Lite or Nova Pro, and complex reasoning (with justification) may use Anthropic Claude models on Bedrock."),
        ("What can we use for sensitive data at high volume?",
         f"The {GEN} approves open-weight models on the internal vLLM platform for sensitive data and high volume."),
    ],
    ("08", "Quotas and budgets"): [
        ("What is a team's default GenAI usage plan?",
         f"The {GEN} gives each team 10,000 requests/day with a 20 requests/second burst, and a default GenAI budget of $500/month per team. Increases need ML Platform and Finance approval."),
        ("What happens if a team's GenAI traffic spikes?",
         f"Per the {GEN}: if a team exceeds 150% of its daily average for 2 hours, the gateway throttles that key and alerts the team. ML Platform can disable a key immediately (the kill switch)."),
    ],
    ("08", "Data rules"): [
        ("Can confidential data be sent to a model?",
         f"Only to Bedrock (the data stays in our AWS account) or the internal vLLM platform, never to external APIs. The {GEN} also says no `restricted` data may be sent to any model."),
        ("How long are prompts and responses kept?",
         f"The {GEN} says they are logged for 30 days, with customer PII masked by the gateway before logging."),
    ],
    ("09", "Overview"): [
        ("What triggers the Lambda error runbook?",
         f"The {LAM} is triggered by CloudWatch alarms named `<function>-errors-high` (error rate above 5% for 10 minutes) or `<function>-throttles`."),
    ],
    ("09", "Step 1: Assess impact"): [
        ("How severe is a Lambda alarm for a customer-facing function?",
         f"Severity 2, per the {LAM} (Step 1). Check the function's Errors, Throttles, Duration and ConcurrentExecutions metrics and whether it is customer-facing via the `application` tag."),
    ],
    ("09", "Step 2: Common causes and fixes"): [
        ("Lambda errors spiked right after a deploy. What should we do?",
         f"Roll back to the previous alias or version. The {LAM} lists a bad release as the likely cause of errors that spike right after a deploy."),
        ("What causes Lambda throttles and how do we fix them?",
         f"Per the {LAM}: reserved concurrency set too low or the account limit reached. Increase reserved concurrency and check for runaway invocations."),
        ("Errors and cost are both rising on a Lambda. What is likely happening?",
         f"A retry storm from an event source such as SQS or Kinesis, according to the {LAM}. The fix is a dead-letter queue and a maximum number of retry attempts."),
    ],
    ("09", "Step 3: Cost check"): [
        ("When does a Lambda problem also become a cost problem?",
         f"If invocations are more than 3x normal, the {LAM} (Step 3) says to follow the {ANOM} as well, because a retry storm can multiply cost quickly."),
    ],
    ("12", "Escalation"): [
        ("Who do I escalate a cost issue to?",
         f"The {DIR} (Escalation): the team lead first, then #cloud-cost (Finance Ops). Security issues go to #security, with the on-call security engineer paged for Severity 1. If ownership is unclear, Platform Engineering owns triage."),
    ],
    ("13", "Cross-AZ and cross-region traffic"): [
        ("Is cross-AZ traffic free?",
         f"No. The {DTC} says cross-AZ traffic is charged in both directions, so keep chatty services such as an application and its cache in the same AZ where availability rules allow."),
        ("Can we replicate data across regions?",
         f"Only with CCoE approval and a DR requirement, per the {DTC}."),
    ],
    ("13", "Signs of a networking cost problem"): [
        ("How do I spot a networking cost problem in Cost Explorer?",
         f"The {DTC} points to two signals: the `NatGateway-Bytes` usage type rising, and `DataTransfer-Regional-Bytes` (cross-AZ) growing faster than traffic."),
    ],
    ("14", "Detection"): [
        ("How was the March 2026 NAT gateway spike detected?",
         f"By the monthly budget alert at 80%, not by anomaly detection, which had not been enabled in that account. The {PM14} says detection took 9 days."),
    ],
    ("14", "Fix"): [
        ("What was changed after the NAT gateway cost spike?",
         f"The {PM14} lists: an S3 gateway endpoint was added (NAT charges returned to normal within an hour), the S3 and DynamoDB gateway endpoints became mandatory in the shared VPC Terraform module, and Cost Anomaly Detection was enabled in all accounts."),
    ],
    ("15", "Detection"): [
        ("How was the June 2026 Bedrock runaway found?",
         f"Through the sandbox $300 cap terminating resources, followed by a manual review of the bill. The {PM15} notes the cap did not stop the Bedrock calls themselves."),
    ],
    ("15", "Fix"): [
        ("What fixes followed the runaway Bedrock incident?",
         f"Per the {PM15}: direct Bedrock access was blocked in sandbox accounts with a service control policy (all calls must use the gateway), the agent framework got a maximum of 3 retries and a per-run token budget, and a CloudWatch alarm was added on gateway requests per key (150% of daily average for 2 hours)."),
    ],
    ("15", "Lessons"): [
        ("What is the main lesson from the Bedrock runaway postmortem?",
         f"Every agent loop needs a hard limit on iterations and spend, according to the {PM15}, which points to the {GEN}."),
    ],
}

# Questions the library cannot answer (used for the retrieval-miss cases). None of these
# topics appear in any document.
MISSES = [
    ("What is our paid time off policy?", "paid time off"),
    ("How long do we keep RDS automated backups?", "RDS backup retention"),
    ("What is the Kubernetes version upgrade cadence?", "Kubernetes upgrades"),
    ("Do we have a policy on using a corporate VPN?", "a VPN policy"),
    ("What is the process for buying a new laptop?", "laptop purchases"),
    ("How do I configure CloudFront cache behaviours?", "CloudFront configuration"),
    ("What is our DynamoDB on-demand capacity policy?", "DynamoDB capacity"),
    ("What are the rules for expensing conference travel?", "travel expenses"),
    ("Which password manager does the company use?", "a password manager"),
    ("What is our disaster recovery RTO for the checkout API?", "recovery time objectives"),
]
