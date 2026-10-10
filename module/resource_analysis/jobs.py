"""Small bounded queue for user-requested WebDAV reanalysis; files never move."""

import asyncio
import copy
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

import requests

from .library import clean_path
from .download import download_for_analysis
from .service import analysis_status, analyze_resource, can_refresh_video_summary
from .taxonomy import detect_resource_kind, RESOURCE_KINDS


class AnalysisJobs:
    def __init__(self):
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="resource-analysis"
        )
        self.jobs = {}

    def status(self, key):
        with self.lock:
            return dict(self.jobs.get(key, {}))

    def submit(
        self, key, entry, url, auth, config, store, *, on_result=None, on_progress=None, mode="full"
    ):
        path = clean_path(entry["path"])
        config = copy.deepcopy(config)
        if mode not in ("full", "summary", "deep"):
            raise ValueError("无效的分析模式")
        if mode == "summary" and not can_refresh_video_summary(entry.get("analysis")):
            raise ValueError("只有已确认分类的视频可以单独更新简介")
        if not config.get("enable") or not (
            config.get("api_key") or os.getenv("OPENAI_API_KEY")
        ):
            raise ValueError("请先启用资源分析并配置 API Key")
        kind = detect_resource_kind(entry["name"], entry.get("content_type", ""))
        if mode == "deep" and kind != "video":
            raise ValueError("深度采样仅适用于视频")
        if kind not in config.get("resource_types", list(RESOURCE_KINDS)):
            raise ValueError("此资源类型未启用分析，请先在设置中勾选")
        with self.lock:
            if self.jobs.get(key, {}).get("status") in ("queued", "running"):
                return dict(self.jobs[key])
            if (
                sum(
                    job["status"] in ("queued", "running") for job in self.jobs.values()
                )
                >= 2
            ):
                raise ValueError("已有两个资源正在分析，请稍后再试")
            if len(self.jobs) >= 256:
                self.jobs = {
                    item: job
                    for item, job in self.jobs.items()
                    if job["status"] in ("queued", "running")
                }
            self.jobs[key] = {"path": path, "status": "queued", "message": "等待分析"}
        self.executor.submit(
            self._run,
            key,
            copy.deepcopy(entry),
            url,
            auth,
            config,
            store,
            on_result=on_result,
            on_progress=on_progress,
            mode=mode,
        )
        return self.status(key)

    def _update(self, key, **values):
        with self.lock:
            self.jobs[key].update(values)

    def _run(
        self, key, entry, url, auth, config, store, *, on_result=None, on_progress=None, mode="full"
    ):
        def progress(message):
            self._update(key, message=message)
            if on_progress:
                on_progress(message)

        try:
            limit = (
                max(
                    1,
                    float(
                        config.get(
                            "max_file_size_mb", config.get("max_video_size_mb", 2048)
                        )
                    ),
                )
                * 1024
                * 1024
            )
            self._update(key, status="running", message="读取远程资源")
            progress("读取远程资源")
            with tempfile.TemporaryDirectory(prefix="tdl-reanalysis-") as temp_dir:
                local_path = os.path.join(temp_dir, "resource")
                # Support signed storage redirects; Requests strips Authorization
                # when crossing origins through Session.rebuild_auth.
                downloaded = download_for_analysis(
                    lambda headers: requests.get(url, auth=auth, headers=headers, stream=True,
                                                 timeout=(10, 30), allow_redirects=True),
                    # The listing may be stale. Validate the current HTTP entity
                    # and update its metadata; do not join different versions.
                    local_path, max_size=limit,
                )
                entry.update(size=downloaded['size'], content_type=downloaded['content_type'],
                             modified=downloaded['modified'] or entry.get('modified', ''))
                analysis = asyncio.run(
                    analyze_resource(
                        local_path,
                        config.get("api_key") or os.getenv("OPENAI_API_KEY", ""),
                        api_base=config.get("api_base", "https://api.openai.com/v1"),
                        model=config.get("model", "gpt-4o-mini"),
                        reasoning_effort=config.get("reasoning_effort"),
                        max_frames=config.get("max_frames", 6),
                        narrative_frames=config.get("narrative_frames", 24),
                        timeout=config.get("timeout", 60),
                        resource_name=entry["name"],
                        mime_type=entry["content_type"],
                        text_hints={"file_name": entry["name"]},
                        source_context=entry.get("source_context"),
                        share_source_text=config.get("share_source_text") is True,
                        use_subtitles=config.get("use_subtitles") is True,
                        analysis_depth="deep" if mode == "deep" else "standard",
                        transcribe=bool(config.get("transcribe", False)),
                        min_confidence=config.get("min_confidence", 0.65),
                        max_audio_seconds=config.get("max_audio_seconds", 180),
                        progress_callback=progress,
                        summary_only=mode == "summary",
                        previous_analysis=entry.get("analysis") if mode == "summary" else None,
                    )
                )
                # A retry is a candidate until both classification and the video
                # synopsis pass. Keep the previous result intact on rejection;
                # the job/progress channel reports this attempt separately.
                previous = entry.get("analysis", {})
                if (
                    previous.get("status") == "complete"
                    and (analysis.get("status") != "complete"
                         or (detect_resource_kind(entry["name"], entry.get("content_type", "")) == "video"
                             and analysis.get("summary_quality", {}).get("status") != "ready"))
                ):
                    diagnostics = analysis.get("diagnostics", {})
                    attempts = [{name: item[name] for name in (
                        "stage", "attempt", "revision", "status", "reason", "http_status", "timeout_seconds", "elapsed_ms"
                    ) if name in item} for item in diagnostics.get("attempts", [])]
                    self._update(key, last_attempt={
                        "status": analysis.get("status"),
                        "reason": analysis.get("reason") or (attempts[-1].get("reason") if attempts else None),
                        "quality_issues": analysis.get("summary_quality", {}).get("issues", []),
                        "stage": analysis.get("failure_stage") or diagnostics.get("stage"),
                        "sampling": copy.deepcopy(analysis.get("sampling", {})),
                        "narrative_input": copy.deepcopy(diagnostics.get("narrative_input", {})),
                        "narrative_shape": copy.deepcopy(diagnostics.get("narrative_shape", {})),
                        "segments": copy.deepcopy(diagnostics.get("segments", {})),
                        "audit": copy.deepcopy(analysis.get("summary_quality", {}).get("audit", {})),
                        "primary_activity": copy.deepcopy(diagnostics.get("primary_activity", {})),
                        "dialogue_context": copy.deepcopy(diagnostics.get("dialogue_context", {})),
                        "ending_observation": copy.deepcopy(diagnostics.get("ending_observation", {})),
                        "ending_event_projection": copy.deepcopy(diagnostics.get("ending_event_projection", {})),
                        "summary_revision": copy.deepcopy(diagnostics.get("summary_revision", {})),
                        "attempts": attempts,
                    })
                    raise ValueError("本次分析或简介未通过检查，已保留原分类与简介；可重新尝试。")
                entry.update(analysis=analysis, origin="reanalysis")
                store.put(entry)
                if on_result:
                    on_result(analysis)
            self._update(
                key,
                status="failed" if analysis["status"] == "failed" else "done",
                message=analysis_status(analysis)
                if analysis["status"] == "complete"
                else analysis["summary"],
            )
        except Exception as error:
            message = (
                str(error)
                if isinstance(error, ValueError)
                else f"分析失败（{type(error).__name__}），请稍后重试"
            )
            self._update(key, status="failed", message=message)
            if on_progress:
                on_progress(f"重新分析失败：{message}")


analysis_jobs = AnalysisJobs()
