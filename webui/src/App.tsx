/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useMemo, useState, useRef } from "react";
import { fetchBootstrap } from "./bootstrap";
import {
  Activity,
  CloudLightning,
  FolderOpen,
  Info,
  Moon,
  Settings2,
  Sun,
  Users,
  X,
} from "lucide-react";
import { AccountManager } from "./components/AccountManager";
import { ConfigPanel } from "./components/ConfigPanel";
import { FileManager } from "./components/FileManager";
import { TaskTable } from "./components/TaskTable";
import { backendProgress, buildDashboardTasks, statusFromBackendState } from "./taskModel";
import {
  BotAccessConfig,
  BotStatusConfig,
  CloudStorageConfig,
  MediaType,
  SyncRule,
  SyncTask,
  TelegramAccount,
  VideoClassifierConfig,
  defaultVideoClassifier,
} from "./types";

type ActiveTab = "dashboard" | "files" | "config" | "accounts";

interface BackendTask {
  chat: string;
  id: string;
  profile_id?: string | null;
  profileId?: string | null;
  filename: string;
  total_size: string;
  download_progress: string;
  upload_progress: string;
  download_speed?: string;
  upload_speed?: string;
  save_path?: string;
  remote_path?: string;
  relative_path?: string;
  created_at?: string;
  completed_at?: string | null;
  created_ts?: number;
  completed_ts?: number | null;
  state?: string;
  status?: string;
  phase?: string;
  phase_label?: string;
  ai_status?: string;
  ai_summary?: string;
  ai_tags?: string[];
  error?: string;
}

function backendTaskProfile(item: BackendTask): string | null {
  return item.profile_id || item.profileId || null;
}

function backendTaskId(item: BackendTask): string {
  const profileId = backendTaskProfile(item);
  return profileId
    ? `${profileId}:${item.chat}:${item.id}`
    : `${item.chat}:${item.id}`;
}

interface BootstrapPayload {
  version: string;
  config: Record<string, any>;
  account: {
    logged_in: boolean;
    session_exists: boolean;
    account: TelegramAccount | null;
    accounts?: TelegramAccount[];
  };
}

const defaultCloudConfig: CloudStorageConfig = {
  type: "webdav",
  url: "",
  username: "",
  password: "",
  remoteDir: "/TelegramBackup",
  downloadRateLimitKb: 0,
  uploadRateLimitKb: 0,
};

const defaultRule: SyncRule = {
  id: "rule-default",
  sourceType: "all",
  targetChannels: [],
  mediaTypes: ["photo", "video", "document", "audio", "voice"],
  minSizeMb: 0,
  maxSizeMb: 2048,
  savePathPattern: "channel_date",
  autoSync: true,
  generateVideoCovers: true,
  dateThreshold: new Date(new Date().getFullYear(), 0, 1)
    .toISOString()
    .slice(0, 10),
};

const defaultBotAccess: BotAccessConfig = {
  mode: "self",
  allowedUsers: [],
};

const defaultBotStatus: BotStatusConfig = {
  startupNotificationMode: "off",
  statusChatId: "",
};

function parseHumanBytes(value?: string): number {
  if (!value) return 0;
  const match = String(value)
    .trim()
    .match(/^([\d.]+)\s*([a-zA-Z]+)?/);
  if (!match) return 0;
  const amount = Number(match[1]);
  const unit = (match[2] || "B").toUpperCase();
  const factors: Record<string, number> = {
    B: 1,
    KB: 1024,
    MB: 1024 ** 2,
    GB: 1024 ** 3,
    TB: 1024 ** 4,
  };
  return Math.round(amount * (factors[unit] || 1));
}

function parseSpeedKb(value?: string): number {
  if (!value) return 0;
  return Math.round(parseHumanBytes(value.replace("/s", "")) / 1024);
}

