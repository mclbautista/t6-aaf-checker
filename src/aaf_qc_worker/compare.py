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
    for keys in (mob_keys, locator_keys, fallback_keys):
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
                if queue:
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
    classify_findings(
        [finding for finding in flagged.findings if finding["track"] is None],
        [finding for finding in corrected.findings if finding["track"] is None],
        emit,
    )
    before_by_after = {right: left for left, right in pairs}
    blocking = 0
    # Walk corrected positions for all non-dropped diff items, regardless of kind.
    for right in range(len(corrected.clips)):
        if right in before_by_after:
            left = before_by_after[right]
            classify_findings(flagged.clip_findings[left], corrected.clip_findings[right], emit)
            # Paired-clip timeline/content changes (#16/#17) can be emitted here.
        else:
            emit_unpaired("added", "corrected", corrected, right, emit)
            blocking += 1
    paired_before = {left for left, _ in pairs}
    for left in range(len(flagged.clips)):
        if left not in paired_before:
            emit_unpaired("dropped", "flagged", flagged, left, emit)
            blocking += 1
    return dict(summary, verdict="FAIL" if blocking else corrected.verdict,
                blocking_regressions=blocking)


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
