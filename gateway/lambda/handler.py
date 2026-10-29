"""LLM gateway Lambda: one OpenAI-compatible endpoint in front of two backends.

    POST /v1/chat/completions    (API Gateway REST API, x-api-key required)

The request and response use the OpenAI chat-completions format, so LangChain's ChatOpenAI,
the OpenAI SDK, curl, etc. can all talk to it. Behind it:
  - "bedrock": the request is translated to the Bedrock Converse API and back (tool calls included)
  - "vllm":    the request is forwarded to a vLLM/Ollama OpenAI-compatible server

Which backend runs is picked by the request's "model" field ("bedrock", "bedrock/<model id>",
or "vllm"); anything else uses DEFAULT_BACKEND.

Guardrails (from the Bedrock runaway postmortem): max_tokens is capped, streaming is refused,
the request body is size-limited, every call is attributed to a team through its API key, and
per-team metrics are emitted to CloudWatch (embedded metric format): requests, tokens, latency
(total and time spent in the model backend), estimated cost, server errors and backend throttles.
Prompts and answers are never logged.

No third-party packages: only the standard library plus boto3 (included in the Lambda runtime).
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request
import uuid

DEFAULT_BACKEND = os.getenv("DEFAULT_BACKEND", "bedrock")
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.amazon.nova-lite-v1:0")
VLLM_BASE_URL = os.getenv("VLLM_BASE_URL", "").rstrip("/")
VLLM_MODEL = os.getenv("VLLM_MODEL", "llama3.2:3b")
VLLM_API_KEY = os.getenv("VLLM_API_KEY", "")
MAX_TOKENS_CAP = int(os.getenv("MAX_TOKENS_CAP", "1024"))
MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", "200000"))
# The AWS SDK retries a throttled call with growing waits (about 8 s on average in our measurements)
# before giving up, which holds the caller's request open. Keep it short: the caller can retry.
BEDROCK_MAX_ATTEMPTS = max(1, int(os.getenv("BEDROCK_MAX_ATTEMPTS", "2")))  # 1 = no retry
KEY_TEAMS = json.loads(os.getenv("KEY_TEAMS", "{}"))  # API key id -> team name
# Price per 1M tokens as [input_usd, output_usd], by Bedrock model id. Set from Terraform
# (variable model_prices); the defaults there are examples to check against the AWS price list.
MODEL_PRICES = json.loads(os.getenv("MODEL_PRICES", "{}"))

_FINISH = {"end_turn": "stop", "tool_use": "tool_calls", "max_tokens": "length"}


class GatewayError(Exception):
    def __init__(self, status: int, message: str, kind: str = "invalid_request_error"):
        super().__init__(message)
        self.status, self.kind = status, kind


# --------------------------------------------------------------------------------------
# OpenAI format  ->  Bedrock Converse
# --------------------------------------------------------------------------------------
def _text_of(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")


def _clamp_max_tokens(body: dict) -> int:
    asked = body.get("max_tokens") or body.get("max_completion_tokens") or MAX_TOKENS_CAP
    return max(1, min(int(asked), MAX_TOKENS_CAP))


def to_converse(body: dict) -> dict:
    """Build the keyword arguments for bedrock-runtime converse()."""
    system, turns = [], []  # turns: [role, [content blocks]]

    def add(role, blocks):
        if turns and turns[-1][0] == role:  # Converse needs alternating roles, so merge neighbours
            turns[-1][1].extend(blocks)
        else:
            turns.append([role, list(blocks)])

    for m in body["messages"]:
        role = m.get("role")
        if role in ("system", "developer"):
            text = _text_of(m.get("content"))
            if text:
                system.append({"text": text})
        elif role == "user":
            add("user", [{"text": _text_of(m.get("content")) or " "}])
        elif role == "assistant":
            blocks = []
            text = _text_of(m.get("content"))
            if text:
                blocks.append({"text": text})
            for tc in m.get("tool_calls") or []:
                args = tc["function"].get("arguments") or "{}"
                blocks.append({"toolUse": {
                    "toolUseId": tc["id"], "name": tc["function"]["name"],
                    "input": json.loads(args) if isinstance(args, str) else args,
                }})
            if blocks:
                add("assistant", blocks)
        elif role == "tool":
            add("user", [{"toolResult": {
                "toolUseId": m["tool_call_id"],
                "content": [{"text": _text_of(m.get("content")) or "(empty)"}],
            }}])
        else:
            raise GatewayError(400, f"unsupported message role: {role!r}")

    if not turns or turns[0][0] != "user":
        raise GatewayError(400, "the conversation must start with a user message")

    kwargs = {
        "messages": [{"role": r, "content": c} for r, c in turns],
        "inferenceConfig": {"maxTokens": _clamp_max_tokens(body)},
    }
    if system:
        kwargs["system"] = system
    if body.get("temperature") is not None:
        kwargs["inferenceConfig"]["temperature"] = float(body["temperature"])
    stop = body.get("stop")
    if stop:
        kwargs["inferenceConfig"]["stopSequences"] = [stop] if isinstance(stop, str) else list(stop)

    tools, choice = body.get("tools"), body.get("tool_choice", "auto")
    if tools and choice != "none":
        cfg = {"tools": [{"toolSpec": {
            "name": t["function"]["name"],
            "description": t["function"].get("description") or t["function"]["name"],
            "inputSchema": {"json": t["function"].get("parameters") or {"type": "object", "properties": {}}},
        }} for t in tools]}
        if choice == "required":
            cfg["toolChoice"] = {"any": {}}
        elif isinstance(choice, dict) and choice.get("function"):
            cfg["toolChoice"] = {"tool": {"name": choice["function"]["name"]}}
        kwargs["toolConfig"] = cfg
    return kwargs


def from_converse(resp: dict, model: str) -> dict:
    """Turn a Converse response into an OpenAI chat.completion object."""
    text, calls = [], []
    for block in resp["output"]["message"]["content"]:
        if "text" in block:
            text.append(block["text"])
        elif "toolUse" in block:
            tu = block["toolUse"]
            calls.append({"id": tu["toolUseId"], "type": "function",
                          "function": {"name": tu["name"], "arguments": json.dumps(tu["input"])}})
    message = {"role": "assistant", "content": "".join(text) or None}
    if calls:
        message["tool_calls"] = calls
    usage = resp.get("usage", {})
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:24],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message,
                     "finish_reason": _FINISH.get(resp.get("stopReason"), "stop")}],
        "usage": {"prompt_tokens": usage.get("inputTokens", 0),
                  "completion_tokens": usage.get("outputTokens", 0),
                  "total_tokens": usage.get("totalTokens", 0)},
    }


# --------------------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------------------
_bedrock = None


def bedrock_config(config_cls):
    """SDK settings for the Bedrock client: capped retries, and timeouts below the Lambda's 28 s."""
    return config_cls(retries={"total_max_attempts": BEDROCK_MAX_ATTEMPTS, "mode": "standard"},
                      connect_timeout=3, read_timeout=24)


