import { useEffect, useState } from "react";
import { ChevronLeft, Folder, File, LoaderCircle } from "lucide-react";
import { libraryRequest } from "./api";
import { LibraryResponse, parentPath, ResourceEntry } from "./model";
import { Modal } from "../components/ui/Modal";

export function DirectoryBrowser({
  initialPath = "",
  onClose,
  onOpen,
}: {
  initialPath?: string;
  onClose: () => void;
  onOpen: (entry: ResourceEntry, root: string) => void;
}) {
  const [path, setPath] = useState(initialPath);
  const [data, setData] = useState<LibraryResponse | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError("");
    libraryRequest<LibraryResponse>(
      `/api/webdav/browse?path=${encodeURIComponent(path)}`,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [path, revision]);
  return (
    <Modal title="来源目录" wide onClose={onClose}>
      <div className="directory-toolbar">
        <button
          className="button"
          onClick={() => setPath(parentPath(path))}
          disabled={!path}
        >
          <ChevronLeft size={17} />
          上一级目录
        </button>
        <button className="text-button" onClick={() => setPath("")}>
          云盘根目录
        </button>
        <span>/{path}</span>
      </div>
      {error ? (
        <p role="alert" className="notice error">
          {error}
          <button
            className="text-button"
            onClick={() => setRevision((v) => v + 1)}
          >
            重试
          </button>
        </p>
      ) : !data ? (
        <p role="status">
          <LoaderCircle size={20} className="spin" />
          正在读取目录…
        </p>
      ) : (
        <div className="directory-items">
          {data.folders?.map((folder) => (
            <button key={folder.path} onClick={() => setPath(folder.path)}>
              <Folder size={22} />
              {folder.name}
            </button>
          ))}
          {data.files.map((entry) => (
            <button key={entry.path} onClick={() => onOpen(entry, data.root)}>
              <File size={22} />
              <span>
                {entry.name}
                <small>{entry.analysis.category_label}</small>
              </span>
            </button>
          ))}
          {!data.files.length && !data.folders?.length && (
            <p className="empty-state">此目录为空</p>
          )}
        </div>
      )}
    </Modal>
  );
}
