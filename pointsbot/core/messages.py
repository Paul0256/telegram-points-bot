"""The adapter contract: what an adapter sends to the core and what it gets back.

    adapter                              core
    ───────                              ────
    IncomingCommand / IncomingAction ──► CommandProcessor ──► Response
                                                              (rich text + buttons)

Replies are *rich text*: a sequence of spans with a semantic style (bold,
code, ...). The core never produces HTML or Markdown, so user-provided text
can't break formatting, and each adapter renders spans the way its platform
wants (Telegram HTML, Discord Markdown, Slack mrkdwn, plain text, ...).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .models import Identity

# --------------------------------------------------------------------------- rich text


class Style(str, Enum):
    PLAIN = "plain"
    BOLD = "bold"
    ITALIC = "italic"
    CODE = "code"  # inline monospace, e.g. IDs
    PRE = "pre"  # monospace block, e.g. tables


@dataclass(frozen=True)
class Span:
    text: str
    style: Style = Style.PLAIN


RichText = tuple[Span, ...]


def bold(text: object) -> Span:
    return Span(str(text), Style.BOLD)


def italic(text: object) -> Span:
    return Span(str(text), Style.ITALIC)


def code(text: object) -> Span:
    return Span(str(text), Style.CODE)


def pre(text: object) -> Span:
    return Span(str(text), Style.PRE)


def rich(*parts: str | Span | RichText) -> RichText:
    """Build rich text: plain strings, spans and other rich texts, in order."""
    spans: list[Span] = []
    for part in parts:
        if isinstance(part, Span):
            spans.append(part)
        elif isinstance(part, tuple):
            spans.extend(part)
        else:
            spans.append(Span(part))
    return tuple(spans)


def join_lines(lines: list[RichText]) -> RichText:
    out: list[Span] = []
    for i, line in enumerate(lines):
        if i:
            out.append(Span("\n"))
        out.extend(line)
    return tuple(out)


def render_plain(text: RichText) -> str:
    """Formatting-free rendering: console, logs, pop-up alerts, platforms without markup."""
    return "".join(span.text for span in text)


# --------------------------------------------------------------------------- adapter contract


@dataclass(frozen=True)
class IncomingCommand:
    """A command typed by a user, already parsed by the adapter.

    `event_id` must be stable when the platform re-delivers the same event and
    unique within the platform, e.g. "tg-u123-42" for Telegram message 42 in
    chat 123. The core derives the transaction ID from it.
    """

    sender: Identity
    event_id: str
    command: str  # lower-case, without "/", e.g. "deduct"
    args: str = ""


@dataclass(frozen=True)
class IncomingAction:
    """A button press: the `action` and `token` of a `Button` the core sent earlier."""

    sender: Identity
    action: str
    token: str


@dataclass(frozen=True)
class Button:
    text: str
    action: str
    token: str  # opaque for the adapter; currently the transaction ID (< 48 chars)


@dataclass(frozen=True)
class Response:
    text: RichText
    buttons: tuple[Button, ...] = ()
    # For button presses: show a short pop-up and leave the original message as is.
    alert_only: bool = False

    @property
    def plain(self) -> str:
        return render_plain(self.text)


def parse_command_text(text: str) -> tuple[str, str | None, str] | None:
    """Helper for text-based platforms: "/award@MyBot Anna 5" -> ("award", "MyBot", "Anna 5").

    Returns None if the text is not a command.
    """
    text = text.strip()
    if not text.startswith("/") or len(text) < 2:
        return None
    head, *rest_parts = text.split(maxsplit=1)  # any whitespace, newlines included
    rest = rest_parts[0] if rest_parts else ""
    command, _, mention = head[1:].partition("@")
    return command.lower(), (mention or None), rest.strip()
