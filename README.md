# monograph-splitter

Per-entry PDF excerpts from a scanned reference book.

A scanned textbook with an OCR text layer goes in; one small, clean PDF per
entry (monograph) comes out — each holding exactly that entry's pages, with the
neighbouring entries' share of a shared page removed by real redaction — plus a
manifest, a review sheet and a hand-override file.

It is *not* an OCR tool and not a generic "PDF splitter": it finds **where each
entry starts and ends on the page**, in a two-column book whose entries carry a
recognisable header block, and cuts there. Built for Chen & Chen's two TCM
reference books (526 monographs, 0 leaks / 0 truncations on `--verify`); the
book-specific knowledge lives in a TOML *profile*, so another book needs a
profile, not code.

```
uv tool install git+https://github.com/BigSpoon33/pdf-splitter-engine
monograph-splitter --pdf book.pdf --profile chen-chen-herbology \
    --entries entries.json --out out/ --preview --verify
```

## How it works

Three layers:

| Layer | What it holds | Reusable for another book? |
|---|---|---|
| **Engine** (`index`, `classify`, `cuts`, `render`, `verify`) | text-layer indexing (y, x, glyph size per line); label-block clustering into anchors; entry-vs-sub-entry classification by *type-size signature*; chapter-break stops; two-column reading order with **bands** (a full-width title splits a page into an upper and lower band; inside a band the left column reads before the right); column/band-aware redaction rectangles; manifest + `overrides.json` + review sheet; `--verify` (foreign headers = `leak`, dropped tail = `possible-truncation`); context-based resolution of ambiguous headers; continuation-page walk-back | yes — nothing here knows the book |
| **Profile** (`profiles/*.toml`) | header labels (OCR-tolerant regexes), the type-size signature of a title, chapter-break words, an entry's trailing parent headings, column split ratio, printed-page↔sheet offset, header/footer bands | no — every value is one book's layout |
| **Entries** (`entries.py`) | the list of entries and their printed start pages: `--entries entries.json`, or `--from-vault` (markdown notes with `source_page:` frontmatter) | your data |

### The decisions, and the failure each rule came from

1. **Index the book once** (≈5 min per 1,600 sheets, cached in `.book-index.json`): every line with its y, x, text and glyph size.
2. **Anchors** = header label blocks (`Pinyin Name…` etc.), clustered within `cluster_gap` in the same column. OCR mangles titles far more than labels, so labels are the anchor, not titles.
3. **Entry vs sub-entry.** Both carry the same labels; the discriminator is the title above them — an entry has big script lines (a "mostly-CJK line ≥ `script_min_size`"), a sub-entry's are body-sized. Fallbacks: a title the OCR shatters into single glyphs still counts; `big_lines_min` big lines are an entry signature by themselves. A Latin line with one stray CJK glyph is *not* a script line.
4. **Titles that start a page sit inside the running-header band**, so title detection looks from `title_min_y` while cut/line counting keeps the band.
5. **Chapter breaks** are big (≥ `breaks.min_size`) headings *anywhere on the page* — position-based band exclusion missed a 19pt "Summary" at y≈45.
6. **Extent.** An entry runs to the next entry title (its own sub-entries belong to it); a sub-entry runs to the next header of any kind or the parent's trailing headings.
7. **Cut point** = the largest vertical gap within `gap_window` above the label, measured between consecutive lines from the bottom of every line above the window — two-column pages interleave lines by y.
8. **Column geometry** from `column_split` (a ratio of the page width; W/2 misfiles headings in this book). Sub-entry cuts are column- and band-aware; entry cuts are full-width.
9. **Banner vs tail.** Big type above a title is a section banner (kept or handed on); body-sized text above a title is the previous entry's tail — decided by glyph size, never by line count.
10. **Ambiguity.** A big Latin line with no script after it is "uncertain": promoted to an entry only by context mid-page; in the top band it stays a sub-entry and is flagged.
11. **Wrong start pages.** A continuation page is recognised by the running sub-header matching the entry name and walked back up to `walk_back` sheets.
12. **Redaction, not cropping:** text and image pixels under the rectangles are removed; the running header and the page number are kept, and an excerpt's text layer parses as one entry.
13. **A cut above our own labels is a bug, not a boundary** (a label cluster split by a too-small `cluster_gap`): flagged `ends-inside-own-header`.

## Profiles

Every number the engine uses comes from the profile. The built-in defaults
*are* `chen-chen-formulas`; a profile overrides any subset; an unknown table or
key is an error naming it. The manifest records `profile: "<name>@<sha256[:8]>"`
per entry, and the index cache is keyed on engine version + profile hash.

