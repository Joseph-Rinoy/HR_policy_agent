from __future__ import annotations

import re
from collections import Counter

from policy_loader import PolicySection


STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "have", "has", "had",
    "of", "in", "on", "at", "to", "for", "with", "and", "or", "but", "if",
    "what", "which", "who", "whom", "when", "where", "why", "how",
    "this", "that", "these", "those",
    "i", "you", "he", "she", "it", "we", "they",
    "my", "your", "our", "their", "me", "us", "them",
    "can", "could", "should", "would", "will", "shall", "may", "might", "must",
    "not", "no", "about", "into", "from", "by", "as", "than", "then", "so",
    "also", "just", "any", "some", "all", "many", "much", "more", "most",
    "few", "every", "each", "one", "two", "get", "got",
    "policy", "policies", "company", "employee", "employees",
}


def _stem(token: str) -> str:
    # Very light stemming: fold simple plurals so "leaves" matches "leave".
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]*", text.lower())
    return [_stem(t) for t in tokens if t not in STOPWORDS and len(t) > 1]


# Groups of words employees use interchangeably with the official policy terms,
# e.g. people ask about "annual leave" but the policy calls it "Earned Leave".
_SYNONYM_GROUPS = [
    ["annual", "earned", "vacation", "privilege", "pto"],
    ["sick", "medical", "illness"],
    ["casual", "personal"],
    ["maternity", "paternity", "parental", "childbirth"],
    ["compensatory", "compoff"],
    ["wfh", "remote", "telework", "telecommute"],
    ["resign", "resignation", "quit", "notice", "termination", "separation"],
    ["harassment", "posh", "sexual"],
    ["reimbursement", "reimburse", "expense", "travel"],
    ["probation", "confirmation"],
]

# token -> set of equivalent tokens (all stemmed to match _tokenize output)
_SYNONYMS: dict[str, set[str]] = {}
for _group in _SYNONYM_GROUPS:
    _stemmed = {_stem(w) for w in _group}
    for _w in _stemmed:
        _SYNONYMS.setdefault(_w, set()).update(_stemmed)


def _expand(tokens: list[str]) -> list[str]:
    expanded = list(tokens)
    for t in tokens:
        if t in _SYNONYMS:
            expanded.extend(_SYNONYMS[t] - {t})
    return expanded


def _score(query_tokens: list[str], section: PolicySection) -> float:
    if not query_tokens:
        return 0.0
    body = Counter(_tokenize(section.content))
    title = Counter(_tokenize(section.title) + _tokenize(section.file))
    score = 0.0
    for qt in query_tokens:
        score += body.get(qt, 0) * 1.0
        score += title.get(qt, 0) * 3.0
    return score


def retrieve_scored(
    query: str,
    sections: list[PolicySection],
    top_k: int = 3,
    max_chars: int = 2500,
) -> list[tuple[PolicySection, float]]:
    """Return the top sections for a query, each paired with its score so callers
    can judge match strength (e.g. to tell a real question from a vague follow-up)."""
    qtokens = _expand(_tokenize(query))
    if not qtokens or not sections:
        return []

    scored = [(s, _score(qtokens, s)) for s in sections]
    scored = [t for t in scored if t[1] > 0]
    scored.sort(key=lambda x: x[1], reverse=True)

    chosen: list[tuple[PolicySection, float]] = []
    used = 0
    for s, sc in scored[:top_k]:
        if chosen and used + len(s.full_text) > max_chars:
            break
        chosen.append((s, sc))
        used += len(s.full_text)
    return chosen
