from __future__ import annotations

import html as html_lib
import math
import random
import re
from pathlib import Path

from PySide6.QtCore import (
    QDate,
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QLinearGradient,
    QMovie,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDateEdit,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from credentials import get_credentials, set_credentials
from intent import classify_intent
from leave_automation import LEAVE_TYPES, apply_leave
from llm import (
    DEFAULT_PROVIDER,
    PROVIDERS,
    build_user_message,
    model_for,
    stream_answer,
)
from paths import app_base_dir
from policy_loader import PolicySection, load_contacts, load_policies
from retriever import retrieve_scored


LOGO_FILENAME = "Qubi.png"
LAUNCHER_GIF_FILENAME = "Qubi_launcher.gif"
TYPING_GIF_FILENAME = "typing.gif"


WIDGET_WIDTH = 380
WIDGET_HEIGHT = 560

# --- Brand theme (matched to the Qubi logo's turquoise-green) --------------
BRAND_GREEN = "#00d8a0"        # bright logo green (accents, rings)
BRAND_GREEN_DEEP = "#00a386"   # filled elements w/ white text
BRAND_GREEN_DEEPER = "#00876f"  # hover/pressed
LINK_COLOR = "#00806a"         # readable teal link on white
THINK_ACTIVE_COLOR = "#00b88f"
THINK_IDLE_COLOR = "#cdd2e0"
RING_COLOR = BRAND_GREEN


def _anchor(url: str, label: str) -> str:
    return (
        f'<a href="{url}" '
        f'style="color:{LINK_COLOR}; text-decoration:underline;">{label}</a>'
    )


# A URL may contain balanced parentheses, e.g. .../Work-From-Home-(WFH).aspx,
# so match either non-paren chars or a single balanced (...) group.
_URL_BODY = r"(?:[^()\s]|\([^()\s]*\))+"


class FlowLayout(QLayout):
    """A layout that lays widgets left-to-right and wraps to the next line when
    it runs out of width — used for the suggestion "chips" so they fit the
    narrow chat window regardless of label length. (Standard Qt pattern.)"""

    def __init__(self, parent: QWidget | None = None, spacing: int = 6):
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        if parent is not None:
            self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802 (Qt override)
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only: bool) -> int:
        m = self.contentsMargins()
        x = rect.x() + m.left()
        y = rect.y() + m.top()
        right = rect.right() - m.right()
        line_height = 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width()
            if next_x > right and line_height > 0:
                x = rect.x() + m.left()
                y = y + line_height + self._spacing
                next_x = x + hint.width()
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x + self._spacing
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + m.bottom()


def _markdown_to_html(text: str) -> str:
    """Minimal Markdown -> HTML so links can carry an inline color the
    widget stylesheet can't override. Handles links, bold, and line breaks."""
    out = html_lib.escape(text)

    # Anchors are stashed as placeholders first so a later bare-URL pass can't
    # re-link the href inside one we just built.
    saved: list[str] = []

    def _stash(html_frag: str) -> str:
        saved.append(html_frag)
        return f"\x00{len(saved) - 1}\x00"

    # Turn "- " / "* " list markers at the start of a line into real bullets.
    out = re.sub(r"(?m)^[ \t]*[-*][ \t]+", "&#8226;&nbsp;", out)
    # 1) Proper Markdown links: [label](url).
    out = re.sub(
        rf"\[([^\]]+)\]\(({_URL_BODY})\)",
        lambda m: _stash(_anchor(m.group(2), m.group(1))),
        out,
    )
    # 2) Fallback: a bare URL the model wrote without Markdown (e.g.
    #    "Source: POSH Policy (https://...)") — still make it clickable.
    out = re.sub(
        rf"https?://{_URL_BODY}",
        lambda m: _stash(_anchor(m.group(0), m.group(0))),
        out,
    )
    # 3) Fallback: a bare email address (the model often prints HR@qubiqon.io as
    #    plain/bold text) — turn it into a clickable mailto link. Anchors built
    #    above are already stashed, so their hrefs aren't re-matched here.
    out = re.sub(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        lambda m: _stash(_anchor(f"mailto:{m.group(0)}", m.group(0))),
        out,
    )
    out = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", out)
    out = out.replace("\n", "<br>")
    # Restore the stashed anchors.
    for i, frag in enumerate(saved):
        out = out.replace(f"\x00{i}\x00", frag)
    return out


# Starter suggestions shown under the welcome: (chip label, question sent).
# Labels carry a topic emoji for scannability; the question stays plain text so
# the keyword retriever (retrieve_scored) isn't thrown off. Each maps to a real
# policy: WFH=P5, Leave=P2, Working Hours=P1, Notice Period=P9, Travel=P16,
# Dress Code=P6.
_STARTER_CHIPS = [
    ("🏠  Work from home", "How does the work-from-home policy work?"),
    ("🌴  Leave & holidays", "How much leave do I get, and what types are there?"),
    ("🕘  Working hours", "What are the standard working hours?"),
    ("📅  Notice period", "What's the notice period when I resign?"),
    ("✈️  Travel", "What does the travel policy cover?"),
    ("👔  Dress code", "What's the dress code?"),
]

# Generic fallback follow-ups when a policy has no sibling sub-topics to suggest.
_FOLLOWUP_CHIPS = [
    ("Make it shorter", "Make it shorter"),
    ("Tell me more", "Tell me more about this"),
    ("How do I request this?", "How do I request this?"),
]


# A "shorten this" follow-up: cap the reply hard so it can't run longer.
_CONDENSE_RE = re.compile(
    r"\b(short(er|en)?|brief(ly)?|concise|condense|summar(y|ise|ize)|tl;?dr|"
    r"in short|too long|less detail)\b",
    re.IGNORECASE,
)
_CONDENSE_MAX_TOKENS = 160


def _is_condense_request(text: str) -> bool:
    return bool(_CONDENSE_RE.search(text))


# Cues that a low-scoring message is a follow-up about the SAME policy (so we
# reuse the previous topic) rather than a brand-new / off-topic question (which
# should get an empty context, not the last policy's chips).
_FOLLOWUP_CUE_RE = re.compile(
    r"\b(more|detail|details|elaborate|explain|expand|continue|go on|"
    r"what about|how about|what else|in detail|"
    r"how\s+(do|can|should|to)|request|apply|process|step|steps|procedure)\b",
    re.IGNORECASE,
)
# A message that opens with a continuation word ("and for managers?", "also…").
_CONTINUATION_RE = re.compile(
    r"^(and|but|also|or|plus|so|what about|how about)\b", re.IGNORECASE
)


def _is_followup(text: str) -> bool:
    t = text.strip()
    return (
        _is_condense_request(t)
        or bool(_FOLLOWUP_CUE_RE.search(t))
        or bool(_CONTINUATION_RE.match(t))
    )


