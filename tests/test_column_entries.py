"""Entries that start inside a column (Bensky Materia Medica): the header's column is the
cut's column, a label's value on the same baseline is the name, and a pinyin-named entry
finds its pharmaceutical-name label block through the alias."""

from pathlib import Path

import fitz
import pytest

from monograph_splitter.cuts import cut_rects, plan
from monograph_splitter.index import index_book, label_value, page_anchors
from monograph_splitter.profile import load_profile
from tests.fixtures import FakeBook, H, W

TESTS = Path(__file__).parent


@pytest.fixture(scope="module")
def cprof():
    return load_profile(TESTS / "profile-column.toml")


@pytest.fixture(scope="module")
def column_book(tmp_path_factory, cprof):
    """p1: Alpha at the top-left; right column = Alpha's prose, then Beta mid-column (its
    pinyin line is OCR junk). p2: left column = Beta's tail; Gamma at the top of the right
    column."""
    b = FakeBook()
    b.page(1); y = b.herb(60, "alpha cao", "Alphae Herba"); b.body(y, 14, prefix="alpha body")
    b.body(60, 10, "right", prefix="alpha right"); y = b.herb(230, "~~ ..Jt+", "Betae Radix", col="right"); b.body(y, 10, "right", prefix="beta body")
    b.page(2); b.body(60, 30, prefix="beta tail"); y = b.herb(64, "gamma zi", "Gammae Semen", col="right"); b.body(y, 10, "right", prefix="gamma body")
    pdf = tmp_path_factory.mktemp("col") / "book.pdf"
    b.save(pdf)
    doc = fitz.open(str(pdf))
    return {"doc": doc, "index": index_book(doc, None, cprof, log=lambda *a, **k: None)}


def test_a_label_value_on_the_same_baseline_becomes_the_name(cprof):
    lines = [(100.0, 110.0, 57.0, 150.0, "PHARMACEUTICAL NAME", 8.0),
             (96.3, 110.9, 160.0, 260.0, "Alphae Herba", 9.0),                  # a 9pt Times value tops out 4pt above the 8pt small-caps label
             (100.0, 110.0, 320.0, 400.0, "prose in the other column", 9.0)]
    assert label_value(lines, lines[0], W, cprof) == "Alphae Herba"
    assert page_anchors(lines, W, cprof)[0]["name"] == "Alphae Herba"
    assert label_value(lines[:1] + lines[2:], lines[0], W, cprof) == ""       # the other column never counts


def test_column_entries_keep_their_column(column_book, cprof):
    a = column_book["index"][0]["anchors"]
    assert [(x["name"], x["kind"], x["col"]) for x in a] == [("Alphae Herba", "monograph", "left"), ("Betae Radix", "monograph", "right")]
    assert a[0]["linesAbove"] == 0                                             # starts the page
    assert a[1]["linesAbove"] > 0 and a[1]["titleTop"] < a[1]["y"]            # mid-column: cut above its pinyin line
    g = column_book["index"][1]["anchors"]
    assert [(x["name"], x["col"]) for x in g] == [("Gammae Semen", "right")]


def test_the_alias_picks_our_block_when_the_name_is_pinyin(column_book, cprof):
    index = column_book["index"]
    p = plan("Beta Gen", 1, index, cprof, aliases=("Betae Radix",))
    assert p["flags"] == [] and p["startCol"] == "right" and p["startCut"] == index[0]["anchors"][1]["titleTop"]
    assert (p["sheet0"], p["sheet1"]) == (0, 1) and p["endCol"] == "right" and p["endCut"] == index[1]["anchors"][0]["titleTop"]
    q = plan("Beta Gen", 1, index, cprof)                                      # no alias, junk pinyin line: ambiguous
    assert "start-header-ambiguous" in q["flags"] and q["startCol"] == "left"
    # Alpha: ends at Beta's block in the right column; the left column below stays Alpha's
    a = plan("Alpha Cao", 1, index, cprof, aliases=("Alphae Herba",))
    assert a["startCut"] is None and (a["sheet0"], a["sheet1"]) == (0, 0) and a["endCol"] == "right"
    split = cprof.column_split * W
    assert cut_rects(a, W, H, cprof) == [(0, (split, a["endCut"], W, H - cprof.footer_band))]
    # Beta's start on p1 removes the whole left column (Alpha's) and the right column above its title
    rects = cut_rects(p, W, H, cprof)
    assert (0, (0, cprof.redact_top, split, H - cprof.footer_band)) in rects
    assert (0, (split, cprof.redact_top, W, p["startCut"])) in rects


def test_big_lines_min_zero_makes_every_label_block_an_entry(tmp_path):
    prof = load_profile(TESTS / "profile-column.toml")
    zero = load_profile(TESTS / "profile-column-zero.toml")
    b = FakeBook()
    b.page(1); y = b.herb(60, "alpha cao", "Alphae Herba"); b.body(y, 6, prefix="alpha body")
    b.text(y + 90, "b3ta g3n", size=9.5, x=53, font="heit")                      # a pinyin line the OCR made body-sized
    b.text(y + 110, "PHARMACEUTICAL NAME", size=8, x=53); b.text(y + 110, "Betae Radix", size=9, x=153)
    b.text(y + 123, "FAMILY", size=8, x=53); b.text(y + 123, "Testaceae", size=9, x=153)
    pdf = tmp_path / "book.pdf"
    b.save(pdf)
    doc = fitz.open(str(pdf))
    kinds = lambda p: [(a["name"], a["kind"], a["col"]) for a in index_book(doc, None, p, log=lambda *a, **k: None)[0]["anchors"]]
    assert kinds(prof) == [("Alphae Herba", "monograph", "left"), ("Betae Radix", "related", "left")]
    assert kinds(zero) == [("Alphae Herba", "monograph", "left"), ("Betae Radix", "monograph", "left")]
    a = index_book(doc, None, zero, log=lambda *a, **k: None)[0]["anchors"][1]
    assert a["titleTop"] < a["y"] and a["method"] in ("gap", "title")           # cut above the small pinyin line, not an estimate
