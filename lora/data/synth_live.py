"""Random live-tool results in the exact shapes the agent's tools return, plus the facts
computed from them. Because the numbers are random, the model cannot memorize them: it has
to read the tool result, which is the behaviour the fine-tune is for."""
import random

SERVICES = {  # short name used in questions -> name Cost Explorer returns
    "EC2": "Amazon Elastic Compute Cloud - Compute",
    "S3": "Amazon Simple Storage Service",
    "RDS": "Amazon Relational Database Service",
    "EC2-Other": "EC2 - Other",
    "EKS": "Amazon Elastic Container Service for Kubernetes",
    "Bedrock": "Amazon Bedrock",
    "Glue": "AWS Glue",
    "CloudWatch": "Amazon CloudWatch",
    "Lambda": "AWS Lambda",
    "Athena": "Amazon Athena",
    "DynamoDB": "Amazon DynamoDB",
    "Route 53": "Amazon Route 53",
    "Kinesis": "Amazon Kinesis",
    "KMS": "AWS Key Management Service",
    "SageMaker": "Amazon SageMaker",
    "OpenSearch": "Amazon OpenSearch Service",
}
NOT_PRESENT = ["Redshift", "CloudFront", "QuickSight", "AWS Shield", "Textract", "Amazon MQ"]
TAGS = ["owner", "team", "cost-center", "environment", "application"]
SOURCE = "fixture (sample data for the Brightway demo company)"


def money(x: float) -> str:
    return f"${x:,.2f}"


def make_cost(rng: random.Random, days: int = 30, must_include: str | None = None) -> dict:
    names = rng.sample(list(SERVICES.values()), rng.randint(6, 10))
    if must_include and SERVICES[must_include] not in names:
        names[-1] = SERVICES[must_include]
    vals = sorted((round(rng.uniform(300, 32000) * (days / 30) ** 0.5, 2) for _ in names), reverse=True)
    rows = [{"service": n, "usd": v} for n, v in zip(names, vals)]
    total = round(sum(vals) * rng.uniform(1.03, 1.35), 2)
    if rng.random() < 0.5:   # fixture-style result
        return {"source": SOURCE, "period": f"last {days} days (fixture)", "total_usd": total,
                "top_services": rows, "note": None}
    return {"period": f"2026-{rng.randint(1, 9):02d}-{rng.randint(1, 9):02d} to 2026-{rng.randint(10, 12):02d}-{rng.randint(10, 28):02d}",
            "total_usd": total, "top_services": rows, "note": None}


def _hex(rng, n):
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


def _arn(rng) -> str:
    kind = rng.choice(["ec2", "s3", "rds", "lambda", "ebs", "alb", "eks", "nat"])
    acct = "111122223333"
    word = rng.choice(["tmp", "reports", "scratch", "ml", "etl", "promo", "sync", "batch", "dev", "audit"])
    if kind == "ec2":
        return f"arn:aws:ec2:us-east-1:{acct}:instance/i-{_hex(rng, 17)}"
    if kind == "s3":
        return f"arn:aws:s3:::brightway-{word}-{rng.randint(1000, 9999)}"
    if kind == "rds":
        return f"arn:aws:rds:us-east-1:{acct}:db:{word}-{rng.choice(['db', 'mart', 'store'])}"
    if kind == "lambda":
        return f"arn:aws:lambda:us-east-1:{acct}:function:{word}-{rng.choice(['handler', 'job', 'test'])}"
    if kind == "ebs":
        return f"arn:aws:ec2:us-east-1:{acct}:volume/vol-{_hex(rng, 17)}"
    if kind == "alb":
        return f"arn:aws:elasticloadbalancing:us-east-1:{acct}:loadbalancer/app/{word}-lb/{_hex(rng, 16)}"
    if kind == "eks":
        return f"arn:aws:eks:us-east-1:{acct}:cluster/{word}-sbx"
    return f"arn:aws:ec2:us-east-1:{acct}:natgateway/nat-{_hex(rng, 17)}"


def make_untagged(rng: random.Random, n: int | None = None) -> dict:
    n = rng.choice([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) if n is None else n
    arns = set()
    while len(arns) < n:
        arns.add(_arn(rng))
    rows = []
    for a in arns:
        pick = set(rng.sample(TAGS, rng.randint(1, 4)))
        rows.append({"arn": a, "missing": [t for t in TAGS if t in pick]})
    rng.shuffle(rows)
    return {"source": SOURCE, "region": "us-east-1", "scanned": rng.randint(max(n, 40), 600),
            "non_compliant": n, "truncated": False, "resources": rows}


# ---- computed facts (the ground truth the answers are built from) ----------------------
def share(cost: dict, name: str) -> float:
    row = next(r for r in cost["top_services"] if r["service"] == name)
    return round(100 * row["usd"] / cost["total_usd"], 1)


def missing_tag(data: dict, tag: str) -> list[dict]:
    return [r for r in data["resources"] if tag in r["missing"]]


def bullet(r: dict) -> str:
    return f"- `{r['arn']}`: missing {', '.join(r['missing'])}"
