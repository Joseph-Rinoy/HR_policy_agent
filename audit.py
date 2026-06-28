"""Audit logging for tool calls — one structured record per call, reads included.

Every tool invocation flows through :mod:`gateway`, which calls :func:`log_tool_call`
here. That gives a single, complete trail of *who* did *what*, *when*, with what
arguments and outcome — the forensic/compliance backstop before the assistant
reaches more systems and more sensitive data.

Records are appended as JSON Lines to ``logs/audit.jsonl`` next to the app. The
sink is deliberately behind this one function so it can be redirected to a
central log service when the gateway is extracted out of the desktop process —
callers don't change.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from paths import app_base_dir

_LOG_DIR = app_base_dir() / "logs"
_LOG_PATH = _LOG_DIR / "audit.jsonl"
_LOCK = threading.Lock()

# Cap stored arg/result text so the log stays readable and bounded; the full
# data still lives in the downstream system.
_MAX_FIELD_CHARS = 2000


def _truncate(value, limit: int = _MAX_FIELD_CHARS):
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(text) > limit:
        return text[:limit] + f"…(+{len(text) - limit} chars)"
    return text


def log_tool_call(
    *,
    user: str | None,
    system: str,
    tool: str,
    args: dict | None,
    status: str,
    result: str | None = None,
    error: str | None = None,
) -> None:
    """Append one audit record. Never raises — logging must not break a call."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user": user or "(desktop-user)",
        "system": system,
        "tool": tool,
        "args": _truncate(args or {}),
        "status": status,  # "ok" | "error" | "blocked"
        "result": _truncate(result) if result is not None else None,
        "error": error,
    }
    try:
        with _LOCK:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            with _LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass  # best-effort; a write failure must not abort the user's request
