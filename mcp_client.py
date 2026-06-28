"""Thin transport for per-system MCP servers (read-only today).

This is the gateway's transport detail — it speaks the Model Context Protocol
(Streamable HTTP) and nothing else. Policy (auth, audit, allow-listing,
namespacing, untrusted-output framing) lives in :mod:`gateway`, not here.

Each internal system (finance, later hr/project/crm) is one MCP server, keyed by
name in :data:`SERVERS` and configured by its own ``<SYS>_MCP_URL`` env var:

    - ``list_tools(system, token)`` discovers a server's tools as neutral dicts
      ``{name, description, parameters}`` (the MCP ``inputSchema`` is JSON Schema).
      The LLM-vendor tool shape is applied later by :mod:`llm_adapter`.
    - ``call_tool(system, token, name, args)`` invokes one and returns its text.

The MCP SDK is async; this module exposes a small **synchronous** facade so the
GUI's QThread worker can call it without managing an event loop. Each call opens
a short-lived session — simple and robust for a desktop chat cadence.
"""

from __future__ import annotations

import asyncio
import os
import time

try:
    from dotenv import load_dotenv

    from paths import app_base_dir

    load_dotenv(app_base_dir() / ".env")
except ImportError:
    pass

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


# Per-system MCP server URLs. Adding a system = adding its env var here (and an
# Entra scope in :mod:`identity`). FINANCE_MCP_URL is the one wired up today.
SERVERS: dict[str, str] = {
    "finance": os.getenv("FINANCE_MCP_URL", "").rstrip("/"),
    # "hr": os.getenv("HR_MCP_URL", "").rstrip("/"),         # when HR comes online
    # "project": os.getenv("PROJECT_MCP_URL", "").rstrip("/"),
    # "crm": os.getenv("CRM_MCP_URL", "").rstrip("/"),
}

# Discovered tool lists, cached per system per process (tool sets are static).
_tools_cache: dict[str, list[dict]] = {}

# A finance MCP server (and its on-behalf-of backend) can cold-start: the first
# hit after idle errors, a retry seconds later succeeds. Since every tool today
# is read-only (idempotent — the gateway write-gate guarantees this), we retry a
# failed call a few times with a short backoff so the first query "just works".
_MAX_ATTEMPTS = 5  # delays 2s,4s,6s,8s between tries → ~20s to ride out a cold start
_RETRY_BACKOFF_S = 2.0


class McpError(RuntimeError):
    """Raised when an MCP server cannot be reached or a tool call fails."""


def server_url(system: str) -> str:
    """The configured URL for a system's MCP server (empty if unset)."""
    return SERVERS.get(system, "")


def configured_systems() -> list[str]:
    """Systems that have an MCP server URL set."""
    return [sys for sys, url in SERVERS.items() if url]


def is_configured(system: str) -> bool:
    """True when ``system`` has an MCP server URL."""
    return bool(server_url(system))


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _run(coro):
    """Run an async coroutine to completion on a fresh event loop.

    Safe because the caller is a worker QThread with no running loop. Any error
    is normalised to :class:`McpError` so callers can fall back gracefully.
    """
    try:
        return asyncio.run(coro)
    except McpError:
        raise
    except Exception as exc:  # connection refused, auth rejected, protocol error…
        raise McpError(str(exc)) from exc


def _run_with_retry(make_coro):
    """Run an MCP coroutine, retrying transient failures (e.g. server cold start).

    ``make_coro`` is a thunk that builds a *fresh* coroutine each attempt (a
    coroutine can only be awaited once). Safe for the read-only tools we expose
    today; do not use for state-changing calls without idempotency guarantees.
    """
    last: McpError | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return _run(make_coro())
        except McpError as exc:
            last = exc
            if attempt + 1 < _MAX_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF_S * (attempt + 1))
    assert last is not None
    raise last


async def _list_tools(url: str, token: str) -> list[dict]:
    async with streamablehttp_client(url, headers=_headers(token)) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
    return [
        {
            "name": t.name,
            "description": t.description or "",
            "parameters": t.inputSchema or {"type": "object", "properties": {}},
        }
        for t in result.tools
    ]


async def _call_tool(url: str, token: str, name: str, args: dict) -> str:
    async with streamablehttp_client(url, headers=_headers(token)) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, args or {})
    # Flatten the returned content blocks into a single string (the tools return
    # pretty-printed JSON as text content).
    parts: list[str] = []
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    out = "\n".join(parts).strip()
    if result.isError:
        raise McpError(out or f"Tool '{name}' reported an error.")
    return out or "(the tool returned no content)"


def list_tools(system: str, token: str, *, refresh: bool = False) -> list[dict]:
    """Return a system's tools as neutral ``{name, description, parameters}`` dicts."""
    url = server_url(system)
    if not url:
        raise McpError(f"No MCP server configured for system '{system}'.")
    if refresh or system not in _tools_cache:
        _tools_cache[system] = _run_with_retry(lambda: _list_tools(url, token))
    return _tools_cache[system]


def call_tool(system: str, token: str, name: str, args: dict) -> str:
    """Invoke an MCP tool on a system's server and return its text result."""
    url = server_url(system)
    if not url:
        raise McpError(f"No MCP server configured for system '{system}'.")
    return _run_with_retry(lambda: _call_tool(url, token, name, args))
