# Docs: docs/tooling/monograph-splitter.md
"""
The command line. `main(argv, defaults=...)` so a book-specific entry script
(Inkwell's extract_formula_pdfs.py) can preset --pdf/--out/--profile/--from-vault
and stay a dozen lines.

    split --pdf book.pdf --profile profiles/x.toml --entries entries.json --out dir
         [--known-pages toc.json ...] [--only "A,B"] [--limit N]
         [--preview] [--verify] [--no-redact] [--help-overrides]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from .cuts import apply_overrides, plan, plan_headings
from .entries import EntryList, entries_from_vault, load_entries_json, load_known_pages, select
from .index import add_heading_anchors, add_known_starts, index_book
from .profile import ProfileError, load_profile
from .render import render_review, write_excerpt, write_index_html
from .verify import truncation_check, verify_excerpt, verify_headings

OVERRIDES_HELP = """
overrides.json — one entry per name, every field optional:

  {
    "Gui Zhi Tang": {
      "pages": [51, 53],       // printed first/last page (replaces detection)
      "startCut": 312.4,       // y (PDF points) on the FIRST page: what comes
                               // before it is removed. null = no cut. Omit = auto.
      "startCol": "full",      // "full" | "left" | "right" — which column the
                               // boundary is in (full = entry-title rule)
      "endCut": null,          // y on the LAST page: what comes after it is removed
      "endCol": "left",
      "note": "why"
    }
  }

