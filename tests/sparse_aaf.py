"""Relocate synthetic essence sectors without filling the sparse 2 GiB gap."""

from array import array
import struct
import sys

import aaf2
from aaf2.cfb import DIFSECT, ENDOFCHAIN, FATSECT, FREESECT

from synthetic_aaf import embedded_essence_by_name


def place_essence_at_boundary(path):
    """Clip 1 ends at the boundary, Clip 2 straddles, Clip 3 is wholly above.

    Rebuild the FAT/DIFAT once, keeping all AAF objects and audio bytes intact.
    Moving Clip 2's interior sector also makes its chain non-contiguous: neither
    the stream start nor start + byte size can reveal its high sector.
    """
    with aaf2.open(str(path), "r") as aaf:
        cfb = aaf.cfb
        sector_size = cfb.sector_size
        fat = array("I", cfb.fat)
        old_fat_sectors = [sid for _, _, sid in cfb.iter_difat() if sid < DIFSECT]
        streams = {}
        for name, item in embedded_essence_by_name(aaf).items():
            stream = item.open("r")
            streams[name] = (
                stream.fat_chain, cfb.dir_entry_pos(stream.dir.dir_id),
            )

    boundary_sid = 2**31 // sector_size - 1
    chain1, directory1 = streams["clip_1.wav"]
    chain2, directory2 = streams["clip_2.wav"]
    chain3, directory3 = streams["clip_3.wav"]
    new1 = chain1[:-1] + [boundary_sid - 1]
    new2 = chain2[:1] + [boundary_sid] + chain2[2:]
    new3 = list(range(boundary_sid + 1, boundary_sid + 1 + len(chain3)))
    fat_start = new3[-1] + 1
    entries = sector_size // 4
    fat_count = difat_count = 0
    while True:
        required = (fat_start + fat_count + difat_count + entries - 1) // entries
        external = max(0, (required - 109 + entries - 2) // (entries - 1))
        if (required, external) == (fat_count, difat_count):
            break
        fat_count, difat_count = required, external
    fat.extend(array("I", [FREESECT]) * (fat_count * entries - len(fat)))
    fat_sectors = list(range(fat_start, fat_start + fat_count))
    difat_sectors = list(range(fat_start + fat_count, fat_start + fat_count + difat_count))
    for sid in old_fat_sectors:
        fat[sid] = FREESECT

    with path.open("r+b") as output:
        for old, new, directory in (
            (chain1, new1, directory1),
            (chain2, new2, directory2),
            (chain3, new3, directory3),
        ):
            for previous, current in zip(old, new):
                if previous != current:
                    output.seek((previous + 1) * sector_size)
                    data = output.read(sector_size)
                    output.seek((current + 1) * sector_size)
                    output.write(data)
                    fat[previous] = FREESECT
            for index, sid in enumerate(new):
                fat[sid] = new[index + 1] if index + 1 < len(new) else ENDOFCHAIN
            output.seek(directory + 116)
            output.write(struct.pack("<I", new[0]))
        for sid in fat_sectors:
            fat[sid] = FATSECT
        for sid in difat_sectors:
            fat[sid] = DIFSECT
        if sys.byteorder != "little":
            fat.byteswap()
        # Write only FAT sectors and relocated audio, never the sparse gap.
        view = memoryview(fat).cast("B")
        for index, sid in enumerate(fat_sectors):
            output.seek((sid + 1) * sector_size)
            output.write(view[index * sector_size:(index + 1) * sector_size])
        remaining = fat_sectors[109:]
        for index, sid in enumerate(difat_sectors):
            table = remaining[index * (entries - 1):(index + 1) * (entries - 1)]
            table += [FREESECT] * (entries - 1 - len(table))
            table.append(difat_sectors[index + 1] if index + 1 < difat_count else ENDOFCHAIN)
            output.seek((sid + 1) * sector_size)
            output.write(struct.pack(f"<{entries}I", *table))
        output.seek(44)
        output.write(struct.pack("<I", fat_count))
        output.seek(68)
        output.write(struct.pack("<II", difat_sectors[0] if difat_count else ENDOFCHAIN, difat_count))
        output.seek(76)
        header_difat = fat_sectors[:109]
        header_difat += [FREESECT] * (109 - len(header_difat))
        output.write(struct.pack("<109I", *header_difat))
