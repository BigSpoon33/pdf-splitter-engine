import json

import fitz
import pytest

from monograph_splitter.cuts import apply_overrides, cut_rects, plan
from monograph_splitter.index import add_known_starts, index_book, page_anchors, page_lines
from monograph_splitter.render import render_review, write_excerpt
from monograph_splitter.verify import truncation_check, verify_excerpt
from tests.fixtures import scenario_book


@pytest.fixture(scope="module")
def scenario(tmp_path_factory, prof):
    d = tmp_path_factory.mktemp("book")
    info = scenario_book(d / "book.pdf")
    book = fitz.open(info["pdf"])
    index = index_book(book, None, prof, log=lambda *a, **k: None)
    plans = {e["name"]: plan(e["name"], e["page"], index, prof) for e in info["entries"]}
    return {"dir": d, "book": book, "index": index, "plans": plans, "entries": info["entries"]}


def anchor(scenario, sheet, name):
    return next(a for a in scenario["index"][sheet]["anchors"] if a["name"] == name)


def test_extents_follow_the_next_entry_title_the_chapter_break_and_the_page_top(scenario):
    p = scenario["plans"]
    assert (p["Alpha Tang"]["sheet0"], p["Alpha Tang"]["sheet1"]) == (0, 1)
    assert p["Alpha Tang"]["endCut"] == anchor(scenario, 1, "Beta San")["titleTop"] and p["Alpha Tang"]["endCol"] == "full"
    assert p["Alpha Tang"]["startCut"] is None and p["Alpha Tang"]["flags"] == []
    # Beta: starts mid-page 2 (cut above its title), keeps its own sub-entry on p3, ends before Delta at the top of p4
    assert (p["Beta San"]["sheet0"], p["Beta San"]["sheet1"]) == (1, 2)
    assert p["Beta San"]["startCut"] == p["Alpha Tang"]["endCut"] and p["Beta San"]["endCut"] is None
    assert p["Beta San"]["flags"] == [] and p["Beta San"]["nextFormula"] == "Delta Yin"
    # Delta: stops at the chapter Summary page
    assert (p["Delta Yin"]["sheet0"], p["Delta Yin"]["sheet1"]) == (3, 3) and "end-at-chapter-break" in p["Delta Yin"]["notes"]
    # Epsilon → Zeta mid-page; Zeta → Eta at the top of the next page
    assert p["Epsilon Tang"]["endCut"] == anchor(scenario, 6, "Zeta Wan")["titleTop"]
    assert (p["Zeta Wan"]["sheet0"], p["Zeta Wan"]["sheet1"]) == (6, 6) and p["Zeta Wan"]["endCut"] is None
    # the last entry of the book has nothing to stop it — the clamp says so
    assert "span-clamped" in p["Eta San"]["flags"]


def test_a_sub_entry_is_column_bound_and_ends_at_the_parent_heading(scenario):
    g = scenario["plans"]["Gamma Wan"]
    assert g["kind"] == "related" and (g["sheet0"], g["sheet1"]) == (2, 2)
    assert g["startCol"] == "left" and g["startCut"] == anchor(scenario, 2, "Gamma Wan")["titleTop"]
    assert g["endCol"] == "right" and "end-at-parent-heading" in g["notes"]
    # the heading sits HIGHER on the page than Gamma's own labels but in the other
    # column — reading order makes that a real end, not a self-truncation
    assert g["endCut"] < anchor(scenario, 2, "Gamma Wan")["y"] and g["flags"] == []
    w, h, prof = 522.72, 789.6, scenario_prof()
    rects = cut_rects(g, w, h, prof)
    split = prof.column_split * w
    # start: previous entry's running name + the left column above our block
    assert (0, (0, prof.redact_top, w, min(g["startCut"], prof.subheader_bottom))) in rects
    assert (0, (0, prof.redact_top, split, g["startCut"])) in rects
    # end: the right column from the heading down (the left column below is ours)
    assert (0, (split, g["endCut"], w, h - prof.footer_band)) in rects
    assert len(rects) == 3


def test_a_continuation_page_is_walked_back_to_the_real_start(scenario, prof):
    p = plan("Beta San", 3, scenario["index"], prof)             # p3 carries "Beta San" as the running sub-header
    assert p["sheet0"] == 1 and "start-page-corrected" in p["flags"]


def test_overrides_win_verbatim(scenario, prof):
    p = dict(scenario["plans"]["Alpha Tang"], flags=[])
    p = apply_overrides(p, {"pages": [1, 3], "endCut": 123.0, "endCol": "left"}, prof)
    assert (p["sheet0"], p["sheet1"]) == (0, 2) and p["endCut"] == 123.0 and p["endCol"] == "left" and "override" in p["flags"]


