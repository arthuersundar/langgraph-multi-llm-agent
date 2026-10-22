"""Offline check of the router rules against the eval questions (no models, no AWS).
Run from the repo root:  python eval\test_router.py
Expected tier = what a person would say the question needs, NOT what a model happened to pass."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from router import classify  # noqa: E402

EXPECT = {"p": "simple", "l": "simple", "c": "hard", "s": "hard"}  # by id prefix
questions = json.loads((Path(__file__).resolve().parent / "questions.json").read_text(encoding="utf-8"))
wrong = 0
for q in questions:
    tier, reason = classify(q["question"])
    got = "hard" if tier == "hard" else "simple"
    ok = got == EXPECT[q["id"][0]]
    wrong += not ok
    print(f"{'ok ' if ok else 'BAD'} {q['id']:<20} -> {tier:<14} ({reason})")
print(f"\n{len(questions) - wrong}/{len(questions)} eval questions routed as expected")

# Questions written AFTER the rules, so they show whether the rules generalize beyond the eval set.
HELD_OUT = [
    ("What is the tagging policy for S3 buckets?", "simple"),
    ("Show me the top 5 services by cost this month", "simple"),
    ("Which team owns the loyalty-api application?", "simple"),
    ("How many untagged resources do we have right now?", "simple"),
    ("What is the retention period for audit logs?", "simple"),
    ("Terminate the sandbox EKS cluster", "hard"),
    ("Compare our Bedrock spend to the budget policy", "hard"),
    ("Why did NAT gateway costs spike last month and what does the runbook say?", "hard"),
    ("What is our GCP spend?", "hard"),
]
miss = 0
print("\nHeld-out questions:")
for text, want in HELD_OUT:
    tier, reason = classify(text)
    got = "hard" if tier == "hard" else "simple"
    miss += got != want
    print(f"{'ok ' if got == want else 'BAD'} {text[:60]:<60} -> {tier} ({reason})")
print(f"{len(HELD_OUT) - miss}/{len(HELD_OUT)} held-out routed as expected")
sys.exit(1 if (wrong or miss) else 0)
