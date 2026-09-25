"""Web mode (STORY-002): section proposals from the PDF outline and from big headings."""
from pathlib import Path

import fitz
import pytest

from monograph_splitter import detect
from monograph_splitter.profile import profile_from_dict
from monograph_splitter.session import Book
from tests.fixtures import H, W, headed_book, single_column_book


@pytest.fixture(scope="module")
def book(tmp_path_factory):
    d = tmp_path_factory.mktemp("detect")
    info = headed_book(d / "book.pdf")
    return d, info, fitz.open(info["pdf"])


@pytest.fixture(scope="module")
def single(tmp_path_factory):
    d = tmp_path_factory.mktemp("single")
    info = single_column_book(d / "book.pdf")
    return d, info, fitz.open(info["pdf"])


def _blank(n: int, w: float = W, h: float = H) -> fitz.Document:
    doc = fitz.open()
    for _ in range(n):
        doc.new_page(width=w, height=h)
    return doc


# ── outline ─────────────────────────────────────────────────────────────────

def test_outline_levels_count_items_per_depth_and_an_outline_less_pdf_has_none(book):
    _, _, doc = book
    # '3.1 Final Section' is a URI (page -1): it lands on no sheet and is not counted
    assert detect.outline_levels(doc) == [{"level": 1, "count": 3}, {"level": 2, "count": 3}]
    assert detect.outline_levels(_blank(2)) == []
    assert detect.outline_entries(_blank(2), 1) == []


def test_outline_entries_are_rows_at_exactly_one_level_with_1_based_sheets(book):
    _, info, doc = book
    rows = detect.outline_entries(doc, 1)
    assert [(r["name"], r["page"], r["heading"], r["level"]) for r in rows] == [
        ("1 Foundations of Testing", 1, "Foundations of Testing", 1),
        ("2 Chapter Two: The Middle of the Synthetic Book", 3, "Chapter Two: The Middle of the Synthetic Book", 1),
        ("3 Closing Chapter", 4, "Closing Chapter", 1),
    ]
    assert [r["heading"] for r in rows] == [c["name"] for c in info["chapters"]]
    assert [r["name"] for r in detect.outline_entries(doc, 2)] == [
        "1.1 First Principles", "1.2 Second Principles of Wrapped Section Headings", "2.1 Middle Matters"]
    assert detect.outline_entries(doc, 3) == []


def test_outline_y_is_the_destination_top_only_when_the_file_names_a_point(book):
    _, _, doc = book
    ys = {r["name"]: r.get("y") for lvl in (1, 2) for r in detect.outline_entries(doc, lvl)}
    assert ys["1 Foundations of Testing"] == 70.0                    # an explicit point, page coordinates (y down)
    assert ys["3 Closing Chapter"] == 400.0                          # a named destination: PDF space (y up) converted
    # set_toc without a dest writes /XYZ 72 H-36: a real point in the file, kept as written
    assert ys["2 Chapter Two: The Middle of the Synthetic Book"] == 36.0
    assert ys["2.1 Middle Matters"] == 36.0
    # /Fit and /XYZ null null name no point: no y (PyMuPDF reports both as (0, 0))
    rows = {r["name"]: r for r in detect.outline_entries(doc, 2)}
    assert "y" not in rows["1.1 First Principles"]
    assert "y" not in rows["1.2 Second Principles of Wrapped Section Headings"]


@pytest.mark.parametrize("title, heading", [
    ("1 Introduction", "Introduction"), ("1.2.3 Scope", "Scope"), ("2. Methods", "Methods"),
    ("IV. Results", "Results"), ("iv) Notes", "Notes"), ("Appendix A", "Appendix A"),
    ("A Study of Things", "A Study of Things"), ("I Ching Basics", "I Ching Basics"), ("12", "12"),
])
def test_outline_headings_lose_only_their_leading_number(title, heading):
    doc = _blank(1)
    doc.set_toc([[1, title, 1]])
    assert detect.outline_entries(doc, 1)[0]["heading"] == heading


