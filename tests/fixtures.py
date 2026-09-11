"""
A synthetic two-column "book" in Chen & Chen's typography, built with PyMuPDF so
the engine can be tested without the copyrighted scan. Sizes mirror the real
OCR layer: 9pt body, 19pt entry titles, 17pt Chinese title lines, 9pt sub-entry
titles with 8.5pt Chinese, 19pt running sub-headers, 9pt chapter running head.
Coordinates are BASELINES (PyMuPDF insert_text); glyph tops land ≈1.07×size above.
"""

from __future__ import annotations

import fitz

W, H = 522.72, 789.6
LEFT, RIGHT = 53, 271
LINE = 12


class FakeBook:
    def __init__(self) -> None:
        self.doc = fitz.open()
        self.cur = None

    # ── pages ───────────────────────────────────────────────────────────────
    def page(self, number: int | None = None, subheader: str | None = None,
             chapter: str = "Chapter 1 - Test Formulas", section: str = "Section 1 - Testing"):
        pg = self.doc.new_page(width=W, height=H)
        pg.insert_text((54, 35), chapter, fontsize=9)
        pg.insert_text((300, 35), section, fontsize=9)
        if subheader:
            pg.insert_text((71, 60), subheader, fontsize=19)          # running sub-header, top ≈ 39.6
        if number is not None:
            pg.insert_text((54, 770), str(number), fontsize=8)         # printed page number, in the footer band
        self.cur = pg
        return self

    def summary_page(self, number: int | None = None, chapter_no: int = 1):
        self.page(number)
        self.cur.insert_text((71, 60), f"chapter {chapter_no} -", fontsize=19)
        self.cur.insert_text((71, 130), "Summary", fontsize=19)
        self.body(170, 6, prefix="summary table row")
        return self

    # ── content ─────────────────────────────────────────────────────────────
    def text(self, y: float, s: str, size: float = 9, x: float = LEFT, font: str = "helv") -> float:
        self.cur.insert_text((x, y), s, fontsize=size, fontname=font)
        return y + LINE

    def body(self, y: float, n: int, col: str = "left", prefix: str = "body") -> float:
        x = LEFT if col == "left" else RIGHT
        for i in range(n):
            self.text(y + LINE * i, f"{prefix} line {i} of the running prose text", x=x)
        return y + LINE * n

    def monograph(self, y: float, name: str, cjk: str = "桂枝汤", english: str = "(Test Decoction)") -> float:
        """A full entry header block; y is the title baseline. Returns the next free baseline."""
        self.text(y, name, size=19, x=71)
        self.text(y + 22, english, size=12, x=71)
        self.text(y + 47, cjk, size=17, x=73, font="china-s")
        self.text(y + 70, cjk, size=17, x=73, font="china-s")
        self.text(y + 95, f"Pinyin Name: {name}")
        self.text(y + 107, "Literal Name: Test Literal Name")
        self.text(y + 119, "Original Source: Test Source (Test Book) by Tester in 1900")
        return y + 143

    def related(self, y: float, name: str, cjk: str = "小柴胡汤", col: str = "left", heading: bool = True) -> float:
        """A RELATED FORMULA sub-entry in one column; y is the heading (or title) baseline."""
        x = LEFT if col == "left" else RIGHT
        if heading:
            y = self.text(y, "RELATED FORMULAS", size=9.5, x=x) + 4
        y = self.text(y, name, size=9.5, x=x)
        y = self.text(y, cjk, size=8.5, x=x + 2, font="china-s")
        y = self.text(y, f"Pinyin Name: {name}", x=x)
        y = self.text(y, "Literal Name: Test Sub-entry", x=x)
        y = self.text(y, "Original Source: Test Source", x=x)
        return y

    def heading(self, y: float, text: str = "AUTHORS' COMMENTS", col: str = "right") -> float:
        return self.text(y, text, size=9.5, x=LEFT if col == "left" else RIGHT) + 4

    # ── Bensky Materia Medica-like typography (entries start inside a column) ──
    def herb(self, y: float, pinyin: str, pharm: str, col: str = "left") -> float:
        """A Materia Medica header: a 12.5pt italic pinyin line, then small-caps labels whose
        values sit on the same baseline to the right. y is the pinyin baseline; returns the
        next body baseline."""
        x = LEFT if col == "left" else RIGHT
        self.text(y, pinyin, size=12.5, x=x, font="heit")
        self.text(y + 20, "PHARMACEUTICAL NAME", size=8, x=x)
        self.text(y + 20, pharm, size=9, x=x + 100)
        self.text(y + 33, "FAMILY", size=8, x=x)
        self.text(y + 33, "Testaceae", size=9, x=x + 100)
        self.text(y + 46, "STANDARD SPECIES", size=8, x=x)
        self.text(y + 59, "ENGLISH", size=8, x=x)
        self.text(y + 59, "test root", size=9, x=x + 100)
        return y + 78

    # ── Maciocia-like typography (headings mode) ─────────────────────────────
    def pattern(self, y: float, name: str, col: str = "left", wrap: str | None = None, margin: str | None = None) -> float:
        """A pattern heading (12.5pt bold, column-bound), optionally wrapped over two lines
        13.5pt apart with a small margin caption interleaved, then the 12pt 'Clinical
        manifestations' sub-heading. y is the heading baseline; returns the next body baseline."""
        x = LEFT if col == "left" else RIGHT
        self.text(y, name, size=12.5, x=x, font="hebo")
        if wrap:
            if margin:
                self.text(y + 6, margin, size=8, x=x + 190)
            y += 13.5
            self.text(y, wrap, size=12.5, x=x, font="hebo")
        self.text(y + 20, "Clinical manifestations", size=12, x=x, font="hebo")
        return y + 36

    def banner(self, y: float, text: str = "FULL PATTERNS", col: str = "left") -> float:
        self.text(y, text, size=13, x=LEFT if col == "left" else RIGHT, font="hebo")
        return y + 16

    def save(self, path) -> str:
        self.doc.save(str(path))
        return str(path)


