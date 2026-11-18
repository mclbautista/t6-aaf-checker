"""Bounded PCM peak scans shared by silence and low-level rules."""

import math

import numpy as np
import soundfile


class EmbeddedPeak:
    """Keep only the peak and a partial PCM frame across reader blocks.

    Bounds are exact seconds; an end of None scans to the end of the stream.
    A sample counts when its start lies in the half-open window, so both
    bounds round up to integer sample frames.
    """

    def __init__(self, start, end):
        self.start = start
        self.end = end
        self.frames_read = 0
        self.peak = None
        self.pending = b""

    def consume(self, block, fmt):
        if block.chunk_id != b"data" or fmt is None or fmt.encoding != 1:
            return
        if fmt.bits not in (16, 24, 32) or fmt.block_align != fmt.channels * (fmt.bits // 8):
            return
        data = self.pending + block.data
        count = len(data) // fmt.block_align * fmt.block_align
        self.pending = data[count:]
        first = max(0, math.ceil(self.start * fmt.sample_rate) - self.frames_read)
        last = count // fmt.block_align
        if self.end is not None:
            last = min(last, math.ceil(self.end * fmt.sample_rate) - self.frames_read)
        self.frames_read += count // fmt.block_align
        if last <= first:
            return
        data = data[first * fmt.block_align:last * fmt.block_align]
        count = len(data)
        if fmt.bits == 24:
            octets = np.frombuffer(data[:count], dtype=np.uint8).reshape(-1, 3).astype(np.int32)
            values = octets[:, 0] | octets[:, 1] << 8 | octets[:, 2] << 16
            values = (values ^ (1 << 23)) - (1 << 23)
        else:
            values = np.frombuffer(data[:count], dtype=f"<i{fmt.bits // 8}").astype(np.int64)
        peak = int(np.max(np.abs(values))) / (2 ** (fmt.bits - 1))
        self.peak = max(self.peak or 0, peak)


def linked_peak(path, chunk_bytes, start, end):
    peak = None
    with soundfile.SoundFile(str(path), "r") as media:
        first = min(media.frames, max(0, math.ceil(start * media.samplerate)))
        last = media.frames if end is None else min(
            media.frames, max(0, math.ceil(end * media.samplerate))
        )
        media.seek(first)
        remaining = last - first
        frames = max(1, chunk_bytes // (8 * media.channels))
        while remaining > 0:
            block = media.read(min(frames, remaining), dtype="float64", always_2d=True)
            if not len(block):
                raise ValueError("Linked audio ended before its scan window.")
            remaining -= len(block)
            peak = max(peak or 0, float(np.max(np.abs(block))))
    return peak
