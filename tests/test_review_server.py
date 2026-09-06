import json
import sys
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from monograph_splitter.review.server import create_app, load_config  # noqa: E402
from tests.fixtures import scenario_book  # noqa: E402

TEST_PROFILE = Path(__file__).parent / "profile-test.toml"


@pytest.fixture
def client(tmp_path):
    info = scenario_book(tmp_path / "book.pdf")
    entries = tmp_path / "entries.json"
    entries.write_text(json.dumps(info["entries"] + [{"name": "Ghost Tang", "page": None}]))
    out = tmp_path / "out"
    marker = tmp_path / "hook-ran.txt"
    cfg = {"books": [{
        "id": "test", "title": "Test book", "pdf": info["pdf"], "out": str(out), "profile": str(TEST_PROFILE),
        "entries": str(entries),
        "hooks": {"register": {"label": "Register", "cmd": [sys.executable, "-c",
                                                             f"import sys; open({str(marker)!r}, 'w').write(sys.argv[1]); print('registered', sys.argv[1])", "{name}"]}},
    }]}
    (tmp_path / "books.json").write_text(json.dumps(cfg))
    app = create_app(load_config(tmp_path / "books.json"))
    c = TestClient(app)
    c.out, c.marker = out, marker
    return c


def test_books_entries_and_detail(client):
    assert client.get("/").text.startswith("<!doctype html>")
    books = client.get("/api/books").json()
    assert books[0]["id"] == "test" and books[0]["hooks"] == [{"id": "register", "label": "Register"}]
    b = client.get("/api/books/test").json()
    names = [e["name"] for e in b["entries"]]
    assert "Alpha Tang" in names and b["skipped"] == ["Ghost Tang (no printed start page)"]
    assert b["counts"]["unmapped"] == 1 and b["counts"]["excerpts"] == 0
    d = client.get("/api/books/test/entries/Alpha Tang").json()
    assert d["auto"]["pages"] == [1, 2] and d["current"]["pages"] == [1, 2] and d["override"] is None
    assert d["pageSize"][0] > 500 and isinstance(d["sheetOffset"], int) and isinstance(d["anchors"], dict)
    assert any(a["name"] for s in d["anchors"].values() for a in s)
    assert client.get("/api/books/test/entries/Nobody").status_code == 404
    assert client.get("/api/books/nope").status_code == 404


def test_preview_save_reset_and_hook(client):
    body = {"pages": [1, 1], "startCut": None, "startCol": "full", "endCut": 600.0, "endCol": "full",
            "startBand": None, "endBand": None, "note": "one page is enough",
            "fields": ["pages", "startCut", "startCol", "endCut", "endCol", "startBand", "endBand", "note"]}
    pv = client.post("/api/books/test/entries/Alpha Tang/preview", json=body).json()
    assert pv["plan"]["pages"] == [1, 1] and pv["plan"]["endCut"] == 600.0 and "override" in pv["plan"]["flags"]
    assert pv["override"]["startCut"] is None and pv["override"]["note"] == "one page is enough"
    assert any(r["sheet"] == 0 and r["rect"][1] == 600.0 for r in pv["plan"]["rects"])
    assert not (client.out / "overrides.json").exists() or json.loads((client.out / "overrides.json").read_text()) == {}

    sv = client.post("/api/books/test/entries/Alpha Tang/save", json=body).json()
    assert sv["row"]["printedPages"] == [1, 1] and sv["row"]["pageSource"] == "override"
    assert sv["detail"]["override"]["endCut"] == 600.0 and sv["detail"]["hasExcerpt"]
    assert json.loads((client.out / "overrides.json").read_text())["Alpha Tang"]["note"] == "one page is enough"
    man = {m["formula"]: m for m in json.loads((client.out / "manifest.json").read_text())}
    assert man["Alpha Tang"]["endCut"] == 600.0 and (client.out / "review" / "index.html").exists()
    pdf = client.get("/api/books/test/excerpts/Alpha Tang.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    b = client.get("/api/books/test").json()
    row = next(e for e in b["entries"] if e["name"] == "Alpha Tang")
    assert row["hasOverride"] and row["hasExcerpt"] and row["note"] == "one page is enough"

    hk = client.post("/api/books/test/entries/Alpha Tang/hooks/register").json()
    assert hk["returncode"] == 0 and "registered Alpha Tang" in hk["stdout"] and client.marker.read_text() == "Alpha Tang"
    assert client.post("/api/books/test/entries/Alpha Tang/hooks/nope").status_code == 404

    rs = client.post("/api/books/test/entries/Alpha Tang/reset").json()
    assert rs["row"]["printedPages"] == [1, 2] and rs["detail"]["override"] is None
    assert json.loads((client.out / "overrides.json").read_text()) == {}


def test_mapping_a_skipped_name_and_refusing_pages_off_the_book(client):
    d = client.get("/api/books/test/entries/Ghost Tang").json()
    assert d["current"] is None and d["entry"]["page"] is None
    r = client.post("/api/books/test/entries/Ghost Tang/save", json={"pages": [4, 4], "fields": ["pages"]}).json()
    assert r["row"]["printedPages"] == [4, 4] and r["detail"]["entry"]["source"] == "override"
    b = client.get("/api/books/test").json()
    assert b["skipped"] == [] and any(e["name"] == "Ghost Tang" and e["hasExcerpt"] for e in b["entries"])
    bad = client.post("/api/books/test/entries/Alpha Tang/save", json={"pages": [900, 901], "fields": ["pages"]})
    assert bad.status_code == 400 and "outside the book" in bad.json()["detail"]
    assert "Alpha Tang" not in json.loads((client.out / "overrides.json").read_text())
    assert client.post("/api/books/test/entries/Alpha Tang/save", json={"fields": []}).status_code == 400
    assert client.get("/api/books/test/sheets/0.png?dpi=40").headers["content-type"] == "image/png"
    assert client.get("/api/books/test/sheets/999.png").status_code == 404
