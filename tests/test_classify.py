import fitz

from monograph_splitter.classify import is_script_line, latin_key, name_ratio
from monograph_splitter.index import index_page, page_anchors, page_lines
from tests.fixtures import FakeBook


def anchors_of(book: FakeBook, prof, sheet: int = 0):
    return page_anchors(page_lines(book.doc[sheet]), book.doc[sheet].rect.width, prof)


def test_script_line_needs_mostly_script_glyphs_but_a_single_glyph_counts(prof):
    assert is_script_line("桂枝汤", prof)
    assert is_script_line("贯", prof)                      # a shattered title fragment
    assert not is_script_line("δì Nì 丁ang", prof)         # a stray glyph inside a Latin title
    assert not is_script_line("Pinyin Name: X", prof)


def test_latin_key_and_name_ratio_survive_ocr_accents():
    assert latin_key("Dà Qín Jíao Tang (Major Gentiana…)") == "daqinjiaotang"
    assert name_ratio("Da Qin Jiao Tang", "", "Dà Qín Jíao Tang (Major…)") > 0.9
    assert name_ratio("Da Qin Jiao Tang", "Da Qin liao Tang", "") > 0.85


def test_monograph_vs_sub_entry_by_title_size(prof):
    b = FakeBook()
    b.page(1); y = b.monograph(60, "Alpha Tang", "阿尔法汤"); y = b.body(y + 20, 6)
    b.related(y + 20, "Gamma Wan", "伽马丸")
    a = anchors_of(b, prof)
    kinds = [(x["kind"], x["name"], x["col"]) for x in a]
    assert kinds == [("monograph", "Alpha Tang", "full"), ("related", "Gamma Wan", "left")]
    assert a[0]["linesAbove"] == 0 and a[0]["bodyAbove"] == 0           # starts the page
    assert a[1]["method"] == "related-heading" and not a[1]["uncertain"]


def test_a_title_shattered_into_glyph_lines_is_still_a_monograph(prof):
    b = FakeBook()
    b.page(1)
    for i, ch in enumerate("贯前一"):                                  # three single-glyph 17pt lines
        b.text(60 + 24 * i, ch, size=17, x=90 + 20 * i, font="china-s")
    b.text(150, "Pinyin Name: Yi Guan Jian"); b.text(162, "Literal Name: Linking Decoction")
    a = anchors_of(b, prof)
    assert a[0]["kind"] == "monograph"


def test_a_big_latin_line_with_no_script_line_after_it_is_uncertain(prof):
    b = FakeBook()
    b.page(1, subheader="Previous Tang")                                 # big line in the band, nothing Chinese
    b.text(120, "Pinyin Name: Orphan Wan"); b.text(132, "Literal Name: Orphan")
    a = anchors_of(b, prof)
    assert a[0]["kind"] == "related" and a[0]["uncertain"]


def test_sub_entry_under_a_running_sub_header_is_not_uncertain(prof):
    b = FakeBook()
    b.page(1, subheader="Previous Tang")
    b.related(100, "Gamma Wan", "伽马丸", heading=False)            # its own 8.5pt Chinese line follows the big line
    a = anchors_of(b, prof)
    assert a[0]["kind"] == "related" and not a[0]["uncertain"]


def test_chapter_summary_page_is_a_break_and_a_body_page_is_not(prof):
    b = FakeBook()
    b.summary_page(1)
    b.page(2); b.body(90, 5, prefix="in summary, the formula")            # prose containing the word is NOT a break
    assert index_page(b.doc[0], prof)["chapterBreak"] is True
    assert index_page(b.doc[1], prof)["chapterBreak"] is False


def test_parent_heading_becomes_a_heading_anchor_after_the_left_column(prof):
    b = FakeBook()
    b.page(1); b.body(90, 5); b.related(200, "Gamma Wan", "伽马丸"); b.heading(150, col="right")
    a = anchors_of(b, prof)
    assert [(x["kind"], x["col"]) for x in a] == [("related", "left"), ("heading", "right")]
    assert a[1]["ord"] > a[0]["ord"]                                       # right column reads after left
