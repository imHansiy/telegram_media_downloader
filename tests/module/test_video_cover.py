"""Durable covers use synthetic media only; existing user files are immutable."""

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image

import module.video_cover as covers
import media_downloader as downloader
from module.app import Application


def jpeg_bytes(color="blue"):
    output = io.BytesIO()
    Image.new("RGB", (64, 36), color).save(output, "JPEG")
    return output.getvalue()


class VideoCoverTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "download.part"
        self.source.write_bytes(b"synthetic video stand-in")
        self.video = self.root / "分类" / "频道" / "video #1.mp4"
        self.target = Path(covers.cover_path(self.video))

    def generated(self, duration=100):
        def run(args, timeout):
            if "-show_entries" in args:
                return SimpleNamespace(
                    stdout=json.dumps({"format": {"duration": duration}}).encode()
                )
            return SimpleNamespace(stdout=jpeg_bytes())

        return mock.patch.object(covers, "_run", side_effect=run)

    def test_names_keep_full_extension(self):
        self.assertNotEqual(
            covers.cover_path("clip.mp4"), covers.cover_path("clip.mkv")
        )
        self.assertEqual("clip.mp4.cover.jpg", covers.cover_path("clip.mp4"))

    def test_ten_percent_frame_is_published_at_final_path_and_reused(self):
        with self.generated() as run, mock.patch.object(
            covers.shutil, "which", side_effect=lambda name: name
        ):
            result = covers.ensure_video_cover(self.source, self.video)
            self.assertEqual("created", result.status)
            self.assertEqual(str(self.target), result.path)
            args = run.call_args.args[0]
            self.assertEqual("10.0", args[args.index("-ss") + 1])
            self.assertEqual(str(self.source), args[args.index("-i") + 1])
            run.reset_mock()
            self.assertEqual(
                "existing", covers.ensure_video_cover(self.source, self.video).status
            )
            run.assert_not_called()
        self.assertEqual(jpeg_bytes(), self.target.read_bytes())
        self.assertEqual(b"synthetic video stand-in", self.source.read_bytes())
        self.assertEqual([], list(self.target.parent.glob("*.tmp")))

    def test_existing_invalid_cover_is_not_overwritten(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"user-owned contents")
        with mock.patch.object(covers, "_run") as run:
            result = covers.ensure_video_cover(self.source, self.video)
        self.assertEqual("failed", result.status)
        self.assertFalse(result.path)
        run.assert_not_called()
        self.assertEqual(b"user-owned contents", self.target.read_bytes())

    def test_missing_ffmpeg_does_not_publish_placeholder(self):
        with mock.patch.object(covers.shutil, "which", return_value=None):
            result = covers.ensure_video_cover(self.source, self.video)
        self.assertEqual("failed", result.status)
        self.assertFalse(self.target.exists())
        self.assertTrue(self.source.exists())

    def test_failed_seek_falls_back_to_first_frame(self):
        results = [
            SimpleNamespace(stdout=b'{"format":{"duration":100}}'),
            subprocess.TimeoutExpired("ffmpeg", 30),
            SimpleNamespace(stdout=jpeg_bytes()),
        ]
        with mock.patch.object(
            covers, "_run", side_effect=results
        ) as run, mock.patch.object(
            covers.shutil, "which", side_effect=lambda name: name
        ):
            self.assertEqual(
                "created", covers.ensure_video_cover(self.source, self.video).status
            )
        args = run.call_args.args[0]
        self.assertEqual("0.0", args[args.index("-ss") + 1])

    def test_failed_extraction_and_publication_do_not_leave_partial_final_cover(self):
        with mock.patch.object(covers.shutil, "which", side_effect=lambda name: name):
            with mock.patch.object(
                covers, "_run", side_effect=subprocess.TimeoutExpired("ffmpeg", 30)
            ):
                self.assertEqual(
                    "failed", covers.ensure_video_cover(self.source, self.video).status
                )
            self.assertFalse(self.target.exists())
            with self.generated(), mock.patch.object(
                covers.os, "link", side_effect=PermissionError
            ):
                self.assertEqual(
                    "failed", covers.ensure_video_cover(self.source, self.video).status
                )
            self.assertFalse(self.target.exists())
            self.assertEqual([], list(self.target.parent.glob("*.tmp")))

    def test_nonfinite_duration_falls_back_safely(self):
        with self.generated("NaN") as run, mock.patch.object(
            covers.shutil, "which", side_effect=lambda name: name
        ):
            self.assertEqual(
                "created", covers.ensure_video_cover(self.source, self.video).status
            )
        args = run.call_args.args[0]
        self.assertEqual("0.0", args[args.index("-ss") + 1])

    def test_publish_race_preserves_winner(self):
        winner = jpeg_bytes("red")

        def racing_link(source, target):
            Path(target).write_bytes(winner)
            raise FileExistsError

        with self.generated(), mock.patch.object(
            covers.os, "link", side_effect=racing_link
        ), mock.patch.object(covers.shutil, "which", side_effect=lambda name: name):
            self.assertEqual(
                "existing", covers.ensure_video_cover(self.source, self.video).status
            )
        self.assertEqual(winner, self.target.read_bytes())

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required"
    )
    def test_real_synthetic_video(self):
        source = self.root / "synthetic.mp4"
        subprocess.run(
            [
                shutil.which("ffmpeg"),
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=1280x720:rate=10",
                "-t",
                "2",
                "-c:v",
                "mpeg4",
                str(source),
            ],
            capture_output=True,
            check=True,
            timeout=30,
            **(
                {"creationflags": subprocess.CREATE_NO_WINDOW}
                if os.name == "nt"
                else {}
            )
        )
        original = source.read_bytes()
        result = covers.ensure_video_cover(source, self.video)
        self.assertEqual("created", result.status, result.error)
        with Image.open(result.path) as image:
            self.assertEqual("JPEG", image.format)
            self.assertEqual((960, 540), image.size)
        self.assertEqual(original, source.read_bytes())


