"""Live AWS tools for the agent. All read-only.

Usage:
    python app/aws_tools.py            # run each tool once and print the result
"""
import datetime as dt
import json
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # optional
    pass

REGION = os.getenv("AWS_REGION", "us-east-1")
# Required tags from the Tagging Policy (data/docs/01-tagging-policy.md).
REQUIRED_TAGS = ["owner", "team", "cost-center", "environment", "application"]
NON_TAGGABLE_PREFIXES = ("arn:aws:payments:",)

# USE_FIXTURES=1 serves fixed sample data (data/fixtures/) instead of calling AWS, so
# demos and evals give the same answers every run.
FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"


def _fixture(name: str) -> dict | None:
    if os.getenv("USE_FIXTURES") != "1":
        return None
    variant = os.getenv("FIXTURE_VARIANT")  # e.g. "poisoned" -> untagged.poisoned.json (security evals)
    if variant:
        alt = FIXTURES / name.replace(".json", f".{variant}.json")
        if alt.exists():
            name = alt.name
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def get_cost_by_service(days: int = 30, top_n: int = 10) -> dict:
    """Unblended cost per AWS service for the last `days` days, highest first.
    Note: each Cost Explorer API request is billed (about $0.01)."""
    fx = _fixture("cost_30d.json")
    if fx:
        return {**fx, "top_services": fx["top_services"][:top_n]}
    import boto3

    days = max(1, min(int(days), 365))
    ce = boto3.client("ce", region_name="us-east-1")  # Cost Explorer lives in us-east-1
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    totals: dict[str, float] = {}
    for period in resp["ResultsByTime"]:
        for g in period["Groups"]:
            totals[g["Keys"][0]] = totals.get(g["Keys"][0], 0.0) + float(g["Metrics"]["UnblendedCost"]["Amount"])
    # Drop services with no real spend; "+ 0.0" turns a -0.0 from rounding into 0.0.
    ranked = sorted(((s, c) for s, c in totals.items() if round(c, 2) != 0), key=lambda kv: kv[1], reverse=True)
    return {
        "period": f"{start} to {end}",
        "total_usd": round(sum(totals.values()), 2) + 0.0,
        "top_services": [{"service": s, "usd": round(c, 2) + 0.0} for s, c in ranked[:top_n]],
        "note": "No service has any spend in this period." if not ranked else None,
    }


def find_untagged_resources(max_results: int = 50) -> dict:
    """Resources missing any required tag. Uses the Resource Groups Tagging API, which
    only sees resources that are tagged now or were tagged before, so a resource that
    never had any tag may not appear."""
    fx = _fixture("untagged.json")
    if fx:
        return {**fx, "resources": fx["resources"][:max_results]}
    import boto3

    client = boto3.client("resourcegroupstaggingapi", region_name=REGION)
    offenders = []
    scanned = 0
    for page in client.get_paginator("get_resources").paginate(ResourcesPerPage=100):
        for r in page["ResourceTagMappingList"]:
            # Billing objects (e.g. payment instruments) show up here but can't be tagged by teams.
            if r["ResourceARN"].startswith(NON_TAGGABLE_PREFIXES):
                continue
            scanned += 1
            keys = {t["Key"].lower() for t in r.get("Tags", [])}
            missing = [t for t in REQUIRED_TAGS if t not in keys]
            if missing:
                offenders.append({"arn": r["ResourceARN"], "missing": missing})
    return {
        "region": REGION,
        "scanned": scanned,
        "non_compliant": len(offenders),
        "resources": offenders[:max_results],
        "truncated": len(offenders) > max_results,
    }


if __name__ == "__main__":
    for fn in (get_cost_by_service, find_untagged_resources):
        print(f"\n== {fn.__name__} ==")
        try:
            print(json.dumps(fn(), indent=2))
        except Exception as e:  # show the real error, e.g. missing IAM permission
            print(f"ERROR: {type(e).__name__}: {e}")
