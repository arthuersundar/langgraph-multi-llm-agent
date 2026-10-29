# LLM gateway (API Gateway + Lambda, Terraform)

One OpenAI-compatible endpoint in front of two model backends, with per-team API keys, rate
limits, daily quotas and token metrics.

```
client / agent --x-api-key--> API Gateway (usage plan per team)
                                   |  POST /v1/chat/completions
                                   v
                               Lambda (python, no dependencies)
                                   |-- model "bedrock" --> Bedrock Converse (request/response translated, tool calls included)
                                   '-- model "vllm"    --> vLLM / Ollama OpenAI-compatible server
```

## Why it exists
The company's GenAI policy says production apps must not call Bedrock directly; they go through a
central gateway with a key and usage plan per team. The June 2026 runaway-Bedrock postmortem is
why the guardrails exist: capped `max_tokens`, no streaming, body size limit, per-team quotas, and
per-team token metrics with an alarm.

## Deploy
Needs Terraform 1.6+ and AWS credentials for the account.
```powershell
cd gateway\terraform
terraform init
terraform plan
terraform apply
```
Cost is small at demo volume (API Gateway REST, Lambda, alarms, a dashboard, custom metrics: see Observability for the details). `terraform destroy` removes everything.

## Smoke tests that need no model access
```powershell
$url = terraform output -raw base_url
$key = (terraform output -json api_keys | ConvertFrom-Json)."ml-platform"

# 1. No key: API Gateway rejects it (403)
Invoke-RestMethod -Method Post -Uri "$url/chat/completions" -ContentType "application/json" -Body '{}'

# 2. Valid key, empty messages: the Lambda answers 400 (proves key + Lambda wiring)
Invoke-RestMethod -Method Post -Uri "$url/chat/completions" -Headers @{"x-api-key"=$key} -ContentType "application/json" -Body '{"messages":[]}'
```
Then a real call (needs Bedrock quota, or `vllm_base_url` set):
```powershell
Invoke-RestMethod -Method Post -Uri "$url/chat/completions" -Headers @{"x-api-key"=$key} -ContentType "application/json" `
  -Body '{"model":"bedrock","messages":[{"role":"user","content":"Say hi in five words"}]}'
```
While Bedrock quotas are zero this returns 429 `rate_limit_error`. That is the gateway reporting the backend's throttle correctly.

## Use it from the agent
In `.env`:
```
LLM_PROVIDER=gateway
GATEWAY_URL=<terraform output base_url>
GATEWAY_API_KEY=<the team key>
GATEWAY_BACKEND=bedrock        # or vllm
```

## Tests (no AWS needed)
```
cd gateway\lambda
python -m unittest test_handler -v
```
Covers request translation (system, tool calls, merged tool results, tool_choice), response
translation, max_tokens cap, error mapping (throttle becomes 429, internals never leak), the
vLLM passthrough and the metric line (cost, errors, throttles, no prompt text).
`python demo_metrics.py` pushes 300 simulated requests through the real handler and prints the
per-team totals CloudWatch would receive.

## Terraform state
By default state is a local file (`terraform\terraform.tfstate`), gitignored because it contains the
API keys. That is fine for one person and one stack. To keep state in S3 instead (versioned, encrypted,
private, locked), do this once:
```powershell
cd gateway\bootstrap
terraform init
terraform apply                      # creates the state bucket; prints a ready-made backend block

