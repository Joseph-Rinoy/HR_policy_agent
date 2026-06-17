from __future__ import annotations

import re


# Small-talk split into buckets so each gets a fitting reply (a "bye" shouldn't
# be answered like a "hi"). Each maps to its own intent label.
_GREETING_WORDS = [
    r"hi",
    r"hello",
    r"hey",
    r"hiya",
    r"yo",
    r"good\s+morning",
    r"good\s+afternoon",
    r"good\s+evening",
    r"how\s+are\s+you",
    r"how\s+s\s+it\s+going",
    r"how\s+is\s+it\s+going",
]

_THANKS_WORDS = [
    r"thanks",
    r"thank\s+you",
    r"thank\s+u",
    r"ty",
    r"thx",
    r"much\s+appreciated",
    r"appreciate\s+it",
]

_FAREWELL_WORDS = [
    r"bye",
    r"goodbye",
    r"good\s+night",
    r"see\s+you",
    r"see\s+ya",
    r"cya",
    r"take\s+care",
]

_ACK_WORDS = [
    r"ok",
    r"okay",
    r"k",
    r"cool",
    r"nice",
    r"great",
    r"sure",
    r"got\s+it",
    r"awesome",
    r"perfect",
]

_OPTIONAL_TRAIL = r"(?:\s+(?:there|team|bot|everyone|all|claude|qubi|so\s+much))?"


def _smalltalk_re(words: list[str]) -> "re.Pattern[str]":
    return re.compile(r"^(?:" + "|".join(words) + r")" + _OPTIONAL_TRAIL + r"$")


_GREETING_RE = _smalltalk_re(_GREETING_WORDS)
_THANKS_RE = _smalltalk_re(_THANKS_WORDS)
_FAREWELL_RE = _smalltalk_re(_FAREWELL_WORDS)
_ACK_RE = _smalltalk_re(_ACK_WORDS)

_META_PHRASES = [
    "who are you",
    "what are you",
    "what is your name",
    "your name",
    "what can you do",
    "what do you do",
    "what can you help",
    "how can you help",
    "what kind of questions",
    "what sort of questions",
    "are you a bot",
    "are you human",
    "are you ai",
    "are you an ai",
]


# A leave *application* needs an action verb AND a leave keyword together, so
# that policy questions which merely mention leave (e.g. "what is the sick leave
# policy") stay routed to "policy".
_ACTION_RE = re.compile(
    r"\b(apply|applying|take|taking|book|booking|request|file|filing|raise|"
    r"put in|need|want)\b"
)
_LEAVE_HINT_RE = re.compile(
    r"\b(leave|compoff|comp off|optional holiday|lwp)\b"
)
# A leave message that is really a *question* about the policy (not a request to
# file one). When this matches, route to "policy" so the form isn't suggested for
# e.g. "how do I apply for leave?" or "what is the leave policy?".
_LEAVE_QUESTION_RE = re.compile(
    r"^(what|how|when|where|why|which|who|can|could|should|do|does|is|are)\b"
    r"|\b(policy|process|rule|rules|eligib|entitle|entitlement|balance|"
    r"how many|about|know)\b"
)


def _normalise(text: str) -> str:
    lowered = text.lower().strip()
    stripped = re.sub(r"[^\w\s]", " ", lowered)
    return re.sub(r"\s+", " ", stripped).strip()


def classify_intent(text: str) -> str:
    """Return 'greeting', 'thanks', 'farewell', 'ack', 'meta', 'apply_leave',
    or 'policy'."""
    norm = _normalise(text)
    if not norm:
        return "policy"
    if _GREETING_RE.match(norm):
        return "greeting"
    if _THANKS_RE.match(norm):
        return "thanks"
    if _FAREWELL_RE.match(norm):
        return "farewell"
    if _ACK_RE.match(norm):
        return "ack"
    # Check the action intent before greeting/meta fall-through so a phrase like
    # "apply sick leave" wins over an incidental keyword match. Skip it when the
    # message is really a question about the policy ("how do I apply for leave?").
    if (
        _ACTION_RE.search(norm)
        and _LEAVE_HINT_RE.search(norm)
        and not _LEAVE_QUESTION_RE.search(norm)
    ):
        return "apply_leave"
    if norm in ("help", "help me"):
        return "meta"
    for phrase in _META_PHRASES:
        if phrase in norm:
            return "meta"
    return "policy"
