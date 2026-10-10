import { useEffect, useState } from "react";
import { LoaderCircle, Trash2 } from "lucide-react";
import { Modal } from "../components/ui/Modal";
import { libraryRequest, LibraryRequestError } from "./api";
import { flushActivityWrites } from "./activity";
import { ResourceEntry } from "./model";

interface Target {
  id: string;
  label: string;
  path: string;
  storage: "remote" | "local";
  exists: boolean;
}
interface Plan {
  token: string;
  targets: Target[];
  recycle_path: string;
}
interface Outcome {
  id: string;
  label: string;
  path: string;
  status: "deleted" | "recycled" | "missing" | "pending";
  destination: string;
}
interface Result {
  success: boolean;
  message: string;
  results?: Outcome[];
}
const statusLabels = {
  deleted: "云盘已删除",
  recycled: "已移入本地回收目录",
  missing: "文件不存在，无需处理",
  pending: "尚未完成",
};

export function ResourceDeleteDialog({
  entry,
  scopeId,
  onClose,
  onDeleted,
}: {
  entry: ResourceEntry;
  scopeId: string;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setPlan(null);
    setConfirmed(false);
    setError("");
    libraryRequest<Plan>("/api/library/delete/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: entry.path, scope_id: scopeId }),
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) setPlan(value);
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setError(
            reason instanceof Error ? reason.message : "无法核实删除范围",
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [entry.path, scopeId, revision]);

  async function remove() {
    if (!plan || !confirmed || busy) return;
    setBusy(true);
    setError("");
    try {
      await flushActivityWrites();
      const response = await libraryRequest<Result>("/api/library/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: plan.token, confirmed: true }),
      });
      setResult(response);
      onDeleted();
    } catch (reason) {
      if (reason instanceof LibraryRequestError && (reason.data as Result)?.results)
        setResult(reason.data as Result);
      setError(
        reason instanceof Error
          ? reason.message
          : "删除结果未确认，请重试核实，勿重复删除其他文件",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="删除资源" onClose={onClose} busy={busy}>
      <p className="confirm-filename">{entry.name}</p>
      <p>
        云盘资源及对应封面将实际删除，无法在本应用撤销。本地副本及封面移入下载目录下的回收目录，可手动恢复。不删除父目录或其他同名资源。
      </p>
      {loading && (
        <p role="status">
          <LoaderCircle size={17} className="spin" /> 正在核实文件及关联封面…
        </p>
      )}
      {plan && !result?.success && (
        <ul className="resource-delete-targets" aria-label="删除范围">
          {plan.targets.map((target) => (
            <li key={target.id}>
              <strong>{target.label}</strong>
              <code>{target.path}</code>
              <small>
                {!target.exists
                  ? "文件不存在，无需处理"
                  : target.storage === "local"
                    ? "移入本地回收目录"
                    : "删除云盘文件"}
              </small>
            </li>
          ))}
        </ul>
      )}
      {result?.results && (
        <div aria-label="删除执行结果">
          <p role="status">
            {result.success
              ? "删除完成，收藏和历史记录已清理。"
              : "删除尚未全部完成，以下是已确认的处理结果："}
          </p>
          <ul className="resource-delete-targets">
            {result.results.map((item) => (
              <li key={item.id}>
                <strong>
                  {item.label} · {statusLabels[item.status]}
                </strong>
                <code>{item.destination || item.path}</code>
              </li>
            ))}
          </ul>
        </div>
      )}
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {plan && !result?.success && (
        <>
          <p className="muted resource-delete-recovery">
            本地恢复位置：<code>{plan.recycle_path}</code>
            <br />
            完成后会同时清理资源索引、收藏、打开及观看记录。回收目录仍占用本地磁盘空间。
          </p>
          <label className="resource-delete-confirm">
            <input
              type="checkbox"
              aria-label="确认删除云盘资源并回收本地副本"
              checked={confirmed}
              disabled={busy}
              onChange={(event) => setConfirmed(event.target.checked)}
            />
            我已核对路径，确认一起处理资源、封面及关联记录。
          </label>
        </>
      )}
      <div className="dialog-actions">
        <button className="button" disabled={busy} onClick={onClose}>
          {result?.success ? "完成" : "取消"}
        </button>
        {!result?.success && (
          <>
            {error && (
              <button
                className="button"
                disabled={busy || loading}
                onClick={() => setRevision((value) => value + 1)}
              >
                重新核对范围
              </button>
            )}
            <button
              className="button danger"
              disabled={!plan || loading || busy || !confirmed}
              onClick={() => void remove()}
            >
              {busy ? (
                <LoaderCircle size={16} className="spin" />
              ) : (
                <Trash2 size={16} />
              )}
              {busy
                ? "正在删除…"
                : result?.results
                  ? "重试未完成项目"
                  : "确认删除资源"}
            </button>
          </>
        )}
      </div>
    </Modal>
  );
}
