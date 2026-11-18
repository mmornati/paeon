"""A postmortem's markdown as a page. The file stays markdown; this is only how it is shown.

The text is partly a model's prose, so nothing in it is trusted: raw HTML is not parsed (it comes
out as text), a link is kept only if it goes to an http(s) or mailto address, to a heading of the
page or to another postmortem file, and an image is shown as a link, since the pages load nothing
from another host. The caller masks secrets in the text before it gets here.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict
from markupsafe import Markup

_LINK = re.compile(r"(https?://|mailto:|#)\S*|[A-Za-z0-9][A-Za-z0-9._-]{0,150}\.md(#\S*)?", re.I)
DIAGRAM = '<code class="language-mermaid">'  # what a ```mermaid block is rendered as
_BOX = re.compile(r"\[([ xX])\]\s+")


def _image(
    self: RendererHTML, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    token = tokens[idx]
    words = self.renderInlineAsText(token.children or [], options, env) or "image"
    link = Token("link_open", "a", 1, attrs={"href": token.attrGet("src") or "", **_OUTSIDE})
    return f"<a{self.renderAttrs(link)}>{Markup.escape(words)}</a>"


_OUTSIDE: dict[str, Any] = {"rel": "noreferrer noopener"}


def _parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
    md.validateLink = lambda url: _LINK.fullmatch(url.strip()) is not None  # type: ignore[method-assign]
    md.add_render_rule("image", _image)
    return md


_MD = _parser()


def body_of(text: str) -> str:
    """The text without its front matter, which the page shows as the document's properties."""
    if text.startswith("---\n"):
        _, found, rest = text[4:].partition("\n---")
        if found:
            return rest.partition("\n")[2]
    return text


def _dress(tokens: list[Token]) -> list[Token]:
    """Outside links say so, tables can scroll, and `- [ ]` becomes a ticked or empty box."""
    if len(tokens) >= 3 and tokens[0].type == "heading_open" and tokens[0].tag == "h1":
        tokens = tokens[3:]  # the title is already the page's heading
    out: list[Token] = []
    for index, token in enumerate(tokens):
        if token.type == "table_open":
            out.append(Token("html_block", "", 0, content='<div class="md-table">'))
        out.append(token)
        if token.type == "table_close":
            out.append(Token("html_block", "", 0, content="</div>"))
        for child in token.children or []:
            href = str(child.attrGet("href"))
            if child.type == "link_open" and href.startswith(("http", "mailto")):
                child.attrs.update(_OUTSIDE)
        if token.type != "inline" or index < 2 or tokens[index - 2].type != "list_item_open":
            continue
        first = token.children[0] if token.children else None
        ticked = _BOX.match(first.content) if first is not None and first.type == "text" else None
        if first is None or ticked is None:
            continue
        done = ticked.group(1) != " "
        first.content = first.content[ticked.end() :]
        tokens[index - 2].attrJoin("class", "md-task md-done" if done else "md-task")
        box = '<input type="checkbox" disabled{}> '.format(" checked" if done else "")
        token.children.insert(0, Token("html_inline", "", 0, content=box))  # type: ignore[union-attr]
    return out


def render(text: str) -> Markup:
    """The body of a postmortem as HTML that is safe to put in a page."""
    tokens = _dress(_MD.parse(body_of(text)))
    # The renderer escapes every text it prints, and raw HTML was never parsed as such.
    return Markup(_MD.renderer.render(tokens, _MD.options, {}))  # noqa: S704
