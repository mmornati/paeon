"""MkDocs hooks: links that leave docs/ go to the repository on GitHub.

The Markdown in docs/ is written to read well on GitHub, where `../examples/compose.yaml` is a
file next to it. On the site that file does not exist, so the link is rewritten at build time.
"""

from __future__ import annotations

import re

REPO = "https://github.com/mmornati/paeon/blob/main/"
_OUTSIDE = re.compile(r"\]\(\.\./([^)#\s]+)(#[^)\s]*)?\)")


def on_page_markdown(markdown: str, **_: object) -> str:
    return _OUTSIDE.sub(lambda m: f"]({REPO}{m.group(1)}{m.group(2) or ''})", markdown)
