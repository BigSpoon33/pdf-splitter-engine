#!/usr/bin/env python3
"""
Regression gate for the splitter (STORY-203 AC-3): compare two manifests on
the fields that ARE the boundary decisions. Anything else (bytes, review
paths, profile tag) is allowed to differ.

    python diff_manifest.py <previous-manifest.json> <new-manifest.json>
    exit 0 = identical decisions; 1 = something changed (listed)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

KEYS = ("printedPages", "startCut", "startCol", "startBand", "endCut", "endCol", "endBand",
        "kind", "nextFormula", "flags", "notes")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    prev = {e["formula"]: e for e in json.loads(Path(argv[0]).read_text())}
    new = {e["formula"]: e for e in json.loads(Path(argv[1]).read_text())}
    changed: list[str] = []
    for name in sorted(set(prev) | set(new)):
        a, b = prev.get(name), new.get(name)
        if a is None:
            changed.append(f"  {name}: NEW")
            continue
        if b is None:
            changed.append(f"  {name}: GONE")
            continue
        diffs = {k: (a.get(k), b.get(k)) for k in KEYS if a.get(k) != b.get(k)}
        if diffs:
            changed.append(f"  {name}: {diffs}")
    print(f"{len(changed)} of {len(new)} entries changed" + (":" if changed else ""))
    for line in changed:
        print(line)
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


def main_entry() -> None:
    """Console-script entry (`monograph-splitter-diff`)."""
    import sys
    raise SystemExit(main(sys.argv[1:]))