| Table | Keys | What they encode |
|---|---|---|
| `[book]` | `sheet_offset` | printed page N = sheet N+offset−1 (calibrate on two known entries) |
| `[layout]` | `header_band`, `redact_top`, `subheader_bottom`, `footer_band`, `column_split`, `full_width_ratio`, `title_min_y` | running headers, page number, column gutter; how wide a line must be to span both columns |
| `[title]` | `big_min_size`, `script_min_size`, `script_regex`, `script_ratio`, `body_max_size`, `big_lines_min`, `title_reach`, `gap_window`, `min_gap` | the type-size signature of an entry title vs a sub-entry vs body; what "the script" is (`script_regex = ''` for a single-script book — lean on `big_lines_min`) |
| `[labels]` | `name`, `secondary`, `cluster_gap`, `related_heading`, `related_reach`, `block_reach`, `estimate_above`, `parent_headings`, `parent_size`, `parent_max_len` | the header label lines that anchor an entry, the sub-entry heading, an entry's trailing sections |
| `[breaks]` | `patterns`, `chapter_only`, `min_size`, `max_len` | the big headings that end a chapter |
| `[limits]` | `max_span`, `long_span`, `name_match`, `walk_back` | safety clamp, review threshold, name-match ratio, continuation walk-back |

Regexes are TOML literal strings (`'…'`). Bundled: `chen-chen-formulas`
(Chen & Chen, *Chinese Herbal Formulas and Applications*) and
`chen-chen-herbology` (Chen & Chen, *Chinese Medical Herbology and
Pharmacology* — seven keys differ from the formulas book, each a measurement).
Writing a profile for a new book: calibrate `sheet_offset` on two entries,
measure `column_split` from where right-column lines start, take the label
lines from one header block, and watch the mean excerpt length of the first
run — a mean near one page means the label block is being split in two
(`cluster_gap` too small).

## Entries

`--entries entries.json` — `[{"name": "Gui Zhi Tang", "page": 51}, …]`; the
name becomes the output filename and the manifest key. `--known-pages
<json>…` adds start pages from a TOC/index map so a sheet where the OCR found no
header still gets a synthetic anchor. `--from-vault <dir>` (`--vault-folder`)
reads markdown notes with a `source_page:` frontmatter field, one entry per
note.

## Outputs

| File | Purpose |
|---|---|
| `<Entry>.pdf` | the excerpt |
| `manifest.json` | per entry: printed pages, cut y + column + band, kind, next entry, flags (need a human), notes (informational), leaks |
| `overrides.json` | hand corrections: `{"Name": {"pages": [a, b], "startCut": y\|null, "startCol": "full\|left\|right", "endCut": y\|null, "endCol": …}}`, any subset (`--help-overrides`) |
| `review/index.html` + PNGs | first/last page of every excerpt with the **removed regions hatched** and a y-ruler; flagged entries first |
| `.book-index.json` | the cached raw index |

**Fix one entry by hand:** read the y off the review PNG's ruler → add it to
`overrides.json` → re-run `--only "Name" --preview --verify`.

**Flags** (review): `start-name-fuzzy`, `passed-uncertain-header`,
`kind-uncertain`, `start-page-corrected`, `start/end-cut-estimated`,
`end-at-known-start`, `end-at-promoted-header`, `ends-inside-own-header`,
`long-span`, `span-clamped`, `leak`, `possible-truncation`, `override` ·
**notes** (informational): `related-entry`, `uncut-banner-above`,
`end-at-chapter-break`, `end-at-related-entry`, `end-at-parent-heading`.

## Regression gate

Any engine change must leave a known-good book's boundary decisions identical:

```
monograph-splitter-diff out/manifest.prev.json out/manifest.json   # exit 0 = identical decisions
```

## Development

```
uv run --group dev pytest          # synthetic-book suite; no test reads a real book
```

`tests/fixtures.py` builds a two-column book in the same typography (body,
titles, script lines, sub-entry script, running sub-headers, a Summary page);
the suite covers classification incl. shattered titles and uncertainty,
full-width / column / band cuts, the parent-heading and chapter-break stops,
continuation-page walk-back, overrides, redaction, `--verify`, the profile
loader's errors, the entries adapters and the CLI end to end.

## Prerequisites and limits

- The PDF needs an **OCR text layer with glyph sizes** (a pure-image PDF needs
  `ocrmypdf` first).
- Redaction removes what is under the rectangles; it cannot reflow a column, so
  two sub-entries sharing a column on the same line would need a hand override.
