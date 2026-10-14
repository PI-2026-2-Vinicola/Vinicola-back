"""
Sanitização, por lista de permissões, do HTML vindo de fontes externas (respostas do RespondeAgro).

Mantém só marcação de texto (parágrafos, listas, ênfases, tabelas), imagens embutidas em base64,
destaques da busca (<span class="highlight">) e links http(s). Scripts, estilos, eventos (on*),
URLs javascript: e qualquer outro atributo são removidos. O texto é sempre reescapado.
"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

ALLOWED_TAGS = {
    "p", "br", "ul", "ol", "li", "strong", "b", "em", "i", "u", "sup", "sub", "small", "span", "div", "blockquote",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "h3", "h4", "h5", "h6", "img", "a", "figure", "figcaption",
}
VOID_TAGS = {"br", "img"}
# Tags descartadas junto com todo o conteúdo.
DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "noscript", "template", "svg", "math", "head", "title", "form", "textarea", "select", "button"}
# Títulos grandes viram subtítulos para não competir com a hierarquia da página.
HEADING_MAP = {"h1": "h4", "h2": "h4"}
BLOCK_TAGS = {"p", "div", "li", "tr", "h3", "h4", "h5", "h6", "blockquote", "figcaption", "caption"}

_DATA_IMAGE = re.compile(r"^data:image/(png|jpe?g|gif|webp);base64,[A-Za-z0-9+/=\s]+$", re.IGNORECASE)
_HTTP_URL = re.compile(r"^https?://[^\s\"'<>]+$", re.IGNORECASE)
_SPAN_NUMBER = re.compile(r"^\d{1,2}$")


def _attrs(tag: str, attrs: dict[str, str | None]) -> str | None:
    """Atributos permitidos já escapados; None = descartar a tag (ex.: imagem sem fonte segura)."""
    if tag == "img":
        src = (attrs.get("src") or "").strip()
        if not _DATA_IMAGE.match(src):
            return None
        alt = escape((attrs.get("alt") or "Figura")[:200])
        return f' src="{escape(src)}" alt="{alt}" loading="lazy"'
    if tag == "a":
        href = (attrs.get("href") or "").strip()
        if not _HTTP_URL.match(href):
            return ""  # mantém o texto do link, sem destino
        return f' href="{escape(href)}" target="_blank" rel="noopener noreferrer nofollow"'
    if tag == "span":
        return ' class="highlight"' if (attrs.get("class") or "").strip() == "highlight" else ""
    if tag in ("td", "th"):
        out = ""
        for name in ("colspan", "rowspan"):
            value = (attrs.get(name) or "").strip()
            if _SPAN_NUMBER.match(value):
                out += f' {name}="{value}"'
        return out
    return ""


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.html: list[str] = []
        self.text: list[str] = []
        self.stack: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in DROP_CONTENT:
            self.skip += 1
            return
        if self.skip:
            return
        tag = HEADING_MAP.get(tag, tag)
        if tag not in ALLOWED_TAGS:
            return
        clean = _attrs(tag, dict(attrs))
        if clean is None:
            return
        self.html.append(f"<{tag}{clean}>")
        if tag == "br":
            self.text.append("\n")
        elif tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS and tag not in DROP_CONTENT:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in DROP_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        tag = HEADING_MAP.get(tag, tag)
        if tag in VOID_TAGS or tag not in self.stack:
            return
        while self.stack:  # fecha também tags que ficaram abertas (HTML malformado)
            open_tag = self.stack.pop()
            self.html.append(f"</{open_tag}>")
            if open_tag == tag:
                break
        if tag in BLOCK_TAGS:
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        self.html.append(escape(data, quote=False))
        self.text.append(data)

    def result(self) -> tuple[str, str]:
        self.close()
        while self.stack:
            self.html.append(f"</{self.stack.pop()}>")
        return "".join(self.html).strip(), re.sub(r"\s+", " ", "".join(self.text)).strip()


def sanitize_html(raw: str | None) -> tuple[str, str]:
    """HTML externo → (HTML seguro, texto puro)."""
    if not raw:
        return "", ""
    parser = _Sanitizer()
    parser.feed(str(raw))
    return parser.result()


def excerpt(text: str, limit: int = 280) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",.;:")
    return f"{cut}…"
