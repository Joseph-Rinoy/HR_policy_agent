# Agent Layering — foundation for multi-system MCP

This is the cleanup/prep pass that turns the single-file Finance integration into
layered seams, so adding system #2 (HR/Project/CRM) is config + a small registry
entry, not a refactor. Nothing here adds a new integration; Finance behaviour and
the HR-policy RAG fallback are unchanged.

## Layers (top → bottom)

| Layer | Module | Responsibility | Vendor coupling |
|---|---|---|---|
| UI | `chat_widget.py` | Chat window, `LlmWorker` thread | none |
| Entry / prompts | `llm.py` | `SYSTEM_PROMPT`/`AGENT_SYSTEM_PROMPT`, RAG `CONTEXT` assembly; `stream_answer` / `stream_agent_answer` shims | none |
| Orchestrator | `orchestrator.py` | Conversation + tool-call loop on abstract `ToolCall`s; routing decision | none |
| Routing | `routing.py` | Keyword classifier → which system(s) a query touches | none |
| Gateway (chokepoint) | `gateway.py` | Token mint, audit, allow-list, namespacing, write-gate, untrusted-output framing | none |
| Identity seam | `identity.py` → `auth_entra.py` | `mint_downstream_token(system)`; only `auth_entra` knows MSAL/Entra | Entra (isolated) |
| Transport | `mcp_client.py` | Per-system MCP servers over Streamable HTTP | none |
| **LLM adapter** | `llm_adapter.py` | **The only vendor-coupled layer** — OpenAI/Azure SDK + tool-schema translation | OpenAI/Azure |
| Audit | `audit.py` | One JSONL record per tool call (`logs/audit.jsonl`) | none |

Two independent coupling axes are each quarantined to one module: **LLM vendor**
→ `llm_adapter.py`; **identity provider (Entra)** → `auth_entra.py` (behind
`identity.py`). Swapping either touches only that module.

## Adding a system (e.g. HR)

1. `mcp_client.SERVERS` — add `"hr": os.getenv("HR_MCP_URL", ...)`.
2. `identity._SYSTEM_SCOPES` — add `"hr": os.getenv("ENTRA_SCOPE_HR", ...)`.
3. `routing._SYSTEM_KEYWORDS` — add HR cue words.
4. `gateway._ENABLED_SYSTEMS` — add `"hr"`.
5. `.env` — `HR_MCP_URL`, `ENTRA_SCOPE_HR`.

No change to the orchestrator, adapter, UI, or prompts.

## Read/write separation (scaffolding only — everything is read-only today)

- Tools are namespaced `system.tool` (e.g. `finance.get_expenses`).
- The gateway flags any tool whose name starts with a state-changing verb
  (`create`, `update`, `delete`, `apply`, `submit`, …) as a **write** and
  **blocks** it unless its namespaced name is in `gateway._WRITE_ALLOWLIST`
  (empty today). So an ungoverned mutation is never even offered to the model.
- When writes arrive, they get governed here: allow-list by role, mandatory
  confirmation (reuse the leave `ConfirmationCard`), dry-run, idempotency keys.

## Security properties enforced now

- **No standing/broad credential.** Per-user delegated tokens only; the MCP
  server does the On-Behalf-Of exchange. `mint_downstream_token` mints **per
  call**, so MSAL silent-refresh keeps long agent loops authenticated.
- **Audit everything.** Every tool call (reads included) is logged at the
  gateway with user, system, tool, args, status, result/error, timestamp.
- **Tool output is untrusted.** The gateway wraps results in `<<<TOOL_OUTPUT …>>>`
  markers and the prompt forbids treating anything inside as instructions. The
  per-user authz downstream is the backstop (injection can't exceed the user's
  own rights).
- **LLM never authorises.** Authorization lives in identity (scope) + the gateway
  (allow-list/write-gate) + each MCP server (row/field enforcement).

## Future: lift the gateway into a central service

`gateway.list_tools(systems)` / `gateway.call_tool(name, args)` and
`identity.mint_downstream_token(user, system)` already carry a `user` parameter
and hold no per-process state beyond config, so they can move behind a network
boundary without changing callers. The audit sink is behind one function for the
same reason.
