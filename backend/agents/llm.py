"""LLM client — Gemini or Groq, both free tier, native function-calling.

`get_llm()` is the one place that picks a provider, keyed on `config.LLM_PROVIDER`
("gemini" default, or "groq") — everything downstream (`loop.py`, `agents/cart.py`)
only ever calls the returned client's `complete()`, so swapping providers really is
an env change, not a code change. If both quotas are exhausted mid-demo, set
`DEMO_MODE=replay` and `agents/loop.py` serves recorded fixtures instead of calling
either — that path must keep working.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import httpx
from google import genai
from google.genai import types

from .. import config

GEMINI_MODEL = "gemini-3.6-flash"
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


@dataclass
class ToolCall:
    id: str | None
    name: str
    args: dict
    thought_signature: bytes | None = None


@dataclass
class Completion:
    text: str | None
    tool_calls: list[ToolCall]


def _to_contents(messages: list[dict]) -> list[types.Content]:
    """messages: [{"role": "user"|"model", "text": ...} | {"role": "tool", "name":
    ..., "response": ..., "call_id": ...}]. Kept as plain dicts so loop.py never
    imports google.genai types directly — only this module speaks the SDK.
    """
    contents = []
    for m in messages:
        if m["role"] == "tool":
            part = types.Part(function_response=types.FunctionResponse(
                id=m.get("call_id"), name=m["name"], response={"result": m["response"]}))
            contents.append(types.Content(role="user", parts=[part]))
        elif m["role"] == "model" and m.get("tool_calls"):
            # thought_signature must be echoed back verbatim on the same Part that
            # carried the function_call, or Gemini 3's thinking models reject the
            # turn with 400 INVALID_ARGUMENT ("missing a thought_signature").
            parts = [types.Part(
                function_call=types.FunctionCall(id=tc.id, name=tc.name, args=tc.args),
                thought_signature=tc.thought_signature,
            ) for tc in m["tool_calls"]]
            contents.append(types.Content(role="model", parts=parts))
        else:
            contents.append(types.Content(role=m["role"], parts=[types.Part(text=m["text"])]))
    return contents


class GeminiClient:
    def __init__(self, api_key: str | None = None):
        self._client = genai.Client(api_key=api_key or os.environ["GEMINI_API_KEY"])

    def complete(
        self, system_prompt: str, messages: list[dict], tool_specs: list[dict]
    ) -> Completion:
        tools = [types.Tool(function_declarations=[
            types.FunctionDeclaration(**spec) for spec in tool_specs
        ])]
        response = self._client.models.generate_content(
            model=GEMINI_MODEL,
            contents=_to_contents(messages),
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                tools=tools,
                temperature=0.2,
            ),
        )
        candidate = response.candidates[0]
        text = None
        tool_calls = []
        for part in candidate.content.parts:
            if part.text:
                text = (text or "") + part.text
            if part.function_call:
                fc = part.function_call
                tool_calls.append(ToolCall(
                    id=fc.id, name=fc.name, args=dict(fc.args or {}),
                    thought_signature=part.thought_signature,
                ))
        return Completion(text=text, tool_calls=tool_calls)


def _to_openai_messages(system_prompt: str, messages: list[dict]) -> list[dict]:
    """Same `messages` shape `_to_contents` reads — this just renders it into
    OpenAI's chat-completions convention instead of Gemini's `Content`/`Part`
    objects. Groq's endpoint is OpenAI-compatible, so this is the whole adapter.
    """
    out = [{"role": "system", "content": system_prompt}]
    for m in messages:
        if m["role"] == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": m.get("call_id"),
                "content": str(m["response"]),
            })
        elif m["role"] == "model" and m.get("tool_calls"):
            out.append({
                "role": "assistant",
                "content": m.get("text") or None,
                "tool_calls": [{
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.args)},
                } for tc in m["tool_calls"]],
            })
        else:
            out.append({
                "role": "assistant" if m["role"] == "model" else "user",
                "content": m["text"],
            })
    return out


class GroqClient:
    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.environ["GROQ_API_KEY"]

    def complete(
        self, system_prompt: str, messages: list[dict], tool_specs: list[dict]
    ) -> Completion:
        response = httpx.post(
            GROQ_API_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": GROQ_MODEL,
                "messages": _to_openai_messages(system_prompt, messages),
                "tools": [{"type": "function", "function": spec} for spec in tool_specs],
                "temperature": 0.2,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
        tool_calls = [
            ToolCall(id=tc["id"], name=tc["function"]["name"],
                      args=json.loads(tc["function"]["arguments"] or "{}"))
            for tc in message.get("tool_calls") or []
        ]
        return Completion(text=message.get("content"), tool_calls=tool_calls)


def get_llm() -> GeminiClient | GroqClient:
    """The one place that decides which provider `chat.py`/`stream.py` get. Both
    clients expose the same `complete()`, so this is the entire swap."""
    if config.LLM_PROVIDER == "groq":
        return GroqClient()
    return GeminiClient()
