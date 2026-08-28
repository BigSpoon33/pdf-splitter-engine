from pathlib import Path

import pytest

from monograph_splitter.profile import load_profile


@pytest.fixture(scope="session")
def prof():
    return load_profile(Path(__file__).parent / "profile-test.toml")
