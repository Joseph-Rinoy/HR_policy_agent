# CLAUDE.md — Qubiqon HR Policy Assistant ("Qubi")

A small Windows desktop app: a floating chat assistant that answers Qubiqon HR
policy questions using **Azure OpenAI** over a local set of policy documents
(RAG-lite). Built with **PySide6 (Qt for Python)**. Currently a demo/prototype
for internal feedback — not a production release.

## Run & environment

- **Python**: 3.11, in a local `venv/` (Windows). Use `.\venv\Scripts\python.exe`.
- **Run the app**: `.\venv\Scripts\python.exe app.py`
- **Platform**: Windows 11, PowerShell 7+. The project lives in a **OneDrive-synced
  folder** — OneDrive can briefly lock files during builds (see Build gotchas).
- **Dependencies** (`requirements.txt`): `PySide6`, `openai`, `python-dotenv`.
  (No heavy ML deps — retrieval is pure-Python keyword matching.)

## Configuration (`.env`, next to `app.py` / the `.exe`)

Loaded via `python-dotenv` from `app_base_dir()` (see `paths.py`), so it's found
regardless of the working directory. `.env.example` is the template.

- `AZURE_OPENAI_ENDPOINT` — e.g. `https://<resource>.openai.azure.com/`
- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_API_VERSION` — a **REST API version** like `2024-10-21`
  (NOT the model version `2025-04-14`; using a model version → `404 Resource not found`).
- `AZURE_OPENAI_DEPLOYMENT` — the Azure **deployment name** (NOT the base model
  name). The real deployment here is `gpt-4.1-mini`. `ChatWidget`'s default model
  is `AZURE_OPENAI_DEPLOYMENT`, so the app honors `.env` — do not hardcode a model.

⚠️ The Azure key ships inside the demo zip and is extractable. Rotate/delete it
after a demo and set a low Azure spending cap.

## Architecture / files

| File | Role |
|---|---|
| `app.py` | Entry point. Creates `ChatLauncher` (floating button) + `ChatWidget`; click toggles the chat. Frameless always-on-top tool windows. |
| `chat_widget.py` | All UI: `ChatLauncher` (logo button), `ChatWidget` (chat window), `MessageBubble`, `LlmWorker` (QThread streaming), `FlowLayout` (wrapping chip rows). Theme constants + stylesheet live here. |
| `llm.py` | Azure OpenAI client + streaming `stream_answer()`, the `SYSTEM_PROMPT`, and `build_user_message()` (builds the CONTEXT block). |
| `policy_loader.py` | `load_policies()` → list of `PolicySection`; parses `policies/*.md` by headings, attaches SharePoint `url` from `policy_links.json`. `load_contacts()` reads `hr_contact`/`posh_contact` from the handbook frontmatter. |
| `retriever.py` | `retrieve_scored()` — keyword scoring with light stemming + synonym expansion, returning each section with its score. No embeddings. |
| `intent.py` | `classify_intent()` → `greeting` / `thanks` / `farewell` / `ack` / `meta` / `apply_leave` / `policy`. Small-talk + `apply_leave` get canned replies (no LLM call). `apply_leave` fires only on imperative *filing* requests, not leave *questions* (`_LEAVE_QUESTION_RE` guard). |
| `paths.py` | `app_base_dir()` — folder of the `.exe` when frozen, else the project dir. Used for `.env`, `policies/`, `Qubi.png`. |
| `test_llm.py` | Standalone Azure connectivity test (no GUI): `python test_llm.py`. |
| `build.ps1` | Packages a standalone zip via PyInstaller (see Build). |
| `policies/All_policies.md` | The 16 HR policies (one `## Policy N: Name` per policy). |
| `policies/policy_links.json` | Maps each `Policy N: Name` heading → SharePoint URL. Blank `""` = cite by name only. |
| `Qubi.png` | The robot mascot logo (1254×1254), used as the launcher icon. |

### Request flow
`_on_send` → `_submit` → `classify_intent` (canned reply for small-talk /
`apply_leave`) → `retrieve_scored()` top sections → `LlmWorker` streams
`stream_answer()` → chunks append to the bot `MessageBubble` → on completion
`finalize()` renders HTML.

### Leave application (button-only)
Filing leave is triggered **only** by the header **🗓** button (`leave_btn` →
`_open_leave_form`), which opens a blank `ConfirmationCard` directly (no LLM
extraction — the form is always filled in by hand). Typed leave-filing requests
do not open the form — `classify_intent` returns `apply_leave` and Qubi posts a
**nudge** to the 🗓 button (from the `_CANNED_REPLIES["apply_leave"]` pool). The
card → `_on_leave_confirmed` → `LeaveWorker` → `apply_leave()` chain is unchanged.
`_open_leave_form` ignores clicks while busy or a card is already open.

### RAG details
- **Retrieval** (`retriever.py`): tokenize → drop stopwords → light stem (plural
  `s` folding, so "leaves"≈"leave") → expand synonyms (e.g. user says "annual
  leave", policy says "Earned Leave"; `_SYNONYM_GROUPS` maps these). Scores title
  matches 3× body. Returns top 3 within a char budget.
- **Grounding**: `SYSTEM_PROMPT` forbids outside knowledge; if context lacks the
  answer the model must reply with the exact "couldn't find this…" line.

## UI conventions (chat_widget.py)

- **Brand theme** = Qubi logo turquoise-green. Constants at top of file:
  `BRAND_GREEN #00d8a0` (accents/rings), `BRAND_GREEN_DEEP #00a386` (filled
  elements w/ white text), `BRAND_GREEN_DEEPER #00876f` (hover), `LINK_COLOR
  #00806a`. Filled elements use the *deeper* green so white text stays legible;
  bright green is for accents only. Stylesheet hexes are in `_stylesheet()`.
- **Launcher icon**: `Qubi.png` clipped into the circle at **0.88** scale (bigger
  clips the antenna/tail). Falls back to a drawn "AI sparkle" if the PNG is missing.
- **"AI thinking" loader**: animated 3 dots (`QTimer`, brand green) shown while
  waiting; replaced by the first streamed chunk.
- **Answer rendering**: streamed text is shown as plain text; on `finalize()` it's
  converted by `_markdown_to_html()` → handles `**bold**`, `[text](url)` links
  (with inline `LINK_COLOR` so the stylesheet can't override it), `- `/`* ` →
  real `•` bullets, and `\n` → `<br>`. NOTE: this is a *minimal* markdown subset —
  no real lists/headings/tables. The URL link regex allows **balanced parens**
  (SharePoint URLs like `.../Work-From-Home-(WFH)-Process.aspx`).

## Answer style (the prompt)

`SYSTEM_PROMPT` in `llm.py` enforces crisp, scannable answers: one-line direct
answer, then short `- ` bullets (<~12 words), **bold** key numbers, numbered
steps for procedures, no headings, ending with a `Source: [Policy Name](URL)`
line using only URLs present in the CONTEXT. `max_tokens=400`, `temperature=0.3`.

**Tone**: a `TONE` block asks for a warm, professional-friendly HR voice (brief
acknowledgement, reassuring) *without* padding answers. Keep warmth in phrasing,
not length. Grounding rules ("ONLY the CONTEXT", "Never use outside knowledge")
are unchanged.

**Not-found / contacts**: when the answer isn't in the docs (or it's out of
scope), the model replies kindly and hands off to a real, clickable **mailto**
link. `stream_answer(..., contacts=...)` appends a CONTACTS line built from
`load_contacts()` (HR for general, POSH for harassment). `_markdown_to_html`
already linkifies `[text](mailto:...)`.

**Suggestion chips** (`chat_widget.py`): clickable pills that call `_submit()`.
`_STARTER_CHIPS` show under the welcome (removed on first send). After a
*grounded* policy answer (gated by `_pending_has_context`), follow-up chips are
**context-aware**: `_followup_chips_for()` offers the sibling subsections of the
policy just answered (anchored on the top retrieved section's `parent_heading`,
labels cleaned by `_topic_label`), e.g. Travel → "Booking Rules", "Travel Class".
Single-section policies (no subsections) fall back to the generic `_FOLLOWUP_CHIPS`.
Both rows use `FlowLayout` so chips wrap in the 380px window. `_on_send` reads the
input then delegates to `_submit(text)`; chips call `_submit()` directly.
Friendlier errors: `_on_failed` shows a warm line and keeps the raw error in the
bubble tooltip.

## Build & package (`.\build.ps1`)

Produces `dist\QubiqonPolicyAssistant\` (standalone, no Python needed) and
`QubiqonPolicyAssistant.zip`. One-folder, `--windowed`. Copies `policies/`,
`.env`, and `Qubi.png` next to the `.exe` (the app reads them via `app_base_dir()`).

**Build gotchas**
- The app must resolve `policies/`, `.env`, `Qubi.png` via `paths.app_base_dir()`
  (handles PyInstaller's frozen path). Never use `Path(__file__)` for these.
- **OneDrive lock**: `--clean` could fail with `PermissionError` on `build\...`.
  `build.ps1` now pre-cleans with a retry and checks `$LASTEXITCODE` after
  PyInstaller (native commands don't trip `$ErrorActionPreference`), so it fails
  loudly instead of zipping a stale build. If locks persist, pause OneDrive sync.
- Always test the **unzipped exe on a clean machine** before sharing; bundling
  gaps don't show on the dev box.
- Unsigned exe → Windows SmartScreen warns on first run ("More info → Run anyway").

## Conventions

- `from __future__ import annotations` at top of every module; type hints used.
- Keep new code matching the existing style (small, dependency-light, pure-Python
  where possible). Match comment density of surrounding code.
- After editing `policy_links.json` or policy `.md` files, **restart the app** —
  policies/links load once at startup.
