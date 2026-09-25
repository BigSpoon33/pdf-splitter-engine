# Docs: docs/tooling/monograph-splitter.md
"""
The layout profile: every number and pattern that describes ONE book's typography.

The engine never hard-codes a size, an offset, a label or a heading word — it
reads them from a Profile. The built-in defaults are Chen & Chen's *Chinese
Herbal Formulas and Applications* (the first book, STORY-199); a TOML profile
overrides any subset, and an unknown key is an error naming it (a typo in a
threshold must not silently fall back to the default).
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, fields, replace
from functools import cached_property
from pathlib import Path
from typing import Any

# TOML table.key → Profile field. Keep the TOML side human, the field side flat.
SCHEMA: dict[str, dict[str, str]] = {
    "book": {"sheet_offset": "sheet_offset"},
    "layout": {
        "header_band": "header_band", "redact_top": "redact_top", "subheader_bottom": "subheader_bottom",
        "footer_band": "footer_band", "column_split": "column_split", "full_width_ratio": "full_width_ratio",
        "title_min_y": "title_min_y",
    },
    "title": {
        "big_min_size": "big_min_size", "script_min_size": "script_min_size", "script_regex": "script_regex",
        "script_ratio": "script_ratio", "body_max_size": "body_max_size", "big_lines_min": "big_lines_min",
        "title_reach": "title_reach", "gap_window": "gap_window", "min_gap": "min_gap", "col": "title_col",
    },
    "labels": {
        "name": "label_name", "secondary": "label_secondary", "cluster_gap": "cluster_gap",
        "related_heading": "related_heading", "related_reach": "related_reach", "block_reach": "block_reach",
        "estimate_above": "estimate_above", "parent_headings": "parent_headings", "parent_size": "parent_size",
        "parent_max_len": "parent_max_len",
    },
    "breaks": {"patterns": "break_patterns", "chapter_only": "chapter_only", "min_size": "break_min_size", "max_len": "break_max_len"},
    "limits": {"max_span": "max_span", "long_span": "long_span", "name_match": "name_match", "walk_back": "walk_back"},
    "anchors": {
        "source": "anchor_source", "heading_min_size": "heading_min_size", "heading_wrap_gap": "heading_wrap_gap",
        "heading_pad": "heading_pad", "heading_match": "heading_match",
    },
}
ANCHOR_SOURCES = ("labels", "headings")
TITLE_COLS = ("full", "column")
TOP_LEVEL = {"name", "description"}


@dataclass(frozen=True)
class Profile:
    name: str = "chen-chen-formulas"
    description: str = "Chen & Chen, Chinese Herbal Formulas and Applications (scanned, OCR text layer)"
    path: str = ""
    sha256: str = ""
    # [book] — printed page N is sheet index N + sheet_offset - 1
    sheet_offset: int = 39
    # [layout]
    header_band: float = 50.0        # lines starting above this are running (sub)headers, not content
    redact_top: float = 45.0         # redaction starts here: keeps the running chapter/section header
    subheader_bottom: float = 72.0   # a running sub-header (previous entry's name) ends about here
    footer_band: float = 32.0        # the printed page number lives below H - this
    column_split: float = 0.487      # × page width; the right column starts just right of it
    full_width_ratio: float = 0.55   # a line wider than this × W spans both columns
    title_min_y: float = 30.0        # a title that STARTS a page sits inside the header band
    # [title]
    big_min_size: float = 12.5       # entry titles are bigger than this; sub-entry titles are not
    script_min_size: float = 12.0    # the script title lines (Chinese here) of a real entry are ≥ this
    script_regex: str = "[一-鿿]"     # what "the script" is; empty ⇒ no script-line rule
    script_ratio: float = 0.6        # a line is a script line when ≥ this share of its glyphs match
    body_max_size: float = 11.0      # body text is below this; banner captions and titles above
    big_lines_min: int = 3           # ≥ this many big lines above the labels = a title shattered by OCR; 0 = every label block is an entry (no sub-entries)
    title_reach: float = 150.0       # a title sits within this far above its first label line
    gap_window: float = 160.0        # look this far above a label for the title-block gap
    min_gap: float = 18.0            # gaps inside a title block are smaller than this
    title_col: str = "full"          # "full": an entry title spans both columns (its cut is a rule across the page);
                                     # "column": entries start INSIDE a column (Bensky Materia Medica) — start and end cuts are column cuts
    # [labels]
    label_name: str = r"p\s*[i1l|]\s*n\s*y\s*[i1l|]\s*n\s+n\s*a\s*[mr]\s*[nr]?\s*e\s*[:：]?\s*(.*)"
    label_secondary: tuple[str, ...] = (
        r"l\s*[i1l|]\s*t\s*e\s*r\s*a\s*l\s+n\s*a\s*[mr]\s*[nr]?\s*e",
        r"or\s*[i1l|]\s*g\s*[i1l|]\s*n\s*a\s*l\s+s\s*o\s*u\s*r\s*c\s*e",
    )
    cluster_gap: float = 70.0        # label lines of one header block sit within this
    related_heading: str = r"related\s+formula"
    related_reach: float = 130.0     # a RELATED heading this far above a sub-entry's labels is its start
    block_reach: float = 62.0        # else the sub-entry's own title block starts within this
    estimate_above: float = 55.0     # last resort: assume the block starts this far above the labels
    parent_headings: str = r"authors.?\s*comments|^\s*references\s*$|case\s+stud|clinical\s+stud"
    parent_size: tuple[float, float] = (8.0, 12.5)
    parent_max_len: int = 32
    # [breaks]
    break_patterns: tuple[str, ...] = (r"umma[rn]", r"table\s+of\s+contents", r"^\s*overv[i1l]ew\s*$", r"^\s*b[i1l]ograph")
    chapter_only: str = r"^\s*chapter\s*\d+\s*[-—一]?\s*$"
    break_min_size: float = 14.0
    break_max_len: int = 28
    # [limits]
    max_span: int = 20               # sheets; safety net only
    long_span: int = 10              # flag anything longer for a human look
    name_match: float = 0.75         # difflib ratio for "this header is our entry"
    walk_back: int = 8               # sheets to search backwards for a continuation page's real start
    # [anchors] — where an entry's anchor comes from. "labels": the header label block the
    # OCR left behind (Chen & Chen — the engine finds every entry on its own). "headings": the
    # book introduces an entry with a bare heading and nothing else (Maciocia), so the entries
    # list carries each heading's text and the engine locates it on the given start sheet.
    anchor_source: str = "labels"
    heading_min_size: float = 12.5   # heading lines are at least this big; smaller lines match only exactly
    heading_wrap_gap: float = 16.0   # a heading wrapped over lines: consecutive line tops within this
    heading_pad: float = 3.0         # the start cut sits this far above the heading's top (clamped to the line above)
    heading_match: float = 0.85      # difflib ratio for "these lines are the heading"

    # compiled patterns (cached_property writes to __dict__, which a frozen dataclass allows)
    @cached_property
    def label_name_re(self) -> re.Pattern:
        return re.compile(self.label_name, re.I)

    @cached_property
    def label_secondary_res(self) -> tuple[re.Pattern, ...]:
        return tuple(re.compile(p, re.I) for p in self.label_secondary)

    @cached_property
    def related_re(self) -> re.Pattern:
        return re.compile(self.related_heading, re.I)

    @cached_property
    def parent_re(self) -> re.Pattern:
        return re.compile(self.parent_headings, re.I)

    @cached_property
    def break_res(self) -> tuple[re.Pattern, ...]:
        return tuple(re.compile(p, re.I) for p in self.break_patterns)

    @cached_property
    def chapter_only_re(self) -> re.Pattern:
        # an empty pattern would match every line; empty means "no chapter-only rule"
        return re.compile(self.chapter_only or r"(?!)", re.I)

    @cached_property
    def script_re(self) -> re.Pattern | None:
        return re.compile(self.script_regex) if self.script_regex else None

    @property
    def tag(self) -> str:
        return f"{self.name}@{self.sha256[:8]}" if self.sha256 else f"{self.name}@builtin"


class ProfileError(ValueError):
    pass


def _check_type(table: str, key: str, value: Any, default: Any) -> Any:
    if isinstance(default, bool):
        raise ProfileError(f"[{table}].{key}: booleans are not profile values")
    if isinstance(default, int) and not isinstance(value, bool) and isinstance(value, int):
        return value
    if isinstance(default, float) and isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(default, str) and isinstance(value, str):
        return value
    if isinstance(default, tuple):
        if isinstance(value, list) and all(isinstance(v, type(default[0]) if default else (int, float, str)) or
                                           (isinstance(default[0], float) and isinstance(v, (int, float))) for v in value):
            return tuple(float(v) if isinstance(default[0], float) else v for v in value)
        raise ProfileError(f"[{table}].{key}: expected a list like {list(default)!r}, got {value!r}")
    raise ProfileError(f"[{table}].{key}: expected {type(default).__name__}, got {type(value).__name__} ({value!r})")


def load_profile(path: str | Path | None) -> Profile:
    """Built-in defaults when path is None; otherwise the TOML overrides any subset."""
    if path is None:
        return Profile()
    p = Path(path)
    raw_bytes = p.read_bytes()
    try:
        data = tomllib.loads(raw_bytes.decode("utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ProfileError(f"{p}: not valid TOML — {e}") from e
    defaults = Profile()
    values: dict[str, Any] = {"path": str(p), "sha256": hashlib.sha256(raw_bytes).hexdigest()}
    for key, value in data.items():
        if key in TOP_LEVEL:
            if not isinstance(value, str):
                raise ProfileError(f"{p}: `{key}` must be a string")
            values[key] = value
            continue
        if key not in SCHEMA:
            raise ProfileError(f"{p}: unknown table [{key}] (known: {', '.join(SCHEMA)})")
        if not isinstance(value, dict):
            raise ProfileError(f"{p}: [{key}] must be a table")
        for sub, v in value.items():
            if sub not in SCHEMA[key]:
                raise ProfileError(f"{p}: unknown key [{key}].{sub} (known: {', '.join(SCHEMA[key])})")
            field = SCHEMA[key][sub]
            values[field] = _check_type(key, sub, v, getattr(defaults, field))
    return _validated(Profile(**values), p)


def _validated(prof: Profile, where) -> Profile:
    if prof.label_name_re.groups < 1:
        raise ProfileError(f"{where}: [labels].name must have one capture group for the entry name")
    if not (0.0 < prof.column_split < 1.0):
        raise ProfileError(f"{where}: [layout].column_split must be a fraction of the page width")
    if prof.title_col not in TITLE_COLS:
        raise ProfileError(f"{where}: [title].col must be one of {', '.join(TITLE_COLS)} (got {prof.title_col!r})")
    if prof.anchor_source not in ANCHOR_SOURCES:
        raise ProfileError(f"{where}: [anchors].source must be one of {', '.join(ANCHOR_SOURCES)} (got {prof.anchor_source!r})")
    return prof


# ── web mode ─────────────────────────────────────────────────────────────────
# The web service drives the engine with a settings dict, never a TOML file: headings
# mode (the user's section list carries each heading), no script-title or chapter-summary
# rules (those describe Chen & Chen, not an arbitrary PDF), and a "page" is the 1-based
# sheet number — PDF page 1 is the first sheet. The engine maps page N to sheet index
# N + sheet_offset - 1, so that is sheet_offset = 0.
# A book without running sub-headers must not lose a strip under the header band on a
# column cut, hence subheader_bottom = redact_top (cut_rects' "previous entry's running
# name" rectangle becomes zero-height). The user's section list is the only authority on
# where a section starts, so no bare "Chapter N" line breaks one (chapter_only = ""), and
# a long section is normal, not a review flag (long_span = max_span).
WEB_BASE = Profile(
    name="web",
    description="pdf-splitter web mode: headings from the user's section list, sheet numbers as pages",
    anchor_source="headings",
    sheet_offset=0,
    script_regex="",
    break_patterns=(),
    chapter_only="",
    max_span=200,
    long_span=200,
    subheader_bottom=Profile.redact_top,
)
WEB_KEYS = ("column_split", "header_band", "footer_band", "redact_top", "heading_min_size",
            "heading_match", "heading_wrap_gap", "max_span", "single_column")
# Every line counts as "left" (x0 < 0.999 W: nothing starts in the last thousandth of a
# page) and every line is wider than 0 × W, so every located heading is full-width and
# every cut spans the page. column_split stays inside (0, 1) so validation still holds.
SINGLE_COLUMN = {"column_split": 0.999, "full_width_ratio": 0.0}
_FIELD_TOML = {field: (table, key) for table, keys in SCHEMA.items() for key, field in keys.items()}


def profile_from_dict(d: dict, base: Profile = WEB_BASE) -> Profile:
    """A Profile from the web service's settings: `base` with only WEB_KEYS overridden.
    An unknown key or a wrongly typed value is a ProfileError naming it (same wording as
    a TOML profile). `single_column: true` wins over an explicit column_split. sha256 is
    the hash of the effective values, so two dicts that mean the same thing (any key
    order, a default spelled out or left out) share an index cache and a settings hash."""
    if not isinstance(d, dict):
        raise ProfileError(f"settings: expected an object of {{key: value}}, got {type(d).__name__}")
    unknown = sorted(k for k in d if k not in WEB_KEYS)
    if unknown:
        raise ProfileError(f"settings: unknown key(s) {', '.join(map(repr, unknown))} (known: {', '.join(WEB_KEYS)})")
    values: dict[str, Any] = {}
    single = d.get("single_column", False)
    if not isinstance(single, bool):
        raise ProfileError(f"settings: single_column: expected bool, got {type(single).__name__} ({single!r})")
    for key, value in d.items():
        if key == "single_column":
            continue
        table, toml_key = _FIELD_TOML[key]
        values[key] = _check_type(table, toml_key, value, getattr(base, key))
    if single:
        values.update(SINGLE_COLUMN)
    for key in ("header_band", "footer_band", "redact_top", "heading_min_size", "heading_wrap_gap"):
        if key in values and values[key] < 0:
            raise ProfileError(f"settings: {key} must be ≥ 0 (got {values[key]!r})")
    if "heading_match" in values and not (0.0 < values["heading_match"] <= 1.0):
        raise ProfileError(f"settings: heading_match must be in (0, 1] (got {values['heading_match']!r})")
    if "max_span" in values and values["max_span"] < 1:
        raise ProfileError(f"settings: max_span must be ≥ 1 (got {values['max_span']!r})")
    if "redact_top" in values and base.subheader_bottom == base.redact_top:
        values["subheader_bottom"] = values["redact_top"]
    if "max_span" in values and base.long_span == base.max_span:
        values["long_span"] = values["max_span"]
    prof = replace(base, path="", sha256="", **values)
    effective = {f.name: getattr(prof, f.name) for f in fields(Profile) if f.name not in ("path", "sha256")}
    digest = hashlib.sha256(json.dumps(effective, sort_keys=True).encode("utf-8")).hexdigest()
    return _validated(replace(prof, sha256=digest), "settings")


def profile_field_names() -> list[str]:
    return [f.name for f in fields(Profile)]
