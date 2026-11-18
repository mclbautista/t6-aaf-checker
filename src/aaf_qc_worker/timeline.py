"""Read-only sound timeline traversal and per-occurrence media resolution."""

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from urllib.parse import unquote, urlsplit

from aaf2.components import OperationGroup, Sequence, SourceClip, Transition
from aaf2.essence import FileDescriptor


@dataclass(frozen=True)
class TimelineClip:
    slot_id: int
    track_name: str
    record_start: Fraction
    record_in_timecode: str
    name: str | None
    source_file: str | None
    embedded: bool
    linked_path: Path | None


def locator_path(url):
    parsed = urlsplit(url)
    return Path(unquote(parsed.path)) if parsed.scheme == "file" else Path(url)


def linked_file(path, locators, levels_above):
    """Try longest locator suffixes in delivery folders before stored paths."""
    roots = [path.resolve().parent]
    for _ in range(levels_above):
        roots.append(roots[-1].parent)
    for locator in locators:
        stored = locator_path(locator)
        parts = stored.parts[1:] if stored.is_absolute() else stored.parts
        for start in range(len(parts)):
            for root in roots:
                candidate = root.joinpath(*parts[start:])
                if candidate.is_file():
                    return candidate
    for locator in locators:
        stored = locator_path(locator)
        if stored.is_file():
            return stored
    return None


def clip_media(component, essence_ids, path, config):
    """Follow references until the file mob; physical tape mobs are not files."""
    seen = set()
    file_mob = None
    reference = component
    while isinstance(reference, SourceClip):
        identity = (reference.mob_id, reference.slot_id)
        if identity in seen:
            break
        seen.add(identity)
        mob = reference.mob
        if mob is None:
            break
        try:
            target_slot = reference.slot
        except IndexError:
            break
        if target_slot is None:
            break
        descriptor = getattr(mob, "descriptor", None)
        if mob.mob_id in essence_ids:
            locators = descriptor["Locator"].value if descriptor else []
            name = locator_path(locators[0]["URLString"].value).name if locators else mob.name
            return True, None, name
        if isinstance(descriptor, FileDescriptor):
            file_mob = mob
        reference = _first_source_clip(target_slot.segment)
    if file_mob is None:
        return False, None, None
    locators = [item["URLString"].value for item in file_mob.descriptor["Locator"].value]
    return (
        False,
        linked_file(path, locators, config["linked_media"]["levels_above"]),
        locator_path(locators[0]).name if locators else file_mob.name,
    )


def _first_source_clip(segment):
    """Mob slots may wrap their SourceClip in a Sequence (Premiere Pro does)."""
    while isinstance(segment, (Sequence, OperationGroup)):
        children = segment.components if isinstance(segment, Sequence) else segment.segments
        segment = next((c for c in children if isinstance(c, (SourceClip, Sequence, OperationGroup))), None)
    return segment


def timeline_occurrences(segment, position=0):
    """Only timeline SourceClip occurrences count; transitions overlap neighbours."""
    if isinstance(segment, SourceClip):
        yield position, segment
    elif isinstance(segment, Sequence):
        for _, offset, component in segment.positions():
            if not isinstance(component, Transition):
                yield from timeline_occurrences(component, position + offset)
    elif isinstance(segment, OperationGroup):
        for component in segment.segments:
            yield from timeline_occurrences(component, position)


def timecode_label(frames, fps, drop):
    """Convert a frame count to nominal labels, skipping drop-frame numbers."""
    sign = "-" if frames < 0 else ""
    frames = abs(frames)
    if drop:
        dropped = round(Fraction(fps, 15))
        ten_minutes = fps * 600 - dropped * 9
        minute = fps * 60 - dropped
        tens, remainder = divmod(frames, ten_minutes)
        frames += dropped * 9 * tens
        if remainder >= dropped:
            frames += dropped * ((remainder - dropped) // minute)
    seconds, frame = divmod(frames, fps)
    minutes, second = divmod(seconds, 60)
    hour, minute = divmod(minutes, 60)
    separator = ";" if drop else ":"
    return f"{sign}{hour:02}:{minute:02}:{second:02}{separator}{frame:02}"


def sound_clips(aaf, path, config):
    essence_ids = {essence.mob_id for essence in aaf.content.essencedata}
    clips = []
    for composition in aaf.content.compositionmobs():
        timecode_slot = next(
            (slot for slot in composition.slots if slot.media_kind == "Timecode"), None
        )
        timecode_start = timecode_slot.segment.start if timecode_slot else 0
        drop = timecode_slot.segment.drop if timecode_slot else False
        for slot in composition.slots:
            if slot.media_kind != "Sound":
                continue
            for position, component in timeline_occurrences(slot.segment):
                master = component.mob
                embedded, linked_path, source_file = clip_media(
                    component, essence_ids, path, config
                )
                record_start = Fraction(position, 1) / slot.edit_rate
                timecode_rate = timecode_slot.edit_rate if timecode_slot else slot.edit_rate
                fps = timecode_slot.segment.fps if timecode_slot else round(slot.edit_rate)
                frames = (timecode_start + record_start * timecode_rate) // 1
                clips.append(TimelineClip(
                    slot.slot_id, slot.name, record_start,
                    timecode_label(frames, fps, drop),
                    master.name if master else None, source_file,
                    embedded, linked_path,
                ))
    return sorted(clips, key=lambda clip: (clip.slot_id, clip.record_start))
