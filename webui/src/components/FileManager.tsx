import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Heart,
  History,
  Grid2X2,
  Search,
  X,
  EyeOff,
  Eye,
  FolderOpen,
  RefreshCw,
  ChevronLeft,
  ChevronRight,
  Info,
  Play,
  Trash2,
  Pause,
  SlidersHorizontal,
} from "lucide-react";
import { libraryRequest } from "../library/api";
import { useLibraryActivity } from "../library/activity";
import {
  LibraryResponse,
  ResourceEntry,
  Taxonomy,
  formatSize,
  formatDuration,
  historyDay,
  kindLabels,
  parentPath,
} from "../library/model";
import { ResourceCover, CoverScope } from "../library/ResourceCover";
import { HoverPreview } from "../library/HoverPreview";
import { ResourceDetails, ResourcePreview } from "../library/ResourceDetails";
import { ResourceDeleteDialog } from "../library/ResourceDeleteDialog";
import { DirectoryBrowser } from "../library/DirectoryBrowser";
import { Modal } from "./ui/Modal";

type Scope = "adult" | "movie" | "other";
type View = "all" | "favorites" | "history";
interface Filters {
  view: View;
  category: string;
  subtype: string;
  kind: string;
  status: string;
  tags: string[];
  search: string;
  tagSearch: string;
  sort: string;
  page: number;
  historyKind: "opened" | "watched";
  scroll: number;
}
const defaults = (): Filters => ({
  view: "all",
  category: "",
  subtype: "",
  kind: "",
  status: "",
  tags: [],
  search: "",
  tagSearch: "",
  sort: "newest",
  page: 1,
  historyKind: "opened",
  scroll: 0,
});
// Navigation continuity is memory-only; favorites/history live on the server.
let saved = {
  scope: "adult" as Scope,
  filters: { adult: defaults(), movie: defaults(), other: defaults() },
};
function useDebounced(value: string) {
  const [result, setResult] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setResult(value), 220);
    return () => clearTimeout(timer);
  }, [value]);
  return result;
}

