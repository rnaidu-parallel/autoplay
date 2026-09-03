import json
import unittest
import urllib.error
from unittest.mock import patch

from autoplay_harness.openrouter import OpenRouterClient, OpenRouterError
from autoplay_harness.tools import ACTOR_TOOLS


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class OpenRouterClientTests(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_glm_default_routing_cache_prefix_and_low_reasoning(self, urlopen):
        urlopen.return_value = _Response({"provider": "DeepInfra", "usage": {"prompt_tokens_details": {"cached_tokens": 1024}},
            "choices": [{"message": {"tool_calls": [{"function": {"name": "press", "arguments": '{"buttons":["D"],"say":"move"}'}}]}}]})
        self.assertEqual(OpenRouterClient.GLM_MODEL, OpenRouterClient.DEFAULT_MODEL)
        client = OpenRouterClient("secret", OpenRouterClient.DEFAULT_MODEL, "run")
        for context in ("Farm 9:00", "Town 10:00"):
            decision = client.choose_tool("static", context, None, ACTOR_TOOLS, stable_context="long lived notes")
        payloads = [json.loads(call.args[0].data) for call in urlopen.call_args_list]
        self.assertEqual(payloads[0]["prompt_cache_key"], payloads[1]["prompt_cache_key"])
        self.assertEqual(payloads[0]["messages"][0], payloads[1]["messages"][0])
        self.assertEqual(payloads[0]["tools"], payloads[1]["tools"])
        self.assertEqual(payloads[0]["session_id"], payloads[1]["session_id"])
        for payload in payloads:
            self.assertEqual(["deepinfra/fp8", "nextbit/fp8", "baseten/fp8"], payload["provider"]["order"])
            self.assertEqual(payload["provider"]["order"], payload["provider"]["only"])
            self.assertTrue(payload["provider"]["allow_fallbacks"])
            self.assertNotIn("sort", payload["provider"])
            self.assertEqual({"effort": "low"}, payload["reasoning"])
            self.assertNotIn("prompt_cache_options", payload)
            self.assertNotIn("prompt_cache_breakpoint", json.dumps(payload))
            self.assertEqual("long lived notes", payload["messages"][1]["content"][0]["text"])
        self.assertEqual(1024, decision.usage["prompt_tokens_details"]["cached_tokens"])
        with self.assertRaises(OpenRouterError):
            client.choose_tool("static", "state", None, ACTOR_TOOLS, reasoning_effort="medium")

    @patch("urllib.request.urlopen")
    def test_system_message_and_tools_serialize_identically(self, urlopen):
        urlopen.return_value = _Response({"provider": "OpenAI", "choices": [{"message": {"tool_calls": [{"function": {"name": "press", "arguments": '{"buttons":["D"],"say":"move"}'}}]}}]})
        client = OpenRouterClient("secret", OpenRouterClient.LUNA_MODEL, "run")
        tools = [{"function": {"parameters": {"b": 2, "a": 1}, "name": "press"}, "type": "function"}]
        client.choose_tool("system", "dynamic one", None, tools, cache_namespace="director")
        client.choose_tool("system", "dynamic two", None, tools, cache_namespace="director")
        payloads = [json.loads(call.args[0].data) for call in urlopen.call_args_list]
        self.assertEqual(json.dumps(payloads[0]["messages"][0], sort_keys=True, separators=(",", ":")), json.dumps(payloads[1]["messages"][0], sort_keys=True, separators=(",", ":")))
        self.assertEqual(json.dumps(payloads[0]["tools"], sort_keys=True, separators=(",", ":")), json.dumps(payloads[1]["tools"], sort_keys=True, separators=(",", ":")))
    @patch("urllib.request.urlopen")
    def test_official_trials_pin_provider_and_low_reasoning(self, urlopen):
        for model, slug, provider in (
            (OpenRouterClient.GEMINI_FLASH_MODEL, "google-ai-studio", "Google AI Studio"),
            (OpenRouterClient.LUNA_MODEL, "openai", "OpenAI"),
        ):
            with self.subTest(model=model):
                urlopen.return_value = _Response({"provider": provider, "choices": [{"message": {"tool_calls": [{"function": {
                    "name": "press", "arguments": '{"buttons":["D"],"say":"move"}'}}]}}]})
                client = OpenRouterClient("secret", model, "run", reasoning_effort="low")
                decision = client.choose_tool("prompt", "{}", "image", ACTOR_TOOLS)
                payload = json.loads(urlopen.call_args.args[0].data)
                self.assertEqual([slug], payload["provider"]["only"])
                self.assertFalse(payload["provider"]["allow_fallbacks"])
                self.assertTrue(payload["provider"]["require_parameters"])
                self.assertEqual({"effort": "low"}, payload["reasoning"])
                self.assertEqual("required", payload["tool_choice"])
                self.assertEqual(600, payload["max_tokens"])
                self.assertEqual(provider, decision.provider)
                if model == OpenRouterClient.LUNA_MODEL:
                    self.assertNotIn("temperature", payload)
                    self.assertEqual({"mode": "explicit", "ttl": "30m"}, payload["prompt_cache_options"])
                    self.assertEqual({"mode": "explicit"}, payload["messages"][0]["content"][0]["prompt_cache_breakpoint"])

    @patch("urllib.request.urlopen")
    def test_luna_cache_key_reuses_static_prefix_across_runs_without_caching_dynamic_context(self, urlopen):
        urlopen.return_value = _Response({"provider": "OpenAI", "choices": [{"message": {"tool_calls": [{"function": {
            "name": "press", "arguments": '{"buttons":["D"],"say":"move"}'}}]}}]})
        for run_id, context in (("first", "state-1"), ("second", "state-2")):
            OpenRouterClient("secret", OpenRouterClient.LUNA_MODEL, run_id).choose_tool("stable", context, None, ACTOR_TOOLS)
        payloads = [json.loads(call.args[0].data) for call in urlopen.call_args_list]
        self.assertEqual(payloads[0]["prompt_cache_key"], payloads[1]["prompt_cache_key"])
        self.assertNotEqual(payloads[0]["session_id"], payloads[1]["session_id"])
        for payload in payloads:
            self.assertEqual(["text"], [block["type"] for block in payload["messages"][1]["content"]])
            self.assertNotIn("prompt_cache_breakpoint", payload["messages"][1]["content"][0])

    @patch("urllib.request.urlopen")
    def test_stable_objective_cache_boundary_excludes_changing_observation(self, urlopen):
        urlopen.return_value = _Response({"provider": "OpenAI", "choices": [{"message": {"tool_calls": [{"function": {
            "name": "press", "arguments": '{"buttons":["D"],"say":"move"}'}}]}}]})
        client = OpenRouterClient("secret", OpenRouterClient.LUNA_MODEL, "run")
        for context in ("state-1", "state-2"):
            client.choose_tool("static", context, None, ACTOR_TOOLS, stable_context="plant five")
        payloads = [json.loads(call.args[0].data) for call in urlopen.call_args_list]
        self.assertEqual(payloads[0]["prompt_cache_key"], payloads[1]["prompt_cache_key"])
        content = payloads[1]["messages"][1]["content"]
        self.assertEqual("plant five", content[0]["text"])
        self.assertEqual({"mode": "explicit"}, content[0]["prompt_cache_breakpoint"])
        self.assertEqual({"type": "text", "text": "state-2"}, content[1])

    @patch("urllib.request.urlopen")
    def test_luna_cache_key_ignores_changing_stable_block_and_marks_both_prefixes(self, urlopen):
        # Runs 7a81817b/baa41570 hit the cache 32-37% because the key changed with location, time and ledger.
        urlopen.return_value = _Response({"provider": "OpenAI", "choices": [{"message": {"tool_calls": [{"function": {
            "name": "press", "arguments": '{"buttons":["D"],"say":"move"}'}}]}}]})
        client = OpenRouterClient("secret", OpenRouterClient.LUNA_MODEL, "run")
        for stable in ("Farm 06:00 plant five", "Town 09:10 visit Pierre"):
            client.choose_tool("static", "state", None, ACTOR_TOOLS, stable_context=stable)
        client.choose_tool("other instructions", "state", None, ACTOR_TOOLS, stable_context="Farm 06:00 plant five")
        payloads = [json.loads(call.args[0].data) for call in urlopen.call_args_list]
        self.assertEqual(payloads[0]["prompt_cache_key"], payloads[1]["prompt_cache_key"])
        self.assertNotEqual(payloads[0]["prompt_cache_key"], payloads[2]["prompt_cache_key"])
        for payload in payloads:
            self.assertEqual({"mode": "explicit"}, payload["messages"][0]["content"][0]["prompt_cache_breakpoint"])
            self.assertEqual({"mode": "explicit"}, payload["messages"][1]["content"][0]["prompt_cache_breakpoint"])
            self.assertNotIn("prompt_cache_breakpoint", payload["messages"][1]["content"][1])

    @patch("urllib.request.urlopen")
    def test_per_request_reasoning_effort_overrides_client_default(self, urlopen):
        urlopen.return_value = _Response({"provider": "OpenAI", "choices": [{"message": {"tool_calls": [{"function": {
            "name": "press", "arguments": '{"buttons":["D"],"say":"move"}'}}]}}]})
        client = OpenRouterClient("secret", OpenRouterClient.LUNA_MODEL, "run")
        client.choose_tool("static", "state", None, ACTOR_TOOLS)
        client.choose_tool("static", "state", None, ACTOR_TOOLS, reasoning_effort="medium", max_tokens=2400)
        client.choose_tool("static", "state", None, ACTOR_TOOLS)
        payloads = [json.loads(call.args[0].data) for call in urlopen.call_args_list]
        self.assertEqual({"effort": "low"}, payloads[0]["reasoning"])
        self.assertEqual({"effort": "medium"}, payloads[1]["reasoning"])
        self.assertEqual([600, 2400, 600], [payload["max_tokens"] for payload in payloads])

    @patch("urllib.request.urlopen")
    def test_official_trial_rejects_wrong_or_missing_provider_without_response_retry(self, urlopen):
        for provider in ("Azure", "Amazon Bedrock", None):
            with self.subTest(provider=provider):
                urlopen.reset_mock()
                urlopen.return_value = _Response({"provider": provider, "usage": {"cost": 0.01}, "choices": [{"message": {"tool_calls": [{"function": {
                    "name": "press", "arguments": '{"buttons":["D"],"say":"move"}'}}]}}]})
                client = OpenRouterClient("secret", OpenRouterClient.LUNA_MODEL, "run")
                with self.assertRaises(OpenRouterError) as caught:
                    client.choose_tool("prompt", "{}", "image", ACTOR_TOOLS)
                self.assertEqual(1, urlopen.call_count)
                self.assertEqual("provider_rejected", caught.exception.attempts[0]["outcome"])
                self.assertEqual(0.01, caught.exception.usage["cost"])

    @patch("urllib.request.urlopen")
    def test_qwen_uses_alibaba_and_explicit_low_reasoning(self, urlopen):
        urlopen.return_value = _Response({"choices": [{"message": {"tool_calls": [{"function": {
            "name": "press", "arguments": '{"buttons":["D"],"say":"move"}'}}]}}]})
        client = OpenRouterClient("secret", OpenRouterClient.QWEN_MODEL, "run", reasoning_effort="low")
        client.choose_tool("prompt", "{}", "image", ACTOR_TOOLS)
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual({"effort": "low"}, payload["reasoning"])
        self.assertEqual(["alibaba"], payload["provider"]["only"])
        self.assertTrue(payload["provider"]["require_parameters"])
        self.assertFalse(payload["provider"]["allow_fallbacks"])
        self.assertEqual(600, payload["max_tokens"])
        self.assertEqual("auto", payload["tool_choice"])

    @patch("urllib.request.urlopen")
    def test_gemini_requires_tool_output_without_broadening_provider_policy(self, urlopen):
        urlopen.return_value = _Response({"choices": [{"message": {"tool_calls": [{"function": {
            "name": "press", "arguments": '{"buttons":["D"],"say":"move"}'}}]}}]})
        client = OpenRouterClient("secret", OpenRouterClient.GEMINI_MODEL, "run")
        client.choose_tool("prompt", "{}", "image", ACTOR_TOOLS)
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual("required", payload["tool_choice"])
        self.assertEqual(["google-ai-studio"], payload["provider"]["only"])
        self.assertFalse(payload["provider"]["allow_fallbacks"])
        self.assertEqual(600, payload["max_tokens"])
        self.assertNotIn("reasoning", payload)

    @patch("autoplay_harness.openrouter.time.sleep")
    @patch("urllib.request.urlopen")
    def test_retry_diagnostics_preserve_rejections_http_attempts_and_reasoning(self, urlopen, _sleep):
        urlopen.side_effect = [
            urllib.error.HTTPError("url", 429, "busy", {}, None),
            _Response({"id": "truncated", "provider": "Z.AI", "usage": {
                "completion_tokens": 600, "completion_tokens_details": {"reasoning_tokens": 599}},
                "choices": [{"finish_reason": "length", "native_finish_reason": "length", "message": {}}]}),
            _Response({"id": "valid", "usage": {"completion_tokens": 20}, "choices": [{
                "finish_reason": "tool_calls", "message": {"tool_calls": [{"function": {
                    "name": "press", "arguments": '{"buttons":["D"],"say":"move"}'}}]}}]}),
        ]
        client = OpenRouterClient("secret", OpenRouterClient.GLM_MODEL, "run")
        with patch.object(client.cancelled, "wait"):
            decision = client.choose_tool("prompt", "{}", "image", ACTOR_TOOLS)
        self.assertEqual(["http_error", "rejected", "accepted"], [a["outcome"] for a in decision.attempts])
        self.assertEqual("length", decision.attempts[1]["finish_reason"])
        self.assertEqual("truncated", decision.attempts[1]["response_id"])
        self.assertEqual(599, decision.usage["completion_tokens_details"]["reasoning_tokens"])
        self.assertEqual(620, decision.usage["completion_tokens"])
        self.assertNotIn("secret", json.dumps(decision.attempts))

    @patch("autoplay_harness.openrouter.time.sleep")
    @patch("urllib.request.urlopen")
    def test_failed_logical_call_keeps_billable_usage_and_all_reasons(self, urlopen, _sleep):
        urlopen.return_value = _Response({"usage": {"cost": 0.01}, "choices": [{"message": {}}]})
        client = OpenRouterClient("secret", OpenRouterClient.GLM_MODEL, "run")
        with self.assertRaises(OpenRouterError) as caught:
            client.choose_tool("prompt", "{}", "image", ACTOR_TOOLS)
        self.assertEqual(3, len(caught.exception.attempts))
        self.assertAlmostEqual(0.03, caught.exception.usage["cost"])

    @patch("urllib.request.urlopen")
    def test_request_has_stable_system_prefix_image_tools_and_session(self, urlopen) -> None:
        urlopen.return_value = _Response(
            {
                "model": "test/model",
                "usage": {"prompt_tokens": 12},
                "choices": [
                    {
                        "message": {
                            "content": "Waiting for the menu.",
                            "reasoning": "The title menu has not settled.",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "wait",
                                        "arguments": '{"field":"menu"}',
                                    }
                                }
                            ]
                        }
                    }
                ],
            }
        )
        client = OpenRouterClient("secret", OpenRouterClient.MINIMAX_MODEL, "run-1")
        decision = client.choose_tool(
            "stable prompt",
            '{"dynamic":true}',
            "data:image/jpeg;base64,abc",
            [{"type": "function", "function": {"name": "wait"}}],
        )

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual("Waiting for the menu.", decision.content)
        self.assertEqual("The title menu has not settled.", decision.reasoning)
        self.assertEqual(600, payload["max_tokens"])
        self.assertEqual("stable prompt", payload["messages"][0]["content"])
        self.assertEqual("data:image/jpeg;base64,abc", payload["messages"][1]["content"][1]["image_url"]["url"])
        self.assertEqual("run-1:agent", payload["session_id"])
        self.assertEqual("auto", payload["tool_choice"])
        self.assertNotIn("parallel_tool_calls", payload)
        self.assertEqual(["gmicloud/fp8"], payload["provider"]["only"])
        self.assertNotIn("reasoning", payload)
        self.assertEqual(["fp8"], payload["provider"]["quantizations"])
        self.assertNotIn("sort", payload["provider"])
        self.assertFalse(payload["provider"]["allow_fallbacks"])
        self.assertTrue(payload["provider"]["require_parameters"])
        self.assertEqual("wait", decision.name)

    @patch("urllib.request.urlopen")
    def test_cache_namespace_is_part_of_sticky_session(self, urlopen) -> None:
        urlopen.return_value = _Response(
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "wait",
                                        "arguments": '{"field":"menu","value":"none","timeout_ticks":1,"say":"wait"}',
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        )
        client = OpenRouterClient("secret", OpenRouterClient.MINIMAX_MODEL, "run-1")
        client.choose_tool("prompt", "{}", "data:image/jpeg;base64,abc", ACTOR_TOOLS, cache_namespace="director")
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual("run-1:director", payload["session_id"])

    @patch("urllib.request.urlopen")
    def test_only_first_parallel_tool_call_is_used(self, urlopen) -> None:
        urlopen.return_value = _Response(
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {"function": {"name": "press", "arguments": '{"buttons":["A"],"say":"move"}'}},
                                {"function": {"name": "press", "arguments": '{"buttons":["S"],"say":"move"}'}},
                            ]
                        }
                    }
                ]
            }
        )
        client = OpenRouterClient("secret", OpenRouterClient.MINIMAX_MODEL, "run-1")
        decision = client.choose_tool("prompt", "{}", "data:image/jpeg;base64,abc", ACTOR_TOOLS)
        self.assertEqual("press", decision.name)
        self.assertEqual(["A"], decision.arguments["buttons"])

    @patch("autoplay_harness.openrouter.time.sleep")
    @patch("urllib.request.urlopen")
    def test_retries_missing_tool_call_and_accumulates_usage(self, urlopen, _sleep) -> None:
        urlopen.side_effect = [
            _Response(
                {
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 5,
                        "prompt_tokens_details": {"cached_tokens": 20},
                    },
                    "choices": [{"message": {"content": "I should use a tool."}}],
                }
            ),
            _Response(
                {
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 8,
                        "prompt_tokens_details": {"cached_tokens": 80},
                    },
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {"function": {"name": "press", "arguments": '{"buttons":["D"],"say":"move"}'}}
                                ]
                            }
                        }
                    ],
                }
            ),
        ]
        client = OpenRouterClient("secret", OpenRouterClient.MINIMAX_MODEL, "run-1")
        decision = client.choose_tool("prompt", "{}", "data:image/jpeg;base64,abc", ACTOR_TOOLS)
        self.assertEqual("press", decision.name)
        self.assertEqual(200, decision.usage["prompt_tokens"])
        self.assertEqual(100, decision.usage["prompt_tokens_details"]["cached_tokens"])

    @patch("autoplay_harness.openrouter.time.sleep")
    @patch("urllib.request.urlopen")
    def test_retries_missing_required_tool_argument(self, urlopen, _sleep) -> None:
        urlopen.side_effect = [
            _Response(
                {
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {
                                        "function": {
                                            "name": "objective_progress",
                                            "arguments": '{"note\\\"":"Moved","evidence":"tile changed"}',
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                }
            ),
            _Response(
                {
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {
                                        "function": {
                                            "name": "objective_progress",
                                            "arguments": '{"note":"Moved","evidence":"tile changed","say":"progress"}',
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                }
            ),
        ]
        client = OpenRouterClient("secret", OpenRouterClient.MINIMAX_MODEL, "run-1")
        decision = client.choose_tool("prompt", "{}", "data:image/jpeg;base64,abc", ACTOR_TOOLS)
        self.assertEqual("Moved", decision.arguments["note"])

    def test_rejects_out_of_enum_wait_field(self) -> None:
        wait_tool = next(tool["function"] for tool in ACTOR_TOOLS if tool["function"]["name"] == "wait")
        errors = OpenRouterClient._argument_errors(
            {"field": "simulationPaused", "value": False, "timeout_ticks": 120},
            wait_tool["parameters"],
        )
        self.assertIn("field is outside its enum", errors)

    @patch("urllib.request.urlopen")
    def test_glm_uses_low_reasoning_and_only_approved_providers(self, urlopen) -> None:
        urlopen.return_value = _Response({
            "model": OpenRouterClient.GLM_MODEL,
            "provider": "Z.AI",
            "choices": [{"message": {"tool_calls": [{"function": {
                "name": "hold", "arguments": '{"buttons":["D"],"ticks":120,"say":"move"}'
            }}]}}],
        })
        client = OpenRouterClient("secret", OpenRouterClient.GLM_MODEL, "run-1")
        for context in ('{"step":1}', '{"step":2}'):
            decision = client.choose_tool("stable prompt", context, "data:image/jpeg;base64,abc", ACTOR_TOOLS)
        payloads = [json.loads(call.args[0].data) for call in urlopen.call_args_list]
        for payload in payloads:
            self.assertEqual({"effort": "low"}, payload["reasoning"])
            self.assertEqual(["deepinfra/fp8", "nextbit/fp8", "baseten/fp8"], payload["provider"]["only"])
            self.assertEqual(payload["provider"]["only"], payload["provider"]["order"])
            self.assertTrue(payload["provider"]["allow_fallbacks"])
            self.assertTrue(payload["provider"]["require_parameters"])
        self.assertEqual(payloads[0]["messages"][0], payloads[1]["messages"][0])
        self.assertEqual(payloads[0]["tools"], payloads[1]["tools"])
        self.assertEqual(payloads[0]["session_id"], payloads[1]["session_id"])
        self.assertEqual("Z.AI", decision.provider)

    def test_rejects_unconfigured_model_and_unsupported_glm_effort(self) -> None:
        with self.assertRaises(OpenRouterError):
            OpenRouterClient("secret", "unknown/model", "run-1")
        with self.assertRaises(OpenRouterError):
            OpenRouterClient("secret", OpenRouterClient.GLM_MODEL, "run-1", reasoning_effort="medium")


if __name__ == "__main__":
    unittest.main()
