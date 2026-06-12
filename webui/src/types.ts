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
  isActive?: boolean;
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
  status: 'pending' | 'downloading' | 'uploading' | 'syncing' | 'completed' | 'paused' | 'failed';
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
