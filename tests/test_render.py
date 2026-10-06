from __future__ import annotations

from pointsbot.adapters.telegram.render import to_html
from pointsbot.core.messages import bold, code, italic, pre, render_plain, rich


def test_telegram_html_rendering_escapes_every_span():
    text = rich("Hi ", bold("<Anna>"), " & ", italic("x"), " ", code("id-1"), "\n", pre("a < b"))
    assert to_html(text) == "Hi <b>&lt;Anna&gt;</b> &amp; <i>x</i> <code>id-1</code>\n<pre>a &lt; b</pre>"


def test_plain_rendering_and_empty_spans():
    text = rich("a", bold(""), "b")
    assert render_plain(text) == "ab"
    assert to_html(text) == "ab"
