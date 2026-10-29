"""The agent's system prompt and tool schemas, exactly as the model sees them.

SYSTEM_PROMPT is read out of app/agent.py (parsed, not imported, so no LangChain is needed),
so training data can never drift from the prompt the agent really uses. TOOLS mirrors the
three @tool functions in app/agent.py; check_schema.py fails if they ever disagree.
"""
import ast
from pathlib import Path

AGENT_PY = Path(__file__).resolve().parent.parent / "app" / "agent.py"


def load_system_prompt() -> str:
    tree = ast.parse(AGENT_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "SYSTEM_PROMPT" for t in node.targets):
            return ast.literal_eval(node.value)
    raise RuntimeError("SYSTEM_PROMPT not found in app/agent.py")


SYSTEM_PROMPT = load_system_prompt()

TOOLS = [
    {"type": "function", "function": {
        "name": "search_policies_tool",
        "description": ("Search company policies, runbooks, guides, standards, the team directory and\n"
                        "    postmortems. Returns the passages most relevant to the question."),
        "parameters": {"type": "object", "properties": {"question": {"type": "string"}},
                       "required": ["question"]}}},
    {"type": "function", "function": {
        "name": "cost_by_service_tool",
        "description": "Live AWS spend per service for the last N days (default 30), highest first.",
        "parameters": {"type": "object", "properties": {"days": {"type": "integer", "default": 30}}}}},
    {"type": "function", "function": {
        "name": "untagged_resources_tool",
        "description": ("Live list of AWS resources missing any required tag (owner, team, cost-center,\n"
                        "    environment, application)."),
        "parameters": {"type": "object", "properties": {}}}},
]
TOOL_NAMES = [t["function"]["name"] for t in TOOLS]
