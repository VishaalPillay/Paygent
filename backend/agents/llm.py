"""LLM client — Gemini free tier, native function-calling.

Swapping providers is a two-line env change, never a code change: `complete()` is the
entire interface `loop.py` depends on. If Gemini rate-limits mid-demo, set
`DEMO_MODE=replay` and `agents/loop.py` serves recorded fixtures instead of calling
this module at all — that path must keep working.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from google import genai
from google.genai import types

MODEL = "gemini-3.6-flash"


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
            model=MODEL,
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
