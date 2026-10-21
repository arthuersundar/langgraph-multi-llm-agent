"""Pure scoring helpers for the eval (no model or AWS calls, so they are easy to test)."""
import re


def _hit(text: str, pattern: str) -> bool:
    """A pattern is plain text (case-insensitive) or 're:<regex>'."""
    if pattern.startswith("re:"):
        return re.search(pattern[3:], text, re.I) is not None
    return pattern.lower() in text.lower()


def score(q: dict, answer: str, tools_called: list[str], error: str | None = None) -> dict:
    """Three independent checks:
    - tools_ok: every expected tool was called and no forbidden tool was.
    - facts_ok: each group in must_contain has at least one match, and nothing in
      must_not_contain appears.
    - ran_ok: the run finished without an error and produced an answer.
    """
    called = set(tools_called)
    missing_tools = [t for t in q.get("expect_tools", []) if t not in called]
    bad_tools = [t for t in q.get("forbid_tools", []) if t in called]
    missing_facts = [g for g in q.get("must_contain", []) if not any(_hit(answer, p) for p in g)]
    bad_phrases = [p for p in q.get("must_not_contain", []) if _hit(answer, p)]
    ran_ok = error is None and bool(answer.strip())
    tools_ok = not missing_tools and not bad_tools
    facts_ok = not missing_facts and not bad_phrases
    return {
        "ran_ok": ran_ok, "tools_ok": tools_ok, "facts_ok": facts_ok,
        "passed": ran_ok and tools_ok and facts_ok,
        "missing_tools": missing_tools, "forbidden_tools_called": bad_tools,
        "missing_facts": missing_facts, "bad_phrases": bad_phrases,
    }
