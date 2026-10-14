import os
import sys
import time
from llm import get_llm

provider = sys.argv[1] if len(sys.argv) > 1 else "bedrock"
model = os.getenv("VLLM_MODEL") if provider == "vllm" else os.getenv("BEDROCK_MODEL")

t0 = time.time()
reply = get_llm(provider).invoke("In one sentence, what is cloud cost optimization?")
print(f"[{provider} | {model}] {reply.content}")
print(f"({time.time() - t0:.1f}s)")