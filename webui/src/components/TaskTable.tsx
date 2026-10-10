import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertCircle,
  ArrowDown,
  ArrowUp,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Download,
  Eye,
  FileText,
  HelpCircle,
  Image,
  Loader2,
  Music,
  Pause,
  Play,
  RotateCcw,
  Search,
  Trash2,
  Video,
} from "lucide-react";
import { SyncTask } from "../types";
import { formatSize } from "../library/model";
import { buildWebdavResourceUrl, isPdfFile } from "../webdavPreview";
import { Modal } from "./ui/Modal";

interface TaskTableProps {
  tasks: SyncTask[];
  onPauseTask: (id: string) => Promise<void> | void;
  onResumeTask: (id: string) => Promise<void> | void;
  onResetUpload: (id: string) => Promise<void> | void;
  onDeleteTask: (id: string, deleteRemote: boolean) => Promise<void>;
  onOpenAccounts: () => void;
}
const labels: Record<SyncTask["status"], string> = {
  pending: "等待中",
  downloading: "下载中",
  uploading: "上传中",
  syncing: "处理中",
  completed: "已完成",
  paused: "已暂停",
  failed: "下载失败",
  upload_failed: "上传失败",
};
const transferring = (task: SyncTask) =>
  ["downloading", "uploading", "syncing"].includes(task.status);
const failed = (task: SyncTask) =>
  ["failed", "upload_failed"].includes(task.status);
const speedLabel = (speed: number) =>
  speed >= 1024
    ? `${(speed / 1024).toFixed(1)} MB/s`
    : `${Math.max(0, speed).toFixed(0)} KB/s`;
