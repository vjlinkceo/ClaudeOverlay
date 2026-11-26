"""Markdown answers to HTML for a QTextDocument, which supports only a small CSS subset (inline styles).

Formulas are cut out before markdown and come back as `<img src="math://…">` placeholders that the
chat view resolves to images, or to plain text when matplotlib cannot typeset them.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field

import mistune
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.style import Style
from pygments.token import Comment, Generic, Keyword, Name, Number, Operator, Punctuation, String
from pygments.util import ClassNotFound

from overlay.gui.chat.mathtext import (
    CODE_SPAN_RE,
    DISPLAY_MATH_RE,
    INLINE_MATH_RE,
    has_matrix,
    is_table_row,
    latex_inline,
    math_block,
)

MATH_LANGS = {"latex", "tex", "math", "katex"}
MATH_SCHEME = "math://"
_PLAIN_LANGS = {"", "text", "txt", "plain", "plaintext", "output"}
_PLACEHOLDER = "QMATH{}QEND"
_PLACEHOLDER_RE = re.compile(r"QMATH(\d+)QEND")
_DISPLAY_P_RE = re.compile(r"<p>QMATH(\d+)QEND</p>")
_TH_RE = re.compile(r"<th(\s[^>]*)?>")
_STASHED_RE = re.compile("(\\d+)")


@dataclass
class MathSpan:
    latex: str
    display: bool


@dataclass
class Rendered:
    html: str
    math: dict[str, MathSpan] = field(default_factory=dict)


def math_url(span: MathSpan) -> str:
    digest = hashlib.sha1(f"{span.display}:{span.latex}".encode()).hexdigest()[:16]
    return f"{MATH_SCHEME}{digest}"


def img_tag(url: str) -> str:
    return f'<img src="{url}" align="middle">'


def fallback_html(span: MathSpan, colors: dict[str, str]) -> str:
    """Plain-text rendering for a formula matplotlib could not draw."""
    if span.display or has_matrix(span.latex):
        return _code_box(html.escape(math_block(span.latex)), colors)
    return f'<span style="font-style:italic">{html.escape(latex_inline(span.latex))}</span>'


def _code_box(body: str, colors: dict[str, str]) -> str:
    return (
        f'<table width="100%" cellpadding="7" cellspacing="0" bgcolor="{colors["code_bg"]}" '
        'style="margin-top:5px;margin-bottom:5px"><tr><td>'
        f'<pre style="font-family:{colors["mono"]};color:{colors["code_fg"]};white-space:pre-wrap">{body}</pre>'
        "</td></tr></table>"
    )


def _style(colors: dict[str, str]) -> type[Style]:
    class OverlayStyle(Style):
        background_color = colors["code_bg"]
        styles = {
            Comment: "italic #7a8089",
            Keyword: "#c678dd",
            Keyword.Constant: "#d19a66",
            Keyword.Type: "#e5c07b",
            Name.Function: "#61afef",
            Name.Class: "#e5c07b",
            Name.Builtin: "#56b6c2",
            Name.Decorator: "#e5c07b",
            Name.Tag: "#e06c75",
            Name.Attribute: "#d19a66",
            String: "#98c379",
            Number: "#d19a66",
            Operator: colors["code_fg"],
            Punctuation: "#abb2bf",
            Generic.Error: "#f28b82",
            Generic.Heading: "bold",
        }

    return OverlayStyle


class _Renderer(mistune.HTMLRenderer):
    def __init__(self, colors: dict[str, str]):
        super().__init__(escape=True)
        self.colors = colors
        self.formatter = HtmlFormatter(nowrap=True, noclasses=True, style=_style(colors))

    def block_code(self, code: str, info: str | None = None) -> str:
        lang = info.strip().split()[0].lower() if info and info.strip() else ""
        body = None
        if lang not in _PLAIN_LANGS:
            try:
                body = highlight(code, get_lexer_by_name(lang), self.formatter)
            except ClassNotFound:
                body = None
        if body is None:
            body = html.escape(code)
        return _code_box(body.rstrip("\n"), self.colors) + "\n"

    def codespan(self, text: str) -> str:
        c = self.colors
        return (
            f'<code style="font-family:{c["mono"]};color:{c["code_fg"]};background-color:{c["code_bg"]}">'
            f"{html.escape(text)}</code>"
        )

    def heading(self, text: str, level: int, **attrs) -> str:
        size = {1: 14, 2: 12.5, 3: 11.5}.get(level, 11)
        return (
            f'<h{level} style="font-size:{size}pt;font-weight:600;margin-top:6px;margin-bottom:2px">{text}</h{level}>\n'
        )

    def paragraph(self, text: str) -> str:
        if text.startswith("❓"):
            return f'<p><span style="color:{self.colors["accent"]};font-weight:600">{text}</span></p>\n'
        return f"<p>{text}</p>\n"

    def link(self, text: str, url: str, title: str | None = None) -> str:
        if url.startswith(("http://", "https://")):
            return f'<a href="{html.escape(url, quote=True)}">{text}</a>'
        return text

    def image(self, text: str, url: str, title: str | None = None) -> str:
        return html.escape(text)

    def block_quote(self, text: str) -> str:
        return f'<blockquote style="color:{self.colors["muted"]}">{text}</blockquote>\n'

    def softbreak(self) -> str:
        return "<br/>\n"

    def thematic_break(self) -> str:
        return f'<hr color="{self.colors["border"]}"/>\n'


class Renderer:
    """One instance per theme; `render` is called on every stream update, so it stays cheap."""

    def __init__(self, colors: dict[str, str]):
        self.colors = colors
        self._md = mistune.create_markdown(renderer=_Renderer(colors), plugins=["table", "strikethrough"])

    def render(self, text: str) -> Rendered:
        spans: list[MathSpan] = []
        prepared = _prepare(text, spans)
        body = self._md(prepared)
        body = body.replace(
            "<table>",
            f'<table cellpadding="5" cellspacing="0" border="1" bordercolor="{self.colors["border"]}" '
            'style="margin-top:5px;margin-bottom:5px">',
        )
        body = _TH_RE.sub(lambda m: f'<th bgcolor="{self.colors["card"]}"{m[1] or ""}>', body)
        math: dict[str, MathSpan] = {}
        for span in spans:
            math[math_url(span)] = span
        body = _DISPLAY_P_RE.sub(lambda m: f'<p align="center">{img_tag(math_url(spans[int(m[1])]))}</p>', body)
        body = _PLACEHOLDER_RE.sub(lambda m: img_tag(math_url(spans[int(m[1])])), body)
        return Rendered(body, math)


def _prepare(text: str, spans: list[MathSpan]) -> str:
    """Math outside code blocks becomes placeholders; ```latex blocks become readable plain blocks."""
    out: list[str] = []
    prose: list[str] = []
    code: list[str] | None = None
    lang = ""
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            if code is None:
                if prose:
                    out.append(_extract_math("\n".join(prose), spans))
                prose, code, lang = [], [], line.strip()[3:].strip().lower()
                continue
            body = "\n".join(code)
            out.append(f"```text\n{math_block(body)}\n```" if lang in MATH_LANGS else f"```{lang}\n{body}\n```")
            code = None
            continue
        (prose if code is None else code).append(line)
    if code is not None:
        out.append(f"```{lang}\n" + "\n".join(code))
    elif prose:
        out.append(_extract_math("\n".join(prose), spans))
    return "\n".join(out)


