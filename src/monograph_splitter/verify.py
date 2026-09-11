# Docs: docs/tooling/monograph-splitter.md
"""
Checks on the finished excerpts — the two ways a boundary can be wrong:
a `leak` (a foreign header survived inside the excerpt) and a
`possible-truncation` (our own tail sits above the next page's first title).
"""

from __future__ import annotations

from pathlib import Path

from .classify import name_ratio
from .index import locate_heading, page_anchors, page_lines
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


def verify_excerpt(path: Path, entry: str, kind: str, prof: Profile, aliases: tuple[str, ...] = ()) -> list[str]:
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
            if max(name_ratio(n, a["name"], a.get("titleText", "")) for n in (entry, *aliases)) >= prof.name_match:
                continue
            if kind == "monograph" and a["kind"] == "related":
                continue
            foreign.append(f"p{page.number + 1} {a['kind']} {a['name'] or '?'}")
    doc.close()
    return foreign


def verify_headings(path: Path, entry: str, p: dict, index: list[dict], prof: Profile) -> list[str]:
    """Headings mode: another entry's heading (or a stop) still readable inside the finished
    excerpt — in its own type size; a smaller repetition of the name is a cross-reference,
    not a leak — is a leak. On the first sheet, anchors BEFORE ours in reading order are
    exempt when there is no start cut — that is a section banner deliberately kept."""
    import fitz

    doc = fitz.open(path)
    ours_ord = next((a["ord"] for a in index[p["sheet0"]]["anchors"] if a["name"] == entry), -1)
    foreign: list[str] = []
    for i, page in enumerate(doc):
        s = p["sheet0"] + i
        if s >= len(index):
            break
        lines = page_lines(page)
        for a in index[s]["anchors"]:
            if a["name"] == entry or a.get("synthetic") or not a.get("titleText"):
                continue
            if i == 0 and p["startCut"] is None and a["ord"] < ours_ord:
                continue
            if locate_heading(lines, a["titleText"], page.rect.width, prof, min_size=a["titleSize"] - 0.25, match=1.0):
                foreign.append(f"p{i + 1} {a['name']}")
    doc.close()
    return foreign
