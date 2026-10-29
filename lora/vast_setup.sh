#!/usr/bin/env bash
# One-time setup on a fresh Vast.ai instance. Run from the repo root:  bash lora/vast_setup.sh
# Needs: a CUDA GPU with >= 24 GB (RTX 4090 / A5000 / L4 or better), ~60 GB disk, Python 3.10+.
# Two virtualenvs, because vLLM pins its own torch: train-venv (PEFT) and serve-venv (vLLM).
set -euo pipefail
cd "$(dirname "$0")/.."
nvidia-smi --query-gpu=name,memory.total --format=csv

python3 -m venv --system-site-packages /workspace/train-venv
/workspace/train-venv/bin/pip install -q -U pip
/workspace/train-venv/bin/pip install -q -U torch transformers peft accelerate

python3 -m venv /workspace/serve-venv
/workspace/serve-venv/bin/pip install -q -U pip
/workspace/serve-venv/bin/pip install -q -U vllm

echo
echo "Done. Next:"
echo "  export HF_TOKEN=hf_...        # your own token; never commit or paste it anywhere"
echo "  export VLLM_API_KEY=$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
echo "  source /workspace/train-venv/bin/activate && python lora/render_check.py"
