from __future__ import annotations

import json
import hashlib
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
    LUNA_MODEL = "openai/gpt-5.6-luna"
    DEFAULT_MODEL = GLM_MODEL
    OFFICIAL_PROVIDERS = {
        GEMINI_FLASH_MODEL: "Google AI Studio",
        LUNA_MODEL: "OpenAI",
    }
    PROVIDER_PREFERENCES = {
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
        self.reasoning_effort = reasoning_effort if model in {self.GLM_MODEL, self.QWEN_MODEL, self.GEMINI_FLASH_MODEL, self.LUNA_MODEL} else None
        self.cancelled = threading.Event()

    @classmethod
    def validate_effort(cls, model: str, reasoning_effort: str | None) -> None:
        if model == cls.GLM_MODEL and reasoning_effort not in {"low", "high", "max"}:
            raise OpenRouterError("GLM 5.3 Flash supports reasoning effort low, high, or max.")
        if model == cls.QWEN_MODEL and reasoning_effort not in {"low", "medium", "high", "max"}:
            raise OpenRouterError("Qwen 3.8 Flash requires a supported reasoning effort; use low for gameplay trials.")
        if model == cls.GEMINI_FLASH_MODEL and reasoning_effort not in {"low", "medium", "high"}:
            raise OpenRouterError("Gemini 3.7 Flash supports low, medium, or high reasoning effort.")
        if model == cls.LUNA_MODEL and reasoning_effort not in {"low", "medium", "high", "max"}:
            raise OpenRouterError("Use a supported Luna reasoning effort; low is the gameplay trial default.")

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
            "tool_choice": "required" if self.model in {self.GEMINI_MODEL, self.GEMINI_FLASH_MODEL, self.LUNA_MODEL} else "auto",
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
        deadline = time.monotonic() + self.decision_budget_seconds
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
                "input_image": bool(image_data_url),
                "system_chars": len(system_prompt),
                "context_chars": len(dynamic_context),
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
                            )
            diagnostic.update({"outcome": "rejected", "error": last_error})
            if response_attempt < 2:
                time.sleep(1)
        raise OpenRouterError(f"{last_error} Three response attempts failed.", attempts, aggregate_usage)

    def _post(self, request: urllib.request.Request, deadline: float, diagnostics: list[dict[str, Any]], response_attempt: int) -> dict[str, Any]:
        for attempt in range(3):
            remaining = min(self.timeout_seconds, deadline - time.monotonic())
            if self.cancelled.is_set() or remaining <= 0:
                raise OpenRouterError("OpenRouter request skipped: decision time budget exhausted.")
            diagnostic = {"response_attempt": response_attempt, "http_attempt": attempt + 1}
            diagnostics.append(diagnostic)
            started = time.perf_counter()
            try:
                with urllib.request.urlopen(request, timeout=remaining) as response:
                    diagnostic["http_status"] = 200
                    return json.load(response)
            except urllib.error.HTTPError as error:
                diagnostic.update({"http_status": error.code, "outcome": "http_error"})
                retryable = error.code == 429 or error.code >= 500
                if not retryable or attempt == 2:
                    detail = error.read().decode("utf-8", errors="replace")
                    raise OpenRouterError(f"OpenRouter returned HTTP {error.code}: {detail}") from error
            except (urllib.error.URLError, TimeoutError) as error:
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
