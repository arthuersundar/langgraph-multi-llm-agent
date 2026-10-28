"""The logic behind the MCP tools. It does not import the MCP SDK, so it can be tested alone.

Every tool call passes through the same checks as the agent (app/guardrails.py), plus two that
only matter when the caller is somebody else's model:
  - a rate limit, so a looping client cannot hammer Cost Explorer (each request is billed) or S3
  - an audit line per call, written to STDERR (stdout belongs to the MCP protocol on stdio)

All tools are read-only. Nothing here can change AWS.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import guardrails  # noqa: E402
from aws_tools import REQUIRED_TAGS, find_untagged_resources, get_cost_by_service  # noqa: E402

DOCS = ROOT / "data" / "docs"
RATE_MESSAGE = "Rate limit reached: too many tool calls in the last minute. Wait a moment and try again."


# ----------------------------------------------------------------------------- rate limit
class RateLimiter:
    """At most max_calls in any window of per_seconds."""

    def __init__(self, max_calls: int, per_seconds: float = 60.0, clock=time.monotonic):
        self.max_calls, self.per_seconds, self.clock = max_calls, per_seconds, clock
        self.calls: deque = deque()

    def allow(self) -> bool:
        now = self.clock()
        while self.calls and now - self.calls[0] > self.per_seconds:
            self.calls.popleft()
        if len(self.calls) >= self.max_calls:
            return False
        self.calls.append(now)
        return True


LIMITER = RateLimiter(int(os.getenv("MCP_MAX_CALLS_PER_MIN", "60")))


# ----------------------------------------------------------------------------- audit
def audit(tool: str, args: dict, outcome: str, started: float) -> None:
    """One JSON line per call on stderr (and in MCP_AUDIT_LOG if set). Arguments are redacted."""
    safe = {k: (guardrails.redact(v)[0] if isinstance(v, str) else v) for k, v in args.items()}
    line = json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": tool, "args": safe,
                       "outcome": outcome, "ms": int((time.time() - started) * 1000)})
    print(line, file=sys.stderr, flush=True)
    path = os.getenv("MCP_AUDIT_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def _clean(tool: str, text: str) -> str:
    """Run a tool result through the tool-output guardrail (instruction removal, secret redaction)."""
    return guardrails.sanitize_tool_output(tool, text)[0] if guardrails.enabled() else text


def _gate(tool: str, args: dict, started: float, question: str | None = None):
    """Rate limit, then check the free-text argument. -> (refusal or None, cleaned question)."""
    if not LIMITER.allow():
        audit(tool, args, "rate_limited", started)
        return RATE_MESSAGE, question
    if question is not None and guardrails.enabled():
        verdict = guardrails.check_input(question)
        if verdict.blocked:
            audit(tool, args, "blocked:" + ",".join(verdict.findings), started)
            return verdict.text, question
        question = verdict.text  # PII already replaced by labels
    return None, question


# ----------------------------------------------------------------------------- the tools
def policy_search(question: str, top_k: int = 4) -> str:
    """Passages from the company policy library that best match the question (JSON)."""
    started = time.time()
    args = {"question": question, "top_k": top_k}
    refusal, question = _gate("policy_search", args, started, question[:500])
    if refusal:
        return refusal
    top_k = max(1, min(int(top_k), 8))
    try:
        from search import search_policies  # lazy: needs boto3, VECTOR_BUCKET and an embedding model
        hits = search_policies(question, top_k=top_k)
        out = _clean("policy_search", json.dumps(
            [{"title": h["title"], "section": h["section"], "text": h["text"]} for h in hits]))
    except Exception as e:  # a tool should say what failed, not crash the server
        audit("policy_search", args, f"error:{type(e).__name__}", started)
        return f"policy_search failed: {type(e).__name__}: {str(e)[:200]}"
    audit("policy_search", args, f"ok:{len(hits)}_chunks", started)
    return out


def cost_by_service(days: int = 30, top_n: int = 10) -> str:
    """Live AWS spend per service for the last N days, highest first (JSON)."""
    started = time.time()
    args = {"days": days, "top_n": top_n}
    refusal, _ = _gate("cost_by_service", args, started)
    if refusal:
        return refusal
    days, top_n = max(1, min(int(days), 365)), max(1, min(int(top_n), 25))
    try:
        out = _clean("cost_by_service", json.dumps(get_cost_by_service(days=days, top_n=top_n)))
    except Exception as e:
        audit("cost_by_service", args, f"error:{type(e).__name__}", started)
        return f"cost_by_service failed: {type(e).__name__}: {str(e)[:200]}"
    audit("cost_by_service", args, "ok", started)
    return out


def untagged_resources(missing_tag: str | None = None) -> str:
    """Resources missing required tags; optionally only those missing one specific tag (JSON)."""
    started = time.time()
    args = {"missing_tag": missing_tag}
    refusal, _ = _gate("untagged_resources", args, started)
    if refusal:
        return refusal
    if missing_tag is not None:
        missing_tag = missing_tag.strip().lower()
        if missing_tag not in REQUIRED_TAGS:
            audit("untagged_resources", args, "bad_argument", started)
            return f"missing_tag must be one of: {', '.join(REQUIRED_TAGS)}"
    try:
        out = _clean("untagged_resources", json.dumps(find_untagged_resources(missing_tag=missing_tag)))
    except Exception as e:
        audit("untagged_resources", args, f"error:{type(e).__name__}", started)
        return f"untagged_resources failed: {type(e).__name__}: {str(e)[:200]}"
    audit("untagged_resources", args, "ok", started)
    return out


# ----------------------------------------------------------------------------- documents
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")


def _front_matter(text: str) -> dict:
    meta = {}
    if text.startswith("---"):
        for line in text.split("---", 2)[1].splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    return meta


def list_documents() -> str:
    """Index of the policy library: one line per document."""
    rows = []
    for f in sorted(DOCS.glob("*.md")):
        if f.name.lower() == "readme.md":
            continue
        meta = _front_matter(f.read_text(encoding="utf-8"))
        rows.append(f"{f.stem} | {meta.get('doc_type', '?')} | {meta.get('title', f.stem)}")
    return "name | type | title\n" + "\n".join(rows)


def read_document(name: str) -> str:
    """The full text of one policy document. Only names from list_documents() are accepted."""
    if not _NAME.match(name or ""):
        return "Invalid document name. Use a name from policy://index."
    path = (DOCS / f"{name}.md").resolve()
    if path.parent != DOCS.resolve() or not path.is_file():
        return "No such document. Use a name from policy://index."
    return path.read_text(encoding="utf-8")


GOVERNANCE_REVIEW = """You are reviewing AWS cost and governance for Brightway{team}.
1. Call untagged_resources and list the resources missing tags, grouped by missing tag.
2. Call policy_search for the tagging policy and the remediation runbook, and quote them by title.
3. Call cost_by_service and name the three biggest services and anything unusual.
4. Finish with three prioritised, concrete recommendations. You are read-only: recommend, never claim to have changed anything."""


def review_prompt(team: str = "") -> str:
    team = re.sub(r"[^A-Za-z0-9 _-]", "", team)[:40].strip()
    return GOVERNANCE_REVIEW.format(team=f" for the {team} team" if team else "")
