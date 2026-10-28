"""LangGraph agent: answers AWS cost/governance questions from company policies (RAG)
plus live AWS data, on either Bedrock or a self-hosted model.

Graph:   START -> agent -> (tool calls?) -> tools -> agent -> ... -> END

Usage:
    python app/agent.py "Which resources are missing required tags and what happens to them?"
    python app/agent.py --provider vllm "What were our top services last month?"
    python app/agent.py --provider auto "..."  # router: cheap model for easy questions, strong for hard
    python app/agent.py --sources "..."        # also show the policy chunks and live data it was given
    USE_FIXTURES=1 python app/agent.py "..."   # sample cost/tag data instead of live AWS
        (PowerShell: $env:USE_FIXTURES="1"; python app\agent.py "...")
    GUARDRAILS=0 python app/agent.py "..."     # switch the guardrails off (see app/guardrails.py)
"""
import json
import os
import sys

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

import guardrails
from aws_tools import find_untagged_resources, get_cost_by_service
from llm import get_llm
from router import choose, is_action, parse_spec
from search import search_policies

load_dotenv()

SYSTEM_PROMPT = """You are a cloud cost and governance assistant for Brightway.
You have two kinds of sources:
- search_policies: company policies, runbooks and postmortems (what the rules are, what to do).
- get_cost_by_service and find_untagged_resources: live AWS data (what is happening now).

Rules:
- Use a tool whenever the question depends on company policy or on current AWS data. Do not guess.
- For "what should we do" questions, combine both: look up the policy, then check the live data.
- Quote the policy by its document title. Report numbers exactly as the tools return them.
- Always state what the live-data tools returned (counts, amounts, resource ARNs), even when the result is zero or empty.
- If a tool returns an error or nothing relevant, say so plainly. Never invent figures or policies.
- You can only read. You cannot change AWS resources; recommend actions instead."""


def _safe(tool_name: str, text: str) -> str:
    """Guardrail layer 2: clean what a tool returns before the model reads it."""
    return guardrails.sanitize_tool_output(tool_name, text)[0] if guardrails.enabled() else text


@tool
def search_policies_tool(question: str) -> str:
    """Search company policies, runbooks, guides, standards, the team directory and
    postmortems. Returns the passages most relevant to the question."""
    # No doc_type filter is offered to the model: a wrong filter silently returns nothing.
    hits = search_policies(question[:500], top_k=4)  # cap the query length the model can send
    return _safe("search_policies_tool", json.dumps(
        [{"title": h["title"], "section": h["section"], "text": h["text"]} for h in hits]
    ))


@tool
def cost_by_service_tool(days: int = 30) -> str:
    """Live AWS spend per service for the last N days (default 30), highest first."""
    return _safe("cost_by_service_tool", json.dumps(get_cost_by_service(days=days)))


@tool
def untagged_resources_tool() -> str:
    """Live list of AWS resources missing any required tag (owner, team, cost-center,
    environment, application)."""
    return _safe("untagged_resources_tool", json.dumps(find_untagged_resources()))


# Guardrail in code, not in the model: a request to change AWS is always answered with this
# first, because small models often skip the refusal. Set GUARD_ACTIONS=0 to see the raw model.
ACTION_REFUSAL = ("I can't delete or change AWS resources: this assistant has read-only access. "
                  "What I can do is tell you what the policies say should happen:\n\n")

TOOLS = [search_policies_tool, cost_by_service_tool, untagged_resources_tool]


def build_graph(provider: str | None = None, model: str | None = None):
    llm = get_llm(provider or os.getenv("LLM_PROVIDER", "bedrock"), model).bind_tools(TOOLS)

    def agent(state: MessagesState):
        return {"messages": [llm.invoke([SystemMessage(SYSTEM_PROMPT)] + state["messages"])]}

    g = StateGraph(MessagesState)
    g.add_node("agent", agent)
    g.add_node("tools", ToolNode(TOOLS, handle_tool_errors=True))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition)  # tool call -> "tools", else END
    g.add_edge("tools", "agent")
    return g.compile()


def show_result(msg, detail: bool) -> None:
    """Print what a tool handed back to the model. With detail=True the policy chunks are
    listed by title and opening text, and live data is shown in full, so you can see exactly
    what the model was given before it wrote its answer."""
    content = str(msg.content)
    if not detail:
        print(f"  <- {msg.name}: {content[:120]}...")
        return
    print(f"  <- {msg.name} returned:")
    try:
        data = json.loads(content)
    except ValueError:
        print("     " + content[:600])
        return
    if msg.name == "search_policies_tool" and isinstance(data, list):
        for hit in data:
            body = hit["text"].split("\n\n", 1)[-1].replace("\n", " ")
            print(f"     [policy] {hit['title']} > {hit['section']}: {body[:140]}...")
    else:
        print("     [live data] " + json.dumps(data)[:900])


