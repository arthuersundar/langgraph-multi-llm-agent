"""Question router: send easy questions to a cheap model and hard ones to a stronger model.

Rules (cheap, instant, no extra model call). A question is HARD when it needs more than one
step of reasoning, because that is where the small models failed in our eval:
  - it needs BOTH policy documents and live AWS data
  - it needs arithmetic or a comparison (totals, "within", "higher than")
  - it asks the assistant to take an action (delete, stop, change): a safe refusal is needed
  - it is about something outside our data (other clouds)
  - it is long or asks several questions at once
Everything else is SIMPLE: a lookup in one source.

Which models fill the two tiers is configuration, as "provider:model":
  ROUTER_CHEAP=vllm:llama3.2:3b          ROUTER_STRONG=vllm:qwen2.5:7b
  ROUTER_CHEAP=bedrock:us.amazon.nova-micro-v1:0   ROUTER_STRONG=bedrock:us.amazon.nova-pro-v1:0
  ROUTER_POLICY_TO=cheap|strong          where simple POLICY-only questions go (default cheap)
"""
import os
import re

LIVE = re.compile(
    r"\b(currently|right now|today|this month|last \d+ days|last (week|month|quarter)|"
    r"how many resources|which resources|list the resources|how much|spend|spent|"
    r"total aws|cost drivers|top services)\b|missing (the )?[\w-]+ tags?", re.I)
POLICY = re.compile(
    r"\b(policy|policies|runbook|guide|guides|postmortem|incident|grace period|budgets?|alerts?|"
    r"savings plans?|team lead|how must|how do i|what should|what do our|allowed)\b", re.I)
COMPUTE = re.compile(
    r"\b(total|sum|combined|compare|comparison|within|exceed|versus|vs|trend|percent|"
    r"difference|higher than|lower than|more than|less than|over budget)\b", re.I)
ACTION = re.compile(r"^\s*(please\s+)?(delete|remove|terminate|stop|shut|destroy|change|modify|tag|fix|apply)\b", re.I)
OUT_OF_SCOPE = re.compile(r"\b(azure|gcp|google cloud|oracle|alibaba)\b", re.I)


def is_action(question: str) -> bool:
    """True when the user is asking the assistant to change something (delete, stop, ...)."""
    return bool(ACTION.search(question))


def classify(question: str) -> tuple[str, str]:
    """-> (tier, reason). tier is 'hard' or 'simple'."""
    q = question.strip()
    live, policy = bool(LIVE.search(q)), bool(POLICY.search(q))
    if ACTION.search(q):
        return "hard", "asks for an action"
    if OUT_OF_SCOPE.search(q):
        return "hard", "outside our data"
    if live and policy:
        return "hard", "needs policy documents and live data"
    if COMPUTE.search(q):
        return "hard", "needs a calculation or comparison"
    if len(q.split()) > 40 or q.count("?") > 1:
        return "hard", "long or multi-part"
    if policy and not live:
        return "simple-policy", "single lookup in the policy documents"
    return "simple", "single lookup in live data" if live else "short single-source question"


def parse_spec(spec: str) -> tuple[str, str]:
    """'vllm:qwen2.5:7b' -> ('vllm', 'qwen2.5:7b')"""
    provider, _, model = spec.partition(":")
    return provider, model


def choose(question: str) -> dict:
    """-> {'tier': 'cheap'|'strong', 'spec': 'provider:model', 'reason': str}"""
    tier, reason = classify(question)
    cheap = os.getenv("ROUTER_CHEAP", "vllm:llama3.2:3b")
    strong = os.getenv("ROUTER_STRONG", "vllm:qwen2.5:7b")
    if tier == "hard" or (tier == "simple-policy" and os.getenv("ROUTER_POLICY_TO", "cheap") == "strong"):
        return {"tier": "strong", "spec": strong, "reason": reason}
    return {"tier": "cheap", "spec": cheap, "reason": reason}