function Progress({
  value,
  label,
  upload = false,
  statusText,
}: {
  value: number;
  label: string;
  upload?: boolean;
  statusText?: string;
}) {
  const progress = Number.isFinite(value)
    ? Math.max(0, Math.min(100, value))
    : 0;
  return (
    <div className={`transfer-progress ${upload ? "upload" : ""}`}>
      <span>
        {upload ? <ArrowUp size={13} /> : <ArrowDown size={13} />}
        {label}
        <b>{statusText || `${Math.round(progress)}%`}</b>
      </span>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuenow={Math.round(progress)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuetext={statusText || `${Math.round(progress)}%`}
      >
        <i style={{ width: `${progress}%` }} />
      </div>
    </div>
  );
}

export function TaskTable({
  tasks,
  onPauseTask,
  onResumeTask,
  onResetUpload,
  onDeleteTask,
  onOpenAccounts,
}: TaskTableProps) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(15);
  const [expanded, setExpanded] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [help, setHelp] = useState(false);
  const [preview, setPreview] = useState<SyncTask | null>(null);
  const [confirm, setConfirm] = useState<{
    task: SyncTask;
    action: "delete" | "reset";
  } | null>(null);
  const [confirmError, setConfirmError] = useState("");
  const counts = {
    all: tasks.length,
    syncing: tasks.filter(transferring).length,
    pending: tasks.filter((task) => task.status === "pending").length,
    failed: tasks.filter(failed).length,
    completed: tasks.filter((task) => task.status === "completed").length,
    paused: tasks.filter((task) => task.status === "paused").length,
  };
  const filtered = useMemo(
    () =>
      tasks.filter((task) => {
        const matches = [task.filename, task.sourceName, task.sourceId]
          .join(" ")
          .toLocaleLowerCase()
          .includes(query.trim().toLocaleLowerCase());
        return (
          matches &&
          (filter === "all" ||
            (filter === "syncing"
              ? transferring(task)
              : filter === "failed"
                ? failed(task)
                : task.status === filter))
        );
      }),
    [tasks, query, filter],
  );
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const currentPage = Math.min(page, pages);
  useEffect(() => setPage(1), [query, filter, pageSize]);
  const runAction = async (id: string, action: () => Promise<void> | void) => {
    setBusy(id);
    setError("");
    try {
      await action();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败，请重试。");
    } finally {
      setBusy("");
    }
  };
  const confirmAction = async (deleteRemote = false) => {
    if (!confirm) return;
    setBusy(confirm.task.id);
    setConfirmError("");
    try {
      if (confirm.action === "reset") await onResetUpload(confirm.task.id);
      else await onDeleteTask(confirm.task.id, deleteRemote);
      setConfirm(null);
    } catch (reason) {
      setConfirmError(
        reason instanceof Error ? reason.message : "操作失败，请重试。",
      );
    } finally {
      setBusy("");
    }
  };
  const ask = (task: SyncTask, action: "delete" | "reset") => {
    setConfirmError("");
    setConfirm({ task, action });
  };

  return (
    <div className="task-workspace">
      <header className="workspace-page-header">
        <div className="page-heading">
          <div>
            <div className="eyebrow">同步工作流</div>
            <h1>传输任务</h1>
            <p>从 Telegram 下载到本地，再同步到云盘。</p>
          </div>
          <button className="button" onClick={() => setHelp(true)}>
            <HelpCircle size={18} />
            如何添加任务
          </button>
        </div>
        <div className="task-overview">
          {[
            ["syncing", "进行中", Activity],
            ["pending", "等待中", Loader2],
            ["failed", "需要处理", AlertCircle],
            ["completed", "已完成", Check],
          ].map(([id, label, Icon]: any[]) => (
            <button
              key={id}
              className={`task-stat ${id} ${filter === id ? "active" : ""}`}
              onClick={() => setFilter(filter === id ? "all" : id)}
              aria-pressed={filter === id}
            >
              <Icon size={20} />
              <span>{label}</span>
              <strong>{counts[id as keyof typeof counts]}</strong>
            </button>
          ))}
          <div className="transfer-speed">
            <span>总传输速度</span>
            <strong>
              {speedLabel(
                tasks
                  .filter(transferring)
                  .reduce((sum, task) => sum + task.speedKb, 0),
              )}
            </strong>
          </div>
        </div>
      </header>
      <div className="task-toolbar">
        <div className="filter-tabs" aria-label="任务状态筛选">
          {[
            ["all", "全部"],
            ["syncing", "进行中"],
            ["pending", "等待"],
            ["paused", "暂停"],
            ["completed", "完成"],
            ["failed", "失败"],
          ].map(([id, label]) => (
            <button
              key={id}
              id={`tab-filter-${id}`}
              aria-pressed={filter === id}
              onClick={() => setFilter(id)}
            >
              {label}
              <span>{counts[id as keyof typeof counts]}</span>
            </button>
          ))}
        </div>
        <label className="search-field">
          <Search size={18} />
          <input
            id="search-tasks-input"
            aria-label="搜索任务"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索文件或来源…"
          />
        </label>
      </div>
      {error && (
        <div className="notice error" role="alert">
          {error}
          <button className="text-button" onClick={() => setError("")}>
            关闭
          </button>
        </div>
      )}
      <div className="task-list-scroll">
        <div className="task-column-head" aria-hidden="true">
          <span>文件 / 来源</span>
          <span>传输进度</span>
          <span>速度</span>
          <span>状态</span>
          <span>操作</span>
        </div>
        <section aria-label="任务列表" className="task-list">
          {filtered
            .slice((currentPage - 1) * pageSize, currentPage * pageSize)
            .map((task) => {
              const Icon = {
                photo: Image,
                video: Video,
                document: FileText,
                audio: Music,
                voice: Music,
              }[task.type];
              const previewable =
                task.status === "completed" && Boolean(task.remotePath?.trim());
              return (
                <article
                  key={task.id}
                  id={`task-row-${task.id}`}
                  className={`task-record ${failed(task) ? "task-failed" : ""}`}
                  aria-label={task.filename}
                >
                  <div className="task-row">
                    <div className="task-file">
                      <span className={`resource-icon kind-${task.type}`}>
                        <Icon size={22} />
                      </span>
                      <div>
                        <button
                          className="task-name"
                          title={task.filename}
                          aria-expanded={expanded === task.id}
                          onClick={() =>
                            setExpanded(expanded === task.id ? "" : task.id)
                          }
                        >
                          <span>{task.filename}</span>
                          <ChevronDown size={15} />
                        </button>
                        <p className="resource-meta">
                          <span title={task.sourceName || task.sourceId}>
                            {task.sourceName || task.sourceId || "来源未知"}
                          </span>
                          <span>{formatSize(task.sizeBytes)}</span>
                        </p>
                      </div>
                    </div>
                    <div className="task-progress">
                      <Progress
                        label="下载"
                        value={
                          task.status === "completed"
                            ? 100
                            : task.downloadProgress
                        }
                      />
                      <Progress
                        label="上传"
                        upload
                        statusText={task.uploadProgress === 0 && ["pending", "downloading", "analyzing", "waiting_upload"].includes(task.phase || "") ? "待上传" : undefined}
                        value={
                          task.status === "completed"
                            ? 100
                            : task.uploadProgress
                        }
                      />
                    </div>
                    <div className="task-speed">
                      {transferring(task) && task.speedKb > 0
                        ? speedLabel(task.speedKb)
                        : "—"}
                    </div>
                    <div className="task-status">
                      <span className={`status-pill ${task.status}`}>
                        {transferring(task) && (
                          <Loader2 size={14} className="spin" />
                        )}
                        {task.phaseLabel || labels[task.status]}
                      </span>
                      {task.aiStatus && (
                        <span className="task-ai-state" title={task.aiStatus}>
                          {task.aiStatus}
                        </span>
                      )}
                    </div>
                    <div className="task-actions">
                      {previewable && (
                        <button
                          id={`btn-preview-${task.id}`}
                          className="icon-button"
                          aria-label="预览已完成文件"
                          onClick={() => setPreview(task)}
                        >
                          <Eye size={18} />
                        </button>
                      )}
                      {task.status === "paused" && (
                        <button
                          className="icon-button"
                          aria-label="继续传输"
                          disabled={!!busy}
                          onClick={() =>
                            runAction(task.id, () => onResumeTask(task.id))
                          }
                        >
                          <Play size={18} />
                        </button>
                      )}
                      {["downloading", "uploading", "pending"].includes(
                        task.status,
                      ) && (
                        <button
                          className="icon-button"
                          aria-label="暂停传输"
                          disabled={!!busy}
                          onClick={() =>
                            runAction(task.id, () => onPauseTask(task.id))
                          }
                        >
                          <Pause size={18} />
                        </button>
                      )}
                      {failed(task) && (
                        <button
                          className="icon-button"
                          aria-label="重置上传"
                          disabled={!!busy}
                          onClick={() => ask(task, "reset")}
                        >
                          <RotateCcw size={18} />
                        </button>
                      )}
                      <button
                        className="icon-button danger-text"
                        aria-label="删除任务"
                        disabled={!!busy}
                        onClick={() => ask(task, "delete")}
                      >
                        <Trash2 size={17} />
                      </button>
                    </div>
                  </div>
                  {task.errorMsg && (
                    <button
                      className="task-error"
                      onClick={() =>
                        setExpanded(expanded === task.id ? "" : task.id)
                      }
                    >
                      <AlertCircle size={15} />
                      <span>{task.errorMsg}</span>
                      <span>查看详情</span>
                    </button>
                  )}
                  {expanded === task.id && (
                    <div className="task-expanded">
                      <h3>任务详情</h3>
                      {task.aiSummary && (
                        <p className="task-summary">{task.aiSummary}</p>
                      )}
                      {task.errorMsg && (
                        <p className="notice error">{task.errorMsg}</p>
                      )}
                      <dl>
                        <div>
                          <dt>完整文件名</dt>
                          <dd>{task.filename}</dd>
                        </div>
                        <div>
                          <dt>来源</dt>
                          <dd>
                            {task.sourceName} {task.sourceId}
                          </dd>
                        </div>
                        <div>
                          <dt>云端路径</dt>
                          <dd>{task.remotePath || "尚未生成"}</dd>
                        </div>
                        <div>
                          <dt>创建时间</dt>
                          <dd>
                            {task.createdAt &&
                            !Number.isNaN(Date.parse(task.createdAt))
                              ? new Date(task.createdAt).toLocaleString()
                              : "未知"}
                          </dd>
                        </div>
                      </dl>
                    </div>
                  )}
                </article>
              );
            })}
          {filtered.length === 0 && (
            <div className="empty-state">
              <Activity size={38} />
              <h2>{tasks.length ? "没有符合条件的任务" : "还没有传输任务"}</h2>
              <p>
                {tasks.length
                  ? "试试其他状态或关键词。"
                  : "连接 Telegram 账号后，通过监控会话或 Bot 投递资源。任务进度会在这里实时显示。"}
              </p>
              <button
                className="button primary"
                onClick={
                  tasks.length
                    ? () => {
                        setFilter("all");
                        setQuery("");
                      }
                    : onOpenAccounts
                }
              >
                {tasks.length ? "清除筛选" : "前往账号管理"}
              </button>
            </div>
          )}
        </section>
      </div>
      <footer className="table-footer">
        <span>{filtered.length} 个任务</span>
        <div className="button-row">
          <select
            aria-label="每页任务数"
            value={pageSize}
            onChange={(event) => setPageSize(Number(event.target.value))}
          >
            {[15, 30, 60].map((size) => (
              <option key={size} value={size}>
                {size} 条 / 页
              </option>
            ))}
          </select>
          <button
            className="icon-button"
            aria-label="上一页任务"
            disabled={currentPage <= 1}
            onClick={() => setPage(currentPage - 1)}
          >
            <ChevronLeft size={18} />
          </button>
          <span>
            {currentPage} / {pages}
          </span>
          <button
            className="icon-button"
            aria-label="下一页任务"
            disabled={currentPage >= pages}
            onClick={() => setPage(currentPage + 1)}
          >
            <ChevronRight size={18} />
          </button>
        </div>
      </footer>
      {help && (
        <Modal title="添加同步任务" onClose={() => setHelp(false)}>
          <p>当前任务来自真实的 Telegram 监控会话或 Bot 投递。</p>
          <ol className="help-steps">
            <li>在「账号」中登录并启动 Telegram 会话。</li>
            <li>通过已配置的监控会话接收媒体，或向 Bot 发送资源链接。</li>
            <li>返回此页查看下载、分析与上传状态。</li>
          </ol>
          <div className="dialog-actions">
            <button className="button primary" onClick={onOpenAccounts}>
              管理账号
            </button>
          </div>
        </Modal>
      )}
      {confirm && (
        <Modal
          title={confirm.action === "reset" ? "重置上传" : "删除任务"}
          onClose={() => setConfirm(null)}
          busy={!!busy}
        >
          <p className="confirm-filename">{confirm.task.filename}</p>
          <p>
            {confirm.action === "reset"
              ? "将清理该任务的云端异常文件，并重新加入上传队列。已完整下载的本地文件会用于重传。"
              : "选择仅删除任务记录，或同时删除云盘文件。删除云盘文件无法在此撤销。"}
          </p>
          {confirmError && (
            <p role="alert" className="notice error">
              {confirmError}
            </p>
          )}
          <div className="confirmation-options">
            {confirm.action === "reset" ? (
              <button
                className="button primary"
                disabled={!!busy}
                onClick={() => confirmAction()}
              >
                确认重置并重传
              </button>
            ) : (
              <>
                <button
                  className="button"
                  disabled={!!busy}
                  onClick={() => confirmAction(false)}
                >
                  仅删除任务
                </button>
                <button
                  className="button danger"
                  disabled={!!busy || !confirm.task.remotePath?.trim()}
                  onClick={() => confirmAction(true)}
                >
                  删除任务和远程资源
                </button>
              </>
            )}
          </div>
          {busy && (
            <p role="status" className="muted">
              正在处理，请稍候…
            </p>
          )}
          <div className="dialog-actions">
            <button
              className="button quiet"
              disabled={!!busy}
              onClick={() => setConfirm(null)}
            >
              取消
            </button>
          </div>
        </Modal>
      )}
      {preview && (
        <TaskPreview task={preview} onClose={() => setPreview(null)} />
      )}
    </div>
  );
}

