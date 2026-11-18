"""Container checks use CFB header bytes, independently of the AAF reader."""

import struct


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
