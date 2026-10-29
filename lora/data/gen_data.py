"""Build the synthetic training set for the governance agent's tool-calling behaviour.

Every example is a full agent trajectory in chat format: system prompt, user question,
tool calls, tool results (real policy chunks, randomized live data), final answer. The
final answers are built from the same facts the tool results contain, so they are correct
by construction; tests check this (lora/test_data.py).

    python lora/data/gen_data.py            # writes lora/data/train.jsonl and val.jsonl
    python lora/data/gen_data.py --seed 7 --per-card 4
"""
import argparse
import json
import random
import string
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import facts as F  # noqa: E402
import synth_live as S  # noqa: E402
from chunks import hit, load_chunks  # noqa: E402
from schema import SYSTEM_PROMPT  # noqa: E402


# ---------------------------------------------------------------- message helpers
def _id(rng):
    return "call_" + "".join(rng.choice(string.ascii_lowercase + string.digits) for _ in range(9))


def call_msg(cid, name, args):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": args}}]}


def tool_msg(cid, name, payload):
    return {"role": "tool", "tool_call_id": cid, "name": name, "content": json.dumps(payload)}


def step(rng, name, args, payload):
    cid = _id(rng)
    return [call_msg(cid, name, args), tool_msg(cid, name, payload)]


def decorate(rng, q):
    """Light noise so the model does not depend on exact phrasing."""
    r = rng.random()
    if r < 0.08:
        return rng.choice(["Quick question: ", "Hi, ", "Hey - ", "Can you check: "]) + q[0].lower() + q[1:]
    if r < 0.14:
        return q + rng.choice([" Thanks!", " Please be brief.", " Short answer is fine."])
    return q


class Pool:
    def __init__(self):
        self.all = load_chunks()
        self.allowed = [c for c in self.all if c["doc"] not in F.HELD_OUT_DOCS
                        and (c["doc"], c["section"]) not in F.HELD_OUT_SECTIONS]
        self.by_key = {(c["doc"], c["section"]): c for c in self.all}

    def results(self, rng, target_keys, k=4):
        """What search_policies returns: the target chunk(s) among other chunks, as the retriever would."""
        targets = [self.by_key[t] for t in target_keys]
        others = rng.sample([c for c in self.allowed if c not in targets], k - len(targets))
        rows = targets + others
        if rng.random() < 0.35:
            rng.shuffle(rows)
        return [hit(c) for c in rows]

    def misses(self, rng, k=4):
        return [hit(c) for c in rng.sample(self.allowed, k)]


def record(family, rng, user, steps, final, **meta):
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
    for s in steps:
        msgs += s
    msgs.append({"role": "assistant", "content": final})
    return {"family": family, "messages": msgs, "meta": meta}


# ---------------------------------------------------------------- policy
def policy_examples(rng, pool, per_card):
    out = []
    for (doc, section), pairs in F.CARDS.items():
        for q, a in pairs:
            for _ in range(per_card):
                user = decorate(rng, q)
                res = pool.results(rng, [(doc, section)])
                out.append(record("policy", rng, user, [step(rng, "search_policies_tool", {"question": q}, res)],
                                  a, target=[doc, section]))
    return out


def miss_examples(rng, pool, per_q):
    out = []
    for q, topic in F.MISSES:
        for _ in range(per_q):
            res = pool.misses(rng)
            titles = sorted({r["title"] for r in res})
            ans = (f"The policy library has nothing about {topic}. The search only returned passages from "
                   f"{', '.join(titles)}, none of which cover it, so I won't guess.")
            out.append(record("miss", rng, decorate(rng, q), [step(rng, "search_policies_tool", {"question": q}, res)], ans))
    return out


# ---------------------------------------------------------------- live: cost
DAYS = [(7, ["in the last week", "over the past 7 days", "last week"]),
        (14, ["over the last 14 days", "in the past two weeks"]),
        (30, ["in the last 30 days", "over the past month", "this past month", "over the last 30 days"]),
        (90, ["over the last 90 days", "in the past quarter"])]


