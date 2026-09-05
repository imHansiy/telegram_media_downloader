/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

export interface TelegramAccount {
  id: string;
  profileId?: string;
  profileName?: string;
  userId?: string;
  phoneNumber: string;
  username: string;
  firstName: string;
  status: 'connected' | 'disconnected' | 'connecting';
  sessionName: string;
  createdAt: string;
  hasSession?: boolean;
  isRunning?: boolean;
  runtimeStatus?: 'starting' | 'running' | 'stopping' | 'stopped' | 'error' | string;
  runtimeMessage?: string;
  botRunning?: boolean;
  runtimeEnabled?: boolean;
  botAccess?: BotAccessConfig;
}

export type MediaType = 'photo' | 'video' | 'document' | 'audio' | 'voice';

export interface SyncRule {
  id: string;
  sourceType: 'channel' | 'group' | 'all';
  targetChannels: string[]; // List of telegram usernames or group IDs
  mediaTypes: MediaType[];
  minSizeMb: number;
  maxSizeMb: number;
  savePathPattern: 'channel_media' | 'channel_date' | 'date_channel';
  autoSync: boolean;
  dateThreshold: string; // ISO date string
}

export interface CloudStorageConfig {
  type: 'webdav' | 'onedrive' | 's3';
  url: string;
  username: string;
  password?: string;
  bucket?: string;
  remoteDir: string;
  downloadRateLimitKb: number; // 0 for unlimited
  uploadRateLimitKb: number; // 0 for unlimited
}

export type BotAccessMode = 'self' | 'allowed' | 'public';

export interface BotAccessConfig {
  mode: BotAccessMode;
  allowedUsers: string[];
}

export type BotStartupNotificationMode = 'off' | 'admin' | 'status_chat';

export interface VideoClassifierConfig {
  enable: boolean;
  apiBase: string;
  apiKey: string;
  model: string;
  maxFrames: number;
  timeoutSec: number;
  maxVideoSizeMb: number;
  minConfidence: number;
}

export const defaultVideoClassifier: VideoClassifierConfig = {
  enable: false,
  apiBase: 'https://api.openai.com/v1',
  apiKey: '',
  model: 'gpt-4o-mini',
  maxFrames: 4,
  timeoutSec: 60,
  maxVideoSizeMb: 2048,
  minConfidence: 0.4,
};

export interface BotStatusConfig {
  startupNotificationMode: BotStartupNotificationMode;
  statusChatId: string;
}

export interface SyncTask {
  id: string;
  profileId?: string;
  type: MediaType;
  sourceId: string; // e.g. "@durov" or "t.me/c/12345/2"
  sourceName: string; // Friendly name of channel
  filename: string;
  sizeBytes: number;
  createdAt: string;
  downloadProgress: number; // 0 to 100
  uploadProgress: number; // 0 to 100
  status: 'pending' | 'downloading' | 'uploading' | 'syncing' | 'completed' | 'paused' | 'failed' | 'upload_failed';
  aiStatus?: string; // AI 分类阶段：抽帧中 / 语音转写中 / AI 识图中 / 分类完成:nsfw/子类
  aiSummary?: string; // AI 生成的视频一句话简介（基于转写文本）
  aiTags?: string[]; // AI 内容标签
  speedKb: number; // Download / Upload current combined transfer speed
  remotePath: string; // Target location on cloud drive
  errorMsg?: string;
}

export interface CompletedFile {
  id: string;
  profileId?: string;
  name: string;
  type: MediaType;
  sizeBytes: number;
  completedAt: string;
  remotePath: string;
  relativePath?: string; // 真实目录结构（含 AI 类别层），用于文件夹分组
  aiSummary?: string; // AI 生成的视频一句话简介
  sourceName: string;
  sourceId: string;
}

export interface Level2Folder {
  name: string; // e.g. "Photos", "Videos", "Documents"
  files: CompletedFile[];
}

export interface Level1Folder {
  id: string;
  name: string; // e.g. "@durov_channel", "Crypto News" (usually Channel/source name or Date)
  latestCompletedAt: string; // ISO date string for sorting level 1
  subFolders: {
    [subFolderName: string]: Level2Folder;
  };
}
