"""Knowledge search: keyword search over directories of markdown (postmortems, runbooks, notes).

The corpus of one server is a few hundred files at most, so the files are scanned at query time:
no index to build or to keep in step. Results are excerpts, never whole files.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

_WORD = re.compile(r"[a-z0-9][a-z0-9_.-]{2,}")
_MAX_FILE_BYTES = 512_000
_EXCERPT = 400


@dataclass(frozen=True)
class Hit:
    path: str
    title: str
    excerpt: str
    score: int


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def search(paths: Iterable[Path | str], query: str, *, limit: int = 5) -> list[Hit]:
    """The markdown files that mention the most words of the query, best first."""
    terms = set(_words(query))
    if not terms:
        return []
    hits: list[Hit] = []
    for root in paths:
        base = Path(root)
        if not base.is_dir():
            continue
        for file in sorted(base.rglob("*.md")):
            try:
                if file.stat().st_size > _MAX_FILE_BYTES:
                    continue
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lowered = text.lower()
            title = _title(text, file.stem)
            # A term in the title or the name counts more than one in the body.
            head = set(_words(title)) | set(_words(file.stem))
            score = sum(3 if term in head else 1 for term in terms if term in lowered)
            if score == 0:
                continue
            first = min(pos for term in terms if (pos := lowered.find(term)) >= 0)
            start = max(first - _EXCERPT // 4, 0)
            excerpt = " ".join(text[start : start + _EXCERPT].split())
            hits.append(
                Hit(path=str(file.relative_to(base)), title=title, excerpt=excerpt, score=score)
            )
    hits.sort(key=lambda hit: (-hit.score, hit.path))
    return hits[:limit]
