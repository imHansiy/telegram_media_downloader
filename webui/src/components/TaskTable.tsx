/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from 'react';
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
  Loader2 
} from 'lucide-react';

interface TaskTableProps {
  tasks: SyncTask[];
  onPauseTask: (id: string) => void;
  onResumeTask: (id: string) => void;
  onDeleteTask: (id: string) => void;
  onAddTask: (channel: string, filename: string, type: MediaType, sizeMb: number) => void;
}

export function TaskTable({ 
  tasks, 
  onPauseTask, 
  onResumeTask, 
  onDeleteTask, 
  onAddTask 
}: TaskTableProps) {
  const [searchTerm, setSearchTerm] = useState('');
  const [filterStatus, setFilterStatus] = useState<string>('all');
  
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
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const getMediaIcon = (type: MediaType) => {
    switch (type) {
      case 'photo':
        return <Image id={`icon-photo-${type}`} className="w-4 h-4 text-emerald-500" />;
      case 'video':
        return <Video id={`icon-video-${type}`} className="w-4 h-4 text-indigo-500" />;
      case 'document':
        return <FileText id={`icon-doc-${type}`} className="w-4 h-4 text-sky-500" />;
      case 'audio':
        return <Music id={`icon-audio-${type}`} className="w-4 h-4 text-amber-500" />;
      case 'voice':
        return <Mic id={`icon-voice-${type}`} className="w-4 h-4 text-rose-500" />;
    }
  };

  const filteredTasks = tasks.filter(task => {
    const matchesSearch = 
      task.filename.toLowerCase().includes(searchTerm.toLowerCase()) ||
      task.sourceName.toLowerCase().includes(searchTerm.toLowerCase()) ||
      task.sourceId.toLowerCase().includes(searchTerm.toLowerCase());
    
    if (filterStatus === 'all') return matchesSearch;
    if (filterStatus === 'syncing') return matchesSearch && (task.status === 'downloading' || task.status === 'uploading' || task.status === 'syncing');
    return matchesSearch && task.status === filterStatus;
  });

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
              <p className="text-xs text-slate-400">暂无符合条件的同步传输任务</p>
              <p className="text-[11px] text-slate-600">可以点击上方“创建手动同步”模拟一个下载任务</p>
            </div>
          ) : (
            <>
              {/* DESKTOP TABLE VIEW */}
              <table className="hidden md:table w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-slate-800 bg-slate-950/60 text-[11px] text-slate-400 font-medium uppercase tracking-wider">
                    <th className="py-3 px-4">媒体类型</th>
                    <th className="py-3 px-3">文件信息 / 来源 ID</th>
                    <th className="py-3 px-3">文件大小</th>
                    <th className="py-3 px-3">下载进度 (TG)</th>
                    <th className="py-3 px-3">上传进度 (云盘)</th>
                    <th className="py-3 px-3">当前速度</th>
                    <th className="py-3 px-3">最新状态</th>
                    <th className="py-3 px-4 text-right">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800 text-xs">
                  {filteredTasks.map((task) => {
                    const isDownloading = task.status === 'downloading';
                    const isUploading = task.status === 'uploading';
                    
                    return (
                      <tr 
                        id={`task-row-${task.id}`}
                        key={task.id} 
                        className="hover:bg-slate-850/45 transition-colors group"
                      >
                        {/* Media type */}
                        <td className="py-3.5 px-4 whitespace-nowrap">
                          <div className="flex items-center gap-2">
                            <div className="p-1.5 rounded-lg bg-slate-800 border border-slate-700/80">
                              {getMediaIcon(task.type)}
                            </div>
                            <span className="text-[11px] font-medium text-slate-300 capitalize">{task.type}</span>
                          </div>
                        </td>

                        {/* File Name & TG Source */}
                        <td className="py-3.5 px-3 max-w-xs md:max-w-sm">
                          <div className="space-y-1 truncate">
                            <div className="font-medium text-slate-200 truncate group-hover:text-white transition-colors" title={task.filename}>
                              {task.filename}
                            </div>
                            <div className="flex items-center gap-1.5 text-[10px] text-slate-400">
                              <span className="text-slate-500 font-mono">From:</span>
                              <span className="bg-slate-800 px-1 py-0.5 rounded text-indigo-400 font-mono truncate">{task.sourceId}</span>
                              <span className="text-slate-600">({task.sourceName})</span>
                            </div>
                          </div>
                        </td>

                        {/* Size */}
                        <td className="py-3.5 px-3 whitespace-nowrap text-slate-300 font-mono text-[11px]">
                          {formatSize(task.sizeBytes)}
                        </td>

                        {/* Download Progress */}
                        <td className="py-3.5 px-3 min-w-[130px]">
                          <div className="space-y-1">
                            <div className="flex items-center justify-between text-[10px] font-mono">
                              <span className="text-slate-400 flex items-center gap-1">
                                <Download className={`w-2.5 h-2.5 ${isDownloading ? 'text-emerald-400 animate-bounce' : 'text-slate-500'}`} />
                                {task.status === 'completed' ? '100.0%' : task.status === 'uploading' ? '已落盘' : `${task.downloadProgress.toFixed(1)}%`}
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
                        <td className="py-3.5 px-3 min-w-[130px]">
                          <div className="space-y-1">
                            <div className="flex items-center justify-between text-[10px] font-mono">
                              <span className="text-slate-400 flex items-center gap-1">
                                <Upload className={`w-2.5 h-2.5 ${isUploading ? 'text-indigo-400 animate-pulse' : 'text-slate-500'}`} />
                                {task.status === 'completed' ? '100.0%' : `${task.uploadProgress.toFixed(1)}%`}
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
                        <td className="py-3.5 px-3 whitespace-nowrap text-[11px] font-mono text-slate-300">
                          {task.speedKb > 0 && (task.status === 'downloading' || task.status === 'uploading' || task.status === 'syncing') ? (
                            <div className="flex items-center gap-1">
                              <span className="text-slate-200">
                                {task.speedKb >= 1024 
                                  ? `${(task.speedKb / 1024).toFixed(2)} MB/s` 
                                  : `${task.speedKb} KB/s`}
                              </span>
                            </div>
                          ) : (
                            <span className="text-slate-600">-</span>
                          )}
                        </td>

                        {/* State Badge */}
                        <td className="py-3.5 px-3 whitespace-nowrap">
                          {getStatusBadge(task.status)}
                          {task.errorMsg && (
                            <p className="text-[10px] text-rose-400 mt-1 max-w-[120px] truncate" title={task.errorMsg}>
                              {task.errorMsg}
                            </p>
                          )}
                        </td>

                        {/* Row actions */}
                        <td className="py-3.5 px-4 whitespace-nowrap text-right">
                          <div className="flex items-center justify-end gap-1">
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
                            <button
                              id={`btn-delete-${task.id}`}
                              onClick={() => onDeleteTask(task.id)}
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
                {filteredTasks.map((task) => {
                  const isDownloading = task.status === 'downloading';
                  const isUploading = task.status === 'uploading';
                  
                  return (
                    <div 
                      id={`task-card-mobile-${task.id}`}
                      key={task.id}
                      className="p-4 space-y-3 bg-slate-900/50 hover:bg-slate-850/10 active:bg-slate-850/20 transition-all"
                    >
                      {/* Top: Icons, title and status */}
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex items-center gap-2.5 min-w-0">
                          <div className="p-1.5 rounded-lg bg-slate-800 border border-slate-705/80 shrink-0">
                            {getMediaIcon(task.type)}
                          </div>
                          <div className="min-w-0">
                            <h4 className="font-semibold text-slate-100 truncate text-[12px] leading-snug" title={task.filename}>
                              {task.filename}
                            </h4>
                            <span className="text-[10px] text-slate-450 font-mono">
                              {formatSize(task.sizeBytes)}
                            </span>
                          </div>
                        </div>
                        <div className="shrink-0">
                          {getStatusBadge(task.status)}
                        </div>
                      </div>

                      {/* Source detail banner */}
                      <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-slate-400 bg-slate-950/40 px-2 py-1 rounded-lg border border-slate-850/60">
                        <span className="text-slate-500">From:</span>
                        <span className="bg-slate-805/80 px-1 py-0.2 rounded text-indigo-400 font-mono truncate max-w-[150px]">{task.sourceId}</span>
                        <span className="text-slate-500 max-w-[120px] truncate">({task.sourceName})</span>
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
                          <button
                            id={`btn-delete-mobile-${task.id}`}
                            onClick={() => onDeleteTask(task.id)}
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
        {/* Foot Stats summary info style */}
        <div className="bg-slate-950/40 border-t border-slate-800 py-3 px-4 flex items-center justify-between text-[11px] text-slate-400">
          <div className="flex gap-4">
            <span>正在传输: <strong className="text-indigo-400">{tasks.filter(t => t.status === 'downloading' || t.status === 'uploading').length}</strong></span>
            <span>已暂停: <strong className="text-yellow-500">{tasks.filter(t => t.status === 'paused').length}</strong></span>
            <span>排队等候: <strong className="text-slate-300">{tasks.filter(t => t.status === 'pending').length}</strong></span>
          </div>
          <span className="text-slate-500">双端协议: Telegram Client API & WebDAV Protocol</span>
        </div>
      </div>
    </div>
  );
}
