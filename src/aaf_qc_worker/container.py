"""Container checks use CFB header bytes, independently of the AAF reader."""

import struct


def fat_count_mismatch_message(stream, header, file_size, rule):
    """Count over the declared DIFAT length; anomalies are informational only."""
    declared, = struct.unpack_from("<I", header, 44)
    actual = sum(sid < 0xFFFFFFFA for sid in struct.unpack_from("<109I", header, 76))
    sid, chain_length = struct.unpack_from("<II", header, 68)

    def unreadable(reason):
        return rule["unreadable_message"].format(
            reason=reason, declared=declared, actual=actual,
        )

    if chain_length:
        shift, = struct.unpack_from("<H", header, 30)
        if shift not in (9, 12):
            return unreadable("invalid CFB sector shift")
        sector_size = 1 << shift
        visited = set()
        for _ in range(chain_length):
            offset = (sid + 1) * sector_size
            if sid in visited:
                return unreadable("cycle in the DIFAT chain")
            if sid >= 0xFFFFFFFA:
                return unreadable("DIFAT chain ends before its declared length")
            if offset + sector_size > file_size:
                return unreadable("DIFAT sector extends beyond EOF")
            visited.add(sid)
            try:
                stream.seek(offset)
                sector = stream.read(sector_size)
            except (OSError, ValueError) as error:
                return unreadable(str(error))
            if len(sector) != sector_size:
                return unreadable("incomplete DIFAT sector")
            entries = struct.unpack(f"<{sector_size // 4}I", sector)
            actual += sum(entry < 0xFFFFFFFA for entry in entries[:-1])
            sid = entries[-1]
    # Count only declared sectors, as pyaaf2 does, even if another is linked.
    if sid < 0xFFFFFFFA:
        return (
            rule["message"].format(declared=declared, actual=actual)
            + " " + rule["continuation_message"]
        )
    # pyaaf2 can leave the final pointer as FREESECT.
    if sid not in (0xFFFFFFFE, 0xFFFFFFFF):
        return unreadable("invalid DIFAT chain terminator")
    if declared != actual:
        return rule["message"].format(declared=declared, actual=actual)
    return None


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
