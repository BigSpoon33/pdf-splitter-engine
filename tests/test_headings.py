"""Headings mode (STORY-220): a book with no label blocks is cut at the entries' own headings."""
import json
from pathlib import Path

import fitz
import pytest

from monograph_splitter.cli import bundled_profile, main
from monograph_splitter.cuts import cut_rects, plan_headings
from monograph_splitter.entries import load_entries_json
from monograph_splitter.index import add_heading_anchors, index_book, locate_heading, page_lines
from monograph_splitter.profile import ProfileError, load_profile
from monograph_splitter.render import write_excerpt
from monograph_splitter.verify import truncation_check, verify_headings
from tests.fixtures import heading_book

HEADINGS_PROFILE = Path(__file__).parent / "profile-headings.toml"


@pytest.fixture(scope="module")
def hprof():
    return load_profile(HEADINGS_PROFILE)


@pytest.fixture(scope="module")
def scenario(tmp_path_factory, hprof):
    d = tmp_path_factory.mktemp("hbook")
    info = heading_book(d / "book.pdf")
    book = fitz.open(info["pdf"])
    index = index_book(book, None, hprof, log=lambda *a, **k: None)
    refs = [(e["name"], e["heading"], e["page"]) for e in info["entries"]]
    located, missing = add_heading_anchors(index, book, refs, hprof)
    plans = {e["name"]: plan_headings(e["name"], e["page"], index, hprof) for e in info["entries"] if not e.get("stop")}
    return {"dir": d, "book": book, "index": index, "plans": plans, "entries": info["entries"],
            "located": located, "missing": missing}


def anchor(scenario, sheet, name):
    return next(a for a in scenario["index"][sheet]["anchors"] if a["name"] == name)


def test_profile_accepts_the_anchors_table_and_rejects_an_unknown_source(tmp_path, hprof):
    assert hprof.anchor_source == "headings" and hprof.heading_min_size == 11.5
    assert load_profile(None).anchor_source == "labels"
    bad = tmp_path / "bad.toml"
    bad.write_text('[anchors]\nsource = "bogus"\n')
    with pytest.raises(ProfileError) as e:
        load_profile(bad)
    assert "[anchors].source" in str(e.value)
    m = load_profile(bundled_profile("maciocia-foundations"))
    assert m.anchor_source == "headings" and m.sheet_offset == 29 and m.script_re is None and m.break_res == ()


def test_entries_carry_headings_and_stop_rows_plant_anchors_without_excerpts(tmp_path):
    f = tmp_path / "e.json"
    f.write_text(json.dumps([{"name": "A", "page": 1, "heading": "A heading"}, {"name": "B", "page": 2},
                             {"name": "Tail", "page": 3, "stop": True}]))
    el = load_entries_json(f)
    assert [(e.name, e.heading) for e in el.entries] == [("A", "A heading"), ("B", "")]
    assert el.headings == [("A", "A heading", 1), ("B", "", 2), ("Tail", "Tail", 3)]
    assert el.known_pages == {1, 2}


def test_every_heading_is_located_wrapped_interleaved_banner_adjacent_and_small_exact(scenario, hprof):
    assert scenario["located"] == 7 and scenario["missing"] == []
    lines = page_lines(scenario["book"][1])
    w = scenario["book"][1].rect.width
    gamma = locate_heading(lines, "Gamma Pattern turning into Heat", w, hprof)
    assert gamma and gamma["ratio"] == 1.0 and gamma["text"] == "Gamma Pattern turning into Heat"   # the 8pt "34" is skipped
    assert gamma["size"] == 12.5 and gamma["x0"] > w * hprof.column_split                          # the real heading, not Beta's 11.5pt cross-reference
    assert locate_heading(lines, "Gamma Pattern turning into Heat", w, hprof, min_size=12.25)["size"] == 12.5
    assert locate_heading(lines, "Gamma Pattern turning into Meat", w, hprof, match=1.0) is None      # near-miss: not the same heading
    assert locate_heading(lines, "Beta Pattern", w, hprof)["size"] == 12.5                         # not the 13pt banner
    assert locate_heading(lines, "Nothing like this", w, hprof) is None
    stop = locate_heading(page_lines(scenario["book"][4]), "Self-assessment questions", w, hprof)
    assert stop and stop["size"] == 9.5                                                            # below the size floor: exact only
    assert locate_heading(page_lines(scenario["book"][4]), "Self-assessment question", w, hprof) is None


