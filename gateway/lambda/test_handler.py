"""Offline tests for the gateway Lambda. Run:  python -m unittest test_handler -v
No AWS account, network or boto3 needed: the Bedrock client and the vLLM call are faked."""
import io
import json
import os
import unittest
from unittest import mock

os.environ["KEY_TEAMS"] = json.dumps({"key123": "ml-platform"})
os.environ["VLLM_BASE_URL"] = "http://vllm.example/v1"
os.environ["MAX_TOKENS_CAP"] = "256"
os.environ["MODEL_PRICES"] = json.dumps({"us.amazon.nova-lite-v1:0": [0.06, 0.24]})
import handler as h  # noqa: E402

TOOL = {"type": "function", "function": {"name": "cost_by_service_tool", "description": "spend",
        "parameters": {"type": "object", "properties": {"days": {"type": "integer"}}}}}


def event(body, key="key123"):
    return {"body": json.dumps(body), "requestContext": {"identity": {"apiKeyId": key}}}


class FakeBedrock:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def converse(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.response


def text_resp(text="hi", i=10, o=3):
    return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}},
            "stopReason": "end_turn", "usage": {"inputTokens": i, "outputTokens": o, "totalTokens": i + o}}


class ToConverse(unittest.TestCase):
    def test_system_and_user(self):
        k = h.to_converse({"messages": [{"role": "system", "content": "be brief"},
                                        {"role": "user", "content": "hello"}]})
        self.assertEqual(k["system"], [{"text": "be brief"}])
        self.assertEqual(k["messages"], [{"role": "user", "content": [{"text": "hello"}]}])
        self.assertNotIn("toolConfig", k)

    def test_max_tokens_is_capped(self):
        k = h.to_converse({"messages": [{"role": "user", "content": "x"}], "max_tokens": 99999})
        self.assertEqual(k["inferenceConfig"]["maxTokens"], 256)
        k = h.to_converse({"messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(k["inferenceConfig"]["maxTokens"], 256)

    def test_tool_round_trip_messages(self):
        k = h.to_converse({"messages": [
            {"role": "user", "content": "top costs?"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "t1", "type": "function", "function": {"name": "cost_by_service_tool", "arguments": "{\"days\": 30}"}},
                {"id": "t2", "type": "function", "function": {"name": "untagged_resources_tool", "arguments": ""}}]},
            {"role": "tool", "tool_call_id": "t1", "content": "{\"total\": 1}"},
            {"role": "tool", "tool_call_id": "t2", "content": ""},
        ], "tools": [TOOL]})
        a = k["messages"][1]
        self.assertEqual(a["role"], "assistant")
        self.assertEqual(a["content"][0]["toolUse"], {"toolUseId": "t1", "name": "cost_by_service_tool", "input": {"days": 30}})
        self.assertEqual(a["content"][1]["toolUse"]["input"], {})
        # both tool results merge into ONE user turn
        self.assertEqual(len(k["messages"]), 3)
        results = k["messages"][2]["content"]
        self.assertEqual([r["toolResult"]["toolUseId"] for r in results], ["t1", "t2"])
        self.assertEqual(results[1]["toolResult"]["content"][0]["text"], "(empty)")
        self.assertEqual(k["toolConfig"]["tools"][0]["toolSpec"]["name"], "cost_by_service_tool")
        self.assertIn("json", k["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"])

    def test_tool_choice_variants(self):
        base = {"messages": [{"role": "user", "content": "x"}], "tools": [TOOL]}
        self.assertEqual(h.to_converse({**base, "tool_choice": "required"})["toolConfig"]["toolChoice"], {"any": {}})
        named = {"type": "function", "function": {"name": "cost_by_service_tool"}}
        self.assertEqual(h.to_converse({**base, "tool_choice": named})["toolConfig"]["toolChoice"],
                         {"tool": {"name": "cost_by_service_tool"}})
        self.assertNotIn("toolConfig", h.to_converse({**base, "tool_choice": "none"}))

    def test_must_start_with_user(self):
        with self.assertRaises(h.GatewayError):
            h.to_converse({"messages": [{"role": "assistant", "content": "hi"}]})