class CoverHookTest(unittest.IsolatedAsyncioTestCase):
    async def test_setting_defaults_enabled_and_does_not_need_ai(self):
        runtime = SimpleNamespace(video_classifier_config={"enable": False})
        with mock.patch.object(
            downloader,
            "ensure_video_cover",
            return_value=covers.CoverResult("final.mp4.cover.jpg", "created"),
        ) as generate:
            self.assertEqual(
                "final.mp4.cover.jpg",
                await downloader._prepare_video_cover(
                    "temp.part",
                    "final.mp4",
                    SimpleNamespace(mime_type="video/mp4"),
                    runtime,
                    "document",
                ),
            )
        generate.assert_called_once_with("temp.part", "final.mp4")

    async def test_disabled_and_nonvideo_never_generate(self):
        with mock.patch.object(downloader, "ensure_video_cover") as generate:
            for runtime, destination in [
                (SimpleNamespace(generate_video_covers=False), "clip.mp4"),
                (SimpleNamespace(), "document.pdf"),
            ]:
                self.assertEqual(
                    "",
                    await downloader._prepare_video_cover(
                        "tmp", destination, SimpleNamespace(), runtime
                    ),
                )
            generate.assert_not_called()

    async def test_extraction_errors_are_nonfatal(self):
        with mock.patch.object(
            downloader, "ensure_video_cover", side_effect=RuntimeError("fixture")
        ):
            self.assertEqual(
                "",
                await downloader._prepare_video_cover(
                    "tmp", "video.mp4", SimpleNamespace(), SimpleNamespace()
                ),
            )

    def test_configuration_default_disable_and_save(self):
        app = Application("unused-config.yaml", "unused-data.yaml")
        self.addCleanup(app.loop.close)
        self.addCleanup(app.executor.shutdown)
        with mock.patch.dict(os.environ, {}, clear=True):
            app.assign_config({})
            self.assertTrue(app.generate_video_covers)
            app.assign_config({"generate_video_covers": False})
            self.assertFalse(app.generate_video_covers)
            app.update_config(immediate=False)
            self.assertIs(False, app.config["generate_video_covers"])
