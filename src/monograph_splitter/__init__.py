"""
monograph_splitter — per-entry PDF excerpts from a scanned reference book.

STORY-199 built the tool for Chen & Chen's formula book; STORY-203 split it
into an engine (this package), a layout profile (profiles/*.toml) and an
entries adapter (entries.py — the Inkwell vault is one source of entries).
STORY-220 added the second anchor source: a book with no header label blocks
(Maciocia) is cut at the entries' own headings (`[anchors] source = "headings"`).
0.4.0 is web mode: settings-dict profiles, in-memory entry lists, section detection
(`detect`) and `Book.cut_all()` with a progress callback.

    from monograph_splitter.cli import main
    main(["--pdf", "book.pdf", "--profile", "profiles/x.toml", "--entries", "entries.json", "--out", "dir"])
"""

__version__ = "0.4.0"
ENGINE_VERSION = 17   # bumps whenever an indexing rule changes (the index cache is keyed on it)
