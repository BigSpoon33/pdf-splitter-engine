import json
from pathlib import Path

from monograph_splitter.session import Book
from tests.fixtures import scenario_book

TEST_PROFILE = Path(__file__).parent / "profile-test.toml"


def open_book(tmp_path):
    info = scenario_book(tmp_path / "book.pdf")
    entries = tmp_path / "entries.json"
    entries.write_text(json.dumps(info["entries"]))
    book = Book.open(pdf=Path(info["pdf"]), out=tmp_path / "out", profile=TEST_PROFILE, entries=entries, log=lambda *_: None)
    return info, book


def test_book_cuts_like_the_cli_and_persists_overrides(tmp_path):
    info, book = open_book(tmp_path)
    alpha = book.entry("Alpha Tang")
    assert alpha is not None and book.entry("Nobody") is None
    row = book.cut(alpha, preview=True, verify=True)
    assert row["printedPages"] == [1, 2] and row["kind"] == "monograph" and "override" not in row["flags"]
    assert book.excerpt_path("Alpha Tang").exists() and row["review"] == ["Alpha-Tang-first.png", "Alpha-Tang-last.png"]
    book.save_manifest()
    assert json.loads((book.out / "manifest.json").read_text())[0]["formula"] == "Alpha Tang"

    # an override is written to overrides.json, applied on the next cut and flagged
    book.set_override("Alpha Tang", {"pages": [1, 1], "endCut": 600.0, "endCol": "full", "note": "test"})
    assert json.loads((book.out / "overrides.json").read_text())["Alpha Tang"]["note"] == "test"
    row2 = book.cut(alpha)
    assert row2["printedPages"] == [1, 1] and row2["endCut"] == 600.0 and row2["pageSource"] == "override"
    assert book.excerpt_path("Alpha Tang").exists()
    # the plan the editor previews is the same thing, without writing anything
    p = book.planned(alpha)
    assert p["sheet1"] == p["sheet0"] and p["endCut"] == 600.0
    assert any(r["sheet"] == 0 for r in book.rects(p))
    # clearing restores the engine's decision
    assert book.clear_override("Alpha Tang") and not book.clear_override("Alpha Tang")
    assert book.cut(alpha)["printedPages"] == [1, 2]


def test_band_override_widens_a_column_cut_to_the_whole_column(tmp_path, prof):
    from monograph_splitter.cuts import apply_overrides

    p = {"sheet0": 3, "sheet1": 3, "startCut": 300.0, "startCol": "left", "startBand": [100.0, 500.0],
         "endCut": None, "endCol": "full", "endBand": [None, None], "flags": [], "notes": [], "kind": "related", "nextFormula": ""}
    q = apply_overrides(dict(p, flags=[]), {"startBand": None}, prof)
    assert q["startBand"] == [None, None] and "override" in q["flags"]
    q = apply_overrides(dict(p, flags=[]), {"startBand": [120.0, None]}, prof)
    assert q["startBand"] == [120.0, None]


def test_a_skipped_name_becomes_an_entry_when_an_override_maps_its_page(tmp_path):
    info, book = open_book(tmp_path)
    book.entry_list.skipped.append("Ghost Tang (no printed start page)")
    assert "Ghost Tang (no printed start page)" in book.skipped and book.entry("Ghost Tang") is None
    book.set_override("Ghost Tang", {"pages": [4, 4]})
    e = book.entry("Ghost Tang")
    assert e is not None and e.page == 4 and e.source == "override"
    assert book.skipped == []
    row = book.cut(e)
    assert row["printedPages"] == [4, 4] and row["pageSource"] == "override"


def test_sheet_png_is_rendered_once_and_cached(tmp_path):
    info, book = open_book(tmp_path)
    data = book.sheet_png(0, dpi=40)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    cache = book.out / ".sheets" / "40" / "0.png"
    assert cache.exists() and book.sheet_png(0, dpi=40) == data
    w, h = book.page_size(0)
    assert 500 < w < 540 and 780 < h < 800
    assert book.printed_of(book.sheet_of(7)) == 7
