"""Send a burst of requests to the deployed gateway so the dashboard and alarms have data.

    python gateway/scripts/generate_traffic.py --url <base_url> --key <team api key> --n 40
    python gateway/scripts/generate_traffic.py --url ... --key ... --model vllm --n 40

While Bedrock quotas are 0 the bedrock model answers 429 (backend throttle): good for seeing the
throttle metric and alarm. Use --model vllm once the gateway has a reachable vLLM URL for real
tokens. Standard library only. Costs: each Bedrock call is billed once quotas exist (tiny prompts).
"""
import argparse
import json
import time
import urllib.error
import urllib.request
from collections import Counter

ap = argparse.ArgumentParser()
ap.add_argument("--url", required=True, help="terraform output base_url (ends with /v1)")
ap.add_argument("--key", required=True, help="a team's API key")
ap.add_argument("--model", default="bedrock", help="bedrock, bedrock/<model id> or vllm")
ap.add_argument("--n", type=int, default=30)
ap.add_argument("--pause", type=float, default=0.5, help="seconds between requests")
args = ap.parse_args()

PROMPTS = ["Say hi in five words.", "What is a NAT gateway? One sentence.", "List three AWS cost-saving tips.",
           "Summarise: tags let finance allocate cloud spend to teams."]
codes, times = Counter(), []
for i in range(args.n):
    body = json.dumps({"model": args.model, "max_tokens": 64, "messages": [{"role": "user", "content": PROMPTS[i % len(PROMPTS)]}]}).encode()
    req = urllib.request.Request(args.url.rstrip("/") + "/chat/completions", body,
                                 {"content-type": "application/json", "x-api-key": args.key})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as e:  # noqa: BLE001
        code = type(e).__name__
    codes[code] += 1
    times.append((time.time() - t0) * 1000)
    print(f"[{i + 1:>3}/{args.n}] {code}  {times[-1]:.0f} ms")
    time.sleep(args.pause)
times.sort()
print("\nstatus counts:", dict(codes), f"| p50 {times[len(times) // 2]:.0f} ms, max {times[-1]:.0f} ms")