def test_anchors_are_in_reading_order_with_a_clamped_start_cut(scenario, hprof):
    beta, gamma = anchor(scenario, 1, "Beta Pattern"), anchor(scenario, 1, "Gamma Pattern")
    assert beta["col"] == "left" and gamma["col"] == "right" and beta["ord"] < gamma["ord"]
    assert beta["bodyAbove"] == 10 and beta["linesAbove"] == 11                     # alpha's tail + the banner (not body)
    banner_bottom = max(ln[1] for ln in page_lines(scenario["book"][1]) if ln[4] == "FULL PATTERNS")
    assert banner_bottom <= beta["titleTop"] < beta["y"]                           # the cut sits in the gap below the banner
    delta = anchor(scenario, 2, "Delta Pattern")
    assert delta["col"] == "right" and delta["bodyAbove"] == 30 and delta["titleTop"] < delta["y"]   # the whole left column reads first
    alpha = anchor(scenario, 0, "Alpha Pattern")
    assert alpha["linesAbove"] == 0 and alpha["method"] == "heading"


def test_plans_cut_mid_column_right_column_and_page_top_starts_and_stop_at_a_stop(scenario):
    p = scenario["plans"]
    assert (p["Alpha Pattern"]["sheet0"], p["Alpha Pattern"]["sheet1"]) == (0, 1)
    assert p["Alpha Pattern"]["startCut"] is None
    assert p["Alpha Pattern"]["endCut"] == anchor(scenario, 1, "Beta Pattern")["titleTop"] and p["Alpha Pattern"]["endCol"] == "left"
    # Beta starts and ends on the same sheet: its end is Gamma's heading in the other column
    b = p["Beta Pattern"]
    assert (b["sheet0"], b["sheet1"]) == (1, 1) and b["startCut"] == anchor(scenario, 1, "Beta Pattern")["titleTop"] and b["startCol"] == "left"
    assert b["endCut"] == anchor(scenario, 1, "Gamma Pattern")["titleTop"] and b["endCol"] == "right" and b["flags"] == []
    # Gamma ends at Delta, which starts at the TOP of the right column with a full left column — a cut, not a page hand-over
    g = p["Gamma Pattern"]
    assert (g["sheet0"], g["sheet1"]) == (1, 2) and g["startCol"] == "right"
    assert g["endCut"] == anchor(scenario, 2, "Delta Pattern")["titleTop"] and g["endCol"] == "right"
    # Delta: Epsilon owns the whole next page
    d = p["Delta Pattern"]
    assert (d["sheet0"], d["sheet1"]) == (2, 2) and d["startCol"] == "right" and d["endCut"] is None and d["nextFormula"] == "Epsilon Pattern"
    # Epsilon ends at the stop row; Zeta has nothing after it
    e = p["Epsilon Pattern"]
    assert (e["sheet0"], e["sheet1"]) == (3, 4) and e["endCut"] == anchor(scenario, 4, "Self-assessment questions")["titleTop"]
    assert e["nextFormula"] == "Self-assessment questions" and e["flags"] == []
    assert "span-clamped" in p["Zeta Pattern"]["flags"]
    assert all("heading-not-found" not in q["flags"] for q in p.values())


def test_a_missing_heading_is_a_flagged_whole_page_start_and_no_heading_is_a_deliberate_one(scenario, hprof):
    index = [dict(pg, anchors=list(pg["anchors"])) for pg in scenario["index"]]
    located, missing = add_heading_anchors(index, scenario["book"], [("Ghost", "Ghost Pattern", 4), ("Range", "", 7)], hprof)
    assert located == 0 and missing == ["Ghost"]
    ghost = next(a for a in index[3]["anchors"] if a["name"] == "Ghost")
    assert ghost["synthetic"] and ghost["method"] == "heading-missing" and ghost["col"] == "full"
    assert "heading-not-found" in plan_headings("Ghost", 4, index, hprof)["flags"]
    rng = next(a for a in index[6]["anchors"] if a["name"] == "Range")
    assert rng["method"] == "page" and not rng["synthetic"]
    assert "heading-not-found" not in plan_headings("Range", 7, index, hprof)["flags"]