class FromConverse(unittest.TestCase):
    def test_text(self):
        o = h.from_converse(text_resp("hello", 5, 2), "m")
        self.assertEqual(o["choices"][0]["message"]["content"], "hello")
        self.assertEqual(o["choices"][0]["finish_reason"], "stop")
        self.assertEqual(o["usage"], {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7})
        self.assertNotIn("tool_calls", o["choices"][0]["message"])

    def test_tool_use(self):
        resp = {"output": {"message": {"content": [{"toolUse": {"toolUseId": "t9", "name": "cost_by_service_tool", "input": {"days": 7}}}]}},
                "stopReason": "tool_use", "usage": {}}
        o = h.from_converse(resp, "m")
        c = o["choices"][0]
        self.assertEqual(c["finish_reason"], "tool_calls")
        self.assertIsNone(c["message"]["content"])
        self.assertEqual(c["message"]["tool_calls"][0]["function"], {"name": "cost_by_service_tool", "arguments": "{\"days\": 7}"})


class Metrics(unittest.TestCase):
    def setUp(self):
        h._bedrock = None

    def emitted(self, ev, ctx=None):
        with mock.patch("builtins.print") as pr:
            r = h.handler(ev, ctx)
        return r, json.loads(pr.call_args[0][0])

    def test_cost_is_tokens_times_configured_price(self):
        h._bedrock = FakeBedrock(text_resp("x", 1_000_000, 500_000))
        _, m = self.emitted(event({"model": "bedrock", "messages": [{"role": "user", "content": "x"}]}))
        self.assertAlmostEqual(m["CostUsd"], 0.06 + 0.12, places=6)  # 1M in x $0.06 + 0.5M out x $0.24
        self.assertTrue(m["PriceKnown"])

    def test_unpriced_model_costs_zero_and_is_flagged(self):
        h._bedrock = FakeBedrock(text_resp("x", 1000, 1000))
        _, m = self.emitted(event({"model": "bedrock/some.other-model", "messages": [{"role": "user", "content": "x"}]}))
        self.assertEqual((m["CostUsd"], m["PriceKnown"]), (0.0, False))

    def test_vllm_has_no_per_token_cost(self):
        def fake(req, timeout=0):
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": "a"}}], "usage": {"prompt_tokens": 9, "completion_tokens": 9}}).encode())
        with mock.patch("urllib.request.urlopen", fake):
            _, m = self.emitted(event({"model": "vllm", "messages": [{"role": "user", "content": "x"}]}))
        self.assertEqual((m["Backend"], m["CostUsd"], m["PriceKnown"]), ("vllm", 0.0, True))

    def test_error_and_throttle_flags(self):
        err = Exception("slow")
        err.response = {"Error": {"Code": "ThrottlingException"}}
        h._bedrock = FakeBedrock(error=err)
        r, m = self.emitted(event({"model": "bedrock", "messages": [{"role": "user", "content": "x"}]}))
        self.assertEqual((r["statusCode"], m["BackendThrottles"], m["ServerErrors"], m["CostUsd"]), (429, 1, 0, 0.0))
        h._bedrock = FakeBedrock(error=RuntimeError("boom"))
        r, m = self.emitted(event({"model": "bedrock", "messages": [{"role": "user", "content": "x"}]}))
        self.assertEqual((r["statusCode"], m["BackendThrottles"], m["ServerErrors"]), (502, 0, 1))
        r, m = self.emitted(event({"messages": []}))  # a bad request is the caller's fault: not a server error
        self.assertEqual((r["statusCode"], m["ServerErrors"]), (400, 0))

    def test_backend_time_request_id_and_no_prompt_text(self):
        class Ctx:
            aws_request_id = "req-abc"
        h._bedrock = FakeBedrock(text_resp("secret answer"))
        r, m = self.emitted(event({"model": "bedrock", "messages": [{"role": "user", "content": "secret question"}]}), Ctx())
        self.assertEqual(m["RequestId"], "req-abc")
        self.assertLessEqual(m["BackendMs"], m["LatencyMs"])
        self.assertNotIn("secret", json.dumps(m))
        declared = {x["Name"] for x in m["_aws"]["CloudWatchMetrics"][0]["Metrics"]}
        self.assertTrue(declared <= set(m), declared - set(m))  # every declared metric has a value in the line


