# Docs: docs/tooling/monograph-splitter.md
"""
The book index: every sheet's lines → anchors (header blocks, parent headings),
reading order + bands, chapter breaks, running sub-header. Cached on disk,
keyed on the book, the engine version and the profile hash.
"""

from __future__ import annotations

import difflib
import json
import time
from pathlib import Path

from . import ENGINE_VERSION
from .classify import (
    UNCERTAIN_MATCH, Line, header_geometry, is_left, latin_key, norm, same_column,
)
from .profile import Profile


def page_lines(page) -> list[Line]:
    lines: list[Line] = []
    for blk in page.get_text("dict")["blocks"]:
        for ln in blk.get("lines", []):
            t = "".join(sp["text"] for sp in ln["spans"]).strip()
            if t:
                b = ln["bbox"]
                lines.append((b[1], b[3], b[0], b[2], t, max(sp["size"] for sp in ln["spans"])))
    lines.sort()
    return lines


def label_value(lines: list[Line], label: Line, w: float, prof: Profile) -> str:
    """A label whose value is its own line on the same baseline, to its right — Bensky's
    `PHARMACEUTICAL NAME   Achyranthis bidentatae Radix` is two lines in the text layer,
    so the name capture group is empty. Same column only: the other column's line on
    that baseline is somebody else's prose."""
    height = max(label[1] - label[0], 1.0)
    right = [ln for ln in lines if ln is not label and ln[2] >= label[3] - 2.0
             and min(ln[1], label[1]) - max(ln[0], label[0]) >= 0.5 * height      # shares the baseline (a bigger face tops out higher)
             and same_column(ln, label[2], w, prof)]
    return min(right, key=lambda ln: ln[2])[4].strip() if right else ""


def page_anchors(lines: list[Line], w: float, prof: Profile) -> list[dict]:
    raw: list[tuple[float, float, str, str]] = []
    for ln in lines:
        m = prof.label_name_re.search(ln[4])
        if m:
            raw.append((ln[0], ln[2], "name", m.group(1).strip() or label_value(lines, ln, w, prof)))
        elif any(rx.search(ln[4]) for rx in prof.label_secondary_res):
            raw.append((ln[0], ln[2], "secondary", ""))
    # The label lines of one header block sit within cluster_gap; collapse them — per column:
    # two-column pages interleave the columns' label lines by height, and a block in the other
    # column must not break this one (Bensky's blocks sit inside a column).
    anchors: list[dict] = []
    for y, x0, _kind, name in raw:
        prev = next((a for a in reversed(anchors) if is_left(a["x0"], w, prof) == is_left(x0, w, prof)), None)
        if prev is not None and y - prev["y"] <= prof.cluster_gap:
            if not prev["name"] and name:
                prev["name"] = name
            continue
        geo = header_geometry(lines, y, x0, w, prof)
        anchors.append({"y": round(y, 1), "x0": round(x0, 1), "name": name, **geo})
    lo, hi = prof.parent_size
    for ln in lines:
        if ln[0] >= prof.header_band and len(ln[4]) <= prof.parent_max_len and lo <= ln[5] <= hi and prof.parent_re.search(ln[4]):
            col = "left" if is_left(ln[2], w, prof) else "right"
            n_above = sum(1 for m in lines if prof.header_band <= m[0] < ln[0] - 2 and same_column(m, ln[2], w, prof))
            anchors.append({"y": round(ln[0], 1), "x0": round(ln[2], 1), "name": ln[4].strip(), "kind": "heading",
                            "col": col, "titleTop": round(max(prof.header_band, ln[0] - 2), 1), "linesAbove": n_above,
                            "bodyAbove": n_above, "method": "heading", "titleSize": 0.0, "titleText": "", "uncertain": False})
    return finalize_anchors(anchors)


