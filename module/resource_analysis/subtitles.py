"""Bounded local subtitle reading. No network, OCR, or executable resources."""

import math
import re
from pathlib import Path


MAX_BYTES = 2 * 1024 * 1024
_TIME = re.compile(r"(?:(\d{1,3}):)?(\d{2}):(\d{2})[,.](\d{3})")


def _seconds(value):
    match = _TIME.fullmatch(value.strip())
    if not match:
        return None
    hours, minutes, seconds, fraction = match.groups()
    if int(minutes) >= 60 or int(seconds) >= 60:
        return None
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(fraction) / 1000


def parse_subtitles(text, duration=0, *, max_characters=12000):
    """Keep original cue timing; reject malformed, duplicated, and out-of-range cues."""
    clips, seen, characters = [], set(), 0
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        lines = block.strip().splitlines()
        index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if index is None:
            continue
        timing = lines[index].split("-->", 1)
        start = _seconds(timing[0])
        end = _seconds(timing[1].strip().split()[0]) if timing[1].strip() else None
        if start is None or end is None or not 0 <= start < end:
            continue
        if math.isfinite(duration) and duration > 0 and end > duration + 1:
            continue
        value = re.sub(r"<[^>]*>|\{[^}]*\}", "", " ".join(lines[index + 1:])).strip()
        if not value or (start, end, value) in seen:
            continue
        seen.add((start, end, value))
        clips.append({"start": start, "end": end, "text": value, "language": "unknown", "origin": "subtitle"})
    clips.sort(key=lambda clip: (clip["start"], clip["end"]))
    # Spread the text budget across the timeline, not just the opening dialogue.
    selected = []
    if clips:
        stride = max(1, math.ceil(sum(len(clip["text"]) for clip in clips) / max_characters))
        for clip in clips[::stride]:
            if characters + len(clip["text"]) > max_characters:
                continue
            selected.append(clip)
            characters += len(clip["text"])
    return selected, len(clips)


def read_subtitles(path, temp_dir, duration, run, ffmpeg):
    path = Path(path)
    # Exact basename only. Never attach a neighboring video's captions.
    candidates = [path.with_suffix(suffix) for suffix in (".srt", ".vtt")]
    for candidate in candidates:
        if candidate.is_file() and candidate.resolve().parent == path.resolve().parent and candidate.stat().st_size <= MAX_BYTES:
            raw = candidate.read_bytes()
            text = raw.decode("utf-8-sig", errors="replace")
            if "\ufffd" in text:
                continue
            clips, total = parse_subtitles(text, duration)
            if clips:
                return clips, total, "sidecar"
    target = Path(temp_dir) / "embedded-subtitles.srt"
    try:
        run([ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(path), "-map", "0:s:0",
             "-c:s", "srt", "-fs", str(MAX_BYTES), str(target)], timeout=30)
        if target.is_file() and target.stat().st_size <= MAX_BYTES:
            clips, total = parse_subtitles(target.read_text(encoding="utf-8-sig"), duration)
            return clips, total, "embedded"
    except (OSError, ValueError, RuntimeError):
        pass
    return [], 0, "missing"
