"""Bounded downloads for analysis; resume only against a strong entity tag."""
import re
import time
import requests


def download_for_analysis(open_response, path, *, expected_size=0, max_size=2 * 1024**3,
                          progress=None, time_budget=None):
    size, total, etag = 0, int(expected_size or 0), None
    if total < 0 or total > max_size:
        raise ValueError('文件超过分析大小上限')
    started = time.monotonic()
    budget = time_budget or max(300, min(1800, total / (128 * 1024)))
    metadata = {}
    for attempt in range(3):
        headers = {'Accept-Encoding': 'identity'}
        offset = size if etag else 0
        if offset:
            headers.update(Range=f'bytes={offset}-', **{'If-Range': etag})
        try:
            with open_response(headers) as response:
                if response.status_code not in (200, 206):
                    raise ValueError(f'远程文件读取失败（HTTP {response.status_code}）')
                if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                    raise ValueError('远程文件使用了非原始字节编码，不能验证完整性')
                response_tag = response.headers.get('ETag')
                length = int(response.headers.get('Content-Length') or 0)
                if response.status_code == 206:
                    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                    if not offset or not match or response_tag != etag:
                        raise ValueError('远程文件续传标识不一致，未进入分析')
                    begin, end, response_total = map(int, match.groups())
                    if begin != offset or end < begin or end != response_total - 1 or (length and length != end - begin + 1):
                        raise ValueError('远程文件续传范围不一致，未进入分析')
                else:
                    # Ignored Range / changed If-Range: never append a 200 body.
                    size = 0
                    response_total = length or total
                    etag = response_tag if response_tag and response_tag.startswith('"') and response_tag.endswith('"') else None
                if response_total > max_size or (total and response_total != total):
                    raise ValueError('远程文件大小变化或超过上限，未进入分析')
                total = response_total
                if time_budget is None:
                    budget = max(300, min(1800, total / (128 * 1024)))
                metadata = {'content_type': response.headers.get('Content-Type', ''),
                            'modified': response.headers.get('Last-Modified', '')}
                with open(path, 'ab' if response.status_code == 206 else 'wb') as output:
                    for chunk in response.iter_content(256 * 1024):
                        if time.monotonic() - started > budget:
                            raise ValueError('远程资源读取超时，原简介已保留')
                        if not chunk:
                            continue
                        if size + len(chunk) > max_size or (total and size + len(chunk) > total):
                            raise ValueError('远程文件数据超过声明大小，未进入分析')
                        output.write(chunk)
                        size += len(chunk)
                        if progress:
                            progress(size, total, attempt)
                if total and size != total:
                    raise requests.ConnectionError('incomplete_body')
                return {'size': size, **metadata}
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError):
            if attempt == 2 or time.monotonic() - started >= budget:
                raise ValueError('远程资源读取中断，恢复后仍未完成；原简介已保留') from None
            if progress:
                progress(size, total, attempt + 1)
    raise ValueError('远程资源读取未完成')