class BedrockClientConfig(unittest.TestCase):
    def test_retries_are_capped_and_timeouts_fit_inside_the_lambda(self):
        seen = {}
        cfg = h.bedrock_config(lambda **kw: seen.update(kw) or kw)
        self.assertEqual(cfg["retries"], {"total_max_attempts": h.BEDROCK_MAX_ATTEMPTS, "mode": "standard"})
        self.assertLessEqual(h.BEDROCK_MAX_ATTEMPTS, 3)
        self.assertLess(cfg["read_timeout"], 28)  # Lambda timeout is 28 s


class Handler(unittest.TestCase):
    def setUp(self):
        h._bedrock = None

    def test_bedrock_ok_and_metrics(self):
        h._bedrock = FakeBedrock(text_resp("pong", 12, 4))
        with mock.patch("builtins.print") as pr:
            r = h.handler(event({"model": "bedrock", "messages": [{"role": "user", "content": "ping"}]}))
        self.assertEqual(r["statusCode"], 200)
        self.assertEqual(json.loads(r["body"])["choices"][0]["message"]["content"], "pong")
        self.assertEqual(h._bedrock.calls[0]["modelId"], h.BEDROCK_MODEL_ID)
        m = json.loads(pr.call_args[0][0])
        self.assertEqual((m["Team"], m["Backend"], m["InputTokens"], m["OutputTokens"]), ("ml-platform", "bedrock", 12, 4))

    def test_model_override_and_unknown_team(self):
        h._bedrock = FakeBedrock(text_resp())
        h.handler(event({"model": "bedrock/us.amazon.nova-micro-v1:0", "messages": [{"role": "user", "content": "x"}]}, key="nope"))
        self.assertEqual(h._bedrock.calls[0]["modelId"], "us.amazon.nova-micro-v1:0")

    def test_bad_requests(self):
        for body, code in [({"messages": []}, 400), ({"messages": [{"role": "user", "content": "x"}], "stream": True}, 400)]:
            self.assertEqual(h.handler(event(body))["statusCode"], code)
        self.assertEqual(h.handler({"body": "not json"})["statusCode"], 400)
        self.assertEqual(h.handler({"body": "x" * (h.MAX_BODY_BYTES + 1)})["statusCode"], 413)

    def test_throttle_maps_to_429(self):
        err = Exception("slow down")
        err.response = {"Error": {"Code": "ThrottlingException"}}
        h._bedrock = FakeBedrock(error=err)
        r = h.handler(event({"model": "bedrock", "messages": [{"role": "user", "content": "x"}]}))
        self.assertEqual(r["statusCode"], 429)
        self.assertEqual(json.loads(r["body"])["error"]["type"], "rate_limit_error")

    def test_other_backend_error_is_502_without_leaking(self):
        h._bedrock = FakeBedrock(error=RuntimeError("secret internal detail"))
        r = h.handler(event({"model": "bedrock", "messages": [{"role": "user", "content": "x"}]}))
        self.assertEqual(r["statusCode"], 502)
        self.assertNotIn("secret", r["body"])

    def test_vllm_passthrough(self):
        seen = {}

        def fake_urlopen(req, timeout=0):
            seen["url"], seen["body"] = req.full_url, json.loads(req.data)
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": "local"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 1}}).encode())

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            r = h.handler(event({"model": "vllm", "max_tokens": 5000, "messages": [{"role": "user", "content": "x"}]}))
        self.assertEqual(r["statusCode"], 200)
        self.assertEqual(seen["url"], "http://vllm.example/v1/chat/completions")
        self.assertEqual(seen["body"]["max_tokens"], 256)
        self.assertEqual(seen["body"]["model"], h.VLLM_MODEL)
        self.assertFalse(seen["body"]["stream"])


if __name__ == "__main__":
    unittest.main()
