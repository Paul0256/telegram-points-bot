"""Rendering of core rich text as Telegram HTML (parse_mode=HTML)."""

from __future__ import annotations

from html import escape

from ...core.messages import RichText, Style

_TAGS = {Style.BOLD: "b", Style.ITALIC: "i", Style.CODE: "code", Style.PRE: "pre"}


def to_html(text: RichText) -> str:
    """Every span is HTML-escaped, so user-provided names and notes can't inject markup."""
    parts = []
    for span in text:
        body = escape(span.text, quote=False)
        tag = _TAGS.get(span.style)
        parts.append(f"<{tag}>{body}</{tag}>" if tag and body else body)
    return "".join(parts)