def test_redaction_keeps_ours_removes_the_neighbours_and_keeps_header_and_folio(scenario, hprof):
    d, book, p = scenario["dir"], scenario["book"], scenario["plans"]
    for name in ("Alpha Pattern", "Beta Pattern", "Gamma Pattern", "Delta Pattern"):
        write_excerpt(book, p[name], d / f"{name}.pdf", True, hprof)
    text = {n: "".join(pg.get_text() for pg in fitz.open(d / f"{n}.pdf")) for n in ("Alpha Pattern", "Beta Pattern", "Gamma Pattern", "Delta Pattern")}
    assert "alpha body" in text["Alpha Pattern"] and "alpha tail" in text["Alpha Pattern"] and "Beta Pattern" not in text["Alpha Pattern"]
    assert "Beta Pattern" in text["Beta Pattern"] and "beta body" in text["Beta Pattern"] and "beta right" in text["Beta Pattern"]
    assert "Gamma Pattern turning into Heat" in text["Beta Pattern"]                                  # the cross-reference is Beta's own text
    assert "alpha tail" not in text["Beta Pattern"] and "FULL PATTERNS" not in text["Beta Pattern"] and "gamma body" not in text["Beta Pattern"]
    assert "turning into Heat" in text["Gamma Pattern"] and "gamma tail" in text["Gamma Pattern"] and "Delta Pattern" not in text["Gamma Pattern"]
    assert "beta body" not in text["Gamma Pattern"]
    assert "Delta Pattern" in text["Delta Pattern"] and "delta body" in text["Delta Pattern"] and "gamma tail" not in text["Delta Pattern"]
    assert "Chapter 1 - Test Formulas" in text["Delta Pattern"] and "\n3\n" in text["Delta Pattern"]
    # start rects for a right-column start: the whole left column and the right column above the heading
    w, h = book[0].rect.width, book[0].rect.height
    rects = cut_rects(p["Delta Pattern"], w, h, hprof)
    split = hprof.column_split * w
    assert (0, (0, hprof.redact_top, split, h - hprof.footer_band)) in rects
    assert (0, (split, hprof.redact_top, w, p["Delta Pattern"]["startCut"])) in rects


def test_verify_reports_a_leaked_heading_and_nothing_on_clean_excerpts(scenario, hprof):
    d, book, index, p = scenario["dir"], scenario["book"], scenario["index"], scenario["plans"]
    leaky = dict(p["Alpha Pattern"], endCut=None, flags=[])
    write_excerpt(book, leaky, d / "leaky.pdf", True, hprof)
    leaks = verify_headings(d / "leaky.pdf", "Alpha Pattern", leaky, index, hprof)
    assert any("Beta Pattern" in x for x in leaks) and any("Gamma Pattern" in x for x in leaks)
    for name in ("Alpha Pattern", "Beta Pattern", "Gamma Pattern", "Delta Pattern"):
        assert verify_headings(d / f"{name}.pdf", name, p[name], index, hprof) == []             # Beta keeps its smaller cross-reference to Gamma
    assert truncation_check(p["Delta Pattern"], index, hprof) is None
    assert truncation_check(p["Zeta Pattern"], index, hprof) is None


def test_cli_end_to_end_in_headings_mode(tmp_path):
    info = heading_book(tmp_path / "book.pdf")
    entries = tmp_path / "entries.json"
    entries.write_text(json.dumps(info["entries"]))
    out = tmp_path / "out"
    logs: list[str] = []
    rc = main(["--pdf", info["pdf"], "--out", str(out), "--profile", str(HEADINGS_PROFILE),
               "--entries", str(entries), "--preview", "--verify"], log=logs.append)
    assert rc == 0 and any("7 heading anchor(s) located" in l for l in logs)
    man = {e["formula"]: e for e in json.loads((out / "manifest.json").read_text())}
    assert set(man) == {e["name"] for e in info["entries"] if not e.get("stop")}       # no row for the stop
    assert man["Beta Pattern"]["printedPages"] == [2, 2] and man["Gamma Pattern"]["printedPages"] == [2, 3]
    assert man["Epsilon Pattern"]["printedPages"] == [4, 5] and man["Epsilon Pattern"]["nextFormula"] == "Self-assessment questions"
    assert all("leak" not in e["flags"] and "possible-truncation" not in e["flags"] and "heading-not-found" not in e["flags"]
               for e in man.values())
    assert man["Beta Pattern"]["profile"].startswith("test-headings@") and man["Beta Pattern"]["kind"] == "monograph"
    assert (out / "Delta Pattern.pdf").exists() and (out / "review" / "Gamma-Pattern-first.png").exists()
    rc = main(["--pdf", info["pdf"], "--out", str(out), "--profile", str(HEADINGS_PROFILE),
               "--entries", str(entries), "--only", "Beta Pattern", "--preview"], log=logs.append)
    man2 = {e["formula"]: e for e in json.loads((out / "manifest.json").read_text())}
    assert rc == 0 and set(man2) == set(man) and man2["Beta Pattern"] == man["Beta Pattern"] and man2["Delta Pattern"] == man["Delta Pattern"]
