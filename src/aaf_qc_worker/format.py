"""Descriptor and media header observations for the Format mismatch rule."""

import struct

from aaf2.essence import PCMDescriptor, WAVEDescriptor
import soundfile

from .audio import WaveFormat


def wave_properties(fmt):
    return {"sample_rate": fmt.sample_rate, "bits": fmt.bits, "channels": fmt.channels,
            "encoding": {1: "PCM", 3: "FLOAT", 6: "ALAW", 7: "ULAW"}.get(
                fmt.encoding, f"WAV tag {fmt.encoding}"),
            "container": "WAV"}


def descriptor_properties(descriptor):
    if isinstance(descriptor, WAVEDescriptor):
        summary = bytes(descriptor["Summary"].value or ())
        if not summary or summary[:4] != b"RIFF" or summary[8:12] != b"WAVE":
            raise ValueError("Descriptor Summary has no RIFF/WAVE header.")
        offset = 12
        while offset + 8 <= len(summary):
            chunk, size = struct.unpack_from("<4sI", summary, offset)
            offset += 8
            if chunk == b"fmt ":
                if size < 16 or offset + size > len(summary):
                    break
                return wave_properties(WaveFormat.from_chunk(summary[offset:offset + min(size, 40)]))
            offset += size + size % 2
        raise ValueError("Descriptor Summary is missing a complete WAV format chunk.")
    if isinstance(descriptor, PCMDescriptor):
        return {"sample_rate": descriptor["AudioSamplingRate"].value,
                "bits": descriptor["QuantizationBits"].value,
                "channels": descriptor["Channels"].value, "encoding": "PCM", "container": "WAV"}
    raise ValueError("Audio descriptor format cannot be read.")


def linked_properties(path):
    try:
        info = soundfile.info(str(path))
    except (soundfile.SoundFileError, OSError) as error:
        raise ValueError(f"Linked media header cannot be read: {error}") from error
    bits = {"PCM_U8": 8, "PCM_16": 16, "PCM_24": 24, "PCM_32": 32,
            "FLOAT": 32, "DOUBLE": 64}.get(info.subtype)
    return {"sample_rate": info.samplerate, "bits": bits, "channels": info.channels,
            "encoding": "PCM" if info.subtype.startswith("PCM_") else info.subtype,
            "container": "WAV" if info.format in ("WAV", "WAVEX") else info.format}


def mismatch_message(descriptor, media, rule):
    problems = []
    for source, properties in (("AAF descriptor", descriptor), ("Media header", media)):
        if properties is None:
            continue
        for field, expected in rule["expected"].items():
            actual = properties.get(field)
            if actual != expected:
                label = rule["property_names"][field]
                problems.append(f"{source} {label} is {actual}; expected {expected}.")
    if descriptor is not None and media is not None:
        for field, label in rule["property_names"].items():
            if descriptor.get(field) != media.get(field):
                problems.append(
                    f"AAF descriptor and media header disagree on {label}: "
                    f"descriptor {descriptor.get(field)}, media header {media.get(field)}."
                )
    return " ".join(problems)
