"""Used-range audio identity independent of WAV packaging and delivery mode."""

import hashlib
import math

import soundfile

from .audio import EmbeddedPCMWindow


def sample_bytes(values):
    """Canonical sample values: normalized float64, little endian, interleaved."""
    return values.astype("<f8", copy=False).tobytes()


class EmbeddedContentHash:
    def __init__(self, start, end):
        self.window = EmbeddedPCMWindow(start, end)
        self.digest = hashlib.sha256()

    def consume(self, block, fmt):
        values = self.window.consume(block, fmt)
        if values is not None:
            self.digest.update(sample_bytes(values))

    def hexdigest(self):
        # Unsupported or incomplete used ranges have no trustworthy identity.
        if (self.window.end_sample is None or self.window.pending
                or self.window.frames_read < self.window.end_sample):
            return None
        return self.digest.hexdigest()


def linked_content_hash(path, chunk_bytes, start, end):
    digest = hashlib.sha256()
    with soundfile.SoundFile(str(path), "r") as media:
        first = max(0, math.ceil(start * media.samplerate))
        last = max(0, math.ceil(end * media.samplerate))
        if last > media.frames or first > media.frames:
            return None
        media.seek(first)
        remaining = last - first
        frames = max(1, chunk_bytes // (8 * media.channels))
        while remaining > 0:
            block = media.read(min(frames, remaining), dtype="float64", always_2d=True)
            if not len(block):
                raise ValueError("Linked audio ended before its used range.")
            remaining -= len(block)
            digest.update(sample_bytes(block))
    return digest.hexdigest()
