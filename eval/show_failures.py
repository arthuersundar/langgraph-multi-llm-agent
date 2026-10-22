r"""Print every failed question from the newest results file, with the model's answer.
For policy questions it also shows what the policy search returned, to tell a retrieval
problem (right chunk not found) from a model problem (chunk found, answer ignored it).

Usage:  python eval\show_failures.py                 # newest results file
        python eval\show_failures.py vllm            # newest file for one provider
        python eval\show_failures.py --no-search     # skip the retrieval check
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

args = [a for a in sys.argv[1:] if not a.startswith("--")]
provider = args[0] if args else ""
files = sorted((Path(__file__).resolve().parent / "results").glob(f"{provider}*.json"))
if not files:
    raise SystemExit("No results files found.")
data = json.loads(files[-1].read_text(encoding="utf-8"))
questions = {q["id"]: q for q in json.loads((Path(__file__).resolve().parent / "questions.json").read_text(encoding="utf-8"))}
print(f"results: {files[-1].name}\n")

search = None
if "--no-search" not in sys.argv:
    try:
        from search import search_policies as search
    except Exception as e:  # e.g. no AWS credentials or Ollama not running
        print(f"(retrieval check unavailable: {type(e).__name__}: {e})\n")

for r in data["results"]:
    if r["passed"]:
        continue
    q = questions[r["id"]]
    print("=" * 78)
    print(f"{r['id']}  [{r['kind']}]  tools called: {r['tools_called']}")
    print("Q:", r["question"])
    print("Needed:", q.get("must_contain"), "| tools:", q.get("expect_tools"))
    print("A:", (r["answer"] or "(empty)").strip()[:700])
    if search and "search_policies_tool" in q.get("expect_tools", []):
        try:
            hits = search(r["question"], top_k=4)
            print("Search top 4:", [f"{h['title']} > {h['section']}" for h in hits])
        except Exception as e:
            print(f"Search failed: {type(e).__name__}: {e}")
    print()
