"""Pair timeline occurrences by source identity, independent of record position."""

from collections import defaultdict, deque
from fractions import Fraction

from .check import clip_location


def mob_keys(clip):
    return [(clip.media_mob_id, clip.used_start, clip.track_type)]


def locator_keys(clip):
    return [(name, clip.used_start, clip.track_type, clip.name)
            for name in clip.locator_names]


def fallback_keys(clip):
    return [(clip.name, clip.used_start, clip.used_end - clip.used_start, clip.track_type)]


def slip_keys(identity_keys):
    def keys(clip):
        return [(*key, clip.slot_id, clip.track_name, clip.record_start)
                for key in identity_keys(clip)]
    return keys


def slip_mob_keys(clip):
    return [(clip.media_mob_id, clip.track_type)]


def slip_locator_keys(clip):
    return [(name, clip.track_type, clip.name) for name in clip.locator_names]


def pair_clips(flagged, corrected):
    # Key priority is global: a weaker key cannot consume a stronger match.
    before_order = sorted(range(len(flagged)), key=lambda i: (
        flagged[i].slot_id, flagged[i].structural_order,
    ))
    after_order = sorted(range(len(corrected)), key=lambda i: (
        corrected[i].slot_id, corrected[i].structural_order,
    ))
    rank = {index: order for order, index in enumerate(before_order)}
    paired_before, paired_after = set(), set()
    pairs = []
    passes = [(keys, False) for keys in (mob_keys, locator_keys, fallback_keys)]
    # A slipped source needs media identity; a clip name alone cannot establish it.
    passes += [(slip_keys(keys), True) for keys in (slip_mob_keys, slip_locator_keys)]
    for keys, slipped in passes:
        available = defaultdict(deque)
        for left in before_order:
            if left not in paired_before:
                for key in keys(flagged[left]):
                    available[key].append(left)
        for right in after_order:
            if right in paired_after:
                continue
            candidates = []
            for key in keys(corrected[right]):
                queue = available[key]
                while queue and queue[0] in paired_before:
                    queue.popleft()
                if slipped:
                    candidate = next((left for left in queue
                                      if left not in paired_before
                                      and flagged[left].used_start != corrected[right].used_start), None)
                    if candidate is not None:
                        candidates.append(candidate)
                elif queue:
                    candidates.append(queue[0])
            if candidates:
                left = min(candidates, key=rank.__getitem__)
                pairs.append((left, right))
                paired_before.add(left)
                paired_after.add(right)
    return sorted(pairs, key=lambda pair: pair[1])


def compare_checks(flagged, corrected, config, emit):
    pairs = pair_clips(flagged.clips, corrected.clips)
    summary = {
        "flagged_verdict": flagged.verdict, "corrected_verdict": corrected.verdict,
        "paired_clips": len(pairs),
    }
    reason = None
    if flagged.edit_rates != corrected.edit_rates:
        reason = "The AAFs have different edit rates."
    elif len(pairs) < Fraction(config["compare"]["minimum_paired_fraction"]) * max(
        len(flagged.clips), len(corrected.clips),
    ):
        reason = "Fewer than half the clips pair; the AAFs do not look like the same reel."
    if reason:
        return dict(summary, verdict="ERROR", reason=reason)
    pending = []
    position = (-1, 0, Fraction(0))

    def collect(event_type, **fields):
        pending.append((position, event_type, fields))

    shifts = {(corrected.clips[right].record_start - flagged.clips[left].record_start)
              * corrected.clips[right].edit_rate for left, right in pairs}
    blocking = 0
    if len(shifts) == 1 and (shift := next(iter(shifts))):
        collect("diff_item", kind="whole_reel_offset", blocking=True,
                delta_frames=frame_value(shift), paired_clips=len(pairs),
                flagged_positions=[clip_position(flagged.clips[left]) for left, _ in pairs],
                corrected_positions=[clip_position(corrected.clips[right]) for _, right in pairs],
                message=f"Whole reel moved by {frame_text(shift)}.")
        blocking += 1
    for before, after in zip(flagged.sequence_timings, corrected.sequence_timings):
        delta = after.start - before.start
        if delta:
            collect("diff_item", kind="start_timecode_change", blocking=True,
                    flagged_position=sequence_position(before),
                    corrected_position=sequence_position(after),
                    delta_frames=frame_value(delta),
                    message=f"Sequence start timecode changed by {frame_text(delta)}.")
            blocking += 1
        delta = after.length - before.length
        if delta:
            collect("diff_item", kind="reel_length_change", blocking=True,
                    flagged_position=sequence_position(before),
                    corrected_position=sequence_position(after),
                    delta_frames=frame_value(delta),
                    message=f"Reel length changed by {frame_text(delta)}.")
            blocking += 1
    classify_findings(
        [finding for finding in flagged.findings if finding["track"] is None],
        [finding for finding in corrected.findings if finding["track"] is None],
        collect,
    )
    before_by_after = {right: left for left, right in pairs}
    # Walk corrected positions for all non-dropped diff items, regardless of kind.
    for right in range(len(corrected.clips)):
        clip = corrected.clips[right]
        position = (0, clip.slot_id, clip.record_start)
        if right in before_by_after:
            left = before_by_after[right]
            classify_findings(flagged.clip_findings[left], corrected.clip_findings[right], collect)
            blocking += compare_positions(flagged.clips[left], corrected.clips[right], collect)
        else:
            emit_unpaired("added", "corrected", corrected, right, collect)
            blocking += 1
    paired_before = {left for left, _ in pairs}
    for left in range(len(flagged.clips)):
        if left not in paired_before:
            clip = flagged.clips[left]
            position = (1, clip.slot_id, clip.record_start)
            emit_unpaired("dropped", "flagged", flagged, left, collect)
            blocking += 1
    for _, event_type, fields in sorted(pending, key=lambda item: (
        *item[0], 0 if item[2]["kind"] == "whole_reel_offset" else 1,
        item[2].get("rule_id", item[2]["kind"]),
    )):
        emit(event_type, **fields)
    return dict(summary, verdict="FAIL" if blocking else corrected.verdict,
                blocking_regressions=blocking)


