"""Handle availability in sequence frames of delivered audio."""


def delivered_handles(clip, duration):
    if duration is None:
        return None
    return {
        "head": max(0, min(clip.used_start, duration)) * clip.edit_rate,
        "tail": max(0, duration - clip.used_end) * clip.edit_rate,
    }


def short_handles_message(available, config):
    if available is None:
        return None
    short = [
        f"{side}: {frames // 1} frames" for side, frames in available.items()
        if frames < config["handle_frames"]
    ]
    if short:
        return config["short_handles"]["message"].format(
            handles=", ".join(short), threshold=config["handle_frames"],
        )
    return None
