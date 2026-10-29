"""See the gateway's metrics without AWS: push simulated traffic through the real handler
(with a fake Bedrock and a fake vLLM) and summarise the metric lines it emits.

    python gateway/lambda/demo_metrics.py

This is what CloudWatch would receive. It proves the numbers (cost, tokens, errors, throttles)
add up; it does not measure real latency or real prices.
"""
import io
import json
import os
import random
import statistics
from collections import defaultdict
from unittest import mock

os.environ["KEY_TEAMS"] = json.dumps({"k1": "ml-platform", "k2": "data-eng"})
os.environ["VLLM_BASE_URL"] = "http://vllm.example/v1"
os.environ["MODEL_PRICES"] = json.dumps({"us.amazon.nova-lite-v1:0": [0.06, 0.24], "us.amazon.nova-pro-v1:0": [0.80, 3.20]})
import handler as h  # noqa: E402

rng = random.Random(1)


class FakeBedrock:
    def converse(self, **kw):
        roll = rng.random()
        if roll < 0.10:
            e = Exception("throttled")
            e.response = {"Error": {"Code": "ThrottlingException"}}
            raise e
        if roll < 0.13:
            raise RuntimeError("backend exploded")
        i, o = rng.randint(300, 4000), rng.randint(20, 600)
        return {"output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}}, "stopReason": "end_turn",
                "usage": {"inputTokens": i, "outputTokens": o, "totalTokens": i + o}}


def fake_vllm(req, timeout=0):
    return io.BytesIO(json.dumps({"choices": [{"message": {"content": "local"}}],
                                  "usage": {"prompt_tokens": rng.randint(300, 4000), "completion_tokens": rng.randint(20, 600)}}).encode())


h._bedrock = FakeBedrock()
lines = []
with mock.patch("builtins.print", lambda s: s.startswith("{") and lines.append(json.loads(s))), mock.patch("urllib.request.urlopen", fake_vllm):
    for _ in range(300):
        key = rng.choice(["k1", "k1", "k2"])
        model = rng.choice(["bedrock", "bedrock", "bedrock/us.amazon.nova-pro-v1:0", "vllm"])
        h.handler({"body": json.dumps({"model": model, "messages": [{"role": "user", "content": "hi"}]}),
                   "requestContext": {"identity": {"apiKeyId": key}}})

agg = defaultdict(lambda: defaultdict(float))
lat = defaultdict(list)
for m in lines:
    k = (m["Team"], m["Backend"])
    for f in ("Requests", "InputTokens", "OutputTokens", "CostUsd", "ServerErrors", "BackendThrottles"):
        agg[k][f] += m[f]
    lat[k].append(m["LatencyMs"])
print(f"{'team':<12}{'backend':<9}{'reqs':>6}{'in tok':>10}{'out tok':>9}{'cost $':>10}{'5xx':>5}{'429':>5}")
for k in sorted(agg):
    a = agg[k]
    print(f"{k[0]:<12}{k[1]:<9}{a['Requests']:>6.0f}{a['InputTokens']:>10.0f}{a['OutputTokens']:>9.0f}{a['CostUsd']:>10.4f}{a['ServerErrors']:>5.0f}{a['BackendThrottles']:>5.0f}")
tot = sum(a["CostUsd"] for a in agg.values())
print(f"\ntotal estimated cost: ${tot:.4f} over {len(lines)} requests (${tot / len(lines):.6f} per request)")
