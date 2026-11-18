from pathlib import Path
import struct

import aaf2
import pytest


@pytest.fixture
def make_aaf(tmp_path):
    def build(name="synthetic.aaf", *, sector_size=512, major_version=None):
        path = tmp_path / name
        with aaf2.open(str(path), "w", sector_size=sector_size) as aaf:
            composition = aaf.create.CompositionMob("Synthetic sequence")
            aaf.content.mobs.append(composition)
            slot = composition.create_timeline_slot(edit_rate=24)
            slot.name = "Audio 1"
            slot.segment = aaf.create.Filler(media_kind="sound", length=24)

        # pyaaf2 writes major version 4 even when asked for 512-byte sectors.
        if major_version is not None or sector_size == 512:
            patch_header(path, major_version=major_version or 3)
        return path

    return build


def patch_header(path: Path, *, major_version: int):
    with path.open("r+b") as stream:
        stream.seek(26)
        stream.write(struct.pack("<H", major_version))