def scenario_book(path) -> dict:
    """The standard scenario (printed page = sheet + 1 under tests/profile-test.toml):
      p1  Alpha Tang starts at the top; body in both columns
      p2  Alpha continues (running sub-header); Beta San starts mid-page (full-width title)
      p3  Beta continues (left column) → RELATED FORMULAS → Gamma Wan (left column, continues
          into the right column top) → AUTHORS' COMMENTS (right column) = Beta's trailing section
      p4  Delta Yin starts at the top
      p5  chapter Summary page (a hard stop)
      p6  Epsilon Tang starts at the top
      p7  Epsilon continues; Zeta Wan starts mid-page
      p8  Eta San starts at the top (the book ends there)
    """
    b = FakeBook()
    b.page(1); y = b.monograph(60, "Alpha Tang", "阿尔法汤"); b.body(y + 20, 20); b.body(y + 20, 20, "right")
    b.page(2, subheader="Alpha Tang"); b.body(90, 16); b.body(90, 16, "right")
    y = b.monograph(330, "Beta San", "贝塔散"); b.body(y + 12, 10); b.body(y + 12, 10, "right")
    b.page(3, subheader="Beta San"); b.body(90, 12, prefix="beta body")
    y = b.related(290, "Gamma Wan", "伽马丸"); b.body(y + 8, 10, prefix="gamma body")
    b.body(90, 10, "right", prefix="gamma continued"); b.heading(230); b.body(250, 12, "right", prefix="beta comments")
    b.page(4, subheader=None); y = b.monograph(60, "Delta Yin", "德尔塔饮"); b.body(y + 20, 20)
    b.summary_page(5)
    b.page(6); y = b.monograph(60, "Epsilon Tang", "艾普西龙汤"); b.body(y + 20, 20)
    b.page(7, subheader="Epsilon Tang"); b.body(90, 10); y = b.monograph(300, "Zeta Wan", "泽塔丸"); b.body(y + 12, 10)
    b.page(8); y = b.monograph(60, "Eta San", "伊塔散"); b.body(y + 20, 10)
    b.save(path)
    return {
        "pdf": str(path),
        "entries": [
            {"name": "Alpha Tang", "page": 1}, {"name": "Beta San", "page": 2}, {"name": "Gamma Wan", "page": 3},
            {"name": "Delta Yin", "page": 4}, {"name": "Epsilon Tang", "page": 6}, {"name": "Zeta Wan", "page": 7},
            {"name": "Eta San", "page": 8},
        ],
    }


