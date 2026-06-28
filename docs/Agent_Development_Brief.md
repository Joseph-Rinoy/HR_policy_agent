# Agent Development Brief — Qubi Employee Assistant

> Status: Draft v0.2 (extension brief) · Owner: Qubiqon Engineering · Last updated: 2026-06-18
> Current baseline: "Qubi" HR Policy Assistant (PySide6 desktop widget, Azure OpenAI, keyword RAG, Playwright leave filing on sumHR). This brief covers the extension into a full Employee Assistant.

## 1. Problem Statement

**Agent Name:** Qubi Employee Assistant

### Business Problem / Opportunity
Qubiqon has replaced several paid SaaS subscriptions (e.g. Zoho Books, sumHR) with
in-house apps for HR, project management, CRM, and finance. The replacement saved
licensing cost but recreated the original pain: each app is a separate URL, with
its own login, navigation, and multi-click workflows. An employee who wants to
check their inbox, see today's meetings, apply for leave, and raise a ticket must
visit four or five different tools and learn each one.

There is no single place to *see* the day's important signals (mail, calendar) or
to *act* across internal systems through one conversation. The opportunity is a
single conversational assistant that (a) surfaces daily insights automatically and
(b) executes actions across internal apps on the employee's behalf — turning
"navigate five apps" into "ask once."

Because the action layer is built as **MCP servers** (Model Context Protocol), the
same internal-app connectors can be reused by any MCP-capable LLM or chat client,
and packaged for other enterprises as a product — not just an internal tool.

### Intended Users / Personas
- **Employees (primary):** check mail/calendar, apply for leave, raise & track
  tickets, ask HR-policy questions — without leaving the chat widget.
- **Project teams / managers:** project and task status, approvals, team leave
  visibility (read-heavy, some approval actions).
- **HR team:** policy answers at scale, leave/attendance lookups, lighter ticket load.
- **IT / Admins:** onboard users, manage MCP connector access, monitor usage,
  rotate secrets, configure which tools/actions are enabled per role.
- **(Future) External enterprises:** consume the MCP connectors with their own LLM
  host — the resale persona.

### Current Process / Pain Points
- Internal apps (HR, PM, CRM, finance) each live at a separate bookmarked URL with
  separate logins and multi-step UIs.
- No unified daily briefing: employees manually open Outlook and Teams/calendar
  each morning to find what matters.
- Routine actions (apply leave, raise a ticket, check ticket status) are several
  clicks deep in tools used infrequently enough that the steps are easily forgotten.
- The **current Qubi** only answers HR-policy questions and files leave on sumHR via
  fragile browser automation (Playwright clicking the sumHR UI). It cannot read
  mail/calendar and has no general action layer.

### Expected Outcome
- One conversational "front door" for daily work: insights pushed proactively,
  actions taken on request, status returned in-line.
- Measurable reduction in app-switching and clicks for common tasks (leave, tickets,
  status checks).
- A reusable, vendor-neutral connector layer (MCP) that is LLM-agnostic and
  productizable for other enterprises.

### Scope
**In scope (this initiative):**
- **Daily insights:** Outlook inbox summary and Teams/Outlook calendar summary,
  generated on a morning schedule and on demand. (Copilot-style summarization.)
- **Meeting summary** for a selected/just-finished meeting (where transcripts/notes
  are available).
- **Agentic actions over internal apps** via MCP servers, starting with the
  highest-value flows: HR (leave apply/balance/status), PM (ticket create / status /
  list, project status), then CRM and finance (read-first, then guarded writes).
- **HR-policy Q&A** (the existing capability), migrated onto the same agent loop.
- **Human-in-the-loop confirmation** for any state-changing action.
- Desktop widget UI (retain the PySide6 experience) backed by a central service.

**Out of scope (this initiative):**
- Replacing or re-platforming the internal apps themselves; this is an integration
  layer, not a rewrite.
- Sending email / posting to Teams on the user's behalf (read/summarize only in v1;
  composing/sending is a later phase behind explicit approval).
