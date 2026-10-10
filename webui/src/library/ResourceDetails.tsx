import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ChevronDown,
  Download,
  ExternalLink,
  Eye,
  FileSearch,
  RefreshCw,
  X,
  Heart,
  Trash2,
} from "lucide-react";
import { Modal } from "../components/ui/Modal";
import { libraryRequest } from "./api";
import {
  AnalysisJob,
  formatSize,
  formatDuration,
  kindLabels,
  previewUrl,
  ResourceAnalysis,
  ResourceEntry,
  statusLabels,
} from "./model";
import { synopsisView } from "./synopsis";

const sourceLabels: Record<string, string> = {
  video_frames: "视频采样画面",
  image: "图片画面",
  transcript: "语音转写",
  document_text: "文档正文",
  archive_listing: "压缩包清单",
};
const stageLabels: Record<string, string> = {
  ending: "末段状态观察",
  ending_frame: "末帧独立观察",
  narrative_mode: "叙事模式判定",
  segment: "分段内容理解",
  primary_activity: "主活动核对",
  extraction: "内容提取",
  classification: "分类识别",
  details: "分类核验",
  narrative: "事件整理",
  summary: "剧情与过程总结",
  narrative_audit: "内容事实核验",
};
const errorLabels: Record<string, string> = {
  model_timeout: "超时",
  model_connection: "连接中断",
  model_auth: "鉴权或权限错误",
  model_rate_limit: "接口限流",
  model_server: "服务端错误",
  model_refused: "模型拒绝",
  model_invalid_response: "返回格式错误",
  model_truncated: "输出被截断",
  model_request: "请求不兼容",
};