- Redaction and cross-column figures do not mix. A figure, box or table that
  spans both columns on a redacted first/last page loses the half that sits in
  the neighbouring column (Maciocia Foundations, *Blood stasis of the
  Pericardium*: Fig. 33.13's left box is gone, the arrows survive). The text
  layer is untouched. Not fixed yet — candidates: carve image/drawing bboxes
  wider than `full_width_ratio` out of the redaction rectangles (accepting the
  leak), or flag such pages `cross-column-figure` in the review sheet.
- Two books proven; a differently laid-out book is the real test of generality.
- The excerpts are copies of copyrighted pages: keep them where the book's
  license lets you keep the book.

## License

MIT.

## Headings mode (books without label blocks)

Chen & Chen introduce every monograph with a label block (`Pinyin Name:` …), and the
engine finds those on its own. Maciocia's *Foundations* introduces a pattern with a bare
12.5pt heading and nothing else, so there is nothing for the label rules to anchor on.
A profile with `[anchors] source = "headings"` switches the anchor source: the entries
list carries each entry's `heading` text, and the engine locates it on the given start
sheet (`index.locate_heading` — a window of 1–3 consecutive big lines in one column, so a
wrapped heading with an interleaved margin title still matches; lines below
`heading_min_size` match only exactly). The located lines become the same anchor dict
the label path makes (`index.heading_anchor`: column, start cut `heading_pad` above the
heading clamped into the gap below the previous line, lines/body above counted in
reading order — left column before right), so `cuts.plan`, `cut_rects` and `render` do
not change. A row with `"stop": true` (a chapter tail, a group banner) plants an anchor
so the entry before it ends there but gets no excerpt. A heading that is not on its
sheet becomes a whole-page start flagged `heading-not-found`; a row with no `heading`
is a deliberate whole-page start. `--verify` uses `verify.verify_headings`: another
entry's heading still readable inside the excerpt is a `leak`.

    monograph-splitter --pdf foundations.pdf --profile maciocia-foundations \
        --entries entries.json --out pattern-sources --preview --verify

Bundled profile: `maciocia-foundations` (sheet offset 29, two columns split at 0.45,
header band 46 / redaction from 52 so the running part number survives, footer band 36).


## Web mode (library use, no files per job)

The pdf-splitter web service drives the engine with a settings dict and an in-memory
section list:

```python
from monograph_splitter.profile import profile_from_dict
from monograph_splitter.session import Book

prof = profile_from_dict({"column_split": 0.5, "heading_min_size": 13, "single_column": False})
book = Book.open(pdf=pdf, out=job_dir, profile=prof,
                 entries=[{"name": "Chapter 1", "page": 3, "heading": "Introduction"}])
```

- `profile_from_dict(d, base=WEB_BASE)` accepts only `WEB_KEYS` (`column_split`,
  `header_band`, `footer_band`, `redact_top`, `heading_min_size`, `heading_match`,
  `heading_wrap_gap`, `max_span`, `single_column`); any other key, a wrong type or an
  out-of-range value is a `ProfileError` naming it. `WEB_BASE` is headings mode, no
  script-title or summary-page rules, `max_span = 200`, and a page is the **1-based sheet
  number** (PDF page 1 = the first sheet, i.e. engine `sheet_offset = 0`).
- `single_column: true` sets `column_split = 0.999` and `full_width_ratio = 0.0`: every
  line is in the left column and every heading full-width, so every cut spans the page.
- `sha256` is the hash of the effective values, so the index cache (and any
  settings-keyed cache) changes with the settings and not with key order.
- `Book.open(entries=...)` takes a JSON path, the rows themselves, or an `EntryList`;
  rows go through `entries.entries_from_rows`, the same validation `--entries` uses.

### Proposing sections (`detect`)

Without an entry list, `monograph_splitter.detect` proposes one. Both sources return rows
that `Book.open(entries=…)` takes unchanged (`page` = 1-based sheet, `heading` = the text
to locate), and neither writes anything or reads a page's text more than once:

```python
import fitz
from monograph_splitter import detect

doc = fitz.open(pdf)
detect.outline_levels(doc)          # [{level, count}] per outline depth; no outline → []
detect.outline_entries(doc, 1)      # [{name, page, heading, level, y?}]
detect.heading_candidates(doc, min_ratio=1.3, max_len=90, header_band=50, footer_band=32,
                          wrap_gap=16, column_split=0.487, full_width_ratio=0.55)
# → {body_size, levels: [{size, count}], candidates: [{name, page, heading, size, level, y, col}]}
```

- **Outline:** items at exactly `level` that land on a sheet (a broken or external link,
  page -1, is dropped). `heading` is the title without its leading number (`1.2 Scope` →
  `Scope`). `y` (page coordinates, y down) is present only when the destination names a
  point (`/XYZ` with a top, `/FitH`, `/FitBH`, `/FitR`, or a named destination that
  resolves to one); `/Fit` and `/XYZ null null` have none.
- **Big headings:** the body size is the char-weighted modal type size; a candidate is a
  line (or up to three wrapped lines, tops within `wrap_gap`) at ≥ `body × min_ratio`,
  outside the header/footer bands, ≤ `max_len` characters joined. Running headers/footers
  (the same text, digits aside, at the page edge of ≥ 30% of pages) and page-number-only
  lines never count. Sizes within 0.5 pt share a `level` (largest = 1); `col` is
  `left`/`right`/`full` by the column geometry passed in (use the web settings' profile
  values so it agrees with the cuts).
