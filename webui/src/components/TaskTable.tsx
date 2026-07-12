/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useMemo, useState } from 'react';
import { 
  SyncTask, 
  MediaType 
} from '../types';
import { 
  Pause, 
  Play, 
  Trash2, 
  Download, 
  Upload, 
  AlertCircle, 
  CheckCircle2, 
  Search, 
  PlusCircle, 
  FileText, 
  Image, 
  Video, 
  Music, 
  Mic, 
  Loader2,
  RotateCcw,
  ChevronLeft,
  ChevronRight,
  Eye,
  X,
  ArrowUpRight,
  HardDrive,
} from 'lucide-react';
import { buildWebdavResourceUrl, isPdfFile } from '../webdavPreview';

interface TaskTableProps {
  tasks: SyncTask[];
  onPauseTask: (id: string) => void;
  onResumeTask: (id: string) => void;
  onResetUpload: (id: string) => void;
  onDeleteTask: (id: string, deleteRemote: boolean) => Promise<void>;
  onAddTask: (channel: string, filename: string, type: MediaType, sizeMb: number) => void;
}

/** 已完成任务可预览：需有云端路径 */
function canPreviewTask(task: SyncTask): boolean {
  return task.status === 'completed' && Boolean(task.remotePath?.trim());
}

function taskAsPreviewFile(task: SyncTask) {
  return {
    name: task.filename,
    type: task.type,
    remotePath: task.remotePath,
    sizeBytes: task.sizeBytes,
    sourceName: task.sourceName,
  };
}

const PAGE_SIZE = 5;

const MEDIA_TYPE_LABEL: Record<MediaType, string> = {
  photo: '图片',
  video: '视频',
  document: '文档',
  audio: '音频',
  voice: '语音',
};

/** 仪表盘文件名缩短：保留扩展名，中间用 … 折叠 */
function shortFilename(name: string, max = 28): string {
  if (!name) return '-';
  if (name.length <= max) return name;
  const dot = name.lastIndexOf('.');
  const ext = dot > 0 && name.length - dot <= 8 ? name.slice(dot) : '';
  const base = ext ? name.slice(0, -ext.length) : name;
  const keep = Math.max(8, max - ext.length - 1);
  const head = Math.ceil(keep * 0.6);
  const tail = keep - head;
  if (base.length <= keep) return name.slice(0, max - 1) + '…';
  return `${base.slice(0, head)}…${base.slice(-tail)}${ext}`;
}

/** 来源展示缩短：优先显示频道名，过长则截断 */
function shortSource(sourceName: string, sourceId: string, max = 18): string {
  const label = (sourceName || sourceId || '').trim() || '-';
  if (label.length <= max) return label;
  return `${label.slice(0, max - 1)}…`;
}

