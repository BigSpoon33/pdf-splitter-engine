# Docs: docs/tooling/monograph-splitter.md
"""
Writing the excerpt (real redaction: text AND image pixels under the cut
rectangles; running header and page number kept) and the review sheet.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from .cuts import cut_rects
from .profile import Profile


def write_excerpt(book, p: dict, dest: Path, redact: bool, prof: Profile) -> None:
    import fitz

    out = fitz.open()
    out.insert_pdf(book, from_page=p["sheet0"], to_page=p["sheet1"])
    if redact:
        pg0 = out[0]
        for i, r in cut_rects(p, pg0.rect.width, pg0.rect.height, prof):
            out[i].add_redact_annot(fitz.Rect(*r), fill=(1, 1, 1))
        for pg in out:
            if pg.first_annot:
                pg.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)
    out.save(dest, garbage=4, deflate=True)
    out.close()


def render_review(book, p: dict, slug: str, review_dir: Path, prof: Profile, dpi: int = 60) -> list[str]:
    """First/last sheet PNGs with the removed regions hatched red + a 50pt ruler."""
    import fitz

    names = []
    last = p["sheet1"] - p["sheet0"]
    doc = fitz.open()
    doc.insert_pdf(book, from_page=p["sheet0"], to_page=p["sheet1"])
    w, h = doc[0].rect.width, doc[0].rect.height
    rects = cut_rects(p, w, h, prof)
    for which, i in (("first", 0), ("last", last)):
        if which == "last" and last == 0:
            continue
        pg = doc[i]
        sh = pg.new_shape()
        for y in range(50, int(h), 50):
            sh.draw_line((0, y), (14, y))
            sh.insert_text((16, y + 3), str(y), fontsize=6, color=(0.3, 0.3, 0.3))
        sh.finish(color=(0.3, 0.3, 0.3), width=0.5)
        for j, r in rects:
            if j == i:
                sh.draw_rect(fitz.Rect(*r))
        sh.finish(color=(1, 0, 0), fill=(1, 0, 0), fill_opacity=0.18, width=1.5)
        for key in ("startCut", "endCut"):
            if (key == "startCut") != (i == 0) and last != 0:
                continue                               # label the cut on the page it applies to
            if p.get(key) is not None:
                sh.insert_text((w - 70, p[key] - 3), f"{key[:-3]} {p[key]:.0f} {p.get(key[:-3] + 'Col', 'full')}",
                               fontsize=7, color=(1, 0, 0))
        sh.commit()
        name = f"{slug}-{which}.png"
        pg.get_pixmap(dpi=dpi).save(review_dir / name)
        names.append(name)
    doc.close()
    return names


def write_index_html(review_dir: Path, rows: list[dict], title: str = "excerpts") -> None:
    rows = sorted(rows, key=lambda r: (not r["flags"], r["formula"]))
    parts = [f"<!doctype html><meta charset=utf-8><title>{html.escape(title)} — review</title>",
             "<style>body{font:13px system-ui;margin:16px}table{border-collapse:collapse}td,th{border:1px solid #ccc;"
             "padding:6px;vertical-align:top}img{max-width:330px;border:1px solid #999}.flag{color:#b00;font-weight:600}"
             "code{background:#f4f4f4;padding:2px 4px;font-size:11px}</style>",
             f"<h1>{html.escape(title)} — {len(rows)} reviewed, {sum(1 for r in rows if r['flags'])} flagged</h1>",
             "<p>Red = removed. Ruler labels are y in PDF points — paste into <code>overrides.json</code> "
             "(<code>startCut</code>/<code>endCut</code>, plus <code>startCol</code>/<code>endCol</code> = full | left | right).</p>",
             "<table><tr><th>entry</th><th>pages · next</th><th>flags / notes</th><th>first page</th><th>last page</th><th>override snippet</th></tr>"]
    for r in rows:
        imgs = {n.rsplit("-", 1)[1][:-4]: n for n in r["review"]}
        snippet = json.dumps({r["formula"]: {"pages": r["printedPages"], "startCut": r["startCut"], "startCol": r["startCol"],
                                             "endCut": r["endCut"], "endCol": r["endCol"]}})
        parts.append(
            f"<tr><td><b>{html.escape(r['formula'])}</b><br><small>{r['kind']}</small></td>"
            f"<td>{r['printedPages'][0]}–{r['printedPages'][1]} ({r['pageCount']}p)<br>→ {html.escape(r['nextFormula'] or '—')}</td>"
            f"<td><span class=flag>{'<br>'.join(r['flags'])}</span><br><small>{'<br>'.join(r['notes'] + r.get('leaks', []))}</small></td>"
            f"<td>{'<img src=%s>' % imgs['first'] if 'first' in imgs else ''}</td>"
            f"<td>{'<img src=%s>' % imgs['last'] if 'last' in imgs else '<i>same page</i>'}</td>"
            f"<td><code>{html.escape(snippet)}</code></td></tr>")
    parts.append("</table>")
    (review_dir / "index.html").write_text("\n".join(parts), encoding="utf-8")
