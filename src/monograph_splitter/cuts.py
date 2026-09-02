# Docs: docs/tooling/monograph-splitter.md
"""
From an entry's start page to its extent and its cut rectangles.

plan()        — which sheets, where the start/end cuts are, in which column/band
apply_overrides() — the human's corrections win, verbatim
cut_rects()   — the regions redaction removes on the first/last sheet
"""

from __future__ import annotations

import difflib

from .classify import UNCERTAIN_MATCH, latin_key, name_ratio, norm
from .profile import Profile


def plan(entry: str, start_page: int, index: list[dict], prof: Profile) -> dict:
    sheet0 = start_page + prof.sheet_offset - 1
    flags: list[str] = []     # needs a human look
    notes: list[str] = []     # informational
    blocks = [a for a in index[sheet0]["anchors"] if a["kind"] != "heading"]
    monos = [a for a in blocks if a["kind"] == "monograph"]
    ours, best = None, 0.0
    for a in blocks:
        r = name_ratio(entry, a["name"], a.get("titleText", ""))
        if r > best:
            ours, best = a, r
    if not (ours and best >= prof.name_match):
        # The given page may be a CONTINUATION page (it carries our name as the
        # running sub-header). Walk back to the real start.
        sub = index[sheet0].get("subheader", "")
        if sub and difflib.SequenceMatcher(None, latin_key(sub), norm(entry)).ratio() >= UNCERTAIN_MATCH:
            for back in range(sheet0 - 1, max(sheet0 - prof.walk_back, 0) - 1, -1):
                cands = [a for a in index[back]["anchors"] if a["kind"] == "monograph"
                         and name_ratio(entry, a["name"], a.get("titleText", "")) >= UNCERTAIN_MATCH]
                if cands:
                    ours, best = cands[-1], 1.0
                    sheet0 = back
                    blocks = [a for a in index[sheet0]["anchors"] if a["kind"] != "heading"]
                    monos = [a for a in blocks if a["kind"] == "monograph"]
                    flags.append("start-page-corrected")
                    break
    if ours and best >= prof.name_match:
        pass
    elif len(monos) == 1:
        ours = monos[0]
        flags.append("start-name-fuzzy")
    elif monos:
        ours = monos[0]
        flags.append("start-header-ambiguous")
    elif blocks:
        ours = blocks[0]
        flags.append("start-header-weak")
    else:
        ours = None
        flags.append("start-header-missing")
    kind = ours["kind"] if ours else "monograph"
    if kind == "related":
        notes.append("related-entry")
        if ours.get("uncertain"):
            flags.append("kind-uncertain")

    start_cut: float | None = None
    start_col = ours["col"] if ours else "full"
    start_band = (ours.get("bandTop"), ours.get("bandBottom")) if ours else (None, None)
    if ours:
        if ours["linesAbove"] == 0 and start_col == "full":
            start_cut = None
        elif kind == "monograph" and ours.get("bodyAbove", ours["linesAbove"]) == 0:
            start_cut = None                       # only a Section banner up there
            notes.append("uncut-banner-above")
        else:
            start_cut = ours["titleTop"]
            if ours["method"] == "estimate":
                flags.append("start-cut-estimated")

    # A monograph runs to the next MONOGRAPH header (its own sub-entries belong
    # to it); a sub-entry runs to the next header of any kind. Both stop at a
    # chapter break.
    end_sheet: int | None = None
    end_cut: float | None = None
    end_col = "full"
    end_band: tuple = (None, None)
    next_name = ""
    ord_from = ours["ord"] if ours else -1
    for s in range(sheet0, min(sheet0 + prof.max_span, len(index))):
        p = index[s]
        if s != sheet0 and p["chapterBreak"]:
            end_sheet = s - 1
            notes.append("end-at-chapter-break")
            break
        after = [a for a in p["anchors"] if s != sheet0 or a["ord"] > ord_from]
        if kind == "monograph":
            cands = [a for a in after if a["kind"] == "monograph"]
            if any(a.get("uncertain") for a in after if a["kind"] == "related"
                   and (not cands or a["ord"] < cands[0]["ord"])):
                flags.append("passed-uncertain-header")
        else:
            cands = after
        if cands:
            nxt = cands[0]
            next_name = nxt["name"]
            end_col = nxt["col"]
            end_band = (nxt.get("bandTop"), nxt.get("bandBottom"))
            if nxt.get("synthetic"):
                flags.append("end-at-known-start")
            if nxt.get("uncertain"):
                flags.append("end-at-promoted-header")   # monograph by context only — check the boundary
            if nxt["kind"] == "heading":
                notes.append("end-at-parent-heading")
            # nothing of OURS above their title (banner or blank) ⇒ the page is theirs
            whole_page_theirs = nxt.get("bodyAbove", nxt["linesAbove"]) == 0 and (nxt["col"] in ("full", "left"))
            if s == sheet0:
                end_sheet, end_cut = s, nxt["titleTop"]
            elif whole_page_theirs:
                end_sheet = s - 1
            else:
                end_sheet, end_cut = s, nxt["titleTop"]
            if end_cut is not None and nxt["method"] == "estimate":
                flags.append("end-cut-estimated")
            if end_cut is not None and nxt["kind"] == "related":
                notes.append("end-at-related-entry")
            break
    # An end cut ABOVE our own label lines on our first sheet means the "next
    # entry" was the second half of our own header block (a label cluster split
    # by a too-small cluster_gap) — the excerpt would be a title and nothing else.
    # (Column-aware: a sub-entry in the left column legitimately ends at a
    # right-column header that sits HIGHER on the page — the page reads left
    # column first — so only a same-column or full-width end can be inside us.)
    if (ours and end_sheet == sheet0 and end_cut is not None and end_cut < ours["y"]
            and (end_col == "full" or end_col == start_col)):
        flags.append("ends-inside-own-header")
    if end_sheet is None:
        end_sheet = min(sheet0 + prof.max_span - 1, len(index) - 1)
        flags.append("span-clamped")
    if end_sheet - sheet0 + 1 > prof.long_span:
        flags.append("long-span")
    return {"sheet0": sheet0, "sheet1": end_sheet, "startCut": start_cut, "startCol": start_col,
            "startBand": list(start_band), "endCut": end_cut, "endCol": end_col, "endBand": list(end_band),
            "nextFormula": next_name, "kind": kind, "flags": flags, "notes": notes}