def test_outline_y_follows_an_indirect_destination_to_the_same_point_as_a_direct_array():
    doc = _blank(4)
    doc.set_toc([[1, "Direct", 1], [1, "Indirect action D", 2], [1, "Indirect Dest", 3], [1, "Indirect dict", 4]])
    items = {it[1]: it[3]["xref"] for it in doc.get_toc(simple=False)}
    p = [doc[i].xref for i in range(4)]
    doc.xref_set_key(items["Direct"], "A", f"<</S/GoTo/D[{p[0]} 0 R/XYZ 0 {H - 200:.1f} null]>>")
    arr = doc.get_new_xref()
    doc.update_object(arr, f"[{p[1]} 0 R/XYZ 0 {H - 200:.1f} null]")
    doc.xref_set_key(items["Indirect action D"], "A", f"<</S/GoTo/D {arr} 0 R>>")
    arr2 = doc.get_new_xref()
    doc.update_object(arr2, f"[{p[2]} 0 R/FitH {H - 200:.1f}]")
    doc.xref_set_key(items["Indirect Dest"], "A", "null")
    doc.xref_set_key(items["Indirect Dest"], "Dest", f"{arr2} 0 R")
    dct = doc.get_new_xref()                      # a dict holding the array under /D
    doc.update_object(dct, f"<</D[{p[3]} 0 R/XYZ 0 {H - 200:.1f} null]>>")
    doc.xref_set_key(items["Indirect dict"], "A", f"<</S/GoTo/D {dct} 0 R>>")
    assert doc.xref_get_key(items["Indirect action D"], "A/D")[0] == "xref"
    rows = {r["name"]: r for r in detect.outline_entries(doc, 1)}
    assert rows["Direct"]["y"] == 200.0
    assert rows["Indirect action D"]["y"] == 200.0 and rows["Indirect action D"]["page"] == 2
    assert rows["Indirect Dest"]["y"] == 200.0 and rows["Indirect Dest"]["page"] == 3
    # PyMuPDF resolves no page for the dict form (get_toc says -1): the row is dropped, not mis-placed
    assert "Indirect dict" not in rows and detect.outline_levels(doc) == [{"level": 1, "count": 3}]


# ── big headings ────────────────────────────────────────────────────────────

def test_body_size_is_the_char_weighted_mode(book):
    _, _, doc = book
    assert detect.heading_candidates(doc)["body_size"] == 9.5
    # many short 16pt lines lose to fewer, longer 9.5pt lines: characters vote, not lines
    doc2 = _blank(1)
    pg = doc2[0]
    for i in range(12):
        pg.insert_text((50, 60 + 20 * i), "Hd", fontsize=16)
    for i in range(3):
        pg.insert_text((50, 400 + 12 * i), "a much longer line of body prose set small", fontsize=9.5)
    assert detect.heading_candidates(doc2, header_band=0, footer_band=0)["body_size"] == 9.5


def test_candidates_pass_the_size_ratio_the_bands_and_max_len(book):
    _, _, doc = book
    res = detect.heading_candidates(doc)                              # 1.3 × 9.5 = 12.35: sections (12pt) are out
    assert {c["size"] for c in res["candidates"]} == {16.0}
    res = detect.heading_candidates(doc, min_ratio=1.25)              # 11.875: sections are in
    assert {c["size"] for c in res["candidates"]} == {16.0, 12.0}
    names = [c["name"] for c in detect.heading_candidates(doc, min_ratio=1.25, max_len=30)["candidates"]]
    assert "Chapter Two: The Middle of the Synthetic Book" not in names and "Closing Chapter" in names
    # the wrapped section is ONE candidate, its lines joined (27 + 16 chars), so max_len applies to the join
    sec = next(c for c in res["candidates"] if c["page"] == 2)
    assert sec["heading"] == "Second Principles of Wrapped Section Headings" and sec["col"] == "right"
    assert "Second Principles of Wrapped" not in names and "Section Headings" not in names
    # bands: nothing above header_band or below H - footer_band, even at a low ratio
    for c in detect.heading_candidates(doc, min_ratio=1.0)["candidates"]:
        assert 50.0 <= c["y"] < H - 32.0


def test_wrap_gap_decides_whether_lines_join(book):
    _, _, doc = book
    names = [c["name"] for c in detect.heading_candidates(doc, min_ratio=1.25, wrap_gap=10)["candidates"]]
    assert "Second Principles of Wrapped" in names and "Section Headings" in names


WIDE, NARROW = "Differential Diagnosis of the Principal Patterns of Disharmony in", "Clinical Practice"   # 0.65·W / 0.16·W at 13pt