export function TaskTable({ 
  tasks, 
  onPauseTask, 
  onResumeTask, 
  onResetUpload,
  onDeleteTask, 
  onAddTask 
}: TaskTableProps) {
  const [searchTerm, setSearchTerm] = useState('');
  const [filterStatus, setFilterStatus] = useState<string>('all');
  const [page, setPage] = useState(1);
  // 已完成任务 WebDAV 预览（图片/视频/音频/PDF）
  const [previewTask, setPreviewTask] = useState<SyncTask | null>(null);
  const [previewError, setPreviewError] = useState('');
  const [deleteTask, setDeleteTask] = useState<SyncTask | null>(null);
  const [deleteMode, setDeleteMode] = useState<'task' | 'remote' | null>(null);
  const [deleteError, setDeleteError] = useState('');
  
  // States for Manuel Add Simulation Link or Channel ID
  const [isOpenAdd, setIsOpenAdd] = useState(false);
  const [manualChannel, setManualChannel] = useState('@durov');
  const [manualFilename, setManualFilename] = useState('telegram_whitepaper_v2.pdf');
  const [manualType, setManualType] = useState<MediaType>('document');
  const [manualSize, setManualSize] = useState<number>(34.5);

  const formatSize = (bytes: number) => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  };

  const getMediaIcon = (type: MediaType) => {
    switch (type) {
      case 'photo':
        return <Image className="w-4 h-4 text-emerald-500" />;
      case 'video':
        return <Video className="w-4 h-4 text-indigo-500" />;
      case 'document':
        return <FileText className="w-4 h-4 text-sky-500" />;
      case 'audio':
        return <Music className="w-4 h-4 text-amber-500" />;
      case 'voice':
        return <Mic className="w-4 h-4 text-rose-500" />;
    }
  };

  const filteredTasks = useMemo(() => tasks.filter(task => {
    const matchesSearch = 
      task.filename.toLowerCase().includes(searchTerm.toLowerCase()) ||
      task.sourceName.toLowerCase().includes(searchTerm.toLowerCase()) ||
      task.sourceId.toLowerCase().includes(searchTerm.toLowerCase());
    
    if (filterStatus === 'all') return matchesSearch;
    if (filterStatus === 'syncing') return matchesSearch && (task.status === 'downloading' || task.status === 'uploading' || task.status === 'syncing');
    // “失败”筛选包含下载失败与本机已完整的上传失败
    if (filterStatus === 'failed') {
      return matchesSearch && (task.status === 'failed' || task.status === 'upload_failed');
    }
    return matchesSearch && task.status === filterStatus;
  }), [tasks, searchTerm, filterStatus]);

  const totalPages = Math.max(1, Math.ceil(filteredTasks.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const pageStart = (currentPage - 1) * PAGE_SIZE;
  const pagedTasks = filteredTasks.slice(pageStart, pageStart + PAGE_SIZE);

  // 筛选/搜索变化时回到第 1 页，避免空页
  useEffect(() => {
    setPage(1);
  }, [searchTerm, filterStatus]);

  // 任务数量减少导致当前页越界时回退
  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  useEffect(() => {
    setPreviewError('');
  }, [previewTask?.id]);

  // Esc 关闭预览层
  useEffect(() => {
    if (!previewTask) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setPreviewTask(null);
        setPreviewError('');
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [previewTask]);

  const openPreview = (task: SyncTask) => {
    if (!canPreviewTask(task)) return;
    setPreviewError('');
    setPreviewTask(task);
  };

  const closePreview = () => {
    setPreviewTask(null);
    setPreviewError('');
  };

  const confirmDeleteTask = async (deleteRemote: boolean) => {
    if (!deleteTask) return;
    setDeleteMode(deleteRemote ? 'remote' : 'task');
    setDeleteError('');
    try {
      await onDeleteTask(deleteTask.id, deleteRemote);
      setDeleteTask(null);
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : '删除失败，请稍后重试。');
    } finally {
      setDeleteMode(null);
    }
  };

  const handleCreateTask = (e: React.FormEvent) => {
    e.preventDefault();
    if (!manualChannel || !manualFilename) return;
    onAddTask(manualChannel, manualFilename, manualType, manualSize);
    setIsOpenAdd(false);
    // Reset file helper defaults nicely
    setManualFilename('');
  };

  const getStatusBadge = (status: SyncTask['status']) => {
    switch (status) {
      case 'downloading':
        return (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            <Loader2 className="w-3 h-3 animate-spin" />
            下载中
          </span>
        );
      case 'uploading':
        return (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
            <Loader2 className="w-3 h-3 animate-spin"/>
            同步上传中
          </span>
        );
      case 'syncing':
        return (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">
            <Loader2 className="w-3 h-3 animate-spin"/>
            处理中
          </span>
        );
      case 'pending':
        return (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium bg-slate-500/10 text-slate-400 border border-slate-500/20">
            排队中
          </span>
        );
      case 'paused':
        return (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium bg-yellow-500/10 text-yellow-400 border border-yellow-500/20">
            已暂停
          </span>
        );
      case 'completed':
        return (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium bg-sky-500/10 text-sky-400 border border-sky-500/20">
            <CheckCircle2 className="w-3 h-3" />
            已完成
          </span>
        );
      case 'failed':
        return (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium bg-rose-500/10 text-rose-400 border border-rose-500/20">
            <AlertCircle className="w-3 h-3" />
            失败
          </span>
        );
      case 'upload_failed':
        return (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium bg-amber-500/10 text-amber-400 border border-amber-500/20">
            <Upload className="w-3 h-3" />
            上传失败
          </span>
        );
    }
  };

  return (
    <div className="space-y-4">
      {/* Search and control bar */}
      <div className="flex flex-col sm:flex-row gap-3 justify-between items-stretch sm:items-center">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative max-w-xs w-full">
            <span className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-slate-500">
              <Search className="w-4 h-4" />
            </span>
            <input
              id="search-tasks-input"
              type="text"
              className="block w-full pl-9 pr-3 py-1.5 text-xs bg-slate-800/80 border border-slate-700 rounded-lg text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500/80 transition-colors"
              placeholder="搜索源频道、文件名..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
          </div>

          <div className="flex bg-slate-800/90 p-0.5 rounded-lg border border-slate-700/80">
            {[
              { id: 'all', label: '全部任务' },
              { id: 'syncing', label: '传输中' },
              { id: 'paused', label: '已暂停' },
              { id: 'completed', label: '成功' },
              { id: 'failed', label: '失败' }
            ].map(tab => (
              <button
                id={`tab-filter-${tab.id}`}
                key={tab.id}
                onClick={() => setFilterStatus(tab.id)}
                className={`px-3 py-1 text-xs rounded-md transition-all font-medium ${
                  filterStatus === tab.id 
                    ? 'bg-slate-700 text-slate-100 shadow-sm' 
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        <button
          id="btn-trigger-add-task"
          onClick={() => setIsOpenAdd(!isOpenAdd)}
          className="flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white shadow-md shadow-indigo-950/20 transition-all cursor-pointer"
        >
          <PlusCircle className="w-4 h-4" />
          创建手动同步
        </button>
      </div>

      {/* Manual Task Submission Panel */}
      {isOpenAdd && (
        <form 
          id="form-add-manual-task"
          onSubmit={handleCreateTask} 
          className="bg-slate-800/60 border border-slate-700/80 p-4 rounded-xl space-y-3 animate-fadeIn"
        >
          <div className="flex items-center justify-between border-b border-slate-700 pb-2">
            <h3 className="text-xs font-semibold text-slate-200">创建新同步下载任务</h3>
            <span className="text-[10px] text-slate-400">支持模拟从特定 Telegram 链接或对话中提取媒体</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <div className="space-y-1">
              <label className="block text-[11px] text-slate-400">Telegram 来源 (名称/用户名/ID)</label>
              <input
                id="input-manual-channel"
                type="text"
                required
                className="w-full text-xs bg-slate-950/60 border border-slate-700 rounded-lg p-2 text-slate-300 focus:outline-none focus:border-indigo-500"
                placeholder="例如: @durov, t.me/telegram_news"
                value={manualChannel}
                onChange={(e) => setManualChannel(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <label className="block text-[11px] text-slate-400">保存文件名</label>
              <input
                id="input-manual-filename"
                type="text"
                required
                className="w-full text-xs bg-slate-950/60 border border-slate-700 rounded-lg p-2 text-slate-300 focus:outline-none focus:border-indigo-500"
                placeholder="例如: video_2026_rec.mp4"
                value={manualFilename}
                onChange={(e) => setManualFilename(e.target.value)}
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <label className="block text-[11px] text-slate-400">媒体类型</label>
                <select
                  id="select-manual-type"
                  value={manualType}
                  onChange={(e) => setManualType(e.target.value as MediaType)}
                  className="w-full text-xs bg-slate-950 px-2 py-2 border border-slate-700 rounded-lg text-slate-300 focus:outline-none focus:border-indigo-500"
                >
                  <option value="photo">图片 (Photo)</option>
                  <option value="video">视频 (Video)</option>
                  <option value="document">文档 (Document)</option>
                  <option value="audio">音频 (Audio)</option>
                  <option value="voice">语音 (Voice)</option>
                </select>
              </div>
              <div className="space-y-1">
                <label className="block text-[11px] text-slate-400 font-medium">大小 (MB)</label>
                <input
                  id="input-manual-size"
                  type="number"
                  step="0.1"
                  required
                  min="0.1"
                  className="w-full text-xs bg-slate-950/60 border border-slate-700 rounded-lg p-2 text-slate-300 focus:outline-none focus:border-indigo-500"
                  value={manualSize}
                  onChange={(e) => setManualSize(parseFloat(e.target.value) || 1)}
                />
              </div>
            </div>
            <div className="flex items-end">
              <button
                id="btn-submit-manual-task"
                type="submit"
                className="w-full py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold transition-colors cursor-pointer"
              >
                加入同步队列
              </button>
            </div>
          </div>
        </form>
      )}

      {/* Main Task List Table */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-xl">
        <div className="overflow-x-auto">
          {filteredTasks.length === 0 ? (
            <div className="py-12 text-center space-y-2">
              <AlertCircle className="w-8 h-8 text-slate-600 mx-auto" />
              <p className="text-xs text-slate-400">暂无符合当前筛选条件的任务记录</p>
              <p className="text-[11px] text-slate-600">新的下载、上传、成功和失败状态会自动显示在这里</p>
            </div>
          ) : (
            <>
              {/* DESKTOP TABLE VIEW */}
              <table className="hidden md:table w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-slate-800 bg-slate-950/60 text-[11px] text-slate-400 font-medium uppercase tracking-wider">
                    <th className="py-2.5 px-3 w-12 text-center" title="媒体类型">类型</th>
                    <th className="py-2.5 px-3">文件</th>
                    <th className="py-2.5 px-3">大小</th>
                    <th className="py-2.5 px-3">下载</th>
                    <th className="py-2.5 px-3">上传</th>
                    <th className="py-2.5 px-3">速度</th>
                    <th className="py-2.5 px-3">状态</th>
                    <th className="py-2.5 px-3 text-right">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800 text-xs">
                  {pagedTasks.map((task) => {
                    const isDownloading = task.status === 'downloading';
                    const isUploading = task.status === 'uploading';
                    
                    return (
                      <tr 
                        id={`task-row-${task.id}`}
                        key={task.id} 
                        className="hover:bg-slate-850/45 transition-colors group"
                      >
                        {/* 媒体类型：仅图标，悬停显示类型名 */}
                        <td className="py-2.5 px-3 whitespace-nowrap">
                          <div
                            className="mx-auto w-fit p-1.5 rounded-lg bg-slate-800 border border-slate-700/80"
                            title={MEDIA_TYPE_LABEL[task.type] || task.type}
                          >
                            {getMediaIcon(task.type)}
                          </div>
                        </td>

                        {/* 文件信息：短文件名 + 短来源；已完成可点文件名打开预览 */}
                        <td className="py-2.5 px-3 max-w-[11rem]">
                          <div className="space-y-0.5 min-w-0">
                            <div
                              className={`font-medium text-slate-200 truncate group-hover:text-slate-100 transition-colors text-[12px] ${
                                canPreviewTask(task) ? 'cursor-pointer hover:text-sky-400' : ''
                              }`}
                              title={canPreviewTask(task) ? `${task.filename}（点击预览）` : task.filename}
                              onClick={() => canPreviewTask(task) && openPreview(task)}
                            >
                              {shortFilename(task.filename)}
                            </div>
                            <div
                              className="text-[10px] text-indigo-400/90 font-mono truncate"
                              title={`${task.sourceName || ''} ${task.sourceId || ''}`.trim()}
                            >
                              {shortSource(task.sourceName, task.sourceId)}
                            </div>
                          </div>
                        </td>

                        {/* Size */}
                        <td className="py-2.5 px-3 whitespace-nowrap text-slate-300 font-mono text-[11px]">
                          {formatSize(task.sizeBytes)}
                        </td>

                        {/* Download Progress */}
                        <td className="py-2.5 px-3 min-w-[100px]">
                          <div className="space-y-1">
                            <div className="flex items-center justify-between text-[10px] font-mono">
                              <span className="text-slate-400 flex items-center gap-1">
                                <Download className={`w-2.5 h-2.5 ${isDownloading ? 'text-emerald-400 animate-bounce' : 'text-slate-500'}`} />
                                {task.status === 'completed' ? '100%' : task.status === 'uploading' ? 'OK' : `${task.downloadProgress.toFixed(0)}%`}
                              </span>
                            </div>
                            <div className="h-1.5 w-full bg-slate-800 rounded-full overflow-hidden">
                              <div 
                                className={`h-full rounded-full transition-all duration-300 ${
                                  task.status === 'failed' 
                                    ? 'bg-rose-500' 
                                    : task.status === 'paused' 
                                    ? 'bg-yellow-600/60' 
                                    : task.status === 'completed' || task.status === 'uploading'
                                    ? 'bg-emerald-600'
                                    : 'bg-emerald-500'
                                }`}
                                style={{ width: `${task.status === 'completed' || task.status === 'uploading' ? 100 : task.downloadProgress}%` }}
                              />
                            </div>
                          </div>
                        </td>

                        {/* Upload Progress */}
                        <td className="py-2.5 px-3 min-w-[100px]">
                          <div className="space-y-1">
                            <div className="flex items-center justify-between text-[10px] font-mono">
                              <span className="text-slate-400 flex items-center gap-1">
                                <Upload className={`w-2.5 h-2.5 ${isUploading ? 'text-indigo-400 animate-pulse' : 'text-slate-500'}`} />
                                {task.status === 'completed' ? '100%' : `${task.uploadProgress.toFixed(0)}%`}
                              </span>
                            </div>
                            <div className="h-1.5 w-full bg-slate-800 rounded-full overflow-hidden">
                              <div 
                                className={`h-full rounded-full transition-all duration-300 ${
                                  task.status === 'failed' 
                                    ? 'bg-rose-500' 
                                    : task.status === 'paused' 
                                    ? 'bg-yellow-600/60' 
                                    : task.status === 'completed'
                                    ? 'bg-indigo-600'
                                    : task.downloadProgress < 100
                                    ? 'bg-slate-700'
                                    : 'bg-indigo-500'
                                }`}
                                style={{ width: `${task.status === 'completed' ? 100 : task.uploadProgress}%` }}
                              />
                            </div>
                          </div>
                        </td>

                        {/* Combined Sync Speed */}
                        <td className="py-2.5 px-3 whitespace-nowrap text-[11px] font-mono text-slate-300">
                          {task.speedKb > 0 && (task.status === 'downloading' || task.status === 'uploading' || task.status === 'syncing') ? (
                            <div className="flex items-center gap-1">
                              <span className="text-slate-200">
                                {task.speedKb >= 1024 
                                  ? `${(task.speedKb / 1024).toFixed(1)}M/s` 
                                  : `${task.speedKb}K/s`}
                              </span>
                            </div>
                          ) : (
                            <span className="text-slate-600">-</span>
                          )}
                        </td>

                        {/* State Badge */}
                        <td className="py-2.5 px-3 whitespace-nowrap">
                          {getStatusBadge(task.status)}
                          {task.errorMsg && (
                            <p className="text-[10px] text-rose-400 mt-1 max-w-[100px] truncate" title={task.errorMsg}>
                              {task.errorMsg}
                            </p>
                          )}
                        </td>

                        {/* Row actions */}
                        <td className="py-2.5 px-3 whitespace-nowrap text-right">
                          <div className="flex items-center justify-end gap-1">
                            {canPreviewTask(task) && (
                              <button
                                id={`btn-preview-${task.id}`}
                                onClick={() => openPreview(task)}
                                className="p-1 text-sky-400 hover:text-sky-300 hover:bg-slate-800 rounded transition-colors cursor-pointer"
                                title="预览已完成文件"
                              >
                                <Eye className="w-3.5 h-3.5" />
                              </button>
                            )}
                            {task.status === 'paused' && (
                              <button
                                id={`btn-resume-${task.id}`}
                                onClick={() => onResumeTask(task.id)}
                                className="p-1 text-emerald-400 hover:text-emerald-300 hover:bg-slate-800 rounded transition-colors cursor-pointer"
                                title="继续传输"
                              >
                                <Play className="w-3.5 h-3.5" />
                              </button>
                            )}
                            {(task.status === 'downloading' || task.status === 'uploading' || task.status === 'pending') && (
                              <button
                                id={`btn-pause-${task.id}`}
                                onClick={() => onPauseTask(task.id)}
                                className="p-1 text-yellow-500 hover:text-yellow-400 hover:bg-slate-800 rounded transition-colors cursor-pointer"
                                title="暂停传输"
                              >
                                <Pause className="w-3.5 h-3.5" />
                              </button>
                            )}
                            {(task.status === 'failed' || task.status === 'upload_failed') && (
                              <button
                                id={`btn-reset-upload-${task.id}`}
                                onClick={() => onResetUpload(task.id)}
                                className="p-1 text-indigo-400 hover:text-indigo-300 hover:bg-slate-800 rounded transition-colors cursor-pointer"
                                title={task.status === 'upload_failed' ? '清理云端半成品并只重传本地文件' : '删除云端异常文件并重新上传'}
                              >
                                <RotateCcw className="w-3.5 h-3.5" />
                              </button>
                            )}
                            <button
                              id={`btn-delete-${task.id}`}
                              onClick={() => setDeleteTask(task)}
                              className="p-1 text-slate-500 hover:text-rose-400 hover:bg-slate-800 rounded transition-colors cursor-pointer"
                              title="取消并删除"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>

              {/* MOBILE RESPONSIVE CARD VIEW */}
              <div className="md:hidden divide-y divide-slate-800/80 bg-slate-900">
                {pagedTasks.map((task) => {
                  const isDownloading = task.status === 'downloading';
                  const isUploading = task.status === 'uploading';
                  
                  return (
                    <div 
                      id={`task-card-mobile-${task.id}`}
                      key={task.id}
                      className="p-3 space-y-2.5 bg-slate-900/50 hover:bg-slate-850/10 active:bg-slate-850/20 transition-all"
                    >
                      {/* Top: Icons, title and status */}
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex items-center gap-2 min-w-0">
                          <div
                            className="p-1.5 rounded-lg bg-slate-800 border border-slate-705/80 shrink-0"
                            title={MEDIA_TYPE_LABEL[task.type] || task.type}
                          >
                            {getMediaIcon(task.type)}
                          </div>
                          <div className="min-w-0">
                            <h4 className="font-semibold text-slate-100 truncate text-[12px] leading-snug" title={task.filename}>
                              {shortFilename(task.filename, 24)}
                            </h4>
                            <span className="text-[10px] text-slate-450 font-mono">
                              {formatSize(task.sizeBytes)}
                              <span className="text-slate-600 mx-1">·</span>
                              <span className="text-indigo-400/90" title={`${task.sourceName || ''} ${task.sourceId || ''}`.trim()}>
                                {shortSource(task.sourceName, task.sourceId, 14)}
                              </span>
                            </span>
                          </div>
                        </div>
                        <div className="shrink-0">
                          {getStatusBadge(task.status)}
                        </div>
                      </div>

                      {/* Progress grid elements */}
                      <div className="grid grid-cols-2 gap-3 bg-slate-950/20 p-2.5 rounded-lg border border-slate-850/40">
                        <div className="space-y-1">
                          <div className="flex items-center justify-between text-[9px] font-mono text-slate-455">
                            <span className="flex items-center gap-0.5">
                              <Download className={`w-2.5 h-2.5 ${isDownloading ? 'text-emerald-450 animate-bounce' : 'text-slate-500'}`} />
                              TG下载
                            </span>
                            <span className="text-slate-300">
                              {task.status === 'completed' ? '100%' : task.status === 'uploading' ? '已落盘' : `${task.downloadProgress.toFixed(0)}%`}
                            </span>
                          </div>
                          <div className="h-1 w-full bg-slate-800 rounded-full overflow-hidden">
                            <div 
                              className={`h-full rounded-full transition-all duration-300 ${
                                task.status === 'failed' ? 'bg-rose-500' : task.status === 'paused' ? 'bg-yellow-600/60' : 'bg-emerald-500'
                              }`}
                              style={{ width: `${task.status === 'completed' || task.status === 'uploading' ? 100 : task.downloadProgress}%` }}
                            />
                          </div>
                        </div>

                        <div className="space-y-1">
                          <div className="flex items-center justify-between text-[9px] font-mono text-slate-455">
                            <span className="flex items-center gap-0.5">
                              <Upload className={`w-2.5 h-2.5 ${isUploading ? 'text-indigo-450 animate-pulse' : 'text-slate-500'}`} />
                              云盘上传
                            </span>
                            <span className="text-slate-300">
                              {task.status === 'completed' ? '100%' : `${task.uploadProgress.toFixed(0)}%`}
                            </span>
                          </div>
                          <div className="h-1 w-full bg-slate-850 rounded-full overflow-hidden">
                            <div 
                              className={`h-full rounded-full transition-all duration-305 ${
                                task.status === 'failed' ? 'bg-rose-500' : task.status === 'paused' ? 'bg-yellow-600/60' : 'bg-indigo-500'
                              }`}
                              style={{ width: `${task.status === 'completed' ? 100 : task.uploadProgress}%` }}
                            />
                          </div>
                        </div>
                      </div>

                      {/* Speed & Touch-friendly Action targets */}
                      <div className="flex items-center justify-between pt-2 border-t border-slate-800/60">
                        <div className="text-[10px] font-mono text-slate-400">
                          {task.speedKb > 0 && (task.status === 'downloading' || task.status === 'uploading' || task.status === 'syncing') ? (
                            <span className="bg-slate-950/60 px-1.5 py-0.5 rounded border border-slate-800 text-indigo-400">
                              ⚡{task.speedKb >= 1024 ? `${(task.speedKb / 1024).toFixed(1)}M/s` : `${task.speedKb}K/s`}
                            </span>
                          ) : (
                            <span className="text-slate-550">-</span>
                          )}
                          {task.errorMsg && (
                            <span className="text-rose-400 ml-1 truncate max-w-[100px]" title={task.errorMsg}>
                              ({task.errorMsg})
                            </span>
                          )}
                        </div>

                        <div className="flex items-center gap-1 bg-slate-950/40 p-0.5 rounded-lg border border-slate-855">
                          {canPreviewTask(task) && (
                            <button
                              id={`btn-preview-mobile-${task.id}`}
                              onClick={() => openPreview(task)}
                              className="p-1 px-2.5 text-sky-400 hover:text-sky-300 active:bg-slate-800 rounded transition-colors"
                              title="预览"
                            >
                              <Eye className="w-4 h-4 inline" />
                              <span className="text-[10px] ml-0.5 font-bold">预览</span>
                            </button>
                          )}
                          {task.status === 'paused' && (
                            <button
                              id={`btn-resume-mobile-${task.id}`}
                              onClick={() => onResumeTask(task.id)}
                              className="p-1 px-2.5 text-emerald-450 hover:text-emerald-300 active:bg-slate-800 rounded transition-colors"
                              title="继续"
                            >
                              <Play className="w-4 h-4 inline" />
                              <span className="text-[10px] ml-0.5 font-bold">继续</span>
                            </button>
                          )}
                          {(task.status === 'downloading' || task.status === 'uploading' || task.status === 'pending') && (
                            <button
                              id={`btn-pause-mobile-${task.id}`}
                              onClick={() => onPauseTask(task.id)}
                              className="p-1 px-2.5 text-yellow-500 hover:text-yellow-400 active:bg-slate-800 rounded transition-colors"
                              title="暂停"
                            >
                              <Pause className="w-4 h-4 inline" />
                              <span className="text-[10px] ml-0.5 font-bold">暂停</span>
                            </button>
                          )}
                          {(task.status === 'failed' || task.status === 'upload_failed') && (
                            <button
                              id={`btn-reset-upload-mobile-${task.id}`}
                              onClick={() => onResetUpload(task.id)}
                              className="p-1 px-2.5 text-indigo-400 hover:text-indigo-300 active:bg-slate-800 rounded transition-colors"
                              title="重置上传"
                            >
                              <RotateCcw className="w-4 h-4 inline" />
                              <span className="text-[10px] ml-0.5 font-bold">重传</span>
                            </button>
                          )}
                          <button
                            id={`btn-delete-mobile-${task.id}`}
                            onClick={() => setDeleteTask(task)}
                            className="p-1 px-1.5 text-slate-400 hover:text-rose-455 active:bg-slate-800 rounded transition-colors"
                            title="删除"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
        {/* 底栏：统计 + 分页（默认每页 5 条） */}
        <div className="bg-slate-950/40 border-t border-slate-800 py-2.5 px-4 flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-2 text-[11px] text-slate-400">
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            <span>传输 <strong className="text-indigo-400">{tasks.filter(t => t.status === 'downloading' || t.status === 'uploading').length}</strong></span>
            <span>暂停 <strong className="text-yellow-500">{tasks.filter(t => t.status === 'paused').length}</strong></span>
            <span>排队 <strong className="text-slate-300">{tasks.filter(t => t.status === 'pending').length}</strong></span>
            <span>成功 <strong className="text-emerald-500">{tasks.filter(t => t.status === 'completed').length}</strong></span>
            <span>失败 <strong className="text-rose-500">{tasks.filter(t => t.status === 'failed' || t.status === 'upload_failed').length}</strong></span>
          </div>

          {filteredTasks.length > 0 && (
            <div className="flex items-center justify-between sm:justify-end gap-2">
              <span className="text-slate-500 font-mono text-[10px]">
                {pageStart + 1}-{Math.min(pageStart + PAGE_SIZE, filteredTasks.length)} / {filteredTasks.length}
              </span>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  id="btn-task-page-prev"
                  disabled={currentPage <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  className="p-1 rounded-md border border-slate-700 bg-slate-900 text-slate-300 hover:text-slate-100 hover:bg-slate-800 disabled:opacity-40 disabled:pointer-events-none cursor-pointer"
                  title="上一页"
                >
                  <ChevronLeft className="w-3.5 h-3.5" />
                </button>
                <span className="min-w-[3.5rem] text-center font-mono text-[10px] text-slate-300">
                  {currentPage} / {totalPages}
                </span>
                <button
                  type="button"
                  id="btn-task-page-next"
                  disabled={currentPage >= totalPages}
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  className="p-1 rounded-md border border-slate-700 bg-slate-900 text-slate-300 hover:text-slate-100 hover:bg-slate-800 disabled:opacity-40 disabled:pointer-events-none cursor-pointer"
                  title="下一页"
                >
                  <ChevronRight className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 已完成任务 WebDAV 流式预览：图片/视频/音频/PDF，其它类型引导下载 */}
      {previewTask && canPreviewTask(previewTask) && (() => {
        const file = taskAsPreviewFile(previewTask);
        return (
          <div
            className="fixed inset-0 z-[100] flex flex-col justify-between bg-slate-950/95 backdrop-blur-md animate-fadeIn"
            onClick={closePreview}
            role="dialog"
            aria-modal="true"
            aria-label="已完成任务预览"
          >
            <div
              className="flex items-center justify-between p-4 bg-slate-900/60 border-b border-slate-850/80 backdrop-blur"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center gap-3 min-w-0 pr-6">
                <div className="p-2 rounded-lg bg-slate-950/80 border border-slate-805 text-indigo-400 shrink-0">
                  {getMediaIcon(previewTask.type)}
                </div>
                <div className="min-w-0">
                  <h3
                    className="text-sm font-bold text-slate-100 truncate font-sans max-w-[240px] sm:max-w-xl"
                    title={previewTask.filename}
                  >
                    {previewTask.filename}
                  </h3>
                  <p className="text-[10px] font-mono text-slate-500 mt-0.5 flex flex-wrap gap-2">
                    <span>大小: {formatSize(previewTask.sizeBytes)}</span>
                    <span>•</span>
                    <span>{MEDIA_TYPE_LABEL[previewTask.type] || previewTask.type}</span>
                    <span>•</span>
                    <span className="truncate max-w-[200px]" title={previewTask.sourceName}>
                      来源: {previewTask.sourceName || previewTask.sourceId}
                    </span>
                  </p>
                </div>
              </div>
              <button
                id="btn-close-task-preview"
                type="button"
                onClick={closePreview}
                className="p-2 rounded-xl bg-slate-850 hover:bg-slate-755 text-slate-300 hover:text-slate-100 transition-all cursor-pointer border border-slate-800"
                title="关闭预览"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="flex-1 flex items-center justify-center p-4 sm:p-8" onClick={closePreview}>
              <div className="max-w-4xl w-full max-h-[70vh] flex items-center justify-center" onClick={(e) => e.stopPropagation()}>
                {previewError ? (
                  <div className="flex flex-col items-center justify-center text-center p-8 bg-slate-900 border border-rose-500/20 rounded-2xl max-w-md mx-auto shadow-2xl">
                    <HardDrive className="w-12 h-12 text-rose-400 mb-3" />
                    <h4 className="text-sm font-bold text-slate-200 mb-1">云端媒体加载失败</h4>
                    <p className="text-[11px] text-slate-500 mb-5 max-w-sm">{previewError}</p>
                    <a
                      href={buildWebdavResourceUrl(file, true)}
                      className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-xs font-bold text-white rounded-lg transition-all shadow-lg flex items-center gap-1.5"
                    >
                      <Download className="w-3.5 h-3.5" />
                      下载原文件
                    </a>
                  </div>
                ) : previewTask.type === 'photo' ? (
                  <img
                    src={buildWebdavResourceUrl(file)}
                    alt={previewTask.filename}
                    className="max-w-full max-h-[70vh] rounded-xl object-contain shadow-2xl border border-slate-805/50 mx-auto select-none"
                    onError={() => setPreviewError('图片读取失败，请检查 WebDAV 文件是否仍然存在。')}
                  />
                ) : previewTask.type === 'video' ? (
                  <video
                    src={buildWebdavResourceUrl(file)}
                    controls
                    autoPlay
                    playsInline
                    preload="metadata"
                    onError={() => setPreviewError('视频无法解码或 WebDAV 不支持字节范围读取。')}
                    className="max-w-full max-h-[70vh] rounded-xl shadow-2xl border border-slate-805/50 mx-auto bg-black"
                  />
                ) : previewTask.type === 'audio' || previewTask.type === 'voice' ? (
                  <div className="bg-slate-900 border border-slate-805 rounded-2xl p-6 sm:p-10 max-w-md w-full shadow-2xl text-center space-y-6">
                    <div className="mx-auto w-16 h-16 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 flex items-center justify-center">
                      {getMediaIcon(previewTask.type)}
                    </div>
                    <div className="space-y-1.5">
                      <h4 className="text-sm font-bold text-slate-200 line-clamp-2 px-2">{previewTask.filename}</h4>
                      <p className="text-[10px] font-mono text-slate-500">大小: {formatSize(previewTask.sizeBytes)}</p>
                    </div>
                    <audio
                      src={buildWebdavResourceUrl(file)}
                      controls
                      autoPlay
                      preload="metadata"
                      onError={() => setPreviewError('音频无法解码或云端文件读取失败。')}
                      className="w-full"
                    />
                  </div>
                ) : isPdfFile({ name: previewTask.filename }) ? (
                  <iframe
                    src={`${buildWebdavResourceUrl(file)}#toolbar=1&navpanes=0`}
                    title={`预览 ${previewTask.filename}`}
                    className="w-full h-[70vh] rounded-xl bg-white shadow-2xl border border-slate-805/50"
                  />
                ) : (
                  <div className="bg-slate-900 border border-slate-805 rounded-2xl p-8 max-w-sm w-full text-center space-y-5 shadow-2xl">
                    <div className="mx-auto w-12 h-12 rounded-xl bg-sky-500/10 border border-sky-500/20 text-sky-400 flex items-center justify-center">
                      {getMediaIcon(previewTask.type)}
                    </div>
                    <div className="space-y-1.5">
                      <h4 className="text-xs font-semibold text-slate-300 break-all">{previewTask.filename}</h4>
                      <p className="text-[10px] font-mono text-slate-500">
                        {(MEDIA_TYPE_LABEL[previewTask.type] || previewTask.type).toUpperCase()} 文件
                      </p>
                    </div>
                    <p className="text-[10px] text-slate-450 leading-relaxed">
                      此类型暂不支持应用内预览，可下载原文件或在新标签页打开。
                    </p>
                    <a
                      href={buildWebdavResourceUrl(file, true)}
                      className="inline-flex px-4 py-2 bg-slate-800 hover:bg-slate-750 text-xs font-bold text-slate-200 rounded-lg transition-all items-center justify-center gap-1.5"
                    >
                      <Download className="w-3.5 h-3.5" />
                      下载原文件
                    </a>
                  </div>
                )}
              </div>
            </div>

            <div
              className="p-4 bg-slate-900/40 border-t border-slate-850/80 backdrop-blur flex flex-col sm:flex-row items-center justify-between gap-3 text-xs"
              onClick={(e) => e.stopPropagation()}
            >
              <span className="text-[11px] text-slate-500 font-mono truncate max-w-full sm:max-w-md" title={previewTask.remotePath}>
                {previewTask.remotePath}
              </span>
              <div className="flex items-center gap-2 w-full sm:w-auto justify-end">
                <a
                  href={buildWebdavResourceUrl(file)}
                  target="_blank"
                  rel="noreferrer"
                  className="px-3 py-2 bg-slate-800 hover:bg-slate-750 text-slate-200 rounded-lg text-xs font-bold flex items-center gap-1 transition-all"
                >
                  新标签页
                  <ArrowUpRight className="w-3.5 h-3.5" />
                </a>
                <a
                  href={buildWebdavResourceUrl(file, true)}
                  className="px-3 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-bold flex items-center gap-1 transition-all"
                >
                  <Download className="w-3.5 h-3.5" />
                  下载
                </a>
              </div>
            </div>
          </div>
        );
      })()}

      {deleteTask && (
        <div
          className="fixed inset-0 z-[110] flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm animate-fadeIn"
          role="dialog"
          aria-modal="true"
          aria-labelledby="delete-task-title"
          onClick={() => {
            if (!deleteMode) setDeleteTask(null);
          }}
        >
          <div
            className="w-full max-w-md rounded-2xl border border-slate-800 bg-slate-900 p-5 shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <h3 id="delete-task-title" className="text-sm font-bold text-slate-100">
                  选择删除方式
                </h3>
                <p className="mt-1 text-[11px] leading-relaxed text-slate-400">
                  任务会立即停止。删除远程资源后无法恢复。
                </p>
              </div>
              <button
                type="button"
                disabled={deleteMode !== null}
                onClick={() => setDeleteTask(null)}
                className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-40"
                title="取消"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="mt-4 rounded-xl border border-slate-800 bg-slate-950/60 px-3 py-2.5">
              <p className="truncate text-xs font-medium text-slate-200" title={deleteTask.filename}>
                {deleteTask.filename}
              </p>
              <p className="mt-1 truncate font-mono text-[10px] text-slate-500" title={deleteTask.remotePath}>
                {deleteTask.remotePath || '未记录远程路径'}
              </p>
            </div>

            <div className="mt-5 grid gap-2 sm:grid-cols-2">
              <button
                type="button"
                disabled={deleteMode !== null}
                onClick={() => confirmDeleteTask(false)}
                className="flex items-center justify-center gap-2 rounded-xl border border-slate-700 bg-slate-800 px-3 py-2.5 text-xs font-semibold text-slate-200 hover:bg-slate-750 disabled:cursor-wait disabled:opacity-60"
              >
                {deleteMode === 'task' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                {deleteMode === 'task' ? '正在删除任务…' : '仅删除任务'}
              </button>
              <button
                type="button"
                disabled={!deleteTask.remotePath || deleteMode !== null}
                onClick={() => confirmDeleteTask(true)}
                className="flex items-center justify-center gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2.5 text-xs font-semibold text-rose-400 hover:bg-rose-500/20 disabled:cursor-wait disabled:opacity-40"
                title={deleteTask.remotePath ? '删除任务和 WebDAV 远程文件' : '该任务没有远程路径'}
              >
                {deleteMode === 'remote' ? <Loader2 className="h-4 w-4 animate-spin" /> : <HardDrive className="h-4 w-4" />}
                {deleteMode === 'remote' ? '正在删除远程资源…' : '删除任务和远程资源'}
              </button>
            </div>

            {deleteMode && (
              <p className="mt-3 text-center text-[11px] text-indigo-400">
                请稍候，远程资源删除可能需要几秒钟。
              </p>
            )}
            {deleteError && (
              <p className="mt-3 rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-[11px] text-rose-400">
                {deleteError}
              </p>
            )}

            <button
              type="button"
              disabled={deleteMode !== null}
              onClick={() => setDeleteTask(null)}
              className="mt-3 w-full rounded-lg px-3 py-2 text-xs font-medium text-slate-500 hover:bg-slate-800/70 hover:text-slate-300 disabled:cursor-not-allowed disabled:opacity-40"
            >
              取消
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
