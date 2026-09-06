"""Conservative displayed-text input for semantic matching (no citation offsets)."""
from __future__ import annotations

import re
from html.parser import HTMLParser

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "param", "source", "track", "wbr"}
_HIDDEN = {"script", "style", "ix:hidden", "template"}
_BLOCK = {"p", "div", "section", "article", "table", "tr", "td", "th", "li", "ul", "ol",
          "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr", "header", "footer"}
_HIDDEN_STYLE = re.compile(r"(?:^|;)(?:display:none|visibility:hidden)(?:!important)?(?:;|$)")


class _DisplayedText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if len({name for name, _value in attrs}) != len(attrs):
            raise ValueError("duplicate HTML attributes have ambiguous visibility")
        attributes = dict(attrs)
        style = re.sub(r"/\*.*?\*/", "", attributes.get("style") or "", flags=re.S)
        if "/*" in style or "\\" in style:
            raise ValueError("unsupported escaped or unterminated inline style")
        style = re.sub(r"\s+", "", style).lower()
        hidden = (
            tag in _HIDDEN or "hidden" in attributes
            or bool(_HIDDEN_STYLE.search(style))
            or str(attributes.get("aria-hidden", "")).lower() == "true"
        )
        self.parts.append(" . " if hidden or tag in _BLOCK else " ")
        if tag not in _VOID:
            self.stack.append((tag, hidden))

    def handle_startendtag(self, tag, attrs):
        # HTML ignores the self-closing slash on non-void elements. A <script/> or
        # <div hidden/> must not reopen visible authority for its following content.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                hidden = any(value for _name, value in self.stack[index:])
                del self.stack[index:]
                self.parts.append(" . " if hidden or tag in _BLOCK else " ")
                break

    def handle_data(self, data):
        if not any(hidden for _tag, hidden in self.stack):
            self.parts.append(data)

    def handle_comment(self, data):
        # Removing a hidden island must not concatenate two incomplete authority clauses.
        self.parts.append(" . ")


def displayed_text(decoded: str) -> str:
    parser = _DisplayedText()
    parser.feed(decoded)
    parser.close()
    return "".join(parser.parts)