@pytest.mark.parametrize("first, second", [(WIDE, NARROW), (NARROW, WIDE)])
def test_a_wrapped_heading_straddling_the_full_width_threshold_is_one_full_candidate(first, second):
    doc = _blank(1, 612, 792)
    pg = doc[0]
    for i in range(30):
        pg.insert_text((72, 400 + 12 * i), "single column body prose that runs the full text width of the page", fontsize=10)
    pg.insert_text((72, 200), first, fontsize=13, fontname="hebo")
    pg.insert_text((72, 215.6), second, fontsize=13, fontname="hebo")
    # a same-size line on the OTHER side of column_split inside wrap_gap is not part of the wrap
    pg.insert_text((330, 231.2), "Right Aside", fontsize=13, fontname="hebo")
    res = detect.heading_candidates(doc)                              # default two-column geometry
    assert [(c["name"], c["col"], c["level"]) for c in res["candidates"]] == [
        (f"{first} {second}", "full", 1), ("Right Aside", "right", 1)]


def test_single_column_book_chapters_are_one_candidate_each_under_both_geometries(single):
    _, info, doc = single
    prof = profile_from_dict({"single_column": True})
    for geometry in ({}, {"column_split": prof.column_split, "full_width_ratio": prof.full_width_ratio}):
        res = detect.heading_candidates(doc, wrap_gap=24, header_band=0, footer_band=0, **geometry)
        assert res["body_size"] == 10.0
        lvl1 = [c for c in res["candidates"] if c["level"] == 1]
        assert [{k: c[k] for k in ("name", "page")} for c in lvl1] == info["chapters"]
        assert {c["col"] for c in lvl1} == {"full"}                  # each chapter straddles 0.55·W: one line is wide
        assert [{k: c[k] for k in ("name", "page")} for c in res["candidates"] if c["level"] == 2] == info["sections"]
        names = [c["name"] for c in detect.heading_candidates(doc, min_ratio=1.0, header_band=0, footer_band=0, **geometry)["candidates"]]
        assert "The Single Column Reader" not in names and not {"i", "ii", "iii"} & set(names)   # running header, roman folios
    # the chapter lines are 24pt apart: below that wrap_gap they are two candidates
    names = [c["name"] for c in detect.heading_candidates(doc, wrap_gap=16)["candidates"]]
    assert "Identification of Patterns" in names and "according to the Eight Guiding Principles" in names


def test_single_column_candidates_open_a_book_and_every_heading_is_located(single):
    d, info, doc = single
    rows = detect.heading_candidates(doc, wrap_gap=24)["candidates"]
    prof = profile_from_dict({"single_column": True, "heading_wrap_gap": 24})
    b = Book.open(pdf=Path(info["pdf"]), out=d / "cands", profile=prof, entries=rows, log=lambda *_: None)
    plans = {e.name: b.planned(e) for e in b.entries}
    ch, sec = info["chapters"], info["sections"]
    assert list(plans) == [ch[0]["name"], sec[0]["name"], sec[1]["name"], ch[1]["name"]]
    assert all("heading-not-found" not in p["flags"] for p in plans.values()), plans
    assert [p["sheet0"] for p in plans.values()] == [0, 0, 1, 2]


def test_levels_cluster_sizes_largest_first_within_half_a_point():
    doc = _blank(1)
    pg = doc[0]
    for i in range(30):
        pg.insert_text((50, 300 + 12 * i), "body prose line that carries the page", fontsize=9.5)
    for y, s, t in [(80, 18, "Big One"), (120, 17.6, "Big Two"), (160, 14, "Mid One"), (200, 13.6, "Mid Two"), (240, 13.0, "Small")]:
        pg.insert_text((50, y), t, fontsize=s)
    res = detect.heading_candidates(doc)
    assert [(c["name"], c["level"]) for c in res["candidates"]] == [
        ("Big One", 1), ("Big Two", 1), ("Mid One", 2), ("Mid Two", 2), ("Small", 3)]
    assert res["levels"] == [{"size": 18.0, "count": 2}, {"size": 14.0, "count": 2}, {"size": 13.0, "count": 1}]


