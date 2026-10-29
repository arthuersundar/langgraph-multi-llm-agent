#!/usr/bin/env bash
# Serve the model with an OpenAI-compatible API on the Vast box (localhost only; reach it
# through an SSH tunnel). Same chat template as training (lora/out/<model name>/chat_template.jinja).
# Choose the model with BASE_MODEL (default Llama-3.2-3B-Instruct).
#
#   bash lora/serve_vllm.sh base      # base model only            -> model name: base
#   bash lora/serve_vllm.sh lora      # base + the trained adapter -> model names: base, tuned
set -euo pipefail
cd "$(dirname "$0")/.."
MODE="${1:-base}"
BASE="${BASE_MODEL:-meta-llama/Llama-3.2-3B-Instruct}"
: "${VLLM_API_KEY:?export VLLM_API_KEY first}"
SHORT="$(basename "$BASE" | tr 'A-Z' 'a-z')"
OUT="lora/out/$SHORT"
TEMPLATE="$OUT/chat_template.jinja"
[ -f "$TEMPLATE" ] || { echo "run lora/render_check.py first (it writes $TEMPLATE)"; exit 1; }
case "$BASE" in *Qwen*) PARSER=hermes ;; *) PARSER=llama3_json ;; esac

ARGS=(serve "$BASE" --served-model-name base --dtype bfloat16 --max-model-len 8192
      --enable-auto-tool-choice --tool-call-parser "$PARSER" --chat-template "$TEMPLATE"
      --api-key "$VLLM_API_KEY" --host 127.0.0.1 --port 8000)
if [ "$MODE" = "lora" ]; then
  [ -f "$OUT/adapter/adapter_config.json" ] || { echo "no adapter at $OUT/adapter: train first"; exit 1; }
  ARGS+=(--enable-lora --lora-modules "tuned=$OUT/adapter" --max-lora-rank 16)
fi
exec /workspace/serve-venv/bin/vllm "${ARGS[@]}"
