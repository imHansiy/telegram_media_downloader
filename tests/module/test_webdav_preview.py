"""Regression tests for authenticated WebDAV media preview."""

import unittest
import aiohttp
from types import SimpleNamespace
from unittest import mock

from module.cloud_drive import CloudDrive, CloudDriveConfig
import module.web as web


class FakeWebDavResponse:
    def __init__(self, status_code=206, headers=None, chunks=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks or [b"test"]
        self.closed = False

    def iter_content(self, chunk_size):
        del chunk_size
        yield from self._chunks

    def close(self):
        self.closed = True


class FakeAsyncWebDavResponse:
    def __init__(self, status=204, body=""):
        self.status = status
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def text(self):
        return self.body


class FakeAsyncWebDavSession:
    def __init__(self, response):
        self.response = response
        self.deleted_url = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def delete(self, url):
        self.deleted_url = url
        return self.response


class FakeUploadSession(FakeAsyncWebDavSession):
    def __init__(self, put_response=None, put_error=None, head_status=404, head_length=None):
        super().__init__(FakeAsyncWebDavResponse(status=204))
        self.put_response = put_response
        self.put_error = put_error
        self.head_status = head_status
        self.head_length = head_length
        self.put_urls = []
        self.move_calls = []

    def request(self, method, url, headers=None):
        # MKCOL / MOVE 共用 request；MOVE 时带 Destination 头。
        if str(method).upper() == "MOVE":
            self.move_calls.append((url, headers or {}))
            return FakeAsyncWebDavResponse(status=201)
        return FakeAsyncWebDavResponse(status=405)

    def head(self, url, allow_redirects=True):
        del url, allow_redirects
        resp = FakeAsyncWebDavResponse(status=self.head_status)
        resp.headers = {}
        if self.head_length is not None:
            resp.headers["Content-Length"] = str(self.head_length)
        return resp

    def put(self, url, data, headers):
        del data, headers
        self.put_urls.append(url)
        if self.put_error:
            raise self.put_error
        return self.put_response


class WebDavPathTestCase(unittest.TestCase):
    def setUp(self):
        self.config = CloudDriveConfig(
            remote_dir="/Telegram Backup",
            webdav_url="https://dav.example.test/root",
        )

    def test_build_url_encodes_each_path_segment(self):
        url = CloudDrive.build_webdav_remote_url(
            self.config,
            "/Telegram Backup/频道/视频 #1.mp4",
        )

        self.assertEqual(
            "https://dav.example.test/root/Telegram%20Backup/%E9%A2%91%E9%81%93/%E8%A7%86%E9%A2%91%20%231.mp4",
            url,
        )

    def test_rejects_paths_outside_remote_root(self):
        with self.assertRaises(ValueError):
            CloudDrive.build_webdav_remote_url(
                self.config,
                "/Telegram Backup/../secret.txt",
            )


class WebDavResetTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_reset_deletes_only_validated_remote_object(self):
        config = CloudDriveConfig(
            remote_dir="/Telegram Backup",
            webdav_url="https://dav.example.test/root",
            webdav_username="user",
            webdav_password="secret",
        )
        session = FakeAsyncWebDavSession(FakeAsyncWebDavResponse(status=204))

        with mock.patch("module.cloud_drive.aiohttp.ClientSession", return_value=session):
            success, outcome = await CloudDrive.webdav_delete_path(
                config,
                "/Telegram Backup/channel/video #1.mp4",
            )

        self.assertTrue(success)
        self.assertEqual("deleted", outcome)
        self.assertEqual(
            "https://dav.example.test/root/Telegram%20Backup/channel/video%20%231.mp4",
            session.deleted_url,
        )

    async def test_reset_treats_missing_object_as_already_clean(self):
        config = CloudDriveConfig(
            remote_dir="/TelegramBackup",
            webdav_url="https://dav.example.test/root",
        )
        session = FakeAsyncWebDavSession(FakeAsyncWebDavResponse(status=404))

        with mock.patch("module.cloud_drive.aiohttp.ClientSession", return_value=session):
            success, outcome = await CloudDrive.webdav_delete_path(
                config,
                "/TelegramBackup/missing.mp4",
            )

        self.assertTrue(success)
        self.assertEqual("not_found", outcome)

    async def test_interrupted_put_is_deleted_before_retry(self):
        config = CloudDriveConfig(
            remote_dir="/TelegramBackup",
            webdav_url="https://dav.example.test/root",
        )
        first_attempt = FakeUploadSession(
            put_error=aiohttp.ServerDisconnectedError(),
        )
        second_attempt = FakeUploadSession(
            put_response=FakeAsyncWebDavResponse(status=201),
        )

        async def stream():
            yield b"test"

        with mock.patch(
            "module.cloud_drive.aiohttp.ClientSession",
            side_effect=[first_attempt, second_attempt],
        ), mock.patch.object(
            CloudDrive,
            "webdav_head_size",
            new=mock.AsyncMock(side_effect=[None, 4]),
        ), mock.patch.object(
            CloudDrive,
            "webdav_move_path",
            new=mock.AsyncMock(return_value=True),
        ), mock.patch.object(
            CloudDrive,
            "webdav_delete_path",
            new=mock.AsyncMock(return_value=(True, "deleted")),
        ) as delete_mock, mock.patch(
            "module.cloud_drive.asyncio.sleep", new=mock.AsyncMock()
        ):
            success = await CloudDrive.webdav_upload_stream(
                config,
                "downloads",
                "downloads/channel/video.mp4",
                stream,
                4,
                max_retries=2,
            )

        self.assertTrue(success)
        # 中断后与成功前都会清理 staging（.uploading），避免半文件污染最终路径。
        deleted_paths = [call.args[1] for call in delete_mock.await_args_list]
        self.assertTrue(deleted_paths)
        self.assertTrue(
            all(path.endswith(".uploading") for path in deleted_paths),
            deleted_paths,
        )

    def test_open_ended_preview_range_is_bounded(self):
        self.assertEqual(
            "bytes=0-4194303",
            web._bounded_webdav_preview_range("bytes=0-"),
        )
        self.assertEqual(
            "bytes=100-200",
            web._bounded_webdav_preview_range("bytes=100-200"),
        )


class WebDavPreviewRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.previous_login_disabled = web._flask_app.config.get("LOGIN_DISABLED")
        web._flask_app.config["LOGIN_DISABLED"] = True
        self.config = CloudDriveConfig(
            remote_dir="/TelegramBackup",
            webdav_url="https://dav.example.test/dav",
            webdav_username="preview-user",
            webdav_password="preview-pass",
        )

    def tearDown(self):
        web._flask_app.config["LOGIN_DISABLED"] = self.previous_login_disabled

    def test_range_request_is_streamed_with_media_headers(self):
        upstream = FakeWebDavResponse(
            headers={
                "Content-Type": "video/mp4",
                "Content-Length": "4",
                "Content-Range": "bytes 0-3/100",
                "Accept-Ranges": "bytes",
                "ETag": '"preview-etag"',
            },
            chunks=[b"te", b"st"],
        )

        with mock.patch.object(
            web, "_app_instance", SimpleNamespace(cloud_drive_config=self.config)
        ), mock.patch.object(web.requests, "request", return_value=upstream) as request_mock:
            response = web._flask_app.test_client().get(
                "/api/webdav/preview",
                query_string={"path": "/TelegramBackup/channel/video.mp4"},
                headers={"Range": "bytes=0-3"},
                buffered=True,
            )

        self.assertEqual(206, response.status_code)
        self.assertEqual(b"test", response.data)
        self.assertEqual("video/mp4", response.headers["Content-Type"])
        self.assertEqual("bytes 0-3/100", response.headers["Content-Range"])
        self.assertEqual("inline", response.headers["Content-Disposition"].split(";", 1)[0])
        self.assertTrue(upstream.closed)
        request_headers = request_mock.call_args.kwargs["headers"]
        self.assertEqual("bytes=0-3", request_headers["Range"])
        self.assertEqual(("preview-user", "preview-pass"), request_mock.call_args.kwargs["auth"])

    def test_unsafe_inline_type_is_forced_to_download(self):
        upstream = FakeWebDavResponse(
            status_code=200,
            headers={"Content-Type": "text/html", "Content-Length": "4"},
        )

        with mock.patch.object(
            web, "_app_instance", SimpleNamespace(cloud_drive_config=self.config)
        ), mock.patch.object(web.requests, "request", return_value=upstream):
            response = web._flask_app.test_client().get(
                "/api/webdav/preview",
                query_string={"path": "/TelegramBackup/channel/page.html"},
                buffered=True,
            )

        self.assertEqual(200, response.status_code)
        self.assertEqual("application/octet-stream", response.headers["Content-Type"])
        self.assertEqual(
            "attachment",
            response.headers["Content-Disposition"].split(";", 1)[0],
        )


if __name__ == "__main__":
    unittest.main()
