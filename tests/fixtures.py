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
