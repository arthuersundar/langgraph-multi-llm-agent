"""Build a side-by-side table from the newest result file of each provider.

Usage:  python eval/report.py            # prints markdown, ready for the README
"""
import json
from pathlib import Path

results_dir = Path(__file__).resolve().parent / "results"
latest = {}
for f in sorted(results_dir.glob("*.json")):  # filenames sort by time, so the last one wins
    d = json.loads(f.read_text(encoding="utf-8"))
    latest[d["summary"]["provider"]] = d

if not latest:
    raise SystemExit("No result files yet. Run: python eval/run_eval.py --provider vllm")

names = sorted(latest)
print("| | " + " | ".join(names) + " |")
print("|---|" + "---|" * len(names))
rows = [
    ("Passed (all checks)", lambda s, n: f"{s['passed']}/{n}"),
    ("Right tools used", lambda s, n: f"{s['tools_ok']}/{n}"),
    ("Facts correct", lambda s, n: f"{s['facts_ok']}/{n}"),
    ("Run errors", lambda s, n: str(s["errors"])),
    ("Avg seconds per question", lambda s, n: str(s["avg_seconds"])),
    ("Tokens in / out", lambda s, n: f"{s['input_tokens']:,} / {s['output_tokens']:,}" if s["input_tokens"] else "n/a"),
]
for label, fn in rows:
    print(f"| {label} | " + " | ".join(fn(latest[p]["summary"], latest[p]["summary"]["questions"]) for p in names) + " |")

print("\nBy question type (passed):")
kinds = sorted({r["kind"] for d in latest.values() for r in d["results"]})
print("| type | " + " | ".join(names) + " |")
print("|---|" + "---|" * len(names))
for k in kinds:
    cells = []
    for p in names:
        rs = [r for r in latest[p]["results"] if r["kind"] == k]
        cells.append(f"{sum(r['passed'] for r in rs)}/{len(rs)}")
    print(f"| {k} | " + " | ".join(cells) + " |")