- Full free-text leave parsing → auto-file without review (confirmation stays
  mandatory).
- Mobile / browser clients (desktop-first; web/Teams client is a later option).
- External-enterprise productization/packaging (architecture must *allow* it, but
  GA hardening, multi-tenancy, and billing are a separate track).
- Replacing M365's own data retention/compliance; we summarize, we don't archive.

### Success Criteria
- **Reliability:** action success rate ≥ a target threshold per connector; graceful,
  explicit fallback on every failure (never a silent or hallucinated "done").
- **Grounding & safety:** no fabricated answers or actions; policy answers stay
  grounded in source docs; every write action is confirmed by the user first.
- **Daily-insight quality:** morning summaries are accurate, concise, and ranked by
  importance; users trust them enough to act without opening the source app.
- **Onboarding:** a new employee is productive in minutes (sign-in + consent, no
  manual config of endpoints/secrets).
- **Extensibility:** adding a new internal app = adding one MCP server, with no
  changes to the agent core or UI.
- **Latency:** interactive responses stream quickly; scheduled summaries are ready
  before the workday starts.

---

## 2. Workflow / Functional Flow

*(Business and UX flow only — no architecture/tools here; those are in Sections 3–4.)*

### Trigger Point
- **Proactive (scheduled):** a morning job (per user's working hours/timezone)
  generates the inbox + calendar summary and has it waiting when the widget opens.
- **Interactive (on demand):** the user opens the desktop widget and chats — asks a
  question, requests a summary, or asks the agent to perform an action.
- **Event-driven (later):** e.g. a just-ended meeting prompts an offer to summarize.

### User Journey
1. Employee starts their day; the widget shows a ready **daily briefing**
   (top inbox items + today's meetings, ranked).
2. They ask follow-ups in plain language ("anything urgent from finance?",
   "what's my 11am about?").
3. They request an action ("apply casual leave for next Monday", "raise an IT ticket
   for my laptop", "status of ticket 4821").
4. The agent gathers any missing details by asking, then **shows a confirmation
   card** summarizing exactly what it will do.
5. On confirm, the agent performs the action and reports the concrete result
   (success + reference id, or a clear failure with next steps).
6. The conversation continues; context (e.g. the policy just discussed, the meeting
   just summarized) is reused for follow-ups.

### Inputs Required
- **Standing:** the user's identity/consent for mail & calendar; role (drives which
  tools/actions are available); HR policy documents (already loaded today).
- **Per task:** only the fields a given action needs, requested conversationally and
  validated before execution — e.g. leave: type, date(s), reason; ticket: category,
  priority, description.

### Functional Steps

| Step | Actor | Functional Action | Expected Result |
|---|---|---|---|
| 1 | System | On schedule, fetch & summarize inbox + calendar | Ranked daily briefing prepared for the user |
| 2 | User | Opens widget / asks a question or issues a command | Intent understood; briefing or answer shown |
| 3 | Agent | Classify intent → answer (policy Q&A), summarize, or plan an action | Correct path chosen; grounded answer or action plan |
| 4 | Agent | For an action, collect & validate missing inputs | Complete, valid parameters assembled |
| 5 | Agent → User | Present a confirmation card for any state change | User sees exactly what will happen before it happens |
| 6 | User | Reviews, edits, and confirms (or cancels) | Explicit human approval captured |
| 7 | Agent | Execute the action against the target internal app | Action completed; result captured |
| 8 | Agent → User | Report concrete outcome (success + reference, or failure + remedy) | User knows the real state; no ambiguity |

### Decision Points / Business Rules

| Decision Point | Condition | Expected Agent Behavior |
|---|---|---|
| Question vs. action | User *asks about* a process vs. *commands* an action | Questions → grounded answer; commands → action flow (mirrors today's `apply_leave` vs. leave-question guard) |
| Read vs. write | Action changes state in a downstream system | Require an explicit confirmation card before executing |
| Missing/ambiguous inputs | Required parameter absent or unclear | Ask a focused follow-up; never guess critical fields (dates, amounts, recipients) |
| Out of scope / not in docs | Answer not grounded or capability unavailable | Say so plainly; hand off to the right human (HR/POSH/IT) via a real contact link |
| Permission / role | User lacks rights for a tool or action | Refuse politely and explain; offer an in-scope alternative |
| Low confidence | Agent unsure which action or target | Surface options / ask, rather than act |

### Human-in-the-Loop Checkpoints
- **Mandatory confirmation** before any write/state change (leave, tickets,
  approvals, any future send/compose) — extends today's leave `ConfirmationCard`.
- **Escalation/hand-off** to a human (HR, POSH, IT) when out of scope, blocked, or
  the user explicitly asks for a person.
- **Admin gate** for sensitive connectors/actions (configurable per role).

### Outputs
- Daily inbox & calendar summaries (ranked, concise).
- Meeting summaries (where notes/transcripts exist).
- Grounded HR-policy answers with cited sources.
- Completed actions with confirmations and reference ids (leave applied, ticket
  raised, status retrieved).
- Clear, friendly failure messages with a next step when something can't be done.

### Exception Handling
- **Missing input:** ask a targeted question; proceed only when valid.
- **Invalid/conflicting input** (e.g. leave date in the past, overlapping leave):
  flag the conflict, propose a correction, do not file.
- **Duplicate request** (same ticket/leave just submitted): detect and confirm intent
  before creating a second.
- **Downstream failure / timeout / auth expiry:** report honestly, never claim
  success; offer retry or hand-off. (Today's workers already "never raise" and
  return structured `{ok, error}` — keep this contract everywhere.)
- **Ambiguous intent:** ask, don't assume.

### Completion Criteria
A workflow is complete when the requested information is delivered, **or** a
confirmed action has executed and its real outcome (success + reference, or a clear
failure + remedy) has been reported back to the user.

---

## 3. Implementation Options Evaluated

The core decision is how the assistant talks to internal apps and which "brain"
orchestrates it. Three options were evaluated.

### Option 1 — Vendor-neutral MCP servers + swappable LLM host *(per-app connectors, central service)*
Build one **MCP server per internal domain** (HR, PM, CRM, finance) that exposes
typed tools (e.g. `apply_leave`, `get_leave_balance`, `create_ticket`,
`get_ticket_status`, `get_project_status`) over each app's existing/likely REST API.
Mail & calendar come from **Microsoft 365 Graph** (the company is on M365 — Outlook
+ Teams) via delegated per-user OAuth, also wrapped as MCP tools. A central backend
service hosts the MCP servers, scheduled summary jobs, and secrets. The LLM host
(the agent loop doing tool-calling) is **swappable** — Azure OpenAI today (already
in use), any MCP-capable LLM later. The existing PySide6 widget stays as the UI.

**Pros:**
- Directly matches the stated goal: connectors are LLM-agnostic and resellable to
  other enterprises.
- Adding an app = adding one MCP server; agent core and UI unchanged (extensibility).
- Replaces the fragile Playwright/sumHR UI automation with a typed API tool — far
  more reliable and testable.
- Reuses current investments: Azure OpenAI, the widget, the policy RAG, the
  confirmation-card pattern.
- Central service centralizes secrets, scheduling, logging, and per-role access.

**Cons / Risks:**
- Most upfront engineering: MCP servers, a central service, OAuth/Graph consent,
  auth/secrets management.
- Requires stable APIs on the internal apps; gaps may need new endpoints from those
  teams.
- New backend service to operate (hosting, monitoring, on-call).

### Option 2 — Microsoft Copilot Studio / M365 Copilot extensibility
Lean fully into the Microsoft stack: build the agent in Copilot Studio, use native
M365 connectors for mail/calendar/Teams, and add internal apps via Copilot plugins.

**Pros:**
- Least custom code for mail/calendar/Teams; native M365 integration and identity.
- Microsoft handles much of the hosting, compliance, and summarization plumbing.

**Cons / Risks:**
- Ties the product to Microsoft licensing and platform — undermines the
  "any LLM / resell to other enterprises" goal.
- Abandons the PySide6 widget and Azure-OpenAI/Ollama work already done.
- Connectors are Copilot-shaped, not portable MCP servers; limited control over the
  agent loop, guardrails, and UX.
- Per-seat licensing cost scales with headcount.

### Option 3 — Single custom agent with direct API calls (no MCP layer)
Keep one desktop app that calls each internal app's API directly using LLM
function-calling, plus Graph for mail/calendar. No separate MCP/connector layer.

**Pros:**
- Fastest path to a working v1; fewest moving parts.
- No new backend service if kept local.

**Cons / Risks:**
- Not reusable or sellable — integrations are welded to this one app (fails the
  core product goal).
- Per-machine secrets and OAuth tokens; no central scheduling for morning summaries.
- Tool logic and app coupling grow into a monolith that's hard to extend (every new
  app touches the core).
- Repeats today's tight-coupling problem at larger scale.

### Key Trade-offs Considered
- **Reusability / resale:** Option 1 ✔ (portable MCP) · Option 2 ✗ (MS-locked) ·
  Option 3 ✗ (welded to one app).
- **Speed of delivery:** Option 3 fastest, Option 2 medium, Option 1 slowest upfront
  but cheapest to extend afterward.
- **Reliability:** typed APIs (Options 1 & 3) >> Playwright UI automation (today).
- **Maintainability / extensibility:** Option 1 best (one server per app); Option 3
  worst (monolith); Option 2 platform-bounded.
- **Cost:** Option 1 = build + host our own; Option 2 = ongoing per-seat licensing;
  Option 3 = cheapest to start, most expensive to evolve.
- **Compliance / secrets:** central service (Option 1) centralizes control; local
  (Option 3) scatters secrets per machine.
- **User experience:** Options 1 & 3 keep the familiar desktop widget; Option 2
  pushes users into the Copilot/Teams surface.
- **Lock-in:** Option 1 LLM-agnostic; Option 2 highest lock-in.

---

## 4. Recommended Option Selected and Why

### Recommended Option
**Option 1 — Vendor-neutral MCP servers + swappable LLM host, delivered as the
existing desktop widget over a central backend service.**

### Rationale
It is the only option that satisfies the defining requirement: a connector layer
that works with **any** LLM and can be packaged for other enterprises. It maximizes
reuse of what already works (Azure OpenAI, the PySide6 widget, the keyword-RAG
policy Q&A, the leave confirmation-card pattern) while replacing the one fragile
piece — Playwright UI automation against sumHR — with a typed, testable API tool.
Its one-server-per-app shape makes the system extensible by construction: new
internal apps plug in without touching the agent core or UI. A central service gives
us the scheduling (morning summaries), secret management, and per-role access control
that a per-machine app cannot.

### Why Other Options Were Not Selected
- **Option 2 (Copilot Studio):** lowest effort for M365 features, but Microsoft
  lock-in directly contradicts the "any LLM / resell" goal and would discard existing
  work; per-seat licensing scales poorly.
- **Option 3 (direct API monolith):** fastest v1 but produces non-reusable,
  tightly-coupled integrations with scattered per-machine secrets and no central
  scheduling — it recreates today's coupling problem at a larger scale.

### Key Benefits
- LLM-agnostic, portable connectors (internal use **and** external product).
- Extensible: one MCP server per app; core and UI stay stable.
- More reliable & testable than UI automation; safer with mandatory confirmations.
- Reuses current stack (Azure OpenAI, widget, RAG, confirmation card).
- Central control of secrets, scheduling, logging, and role-based access.

### Assumptions
- Internal apps (HR, PM, CRM, finance) expose, or can expose, authenticated REST
  APIs sufficient for the target flows (replacing sumHR UI automation with an API).
- Qubiqon is on **Microsoft 365**; mail/calendar/Teams data is reachable via Graph
  with delegated, per-user consent (Entra app registration).
- Azure OpenAI remains the initial agent host; the MCP layer keeps the host swappable.
- A central backend service can be hosted and operated (cloud or on-prem).
- Daily-summary scheduling can run per-user (working hours / timezone aware).
- The desktop widget remains the primary UI for v1.

### Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Internal apps lack adequate APIs | Blocks API-based actions; forces fragile UI automation | Audit APIs first; agree required endpoints with app teams; phase rollout (read before write); keep Playwright only as a temporary fallback per app |
| M365 Graph consent / permissions complexity | Delays mail/calendar features | Start with least-privilege delegated scopes; pilot with a small group; admin-consent the app in Entra |
| Secrets sprawl (API keys, OAuth tokens, sumHR creds) | Security exposure | Central secret store on the backend; per-user OAuth (no shared creds); migrate off the demo's shipped Azure key and `keyring`-on-desktop model; rotate regularly |
| Agent takes a wrong/unintended action | Bad writes to real systems | Mandatory confirmation card for all writes; role-based tool gating; full audit log; dry-run mode per connector |
| LLM hallucination / ungrounded answers | Wrong info or fake "success" | Keep strict grounding prompt; structured tool results only (no narrated success); report real outcomes/reference ids |
| New backend service to operate | Ops burden, downtime | Start minimal; monitoring + health checks; graceful degradation (Q&A works even if a connector is down) |
| Summary quality / ranking poor | Users stop trusting briefings | Tune ranking; let users give feedback; show source links so they can verify |
| OneDrive-synced dev folder file locks (current pain) | Build/dev friction | Continue existing build-script mitigations; move backend out of the synced folder |

### Dependencies
- **Technical:** stable internal-app APIs (HR/PM/CRM/finance); Microsoft Graph access
  + Entra app registration; Azure OpenAI (or chosen host); a central backend host +
  secret store + scheduler; MCP runtime/SDK.
- **Data:** HR policy documents (already loaded); per-app data schemas; role/permission
  definitions.
- **Security:** OAuth/SSO (Entra), secret management, audit logging, data-handling
  review for mail/calendar content.
- **Business / stakeholder:** app-owner teams (to confirm/extend APIs), HR (policy &
  POSH hand-off), IT/admin (onboarding, consent, access policy), leadership sign-off
  for accessing employee mail/calendar.
- **Infrastructure:** hosting for the backend service and scheduled jobs; monitoring.

---

## Appendix A — Current Baseline (what exists today)

| Area | Today |
|---|---|
| UI | PySide6 floating desktop widget (`ChatLauncher` + `ChatWidget`), frameless, always-on-top |
| LLM | Azure OpenAI (`gpt-4.1-mini` deployment), streaming; `LLM_PROVIDER` switch supports local Ollama too |
| Knowledge | 16 HR policies in `policies/All_policies.md`; SharePoint links in `policy_links.json` |
| Retrieval | Keyword scoring with stemming + synonyms (`retriever.py`) — **no embeddings** |
| Intent | `classify_intent()` → small-talk / `apply_leave` / `policy`; canned replies for small-talk |
| Actions | Leave filing only, via **Playwright** UI automation on sumHR (`leave_automation.py`); mandatory `ConfirmationCard` |
| Secrets | sumHR creds in Windows Credential Manager via `keyring`; Azure key in `.env` (ships in demo zip — must rotate) |
| Integrations | **None** — no MCP, Teams, Outlook, or calendar code yet |
| Packaging | PyInstaller one-folder build via `build.ps1` |

## Appendix B — Suggested Phasing (non-binding)

1. **Phase 0 — Foundations:** stand up the central service + agent loop with
   tool-calling; migrate existing policy Q&A onto it; Entra app registration.
2. **Phase 1 — Insights (read-only):** Outlook inbox + calendar summaries via Graph;
   morning schedule; meeting summary where notes exist.
3. **Phase 2 — HR MCP server:** replace Playwright leave with an API tool;
   leave balance/status; keep the confirmation card.
4. **Phase 3 — PM MCP server:** create/list/status tickets; project status.
5. **Phase 4 — CRM & Finance MCP servers:** read-first, then guarded writes.
6. **Phase 5 — Hardening / productization:** role-based access, audit logging,
   multi-tenancy groundwork for external resale.
