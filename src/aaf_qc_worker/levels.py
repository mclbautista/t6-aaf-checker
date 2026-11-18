"""Bounded PCM peak scans shared by silence and low-level rules."""

import math

import numpy as np
import soundfile

from .audio import EmbeddedPCMWindow


class EmbeddedPeak:
    """Keep only the peak and a partial PCM frame across reader blocks.

    Bounds are exact seconds; an end of None scans to the end of the stream.
    A sample counts when its start lies in the half-open window, so both
    bounds round up to integer sample frames.
    """

    def __init__(self, start, end):
        self.window = EmbeddedPCMWindow(start, end)
        self.peak = None

    def consume(self, block, fmt):
        values = self.window.consume(block, fmt)
        if values is not None:
            self.peak = max(self.peak or 0, float(np.max(np.abs(values))))


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
