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
uv tool install git+https://git.gumshu.duckdns.org/shuma/monograph-splitter
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
- Two books proven; a differently laid-out book is the real test of generality.
- The excerpts are copies of copyrighted pages: keep them where the book's
  license lets you keep the book.

## License

MIT.
