"""Routing — decide which internal system(s) a query touches.

Four systems × many tools each would overwhelm tool selection if every tool
were always in context. This step narrows the set: only the relevant system's
tools are exposed to the model for a given turn. An HR-policy question pulls in
*no* tools (it is answered from the RAG CONTEXT), so retrieval can't be shadowed
and the finance tools never tempt the model on an unrelated question.

This is a lightweight keyword classifier on purpose — it is vendor-neutral and
adds no latency. If routing ever needs an LLM, it MUST go through
:mod:`llm_adapter` (or a small dedicated classifier); it must never recouple
routing to one vendor's SDK.

Per-system keyword sets live here; add a system by adding its keywords. The
gateway still has the final say (allow-list + configured/available systems), so
a hit here only *proposes* a system.
"""

from __future__ import annotations

import re

# Keyword cues per system. Kept deliberately specific so policy questions that
# merely mention money words don't wrongly pull in finance tools.
_SYSTEM_KEYWORDS: dict[str, list[str]] = {
    "finance": [
        r"expense", r"expenses", r"reimburse", r"reimbursement", r"advance",
        r"advances", r"invoice", r"invoices", r"bill", r"bills", r"vendor",
        r"vendors", r"client", r"clients", r"dashboard", r"payment", r"payments",
        r"pending\s+approval", r"my\s+finance", r"finance\s+data",
    ],
    # "hr": [r"leave\s+balance", r"my\s+leave", r"attendance", r"payslip", ...],
    # "project": [r"ticket", r"project\s+status", r"task", ...],
    # "crm": [r"deal", r"lead", r"opportunity", r"pipeline", ...],
}

_SYSTEM_RES: dict[str, "re.Pattern[str]"] = {
    system: re.compile(r"\b(?:" + "|".join(words) + r")\b", re.IGNORECASE)
    for system, words in _SYSTEM_KEYWORDS.items()
    if words
}


def route_systems(text: str) -> list[str]:
    """Return the systems whose tools should be offered for this query.

    Empty list ⇒ a pure-RAG turn (no tools). Order follows :data:`_SYSTEM_KEYWORDS`.
    """
    if not text:
        return []
    return [system for system, rx in _SYSTEM_RES.items() if rx.search(text)]
