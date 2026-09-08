"""Probe a model's behaviour on the transcript-style request before trusting it with a game day.

Run: python -m autoplay_harness.probe [--model meta/muse-spark-1.3-contributor]
Writes broadcast/local/<slug>-probe.json. Costs a few cents at most.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .prompts import ACTOR_SYSTEM_PROMPT
from .tools import ACTOR_TOOLS

API_URL = "https://openrouter.ai/api/v1/chat/completions"


def load_env(root: Path) -> None:
    path = root / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def tiny_image() -> str:
    from PIL import Image
    image = Image.new("RGB", (640, 360))
    pixels = image.load()
    for y in range(360):
        for x in range(640):
            pixels[x, y] = (x * 255 // 640, y * 255 // 360, 90)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=75)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def observation(step: int, image: str | None) -> dict:
    text = json.dumps({
        "step": step, "location": "Farm", "time": 700 + step * 10, "day": 3, "season": "spring", "stamina": 250,
        "money": 500, "inventory": [{"slot": 0, "name": "Hoe"}, {"slot": 1, "name": "Parsnip Seeds", "count": 15}],
        "cropsNearby": [{"x": 60 + i, "y": 18, "crop": None, "watered": False} for i in range(6)],
        "objective": {"kind": "free", "goal": "get my bearings", "done_when": "I know what needs doing today"},
        "harnessLastResult": {"tool": "navigate_to", "status": "completed"} if step else None,
    }, separators=(",", ":"))
    parts = [{"type": "text", "text": "Observation:\n" + text}]
    if image:
        parts.append({"type": "image_url", "image_url": {"url": image}})
    return {"role": "user", "content": parts}


def call(model: str, key: str, messages: list[dict], effort: str, max_tokens: int, cache: bool) -> dict:
    payload = {
        "model": model, "messages": messages, "tools": ACTOR_TOOLS, "tool_choice": "auto",
        "max_tokens": max_tokens, "reasoning": {"effort": effort},
        "provider": {"allow_fallbacks": False},
    }
    if cache:
        payload["messages"][0]["content"] = [{"type": "text", "text": ACTOR_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
    body = json.dumps(payload).encode()
    request = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-OpenRouter-Title": "Autoplay probe"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        return {"http_status": error.code, "error": error.read().decode("utf-8", "replace")[:800],
                "latency_ms": round((time.perf_counter() - started) * 1000)}
    choice = (result.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    calls = message.get("tool_calls") or []
    usage = result.get("usage") or {}
    return {
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "provider": result.get("provider"), "model": result.get("model"),
        "finish_reason": choice.get("finish_reason"), "native_finish_reason": choice.get("native_finish_reason"),
        "tool": calls[0]["function"]["name"] if calls else None,
        "arguments": (calls[0]["function"].get("arguments") or "")[:300] if calls else None,
        "content": (message.get("content") or "")[:200] or None,
        "reasoning_chars": len(message.get("reasoning") or "") if isinstance(message.get("reasoning"), str) else None,
        "usage": usage,
        "request_bytes": len(body),
        "tool_call_id": calls[0].get("id") if calls else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="meta/muse-spark-1.3-contributor")
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    load_env(root)
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        print("OPENROUTER_API_KEY missing", file=sys.stderr)
        return 1
    image = tiny_image()
    report: dict = {"model": arguments.model, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "calls": []}

    # 1-3: growing transcript with explicit cache markers, low effort. Cache reads should grow.
    messages = [{"role": "system", "content": ACTOR_SYSTEM_PROMPT}, observation(0, image)]
    for step in range(3):
        result = call(arguments.model, key, messages, "low", 2000, cache=True)
        report["calls"].append({"probe": f"transcript_step_{step}", "effort": "low", **result})
        print(json.dumps(report["calls"][-1], indent=1)[:1200], flush=True)
        if not result.get("tool_call_id"):
            break
        call_id = result["tool_call_id"]
        # move the image off the old observation, append the assistant turn, the tool result and a new observation
        messages[-1] = {"role": "user", "content": [messages[-1]["content"][0]]}
        messages.append({"role": "assistant", "content": result.get("content"),
                         "tool_calls": [{"id": call_id, "type": "function",
                                         "function": {"name": result["tool"], "arguments": result["arguments"] or "{}"}}]})
        messages.append({"role": "tool", "tool_call_id": call_id, "content": json.dumps({"status": "completed"})})
        messages.append(observation(step + 1, image))

    # 4: medium effort on the same transcript.
    result = call(arguments.model, key, messages, "medium", 6000, cache=True)
    report["calls"].append({"probe": "medium_effort", "effort": "medium", **result})
    print(json.dumps(report["calls"][-1], indent=1)[:800], flush=True)

    # 5: small cap at medium effort: do reasoning tokens eat the tool call?
    result = call(arguments.model, key, messages, "medium", 120, cache=True)
    report["calls"].append({"probe": "small_cap_medium", "effort": "medium", **result})
    print(json.dumps(report["calls"][-1], indent=1)[:800], flush=True)

    # 6: without cache markers, for comparison.
    result = call(arguments.model, key, [{"role": "system", "content": ACTOR_SYSTEM_PROMPT}, *messages[1:]], "low", 2000, cache=False)
    report["calls"].append({"probe": "no_cache_markers", "effort": "low", **result})
    print(json.dumps(report["calls"][-1], indent=1)[:800], flush=True)

    out = root / "broadcast" / "local" / (arguments.model.split("/")[-1] + "-probe.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
