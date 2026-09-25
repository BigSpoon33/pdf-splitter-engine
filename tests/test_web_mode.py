"""Web mode end to end (STORY-003): settings dict + detected sections + Book.cut_all()."""
import json
from dataclasses import replace
from pathlib import Path

import fitz
import pytest

from monograph_splitter import detect
from monograph_splitter.cli import main
from monograph_splitter.index import chapter_break, page_lines
from monograph_splitter.profile import WEB_BASE, Profile, profile_from_dict
from monograph_splitter.session import Book, safe_filename
from tests.fixtures import H, W, headed_book, single_column_book

QUIET = {"log": lambda *_: None}
SUMMARY_KEYS = {"written", "flags", "notes", "leaks", "missing", "unknown"}


@pytest.fixture(scope="module")
def headed(tmp_path_factory):
    d = tmp_path_factory.mktemp("web")
    info = headed_book(d / "book.pdf")
    doc = fitz.open(info["pdf"])
    yield d, info, doc
    doc.close()


def _chapters(doc, prof) -> list[dict]:
    return [c for c in detect.heading_candidates(doc, min_ratio=1.25, profile=prof)["candidates"] if c["level"] == 1]


def test_detected_chapters_cut_to_three_pdfs_with_progress_redaction_and_no_leaks(headed):
    d, info, doc = headed
    prof = profile_from_dict({})
    rows = _chapters(doc, prof)
    names = [c["name"] for c in info["chapters"]]
    assert [r["name"] for r in rows] == names
    out = d / "e2e"
    book = Book.open(pdf=Path(info["pdf"]), out=out, profile=prof, entries=rows, **QUIET)
    calls: list[tuple] = []
    summary = book.cut_all(progress=lambda *a: calls.append(a))

    assert calls == [(1, 3, names[0]), (2, 3, names[1]), (3, 3, names[2])]
    assert set(summary) == SUMMARY_KEYS
    assert [r["formula"] for r in summary["written"]] == names
    assert sorted(p.name for p in out.glob("*.pdf")) == sorted(f"{n}.pdf" for n in names)
    assert [r["printedPages"] for r in summary["written"]] == [[1, 2], [3, 4], [4, 6]]
    assert summary["leaks"] == {} and summary["missing"] == [] and summary["unknown"] == []
    assert all(r["leaks"] == [] and "leak" not in r["flags"] and "heading-not-found" not in r["flags"]
               for r in summary["written"])
    assert "long-span" not in summary["flags"]
    assert json.loads((out / "manifest.json").read_text()) == sorted(summary["written"], key=lambda r: r["formula"])

    # Chapter 3 opens mid-right-column on sheet 4, under Chapter 2's tail: its first page keeps
    # only the heading and what follows it in the right column (plus the header/footer bands)
    closing = rows[2]
    first = page_lines(fitz.open(out / "Closing Chapter.pdf")[0])
    inside = [ln for ln in first if prof.header_band <= ln[0] < H - prof.footer_band]
    assert inside and inside[0][4] == "Closing Chapter"
    assert all(ln[2] >= prof.column_split * W for ln in inside), [ln[4] for ln in inside if ln[2] < prof.column_split * W]
    assert all(ln[0] >= closing["y"] - 0.5 for ln in inside)
    # the previous chapter's last page is the same sheet: it keeps what comes BEFORE the heading
    tail = page_lines(fitz.open(out / f"{names[1]}.pdf")[-1])
    assert "Closing Chapter" not in [ln[4] for ln in tail]


def test_progress_counts_an_entry_that_falls_off_the_book(headed):
    d, info, doc = headed
    prof = profile_from_dict({})
    rows = _chapters(doc, prof)[:1] + [{"name": "Ghost", "page": 5, "heading": "Final Section"}]
    book = Book.open(pdf=Path(info["pdf"]), out=d / "ghost", profile=prof, entries=rows, **QUIET)
    # an override's pages are the way a plan leaves the book (a start page past the end is the
    # caller's to refuse: planning indexes that sheet)
    book.set_override("Ghost", {"pages": [40, 41]})
    calls: list[tuple] = []
    summary = book.cut_all(progress=lambda *a: calls.append(a))
    assert calls == [(1, 2, rows[0]["name"]), (2, 2, "Ghost")]
    assert [r["formula"] for r in summary["written"]] == [rows[0]["name"]]
    assert len(summary["missing"]) == 1 and summary["missing"][0].startswith("Ghost (sheets")


def test_only_and_limit_select_like_the_cli_and_report_unknown_names(headed):
    d, info, doc = headed
    prof = profile_from_dict({})
    book = Book.open(pdf=Path(info["pdf"]), out=d / "only", profile=prof, entries=_chapters(doc, prof), **QUIET)
    names = [c["name"] for c in info["chapters"]]
    s = book.cut_all(only=[names[2], names[0], "Nobody"], verify=False)
    assert [r["formula"] for r in s["written"]] == [names[0], names[2]] and s["unknown"] == ["Nobody"]
    s = book.cut_all(limit=1, verify=False)
    assert [r["formula"] for r in s["written"]] == [names[0]]
    # the manifest merges across calls, like repeated --only runs
    assert {m["formula"] for m in json.loads((d / "only" / "manifest.json").read_text())} == {names[0], names[2]}