Read y off the review PNG's ruler (labels every 50pt). Re-run with
--only "Gui Zhi Tang" --preview --verify to check a correction; the manifest merges.
"""


BUNDLED_PROFILES = Path(__file__).parent / "profiles"


def resolve_profile(spec: str | Path | None) -> Path | None:
    """A path is used as given; a bare name selects a bundled profile (profiles/<name>.toml)."""
    if spec is None:
        return None
    p = Path(spec)
    if p.suffix == ".toml" or p.exists():
        return p
    bundled = BUNDLED_PROFILES / f"{p.name}.toml"
    if bundled.exists():
        return bundled
    return p


def bundled_profile(name: str) -> Path:
    """Path of a bundled profile, for entry scripts that preset --profile."""
    return BUNDLED_PROFILES / f"{name}.toml"


def main_entry() -> None:
    """Console-script entry (`monograph-splitter`)."""
    raise SystemExit(main())


def build_parser(defaults: dict | None = None) -> argparse.ArgumentParser:
    d = defaults or {}
    ap = argparse.ArgumentParser(prog="monograph-splitter", description="one clean PDF per entry of a scanned reference book")
    ap.add_argument("--pdf", type=Path, default=d.get("pdf"), help="the book (OCR text layer required)")
    ap.add_argument("--out", type=Path, default=d.get("out"), help="output dir (PDFs, manifest.json, overrides.json, review/)")
    ap.add_argument("--profile", type=str, default=d.get("profile"), help="layout profile: a TOML path, or the name of a bundled one (chen-chen-formulas, chen-chen-herbology, maciocia-foundations; default: built-in Chen & Chen formulas)")
    ap.add_argument("--entries", type=Path, default=d.get("entries"), help='JSON list of {"name", "page"} (+ "heading" and "stop": true rows in headings mode)')
    ap.add_argument("--from-vault", type=Path, default=d.get("from_vault"), help="Inkwell adapter: entries from <vault>/<folder>/*.md source_page frontmatter")
    ap.add_argument("--vault-folder", default=d.get("vault_folder", "TCM_Formulas"))
    ap.add_argument("--known-pages", type=Path, nargs="*", default=d.get("known_pages", []),
                    help="extra JSON files of printed start pages (TOC/index maps) for synthetic anchors")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only", help="comma-separated entry names")
    ap.add_argument("--preview", action="store_true", help="write review/ PNGs + index.html")
    ap.add_argument("--no-redact", action="store_true", help="whole pages, no neighbour removal")
    ap.add_argument("--verify", action="store_true", help="re-scan each excerpt: flags leak / possible-truncation")
    ap.add_argument("--help-overrides", action="store_true")
    return ap


def main(argv: list[str] | None = None, *, defaults: dict | None = None, log=print) -> int:
    args = build_parser(defaults).parse_args(argv)
    if args.help_overrides:
        log(OVERRIDES_HELP)
        return 0
    if not args.pdf or not args.out:
        log("Error: --pdf and --out are required", file=sys.stderr) if log is print else log("Error: --pdf and --out are required")
        return 2
    if not args.entries and not args.from_vault:
        log("Error: give --entries <json> or --from-vault <path>")
        return 2

    try:
        prof = load_profile(resolve_profile(args.profile))
    except (ProfileError, OSError) as e:
        log(f"Error: profile — {e}")
        return 2

    import fitz  # pymupdf; imported late so --help works without it

    book = fitz.open(str(args.pdf))
    args.out.mkdir(parents=True, exist_ok=True)
    index = index_book(book, args.out / ".book-index.json", prof, log=log)

    # ── entries ─────────────────────────────────────────────────────────────
    if args.entries:
        el: EntryList = load_entries_json(args.entries)
    else:
        el = entries_from_vault(args.from_vault, args.vault_folder)
    only = {n.strip() for n in args.only.split(",")} if args.only else None
    chosen, unknown = select(el.entries, only, args.limit)
    if unknown:
        log(f"--only: no entry for {unknown}")
    headings_mode = prof.anchor_source == "headings"
    if headings_mode:
        located, not_found = add_heading_anchors(index, book, el.headings, prof)
        log(f"  headings mode: {located} heading anchor(s) located on their sheets"
            + (f", {len(not_found)} NOT found (whole-page start, flagged): {not_found}" if not_found else ""))
    known = set(el.known_pages)
    for kp in args.known_pages or []:
        known |= load_known_pages(kp)
    synth = add_known_starts(index, {p + prof.sheet_offset - 1 for p in known}, prof)
    if synth:
        log(f"  {synth} known start sheet(s) had no detectable header — synthetic top-of-page anchors added")

    ov_path = args.out / "overrides.json"
    if not ov_path.exists():
        ov_path.write_text("{}\n")
    overrides = json.loads(ov_path.read_text() or "{}")

    man_path = args.out / "manifest.json"
    manifest = {m["formula"]: m for m in json.loads(man_path.read_text())} if man_path.exists() else {}
    review_dir = args.out / "review"
    if args.preview:
        review_dir.mkdir(exist_ok=True)

    rows: list[dict] = []
    missing: list[str] = list(el.skipped)
    t0 = time.time()
    for entry in chosen:
        p = (plan_headings if headings_mode else plan)(entry.name, entry.page, index, prof)
        p = apply_overrides(p, overrides.get(entry.name), prof)
        if not (0 <= p["sheet0"] <= p["sheet1"] < book.page_count):
            missing.append(f"{entry.name} (sheets {p['sheet0']}–{p['sheet1']} out of range)")
            continue
        dest = args.out / f"{entry.name}.pdf"
        write_excerpt(book, p, dest, redact=not args.no_redact, prof=prof)
        slug = re.sub(r"[^A-Za-z0-9]+", "-", entry.name).strip("-")
        if args.verify and not args.no_redact:
            leaks = (verify_headings(dest, entry.name, p, index, prof) if headings_mode
                     else verify_excerpt(dest, entry.name, p["kind"], prof))
        else:
            leaks = []
        if leaks:
            p["flags"].append("leak")
        if args.verify:
            trunc = truncation_check(p, index, prof)
            if trunc:
                p["flags"].append("possible-truncation")
                leaks.append(f"truncation? {trunc}")
        review = render_review(book, p, slug, review_dir, prof) if args.preview else []
        row = {
            # `formula` is the key the API's register-reference-excerpts reads (STORY-202) — kept for that consumer.
            "formula": entry.name,
            "file": dest.name,
            "printedPages": [p["sheet0"] - prof.sheet_offset + 1, p["sheet1"] - prof.sheet_offset + 1],
            "pageCount": p["sheet1"] - p["sheet0"] + 1,
            "kind": p["kind"],
            "startCut": p["startCut"], "startCol": p["startCol"], "startBand": p.get("startBand"),
            "endCut": p["endCut"], "endCol": p["endCol"], "endBand": p.get("endBand"),
            "nextFormula": p["nextFormula"],
            "flags": p["flags"],
            "notes": p["notes"],
            "leaks": leaks,
            "pageSource": "override" if "override" in p["flags"] else entry.source,
            "redacted": not args.no_redact,
            "bytes": dest.stat().st_size,
            "review": review,
            "profile": prof.tag,
        }
        manifest[entry.name] = row
        rows.append(row)

    ordered = [manifest[k] for k in sorted(manifest)]
    man_path.write_text(json.dumps(ordered, indent=1, ensure_ascii=False) + "\n")
    if args.preview:
        write_index_html(review_dir, rows, title=f"{prof.name} excerpts")

    counts: dict[str, dict[str, int]] = {"flags": {}, "notes": {}}
    for r in rows:
        for f in r["flags"]:
            counts["flags"][f] = counts["flags"].get(f, 0) + 1
        for n in r["notes"]:
            counts["notes"][n] = counts["notes"].get(n, 0) + 1
    log(f"wrote {len(rows)} per-entry PDFs -> {args.out}  ({time.time() - t0:.0f}s, profile {prof.tag})")
    log(f"  top cuts: {sum(1 for r in rows if r['startCut'] is not None)}   "
        f"bottom cuts: {sum(1 for r in rows if r['endCut'] is not None)}   "
        f"related entries: {sum(1 for r in rows if r['kind'] == 'related')}   "
        f"pages per entry: min {min((r['pageCount'] for r in rows), default=0)}, "
        f"max {max((r['pageCount'] for r in rows), default=0)}, "
        f"mean {sum(r['pageCount'] for r in rows) / max(len(rows), 1):.1f}")
    log(f"  flags (review): {counts['flags'] or 'none'}")
    log(f"  notes (info):   {counts['notes'] or 'none'}")
    log(f"  total size: {sum(r['bytes'] for r in rows) / 1e6:.1f} MB")
    if args.preview:
        log(f"  review sheet: {review_dir / 'index.html'}")
    if missing:
        log(f"  skipped ({len(missing)}): {missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