def finalize_anchors(anchors: list[dict]) -> list[dict]:
    """Reading order + bands. Full-width entry titles split the page into bands;
    inside a band the left column reads before the right one. Each column-bound
    anchor remembers its band's top/bottom (the neighbouring full-width titles'
    cut lines) so removals can stop at the band edge."""
    fulls = sorted(a["y"] for a in anchors if a["col"] == "full")
    tops = sorted(a["titleTop"] for a in anchors if a["col"] == "full")

    def order(a: dict) -> tuple:
        band = sum(1 for fy in fulls if fy < a["y"])
        colk = 2 if a["col"] == "full" else (0 if a["col"] == "left" else 1)
        return (band, colk, a["y"])

    anchors.sort(key=order)
    for i, a in enumerate(anchors):
        a["ord"] = i
        if a["col"] == "full":
            a["bandTop"], a["bandBottom"] = None, None
        else:
            below = [t for t in tops if t <= a["y"]]
            above = [t for t in tops if t > a["y"]]
            a["bandTop"] = round(max(below), 1) if below else None
            a["bandBottom"] = round(min(above), 1) if above else None
    return anchors


def chapter_break(lines: list[Line], prof: Profile) -> bool:
    """Chapter Summary / opener / biography pages: big headings, wherever they
    sit (the Summary heading is 19pt at y≈45). Running headers are 8–9pt, so
    size alone keeps them out."""
    big_ys = {round(ln[0], 1) for ln in lines if ln[5] >= prof.break_min_size}
    return any(round(ln[0], 1) in big_ys and len(ln[4]) <= prof.break_max_len
               and (any(rx.search(ln[4]) for rx in prof.break_res) or prof.chapter_only_re.match(ln[4]))
               for ln in lines)


def resolve_uncertain(pages: list[dict], prof: Profile) -> int:
    """A big Latin line with no script line after it is EITHER the running
    sub-header (it repeats the current entry's name) OR a new entry whose script
    title the OCR destroyed. Reading the book in order tells the two apart —
    except in the top band, where an OCR-garbled sub-header is likelier than a
    lost entry: those stay sub-entries and stay flagged for a human."""
    current = ""
    changed = 0
    for pg in pages:
        touched = False
        for a in pg["anchors"]:
            if a.get("uncertain"):
                if current and any(difflib.SequenceMatcher(None, t, current).ratio() >= UNCERTAIN_MATCH for t in a.get("bigTexts", [])):
                    a["uncertain"] = False
                elif a.get("bigTop", 0.0) < prof.header_band + 6:
                    pass
                else:
                    a["kind"], a["col"], touched, changed = "monograph", "full", True, changed + 1
            if a["kind"] == "monograph" and not a.get("synthetic"):
                key = norm(a["name"]) or latin_key(a.get("titleText", ""))
                if key:
                    current = key
        if touched:
            finalize_anchors(pg["anchors"])
    return changed


def index_page(page, prof: Profile) -> dict:
    w = page.rect.width
    lines = page_lines(page)
    if prof.anchor_source == "headings":
        # No label blocks to find and no running sub-header to walk back from: the
        # anchors come from the entries' headings, planted later by add_heading_anchors.
        anchors, sub = [], []
    else:
        anchors = page_anchors(lines, w, prof)
        sub = [ln[4] for ln in lines if ln[0] < prof.header_band and ln[5] >= prof.big_min_size]
    return {"anchors": anchors, "chapterBreak": chapter_break(lines, prof), "lines": len(lines),
            "subheader": sub[0][:80] if sub else "",
            "W": round(w, 1), "H": round(page.rect.height, 1)}