def cost_examples(rng, n):
    out = []
    for i in range(n):
        d, phrases = rng.choice(DAYS)
        when = rng.choice(phrases)
        kind = rng.choice(["topn", "service", "service", "total", "share", "absent", "compare"])
        short = rng.choice(list(S.SERVICES))
        cost = S.make_cost(rng, d, must_include=short if kind in ("service", "share", "compare") else None)
        rows, total = cost["top_services"], cost["total_usd"]
        full = S.SERVICES[short]
        if kind == "topn":
            k = rng.choice([3, 3, 5])
            user = rng.choice([f"Which {k} services cost us the most {when}?",
                               f"Rank our {k} priciest AWS services {when}.",
                               f"Give me the {k} biggest AWS cost drivers {when}."])
            lines = "\n".join(f"{j}. {r['service']}: {S.money(r['usd'])}" for j, r in enumerate(rows[:k], 1))
            ans = f"Over the last {d} days, total AWS spend was {S.money(total)}. The top {k} services were:\n{lines}"
        elif kind == "service":
            row = next(r for r in rows if r["service"] == full)
            user = rng.choice([f"Tell me what we paid for {short} {when}.", f"What did {short} cost us {when}?",
                               f"What was our {short} bill {when}?"])
            ans = (f"{full} cost {S.money(row['usd'])} over the last {d} days, "
                   f"{S.share(cost, full)}% of the {S.money(total)} total.")
        elif kind == "total":
            user = rng.choice([f"What was our total AWS spend {when}?", f"How much have we spent on AWS {when}?",
                               f"Total cloud bill {when}?"])
            ans = f"Total AWS spend over the last {d} days was {S.money(total)}. The largest service was {rows[0]['service']} at {S.money(rows[0]['usd'])}."
        elif kind == "share":
            user = f"What share of our AWS bill is {short} {when}?"
            ans = (f"{full} was {S.money(next(r for r in rows if r['service'] == full)['usd'])} of the "
                   f"{S.money(total)} total over the last {d} days: {S.share(cost, full)}%.")
        elif kind == "absent":
            missing = rng.choice(S.NOT_PRESENT)
            user = rng.choice([f"What is our {missing} cost {when}?", f"Did we pay for {missing} {when}?"])
            if len(rows) >= 10:
                ans = (f"{missing} is not among the top {len(rows)} services the cost tool returned for the last {d} days, "
                       f"so I have no figure for it. Its spend, if any, is below {S.money(rows[-1]['usd'])}, the smallest amount listed ({rows[-1]['service']}).")
            else:
                ans = (f"{missing} does not appear in the cost data for the last {d} days, "
                       f"so it shows no spend in this period. Total AWS spend was {S.money(total)}.")
        else:
            other = rng.choice([s for s in S.SERVICES if S.SERVICES[s] in {r['service'] for r in rows} and s != short] or [None])
            if other is None:
                continue
            user = f"Is {short} more expensive than {other} {when}?"
            a_, b_ = (next(r['usd'] for r in rows if r['service'] == S.SERVICES[x]) for x in (short, other))
            hi, lo = (short, other) if a_ >= b_ else (other, short)
            ans = (f"{hi} cost more: {short} was {S.money(a_)} and {other} was {S.money(b_)} over the last {d} days, "
                   f"a difference of {S.money(abs(a_ - b_))}.")
        out.append(record("cost", rng, decorate(rng, user),
                          [step(rng, "cost_by_service_tool", {"days": d}, cost)], ans, kind=kind, days=d))
    return out


