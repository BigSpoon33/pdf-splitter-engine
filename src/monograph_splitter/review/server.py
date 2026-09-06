# Docs: docs/tooling/monograph-splitter.md
"""
The review editor: a local web UI over one or more open books that shows every
entry's mapped pages and lets a human correct a cut — add the page before or
after, move the start/end line, change its column, widen it past its band — and
writes the correction to `overrides.json`, re-cuts the excerpt PDF in place and
updates the manifest. Exactly what `--only "<name>"` after a hand edit did, one
click at a time.

    monograph-splitter-review --config books.json [--host 127.0.0.1] [--port 8765]

books.json:
  {"books": [{
     "id": "formulas", "title": "Chen & Chen — Formulas",
     "pdf": "…/book.pdf", "out": "…/formula-sources", "profile": "chen-chen-formulas",
     "entries": "…/entries.json"            // or "from_vault": "…", "vault_folder": "TCM_Formulas"
     "known_pages": ["…/toc.json"],
     "hooks": {"register": {"label": "Register in Inkwell", "cwd": "…/inkwell-api",
                            "cmd": ["bun", "run", "register-reference-excerpts", "--only", "{name}"]}}
  }]}

Optional dependency group `review` (fastapi + uvicorn); the engine itself
stays pymupdf-only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from ..entries import Entry
from ..session import Book

APP_HTML = Path(__file__).parent / "app.html"
DEFAULT_DPI = 110
OVERRIDE_FIELDS = ("pages", "startCut", "startCol", "endCut", "endCol", "startBand", "endBand", "note")


class OverrideBody(BaseModel):
    pages: list[int] | None = None
    startCut: float | None = None
    startCol: str | None = None
    endCut: float | None = None
    endCol: str | None = None
    startBand: list[float | None] | None = None
    endBand: list[float | None] | None = None
    note: str | None = None
    # which optional fields the client means to send (a null cut is a real value)
    fields: list[str] | None = None

    def as_override(self) -> dict:
        """Only the fields the client named (or every non-null one) become the override —
        `startCut: null` is "no cut", not "leave it to the engine"."""
        raw = self.model_dump()
        names = self.fields or [k for k in OVERRIDE_FIELDS if raw.get(k) is not None]
        return {k: raw[k] for k in OVERRIDE_FIELDS if k in names}


class BookConfig:
    def __init__(self, raw: dict) -> None:
        self.id: str = raw["id"]
        self.title: str = raw.get("title") or self.id
        self.raw = raw
        self.hooks: dict[str, dict] = raw.get("hooks") or {}
        self._book: Book | None = None
        self._lock = threading.Lock()
        self.log: list[str] = []

    @property
    def book(self) -> Book:
        with self._lock:
            if self._book is None:
                r = self.raw
                self._book = Book.open(
                    pdf=Path(r["pdf"]), out=Path(r["out"]), profile=r.get("profile"),
                    entries=Path(r["entries"]) if r.get("entries") else None,
                    from_vault=Path(r["from_vault"]) if r.get("from_vault") else None,
                    vault_folder=r.get("vault_folder", "TCM_Formulas"),
                    known_pages=[Path(k) for k in r.get("known_pages") or []],
                    log=self.log.append,
                )
            return self._book


def _entry_row(book: Book, e: Entry) -> dict:
    m = book.manifest.get(e.name)
    return {
        "name": e.name, "page": e.page, "source": e.source,
        "kind": m["kind"] if m else None,
        "printedPages": m["printedPages"] if m else None,
        "pageCount": m["pageCount"] if m else None,
        "flags": m["flags"] if m else [], "notes": m["notes"] if m else [], "leaks": m.get("leaks", []) if m else [],
        "nextFormula": m.get("nextFormula") if m else None,
        "hasExcerpt": book.excerpt_path(e.name).exists(),
        "hasOverride": e.name in book.overrides,
        "note": (book.overrides.get(e.name) or {}).get("note"),
    }


def _plan_view(book: Book, p: dict) -> dict:
    return {
        "pages": [book.printed_of(p["sheet0"]), book.printed_of(p["sheet1"])],
        "sheets": [p["sheet0"], p["sheet1"]],
        "startCut": p["startCut"], "startCol": p["startCol"], "startBand": p.get("startBand"),
        "endCut": p["endCut"], "endCol": p["endCol"], "endBand": p.get("endBand"),
        "kind": p["kind"], "nextFormula": p["nextFormula"], "flags": p["flags"], "notes": p["notes"],
        "inRange": book.in_range(p),
        "rects": book.rects(p) if book.in_range(p) else [],
    }


def _detail(cfg: BookConfig, name: str) -> dict:
    book = cfg.book
    e = book.entry(name)
    if e is None:
        # a skipped name: no page anywhere — the editor offers a page box
        if any(s.split(" (")[0] == name for s in book.entry_list.skipped):
            w, h = book.page_size()
            return {"entry": {"name": name, "page": None, "source": None}, "manifest": None, "override": None,
                    "auto": None, "current": None, "pageSize": [w, h], "sheetOffset": book.prof.sheet_offset,
                    "columnSplit": book.prof.column_split, "bookPages": book.page_count, "anchors": {},
                    "hooks": _hook_list(cfg)}
        raise HTTPException(404, f"no entry {name!r}")
    auto = book.auto_plan(e)
    current = book.planned(e)
    lo, hi = min(auto["sheet0"], current["sheet0"]) - 1, max(auto["sheet1"], current["sheet1"]) + 1
    anchors = {s: book.anchors_on(s) for s in range(max(lo, 0), min(hi, book.page_count - 1) + 1)}
    w, h = book.page_size(current["sheet0"] if book.in_range(current) else 0)
    return {
        "entry": {"name": e.name, "page": e.page, "source": e.source},
        "manifest": book.manifest.get(e.name),
        "override": book.overrides.get(e.name),
        "auto": _plan_view(book, auto),
        "current": _plan_view(book, current),
        "pageSize": [w, h],
        "sheetOffset": book.prof.sheet_offset,
        "columnSplit": book.prof.column_split,
        "redactTop": book.prof.redact_top,
        "footerBand": book.prof.footer_band,
        "bookPages": book.page_count,
        "anchors": anchors,
        "hasExcerpt": book.excerpt_path(e.name).exists(),
        "hooks": _hook_list(cfg),
    }


def _hook_list(cfg: BookConfig) -> list[dict]:
    return [{"id": k, "label": v.get("label") or k} for k, v in cfg.hooks.items()]


def create_app(config: dict) -> FastAPI:
    books: dict[str, BookConfig] = {b["id"]: BookConfig(b) for b in config["books"]}
    app = FastAPI(title="monograph-splitter review")

    def cfg_of(book_id: str) -> BookConfig:
        if book_id not in books:
            raise HTTPException(404, f"no book {book_id!r}")
        return books[book_id]

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return APP_HTML.read_text(encoding="utf-8")

    @app.get("/api/books")
    def list_books() -> list[dict]:
        return [{"id": c.id, "title": c.title, "hooks": _hook_list(c)} for c in books.values()]

    @app.get("/api/books/{book_id}")
    def book_summary(book_id: str) -> dict:
        cfg = cfg_of(book_id)
        b = cfg.book
        rows = [_entry_row(b, e) for e in b.entries]
        # names an override mapped that the entry list had skipped
        for s in b.entry_list.skipped:
            nm = s.split(" (")[0]
            e = b.entry(nm)
            if e is not None and e.source == "override":
                rows.append(_entry_row(b, e))
        rows.sort(key=lambda r: r["name"].lower())
        return {
            "id": cfg.id, "title": cfg.title, "profile": b.prof.name, "profileTag": b.prof.tag,
            "sheetOffset": b.prof.sheet_offset, "bookPages": b.page_count, "headingsMode": b.headings_mode,
            "entries": rows, "skipped": b.skipped, "hooks": _hook_list(cfg),
            "counts": {
                "entries": len(rows),
                "excerpts": sum(1 for r in rows if r["hasExcerpt"]),
                "flagged": sum(1 for r in rows if r["flags"]),
                "overridden": sum(1 for r in rows if r["hasOverride"]),
                "unmapped": len(b.skipped),
            },
            "log": cfg.log[-20:],
        }

    @app.get("/api/books/{book_id}/entries/{name}")
    def entry_detail(book_id: str, name: str) -> dict:
        return _detail(cfg_of(book_id), name)

    @app.post("/api/books/{book_id}/entries/{name}/preview")
    def entry_preview(book_id: str, name: str, body: OverrideBody) -> dict:
        cfg = cfg_of(book_id)
        b = cfg.book
        ov = body.as_override()
        e = b.entry(name) or (Entry(name, int(ov["pages"][0]), "override") if ov.get("pages") else None)
        if e is None:
            raise HTTPException(404, f"no entry {name!r}")
        p = b.planned(e, ov)
        return {"plan": _plan_view(b, p), "override": ov}

    @app.post("/api/books/{book_id}/entries/{name}/save")
    def entry_save(book_id: str, name: str, body: OverrideBody) -> dict:
        cfg = cfg_of(book_id)
        b = cfg.book
        ov = body.as_override()
        if not ov:
            raise HTTPException(400, "empty override — use reset to drop one")
        with cfg._lock:
            b.set_override(name, ov)
            e = b.entry(name)
            if e is None:
                b.clear_override(name)
                raise HTTPException(404, f"no entry {name!r} and the override gives no pages")
            row = b.cut(e, preview=True, verify=True)
            if row is None:
                b.clear_override(name)
                raise HTTPException(400, f"pages fall outside the book: {b.missing[-1]}")
            b.save_manifest()
            b.write_review_index()
        return {"row": row, "detail": _detail(cfg, name)}

    @app.post("/api/books/{book_id}/entries/{name}/reset")
    def entry_reset(book_id: str, name: str) -> dict:
        cfg = cfg_of(book_id)
        b = cfg.book
        with cfg._lock:
            b.clear_override(name)
            e = b.entry(name)
            if e is None:
                b.manifest.pop(name, None)
                b.save_manifest()
                return {"row": None, "detail": _detail(cfg, name)}
            row = b.cut(e, preview=True, verify=True)
            b.save_manifest()
            b.write_review_index()
        return {"row": row, "detail": _detail(cfg, name)}

    @app.get("/api/books/{book_id}/sheets/{sheet}.png")
    def sheet_png(book_id: str, sheet: int, dpi: int = DEFAULT_DPI) -> Response:
        b = cfg_of(book_id).book
        try:
            data = b.sheet_png(sheet, dpi=max(36, min(dpi, 200)))
        except IndexError:
            raise HTTPException(404, f"sheet {sheet} outside the book")
        return Response(content=data, media_type="image/png", headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/books/{book_id}/excerpts/{name}.pdf")
    def excerpt_pdf(book_id: str, name: str) -> FileResponse:
        path = cfg_of(book_id).book.excerpt_path(name)
        if not path.exists():
            raise HTTPException(404, "no excerpt yet")
        return FileResponse(path, media_type="application/pdf", filename=path.name,
                            headers={"Cache-Control": "no-store"})

    @app.post("/api/books/{book_id}/entries/{name}/hooks/{hook}")
    def run_hook(book_id: str, name: str, hook: str) -> dict:
        cfg = cfg_of(book_id)
        spec = cfg.hooks.get(hook)
        if not spec:
            raise HTTPException(404, f"no hook {hook!r}")
        cmd = [str(part).replace("{name}", name) for part in spec["cmd"]]
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, cwd=spec.get("cwd"), capture_output=True, text=True,
                                  timeout=spec.get("timeout", 600))
        except subprocess.TimeoutExpired:
            raise HTTPException(504, f"hook {hook!r} timed out")
        except OSError as e:
            raise HTTPException(500, f"hook {hook!r} failed to start: {e}")
        return {"hook": hook, "cmd": cmd, "returncode": proc.returncode,
                "stdout": proc.stdout[-20000:], "stderr": proc.stderr[-20000:], "seconds": round(time.time() - t0, 1)}

    return app


def load_config(path: Path) -> dict:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(cfg.get("books"), list) or not cfg["books"]:
        raise ValueError(f"{path}: expected {{\"books\": [...]}}")
    for b in cfg["books"]:
        for k in ("id", "pdf", "out"):
            if k not in b:
                raise ValueError(f"{path}: book missing {k!r}: {b}")
    return cfg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="monograph-splitter-review", description="review + edit the excerpt cuts in a browser")
    ap.add_argument("--config", type=Path, required=True, help="books.json (see module docstring)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)
    import uvicorn

    app = create_app(load_config(args.config))
    print(f"review editor: http://{args.host}:{args.port}/")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def main_entry() -> None:
    raise SystemExit(main())


__all__: list[Any] = ["create_app", "load_config", "main", "main_entry"]