def _topic_label(heading: str) -> str:
    """Turn a subsection heading into a short, friendly chip label, e.g.
    'Sick Leave (SL) — Details' -> 'Sick Leave', 'Step 1: Apply' -> 'Apply'."""
    label = re.sub(r"\s*\([^)]*\)", "", heading)          # drop "(SL)" etc.
    label = re.split(r"\s+[—–-]\s+", label, maxsplit=1)[0]  # drop "— Details"
    label = re.sub(r"^Step\s+\d+:\s*", "", label)          # "Step 1: X" -> "X"
    return label.strip()


# Small-talk replies: a pool per intent so Qubi varies its responses instead of
# repeating one line. A reply is picked at random (avoiding the immediate repeat).
_CANNED_REPLIES = {
    "greeting": [
        "Hi there, I'm Qubi 👋 — happy to help. Ask me anything about Qubiqon's HR policies.",
        "Hello! Qubi here. What HR policy can I help you with today?",
        "Hey! 😊 Ask me about leave, WFH, travel, conduct — anything HR.",
        "Hi! Good to see you. What would you like to know about Qubiqon's policies?",
    ],
    "thanks": [
        "You're very welcome! 😊 Anything else I can help with?",
        "Happy to help! Let me know if there's anything else.",
        "Anytime! I'm here whenever you have a policy question.",
        "My pleasure! Feel free to ask me anything else.",
    ],
    "farewell": [
        "Take care! 👋 Come back anytime you need an HR answer.",
        "Bye for now! I'm here whenever you need me.",
        "See you later! Reach out anytime with a policy question.",
        "Have a great day! 😊 I'll be right here if anything comes up.",
    ],
    "ack": [
        "👍 Anything else I can help you with?",
        "Got it! Let me know if you have another question.",
        "Sure thing! What else would you like to know?",
        "Glad that helps! Ask me anything else, anytime.",
    ],
    "meta": [
        "I'm Qubi, your friendly Qubiqon HR helper. Ask me about leave, benefits, "
        "attendance, working hours, conduct, reimbursements — any company HR policy.",
        "I'm Qubi 😊 — I can help with leave, WFH, travel, conduct, reimbursements, "
        "and all things Qubiqon HR. What would you like to know?",
        "Think of me as your HR buddy at Qubiqon. I answer questions about company "
        "policies — leave, attendance, benefits, and more. Just ask!",
    ],
    "apply_leave": [
        "Happy to help you apply! Just tap the 🗓 button at the top and I'll open "
        "the leave form for you.",
        "To file a leave request, tap the 🗓 Apply-for-leave button up top — I'll "
        "pull up the form right away.",
        "Sure! Hit the 🗓 button in the top bar and I'll open the leave form for you "
        "to fill in.",
    ],
}


class LlmWorker(QThread):
    chunk_received = Signal(str)
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(
        self,
        question: str,
        sections: list[PolicySection],
        model: str,
        history: list[dict] | None = None,
        contacts: dict | None = None,
        max_tokens: int = 400,
        provider: str | None = None,
    ):
        super().__init__()
        self.question = question
        self.sections = sections
        self.model = model
        self.history = history or []
        self.contacts = contacts
        self.max_tokens = max_tokens
        self.provider = provider

    def run(self) -> None:
        try:
            for chunk in stream_answer(
                self.question,
                self.sections,
                self.model,
                self.history,
                self.contacts,
                self.max_tokens,
                self.provider,
            ):
                self.chunk_received.emit(chunk)
            self.finished_ok.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class LeaveWorker(QThread):
    """Runs the headless Playwright leave automation off the GUI thread.

    Playwright's sync API blocks for the whole browser session, so it MUST run
    here (a QThread), never directly in a slot, or the chat window would freeze.
    """

    finished_ok = Signal(dict)  # the apply_leave() result dict
    failed = Signal(str)

    def __init__(self, creds, leave_type, leave_date, reason, submit):
        super().__init__()
        self._creds = creds
        self._leave_type = leave_type
        self._leave_date = leave_date
        self._reason = reason
        self._submit = submit

    def run(self) -> None:
        try:
            result = apply_leave(
                self._creds,
                self._leave_type,
                self._leave_date,
                self._reason,
                submit=self._submit,
                headless=False,   # show the browser so the user can watch it work
                slow_mo=400,      # ease the pace to make each step followable
            )
            self.finished_ok.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class MessageBubble(QFrame):
    def __init__(self, text: str, is_user: bool):
        super().__init__()
        self.setObjectName("UserBubble" if is_user else "BotBubble")
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)
        self.setMaximumWidth(WIDGET_WIDTH - 60)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        self._raw = text
        self._label = QLabel(text)
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse
        )
        self._label.setOpenExternalLinks(True)
        self._label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        if not is_user:
            # Darker, more legible link color for the SharePoint citations.
            pal = self._label.palette()
            pal.setColor(QPalette.Link, QColor(LINK_COLOR))
            self._label.setPalette(pal)
        layout.addWidget(self._label)

        self._thinking_timer: QTimer | None = None
        self._thinking_phase = 0
        self._typing_movie: QMovie | None = None

    # --- "AI is thinking" animated indicator -------------------------------
    def start_thinking(self) -> None:
        # Prefer an animated typing GIF; fall back to the brand-green dots.
        gif_path = app_base_dir() / TYPING_GIF_FILENAME
        if gif_path.exists():
            movie = QMovie(str(gif_path))
            if movie.isValid():
                movie.setCacheMode(QMovie.CacheAll)
                movie.setScaledSize(QSize(64, 28))
                self._label.setTextFormat(Qt.PlainText)
                self._label.setText("")
                self._label.setMovie(movie)
                movie.start()
                self._typing_movie = movie
                return
        self._thinking_phase = 0
        self._render_thinking()
        timer = QTimer(self)
        timer.setInterval(320)
        timer.timeout.connect(self._tick_thinking)
        timer.start()
        self._thinking_timer = timer

    def _tick_thinking(self) -> None:
        self._thinking_phase += 1
        self._render_thinking()

    def _render_thinking(self) -> None:
        active = self._thinking_phase % 3
        dots = []
        for i in range(3):
            color = THINK_ACTIVE_COLOR if i == active else THINK_IDLE_COLOR
            dots.append(
                f'<span style="color:{color}; font-size:20px;">&#9679;</span>'
            )
        self._label.setTextFormat(Qt.RichText)
        self._label.setText("&nbsp;".join(dots))

    def _stop_thinking(self) -> None:
        if self._thinking_timer is not None:
            self._thinking_timer.stop()
            self._thinking_timer.deleteLater()
            self._thinking_timer = None
        if self._typing_movie is not None:
            self._typing_movie.stop()
            self._label.setMovie(None)  # detach so text renders again
            self._typing_movie.deleteLater()
            self._typing_movie = None

    # --- content ------------------------------------------------------------
    def set_text(self, text: str) -> None:
        self._stop_thinking()
        self._raw = text
        self._label.setTextFormat(Qt.PlainText)
        self._label.setText(text)

    def append_text(self, chunk: str) -> None:
        # First real chunk replaces the thinking animation.
        self._stop_thinking()
        # Render as plain text while streaming; partial Markdown looks broken.
        self._raw += chunk
        self._label.setTextFormat(Qt.PlainText)
        self._label.setText(self._raw)

    def finalize(self) -> None:
        # Once the full reply is in, render as HTML so [text](url) links work
        # and carry our own (darker) link color.
        self._stop_thinking()
        self._label.setTextFormat(Qt.RichText)
        self._label.setText(_markdown_to_html(self._raw))

    def text(self) -> str:
        return self._raw