def _blank_before_tables(prose: str) -> str:
    """Models often start a table right under a sentence; markdown needs a blank line there."""
    out: list[str] = []
    for line in prose.split("\n"):
        if is_table_row(line) and out and out[-1].strip() and not is_table_row(out[-1]):
            out.append("")
        out.append(line)
    return "\n".join(out)


def _extract_math(prose: str, spans: list[MathSpan]) -> str:
    stashed: list[str] = []

    def stash(m: re.Match) -> str:
        stashed.append(m[0])
        return f"{len(stashed) - 1}"

    def display(m: re.Match) -> str:
        spans.append(MathSpan(" ".join((m[1] or m[2]).split()), True))
        return f"\n\n{_PLACEHOLDER.format(len(spans) - 1)}\n\n"

    def inline(m: re.Match) -> str:
        spans.append(MathSpan((m[1] or m[2]).strip(), False))
        return _PLACEHOLDER.format(len(spans) - 1)

    prose = _blank_before_tables(prose)
    prose = CODE_SPAN_RE.sub(stash, prose)
    prose = DISPLAY_MATH_RE.sub(display, prose)
    prose = INLINE_MATH_RE.sub(inline, prose)
    return _STASHED_RE.sub(lambda m: stashed[int(m[1])], prose) if stashed else prose
