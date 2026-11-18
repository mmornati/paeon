"""Knowledge search: a full-text index over directories of markdown (postmortems, runbooks).

The index is SQLite FTS5, in memory, one row per section of a file (the text under one heading).
It is brought up to date before every search by comparing each file's size and modification
time with what was indexed, so there is nothing to rebuild by hand and nothing to keep in step:
a postmortem written a minute ago is found. Ranking is BM25, with a word in the document's title
or in the section's heading counting more than one in the body. Results are excerpts, never
whole files.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

_WORD = re.compile(r"[a-z0-9]{2,}")
_HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)
_MAX_FILE_BYTES = 512_000
_EXCERPT = 400


@dataclass(frozen=True)
class Hit:
    path: str
    title: str
    excerpt: str
    score: float
    heading: str = ""


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def sections(text: str) -> list[tuple[str, str]]:
    """A markdown text as (heading, body) pairs. What precedes the first heading has none."""
    found: list[tuple[str, str]] = []
    heading, start = "", 0
    for match in _HEADING.finditer(text):
        body = text[start : match.start()].strip()
        if body or heading:
            found.append((heading, body))
        heading, start = match.group(1).strip(), match.end()
    found.append((heading, text[start:].strip()))
    return [(h, b) for h, b in found if h or b]


class Index:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        self._db.execute(
            "CREATE VIRTUAL TABLE sections USING fts5("
            "title, heading, body, root UNINDEXED, path UNINDEXED, doc_title UNINDEXED,"
            " tokenize = 'porter unicode61')"
        )
        self._seen: dict[tuple[str, str], tuple[int, int]] = {}

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _sync(self, roots: list[Path]) -> None:
        present: set[tuple[str, str]] = set()
        for base in roots:
            if not base.is_dir():
                continue
            for file in sorted(base.rglob("*.md")):
                key = (str(base), str(file.relative_to(base)))
                try:
                    stat = file.stat()
                    if stat.st_size > _MAX_FILE_BYTES:
                        continue
                    stamp = (stat.st_size, stat.st_mtime_ns)
                    present.add(key)
                    if self._seen.get(key) == stamp:
                        continue
                    text = file.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                self._drop(key)
                title = _title(text, file.stem)
                # A heading with nothing under it has no excerpt to give: the title is searched
                # with every section anyway.
                parts = [part for part in sections(text) if part[1]] or [("", "")]
                # The file name is searched with the title: `2026-11-02-proxy-outage.md`.
                head = f"{title} {file.stem}"
                self._db.executemany(
                    "INSERT INTO sections (title, heading, body, root, path, doc_title)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    [(head, heading, body, *key, title) for heading, body in parts],
                )
                self._seen[key] = stamp
        scanned = {str(base) for base in roots}
        for key in [key for key in self._seen if key[0] in scanned and key not in present]:
            self._drop(key)
            del self._seen[key]

    def _drop(self, key: tuple[str, str]) -> None:
        self._db.execute("DELETE FROM sections WHERE root = ? AND path = ?", key)

    def search(self, paths: Iterable[Path | str], query: str, *, limit: int = 5) -> list[Hit]:
        terms = list(dict.fromkeys(_words(query)))
        roots = [Path(root) for root in paths]
        if not terms or not roots:
            return []
        match = " OR ".join(f'"{term}"' for term in terms)
        marks = ", ".join("?" * len(roots))
        with self._lock:
            self._sync(roots)
            rows = self._db.execute(
                "SELECT path, doc_title, heading, body, bm25(sections, 6.0, 3.0, 1.0) AS rank"  # noqa: S608
                f" FROM sections WHERE sections MATCH ? AND root IN ({marks})"
                " ORDER BY rank, path LIMIT 200",
                [match, *map(str, roots)],
            ).fetchall()
        hits: dict[str, Hit] = {}
        for path, title, heading, body, rank in rows:
            if path in hits:  # the best section of each file
                continue
            lowered = body.lower()
            first = min((pos for term in terms if (pos := lowered.find(term)) >= 0), default=0)
            start = max(first - _EXCERPT // 4, 0)
            excerpt = " ".join(body[start : start + _EXCERPT].split())
            hits[path] = Hit(
                path=path, title=title, excerpt=excerpt, score=round(-rank, 3), heading=heading
            )
            if len(hits) >= limit:
                break
        return list(hits.values())


_INDEX = Index()


def search(paths: Iterable[Path | str], query: str, *, limit: int = 5) -> list[Hit]:
    """The markdown files that answer the query best, each with its best section."""
    return _INDEX.search(paths, query, limit=limit)
