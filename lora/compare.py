"""Compare eval runs side by side (per question, per kind, tool-use health).

    python lora/compare.py base=vllm-base tuned=vllm-tuned
    python lora/compare.py base=vllm-base tuned=vllm-tuned --heldout

Each NAME=SUBSTRING picks the newest file in eval/results whose name contains SUBSTRING.
With --heldout it reads runs made with --questions lora/heldout_questions.json (labels
should contain 'heldout').
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

RES = Path(__file__).resolve().parent.parent / "eval" / "results"
args = [a for a in sys.argv[1:] if not a.startswith("--")]
heldout = "--heldout" in sys.argv
runs = {}
for a in args:
    name, _, sub = a.partition("=")
    files = sorted((f for f in RES.glob("*.json") if sub in f.name and ("heldout" in f.name) == heldout),
                   key=lambda f: f.stat().st_mtime)
    if not files:
        sys.exit(f"no result file with '{sub}' ({'heldout' if heldout else 'main'} set)")
    d = json.loads(files[-1].read_text(encoding="utf-8"))
    runs[name] = {"file": files[-1].name, "model": d["summary"]["model"], "rows": {r["id"]: r for r in d["results"]}}

names = list(runs)
ids = list(next(iter(runs.values()))["rows"])
print(f"{'':<22}" + "".join(f"{n:>9}" for n in names))
for i in ids:
    print(f"{i:<22}" + "".join(f"{'PASS' if runs[n]['rows'][i]['passed'] else 'fail':>9}" for n in names))
print()
kinds = sorted({r["kind"] for r in next(iter(runs.values()))["rows"].values()})
for k in kinds:
    cells = []
    for n in names:
        rows = [r for r in runs[n]["rows"].values() if r["kind"] == k]
        cells.append(f"{sum(r['passed'] for r in rows)}/{len(rows)}")
    print(f"{k:<22}" + "".join(f"{c:>9}" for c in cells))
tot = [f"{sum(r['passed'] for r in runs[n]['rows'].values())}/{len(ids)}" for n in names]
print(f"{'TOTAL':<22}" + "".join(f"{c:>9}" for c in tot))
print(f"{'tools ok':<22}" + "".join(f"{sum(r['tools_ok'] for r in runs[n]['rows'].values()):>9}" for n in names))
print(f"{'facts ok':<22}" + "".join(f"{sum(r['facts_ok'] for r in runs[n]['rows'].values()):>9}" for n in names))
print(f"{'errors':<22}" + "".join(f"{sum(r['error'] is not None for r in runs[n]['rows'].values()):>9}" for n in names))
print(f"{'avg model calls':<22}" + "".join(f"{sum(r['model_calls'] for r in runs[n]['rows'].values()) / len(ids):>9.1f}" for n in names))
print(f"{'avg seconds':<22}" + "".join(f"{sum(r['seconds'] for r in runs[n]['rows'].values()) / len(ids):>9.1f}" for n in names))
print()
for n in names:
    print(f"{n}: model={runs[n]['model']}  file={runs[n]['file']}")