function mediaTypeFromFilename(filename: string): MediaType {
  const ext = filename.split(".").pop()?.toLowerCase() || "";
  if (["jpg", "jpeg", "png", "gif", "webp", "heic"].includes(ext))
    return "photo";
  if (["mp4", "mkv", "avi", "mov", "webm", "m4v"].includes(ext)) return "video";
  if (["mp3", "wav", "flac", "aac", "m4a", "ogg"].includes(ext)) return "audio";
  if (["opus"].includes(ext)) return "voice";
  return "document";
}

function statusFromBackend(item: BackendTask): SyncTask["status"] {
  return statusFromBackendState(item);
}

function backendDateToIso(text?: string | null, ts?: number | null): string {
  if (ts) return new Date(ts * 1000).toISOString();
  if (!text) return new Date().toISOString();
  const parsed = Date.parse(`${text.replace(" ", "T")}+08:00`);
  return Number.isNaN(parsed)
    ? new Date().toISOString()
    : new Date(parsed).toISOString();
}

function taskFromBackend(item: BackendTask): SyncTask {
  const type = mediaTypeFromFilename(item.filename);
  const status = statusFromBackend(item);
  const downloadProgress = backendProgress(item.download_progress, status === "completed");
  const uploadProgress = backendProgress(item.upload_progress, status === "completed");
  return {
    id: backendTaskId(item),
    profileId: backendTaskProfile(item) || undefined,
    type,
    sourceId: item.chat,
    sourceName: item.chat,
    filename: item.filename,
    sizeBytes: parseHumanBytes(item.total_size),
    createdAt: backendDateToIso(item.created_at, item.created_ts),
    downloadProgress,
    uploadProgress,
    status,
    phase: item.phase,
    phaseLabel: item.phase_label,
    aiStatus: item.ai_status || undefined,
    aiSummary: item.ai_summary || undefined,
    aiTags: item.ai_tags || undefined,
    speedKb: ["downloading", "uploading"].includes(status) ? Math.max(
      parseSpeedKb(item.download_speed),
      parseSpeedKb(item.upload_speed),
    ) : 0,
    remotePath: item.remote_path || item.save_path || "",
    errorMsg: item.error || undefined,
  };
}

function cloudFromConfig(config: Record<string, any>): CloudStorageConfig {
  const upload = config.upload_drive || {};
  return {
    type: "webdav",
    url: upload.webdav_url || "",
    username: upload.webdav_username || "",
    password: upload.webdav_password || "",
    remoteDir: upload.remote_dir || defaultCloudConfig.remoteDir,
    downloadRateLimitKb: 0,
    uploadRateLimitKb: 0,
  };
}

function ruleFromConfig(config: Record<string, any>): SyncRule {
  const prefix = Array.isArray(config.file_path_prefix)
    ? config.file_path_prefix.join("/")
    : "";
  let savePathPattern: SyncRule["savePathPattern"] = "channel_date";
  if (prefix === "chat_title/media_type") savePathPattern = "channel_media";
  if (prefix === "media_datetime/chat_title") savePathPattern = "date_channel";

  return {
    ...defaultRule,
    generateVideoCovers: config.generate_video_covers !== false,
    mediaTypes: (config.media_types || defaultRule.mediaTypes).filter(
      (t: string) =>
        ["photo", "video", "document", "audio", "voice"].includes(t),
    ),
    savePathPattern,
    autoSync: true,
  };
}

function botAccessFromConfig(config: Record<string, any>): BotAccessConfig {
  const configuredMode = config.bot_download_access_mode;
  let mode: BotAccessConfig["mode"] = "self";
  const allowedUsers = Array.isArray(config.allowed_user_ids)
    ? config.allowed_user_ids.map(String)
    : [];

  if (
    configuredMode === "self" ||
    configuredMode === "allowed" ||
    configuredMode === "public"
  ) {
    mode = configuredMode;
  } else if (config.bot_allow_public_download) {
    mode = "public";
  } else if (allowedUsers.length > 0) {
    mode = "allowed";
  }

  return {
    mode,
    allowedUsers,
  };
}

