"""STORY-016: a section spanning sheets of different sizes is cut in each sheet's own geometry."""
from pathlib import Path

import fitz
import pytest

from monograph_splitter.cuts import cut_rects
from monograph_splitter.index import page_lines
from monograph_splitter.profile import load_profile
from monograph_splitter.render import render_review, write_excerpt
from monograph_splitter.session import Book
from monograph_splitter.verify import verify_headings
from tests.fixtures import H, H2, W, W2, mixed_size_book

HEADINGS_PROFILE = Path(__file__).parent / "profile-headings.toml"
QUIET = {"log": lambda *_: None}


@pytest.fixture(scope="module")
def mixed(tmp_path_factory):
    d = tmp_path_factory.mktemp("mixed")
    info = mixed_size_book(d / "book.pdf")
    prof = load_profile(HEADINGS_PROFILE)
    book = Book.open(pdf=Path(info["pdf"]), out=d / "out", profile=prof, entries=info["entries"], **QUIET)
    alpha = next(e for e in book.entries if e.name == "Alpha Pattern")
    return {"dir": d, "book": book, "prof": prof, "alpha": alpha, "plan": book.planned(alpha)}


def sheet_size(page):
    return round(page.rect.width, 1), round(page.rect.height, 1)


def content_lines(page, prof):
    return [ln for ln in page_lines(page) if prof.header_band <= ln[0] < page.rect.height - prof.footer_band]


def test_the_index_and_the_pages_agree_on_every_sheets_size(mixed):
    book = mixed["book"]
    assert book.page_size(0) == (round(W, 1), round(H, 1)) and book.page_size(1) == (W2, H2)
    assert [sheet_size(pg) for pg in book.doc] == [(round(W, 1), round(H, 1)), (W2, H2)]
    p = mixed["plan"]
    assert (p["sheet0"], p["sheet1"]) == (0, 1) and p["startCut"] is None
    assert p["endCol"] == "right" and p["endCut"] is not None and p["flags"] == []


def test_the_fixture_really_straddles_the_two_gutters(mixed):
    """The tail lines cross the narrow sheet's gutter but not the wide one's, and Beta's
    body runs past the narrow sheet's right edge — otherwise the old geometry would pass."""
    book, prof = mixed["book"], mixed["prof"]
    narrow_split, wide_split = prof.column_split * W, prof.column_split * W2
    tail = [ln for ln in content_lines(book.doc[1], prof) if ln[4].startswith("alpha tail")]
    beta = [ln for ln in content_lines(book.doc[1], prof) if ln[4].startswith("beta body")]
    assert len(tail) == 12 and all(narrow_split < ln[3] < wide_split for ln in tail)
    assert len(beta) == 20 and all(ln[2] > wide_split and ln[3] > W for ln in beta)


def test_the_end_cut_is_placed_in_the_last_sheets_own_geometry(mixed):
    book, prof, p = mixed["book"], mixed["prof"], mixed["plan"]
    wide_split = prof.column_split * W2
    assert book.rects(p) == [{"sheet": 1, "rect": [round(wide_split, 1), p["endCut"], W2, H2 - prof.footer_band]}]
    # the old call shape (one size for every sheet) still answers in that size — the uniform books' contract
    assert cut_rects(p, W, H, prof) == [(1, (prof.column_split * W, p["endCut"], W, H - prof.footer_band))]
    assert cut_rects(p, W, H, prof) == cut_rects(p, W, H, prof, last_size=(W, H))
    # a start cut keeps the FIRST sheet's geometry while the end cut takes the last sheet's
    q = dict(p, startCut=120.0, startCol="left", startBand=[None, None])
    rects = cut_rects(q, W, H, prof, last_size=(W2, H2))
    assert (0, (0, prof.redact_top, prof.column_split * W, 120.0)) in rects
    assert (1, (wide_split, p["endCut"], W2, H2 - prof.footer_band)) in rects
    assert all(r[2] <= W for i, r in rects if i == 0) and all(r[2] == W2 for i, r in rects if i == 1)


def test_the_written_excerpt_keeps_alphas_tail_whole_and_drops_betas_column(mixed):
    d, book, prof, p = mixed["dir"], mixed["book"], mixed["prof"], mixed["plan"]
    dest = d / "Alpha Pattern.pdf"
    write_excerpt(book.doc, p, dest, True, prof)
    out = fitz.open(dest)
    assert [sheet_size(pg) for pg in out] == [(round(W, 1), round(H, 1)), (W2, H2)]
    # what write_excerpt applied is what Book.rects showed: the same per-sheet sizes
    applied = cut_rects(p, out[0].rect.width, out[0].rect.height, prof, last_size=(out[-1].rect.width, out[-1].rect.height))
    assert [{"sheet": i, "rect": [round(v, 1) for v in r]} for i, r in applied] == book.rects(p)
    last = content_lines(out[-1], prof)
    wide_split = prof.column_split * W2
    assert [ln[4] for ln in last] == [f"alpha tail {i} of the running prose that reaches across the narrow gutter" for i in range(12)]
    assert all(ln[3] < wide_split for ln in last)
    text = "".join(pg.get_text() for pg in out)
    assert "Beta Pattern" not in text and "beta body" not in text
    assert "Clinical manifestations" not in out[-1].get_text()          # Beta's sub-heading; Alpha's own is on p1
    assert "Chapter 1 - Test Formulas" in out[-1].get_text() and "\n2\n" in out[-1].get_text()
    assert verify_headings(dest, "Alpha Pattern", p, book.index, prof) == []
    out.close()


def test_cut_all_reports_no_leak_on_the_mixed_book(mixed):
    book = mixed["book"]
    summary = book.cut_all(verify=True, preview=True)
    assert summary["leaks"] == {} and summary["missing"] == []
    row = book.manifest["Alpha Pattern"]
    assert row["leaks"] == [] and "leak" not in row["flags"] and row["printedPages"] == [1, 2]
    assert row["review"] == ["Alpha-Pattern-first.png", "Alpha-Pattern-last.png"]


def test_review_pngs_are_drawn_in_each_sheets_size(mixed):
    d, book, prof, p = mixed["dir"], mixed["book"], mixed["prof"], mixed["plan"]
    rv = d / "review"
    rv.mkdir(exist_ok=True)
    render_review(book.doc, p, "mixed", rv, prof, dpi=72)
    first, last = fitz.open(rv / "mixed-first.png"), fitz.open(rv / "mixed-last.png")
    assert (first[0].rect.width, first[0].rect.height) == (round(W), round(H))
    assert (last[0].rect.width, last[0].rect.height) == (W2, H2)