def heading_book(path) -> dict:
    """The headings-mode scenario (printed page = sheet + 1 under tests/profile-headings.toml):
      p1  Alpha Pattern at the top of the left column; body in both columns
      p2  left: Alpha's tail, a FULL PATTERNS banner, Beta Pattern mid-column, and inside
          Beta's body an 11.5pt italic cross-reference repeating Gamma's heading;
          right: Beta's body, then Gamma Pattern wrapped over two lines with an 8pt margin
          caption interleaved, then Gamma's body
      p3  left column full of Gamma's tail; Delta Pattern at the top of the RIGHT column
      p4  Epsilon Pattern at the top of the left column (the whole page is Epsilon's)
      p5  Epsilon continues; a 9.5pt 'Self-assessment questions' box title = a stop
      p6  chapter opener (66pt number, 24pt title) — no anchors at all
      p7  Zeta Pattern at the top-left (the book ends there)
    """
    b = FakeBook()
    b.page(1); y = b.pattern(60, "Alpha Pattern"); b.body(y, 20, prefix="alpha body"); b.body(80, 20, "right", prefix="alpha right")
    b.page(2); y = b.body(80, 10, prefix="alpha tail"); y = b.banner(y + 30); y = b.pattern(y + 26, "Beta Pattern"); y = b.body(y, 8, prefix="beta body")
    b.text(y + 4, "Gamma Pattern turning into Heat", size=11.5, font="heit")           # a cross-reference in Beta's body, smaller type
    b.body(y + 20, 4, prefix="beta body more")
    y = b.body(80, 8, "right", prefix="beta right"); y = b.pattern(y + 30, "Gamma Pattern", col="right", wrap="turning into Heat", margin="34")
    b.body(y, 12, "right", prefix="gamma body")
    b.page(3); b.body(80, 30, prefix="gamma tail"); y = b.pattern(70, "Delta Pattern", col="right"); b.body(y, 20, "right", prefix="delta body")
    b.page(4); y = b.pattern(60, "Epsilon Pattern"); b.body(y, 20, prefix="epsilon body")
    b.page(5); y = b.body(80, 10, prefix="epsilon tail"); b.text(y + 20, "Self-assessment questions", size=9.5, font="hebo"); b.body(y + 40, 8, prefix="questions")
    b.page(6); b.text(200, "35", size=66, x=300); b.text(260, "Lung Patterns", size=24, x=200)
    b.page(7); y = b.pattern(60, "Zeta Pattern"); b.body(y, 10, prefix="zeta body")
    b.save(path)
    return {
        "pdf": str(path),
        "entries": [
            {"name": "Alpha Pattern", "page": 1, "heading": "Alpha Pattern"},
            {"name": "Beta Pattern", "page": 2, "heading": "Beta Pattern"},
            {"name": "Gamma Pattern", "page": 2, "heading": "Gamma Pattern turning into Heat"},
            {"name": "Delta Pattern", "page": 3, "heading": "Delta Pattern"},
            {"name": "Epsilon Pattern", "page": 4, "heading": "Epsilon Pattern"},
            {"name": "Self-assessment questions", "page": 5, "heading": "Self-assessment questions", "stop": True},
            {"name": "Zeta Pattern", "page": 7, "heading": "Zeta Pattern"},
        ],
    }