def run(question: str, provider: str | None = None, graph=None, verbose: bool = False,
        detail: bool = False) -> dict:
    """Run one question. Returns the answer plus what the eval needs: which tools were
    called, how many model calls it took, and token usage when the provider reports it."""
    graph = graph or build_graph(provider)
    answer, tools_called, model_calls = "", [], 0
    tokens = {"input": 0, "output": 0}
    asked, echo = question, []
    guardrails.drain()
    if guardrails.enabled():  # layer 1: block attacks, redact PII, before any model call
        verdict = guardrails.check_input(question)
        guardrails.record("input", verdict.action, verdict.findings)
        if verdict.blocked:
            if verbose:
                print(f"  guardrails: blocked ({', '.join(verdict.findings)})")
            return {"answer": verdict.text, "tools_called": [], "model_calls": 0, "tokens": tokens,
                    "guard": {"blocked": True, "events": guardrails.drain()}}
        question, echo = verdict.text, verdict.originals
    for step in graph.stream(
        {"messages": [HumanMessage(question)]},
        {"recursion_limit": 12},  # caps the tool loop so a confused model cannot spin forever
        stream_mode="updates",
    ):
        for node, update in step.items():
            # A step can carry several messages (parallel tool calls), so look at all of them.
            for msg in update["messages"]:
                if node == "agent":
                    model_calls += 1
                    usage = getattr(msg, "usage_metadata", None) or {}
                    tokens["input"] += usage.get("input_tokens", 0)
                    tokens["output"] += usage.get("output_tokens", 0)
                    for call in getattr(msg, "tool_calls", []) or []:
                        tools_called.append(call["name"])
                        if verbose:
                            print(f"  -> {call['name']}({call['args']})")
                    if not getattr(msg, "tool_calls", None):
                        answer = msg.content if isinstance(msg.content, str) else str(msg.content)
                elif verbose:
                    show_result(msg, detail)
    if guardrails.enabled():  # layer 3: nothing sensitive or secret leaves in the answer
        answer = guardrails.check_output(answer, protected=[SYSTEM_PROMPT], echo=echo).text
    if os.getenv("GUARD_ACTIONS", "1") != "0" and (is_action(asked) or (
            guardrails.enabled() and guardrails.asks_for_change(asked))):
        answer = ACTION_REFUSAL + answer
    events = guardrails.drain()
    if verbose and events:
        print(f"  guardrails: {events}")
    return {"answer": answer, "tools_called": tools_called, "model_calls": model_calls, "tokens": tokens,
            "guard": {"blocked": False, "events": events}}


_routed_graphs: dict = {}


def run_routed(question: str, verbose: bool = False, detail: bool = False) -> dict:
    """Pick the cheap or the strong model for this question (see router.py), then run it."""
    route = choose(question)
    spec = route["spec"]
    if spec not in _routed_graphs:  # build each model's graph once and reuse it
        _routed_graphs[spec] = build_graph(*parse_spec(spec))
    if verbose:
        print(f"  route: {route['tier']} -> {spec}  ({route['reason']})")
    out = run(question, graph=_routed_graphs[spec], verbose=verbose, detail=detail)
    out["route"] = route
    return out


def ask(question: str, provider: str | None = None, verbose: bool = True, detail: bool = False) -> str:
    if provider == "auto":
        return run_routed(question, verbose=verbose, detail=detail)["answer"]
    return run(question, provider, verbose=verbose, detail=detail)["answer"]


if __name__ == "__main__":
    args = sys.argv[1:]
    provider = None
    detail = "--sources" in args  # show what each tool returned, not just that it ran
    args = [a for a in args if a != "--sources"]
    if "--provider" in args:
        i = args.index("--provider")
        provider = args[i + 1]
        args = args[:i] + args[i + 2:]
    q = " ".join(args) or "Which resources are missing required tags and what is the policy for fixing them?"
    print(f"Q: {q}\n")
    try:
        print("\nA:", ask(q, provider, detail=detail))
    except Exception as e:
        if "ThrottlingException" in f"{type(e).__name__} {e}":
            sys.exit("\nBedrock daily token quota reached (account limit, not a code bug).\n"
                     "Retry later, or run on the local model: python app/agent.py --provider vllm \"...\"")
        raise
