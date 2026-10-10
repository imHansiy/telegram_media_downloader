"""Bounded, read-only evidence extraction. Never execute or unpack a resource."""

import base64
import io
import json
import math
import os
import re
import shutil
import subprocess
import tarfile
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image, ImageOps, ImageStat

from .taxonomy import detect_resource_kind
from .subtitles import read_subtitles

# Match the image encoder's 1024-pixel bound without enlarging small sources.
# Ordinary and follow-up video samples must use the same spatial resolution.
VIDEO_FRAME_FILTER = r"scale=min(1024\,iw):-2"


@dataclass
class Evidence:
    kind: str
    text: str = ""
    images: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    limitations: list = field(default_factory=list)
    sampling: dict = field(default_factory=dict)
    audio_clips: list = field(default_factory=list)
    temporal_notes: list = field(default_factory=list)
    activity_notes: list = field(default_factory=list)
    output_schema: dict | None = None
    publisher_context: dict = field(default_factory=dict)

    @property
    def usable(self):
        return bool(self.text.strip() or self.images)


_speech_lock = threading.Lock()
_speech_model = None
MAX_TEXT = 12000
MAX_MEMBER = 2 * 1024 * 1024


def _run(args, timeout=40):
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    return subprocess.run(
        args, capture_output=True, timeout=timeout, check=True, **kwargs
    )