def index_book(book, cache: Path | None, prof: Profile, log=print) -> list[dict]:
    key = {"size": Path(book.name).stat().st_size if book.name else 0, "pages": book.page_count,
           "v": ENGINE_VERSION, "profile": prof.sha256 or "builtin"}
    if cache and cache.exists():
        try:
            data = json.loads(cache.read_text())
            if data.get("key") == key:
                pages = data["pages"]
                promoted = resolve_uncertain(pages, prof)
                log(f"  index cached: {promoted} uncertain header(s) promoted to monograph by context")
                return pages
        except Exception:
            pass
    log(f"indexing {book.page_count} sheets (once; cached at {cache.name if cache else 'nowhere'}) …")
    t0 = time.time()
    pages = [index_page(page, prof) for page in book]
    if cache:
        cache.write_text(json.dumps({"key": key, "pages": pages}))
    promoted = resolve_uncertain(pages, prof)
    mono = sum(1 for pg in pages for a in pg["anchors"] if a["kind"] == "monograph")
    rel = sum(1 for pg in pages for a in pg["anchors"] if a["kind"] == "related")
    unc = sum(1 for pg in pages for a in pg["anchors"] if a.get("uncertain"))
    log(f"  indexed in {time.time() - t0:.0f}s: {mono} monograph headers, {rel} related sub-entries "
        f"({unc} uncertain, {promoted} promoted to monograph by context), "
        f"{sum(1 for pg in pages if pg['chapterBreak'])} chapter-break sheets")
    return pages


def add_known_starts(index: list[dict], sheets: set[int], prof: Profile) -> int:
    """A start the OCR missed entirely gets a synthetic top-of-page anchor."""
    added = 0
    for s in sheets:
        if 0 <= s < len(index) and not index[s]["anchors"]:
            index[s]["anchors"].append({"y": prof.header_band, "x0": 0.0, "name": "", "kind": "monograph", "col": "full",
                                        "titleTop": prof.header_band, "linesAbove": 0, "method": "top",
                                        "bodyAbove": 0, "titleSize": 0.0, "titleText": "", "uncertain": False,
                                        "synthetic": True, "ord": 0, "bandTop": None, "bandBottom": None})
            added += 1
    return added


# ── headings mode ────────────────────────────────────────────────────────────
# A book with no header label blocks (Maciocia): every entry is introduced by a bare
# heading. The entries list says which heading sits on which sheet; the engine finds
# the heading's lines and turns them into the same anchor dict the label path makes,
# so plan()/cut_rects()/render need not know the difference.

def locate_heading(lines: list[Line], heading: str, w: float, prof: Profile, min_size: float | None = None,
                   match: float | None = None) -> dict | None:
    """The heading on this sheet: the best window of 1–3 consecutive big lines (≥
    heading_min_size, one column, line tops within heading_wrap_gap — smaller lines in
    between are skipped, so an interleaved margin title cannot break a wrapped heading)
    whose joined text matches; a line below the size floor matches only exactly.
    Ties go to the bigger type, then the higher line — a pattern's name repeated as a
    smaller cross-reference item ("Pathological developments → Lung Dryness") loses to
    its real heading. With `min_size` and `match` (the leak check: the anchor's own type
    size and 1.0) only lines that big count, the joined text must be the located text
    itself, and the exact-match fallback is off: a repetition in smaller type is not a
    leak, and "Collapse of Yin" is not "Collapse of Yang" (difflib says 0.96)."""
    key = norm(heading)
    if not key:
        return None
    best: tuple[float, float, float, list[Line]] | None = None

    def consider(window: list[Line], ratio: float) -> None:
        nonlocal best
        cand = (ratio, max(ln[5] for ln in window), -window[0][0], window)
        if best is None or cand[:3] > best[:3]:
            best = cand

    floor = prof.heading_min_size if min_size is None else min_size
    threshold = prof.heading_match if match is None else match
    # a heading that STARTS a page has its top inside the header band (like a Chen & Chen title)
    big = [ln for ln in lines if ln[5] >= floor and ln[0] >= prof.title_min_y]
    columns = [[ln for ln in big if is_left(ln[2], w, prof)], [ln for ln in big if not is_left(ln[2], w, prof)]]
    for col in columns:
        for i, first in enumerate(col):
            window = [first]
            for j in range(i, min(i + 3, len(col))):
                if j > i:
                    if col[j][0] - window[-1][0] > prof.heading_wrap_gap:
                        break
                    window.append(col[j])
                r = difflib.SequenceMatcher(None, norm(" ".join(ln[4] for ln in window)), key).ratio()
                if r >= threshold:
                    consider(list(window), r)
    if best is None and min_size is None:
        for ln in lines:
            if ln[0] >= prof.title_min_y and norm(ln[4]) == key:
                consider([ln], 1.0)
    if best is None:
        return None
    window = best[3]
    return {"y": window[0][0], "y1": max(ln[1] for ln in window), "x0": min(ln[2] for ln in window),
            "x1": max(ln[3] for ln in window), "size": best[1], "ratio": best[0],
            "text": " ".join(ln[4] for ln in window)}