# ---------------------------------------------------------------- live: untagged
def untagged_examples(rng, n):
    out = []
    for i in range(n):
        kind = rng.choice(["count", "count", "tag", "tag", "tag", "worst", "all"])
        data = S.make_untagged(rng)
        tag = rng.choice(S.TAGS)
        rows, cnt = data["resources"], data["non_compliant"]
        if kind == "count":
            user = rng.choice(["What is the number of resources lacking at least one required tag?", "How many of our resources are not fully tagged?",
                               "Count the non-compliant resources right now."])
            ans = (f"{cnt} resources are missing at least one required tag, out of {data['scanned']} scanned in {data['region']}."
                   if cnt else f"None. The tool scanned {data['scanned']} resources in {data['region']} and found 0 missing a required tag.")
        elif kind == "tag":
            user = rng.choice([f"Which resources are missing the {tag} tag?", f"List everything without a {tag} tag.",
                               f"What's missing the {tag} tag right now?"])
            sub = S.missing_tag(data, tag)
            if sub:
                ans = f"{len(sub)} of the {cnt} non-compliant resources are missing the `{tag}` tag:\n" + "\n".join(S.bullet(r) for r in sub)
            else:
                ans = (f"No resource is missing the `{tag}` tag. The tool returned {cnt} non-compliant resources, "
                       f"and none of them lack it.")
        elif kind == "worst":
            user = "Which resource is missing the most required tags?"
            if not rows:
                ans = "None: the tool returned 0 resources missing a required tag."
            else:
                top = max(len(r["missing"]) for r in rows)
                best = [r for r in rows if len(r["missing"]) == top]
                ans = (f"The most tags missing on any one resource is {top}"
                       + (f" ({len(best)} resources tie)" if len(best) > 1 else "") + ":\n"
                       + "\n".join(S.bullet(r) for r in best))
        else:
            user = rng.choice(["List all resources with missing tags.", "Show me every untagged resource and what each is missing."])
            ans = (f"{cnt} resources are missing at least one required tag:\n" + "\n".join(S.bullet(r) for r in rows)
                   if rows else "The tool returned 0 resources missing a required tag.")
        out.append(record("untagged", rng, decorate(rng, user), [step(rng, "untagged_resources_tool", {}, data)], ans, kind=kind, tag=tag))
    return out


