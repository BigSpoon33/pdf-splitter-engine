# Docs: docs/tooling/monograph-splitter.md
"""
One open book: the PDF, its profile, the cached index, the entry list, the
hand overrides and the manifest — and the operations the CLI and the review
editor share (plan an entry, cut it, persist an override, render a sheet).

The CLI (`cli.main`) and the web worker call `Book.cut_all()`, one loop over
`Book.cut()`; the review server (`review.server`) calls `cut` one entry at a time, so
an edit made in the browser produces byte-for-byte what a re-run of the CLI would.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from copy import deepcopy
from pathlib import Path

from .cuts import apply_overrides, cut_rects, plan, plan_headings
from .entries import Entry, EntryList, entries_from_rows, entries_from_vault, load_entries_json, load_known_pages, select
from .index import add_heading_anchors, add_known_starts, index_book
from .profile import Profile, load_profile
from .render import render_review, write_excerpt, write_index_html
from .verify import truncation_check, verify_excerpt, verify_headings

MANIFEST_NAME = "manifest.json"
OVERRIDES_NAME = "overrides.json"
SHEET_CACHE_DIR = ".sheets"


def slug_of(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")


def safe_filename(name: str) -> str:
    """`<name>.pdf`, or ValueError when the name could leave the output directory. Names
    stay the filenames the CLI's consumers read (`file`), so nothing is rewritten; a web
    section list is user-editable, so a separator or `..` is refused instead."""
    if not name or name in (".", "..") or any(c in name for c in ("/", "\\", "\0")):
        raise ValueError(f"entry name {name!r} can't be a file name (no '/', '\\', NUL, '.' or '..')")
    return f"{name}.pdf"


class Book:
    def __init__(self, doc, prof: Profile, out: Path, entry_list: EntryList, index: list[dict],
                 *, headings_mode: bool, log=print) -> None:
        self.doc = doc
        self.prof = prof
        self.out = Path(out)
        self.entry_list = entry_list
        self.index = index
        self.headings_mode = headings_mode
        self.log = log
        self.review_dir = self.out / "review"
        self.overrides: dict[str, dict] = self._read_json(self.out / OVERRIDES_NAME, {})
        rows = self._read_json(self.out / MANIFEST_NAME, [])
        self.manifest: dict[str, dict] = {m["formula"]: m for m in rows}
        self.missing: list[str] = list(entry_list.skipped)

    # ── construction ────────────────────────────────────────────────────────
    @classmethod
    def open(cls, *, pdf: Path, out: Path, profile: Profile | Path | str | None,
             entries: Path | str | list[dict] | EntryList | None = None, from_vault: Path | None = None, vault_folder: str = "TCM_Formulas",
             known_pages: list[Path] | None = None, log=print) -> "Book":
        import fitz  # pymupdf; imported late so --help works without it

        prof = profile if isinstance(profile, Profile) else load_profile(_resolve_profile(profile))
        doc = fitz.open(str(pdf))
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        index = index_book(doc, out / ".book-index.json", prof, log=log)
        # entries: a JSON file (the CLI), or the rows / EntryList in memory (the web worker —
        # no file per job); an empty list is a valid, empty book, not a missing argument
        if isinstance(entries, EntryList):
            el = entries
        elif isinstance(entries, list):
            el = entries_from_rows(entries)
        elif entries:
            el = load_entries_json(Path(entries))
        elif from_vault:
            el = entries_from_vault(Path(from_vault), vault_folder)
        else:
            raise ValueError("give entries=<json path | rows | EntryList> or from_vault=<path>")
        headings_mode = prof.anchor_source == "headings"
        if headings_mode:
            located, not_found = add_heading_anchors(index, doc, el.headings, prof)
            log(f"  headings mode: {located} heading anchor(s) located on their sheets"
                + (f", {len(not_found)} NOT found (whole-page start, flagged): {not_found}" if not_found else ""))
        known = set(el.known_pages)
        for kp in known_pages or []:
            known |= load_known_pages(Path(kp))
        synth = add_known_starts(index, {p + prof.sheet_offset - 1 for p in known}, prof)
        if synth:
            log(f"  {synth} known start sheet(s) had no detectable header — synthetic top-of-page anchors added")
        book = cls(doc, prof, out, el, index, headings_mode=headings_mode, log=log)
        ov_path = out / OVERRIDES_NAME
        if not ov_path.exists():
            ov_path.write_text("{}\n")
        return book

    @staticmethod
    def _read_json(path: Path, default):
        if not path.exists():
            return default
        text = path.read_text(encoding="utf-8")
        return json.loads(text) if text.strip() else default

    # ── entries ─────────────────────────────────────────────────────────────
    @property
    def entries(self) -> list[Entry]:
        return self.entry_list.entries

    def entry(self, name: str) -> Entry | None:
        """The entry by name. A name the entry list skipped (no printed page) still
        becomes an entry when an override gives it `pages` — the editor's way of
        mapping something the source never mapped."""
        for e in self.entries:
            if e.name == name:
                return e
        ov = self.overrides.get(name)
        if ov and isinstance(ov.get("pages"), list) and ov["pages"]:
            return Entry(name, int(ov["pages"][0]), "override")
        return None

    @property
    def skipped(self) -> list[str]:
        """Names with no usable start page and no override supplying one."""
        return [s for s in self.entry_list.skipped
                if not (self.overrides.get(s.split(" (")[0]) or {}).get("pages")]

    def sheet_of(self, printed: int) -> int:
        return printed + self.prof.sheet_offset - 1

    def printed_of(self, sheet: int) -> int:
        return sheet - self.prof.sheet_offset + 1

    @property
    def page_count(self) -> int:
        return self.doc.page_count

    def page_size(self, sheet: int = 0) -> tuple[float, float]:
        pg = self.index[sheet] if 0 <= sheet < len(self.index) else self.index[0]
        return float(pg.get("W") or self.doc[0].rect.width), float(pg.get("H") or self.doc[0].rect.height)

    # ── planning ────────────────────────────────────────────────────────────
    def auto_plan(self, entry: Entry) -> dict:
        """The engine's own decision for an entry, before any override."""
        if self.headings_mode:
            return plan_headings(entry.name, entry.page, self.index, self.prof)
        return plan(entry.name, entry.page, self.index, self.prof, aliases=self._aliases(entry))

    @staticmethod
    def _aliases(entry: Entry) -> tuple[str, ...]:
        """Labels mode: an entry's `heading` is the name its label block carries instead."""
        return (entry.heading,) if entry.heading else ()

    def planned(self, entry: Entry, override: dict | None = None, *, use_saved: bool = True) -> dict:
        """The plan after overrides: the given one, else the saved one for that name."""
        ov = override if override is not None else (self.overrides.get(entry.name) if use_saved else None)
        return apply_overrides(self.auto_plan(entry), ov, self.prof)

    def in_range(self, p: dict) -> bool:
        return 0 <= p["sheet0"] <= p["sheet1"] < self.page_count

    def rects(self, p: dict) -> list[dict]:
        """The redaction rectangles of a plan, as the review UI draws them:
        `{sheet: <index within the excerpt>, rect: [x0, y0, x1, y1]}` in PDF points, each
        sheet's rectangles in that sheet's own size (the index carries every sheet's W/H)."""
        w, h = self.page_size(p["sheet0"])
        rects = cut_rects(p, w, h, self.prof, last_size=self.page_size(p["sheet1"]))
        return [{"sheet": i, "rect": [round(v, 1) for v in r]} for i, r in rects]

    def anchors_on(self, sheet: int) -> list[dict]:
        """The headers the index knows on a sheet — where other entries start."""
        if not (0 <= sheet < len(self.index)):
            return []
        keep = ("name", "kind", "col", "y", "titleTop", "bandTop", "bandBottom", "uncertain", "synthetic", "method")
        return [{k: a.get(k) for k in keep if k in a} for a in self.index[sheet]["anchors"]]

    # ── cutting ─────────────────────────────────────────────────────────────
    def cut(self, entry: Entry, *, override: dict | None = None, preview: bool = False, verify: bool = False,
            redact: bool = True) -> dict | None:
        """Write the excerpt for one entry and record its manifest row. Returns the
        row, or None (and appends to `missing`) when the plan falls off the book."""
        dest = self.excerpt_path(entry.name)
        p = self.planned(entry, override)
        if not self.in_range(p):
            self.missing.append(f"{entry.name} (sheets {p['sheet0']}–{p['sheet1']} out of range)")
            return None
        write_excerpt(self.doc, p, dest, redact=redact, prof=self.prof)
        slug = slug_of(entry.name)
        if verify and redact:
            leaks = (verify_headings(dest, entry.name, p, self.index, self.prof) if self.headings_mode
                     else verify_excerpt(dest, entry.name, p["kind"], self.prof, aliases=self._aliases(entry)))
        else:
            leaks = []
        if leaks:
            p["flags"].append("leak")
        if verify:
            trunc = truncation_check(p, self.index, self.prof)
            if trunc:
                p["flags"].append("possible-truncation")
                leaks.append(f"truncation? {trunc}")
        if preview:
            self.review_dir.mkdir(exist_ok=True)
        review = render_review(self.doc, p, slug, self.review_dir, self.prof) if preview else []
        row = {
            # `formula` is the key the API's register-reference-excerpts reads (STORY-202) — kept for that consumer.
            "formula": entry.name,
            "file": dest.name,
            "printedPages": [self.printed_of(p["sheet0"]), self.printed_of(p["sheet1"])],
            "pageCount": p["sheet1"] - p["sheet0"] + 1,
            "kind": p["kind"],
            "startCut": p["startCut"], "startCol": p["startCol"], "startBand": p.get("startBand"),
            "endCut": p["endCut"], "endCol": p["endCol"], "endBand": p.get("endBand"),
            "nextFormula": p["nextFormula"],
            "flags": p["flags"],
            "notes": p["notes"],
            "leaks": leaks,
            "pageSource": "override" if "override" in p["flags"] else entry.source,
            "redacted": redact,
            "bytes": dest.stat().st_size,
            "review": review,
            "profile": self.prof.tag,
        }
        self.manifest[entry.name] = row
        return row

    def cut_all(self, progress: Callable[[int, int, str], None] | None = None, verify: bool = True,
                preview: bool = False, only: Iterable[str] | None = None, *, limit: int | None = None,
                redact: bool = True) -> dict:
        """Cut every entry (or the names in `only`, then the first `limit`), save the manifest
        and, when previewing, the review index. `progress(done, total, name)` fires after each
        entry, one that falls off the book included. An entry name that can't be a file name
        is a ValueError before anything is written. Returns
        {written: [row], flags: {flag: n}, notes: {note: n}, leaks: {name: [str]} (rows flagged
        `leak`), missing: [str] (book.missing), unknown: [names in `only` with no entry]}."""
        chosen, unknown = select(self.entries, set(only) if only else None, limit)
        for entry in chosen:
            safe_filename(entry.name)   # refuse before the first write, not half-way through the book
        rows: list[dict] = []
        for done, entry in enumerate(chosen, 1):
            row = self.cut(entry, preview=preview, verify=verify, redact=redact)
            if row is not None:
                rows.append(row)
            if progress is not None:
                progress(done, len(chosen), entry.name)
        self.save_manifest()
        if preview:
            self.write_review_index(rows)
        flags: dict[str, int] = {}
        notes: dict[str, int] = {}
        for r in rows:
            for f in r["flags"]:
                flags[f] = flags.get(f, 0) + 1
            for n in r["notes"]:
                notes[n] = notes.get(n, 0) + 1
        return {"written": rows, "flags": flags, "notes": notes,
                "leaks": {r["formula"]: r["leaks"] for r in rows if "leak" in r["flags"]},
                "missing": list(self.missing), "unknown": unknown}

    def save_manifest(self) -> None:
        ordered = [self.manifest[k] for k in sorted(self.manifest)]
        (self.out / MANIFEST_NAME).write_text(json.dumps(ordered, indent=1, ensure_ascii=False) + "\n")

    def write_review_index(self, rows: list[dict] | None = None) -> None:
        self.review_dir.mkdir(exist_ok=True)
        write_index_html(self.review_dir, rows if rows is not None else list(self.manifest.values()),
                         title=f"{self.prof.name} excerpts")

    # ── overrides ───────────────────────────────────────────────────────────
    def set_override(self, name: str, override: dict) -> None:
        self.overrides[name] = deepcopy(override)
        self._write_overrides()

    def clear_override(self, name: str) -> bool:
        had = self.overrides.pop(name, None) is not None
        if had:
            self._write_overrides()
        return had

    def _write_overrides(self) -> None:
        (self.out / OVERRIDES_NAME).write_text(json.dumps(self.overrides, indent=1, ensure_ascii=False) + "\n")

    # ── rendering ───────────────────────────────────────────────────────────
    def sheet_png(self, sheet: int, dpi: int = 110) -> bytes:
        """A whole sheet of the book as PNG (cached under out/.sheets/<dpi>/)."""
        if not (0 <= sheet < self.page_count):
            raise IndexError(sheet)
        cache = self.out / SHEET_CACHE_DIR / str(dpi) / f"{sheet}.png"
        if cache.exists():
            return cache.read_bytes()
        data = self.doc[sheet].get_pixmap(dpi=dpi).tobytes("png")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(data)
        return data

    def excerpt_path(self, name: str) -> Path:
        return self.out / safe_filename(name)

    def close(self) -> None:
        self.doc.close()


def _resolve_profile(spec):
    from .cli import resolve_profile  # local import: cli imports this module

    return resolve_profile(spec)