function videoClassifierFromConfig(
  config: Record<string, any>,
): VideoClassifierConfig {
  const vc = config.video_classifier || {};
  return {
    enable: vc.enable === true,
    apiBase: vc.api_base || defaultVideoClassifier.apiBase,
    apiKey: vc.api_key || "",
    model: vc.model || defaultVideoClassifier.model,
    maxFrames: Number(vc.max_frames) || defaultVideoClassifier.maxFrames,
    narrativeFrames: Number(vc.narrative_frames) || defaultVideoClassifier.narrativeFrames,
    timeoutSec: Number(vc.timeout) || defaultVideoClassifier.timeoutSec,
    maxVideoSizeMb:
      Number(vc.max_file_size_mb ?? vc.max_video_size_mb) ||
      defaultVideoClassifier.maxVideoSizeMb,
    minConfidence:
      vc.min_confidence == null
        ? defaultVideoClassifier.minConfidence
        : Number(vc.min_confidence),
    resourceTypes: Array.isArray(vc.resource_types)
      ? vc.resource_types
      : defaultVideoClassifier.resourceTypes,
    transcribe: vc.transcribe === true,
    shareSourceText: vc.share_source_text === true,
    useSubtitles: vc.use_subtitles === true,
    maxAudioSeconds:
      Number(vc.max_audio_seconds) || defaultVideoClassifier.maxAudioSeconds,
    organizeFolders: vc.organize_folders !== false,
  };
}

function applyVideoClassifierConfig(
  currentConfig: Record<string, any>,
  vc: VideoClassifierConfig,
): Record<string, any> {
  return {
    ...currentConfig,
    video_classifier: {
      ...currentConfig.video_classifier,
      enable: vc.enable,
      api_base: vc.apiBase.trim() || defaultVideoClassifier.apiBase,
      api_key: vc.apiKey.trim(),
      model: vc.model.trim() || defaultVideoClassifier.model,
      max_frames: Number(vc.maxFrames) || defaultVideoClassifier.maxFrames,
      narrative_frames: Number(vc.narrativeFrames) || defaultVideoClassifier.narrativeFrames,
      timeout: Number(vc.timeoutSec) || defaultVideoClassifier.timeoutSec,
      max_video_size_mb:
        Number(vc.maxVideoSizeMb) || defaultVideoClassifier.maxVideoSizeMb,
      max_file_size_mb:
        Number(vc.maxVideoSizeMb) || defaultVideoClassifier.maxVideoSizeMb,
      min_confidence: Number(vc.minConfidence),
      resource_types: vc.resourceTypes,
      transcribe: vc.transcribe,
      share_source_text: vc.shareSourceText === true,
      use_subtitles: vc.useSubtitles === true,
      max_audio_seconds: vc.maxAudioSeconds,
      organize_folders: vc.organizeFolders,
    },
  };
}

function botStatusFromConfig(config: Record<string, any>): BotStatusConfig {
  const configuredMode = config.bot_startup_notification_mode;
  const mode =
    configuredMode === "admin" ||
    configuredMode === "status_chat" ||
    configuredMode === "off"
      ? configuredMode
      : defaultBotStatus.startupNotificationMode;

  return {
    startupNotificationMode: mode,
    statusChatId: config.bot_status_chat_id
      ? String(config.bot_status_chat_id)
      : "",
  };
}

