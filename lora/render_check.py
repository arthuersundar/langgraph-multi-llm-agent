"""Look at what the model will really be trained on, before spending GPU time.

    python lora/render_check.py [--base meta-llama/Llama-3.2-3B-Instruct]

Renders a few examples with the model's chat template, marks the assistant spans that get
loss, and checks the invariants masking depends on. Also writes lora/out/<model name>/chat_template.jinja,
which the vLLM server must use so serving matches training exactly.
"""
import argparse
import json
import os
from pathlib import Path

from transformers import AutoTokenizer

import masking
from schema import TOOLS

HERE = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument("--base", default=os.getenv("BASE_MODEL", "meta-llama/Llama-3.2-3B-Instruct"))
ap.add_argument("--show", type=int, default=2, help="how many examples to print in full")
args = ap.parse_args()

tok = AutoTokenizer.from_pretrained(args.base)
tpl = tok.chat_template
if isinstance(tpl, dict):
    tpl = tpl.get("default") or next(iter(tpl.values()))
short = args.base.split("/")[-1].lower()
(HERE / "out" / short).mkdir(parents=True, exist_ok=True)
(HERE / "out" / short / "chat_template.jinja").write_text(tpl, encoding="utf-8")
print(f"wrote lora/out/{short}/chat_template.jinja")


def render(messages, gen):
    return tok.apply_chat_template(messages, tools=TOOLS, tokenize=False, add_generation_prompt=gen)


rows = [json.loads(l) for l in open(HERE / "data" / "train.jsonl", encoding="utf-8")]
by_family = {}
for r in rows:
    by_family.setdefault(r["family"], r)
problems = 0
for fam, r in by_family.items():
    try:
        full, spans = masking.assistant_spans(r["messages"], render)
        enc = tok(full, add_special_tokens=False, return_offsets_mapping=True)
        labels = masking.build_labels(enc["input_ids"], enc["offset_mapping"], spans)
        kept = sum(1 for x in labels if x != masking.IGNORE)
        frac = kept / len(labels)
        trained = "".join(full[s:e] for s, e in spans)
        ok = 0.01 < frac < 0.6 and TOOLS[0]["function"]["name"] in full and r["messages"][1]["content"] in full
        for m in r["messages"]:
            if m["role"] == "assistant" and m.get("content"):
                ok = ok and m["content"][:40] in trained
        print(f"[{'ok' if ok else 'BAD'}] {fam:<9} {len(labels):>5} tokens, {kept:>4} trained ({100 * frac:.1f}%), {len(spans)} assistant turns")
        problems += not ok
    except ValueError as e:
        print(f"[BAD] {fam}: {e}")
        problems += 1

demo = by_family.get("combined") or next(iter(by_family.values()))
full, spans = masking.assistant_spans(demo["messages"], render)
print("\n--- rendered example; trained spans are between >>> and <<< ---")
out, pos = [], 0
for s, e in spans:
    out += [full[pos:s], ">>>", full[s:e], "<<<"]
    pos = e
out.append(full[pos:])
print("".join(out)[:6000])
print("\nRESULT:", "all checks passed" if not problems else f"{problems} problem(s): do not train yet")
raise SystemExit(1 if problems else 0)