def test_cut_all_without_redaction_writes_whole_pages_and_skips_the_leak_scan(headed):
    d, info, doc = headed
    prof = profile_from_dict({})
    book = Book.open(pdf=Path(info["pdf"]), out=d / "nr", profile=prof, entries=_chapters(doc, prof), **QUIET)
    s = book.cut_all(redact=False)
    assert all(r["redacted"] is False and r["leaks"] == [] for r in s["written"])
    assert "body 0 of the running prose" in [ln[4] for ln in page_lines(fitz.open(d / "nr" / "Closing Chapter.pdf")[0])]


@pytest.mark.parametrize("name", ["a/b", "../escape", "a\\b", "nul\0byte", ".", ".."])
def test_a_name_that_could_leave_the_output_dir_is_refused_before_anything_is_written(headed, name):
    d, info, doc = headed
    prof = profile_from_dict({})
    out = d / f"unsafe-{abs(hash(name))}"
    rows = _chapters(doc, prof)
    rows = [rows[0], {**rows[1], "name": name}]
    book = Book.open(pdf=Path(info["pdf"]), out=out, profile=prof, entries=rows, **QUIET)
    with pytest.raises(ValueError, match="can't be a file name") as e:
        book.cut_all()
    assert repr(name) in str(e.value)
    assert list(out.glob("*.pdf")) == [] and not (out / "manifest.json").exists()
    with pytest.raises(ValueError):
        book.cut(book.entries[1])
    with pytest.raises(ValueError):
        book.excerpt_path(name)
    assert not (d / "escape.pdf").exists()


def test_safe_names_keep_their_raw_filename():
    for name in ["Liver-Qi stagnation", "Chapter 1: Why?", "Qi–Blood … (a: b)", "..hidden", "a.b"]:
        assert safe_filename(name) == f"{name}.pdf"


def test_the_cli_reports_an_unsafe_name_as_an_error(headed, tmp_path):
    d, info, doc = headed
    entries = tmp_path / "entries.json"
    entries.write_text(json.dumps([{"name": "x/y", "page": 1, "heading": info["chapters"][0]["name"]}]))
    logs: list[str] = []
    rc = main(["--pdf", info["pdf"], "--out", str(tmp_path / "o"), "--profile", str(Path(__file__).parent / "profile-headings.toml"),
               "--entries", str(entries)], log=logs.append)
    assert rc == 2 and any("Error: entry name 'x/y'" in l for l in logs)


# ── web defaults ─────────────────────────────────────────────────────────────
def test_a_bare_chapter_line_is_not_a_chapter_break_in_web_mode():
    lines = [(60.0, 78.0, 54.0, 160.0, "Chapter 3", 16.0), (100.0, 110.0, 54.0, 300.0, "body text", 9.5)]
    assert chapter_break(lines, Profile())                 # Chen & Chen: a bare "Chapter N" opens a chapter
    assert WEB_BASE.chapter_only == "" and profile_from_dict({}).chapter_only == ""
    assert not chapter_break(lines, WEB_BASE)
    assert not chapter_break(lines, profile_from_dict({}))
    # an empty pattern disables the rule; it does not match every line
    assert WEB_BASE.chapter_only_re.match("anything at all") is None
    assert WEB_BASE.chapter_only_re.match("") is None


def test_web_sections_are_never_flagged_long_span(headed):
    d, info, doc = headed
    assert WEB_BASE.long_span == WEB_BASE.max_span == 200
    assert profile_from_dict({"max_span": 30}).long_span == 30
    rows = [{"name": "Whole Book", "page": 1, "heading": info["chapters"][0]["name"]}]
    for prof, flagged in [(profile_from_dict({}), False), (replace(profile_from_dict({}), long_span=3), True)]:
        book = Book.open(pdf=Path(info["pdf"]), out=d / f"long-{flagged}", profile=prof, entries=rows, **QUIET)
        p = book.planned(book.entries[0])
        assert p["sheet1"] - p["sheet0"] + 1 == 6
        assert ("long-span" in p["flags"]) is flagged


def test_heading_candidates_take_their_geometry_and_wrap_gap_from_the_profile(tmp_path):
    info = single_column_book(tmp_path / "book.pdf")
    doc = fitz.open(info["pdf"])
    prof = profile_from_dict({"single_column": True, "heading_wrap_gap": 24, "header_band": 0, "footer_band": 0})
    explicit = detect.heading_candidates(doc, wrap_gap=24, header_band=0, footer_band=0,
                                         column_split=prof.column_split, full_width_ratio=prof.full_width_ratio)
    assert detect.heading_candidates(doc, profile=prof) == explicit
    assert [c["name"] for c in explicit["candidates"] if c["level"] == 1] == [c["name"] for c in info["chapters"]]
    # an explicit keyword wins over the profile: at wrap_gap 16 the 24pt-leading titles split
    split = detect.heading_candidates(doc, profile=prof, wrap_gap=16)
    assert split == detect.heading_candidates(doc, wrap_gap=16, header_band=0, footer_band=0,
                                              column_split=prof.column_split, full_width_ratio=prof.full_width_ratio)
    assert split != explicit
    # no profile: the Profile defaults, as before
    assert detect.heading_candidates(doc) == detect.heading_candidates(doc, profile=Profile())
    # one profile for detection AND cutting: every detected heading is located
    b = Book.open(pdf=Path(info["pdf"]), out=tmp_path / "o", profile=prof,
                  entries=detect.heading_candidates(doc, profile=prof)["candidates"], **QUIET)
    assert all("heading-not-found" not in b.planned(e)["flags"] for e in b.entries)


def test_the_package_version_is_the_pyproject_version():
    import tomllib

    import monograph_splitter

    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert monograph_splitter.__version__ == pyproject["project"]["version"] == "0.4.1"
    assert monograph_splitter.ENGINE_VERSION == 17