function applyUiConfig(
  currentConfig: Record<string, any>,
  cloud: CloudStorageConfig,
  rule: SyncRule,
): Record<string, any> {
  const next = { ...currentConfig };
  next.media_types = rule.mediaTypes;
  next.generate_video_covers = rule.generateVideoCovers;
  next.upload_drive = {
    ...(next.upload_drive || {}),
    enable_upload_file: true,
    upload_adapter: "webdav",
    remote_dir: cloud.remoteDir,
    webdav_url: cloud.url,
    webdav_username: cloud.username,
    webdav_password: cloud.password || "",
  };

  if (rule.savePathPattern === "channel_media") {
    next.file_path_prefix = ["chat_title", "media_type"];
  } else if (rule.savePathPattern === "date_channel") {
    next.file_path_prefix = ["media_datetime", "chat_title"];
  } else {
    next.file_path_prefix = ["chat_title", "media_datetime"];
  }

  return next;
}

function applyBotAccessConfig(
  currentConfig: Record<string, any>,
  botAccess: BotAccessConfig,
): Record<string, any> {
  return {
    ...currentConfig,
    bot_download_access_mode: botAccess.mode,
    bot_allow_public_download: botAccess.mode === "public",
    allowed_user_ids: botAccess.allowedUsers,
  };
}

function applyBotStatusConfig(
  currentConfig: Record<string, any>,
  statusConfig: BotStatusConfig,
): Record<string, any> {
  return {
    ...currentConfig,
    bot_startup_notification_mode: statusConfig.startupNotificationMode,
    bot_status_chat_id: statusConfig.statusChatId.trim(),
  };
}

function tabFromPath(): ActiveTab {
  if (window.location.pathname.includes("config")) return "config";
  if (
    window.location.pathname.includes("tg_login") ||
    window.location.pathname.includes("accounts")
  )
    return "accounts";
  if (window.location.pathname.includes("tasks")) return "dashboard";
  return "files";
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok || data.success === false || data.status === "error") {
    throw new Error(data.message || "Request failed");
  }
  return data;
}