export function ResourceDetails({
  entry,
  root,
  onClose,
  onPreview,
  onChanged,
  onLocate,
  onFavorite,
  favoriteBusy,
  onDelete,
}: {
  entry: ResourceEntry;
  root: string;
  onClose: () => void;
  onPreview: () => void;
  onChanged: () => void;
  onLocate: () => void;
  onFavorite?: () => void;
  favoriteBusy?: boolean;
  onDelete?: () => void;
}) {
  const [analysis, setAnalysis] = useState(entry.analysis);
  const [job, setJob] = useState<AnalysisJob>({});
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let wasRunning = false;
    setAnalysis(entry.analysis);
    setError("");
    const poll = async () => {
      try {
        const result = await libraryRequest<{
          analysis: ResourceAnalysis;
          job: AnalysisJob;
        }>(`/api/webdav/summary?path=${encodeURIComponent(entry.path)}`, {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setError("");
        setAnalysis(result.analysis);
        setJob(result.job || {});
        if (
          result.job?.status === "running" ||
          result.job?.status === "queued"
        ) {
          wasRunning = true;
          timer = setTimeout(poll, 2000);
        } else if (wasRunning || refresh > 0) {
          onChanged();
        }
      } catch (reason) {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "详情读取失败");
          if (wasRunning || refresh > 0) timer = setTimeout(poll, 3000);
        }
      }
    };
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [entry.path, refresh, onChanged]);

  async function reanalyze(mode: "full" | "summary" | "deep" = "full") {
    setSubmitting(true);
    setError("");
    try {
      const result = await libraryRequest<{ job: AnalysisJob }>(
        "/api/library/analyze",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: entry.path, mode }),
        },
      );
      setJob(result.job);
      setRefresh((value) => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法开始分析");
    } finally {
      setSubmitting(false);
    }
  }
  const busy =
    submitting || job.status === "queued" || job.status === "running";
  const synopsis = synopsisView(analysis);
  const facets = Object.keys(analysis.facets).length > 0 && (
    <div className="analysis-facets">
      {Object.entries(analysis.facets).map(([direction, text]) => (
        <section className="analysis-facet" key={direction}>
          <h3>{direction}</h3><p>{text}</p>
        </section>
      ))}
    </div>
  );
  return (
    <aside aria-label="资源分析详情" className="resource-reader">
      <header className="reader-header">
        <div className="reader-kicker">
          <span>
            <FileSearch size={17} />
            内容分析
          </span>
          <button
            aria-label="关闭详情"
            onClick={onClose}
            className="icon-button"
          >
            <X size={19} className="desktop-close" />
            <ArrowLeft size={20} className="mobile-back" />
          </button>
        </div>
        <h2>{entry.name}</h2>
        <div className="reader-classification">
          <span className="badge">{kindLabels[analysis.resource_kind]}</span>
          <span
            className={`badge ${analysis.category === "adult" ? "adult-badge" : "accent-badge"}`}
          >
            {analysis.category_label}
            {analysis.subtype_label ? ` / ${analysis.subtype_label}` : ""}
          </span>
          <span
            className={`analysis-state ${analysis.status === "complete" && !synopsis.needsUpdate ? "complete" : "review"}`}
          >
            <i />
            {synopsis.stateLabel}
          </span>
        </div>
        <div className="reader-actions">
          {onFavorite && (
            <button
              className="button favorite-toggle"
              onClick={onFavorite}
              disabled={favoriteBusy}
              aria-pressed={!!entry.activity?.favorite}
            >
              <Heart
                size={18}
                fill={entry.activity?.favorite ? "currentColor" : "none"}
              />
              {entry.activity?.favorite ? "已收藏" : "收藏"}
            </button>
          )}
          <button onClick={onPreview} className="button primary">
            <Eye size={17} />
            预览资源
          </button>
          <a href={previewUrl(root, entry.path, true)} className="button">
            <Download size={17} />
            下载原文件
          </a>
          {onDelete && (
            <button
              className="button danger-text"
              disabled={busy}
              onClick={onDelete}
            >
              <Trash2 size={17} />
              删除资源
            </button>
          )}
          {synopsis.videoComplete && (
            <button className="button" disabled={busy}
              onClick={() => void reanalyze("summary")}
              title="沿用已确认分类，重新读取本视频证据并更新简介，不重新决定分类。">
              <RefreshCw size={16} /> 更新简介
            </button>
          )}
          <button
            onClick={() => void reanalyze()}
            disabled={busy}
            className="button quiet"
            aria-label={busy ? "资源分析进行中" : "重新分析此资源"}
            title="重新分析此资源：读取文件并调用已配置的模型，不移动云端文件。"
          >
            <RefreshCw size={16} className={busy ? "spin" : ""} />
            <span className="reanalyze-label">
              {busy ? "分析中…" : "重新分析此资源"}
            </span>
          </button>
        </div>
        {busy && (
          <p className="notice" role="status">
            {job.message || "正在提交分析任务…"}
          </p>
        )}
        {(error || job.status === "failed") && (
          <p role="alert" className="notice error">
            {error || job.message}
          </p>
        )}
      </header>
      <div className="reader-scroll">
        {analysis.status !== "complete" && (
          <p className="notice warning">
            此资源{statusLabels[analysis.status]}
            {analysis.failure_stage
              ? `，未完成阶段：${stageLabels[analysis.failure_stage] || analysis.failure_stage}`
              : "，未确认内容分类"}
            。
            {analysis.retryable
              ? "可稍后重新分析；原文件不受影响。"
              : "请先查看下方原因，原文件不受影响。"}
          </p>
        )}
        {analysis.status !== "complete" && analysis.content_description && (
          <section className="analysis-facet">
            <h3>已识别内容</h3>
            <p>{analysis.content_description}</p>
            <p className="muted">
              模型根据已提供证据整理的内容观察，分类尚未确认。
            </p>
          </section>
        )}
        {synopsis.videoComplete ? (
          <section className="analysis-facet reader-overview">
            <h3>{synopsis.heading}</h3>
            <p>{synopsis.text}</p>
          </section>
        ) : facets ? (
          facets
        ) : (
          <section className="analysis-facet">
            <h3>
              {analysis.status === "failed"
                ? "分析失败原因"
                : analysis.status === "needs_review"
                  ? "待确认原因"
                  : "内容概览"}
            </h3>
            <p>
              {analysis.summary || "尚未生成分析。点击「重新分析此资源」开始。"}
            </p>
            {analysis.review_note && (
              <p className="muted">
                模型说明（待核实）：{analysis.review_note}
              </p>
            )}
          </section>
        )}
        {analysis.tags.length > 0 && (
          <div className="reader-tags">
            {analysis.tags.map((tag) => (
              <span key={tag}>#{tag}</span>
            ))}
          </div>
        )}
        <details className="analysis-provenance">
          <summary>
            <span>分析依据与文件信息</span>
            <ChevronDown size={18} />
          </summary>
          {synopsis.videoComplete && facets}
          {analysis.resource_kind === "video" && (
            <div className="evidence-limitations">
              <button className="button" disabled={busy} onClick={() => void reanalyze("deep")}>
                <FileSearch size={16} /> 深度采样分析
              </button>
              <p className="muted">最多 48 帧、4 个独立窗口；启用转写时扩大语音预算（最多 600 秒）。耗时和模型费用可能增加，不代表看完全片，也不会自动开启文案或字幕外发。</p>
            </div>
          )}
          <div className="evidence-limitations">
            <h4>发布者描述（未经核实）</h4>
            <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{entry.source_context?.caption || entry.source_context?.text || "未保存原始消息文案；不会根据文件名补写。"}</p>
            {entry.source_context?.original_name && <p className="muted">原始文件名：{entry.source_context.original_name}</p>}
            <p className="muted">发布者说法不等于视频事实；仅在允许发送文案后，才会作为分析线索。</p>
            <p className="muted">本次文案使用：{analysis.source_usage ? analysis.source_usage.publisher_text_shared ? "已作为待核实线索发送" : "未发送" : "未记录"}</p>
          </div>
          {!!analysis.narrative?.events.length && (
            <div className="narrative-events evidence-limitations">
              <h4>事件与发展依据</h4>
              {analysis.narrative.events.map((event) => (
                <p key={event.id}><strong>{formatDuration(event.start)}–{formatDuration(event.end)}</strong> · {event.action}</p>
              ))}
              <p className="muted">时间来自实际采样引用；事件先后不代表未证实的因果，未采样部分不作推断。</p>
            </div>
          )}
          {!!analysis.summary_sentences?.length && (
            <div className="summary-evidence evidence-limitations">
              <h4>简介对应证据</h4>
              {analysis.summary_sentences.map((sentence, index) => (
                <p key={index}>
                  第 {index + 1} 句 · {sentence.evidence_refs.map((ref) => {
                    const item = analysis.summary_quality?.evidence_catalog.find((value) => value.id === ref);
                    return item?.kind === "transcript"
                      ? `${item.origin === "subtitle" ? "字幕" : "语音"} ${formatDuration(item.start)}–${formatDuration(item.end)}`
                      : `画面 ${formatDuration(item?.timestamp)}`;
                  }).join("、")}
                </p>
              ))}
            </div>
          )}
          <dl>
            {analysis.sampling?.subtitles && <div><dt>字幕来源</dt><dd>{analysis.sampling.subtitles.origin === "sidecar" ? "本地同名字幕" : "内嵌字幕"} · 选取 {analysis.sampling.subtitles.selected_cues}/{analysis.sampling.subtitles.available_cues} 条，未逐句听音校准。</dd></div>}
            {analysis.diagnostics?.classification_reused && (
              <div><dt>本次分析范围</dt><dd>仅更新内容简介；分类与分类依据沿用上次已确认结果。
                {analysis.diagnostics.classification_analyzed_at && `（${new Date(analysis.diagnostics.classification_analyzed_at).toLocaleString()}）`}
              </dd></div>
            )}
            <div>
              <dt>内容证据</dt>
              <dd>
                {analysis.evidence_sources
                  .map((source) => sourceLabels[source] || source)
                  .join("、") || "暂无直接内容证据"}
              </dd>
            </div>
            <div>
              <dt>置信度</dt>
              <dd>
                {analysis.confidence == null
                  ? "未知"
                  : `${Math.round(analysis.confidence * 100)}%`}
              </dd>
            </div>
            {!!analysis.sampling?.timestamps?.length && (
              <div>
                <dt>视频采样</dt>
                <dd>
                  {analysis.sampling.method === "timeline_windows"
                    ? `${analysis.sampling.timeline_windows?.length || 0} 个时间窗、${analysis.sampling.timestamps.length} 帧（局部前后采样）`
                    : `${analysis.sampling.candidate_count} 个候选画面中选取 ${analysis.sampling.timestamps.length} 帧（非连续画面）`}
                  <br />
                  {analysis.sampling.timestamps
                    .map((seconds) => `${seconds} 秒`)
                    .join("、")}
                </dd>
              </div>
            )}
            <div>
              <dt>分析方向</dt>
              <dd>{analysis.directions.join(" / ") || "未确定"}</dd>
            </div>
            {analysis.sampling?.audio && (
              <div>
                <dt>语音采样</dt>
                <dd>
                  {analysis.sampling.audio.method === "timeline_aligned" ? "与事件时间窗对齐" : analysis.sampling.audio.method === "distributed" ? "前、中、后段" : "有界片段"}
                  {` · ${analysis.sampling.audio.reliable_clips || 0} 段可靠转写`}
                  <br />
                  {analysis.sampling.audio.windows?.map((window) =>
                    `${formatDuration(window.start)}–${formatDuration(window.end)}（${window.status === "accepted" ? "已采用" : "未采用"}）`
                  ).join("、")}
                </dd>
              </div>
            )}
            {analysis.diagnostics?.threshold != null && (
              <div>
                <dt>本次分类阈值</dt>
                <dd>{Math.round(analysis.diagnostics.threshold * 100)}%</dd>
              </div>
            )}
            <div>
              <dt>文件大小</dt>
              <dd>{formatSize(entry.size)}</dd>
            </div>
            <div>
              <dt>更新时间</dt>
              <dd>
                {entry.modified && !Number.isNaN(Date.parse(entry.modified))
                  ? new Date(entry.modified).toLocaleString()
                  : "时间未知"}
              </dd>
            </div>
            <div>
              <dt>云端路径</dt>
              <dd className="file-path">
                /{root}/{entry.path}
              </dd>
            </div>
          </dl>
          {!!analysis.diagnostics?.attempts.length && (
            <div className="evidence-limitations" aria-label="模型请求记录">
              <h4>模型请求记录</h4>
              {analysis.diagnostics.attempts.map((attempt, index) => (
                <p key={index}>
                  {stageLabels[attempt.stage] || attempt.stage} · 第{" "}
                  {attempt.attempt} 次 ·{" "}
                  {attempt.status === "success"
                    ? "成功"
                    : errorLabels[attempt.reason || ""] || "失败"}
                  {attempt.http_status ? `（HTTP ${attempt.http_status}）` : ""}
                  {` · ${(attempt.elapsed_ms / 1000).toFixed(1)} 秒 / 上限 ${attempt.timeout_seconds} 秒`}
                </p>
              ))}
            </div>
          )}
          {analysis.limitations.length > 0 && (
            <div className="evidence-limitations">
              <h4>分析范围与限制</h4>
              {analysis.limitations.map((text, index) => (
                <p key={index}>{text}</p>
              ))}
            </div>
          )}
          <p className="muted">
            重新分析会调用已配置的模型，读取此文件并更新索引，不移动云端文件。
          </p>
        </details>
        <button onClick={onLocate} className="text-button locate-button">
          <ExternalLink size={16} />
          打开所在目录
        </button>
      </div>
    </aside>
  );
}

