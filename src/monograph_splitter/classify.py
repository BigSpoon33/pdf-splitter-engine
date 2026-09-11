# Docs: docs/tooling/monograph-splitter.md
"""
Text-level classification: is this line a script (Chinese) title line, is this
label block a real entry or a sub-entry, where does its title block start.
Pure functions over `Line` tuples and a Profile — no PDF access here.
"""

from __future__ import annotations

import difflib
import re
import unicodedata

from .profile import Profile

Line = tuple[float, float, float, float, str, float]   # y0, y1, x0, x1, text, size

UNCERTAIN_MATCH = 0.7   # engine internal: "this big line repeats the current entry's name"


def norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


def is_script_line(t: str, prof: Profile) -> bool:
    """A script title line: mostly script glyphs. OCR-garbled Latin titles
    ("δì Nì 丁ang") carry one or two stray glyphs — those must not count.
    Single-glyph lines DO count: that is what the OCR makes of a big title it
    could not group ("贯" / "前" / "一")."""
    rx = prof.script_re
    if rx is None:
        return False
    chars = [c for c in t if not c.isspace()]
    n = sum(1 for c in chars if rx.match(c))
    return n >= 1 and n >= prof.script_ratio * len(chars)


def latin_key(t: str) -> str:
    """OCR'd title → comparable letters: 'Dà Qín Jíao Tang (Major…)' → 'daqinjiaotang'."""
    t = t.split("(")[0]
    t = unicodedata.normalize("NFKD", t)
    return re.sub(r"[^a-z]", "", "".join(c for c in t if not unicodedata.combining(c)).lower())


def name_ratio(entry: str, name: str, title: str) -> float:
    key = norm(entry)
    r = difflib.SequenceMatcher(None, norm(name), key).ratio() if name else 0.0
    if title:
        r = max(r, difflib.SequenceMatcher(None, latin_key(title), key).ratio())
    return r


# ── geometry ────────────────────────────────────────────────────────────────

def is_left(x0: float, w: float, prof: Profile) -> bool:
    return x0 < prof.column_split * w


def same_column(ln: Line, x0: float, w: float, prof: Profile) -> bool:
    if ln[3] - ln[2] > prof.full_width_ratio * w:
        return True                      # full-width line belongs to both
    return is_left(ln[2], w, prof) == is_left(x0, w, prof)


def gap_cut(above: list[Line], y: float, prof: Profile, all_lines: list[Line] | None = None) -> float | None:
    """Largest vertical gap within gap_window above a label — the space above an
    entry's title block. Gaps are measured between consecutive lines, starting
    from the bottom of EVERY line above the window (two-column pages interleave
    lines by y, and a running sub-header occupies the band — 'nothing between
    the header band and the window' is never evidence of a gap)."""
    window = [ln for ln in above if y - prof.gap_window < ln[0] < y]
    if not window:
        return None
    if all_lines is None:
        all_lines = above
    prev_bottom = max([prof.header_band] + [ln[1] for ln in all_lines if ln[0] <= y - prof.gap_window])
    best: tuple[float, float, float] | None = None
    for ln in window:
        cand = (ln[0] - prev_bottom, prev_bottom, ln[0])
        if best is None or cand > best:
            best = cand
        prev_bottom = max(prev_bottom, ln[1])
    if best is None or best[0] < prof.min_gap:
        return None
    return (best[1] + best[2]) / 2


def header_geometry(lines: list[Line], y: float, x0: float, w: float, prof: Profile) -> dict:
    """Classify a header block (first label line at y, x0) and find where it starts."""
    above = [ln for ln in lines if prof.header_band <= ln[0] < y]
    reach = [ln for ln in lines if prof.title_min_y <= ln[0] < y and y - prof.title_reach < ln[0]]
    big = [ln for ln in reach if ln[5] >= prof.big_min_size]
    # The script title lines are the discriminator: a real entry prints them big
    # (14–18pt in Chen & Chen), a sub-entry small (8.5pt), a running sub-header
    # has none. Latin titles OCR at anything from 13.6 to 24pt — not reliable.
    script_big = [ln for ln in reach if is_script_line(ln[4], prof) and ln[5] >= prof.script_min_size]
    # …or the title shattered into per-glyph lines: big_lines_min big lines never
    # happen otherwise (a continuation page has exactly one — the sub-header).
    title: Line | None = (
        min(big + script_big, key=lambda ln: ln[0]) if (script_big or (big and len(big) >= prof.big_lines_min)) else None
    )
    if title is None and prof.big_lines_min == 0:
        # big_lines_min = 0: this book has no sub-entries — every label block is an entry, and
        # its title is whatever sits just above the labels in its column (or the label line itself)
        near = [ln for ln in reach if same_column(ln, x0, w, prof)]
        title = max(near, key=lambda ln: ln[0]) if near else next(ln for ln in lines if ln[0] == y and ln[2] == x0)
    if title is not None:
        cut = gap_cut(above, y, prof, lines)
        method = "gap"
        if cut is None:
            cut, method = title[0] - 4, "title"
        kind, size = "monograph", title[5]
        # a Chen & Chen title spans both columns; a Bensky Materia Medica entry starts inside one
        col = "full" if prof.title_col == "full" else ("left" if is_left(x0, w, prof) else "right")
    else:
        col_lines = [ln for ln in above if same_column(ln, x0, w, prof)]
        col = "left" if is_left(x0, w, prof) else "right"
        rel = [ln for ln in col_lines if prof.related_re.search(ln[4]) and y - prof.related_reach < ln[0]]
        if rel:
            cut, method = rel[-1][0] - 2, "related-heading"
        else:
            blk = [ln for ln in col_lines if y - prof.block_reach < ln[0]]
            if blk:
                cut, method = min(ln[0] for ln in blk) - 2, "block"
            else:
                cut, method = max(prof.header_band, y - prof.estimate_above), "estimate"
        kind, size = "related", (max(ln[5] for ln in big) if big else 0.0)
    cut = max(prof.header_band, cut)
    above_cut = [ln for ln in lines if prof.header_band <= ln[0] < cut and (col == "full" or same_column(ln, x0, w, prof))]
    n_above = len(above_cut)
    # What is above the cut decides whether that page-top is a Section banner
    # (big type only — keep it, or hand the page to the next entry) or a real
    # tail of the previous entry (body text — cut it / keep the page).
    body_above = sum(1 for ln in above_cut if ln[5] < prof.body_max_size)
    # the title text (Latin, accents and all) — a second way to recognise OUR
    # header when the name label line was mangled
    if title is not None:
        title_text = " ".join(ln[4] for ln in reach if ln[0] >= title[0] and not is_script_line(ln[4], prof) and ln[5] >= prof.big_min_size)
    else:
        blk = [ln for ln in lines if same_column(ln, x0, w, prof) and y - prof.block_reach < ln[0] < y and not is_script_line(ln[4], prof)]
        title_text = blk[0][4] if blk else ""
    return {"kind": kind, "col": col, "titleTop": round(cut, 1), "linesAbove": n_above, "bodyAbove": body_above,
            "method": method, "titleSize": round(size, 1), "titleText": title_text[:80],
            "bigTexts": [latin_key(ln[4]) for ln in big][:4],
            "bigTop": round(min((ln[0] for ln in big), default=0.0), 1),
            # a big Latin line with NO script line after it before the label: the OCR
            # may have eaten an entry's script title — a human should look
            "uncertain": kind == "related" and any(
                not any(is_script_line(m[4], prof) and b[0] < m[0] < y for m in reach) for b in big)}