def test_ac5_level_1_is_the_three_chapters_with_pages_and_cols(book):
    _, info, doc = book
    res = detect.heading_candidates(doc, min_ratio=1.25)
    lvl1 = [c for c in res["candidates"] if c["level"] == 1]
    assert [{k: c[k] for k in ("name", "page", "col")} for c in lvl1] == info["chapters"]
    assert all(c["heading"] == c["name"] and c["size"] == 16.0 for c in lvl1)
    assert [{k: c[k] for k in ("name", "page", "col")} for c in res["candidates"] if c["level"] == 2] == info["sections"]
    assert res["levels"] == [{"size": 16.0, "count": 3}, {"size": 12.0, "count": 4}]
    # the mid-right-column chapter sits below the right column's body top
    assert next(c for c in lvl1 if c["page"] == 4)["y"] > 300
    empty = detect.heading_candidates(doc, min_ratio=16 / 9.5 + 0.01)
    assert empty["candidates"] == [] and empty["levels"] == [] and empty["body_size"] == 9.5


def test_candidates_are_in_reading_order():
    doc = _blank(1)
    pg = doc[0]
    for i in range(40):
        pg.insert_text((53, 200 + 12 * i), "left body prose line", fontsize=9.5)
        pg.insert_text((271, 200 + 12 * i), "right body prose line", fontsize=9.5)
    pg.insert_text((271, 100), "Right Early", fontsize=16)
    pg.insert_text((53, 150), "Left Later", fontsize=16)
    pg.insert_text((53, 400), "A Full Width Heading That Crosses The Gutter", fontsize=16)
    pg.insert_text((271, 500), "Right Below", fontsize=16)
    res = detect.heading_candidates(doc)
    assert [(c["name"], c["col"]) for c in res["candidates"]] == [
        ("Left Later", "left"), ("Right Early", "right"),
        ("A Full Width Heading That Crosses The Gutter", "full"), ("Right Below", "right")]


def test_single_column_geometry_makes_every_candidate_full(book):
    _, _, doc = book
    prof = profile_from_dict({"single_column": True})
    res = detect.heading_candidates(doc, min_ratio=1.25, column_split=prof.column_split,
                                    full_width_ratio=prof.full_width_ratio)
    assert {c["col"] for c in res["candidates"]} == {"full"}


# ── AC-6: running headers and page numbers ─────────────────────────────────

def test_running_headers_and_page_numbers_are_never_candidates_even_with_no_bands(book):
    _, _, doc = book
    for band in (0.0, 50.0):
        names = [c["name"] for c in detect.heading_candidates(doc, min_ratio=1.0, header_band=band, footer_band=0.0)["candidates"]]
        assert "The Synthetic Handbook" not in names                # 14pt, on every page
        assert not any(n.isdigit() for n in names)                   # 12.5pt page numbers


def _headered(n_pages: int, n_headed: int) -> fitz.Document:
    doc = _blank(n_pages)
    for i, pg in enumerate(doc):
        for j in range(20):
            pg.insert_text((50, 200 + 12 * j), "body prose line for the running header test", fontsize=9.5)
        if i < n_headed:
            pg.insert_text((50, 40), f"Rare Header {i + 1}", fontsize=16)
    return doc


def test_a_header_repeated_on_30_percent_of_pages_is_running_and_below_that_is_not():
    names = lambda doc: [c["name"] for c in detect.heading_candidates(doc, header_band=0)["candidates"]]
    assert names(_headered(10, 2)) == ["Rare Header 1", "Rare Header 2"]    # 20%: two real headings
    assert names(_headered(10, 3)) == []                                    # 30% (digits aside): running


def _numbered(text: str, y: float) -> fitz.Document:
    doc = _blank(1)
    pg = doc[0]
    for j in range(20):
        pg.insert_text((50, 400 + 12 * j), "body prose line on the page", fontsize=9.5)
    pg.insert_text((50, y), text, fontsize=20)
    pg.insert_text((50, 250), "Page Layout Basics", fontsize=20)
    return doc


def _names(doc: fitz.Document, **kw) -> list[str]:
    return [c["name"] for c in detect.heading_candidates(doc, header_band=0, footer_band=0, **kw)["candidates"]]


