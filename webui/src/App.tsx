/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CloudLightning,
  HardDrive,
  Info,
  Menu,
  Moon,
  Sliders,
  Sun,
  Tv,
  Users,
  X,
} from 'lucide-react';
import { AccountManager } from './components/AccountManager';
import { ConfigPanel } from './components/ConfigPanel';
import { FileManager } from './components/FileManager';
import { TaskTable } from './components/TaskTable';
import {
  CloudStorageConfig,
  CompletedFile,
  MediaType,
  SyncRule,
  SyncTask,
  TelegramAccount,
} from './types';

type ActiveTab = 'dashboard' | 'files' | 'config' | 'accounts';

interface BackendTask {
  chat: string;
  id: string;
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
}

interface BootstrapPayload {
  version: string;
  config: Record<string, any>;
  account: {
    logged_in: boolean;
    session_exists: boolean;
    account: TelegramAccount | null;
  };
}

const defaultCloudConfig: CloudStorageConfig = {
  type: 'webdav',
  url: '',
  username: '',
  password: '',
  remoteDir: '/TelegramBackup',
  downloadRateLimitKb: 0,
  uploadRateLimitKb: 0,
};

const defaultRule: SyncRule = {
  id: 'rule-default',
  sourceType: 'all',
  targetChannels: [],
  mediaTypes: ['photo', 'video', 'document', 'audio', 'voice'],
  minSizeMb: 0,
  maxSizeMb: 2048,
  savePathPattern: 'channel_date',
  autoSync: true,
  dateThreshold: new Date(new Date().getFullYear(), 0, 1).toISOString().slice(0, 10),
};

function parseHumanBytes(value?: string): number {
  if (!value) return 0;
  const match = String(value).trim().match(/^([\d.]+)\s*([a-zA-Z]+)?/);
  if (!match) return 0;
  const amount = Number(match[1]);
  const unit = (match[2] || 'B').toUpperCase();
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
  return Math.round(parseHumanBytes(value.replace('/s', '')) / 1024);
}

function mediaTypeFromFilename(filename: string): MediaType {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'heic'].includes(ext)) return 'photo';
  if (['mp4', 'mkv', 'avi', 'mov', 'webm', 'm4v'].includes(ext)) return 'video';
  if (['mp3', 'wav', 'flac', 'aac', 'm4a', 'ogg'].includes(ext)) return 'audio';
  if (['opus'].includes(ext)) return 'voice';
  return 'document';
}

function statusFromBackend(item: BackendTask): SyncTask['status'] {
  if (item.state === 'paused' || item.status === '已暂停') return 'paused';
  if (item.status === '上传中' || Number(item.upload_progress) > 0) return 'uploading';
  if (item.status === '正在完成...') return 'syncing';
  if (item.status === '已完成') return 'completed';
  if (item.status === '等待中') return 'pending';
  return 'downloading';
}

function backendDateToIso(text?: string | null, ts?: number | null): string {
  if (ts) return new Date(ts * 1000).toISOString();
  if (!text) return new Date().toISOString();
  const parsed = Date.parse(`${text.replace(' ', 'T')}+08:00`);
  return Number.isNaN(parsed) ? new Date().toISOString() : new Date(parsed).toISOString();
}

function taskFromBackend(item: BackendTask): SyncTask {
  const type = mediaTypeFromFilename(item.filename);
  const downloadProgress = Number.parseFloat(item.download_progress) || 0;
  const uploadProgress = Number.parseFloat(item.upload_progress) || 0;
  return {
    id: `${item.chat}:${item.id}`,
    type,
    sourceId: item.chat,
    sourceName: item.chat,
    filename: item.filename,
    sizeBytes: parseHumanBytes(item.total_size),
    createdAt: backendDateToIso(item.created_at, item.created_ts),
    downloadProgress,
    uploadProgress,
    status: statusFromBackend(item),
    speedKb: Math.max(parseSpeedKb(item.download_speed), parseSpeedKb(item.upload_speed)),
    remotePath: item.remote_path || item.save_path || '',
  };
}

