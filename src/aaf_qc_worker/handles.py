"""Handle availability in sequence frames of delivered audio."""


def short_handles_message(clip, duration, config):
    if duration is None:
        return None
    available = {
        "head": max(0, min(clip.used_start, duration)) * clip.edit_rate,
        "tail": max(0, duration - clip.used_end) * clip.edit_rate,
    }
    short = [
        f"{side}: {frames // 1} frames" for side, frames in available.items()
        if frames < config["handle_frames"]
    ]
    if short:
        return config["short_handles"]["message"].format(
            handles=", ".join(short), threshold=config["handle_frames"],
        )
    return None