export function ResourcePreview({
  entry,
  root,
  onClose,
  onProgress,
  progressError,
}: {
  entry: ResourceEntry;
  root: string;
  onClose: () => void;
  onProgress?: (position: number, duration: number) => void;
  progressError?: string;
}) {
  const [failed, setFailed] = useState(false);
  const progressCallback = useRef(onProgress);
  progressCallback.current = onProgress;
  const progress = useRef({
    played: false,
    position: 0,
    duration: 0,
    sentAt: 0,
  });
  const report = (force = false) => {
    const state = progress.current;
    if (
      !state.played ||
      !Number.isFinite(state.duration) ||
      state.duration <= 0 ||
      !Number.isFinite(state.position)
    )
      return;
    if (force || Date.now() - state.sentAt >= 5000) {
      state.sentAt = Date.now();
      progressCallback.current?.(state.position, state.duration);
    }
  };
  useEffect(() => {
    const flush = () => report(true);
    const visibility = () => {
      if (document.hidden) flush();
    };
    document.addEventListener("visibilitychange", visibility);
    window.addEventListener("pagehide", flush);
    return () => {
      flush();
      document.removeEventListener("visibilitychange", visibility);
      window.removeEventListener("pagehide", flush);
    };
  }, []);
  const mediaEvents = {
    onLoadedMetadata: (event: React.SyntheticEvent<HTMLMediaElement>) => {
      const media = event.currentTarget;
      const previous = entry.activity;
      if (
        !previous?.completed &&
        previous?.position &&
        Number.isFinite(media.duration)
      ) {
        media.currentTime = Math.min(
          previous.position,
          Math.max(0, media.duration - 1),
        );
      }
    },
    onPlay: (event: React.SyntheticEvent<HTMLMediaElement>) => {
      progress.current = {
        ...progress.current,
        played: true,
        position: event.currentTarget.currentTime,
        duration: event.currentTarget.duration,
      };
      report(true);
    },
    onTimeUpdate: (event: React.SyntheticEvent<HTMLMediaElement>) => {
      progress.current.position = event.currentTarget.currentTime;
      progress.current.duration = event.currentTarget.duration;
      if (!event.currentTarget.paused && !event.currentTarget.seeking) report();
    },
    onPause: () => report(true),
    onEnded: (event: React.SyntheticEvent<HTMLMediaElement>) => {
      progress.current.position = event.currentTarget.duration;
      progress.current.duration = event.currentTarget.duration;
      report(true);
    },
  };
  const url = previewUrl(root, entry.path);
  const kind = entry.analysis.resource_kind;
  return (
    <Modal title="资源预览" wide onClose={onClose}>
      <p className="preview-filename">{entry.name}</p>
      {progressError && (
        <p className="notice error" role="alert">
          观看进度尚未保存：{progressError}
        </p>
      )}
      <div className="media-stage">
        {failed ? (
          <p className="notice error">预览失败，请尝试下载原文件。</p>
        ) : kind === "image" ? (
          <img src={url} alt={entry.name} onError={() => setFailed(true)} />
        ) : kind === "video" ? (
          <video
            {...mediaEvents}
            controls
            playsInline
            preload="metadata"
            src={url}
            onError={() => setFailed(true)}
          />
        ) : kind === "audio" ? (
          <audio
            {...mediaEvents}
            controls
            preload="metadata"
            src={url}
            onError={() => setFailed(true)}
          />
        ) : (
          <p>此格式请下载后查看，内容分析可在详情中阅读。</p>
        )}
      </div>
      <div className="dialog-actions">
        <a href={previewUrl(root, entry.path, true)} className="button">
          <Download size={17} />
          下载原文件
        </a>
        <button className="button primary" onClick={onClose}>
          返回分析
        </button>
      </div>
    </Modal>
  );
}
