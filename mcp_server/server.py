"""MCP server: AWS cost and governance tools for any MCP client (Claude Desktop, Claude Code, ...).

Tools (all read-only):  policy_search, cost_by_service, untagged_resources
Resources:              policy://index and policy://{name}  (the 15 policy documents)
Prompt:                 governance_review

Run:
    python mcp_server/server.py                        # stdio, for Claude Desktop / Claude Code
    MCP_TRANSPORT=streamable-http python mcp_server/server.py   # HTTP, for remote clients
    USE_FIXTURES=1 ...                                  # sample AWS data instead of your account
Test without any client:  python mcp_server/client_demo.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:  # mcp 2.x renamed FastMCP to MCPServer; support both
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as Server

import gov_tools as t  # noqa: E402

mcp = Server(
    "aws-governance",
    instructions=("Brightway AWS cost and governance. Use policy_search for what the company rules are, "
                  "cost_by_service and untagged_resources for what is happening in AWS now. "
                  "Everything is read-only; you cannot change AWS."),
)


def read_only_tool(description: str):
    """Register a tool and mark it read-only for clients that show tool annotations."""
    try:
        from mcp.types import ToolAnnotations
        return mcp.tool(description=description,
                        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    except (ImportError, TypeError):  # older SDK without annotations
        return mcp.tool(description=description)


@read_only_tool("Search Brightway's policies, runbooks, guides, standards, team directory and postmortems. "
                "Returns the most relevant passages as JSON (title, section, text). Use it for what the rules are.")
def policy_search(question: str, top_k: int = 4) -> str:
    return t.policy_search(question, top_k)


@read_only_tool("Live AWS spend per service for the last N days (default 30, max 365), highest first, as JSON. "
                "Use it for what is being spent now.")
def cost_by_service(days: int = 30, top_n: int = 10) -> str:
    return t.cost_by_service(days, top_n)


@read_only_tool("Live list of AWS resources missing required tags (owner, team, cost-center, environment, application). "
                "Pass missing_tag to list only resources missing that one tag; the filtering is done here, not by you.")
def untagged_resources(missing_tag: str | None = None) -> str:
    return t.untagged_resources(missing_tag)


@mcp.resource("policy://index", description="Index of the policy library: name, type and title of each document")
def policy_index() -> str:
    return t.list_documents()


@mcp.resource("policy://{name}", description="Full text of one policy document (names come from policy://index)")
def policy_document(name: str) -> str:
    return t.read_document(name)


@mcp.prompt(description="Review untagged resources, policy and spend, then recommend three actions")
def governance_review(team: str = "") -> str:
    return t.review_prompt(team)


if __name__ == "__main__":
    mcp.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))
