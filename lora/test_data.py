"""Offline tests for the fine-tuning data and the label masking. No GPU, no model, no network.

    python lora/test_data.py
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "data"))

import facts as F  # noqa: E402
import gen_data  # noqa: E402
import masking  # noqa: E402
import synth_live as S  # noqa: E402
from chunks import load_chunks  # noqa: E402
from schema import SYSTEM_PROMPT, TOOL_NAMES, TOOLS  # noqa: E402

TRAIN, VAL = gen_data.build(0, 3)
ALL = TRAIN + VAL
CHUNKS = load_chunks()
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def words(s):
    return set(re.findall(r"[a-z0-9']+", s.lower()))


def nums(s):
    out = set()
    for n in re.findall(r"\d[\d,]*(?:\.\d+)?", s):
        n = n.replace(",", "").rstrip(".")
        out.add(str(float(n)) if "." in n else n)
    return out


def tool_results(r):
    return [m["content"] for m in r["messages"] if m["role"] == "tool"]


# ------------------------------------------------------------------ data
@test
def schema_matches_agent():
    import check_schema
    assert check_schema.main() == 0


@test
def cards_point_at_real_chunks_and_avoid_held_out():
    keys = {(c["doc"], c["section"]) for c in CHUNKS}
    for k in F.CARDS:
        assert k in keys, f"card {k} matches no chunk"
        assert k[0] not in F.HELD_OUT_DOCS and k not in F.HELD_OUT_SECTIONS, f"card {k} is held out"
    for k in F.HELD_OUT_SECTIONS:
        assert k in keys, f"held-out section {k} matches no chunk"


@test
def trajectories_are_well_formed():
    for r in ALL:
        m = r["messages"]
        assert m[0] == {"role": "system", "content": SYSTEM_PROMPT} and m[1]["role"] == "user"
        assert m[-1]["role"] == "assistant" and not m[-1].get("tool_calls") and m[-1]["content"].strip()
        i = 2
        while i < len(m) - 1:
            a, t = m[i], m[i + 1]
            assert a["role"] == "assistant" and len(a["tool_calls"]) == 1, "one tool call per turn"
            c = a["tool_calls"][0]
            assert t["role"] == "tool" and t["tool_call_id"] == c["id"] and t["name"] == c["function"]["name"]
            name, args = c["function"]["name"], c["function"]["arguments"]
            assert name in TOOL_NAMES and isinstance(args, dict)
            allowed = set(TOOLS[TOOL_NAMES.index(name)]["function"]["parameters"]["properties"])
            assert set(args) <= allowed, (name, args)
            if name == "search_policies_tool":
                assert isinstance(args["question"], str) and 0 < len(args["question"]) <= 500
            if name == "cost_by_service_tool":
                assert isinstance(args["days"], int) and 1 <= args["days"] <= 365
            json.loads(t["content"])
            i += 2
        assert i == len(m) - 1


@test
def held_out_text_never_reaches_training():
    banned = [c["text"] for c in CHUNKS if c["doc"] in F.HELD_OUT_DOCS or (c["doc"], c["section"]) in F.HELD_OUT_SECTIONS]
    for r in ALL:
        blob = " ".join(tool_results(r))
        for b in banned:
            assert json.dumps(b)[1:-1] not in blob, "a held-out chunk appears in a tool result"
    assert "azure" not in json.dumps(ALL).lower()


@test
def no_overlap_with_eval_questions():
    held = json.loads((HERE / "heldout_questions.json").read_text(encoding="utf-8"))
    evals = [q["question"] for q in json.loads((ROOT / "eval" / "questions.json").read_text(encoding="utf-8"))]
    evals += [q["question"] for q in held]
    bad = set()
    for r in ALL:
        u = r["messages"][1]["content"]
        for q in evals:
            a, b = words(u), words(q)
            if q.lower() in u.lower() or u.lower() in q.lower() or len(a & b) / len(a | b) >= 0.6:
                bad.add((u, q))
    assert not bad, "too similar to an eval question:\n" + "\n".join(f"  train: {u}\n  eval:  {q}" for u, q in sorted(bad)[:8])


@test
def answers_use_only_numbers_from_the_tool_results():
    for r in ALL:
        ans = r["messages"][-1]["content"]
        src = " ".join(tool_results(r)) + " " + r["messages"][1]["content"]
        allowed = nums(src)
        if r["family"] == "cost":
            rows = json.loads(tool_results(r)[0])["top_services"]
            total = json.loads(tool_results(r)[0])["total_usd"]
            allowed |= {str(r["meta"]["days"]), str(len(rows))}
            allowed |= {str(round(100 * x['usd'] / total, 1)) for x in rows}
            allowed |= {str(round(abs(x['usd'] - y['usd']), 2)) for x in rows for y in rows}
            allowed |= {str(i) for i in range(1, 11)}  # list numbering and "top 3" / "top 5"
        if r["family"] == "untagged":
            data = json.loads(tool_results(r)[0])
            allowed |= {str(len(data["resources"])), str(max([len(x["missing"]) for x in data["resources"]] or [0])),
                        str(len(S.missing_tag(data, r["meta"]["tag"])))}
        if r["family"] == "combined":
            allowed |= {"30"}  # "over the last 30 days"
            for t in tool_results(r):
                data = json.loads(t)
                if isinstance(data, dict) and "resources" in data:
                    allowed |= {str(len(data["resources"])), str(len(S.missing_tag(data, "environment"))),
                                str(len([x for x in data["resources"] if x["arn"].startswith("arn:aws:s3:::")]))}
        extra = nums(ans) - allowed
        assert not extra, f"{r['family']}: numbers {extra} in the answer are not in the tool results\n{ans}"


@test
def untagged_answers_list_exactly_the_right_resources():
    n = 0
    for r in ALL:
        if r["family"] != "untagged" or r["meta"]["kind"] != "tag":
            continue
        data = json.loads(tool_results(r)[0])
        want = {x["arn"] for x in S.missing_tag(data, r["meta"]["tag"])}
        got = set(re.findall(r"`(arn:[^`]+)`", r["messages"][-1]["content"]))
        assert got == want, (got ^ want)
        n += 1
    assert n >= 20


@test
def cost_answers_quote_the_right_service():
    for r in ALL:
        if r["family"] == "cost" and r["meta"]["kind"] == "service":
            data = json.loads(tool_results(r)[0])
            ans = r["messages"][-1]["content"]
            hit = [x for x in data["top_services"] if x["service"] in ans]
            assert hit and S.money(hit[0]["usd"]) in ans


@test
def answers_do_not_trip_the_prompt_leak_guard():
    grams = lambda t: {" ".join(w[i:i + 8]) for w in [re.findall(r"[a-z0-9']+", t.lower())] for i in range(len(w) - 7)}
    prompt = grams(SYSTEM_PROMPT)
    for r in ALL:
        assert not (grams(r["messages"][-1]["content"]) & prompt), r["messages"][-1]["content"]


@test
def action_and_out_of_scope_use_no_tools():
    fams = {r["family"] for r in ALL}
    assert {"policy", "miss", "cost", "untagged", "combined", "action", "oos"} <= fams
    for r in ALL:
        if r["family"] in ("action", "oos"):
            assert len(r["messages"]) == 3


@test
def combined_covers_both_orders_and_all_kinds():
    kinds, orders = set(), set()
    for r in TRAIN:
        if r["family"] == "combined":
            kinds.add(r["meta"]["kind"])
            orders.add(r["messages"][2]["tool_calls"][0]["function"]["name"])
    assert len(kinds) == 6
    assert "search_policies_tool" in orders and len(orders) >= 2  # policy-first and live-first both occur


@test
def every_policy_card_is_trained_on():
    covered = {tuple(r["meta"]["target"]) for r in TRAIN if r["family"] == "policy"}
    assert covered == set(F.CARDS), set(F.CARDS) - covered


@test
def generation_is_deterministic_and_sized():
    a, _ = gen_data.build(0, 3)
    assert a == TRAIN
    assert 450 <= len(TRAIN) <= 900 and len(VAL) >= 20


@test
def heldout_questions_use_unseen_docs():
    held = json.loads((HERE / "heldout_questions.json").read_text(encoding="utf-8"))
    assert len(held) >= 8 and len({q["id"] for q in held}) == len(held)
    for q in held:
        assert q["expect_tools"] == ["search_policies_tool"] and q["must_contain"]


# ------------------------------------------------------------------ masking (toy tokenizer)
def toy_render(messages, gen):
    out = ""
    for m in messages:
        body = m.get("content") or json.dumps(m.get("tool_calls", ""))
        out += f"<|{m['role']}|>{body}<|end|>"
    return out + ("<|assistant|>" if gen else "")


def toy_tok(text, add_special_tokens=False, return_offsets_mapping=True):
    spans = [(m.start(), m.end()) for m in re.finditer(r"<\|[a-z]+\|>|<\|end\|>|\w+|[^\w\s]", text)]
    return {"input_ids": list(range(len(spans))), "offset_mapping": spans}


@test
def masking_labels_only_assistant_text():
    r = next(x for x in TRAIN if x["family"] == "combined")
    ids, labels = masking.encode_example(r["messages"], toy_render, toy_tok, 100000)
    full, spans = masking.assistant_spans(r["messages"], toy_render)
    offs = toy_tok(full)["offset_mapping"]
    kept = "".join(full[a:b] for (a, b), lab in zip(offs, labels) if lab != masking.IGNORE)
    for m in r["messages"]:
        text = m.get("content") or json.dumps(m.get("tool_calls", ""))
        if m["role"] == "assistant":
            assert re.sub(r"\s+", "", text) in re.sub(r"\s+", "", kept)
        elif m["role"] in ("user", "tool", "system") and len(text) > 30:
            assert re.sub(r"\s+", "", text[:60]) not in re.sub(r"\s+", "", kept)
    assert kept.count("<|end|>") == sum(1 for m in r["messages"] if m["role"] == "assistant")  # end-of-turn is learned
    assert "<|assistant|>" not in kept  # the generation prompt itself is not


@test
def masking_drops_too_long_and_rejects_inconsistent_templates():
    r = TRAIN[0]
    assert masking.encode_example(r["messages"], toy_render, toy_tok, 5) is None

    def bad(messages, gen):  # renders earlier turns differently depending on length
        return toy_render(messages, gen).replace("<|user|>", f"<|user{len(messages)}|>")
    try:
        masking.assistant_spans(r["messages"], bad)
    except ValueError:
        return
    raise AssertionError("an inconsistent template must raise")


if __name__ == "__main__":
    failed = 0
    for t in TESTS:
        try:
            t()
            print("ok  ", t.__name__)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("FAIL", t.__name__, "-", str(e)[:600])
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    sys.exit(1 if failed else 0)