function completedFromBackend(item: BackendTask): CompletedFile {
  const relativeParts = (item.relative_path || '').split('/').filter(Boolean);
  return {
    id: `${item.chat}:${item.id}`,
    name: item.filename,
    type: mediaTypeFromFilename(item.filename),
    sizeBytes: parseHumanBytes(item.total_size),
    completedAt: backendDateToIso(item.completed_at || item.created_at, item.completed_ts || item.created_ts),
    remotePath: item.remote_path || item.save_path || '',
    sourceName: relativeParts[0] || item.chat,
    sourceId: item.chat,
  };
}

function cloudFromConfig(config: Record<string, any>): CloudStorageConfig {
  const upload = config.upload_drive || {};
  return {
    type: 'webdav',
    url: upload.webdav_url || '',
    username: upload.webdav_username || '',
    password: upload.webdav_password || '',
    remoteDir: upload.remote_dir || defaultCloudConfig.remoteDir,
    downloadRateLimitKb: 0,
    uploadRateLimitKb: 0,
  };
}

function ruleFromConfig(config: Record<string, any>): SyncRule {
  const prefix = Array.isArray(config.file_path_prefix) ? config.file_path_prefix.join('/') : '';
  let savePathPattern: SyncRule['savePathPattern'] = 'channel_date';
  if (prefix === 'chat_title/media_type') savePathPattern = 'channel_media';
  if (prefix === 'media_datetime/chat_title') savePathPattern = 'date_channel';

  return {
    ...defaultRule,
    mediaTypes: (config.media_types || defaultRule.mediaTypes).filter((t: string) => (
      ['photo', 'video', 'document', 'audio', 'voice'].includes(t)
    )),
    savePathPattern,
    autoSync: true,
  };
}

function applyUiConfig(
  currentConfig: Record<string, any>,
  cloud: CloudStorageConfig,
  rule: SyncRule,
): Record<string, any> {
  const next = { ...currentConfig };
  next.media_types = rule.mediaTypes;
  next.upload_drive = {
    ...(next.upload_drive || {}),
    enable_upload_file: true,
    upload_adapter: 'webdav',
    remote_dir: cloud.remoteDir,
    webdav_url: cloud.url,
    webdav_username: cloud.username,
    webdav_password: cloud.password || '',
  };

  if (rule.savePathPattern === 'channel_media') {
    next.file_path_prefix = ['chat_title', 'media_type'];
  } else if (rule.savePathPattern === 'date_channel') {
    next.file_path_prefix = ['media_datetime', 'chat_title'];
  } else {
    next.file_path_prefix = ['chat_title', 'media_datetime'];
  }

  return next;
}

function tabFromPath(): ActiveTab {
  if (window.location.pathname.includes('config')) return 'config';
  if (window.location.pathname.includes('tg_login') || window.location.pathname.includes('accounts')) return 'accounts';
  if (window.location.pathname.includes('files')) return 'files';
  return 'dashboard';
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok || data.success === false || data.status === 'error') {
    throw new Error(data.message || 'Request failed');
  }
  return data;
}

