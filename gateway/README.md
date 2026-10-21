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
Cost is close to zero at demo volume (API Gateway REST, Lambda, 3 alarms). `terraform destroy` removes everything.

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
translation, max_tokens cap, error mapping (throttle becomes 429, internals never leak) and the
vLLM passthrough.

## Known limits
- No streaming (API Gateway REST waits for the full reply; 29 second limit).
- The vLLM URL must be reachable from the internet. Use a throwaway key: it lives in the Lambda environment.
- Metrics are per team and backend. Token cost in dollars is not computed.
