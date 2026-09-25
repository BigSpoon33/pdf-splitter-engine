# Docs: docs/tooling/monograph-splitter.md
"""
Section proposals for an arbitrary book (web mode): the PDF outline, or the lines
set in type noticeably bigger than the body. Both return rows `Book.open(entries=…)`
takes unchanged under a `profile_from_dict` profile: `page` is the 1-based sheet
number, `heading` the text `locate_heading` looks for on that sheet.

Pure: nothing is written, the document is not modified, and every sheet's text is
read once (`index.page_lines`).
"""

from __future__ import annotations

import math
import re
from collections import Counter

from .classify import Line
from .index import page_lines
from .profile import Profile

# "1 Introduction", "1.2.3 Scope", "2. Methods", "IV. Results", "iv) Notes" — an outline
# title carries its number, the heading on the page often does not.
_LEADING_NUMBER = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?|[ivxlcdm]+[.)])\s+", re.I)
# "12", "- 12 -", "Page 3" are folios wherever they sit; "xiv" only when it is a well-formed
# numeral AT the page edge — "C", "Mix" and "Dill" mid-page are headings, not folios.
_DIGIT_PAGE = re.compile(r"^\W*(?:page\s*)?\d+\W*$", re.I)
_ROMAN_PAGE = re.compile(r"^\W*(?:page\s*)?(?=[ivxlcdm])m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})\W*$", re.I)
_REF = re.compile(r"^\s*(\d+)\s+\d+\s+R\s*$")                # an indirect object reference, "16 0 R"
MAX_REF_HOPS = 4          # a destination reference chain in a broken file must not loop forever
_DEST_TOP = {"XYZ": 1, "FitH": 0, "FitBH": 0, "FitR": 3}   # index of `top` among each view's operands
RUNNING_SHARE = 0.3       # a line repeated on this share of pages at the page edge is running matter
EDGE_SHARE = 0.12         # "the page edge" when the caller's bands are narrower than this × H
LEVEL_TOLERANCE = 0.5     # sizes within this many points are one heading level
MAX_WRAP_LINES = 3        # locate_heading joins at most three lines; a bigger block is display prose


def _clean(s: str) -> str:
    return " ".join(s.split())


# ── outline ──────────────────────────────────────────────────────────────────

def _dest_value(doc, xref: int, key: str) -> tuple[str, str]:
    """`xref_get_key` with indirect destinations followed: `/D 16 0 R` (or `/Dest 16 0 R`)
    points at the destination array itself, or at a dict that holds it under /D."""
    for _ in range(MAX_REF_HOPS):
        kind, value = doc.xref_get_key(xref, key)
        if kind != "xref":
            return kind, value
        m = _REF.match(value)
        if not m:
            break
        xref = int(m.group(1))
        obj = doc.xref_object(xref, compressed=True).strip()
        if obj.startswith("["):
            return "array", obj
        key = "D"
    return "null", "null"


def _dest_top(doc, item: list, names: dict) -> float | None:
    """The destination's top in page coordinates (y down), or None when the file names
    no point. PyMuPDF reports /Fit and `/XYZ null null` as (0, 0) and a named
    destination's point unconverted (y up), so the raw destination decides."""
    dest, sheet = item[3], item[2] - 1
    xref = dest.get("xref", 0)
    raw = None
    for key in ("A/D", "Dest"):
        kind, value = _dest_value(doc, xref, key) if xref else ("null", "null")
        if kind == "array":
            raw = value
            break
        if kind in ("string", "name"):
            # a named destination: resolve_names gives the point in PDF space; a point at
            # y = 0 is how it reports "no point" (and no heading starts at the page's bottom edge)
            if not names:
                names.update(doc.resolve_names())
            named = names.get(value.lstrip("/"), {})
            y_up = (named.get("to") or (0.0, 0.0))[1]
            return _to_page_y(doc, sheet, y_up) if y_up else None
    if raw is None:
        return None
    m = re.search(r"/(XYZ|FitH|FitBH|FitR)\b([^\]]*)", raw)
    if not m:
        return None
    operands = m.group(2).split()
    i = _DEST_TOP[m.group(1)]
    if i >= len(operands):
        return None
    try:
        return _to_page_y(doc, sheet, float(operands[i]))
    except ValueError:            # `null`: keep the current view — no point
        return None