def compare_positions(before, after, emit):
    blocking = 0
    delta = (after.record_start - before.record_start) * after.edit_rate
    if delta:
        emit_regression("sync_shift", before, after, delta,
                        f"Clip moved by {frame_text(delta)}.", emit)
        blocking += 1
    delta = (after.used_start - before.used_start) * after.edit_rate
    if delta:
        emit_regression("source_slip", before, after, delta,
                        f"Source in-point slipped by {frame_text(delta)}.", emit)
        blocking += 1
    delta = ((after.used_end - after.used_start) -
             (before.used_end - before.used_start)) * after.edit_rate
    if delta:
        emit_regression("length_change", before, after, delta,
                        f"Clip length changed by {frame_text(delta)}.", emit)
        blocking += 1
    if (before.slot_id, before.track_name) != (after.slot_id, after.track_name):
        delta = (after.record_start - before.record_start) * after.edit_rate
        emit_regression("track_change", before, after, delta,
                        "Clip moved to a different track.", emit)
        blocking += 1
    return blocking


def classify_findings(before_findings, after_findings, emit):
    before = {finding["rule_id"]: finding for finding in before_findings}
    after = {finding["rule_id"]: finding for finding in after_findings}
    for rule_id in sorted(before.keys() | after.keys()):
        kind = ("new" if rule_id not in before else
                "persisting" if rule_id in after else "resolved")
        emit("diff_item", kind=kind, rule_id=rule_id, blocking=False,
             flagged_finding=before.get(rule_id), corrected_finding=after.get(rule_id))


def emit_unpaired(kind, role, result, index, emit):
    emit("diff_item", kind=kind, blocking=True, aaf_role=role,
         aaf_path=result.path, **clip_location(result.clips[index]),
         findings=result.clip_findings[index])


def frame_value(value):
    """Preserve subframe precision in JSON without floating-point rounding."""
    return int(value) if value.denominator == 1 else str(value)


def frame_text(value):
    return f"{frame_value(value)} {'frame' if abs(value) == 1 else 'frames'}"


def sequence_position(timing):
    return {"track": None, "record_in_timecode": timing.start_label,
            "record_in_frames": frame_value(timing.start),
            "length_frames": frame_value(timing.length)}


def clip_position(clip):
    return {
        "track": {"slot_id": clip.slot_id, "name": clip.track_name},
        "record_in_timecode": clip.record_in_timecode,
        "record_in_frames": frame_value(clip.record_in_frames),
        "source_in_frames": frame_value(clip.used_start * clip.edit_rate),
        "length_frames": frame_value((clip.used_end - clip.used_start) * clip.edit_rate),
    }


def emit_regression(kind, before, after, delta, message, emit):
    emit("diff_item", kind=kind, blocking=True, **clip_location(after),
         flagged_position=clip_position(before), corrected_position=clip_position(after),
         delta_frames=frame_value(delta), message=message)