class ConfirmationCard(QFrame):
    """An inline, editable card the user reviews before a leave is filed.

    Pre-filled from the LLM extraction; the user can correct any field. v1 only
    automates a single, full-day leave, so the multi-day / half-day options are
    shown (to match the real sumHR form) but disabled with a "coming soon" hint.
    """

    confirmed = Signal(dict)  # {"leave_type": str, "date": date, "reason": str}
    cancelled = Signal()

    def __init__(self, details: dict):
        super().__init__()
        self.setObjectName("ConfirmCard")
        self.setMaximumWidth(WIDGET_WIDTH - 40)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        heading = QLabel("Review your leave")
        heading.setObjectName("CardHeading")
        layout.addWidget(heading)

        # Leave type ---------------------------------------------------------
        layout.addWidget(self._field_label("Leave type"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(LEAVE_TYPES)
        if details.get("leave_type") in LEAVE_TYPES:
            self.type_combo.setCurrentText(details["leave_type"])
        layout.addWidget(self.type_combo)

        # Date ---------------------------------------------------------------
        layout.addWidget(self._field_label("Date"))
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setMinimumDate(QDate.currentDate())
        self.date_edit.setDate(self._initial_date(details.get("date", "")))
        layout.addWidget(self.date_edit)

        # Duration (single/full only in v1) ---------------------------------
        self.span_group = QButtonGroup(self)
        single = QRadioButton("Single day")
        single.setChecked(True)
        multi = QRadioButton("Multiple days")
        multi.setEnabled(False)
        multi.setToolTip("Coming soon")
        self.span_group.addButton(single)
        self.span_group.addButton(multi)
        layout.addLayout(self._radio_row(single, multi))

        self.half_group = QButtonGroup(self)
        full = QRadioButton("Full day")
        full.setChecked(True)
        first_half = QRadioButton("1st half")
        first_half.setEnabled(False)
        first_half.setToolTip("Coming soon")
        second_half = QRadioButton("2nd half")
        second_half.setEnabled(False)
        second_half.setToolTip("Coming soon")
        for btn in (full, first_half, second_half):
            self.half_group.addButton(btn)
        layout.addLayout(self._radio_row(full, first_half, second_half))

        # Reason -------------------------------------------------------------
        layout.addWidget(self._field_label("Reason"))
        self.reason_edit = QTextEdit()
        self.reason_edit.setObjectName("CardReason")
        self.reason_edit.setFixedHeight(48)
        self.reason_edit.setPlainText(details.get("reason", ""))
        layout.addWidget(self.reason_edit)

        # Inline validation hint (hidden until needed) -----------------------
        self.hint = QLabel("")
        self.hint.setObjectName("CardHint")
        self.hint.setWordWrap(True)
        self.hint.hide()
        layout.addWidget(self.hint)

        # Buttons ------------------------------------------------------------
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("CardCancel")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.clicked.connect(self.cancelled.emit)
        buttons.addWidget(self.cancel_btn)
        self.confirm_btn = QPushButton("Confirm & Apply")
        self.confirm_btn.setObjectName("CardConfirm")
        self.confirm_btn.setCursor(Qt.PointingHandCursor)
        self.confirm_btn.clicked.connect(self._on_confirm)
        buttons.addWidget(self.confirm_btn)
        layout.addLayout(buttons)

    # --- helpers -----------------------------------------------------------
    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("CardField")
        return label

    @staticmethod
    def _radio_row(*buttons: QRadioButton) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        for btn in buttons:
            row.addWidget(btn)
        row.addStretch()
        return row

    @staticmethod
    def _initial_date(iso: str) -> QDate:
        if iso:
            parsed = QDate.fromString(iso, "yyyy-MM-dd")
            if parsed.isValid() and parsed >= QDate.currentDate():
                return parsed
        return QDate.currentDate()

    def _on_confirm(self) -> None:
        reason = self.reason_edit.toPlainText().strip()
        if not reason:
            self.hint.setText("Please add a reason for your leave.")
            self.hint.show()
            return
        # Lock the card so a leave can't be submitted twice.
        self.confirm_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.confirmed.emit(
            {
                "leave_type": self.type_combo.currentText(),
                "date": self.date_edit.date().toPython(),  # datetime.date
                "reason": reason,
            }
        )


class ChatWidget(QWidget):
    opened = Signal()  # emitted whenever the chat becomes visible
    closed = Signal()  # emitted whenever the chat is hidden/closed

    def __init__(self, policies_dir: Path, provider: str = DEFAULT_PROVIDER):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(WIDGET_WIDTH, WIDGET_HEIGHT)
        self.setMinimumSize(WIDGET_WIDTH, WIDGET_HEIGHT)  # never collapse on DPI change
        self.setWindowTitle("Qubi - Policy Assistant")

        # Active LLM provider ("azure"/"ollama") and its model name. Switchable
        # live from the header; the model tracks the provider's configured model.
        self.provider = provider
        self.model = model_for(provider)
        self.policies_dir = policies_dir
        self.sections: list[PolicySection] = load_policies(policies_dir)
        # Real HR contacts (from the handbook frontmatter) so Qubi can hand off
        # to a clickable email when an answer isn't in the docs.
        self._contacts: dict = load_contacts(policies_dir)

        self._drag_offset: QPoint | None = None
        self._worker: LlmWorker | None = None
        self._current_bot_bubble: MessageBubble | None = None
        # Prior policy turns ({"role", "content"} dicts) sent with each new
        # question so follow-ups like "make it shorter" keep their context.
        self._history: list[dict] = []
        self._pending_user_msg: str | None = None  # current turn, added on success
        self._pending_has_context = False  # did this turn ground on a policy?
        self._last_canned: str | None = None  # avoid repeating the same small-talk
        # Sections from the last real policy question, reused for vague
        # follow-ups ("make it short") so they stay on the same topic.
        self._last_sections: list[PolicySection] = []
        # Clickable suggestion "chips" (starter prompts + post-answer follow-ups);
        # tracked so we can remove the stale row when a new message is sent.
        self._starter_chips: QWidget | None = None
        self._followup_chips: QWidget | None = None
        self._leave_worker: LeaveWorker | None = None
        self._active_card: ConfirmationCard | None = None
        self._pending_leave_bubble: MessageBubble | None = None
        self._screen_signal_connected = False

        self._build_ui()
        self._show_welcome()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        root = QFrame()
        root.setObjectName("Root")
        outer.addWidget(root)

        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        header = QFrame()
        header.setObjectName("Header")
        header.setFixedHeight(46)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(14, 0, 8, 0)
        title = QLabel("Qubi - Policy Assistant")
        title.setObjectName("Title")
        hl.addWidget(title)
        hl.addStretch()
        # Live LLM provider switch (Azure cloud ↔ local Ollama).
        self.model_btn = QPushButton()
        self.model_btn.setObjectName("ModelBtn")
        self.model_btn.setFixedHeight(24)
        self.model_btn.setCursor(Qt.PointingHandCursor)
        self.model_btn.clicked.connect(self._cycle_provider)
        self._refresh_model_btn()
        hl.addWidget(self.model_btn)
        self.leave_btn = QPushButton("🗓")
        self.leave_btn.setObjectName("HeaderBtn")
        self.leave_btn.setFixedSize(28, 28)
        self.leave_btn.setCursor(Qt.PointingHandCursor)
        self.leave_btn.setToolTip("Apply for leave")
        self.leave_btn.clicked.connect(self._open_leave_form)
        hl.addWidget(self.leave_btn)
        self.clear_btn = QPushButton("⟲")
        self.clear_btn.setObjectName("HeaderBtn")
        self.clear_btn.setFixedSize(28, 28)
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.setToolTip("Clear conversation")
        self.clear_btn.clicked.connect(self._clear_chat)
        hl.addWidget(self.clear_btn)
        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("HeaderBtn")
        self.close_btn.setFixedSize(28, 28)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self.hide)
        hl.addWidget(self.close_btn)
        header.installEventFilter(self)
        v.addWidget(header)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("Scroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget()
        container.setObjectName("ScrollInner")
        self.messages_layout = QVBoxLayout(container)
        self.messages_layout.setContentsMargins(12, 12, 12, 12)
        self.messages_layout.setSpacing(8)
        self.messages_layout.addStretch()
        self.scroll.setWidget(container)
        v.addWidget(self.scroll, 1)

        # Keep the newest message in view. A single deferred scroll undershoots
        # because tall rows (a growing answer, or the leave card's combo/date/
        # text-edit children) settle their height a few ticks *after* insertion.
        # So we also re-pin whenever the scroll range grows — unless the user has
        # scrolled up to read history, in which case we leave them where they are.
        self._auto_scroll = True
        bar = self.scroll.verticalScrollBar()
        bar.rangeChanged.connect(self._on_scroll_range_changed)
        bar.valueChanged.connect(self._on_scroll_value_changed)

        input_bar = QFrame()
        input_bar.setObjectName("InputBar")
        ib = QHBoxLayout(input_bar)
        ib.setContentsMargins(10, 10, 10, 12)
        ib.setSpacing(8)
        self.input = QTextEdit()
        self.input.setObjectName("Input")
        self.input.setPlaceholderText("Ask about leave, WFH, conduct...")
        self.input.setFixedHeight(60)
        self.input.installEventFilter(self)
        ib.addWidget(self.input, 1)
        self.send_btn = QPushButton("Send")
        self.send_btn.setObjectName("SendBtn")
        self.send_btn.setFixedHeight(60)
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.clicked.connect(self._on_send)
        ib.addWidget(self.send_btn)
        v.addWidget(input_bar)

        self.setStyleSheet(self._stylesheet())

    def _stylesheet(self) -> str:
        return """
        #Root {
            background-color: #ffffff;
            border-radius: 14px;
            border: 1px solid #cfeee6;
        }
        #Header {
            background-color: qlineargradient(
                x1:0, y1:0, x2:1, y2:1,
                stop:0 #00c79a, stop:1 #00a386);
            border-top-left-radius: 14px;
            border-top-right-radius: 14px;
        }
        #Title {
            color: white;
            font-size: 14px;
            font-weight: 600;
        }
        #HeaderBtn {
            background: transparent;
            color: white;
            border: none;
            font-size: 18px;
            font-weight: bold;
        }
        #HeaderBtn:hover {
            background-color: rgba(255, 255, 255, 60);
            border-radius: 6px;
        }
        #ModelBtn {
            background-color: rgba(255, 255, 255, 45);
            color: white;
            border: none;
            border-radius: 12px;
            padding: 0 10px;
            font-size: 11px;
            font-weight: bold;
        }
        #ModelBtn:hover {
            background-color: rgba(255, 255, 255, 80);
        }
        QScrollArea#Scroll {
            background-color: #f2faf7;
            border: none;
        }
        QWidget#ScrollInner {
            background-color: #f2faf7;
        }
        #UserBubble {
            background-color: #00a386;
            border-radius: 12px;
        }
        #UserBubble QLabel {
            color: white;
            font-size: 13px;
        }
        #BotBubble {
            background-color: #ffffff;
            border-radius: 12px;
            border: 1px solid #d6ebe4;
        }
        #BotBubble QLabel {
            color: #16312b;
            font-size: 13px;
        }
        #InputBar {
            background-color: white;
            border-top: 1px solid #d6ebe4;
            border-bottom-left-radius: 14px;
            border-bottom-right-radius: 14px;
        }
        #Input {
            border: 1px solid #bfe3d8;
            border-radius: 10px;
            padding: 8px;
            font-size: 13px;
            background: #f5fcfa;
            color: #16312b;
        }
        #Input:focus { border: 1px solid #00c79a; }
        #SendBtn {
            background-color: #00a386;
            color: white;
            border: none;
            border-radius: 10px;
            padding: 0 16px;
            font-weight: 600;
        }
        #SendBtn:hover { background-color: #00876f; }
        #SendBtn:disabled { background-color: #9bd6c8; }
        QScrollBar:vertical {
            background: transparent;
            width: 8px;
            margin: 4px;
        }
        QScrollBar::handle:vertical {
            background: #b6d8cf;
            border-radius: 4px;
            min-height: 20px;
        }
        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical { height: 0px; }
        #ConfirmCard {
            background-color: #ffffff;
            border-radius: 12px;
            border: 1px solid #d6ebe4;
        }
        #CardHeading {
            color: #00876f;
            font-size: 13px;
            font-weight: 600;
        }
        #CardField {
            color: #5a7068;
            font-size: 11px;
            font-weight: 600;
        }
        #ConfirmCard QComboBox,
        #ConfirmCard QDateEdit,
        #ConfirmCard QTextEdit {
            border: 1px solid #bfe3d8;
            border-radius: 8px;
            padding: 5px;
            background: #f5fcfa;
            color: #16312b;
            font-size: 13px;
        }
        #ConfirmCard QComboBox::drop-down {
            border: none;
            width: 22px;
        }
        #ConfirmCard QComboBox QAbstractItemView {
            border: 1px solid #bfe3d8;
            background: #ffffff;
            color: #16312b;
            selection-background-color: #00a386;
            selection-color: #ffffff;
            outline: none;
        }
        #ConfirmCard QComboBox QAbstractItemView::item {
            min-height: 24px;
            padding: 4px 8px;
            color: #16312b;
        }
        #ConfirmCard QRadioButton {
            color: #16312b;
            font-size: 12px;
        }
        #ConfirmCard QRadioButton:disabled { color: #9bb4ac; }
        #CardHint { color: #c0392b; font-size: 11px; }
        #CardConfirm {
            background-color: #00a386;
            color: white;
            border: none;
            border-radius: 8px;
            padding: 6px 14px;
            font-weight: 600;
        }
        #CardConfirm:hover { background-color: #00876f; }
        #CardConfirm:disabled { background-color: #9bd6c8; }
        #CardCancel {
            background: transparent;
            color: #00806a;
            border: 1px solid #bfe3d8;
            border-radius: 8px;
            padding: 6px 14px;
        }
        #CardCancel:hover { background-color: #eef8f5; }
        #CardCancel:disabled { color: #9bb4ac; }
        #Chip {
            background-color: #ffffff;
            color: #00806a;
            border: 1px solid #9bd6c8;
            border-radius: 13px;
            padding: 5px 12px;
            font-size: 12px;
            font-weight: 600;
        }
        #Chip:hover {
            background-color: #eef8f5;
            border: 1px solid #00c79a;
        }
        #WelcomeTitle {
            color: #16312b;
            font-size: 17px;
            font-weight: 700;
        }
        #WelcomeSubtitle {
            color: #5a7068;
            font-size: 12px;
        }
        """

    def show_above(self, launcher_rect: QRect, gap: int = 12) -> None:
        screen = QGuiApplication.primaryScreen().availableGeometry()
        x = launcher_rect.right() - self.width()
        y = launcher_rect.top() - self.height() - gap
        x = max(screen.left() + 8, min(x, screen.right() - self.width() - 8))
        y = max(screen.top() + 8, y)
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # windowHandle() is only valid after the platform window exists (first
        # show); connect the per-monitor signal once for the widget's lifetime.
        handle = self.windowHandle()
        if handle is not None and not self._screen_signal_connected:
            handle.screenChanged.connect(self._on_screen_changed)
            self._screen_signal_connected = True
        self.opened.emit()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.closed.emit()

    def _clamp_to_current_screen(self) -> None:
        """Keep the whole window on whatever screen it now sits on."""
        handle = self.windowHandle()
        screen = handle.screen() if handle is not None else QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        x = max(avail.left() + 8, min(self.x(), avail.right() - self.width() - 8))
        y = max(avail.top() + 8, min(self.y(), avail.bottom() - self.height() - 8))
        self.move(x, y)

    def _on_screen_changed(self, _screen) -> None:
        # Crossing monitors with different DPI can shrink a frameless Tool
        # window; restore the intended logical size and keep it on-screen.
        self.resize(WIDGET_WIDTH, WIDGET_HEIGHT)
        self._clamp_to_current_screen()

    def eventFilter(self, obj, event) -> bool:
        if obj.objectName() == "Header":
            if event.type() == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton:
                # Manual escape hatch: snap back to the normal size/position.
                self.resize(WIDGET_WIDTH, WIDGET_HEIGHT)
                self._clamp_to_current_screen()
                return True
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._drag_offset = (
                    event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                )
                return True
            if event.type() == QEvent.MouseMove and self._drag_offset is not None:
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                return True
            if event.type() == QEvent.MouseButtonRelease:
                self._drag_offset = None
                return True
        input_widget = getattr(self, "input", None)
        if input_widget is not None and obj is input_widget and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (
                event.modifiers() & Qt.ShiftModifier
            ):
                self._on_send()
                return True
        return super().eventFilter(obj, event)

    def _add_user_message(self, text: str) -> None:
        bubble = MessageBubble(text, is_user=True)
        self._insert_row(bubble, align_right=True)

    def _add_bot_message(self, text: str) -> MessageBubble:
        bubble = MessageBubble(text, is_user=False)
        self._insert_row(bubble, align_right=False)
        return bubble

    def _circular_avatar(self, size: int) -> QPixmap | None:
        """Return the Qubi mascot scaled and clipped into a circle, or None if
        the logo asset is missing. Same clip technique as the launcher button."""
        logo = QPixmap(str(app_base_dir() / LOGO_FILENAME))
        if logo.isNull():
            return None
        canvas = QPixmap(size, size)
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addEllipse(QRectF(0, 0, size, size))
        painter.setClipPath(clip)
        painter.fillRect(0, 0, size, size, QColor("white"))
        scaled = logo.scaled(
            size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        painter.drawPixmap(
            int((size - scaled.width()) / 2),
            int((size - scaled.height()) / 2),
            scaled,
        )
        painter.end()
        return canvas

    def _build_welcome_hero(self) -> QWidget:
        """Centered branded intro: mascot avatar + short greeting + subtitle."""
        hero = QWidget()
        col = QVBoxLayout(hero)
        col.setContentsMargins(0, 10, 0, 4)
        col.setSpacing(6)

        avatar = self._circular_avatar(72)
        if avatar is not None:
            avatar_label = QLabel()
            avatar_label.setPixmap(avatar)
            avatar_label.setAlignment(Qt.AlignHCenter)
            col.addWidget(avatar_label, 0, Qt.AlignHCenter)

        title = QLabel("Hi, I'm Qubi 👋")
        title.setObjectName("WelcomeTitle")
        title.setAlignment(Qt.AlignHCenter)
        col.addWidget(title)

        subtitle = QLabel(
            "Your Qubiqon HR assistant.\nAsk me anything, or tap a topic below."
        )
        subtitle.setObjectName("WelcomeSubtitle")
        subtitle.setAlignment(Qt.AlignHCenter)
        subtitle.setWordWrap(True)
        col.addWidget(subtitle)
        return hero

    def _chip_row(
        self, chips: list[tuple[str, str]], followup: bool = False
    ) -> QWidget:
        """Build a wrapping row of clickable suggestion chips. Each chip is a
        (label, question) pair; clicking sends the question like a typed message.
        `followup=True` marks the chips as same-topic follow-ups (so they reuse
        the current policy's context); starter chips are fresh questions."""
        wrapper = QWidget()
        flow = FlowLayout(wrapper, spacing=6)
        for label, question in chips:
            btn = QPushButton(label)
            btn.setObjectName("Chip")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            btn.clicked.connect(
                lambda _=False, q=question, f=followup: self._submit(q, followup=f)
            )
            flow.addWidget(btn)
        return wrapper

    def _show_welcome(self) -> None:
        if not self.sections:
            self._add_bot_message(
                "No policy documents were found in the policies/ folder. "
                "Add .md files there and restart the app."
            )
            return
        # Full-width centered hero (the hero centers its own contents), then the
        # starter chips below it.
        self._insert_full_width(self._build_welcome_hero())
        self._starter_chips = self._chip_row(_STARTER_CHIPS)
        self._insert_row(self._starter_chips, align_right=False)

    def _remove_chip_rows(self) -> None:
        """Drop any live suggestion chips before the next turn so stale prompts
        don't linger in the conversation."""
        for attr in ("_starter_chips", "_followup_chips"):
            chips = getattr(self, attr)
            if chips is not None:
                wrapper = chips.parent()  # the row wrapper added by _insert_row
                (wrapper or chips).deleteLater()
                setattr(self, attr, None)

    def _refresh_model_btn(self) -> None:
        """Sync the header switch label/tooltip to the active provider."""
        info = PROVIDERS[self.provider]
        self.model_btn.setText(info["label"])
        self.model_btn.setToolTip(
            f"Model: {info['model']} ({self.provider}) — click to switch"
        )

    def _cycle_provider(self) -> None:
        # Don't switch mid-answer; the in-flight worker keeps its own provider.
        if self._busy():
            return
        keys = list(PROVIDERS)
        self.provider = keys[(keys.index(self.provider) + 1) % len(keys)]
        self.model = model_for(self.provider)
        self._refresh_model_btn()
        info = PROVIDERS[self.provider]
        self._add_bot_message(
            f"Now answering with **{info['label']}** (`{info['model']}`)."
        ).finalize()

    def _clear_chat(self) -> None:
        # Ignore while a reply or leave automation is in flight, to avoid
        # deleting a live bubble or an open confirmation card.
        if self._busy():
            return
        self._current_bot_bubble = None
        self._active_card = None
        self._history = []
        self._pending_user_msg = None
        self._last_sections = []
        self._starter_chips = None
        self._followup_chips = None
        # Remove every row except the trailing stretch (last item, no widget).
        while self.messages_layout.count() > 1:
            item = self.messages_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._show_welcome()

    def _insert_full_width(self, widget: QWidget) -> None:
        """Insert a widget that spans the full content width (it handles its own
        alignment), e.g. the centered welcome hero."""
        idx = self.messages_layout.count() - 1
        self.messages_layout.insertWidget(idx, widget)
        self._animate_entrance(widget)
        self._scroll_to_bottom()

    def _insert_row(self, bubble: QWidget, align_right: bool) -> None:
        wrapper = QWidget()
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        if align_right:
            row.addStretch()
            row.addWidget(bubble, 0, Qt.AlignRight)
        else:
            row.addWidget(bubble, 0, Qt.AlignLeft)
            row.addStretch()
        idx = self.messages_layout.count() - 1
        self.messages_layout.insertWidget(idx, wrapper)
        self._animate_entrance(wrapper)
        self._scroll_to_bottom()

    def _animate_entrance(self, wrapper: QWidget) -> None:
        # Subtle fade-in for each new row. The effect + animation are parented
        # to the wrapper so they aren't garbage-collected mid-animation and are
        # freed when the row is deleted (e.g. on clear).
        effect = QGraphicsOpacityEffect(wrapper)
        wrapper.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", wrapper)
        anim.setDuration(220)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        # Drop the effect once done so it can't interfere with link/text clicks.
        anim.finished.connect(lambda: wrapper.setGraphicsEffect(None))
        anim.start()

    def _scroll_to_bottom(self) -> None:
        # A new/grown row means "follow along": re-arm auto-scroll, then pin on
        # the next tick. The rangeChanged handler re-pins as later layout passes
        # finish growing the row, so tall content (the leave card) isn't missed.
        self._auto_scroll = True
        QTimer.singleShot(0, self._pin_to_bottom)

    def _pin_to_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_scroll_range_changed(self, _minimum: int, _maximum: int) -> None:
        # Fires every time content height changes. While following the latest
        # message, keep the view pinned to the bottom as the range grows.
        if self._auto_scroll:
            self._pin_to_bottom()

    def _on_scroll_value_changed(self, value: int) -> None:
        # Track whether the user is parked at the bottom. Scrolling up pauses
        # auto-scroll (so they can read history); returning to the bottom resumes
        # it. Programmatic pins land at maximum, so they keep auto-scroll on.
        bar = self.scroll.verticalScrollBar()
        self._auto_scroll = value >= bar.maximum() - 4

    def _busy(self) -> bool:
        """True while a chat stream or a leave automation is in flight."""
        if self._worker is not None and self._worker.isRunning():
            return True
        if self._leave_worker is not None and self._leave_worker.isRunning():
            return True
        return False

    # Below this score a query looks like a vague follow-up ("make it short")
    # rather than a real new question; genuine questions score ~6+ (see tests).
    _FOLLOWUP_SCORE_FLOOR = 4.0

    def _sections_for(
        self, text: str, followup: bool = False
    ) -> tuple[list[PolicySection], bool]:
        """Pick the policy context for this turn, plus a `confident` flag that
        says whether we're sure of the topic (used to decide if follow-up chips
        should be shown).

        - Strong keyword match → use & remember those sections (confident).
        - Weak match that's a follow-up (a follow-up chip, or "make it shorter")
          → reuse the previous topic's sections (confident).
        - Weak match otherwise (e.g. an off-topic question) → answer with its own
          best (often empty) sections but NOT confident, so no chips persist and
          the remembered topic is left untouched for a later real follow-up.
        """
        scored = retrieve_scored(text, self.sections)
        top = scored[0][1] if scored else 0.0
        if top >= self._FOLLOWUP_SCORE_FLOOR:
            sections = [s for s, _ in scored]
            self._last_sections = sections
            return sections, True
        if self._last_sections and (followup or _is_followup(text)):
            return self._last_sections, True
        return [s for s, _ in scored], False

    def _canned_reply(self, intent: str) -> str:
        """Pick a small-talk reply at random, avoiding the one we just used."""
        pool = _CANNED_REPLIES[intent]
        choices = [r for r in pool if r != self._last_canned] or pool
        reply = random.choice(choices)
        self._last_canned = reply
        return reply

    def _on_send(self) -> None:
        text = self.input.toPlainText().strip()
        self.input.clear()
        self._submit(text)

    def _submit(self, text: str, followup: bool = False) -> None:
        """Send a message — shared by the input box, Enter, and suggestion chips.
        `followup=True` (set by follow-up chips) marks it as a same-topic
        follow-up so it reuses the current policy's context."""
        text = text.strip()
        if not text or self._busy():
            return

        # Any pending suggestion chips belong to the previous turn — clear them.
        self._remove_chip_rows()
        self._add_user_message(text)

        intent = classify_intent(text)
        # Filing leave is button-only now; a typed leave request just nudges the
        # user to the header 🗓 button (handled via the "apply_leave" reply pool).
        if intent in _CANNED_REPLIES:
            self._add_bot_message(self._canned_reply(intent))
            return

        sections, confident = self._sections_for(text, followup)
        self._current_bot_bubble = self._add_bot_message("")
        self._current_bot_bubble.start_thinking()

        # Remember this turn's full user message so we can append it to history
        # once (and only if) the answer succeeds.
        self._pending_user_msg = build_user_message(text, sections)
        # Only offer follow-up chips when we're confident of the topic, so an
        # off-topic / "couldn't find" reply doesn't inherit the last policy's chips.
        self._pending_has_context = confident

        # A "make it shorter" follow-up gets a hard token cap so the reply can't
        # come back longer than the answer it's meant to condense.
        max_tokens = _CONDENSE_MAX_TOKENS if _is_condense_request(text) else 400

        self.send_btn.setEnabled(False)
        self._worker = LlmWorker(
            text, sections, self.model, list(self._history), self._contacts,
            max_tokens, self.provider,
        )
        self._worker.chunk_received.connect(self._on_chunk)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_chunk(self, chunk: str) -> None:
        if self._current_bot_bubble is not None:
            self._current_bot_bubble.append_text(chunk)
            self._scroll_to_bottom()

    def _on_done(self) -> None:
        answer = ""
        if self._current_bot_bubble is not None:
            answer = self._current_bot_bubble.text().strip()
            if not answer:
                self._current_bot_bubble.set_text("(no response)")
            else:
                self._current_bot_bubble.finalize()
            self._scroll_to_bottom()
        # Record the completed turn so the next question carries this context.
        if answer and self._pending_user_msg is not None:
            self._history.append({"role": "user", "content": self._pending_user_msg})
            self._history.append({"role": "assistant", "content": answer})
            self._trim_history()
        # Offer quick follow-ups on a grounded answer.
        if answer and self._pending_has_context:
            chips = self._followup_chips_for(self._last_sections)
            self._followup_chips = self._chip_row(chips, followup=True)
            self._insert_row(self._followup_chips, align_right=False)
        self._pending_user_msg = None
        self._pending_has_context = False
        self.send_btn.setEnabled(True)
        self._current_bot_bubble = None

    def _trim_history(self, max_turns: int = 4) -> None:
        """Keep only the last few Q&A pairs so requests stay small."""
        if len(self._history) > max_turns * 2:
            self._history = self._history[-max_turns * 2:]

    def _followup_chips_for(
        self, sections: list[PolicySection]
    ) -> list[tuple[str, str]]:
        """Context-aware follow-ups: suggest the *sibling* sub-topics of the
        policy just answered (e.g. after Travel Policy → 'Booking Rules',
        'Reimbursements'). Falls back to the generic chips when the policy has
        no other subsections (e.g. single-section policies)."""
        if not sections:
            return _FOLLOWUP_CHIPS
        # Anchor on the top (best-scored) section's policy; skip only that exact
        # subsection so the chips drill into *other* parts of the same policy.
        top = sections[0]
        parent = top.parent_heading or top.heading
        topics: list[tuple[str, str]] = []
        seen: set[str] = set()
        for s in self.sections:
            if s.parent_heading != parent or s.heading == top.heading:
                continue
            label = _topic_label(s.heading)
            key = label.lower()
            if not label or key in seen:
                continue
            seen.add(key)
            topics.append((label, f"Tell me about {label}"))
            if len(topics) == 3:
                break
        if not topics:
            return _FOLLOWUP_CHIPS
        # Keep one universal action plus the topic suggestions.
        return [("Make it shorter", "Make it shorter")] + topics

    def _on_failed(self, err: str) -> None:
        if self._current_bot_bubble is not None:
            self._current_bot_bubble.set_text(
                "Sorry, something went wrong on my end. Please try again in a moment."
            )
            # Keep the technical detail handy for debugging without showing it.
            self._current_bot_bubble.setToolTip(err)
        self._pending_user_msg = None
        self._pending_has_context = False
        self.send_btn.setEnabled(True)
        self._current_bot_bubble = None

    # --- Leave application -------------------------------------------------
    def _open_leave_form(self) -> None:
        """Open the leave form from the header button (the only entry point)."""
        # Don't stack a second card or interrupt a reply/leave run in flight.
        if self._busy() or self._active_card is not None:
            self._scroll_to_bottom()
            return
        # Button-initiated: open a blank form (filing leave is button-only).
        self._add_bot_message("Fill in the details below and hit Confirm to apply.")
        card = ConfirmationCard({"leave_type": "", "date": "", "reason": ""})
        card.confirmed.connect(self._on_leave_confirmed)
        card.cancelled.connect(self._on_leave_cancelled)
        self._active_card = card
        self._insert_row(card, align_right=False)

    def _on_leave_cancelled(self) -> None:
        self._add_bot_message("No problem — I've cancelled that leave application.")
        self._active_card = None

    def _on_leave_confirmed(self, data: dict) -> None:
        creds = self._ensure_credentials()
        if creds is None:
            self._add_bot_message(
                "I need your sumHR login to apply leave. Try again when you're ready."
            )
            self._active_card = None
            return

        bubble = self._add_bot_message("")
        bubble.start_thinking()
        self._pending_leave_bubble = bubble
        self.send_btn.setEnabled(False)

        self._leave_worker = LeaveWorker(
            creds,
            data["leave_type"],
            data["date"],
            data["reason"],
            submit=True,
        )
        self._leave_worker.finished_ok.connect(self._on_leave_done)
        self._leave_worker.failed.connect(self._on_leave_failed)
        self._leave_worker.start()

    def _on_leave_done(self, result: dict) -> None:
        if result.get("ok"):
            msg = "✅ " + result.get("message", "Leave applied.")
        else:
            msg = "⚠️ Couldn't apply leave: " + result.get("error", "unknown error")
        if self._pending_leave_bubble is not None:
            self._pending_leave_bubble.set_text(msg)
        self.send_btn.setEnabled(True)
        self._pending_leave_bubble = None
        self._active_card = None

    def _on_leave_failed(self, err: str) -> None:
        if self._pending_leave_bubble is not None:
            self._pending_leave_bubble.set_text(f"⚠️ Leave automation error: {err}")
        self.send_btn.setEnabled(True)
        self._pending_leave_bubble = None
        self._active_card = None

    def _ensure_credentials(self) -> tuple[str, str] | None:
        """Return stored sumHR creds, prompting (and saving) on first use."""
        creds = get_credentials()
        if creds is not None:
            return creds
        email, ok = QInputDialog.getText(self, "sumHR login", "sumHR email:")
        if not ok or not email.strip():
            return None
        password, ok = QInputDialog.getText(
            self, "sumHR login", "sumHR password:", QLineEdit.Password
        )
        if not ok or not password:
            return None
        email = email.strip()
        set_credentials(email, password)
        return email, password


def _sparkle_path(cx: float, cy: float, outer: float, inner: float) -> QPainterPath:
    """A 4-pointed 'AI sparkle' star centered at (cx, cy)."""
    path = QPainterPath()
    for k in range(8):
        angle = math.pi / 2 - k * (math.pi / 4)
        radius = outer if k % 2 == 0 else inner
        x = cx + radius * math.cos(angle)
        y = cy - radius * math.sin(angle)
        if k == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    path.closeSubpath()
    return path


class ChatLauncher(QWidget):
    clicked = Signal()
    context_menu_requested = Signal(QPoint)

    SIZE = 60

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Qubi - Policy Assistant")
        self._hover = False
        self._pressed = False
        self._drag_origin: QPoint | None = None
        self._dragging = False
        self._logo = QPixmap(str(app_base_dir() / LOGO_FILENAME))

        # Animated mascot (optional): play a GIF frame-by-frame into the circle.
        self._movie_frame: QPixmap | None = None
        self._movie: QMovie | None = None
        gif_path = app_base_dir() / LAUNCHER_GIF_FILENAME
        if gif_path.exists():
            movie = QMovie(str(gif_path))
            if movie.isValid():  # guard: invalid movie => blank frames
                movie.setCacheMode(QMovie.CacheAll)
                movie.frameChanged.connect(self._on_movie_frame)
                self._movie = movie
                movie.start()

        # Hover glow (no asset): a breathing ring that animates only on hover.
        self._glow = 0.0
        self._glow_anim = QVariantAnimation(self)  # stored ref => not GC'd
        self._glow_anim.setStartValue(0.0)
        self._glow_anim.setEndValue(1.0)
        self._glow_anim.setDuration(1100)
        self._glow_anim.setLoopCount(-1)
        self._glow_anim.valueChanged.connect(self._on_glow)

        self._anchor_bottom_right()

    def _on_movie_frame(self, _frame: int) -> None:
        if self._movie is not None:
            self._movie_frame = self._movie.currentPixmap()
            self.update()

    def _on_glow(self, value) -> None:
        # Triangle wave (0->1->0) for a smooth breathing effect.
        self._glow = 1.0 - abs(2.0 * float(value) - 1.0)
        self.update()

    def _anchor_bottom_right(self) -> None:
        screen = QGuiApplication.primaryScreen().availableGeometry()
        margin = 24
        x = screen.right() - self.width() - margin
        y = screen.bottom() - self.height() - margin
        self.move(x, y)

    def enterEvent(self, event) -> None:
        self._hover = True
        self._glow_anim.start()  # animate only while hovered => near-zero idle CPU
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = False
        self._glow_anim.stop()
        self._glow = 0.0
        self.update()

    def showEvent(self, event) -> None:
        if self._movie is not None:
            self._movie.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        if self._movie is not None:
            self._movie.stop()
        super().hideEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton:
            self.context_menu_requested.emit(event.globalPosition().toPoint())
            return
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._dragging = False
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_origin is None:
            return
        delta = event.globalPosition().toPoint() - (self._drag_origin + self.frameGeometry().topLeft())
        if not self._dragging and (abs(delta.x()) + abs(delta.y()) > 6):
            self._dragging = True
        if self._dragging:
            self.move(event.globalPosition().toPoint() - self._drag_origin)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        was_pressed = self._pressed
        was_drag = self._dragging
        self._pressed = False
        self._dragging = False
        self._drag_origin = None
        self.update()
        if was_pressed and not was_drag and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        d = self.SIZE - 8  # circle diameter
        circle = QRectF(2, 2, d, d)

        # Soft drop shadow
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0, 55)))
        painter.drawEllipse(4, 6, d, d)

        # Prefer the current animated GIF frame; fall back to the static logo.
        source = self._movie_frame if self._movie_frame is not None else self._logo
        if source is not None and not source.isNull():
            # Mascot clipped into the circle on a white background.
            painter.save()
            clip = QPainterPath()
            clip.addEllipse(circle)
            painter.setClipPath(clip)
            painter.fillRect(circle, QColor("white"))
            # Fit the whole mascot inside the circle (antenna + tail visible).
            inner = int(d * 0.88)
            scaled = source.scaled(
                inner, inner, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            painter.drawPixmap(
                int(2 + (d - scaled.width()) / 2),
                int(2 + (d - scaled.height()) / 2),
                scaled,
            )
            painter.restore()
            # Hover ring / press dim for feedback (ring breathes via self._glow).
            if self._pressed:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(0, 0, 0, 35))
                painter.drawEllipse(circle)
            elif self._hover:
                ring = QColor(RING_COLOR)
                ring.setAlpha(int(120 + 135 * self._glow))
                pen = QPen(ring)
                pen.setWidthF(1.5 + 1.5 * self._glow)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(circle.adjusted(1, 1, -1, -1))
            return

        # Fallback (logo missing): brand-green gradient circle + sparkle glyph
        if self._pressed:
            top, bottom = QColor("#00b88f"), QColor("#007a63")
        elif self._hover:
            top, bottom = QColor("#00e0a8"), QColor("#009a7c")
        else:
            top, bottom = QColor(BRAND_GREEN), QColor(BRAND_GREEN_DEEP)
        grad = QLinearGradient(2, 2, d, d)
        grad.setColorAt(0.0, top)
        grad.setColorAt(1.0, bottom)
        painter.setBrush(QBrush(grad))
        painter.drawEllipse(2, 2, d, d)
        cx = 2 + d / 2
        cy = 2 + d / 2
        main = _sparkle_path(cx + 1, cy + 2, d * 0.30, d * 0.105)
        painter.fillPath(main, QBrush(QColor("white")))
        accent = _sparkle_path(cx + d * 0.22, cy - d * 0.20, d * 0.12, d * 0.042)
        painter.fillPath(accent, QBrush(QColor(255, 255, 255, 230)))
