"""LLM provider adapter — the ONLY vendor-coupled layer.

Everything above this module (orchestrator, gateway, UI) speaks a small
vendor-neutral vocabulary:

- :class:`ToolSpec`  — a tool the model may call (name, description, JSON Schema).
- :class:`ToolCall`  — the model's request to call one (id, name, parsed args).
- :class:`LlmAdapter` — ``complete()`` (one tool-calling round) and ``stream()``
  (final prose), plus helpers to build the provider-native assistant/tool
  messages so callers never hand-craft a vendor payload.

Swapping LLM vendors = writing a new ``LlmAdapter`` here. No OpenAI/Azure symbol
should appear anywhere else in the codebase. The provider registry and the Azure
/ Ollama env config also live here (they are intrinsically vendor-specific); the
rest of the app imports them from this module.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

from paths import app_base_dir

try:
    from dotenv import load_dotenv

    # Load the .env next to the app (or exe), regardless of the working dir.
    load_dotenv(app_base_dir() / ".env")
except ImportError:  # python-dotenv is optional; env vars can be set directly
    pass

from openai import APIConnectionError, AzureOpenAI, OpenAI, OpenAIError


# --- Azure OpenAI configuration (read from environment) --------------------
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
# For Azure, the "model" is the name of your deployment of gpt-4.1-mini.
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")

# --- Provider selection (Azure cloud vs local Ollama) ----------------------
# "azure" (default) uses the Azure settings above. "ollama" talks to a local
# Ollama server via its OpenAI-compatible API. LLM_PROVIDER in .env sets the
# startup default; the UI can switch providers live (passed per request).
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "azure").strip().lower()
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5")

# Registry of selectable providers — label for the UI, and the default model
# name (Azure deployment name, or Ollama model tag) for each.
PROVIDERS = {
    "azure": {"label": "Azure", "model": AZURE_OPENAI_DEPLOYMENT},
    "ollama": {"label": "Local", "model": OLLAMA_MODEL},
}

# The provider the app starts on, falling back to "azure" if .env names an
# unknown one.
DEFAULT_PROVIDER = LLM_PROVIDER if LLM_PROVIDER in PROVIDERS else "azure"


def model_for(provider: str) -> str:
    """The default model name configured for a provider."""
    return PROVIDERS.get(provider, PROVIDERS["azure"])["model"]


# The default model name for the active provider. The UI imports this so its
# default follows the provider.
DEFAULT_MODEL = model_for(DEFAULT_PROVIDER)


# --- Vendor-neutral abstractions -------------------------------------------


@dataclass
class ToolSpec:
    """A tool offered to the model, in vendor-neutral form."""

    name: str
    description: str
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})


@dataclass
class ToolCall:
    """The model's request to invoke a tool, with arguments already parsed."""

    id: str
    name: str
    arguments: dict = field(default_factory=dict)


class AdapterConnectionError(RuntimeError):
    """The LLM service could not be reached (network/endpoint problem)."""


class AdapterError(RuntimeError):
    """Any other provider-side failure (auth, quota, bad request…)."""


class LlmAdapter(Protocol):
    """The contract every provider adapter implements. Vendor-neutral in, out."""

    def complete(
        self,
        messages: list[dict],
        tools: list[ToolSpec] | None = None,
        *,
        max_tokens: int = 400,
        temperature: float = 0.3,
    ) -> tuple[str | None, list[ToolCall]]:
        """One non-streaming round. Returns ``(text, tool_calls)``."""
        ...

    def stream(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 400,
        temperature: float = 0.3,
    ) -> Iterator[str]:
        """Stream the final answer as text chunks (no tools)."""

    def assistant_tool_call_message(
        self, text: str | None, tool_calls: list[ToolCall]
    ) -> dict:
        """Build the provider-native assistant message echoing tool calls."""

    def tool_result_message(self, call: ToolCall, content: str) -> dict:
        """Build the provider-native message carrying a tool's result."""


# --- OpenAI / Azure implementation -----------------------------------------


def _make_client(provider: str) -> OpenAI:
    if provider == "ollama":
        # Ollama's OpenAI-compatible API; api_key is required by the SDK but
        # ignored by Ollama.
        return OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")
    return AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION,
    )


class OpenAIAdapter:
    """Adapter for Azure OpenAI and OpenAI-compatible servers (Ollama).

    Both speak the OpenAI chat-completions API, so a single adapter covers them;
    ``provider`` only selects how the client is built and which model is default.
    """

    def __init__(self, provider: str | None = None, model: str | None = None):
        self.provider = provider or DEFAULT_PROVIDER
        self.model = model or model_for(self.provider)

    def is_ready(self) -> bool:
        """True when this provider has the config it needs to make a call."""
        if self.provider == "azure":
            return bool(AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY)
        return True  # Ollama: a missing local server surfaces as a connection error

    @staticmethod
    def _to_openai_tools(tools: list[ToolSpec]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": t.parameters or {"type": "object", "properties": {}},
                },
            }
            for t in tools
        ]

    def complete(
        self,
        messages: list[dict],
        tools: list[ToolSpec] | None = None,
        *,
        max_tokens: int = 400,
        temperature: float = 0.3,
    ) -> tuple[str | None, list[ToolCall]]:
        client = _make_client(self.provider)
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = self._to_openai_tools(tools)
            kwargs["tool_choice"] = "auto"
        try:
            resp = client.chat.completions.create(**kwargs)
        except APIConnectionError as exc:
            raise AdapterConnectionError(str(exc)) from exc
        except OpenAIError as exc:
            raise AdapterError(str(exc)) from exc

        msg = resp.choices[0].message
        calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        return msg.content, calls

    def stream(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 400,
        temperature: float = 0.3,
    ) -> Iterator[str]:
        client = _make_client(self.provider)
        try:
            stream = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            for event in stream:
                if not event.choices:
                    continue
                chunk = event.choices[0].delta.content or ""
                if chunk:
                    yield chunk
        except APIConnectionError as exc:
            raise AdapterConnectionError(str(exc)) from exc
        except OpenAIError as exc:
            raise AdapterError(str(exc)) from exc

    def assistant_tool_call_message(
        self, text: str | None, tool_calls: list[ToolCall]
    ) -> dict:
        return {
            "role": "assistant",
            "content": text or None,
            "tool_calls": [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {
                        "name": c.name,
                        "arguments": json.dumps(c.arguments),
                    },
                }
                for c in tool_calls
            ],
        }

    def tool_result_message(self, call: ToolCall, content: str) -> dict:
        return {"role": "tool", "tool_call_id": call.id, "content": content}


def get_adapter(provider: str | None = None, model: str | None = None) -> LlmAdapter:
    """Return the adapter for a provider. Today everything is OpenAI-compatible;
    add a branch here (and a new class) to introduce a different vendor."""
    return OpenAIAdapter(provider, model)
