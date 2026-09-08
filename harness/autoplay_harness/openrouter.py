from __future__ import annotations

import json
import hashlib
import pathlib
import re
import time
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class OpenRouterError(RuntimeError):
    def __init__(self, message: str, attempts: list[dict[str, Any]] | None = None, usage: dict[str, Any] | None = None):
        super().__init__(message)
        self.attempts = attempts or []
        self.usage = usage or {}


@dataclass(frozen=True)
class ToolDecision:
    name: str
    arguments: dict[str, Any]
    usage: dict[str, Any]
    model: str | None
    content: str | None = None
    reasoning: str | None = None
    provider: str | None = None
    attempts: tuple[dict[str, Any], ...] = ()
    call_id: str | None = None


def _message_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, list):
        text = " ".join(part.get("text", "") for part in value if isinstance(part, dict))
        return text or None
    return None


class OpenRouterClient:
    API_URL = "https://openrouter.ai/api/v1/chat/completions"
    MINIMAX_MODEL = "minimax/minimax-m3:free"
    GLM_MODEL = "z-ai/glm-5.3-flash"
    GEMINI_MODEL = "google/gemini-2.5-flash-lite"
    QWEN_MODEL = "qwen/qwen3.8-flash"
    GEMINI_FLASH_MODEL = "google/gemini-3.7-flash"
    GEMINI_38_MODEL = "google/gemini-3.8-flash"
    LUNA_MODEL = "openai/gpt-5.6-luna"
    MUSE_MODEL = "meta/muse-spark-1.3-contributor"
    DEFAULT_MODEL = LUNA_MODEL
    OFFICIAL_PROVIDERS = {
        MUSE_MODEL: "Meta",
        GEMINI_38_MODEL: "Google AI Studio",
        GEMINI_FLASH_MODEL: "Google AI Studio",
        LUNA_MODEL: "OpenAI",
    }
    PROVIDER_PREFERENCES = {
        MUSE_MODEL: {
            "only": ["meta"],
            "allow_fallbacks": False,
            "require_parameters": True,
        },
        GEMINI_38_MODEL: {
            "only": ["google-ai-studio"],
            "allow_fallbacks": False,
            "require_parameters": True,
        },
        GEMINI_FLASH_MODEL: {
            "only": ["google-ai-studio"],
            "allow_fallbacks": False,
            "require_parameters": True,
        },
        LUNA_MODEL: {
            "only": ["openai"],
            "allow_fallbacks": False,
            "require_parameters": True,
        },
        QWEN_MODEL: {
            "only": ["alibaba"],
            "allow_fallbacks": False,
            "require_parameters": True,
        },
        GEMINI_MODEL: {
            "only": ["google-ai-studio"],
            "allow_fallbacks": False,
            "require_parameters": True,
        },
        MINIMAX_MODEL: {
            "only": ["gmicloud/fp8"],
            "quantizations": ["fp8"],
            "allow_fallbacks": False,
            "require_parameters": True,
        },
        GLM_MODEL: {
            "order": ["deepinfra/fp8", "nextbit/fp8", "baseten/fp8"],
            "only": ["deepinfra/fp8", "nextbit/fp8", "baseten/fp8"],
            "quantizations": ["fp8"],
            "allow_fallbacks": True,
            "require_parameters": True,
        },
    }

    def __init__(
        self,
        api_key: str,
        model: str,
        run_id: str,
        timeout_seconds: int = 45,
        max_tokens: int = 600,
        decision_budget_seconds: int = 150,
        reasoning_effort: str | None = "low",
    ) -> None:
        if not api_key:
            raise OpenRouterError("OPENROUTER_API_KEY is required for autonomous runs.")
        if not model:
            raise OpenRouterError("OPENROUTER_MODEL is required for autonomous runs.")
        if model not in self.PROVIDER_PREFERENCES:
            raise OpenRouterError(f"No provider policy is configured for {model!r}.")
        self.validate_effort(model, reasoning_effort)
        self.api_key = api_key
        self.model = model
        self.run_id = run_id
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.decision_budget_seconds = decision_budget_seconds
        self.reasoning_effort = reasoning_effort if model in {self.GLM_MODEL, self.QWEN_MODEL, self.GEMINI_FLASH_MODEL, self.GEMINI_38_MODEL, self.LUNA_MODEL, self.MUSE_MODEL} else None
        self.cancelled = threading.Event()

    @classmethod
    def validate_effort(cls, model: str, reasoning_effort: str | None) -> None:
        if model == cls.GLM_MODEL and reasoning_effort not in {"low", "high", "max"}:
            raise OpenRouterError("GLM 5.3 Flash supports reasoning effort low, high, or max.")
        if model == cls.QWEN_MODEL and reasoning_effort not in {"low", "medium", "high", "max"}:
            raise OpenRouterError("Qwen 3.8 Flash requires a supported reasoning effort; use low for gameplay trials.")
        if model in {cls.GEMINI_FLASH_MODEL, cls.GEMINI_38_MODEL} and reasoning_effort not in {"low", "medium", "high"}:
            raise OpenRouterError("Gemini Flash supports low, medium, or high reasoning effort.")
        if model == cls.LUNA_MODEL and reasoning_effort not in {"low", "medium", "high", "max"}:
            raise OpenRouterError("Use a supported Luna reasoning effort; low is the gameplay trial default.")
        if model == cls.MUSE_MODEL and reasoning_effort not in {"low", "medium", "high"}:
            raise OpenRouterError("Muse Spark supports low, medium, or high reasoning effort.")

    def choose_tool(
        self,
        system_prompt: str,
        dynamic_context: str,
        image_data_url: str | None,
        tools: list[dict[str, Any]],
        cache_namespace: str = "agent",
        stable_context: str | None = None,
        reasoning_effort: str | None = None,
        max_tokens: int | None = None,
    ) -> ToolDecision:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        *([{"type": "text", "text": stable_context}] if stable_context else []),
                        {"type": "text", "text": dynamic_context},
                        *([{"type": "image_url", "image_url": {"url": image_data_url}}] if image_data_url else []),
                    ],
                },
            ],
            "tools": tools,
            "tool_choice": "required" if self.model in {self.GEMINI_MODEL, self.GEMINI_FLASH_MODEL, self.GEMINI_38_MODEL, self.LUNA_MODEL} else "auto",  # Meta serves Muse with tool_choice auto only
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "session_id": f"{self.run_id}:{cache_namespace}",
            "provider": self.PROVIDER_PREFERENCES[self.model],
        }
        if self.model != self.LUNA_MODEL:
            payload["temperature"] = 0.2
        if self.reasoning_effort is not None:
            self.validate_effort(self.model, reasoning_effort or self.reasoning_effort)
            payload["reasoning"] = {"effort": reasoning_effort or self.reasoning_effort}
        if self.model == self.GEMINI_38_MODEL:
            # Cache only fixed instructions/tools; state, objectives and screenshots change on every turn.
            payload["messages"][0]["content"] = [{"type": "text", "text": system_prompt,
                                                   "cache_control": {"type": "ephemeral"}}]
            if (reasoning_effort or self.reasoning_effort) == "high":
                payload["max_tokens"] = max(payload["max_tokens"], 8192)
        if self.model in {self.LUNA_MODEL, self.GLM_MODEL}:
            # The key only routes to a cache-warm server, so it must not change with the objective or world.
            # A breakpoint on the fixed instructions lets a changed slow block fall back to that prefix.
            prefix = json.dumps([self.model, system_prompt, tools], sort_keys=True, separators=(",", ":"))
            payload["prompt_cache_key"] = "autoplay:" + hashlib.sha256(prefix.encode()).hexdigest()[:24]
        if self.model == self.LUNA_MODEL:
            payload["prompt_cache_options"] = {"mode": "explicit", "ttl": "30m"}
            payload["messages"][0]["content"] = [{"type": "text", "text": system_prompt,
                                                   "prompt_cache_breakpoint": {"mode": "explicit"}}]
            if stable_context:
                payload["messages"][1]["content"][0]["prompt_cache_breakpoint"] = {"mode": "explicit"}
        return self._request_tool_call(payload, tools, {"input_image": bool(image_data_url), "system_chars": len(system_prompt),
                                                         "context_chars": len(dynamic_context)})

    def complete_turn(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        reasoning_effort: str | None = None,
        max_tokens: int | None = None,
        timeout_seconds: int | None = None,
        cache_namespace: str = "agent",
    ) -> ToolDecision:
        """One turn of a running conversation: system + transcript + newest observation in, one tool call out.

        The system text and every transcript turn before the newest user message are a stable prefix;
        explicit cache markers go on the system text and on the last user turn before the newest one."""
        system: Any = system_prompt
        transcript = [dict(message) for message in messages]
        marker = ({"cache_control": {"type": "ephemeral"}} if self.model in {self.MUSE_MODEL, self.GEMINI_38_MODEL}
                  else {"prompt_cache_breakpoint": {"mode": "explicit"}} if self.model == self.LUNA_MODEL else None)
        if marker:
            system = [{"type": "text", "text": system_prompt, **marker}]
            earlier_users = [index for index, message in enumerate(transcript[:-1]) if message.get("role") == "user"]
            if earlier_users:
                index = earlier_users[-1]
                content = transcript[index].get("content")
                if isinstance(content, list) and content and content[-1].get("type") == "text":
                    transcript[index] = {**transcript[index], "content": [*content[:-1], {**content[-1], **marker}]}
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *transcript],
            "tools": tools,
            "tool_choice": "required" if self.model in {self.GEMINI_MODEL, self.GEMINI_FLASH_MODEL, self.GEMINI_38_MODEL, self.LUNA_MODEL} else "auto",  # Meta serves Muse with tool_choice auto only
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "session_id": f"{self.run_id}:{cache_namespace}",
            "provider": self.PROVIDER_PREFERENCES[self.model],
        }
        if self.model != self.LUNA_MODEL:
            payload["temperature"] = 0.2
        if self.reasoning_effort is not None:
            self.validate_effort(self.model, reasoning_effort or self.reasoning_effort)
            payload["reasoning"] = {"effort": reasoning_effort or self.reasoning_effort}
        if self.model in {self.LUNA_MODEL, self.GLM_MODEL}:
            prefix = json.dumps([self.model, system_prompt, tools], sort_keys=True, separators=(",", ":"))
            payload["prompt_cache_key"] = "autoplay:" + hashlib.sha256(prefix.encode()).hexdigest()[:24]
        if self.model == self.LUNA_MODEL:
            payload["prompt_cache_options"] = {"mode": "explicit", "ttl": "30m"}
        image = any(part.get("type") == "image_url" for message in transcript
                    if isinstance(message.get("content"), list) for part in message["content"])
        context_chars = sum(len(json.dumps(message.get("content") or "")) for message in transcript)
        return self._request_tool_call(payload, tools, {"input_image": image, "system_chars": len(system_prompt),
                                                         "context_chars": context_chars, "turns": len(transcript)},
                                       timeout_seconds=timeout_seconds)

    @staticmethod
    def _append_correction(payload: dict[str, Any], text: str) -> None:
        last = payload["messages"][-1]
        if last.get("role") == "user" and isinstance(last.get("content"), list):
            last["content"].append({"type": "text", "text": text})
        else:
            payload["messages"].append({"role": "user", "content": [{"type": "text", "text": text}]})

    def _request_tool_call(self, payload: dict[str, Any], tools: list[dict[str, Any]], diagnostics_extra: dict[str, Any],
                           timeout_seconds: int | None = None) -> ToolDecision:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self.API_URL,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "X-OpenRouter-Title": "Autoplay",
            },
        )

        aggregate_usage: dict[str, Any] = {}
        attempts: list[dict[str, Any]] = []
        last_error = "Expected a tool call, received none."
        deadline = time.monotonic() + (timeout_seconds or self.decision_budget_seconds)
        for response_attempt in range(3):
            if self.cancelled.is_set() or time.monotonic() >= deadline:
                raise OpenRouterError(f"{last_error} Decision cancelled or time budget exhausted.", attempts, aggregate_usage)
            try:
                result = self._post(request, deadline, attempts, response_attempt + 1)
            except OpenRouterError as error:
                error.attempts = attempts
                error.usage = aggregate_usage
                raise
            aggregate_usage = self._merge_usage(aggregate_usage, result.get("usage") or {})
            choice = (result.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            diagnostic = attempts[-1]
            diagnostic.update({
                "request_bytes": len(body),
                **diagnostics_extra,
                "tools_count": len(tools),
                "response_id": result.get("id"),
                "model": result.get("model"),
                "provider": result.get("provider"),
                "finish_reason": choice.get("finish_reason"),
                "native_finish_reason": choice.get("native_finish_reason"),
                "usage": result.get("usage") or {},
            })
            expected_provider = self.OFFICIAL_PROVIDERS.get(self.model)
            if expected_provider and result.get("provider") != expected_provider:
                diagnostic.update({"outcome": "provider_rejected", "error": "Response provider does not match the official-only route."})
                raise OpenRouterError(diagnostic["error"], attempts, aggregate_usage)
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                last_error = "Expected a tool call, received none."
            else:
                function = tool_calls[0]["function"]
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except json.JSONDecodeError:
                    last_error = "The model returned invalid tool arguments."
                else:
                    definition = next(
                        (
                            tool.get("function", {})
                            for tool in tools
                            if tool.get("function", {}).get("name") == function.get("name")
                        ),
                        None,
                    )
                    if definition is None:
                        last_error = f"The model selected unknown tool {function.get('name')!r}."
                    else:
                        argument_errors = self._argument_errors(arguments, definition.get("parameters", {}))
                        if argument_errors:
                            last_error = "The model returned invalid tool arguments: " + "; ".join(argument_errors)
                        else:
                            diagnostic["outcome"] = "accepted"
                            return ToolDecision(
                                name=function["name"],
                                arguments=arguments,
                                usage=aggregate_usage,
                                model=result.get("model"),
                                content=_message_text(message.get("content")),
                                reasoning=_message_text(message.get("reasoning")),
                                provider=result.get("provider"),
                                attempts=tuple(attempts),
                                call_id=tool_calls[0].get("id"),
                            )
            diagnostic.update({"outcome": "rejected", "error": last_error})
            if response_attempt < 2:
                # A retry must explain the observed failure; an identical prompt repeats it.
                self._append_correction(payload,
                    "Your previous response was rejected: " + last_error +
                    " Return one valid tool call. Follow the schema exactly; keep text well below maxLength. "
                    "Do not repeat the invalid arguments.")
                body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
                request = urllib.request.Request(self.API_URL, data=body, method="POST", headers=dict(request.header_items()))
                time.sleep(1)
        raise OpenRouterError(f"{last_error} Three response attempts failed.", attempts, aggregate_usage)

    def _post(self, request: urllib.request.Request, deadline: float, diagnostics: list[dict[str, Any]], response_attempt: int) -> dict[str, Any]:
        for attempt in range(3):
            remaining = min(self.timeout_seconds, deadline - time.monotonic())
            if self.cancelled.is_set() or remaining <= 0:
                raise OpenRouterError("OpenRouter request skipped: decision time budget exhausted.")
            diagnostic = {"response_attempt": response_attempt, "http_attempt": attempt + 1,
                          "request_bytes": len(request.data or b"")}
            diagnostics.append(diagnostic)
            started = time.perf_counter()
            try:
                with urllib.request.urlopen(request, timeout=remaining) as response:
                    diagnostic["http_status"] = 200
                    return json.load(response)
            except urllib.error.HTTPError as error:
                diagnostic.update({"http_status": error.code, "outcome": "http_error"})
                detail = error.read().decode("utf-8", errors="replace")
                # Meta's validator intermittently rejects a request it accepts seconds later; that is worth a retry.
                flaky_validation = error.code == 400 and "Invalid input" in detail and "provider_name" in detail
                retryable = error.code == 429 or error.code >= 500 or flaky_validation
                if not retryable or attempt == 2:
                    # A validator that names an index ("[35] : Invalid input") is talking about one message; show it.
                    match = re.search(r"\[(\d+)\]", detail)
                    if error.code == 400 and match:
                        try:
                            messages = json.loads(request.data)["messages"]
                            index = int(match[1])
                            if index < len(messages):
                                detail += " | message[%d]=%s" % (index, json.dumps(messages[index])[:1500])
                                shape = [{"i": i, "role": m.get("role"), "parts": [p.get("type") for p in m["content"]] if isinstance(m.get("content"), list) else type(m.get("content")).__name__,
                                          "tool_calls": [c.get("type") for c in m.get("tool_calls", [])], "tool_call_id": m.get("tool_call_id")} for i, m in enumerate(messages)]
                                dump = pathlib.Path(__file__).resolve().parents[1] / "state" / "bad-requests"
                                dump.mkdir(parents=True, exist_ok=True)
                                (dump / f"{int(time.time())}.json").write_text(json.dumps({"error": detail[:600], "shape": shape}, indent=1), encoding="utf-8")
                                (dump / f"{int(time.time())}.body.json").write_bytes(request.data or b"")
                        except (ValueError, KeyError, TypeError):
                            pass
                    raise OpenRouterError(f"OpenRouter returned HTTP {error.code}: {detail}") from error
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
                # URLError, a reset while reading the body, or a truncated JSON body: all worth one more try.
                diagnostic.update({"outcome": "transport_error", "error": str(error)})
                if attempt == 2:
                    raise OpenRouterError(f"OpenRouter request failed: {error}") from error
            finally:
                diagnostic["latency_ms"] = round((time.perf_counter() - started) * 1000)
            self.cancelled.wait(2**attempt)
        raise OpenRouterError("OpenRouter request failed without a response.")

    @staticmethod
    def _merge_usage(total: dict[str, Any], usage: dict[str, Any]) -> dict[str, Any]:
        merged = dict(total)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cost"):
            merged[key] = (merged.get(key) or 0) + (usage.get(key) or 0)
        prompt_details = dict(merged.get("prompt_tokens_details") or {})
        incoming_details = usage.get("prompt_tokens_details") or {}
        for key in ("cached_tokens", "cache_write_tokens", "audio_tokens", "video_tokens"):
            prompt_details[key] = (prompt_details.get(key) or 0) + (incoming_details.get(key) or 0)
        merged["prompt_tokens_details"] = prompt_details
        completion_details = dict(merged.get("completion_tokens_details") or {})
        for key, value in (usage.get("completion_tokens_details") or {}).items():
            if isinstance(value, (int, float)):
                completion_details[key] = (completion_details.get(key) or 0) + value
        merged["completion_tokens_details"] = completion_details
        if "is_byok" in usage:
            merged["is_byok"] = usage["is_byok"]
        return merged

    @staticmethod
    def _argument_errors(arguments: Any, parameters: dict[str, Any]) -> list[str]:
        if not isinstance(arguments, dict):
            return ["arguments must be an object"]
        errors = OpenRouterClient._value_errors("arguments", arguments, parameters)
        return [
            error.removeprefix("arguments.").removeprefix("arguments ")
            for error in errors
        ]

    @staticmethod
    def _value_errors(label: str, value: Any, schema: dict[str, Any]) -> list[str]:
        expected_types = schema.get("type")
        expected_types = expected_types if isinstance(expected_types, list) else [expected_types]
        type_matches = expected_types == [None] or any(
            (expected == "string" and isinstance(value, str))
            or (expected == "boolean" and isinstance(value, bool))
            or (expected == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
            or (expected == "integer" and isinstance(value, int) and not isinstance(value, bool))
            or (expected == "array" and isinstance(value, list))
            or (expected == "object" and isinstance(value, dict))
            for expected in expected_types
        )
        if not type_matches:
            return [f"{label} has the wrong type"]

        errors: list[str] = []
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{label} is outside its enum")
        if isinstance(value, str) and "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{label} is longer than {schema['maxLength']} characters")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                errors.append(f"{label} is below its minimum")
            if "maximum" in schema and value > schema["maximum"]:
                errors.append(f"{label} is above its maximum")
        if isinstance(value, list):
            if "minItems" in schema and len(value) < schema["minItems"]:
                errors.append(f"{label} has too few items")
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                errors.append(f"{label} has too many items")
            item_schema = schema.get("items", {})
            for index, item in enumerate(value):
                errors.extend(OpenRouterClient._value_errors(f"{label}[{index}]", item, item_schema))
        if isinstance(value, dict):
            properties = schema.get("properties", {})
            for key in schema.get("required", []):
                if key not in value:
                    errors.append(f"{label} missing {key}")
            if schema.get("additionalProperties") is False:
                for key in value:
                    if key not in properties:
                        errors.append(f"{label} has unexpected {key}")
            for key, item in value.items():
                if key in properties:
                    errors.extend(OpenRouterClient._value_errors(f"{label}.{key}", item, properties[key]))
        return errors
