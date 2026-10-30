# LoRA fine-tune of the governance agent's model

Can a small model be taught to use this agent's tools better than it does out of the box?
The 3B model passes 6/6 policy questions but only 1/5 combined ones (policy lookup + live call + join). This folder fine-tunes it with LoRA and measures the change **with the same eval, the same prompt, the same tools and the same serving stack**, base vs tuned. The result is reported whichever way it goes.

## What is trained

Full agent trajectories in chat format: system prompt, question, tool calls, tool results, final answer. 573 training and 52 validation examples (`data/train.jsonl`, `data/val.jsonl`), built by `data/gen_data.py`:

| Family | Examples | What it teaches |
|---|---|---|
| policy | 138 | call `search_policies_tool`, answer from the retrieved passage, quote the document title |
| cost / untagged | 241 | call the right live tool with the right arguments (`days`), read exact numbers, filter and count lists correctly, say so when a service is absent |
| combined | 123 | policy + live in either order, then join them (6 task types) |
| miss | 29 | retrieval returns nothing relevant: say so, do not guess |
| action / out of scope | 42 | refuse changes (read-only), decline non-AWS questions, call no tool |

How the data avoids fooling the eval:
- **Live data is random** every time (amounts, ARNs, tag sets), so facts cannot be memorized; answers are computed from the tool result in code, then checked by tests.
- **Policy text is the real chunks** the retriever returns (same chunker as `app/ingest.py`), with other chunks as distractors, as in real retrieval.
- **Held out of training entirely:** three documents (EC2 rightsizing, S3 security baseline, IAM baseline) and every section the 16 eval questions depend on. `heldout_questions.json` asks 8 questions about the three unseen documents.
- No training question is close to an eval question (word-overlap test in `test_data.py`).
- Honest limit: the training questions are templated, and the documents are the same company library, so this tests *behaviour on this domain*, not general ability.

`python lora/test_data.py` runs 16 offline checks (schema matches the agent, tool calls valid, numbers in answers come from tool results, no held-out text, no eval overlap, label masking).

## Method

LoRA rank 16 (alpha 32, dropout 0.05) on all attention and MLP projections, bf16 (no quantization), 3 epochs, lr 2e-4 cosine, effective batch 16. Loss only on assistant turns (`masking.py`): tool calls, answers and the end-of-turn token; not the system prompt, question or tool results. Bases: `Qwen/Qwen2.5-3B-Instruct` and `meta-llama/Llama-3.2-3B-Instruct` (Llama is the model behind the 3B results in the main README), each trained and evaluated separately. Training and serving use the model's own chat template, exported to `out/<model name>/chat_template.jinja`, so what vLLM shows the model is what it trained on.

## Run it (Vast.ai, one GPU with 24 GB or more)

**Two base models, one folder each.** Everything below is run once per model: `meta-llama/Llama-3.2-3B-Instruct` and `Qwen/Qwen2.5-3B-Instruct` (set `BASE_MODEL` on the Vast box before every command; outputs go to `lora/out/<model name>/`). Use a different `--label` per model, e.g. `vllm-qwen-base` and `vllm-qwen-tuned`, then compare each pair. Run Qwen first if Llama access is still pending.

**Before renting (laptop):** accept the Llama 3.2 license on Hugging Face (gated, approval can take a while) and create a read token. No access yet? Use `export BASE_MODEL=Qwen/Qwen2.5-3B-Instruct` for every command below; it is not gated. Commit and push `lora/` so the box can clone it.

**1. Set up (Vast shell)**
```bash
git clone https://github.com/arthuersundar/langgraph-multi-llm-agent && cd langgraph-multi-llm-agent
bash lora/vast_setup.sh
export HF_TOKEN=hf_...            # yours; do not commit or paste it anywhere
export VLLM_API_KEY=...           # any long random string; the same one goes in your laptop shell
source /workspace/train-venv/bin/activate
python lora/render_check.py       # must end with "all checks passed"; writes out/<model name>/chat_template.jinja
python lora/train.py --smoke      # 3 steps + one generation, ~2 minutes
```