function TaskPreview({
  task,
  onClose,
}: {
  task: SyncTask;
  onClose: () => void;
}) {
  const [error, setError] = useState(false);
  const file = {
    name: task.filename,
    type: task.type,
    remotePath: task.remotePath,
    sizeBytes: task.sizeBytes,
    sourceName: task.sourceName,
  };
  const url = buildWebdavResourceUrl(file);
  return (
    <Modal title="文件预览" wide onClose={onClose}>
      <p className="preview-filename">{task.filename}</p>
      <div className="media-stage">
        {error ? (
          <p className="notice error">
            无法加载此媒体，请检查云端文件或下载后查看。
          </p>
        ) : task.type === "photo" ? (
          <img src={url} alt={task.filename} onError={() => setError(true)} />
        ) : task.type === "video" ? (
          <video
            src={url}
            controls
            playsInline
            preload="metadata"
            onError={() => setError(true)}
          />
        ) : task.type === "audio" || task.type === "voice" ? (
          <audio
            src={url}
            controls
            preload="metadata"
            onError={() => setError(true)}
          />
        ) : isPdfFile(file) ? (
          <iframe src={url} title={task.filename} />
        ) : (
          <p>此格式请下载后查看。</p>
        )}
      </div>
      <div className="dialog-actions">
        <a href={buildWebdavResourceUrl(file, true)} className="button primary">
          <Download size={17} />
          下载原文件
        </a>
      </div>
    </Modal>
  );
}
