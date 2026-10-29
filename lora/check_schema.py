"""Fail if TOOLS in schema.py no longer matches the @tool functions in app/agent.py
(names, argument names, docstrings). Run it after any change to the agent's tools.

    python lora/check_schema.py
"""
import ast
import re
import sys

from schema import AGENT_PY, TOOLS


def agent_tools() -> dict:
    tree = ast.parse(AGENT_PY.read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and any(getattr(d, "id", "") == "tool" for d in node.decorator_list):
            doc = re.sub(r"\s+", " ", ast.get_docstring(node) or "").strip()
            found[node.name] = {"args": [a.arg for a in node.args.args], "doc": doc}
    return found


def main() -> int:
    real, problems = agent_tools(), []
    ours = {t["function"]["name"]: t["function"] for t in TOOLS}
    if set(real) != set(ours):
        problems.append(f"tool names differ: agent={sorted(real)} schema={sorted(ours)}")
    for name, fn in ours.items():
        if name not in real:
            continue
        if list(fn["parameters"]["properties"]) != real[name]["args"]:
            problems.append(f"{name}: arguments differ: {real[name]['args']} vs {list(fn['parameters']['properties'])}")
        if re.sub(r"\s+", " ", fn["description"]).strip() != real[name]["doc"]:
            problems.append(f"{name}: description differs from the docstring")
    for p in problems:
        print("MISMATCH:", p)
    print("schema matches app/agent.py" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