def _probe(path):
    data = _run(
        [
            shutil.which("ffprobe") or "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    return float(json.loads(data.stdout).get("format", {}).get("duration") or 0)


def _image_data(source):
    with Image.open(source) as image:
        if image.width * image.height > 40_000_000:
            raise ValueError("图片像素超过分析上限")
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((1024, 1024))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode(
        "ascii"
    )


def _frame_features(path):
    with Image.open(path) as image:
        small = image.convert("RGB").resize((16, 16))
        gray = image.convert("L").resize((64, 64))
        luminance = ImageStat.Stat(gray)
        # Exclude near-blank lead-ins only when enough readable candidates exist.
        blank = luminance.stddev[0] < 5 and (
            luminance.mean[0] < 8 or luminance.mean[0] > 247
        )
        # A bright watermark can give an otherwise unreadable frame high
        # contrast. Lighting changes should not displace clear content frames.
        dark_fraction = sum(value < 24 for value in gray.getdata()) / (64 * 64)
        center = ImageStat.Stat(gray.crop((16, 16, 48, 48)))
        # Letterboxing or an inset recording can leave most pixels black while
        # the actual scene is still legible. Inspect the center independently;
        # a bright corner watermark alone must not rescue an empty dark frame.
        has_center_content = center.mean[0] >= 32 and center.stddev[0] >= 16
        underexposed = (
            luminance.mean[0] < 24 and dark_fraction > 0.7 and not has_center_content
        )
        return (
            tuple(value for pixel in small.getdata() for value in pixel),
            not (blank or underexposed),
        )


def _choose_frames(candidates, count, duration):
    readable = [item for item in candidates if item["readable"]]
    pool = readable if len(readable) >= count else list(candidates)
    if not pool:
        return []
    span = max(duration, max(item["time"] for item in pool), 1)
    selected = [
        min(pool, key=lambda item: (not item["readable"], abs(item["time"] - span / 2)))
    ]
    pool.remove(selected[0])
    while pool and len(selected) < count:

        def score(item):
            novelty = min(
                sum(abs(a - b) for a, b in zip(item["features"], other["features"]))
                / (len(item["features"]) * 255)
                for other in selected
            )
            spacing = (
                min(abs(item["time"] - other["time"]) for other in selected) / span
            )
            return novelty * 3 + spacing + (0.1 if item["readable"] else 0)

        chosen = max(pool, key=score)
        pool.remove(chosen)
        selected.append(chosen)
    return sorted(selected, key=lambda item: item["time"])


def _timeline_windows(duration, count, seconds):
    if duration <= 0:
        return []
    # At the six-frame UI minimum use three pairs, not two triples: otherwise
    # the entire middle of a long video (and its aligned speech) disappears.
    groups = min(16 if count >= 36 else 8, max(1, count // 2), max(3, count // 3), max(1, math.ceil(duration / 30)))
    # Protect setup AND the closing transition before spreading the remaining
    # budget. Short tail windows can show only an empty scene after people leave.
    if groups >= 4 and seconds >= 120 and duration > max(300, seconds * 2):
        opening = min(60, seconds / 3)
        closing = min(60, seconds / 3)
        remaining_groups = groups - 2
        length = min(60, (seconds - opening - closing) / remaining_groups)
        windows = [{"start": 0.0, "end": opening}]
        for index in range(remaining_groups):
            start = opening + (duration - closing - length - opening) * (index + 1) / (remaining_groups + 1)
            windows.append({"start": round(start, 3), "end": round(start + length, 3)})
        windows.append({"start": round(duration - closing, 3), "end": round(duration, 3)})
        return windows
    length = min(max(1, seconds) / groups, duration / groups, 60)
    return [{"start": round((duration - length) * index / max(1, groups - 1), 3),
             "end": round((duration - length) * index / max(1, groups - 1) + length, 3)}
            for index in range(groups)]


def _scene_scan(path):
    """Optional low-resolution local scan; a timeout never blocks fixed windows."""
    try:
        completed = _run([
            shutil.which("ffmpeg") or "ffmpeg", "-nostdin", "-v", "info", "-i", str(path),
            "-an", "-vf", "fps=1/2,scale=160:-2,select=gt(scene\\,0.3),showinfo",
            "-frames:v", "128", "-threads", "1", "-f", "null", "-"], timeout=45)
        output = completed.stderr.decode("utf-8", errors="replace")
        times = sorted({float(value) for value in re.findall(r"pts_time:([0-9.]+)", output)})
        return times, {"status": "candidate_limit" if len(times) >= 128 else "complete", "candidate_count": len(times)}
    except (OSError, ValueError, subprocess.SubprocessError):
        return [], {"status": "unavailable", "candidate_count": 0}


def _scene_aligned_windows(windows, times):
    """Refine middle windows only; retain temporal spread and opening/closing."""
    adjusted = [dict(window) for window in windows]
    for index in range(1, len(windows) - 1):
        window = windows[index]
        length = window["end"] - window["start"]
        low, high = windows[index - 1]["end"], windows[index + 1]["start"] - length
        # Each window owns a disjoint time cell even if adjacent windows move.
        low = max(low, (windows[index - 1]["end"] + window["start"]) / 2)
        high = min(high, (window["end"] + windows[index + 1]["start"]) / 2 - length)
        candidates = [value for value in times if low + length / 2 <= value <= high + length / 2]
        if candidates:
            point = min(candidates, key=lambda value: abs(value - (window["start"] + window["end"]) / 2))
            adjusted[index] = {"start": round(point - length / 2, 3), "end": round(point + length / 2, 3)}
    return adjusted


def _frames(path, count, temp_dir, evidence, *, timeline=False, audio_seconds=180, adaptive=False):
    budget = 120 if timeline else 60
    deadline = time.monotonic() + budget
    try:
        duration = _probe(path)
        if not math.isfinite(duration) or duration < 0:
            duration = 0
    except (OSError, ValueError, subprocess.SubprocessError):
        duration = 0
    count = max(1, min(48 if timeline else 16, count))
    windows = _timeline_windows(duration, count, audio_seconds) if timeline else []
    scene_scan = None
    if adaptive and windows:
        scenes, scene_scan = _scene_scan(path)
        windows = _scene_aligned_windows(windows, scenes)
    candidate_count = min(48, max(12, count * 3))
    positions = (
        [
            duration * (0.01 + 0.98 * index / (candidate_count - 1))
            for index in range(candidate_count)
        ]
        if duration > 0
        else [0, 2, 5, 10, 20, 30, 60, 90][: max(3, count)]
    )
    if windows:
        if adaptive:
            # Short videos have fewer audio windows, not a smaller visual
            # budget. Distribute the remaining frames inside those same windows.
            frame_budget = min(count, max(len(windows), math.ceil(duration * 2)))
            base, extra = divmod(frame_budget, len(windows))
            positions = []
            for index, window in enumerate(windows):
                amount = base + (index < extra)
                fractions = [0.5] if amount == 1 else [0.02 + 0.96 * i / (amount - 1) for i in range(amount)]
                positions.extend(window["start"] + (window["end"] - window["start"]) * fraction for fraction in fractions)
            # Decode the opening and ending first, then fill the largest gaps.
            # A decode deadline must not silently leave only the opening half.
            pool = sorted(set(positions))
            positions = [pool.pop(0)] if pool else []
            if pool:
                positions.append(pool.pop())
            while pool:
                point = max(pool, key=lambda value: min(abs(value - prior) for prior in positions))
                positions.append(point)
                pool.remove(point)
        else:
            per_window = max(1, min(3, count // len(windows)))
            positions = [window["start"] + (window["end"] - window["start"]) * fraction
                         for window in windows
                         for fraction in ([0.02, 0.5, 0.98] if per_window == 3 else [0.02, 0.98] if per_window == 2 else [0.5])]
    candidates = []
    for index, seconds in enumerate(positions):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        target = os.path.join(temp_dir, f"candidate-{index}.jpg")
        try:
            _run(
                [
                    shutil.which("ffmpeg") or "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-y",
                    "-ss",
                    str(round(seconds, 3)),
                    "-i",
                    str(path),
                    "-frames:v",
                    "1",
                    "-vf",
                    VIDEO_FRAME_FILTER,
                    "-threads",
                    "1",
                    target,
                ],
                timeout=min(10, remaining),
            )
            features, readable = _frame_features(target)
            candidates.append(
                {
                    "path": target,
                    "time": round(seconds, 3),
                    "features": features,
                    "readable": readable,
                }
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
    # Narrative sampling retains each time window, including setup and outcome.
    # A dark start must not silently be replaced with another bright middle frame.
    selected = sorted(candidates, key=lambda item: item["time"])[:count] if windows else _choose_frames(candidates, count, duration)
    readable_count = sum(item["readable"] for item in candidates)
    quality_filtered_count = 0 if windows else (
        len(candidates) - readable_count if readable_count >= count else 0
    )
    timestamps = []
    for item in selected:
        try:
            evidence.images.append(_image_data(item["path"]))
            timestamps.append(item["time"])
        except (OSError, ValueError):
            continue
    evidence.sampling = {
        "method": "timeline_windows" if windows else "visual_diversity",
        "duration_seconds": round(duration, 3),
        "candidate_count": len(candidates),
        "requested_frame_count": count,
        "planned_frame_count": len(positions),
        "readable_candidate_count": readable_count,
        "quality_filtered_count": quality_filtered_count,
        "timestamps": timestamps,
    }
    if windows:
        evidence.sampling["timeline_windows"] = [
            {**window, "frame_count": sum(window["start"] <= timestamp <= window["end"] for timestamp in timestamps)}
            for window in windows
        ]
    if scene_scan is not None:
        evidence.sampling["scene_scan"] = scene_scan
        evidence.limitations.append("镜头变化只用于选择采样位置，不代表已理解全片；扫描失败或达到上限时保留时间窗采样。")
    if evidence.images:
        evidence.sources.append("video_frames")
    evidence.limitations.append(
        f"按{len(windows)}个时间窗提取{len(evidence.images)}帧，含局部前后画面；窗间仍有未采样内容，不能声称看过全片。"
        if windows else f"从 {len(candidates)} 个候选画面按视觉差异和时间覆盖选取 {len(evidence.images)} 帧；非连续采样，不能声称看过全片。"
    )
    if quality_filtered_count:
        evidence.limitations.append(
            f"清晰候选足够，未使用 {quality_filtered_count} 张严重偏暗或近空白画面；未据此推断暗处内容。"
        )
    elif candidates and readable_count < count:
        evidence.limitations.append("清晰候选不足，保留有限采样；部分暗处或空白画面的细节可能无法确认。")
    if time.monotonic() >= deadline:
        evidence.limitations.append(f"候选帧提取达到{budget}秒上限，仅使用已取得的画面。")


def _detect_language(path, temp_dir, duration):
    """One language decision per file from three spread 30-second probes."""
    global _speech_model
    votes = {}
    starts = [0.0] if duration < 90 else [0.0, max(0.0, duration / 2 - 15), max(0.0, duration - 30)]
    for start in starts:
        target = os.path.join(temp_dir, f"lang-{round(start * 1000)}.wav")
        _run([shutil.which("ffmpeg") or "ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", str(start), "-i", str(path),
              "-t", "30", "-vn", "-ac", "1", "-ar", "16000", target], timeout=90)
        with _speech_lock:
            if _speech_model is None:
                from faster_whisper import WhisperModel

                _speech_model = WhisperModel("small", device="cpu", compute_type="int8")
            language, probability, _ = _speech_model.detect_language(_decode_audio(target))
        if isinstance(language, str) and math.isfinite(float(probability)) and float(probability) >= 0.5:
            votes[language] = votes.get(language, 0.0) + float(probability)
    if not votes:
        return None
    language = max(votes, key=votes.get)
    return language if votes[language] >= 0.8 else None


def _decode_audio(target):
    from faster_whisper.audio import decode_audio

    return decode_audio(target, sampling_rate=16000)


def _speech(path, temp_dir, seconds, *, start=0, language=None):
    global _speech_model
    target = os.path.join(temp_dir, f"speech-{round(start * 1000)}.wav")
    _run(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-ss",
            str(start),
            "-i",
            str(path),
            "-t",
            str(seconds),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            target,
        ],
        timeout=90,
    )
    with _speech_lock:
        if _speech_model is None:
            from faster_whisper import WhisperModel

            _speech_model = WhisperModel("small", device="cpu", compute_type="int8")
        # Beam 5 and a per-window language pin: on real material beam 1 and
        # per-window language detection lost most conversational Chinese.
        segments, info = _speech_model.transcribe(
            target, vad_filter=True, beam_size=5, condition_on_previous_text=False,
            language=language, compression_ratio_threshold=2.4, log_prob_threshold=-1.0,
            no_speech_threshold=0.6,
        )
        probability = float(info.language_probability)
        accepted, discarded, seen, strong_short = [], 0, set(), False
        speech_seconds, logprobs = 0.0, []
        for segment in segments:
            text = " ".join(segment.text.split())
            scores = (segment.no_speech_prob, segment.avg_logprob, segment.compression_ratio)
            if (
                not text or text in seen
                or not all(math.isfinite(value) for value in scores)
                or scores[0] > 0.6 or scores[1] < -1.0 or scores[2] > 2.4
            ):
                discarded += 1
                continue
            seen.add(text)
            accepted.append(text)
            speech_seconds += max(0.0, float(segment.end) - float(segment.start))
            logprobs.append(scores[1])
            characters = re.findall(r"[\u4e00-\u9fff]", text)
            strong_short |= (len(characters) >= 4 and len(set(characters)) >= 4
                             and scores[0] <= 0.2 and scores[1] >= -0.4 and scores[2] <= 2.0)
        text = " ".join(accepted)
    reliable = (math.isfinite(probability) and probability >= 0.8
                and ((len(text) >= 12 and len(set(text)) >= 6) or strong_short))
    return {
        "text": text[:MAX_TEXT] if reliable else "",
        "language": info.language if reliable else "",
        "language_probability": round(probability, 3) if math.isfinite(probability) else 0,
        "discarded_segments": discarded,
        "accepted_characters": len(text),
        "accepted_segments": len(accepted),
        "distinct_characters": len(set(text)),
        "speech_seconds": round(speech_seconds, 1),
        "mean_logprob": round(sum(logprobs) / len(logprobs), 3) if logprobs else None,
        "status": "accepted" if reliable else "unreliable",
    }


def _speech_samples(path, temp_dir, seconds, duration, *, timeline_windows=None):
    budget = min(seconds, duration) if duration > 0 else seconds
    try:
        # Any failure here only means windows are transcribed without a pin.
        language = _detect_language(path, temp_dir, duration) if duration > 0 else None
    except Exception:  # noqa: BLE001
        language = None
    count = 3 if duration > budget and budget >= 30 else 1
    length = budget / count
    starts = [0, (duration - length) / 2, duration - length] if count == 3 else [0]
    if timeline_windows:
        starts = [window["start"] for window in timeline_windows]
        lengths = [window["end"] - window["start"] for window in timeline_windows]
    else:
        lengths = [length] * len(starts)
    clips, windows, remaining_text = [], [], MAX_TEXT
    remaining_seconds = budget
    for start, requested_length in zip(starts, lengths):
        length = min(requested_length, remaining_seconds)
        if length <= 0:
            break
        start, end = round(start, 3), round(start + length, 3)
        remaining_seconds -= end - start
        try:
            sample = _speech(path, temp_dir, length, start=start, language=language)
        except (ImportError, OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            sample = {"text": "", "status": "unavailable", "discarded_segments": 0}
        text = sample.pop("text")[:remaining_text]
        metadata = {"start": start, "end": end, **sample}
        windows.append(metadata)
        if text:
            clips.append({**metadata, "text": text})
            remaining_text -= len(text)
    return clips, {
        "method": "timeline_aligned" if timeline_windows else "distributed" if count == 3 else "bounded_start",
        "language": language or "",
        "requested_seconds": seconds,
        "attempted_seconds": round(sum(item["end"] - item["start"] for item in windows), 3),
        "sampled_seconds": round(sum(item["end"] - item["start"] for item in windows if item["status"] != "unavailable"), 3),
        "reliable_clips": len(clips),
        "windows": windows,
    }


def _decode(raw):
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            text = raw.decode(encoding)
            if "\x00" not in text:
                return text
        except UnicodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def _office_text(path, suffix):
    prefixes = {
        ".docx": ("word/document.xml",),
        ".pptx": ("ppt/slides/slide",),
        ".xlsx": ("xl/sharedStrings.xml", "xl/worksheets/sheet"),
        ".epub": ("",),
        ".odt": ("content.xml",),
        ".ods": ("content.xml",),
        ".odp": ("content.xml",),
    }
    chunks, total = [], 0
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist()[:200]:
            if not entry.filename.startswith(prefixes[suffix]):
                continue
            if suffix == ".epub" and not entry.filename.endswith((".xhtml", ".html")):
                continue
            if entry.file_size > MAX_MEMBER or entry.flag_bits & 1:
                continue
            total += entry.file_size
            if total > 8 * 1024 * 1024:
                break
            raw = archive.read(entry)
            if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                continue
            try:
                tree = ElementTree.fromstring(raw)
            except ElementTree.ParseError:
                continue
            chunks.append(
                " ".join(text.strip() for text in tree.itertext() if text.strip())
            )
            if sum(map(len, chunks)) >= MAX_TEXT:
                break
    return "\n".join(chunks)[:MAX_TEXT]


def _document(path, name, evidence):
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(path)
        if reader.is_encrypted:
            evidence.limitations.append("加密 PDF 未读取正文。")
            return
        evidence.text = "\n".join(
            (page.extract_text() or "")[:4000] for page in reader.pages[:6]
        )[:MAX_TEXT]
        evidence.limitations.append("仅提取 PDF 前六页文字；扫描图片页未做 OCR。")
    elif suffix in (".docx", ".pptx", ".xlsx", ".epub", ".odt", ".ods", ".odp"):
        evidence.text = _office_text(path, suffix)
        evidence.limitations.append("仅分析部分文档文字，不包含完整排版和插图。")
    elif suffix in (
        ".txt",
        ".md",
        ".rst",
        ".csv",
        ".json",
        ".yaml",
        ".yml",
        ".xml",
        ".srt",
        ".vtt",
        ".ass",
    ):
        with open(path, "rb") as stream:
            evidence.text = _decode(stream.read(64000))[:MAX_TEXT]
        evidence.limitations.append("仅分析文档开头的有限文字。")
    else:
        evidence.limitations.append("此文档格式暂不支持提取正文。")
    if evidence.text.strip():
        evidence.sources.append("document_text")


def _archive(path, evidence):
    entries = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            entries = [
                f"{entry.filename[:240]} ({entry.file_size} bytes)"
                for entry in archive.infolist()[:150]
            ]
    elif tarfile.is_tarfile(path):
        with tarfile.open(path) as archive:
            for index, entry in enumerate(archive):
                if index >= 150:
                    break
                entries.append(f"{entry.name[:240]} ({entry.size} bytes)")
    else:
        evidence.limitations.append("此压缩格式暂不支持读取文件清单。")
    evidence.text = "\n".join(entries)[:MAX_TEXT]
    if entries:
        evidence.sources.append("archive_listing")
    evidence.limitations.append("只读取最多150项文件清单，未解压、执行或检查其中的文件正文。")


def extract_evidence(
    path,
    temp_dir,
    *,
    name="",
    mime_type="",
    media_type="",
    max_frames=6,
    transcribe=False,
    max_audio_seconds=180,
    timeline=False,
    use_subtitles=False,
    adaptive_sampling=False,
):
    name = name or Path(path).name
    kind = detect_resource_kind(name, mime_type, media_type)
    evidence = Evidence(kind)
    try:
        if kind == "video":
            if timeline:
                options = {"adaptive": True} if adaptive_sampling else {}
                _frames(path, max_frames, temp_dir, evidence, timeline=True, audio_seconds=max(10, min(600, max_audio_seconds)), **options)
            else:
                _frames(path, max_frames, temp_dir, evidence)
        elif kind == "image":
            evidence.images = [_image_data(path)]
            evidence.sources.append("image")
            evidence.limitations.append("图片只反映当前画面，不能据此推断完整事件。")
        elif kind == "document":
            _document(path, name, evidence)
        elif kind == "archive":
            _archive(path, evidence)
        elif kind == "software":
            evidence.limitations.append("安装包仅识别文件格式，未执行程序或验证用途与安全性。")
        if kind in ("video", "audio"):
            if use_subtitles and kind == "video":
                try:
                    clips, total, origin = read_subtitles(path, temp_dir, evidence.sampling.get("duration_seconds", 0),
                                                         _run, shutil.which("ffmpeg") or "ffmpeg")
                    if clips:
                        evidence.audio_clips = clips
                        evidence.text = "\n\n".join(
                            f"字幕片段 speech_{index + 1}：{clip['start']:g}–{clip['end']:g} 秒\n{clip['text']}"
                            for index, clip in enumerate(clips))
                        evidence.sources.append("transcript")
                        evidence.sampling["subtitles"] = {"origin": origin, "available_cues": total, "selected_cues": len(clips)}
                        evidence.limitations.append("字幕按原时间戳读取，未经逐句听音验证；可能有翻译、错配或偏移，不证明对白及身份真实。")
                except (OSError, ValueError, subprocess.SubprocessError):
                    evidence.limitations.append("本地字幕不可用；如启用转写，将继续尝试语音识别。")
            if transcribe and not evidence.audio_clips:
                try:
                    seconds = max(10, min(600, max_audio_seconds))
                    duration = evidence.sampling.get("duration_seconds", 0)
                    if not duration:
                        try:
                            duration = _probe(path)
                            if not math.isfinite(duration) or duration < 0:
                                duration = 0
                        except (OSError, ValueError, subprocess.SubprocessError):
                            duration = 0
                    clips, audio = _speech_samples(path, temp_dir, seconds, duration,
                                                   timeline_windows=evidence.sampling.get("timeline_windows"))
                    evidence.audio_clips = clips
                    evidence.sampling["audio"] = audio
                    if clips:
                        evidence.text = "\n\n".join(
                            f"语音片段 speech_{index + 1}：{clip['start']:g}–{clip['end']:g} 秒，"
                            f"该段识别语言 {clip['language']}\n{clip['text']}"
                            for index, clip in enumerate(clips)
                        )
                        evidence.sources.append("transcript")
                        evidence.limitations.append(
                            f"语音在总计不超过 {seconds} 秒预算内分段采样，获得 {len(clips)} 段可靠转写；"
                            "只支持对应片段的对白线索，不代表完整音轨或音乐分析。"
                        )
                    else:
                        evidence.limitations.append("已尝试有界语音采样，但未获得可靠转写；不据此判断对白或语言。")
                except (
                    ImportError,
                    OSError,
                    ValueError,
                    RuntimeError,
                    subprocess.SubprocessError,
                ):
                    evidence.limitations.append("语音转写不可用，未获得可靠音频内容。")
            elif not evidence.audio_clips:
                evidence.limitations.append("未启用语音转写；未分析声音，不能推测对白、曲风或声线。")
    except Exception as error:
        # Invalid user files must not stop the downloader; don't log content or credentials.
        evidence.limitations.append(f"内容提取未完成（{type(error).__name__}）。")
    if not evidence.usable:
        evidence.limitations.append("只有文件格式与辅助文案，内容分类需要确认。")
    return evidence