**2. Baseline first (Vast shell, then laptop)**
```bash
tmux new -s serve; bash lora/serve_vllm.sh base       # wait for "Application startup complete"
```
Laptop, in a second PowerShell window (the port and IP are on the Vast instance page):
```powershell
ssh -p <PORT> root@<IP> -L 8000:localhost:8000 -N
```
Laptop, in the repo:
```powershell
$env:VLLM_BASE_URL="http://localhost:8000/v1"; $env:VLLM_API_KEY="<same key>"; $env:VLLM_MODEL="base"
python eval\run_eval.py --provider vllm --label vllm-base
python eval\run_eval.py --provider vllm --label vllm-base-heldout --questions lora\heldout_questions.json
```
Stop the server (Ctrl+C in tmux) before training: it holds the GPU.

**3. Train (Vast shell)**
```bash
python lora/train.py                 # about 15-30 minutes on a 4090 (estimate, not measured)
bash lora/serve_vllm.sh lora         # serves "base" and "tuned" from one server
```

**4. Tuned eval (laptop)**
```powershell
$env:VLLM_MODEL="tuned"
python eval\run_eval.py --provider vllm --label vllm-tuned
python eval\run_eval.py --provider vllm --label vllm-tuned-heldout --questions lora\heldout_questions.json
$env:VLLM_MODEL="base"
python eval\run_eval.py --provider vllm --label vllm-base2        # base again on the same server
python lora\compare.py base=vllm-base tuned=vllm-tuned
python lora\compare.py base=vllm-base tuned=vllm-tuned --heldout
```
Copy the adapter home (`scp -P <PORT> -r root@<IP>:~/langgraph-multi-llm-agent/lora/out/<model name>/adapter .`; about 50 MB, not committed), then **destroy the Vast instance**; it bills while idle.

## How to read the result

- 16 questions are noisy: a one-question difference is a tie. Look at **tools ok**, **combined** and **avg model calls**, not only the total.
- The base numbers here come from vLLM in bf16 and will not match the Ollama Q4 numbers in the main README (11/16); compare base to tuned on the same server only.
- Check **safety** did not regress, and compare `vllm-base` with `vllm-base2` to see run-to-run noise.
- If tuned is not better, that is the finding: report it, and read `eval\show_failures.py` to see why.

## Results

Qwen2.5-3B-Instruct, LoRA (rank 16, 3 epochs, 573 synthetic training trajectories). `base` and `tuned` were served from one vLLM server (bf16, same chat template, same prompt and tools) and scored by the same eval. Base was run twice (before and after training) and gave identical totals.

| | base | tuned |
|---|---|---|
| Main eval (16 questions), passed | 9 | **13** |
| Main eval, correct tools | 10 | **15** |
| Main eval, facts in the answer | 13 | 13 |
| Main eval, avg seconds per question | 6.7 | **3.5** |
| Held-out docs (8 questions), passed | 3 | **7** |
| Held-out, correct tools | 3 | **8** |
| Held-out, facts in the answer | 5 | 7 |

What changed: tool use. The base model sometimes answers a policy question without searching and calls unneeded cost or untagged-resource tools; the tuned model picks the right tool more consistently. Facts-in-answer on the main eval did not change (13 and 13), so the extra passes come from fewer forbidden or missing tool calls. Average model calls per question were nearly the same (2.4 vs 2.2), so the lower latency is not explained by fewer calls; I did not investigate why.

Still failing after tuning: `c2`, `c3`, `c5` (main) and `h8` (held-out). `c2` made one extra tool call; `c3`, `c5` and `h8` missed a number or keyword in the answer. `c2` and `h8` passed on base and failed on tuned.

Limits: 16 and 8 questions are small samples; training data is templated and built from the same document library, so this shows the behavior improved on this domain, not that it generalizes. Base numbers (bf16, vLLM) are not comparable to the Ollama Q4 numbers in the main README. Llama-3.2-3B has not been run yet.

Raw results are in `eval/results/` (`vllm-qwen-base2*`, `vllm-qwen-tuned*`).

## Not verified yet

`train.py`, `render_check.py`, vLLM serving with the adapter and the tuned eval have now been run for Qwen2.5-3B (see Results). Llama-3.2-3B is untested. The adapter is not committed.