def _to_page_y(doc, sheet: int, y_up: float) -> float:
    import fitz

    page = doc[sheet]
    y = (fitz.Point(0, y_up) * page.transformation_matrix).y
    return round(min(max(y, 0.0), page.rect.height), 1)


def _outline(doc) -> list[list]:
    """Outline items that land on a sheet of this document (a broken or external link
    has page -1) and have a title."""
    n = doc.page_count
    return [it for it in doc.get_toc(simple=False) if 1 <= it[2] <= n and _clean(it[1])]


def outline_levels(doc) -> list[dict]:
    """[{level, count}] per outline depth, for the level picker; no outline → []."""
    counts = Counter(it[0] for it in _outline(doc))
    return [{"level": lvl, "count": counts[lvl]} for lvl in sorted(counts)]


def outline_entries(doc, level: int) -> list[dict]:
    """Every outline item at exactly `level`, in outline order: {name, page, heading,
    level, y?}. `name` is the title, `heading` the title without its leading number,
    `y` the destination's top when the file names a point (a writer's default point
    such as PyMuPDF's (72, 36) is indistinguishable from a real one and is kept)."""
    rows: list[dict] = []
    names: dict = {}              # resolved once, on the first named destination
    for it in _outline(doc):
        if it[0] != level:
            continue
        name = _clean(it[1])
        row = {"name": name, "page": it[2], "heading": _LEADING_NUMBER.sub("", name) or name, "level": level}
        y = _dest_top(doc, it, names)
        if y is not None:
            row["y"] = y
        rows.append(row)
    return rows


# ── big headings ─────────────────────────────────────────────────────────────

def body_size(sheets: list[list[Line]]) -> float:
    """The char-weighted modal type size: votes in 0.5 pt bins, then the char-weighted
    mean inside the winning bin (a text layer rarely repeats a size to the last digit)."""
    votes: Counter = Counter()
    for lines in sheets:
        for ln in lines:
            votes[round(ln[5] * 2) / 2] += len(ln[4])
    if not votes:
        return 0.0
    mode = max(votes, key=lambda b: (votes[b], -b))
    chars = [(ln[5], len(ln[4])) for lines in sheets for ln in lines if round(ln[5] * 2) / 2 == mode]
    return round(sum(s * n for s, n in chars) / sum(n for _, n in chars), 2)


def _running_key(ln: Line) -> tuple[str, float]:
    return re.sub(r"\d+", "#", _clean(ln[4]).lower()), round(ln[5] * 2) / 2


def _at_edge(ln: Line, h: float, header_band: float, footer_band: float) -> bool:
    """In the page-edge strip, at least EDGE_SHARE × H deep at either end, so a band
    set too thin still covers the running matter and the folios."""
    return ln[0] < max(header_band, EDGE_SHARE * h) or ln[0] >= h - max(footer_band, EDGE_SHARE * h)


def _running(sheets: list[tuple[float, list[Line]]], header_band: float, footer_band: float) -> set:
    """Lines repeated (digits aside) at the page edge of ≥ RUNNING_SHARE of the pages:
    running headers and footers. The key includes the size, so a chapter title set big
    at the top of its first page is not its own small running header."""
    seen: Counter = Counter()
    for h, lines in sheets:
        seen.update({_running_key(ln) for ln in lines if _at_edge(ln, h, header_band, footer_band)})
    need = max(2, math.ceil(RUNNING_SHARE * len(sheets)))
    return {k for k, n in seen.items() if n >= need}


def _page_number(ln: Line, h: float, header_band: float, footer_band: float) -> bool:
    return bool(_DIGIT_PAGE.match(ln[4])) or (bool(_ROMAN_PAGE.match(ln[4])) and _at_edge(ln, h, header_band, footer_band))


def _full(ln: Line, w: float, full_width_ratio: float) -> bool:
    return ln[3] - ln[2] > full_width_ratio * w


def _side(ln: Line, w: float, column_split: float) -> str:
    return "left" if ln[2] < column_split * w else "right"


