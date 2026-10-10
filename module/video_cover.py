"""Generate one durable JPEG beside a completed video; never replace an existing file."""

import io
import json
import math
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

SUFFIX = ".cover.jpg"
MAX_BYTES = 4 * 1024 * 1024
_workers = threading.BoundedSemaphore(2)


def cover_path(video_path):
    """Keep the video extension so clip.mp4 and clip.mkv cannot share a cover."""
    return str(video_path) + SUFFIX


@dataclass(frozen=True)
class CoverResult:
    path: str = ""
    status: str = "failed"
    error: str = ""


def _run(args, timeout):
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    return subprocess.run(
        args, capture_output=True, check=True, timeout=timeout, **options
    )


def _valid_jpeg(data):
    if not data or len(data) > MAX_BYTES:
        return False
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "JPEG" or image.width * image.height > 16_000_000:
                return False
            image.verify()
        return True
    except (OSError, ValueError):
        return False


def read_cover(path):
    """Read only a small, valid JPEG. Invalid existing files are never overwritten."""
    try:
        if Path(path).is_symlink() or not Path(path).is_file():
            return None
        with open(path, "rb") as source:
            data = source.read(MAX_BYTES + 1)
        return data if _valid_jpeg(data) else None
    except OSError:
        return None


def ensure_video_cover(source_path, video_path):
    """Extract from completed local source, publish atomically at final video path.

    The source may still be in the downloader's temporary directory. FFmpeg runs
    off the event loop (caller uses to_thread); at most two extractions run at once.
    Failures are returned, not allowed to fail the video download.
    """
    target = Path(cover_path(video_path))
    temp_path = None
    try:
        with _workers:
            if target.exists() or target.is_symlink():
                return (
                    CoverResult(str(target), "existing")
                    if read_cover(target)
                    else CoverResult(error="同名封面已存在但不是有效 JPEG，未覆盖")
                )
            source = Path(source_path).resolve(strict=True)
            if not source.is_file() or not source.stat().st_size:
                return CoverResult(error="本地视频为空或不可读取")
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                return CoverResult(error="未找到 FFmpeg")
            seconds = 0.0
            try:
                probe = _run(
                    [
                        shutil.which("ffprobe") or "ffprobe",
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "json",
                        str(source),
                    ],
                    10,
                )
                duration = float(
                    json.loads(probe.stdout).get("format", {}).get("duration") or 0
                )
                if math.isfinite(duration) and duration > 0:
                    seconds = duration * 0.1
            except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                pass
            data = None
            for position in dict.fromkeys((seconds, 0.0)):
                try:
                    frame = _run(
                        [
                            ffmpeg,
                            "-nostdin",
                            "-v",
                            "error",
                            "-ss",
                            str(round(position, 3)),
                            "-i",
                            str(source),
                            "-map",
                            "0:v:0",
                            "-frames:v",
                            "1",
                            "-an",
                            "-vf",
                            "scale=960:960:force_original_aspect_ratio=decrease",
                            "-threads",
                            "1",
                            "-q:v",
                            "3",
                            "-f",
                            "image2pipe",
                            "-c:v",
                            "mjpeg",
                            "pipe:1",
                        ],
                        30,
                    )
                    if _valid_jpeg(frame.stdout):
                        data = frame.stdout
                        break
                except (OSError, subprocess.SubprocessError):
                    continue
            if data is None:
                return CoverResult(error="视频截帧失败或超时")
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=target.parent, prefix=".video-cover-", suffix=".tmp", delete=False
            ) as output:
                temp_path = Path(output.name)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            try:
                # Unlike POSIX rename/replace, link is atomic AND refuses an existing target.
                os.link(temp_path, target)
                return CoverResult(str(target), "created")
            except FileExistsError:
                return (
                    CoverResult(str(target), "existing")
                    if read_cover(target)
                    else CoverResult(error="封面路径已被占用，未覆盖")
                )
    except (OSError, ValueError) as error:
        return CoverResult(error=f"封面保存失败：{type(error).__name__}")
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass  # A leftover temporary image must not fail the video download.