def test_redaction_removes_the_neighbours_text_and_keeps_ours(scenario, prof):
    d, book, p = scenario["dir"], scenario["book"], scenario["plans"]
    for name in ("Alpha Tang", "Beta San", "Gamma Wan"):
        write_excerpt(book, p[name], d / f"{name}.pdf", True, prof)
    alpha = "".join(pg.get_text() for pg in fitz.open(d / "Alpha Tang.pdf"))
    beta = "".join(pg.get_text() for pg in fitz.open(d / "Beta San.pdf"))
    gamma = "".join(pg.get_text() for pg in fitz.open(d / "Gamma Wan.pdf"))
    assert "Pinyin Name: Alpha Tang" in alpha and "Pinyin Name: Beta San" not in alpha and "line 0 of the running" in alpha
    assert "Pinyin Name: Beta San" in beta and "Pinyin Name: Alpha Tang" not in beta and "Alpha Tang" not in beta
    assert "Pinyin Name: Gamma Wan" in gamma and "gamma continued" in gamma
    assert "AUTHORS" not in gamma and "beta comments" not in gamma and "beta body" not in gamma
    # the running chapter header and the page number survive every cut
    assert "Chapter 1 - Test Formulas" in beta and "\n2\n" in beta


def test_verify_flags_a_leak_and_a_dropped_tail(scenario, prof):
    d, book, index = scenario["dir"], scenario["book"], scenario["index"]
    leaky = dict(scenario["plans"]["Alpha Tang"], endCut=None, flags=[])          # keep Beta's head on p2
    write_excerpt(book, leaky, d / "leaky.pdf", True, prof)
    assert any("Beta San" in x for x in verify_excerpt(d / "leaky.pdf", "Alpha Tang", "monograph", prof))
    assert verify_excerpt(d / "Beta San.pdf", "Beta San", "monograph", prof) == []   # its own sub-entry is allowed
    short = dict(scenario["plans"]["Beta San"], sheet1=1, endCut=None, notes=[])   # p3 (its tail + sub-entry) dropped
    assert "body line" in (truncation_check(short, index, prof) or "")
    assert truncation_check(scenario["plans"]["Beta San"], index, prof) is None
    assert truncation_check(scenario["plans"]["Delta Yin"], index, prof) is None    # chapter break is a legitimate end


def test_review_pngs_are_rendered_for_first_and_last_page(scenario, prof):
    d = scenario["dir"] / "review"
    d.mkdir(exist_ok=True)
    names = render_review(scenario["book"], scenario["plans"]["Alpha Tang"], "Alpha-Tang", d, prof, dpi=30)
    assert names == ["Alpha-Tang-first.png", "Alpha-Tang-last.png"] and all((d / n).exists() for n in names)


def test_known_starts_seed_a_synthetic_anchor_only_where_nothing_was_found(scenario, prof):
    index = [dict(pg, anchors=list(pg["anchors"])) for pg in scenario["index"]]
    added = add_known_starts(index, {4, 0}, prof)          # p5 = the summary page (no anchors); p1 has one
    assert added == 1 and index[4]["anchors"][0]["synthetic"] and len(index[0]["anchors"]) == 1


def scenario_prof():
    from tests.conftest import TOOLS
    from monograph_splitter.profile import load_profile
    return load_profile(TOOLS / "tests" / "profile-test.toml")


def test_a_label_block_wider_than_cluster_gap_is_caught_not_silently_truncated(tmp_path, prof):
    """A header whose labels sit further apart than the profile's cluster_gap splits
    into two anchors; the second looks like the next entry. The plan flags it."""
    from tests.fixtures import FakeBook
    b = FakeBook()
    b.page(1); b.text(60, "Wide Block Tang", size=19, x=71); b.text(107, "宽块汤", size=17, x=73, font="china-s")
    b.text(150, "Pinyin Name: Wide Block Tang"); b.text(162, "Literal Name: wide")
    b.text(150 + prof.cluster_gap + 20, "Original Source: far below the first label")   # beyond cluster_gap
    b.body(330, 10)
    b.page(2); b.monograph(60, "Next Tang", "下一汤")
    book = fitz.open(b.save(tmp_path / "wide.pdf"))
    index = index_book(book, None, prof, log=lambda *a, **k: None)
    assert [a["name"] for a in index[0]["anchors"]] == ["Wide Block Tang", ""]      # the split block
    p = plan("Wide Block Tang", 1, index, prof)
    assert "ends-inside-own-header" in p["flags"] and p["sheet1"] == 0
