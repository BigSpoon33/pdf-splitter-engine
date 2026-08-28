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


def page_anchors(lines: list[Line], w: float, prof: Profile) -> list[dict]:
    raw: list[tuple[float, float, str, str]] = []
    for ln in lines:
        m = prof.label_name_re.search(ln[4])
        if m:
            raw.append((ln[0], ln[2], "name", m.group(1).strip()))
        elif any(rx.search(ln[4]) for rx in prof.label_secondary_res):
            raw.append((ln[0], ln[2], "secondary", ""))
    # The label lines of one header block sit within cluster_gap; collapse them.
    anchors: list[dict] = []
    for y, x0, _kind, name in raw:
        if anchors and y - anchors[-1]["y"] <= prof.cluster_gap and is_left(x0, w, prof) == is_left(anchors[-1]["x0"], w, prof):
            if not anchors[-1]["name"] and name:
                anchors[-1]["name"] = name
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
