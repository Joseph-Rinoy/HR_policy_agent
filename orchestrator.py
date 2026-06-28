"""Orchestrator — the vendor-neutral conversation + tool-call loop.

Owns the loop, not the prompts and not the vendor SDK:

  - It asks :mod:`routing` which system(s) a query touches, then :mod:`gateway`
    for those systems' tools (namespaced, policy-checked).
  - It drives tool rounds through an :class:`~llm_adapter.LlmAdapter`, working
    only on abstract :class:`~llm_adapter.ToolCall` objects — never a vendor
    payload. The adapter builds the provider-native assistant/tool messages.
  - It streams the final answer with tools withheld.

RAG-vs-tool preference is structural: routing decides if any authoritative tool
applies. No system hit ⇒ a pure-RAG turn (tools never shadow retrieval, and the
finance tools can't tempt the model on an HR question).

Prompt text (system prompt, CONTEXT/user message) is built by :mod:`llm` and
passed in, so this module never imports ``llm`` (no import cycle) and stays free
of HR-specific content.
"""

from __future__ import annotations

from collections.abc import Iterator

import gateway
import routing
from llm_adapter import (
    AdapterConnectionError,
    AdapterError,
    get_adapter,
)

# Hard cap on tool-call rounds so a confused model can't loop forever.
_MAX_TOOL_ROUNDS = 5

_ERR_NOT_READY = (
    "I'm not fully set up yet — my connection to the AI service is missing. "
    "Please ask IT to configure the Azure OpenAI settings, then try again."
)
_ERR_CONNECT = (
    "I'm having trouble connecting right now. Please check your internet "
    "connection and try again in a moment."
)
_ERR_GENERIC = (
    "Sorry, something went wrong on my end. Please try asking again in a moment."
)


def stream_agent_answer(
    *,
    question: str,
    system_content: str,
    user_message: str,
    agent_system_content: str | None = None,
    history: list[dict] | None = None,
    provider: str,
    model: str | None = None,
    max_tokens: int = 400,
    enable_tools: bool = True,
    user: str | None = None,
) -> Iterator[str]:
    """Stream an answer, calling read-only system tools when the query needs them.

    ``question`` is the raw user text (used for routing); ``user_message`` is the
    fully-built message including the RAG CONTEXT block. ``system_content`` is the
    plain (RAG-only) system prompt; ``agent_system_content`` is the tools-aware
    variant — it is used only when tools are actually offered this turn, so a
    pure-RAG turn looks exactly like the classic path.
    """
    adapter = get_adapter(provider, model)
    if not getattr(adapter, "is_ready", lambda: True)():
        yield _ERR_NOT_READY
        return

    # Ollama models here aren't reliable tool-callers, so skip tools for them.
    tools = []
    if enable_tools and provider != "ollama" and gateway.is_available():
        tools = gateway.list_tools(routing.route_systems(question), user=user)

    chosen_system = agent_system_content if (tools and agent_system_content) else system_content
    messages: list[dict] = [{"role": "system", "content": chosen_system}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": user_message})

    try:
        if tools:
            # Decision rounds: let the model call tools until it's ready to
            # answer, working purely on abstract ToolCall objects.
            for _ in range(_MAX_TOOL_ROUNDS):
                text, tool_calls = adapter.complete(
                    messages, tools, max_tokens=max_tokens
                )
                if not tool_calls:
                    break
                messages.append(adapter.assistant_tool_call_message(text, tool_calls))
                for call in tool_calls:
                    result = gateway.call_tool(call.name, call.arguments, user=user)
                    messages.append(adapter.tool_result_message(call, result))

        # Final answer — streamed, tools withheld so the model commits to prose.
        yield from adapter.stream(messages, max_tokens=max_tokens)
    except AdapterConnectionError:
        yield _ERR_CONNECT
    except AdapterError:
        yield _ERR_GENERIC