def plan_headings(entry: str, start_page: int, index: list[dict], prof: Profile) -> dict:
    """Headings mode: plan() unchanged, plus a flag when our own heading was not found on
    its sheet (the excerpt then starts at the top of the page — a human should look)."""
    p = plan(entry, start_page, index, prof)
    if any(a["name"] == entry and a.get("method") == "heading-missing" for a in index[p["sheet0"]]["anchors"]):
        p["flags"].append("heading-not-found")
    return p


OVERRIDE_KEYS = ("startCut", "startCol", "endCut", "endCol")


def apply_overrides(p: dict, ov: dict | None, prof: Profile) -> dict:
    if not ov:
        return p
    if "pages" in ov:
        a, b = ov["pages"]
        p["sheet0"], p["sheet1"] = a + prof.sheet_offset - 1, b + prof.sheet_offset - 1
    for k in OVERRIDE_KEYS:
        if k in ov:
            p[k] = ov[k]
    p["flags"].append("override")
    return p


def cut_rects(p: dict, w: float, h: float, prof: Profile) -> list[tuple[int, tuple[float, float, float, float]]]:
    """The regions removed on the first / last sheet: [(sheet_index_in_excerpt, (x0, y0, x1, y1))].

    An entry title is full-width, so its cut is a horizontal line. A sub-entry
    sits inside a column, and the page reads left column → right column *within
    a band* (the stretch between two full-width titles), then the next band.
    "Before our start" / "after the next start" is taken in that order."""
    rects: list[tuple[int, tuple[float, float, float, float]]] = []
    last = p["sheet1"] - p["sheet0"]
    bottom = h - prof.footer_band
    split = prof.column_split * w
    top_edge = prof.redact_top
    if p["startCut"] is not None:
        y, col = p["startCut"], p.get("startCol", "full")
        bt, bb = (p.get("startBand") or [None, None])
        top = bt if bt is not None else top_edge
        if col == "full":
            rects.append((0, (0, top_edge, w, y)))
        else:
            if top > top_edge:
                rects.append((0, (0, top_edge, w, top)))                               # earlier bands
            else:
                rects.append((0, (0, top_edge, w, min(y, prof.subheader_bottom))))   # previous entry's running name
            if col == "left":
                rects.append((0, (0, top, split, y)))
            else:
                rects.append((0, (0, top, split, bb if bb is not None else bottom)))
                rects.append((0, (split, top, w, y)))
    if p["endCut"] is not None:
        y, col = p["endCut"], p.get("endCol", "full")
        bt, bb = (p.get("endBand") or [None, None])
        band_bottom = bb if bb is not None else bottom
        if col == "full":
            rects.append((last, (0, y, w, bottom)))
        else:
            if col == "left":
                rects.append((last, (0, y, split, band_bottom)))
                right_top = bt if bt is not None else prof.subheader_bottom
                rects.append((last, (split, right_top, w, band_bottom)))
            else:
                rects.append((last, (split, y, w, band_bottom)))
            if bb is not None:
                rects.append((last, (0, bb, w, bottom)))                              # later bands
    return rects
