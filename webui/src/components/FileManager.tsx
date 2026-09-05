/**
 * WebDAV 实时文件浏览器（完全重构版）。
 *
 * 数据流：
 *   /api/webdav/browse  → 目录列表（文件夹 + 文件）
 *   /api/webdav/summary → 选中文件时按远程路径补拉 AI 简介
 *   /api/webdav/preview → 预览/下载代理（路径 = browse 返回的 entry.path 前加 root）
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ArrowUpRight, Calendar, ChevronLeft, ChevronRight, Clock, Copy, Check,
  Eye, FileText, Folder, FolderOpen, HardDrive, Image as ImageIcon,
  LayoutGrid, List, Mic, Music, RefreshCw, Search, Video, X,
} from 'lucide-react';
import { MediaType } from '../types';

/* ---------- 类型 ---------- */

interface DavEntry {
  name: string;
  path: string;
}

interface DavFileEntry extends DavEntry {
  size: number;
  modified: string;
  content_type: string;
}

interface BrowseResult {
  success: boolean;
  path: string;
  root?: string;
  folders: DavEntry[];
  files: DavFileEntry[];
  message?: string;
}

interface FileMeta {
  entry: DavFileEntry;
  type: MediaType;
  summary: string;
  summaryLoading: boolean;
  tags: string[];
  tagsLoading: boolean;
}

/* ---------- 工具 ---------- */