export function FileManager() {
  const [scope, setScope] = useState<Scope>(saved.scope);
  const [filters, setFilters] = useState(saved.filters);
  const filter = filters[scope];
  const [taxonomy, setTaxonomy] = useState<Taxonomy | null>(null);
  const [taxonomyError, setTaxonomyError] = useState("");
  const [data, setData] = useState<LibraryResponse>({ files: [], root: "" });
  const [loaded, setLoaded] = useState("");
  const [loadedScope, setLoadedScope] = useState<Scope | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [filterPanel, setFilterPanel] = useState(false);
  const [privateMode, setPrivateMode] = useState(
    () => localStorage.getItem("library_privacy") === "true",
  );
  const [directory, setDirectory] = useState<string | null>(null);
  const [detail, setDetail] = useState<{
    entry: ResourceEntry;
    root: string;
  } | null>(null);
  const [playing, setPlaying] = useState(false);
  const [deleting, setDeleting] = useState<ResourceEntry | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [hover, setHover] = useState<{
    entry: ResourceEntry;
    anchor: HTMLElement;
  } | null>(null);
  const timers = useRef<{
    enter?: ReturnType<typeof setTimeout>;
    leave?: ReturnType<typeof setTimeout>;
  }>({});
  const scroll = useRef<HTMLDivElement>(null);
  const focusedCard = useRef<HTMLElement | null>(null);
  const scrollPosition = useRef(filter.scroll);
  const activity = useLibraryActivity();
  const query = useDebounced(filter.search);
  const tagQuery = useDebounced(filter.tagSearch);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  const closeHover = useCallback(() => {
    clearTimeout(timers.current.enter);
    clearTimeout(timers.current.leave);
    setHover(null);
  }, []);
  const askDelete = (entry: ResourceEntry) => {
    closeHover();
    setPlaying(false);
    setDetail(null);
    setDirectory(null);
    setDeleting(entry);
  };
  const keepHover = () => {
    clearTimeout(timers.current.enter);
    clearTimeout(timers.current.leave);
  };
  const leaveHover = () => {
    clearTimeout(timers.current.enter);
    clearTimeout(timers.current.leave);
    timers.current.leave = setTimeout(() => setHover(null), 220);
  };
  const scheduleHover = (
    entry: ResourceEntry,
    anchor: HTMLElement,
    immediate = false,
  ) => {
    keepHover();
    if (immediate) setHover({ entry, anchor });
    else
      timers.current.enter = setTimeout(() => setHover({ entry, anchor }), 320);
  };
  const update = (patch: Partial<Filters>) => {
    closeHover();
    scrollPosition.current = 0;
    setFilters((values) => ({
      ...values,
      [scope]: { ...values[scope], page: 1, scroll: 0, ...patch },
    }));
  };
  const switchScope = (next: Scope) => {
    closeHover();
    setFilters((values) => ({
      ...values,
      [scope]: { ...values[scope], scroll: scrollPosition.current },
    }));
    scrollPosition.current = filters[next].scroll;
    setScope(next);
  };
  const toggleTag = (tag: string) =>
    update({
      tags: filter.tags.includes(tag)
        ? filter.tags.filter((item) => item !== tag)
        : [...filter.tags, tag],
    });
  const snapshot = useRef(saved);
  snapshot.current = { scope, filters };
  useEffect(
    () => () => {
      const current = snapshot.current;
      saved = {
        ...current,
        filters: {
          ...current.filters,
          [current.scope]: {
            ...current.filters[current.scope],
            scroll: scrollPosition.current,
          },
        },
      };
      clearTimeout(timers.current.enter);
      clearTimeout(timers.current.leave);
    },
    [],
  );
  useEffect(() => {
    localStorage.setItem("library_privacy", String(privateMode));
  }, [privateMode]);
  useEffect(() => {
    const controller = new AbortController();
    libraryRequest<Taxonomy>("/api/library/taxonomy", {
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) {
          setTaxonomy(value);
          setTaxonomyError("");
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setTaxonomyError(reason.message);
      });
    return () => controller.abort();
  }, [revision]);
  const endpoint = useMemo(() => {
    const params = new URLSearchParams({
      scope,
      view: filter.view,
      category: filter.category,
      subtype: filter.subtype,
      kind: filter.kind,
      status: filter.status,
      q: query,
      tag_q: tagQuery,
      sort: filter.sort,
      page: String(filter.page),
      page_size: "48",
      history_kind: filter.historyKind,
    });
    filter.tags.forEach((tag) => params.append("tag", tag));
    return "/api/library?" + params;
  }, [scope, filter, query, tagQuery]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    closeHover();
    const epoch = activity.version.current;
    libraryRequest<LibraryResponse>(endpoint, { signal: controller.signal })
      .then((result) => {
        if (controller.signal.aborted) return;
        setData(result);
        setLoadedScope(scope);
        setLoaded(endpoint);
        activity.hydrate(result.files, !!result.paused, epoch);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setError(reason.message);
          setLoaded(endpoint);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [endpoint, revision, closeHover, activity.hydrate]);
  const pending =
    loading ||
    loaded !== endpoint ||
    query !== filter.search ||
    tagQuery !== filter.tagSearch;
  useEffect(() => {
    if (!pending && scroll.current)
      scroll.current.scrollTop = scrollPosition.current;
  }, [pending]);
  const resolved = (entry: ResourceEntry): ResourceEntry => ({
    ...entry,
    activity: activity.overrides[entry.path] ?? entry.activity,
  });
  const favorite = async (entry: ResourceEntry) => {
    if (
      await activity.mutate({
        action: "favorite",
        path: entry.path,
        value: !resolved(entry).activity?.favorite,
      })
    ) {
      if (filter.view === "favorites") refresh();
    }
  };
  const open = (entry: ResourceEntry, root = data.root, play = false) => {
    focusedCard.current = document.activeElement as HTMLElement;
    closeHover();
    setDirectory(null);
    setDetail({ entry, root });
    setPlaying(play);
    void activity.mutate({ action: "open", path: entry.path });
  };
  const closeDetail = () => {
    setDetail(null);
    setPlaying(false);
    if (filter.view === "history") refresh();
    requestAnimationFrame(() => {
      if (focusedCard.current?.isConnected) focusedCard.current.focus();
    });
  };
  const clearFilters = () =>
    update({
      category: "",
      subtype: "",
      kind: "",
      status: "",
      search: "",
      tagSearch: "",
      tags: [],
    });
  const categories =
    scope === "adult"
      ? taxonomy?.adult_subtypes || []
      : scope === "other"
        ? taxonomy?.categories.filter(
            (item) => !["adult", "movie"].includes(item.id),
          ) || []
        : [];
  const activeCategory = scope === "adult" ? filter.subtype : filter.category;
  const hasFilters = !!(
    activeCategory ||
    filter.kind ||
    filter.status ||
    filter.tags.length ||
    filter.search
  );
  const tagFacets = loadedScope === scope ? data.tag_facets || [] : [];
  const visibleTags = tagFacets.filter(
    (tag) => !filter.tags.includes(tag.name),
  );
  const extraFilterCount = Number(!!filter.kind) + Number(!!filter.status);
  const files = data.files.map(resolved);
  const groups = new Map<string, ResourceEntry[]>();
  if (filter.view === "history")
    for (const entry of files) {
      const day = historyDay(
        entry.activity?.[
          filter.historyKind === "opened" ? "opened_at" : "watched_at"
        ],
      );
      groups.set(day, [...(groups.get(day) || []), entry]);
    }
  const favoriteButton = (entry: ResourceEntry) => (
    <button
      className="poster-favorite"
      disabled={activity.pending.has(entry.path)}
      aria-label={
        (entry.activity?.favorite ? "取消收藏 " : "收藏 ") +
        (privateMode ? "此资源" : entry.name)
      }
      aria-pressed={!!entry.activity?.favorite}
      onClick={() => void favorite(entry)}
    >
      <Heart
        size={21}
        fill={entry.activity?.favorite ? "currentColor" : "none"}
      />
    </button>
  );

  return (
    <CoverScope.Provider value={data.scope_id || data.root}>
      <div className="cinema-library" data-scope={scope}>
        <header className="cinema-header">
          <div className="cinema-primary">
            <nav className="library-scopes" aria-label="片库切换">
              {(
                [
                  ["adult", "NSFW"],
                  ["movie", "电影"],
                  ["other", "其他资源"],
                ] as const
              ).map(([id, name]) => (
                <button
                  key={id}
                  aria-pressed={scope === id}
                  onClick={() => switchScope(id)}
                >
                  {name}
                </button>
              ))}
            </nav>
            <label className="cinema-search">
              <Search size={19} />
              <input
                aria-label="搜索资源"
                placeholder="搜索标题、标签、文件名"
                value={filter.search}
                onChange={(event) => update({ search: event.target.value })}
              />
              {filter.search && (
                <button
                  className="icon-button"
                  aria-label="清空搜索"
                  onClick={() => update({ search: "" })}
                >
                  <X size={16} />
                </button>
              )}
            </label>
            <nav aria-label="资源视图" className="cinema-views">
              {(
                [
                  { id: "all", label: "全部资源", Icon: Grid2X2 },
                  { id: "favorites", label: "收藏", Icon: Heart },
                  { id: "history", label: "历史记录", Icon: History },
                ] as const
              ).map(({ id, label, Icon }) => (
                <button
                  key={id}
                  aria-label={label}
                  title={label}
                  aria-current={filter.view === id ? "page" : undefined}
                  onClick={() => update({ view: id })}
                >
                  <Icon size={18} />
                  <span>{label}</span>
                </button>
              ))}
            </nav>
            <span className="result-count" aria-live="polite">
              {pending ? "读取中…" : (data.total || 0) + " 项"}
            </span>
            <div className="library-tools">
              {hasFilters && (
                <button
                  className="icon-button clear-filters"
                  aria-label="清除筛选"
                  title="清除筛选"
                  onClick={clearFilters}
                >
                  <X size={17} />
                </button>
              )}
              <button
                className="icon-button privacy-toggle"
                aria-label="隐私模式"
                title={privateMode ? "关闭隐私模式" : "开启隐私模式"}
                aria-pressed={privateMode}
                onClick={() => {
                  closeHover();
                  setPrivateMode((value) => !value);
                }}
              >
                {privateMode ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
              <button
                className="icon-button library-filter-toggle"
                aria-label="筛选与排序"
                title="筛选、排序与来源目录"
                aria-haspopup="dialog"
                aria-expanded={filterPanel}
                onClick={() => {
                  closeHover();
                  setFilterPanel(true);
                }}
              >
                <SlidersHorizontal size={18} />
                {extraFilterCount > 0 && (
                  <span className="filter-badge">{extraFilterCount}</span>
                )}
              </button>
            </div>
          </div>
          <section className="quick-filters" aria-label="分类和标签筛选">
            {categories.length > 0 && (
              <div className="quick-filter-row">
                <span className="filter-caption">分类</span>
                <div className="filter-options categories">
                  <button
                    className="filter-chip"
                    aria-pressed={!activeCategory}
                    onClick={() => update({ subtype: "", category: "" })}
                  >
                    全部
                  </button>
                  {categories.map((item) => (
                    <button
                      key={item.id}
                      className="filter-chip"
                      aria-pressed={activeCategory === item.id}
                      onClick={() =>
                        update(
                          scope === "adult"
                            ? {
                                subtype:
                                  activeCategory === item.id ? "" : item.id,
                              }
                            : {
                                category:
                                  activeCategory === item.id ? "" : item.id,
                              },
                        )
                      }
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {(tagFacets.length > 0 ||
              filter.tags.length > 0 ||
              filter.tagSearch) && (
              <div className="quick-filter-row">
                <span className="filter-caption">标签</span>
                <div className="filter-options tags">
                  {filter.tags.map((tag) => (
                    <button
                      key={tag}
                      className="filter-chip"
                      aria-pressed="true"
                      aria-label={"移除标签 " + tag}
                      onClick={() => toggleTag(tag)}
                    >
                      {tag}
                      <X size={13} />
                    </button>
                  ))}
                  {visibleTags.slice(0, 8).map((tag) => (
                    <button
                      key={tag.name}
                      className="filter-chip"
                      aria-pressed={filter.tags.includes(tag.name)}
                      onClick={() => toggleTag(tag.name)}
                    >
                      {tag.name}
                      <small>{tag.count}</small>
                    </button>
                  ))}
                  {!pending && filter.tagSearch && !tagFacets.length && (
                    <span className="filter-hint">没有匹配的标签</span>
                  )}
                  <button
                    className="text-button tag-search-toggle"
                    onClick={() => {
                      closeHover();
                      setFilterPanel(true);
                    }}
                  >
                    <Search size={14} />
                    {visibleTags.length > 8 ? "更多标签" : "查找标签"}
                  </button>
                </div>
              </div>
            )}
          </section>
          {filter.view === "history" && (
            <div className="history-toolbar">
              <div className="history-tabs">
                <button
                  aria-pressed={filter.historyKind === "opened"}
                  onClick={() => update({ historyKind: "opened" })}
                >
                  最近打开
                </button>
                <button
                  aria-pressed={filter.historyKind === "watched"}
                  onClick={() => update({ historyKind: "watched" })}
                >
                  观看记录
                </button>
              </div>
              <button
                className="button"
                disabled={activity.pending.has("pause")}
                aria-pressed={activity.paused}
                onClick={() =>
                  void activity.mutate({
                    action: "pause",
                    value: !activity.paused,
                  })
                }
              >
                {activity.paused ? <Play size={15} /> : <Pause size={15} />}{" "}
                {activity.paused ? "恢复记录" : "暂停记录"}
              </button>
              <button className="button" onClick={() => setConfirmClear(true)}>
                <Trash2 size={15} />
                清空记录
              </button>
            </div>
          )}
          {(taxonomyError || activity.error) && (
            <p role="alert" className="notice error">
              {taxonomyError || activity.error}
              <button
                className="text-button"
                onClick={() => {
                  activity.setError("");
                  refresh();
                }}
              >
                重新读取
              </button>
            </p>
          )}
          {activity.paused && (
            <p className="history-paused" role="status">
              历史记录已暂停，收藏不受影响。
              <button
                className="text-button"
                onClick={() =>
                  void activity.mutate({ action: "pause", value: false })
                }
              >
                恢复记录
              </button>
            </p>
          )}
        </header>
        <div
          className="cinema-scroll"
          ref={scroll}
          aria-label="资源列表"
          aria-busy={pending}
          onScroll={(event) => {
            if (!pending)
              scrollPosition.current = event.currentTarget.scrollTop;
          }}
        >
          {error ? (
            <div className="empty-state" role="alert">
              <FolderOpen size={36} />
              <h2>暂时无法读取资源</h2>
              <p>{error}</p>
              <button className="button primary" onClick={refresh}>
                重新加载
              </button>
            </div>
          ) : pending ? (
            <div
              className="poster-grid"
              role="status"
              aria-label="正在读取资源"
            >
              {Array.from({ length: 12 }, (_, index) => (
                <div key={index} className="poster-skeleton" />
              ))}
            </div>
          ) : !files.length ? (
            <div className="empty-state">
              <FolderOpen size={38} />
              <h2>
                {hasFilters
                  ? "没有匹配的资源"
                  : filter.view === "favorites"
                    ? "还没有收藏"
                    : filter.view === "history"
                      ? "还没有历史记录"
                      : "此片库还没有资源"}
              </h2>
              <p>
                {filter.view === "favorites"
                  ? "点击卡片上的爱心收藏，之后可在这里继续筛选。"
                  : filter.view === "history"
                    ? "打开详情或播放后记录会出现在这里；悬停不会留下记录。"
                    : "从来源目录打开已有文件并分析，资源会按对应分类归档。"}
              </p>
              <button
                className="button"
                onClick={hasFilters ? clearFilters : () => setDirectory("")}
              >
                {hasFilters ? "清除筛选" : "浏览来源目录"}
              </button>
            </div>
          ) : filter.view === "history" ? (
            <div className="history-groups">
              {Array.from(groups).map(([day, items]) => (
                <section key={day}>
                  <h2>{day}</h2>
                  {items.map((entry) => (
                    <article className="history-entry" key={entry.path}>
                      <button
                        className="history-cover"
                        aria-label={
                          "打开 " + (privateMode ? "此资源" : entry.name)
                        }
                        onClick={() => open(entry)}
                      >
                        <ResourceCover
                          entry={entry}
                          root={data.root}
                          hidden={privateMode}
                        />
                      </button>
                      <div className="history-main">
                        <button
                          className="history-title"
                          title={privateMode ? undefined : entry.name}
                          onClick={() => open(entry)}
                        >
                          {privateMode ? "内容已隐藏" : entry.name}
                        </button>
                        <div className="poster-meta">
                          <span>
                            {entry.analysis.category === "adult"
                              ? "NSFW"
                              : entry.analysis.category_label}
                          </span>
                          <span>
                            {kindLabels[entry.analysis.resource_kind]}
                          </span>
                          <span>{formatSize(entry.size)}</span>
                        </div>
                        {filter.historyKind === "watched" && (
                          <div className="history-progress">
                            <progress
                              value={entry.activity?.position || 0}
                              max={entry.activity?.duration || 1}
                            />
                            <span>
                              {entry.activity?.completed
                                ? "已看完"
                                : formatDuration(entry.activity?.position) +
                                  " / " +
                                  formatDuration(entry.activity?.duration)}
                            </span>
                          </div>
                        )}
                      </div>
                      <button
                        className="button resume-button"
                        onClick={() =>
                          open(
                            entry,
                            data.root,
                            filter.historyKind === "watched",
                          )
                        }
                      >
                        <Play size={16} />
                        {filter.historyKind === "opened"
                          ? "打开详情"
                          : entry.activity?.completed
                            ? "重新观看"
                            : "继续观看"}
                      </button>
                      <time>
                        {new Date(
                          entry.activity?.[
                            filter.historyKind === "opened"
                              ? "opened_at"
                              : "watched_at"
                          ] || "",
                        ).toLocaleTimeString([], {
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </time>
                      {favoriteButton(entry)}
                      <button
                        className="icon-button remove-record"
                        aria-label={
                          "移除记录 " + (privateMode ? "此资源" : entry.name)
                        }
                        disabled={activity.pending.has(entry.path)}
                        onClick={async () => {
                          if (
                            await activity.mutate({
                              action: "remove",
                              path: entry.path,
                              history_kind: filter.historyKind,
                              scope,
                            })
                          )
                            refresh();
                        }}
                      >
                        <Trash2 size={17} />
                      </button>
                    </article>
                  ))}
                </section>
              ))}
            </div>
          ) : (
            <div className="poster-grid">
              {files.map((entry) => (
                <article
                  key={entry.path}
                  className={
                    "poster-card" +
                    (hover?.entry.path === entry.path ? " is-hovered" : "")
                  }
                  onPointerEnter={(event) => {
                    if (event.pointerType === "mouse")
                      scheduleHover(entry, event.currentTarget);
                  }}
                  onPointerLeave={leaveHover}
                >
                  <button
                    className="poster-open"
                    title={privateMode ? undefined : entry.name}
                    onClick={() => open(entry)}
                  >
                    <ResourceCover
                      entry={entry}
                      root={data.root}
                      hidden={privateMode}
                    />
                    <div className="poster-copy">
                      <h2>{privateMode ? "内容已隐藏" : entry.name}</h2>
                      <span
                        className={
                          "poster-status" +
                          (entry.analysis.status === "complete" && !synopsisView(entry.analysis).needsUpdate
                            ? ""
                            : " review")
                        }
                      >
                        {entry.analysis.subtype_label ||
                          entry.analysis.category_label}
                        {entry.analysis.status !== "complete"
                          ? " · 待核对"
                          : synopsisView(entry.analysis).needsUpdate
                            ? ` · ${synopsisView(entry.analysis).stateLabel.replace("分类完成·", "")}`
                            : ""}
                      </span>
                    </div>
                  </button>
                  {favoriteButton(entry)}
                  <button
                    className="poster-delete"
                    aria-label={
                      "删除资源 " + (privateMode ? "此资源" : entry.name)
                    }
                    aria-haspopup="dialog"
                    disabled={privateMode}
                    title={
                      privateMode
                        ? "关闭隐私模式后才能核对删除范围"
                        : "删除资源及封面"
                    }
                    onPointerEnter={closeHover}
                    onClick={() => askDelete(entry)}
                  >
                    <Trash2 size={16} />
                  </button>
                  <button
                    className="poster-info"
                    aria-label={
                      "查看简介 " + (privateMode ? "此资源" : entry.name)
                    }
                    aria-haspopup="dialog"
                    onClick={(event) =>
                      scheduleHover(
                        entry,
                        event.currentTarget.closest("article")!,
                        true,
                      )
                    }
                  >
                    <Info size={19} />
                  </button>
                </article>
              ))}
            </div>
          )}
        </div>
        {(data.pages || 1) > 1 && (
          <footer className="cinema-footer">
            <div className="button-row">
              <button
                className="icon-button"
                aria-label="上一页资源"
                disabled={pending || (data.page || 1) <= 1}
                onClick={() => update({ page: (data.page || 1) - 1 })}
              >
                <ChevronLeft size={18} />
              </button>
              <span>
                {data.page || 1} / {data.pages || 1}
              </span>
              <button
                className="icon-button"
                aria-label="下一页资源"
                disabled={pending || (data.page || 1) >= (data.pages || 1)}
                onClick={() => update({ page: (data.page || 1) + 1 })}
              >
                <ChevronRight size={18} />
              </button>
            </div>
          </footer>
        )}
        {filterPanel && (
          <Modal title="筛选与排序" onClose={() => setFilterPanel(false)}>
            <div className="library-filter-panel">
              <div className="library-filter-fields">
                <label>
                  资源格式
                  <select
                    aria-label="资源类型"
                    value={filter.kind}
                    onChange={(event) => update({ kind: event.target.value })}
                  >
                    <option value="">全部格式</option>
                    {Object.entries(kindLabels).map(([id, label]) => (
                      <option key={id} value={id}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  分析状态
                  <select
                    aria-label="分析状态"
                    value={filter.status}
                    onChange={(event) => update({ status: event.target.value })}
                  >
                    <option value="">全部分析状态</option>
                    <option value="complete">已分析</option>
                    <option value="review">待核对 / 未分析</option>
                    <option value="failed">分析失败</option>
                  </select>
                </label>
                {filter.view !== "history" && (
                  <label>
                    排序
                    <select
                      aria-label="排序方式"
                      value={filter.sort}
                      onChange={(event) => update({ sort: event.target.value })}
                    >
                      <option value="newest">
                        {filter.view === "favorites" ? "最近收藏" : "最近加入"}
                      </option>
                      <option value="name">名称排序</option>
                      <option value="size">大小排序</option>
                    </select>
                  </label>
                )}
              </div>
              <label className="library-tag-search">
                标签 · 同时匹配
                <input
                  aria-label="搜索标签"
                  placeholder="输入标签名称"
                  value={filter.tagSearch}
                  onChange={(event) =>
                    update({ tagSearch: event.target.value })
                  }
                />
              </label>
              <div className="library-tag-picker">
                {filter.tags.map((tag) => (
                  <button
                    key={tag}
                    className="filter-chip"
                    aria-pressed="true"
                    onClick={() => toggleTag(tag)}
                  >
                    {tag}
                    <X size={13} />
                  </button>
                ))}
                {visibleTags.map((tag) => (
                  <button
                    key={tag.name}
                    className="filter-chip"
                    aria-pressed="false"
                    onClick={() => toggleTag(tag.name)}
                  >
                    {tag.name}
                    <small>{tag.count}</small>
                  </button>
                ))}
                {!pending && !tagFacets.length && (
                  <span className="filter-hint">
                    {filter.tagSearch
                      ? "没有匹配的标签"
                      : "当前资源暂无已分析标签"}
                  </span>
                )}
              </div>
              <div className="library-source-actions">
                <button
                  className="button"
                  aria-label="来源目录"
                  onClick={() => {
                    setFilterPanel(false);
                    setDirectory("");
                  }}
                >
                  <FolderOpen size={17} />
                  来源目录
                </button>
                <button
                  className="button"
                  aria-label="刷新资源库"
                  onClick={() => {
                    refresh();
                    setFilterPanel(false);
                  }}
                >
                  <RefreshCw size={17} />
                  刷新资源库
                </button>
              </div>
              <div className="dialog-actions">
                <button className="button" onClick={clearFilters}>
                  重置筛选
                </button>
                <button
                  className="button primary"
                  onClick={() => setFilterPanel(false)}
                >
                  {pending ? "读取中…" : `查看 ${data.total || 0} 项资源`}
                </button>
              </div>
            </div>
          </Modal>
        )}
        {hover && !pending && !detail && !filterPanel && !deleting && (
          <HoverPreview
            entry={resolved(hover.entry)}
            root={data.root}
            anchor={hover.anchor}
            privateMode={privateMode}
            busy={activity.pending.has(hover.entry.path)}
            onEnter={keepHover}
            onLeave={leaveHover}
            onClose={closeHover}
            onOpen={() => open(hover.entry)}
            onFavorite={() => void favorite(hover.entry)}
          />
        )}
        {directory !== null && (
          <DirectoryBrowser
            initialPath={directory}
            onClose={() => setDirectory(null)}
            onOpen={open}
          />
        )}
        {detail && privateMode ? (
          <Modal title="隐私模式" onClose={closeDetail}>
            <p>封面与内容简介已隐藏。只有主动关闭隐私模式后才显示内容。</p>
            <div className="dialog-actions">
              <button
                className="button primary"
                onClick={() => setPrivateMode(false)}
              >
                关闭隐私模式并查看
              </button>
            </div>
          </Modal>
        ) : detail && playing ? (
          <ResourcePreview
            entry={resolved(detail.entry)}
            root={detail.root}
            onClose={() => setPlaying(false)}
            progressError={activity.error}
            onProgress={(position, duration) => {
              if (!activity.paused)
                void activity.mutate({
                  action: "progress",
                  path: detail.entry.path,
                  position,
                  duration,
                });
            }}
          />
        ) : (
          detail && (
            <Modal title="资源详情" wide onClose={closeDetail}>
              {activity.error && (
                <p className="notice error" role="alert">
                  {activity.error}
                </p>
              )}
              <ResourceDetails
                entry={resolved(detail.entry)}
                root={detail.root}
                onClose={closeDetail}
                onPreview={() => setPlaying(true)}
                onChanged={refresh}
                onFavorite={() => void favorite(detail.entry)}
                favoriteBusy={activity.pending.has(detail.entry.path)}
                onDelete={() => askDelete(detail.entry)}
                onLocate={() => {
                  setDirectory(parentPath(detail.entry.path));
                  setDetail(null);
                }}
              />
            </Modal>
          )
        )}
        {deleting && (
          <ResourceDeleteDialog
            entry={deleting}
            scopeId={data.scope_id || ""}
            onClose={() => setDeleting(null)}
            onDeleted={() => {
              activity.forget(deleting.path);
              setData((value) => ({
                ...value,
                files: value.files.filter(
                  (entry) => entry.path !== deleting.path,
                ),
              }));
              refresh();
            }}
          />
        )}
        {confirmClear && (
          <Modal
            title="清空历史记录"
            busy={activity.pending.has("clear")}
            onClose={() => setConfirmClear(false)}
          >
            <p>
              清空当前
              {scope === "adult"
                ? " NSFW "
                : scope === "movie"
                  ? "电影"
                  : "其他资源"}
              库的全部{filter.historyKind === "opened" ? "最近打开" : "观看"}
              记录？包括当前筛选条件以外的记录。
            </p>
            <p className="muted">不会删除媒体文件或收藏；操作不可撤销。</p>
            <div className="dialog-actions">
              <button className="button" onClick={() => setConfirmClear(false)}>
                取消
              </button>
              <button
                className="button danger"
                disabled={activity.pending.has("clear")}
                onClick={async () => {
                  if (
                    await activity.mutate({
                      action: "clear",
                      history_kind: filter.historyKind,
                      scope,
                    })
                  ) {
                    setConfirmClear(false);
                    refresh();
                  }
                }}
              >
                确认清空
              </button>
            </div>
            {activity.error && (
              <p className="notice error" role="alert">
                {activity.error}
              </p>
            )}
          </Modal>
        )}
      </div>
    </CoverScope.Provider>
  );
}
import { synopsisView } from "../library/synopsis";