def _bedrock_client():
    global _bedrock
    if _bedrock is None:
        import boto3  # imported here so the translation code can be tested without boto3
        from botocore.config import Config

        _bedrock = boto3.client("bedrock-runtime", config=bedrock_config(Config))
    return _bedrock


def call_bedrock(body: dict, model_id: str) -> dict:
    return from_converse(_bedrock_client().converse(modelId=model_id, **to_converse(body)), model_id)


def call_vllm(body: dict) -> dict:
    if not VLLM_BASE_URL:
        raise GatewayError(503, "the vllm backend is not configured on this gateway", "backend_unavailable")
    payload = {**body, "model": VLLM_MODEL, "stream": False, "max_tokens": _clamp_max_tokens(body)}
    payload.pop("max_completion_tokens", None)
    headers = {"content-type": "application/json"}
    if VLLM_API_KEY:
        headers["authorization"] = f"Bearer {VLLM_API_KEY}"
    req = urllib.request.Request(VLLM_BASE_URL + "/chat/completions", json.dumps(payload).encode(), headers)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise GatewayError(502, f"vllm backend returned HTTP {e.code}", "backend_error")
    except (urllib.error.URLError, TimeoutError) as e:
        raise GatewayError(504, f"vllm backend unreachable: {getattr(e, 'reason', e)}", "backend_timeout")


def pick_backend(model: str | None) -> tuple[str, str]:
    """-> (backend, model id used by that backend)."""
    m = (model or "").strip()
    if m.startswith("bedrock/"):
        return "bedrock", m.split("/", 1)[1]
    if m == "vllm":
        return "vllm", VLLM_MODEL
    if m == "bedrock":
        return "bedrock", BEDROCK_MODEL_ID
    return (DEFAULT_BACKEND, BEDROCK_MODEL_ID if DEFAULT_BACKEND == "bedrock" else VLLM_MODEL)


