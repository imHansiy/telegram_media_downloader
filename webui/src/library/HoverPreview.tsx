import { useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Heart, X, ArrowUpRight, EyeOff } from "lucide-react";
import { ResourceEntry, kindLabels } from "./model";
import { synopsisView } from "./synopsis";
import { ResourceCover } from "./ResourceCover";

export function HoverPreview({
  entry,
  root,
  anchor,
  privateMode,
  busy,
  onEnter,
  onLeave,
  onClose,
  onOpen,
  onFavorite,
}: {
  entry: ResourceEntry;
  root: string;
  anchor: HTMLElement;
  privateMode: boolean;
  busy: boolean;
  onEnter: () => void;
  onLeave: () => void;
  onClose: () => void;
  onOpen: () => void;
  onFavorite: () => void;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: 12, top: 12 });
  const synopsis = synopsisView(entry.analysis);
  useLayoutEffect(() => {
    const update = () => {
      const rect = anchor.getBoundingClientRect();
      const width = panel.current?.offsetWidth || 420;
      const height = panel.current?.offsetHeight || 540;
      const right = rect.right + 10;
      const left =
        right + width < window.innerWidth - 12 ? right : rect.left - width - 10;
      setPosition({
        left: Math.max(12, Math.min(left, window.innerWidth - width - 12)),
        top: Math.max(12, Math.min(rect.top, window.innerHeight - height - 12)),
      });
    };
    update();
    const observer = new ResizeObserver(update);
    if (panel.current) observer.observe(panel.current);
    const dismiss = (event: Event) => {
      if (!panel.current?.contains(event.target as Node)) onClose();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        anchor.querySelector<HTMLElement>(".poster-open")?.focus();
      }
    };
    window.addEventListener("resize", update);
    window.addEventListener("scroll", dismiss, true);
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", dismiss, true);
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, [anchor, onClose]);
  return createPortal(
    <div
      ref={panel}
      className="hover-preview"
      role="dialog"
      aria-label="资源悬浮预览"
      style={position}
      onPointerEnter={onEnter}
      onPointerLeave={onLeave}
      onFocus={onEnter}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) onLeave();
      }}
    >
      <button
        className="hover-close icon-button"
        aria-label="关闭悬浮预览"
        onClick={onClose}
      >
        <X size={18} />
      </button>
      <ResourceCover entry={entry} root={root} hidden={privateMode} eager />
      <div className="hover-copy">
        <h2>{privateMode ? "内容已隐藏" : entry.name}</h2>
        <div className="poster-meta">
          <span>{kindLabels[entry.analysis.resource_kind]}</span>
          <span>
            {entry.analysis.category === "adult"
              ? "NSFW"
              : entry.analysis.category_label}
          </span>
          <span>{synopsis.stateLabel}</span>
        </div>
        <h3>
          {privateMode ? "简介" : synopsis.heading}
        </h3>
        <p className="hover-summary">
          {privateMode
            ? "隐私模式已开启，封面、简介与标签不会自动显示。"
            : synopsis.text}
        </p>
        {!privateMode && (
          <div className="hover-tags">
            {entry.analysis.tags.map((tag) => (
              <span key={tag}>{tag}</span>
            ))}
          </div>
        )}
        <div className="hover-actions">
          <button className="button primary" onClick={onOpen}>
            打开详情 <ArrowUpRight size={17} />
          </button>
          <button
            className="button favorite-toggle"
            disabled={busy}
            aria-pressed={!!entry.activity?.favorite}
            onClick={onFavorite}
          >
            <Heart
              size={18}
              fill={entry.activity?.favorite ? "currentColor" : "none"}
            />
            {entry.activity?.favorite ? "已收藏" : "收藏"}
          </button>
        </div>
        {privateMode && (
          <small>
            <EyeOff size={13} /> 隐私模式
          </small>
        )}
      </div>
    </div>,
    document.body,
  );
}
