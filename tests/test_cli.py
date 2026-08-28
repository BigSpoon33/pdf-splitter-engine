import json

from monograph_splitter.cli import main
from pathlib import Path

TEST_PROFILE = Path(__file__).parent / "profile-test.toml"
from tests.fixtures import scenario_book


def test_end_to_end_manifest_pdfs_review_and_merge(tmp_path):
    info = scenario_book(tmp_path / "book.pdf")
    entries = tmp_path / "entries.json"
    entries.write_text(json.dumps(info["entries"]))
    out = tmp_path / "out"
    logs: list[str] = []
    rc = main(["--pdf", info["pdf"], "--out", str(out), "--profile", str(TEST_PROFILE),
               "--entries", str(entries), "--preview", "--verify"], log=logs.append)
    assert rc == 0
    man = {e["formula"]: e for e in json.loads((out / "manifest.json").read_text())}
    assert set(man) == {e["name"] for e in info["entries"]}
    assert man["Alpha Tang"]["printedPages"] == [1, 2] and man["Alpha Tang"]["kind"] == "monograph"
    assert man["Gamma Wan"]["kind"] == "related" and man["Gamma Wan"]["pageSource"] == "entries"
    assert man["Alpha Tang"]["profile"].startswith("test-book@")
    assert all("leak" not in e["flags"] and "possible-truncation" not in e["flags"] for e in man.values())
    assert (out / "Alpha Tang.pdf").exists() and (out / "review" / "index.html").exists()
    assert (out / "review" / "Gamma-Wan-first.png").exists() and (out / "overrides.json").read_text() == "{}\n"
    assert any("wrote 7 per-entry PDFs" in l for l in logs)

    # --only re-runs one entry and MERGES into the manifest; an override is applied and flagged
    (out / "overrides.json").write_text(json.dumps({"Delta Yin": {"pages": [4, 4], "endCut": 500.0, "endCol": "full"}}))
    rc = main(["--pdf", info["pdf"], "--out", str(out), "--profile", str(TEST_PROFILE),
               "--entries", str(entries), "--only", "Delta Yin,Nobody"], log=logs.append)
    assert rc == 0 and any("no entry for ['Nobody']" in l for l in logs)
    man2 = {e["formula"]: e for e in json.loads((out / "manifest.json").read_text())}
    assert set(man2) == set(man)
    assert man2["Delta Yin"]["endCut"] == 500.0 and "override" in man2["Delta Yin"]["flags"] and man2["Delta Yin"]["pageSource"] == "override"
    assert man2["Alpha Tang"] == man["Alpha Tang"]


def test_cli_refuses_to_run_without_entries_or_with_a_bad_profile(tmp_path):
    info = scenario_book(tmp_path / "book.pdf")
    logs: list[str] = []
    assert main(["--pdf", info["pdf"], "--out", str(tmp_path / "o")], log=logs.append) == 2
    bad = tmp_path / "bad.toml"; bad.write_text("[nope]\nx = 1\n")
    e = tmp_path / "e.json"; e.write_text("[]")
    assert main(["--pdf", info["pdf"], "--out", str(tmp_path / "o"), "--profile", str(bad), "--entries", str(e)], log=logs.append) == 2
    assert any("unknown table [nope]" in l for l in logs)