export default function App() {
  const [theme, setTheme] = useState<"dark" | "light">(() =>
    localStorage.getItem("tg_sync_theme") === "light" ? "light" : "dark",
  );
  const [activeTab, setActiveTab] = useState<ActiveTab>(() => tabFromPath());
  const [connection, setConnection] = useState<
    "connecting" | "connected" | "reconnecting"
  >("connecting");
  const [version, setVersion] = useState("...");
  const [rawConfig, setRawConfig] = useState<Record<string, any>>({});
  const [cloudConfig, setCloudConfig] =
    useState<CloudStorageConfig>(defaultCloudConfig);
  const [syncRule, setSyncRule] = useState<SyncRule>(defaultRule);
  const [botAccess, setBotAccess] = useState<BotAccessConfig>(defaultBotAccess);
  const [botStatus, setBotStatus] = useState<BotStatusConfig>(defaultBotStatus);
  const [videoClassifier, setVideoClassifier] = useState<VideoClassifierConfig>(
    defaultVideoClassifier,
  );
  const [liveTasks, setLiveTasks] = useState<SyncTask[]>([]);
  const [terminalTasks, setTerminalTasks] = useState<SyncTask[]>([]);
  const [accounts, setAccounts] = useState<TelegramAccount[]>([]);
  const [sessionExists, setSessionExists] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [bootstrapState, setBootstrapState] = useState<"loading" | "ready" | "error">("loading");
  const [bootstrapError, setBootstrapError] = useState("");
  const initialBootstrapUsed = useRef(false);
  const [apiVerified, setApiVerified] = useState(false);

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("light-mode", theme === "light");
    localStorage.setItem("tg_sync_theme", theme);
  }, [theme]);

  useEffect(() => {
    const currentPath = window.location.pathname;
    const onPopState = () => {
      if (
        document.querySelector('[data-unsaved="true"]') &&
        !window.confirm("设置尚未保存，确定离开并放弃修改吗？")
      ) {
        window.history.pushState({}, "", currentPath);
        return;
      }
      setActiveTab(tabFromPath());
      setStatusMessage("");
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [activeTab]);
  useEffect(() => {
    if (!statusMessage) return;
    const timer = setTimeout(() => setStatusMessage(""), 8000);
    return () => clearTimeout(timer);
  }, [statusMessage]);

  const applyAccountStatus = (accountStatus: BootstrapPayload["account"]) => {
    const accountList = Array.isArray(accountStatus.accounts)
      ? accountStatus.accounts
      : [];
    const account = accountStatus.account;
    setSessionExists(accountStatus.session_exists);
    if (accountList.length > 0) {
      setAccounts(accountList);
    } else if (account) {
      setAccounts([account]);
    } else {
      setAccounts([]);
    }
  };

  const loadBootstrap = async () => {
    setBootstrapState("loading");
    setBootstrapError("");
    try {
    let initial: BootstrapPayload | null = null;
    if (!initialBootstrapUsed.current) {
      initialBootstrapUsed.current = true;
      try {
        const text = document.getElementById("app-bootstrap")?.textContent;
        const candidate = text ? JSON.parse(text) : null;
        if (candidate?.config && candidate?.account) initial = candidate;
      } catch { /* Fall back to the authenticated endpoint. */ }
    }
    const payload = initial || await fetchBootstrap<BootstrapPayload>();
    if (!initial) setApiVerified(true);
    else {
      void fetchBootstrap<BootstrapPayload>().then(() => setApiVerified(true)).catch(() => {
        setBootstrapError("已显示服务器保存的数据，但浏览器后台接口未响应。为避免保存结果不明，暂时只读；可在 Chrome 中编辑，或刷新重试。");
      });
    }
    setVersion(payload.version);
    setRawConfig(payload.config || {});
    setCloudConfig(cloudFromConfig(payload.config || {}));
    setSyncRule(ruleFromConfig(payload.config || {}));
    setBotAccess(botAccessFromConfig(payload.config || {}));
    setBotStatus(botStatusFromConfig(payload.config || {}));
    setVideoClassifier(videoClassifierFromConfig(payload.config || {}));
    applyAccountStatus(payload.account);
    setBootstrapState("ready");
    } catch (error) {
      setBootstrapState("error");
      setBootstrapError(error instanceof Error ? error.message : "账户和配置加载失败");
      throw error;
    }
  };

  useEffect(() => {
    loadBootstrap().catch((error) => setStatusMessage(error.message));
  }, []);

  useEffect(() => {
    // Web 服务会先于 Telegram runtime 就绪；定时刷新让账号卡片自动收敛到真实运行状态。
    const intervalId = window.setInterval(() => {
      fetch("/api/account/status")
        .then((response) => response.json())
        .then(applyAccountStatus)
        .catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(intervalId);
  }, []);

  useEffect(() => {
    const source = new EventSource("/stream");
    source.onopen = () => setConnection("connected");
    source.onmessage = (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }
      setConnection("connected");
      if (payload.type !== "update") return;
      if (Array.isArray(payload.tasks)) {
        setLiveTasks(payload.tasks.map(taskFromBackend));
      }
      if (Array.isArray(payload.history)) {
        setTerminalTasks(payload.history.map(taskFromBackend));
      }
    };
    source.onerror = () => setConnection("reconnecting");
    return () => source.close();
  }, []);

  const saveConfig = async (
    cloud: CloudStorageConfig,
    rule: SyncRule,
    statusConfig: BotStatusConfig,
    vcConfig?: VideoClassifierConfig,
  ) => {
    if (bootstrapState !== "ready" || !apiVerified) throw new Error("后台连接尚未验证，不能保存。请刷新重试或在 Chrome 中编辑。");
    const nextVideoClassifier = vcConfig || videoClassifier;
    const next = applyVideoClassifierConfig(
      applyBotStatusConfig(
        applyBotAccessConfig(applyUiConfig(rawConfig, cloud, rule), botAccess),
        statusConfig,
      ),
      nextVideoClassifier,
    );
    await postJson<{ status: string; message: string }>("/api/config", {
      config: next,
    });
    setRawConfig(next);
    setCloudConfig(cloud);
    setSyncRule(rule);
    setBotStatus(statusConfig);
    setVideoClassifier(nextVideoClassifier);
  };

  const saveBotAccess = async (nextBotAccess: BotAccessConfig) => {
    const result = await postJson<{
      account: BootstrapPayload["account"];
      message?: string;
      config?: Record<string, any>;
    }>("/api/bot/access", {
      mode: nextBotAccess.mode,
      allowedUsers: nextBotAccess.allowedUsers,
    });
    applyAccountStatus(result.account);
    if (result.config) {
      setRawConfig(result.config);
      setBotAccess(botAccessFromConfig(result.config));
    }
    setStatusMessage(result.message || "配置已保存。");
  };

  const handleTaskAction = async (
    id: string,
    action: "pause" | "resume" | "reset_upload" | "delete" | "delete_remote",
  ) => {
    const parts = id.split(":");
    const hasProfile = parts.length >= 3;
    const profileId = hasProfile ? parts[0] : undefined;
    const chatId = hasProfile ? parts[1] : parts[0];
    const messageId = hasProfile ? parts[2] : parts[1];
    const result = await postJson<{ message?: string }>("/task_control", {
      chat_id: chatId,
      message_id: messageId,
      profile_id: profileId,
      action,
    });
    if (action === "reset_upload") {
      setStatusMessage(
        result.message || "云端异常文件已重置，任务已重新入队。",
      );
    } else if (action === "delete" || action === "delete_remote") {
      setLiveTasks((current) => current.filter((task) => task.id !== id));
      setTerminalTasks((current) => current.filter((task) => task.id !== id));
      setStatusMessage(
        result.message ||
          (action === "delete_remote"
            ? "任务及远程资源已删除。"
            : "任务已取消并删除。"),
      );
    }
  };

  const handleAccountRefresh = async () => {
    const response = await fetch("/api/account/status");
    const data = await response.json();
    applyAccountStatus(data);
  };

  const handleCreateProfile = async (name: string) => {
    const data = await postJson<{ account: BootstrapPayload["account"] }>(
      "/api/profiles",
      {
        name,
      },
    );
    applyAccountStatus(data.account);
    setStatusMessage("已创建账号档案。");
  };

  const handleRenameProfile = async (profileId: string, name: string) => {
    const data = await postJson<{ account: BootstrapPayload["account"] }>(
      "/api/profiles/update",
      {
        profile_id: profileId,
        name,
      },
    );
    applyAccountStatus(data.account);
    setStatusMessage("账号档案已重命名。");
  };

  const handleDeleteProfile = async (profileId: string) => {
    const data = await postJson<{ account: BootstrapPayload["account"] }>(
      "/api/profiles/delete",
      {
        profile_id: profileId,
      },
    );
    applyAccountStatus(data.account);
    setStatusMessage("账号档案已删除。");
  };

  const handleConnectSaved = async (profileId?: string) => {
    const data = await postJson<{
      account: BootstrapPayload["account"];
      runtime?: any;
    }>("/api/account/connect_saved_session", {
      profile_id: profileId,
    });
    applyAccountStatus(data.account);
    await loadBootstrap();
    setStatusMessage(
      data.runtime?.message || "已连接保存的 Telegram session。",
    );
  };

  const handleStartAccount = async (profileId: string) => {
    const data = await postJson<{
      account: BootstrapPayload["account"];
      runtime?: any;
    }>("/api/profiles/start", {
      profile_id: profileId,
    });
    applyAccountStatus(data.account);
    setStatusMessage(data.runtime?.message || "账号运行态已启动。");
  };

  const handleStopAccount = async (profileId: string) => {
    const data = await postJson<{
      account: BootstrapPayload["account"];
      runtime?: any;
    }>("/api/profiles/stop", {
      profile_id: profileId,
    });
    applyAccountStatus(data.account);
    setStatusMessage(data.runtime?.message || "账号运行态已停止。");
  };

  const handleLogout = async (profileId?: string) => {
    const data = await postJson<{ account: BootstrapPayload["account"] }>(
      "/api/account/logout",
      {
        profile_id: profileId,
      },
    );
    applyAccountStatus(data.account);
    setStatusMessage("Telegram session 已断开。");
  };

  const handleSendCode = async (
    phoneNumber: string,
    apiId?: string,
    apiHash?: string,
    profileId?: string,
    createProfile = true,
    profileName?: string,
  ) => {
    await postJson("/api/account/send_code", {
      phone_number: phoneNumber,
      api_id: apiId,
      api_hash: apiHash,
      profile_id: profileId,
      create_profile: createProfile,
      profile_name: profileName || phoneNumber,
    });
    // Bootstrap loading unmounts AccountManager and loses the pending login
    // step. Refresh account data after verification, not between phone and code.
  };

  const handleVerifyCode = async (code: string) => {
    const data = await postJson<{
      needs_password?: boolean;
      account?: BootstrapPayload["account"];
      runtime?: any;
    }>("/api/account/verify_code", { code });
    if (data.needs_password) return { needsPassword: true };
    if (data.account) applyAccountStatus(data.account);
    await loadBootstrap();
    setStatusMessage(data.runtime?.message || "Telegram 登录成功。");
    return { needsPassword: false };
  };

  const handleVerifyPassword = async (password: string) => {
    const data = await postJson<{
      account: BootstrapPayload["account"];
      runtime?: any;
    }>("/api/account/verify_password", {
      password,
    });
    applyAccountStatus(data.account);
    await loadBootstrap();
    setStatusMessage(data.runtime?.message || "Telegram 登录成功。");
  };

  const runningAccounts = useMemo(
    () =>
      accounts.filter(
        (account) => account.isRunning || account.status === "connected",
      ),
    [accounts],
  );
  const navigate = (tab: ActiveTab) => {
    if (tab === activeTab) return;
    if (
      document.querySelector('[data-unsaved="true"]') &&
      !window.confirm("设置尚未保存，确定离开并放弃修改吗？")
    )
      return;
    setActiveTab(tab);
    setStatusMessage("");
    const path =
      tab === "dashboard"
        ? "/tasks"
        : tab === "accounts"
          ? "/tg_login"
          : tab === "config"
            ? "/config"
            : "/files";
    window.history.pushState({}, "", path);
  };
  const tasks = useMemo(
    () => buildDashboardTasks(liveTasks, terminalTasks),
    [liveTasks, terminalTasks],
  );
  const navigation = [
    { id: "files", label: "资源库", icon: FolderOpen },
    { id: "dashboard", label: "传输任务", icon: Activity },
    { id: "accounts", label: "账号", icon: Users },
    { id: "config", label: "设置", icon: Settings2 },
  ] as const;

  return (
    <div className="app-shell workbench-shell">
      <a className="skip-link" href="#workspace">
        跳到主要内容
      </a>
      <header className="app-header">
        <a
          className="app-brand"
          href="/files"
          onClick={(event) => {
            event.preventDefault();
            navigate("files");
          }}
          aria-label="媒体工作台"
          title="媒体工作台"
        >
          <span className="brand-symbol">
            <CloudLightning size={23} />
          </span>
          <span>
            媒体工作台<small>TELEGRAM SYNC</small>
          </span>
        </a>
        <nav className="app-navigation" aria-label="主导航">
          {navigation.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              aria-label={label}
              title={label}
              onClick={() => navigate(id)}
              aria-current={activeTab === id ? "page" : undefined}
            >
              <Icon size={19} />
              <span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="app-utilities">
          <span
            className={`connection-state ${connection}`}
            role="status"
            title={`${connection === "connected" ? "实时连接" : connection === "connecting" ? "连接中" : "重新连接中"} · v${version}`}
          >
            <i />
            <span className="connection-label">
              {connection === "connected"
                ? "实时连接"
                : connection === "connecting"
                  ? "连接中"
                  : "重新连接中"}
            </span>
          </span>
          <button
            className="account-shortcut"
            onClick={() => navigate("accounts")}
            title="管理 Telegram 账号"
          >
            <Users size={16} />
            {runningAccounts.length}/{accounts.length} 运行
          </button>
          <button
            className="icon-button"
            onClick={() =>
              setTheme((previous) => (previous === "light" ? "dark" : "light"))
            }
            aria-label={theme === "dark" ? "切换为白天模式" : "切换为夜间模式"}
          >
            {theme === "dark" ? <Sun size={19} /> : <Moon size={19} />}
          </button>
        </div>
      </header>
      <main
        id="workspace"
        tabIndex={-1}
        className={`app-workspace page-${activeTab}`}
      >
        {activeTab === "files" && <FileManager />}
        {activeTab === "dashboard" && (
          <TaskTable
            tasks={tasks}
            onPauseTask={(id) => handleTaskAction(id, "pause")}
            onResumeTask={(id) => handleTaskAction(id, "resume")}
            onResetUpload={(id) => handleTaskAction(id, "reset_upload")}
            onDeleteTask={(id, deleteRemote) =>
              handleTaskAction(id, deleteRemote ? "delete_remote" : "delete")
            }
            onOpenAccounts={() => navigate("accounts")}
          />
        )}
        {(activeTab === "config" || activeTab === "accounts") && bootstrapState !== "ready" && (
          <section role={bootstrapState === "error" ? "alert" : "status"} style={{ padding: "32px", maxWidth: "720px" }}>
            <h1>{bootstrapState === "loading" ? "正在加载账户和配置…" : "账户和配置未加载"}</h1>
            <p>{bootstrapError || "正在读取已保存的数据，完成后才能编辑。此时不代表数据库为空。"}</p>
            {bootstrapState === "error" && <button type="button" onClick={() => { void loadBootstrap().catch(() => undefined); }}>重新加载</button>}
          </section>
        )}
        {activeTab === "config" && bootstrapState === "ready" && (
          <div>
          {!apiVerified && <p role="status" style={{padding: "12px 24px"}}>{bootstrapError || "配置已读取，正在验证后台连接；验证完成前只读。"}</p>}
          <fieldset disabled={!apiVerified} style={{border: 0, margin: 0, padding: 0, minWidth: 0}}>
          <ConfigPanel
            config={cloudConfig}
            rule={syncRule}
            statusConfig={botStatus}
            vcConfig={videoClassifier}
            onSaveConfig={setCloudConfig}
            onSaveRule={setSyncRule}
            onSaveAll={saveConfig}
          />
          </fieldset>
          </div>
        )}
        {activeTab === "accounts" && bootstrapState === "ready" && (
          <AccountManager
            accounts={accounts}
            botAccess={botAccess}
            sessionExists={sessionExists}
            onCreateProfile={handleCreateProfile}
            onRenameProfile={handleRenameProfile}
            onDeleteProfile={handleDeleteProfile}
            onDisconnectAccount={handleLogout}
            onStartAccount={handleStartAccount}
            onStopAccount={handleStopAccount}
            onConnectSavedSession={handleConnectSaved}
            onSendCode={handleSendCode}
            onVerifyCode={handleVerifyCode}
            onVerifyPassword={handleVerifyPassword}
            onRefresh={handleAccountRefresh}
            onSaveBotAccess={saveBotAccess}
          />
        )}
      </main>
      {statusMessage && (
        <div className="app-toast" role="status">
          <Info size={19} />
          <span>{statusMessage}</span>
          <button
            className="icon-button"
            aria-label="关闭通知"
            onClick={() => setStatusMessage("")}
          >
            <X size={18} />
          </button>
        </div>
      )}
    </div>
  );
}
