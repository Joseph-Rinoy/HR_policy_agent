from __future__ import annotations

import os
from collections.abc import Iterator

from paths import app_base_dir

try:
    from dotenv import load_dotenv

    # Load the .env that sits next to the app (or exe), regardless of the
    # current working directory the app was launched from.
    load_dotenv(app_base_dir() / ".env")
except ImportError:  # python-dotenv is optional; env vars can be set directly
    pass

from openai import APIConnectionError, AzureOpenAI, OpenAI, OpenAIError

from policy_loader import PolicySection


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


SYSTEM_PROMPT = (
    "You are Qubi, Qubiqon's HR policy assistant. Answer using ONLY the CONTEXT in "
    "each message. Never use outside knowledge.\n\n"
    "TONE — sound like a warm, approachable HR colleague:\n"
    "- When it feels natural, you may open with a brief, friendly acknowledgement "
    "of a few words. VARY it and let it fit the specific question — do not reuse "
    "the same opener every time, and never default to one stock phrase like "
    "\"Happy to help\". Often it's better to just answer directly with no opener.\n"
    "- Be reassuring and human, but stay concise and easy to scan. Do not add "
    "filler, pad answers, or get chatty. Warmth is in the phrasing, not length.\n\n"
    "IF YOU CAN'T ANSWER: If the CONTEXT does not contain the answer, say so kindly "
    "and point the person to HR — e.g. \"I couldn't find that in our current policy "
    "docs — for this one it's best to check with [HR](mailto:HR@qubiqon.io).\" Use "
    "the real contact from the CONTACTS line below (the POSH contact for harassment "
    "matters). If the question is not about company policy, gently say you can only "
    "help with Qubiqon HR policy questions.\n\n"
    "ANSWER STYLE — keep it crisp and easy to scan:\n"
    "- Open with ONE short sentence that directly answers the question.\n"
    "- Then add only the key details as short bullet points, each on its own line "
    "starting with \"- \".\n"
    "- Keep each bullet under ~12 words. No long paragraphs, no filler, no preamble "
    "like \"According to the policy\".\n"
    "- Put key numbers, limits and deadlines in **bold** (e.g. **18 days**, "
    "**3 days' notice**).\n"
    "- For a step-by-step process, use a numbered list (1., 2., 3.), one short step "
    "per line.\n"
    "- Do not use headings. Include only what the user asked about.\n\n"
    "SOURCE: End with a line in the form \"Source: [Policy Name](URL)\" using the URL "
    "from the matching [Source ...] block. ALWAYS write it as a Markdown link with "
    "square brackets around the name and round brackets around the URL — never paste a "
    "bare URL or wrap the URL in parentheses after the name. Use only URLs given in the "
    "CONTEXT — never invent one. If no URL is present, write \"Source: Policy Name\" "
    "with no link. Cite more than one policy only if the answer genuinely spans them.\n\n"
    "FOLLOW-UPS: For a follow-up like \"make it shorter\" or \"explain that\", keep "
    "answering about the SAME policy from the earlier turn — reuse the earlier CONTEXT "
    "and its Source link. Do not switch topics unless the user clearly asks about a "
    "different policy.\n"
    "- If the user asks to shorten or summarise (e.g. \"make it shorter\", \"tldr\", "
    "\"in brief\"), return a MORE concise version of your previous answer — keep only "
    "the few most essential points, use fewer and shorter bullets, and NEVER make it "
    "longer than before. Keep the Source line."
)


def build_user_message(question: str, sections: list[PolicySection]) -> str:
    if not sections:
        context_block = "(no relevant policy excerpts were found)"
    else:
        blocks = []
        for s in sections:
            url_part = f" -- URL: {s.url}" if s.url else ""
            blocks.append(f"[Source: {s.title}{url_part}]\n{s.content}")
        context_block = "\n\n---\n\n".join(blocks)
    return f"CONTEXT:\n{context_block}\n\nQUESTION: {question}"


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


def _system_content(contacts: dict | None) -> str:
    """The system prompt, with the real HR contacts appended so the model can
    offer a clickable mailto link when an answer isn't in the docs."""
    if not contacts:
        return SYSTEM_PROMPT
    hr = contacts.get("hr", "")
    posh = contacts.get("posh", "")
    lines = []
    if hr:
        lines.append(f"general HR queries — [{hr}](mailto:{hr})")
    if posh:
        lines.append(f"harassment / POSH matters — [{posh}](mailto:{posh})")
    if not lines:
        return SYSTEM_PROMPT
    return SYSTEM_PROMPT + "\n\nCONTACTS to share when needed: " + "; ".join(lines) + "."


def stream_answer(
    question: str,
    sections: list[PolicySection],
    model: str | None = None,
    history: list[dict] | None = None,
    contacts: dict | None = None,
    max_tokens: int = 400,
    provider: str | None = None,
) -> Iterator[str]:
    provider = provider or DEFAULT_PROVIDER
    model_name = model or model_for(provider)

    # Azure needs an endpoint + key; Ollama runs locally, so a missing server
    # surfaces below as APIConnectionError (handled with a friendly message).
    if provider == "azure" and (
        not AZURE_OPENAI_ENDPOINT or not AZURE_OPENAI_API_KEY
    ):
        yield (
            "I'm not fully set up yet — my connection to the AI service is missing. "
            "Please ask IT to configure the Azure OpenAI settings, then try again."
        )
        return

    # Prior turns (each already a {"role", "content"} dict) let the model handle
    # follow-ups like "make it shorter" using the earlier CONTEXT and answer.
    messages = [{"role": "system", "content": _system_content(contacts)}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": build_user_message(question, sections)})

    try:
        client = _make_client(provider)
        stream = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0.3,
            max_tokens=max_tokens,
            stream=True,
        )
        for event in stream:
            if not event.choices:
                continue
            chunk = event.choices[0].delta.content or ""
            if chunk:
                yield chunk
    except APIConnectionError:
        yield (
            "I'm having trouble connecting right now. Please check your internet "
            "connection and try again in a moment."
        )
    except OpenAIError:
        yield (
            "Sorry, something went wrong on my end. Please try asking again in a "
            "moment."
        )
