"""Web mode (STORY-001): a Profile from a settings dict — WEB_BASE, WEB_KEYS, single_column, sha256."""
from dataclasses import fields
from pathlib import Path

import fitz
import pytest

from monograph_splitter.classify import is_left
from monograph_splitter.index import page_lines
from monograph_splitter.profile import (
    WEB_BASE, WEB_KEYS, Profile, ProfileError, load_profile, profile_from_dict,
)
from monograph_splitter.session import Book
from tests.fixtures import heading_book

WEB_VALUES = {"column_split": 0.5, "header_band": 40, "footer_band": 30.5, "redact_top": 38.0, "heading_min_size": 11.5,
              "heading_match": 0.9, "heading_wrap_gap": 14.0, "max_span": 50, "single_column": False}


def test_web_base_is_headings_mode_on_sheet_numbers_with_no_chen_chen_rules():
    assert WEB_BASE.anchor_source == "headings" and WEB_BASE.max_span == 200
    assert WEB_BASE.script_regex == "" and WEB_BASE.script_re is None and WEB_BASE.break_patterns == ()
    # page N = sheet index N + sheet_offset - 1: page 1 is the first sheet
    assert 1 + WEB_BASE.sheet_offset - 1 == 0
    assert set(WEB_KEYS) == set(WEB_VALUES)


def test_an_empty_dict_is_web_base_and_every_web_key_overrides_only_itself():
    empty = profile_from_dict({})
    same = [f.name for f in fields(Profile) if f.name != "sha256"]
    assert all(getattr(empty, n) == getattr(WEB_BASE, n) for n in same) and empty.sha256
    prof = profile_from_dict(WEB_VALUES)
    for key, value in WEB_VALUES.items():
        if key != "single_column":
            assert getattr(prof, key) == value
    assert isinstance(prof.header_band, float) and isinstance(prof.max_span, int)
    untouched = [n for n in same if n not in WEB_VALUES and n != "subheader_bottom"]
    assert all(getattr(prof, n) == getattr(WEB_BASE, n) for n in untouched)
    # no running sub-header in a web book: the strip under the header band is never cut
    assert prof.subheader_bottom == prof.redact_top == 38.0
    assert prof.tag.startswith("web@")


@pytest.mark.parametrize("bad", [{"sheet_offset": 5}, {"script_regex": "x"}, {"column_spilt": 0.5}, {"anchor_source": "labels"}])
def test_any_other_key_is_an_error_naming_it(bad):
    with pytest.raises(ProfileError) as e:
        profile_from_dict(bad)
    assert repr(next(iter(bad))) in str(e.value)


def test_type_errors_read_like_the_toml_ones(tmp_path):
    toml = tmp_path / "bad.toml"
    toml.write_text('[limits]\nmax_span = 1.5\n')
    with pytest.raises(ProfileError) as from_toml:
        load_profile(toml)
    with pytest.raises(ProfileError) as from_dict:
        profile_from_dict({"max_span": 1.5})
    assert str(from_dict.value) == str(from_toml.value) == "[limits].max_span: expected int, got float (1.5)"
    with pytest.raises(ProfileError, match=r"\[layout\].column_split: expected float"):
        profile_from_dict({"column_split": "0.5"})
    with pytest.raises(ProfileError, match="single_column: expected bool"):
        profile_from_dict({"single_column": 1})
    with pytest.raises(ProfileError, match="expected an object"):
        profile_from_dict([("max_span", 3)])


@pytest.mark.parametrize("bad, what", [({"column_split": 1.0}, "column_split"), ({"column_split": 0}, "column_split"),
                                       ({"heading_match": 0}, "heading_match"), ({"heading_match": 1.2}, "heading_match"),
                                       ({"max_span": 0}, "max_span"), ({"header_band": -1}, "header_band")])
def test_out_of_range_values_are_refused(bad, what):
    with pytest.raises(ProfileError, match=what):
        profile_from_dict(bad)


def test_same_values_same_hash_in_any_order_different_values_different_hash():
    a = profile_from_dict(WEB_VALUES)
    b = profile_from_dict(dict(reversed(list(WEB_VALUES.items()))))
    assert a.sha256 == b.sha256
    # a default spelled out means the same book as a default left out
    assert profile_from_dict({"max_span": 200, "single_column": False}).sha256 == profile_from_dict({}).sha256
    assert profile_from_dict(dict(WEB_VALUES, max_span=51)).sha256 != a.sha256
    assert profile_from_dict(dict(WEB_VALUES, heading_match=0.91)).sha256 != a.sha256
    assert profile_from_dict(dict(WEB_VALUES, single_column=True)).sha256 != a.sha256
    assert len({profile_from_dict({"column_split": v}).sha256 for v in (0.4, 0.45, 0.5)}) == 3


# ── single_column on the synthetic two-column book ─────────────────────────────

@pytest.fixture(scope="module")
def two_col(tmp_path_factory):
    d = tmp_path_factory.mktemp("single")
    return d, heading_book(d / "book.pdf")


def _open(d: Path, info: dict, settings: dict, tag: str) -> Book:
    return Book.open(pdf=Path(info["pdf"]), out=d / tag, profile=profile_from_dict(settings),
                     entries=info["entries"], log=lambda *_: None)


def test_single_column_puts_every_line_left_and_makes_every_cut_full_width(two_col):
    d, info = two_col
    book = _open(d, info, {"single_column": True, "heading_min_size": 11.5}, "single")
    prof = book.prof
    assert prof.column_split == 0.999 and prof.full_width_ratio == 0.0
    doc = fitz.open(info["pdf"])
    right_lines = 0
    for page in doc:
        w = page.rect.width
        for ln in page_lines(page):
            assert is_left(ln[2], w, prof), ln
            right_lines += ln[2] > 0.5 * w
    assert right_lines > 50                                    # the book really has a right column
    anchors = [a for pg in book.index for a in pg["anchors"]]
    assert anchors and all(a["col"] == "full" for a in anchors)
    rects = []
    for e in book.entries:
        p = book.planned(e)
        assert p["startCol"] == "full" and p["endCol"] == "full"
        w, _ = book.page_size(p["sheet0"])
        rects += [(r["rect"], w) for r in book.rects(p)]
    assert rects and all(r[0] == 0 and r[2] == round(w, 1) for r, w in rects)


def test_without_single_column_the_same_book_has_column_cuts(two_col):
    d, info = two_col
    book = _open(d, info, {"heading_min_size": 11.5}, "double")
    cols = {book.planned(e)["startCol"] for e in book.entries}
    assert {"left", "right"} <= cols
    rects = [r["rect"] for e in book.entries for r in book.rects(book.planned(e))]
    assert any(r[2] < book.page_size()[0] - 1 for r in rects)