# --------------------------------------------------------------------------------------
# Handler
# --------------------------------------------------------------------------------------
def _reply(status: int, payload: dict) -> dict:
    return {"statusCode": status, "headers": {"content-type": "application/json"}, "body": json.dumps(payload)}


def estimate_cost(backend: str, model_id: str, usage: dict) -> tuple[float, bool]:
    """Estimated USD for one request from token counts x configured list prices.
    -> (cost, price_known). A self-hosted vLLM backend has no per-token charge, so it costs 0 here;
    a Bedrock model with no configured price also costs 0 but is flagged as unpriced."""
    if backend != "bedrock":
        return 0.0, True
    price = MODEL_PRICES.get(model_id)
    if not price:
        return 0.0, False
    cost = (usage.get("prompt_tokens", 0) * price[0] + usage.get("completion_tokens", 0) * price[1]) / 1_000_000
    return round(cost, 8), True


def _emit_metrics(team: str, backend: str, model_id: str, usage: dict, latency_ms: int,
                  backend_ms: int, status: int, request_id: str) -> None:
    """One log line that CloudWatch turns into per-team metrics (embedded metric format).
    The same line is searchable in Logs Insights (RequestId, Model, Status ...). No prompt text."""
    cost, price_known = estimate_cost(backend, model_id, usage)
    print(json.dumps({
        "_aws": {"Timestamp": int(time.time() * 1000), "CloudWatchMetrics": [{
            "Namespace": "LLMGateway", "Dimensions": [["Team", "Backend"]],
            "Metrics": [{"Name": "Requests", "Unit": "Count"}, {"Name": "InputTokens", "Unit": "Count"},
                        {"Name": "OutputTokens", "Unit": "Count"}, {"Name": "LatencyMs", "Unit": "Milliseconds"},
                        {"Name": "BackendMs", "Unit": "Milliseconds"}, {"Name": "CostUsd", "Unit": "None"},
                        {"Name": "ServerErrors", "Unit": "Count"}, {"Name": "BackendThrottles", "Unit": "Count"}],
        }]},
        "Team": team, "Backend": backend, "Requests": 1, "Status": status,
        "InputTokens": usage.get("prompt_tokens", 0), "OutputTokens": usage.get("completion_tokens", 0),
        "LatencyMs": latency_ms, "BackendMs": backend_ms, "CostUsd": cost,
        "ServerErrors": 1 if status >= 500 else 0, "BackendThrottles": 1 if status == 429 else 0,
        "RequestId": request_id, "Model": model_id, "PriceKnown": price_known,
    }))


def handler(event, context=None):
    started = time.time()
    team = KEY_TEAMS.get(((event.get("requestContext") or {}).get("identity") or {}).get("apiKeyId"), "unknown")
    backend, model_id, usage, status, backend_ms = "unknown", "unknown", {}, 500, 0
    request_id = getattr(context, "aws_request_id", None) or "local"
    try:
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode("utf-8")
        if len(raw.encode("utf-8")) > MAX_BODY_BYTES:
            raise GatewayError(413, "request body too large")
        try:
            body = json.loads(raw)
        except ValueError:
            raise GatewayError(400, "body must be valid JSON")
        if not isinstance(body.get("messages"), list) or not body["messages"]:
            raise GatewayError(400, "'messages' must be a non-empty list")
        if body.get("stream"):
            raise GatewayError(400, "streaming is not supported by this gateway")

        backend, model_id = pick_backend(body.get("model"))
        t_backend = time.time()
        try:
            out = call_bedrock(body, model_id) if backend == "bedrock" else call_vllm(body)
        finally:
            backend_ms = int((time.time() - t_backend) * 1000)
        usage, status = out.get("usage", {}), 200
        return _reply(200, out)
    except GatewayError as e:
        status = e.status
        return _reply(e.status, {"error": {"message": str(e), "type": e.kind}})
    except Exception as e:  # backend SDK errors: map the common ones, never leak internals
        code = ((getattr(e, "response", None) or {}).get("Error") or {}).get("Code", "")
        if code in ("ThrottlingException", "ServiceQuotaExceededException", "TooManyRequestsException"):
            status = 429
            return _reply(429, {"error": {"message": "the model backend is throttling requests; retry later",
                                          "type": "rate_limit_error"}})
        print(f"backend error: {type(e).__name__}: {str(e)[:300]}")
        status = 502
        return _reply(502, {"error": {"message": "the model backend failed", "type": "backend_error"}})
    finally:
        _emit_metrics(team, backend, model_id, usage, int((time.time() - started) * 1000), backend_ms, status, request_id)
