"""Run the question set against one backend and save the results.

Usage (from the repo root):
    python eval/run_eval.py --provider vllm
    python eval/run_eval.py --provider bedrock --sleep 5
    python eval/run_eval.py --provider vllm --only c1-owner-notify
    python eval/run_eval.py --provider vllm --live      # live AWS instead of the sample data

By default the AWS tools return the sample data in data/fixtures/, so every run sees the
same cost and tag numbers and the two backends are compared fairly.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

ap = argparse.ArgumentParser()
ap.add_argument("--provider", required=True, help="bedrock, vllm or gateway")
ap.add_argument("--only", help="run a single question id")
ap.add_argument("--label", help="name for this run in the report (default: provider and model)")
ap.add_argument("--sleep", type=float, default=0, help="seconds to wait between questions")
ap.add_argument("--live", action="store_true", help="use live AWS data instead of fixtures")
ap.add_argument("--questions", help="path to another question file (default: eval/questions.json)")
args = ap.parse_args()

os.environ["USE_FIXTURES"] = "0" if args.live else "1"  # must be set before the tools load

from agent import build_graph, run, run_routed  # noqa: E402
from scoring import score  # noqa: E402

qpath = Path(args.questions) if args.questions else Path(__file__).resolve().parent / "questions.json"
questions = json.loads(qpath.read_text(encoding="utf-8"))
if args.only:
    questions = [q for q in questions if q["id"] == args.only]

if args.provider == "bedrock":
    model = os.getenv("BEDROCK_MODEL", "bedrock")
elif args.provider == "gateway":
    model = "via-gateway-" + os.getenv("GATEWAY_BACKEND", "bedrock")
elif args.provider == "auto":
    model = "router-{}-{}-policy-{}".format(
        os.getenv("ROUTER_CHEAP", "vllm:llama3.2:3b").split(":", 1)[-1],
        os.getenv("ROUTER_STRONG", "vllm:qwen2.5:7b").split(":", 1)[-1],
        os.getenv("ROUTER_POLICY_TO", "cheap"))
else:
    model = os.getenv("VLLM_MODEL", args.provider)
label = args.label or f"{args.provider}-{model}"
label = "".join(c if c.isalnum() or c in "-._" else "_" for c in label)
print(f"run: {label}  (data: {'live' if args.live else 'fixtures'})\n")

graph = None if args.provider == "auto" else build_graph(args.provider)
results = []
for i, q in enumerate(questions, 1):
    t0 = time.time()
    error = None
    out = {"answer": "", "tools_called": [], "model_calls": 0, "tokens": {"input": 0, "output": 0}, "route": None}
    try:
        out = run_routed(q["question"]) if args.provider == "auto" else run(q["question"], graph=graph)
    except Exception as e:  # keep going: one failure should not stop the whole run
        error = f"{type(e).__name__}: {str(e)[:200]}"
    seconds = round(time.time() - t0, 1)
    s = score(q, out["answer"], out["tools_called"], error)
    results.append({"id": q["id"], "kind": q["kind"], "question": q["question"], "seconds": seconds,
                    "error": error, **out, **s})
    flag = "PASS" if s["passed"] else "FAIL"
    detail = error or ", ".join(
        [f"missing tools {s['missing_tools']}"] * bool(s["missing_tools"])
        + [f"forbidden tools {s['forbidden_tools_called']}"] * bool(s["forbidden_tools_called"])
        + [f"missing facts {s['missing_facts']}"] * bool(s["missing_facts"])
        + [f"bad phrases {s['bad_phrases']}"] * bool(s["bad_phrases"])
    )
    tier = f" [{out['route']['tier']}]" if out.get("route") else ""
    print(f"[{i:>2}/{len(questions)}] {flag} {q['id']:<20} {seconds:>6}s{tier}  tools={out['tools_called']}  {detail}")
    if args.sleep and i < len(questions):
        time.sleep(args.sleep)

n = len(results)
summary = {
    "provider": args.provider,
    "label": label,
    "model": model,
    "data": "live" if args.live else "fixtures",
    "when": datetime.now().isoformat(timespec="seconds"),
    "questions": n,
    "passed": sum(r["passed"] for r in results),
    "tools_ok": sum(r["tools_ok"] for r in results),
    "facts_ok": sum(r["facts_ok"] for r in results),
    "errors": sum(r["error"] is not None for r in results),
    "guard_blocked": sum(1 for r in results if (r.get("guard") or {}).get("blocked")),
    "guard_flagged": sum(1 for r in results if (r.get("guard") or {}).get("events")),
    "routed_cheap": sum(1 for r in results if (r.get("route") or {}).get("tier") == "cheap"),
    "total_seconds": round(sum(r["seconds"] for r in results), 1),
    "avg_seconds": round(sum(r["seconds"] for r in results) / max(n, 1), 1),
    "input_tokens": sum(r["tokens"]["input"] for r in results),
    "output_tokens": sum(r["tokens"]["output"] for r in results),
}
print(f"\n{label}: {summary['passed']}/{n} passed | tools {summary['tools_ok']}/{n} | "
      f"facts {summary['facts_ok']}/{n} | errors {summary['errors']} | avg {summary['avg_seconds']}s")
print(f"guardrails: {summary['guard_blocked']} questions blocked, {summary['guard_flagged']} flagged "
      f"(on the normal question set both should be 0)")

out_dir = Path(__file__).resolve().parent / "results"
out_dir.mkdir(exist_ok=True)
path = out_dir / f"{label}-{datetime.now():%Y%m%d-%H%M%S}.json"
path.write_text(json.dumps({"summary": summary, "results": results}, indent=2), encoding="utf-8")
print(f"saved {path.relative_to(ROOT)}")
