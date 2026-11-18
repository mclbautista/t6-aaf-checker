import pytest

from synthetic_aaf import Timeline


@pytest.fixture
def make_aaf(tmp_path):
    def build(name="synthetic.aaf", *, sector_size=512, major_version=None):
        return Timeline().export(
            tmp_path / name, sector_size=sector_size, major_version=major_version
        )

    return build
