export type ResourceKind =
  | "video"
  | "image"
  | "audio"
  | "document"
  | "archive"
  | "software"
  | "unknown";

export interface ResourceAnalysis {
  schema_version: number;
  resource_kind: ResourceKind;
  category: string;
  category_label: string;
  subtype: string;
  subtype_label: string;
  status:
    | "complete"
    | "needs_review"
    | "legacy"
    | "unanalysed"
    | "failed"
    | "skipped";
  confidence: number | null;
  summary: string;
  source_usage?: { publisher_text_shared: boolean };
  summary_sentences?: { text: string; evidence_refs: string[]; event_refs?: string[] }[];
  narrative?: {
    version: number;
    mode: "story" | "activity" | "insufficient";
    issues: string[];
    events: { id: string; phase: string; action: string; start: number; end: number; evidence_refs: string[] }[];
  };
  summary_quality?: {
    version: number;
    status: "ready" | "review";
    issues: string[];
    audit?: { version?: number; status: "passed" | "review" | "failed"; issues: string[] };
    evidence_catalog: { id: string; kind: string; origin?: "subtitle"; timestamp?: number; start?: number; end?: number }[];
  };
  content_description?: string;
  observations?: { text: string; sources: string[] }[];
  sampling?: {
    method?: string;
    analysis_depth?: "standard" | "deep";
    subtitles?: { origin: string; available_cues: number; selected_cues: number };
    duration_seconds?: number;
    candidate_count?: number;
    timestamps?: number[];
    timeline_windows?: { start: number; end: number; frame_count: number }[];
    audio?: { method?: string; reliable_clips?: number; attempted_seconds?: number; sampled_seconds?: number; windows?: { start: number; end: number; status: string }[] };
  };
  directions: string[];
  facets: Record<string, string>;
  tags: string[];
  evidence_sources: string[];
  limitations: string[];
  analyzed_at?: string;
  reason?: string;
  review_note?: string;
  failure_stage?: string;
  retryable?: boolean;
  diagnostics?: {
    classification_reused?: boolean;
    classification_analyzed_at?: string;
    threshold?: number;
    stage?: string;
    attempts: {
      stage: string;
      attempt: number;
      timeout_seconds: number;
      elapsed_ms: number;
      status: "success" | "failed";
      reason?: string;
      http_status?: number;
    }[];
  };
}

export interface FolderEntry {
  name: string;
  path: string;
}
export interface LibraryActivity {
  favorite?: boolean;
  favorited_at?: string;
  opened_at?: string;
  watched_at?: string;
  position?: number;
  duration?: number;
  completed?: boolean;
}
export interface ResourceEntry extends FolderEntry {
  source_context?: {
    original_name?: string;
    caption?: string;
    text?: string;
    association?: "message" | "media_group";
  };
  size: number;
  modified: string;
  content_type: string;
  analysis: ResourceAnalysis;
  activity?: LibraryActivity;
}
export interface Category {
  id: string;
  label: string;
  group: string;
  kinds: ResourceKind[];
  directions: string[];
  hint: string;
}
export interface Taxonomy {
  categories: Category[];
  adult_subtypes: Category[];
  resource_kinds: Record<ResourceKind, string>;
}
export interface AnalysisJob {
  status?: "queued" | "running" | "done" | "failed";
  message?: string;
}
export interface LibraryResponse {
  files: ResourceEntry[];
  folders?: FolderEntry[];
  root: string;
  total?: number;
  indexed_total?: number;
  page?: number;
  pages?: number;
  counts?: {
    categories: Record<string, number>;
    kinds: Record<string, number>;
    subtypes: Record<string, number>;
  };
  tag_facets?: { name: string; count: number }[];
  paused?: boolean;
  scope_id?: string;
}

export function formatDuration(value?: number): string {
  if (value == null || !Number.isFinite(value) || value < 0) return "—";
  const seconds = Math.floor(value);
  return `${seconds >= 3600 ? Math.floor(seconds / 3600) + ":" : ""}${String(Math.floor(seconds / 60) % 60).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

export function historyDay(value?: string): string {
  if (!value || Number.isNaN(Date.parse(value))) return "时间未知";
  const date = new Date(value);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  return date.toDateString() === today.toDateString()
    ? "今天"
    : date.toDateString() === yesterday.toDateString()
      ? "昨天"
      : date.toLocaleDateString();
}

export const statusLabels: Record<ResourceAnalysis["status"], string> = {
  complete: "已分析",
  needs_review: "待确认",
  legacy: "旧版待更新",
  unanalysed: "未分析",
  failed: "分析失败",
  skipped: "未启用",
};
export const kindLabels: Record<ResourceKind, string> = {
  video: "视频",
  image: "图片",
  audio: "音频",
  document: "文档",
  archive: "压缩包",
  software: "软件",
  unknown: "其他文件",
};

export function previewUrl(
  root: string,
  path: string,
  download = false,
): string {
  const query = new URLSearchParams({
    path:
      "/" +
      [root.replace(/^\/+|\/+$/g, ""), path.replace(/^\/+/, "")]
        .filter(Boolean)
        .join("/"),
  });
  if (download) query.set("download", "1");
  return `/api/webdav/preview?${query}`;
}

export function coverUrl(root: string, videoPath: string): string {
  return previewUrl(root, videoPath + ".cover.jpg");
}

export function parentPath(path: string): string {
  return path.split("/").filter(Boolean).slice(0, -1).join("/");
}

export function formatSize(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  const index = Math.min(4, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** index).toFixed(index ? 1 : 0)} ${["B", "KB", "MB", "GB", "TB"][index]}`;
}

const activityLabels = new Set([
  "参与者进行成人亲密互动", "参与者进行交谈", "参与者进行按摩",
  "参与者表演舞蹈", "参与者进行舞台表演", "参与者面向镜头展示", "参与者进行角色扮演",
]);

export function hasCategoryOnlySynopsis(analysis: ResourceAnalysis): boolean {
  return analysis.resource_kind === "video" && analysis.status === "complete" &&
    (analysis.summary_quality?.issues?.includes("category_only_summary") === true ||
      (analysis.category === "adult" && activityLabels.has((analysis.summary || "").replace(/[\s。.!！?？]/g, ""))));
}

export function filterDirectory(
  files: ResourceEntry[],
  query: string,
  kind: string,
  status: string,
  sort: string,
): ResourceEntry[] {
  const q = query.trim().toLocaleLowerCase();
  const filtered = files.filter(
    (entry) =>
      (!kind || entry.analysis.resource_kind === kind) &&
      (!status ||
        (status === "review"
          ? entry.analysis.status !== "complete" || entry.analysis.summary_quality?.status === "review" || hasCategoryOnlySynopsis(entry.analysis)
          : entry.analysis.status === status && !(status === "complete" && (entry.analysis.summary_quality?.status === "review" || hasCategoryOnlySynopsis(entry.analysis))))) &&
      (!q ||
        [
          entry.name,
          entry.analysis.summary,
          entry.analysis.content_description,
          ...entry.analysis.tags,
        ]
          .join(" ")
          .toLocaleLowerCase()
          .includes(q)),
  );
  return filtered.sort((a, b) =>
    sort === "size"
      ? b.size - a.size
      : sort === "name"
        ? a.name.localeCompare(b.name, "zh-CN", { numeric: true })
        : (Date.parse(b.modified) || 0) - (Date.parse(a.modified) || 0),
  );
}
