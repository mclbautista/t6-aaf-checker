"""Container checks use CFB header bytes, independently of the AAF reader."""

import struct


def stream_reaches_offset(stream, boundary):
    """Inspect actual chain positions, including fragmented and mini streams.

    pyaaf2 builds its compact FAT array once from the header and DIFAT when the
    AAF opens. Stream.fat_chain walks that shared table; these are sector IDs,
    not an estimate from a stream's start and length. No essence is read here.
    """
    storage = stream.storage
    for sid in stream.fat_chain:
        offset = 0
        if stream.is_mini_stream():
            index, offset = divmod(sid * storage.mini_stream_sector_size, storage.sector_size)
            sid = storage.mini_stream_chain[index]
        if (sid + 1) * storage.sector_size + offset >= boundary:
            return True
    return False


def corruption_message(header, file_size, rule):
    if len(header) != 512:
        raise ValueError("Cannot read a complete 512-byte CFB header.")
    if header[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise ValueError("Not an AAF compound file: invalid CFB signature.")
    major_version, = struct.unpack_from("<H", header, 26)
    sector_shift, = struct.unpack_from("<H", header, 30)
    expected_shift = rule["sector_shifts"].get(str(major_version))
    if expected_shift is not None and sector_shift != expected_shift:
        declared_sectors = (
            f"{1 << sector_shift}-byte sectors"
            if sector_shift in rule["sector_shifts"].values()
            else f"an invalid sector shift of {sector_shift}"
        )
        return (
            f"CFB major version {major_version} declares {declared_sectors}; "
            f"it requires {1 << expected_shift}-byte sectors."
        )
    max_bytes = rule["max_file_bytes"].get(str(major_version))
    if max_bytes is not None and file_size > max_bytes:
        return (
            f"CFB major version {major_version} file is {file_size} bytes; "
            "it exceeds the 2 GiB size limit."
        )
    return None