@pytest.mark.parametrize("text, y", [
    ("12", 200), ("- 12 -", 200), ("Page 3", 200), ("PAGE 214", 200), ("— 7 —", 200), ("12", 770),
    ("xiv", 770), ("XIV", 770), ("- iv -", 770), ("Page iv", 770), ("mcmxcix", 40),
])
def test_page_number_lines_are_never_candidates_digits_anywhere_roman_at_the_page_edge(text, y):
    assert _names(_numbered(text, y)) == ["Page Layout Basics"]


@pytest.mark.parametrize("text", ["XIV", "C", "Mix", "iv"])
def test_a_roman_numeral_mid_page_is_a_heading_not_a_folio(text):
    assert _names(_numbered(text, 200)) == [text, "Page Layout Basics"]


@pytest.mark.parametrize("text", ["Dill", "Mild", "Civil", "Mid", "Mill", "Vivid", "Ill", "IIII", "Fennel"])
def test_words_spelled_with_roman_letters_are_headings_even_at_the_page_edge(text):
    # not well-formed numerals: never a folio, even in the footer strip
    assert _names(_numbered(text, 770)) == ["Page Layout Basics", text]


def test_an_a_to_z_glossary_keeps_every_letter():
    doc = fitz.open()
    letters = [chr(c) for c in range(ord("A"), ord("Z") + 1)]
    for L in letters:
        pg = doc.new_page(width=W, height=H)
        pg.insert_text((53, 120), L, fontsize=20, fontname="hebo")
        for i in range(30):
            pg.insert_text((53, 160 + 12 * i), f"{L.lower()}word {i}: a glossary definition line of body prose", fontsize=9.5)
    res = detect.heading_candidates(doc)
    assert [c["name"] for c in res["candidates"]] == letters
    assert res["levels"] == [{"size": 20.0, "count": 26}]


# ── AC-7: pure, one text extraction per page ───────────────────────────────

def test_detection_writes_nothing_and_reads_each_page_once(book, tmp_path, monkeypatch):
    _, info, _ = book
    monkeypatch.chdir(tmp_path)
    pdf = Path(info["pdf"])
    before = (pdf.stat().st_mtime_ns, pdf.read_bytes())
    calls: list[int] = []
    real = fitz.Page.get_text

    def counting(self, *a, **k):
        calls.append(self.number)
        return real(self, *a, **k)

    monkeypatch.setattr(fitz.Page, "get_text", counting)
    doc = fitz.open(pdf)
    detect.heading_candidates(doc, min_ratio=1.25)
    assert sorted(calls) == list(range(doc.page_count))
    calls.clear()
    detect.outline_levels(doc), detect.outline_entries(doc, 1), detect.outline_entries(doc, 2)
    assert calls == []                                              # the outline never reads page text
    assert not doc.is_dirty
    assert list(tmp_path.iterdir()) == [] and (pdf.stat().st_mtime_ns, pdf.read_bytes()) == before


# ── the rows open a Book unchanged ─────────────────────────────────────────

def _flags(book: Book) -> dict:
    return {e.name: book.planned(e) for e in book.entries}


def test_level_1_candidates_open_a_book_and_every_heading_is_located(book):
    d, info, doc = book
    rows = [c for c in detect.heading_candidates(doc, min_ratio=1.25)["candidates"] if c["level"] == 1]
    b = Book.open(pdf=Path(info["pdf"]), out=d / "cands", profile=profile_from_dict({}), entries=rows, log=lambda *_: None)
    plans = _flags(b)
    assert list(plans) == [c["name"] for c in info["chapters"]]
    assert all("heading-not-found" not in p["flags"] for p in plans.values()), plans
    assert [p["sheet0"] for p in plans.values()] == [0, 2, 3]


def test_every_candidate_and_outline_row_opens_a_book_with_every_heading_located(book):
    d, info, doc = book
    prof = profile_from_dict({"heading_min_size": 11.5})
    for label, rows in [("all", detect.heading_candidates(doc, min_ratio=1.25)["candidates"]),
                        ("outline1", detect.outline_entries(doc, 1)), ("outline2", detect.outline_entries(doc, 2))]:
        b = Book.open(pdf=Path(info["pdf"]), out=d / label, profile=prof, entries=rows, log=lambda *_: None)
        plans = _flags(b)
        assert len(plans) == len(rows), label
        assert all("heading-not-found" not in p["flags"] for p in plans.values()), (label, plans)
        assert [p["sheet0"] for p in plans.values()] == [r["page"] - 1 for r in rows], label
