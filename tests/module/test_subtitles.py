import tempfile
import unittest
from pathlib import Path
from unittest import mock

from module.resource_analysis.subtitles import parse_subtitles, read_subtitles
from module.resource_analysis.evidence import extract_evidence


SRT = "1\n00:00:01,000 --> 00:00:04,000\n请帮忙查一下车票。\n\n2\n00:00:10,000 --> 00:00:13,000\n已经找到记录。"


class SubtitleTest(unittest.TestCase):
    def test_timing_and_invalid_cues(self):
        clips, count = parse_subtitles(SRT + "\n\n3\n00:00:14,000 --> 00:00:12,000\n无效。", 20)
        self.assertEqual(2, count)
        self.assertEqual([(1, 4), (10, 13)], [(row["start"], row["end"]) for row in clips])
        self.assertEqual(1, parse_subtitles(SRT, 5)[1])

    def test_exact_sidecar_and_default_off(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.mp4"
            path.touch()
            path.with_suffix(".srt").write_text(SRT, encoding="utf-8")
            runner = mock.Mock()
            self.assertEqual("sidecar", read_subtitles(path, directory, 20, runner, "ffmpeg")[2])
            runner.assert_not_called()
            with mock.patch("module.resource_analysis.evidence._frames"), mock.patch("module.resource_analysis.evidence._speech_samples") as speech:
                off = extract_evidence(path, directory)
                self.assertFalse(off.text)
                on = extract_evidence(path, directory, use_subtitles=True, transcribe=True)
                self.assertIn("请帮忙", on.text)
                self.assertEqual("subtitle", on.audio_clips[0]["origin"])
                speech.assert_not_called()

    def test_different_basename_is_not_read(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "other.srt").write_text(SRT, encoding="utf-8")
            runner = mock.Mock()
            clips, _, _ = read_subtitles(Path(directory) / "fixture.mp4", directory, 20, runner, "ffmpeg")
            self.assertFalse(clips)
            runner.assert_called_once()
