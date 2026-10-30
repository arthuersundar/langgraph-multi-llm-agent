#!/usr/bin/env bash
# One-time setup on a fresh Vast.ai instance. Run from the repo root:  bash lora/vast_setup.sh
# Needs: a CUDA GPU with >= 24 GB (RTX 4090 / A5000 / L4 or better), ~60 GB disk, Python 3.10+.
# Two virtualenvs, because vLLM pins its own torch: train-venv (PEFT) and serve-venv (vLLM).
set -euo pipefail
cd "$(dirname "$0")/.."
nvidia-smi --query-gpu=name,memory.total --format=csv

# Pick the PyTorch build that matches this host's driver (a CUDA 13 build will not run on a CUDA 12.6 driver).
DRV="$(nvidia-smi | sed -n 's/.*CUDA Version: \([0-9]*\)\.\([0-9]*\).*/\1 \2/p' | head -1)"
MAJ="${DRV% *}"; MIN="${DRV#* }"
if   [ "$MAJ" -ge 13 ]; then TORCH_INDEX="cu130"
elif [ "$MIN" -ge 8 ]; then TORCH_INDEX="cu128"
elif [ "$MIN" -ge 6 ]; then TORCH_INDEX="cu126"
else TORCH_INDEX="cu124"; fi
echo "driver supports CUDA $MAJ.$MIN -> PyTorch build $TORCH_INDEX"

rm -rf /workspace/train-venv
python3 -m venv /workspace/train-venv   # clean: no system packages, so no torch/torchvision mix-ups
/workspace/train-venv/bin/pip install -q -U pip
/workspace/train-venv/bin/pip install -q torch --index-url "https://download.pytorch.org/whl/$TORCH_INDEX"
/workspace/train-venv/bin/pip install -q -U transformers peft accelerate
/workspace/train-venv/bin/python -c "import torch;print('train env: torch', torch.__version__, 'cuda', torch.version.cuda, 'gpu ok:', torch.cuda.is_available())"

python3 -m venv /workspace/serve-venv
/workspace/serve-venv/bin/pip install -q -U pip
# pinned: newest vLLM needs a newer driver than many Vast hosts have (CUDA 12.6); 0.11.0 + transformers<5 was verified
/workspace/serve-venv/bin/pip install -q "vllm==0.11.0"
/workspace/serve-venv/bin/pip install -q "transformers>=4.56,<5"
/workspace/serve-venv/bin/python -c "import torch;print('serve env: torch', torch.__version__, 'cuda', torch.version.cuda, 'gpu ok:', torch.cuda.is_available())"

echo
echo "Done. Next:"
echo "  export HF_TOKEN=hf_...        # your own token; never commit or paste it anywhere"
echo "  export VLLM_API_KEY=$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
echo "  source /workspace/train-venv/bin/activate && python lora/render_check.py"