export default function App() {
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    return (localStorage.getItem('tg_sync_theme') as 'dark' | 'light') || 'dark';
  });
  const [activeTab, setActiveTab] = useState<ActiveTab>(() => tabFromPath());
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(() => {
    return localStorage.getItem('tg_sync_sidebar_collapsed_user') === 'true';
  });
  const [version, setVersion] = useState('...');
  const [rawConfig, setRawConfig] = useState<Record<string, any>>({});
  const [cloudConfig, setCloudConfig] = useState<CloudStorageConfig>(defaultCloudConfig);
  const [syncRule, setSyncRule] = useState<SyncRule>(defaultRule);
  const [tasks, setTasks] = useState<SyncTask[]>([]);
  const [completedFiles, setCompletedFiles] = useState<CompletedFile[]>([]);
  const [accounts, setAccounts] = useState<TelegramAccount[]>([]);
  const [activeAccountId, setActiveAccountId] = useState<string | null>(null);
  const [sessionExists, setSessionExists] = useState(false);
  const [statusMessage, setStatusMessage] = useState('');
  const sidebarRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle('light-mode', theme === 'light');
    localStorage.setItem('tg_sync_theme', theme);
  }, [theme]);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (window.innerWidth < 768) return;
      if (sidebarRef.current && !sidebarRef.current.contains(event.target as Node) && !isSidebarCollapsed) {
        setIsSidebarCollapsed(true);
        localStorage.setItem('tg_sync_sidebar_collapsed_user', 'true');
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isSidebarCollapsed]);

  const applyAccountStatus = (accountStatus: BootstrapPayload['account']) => {
    const account = accountStatus.account;
    setSessionExists(accountStatus.session_exists);
    if (account) {
      setAccounts([account]);
      setActiveAccountId(account.id);
    } else {
      setAccounts([]);
      setActiveAccountId(null);
    }
  };

  const loadBootstrap = async () => {
    const response = await fetch('/api/bootstrap');
    if (!response.ok) throw new Error('Failed to load bootstrap data');
    const payload = (await response.json()) as BootstrapPayload;
    setVersion(payload.version);
    setRawConfig(payload.config || {});
    setCloudConfig(cloudFromConfig(payload.config || {}));
    setSyncRule(ruleFromConfig(payload.config || {}));
    applyAccountStatus(payload.account);
  };

  useEffect(() => {
    loadBootstrap().catch((error) => setStatusMessage(error.message));
  }, []);

  useEffect(() => {
    const source = new EventSource('/stream');
    source.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      if (payload.type !== 'update') return;
      if (Array.isArray(payload.tasks)) {
        setTasks(payload.tasks.map(taskFromBackend));
      }
      if (Array.isArray(payload.history)) {
        setCompletedFiles(payload.history.map(completedFromBackend));
      }
    };
    source.onerror = () => setStatusMessage('实时任务流暂时断开，浏览器会自动重连。');
    return () => source.close();
  }, []);

  const saveConfig = async (cloud: CloudStorageConfig, rule: SyncRule) => {
    const next = applyUiConfig(rawConfig, cloud, rule);
    const result = await postJson<{ status: string; message: string; config?: Record<string, any> }>('/api/config', {
      config: next,
    });
    setRawConfig(next);
    setCloudConfig(cloud);
    setSyncRule(rule);
    setStatusMessage(result.message || '配置已保存。');
  };

  const handleTaskAction = async (id: string, action: 'pause' | 'resume' | 'delete') => {
    const [chatId, messageId] = id.split(':');
    await postJson('/task_control', {
      chat_id: chatId,
      message_id: messageId,
      action,
    });
  };

  const handleAccountRefresh = async () => {
    const response = await fetch('/api/account/status');
    const data = await response.json();
    applyAccountStatus(data);
  };

  const handleConnectSaved = async () => {
    const data = await postJson<{ account: BootstrapPayload['account']; runtime?: any }>('/api/account/connect_saved_session', {});
    applyAccountStatus(data.account);
    setStatusMessage(data.runtime?.message || '已连接保存的 Telegram session。');
  };

  const handleLogout = async () => {
    await postJson('/api/account/logout', {});
    setAccounts([]);
    setActiveAccountId(null);
    setSessionExists(false);
    setStatusMessage('Telegram session 已断开。');
  };

  const handleSendCode = async (phoneNumber: string, apiId?: string, apiHash?: string) => {
    await postJson('/api/account/send_code', {
      phone_number: phoneNumber,
      api_id: apiId,
      api_hash: apiHash,
    });
  };

  const handleVerifyCode = async (code: string) => {
    const data = await postJson<{ needs_password?: boolean; account?: BootstrapPayload['account']; runtime?: any }>(
      '/api/account/verify_code',
      { code },
    );
    if (data.needs_password) return { needsPassword: true };
    if (data.account) applyAccountStatus(data.account);
    setStatusMessage(data.runtime?.message || 'Telegram 登录成功。');
    return { needsPassword: false };
  };

  const handleVerifyPassword = async (password: string) => {
    const data = await postJson<{ account: BootstrapPayload['account']; runtime?: any }>('/api/account/verify_password', {
      password,
    });
    applyAccountStatus(data.account);
    setStatusMessage(data.runtime?.message || 'Telegram 登录成功。');
  };

  const currentActiveAccount = useMemo(
    () => accounts.find((account) => account.id === activeAccountId),
    [accounts, activeAccountId],
  );

  const navigate = (tab: ActiveTab) => {
    setActiveTab(tab);
    setIsMobileMenuOpen(false);
    const path = tab === 'dashboard' ? '/' : tab === 'accounts' ? '/tg_login' : tab === 'config' ? '/config' : '/files';
    window.history.pushState({}, '', path);
  };

  const navButton = (tab: ActiveTab, label: string, icon: React.ReactNode, badge?: number) => (
    <button
      onClick={() => navigate(tab)}
      className={`flex items-center ${isSidebarCollapsed ? 'md:justify-center' : 'justify-start'} gap-2.5 px-3 py-2.5 rounded-xl text-xs font-semibold select-none cursor-pointer transition-all border ${
        activeTab === tab
          ? 'bg-slate-900 border-indigo-500/30 text-white shadow-inner'
          : 'bg-transparent border-transparent text-slate-400 hover:text-slate-205 hover:bg-slate-900/40'
      }`}
      title={label}
    >
      {icon}
      <span className={isSidebarCollapsed ? 'md:hidden' : 'inline'}>{label}</span>
      {!!badge && !isSidebarCollapsed && (
        <span className="ml-auto px-1.5 py-0.5 rounded bg-amber-500/10 border border-amber-500/20 text-amber-400 font-mono text-[9px]">
          {badge}
        </span>
      )}
    </button>
  );

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col font-sans antialiased">
      <header className="border-b border-slate-900 bg-slate-900/60 backdrop-blur-md sticky top-0 z-40 px-4 py-3 shrink-0">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <span className="p-2.5 rounded-xl bg-gradient-to-tr from-indigo-600 to-indigo-500 text-white shadow-lg shadow-indigo-950/40">
              <CloudLightning className="w-5 h-5 fill-indigo-200/20" />
            </span>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-xs font-bold uppercase tracking-wider text-white">Telegram Media Sync</h1>
                <span className="text-[9px] bg-indigo-500/15 border border-indigo-500/20 px-1.5 rounded text-indigo-400 font-mono">
                  v{version}
                </span>
              </div>
              <p className="text-[10px] text-slate-400 hidden sm:block">电报媒体流式自动备份与 WebDAV 同步中心</p>
            </div>
          </div>

          <div className="flex items-center gap-2.5 sm:gap-4">
            {currentActiveAccount ? (
              <div className="flex items-center gap-2 text-xs bg-slate-950/80 px-2.5 py-1.5 rounded-lg border border-slate-805/90">
                <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse shrink-0" />
                <span className="text-slate-300 font-mono hidden md:inline text-[11px]">{currentActiveAccount.username || currentActiveAccount.firstName}</span>
                <span className="text-slate-600">|</span>
                <span className="text-indigo-400 font-mono text-[11px] font-semibold">{accounts.length} 会话</span>
              </div>
            ) : (
              <span className="text-xs text-rose-400 flex items-center gap-1 bg-rose-500/10 px-2.5 py-1 rounded-md border border-rose-500/20">
                <AlertCircle className="w-3.5 h-3.5" />
                未连接电报
              </span>
            )}

            <button
              onClick={() => setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'))}
              className="p-2.5 rounded-xl bg-slate-950/80 hover:bg-slate-900 border border-slate-805/90 text-slate-455 hover:text-slate-200 transition-all cursor-pointer flex items-center justify-center gap-1.5"
              title={theme === 'dark' ? '切换为白天模式' : '切换为夜间模式'}
            >
              {theme === 'dark' ? <Sun className="w-4 h-4 text-amber-400" /> : <Moon className="w-4 h-4 text-indigo-400" />}
              <span className="text-[11px] font-semibold hidden sm:inline text-slate-400">
                {theme === 'dark' ? '白天模式' : '夜间模式'}
              </span>
            </button>

            <button
              onClick={() => setIsMobileMenuOpen((prev) => !prev)}
              className="md:hidden p-2.5 rounded-xl bg-slate-950/80 hover:bg-slate-900 border border-slate-805/90 text-slate-400 hover:text-slate-205 transition-all cursor-pointer"
              title="切换导航菜单"
            >
              {isMobileMenuOpen ? <X className="w-4 h-4" /> : <Menu className="w-4 h-4" />}
            </button>
          </div>
        </div>
      </header>

      {isMobileMenuOpen && (
        <>
          <div className="md:hidden fixed inset-0 bg-slate-950/70 backdrop-blur-md z-50 animate-fadeIn" onClick={() => setIsMobileMenuOpen(false)} />
          <div className="md:hidden fixed inset-y-0 left-0 w-72 max-w-[85vw] bg-slate-950 border-r border-slate-900 p-5 z-60 flex flex-col gap-6 shadow-2xl animate-slideRight">
            <div className="flex items-center justify-between border-b border-slate-900 pb-4">
              <span className="text-xs font-bold uppercase tracking-wider text-white">导航控制台</span>
              <button onClick={() => setIsMobileMenuOpen(false)} className="p-1 text-slate-450 hover:text-white rounded-lg hover:bg-slate-900">
                <X className="w-4 h-4" />
              </button>
            </div>
            <nav className="flex flex-col gap-2">
              {navButton('dashboard', '任务仪表盘', <Tv className="w-4 h-4 shrink-0 text-indigo-400" />)}
              {navButton('files', '已归档媒体文件', <HardDrive className="w-4 h-4 shrink-0 text-indigo-400" />, completedFiles.length)}
              {navButton('config', '同步规则与云盘', <Sliders className="w-4 h-4 shrink-0 text-indigo-400" />)}
              {navButton('accounts', '账号登录管理', <Users className="w-4 h-4 shrink-0 text-indigo-400" />)}
            </nav>
          </div>
        </>
      )}

      <div className="flex-1 flex flex-col md:flex-row max-w-7xl w-full mx-auto p-4 gap-4 overflow-hidden">
        <aside ref={sidebarRef} className={`hidden md:flex md:flex-col ${isSidebarCollapsed ? 'md:w-16' : 'md:w-56'} transition-all duration-300 shrink-0 gap-3 justify-between overflow-hidden relative`}>
          <div className="space-y-1.5">
            <div className="flex items-center justify-between pl-2 pb-1">
              <span className={`text-[10px] text-slate-550 font-bold uppercase tracking-wider block ${isSidebarCollapsed ? 'md:hidden' : 'inline'}`}>
                导航控制台
              </span>
              <button
                onClick={() => {
                  setIsSidebarCollapsed((prev) => {
                    localStorage.setItem('tg_sync_sidebar_collapsed_user', String(!prev));
                    return !prev;
                  });
                }}
                className={`p-1.5 rounded-lg text-slate-500 hover:text-white hover:bg-slate-900 transition-colors cursor-pointer flex items-center justify-center ${isSidebarCollapsed ? 'mx-auto' : 'ml-auto'}`}
                title={isSidebarCollapsed ? '展开侧边栏' : '收起侧边栏'}
              >
                {isSidebarCollapsed ? <ChevronRight className="w-3.5 h-3.5" /> : <ChevronLeft className="w-3.5 h-3.5" />}
              </button>
            </div>

            <nav className="flex flex-col gap-1.5">
              {navButton('dashboard', '任务仪表盘', <Tv className="w-4 h-4 shrink-0 text-indigo-400" />)}
              {navButton('files', '已归档媒体文件', <HardDrive className="w-4 h-4 shrink-0 text-indigo-400" />, completedFiles.length)}
              {navButton('config', '同步规则与云盘', <Sliders className="w-4 h-4 shrink-0 text-indigo-400" />)}
              {navButton('accounts', '账号登录管理', <Users className="w-4 h-4 shrink-0 text-indigo-400" />)}
            </nav>
          </div>

          <div className={`hidden md:block bg-slate-900 border border-slate-805/80 p-3.5 rounded-xl space-y-2.5 text-xs transition-opacity duration-350 ${isSidebarCollapsed ? 'opacity-0 h-0 p-0 border-0 pointer-events-none' : 'opacity-100'}`}>
            <div className="flex items-center gap-1.5 text-slate-300 font-semibold border-b border-slate-800 pb-2">
              <Info className="w-3.5 h-3.5 text-indigo-400" />
              <span>运行指示状态</span>
            </div>
            <div className="space-y-1.5 text-[11px] text-slate-450 font-medium">
              <div className="flex justify-between items-center">
                <span>后台自动化守护:</span>
                <span className="text-emerald-400 font-bold flex items-center gap-1">
                  <span className="w-1.5 h-1.5 bg-emerald-500 rounded-full animate-bounce" />
                  运行中
                </span>
              </div>
              <div className="flex justify-between items-center">
                <span>WebDAV 目标:</span>
                <span className="text-indigo-400 font-mono text-[10px] bg-indigo-550/10 px-1 rounded">
                  {cloudConfig.remoteDir || '-'}
                </span>
              </div>
            </div>
          </div>
        </aside>

        <main className="flex-1 overflow-hidden flex flex-col bg-slate-950 border border-slate-900 p-4 sm:p-5 rounded-2xl">
          {statusMessage && (
            <div className="mb-4 bg-indigo-500/10 border border-indigo-500/20 p-3 rounded-lg text-xs text-indigo-300 flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4" />
              <span>{statusMessage}</span>
            </div>
          )}

          {activeTab === 'dashboard' && (
            <div className="flex-1 flex flex-col space-y-4 overflow-hidden">
              <div className="flex items-center justify-between border-b border-slate-900 pb-3">
                <div className="space-y-0.5">
                  <h2 className="text-sm font-semibold text-white">下载同步仪表盘 (进行中)</h2>
                  <p className="text-[10px] text-slate-500 font-medium">同步调度并实时监测 Telegram 队列的流式传输备份状态</p>
                </div>
              </div>
              <div className="flex-1 overflow-y-auto min-h-0">
                <TaskTable
                  tasks={tasks}
                  onPauseTask={(id) => handleTaskAction(id, 'pause')}
                  onResumeTask={(id) => handleTaskAction(id, 'resume')}
                  onDeleteTask={(id) => handleTaskAction(id, 'delete')}
                  onAddTask={() => setStatusMessage('手动创建任务暂未接入后端，请通过监控会话或 Bot 触发下载。')}
                />
              </div>
            </div>
          )}

          {activeTab === 'files' && (
            <div className="flex-1 flex flex-col space-y-4 overflow-hidden">
              <div className="border-b border-slate-900 pb-3">
                <h2 className="text-sm font-semibold text-white font-medium">云端已归档媒体/云盘 (WebDAV Storage)</h2>
                <p className="text-[10px] text-slate-500 font-medium">管理、归类检索已成功上传的 Telegram 文件资源</p>
              </div>
              <div className="flex-1 overflow-hidden">
                <FileManager completedFiles={completedFiles} />
              </div>
            </div>
          )}

          {activeTab === 'config' && (
            <div className="flex-1 overflow-y-auto space-y-4">
              <div className="border-b border-slate-900 pb-3">
                <h2 className="text-sm font-semibold text-white">同步策略与云盘挂载</h2>
                <p className="text-[10px] text-slate-500">配置 WebDAV 目标、媒体过滤规则和远端目录结构</p>
              </div>
              <ConfigPanel
                config={cloudConfig}
                rule={syncRule}
                onSaveConfig={setCloudConfig}
                onSaveRule={setSyncRule}
                onSaveAll={saveConfig}
              />
            </div>
          )}

          {activeTab === 'accounts' && (
            <div className="flex-1 overflow-y-auto space-y-4">
              <div className="border-b border-slate-900 pb-3">
                <h2 className="text-sm font-semibold text-white">Telegram 接入管理 (Client Sessions)</h2>
                <p className="text-[10px] text-slate-500">连接、验证、断开或热启动保存的 Telegram session</p>
              </div>
              <AccountManager
                accounts={accounts}
                activeAccountId={activeAccountId}
                sessionExists={sessionExists}
                onSelectAccount={setActiveAccountId}
                onDisconnectAccount={handleLogout}
                onConnectSavedSession={handleConnectSaved}
                onSendCode={handleSendCode}
                onVerifyCode={handleVerifyCode}
                onVerifyPassword={handleVerifyPassword}
                onRefresh={handleAccountRefresh}
              />
            </div>
          )}
        </main>
      </div>

      <footer className="border-t border-slate-900 py-3.5 px-4 text-center text-[11px] text-slate-600 shrink-0 font-mono">
        <div className="max-w-7xl mx-auto flex flex-col sm:flex-row justify-between items-center gap-2">
          <span>© 2026 Telegram Media Sync Vault.</span>
          <span className="flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
            Backend Daemon Engine Connected
          </span>
        </div>
      </footer>
    </div>
  );
}
