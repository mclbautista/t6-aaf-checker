"""Small synthetic Premiere-style AAFs, generated only in temporary test folders.

Use from CLI tests (no pyaaf2 code is needed)::

    clean = Timeline()
    embedded = clean.export(tmp_path / "embedded.aaf")
    linked = clean.export(tmp_path / "linked.aaf", mode="linked")
    stereo = clean.with_clip(3, channels=2).export(tmp_path / "stereo.aaf")
    mono = clean.with_clip(3, channels=1).export(tmp_path / "mono.aaf")
    corrupt = patch_header(embedded, output=tmp_path / "corrupt.aaf", major_version=4)
    truncated = clean.with_clip(3, truncate_bytes=3).export(tmp_path / "truncated.aaf")
    flagged, corrected = clean.export_pair(tmp_path / "pair")

``with_clip`` uses one-based clip numbers and dataclass field names. It returns a
new timeline, so reds cannot mutate their green counterparts. Clip fields cover
sample rate, channels, bit depth, PCM sample value (zero for silence), metadata,
handles and timing. ``media_mode`` overrides the export mode for one clip;
``chain_depth`` inserts upstream mobs with stale locators before its file mob.
Amplitudes are signed integer PCM values in the selected bit
depth. One clip occupies each sound slot; supply a different ``clips`` tuple for
a different timeline. Durations, positions and handles are sequence frames.

Embedded essence contains complete RIFF/WAVE streams keyed by the file SourceMob
ID. Linked WAVs live in a unique ``<AAF stem>_media`` folder beside each export;
the absolute file-URI locators point into an absent export folder. Both modes
retain those stale locators. Tape is a physical SourceMob name; scene and take
are MasterMob comments, matching the configured metadata fallback sources.

``patch_header`` changes CFB version/sector-shift bytes, optionally on a copy.
``truncate_bytes`` removes WAV data while preserving the original header sizes
and descriptor length, creating an unreadable-audio red with a valid container.
Re-exporting the same Timeline rebuilds all mobs with fresh pyaaf2-generated IDs,
preserving clip names, media names, PCM samples, positions and metadata. The pair
helper defaults to embedded/linked, or accepts the same mode for both exports.

Assertions belong at the worker CLI seam. Until a later rule is implemented,
its defective fixture may still PASS; add that rule's FAIL assertion and observe
it fail before implementing the rule. These support tests do not claim to test
audio rules or comparison. Outputs under the repository are rejected. Always
pass pytest's tmp_path (or a TemporaryDirectory), never a checked-in directory.
"""

from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
import shutil
import struct
import tempfile
import wave

import aaf2


EDIT_RATE = "24000/1001"
CHUNK_FRAMES = 8192


@dataclass(frozen=True)
class Clip:
    name: str
    media_name: str
    track_slot: int
    duration: int = 24
    record_start: int = 0
    head: int = 120
    tail: int = 120
    sample_rate: int = 48000
    bits: int = 24
    channels: int = 1
    sample_value: int = 2**14
    tape: str = "Synthetic tape"
    scene: str = "Scene 1"
    take: str = "1"
    truncate_bytes: int = 0
    media_mode: str | None = None
    chain_depth: int = 0