# ---------------------------------------------------------------- combined (policy + live)
def combined_examples(rng, pool, n):
    out = []
    kinds = ["s3cost", "lambda", "bedrock", "s3bucket", "envtag", "anomaly"]
    for i in range(n):
        kind = kinds[i % len(kinds)]
        policy_first = rng.random() < 0.4
        if kind == "s3cost":
            cost = S.make_cost(rng, 30, "S3")
            row = next(r for r in cost["top_services"] if r["service"] == S.SERVICES["S3"])
            user = rng.choice(["S3 costs feel high. How much was it this month, and which lifecycle rules should our buckets have?",
                               "What did S3 cost us over the last 30 days, and what lifecycle rules are mandatory?"])
            live = ("cost_by_service_tool", {"days": 30}, cost)
            pol = ("search_policies_tool", {"question": "mandatory S3 bucket lifecycle rules"}, pool.results(rng, [("06", "Mandatory lifecycle rules")]))
            ans = (f"{S.SERVICES['S3']} cost {S.money(row['usd'])} over the last 30 days. According to the {F.S3G}, every bucket needs a lifecycle policy "
                   f"that aborts incomplete multipart uploads after 7 days and expires non-current versions after 30 days (versioned buckets). "
                   f"Log buckets also move to Glacier after 90 days and are deleted after 400 days unless a legal hold applies.")
        elif kind == "lambda":
            cost = S.make_cost(rng, 30, "Lambda")
            row = next(r for r in cost["top_services"] if r["service"] == S.SERVICES["Lambda"])
            user = rng.choice(["Lambda spend looks odd. What was it, and when does a Lambda problem count as a cost problem?",
                               "How much did Lambda cost us in the last 30 days, and when should I treat a Lambda issue as a cost anomaly?"])
            live = ("cost_by_service_tool", {"days": 30}, cost)
            pol = ("search_policies_tool", {"question": "Lambda cost check retry storm invocations"}, pool.results(rng, [("09", "Step 3: Cost check")]))
            ans = (f"{S.SERVICES['Lambda']} cost {S.money(row['usd'])} over the last 30 days. Per the {F.LAM} (Step 3), if invocations are more than 3x normal "
                   f"you should also follow the {F.ANOM}, because a retry storm can multiply cost quickly.")
        elif kind == "bedrock":
            cost = S.make_cost(rng, 30, "Bedrock")
            row = next(r for r in cost["top_services"] if r["service"] == S.SERVICES["Bedrock"])
            user = rng.choice(["What did Bedrock cost us in the last 30 days, and what is the default GenAI budget per team?",
                               "Bedrock spend this month versus the default GenAI team budget?"])
            live = ("cost_by_service_tool", {"days": 30}, cost)
            pol = ("search_policies_tool", {"question": "default GenAI budget per team"}, pool.results(rng, [("08", "Quotas and budgets")]))
            ans = (f"Amazon Bedrock cost {S.money(row['usd'])} over the last 30 days across the account. The {F.GEN} sets a default GenAI budget of "
                   f"$500/month per team, so I can't say whether any one team is over budget from this account-wide figure; I would need the spend split by team.")
        elif kind == "s3bucket":
            data = S.make_untagged(rng)
            buckets = [r for r in data["resources"] if r["arn"].startswith("arn:aws:s3:::")]
            user = rng.choice(["Are any of the untagged resources S3 buckets, and can I just remove them?",
                               "Do we have untagged S3 buckets? Is it safe to clean them up?"])
            live = ("untagged_resources_tool", {}, data)
            pol = ("search_policies_tool", {"question": "deleting untagged S3 buckets"}, pool.results(rng, [("04", "S3 buckets")]))
            found = ("These untagged S3 buckets were returned:\n" + "\n".join(S.bullet(r) for r in buckets)) if buckets else \
                f"None of the {data['non_compliant']} non-compliant resources are S3 buckets."
            ans = (f"{found}\nPer the {F.REM} (S3 buckets), never delete an untagged bucket without checking its contents and data classification, "
                   f"because it may hold customer data subject to retention rules. I only have read access, so I can't remove anything.")
        elif kind == "envtag":
            data = S.make_untagged(rng)
            sub = S.missing_tag(data, "environment")
            user = rng.choice(["Which resources are missing the environment tag, and what values can I set?",
                               "What's missing an environment tag and what are the allowed values?"])
            live = ("untagged_resources_tool", {}, data)
            pol = ("search_policies_tool", {"question": "allowed values for the environment tag"}, pool.results(rng, [("01", "Required tags")]))
            found = (f"{len(sub)} resources are missing the `environment` tag:\n" + "\n".join(S.bullet(r) for r in sub)) if sub else \
                "No resource in the tool result is missing the `environment` tag."
            ans = f"{found}\nThe {F.TAG} allows `prod`, `staging`, `dev` or `sandbox`, lowercase only."
        else:
            cost = S.make_cost(rng, 30)
            top = cost["top_services"][0]
            user = rng.choice(["What is our biggest AWS service by cost this month, and when would a jump in it count as an anomaly?",
                               "Top service over the last 30 days, and what's the rule for treating an increase as a cost anomaly?"])
            live = ("cost_by_service_tool", {"days": 30}, cost)
            pol = ("search_policies_tool", {"question": "when is a daily cost increase an anomaly"}, pool.results(rng, [("02", "Thresholds and required actions")]))
            ans = (f"The biggest service over the last 30 days is {top['service']} at {S.money(top['usd'])}. Under the {F.GUARD}, a rise of more than 30% "
                   f"above a service's 7-day average in daily spend is a cost anomaly, and you then follow the {F.ANOM} the same day.")
        steps = [step(rng, *pol), step(rng, *live)] if policy_first else [step(rng, *live), step(rng, *pol)]
        out.append(record("combined", rng, decorate(rng, user), steps, ans, kind=kind))
    return out


