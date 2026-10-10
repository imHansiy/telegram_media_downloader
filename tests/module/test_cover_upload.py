"""Cover publication is tested against a local WebDAV stand-in, never user storage."""

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import unquote, urlsplit

from aiohttp import web

import media_downloader as downloader
from module.app import Application, DownloadStatus
from module.cloud_drive import CloudDrive, CloudDriveConfig
from module.video_cover import CoverResult, cover_path
from tests.module.test_video_cover import jpeg_bytes


class CoverUploadTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.local = self.root / "频道" / "clip #1.mp4.cover.jpg"
        self.local.parent.mkdir()
        self.data = jpeg_bytes()
        self.local.write_bytes(self.data)
        self.files = {}
        self.calls = []
        self.put_status = None
        self.move_status = None
        self.final_path = "/dav/资源库/频道/clip #1.mp4.cover.jpg"
        service = web.Application()
        service.router.add_route("*", "/{path:.*}", self.handle)
        self.runner = web.AppRunner(service)
        await self.runner.setup()
        self.addAsyncCleanup(self.runner.cleanup)
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.config = CloudDriveConfig(
            enable_upload_file=True,
            upload_adapter="webdav",
            remote_dir="资源库",
            webdav_url=f"http://127.0.0.1:{port}/dav",
        )

    async def handle(self, request):
        key = request.path
        self.calls.append((request.method, key, dict(request.headers)))
        if request.method == "HEAD":
            return web.Response(
                status=200 if key in self.files else 404, body=self.files.get(key, b"")
            )
        if request.method == "PUT":
            if self.put_status:
                self.files[key] = b"not-owned" if self.put_status == 412 else b""
                return web.Response(status=self.put_status)
            self.files[key] = await request.read()
            return web.Response(status=201)
        if request.method == "MOVE":
            destination = unquote(urlsplit(request.headers["Destination"]).path)
            if self.move_status:
                if self.move_status == 412:
                    self.files[destination] = b"concurrently-published"
                return web.Response(status=self.move_status)
            self.files[destination] = self.files.pop(key)
            return web.Response(status=201)
        if request.method == "DELETE":
            self.files.pop(key, None)
            return web.Response(status=204)
        return web.Response(status=405)

    async def publish(self):
        return await CloudDrive.upload_sidecar(
            self.config, str(self.root), str(self.local)
        )

    async def test_uploads_jpeg_alongside_video_using_nonreplacing_atomic_move(self):
        self.assertTrue(await self.publish())
        self.assertEqual({self.final_path: self.data}, self.files)
        self.assertEqual(self.data, self.local.read_bytes())
        put = next(call for call in self.calls if call[0] == "PUT")
        move = next(call for call in self.calls if call[0] == "MOVE")
        self.assertEqual("*", put[2]["If-None-Match"])
        self.assertEqual("image/jpeg", put[2]["Content-Type"])
        self.assertEqual("F", move[2]["Overwrite"])
        self.assertTrue(put[1].startswith(self.final_path + ".upload-"))
        self.assertFalse(
            any(
                call[0] == "DELETE" and call[1] == self.final_path
                for call in self.calls
            )
        )

    async def test_existing_remote_cover_is_preserved_without_put(self):
        self.files[self.final_path] = b"custom existing cover"
        self.assertTrue(await self.publish())
        self.assertEqual(b"custom existing cover", self.files[self.final_path])
        self.assertEqual(["HEAD"], [call[0] for call in self.calls])

    async def test_race_at_final_path_does_not_replace_winner(self):
        self.move_status = 412
        self.assertTrue(await self.publish())
        self.assertEqual({self.final_path: b"concurrently-published"}, self.files)

    async def test_rejected_staging_put_is_not_deleted(self):
        self.put_status = 412
        self.assertFalse(await self.publish())
        self.assertFalse(any(call[0] == "DELETE" for call in self.calls))
        self.assertEqual([b"not-owned"], list(self.files.values()))

    async def test_failed_move_cleans_own_staging_but_keeps_local_cover(self):
        self.move_status = 500
        self.assertFalse(await self.publish())
        self.assertEqual({}, self.files)
        self.assertEqual(self.data, self.local.read_bytes())
        self.move_status = None
        self.assertTrue(await self.publish())

    async def test_invalid_local_file_is_not_uploaded(self):
        self.local.write_bytes(b"invalid JPEG")
        self.assertFalse(await self.publish())
        self.assertEqual([], self.calls)

    async def test_aligo_video_upload_returns_success_before_cover_publication(self):
        # The existing video hook must await the upload callable, not its boolean result.
        self.config.upload_adapter = "aligo"
        runtime = SimpleNamespace(
            cloud_drive_config=self.config, save_path=str(self.root)
        )
        caller_thread = threading.get_ident()
        threads = []

        def upload(*args):
            threads.append(threading.get_ident())
            return True

        with mock.patch.object(CloudDrive, "aligo_upload_file", side_effect=upload):
            self.assertTrue(await Application.upload_file(runtime, "clip.mp4"))
        self.assertNotEqual([caller_thread], threads)

    async def test_other_adapters_preserve_local_jpeg_and_do_not_zip(self):
        for adapter in ("rclone", "aligo"):
            with self.subTest(adapter=adapter):
                self.config.upload_adapter = adapter
                self.config.before_upload_file_zip = True
                self.config.after_upload_file_delete = True
                method = adapter + "_upload_file"
                with mock.patch.object(CloudDrive, method, return_value=True) as upload:
                    self.assertTrue(await self.publish())
                options, root, filename = upload.call_args.args
                self.assertIsNot(options, self.config)
                self.assertFalse(options.before_upload_file_zip)
                self.assertFalse(options.after_upload_file_delete)
                self.assertEqual(str(self.local), filename)
                self.assertTrue(self.config.before_upload_file_zip)
                self.assertTrue(self.config.after_upload_file_delete)
                self.assertTrue(self.local.exists())


class DownloadCoverIntegrationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "download.part"
        self.source.write_bytes(b"completed-video")
        self.initial = self.root / "channel" / "clip.mp4"
        self.final = self.root / "category" / "channel" / "clip.mp4"
        self.events = []
        self.media = SimpleNamespace(
            file_size=self.source.stat().st_size, mime_type="video/mp4"
        )
        self.message = SimpleNamespace(
            id=456, chat=SimpleNamespace(id=123), video=self.media, empty=False
        )
        self.node = SimpleNamespace(chat_id=123, profile_id="fixture", task_id="test")
        self.runtime = SimpleNamespace(
            save_path=str(self.root),
            hide_file_name=False,
            generate_video_covers=True,
            video_classifier_config={"enable": False},
            cloud_drive_config=CloudDriveConfig(upload_adapter="webdav"),
        )
        self.patches = {}
        for name in (
            "add_pending_download",
            "remove_pending_download",
            "verify_and_save_download",
            "clear_upload_auto_retry_count",
            "mark_download_failed",
            "mark_upload_failed",
        ):
            self.patch(downloader, name)
        self.patch(downloader, "get_download_result", return_value={})
        self.patch(
            downloader,
            "_get_media_meta",
            return_value=(str(self.initial), str(self.source), "mp4"),
        )
        self.patch(
            downloader, "download_media_resumable", return_value=str(self.source)
        )
        self.patch(
            downloader, "_apply_resource_category_path", return_value=str(self.final)
        )
        self.patch(downloader, "fetch_message", return_value=self.message)
        self.patch(downloader, "RETRY_TIME_OUT", new=0)
        self.patch(downloader, "_check_timeout", return_value=False)
        self.generate = self.patch(
            downloader, "ensure_video_cover", side_effect=self.generate_cover
        )
        self.video_upload = self.patch(
            CloudDrive, "webdav_upload_stream", side_effect=self.upload_video
        )
        self.cover_upload = self.patch(
            CloudDrive, "upload_sidecar", side_effect=self.upload_cover
        )

    def patch(self, target, name, **kwargs):
        patcher = mock.patch.object(target, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        self.patches[name] = result
        return result

    def generate_cover(self, source, destination):
        self.assertEqual(self.media.file_size, Path(source).stat().st_size)
        self.assertEqual(str(self.final), destination)
        self.events.append("generate")
        target = Path(cover_path(destination))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(jpeg_bytes())
        return CoverResult(str(target), "created")

    async def upload_video(self, config, root, filename, stream, size, **kwargs):
        self.events.append("video")
        self.assertEqual(str(self.final), filename)
        content = b"".join([chunk async for chunk in stream()])
        self.assertEqual(b"completed-video", content)
        return True

    async def upload_cover(self, config, root, filename):
        self.events.append("cover")
        self.assertEqual(cover_path(self.final), filename)
        self.assertEqual(jpeg_bytes(), Path(filename).read_bytes())
        return True

    async def download(self):
        return await downloader.download_media.__wrapped__(
            SimpleNamespace(),
            self.message,
            ["video"],
            {"video": ["all"]},
            self.node,
            self.runtime,
        )

    async def test_complete_then_generate_then_upload_video_and_cover(self):
        status, filename = await self.download()
        self.assertEqual(DownloadStatus.SuccessDownload, status)
        self.assertEqual(str(self.final), filename)
        self.assertEqual(["generate", "video", "cover"], self.events)
        self.assertTrue(self.final.exists())
        self.assertTrue(Path(cover_path(self.final)).exists())
        self.patches["verify_and_save_download"].assert_called_once()

    async def test_cover_generation_failure_cannot_fail_video(self):
        self.generate.side_effect = RuntimeError("synthetic failure")
        self.assertEqual(DownloadStatus.SuccessDownload, (await self.download())[0])
        self.assertEqual(["video"], self.events)
        self.cover_upload.assert_not_awaited()

    async def test_cover_upload_failure_cannot_fail_video(self):
        self.cover_upload.side_effect = None
        self.cover_upload.return_value = False
        self.assertEqual(DownloadStatus.SuccessDownload, (await self.download())[0])
        self.assertTrue(Path(cover_path(self.final)).exists())

    async def test_disabled_cover_setting_skips_generation_and_upload(self):
        self.runtime.generate_video_covers = False
        self.assertEqual(DownloadStatus.SuccessDownload, (await self.download())[0])
        self.generate.assert_not_called()
        self.cover_upload.assert_not_awaited()

    async def test_partial_video_never_generates_or_uploads_cover(self):
        self.source.write_bytes(b"partial")
        status, _ = await self.download()
        self.assertEqual(DownloadStatus.FailedDownload, status)
        self.generate.assert_not_called()
        self.video_upload.assert_not_awaited()
        self.cover_upload.assert_not_awaited()

    async def test_cloud_video_retry_reuses_local_source_and_existing_cover(self):
        def generate_once(source, destination):
            existing = Path(cover_path(destination))
            if existing.exists():
                self.events.append("reuse-cover")
                return CoverResult(str(existing), "existing")
            return self.generate_cover(source, destination)

        attempts = 0

        async def retry_upload(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            await self.upload_video(*args, **kwargs)
            return attempts > 1

        self.generate.side_effect = generate_once
        self.video_upload.side_effect = retry_upload
        self.assertEqual(DownloadStatus.SuccessDownload, (await self.download())[0])
        self.assertEqual(
            ["generate", "video", "reuse-cover", "video", "cover"], self.events
        )
        self.cover_upload.assert_awaited_once()
