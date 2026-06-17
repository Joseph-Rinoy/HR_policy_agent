"""Standalone connectivity test for the Azure OpenAI deployment.

Run it without touching the GUI:

    venv\\Scripts\\python.exe test_llm.py

It checks that the env vars are set, then streams a short reply from the
configured gpt-4o-mini deployment so you can confirm the connection works.
"""

from __future__ import annotations

import sys

import llm


def main() -> int:
    print("Azure OpenAI configuration")
    print(f"  endpoint   : {llm.AZURE_OPENAI_ENDPOINT or '(not set)'}")
    print(f"  api_version: {llm.AZURE_OPENAI_API_VERSION}")
    print(f"  deployment : {llm.AZURE_OPENAI_DEPLOYMENT}")
    print(f"  api_key    : {'set' if llm.AZURE_OPENAI_API_KEY else '(not set)'}")
    print()

    if not llm.AZURE_OPENAI_ENDPOINT or not llm.AZURE_OPENAI_API_KEY:
        print("ERROR: set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY in .env")
        return 1

    # A tiny fake policy section so the system prompt has something to cite.
    from policy_loader import PolicySection

    sections = [
        PolicySection(
            file="test.md",
            heading="Connection Test",
            parent_heading="",
            content="The annual leave entitlement is 20 days per year.",
        )
    ]

    print("Streaming reply to: 'How many days of annual leave do I get?'\n")
    print("-" * 50)
    reply = ""
    for chunk in llm.stream_answer(
        "How many days of annual leave do I get?", sections
    ):
        sys.stdout.write(chunk)
        sys.stdout.flush()
        reply += chunk
    print()
    print("-" * 50)

    # stream_answer yields its error messages as plain text, so check for them.
    error_markers = ("LLM request failed", "Cannot reach Azure OpenAI", "is not configured")
    if not reply.strip():
        print("\nERROR: no text returned from the model.")
        return 1
    if any(marker in reply for marker in error_markers):
        print("\nERROR: the model call failed (see message above).")
        return 1

    print("\nOK: connection works.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
