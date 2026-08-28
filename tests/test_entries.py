import json

from monograph_splitter.entries import entries_from_vault, load_entries_json, load_known_pages, select


def test_entries_json_reports_bad_rows_instead_of_dropping_them(tmp_path):
    f = tmp_path / "e.json"
    f.write_text(json.dumps([{"name": "A", "page": 5}, {"name": "B"}, {"page": 3}, {"name": "C", "page": 0}]))
    el = load_entries_json(f)
    assert [e.name for e in el.entries] == ["A"]
    assert el.skipped == ["B (no printed start page)", "#2 (no name)", "C (no printed start page)"]
    assert el.known_pages == {5}


def test_vault_adapter_reads_source_page_frontmatter_and_knows_every_page(tmp_path):
    live = tmp_path / "TCM_Formulas"
    live.mkdir()
    (live / "Gui Zhi Tang.md").write_text('---\nname: Gui Zhi Tang\nsource_page: 51\n---\n# body\n')
    (live / "No Page.md").write_text('---\nname: x\n---\n')
    (live / "Composition_Reference.md").write_text('---\nsource_page: 9\n---\n')
    el = entries_from_vault(tmp_path)
    assert [(e.name, e.page, e.source) for e in el.entries] == [("Gui Zhi Tang", 51, "frontmatter")]
    assert el.skipped == ["No Page"]
    assert el.known_pages == {51}


def test_known_pages_loader_is_tolerant(tmp_path):
    d = tmp_path / "d.json"; d.write_text(json.dumps({"A": 3, "B": {"page": 7}, "C": "x"}))
    l = tmp_path / "l.json"; l.write_text(json.dumps([[9, "k", "raw"], 11, "no"]))
    assert load_known_pages(d) == {3, 7}
    assert load_known_pages(l) == {9, 11}


def test_select_only_and_limit():
    el = load_entries_json_from([("A", 1), ("B", 2), ("C", 3)])
    chosen, unknown = select(el, {"B", "Zzz"}, None)
    assert [e.name for e in chosen] == ["B"] and unknown == ["Zzz"]
    chosen, _ = select(el, None, 2)
    assert [e.name for e in chosen] == ["A", "B"]


def load_entries_json_from(pairs):
    from monograph_splitter.entries import Entry
    return [Entry(n, p) for n, p in pairs]