cd ..\terraform
copy backend.tf.example backend.tf   # edit the bucket name to the one printed above
terraform init -migrate-state        # answer yes: moves your existing local state into the bucket
```
Needs Terraform 1.10+ (S3 native locking, so no DynamoDB table). The bootstrap bucket is protected
with `prevent_destroy`; to remove it later, empty it and delete the safeguard first. The bootstrap
folder keeps its own small local state (the bucket cannot hold the state of its own creator).

## Known limits
- No streaming (API Gateway REST waits for the full reply; 29 second limit).
- The vLLM URL must be reachable from the internet. Use a throwaway key: it lives in the Lambda environment.
- Metrics are per team and backend. Token cost in dollars is not computed.


## Observability

What the gateway reports, and what it alerts on. All of it is in `terraform/observability.tf`.

**Per request** the Lambda writes one log line in CloudWatch embedded metric format (dimensions `Team`, `Backend`; namespace `LLMGateway`):

| Metric | Meaning |
|---|---|
| `Requests`, `InputTokens`, `OutputTokens` | volume per team and backend |
| `LatencyMs`, `BackendMs` | total time in the Lambda, and the part spent waiting for the model (the difference is the gateway's own overhead) |
| `CostUsd` | **estimated** cost = tokens x `model_prices` (Bedrock only; self-hosted vLLM is 0) |
| `ServerErrors`, `BackendThrottles` | 5xx answers, and Bedrock 429s passed on to the caller |

The same line carries `RequestId`, `Model` and `Status`, so one failed request can be found in Logs Insights. Prompts and answers are never logged.

**Dashboard** `llm-gateway` (`terraform output dashboard_url`): requests, tokens and estimated cost per team, p50/p95/p99 latency (total vs. time in Lambda), 4xx/5xx counts, backend throttles, alarm states, a spend-and-latency table by team, and the latest failed requests.

**Alarms** (all notify the SNS topic `llm-gateway-alarms`; set `alarm_email` in `terraform.tfvars` and click the confirmation link AWS sends):

| Alarm | Fires when |
|---|---|
| `5xx-rate` | more than 5% of requests fail with a server error, 2 periods in a row (ignored under 5 requests per 5 min) |
| `latency-p95` | p95 latency above 15 s |
| `<team>-backend-throttles` | Bedrock throttles 10+ of a team's requests in 5 min |
| `<team>-daily-spend` / `total-daily-spend` | estimated spend above $25 per team / $50 total in 24 h (the thresholds are variables) |
| `<team>-bedrock-tokens-per-hour`, `lambda-errors` | the original token and Lambda error alarms |

**Traces:** API Gateway and the Lambda have AWS X-Ray tracing on (`enable_tracing`), so one request shows up as a trace with the time spent in each hop (CloudWatch, then Traces).

**Try it**
```powershell
cd gateway\terraform
# optional: add  alarm_email = "you@example.com"  to terraform.tfvars
terraform apply
$url = terraform output -raw base_url
$key = (terraform output -json api_keys | ConvertFrom-Json)."ml-platform"
python ..\scripts\generate_traffic.py --url $url --key $key --n 40
terraform output dashboard_url      # open it; metrics appear within a few minutes
```
While Bedrock quotas are 0 the traffic gets 429s, which lights up the backend-throttle panel (and the alarm after 10 within 5 minutes). With a reachable `vllm_base_url`, `--model vllm` produces real token counts.

**Limits to know**
- `CostUsd` is an estimate from token counts and the prices in `model_prices`. The defaults are example Nova prices: check them against the current AWS price list. The real bill is in Cost Explorer.
- Each metric and team/backend combination that receives data is a billed custom metric (8 metrics x teams x backends, so up to 32 with the defaults; check the current CloudWatch price). Only combinations that get traffic count. Run `terraform destroy` when you are done experimenting.
- The 24 h spend alarms use a 24-hour CloudWatch period; confirm on first deploy how the window lines up (UTC day vs. rolling) before relying on it.
- The Terraform in `observability.tf` was written without being able to run `terraform validate` or `plan` in the authoring environment: run both and fix anything they report. The Lambda changes are covered by unit tests.


### What the dashboard found (measured on the deployed gateway)

With Bedrock token quotas at 0, 40 test requests to the `bedrock` model all came back 429. The dashboard showed:

| | Before | After |
|---|---|---|
| Time a throttled request spent waiting on Bedrock | **7,968 ms** average over 40 requests, p95 12,557 ms (range 3.7 to 12.6 s) | **179 to 1,071 ms** in the ten newest failed requests (the table mixed old and new, so the combined average of 74 requests was 4,615 ms; the implied average for the new ones is about 670 ms, an estimate) |
| Cause | the AWS SDK retried each throttled call with growing waits before giving up, holding the caller's request open | retries capped: `bedrock_max_attempts = 2` (total tries) and timeouts below the Lambda's limit |

The throttle alarm behaved as designed: one 5-minute bucket held 11 throttled requests (threshold 10), the alarm went `OK` to `ALARM` within about 3 minutes, and returned to `OK` when the next bucket held 8. Tokens and estimated cost stayed at 0 because no call succeeded; the cost panels will show real values once Bedrock quota is restored or the `vllm` backend is used.

Trade-off: with fewer retries inside the gateway, a short real throttle reaches the caller sooner, so callers should retry on their own. Raise `bedrock_max_attempts` if you prefer the gateway to absorb brief throttles.
