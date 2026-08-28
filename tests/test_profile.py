import pytest

from monograph_splitter.profile import Profile, ProfileError, load_profile


def test_builtin_defaults_are_the_first_book():
    p = load_profile(None)
    assert p.sheet_offset == 39 and p.column_split == 0.487 and p.script_regex == "[一-鿿]"
    assert p.label_name_re.groups == 1
    assert p.tag == "chen-chen-formulas@builtin"


def test_partial_profile_overrides_a_subset_and_keeps_the_rest(tmp_path):
    f = tmp_path / "p.toml"
    f.write_text('name = "x"\n[book]\nsheet_offset = 3\n[title]\nmin_gap = 20\n')
    p = load_profile(f)
    assert p.name == "x" and p.sheet_offset == 3 and p.min_gap == 20.0
    assert p.header_band == Profile().header_band
    assert p.sha256 and p.tag.startswith("x@")


@pytest.mark.parametrize("body,needle", [
    ('[typo]\nx = 1\n', "unknown table [typo]"),
    ('[title]\nbig_min_sze = 12\n', "unknown key [title].big_min_sze"),
    ('[book]\nsheet_offset = "39"\n', "expected int"),
    ('[labels]\nname = "no group here"\n', "capture group"),
    ('[layout]\ncolumn_split = 2\n', "fraction"),
])
def test_a_wrong_profile_fails_loudly_naming_the_key(tmp_path, body, needle):
    f = tmp_path / "bad.toml"
    f.write_text(body)
    with pytest.raises(ProfileError) as e:
        load_profile(f)
    assert needle in str(e.value)


def test_the_shipped_chen_chen_profile_equals_the_builtin_defaults():
    from dataclasses import fields
    from monograph_splitter.cli import bundled_profile
    p = load_profile(bundled_profile("chen-chen-formulas"))
    d = Profile()
    diff = [f.name for f in fields(Profile) if f.name not in ("path", "sha256") and getattr(p, f.name) != getattr(d, f.name)]
    assert diff == []