@dataclass(frozen=True)
class Timeline:
    clips: tuple[Clip, ...] = (
        Clip("Clip 1", "clip_1.wav", 3),
        Clip("Clip 2", "clip_2.wav", 4),
        Clip("Clip 3", "clip_3.wav", 5),
    )

    def with_clip(self, number: int, **changes) -> "Timeline":
        """Return a variant changing one clip (one-based); leave this timeline intact."""
        if not 1 <= number <= len(self.clips):
            raise IndexError("Clip number is one-based and must exist.")
        clips = list(self.clips)
        clips[number - 1] = replace(clips[number - 1], **changes)
        return replace(self, clips=tuple(clips))

    def export_pair(
        self, folder: Path, *, flagged_mode: str = "embedded",
        corrected_mode: str = "linked",
    ) -> tuple[Path, Path]:
        """Rebuild the same timeline twice; pyaaf2 generates fresh IDs for every mob."""
        folder = Path(folder)
        flagged = self.export(folder / "flagged.aaf", mode=flagged_mode)
        corrected = self.export(folder / "corrected.aaf", mode=corrected_mode)
        return flagged, corrected

    def export(
        self, path: Path, *, mode: str = "embedded", sector_size: int = 512,
        major_version: int | None = None,
    ) -> Path:
        if mode not in ("embedded", "linked"):
            raise ValueError("mode must be 'embedded' or 'linked'")
        path = _outside_repository(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=path.parent) as scratch:
            media_folder = f"{path.stem}_media"
            media_root = path.parent / media_folder if mode == "linked" else Path(scratch)
            media_root = _outside_repository(media_root)
            media_root.mkdir(exist_ok=True)
            with aaf2.open(str(path), "w", sector_size=sector_size) as aaf:
                identification = aaf.header["IdentificationList"].value[0]
                identification["CompanyName"].value = "Adobe"
                identification["ProductName"].value = "Adobe Premiere Pro"
                composition = aaf.create.CompositionMob("Synthetic sequence")
                aaf.content.mobs.append(composition)
                duration = max(clip.record_start + clip.duration for clip in self.clips)
                timecode = composition.create_timeline_slot(EDIT_RATE, slot_id=1)
                timecode.name = "Timecode"
                timecode.segment = aaf.create.Timecode(fps=24, drop=False, length=duration)
                picture = composition.create_timeline_slot(EDIT_RATE, slot_id=2)
                picture.name = "Picture"
                picture.segment = aaf.create.Filler(media_kind="picture", length=duration)

                for clip in self.clips:
                    wav_path = media_root / clip.media_name
                    samples = _write_wav(wav_path, clip)
                    master, source = _audio_chain(aaf, clip, samples)
                    # This path is never created. The delivered WAV is beside the
                    # AAF; embedded exports retain an equally stale locator.
                    locator = aaf.create.NetworkLocator()
                    locator["URLString"].value = (
                        path.parent / ".unavailable-export" / media_folder / clip.media_name
                    ).as_uri()
                    source.descriptor["Locator"].append(locator)
                    clip_mode = clip.media_mode or mode
                    if clip_mode == "linked" and mode == "embedded":
                        delivered = path.parent / media_folder
                        delivered.mkdir(exist_ok=True)
                        shutil.copyfile(wav_path, delivered / clip.media_name)
                    if clip_mode == "embedded":
                        essence = aaf.create.EssenceData()
                        essence.mob_id = source.mob_id
                        aaf.content.essencedata.append(essence)
                        with wav_path.open("rb") as wav:
                            stream = essence.open("w")
                            while block := wav.read(CHUNK_FRAMES * clip.channels * (clip.bits // 8)):
                                stream.write(block)

                    slot = composition.create_empty_sequence_slot(
                        EDIT_RATE, slot_id=clip.track_slot, media_kind="sound"
                    )
                    slot.name = f"Audio {clip.track_slot - 2}"
                    if clip.record_start:
                        slot.segment.components.append(
                            aaf.create.Filler(media_kind="sound", length=clip.record_start)
                        )
                    slot.segment.components.append(
                        master.create_source_clip(1, start=clip.head, length=clip.duration)
                    )
                    remaining = duration - clip.record_start - clip.duration
                    if remaining:
                        slot.segment.components.append(
                            aaf.create.Filler(media_kind="sound", length=remaining)
                        )
        # pyaaf2 writes version 4 even for 512-byte sectors. Normalize clean
        # exports, or deliberately declare an incompatible version for a red.
        patch_header(
            path, major_version=major_version if major_version is not None
            else {512: 3, 4096: 4}[sector_size],
        )
        return path


def patch_header(
    path: Path, *, major_version: int | None = None, sector_shift: int | None = None,
    output: Path | None = None,
) -> Path:
    """Patch CFB version/shift bytes; optionally copy first to preserve the green."""
    path = _outside_repository(path)
    if output is not None:
        output = _outside_repository(output)
        shutil.copyfile(path, output)
        path = output
    with path.open("r+b") as stream:
        for offset, value in ((26, major_version), (30, sector_shift)):
            if value is not None:
                stream.seek(offset)
                stream.write(struct.pack("<H", value))
    return path


@contextmanager
def edit_export(path):
    """Build structural variants of a synthetic export outside the repository."""
    path = _outside_repository(path)
    with aaf2.open(str(path), "rw") as aaf:
        yield aaf
    patch_header(path, major_version=3)


def operation_group(aaf, *, length, operation="gain"):
    definitions = {
        "gain": ("9d2ea894-0968-11d3-8a38-0050040ef7d2", 1),
        "pan": ("9d2ea893-0968-11d3-8a38-0050040ef7d2", 1),
        "dissolve": ("0c3bea41-fc05-11d2-8a29-0050040ef7d2", 2),
    }
    identifier, inputs = definitions[operation]
    definition = aaf.create.OperationDef(identifier, f"Synthetic {operation}")
    definition.media_kind = "sound"
    definition.number_inputs = inputs
    aaf.dictionary.register_def(definition)
    return aaf.create.OperationGroup(definition, length=length, media_kind="sound")


def _outside_repository(path: Path) -> Path:
    path = Path(path).resolve()
    if path.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError("Synthetic exports must be outside the repository.")
    return path


def _write_wav(path: Path, clip: Clip) -> int:
    if Path(clip.media_name).name != clip.media_name:
        raise ValueError("media_name must be a bare file name.")
    path = _outside_repository(path)
    # Round up so non-integral frame/sample conversions never shorten a handle.
    frames = clip.head + clip.duration + clip.tail
    samples = (frames * clip.sample_rate * 1001 + 23999) // 24000
    width = clip.bits // 8
    sample = clip.sample_value.to_bytes(width, "little", signed=True)
    block = sample * clip.channels * CHUNK_FRAMES
    with wave.open(str(path), "wb") as wav:
        wav.setparams((clip.channels, width, clip.sample_rate, 0, "NONE", "not compressed"))
        for start in range(0, samples, CHUNK_FRAMES):
            count = min(CHUNK_FRAMES, samples - start)
            wav.writeframesraw(block[:count * clip.channels * width])
    if clip.truncate_bytes:
        data_bytes = samples * clip.channels * width
        if not 0 < clip.truncate_bytes <= data_bytes:
            raise ValueError("truncate_bytes must remove only audio data.")
        # Keep the WAV header and AAF descriptor's original declared lengths.
        with path.open("r+b") as stream:
            stream.truncate(path.stat().st_size - clip.truncate_bytes)
    return samples


def _audio_chain(aaf, clip: Clip, samples: int):
    frames = clip.head + clip.duration + clip.tail
    tape = aaf.create.SourceMob(clip.tape)
    aaf.content.mobs.append(tape)
    tape.descriptor = aaf.create.TapeDescriptor()
    tape_slot = tape.create_empty_slot(EDIT_RATE, media_kind="sound", slot_id=1)
    tape_slot.segment.length = frames
    # Mob(name="") substitutes a default name; allow an explicitly blank tape.
    tape.name = clip.tape

    source = aaf.create.SourceMob(clip.media_name)
    aaf.content.mobs.append(source)
    source_slot = source.create_timeline_slot(EDIT_RATE, slot_id=1)
    source_slot.segment = tape.create_source_clip(1, length=frames)
    descriptor = aaf.create.PCMDescriptor()
    source.descriptor = descriptor
    for field, value in {
        "SampleRate": clip.sample_rate,
        "AudioSamplingRate": clip.sample_rate,
        "Channels": clip.channels,
        "QuantizationBits": clip.bits,
        "BlockAlign": clip.channels * (clip.bits // 8),
        "AverageBPS": clip.sample_rate * clip.channels * (clip.bits // 8),
        "Length": samples,
    }.items():
        descriptor[field].value = value

    master = aaf.create.MasterMob(clip.name)
    aaf.content.mobs.append(master)
    master.comments["Scene"] = clip.scene
    master.comments["Take"] = clip.take
    master_slot = master.create_timeline_slot(EDIT_RATE, slot_id=1)
    target = source
    for index in range(clip.chain_depth):
        intermediate = aaf.create.SourceMob(f"Intermediate {index}")
        aaf.content.mobs.append(intermediate)
        intermediate.descriptor = aaf.create.ImportDescriptor()
        stale = aaf.create.NetworkLocator()
        stale["URLString"].value = "file:///nonexistent-synthetic-storage/stale.wav"
        intermediate.descriptor["Locator"].append(stale)
        slot = intermediate.create_timeline_slot(EDIT_RATE, slot_id=1)
        slot.segment = _in_sequence(aaf, target.create_source_clip(1, length=frames))
        target = intermediate
    # Premiere Pro wraps each MasterMob slot's SourceClip in a one-item Sequence.
    master_slot.segment = _in_sequence(aaf, target.create_source_clip(1, length=frames))
    return master, source


def _in_sequence(aaf, component):
    sequence = aaf.create.Sequence(media_kind="sound")
    sequence.components.append(component)
    return sequence
