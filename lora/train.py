"""LoRA fine-tune of a small instruct model on the governance-agent trajectories.

    python lora/train.py --smoke                 # 3 steps on 8 examples, then one test generation
    python lora/train.py                         # full run -> lora/out/adapter

Plain bf16 LoRA (no quantization): rank 16 on all attention and MLP projections. Loss is on
assistant turns only (masking.py). Needs a CUDA GPU with about 24 GB.
"""
import argparse
import json
import os
import random
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_BASE = os.getenv("BASE_MODEL", "meta-llama/Llama-3.2-3B-Instruct")

ap = argparse.ArgumentParser()
ap.add_argument("--base", default=DEFAULT_BASE)
ap.add_argument("--train", default=str(HERE / "data" / "train.jsonl"))
ap.add_argument("--val", default=str(HERE / "data" / "val.jsonl"))
ap.add_argument("--out", default=str(HERE / "out" / "adapter"))
ap.add_argument("--epochs", type=float, default=3)
ap.add_argument("--lr", type=float, default=2e-4)
ap.add_argument("--batch", type=int, default=2)
ap.add_argument("--accum", type=int, default=8)
ap.add_argument("--max-len", type=int, default=4096)
ap.add_argument("--rank", type=int, default=16)
ap.add_argument("--seed", type=int, default=0)
ap.add_argument("--smoke", action="store_true", help="tiny run to check the whole pipeline")
args = ap.parse_args()

import torch  # noqa: E402
from peft import LoraConfig, get_peft_model  # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainingArguments  # noqa: E402

import masking  # noqa: E402
from schema import TOOLS  # noqa: E402

random.seed(args.seed)
tok = AutoTokenizer.from_pretrained(args.base)
if tok.pad_token_id is None:
    tok.pad_token = tok.eos_token


def render(messages, add_generation_prompt):
    return tok.apply_chat_template(messages, tools=TOOLS, tokenize=False,
                                   add_generation_prompt=add_generation_prompt)


def load(path, limit=None):
    rows = [json.loads(l) for l in open(path, encoding="utf-8")]
    out, dropped = [], 0
    for r in rows[:limit] if limit else rows:
        enc = masking.encode_example(r["messages"], render, tok, args.max_len)
        if enc is None:
            dropped += 1
        else:
            out.append({"input_ids": enc[0], "labels": enc[1]})
    return out, dropped


limit = 8 if args.smoke else None
train, d1 = load(args.train, limit)
val, d2 = load(args.val, limit)
n_tok = sum(len(x["input_ids"]) for x in train)
n_lab = sum(sum(1 for t in x["labels"] if t != masking.IGNORE) for x in train)
print(f"train {len(train)} (dropped {d1} too long)  val {len(val)} (dropped {d2})  "
      f"tokens {n_tok}, trained on {n_lab} ({100 * n_lab / max(n_tok, 1):.1f}%)")
assert n_lab > 0, "no assistant tokens found: check the chat template / masking"


def collate(batch):
    n = max(len(x["input_ids"]) for x in batch)
    pad = tok.pad_token_id
    return {
        "input_ids": torch.tensor([x["input_ids"] + [pad] * (n - len(x["input_ids"])) for x in batch]),
        "attention_mask": torch.tensor([[1] * len(x["input_ids"]) + [0] * (n - len(x["input_ids"])) for x in batch]),
        "labels": torch.tensor([x["labels"] + [masking.IGNORE] * (n - len(x["labels"])) for x in batch]),
    }


model = AutoModelForCausalLM.from_pretrained(args.base, torch_dtype=torch.bfloat16, attn_implementation="sdpa").cuda()
model.gradient_checkpointing_enable()
model.enable_input_require_grads()
model = get_peft_model(model, LoraConfig(
    r=args.rank, lora_alpha=2 * args.rank, lora_dropout=0.05, task_type="CAUSAL_LM",
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]))
model.print_trainable_parameters()

out = Path(args.out)
targs = TrainingArguments(
    output_dir=str(out.parent / "trainer"), num_train_epochs=args.epochs, max_steps=3 if args.smoke else -1,
    per_device_train_batch_size=args.batch, per_device_eval_batch_size=args.batch,
    gradient_accumulation_steps=1 if args.smoke else args.accum, learning_rate=args.lr,
    lr_scheduler_type="cosine", warmup_ratio=0.05, bf16=True, logging_steps=5,
    eval_strategy="no" if args.smoke else "epoch", save_strategy="no", report_to="none",
    remove_unused_columns=False, seed=args.seed, dataloader_num_workers=0)
trainer = Trainer(model=model, args=targs, train_dataset=train, eval_dataset=val or None, data_collator=collate)
t0 = time.time()
result = trainer.train()
print(f"trained in {time.time() - t0:.0f}s, final train loss {result.training_loss:.4f}")

if args.smoke:
    out = out.parent / "smoke"
out.mkdir(parents=True, exist_ok=True)
model.save_pretrained(out)
tok.save_pretrained(out)
(out / "train_meta.json").write_text(json.dumps({
    "base": args.base, "examples": len(train), "epochs": args.epochs, "lr": args.lr, "rank": args.rank,
    "batch": args.batch, "accum": args.accum, "max_len": args.max_len, "seed": args.seed,
    "final_train_loss": result.training_loss, "smoke": args.smoke}, indent=2))
print(f"saved adapter to {out}")

# One generation through the trained model, to see that tool calling looks sane.
model.eval()
row = json.loads(open(args.val, encoding="utf-8").readline())
prompt = tok.apply_chat_template(row["messages"][:2], tools=TOOLS, tokenize=False, add_generation_prompt=True)
ids = tok(prompt, return_tensors="pt", add_special_tokens=False).to("cuda")
with torch.no_grad():
    gen = model.generate(**ids, max_new_tokens=120, do_sample=False)
print("\nQ:", row["messages"][1]["content"])
print("model:", tok.decode(gen[0][ids["input_ids"].shape[1]:], skip_special_tokens=False))
