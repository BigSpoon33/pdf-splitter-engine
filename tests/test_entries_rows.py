"""Web mode (STORY-001): entry rows in memory — entries_from_rows and Book.open(entries=<rows | EntryList | Path>)."""
import json
from pathlib import Path

import pytest

from monograph_splitter.entries import EntryList, entries_from_rows, load_entries_json
from monograph_splitter.profile import Profile, load_profile, profile_from_dict
from monograph_splitter.session import Book
from tests.fixtures import heading_book

HEADINGS_PROFILE = Path(__file__).parent / "profile-headings.toml"

ROWS = [
    {"name": "A", "page": 5, "heading": " A heading "}, {"name": "B"}, {"page": 3}, {"name": "C", "page": 0},
    {"name": "D", "page": True}, "not a row", {"name": "  E ", "page": 7, "source": "toc"},
    {"name": "Tail", "page": 9, "stop": True}, {"name": "Banner", "page": 11, "heading": "BANNER", "stop": True},
]


def test_rows_validate_exactly_like_the_json_file(tmp_path):
    f = tmp_path / "e.json"
    f.write_text(json.dumps(ROWS))
    from_file, from_rows = load_entries_json(f), entries_from_rows(ROWS)
    assert from_rows == from_file
    assert [(e.name, e.page, e.source, e.heading) for e in from_rows.entries] == [("A", 5, "entries", "A heading"), ("E", 7, "toc", "")]
    assert from_rows.skipped == ["B (no printed start page)", "#2 (no name)", "C (no printed start page)",
                                 "D (no printed start page)", "#5 (no name)"]
    assert from_rows.headings == [("A", "A heading", 5), ("E", "", 7), ("Tail", "Tail", 9), ("Banner", "BANNER", 11)]
    assert from_rows.known_pages == {5, 7}                    # stop rows are boundaries, not starts


def test_not_a_list_is_an_error_naming_where_it_came_from(tmp_path):
    with pytest.raises(ValueError, match=r"^entries: expected a JSON list"):
        entries_from_rows({"name": "A", "page": 1})
    f = tmp_path / "obj.json"
    f.write_text("{}")
    with pytest.raises(ValueError, match=rf"^{f}: expected a JSON list"):
        load_entries_json(f)
    assert entries_from_rows([]) == EntryList([], [], set(), [])


@pytest.fixture(scope="module")
def book_info(tmp_path_factory):
    d = tmp_path_factory.mktemp("rows")
    info = heading_book(d / "book.pdf")
    f = d / "entries.json"
    f.write_text(json.dumps(info["entries"]))
    return d, info, f


def _summary(book: Book) -> dict:
    return {e.name: book.planned(e) for e in book.entries}


def test_book_opens_from_rows_an_entry_list_or_a_path_with_the_same_plans(book_info):
    d, info, f = book_info
    prof = load_profile(HEADINGS_PROFILE)
    quiet = dict(pdf=Path(info["pdf"]), profile=prof, log=lambda *_: None)
    by_path = Book.open(out=d / "path", entries=f, **quiet)
    by_str = Book.open(out=d / "str", entries=str(f), **quiet)
    by_rows = Book.open(out=d / "rows", entries=info["entries"], **quiet)
    by_list = Book.open(out=d / "el", entries=entries_from_rows(info["entries"]), **quiet)
    expected = _summary(by_path)
    assert len(expected) == 6 and all("heading-not-found" not in p["flags"] for p in expected.values())
    for b in (by_str, by_rows, by_list):
        assert b.entry_list == by_path.entry_list and _summary(b) == expected
    # nothing is written for the entries themselves: a web job has no entries file
    assert {p.name for p in (d / "rows").iterdir()} == {"overrides.json", ".book-index.json"}


def test_book_profile_accepts_a_profile_instance_from_settings(book_info):
    d, info, _ = book_info
    prof = profile_from_dict({"heading_min_size": 11.5})
    assert isinstance(prof, Profile)
    book = Book.open(pdf=Path(info["pdf"]), out=d / "web", profile=prof, entries=info["entries"], log=lambda *_: None)
    assert book.prof is prof and book.headings_mode
    # web pages are sheet numbers: page 1 is the first sheet
    alpha = book.planned(book.entry("Alpha Pattern"))
    assert alpha["sheet0"] == 0 and book.sheet_of(1) == 0
    row = book.cut(book.entry("Alpha Pattern"))
    assert row["printedPages"] == [1, 2] and row["profile"] == prof.tag


def test_an_empty_row_list_is_an_empty_book_not_a_missing_argument(book_info):
    d, info, _ = book_info
    book = Book.open(pdf=Path(info["pdf"]), out=d / "empty", profile=profile_from_dict({}), entries=[], log=lambda *_: None)
    assert book.entries == [] and book.skipped == []
    with pytest.raises(ValueError, match="give entries="):
        Book.open(pdf=Path(info["pdf"]), out=d / "none", profile=profile_from_dict({}), log=lambda *_: None)
