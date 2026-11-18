"""Read-only sound timeline traversal and per-occurrence media resolution."""

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from urllib.parse import unquote, urlsplit

from aaf2.components import OperationGroup, Sequence, SourceClip, Transition
from aaf2.essence import FileDescriptor
from aaf2.mobid import MobID


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
    outside_delivery: bool
    essence_id: MobID | None
    descriptor: FileDescriptor | None
    edit_rate: Fraction
    used_start: Fraction
    used_end: Fraction
    scan_start: Fraction
    scan_end: Fraction


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
                    return candidate, False
    for locator in locators:
        stored = locator_path(locator)
        if stored.is_file():
            return stored, True
    return None, False


def clip_media(component, containing_rate, essence_ids, path, config):
    """Resolve file media and its in-point; use upstream locators to name essence."""
    seen = set()
    file_mob = None
    embedded_id = None
    embedded_name = None
    media_descriptor = None
    reference = component
    media_start = Fraction(0)
    reached_file = False
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
            target_slot = None
        # Reaching essence requires a valid reference. Once found, an upstream
        # mob can still supply a locator even when it has no further slots.
        if embedded_id is None and target_slot is None:
            break
        # StartTime belongs to the slot containing this SourceClip. Stop adding
        # offsets at the delivered file; later hops describe physical sources.
        if not reached_file and target_slot is not None:
            media_start += Fraction(reference.start, 1) / containing_rate
        descriptor = getattr(mob, "descriptor", None)
        if mob.mob_id in essence_ids or isinstance(descriptor, FileDescriptor):
            reached_file = True
        if embedded_id is None and mob.mob_id in essence_ids:
            embedded_id = mob.mob_id
            media_descriptor = descriptor
        if embedded_id is not None:
            embedded_name = embedded_name or mob.name
            locators = descriptor["Locator"].value if descriptor else []
            if locators:
                name = locator_path(locators[0]["URLString"].value).name
                return True, None, name, False, embedded_id, media_descriptor, media_start
        if target_slot is None:
            break
        if embedded_id is None and isinstance(descriptor, FileDescriptor):
            file_mob = mob
        containing_rate = target_slot.edit_rate
        reference = _first_source_clip(target_slot.segment)
    if embedded_id is not None:
        return True, None, embedded_name, False, embedded_id, media_descriptor, media_start
    if file_mob is None:
        return False, None, None, False, None, None, media_start
    locators = [item["URLString"].value for item in file_mob.descriptor["Locator"].value]
    linked_path, outside_delivery = linked_file(
        path, locators, config["linked_media"]["levels_above"]
    )
    return (
        False, linked_path,
        locator_path(locators[0]).name if locators else file_mob.name,
        outside_delivery,
        None,
        file_mob.descriptor,
        media_start,
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
                (
                    embedded, linked_path, source_file, outside_delivery,
                    essence_id, descriptor, used_start,
                ) = clip_media(component, slot.edit_rate, essence_ids, path, config)
                record_start = Fraction(position, 1) / slot.edit_rate
                timecode_rate = timecode_slot.edit_rate if timecode_slot else slot.edit_rate
                fps = timecode_slot.segment.fps if timecode_slot else round(slot.edit_rate)
                frames = (timecode_start + record_start * timecode_rate) // 1
                handles = Fraction(config["handle_frames"], 1) / slot.edit_rate
                used_end = used_start + Fraction(component.length, 1) / slot.edit_rate
                scan_start = used_start - handles
                scan_end = used_end + handles
                clips.append(TimelineClip(
                    slot.slot_id, slot.name, record_start,
                    timecode_label(frames, fps, drop),
                    master.name if master else None, source_file,
                    embedded, linked_path, outside_delivery, essence_id, descriptor,
                    Fraction(slot.edit_rate), used_start, used_end,
                    scan_start, scan_end,
                ))
    return sorted(clips, key=lambda clip: (clip.slot_id, clip.record_start))