function formatSize(bytes: number): string {
  if (bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** i).toFixed(i === 0 ? 0 : 2)} ${units[i]}`;
}

function formatDate(gmt: string): string {
  if (!gmt) return '-';
  const d = new Date(gmt);
  if (Number.isNaN(d.getTime())) return '-';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

const VIDEO_EXT = ['mp4', 'mkv', 'mov', 'avi', 'webm', 'ts', 'flv', 'wmv'];
const IMAGE_EXT = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'avif'];
const AUDIO_EXT = ['mp3', 'flac', 'wav', 'm4a', 'aac'];
const VOICE_EXT = ['ogg', 'oga', 'opus'];

function extOf(name: string): string {
  const idx = name.lastIndexOf('.');
  return idx >= 0 ? name.slice(idx + 1).toLowerCase() : '';
}

function mediaTypeOf(entry: DavFileEntry): MediaType {
  const ext = extOf(entry.name);
  if (VIDEO_EXT.includes(ext)) return 'video';
  if (IMAGE_EXT.includes(ext)) return 'photo';
  if (VOICE_EXT.includes(ext)) return 'voice';
  if (AUDIO_EXT.includes(ext)) return 'audio';
  return 'document';
}

function typeIcon(type: MediaType, cls = 'w-4 h-4') {
  switch (type) {
    case 'video': return <Video className={`${cls} text-indigo-400`} />;
    case 'photo': return <ImageIcon className={`${cls} text-emerald-400`} />;
    case 'audio': return <Music className={`${cls} text-amber-400`} />;
    case 'voice': return <Mic className={`${cls} text-rose-400`} />;
    default: return <FileText className={`${cls} text-sky-400`} />;
  }
}

function previewUrl(root: string | undefined, entryPath: string, download = false): string {
  const full = root ? `${root}/${entryPath}` : entryPath;
  const q = new URLSearchParams({ path: `/${full}` });
  if (download) q.set('download', '1');
  return `/api/webdav/preview?${q.toString()}`;
}

/* ---------- 主组件 ---------- */

export function FileManager() {
  const [path, setPath] = useState<string[]>([]);       // 当前目录（相对 remote_dir 的段）
  const [root, setRoot] = useState<string>('');         // remote_dir，预览 URL 前缀
  const [folders, setFolders] = useState<DavEntry[]>([]);
  const [files, setFiles] = useState<DavFileEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [view, setView] = useState<'grid' | 'list'>('grid');
  const [selected, setSelected] = useState<FileMeta | null>(null);
  const [preview, setPreview] = useState<FileMeta | null>(null);

  /* 目录加载 */
  const load = useCallback(async (segs: string[]) => {
    setLoading(true);
    setError('');
    try {
      const resp = await fetch(`/api/webdav/browse?path=${encodeURIComponent(segs.join('/'))}`);
      const data: BrowseResult = await resp.json();
      if (!data.success) throw new Error(data.message || '目录读取失败');
      setRoot(data.root || '');
      setFolders(data.folders || []);
      setFiles(data.files || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setFolders([]);
      setFiles([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(path); }, [path, load]);

  /* 选中：附上 AI 简介与标签（异步补拉） */
  const select = useCallback(async (entry: DavFileEntry) => {
    setSelected({ entry, type: mediaTypeOf(entry), summary: '', summaryLoading: true, tags: [], tagsLoading: true });
    try {
      const resp = await fetch(`/api/webdav/summary?path=${encodeURIComponent(entry.path)}`);
      const data = await resp.json();
      const summary = data && data.success ? data.ai_summary || '' : '';
      const tags = data && data.success ? data.ai_tags || [] : [];
      setSelected(prev => (prev && prev.entry.path === entry.path ? { ...prev, summary, summaryLoading: false, tags, tagsLoading: false } : prev));
    } catch {
      setSelected(prev => (prev && prev.entry.path === entry.path ? { ...prev, summaryLoading: false, tagsLoading: false } : prev));
    }
  }, []);

  const enter = (folder: DavEntry) => setPath(folder.path.split('/').filter(Boolean));
  const goRoot = () => setPath([]);
  const goUp = () => setPath(p => p.slice(0, -1));
  const goTo = (i: number) => setPath(p => p.slice(0, i + 1));
  const openPreview = (entry: DavFileEntry) => {
    select(entry);
    setPreview({ entry, type: mediaTypeOf(entry), summary: '', summaryLoading: true, tags: [], tagsLoading: true });
  };

  /* 搜索过滤（仅当前目录内） */
  const q = search.trim().toLowerCase();
  const visibleFolders = useMemo(() => (q ? folders.filter(f => f.name.toLowerCase().includes(q)) : folders), [folders, q]);
  const visibleFiles = useMemo(() => (q ? files.filter(f => f.name.toLowerCase().includes(q)) : files), [files, q]);

  return (
    <div className="flex flex-col lg:flex-row gap-4 h-auto lg:h-[calc(100vh-220px)] lg:min-h-[520px] animate-fadeIn">

      {/* 主浏览面板 */}
      <div className="flex-1 flex flex-col bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-xl min-w-0">

        {/* 顶部：搜索 + 视图切换 */}
        <div className="bg-slate-950/70 p-3 border-b border-slate-800 flex flex-col sm:flex-row gap-3 items-stretch sm:items-center justify-between shrink-0">
          <div className="relative max-w-xs w-full sm:w-64">
            <Search className="absolute inset-y-0 left-0 pl-2.5 flex items-center pointer-events-none text-slate-500 w-3.5 h-3.5" />
            <input
              id="files-browser-search"
              type="text"
              className="block w-full pl-8 pr-3 py-1.5 bg-slate-900 border border-slate-700/80 rounded-lg text-xs text-slate-300 placeholder-slate-500 focus:outline-none focus:border-indigo-500 transition-colors"
              placeholder="过滤当前目录…"
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
          </div>
          <div className="flex items-center gap-2">
            <button
              id="btn-refresh-dir"
              onClick={() => load(path)}
              className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-800 rounded-lg transition-colors cursor-pointer"
              title="刷新目录"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin text-indigo-400' : ''}`} />
            </button>
            <div className="flex bg-slate-900 p-0.5 rounded-lg border border-slate-800 shrink-0">
              <button
                id="btn-view-grid"
                onClick={() => setView('grid')}
                className={`p-1.5 rounded transition-all cursor-pointer ${view === 'grid' ? 'bg-slate-800 text-indigo-400' : 'text-slate-500 hover:text-slate-300'}`}
                title="图标视图"
              ><LayoutGrid className="w-3.5 h-3.5" /></button>
              <button
                id="btn-view-list"
                onClick={() => setView('list')}
                className={`p-1.5 rounded transition-all cursor-pointer ${view === 'list' ? 'bg-slate-800 text-indigo-400' : 'text-slate-500 hover:text-slate-300'}`}
                title="列表视图"
              ><List className="w-3.5 h-3.5" /></button>
            </div>
          </div>
        </div>

        {/* 面包屑 */}
        <div className="bg-slate-950/20 px-4 py-2 border-b border-slate-800/70 text-xs flex items-center gap-1.5 overflow-x-auto scrollbar-none select-none shrink-0">
          <button
            id="btn-breadcrumb-root"
            onClick={goRoot}
            className={`hover:text-indigo-400 px-1 py-0.5 rounded transition-colors cursor-pointer flex items-center gap-1 font-medium shrink-0 ${path.length === 0 ? 'text-slate-100' : 'text-slate-400'}`}
          >
            <HardDrive className="w-3.5 h-3.5" />
            云盘根目录
          </button>
          {path.map((seg, i) => (
            <span key={i} className="flex items-center gap-1.5 shrink-0">
              <ChevronRight className="w-3 h-3 text-slate-600" />
              {i === path.length - 1 ? (
                <span className="text-slate-100 font-semibold px-1 py-0.5 bg-slate-800 border border-slate-700 rounded max-w-[200px] truncate">{seg}</span>
              ) : (
                <button
                  id={`btn-breadcrumb-${seg}`}
                  onClick={() => goTo(i)}
                  className="hover:text-indigo-400 px-1 py-0.5 rounded transition-colors cursor-pointer text-slate-400 max-w-[140px] truncate"
                >{seg}</button>
              )}
            </span>
          ))}
          {path.length > 0 && (
            <button
              id="btn-dir-up"
              onClick={goUp}
              className="ml-auto flex items-center gap-1 px-2 py-0.5 text-[10px] font-bold text-indigo-400 hover:text-indigo-300 bg-slate-950 border border-slate-800 rounded transition-colors cursor-pointer shrink-0"
            >
              <ChevronLeft className="w-3 h-3" /> 上一级
            </button>
          )}
        </div>

        {/* 内容区 */}
        <div className="flex-1 overflow-y-auto p-4 custom-scrollbar bg-slate-950/15">
          {loading ? (
            <div className="h-full flex flex-col items-center justify-center gap-3 py-16">
              <Clock className="w-10 h-10 text-indigo-400/70 animate-pulse" />
              <p className="text-xs text-slate-400 font-medium">正在读取云端目录…</p>
            </div>
          ) : error ? (
            <div className="h-full flex flex-col items-center justify-center gap-3 py-16">
              <FolderOpen className="w-12 h-12 text-rose-400/70" />
              <p className="text-xs font-semibold text-rose-400">目录读取失败</p>
              <p className="text-[10px] text-slate-500 max-w-sm break-all">{error}</p>
              <button onClick={() => load(path)} className="mt-1 text-[10px] font-bold text-indigo-400 hover:underline cursor-pointer">重试</button>
            </div>
          ) : visibleFolders.length === 0 && visibleFiles.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center gap-2 py-16 text-center">
              <FolderOpen className="w-12 h-12 text-slate-700" />
              <h4 className="text-xs font-semibold text-slate-400">
                {search ? '没有匹配的条目' : '这是一个空目录'}
              </h4>
              {search && (
                <button onClick={() => setSearch('')} className="mt-1 text-[10px] font-bold text-indigo-400 hover:underline cursor-pointer">
                  清除过滤词
                </button>
              )}
            </div>
          ) : view === 'grid' ? (
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
              {visibleFolders.map(folder => (
                <button
                  key={folder.path}
                  id={`folder-card-${folder.name}`}
                  onClick={() => enter(folder)}
                  className="text-left p-3.5 bg-slate-900 border border-slate-800 hover:border-indigo-500/40 rounded-xl space-y-3 cursor-pointer select-none transition-all hover:scale-[1.02] group shadow-sm"
                >
                  <div className="flex items-start justify-between">
                    <div className="p-2.5 rounded-xl bg-indigo-500/5 group-hover:bg-indigo-500/10 border border-indigo-500/10 text-indigo-400 transition-colors">
                      <Folder className="w-6 h-6" />
                    </div>
                  </div>
                  <p className="text-xs font-semibold text-slate-200 truncate" title={folder.name}>{folder.name}</p>
                </button>
              ))}
              {visibleFiles.map(file => (
                <div
                  key={file.path}
                  id={`file-card-${file.name}`}
                  onClick={() => select(file)}
                  onDoubleClick={() => openPreview(file)}
                  className={`p-3 bg-slate-900 border rounded-xl space-y-3 cursor-pointer select-none transition-all group hover:scale-[1.01] ${
                    selected?.entry.path === file.path
                      ? 'border-indigo-500/80 ring-1 ring-indigo-500/20'
                      : 'border-slate-800 hover:border-slate-700'
                  }`}
                >
                  <div className="flex items-start justify-between">
                    <div className="p-2 bg-slate-950 border border-slate-800 rounded-lg shrink-0">{typeIcon(mediaTypeOf(file))}</div>
                    <span className="text-[9px] font-mono text-slate-500">{formatSize(file.size)}</span>
                  </div>
                  <p className="text-xs text-slate-300 font-medium truncate" title={file.name}>{file.name}</p>
                  <div className="flex gap-1.5">
                    <button
                      onClick={e => { e.stopPropagation(); openPreview(file); }}
                      className="flex-1 py-1 px-2 bg-indigo-500/10 hover:bg-indigo-500/20 text-[10px] text-indigo-400 font-semibold rounded border border-indigo-500/20 flex items-center justify-center gap-1 transition-all cursor-pointer"
                    ><Eye className="w-3 h-3" />预览</button>
                    <a
                      href={previewUrl(root, file.path)}
                      target="_blank"
                      rel="noreferrer"
                      onClick={e => e.stopPropagation()}
                      className="p-1.5 bg-slate-950 border border-slate-800 hover:bg-slate-900 text-slate-400 hover:text-white rounded transition-colors"
                      title="新窗口打开"
                    ><ArrowUpRight className="w-3.5 h-3.5" /></a>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-x-auto custom-scrollbar shadow-inner">
              <table className="w-full min-w-[640px] text-left border-collapse">
                <thead>
                  <tr className="border-b border-slate-800 bg-slate-950/60 text-[10px] text-slate-500 font-bold uppercase tracking-wider">
                    <th className="py-2.5 px-4">名称</th>
                    <th className="py-2.5 px-3">类型</th>
                    <th className="py-2.5 px-3">大小</th>
                    <th className="py-2.5 px-3">修改时间</th>
                    <th className="py-2.5 px-4 text-right">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800 text-xs text-slate-400">
                  {visibleFolders.map(folder => (
                    <tr key={folder.path} id={`folder-row-${folder.name}`} onClick={() => enter(folder)} className="hover:bg-slate-800/40 cursor-pointer transition-colors">
                      <td className="py-2.5 px-4 font-semibold text-slate-200">
                        <span className="flex items-center gap-2"><Folder className="w-4 h-4 text-indigo-400 shrink-0" /><span className="truncate">{folder.name}</span></span>
                      </td>
                      <td className="py-2.5 px-3"><span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-950/65 border border-slate-800">目录</span></td>
                      <td className="py-2.5 px-3 font-mono text-slate-500">-</td>
                      <td className="py-2.5 px-3 font-mono text-[10px]">-</td>
                      <td className="py-2.5 px-4"></td>
                    </tr>
                  ))}
                  {visibleFiles.map(file => {
                    const type = mediaTypeOf(file);
                    const isSel = selected?.entry.path === file.path;
                    return (
                      <tr
                        key={file.path}
                        id={`file-row-${file.name}`}
                        onClick={() => select(file)}
                        className={`hover:bg-slate-800/30 cursor-pointer transition-colors ${isSel ? 'bg-slate-800/30' : ''}`}
                      >
                        <td className="py-2.5 px-4 font-medium">
                          <span className="flex items-center gap-2 truncate max-w-xs">{typeIcon(type)}<span className="truncate text-slate-300" title={file.name}>{file.name}</span></span>
                        </td>
                        <td className="py-2.5 px-3 uppercase text-[10px] font-mono text-slate-500">{type}</td>
                        <td className="py-2.5 px-3 font-mono text-slate-300">{formatSize(file.size)}</td>
                        <td className="py-2.5 px-3 font-mono text-[10px] text-slate-500">{formatDate(file.modified)}</td>
                        <td className="py-2.5 px-4 text-right" onClick={e => e.stopPropagation()}>
                          <span className="flex items-center justify-end gap-1.5">
                            <button
                              onClick={() => openPreview(file)}
                              className="py-1 px-2.5 bg-indigo-500/10 hover:bg-indigo-500/20 text-[11px] text-indigo-400 font-semibold rounded border border-indigo-500/20 flex items-center gap-1 transition-all cursor-pointer"
                            ><Eye className="w-3.5 h-3.5" />预览</button>
                            <a
                              href={previewUrl(root, file.path)}
                              target="_blank"
                              rel="noreferrer"
                              className="p-1 bg-slate-950 border border-slate-800 hover:bg-slate-900 text-slate-400 hover:text-white rounded transition-colors"
                              title="新窗口打开"
                            ><ArrowUpRight className="w-3.5 h-3.5" /></a>
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* 详情侧栏（仅选中后显示） */}
      {selected && (
        <DetailsPanel
          meta={selected}
          root={root}
          onPreview={() => setPreview(selected)}
          onClose={() => setSelected(null)}
        />
      )}

      {/* 预览灯箱 */}
      {preview && (
        <PreviewLightbox meta={preview} root={root} onClose={() => setPreview(null)} />
      )}
    </div>
  );
}

/* ---------- 详情侧栏 ---------- */

function DetailsPanel({ meta, root, onPreview, onClose }: {
  meta: FileMeta;
  root: string;
  onPreview: () => void;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const { entry, type } = meta;
  const remotePath = `/${root ? root + '/' : ''}${entry.path}`;

  const copy = () => {
    navigator.clipboard.writeText(remotePath);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <aside id="file-details-panel" className="w-full lg:w-80 shrink-0 bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-4 overflow-y-auto custom-scrollbar max-h-[60vh] lg:max-h-none">
      {/* 标题 */}
      <div className="flex items-start gap-3 border-b border-slate-800 pb-3">
        <div className="p-2.5 rounded-xl bg-slate-950 border border-slate-800 text-indigo-400 shrink-0">{typeIcon(type, 'w-5 h-5')}</div>
        <div className="min-w-0 flex-1">
          <h4 className="text-xs font-bold text-slate-100 break-words leading-relaxed" title={entry.name}>{entry.name}</h4>
          <span className="text-[9px] uppercase font-bold text-indigo-400 bg-indigo-500/10 px-1.5 py-0.5 rounded border border-indigo-500/20 inline-block mt-1">{type}</span>
        </div>
        <button onClick={onClose} className="p-1 text-slate-400 hover:text-rose-400 rounded-lg hover:bg-slate-800 transition-colors cursor-pointer shrink-0" title="关闭详情">
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* 媒体缩略预览 */}
      {type === 'photo' && (
        <img
          src={previewUrl(root, entry.path)}
          alt={entry.name}
          className="w-full rounded-xl border border-slate-800 object-contain max-h-48 bg-slate-950"
        />
      )}
      {type === 'video' && (
        <video
          src={previewUrl(root, entry.path)}
          controls preload="metadata" playsInline
          className="w-full rounded-xl border border-slate-800 max-h-48 bg-black"
        />
      )}

      {/* AI 内容标签 */}
      <div>
        <span className="text-[10px] text-slate-500 block uppercase font-mono tracking-wider font-bold mb-1.5">AI 标签</span>
        {meta.tagsLoading ? (
          <p className="text-[11px] text-slate-500 italic flex items-center gap-2">
            <Clock className="w-3 h-3 animate-pulse" /> 正在获取标签…
          </p>
        ) : meta.tags.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {meta.tags.map(tag => (
              <span
                key={tag}
                className="text-[10px] font-medium text-violet-300 bg-violet-500/10 border border-violet-500/25 rounded-full px-2 py-0.5"
              >{tag}</span>
            ))}
          </div>
        ) : (
          <p className="text-[11px] text-slate-500 italic">暂无标签。</p>
        )}
      </div>

      {/* AI 简介（一级数据：选中即拉取，直接渲染） */}
      <div>
        <span className="text-[10px] text-slate-500 block uppercase font-mono tracking-wider font-bold mb-1.5">AI 简介</span>
        {meta.summaryLoading ? (
          <p className="text-[11px] text-slate-500 italic flex items-center gap-2">
            <Clock className="w-3 h-3 animate-pulse" /> 正在获取简介…
          </p>
        ) : meta.summary ? (
          <p className="text-[11px] text-slate-200 leading-relaxed bg-indigo-500/5 border border-indigo-500/15 rounded-lg p-2.5">
            {meta.summary}
          </p>
        ) : (
          <p className="text-[11px] text-slate-500 italic">该文件没有 AI 简介（未经分类流程或非下载器归档）。</p>
        )}
      </div>

      {/* 属性 */}
      <div className="space-y-3 text-xs">
        <div className="space-y-1">
          <span className="text-[10px] text-slate-500 block uppercase font-mono tracking-wider font-bold">大小</span>
          <span className="text-slate-300 font-mono">{formatSize(entry.size)} <span className="opacity-45">({entry.size.toLocaleString()} bytes)</span></span>
        </div>
        <div className="space-y-1">
          <span className="text-[10px] text-slate-500 block uppercase font-mono tracking-wider font-bold">云端修改时间</span>
          <span className="text-slate-300 font-mono flex items-center gap-1.5"><Calendar className="w-3.5 h-3.5 text-slate-500" />{formatDate(entry.modified)}</span>
        </div>
        <div className="space-y-1.5">
          <span className="text-[10px] text-slate-500 block uppercase font-mono tracking-wider font-bold">远程路径</span>
          <div className="relative">
            <textarea
              readOnly rows={3}
              className="w-full text-[10px] font-mono bg-slate-950/90 border border-slate-800 rounded-lg p-2.5 pr-14 text-slate-400 focus:outline-none resize-none leading-relaxed"
              value={remotePath}
            />
            <button
              onClick={copy}
              className="absolute bottom-2 right-2 px-2 py-1 text-[10px] font-bold bg-slate-800 hover:bg-slate-700 text-indigo-400 border border-slate-700 rounded flex items-center gap-1 cursor-pointer"
            >
              {copied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
              {copied ? '已复制' : '复制'}
            </button>
          </div>
        </div>
      </div>

      {/* 操作 */}
      <div className="pt-3 border-t border-slate-800 space-y-2">
        <button
          onClick={onPreview}
          className="w-full py-2 bg-gradient-to-r from-indigo-500 to-violet-600 hover:from-indigo-400 hover:to-violet-500 text-slate-50 rounded-lg text-xs font-bold flex items-center justify-center gap-1.5 transition-all cursor-pointer shadow-md"
        ><Eye className="w-3.5 h-3.5" />预览媒体</button>
        <a
          href={previewUrl(root, entry.path, true)}
          target="_blank"
          rel="noreferrer"
          className="w-full py-2 bg-slate-950 border border-slate-800 hover:bg-slate-800 text-slate-300 rounded-lg text-xs font-semibold flex items-center justify-center gap-1.5 transition-all cursor-pointer"
        ><ArrowUpRight className="w-3.5 h-3.5" />下载原文件</a>
      </div>
    </aside>
  );
}

/* ---------- 预览灯箱 ---------- */

function PreviewLightbox({ meta, root, onClose }: {
  meta: FileMeta;
  root: string;
  onClose: () => void;
}) {
  const [failed, setFailed] = useState(false);
  const { entry, type } = meta;
  const url = previewUrl(root, entry.path);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      id="media-preview-lightbox"
      className="fixed inset-0 z-50 bg-slate-950/85 backdrop-blur-sm flex flex-col animate-fadeIn"
      onClick={onClose}
    >
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-800/60" onClick={e => e.stopPropagation()}>
        <p className="text-xs text-slate-300 font-medium truncate pr-4" title={entry.name}>{entry.name}</p>
        <div className="flex items-center gap-2 shrink-0">
          <a
            href={previewUrl(root, entry.path, true)}
            target="_blank"
            rel="noreferrer"
            className="py-1.5 px-3 bg-slate-900 border border-slate-800 hover:bg-slate-800 text-[11px] text-slate-300 rounded flex items-center gap-1.5 transition-colors cursor-pointer"
          >下载原文件</a>
          <button onClick={onClose} className="p-2 bg-slate-900 border border-slate-800 hover:bg-slate-800 text-slate-400 hover:text-white rounded-lg transition-colors cursor-pointer" title="关闭 (Esc)">
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>
      <div className="flex-1 flex items-center justify-center p-4 overflow-hidden" onClick={e => e.stopPropagation()}>
        {failed ? (
          <div className="text-center space-y-2">
            <p className="text-xs text-rose-400 font-semibold">媒体加载失败</p>
            <p className="text-[10px] text-slate-500">文件可能已被移动或云链路暂不可用。</p>
          </div>
        ) : type === 'photo' ? (
          <img src={url} alt={entry.name} onError={() => setFailed(true)} className="max-w-full max-h-[70vh] rounded-xl border border-slate-800 shadow-2xl" />
        ) : type === 'video' ? (
          <video src={url} controls autoPlay playsInline onError={() => setFailed(true)} className="max-w-full max-h-[70vh] rounded-xl border border-slate-800 shadow-2xl bg-black" />
        ) : type === 'audio' || type === 'voice' ? (
          <audio src={url} controls autoPlay onError={() => setFailed(true)} className="w-full max-w-md" />
        ) : (
          <div className="text-center space-y-3 max-w-md">
            {typeIcon(type, 'w-12 h-12 mx-auto text-slate-600')}
            <p className="text-xs text-slate-400">此文件类型不支持在线预览，请下载后查看。</p>
            <a href={previewUrl(root, entry.path, true)} target="_blank" rel="noreferrer" className="inline-block py-2 px-4 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-bold transition-colors">下载原文件</a>
          </div>
        )}
      </div>
    </div>
  );
}
