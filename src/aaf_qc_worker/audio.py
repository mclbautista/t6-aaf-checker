"""One forward-only WAV pass over an embedded essence or a binary file stream.

Iterate to EOF to validate completeness. Blocks expose their RIFF chunk ID and
absolute byte offset so later rules can inspect metadata or PCM in the same
pass. Only the fixed-size format prefix is retained; audio is never accumulated.
The caller owns the stream and opens it read-only.
"""

from dataclasses import dataclass
import struct


class AudioUnreadable(ValueError):
    """The audio stream ended early or has an unreadable WAV structure."""


@dataclass(frozen=True)
class WaveFormat:
    encoding: int
    channels: int
    sample_rate: int
    byte_rate: int
    block_align: int
    bits: int

    @classmethod
    def from_chunk(cls, prefix):
        if len(prefix) < 16:
            raise AudioUnreadable("Incomplete WAV format chunk.")
        fields = list(struct.unpack_from("<HHIIHH", prefix))
        if fields[0] == 0xFFFE:
            if len(prefix) < 40 or struct.unpack_from("<H", prefix, 16)[0] < 22:
                raise AudioUnreadable("Incomplete extensible WAV format chunk.")
            # WAVEFORMATEXTENSIBLE's subformat GUID identifies PCM or float.
            if prefix[28:40] == bytes.fromhex("00001000800000aa00389b71"):
                fields[0] = struct.unpack_from("<I", prefix, 24)[0]
            fields[5] = struct.unpack_from("<H", prefix, 18)[0] or fields[5]
        return cls(*fields)


@dataclass(frozen=True)
class AudioBlock:
    chunk_id: bytes | None
    offset: int
    data: bytes | bytearray


class AudioReader:
    def __init__(self, stream, chunk_bytes):
        self.stream = stream
        self.chunk_bytes = chunk_bytes
        self.bytes_read = 0
        self.format = None
        self.chunk_ids = set()
        self.audio_bytes = 0

    def _part(self, size, chunk_id=None, capture=0):
        prefix = bytearray()
        while size:
            block = self.stream.read(min(size, self.chunk_bytes))
            if not block:
                raise AudioUnreadable("WAV ended before its declared length.")
            offset = self.bytes_read
            self.bytes_read += len(block)
            size -= len(block)
            prefix.extend(block[:max(0, capture - len(prefix))])
            yield AudioBlock(chunk_id, offset, block)
        return prefix

    def __iter__(self):
        header = yield from self._part(12, capture=12)
        if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise AudioUnreadable("Audio has no RIFF/WAVE header.")
        remaining = struct.unpack_from("<I", header, 4)[0] - 4
        if remaining < 0:
            raise AudioUnreadable("Invalid RIFF length.")
        while remaining:
            if remaining < 8:
                raise AudioUnreadable("Incomplete WAV chunk header.")
            header = yield from self._part(8, capture=8)
            chunk_id, size = struct.unpack("<4sI", header)
            padded_size = size + size % 2
            remaining -= 8
            if padded_size == remaining + 1:
                # Premiere Pro omits the pad byte after a final odd-sized chunk
                # and excludes it from the RIFF length. The audio is complete.
                padded_size = size
            if padded_size > remaining:
                raise AudioUnreadable("WAV chunk extends beyond its RIFF length.")
            self.chunk_ids.add(chunk_id)
            prefix = yield from self._part(size, chunk_id, capture=40 if chunk_id == b"fmt " else 0)
            if chunk_id == b"fmt ":
                self.format = WaveFormat.from_chunk(prefix)
            elif chunk_id == b"data":
                self.audio_bytes += size
            if padded_size > size:
                yield from self._part(1)
            remaining -= padded_size
        # Read even bytes outside the RIFF envelope; never use seek to skip data.
        while block := self.stream.read(self.chunk_bytes):
            offset = self.bytes_read
            self.bytes_read += len(block)
            yield AudioBlock(None, offset, block)
        if self.format is None or b"data" not in self.chunk_ids:
            raise AudioUnreadable("WAV is missing its format or audio data chunk.")
        if self.format.encoding == 1 and (
            not self.format.block_align or self.audio_bytes % self.format.block_align
        ):
            raise AudioUnreadable("WAV ends with an incomplete PCM sample frame.")
