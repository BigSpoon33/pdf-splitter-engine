# Docs: docs/tooling/monograph-splitter.md
"""
Where the entry list comes from. The engine only ever sees
`Entry(name, page, source)` — a printed start page per entry — plus an optional
set of "known" printed start pages (a TOC, an index) used to seed a synthetic
top-of-page anchor where the OCR found no header at all.

`--entries <json>` is the generic input: `[{"name": "Gui Zhi Tang", "page": 51}]`.
`entries_from_vault()` is the Inkwell adapter: one note per entry, the page in
its `source_page:` frontmatter. It is the only code that knows what a vault is.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Entry:
    name: str
    page: int
    source: str = "entries"   # "entries" | "frontmatter" — becomes manifest.pageSource


@dataclass
class EntryList:
    entries: list[Entry]
    skipped: list[str]          # names with no usable page — reported, never silent
    known_pages: set[int]       # every start page anyone told us about (for synthetic anchors)


def _page_ok(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def load_entries_json(path: Path) -> EntryList:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a JSON list of {{name, page}} objects")
    entries: list[Entry] = []
    skipped: list[str] = []
    for i, item in enumerate(raw):
        name = item.get("name") if isinstance(item, dict) else None
        page = item.get("page") if isinstance(item, dict) else None
        if not isinstance(name, str) or not name.strip():
            skipped.append(f"#{i} (no name)")
            continue
        if not _page_ok(page):
            skipped.append(f"{name} (no printed start page)")
            continue
        entries.append(Entry(name.strip(), page, str(item.get("source") or "entries")))
    return EntryList(entries, skipped, {e.page for e in entries})


def frontmatter_page(text: str) -> int | None:
    fm = text.split("---", 2)[1] if text.startswith("---") else ""
    m = re.search(r'^source_page:\s*"?(\d+)"?\s*$', fm, re.M)
    if not m:
        return None
    p = int(m.group(1))
    return p if p > 0 else None


def entries_from_vault(vault: Path, folder: str = "TCM_Formulas",
                       exclude: tuple[str, ...] = ("Composition_Reference",)) -> EntryList:
    """Inkwell adapter: every note in <vault>/<folder> is an entry named after
    its file, starting at its `source_page:`. Every note's page (selected or not)
    is a known start — the OCR may have missed a header on any of them."""
    live = Path(vault) / folder
    entries: list[Entry] = []
    skipped: list[str] = []
    known: set[int] = set()
    for note in sorted(live.glob("*.md")):
        if note.stem in exclude:
            continue
        page = frontmatter_page(note.read_text(encoding="utf-8", errors="replace"))
        if page:
            entries.append(Entry(note.stem, page, "frontmatter"))
            known.add(page)
        else:
            skipped.append(note.stem)
    return EntryList(entries, skipped, known)


def load_known_pages(path: Path) -> set[int]:
    """Tolerant loader for auxiliary start-page lists: a dict name→page, a list
    of pages, or a list of rows whose first element is the page."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    pages: set[int] = set()
    if isinstance(raw, dict):
        for v in raw.values():
            v = v.get("page") if isinstance(v, dict) else v
            if _page_ok(v):
                pages.add(v)
    elif isinstance(raw, list):
        for item in raw:
            v = item[0] if isinstance(item, list) and item else item
            if _page_ok(v):
                pages.add(v)
    return pages


def select(entries: list[Entry], only: set[str] | None, limit: int | None) -> tuple[list[Entry], list[str]]:
    """--only / --limit; returns (selected, names in --only with no entry)."""
    chosen = entries
    unknown: list[str] = []
    if only:
        chosen = [e for e in entries if e.name in only]
        unknown = sorted(only - {e.name for e in entries})
    if limit:
        chosen = chosen[:limit]
    return chosen, unknown
