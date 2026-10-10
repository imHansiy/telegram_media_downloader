import { createContext, useContext, useEffect, useRef, useState } from "react";
import {
  EyeOff,
  File,
  Film,
  Image as ImageIcon,
  Music,
  LoaderCircle,
} from "lucide-react";
import { formatDuration, previewUrl, coverUrl, ResourceEntry } from "./model";

export const CoverScope = createContext("");

export function ResourceCover({
  entry,
  root,
  hidden = false,
  eager = false,
}: {
  entry: ResourceEntry;
  root: string;
  hidden?: boolean;
  eager?: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const scope = useContext(CoverScope);
  const [visible, setVisible] = useState(eager);
  const [result, setResult] = useState({ source: "", status: "loading" });
  const kind = entry.analysis.resource_kind;
  const supported = kind === "image" || kind === "video";
  const version = JSON.stringify([scope, entry.modified, entry.size]);
  // Only images are requested here. Playback is reserved for the explicit player.
  const source =
    (kind === "video"
      ? coverUrl(root, entry.path)
      : previewUrl(root, entry.path)) +
    "&v=" +
    encodeURIComponent(version);
  const shouldLoad = !hidden && supported && (eager || visible);
  const ready =
    shouldLoad && result.source === source && result.status === "ready";
  const failed = result.source === source && result.status === "failed";
  useEffect(() => {
    const observer = new IntersectionObserver(
      ([item]) => setVisible(item.isIntersecting),
      { rootMargin: "60px" },
    );
    if (host.current) observer.observe(host.current);
    return () => observer.disconnect();
  }, []);
  const Icon = hidden
    ? EyeOff
    : kind === "video"
      ? Film
      : kind === "image"
        ? ImageIcon
        : kind === "audio"
          ? Music
          : File;
  return (
    <div className={`media-cover cover-${kind}`} ref={host}>
      {shouldLoad && !failed && (
        <img
          key={source}
          src={source}
          alt={`${entry.name} 封面`}
          draggable={false}
          decoding="async"
          onLoad={() => setResult({ source, status: "ready" })}
          onError={() => setResult({ source, status: "failed" })}
        />
      )}
      {!ready && (
        <div className="cover-fallback">
          <Icon size={34} />
          <span>
            {hidden
              ? "隐私遮罩"
              : !supported
                ? "此类型无封面"
                : failed
                  ? kind === "video"
                    ? "暂无已保存封面"
                    : "图片暂不可用"
                  : shouldLoad
                    ? "加载封面中"
                    : "等待加载封面"}
          </span>
          {shouldLoad && !failed && <LoaderCircle size={16} className="spin" />}
        </div>
      )}
      {!hidden && !!entry.activity?.duration && (
        <span className="cover-duration">
          {formatDuration(entry.activity.duration)}
        </span>
      )}
    </div>
  );
}
