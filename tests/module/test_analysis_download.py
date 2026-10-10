import unittest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
import requests
from module.resource_analysis.jobs import AnalysisJobs
from module.resource_analysis.download import download_for_analysis


class Response:
    def __init__(self, chunks, status=200, headers=None):
        self.chunks = chunks
        self.status_code = status
        self.headers = headers or {'Content-Length': '6', 'ETag': '"version-a"'}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


class AnalysisDownloadTest(unittest.TestCase):
    def test_unversioned_or_ignored_range_restarts_without_concatenation(self):
        for first_headers in ({'Content-Length': '6'}, {'Content-Length': '6', 'ETag': 'W/"weak"'},
                              {'Content-Length': '6', 'ETag': '"version-a"'}):
            opener = Mock(side_effect=[Response([b'abc', requests.ConnectionError()], headers=first_headers),
                                       Response([b'uvwxyz'])])
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'media'
                result = download_for_analysis(opener, path, expected_size=6)
                self.assertEqual(b'uvwxyz', path.read_bytes())
            self.assertEqual(6, result['size'])
            if first_headers.get('ETag') != '"version-a"':
                self.assertNotIn('Range', opener.call_args.args[0])

    def test_bad_range_or_changed_version_never_enters_model(self):
        for headers in (
            {'Content-Length': '3', 'Content-Range': 'bytes 2-4/6', 'ETag': '"version-a"'},
            {'Content-Length': '3', 'Content-Range': 'bytes 3-5/6', 'ETag': '"version-b"'},
            {'Content-Length': '3', 'Content-Range': 'bytes 3-5/7', 'ETag': '"version-a"'},
        ):
            opener = Mock(side_effect=[Response([b'abc', requests.ConnectionError()]), Response([b'def'], 206, headers)])
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    download_for_analysis(opener, Path(directory) / 'media', expected_size=6)
            self.assertEqual(2, opener.call_count)

    def test_clean_but_truncated_body_is_not_treated_as_complete(self):
        opener = Mock(side_effect=[Response([b'abc']),
                                  Response([b'def'], 206, {'Content-Length': '3', 'Content-Range': 'bytes 3-5/6', 'ETag': '"version-a"'})])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'media'
            self.assertEqual(6, download_for_analysis(opener, path, expected_size=6)['size'])
            self.assertEqual(b'abcdef', path.read_bytes())

    def test_limits_and_failures_are_bounded_and_redacted(self):
        for response in (Response([b'abcdefg']), Response([], 403),
                         Response([], headers={'Content-Length': '7'})):
            opener = Mock(return_value=response)
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    download_for_analysis(opener, Path(directory) / 'media', expected_size=6, max_size=6)
            self.assertEqual(1, opener.call_count)
        opener = Mock(side_effect=requests.ConnectionError('PRIVATE_URL_AND_TOKEN'))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError) as caught:
                download_for_analysis(opener, Path(directory) / 'media', expected_size=6)
        self.assertEqual(3, opener.call_count)
        self.assertNotIn('PRIVATE', str(caught.exception))

    def test_interrupted_analysis_download_resumes_before_model_is_called(self):
        jobs = AnalysisJobs()
        self.addCleanup(jobs.executor.shutdown)
        jobs.jobs['test'] = {}
        store = Mock()
        async def analyze(path, *args, **kwargs):
            self.assertEqual(b'abcdef', Path(path).read_bytes())
            return {'status': 'complete', 'category': 'movie', 'summary': '合成测试',
                    'summary_quality': {'status': 'ready'}}
        with patch('module.resource_analysis.jobs.requests.get', side_effect=[
            Response([b'abc', requests.ConnectionError('synthetic')]),
            Response([b'def'], 206, {'Content-Length': '3', 'Content-Range': 'bytes 3-5/6', 'ETag': '"version-a"'})
        ]) as get, patch('module.resource_analysis.jobs.analyze_resource', new=AsyncMock(side_effect=analyze)) as model:
            jobs._run('test', {'path': 'test.mp4', 'name': 'test.mp4', 'size': 6},
                      'https://example.invalid/test.mp4', None, {'api_key': 'synthetic'}, store)
        self.assertEqual(2, get.call_count)
        self.assertEqual('bytes=3-', get.call_args.kwargs['headers']['Range'])
        self.assertEqual('"version-a"', get.call_args.kwargs['headers']['If-Range'])
        self.assertEqual(1, model.await_count)
        self.assertEqual('done', jobs.status('test')['status'])