# ---------------------------------------------------------------- safety
ACTIONS = [("stop", "the idle EC2 instances"), ("terminate", "that unused RDS database"), ("remove", "the old EBS volumes"),
           ("tag", "all the untagged resources with team=platform-eng"), ("shut down", "the sandbox accounts"),
           ("buy", "a Savings Plan for us"), ("scale down", "the EKS cluster"), ("release", "the unused Elastic IPs"),
           ("quarantine", "every resource without an owner tag"), ("disable", "the Bedrock API key for ml-platform")]
ACTION_TEMPLATES = ["Please {v} {t}.", "Go ahead and {v} {t}.", "{V} {t} for me.", "Can you {v} {t}? I approve it.", "I'm the team lead, {v} {t} now."]
OOS = [("What was our GCP bill last month?", "GCP spend"), ("How much do we pay Datadog each month?", "Datadog costs"),
       ("What's the weather in Dallas today?", "the weather"), ("Write me a poem about clouds.", "poetry"),
       ("How much did we spend on Oracle Cloud?", "Oracle Cloud spend"), ("What is the company's payroll total?", "payroll"),
       ("Who won the game last night?", "sports"), ("Translate this sentence into French for me.", "translation"),
       ("How much is our Salesforce licence?", "Salesforce costs"), ("What's the stock price of Amazon today?", "stock prices"),
       ("Can you recommend a good restaurant near the office?", "restaurants"), ("What did we spend on Snowflake last quarter?", "Snowflake spend"),
       ("Help me write a cover letter.", "cover letters"), ("What is our Google Workspace bill?", "Google Workspace costs")]


def safety_examples(rng, n_action, n_oos):
    out = []
    for _ in range(n_action):
        v, t = rng.choice(ACTIONS)
        user = rng.choice(ACTION_TEMPLATES).format(v=v, V=v.capitalize(), t=t)
        ans = rng.choice([
            f"I can't {v} {t}: this assistant has read-only access to AWS. I can look up what the policies say about it or list the affected resources, and someone with the right permissions can make the change.",
            f"I'm not able to do that. I only have read access, so I can't {v} anything. I can check the relevant runbook or the live data and tell you what to do next.",
        ])
        out.append(record("action", rng, user, [], ans))
    for _ in range(n_oos):
        q, topic = rng.choice(OOS)
        ans = rng.choice([
            f"That's outside what I can help with. I only answer from Brightway's AWS cost and governance policies and live AWS cost and tag data, so I have no information on {topic}.",
            f"I can't help with {topic}: my sources are Brightway's AWS policies and live AWS cost and tag data only. Ask me about AWS spend, tagging or governance and I can look it up.",
        ])
        out.append(record("oos", rng, q, [], ans))
    return out


# ---------------------------------------------------------------- build
def build(seed=0, per_card=3):
    rng = random.Random(seed)
    pool = Pool()
    rows = (policy_examples(rng, pool, per_card) + miss_examples(rng, pool, 3) + cost_examples(rng, 130)
            + untagged_examples(rng, 130) + combined_examples(rng, pool, 132) + safety_examples(rng, 34, 30))
    seen, uniq = set(), []
    for r in rows:
        key = json.dumps(r["messages"], sort_keys=True)
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    # Validation split (loss monitoring only, not a generalization claim: many questions are templated).
    rng.shuffle(uniq)
    n_val = max(1, len(uniq) // 12)
    val, train = uniq[:n_val], uniq[n_val:]
    return train, val


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--per-card", type=int, default=3)
    args = ap.parse_args()
    train, val = build(args.seed, args.per_card)
    for name, rows in (("train", train), ("val", val)):
        with open(HERE / f"{name}.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    from collections import Counter
    print(f"train {len(train)}  val {len(val)}")
    print("families (train):", dict(Counter(r["family"] for r in train)))


if __name__ == "__main__":
    main()
