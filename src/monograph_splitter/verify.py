# Docs: docs/tooling/monograph-splitter.md
"""
Checks on the finished excerpts — the two ways a boundary can be wrong:
a `leak` (a foreign header survived inside the excerpt) and a
`possible-truncation` (our own tail sits above the next page's first title).
"""

from __future__ import annotations

from pathlib import Path

from .classify import name_ratio
from .index import page_anchors, page_lines
from .profile import Profile


def truncation_check(p: dict, index: list[dict], prof: Profile) -> str | None:
    """A monograph excerpt with no end cut must be followed by a page that starts
    a new entry at its top (or a chapter break). Body text above that title is
    OUR tail that got dropped — report it; the human decides via overrides."""
    if p["kind"] != "monograph" or p["endCut"] is not None or "end-at-chapter-break" in p["notes"]:
        return None
    s = p["sheet1"] + 1
    if s >= len(index):
        return None
    if index[s]["chapterBreak"]:
        return None
    anchors = [a for a in index[s]["anchors"] if a["kind"] != "heading"]
    printed = s - prof.sheet_offset + 1
    if not anchors:
        return f"p.{printed} has no header at all — the entry may continue there"
    nxt = anchors[0]
    body = nxt.get("bodyAbove", nxt["linesAbove"])
    if body > 0:
        return f"p.{printed}: {body} body line(s) above {nxt['name'] or 'the next title'}"
    return None


def verify_excerpt(path: Path, entry: str, kind: str, prof: Profile) -> list[str]:
    """Headers in the finished excerpt that are not ours: a monograph may keep
    its own sub-entries; a sub-entry may keep nothing but itself."""
    import fitz

    doc = fitz.open(path)
    foreign: list[str] = []
    first_seen = False
    for page in doc:
        for a in page_anchors(page_lines(page), page.rect.width, prof):
            if a["kind"] == "heading":
                continue
            if not first_seen:
                first_seen = True      # the start cut guarantees the first header is ours
                continue
            if name_ratio(entry, a["name"], a.get("titleText", "")) >= prof.name_match:
                continue
            if kind == "monograph" and a["kind"] == "related":
                continue
            foreign.append(f"p{page.number + 1} {a['kind']} {a['name'] or '?'}")
    doc.close()
    return foreign
