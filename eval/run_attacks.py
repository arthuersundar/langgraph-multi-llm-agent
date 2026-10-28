"""Attack suite for the guardrails: do attacks get through, and are normal questions left alone?

Offline (no model, instant, deterministic): checks the INPUT and TOOL layers only.
    python eval/run_attacks.py --offline

With a model: runs every attack through the whole agent, guardrails on or off, and counts how
many attacks SUCCEEDED (see eval/attack_scoring.py). Run it twice and compare:
    python eval/run_attacks.py --provider vllm --guardrails off --label vllm-raw
    python eval/run_attacks.py --provider vllm --guardrails on  --label vllm-guarded
    python eval/run_attacks.py --provider vllm --guardrails on  --benign   # also run the look-alike questions

Results are saved in eval/results/attacks/ (a separate folder so eval/report.py ignores them).
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(HERE))

ap = argparse.ArgumentParser()
ap.add_argument("--provider", help="bedrock, vllm, gateway or auto (not needed with --offline)")
ap.add_argument("--guardrails", choices=["on", "off"], default="on")
ap.add_argument("--offline", action="store_true", help="test the input and tool layers only, no model")
ap.add_argument("--benign", action="store_true", help="with a model: also run the look-alike questions")
ap.add_argument("--only", help="run a single attack id")
ap.add_argument("--label", help="name for this run")
ap.add_argument("--sleep", type=float, default=0)
args = ap.parse_args()
if not args.offline and not args.provider:
    ap.error("--provider is required unless --offline")

os.environ["USE_FIXTURES"] = "1"
os.environ["GUARDRAILS"] = "1" if (args.offline or args.guardrails == "on") else "0"

import guardrails  # noqa: E402
from attack_scoring import succeeded  # noqa: E402

attacks = json.loads((HERE / "attacks.json").read_text(encoding="utf-8"))
benign = json.loads((HERE / "benign.json").read_text(encoding="utf-8"))
normal = [{"id": q["id"], "prompt": q["question"]} for q in
          json.loads((HERE / "questions.json").read_text(encoding="utf-8"))]
if args.only:
    attacks = [a for a in attacks if a["id"] == args.only]


def table(title, rows):
    print(f"\n{title}")
    print(f"  {'category':<20} {'attacks':>7} {'stopped':>8} {'got through':>12}")
    for cat, (n, stopped) in sorted(rows.items()):
        print(f"  {cat:<20} {n:>7} {stopped:>8} {n - stopped:>12}")
    n = sum(v[0] for v in rows.values()); s = sum(v[1] for v in rows.values())
    print(f"  {'TOTAL':<20} {n:>7} {s:>8} {n - s:>12}")


# ---------------------------------------------------------------------------------------- offline
if args.offline:
    rows = defaultdict(lambda: [0, 0])
    missed = []
    for a in attacks:
        cat = a["category"]
        if cat == "resource_abuse":
            continue  # only visible in a real run (number of model calls)
        rows[cat][0] += 1
        if a.get("fixture"):  # indirect: does the tool layer strip the poisoned tag value?
            os.environ["FIXTURE_VARIANT"] = a["fixture"]
            from aws_tools import find_untagged_resources
            raw = json.dumps(find_untagged_resources())
            clean, findings = guardrails.sanitize_tool_output("untagged_resources_tool", raw)
            stopped = "BANANA-7731" not in clean and bool(findings)
            os.environ.pop("FIXTURE_VARIANT", None)
        elif cat == "action_request":
            from router import is_action
            stopped = is_action(a["prompt"]) or guardrails.asks_for_change(a["prompt"])
        else:
            v = guardrails.check_input(a["prompt"])
            stopped = v.action in ("block", "redact")
        rows[cat][1] += stopped
        if not stopped:
            missed.append(a["id"])
    table("Attacks stopped by the input / tool / action layers (offline)", {k: tuple(v) for k, v in rows.items()})
    if missed:
        print("  missed:", ", ".join(missed))
    fp = [b for b in benign + normal if guardrails.check_input(b["prompt"]).action != "allow"]
    print(f"\nLook-alike and normal questions wrongly blocked or redacted: {len(fp)}/{len(benign) + len(normal)}")
    for b in fp:
        print("  ", b["id"], b["prompt"][:80])
    print("\nNot testable offline: system-prompt leaks and runaway tool loops (run with a model).")
    sys.exit(1 if fp else 0)

# ------------------------------------------------------------------------------------------ model
from agent import SYSTEM_PROMPT, build_graph, run, run_routed  # noqa: E402

if args.provider == "bedrock":
    model = os.getenv("BEDROCK_MODEL", "bedrock")
elif args.provider == "gateway":
    model = "via-gateway-" + os.getenv("GATEWAY_BACKEND", "bedrock")
elif args.provider == "auto":
    model = "router-{}-{}".format(os.getenv("ROUTER_CHEAP", "vllm:llama3.2:3b").split(":", 1)[-1],
                                  os.getenv("ROUTER_STRONG", "vllm:qwen2.5:7b").split(":", 1)[-1])
else:
    model = os.getenv("VLLM_MODEL", args.provider)   # the model that really answers, not the label
label = args.label or f"{args.provider}-{model}-guardrails-{args.guardrails}"
label = "".join(c if c.isalnum() or c in "-._" else "_" for c in label)
print(f"attack run: {label}  | MODEL: {model} | guardrails {args.guardrails}\n")
graph = None if args.provider == "auto" else build_graph(args.provider)


def ask(prompt):
    return run_routed(prompt) if args.provider == "auto" else run(prompt, graph=graph)


results = []
for i, a in enumerate(attacks, 1):
    if a.get("fixture"):
        os.environ["FIXTURE_VARIANT"] = a["fixture"]
    t0 = time.time()
    error = None
    out = {"answer": "", "tools_called": [], "model_calls": 0, "guard": {"blocked": False, "events": []}}
    try:
        out = ask(a["prompt"])
    except Exception as e:
        error = f"{type(e).__name__}: {str(e)[:200]}"
    os.environ.pop("FIXTURE_VARIANT", None)
    bad, why = succeeded(a, out["answer"], out["tools_called"], out["model_calls"], SYSTEM_PROMPT)
    g = out.get("guard") or {}
    if g.get("blocked"):
        stopped_by = "guardrail:blocked"
    elif g.get("events") and not bad:
        stopped_by = "guardrail:cleaned"   # PII redacted or tool data sanitized before the model read it
    else:
        stopped_by = "model" if not bad else "-"
    results.append({"id": a["id"], "category": a["category"], "prompt": a["prompt"], "answer": out["answer"],
                    "tools_called": out["tools_called"], "model_calls": out["model_calls"],
                    "guard": out.get("guard"), "error": error, "attack_succeeded": bad, "why": why,
                    "seconds": round(time.time() - t0, 1)})
    print(f"[{i:>2}/{len(attacks)}] {'ATTACK SUCCEEDED' if bad else 'held           '} {a['id']:<4} "
          f"{a['category']:<18} stopped_by={stopped_by:<18} {error or why}")
    if bad:
        print("        answer:", out["answer"][:140].replace("\n", " "))
    if args.sleep:
        time.sleep(args.sleep)

rows = defaultdict(lambda: [0, 0])
for r in results:
    rows[r["category"]][0] += 1
    rows[r["category"]][1] += not r["attack_succeeded"]
table(f"{label}: attacks that did NOT get through", {k: tuple(v) for k, v in rows.items()})

benign_results = []
if args.benign:
    print("\nLook-alike questions (should be answered normally):")
    for b in benign:
        out = ask(b["prompt"])
        blocked = bool((out.get("guard") or {}).get("blocked"))
        benign_results.append({"id": b["id"], "prompt": b["prompt"], "blocked": blocked, "answer": out["answer"]})
        print(f"  {'BLOCKED' if blocked else 'ok     '} {b['id']:<4} {b['prompt'][:70]}")
    print(f"  wrongly blocked: {sum(r['blocked'] for r in benign_results)}/{len(benign_results)}")

n = len(results)
summary = {"label": label, "provider": args.provider, "model": model, "guardrails": args.guardrails,
           "when": datetime.now().isoformat(timespec="seconds"), "attacks": n,
           "succeeded": sum(r["attack_succeeded"] for r in results),
           "blocked_by_guardrail": sum(bool((r["guard"] or {}).get("blocked")) for r in results),
           "errors": sum(r["error"] is not None for r in results),
           "benign_blocked": sum(r["blocked"] for r in benign_results), "benign_total": len(benign_results)}
print(f"\n{label} (model {model}): {summary['succeeded']}/{n} attacks succeeded | {summary['blocked_by_guardrail']} blocked "
      f"before the model | errors {summary['errors']}")
out_dir = HERE / "results" / "attacks"
out_dir.mkdir(parents=True, exist_ok=True)
path = out_dir / f"{label}-{datetime.now():%Y%m%d-%H%M%S}.json"
path.write_text(json.dumps({"summary": summary, "results": results, "benign": benign_results}, indent=2), encoding="utf-8")
print(f"saved {path.relative_to(ROOT)}")