def heading_anchor(lines: list[Line], loc: dict, name: str, w: float, h: float, prof: Profile) -> dict:
    """An anchor for a located heading, in the shape page_anchors() makes. The start cut
    is heading_pad above the heading's top, clamped into the gap below the previous line
    of the same column (a section banner may sit 1–6pt above a heading — a fixed pad would
    eat it, and MuPDF removes every glyph a redaction rectangle touches). linesAbove /
    bodyAbove count in READING order — a right-column heading has the whole left column
    before it — so plan() sees a mid-page start where there is one."""
    y, x0 = loc["y"], loc["x0"]
    full = loc["x1"] - x0 > prof.full_width_ratio * w
    col = "full" if full else ("left" if is_left(x0, w, prof) else "right")
    content = [ln for ln in lines if prof.header_band <= ln[0] < h - prof.footer_band]
    ours = [ln for ln in content if ln[0] < y and (col == "full" or same_column(ln, x0, w, prof))]
    prev_bottom = max([prof.header_band] + [ln[1] for ln in ours if ln[1] <= y])
    cut = y - prof.heading_pad
    if cut < prev_bottom:
        cut = (prev_bottom + y) / 2
    cut = min(max(prof.header_band, cut), y)      # a page-top heading: the cut is its own top, never below it
    if col == "right":
        above = [ln for ln in content if is_left(ln[2], w, prof) or (same_column(ln, x0, w, prof) and ln[0] < cut)]
    else:
        above = [ln for ln in content if ln[0] < cut and (col == "full" or same_column(ln, x0, w, prof))]
    return {"y": round(y, 1), "x0": round(x0, 1), "name": name, "kind": "monograph", "col": col,
            "titleTop": round(cut, 1), "linesAbove": len(above),
            "bodyAbove": sum(1 for ln in above if ln[5] < prof.body_max_size),
            "method": "heading", "titleSize": round(loc["size"], 1), "titleText": loc["text"][:80],
            "bigTexts": [], "bigTop": round(y, 1), "uncertain": False}


def add_heading_anchors(index: list[dict], book, refs: list[tuple[str, str, int]], prof: Profile) -> tuple[int, list[str]]:
    """Plant one anchor per (name, heading, printed page). A heading that is not on its
    sheet — or an entry given without one — gets a named synthetic top-of-page anchor
    (a whole-page start); only the former is reported, as `method: heading-missing`.
    Returns (located, names not found)."""
    by_sheet: dict[int, list[tuple[str, str]]] = {}
    for name, heading, page in refs:
        by_sheet.setdefault(page + prof.sheet_offset - 1, []).append((name, heading))
    located, missing = 0, []
    for s, items in sorted(by_sheet.items()):
        if not (0 <= s < len(index)):
            missing.extend(n for n, _ in items)
            continue
        page = book[s]
        lines = page_lines(page)
        w, h = page.rect.width, page.rect.height
        for name, heading in items:
            loc = locate_heading(lines, heading, w, prof) if heading else None
            if loc:
                index[s]["anchors"].append(heading_anchor(lines, loc, name, w, h, prof))
                located += 1
            else:
                if heading:
                    missing.append(name)
                index[s]["anchors"].append({
                    "y": prof.header_band, "x0": 0.0, "name": name, "kind": "monograph", "col": "full",
                    "titleTop": prof.header_band, "linesAbove": 0, "bodyAbove": 0,
                    "method": "heading-missing" if heading else "page", "titleSize": 0.0,
                    "titleText": heading[:80], "bigTexts": [], "bigTop": 0.0, "uncertain": False,
                    "synthetic": bool(heading)})
        finalize_anchors(index[s]["anchors"])
    return located, missing