def _reading_order(cands: list[dict]) -> list[dict]:
    """index.finalize_anchors' order: full-width headings split the page into bands,
    the left column reads before the right one inside a band."""
    fulls = [c["y"] for c in cands if c["col"] == "full"]
    colk = {"left": 0, "right": 1, "full": 2}
    return sorted(cands, key=lambda c: (sum(1 for fy in fulls if fy < c["y"]), colk[c["col"]], c["y"]))


def heading_candidates(doc, *, min_ratio: float = 1.3, max_len: int = 90,
                       header_band: float = Profile.header_band, footer_band: float = Profile.footer_band,
                       wrap_gap: float = Profile.heading_wrap_gap, column_split: float = Profile.column_split,
                       full_width_ratio: float = Profile.full_width_ratio) -> dict:
    """Lines set at ≥ body × min_ratio, outside the header/footer bands, as section
    proposals: {body_size, levels: [{size, count}], candidates: [{name, page, heading,
    size, level, y, col}]}. A heading wrapped over up to three lines (starting on the
    same side of column_split — locate_heading's grouping, so a line that crosses the
    gutter still joins the narrower line under it — tops within wrap_gap, sizes within
    LEVEL_TOLERANCE) is one candidate; the joined text must be ≤ max_len chars. Running
    headers/footers and page-number lines never count. Levels cluster the candidates'
    sizes, largest = 1. Pass the web settings' geometry (`single_column` →
    column_split 0.999, full_width_ratio 0) so `col` agrees with the cuts."""
    sheets: list[tuple[float, float, list[Line]]] = []
    for page in doc:
        sheets.append((page.rect.width, page.rect.height, page_lines(page)))
    body = body_size([lines for _, _, lines in sheets])
    if body <= 0:
        return {"body_size": 0.0, "levels": [], "candidates": []}
    running = _running([(h, lines) for _, h, lines in sheets], header_band, footer_band)
    floor = body * min_ratio

    groups: list[tuple[int, float, list[Line]]] = []
    for sheet, (w, h, lines) in enumerate(sheets):
        big = [ln for ln in lines
               if ln[5] >= floor and header_band <= ln[0] < h - footer_band
               and not _page_number(ln, h, header_band, footer_band) and _running_key(ln) not in running]
        open_: list[list[Line]] = []
        for ln in big:
            side = _side(ln, w, column_split)
            host = next((g for g in reversed(open_)
                         if _side(g[0], w, column_split) == side
                         and ln[0] - g[-1][0] <= wrap_gap and abs(ln[5] - g[0][5]) <= LEVEL_TOLERANCE), None)
            if host is not None:
                host.append(ln)
            else:
                open_.append([ln])
        groups.extend((sheet, w, g) for g in open_)

    cands: list[dict] = []
    for sheet, w, g in groups:
        text = _clean(" ".join(ln[4] for ln in g))
        if len(g) > MAX_WRAP_LINES or len(text) > max_len:
            continue
        col = "full" if any(_full(ln, w, full_width_ratio) for ln in g) else _side(g[0], w, column_split)
        cands.append({"name": text, "page": sheet + 1, "heading": text, "size": round(max(ln[5] for ln in g), 2),
                      "y": round(g[0][0], 1), "col": col})

    tops: list[float] = []            # each level's largest size
    for s in sorted({c["size"] for c in cands}, reverse=True):
        if not tops or tops[-1] - s > LEVEL_TOLERANCE:
            tops.append(s)
    counts: Counter = Counter()
    for c in cands:
        c["level"] = next(i for i, t in enumerate(tops, 1) if t - c["size"] <= LEVEL_TOLERANCE)
        counts[c["level"]] += 1
    by_page: dict[int, list[dict]] = {}
    for c in cands:
        by_page.setdefault(c["page"], []).append(c)
    ordered = [c for p in sorted(by_page) for c in _reading_order(by_page[p])]
    return {"body_size": body, "levels": [{"size": t, "count": counts[i]} for i, t in enumerate(tops, 1)],
            "candidates": [{k: c[k] for k in ("name", "page", "heading", "size", "level", "y", "col")} for c in ordered]}
