/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useMemo, useRef, useEffect } from 'react';
import { CompletedFile, MediaType } from '../types';
import { 
  Folder, 
  FolderOpen, 
  ChevronRight, 
  FileText, 
  Image, 
  Video, 
  Music, 
  Mic, 
  Copy, 
  Check, 
  Calendar, 
  Search, 
  ExternalLink, 
  HardDrive,
  Clock,
  LayoutGrid,
  List,
  ChevronLeft,
  ArrowUpRight,
  Filter,
  CheckCircle2,
  Eye,
  X
} from 'lucide-react';

interface FileManagerProps {
  completedFiles: CompletedFile[];
}

export function FileManager({ completedFiles }: FileManagerProps) {
  // Navigation states
  // We can track the current path by folder level.
  // currentPath[0] = Level 1 folder (Channel: string, or null for root)
  // currentPath[1] = Level 2 folder (Category: string, or null)
  const [currentL1, setCurrentL1] = useState<string | null>(null);
  const [currentL2, setCurrentL2] = useState<string | null>(null);

  // View style configuration: 'grid' or 'list'
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');

  // Search filter
  const [searchTerm, setSearchTerm] = useState('');

  // Selected file for properties panel details on the right
  const [selectedFile, setSelectedFile] = useState<CompletedFile | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  // Lightbox / File Preview Modal State
  const [previewFile, setPreviewFile] = useState<CompletedFile | null>(null);

  // Quick navigation sidebar filter: 'all' | media categories
  const [sidebarFilter, setSidebarFilter] = useState<'all' | MediaType>('all');

  // Category list collapse state inside FileManager
  const [isCategoryCollapsed, setIsCategoryCollapsed] = useState<boolean>(() => {
    return localStorage.getItem('tg_sync_category_collapsed_user') === 'true';
  });

  // Right details sidebar collapse state
  const [isDetailsCollapsed, setIsDetailsCollapsed] = useState<boolean>(() => {
    return localStorage.getItem('tg_sync_details_collapsed_user') === 'true';
  });

  const categorySidebarRef = useRef<HTMLDivElement>(null);
  const detailsSidebarRef = useRef<HTMLDivElement>(null);

  // Auto-collapse category left sidebar when losing focus
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (window.innerWidth < 768) return; // Disable category auto-collapse on mobile
      if (
        categorySidebarRef.current &&
        !categorySidebarRef.current.contains(event.target as Node) &&
        !isCategoryCollapsed
      ) {
        setIsCategoryCollapsed(true);
        localStorage.setItem('tg_sync_category_collapsed_user', 'true');
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isCategoryCollapsed]);

  // Auto-collapse details right sidebar when losing focus
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (window.innerWidth < 768) return; // Disable details auto-collapse on mobile (handled by backdrop/cancel buttons)
      const target = event.target as HTMLElement;
      const isFileCardClick = target.closest('[id^="file-card-"]') || target.closest('[id^="file-row-"]');
      
      if (
        detailsSidebarRef.current &&
        !detailsSidebarRef.current.contains(target) &&
        !isFileCardClick &&
        !isDetailsCollapsed
      ) {
        setIsDetailsCollapsed(true);
        localStorage.setItem('tg_sync_details_collapsed_user', 'true');
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isDetailsCollapsed]);

  const formatSize = (bytes: number) => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const getMediaIcon = (type: MediaType, className = "w-4 h-4") => {
    switch (type) {
      case 'photo':
        return <Image className={`${className} text-emerald-400`} />;
      case 'video':
        return <Video className={`${className} text-indigo-400`} />;
      case 'document':
        return <FileText className={`${className} text-sky-400`} />;
      case 'audio':
        return <Music className={`${className} text-amber-400`} />;
      case 'voice':
        return <Mic className={`${className} text-rose-400`} />;
    }
  };

  const formatDate = (isoStr: string) => {
    const d = new Date(isoStr);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  };

  const handleCopyPath = (path: string, id: string) => {
    navigator.clipboard.writeText(path);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2005);
  };

  // Full flat completed files list after search or sidebar type filter is applied
  const filteredFilesRaw = useMemo(() => {
    return completedFiles.filter(file => {
      const matchesSearch = 
        file.name.toLowerCase().includes(searchTerm.toLowerCase()) || 
        file.sourceName.toLowerCase().includes(searchTerm.toLowerCase());
      
      const matchesSidebar = sidebarFilter === 'all' || file.type === sidebarFilter;
      return matchesSearch && matchesSidebar;
    });
  }, [completedFiles, searchTerm, sidebarFilter]);

  // Folder and leaf nodes parser
  // Dynamically group current files based on the current navigation tier
  const calculatedNavigator = useMemo(() => {
    // If we're at ROOT tier: Group by Channels (Level 1)
    if (!currentL1) {
      const groups: Record<string, { files: CompletedFile[], latestAt: string }> = {};
      
      filteredFilesRaw.forEach(file => {
        const cName = file.sourceName || "Default Channel";
        if (!groups[cName]) {
          groups[cName] = { files: [], latestAt: '1970-01-01T00:00:00.000Z' };
        }
        groups[cName].files.push(file);
        if (file.completedAt > groups[cName].latestAt) {
          groups[cName].latestAt = file.completedAt;
        }
      });

      return {
        type: 'root' as const,
        folders: Object.keys(groups).map(name => ({
          name,
          fileCount: groups[name].files.length,
          latestAt: groups[name].latestAt
        })).sort((a, b) => b.latestAt.localeCompare(a.latestAt)), // Sort channels by latest synced
        files: []
      };
    }

    // If we're inside a Channel (Level 1), but not entered a Category (Level 2): Group by categories
    if (currentL1 && !currentL2) {
      const channelFiles = filteredFilesRaw.filter(f => f.sourceName === currentL1);
      const groups: Record<string, { files: CompletedFile[], latestAt: string }> = {};

      channelFiles.forEach(file => {
        let catFolder = "Documents 📁";
        if (file.type === 'photo') catFolder = "Photos 🏞️";
        else if (file.type === 'video') catFolder = "Videos 🎬";
        else if (file.type === 'audio') catFolder = "Audios 🎵";
        else if (file.type === 'voice') catFolder = "Voices 🎙️";

        if (!groups[catFolder]) {
          groups[catFolder] = { files: [], latestAt: '1970-01-01T00:00:00.000Z' };
        }
        groups[catFolder].files.push(file);
        if (file.completedAt > groups[catFolder].latestAt) {
          groups[catFolder].latestAt = file.completedAt;
        }
      });

      return {
        type: 'l2_folders' as const,
        folders: Object.keys(groups).map(name => ({
          name,
          fileCount: groups[name].files.length,
          latestAt: groups[name].latestAt
        })).sort((a, b) => a.name.localeCompare(b.name, 'zh-CN')), // Sort category names alphabetically 
        files: []
      };
    }

    // If we're entered standard category subfolder (Level 2): Display the corresponding files directly!
    const activeCategoryType = currentL2?.split(' ')[0].toLowerCase().trim() || '';
    // map "Photos", "Videos", "Audios", "Voices" to exact media keys
    let mappedType: string = '';
    if (activeCategoryType.includes('photo')) mappedType = 'photo';
    else if (activeCategoryType.includes('video')) mappedType = 'video';
    else if (activeCategoryType.includes('audio')) mappedType = 'audio';
    else if (activeCategoryType.includes('voice')) mappedType = 'voice';
    else mappedType = 'document';

    const leafFiles = filteredFilesRaw.filter(file => {
      const matchesL1Category = file.sourceName === currentL1;
      const matchesL2Category = file.type === mappedType || (mappedType === 'document' && file.type === 'document');
      return matchesL1Category && matchesL2Category;
    }).sort((a, b) => {
      // Sort: type first, then name
      if (a.type !== b.type) return a.type.localeCompare(b.type);
      return a.name.localeCompare(b.name, 'zh-CN');
    });

    return {
      type: 'files' as const,
      folders: [],
      files: leafFiles
    };

  }, [filteredFilesRaw, currentL1, currentL2]);

  // Click handler to enter directories
  const handleEnterFolder = (name: string) => {
    if (!currentL1) {
      setCurrentL1(name);
    } else if (!currentL2) {
      setCurrentL2(name);
    }
  };

  // Nav back mechanism
  const handleResetToRoot = () => {
    setCurrentL1(null);
    setCurrentL2(null);
  };

  const handleBackToL1 = () => {
    setCurrentL2(null);
  };

  return (
    <div className="flex flex-col xl:flex-row gap-5 h-auto md:h-[calc(100vh-220px)] md:min-h-[550px] animate-fadeIn">
      
      {/* File browser side manager panels */}
      <div className="flex-1 bg-slate-900 border border-slate-805/90 rounded-xl flex flex-col md:flex-row overflow-hidden shadow-xl">
        
        {/* Flat Category sidebar filters left */}
        <div ref={categorySidebarRef} className={`w-full ${isCategoryCollapsed ? 'md:w-16' : 'md:w-44'} transition-all duration-300 bg-slate-950/40 border-b md:border-b-0 md:border-r border-slate-805/85 p-2.5 md:p-3 shrink-0 flex flex-row md:flex-col justify-between items-center md:items-stretch overflow-x-auto md:overflow-hidden scrollbar-none`}>
          <div className="flex md:flex-col items-center md:items-stretch gap-3 md:space-y-4 w-full md:w-auto min-w-0">
            <div className="hidden md:flex items-center justify-between px-1">
              {!isCategoryCollapsed && (
                <span className="text-[10px] text-slate-500 font-bold uppercase tracking-wider block select-none">
                  分类快速检索
                </span>
              )}
              <button
                id="btn-toggle-category-sidebar"
                onClick={() => {
                  setIsCategoryCollapsed(prev => {
                    const next = !prev;
                    localStorage.setItem('tg_sync_category_collapsed_user', String(next));
                    return next;
                  });
                }}
                className={`p-1.5 rounded-lg text-slate-500 hover:text-white hover:bg-slate-800 transition-colors cursor-pointer hidden md:flex items-center justify-center ${isCategoryCollapsed ? 'mx-auto' : 'ml-auto'}`}
                title={isCategoryCollapsed ? "展开分类 (Expand)" : "收起分类 (Collapse)"}
              >
                {isCategoryCollapsed ? <ChevronRight className="w-3.5 h-3.5" /> : <ChevronLeft className="w-3.5 h-3.5" />}
              </button>
            </div>

            <ul className="flex flex-row md:flex-col gap-1.5 md:space-y-1 text-xs overflow-x-auto scrollbar-none max-w-full w-full md:w-auto pr-2 md:pr-0">
              {[
                { key: 'all', emoji: '📁', label: '所有文件', count: completedFiles.length },
                { key: 'photo', emoji: '🏞️', label: '图片', count: completedFiles.filter(c => c.type === 'photo').length },
                { key: 'video', emoji: '🎬', label: '视频', count: completedFiles.filter(c => c.type === 'video').length },
                { key: 'document', emoji: '📄', label: '文档', count: completedFiles.filter(c => c.type === 'document').length },
                { key: 'audio', emoji: '🎵', label: '音频', count: completedFiles.filter(c => c.type === 'audio').length },
                { key: 'voice', emoji: '🎙️', label: '语音', count: completedFiles.filter(c => c.type === 'voice').length }
              ].map(cat => {
                const isSelected = sidebarFilter === cat.key;
                return (
                  <li key={cat.key} className="shrink-0 md:shrink">
                    <button
                      id={`sidebar-filter-${cat.key}`}
                      onClick={() => {
                        setSidebarFilter(cat.key as any);
                        // Reset depth to view all matches in search scope instantly
                        setCurrentL1(null);
                        setCurrentL2(null);
                      }}
                      className={`whitespace-nowrap px-2.5 py-1.5 md:px-2 md:py-2 rounded-lg transition-all flex items-center justify-between gap-1.5 cursor-pointer md:w-full text-left ${
                        isSelected 
                          ? 'bg-indigo-500/10 text-indigo-400 font-bold border border-indigo-505/20' 
                          : 'text-slate-400 hover:text-slate-205 hover:bg-slate-850/40 border border-transparent'
                      }`}
                      title={`${cat.emoji} ${cat.label} (${cat.count})`}
                    >
                      <div className="flex items-center gap-1.5 min-w-0 truncate">
                        <span className="text-sm shrink-0">{cat.emoji}</span>
                        <span className={`${isCategoryCollapsed ? 'inline md:hidden font-medium' : 'inline font-medium'} truncate text-[11px] md:text-xs`}>
                          {cat.label}
                        </span>
                      </div>
                      <span className={`${isCategoryCollapsed ? 'inline md:hidden' : 'inline'} text-[9px] font-mono opacity-65 shrink-0 pl-1`}>
                        {cat.count}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>

          <div className={`hidden md:block transition-all duration-300 ${isCategoryCollapsed ? 'opacity-0 h-0 p-0 border-0 overflow-hidden pointer-events-none' : 'bg-slate-900 border border-slate-805/60 p-2.5 rounded-lg text-[10px] text-slate-500 space-y-1 font-mono'}`}>
            <span className="text-slate-400 font-medium font-sans">本地校验状态：</span>
            <p className="flex items-center gap-1 text-emerald-400">
              <CheckCircle2 className="w-3 h-3 shrink-0" />
              已校验 MD5
            </p>
            <p className="leading-snug text-[9px] text-slate-600">云盘端在更新后会自动重新校对差异块。</p>
          </div>
        </div>

        {/* Core files viewing and folder clicking content panels */}
        <div className="flex-1 flex flex-col overflow-hidden bg-slate-900">
          
          {/* Header toolbar index level metadata */}
          <div className="bg-slate-950/70 p-3 border-b border-slate-805 flex flex-col sm:flex-row gap-3 items-stretch sm:items-center justify-between shrink-0">
            
            {/* Search Input block inside files window */}
            <div className="relative max-w-xs w-full sm:w-60">
              <span className="absolute inset-y-0 left-0 pl-2.5 flex items-center pointer-events-none text-slate-550">
                <Search className="w-3.5 h-3.5" />
              </span>
              <input
                id="files-browser-search"
                type="text"
                className="block w-full pl-8 pr-3 py-1 bg-slate-900 border border-slate-750/80 rounded-lg text-xs text-slate-300 placeholder-slate-500 focus:outline-none focus:border-indigo-650 transition-colors"
                placeholder="在成功同步日志中查找文件..."
                value={searchTerm}
                onChange={(e) => {
                  setSearchTerm(e.target.value);
                  // If searching: automatically unlock levels to permit finding anything
                  if (e.target.value.trim() !== '') {
                    setCurrentL1(null);
                    setCurrentL2(null);
                  }
                }}
              />
            </div>

            {/* Layout grids toggle button */}
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-slate-500 font-medium select-none hidden md:inline">配置视图方式：</span>
              <div className="flex bg-slate-900 p-0.5 rounded-lg border border-slate-800 shrink-0">
                <button
                  id="btn-view-mode-grid"
                  onClick={() => setViewMode('grid')}
                  className={`p-1.5 rounded transition-all cursor-pointer ${viewMode === 'grid' ? 'bg-slate-800 text-indigo-400' : 'text-slate-500 hover:text-slate-300'}`}
                  title="图标网格排列"
                >
                  <LayoutGrid className="w-3.5 h-3.5" />
                </button>
                <button
                  id="btn-view-mode-list"
                  onClick={() => setViewMode('list')}
                  className={`p-1.5 rounded transition-all cursor-pointer ${viewMode === 'list' ? 'bg-slate-800 text-indigo-400' : 'text-slate-505 hover:text-slate-300'}`}
                  title="精细列表清单"
                >
                  <List className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>

          </div>

          {/* Directory location Breadcrumb address bar */}
          <div className="bg-slate-950/20 px-4 py-2 border-b border-slate-805/70 text-xs flex items-center gap-1.5 overflow-x-auto scrollbar-none select-none shrink-0">
            <button
              id="btn-breadcrumb-root"
              onClick={handleResetToRoot}
              className={`hover:text-indigo-400 px-1 py-0.5 rounded transition-colors cursor-pointer flex items-center gap-1 font-medium ${!currentL1 ? 'text-white' : 'text-slate-450'}`}
            >
              <HardDrive className="w-3.5 h-3.5" />
              全部同步 (云盘根)
            </button>

            {currentL1 && (
              <>
                <ChevronRight className="w-3 h-3 text-slate-650 shrink-0" />
                <button
                  id={`btn-breadcrumb-l1-${currentL1}`}
                  onClick={handleBackToL1}
                  className={`hover:text-indigo-400 px-1 py-0.5 rounded transition-colors cursor-pointer font-medium max-w-[120px] sm:max-w-xs truncate ${!currentL2 ? 'text-white font-semibold' : 'text-slate-450'}`}
                >
                  {currentL1}
                </button>
              </>
            )}

            {currentL2 && (
              <>
                <ChevronRight className="w-3 h-3 text-slate-650 shrink-0" />
                <span className="text-white font-semibold px-1 py-0.5 bg-slate-800 border border-slate-750/70 rounded flex items-center gap-1">
                  {currentL2}
                </span>
              </>
            )}

            {/* Back Arrow button when deep inside */}
            {(currentL1 || currentL2) && (
              <button
                id="btn-directory-back-up"
                onClick={currentL2 ? handleBackToL1 : handleResetToRoot}
                className="ml-auto flex items-center gap-1 px-2 py-0.5 text-[10px] font-bold text-indigo-400 hover:text-indigo-300 bg-slate-950 border border-slate-800 rounded transition-colors cursor-pointer"
              >
                <ChevronLeft className="w-3 h-3" />
                返回上一级
              </button>
            )}
          </div>

          {/* Directory main contents area view render */}
          <div className="flex-1 overflow-y-auto p-4 custom-scrollbar bg-slate-950/15">
            
            {calculatedNavigator.folders.length === 0 && calculatedNavigator.files.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center text-center space-y-2 py-16">
                <FolderOpen className="w-12 h-12 text-slate-705 stroke-[1.5]" />
                <h4 className="text-xs font-semibold text-slate-400">
                  {searchTerm ? '没有找到符合搜查条件的资源' : '这是一个空文件夹 📭'}
                </h4>
                <p className="text-[10px] text-slate-605 max-w-sm">
                  {searchTerm ? '请检查您的搜索拼写或尝试在主菜单分类过滤器中选择“所有完成文件”' : '您可以点击导航返回主同步路径下检索其他频道的媒体文件'}
                </p>
                {searchTerm && (
                  <button
                    id="btn-clear-search-box"
                    onClick={() => setSearchTerm('')}
                    className="mt-2 text-[10px] font-bold text-indigo-400 hover:underline cursor-pointer"
                  >
                    重置并清除筛选词
                  </button>
                )}
              </div>
            ) : viewMode === 'grid' ? (
              /* GRID VIEW (大图标卡片视图) */
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4 animate-fadeIn">
                
                {/* Render folders in structure */}
                {calculatedNavigator.folders.map(folder => (
                  <div
                    id={`folder-card-${folder.name}`}
                    key={folder.name}
                    onClick={() => handleEnterFolder(folder.name)}
                    className="p-3.5 bg-slate-900 border border-slate-805/85 hover:border-indigo-500/40 rounded-xl space-y-3 cursor-pointer select-none transition-all hover:scale-[1.02] active:scale-[0.99] group shadow shadow-slate-950/20"
                  >
                    <div className="flex items-start justify-between">
                      <div className="p-2.5 rounded-xl bg-indigo-500/5 group-hover:bg-indigo-500/10 border border-indigo-500/10 text-indigo-400 transition-colors">
                        <Folder className="w-6 h-6 fill-indigo-400/10" />
                      </div>
                      <span className="text-[10px] font-mono text-slate-500 bg-slate-950/60 px-1.5 py-0.5 rounded border border-slate-805">
                        {folder.fileCount} items
                      </span>
                    </div>

                    <div className="space-y-1">
                      <h4 className="text-xs font-semibold text-slate-205 group-hover:text-white truncate" title={folder.name}>
                        {folder.name}
                      </h4>
                      <p className="text-[9px] font-mono text-slate-500 truncate flex items-center gap-1">
                        <Clock className="w-2.5 h-2.5 shrink-0" />
                        {folder.latestAt !== '1970-01-01T00:00:00.000Z' ? formatDate(folder.latestAt) : 'N/A'}
                      </p>
                    </div>
                  </div>
                ))}

                {/* Render files if at leaf level */}
                {calculatedNavigator.files.map(file => {
                  const isSelected = selectedFile?.id === file.id;
                  return (
                    <div
                      id={`file-card-${file.id}`}
                      key={file.id}
                      onClick={() => {
                        setSelectedFile(file);
                        setIsDetailsCollapsed(false);
                        localStorage.setItem('tg_sync_details_collapsed_user', 'false');
                      }}
                      onDoubleClick={() => {
                        setSelectedFile(file);
                        setIsDetailsCollapsed(false);
                        localStorage.setItem('tg_sync_details_collapsed_user', 'false');
                        // Double click emulation: Open external url pathway
                        window.open(file.remotePath, '_blank');
                      }}
                      className={`p-3 bg-slate-900 border rounded-xl space-y-3 cursor-pointer select-none transition-all group hover:scale-[1.01] ${
                        isSelected 
                          ? 'border-indigo-500/80 ring-1 ring-indigo-500/20 bg-slate-850/30' 
                          : 'border-slate-805/80 hover:border-slate-700/80'
                      }`}
                    >
                      <div className="flex items-start justify-between">
                        <div className="p-2 bg-slate-950 border border-slate-805 rounded-lg shrink-0">
                          {getMediaIcon(file.type, "w-4 h-4")}
                        </div>
                        <span className="text-[9px] font-mono font-medium text-slate-500">
                          {formatSize(file.sizeBytes)}
                        </span>
                      </div>

                      <div className="space-y-1">
                        <p className="text-xs text-slate-300 font-medium truncate break-all block group-hover:text-slate-100" title={file.name}>
                          {file.name}
                        </p>
                        <div className="flex justify-between items-center text-[9px] font-mono text-slate-500 pb-1.5 border-b border-slate-805/40">
                          <span className="truncate max-w-[80px] bg-slate-950/60 px-1 rounded">{file.sourceName}</span>
                          <span className="shrink-0">{formatDate(file.completedAt).split(' ')[0]}</span>
                        </div>
                        {/* Mobile and quick responsive action buttons */}
                        <div className="pt-2 flex gap-1.5">
                          <button
                            id={`btn-grid-preview-${file.id}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              setPreviewFile(file);
                            }}
                            className="flex-1 py-1 px-2 bg-indigo-500/10 hover:bg-indigo-500/20 text-[10px] text-indigo-400 font-semibold rounded border border-indigo-500/20 hover:border-indigo-500/35 flex items-center justify-center gap-1 transition-all cursor-pointer"
                          >
                            <Eye className="w-3 h-3" />
                            预览
                          </button>
                          <a
                            href={file.remotePath}
                            target="_blank"
                            rel="noreferrer"
                            onClick={(e) => e.stopPropagation()}
                            className="p-1 bg-slate-950 border border-slate-800 hover:bg-slate-900 text-slate-400 hover:text-white rounded flex items-center justify-center transition-colors cursor-pointer"
                            title="在新窗口打开"
                          >
                            <ArrowUpRight className="w-3.5 h-3.5" />
                          </a>
                        </div>
                      </div>
                    </div>
                  );
                })}

              </div>
            ) : (
              /* LIST VIEW (精细数据列表视图) */
              <div className="bg-slate-900 border border-slate-805 rounded-xl overflow-x-auto custom-scrollbar shadow-inner animate-fadeIn">
                <table className="w-full min-w-[700px] text-left border-collapse">
                  <thead>
                    <tr className="border-b border-slate-800 bg-slate-950/60 text-[10px] text-slate-450 font-bold uppercase tracking-wider select-none">
                      <th className="py-2.5 px-4">名称</th>
                      <th className="py-2.5 px-3">条目类别</th>
                      <th className="py-2.5 px-3">对应大小</th>
                      <th className="py-2.5 px-3">关联渠道 (Source)</th>
                      <th className="py-2.5 px-3">同步到账日期</th>
                      <th className="py-2.5 px-4 text-right">操作</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-805 text-xs text-slate-400">
                    
                    {/* Render folders first inside list */}
                    {calculatedNavigator.folders.map(folder => (
                      <tr
                        id={`folder-row-${folder.name}`}
                        key={folder.name}
                        onClick={() => handleEnterFolder(folder.name)}
                        className="hover:bg-slate-850/40 cursor-pointer transition-colors group"
                      >
                        <td className="py-2.5 px-4 font-semibold text-slate-200">
                          <div className="flex items-center gap-2">
                            <Folder className="w-4 h-4 text-indigo-400 fill-indigo-400/10 group-hover:fill-indigo-400/20" />
                            <span className="truncate group-hover:text-white">{folder.name}</span>
                          </div>
                        </td>
                        <td className="py-2.5 px-3">
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-950/65 border border-slate-800 text-slate-400">
                            子目录 (Folder)
                          </span>
                        </td>
                        <td className="py-2.5 px-3 font-mono text-slate-550">-</td>
                        <td className="py-2.5 px-3 text-[11px] text-slate-550 font-mono">根通道 / 自定义</td>
                        <td className="py-2.5 px-3 font-mono text-[10px] text-slate-500">
                          {folder.latestAt !== '1970-01-01T00:00:00.000Z' ? formatDate(folder.latestAt) : '-'}
                        </td>
                        <td className="py-2.5 px-4"></td>
                      </tr>
                    ))}

                    {/* Render leaf files */}
                    {calculatedNavigator.files.map(file => {
                      const isSelected = selectedFile?.id === file.id;
                      return (
                        <tr
                          id={`file-row-${file.id}`}
                          key={file.id}
                          onClick={() => {
                            setSelectedFile(file);
                            setIsDetailsCollapsed(false);
                            localStorage.setItem('tg_sync_details_collapsed_user', 'false');
                          }}
                          className={`hover:bg-slate-850/30 cursor-pointer transition-colors ${isSelected ? 'bg-slate-850/30 text-white' : ''}`}
                        >
                          <td className="py-2.5 px-4 font-medium">
                            <div className="flex items-center gap-2 truncate max-w-xs sm:max-w-md">
                              {getMediaIcon(file.type)}
                              <span className="truncate text-slate-300" title={file.name}>{file.name}</span>
                            </div>
                          </td>
                          <td className="py-2.5 px-3 uppercase text-[10px] font-mono text-slate-450">
                            {file.type}
                          </td>
                          <td className="py-2.5 px-3 font-mono text-slate-300 font-medium">
                            {formatSize(file.sizeBytes)}
                          </td>
                          <td className="py-2.5 px-3">
                            <span className="bg-slate-950 px-1.5 py-0.5 border border-slate-800 text-indigo-400 rounded font-mono text-[10px]">
                              {file.sourceId}
                            </span>
                          </td>
                          <td className="py-2.5 px-3 font-mono text-[10px] text-slate-500">
                            {formatDate(file.completedAt)}
                          </td>
                          <td className="py-2.5 px-4 text-right">
                            <div className="flex items-center justify-end gap-1.5" onClick={(e) => e.stopPropagation()}>
                              <button
                                onClick={() => setPreviewFile(file)}
                                className="py-1 px-2.5 bg-indigo-500/10 hover:bg-indigo-500/20 text-[11px] text-indigo-400 font-semibold rounded border border-indigo-500/20 hover:border-indigo-500/35 transition-all flex items-center gap-1 cursor-pointer"
                              >
                                <Eye className="w-3.5 h-3.5" />
                                预览
                              </button>
                              <a
                                href={file.remotePath}
                                target="_blank"
                                rel="noreferrer"
                                className="p-1 bg-slate-950 border border-slate-850 hover:bg-slate-900 text-slate-400 hover:text-white rounded transition-colors cursor-pointer"
                                title="直接在新窗口打开"
                              >
                                <ArrowUpRight className="w-3.5 h-3.5" />
                              </a>
                            </div>
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

      </div>

      {/* Selected File Details Pane right sidebar properties dashboard */}
      {selectedFile && (
        <>
          {/* Mobile bottom sheet backdrop overlay */}
          <div 
            id="mobile-drawer-backdrop-overlay"
            className="md:hidden fixed inset-0 bg-slate-950/60 backdrop-blur-sm z-40 animate-fadeIn" 
            onClick={() => setSelectedFile(null)}
          />
          <div 
            ref={detailsSidebarRef} 
            className={`
              fixed bottom-0 inset-x-0 z-50 rounded-t-2xl max-h-[85vh] bg-slate-950 border-t border-slate-800 p-4 pb-8 overflow-y-auto shadow-2xl animate-slideUp transition-all duration-300
              md:relative md:bottom-auto md:inset-auto md:z-auto md:rounded-xl md:max-h-none md:border md:border-slate-805/90 md:bg-slate-900 md:pb-4 md:shadow-xl md:shrink-0
              ${isDetailsCollapsed ? 'md:w-16 md:p-3' : 'md:w-80 md:p-4'}
            `}
          >
            {isDetailsCollapsed ? (
              <div className="hidden md:flex flex-col items-center gap-4 h-full min-h-[450px] justify-between">
                <div className="flex flex-col items-center gap-4">
                  <button
                    id="btn-toggle-details-sidebar-collapsed"
                    onClick={() => {
                      setIsDetailsCollapsed(false);
                      localStorage.setItem('tg_sync_details_collapsed_user', 'false');
                    }}
                    className="p-1 px-1.5 text-slate-500 hover:text-white rounded hover:bg-slate-800 cursor-pointer transition-colors"
                    title="展开详情面板"
                  >
                    <ChevronLeft className="w-4 h-4" />
                  </button>
                  <div className="p-2 rounded-xl bg-slate-950 border border-slate-805 text-indigo-400 mt-1 shrink-0">
                    {getMediaIcon(selectedFile.type, "w-5 h-5")}
                  </div>
                  <div className="text-[9px] uppercase font-bold text-indigo-400 bg-indigo-500/10 px-1 py-0.5 rounded border border-indigo-500/20">
                    {selectedFile.type}
                  </div>
                  <div className="writing-mode-vertical uppercase font-bold tracking-widest text-[10px] text-slate-500 font-mono pt-4 whitespace-nowrap">
                    INFO
                  </div>
                </div>
                <div>
                  <button
                    onClick={() => setSelectedFile(null)}
                    className="p-1 text-rose-455 hover:text-rose-350 hover:bg-rose-500/10 rounded transition-colors cursor-pointer"
                    title="关闭详情"
                  >
                    <ChevronRight className="w-4 h-4 rotate-180" />
                  </button>
                </div>
              </div>
            ) : (
              <div className="space-y-5 h-full flex flex-col justify-between flex-1">
                <div className="space-y-4">
                  
                  {/* Title / Header of the current parsed selection */}
                  <div className="flex items-start gap-3 border-b border-slate-800/80 pb-3 md:pr-14 relative font-medium">
                    <div className="absolute top-0 right-0 flex items-center gap-1.5">
                      <button
                        id="btn-toggle-details-sidebar"
                        onClick={() => {
                          setIsDetailsCollapsed(true);
                          localStorage.setItem('tg_sync_details_collapsed_user', 'true');
                        }}
                        className="hidden md:block p-1 text-slate-500 hover:text-white rounded hover:bg-slate-800 cursor-pointer transition-colors font-medium"
                        title="收起详情面板"
                      >
                        <ChevronRight className="w-4 h-4" />
                      </button>
                      <button
                        id="btn-close-properties-sidebar"
                        onClick={() => setSelectedFile(null)}
                        className="p-1 md:p-1.5 text-slate-400 hover:text-rose-400 rounded-lg hover:bg-slate-800 transition-colors cursor-pointer border border-transparent md:border-none focus:outline-none focus:ring-1 focus:ring-slate-700"
                        title="关闭详情面板"
                      >
                        <span className="md:hidden text-xs bg-slate-900 border border-slate-800 px-2 py-1 rounded text-slate-300 hover:text-white">关闭详情</span>
                        <ChevronRight className="hidden md:block w-4 h-4 rotate-180" />
                      </button>
                    </div>
                    <div className="p-2.5 rounded-xl bg-slate-950 border border-slate-805 text-indigo-400 mt-1 shrink-0">
                      {getMediaIcon(selectedFile.type, "w-5 h-5")}
                    </div>
                    <div className="space-y-1 min-w-0 pr-16 md:pr-2">
                      <h4 className="text-xs font-bold text-slate-100 break-words leading-relaxed" title={selectedFile.name}>
                        {selectedFile.name}
                      </h4>
                      <div className="flex flex-wrap gap-1.5 items-center">
                        <span className="text-[9px] uppercase font-bold text-indigo-400 bg-indigo-500/10 px-1.5 py-0.2 rounded border border-indigo-500/20">
                          {selectedFile.type}
                        </span>
                        <span className="text-[9px] text-slate-500 font-mono">
                          ID: {selectedFile.id.substring(10, 16)}
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Media Instant Preview Box */}
                  <div className="bg-slate-950 rounded-xl overflow-hidden border border-slate-800/80 aspect-video flex items-center justify-center relative group">
                    {selectedFile.type === 'photo' ? (
                      <img 
                        src={selectedFile.remotePath} 
                        alt={selectedFile.name}
                        className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                        referrerPolicy="no-referrer"
                        onError={(e) => {
                          e.currentTarget.style.display = 'none';
                          const fb = document.getElementById(`preview-fb-${selectedFile.id}`);
                          if (fb) fb.classList.remove('hidden');
                        }}
                      />
                    ) : selectedFile.type === 'video' ? (
                      <video 
                        src={selectedFile.remotePath} 
                        controls 
                        playsInline
                        preload="metadata"
                        className="w-full h-full object-contain bg-black"
                      />
                    ) : (selectedFile.type === 'audio' || selectedFile.type === 'voice') ? (
                      <div className="w-full h-full flex flex-col justify-center p-3 space-y-2 bg-slate-950">
                        <div className="flex justify-center text-amber-400">
                          {getMediaIcon(selectedFile.type, "w-8 h-8")}
                        </div>
                        <audio 
                          src={selectedFile.remotePath} 
                          controls 
                          preload="none"
                          className="w-full h-8"
                        />
                      </div>
                    ) : (
                      <div className="text-center p-4">
                        <div className="text-slate-500 mb-1 flex justify-center">
                          {getMediaIcon(selectedFile.type, "w-8 h-8")}
                        </div>
                        <span className="text-[10px] text-slate-500 font-mono break-all line-clamp-2 px-2">
                          {selectedFile.name}
                        </span>
                      </div>
                    )}

                    {/* Image custom error fallback */}
                    {selectedFile.type === 'photo' && (
                      <div id={`preview-fb-${selectedFile.id}`} className="hidden absolute inset-0 flex flex-col items-center justify-center text-center p-3 bg-slate-950">
                        <Image className="w-6 h-6 text-slate-600 mb-1" />
                        <span className="text-[9px] text-slate-400 px-3">由于 WebDAV CORS/鉴权限制无法直接在此加载</span>
                        <a 
                          href={selectedFile.remotePath} 
                          target="_blank" 
                          rel="noreferrer" 
                          className="mt-1.5 text-[9px] text-indigo-400 hover:underline font-bold"
                        >
                          在新窗口尝试打开 ↗
                        </a>
                      </div>
                    )}
                  </div>

                  {/* Structured details properties panel list */}
                  <div className="space-y-4 text-xs">
                    
                    <div className="space-y-1">
                      <span className="text-[10px] text-slate-500 block uppercase font-mono tracking-wider font-bold">文件大小 (Density)</span>
                      <span className="text-slate-300 font-mono font-medium">
                        {formatSize(selectedFile.sizeBytes)} <span className="opacity-45">({selectedFile.sizeBytes.toLocaleString()} bytes)</span>
                      </span>
                    </div>

                    <div className="space-y-1">
                      <span className="text-[10px] text-slate-500 block uppercase font-mono tracking-wider font-bold font-semibold">来源 Telegram 频道 (TG Source)</span>
                      <div className="bg-slate-950/60 p-2 rounded-lg border border-slate-850 flex items-center justify-between text-indigo-400 font-mono text-[11px]">
                        <span className="truncate">{selectedFile.sourceId}</span>
                        <span className="text-slate-555 text-[10px] shrink-0 font-sans">({selectedFile.sourceName})</span>
                      </div>
                    </div>

                    <div className="space-y-1">
                      <span className="text-[10px] text-slate-500 block uppercase font-mono tracking-wider font-bold">同步挂载日期 (Archived At)</span>
                      <span className="text-slate-300 font-mono flex items-center gap-1.5 font-medium">
                        <Calendar className="w-3.5 h-3.5 text-slate-550" />
                        {formatDate(selectedFile.completedAt)}
                      </span>
                    </div>

                    <div className="space-y-1.5">
                      <span className="text-[10px] text-slate-500 block uppercase font-mono tracking-wider font-bold">WebDAV 远程绝对同步路径</span>
                      <div className="relative">
                        <textarea
                          id="textarea-remote-path-explorer"
                          readOnly
                          rows={3}
                          className="w-full text-[10px] font-mono bg-slate-950/90 border border-slate-850 rounded-lg p-2.5 pr-16 text-slate-450 focus:outline-none resize-none leading-relaxed"
                          value={selectedFile.remotePath}
                        />
                        <button
                          id="btn-copy-address"
                          onClick={() => handleCopyPath(selectedFile.remotePath, selectedFile.id)}
                          className="absolute bottom-2.5 right-2 px-2.5 py-1 text-[10px] font-bold bg-slate-850 hover:bg-slate-750 select-none text-indigo-400 hover:text-white border border-slate-750 rounded-md flex items-center gap-1 transition-all cursor-pointer shadow-sm"
                        >
                          {copiedId === selectedFile.id ? (
                            <>
                              <Check className="w-3 h-3 text-emerald-400" />
                              已快抓
                            </>
                          ) : (
                            <>
                              <Copy className="w-3 h-3" />
                              拷贝
                            </>
                          )}
                        </button>
                      </div>
                    </div>
                  </div>

                  {/* Direct linkage triggers */}
                  <div className="pt-3.5 border-t border-slate-800 flex flex-col space-y-2">
                    <button
                      id="btn-sidebar-quick-preview"
                      onClick={() => setPreviewFile(selectedFile)}
                      className="w-full py-2 bg-gradient-to-r from-indigo-500 to-violet-600 hover:from-indigo-400 hover:to-violet-500 text-slate-50 rounded-lg text-xs font-bold flex items-center justify-center gap-1.5 transition-all cursor-pointer shadow-md shadow-indigo-950/25 active:scale-[0.98]"
                    >
                      <Eye className="w-3.5 h-3.5" />
                      立即预览媒体
                    </button>

                    <div className="flex items-center justify-between pt-1 text-[10px]">
                      <span className="text-emerald-450 flex items-center gap-1 font-medium">
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                        WebDAV 流已入卷
                      </span>
                      <a 
                        href={selectedFile.remotePath} 
                        target="_blank" 
                        rel="noreferrer"
                        className="text-indigo-400 hover:text-indigo-300 flex items-center gap-1 font-bold hover:underline cursor-pointer"
                      >
                        直接在云盘中查看
                        <ArrowUpRight className="w-3.5 h-3.5" />
                      </a>
                    </div>
                  </div>

                </div>

                <div className="mt-6 pt-3 border-t border-slate-850/60 flex items-center justify-between text-[9px] text-slate-600 font-mono select-none">
                  <span>HASH: ID_{selectedFile.id.substring(10, 16).toUpperCase()}</span>
                  <span>TYPE: MTPROTO_STABLE</span>
                </div>
              </div>
            )}
          </div>
        </>
      )}

      {/* Dynamic Full Screen Media Lightbox Preview Modal */}
      {previewFile && (
        <div 
          id="media-preview-lightbox"
          className="fixed inset-0 z-[100] flex flex-col justify-between bg-slate-950/95 backdrop-blur-md animate-fadeIn"
          onClick={() => setPreviewFile(null)}
        >
          {/* Header */}
          <div className="flex items-center justify-between p-4 bg-slate-900/60 border-b border-slate-850/80 backdrop-blur" onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-3 min-w-0 pr-6">
              <div className="p-2 rounded-lg bg-slate-950/80 border border-slate-805 text-indigo-400 shrink-0">
                {getMediaIcon(previewFile.type, "w-5 h-5")}
              </div>
              <div className="min-w-0">
                <h3 className="text-sm font-bold text-slate-100 truncate font-sans max-w-[240px] sm:max-w-xl" title={previewFile.name}>
                  {previewFile.name}
                </h3>
                <p className="text-[10px] font-mono text-slate-500 mt-0.5 flex flex-wrap gap-2">
                  <span>大小: {formatSize(previewFile.sizeBytes)}</span>
                  <span>•</span>
                  <span>分类: {previewFile.type.toUpperCase()}</span>
                  <span>•</span>
                  <span>来源: {previewFile.sourceName}</span>
                </p>
              </div>
            </div>
            <button
              id="btn-close-lightbox"
              onClick={() => setPreviewFile(null)}
              className="p-2 rounded-xl bg-slate-850 hover:bg-slate-755 text-slate-300 hover:text-white transition-all cursor-pointer border border-slate-800"
              title="关闭预览"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Active Preview Stage */}
          <div className="flex-1 flex items-center justify-center p-4 sm:p-8" onClick={() => setPreviewFile(null)}>
            <div className="max-w-4xl w-full max-h-[70vh] flex items-center justify-center" onClick={e => e.stopPropagation()}>
              {previewFile.type === 'photo' ? (
                <div className="relative group max-w-full">
                  <img 
                    src={previewFile.remotePath} 
                    alt={previewFile.name}
                    className="max-w-full max-h-[70vh] rounded-xl object-contain shadow-2xl border border-slate-805/50 mx-auto select-none"
                    referrerPolicy="no-referrer"
                    onError={(e) => {
                      e.currentTarget.style.display = 'none';
                      const fallback = document.getElementById(`lightbox-fallback-${previewFile.id}`);
                      if (fallback) fallback.classList.remove('hidden');
                    }}
                  />
                  <div id={`lightbox-fallback-${previewFile.id}`} className="hidden flex flex-col items-center justify-center text-center p-6 bg-slate-900 border border-slate-850 rounded-xl max-w-md mx-auto">
                    <Image className="w-12 h-12 text-slate-600 mb-3" />
                    <h4 className="text-xs font-bold text-slate-300 mb-1">图片无法在此直接加载</h4>
                    <p className="text-[10px] text-slate-500 mb-4 max-w-xs">由于 WebDAV 的跨域访问资源策略 (CORS) 或鉴权限制，浏览器暂无法直接显示。我们建议您直接在新标签页中安全预览或下载。</p>
                    <a 
                      href={previewFile.remotePath} 
                      target="_blank" 
                      rel="noreferrer" 
                      className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-xs font-bold text-white rounded-lg transition-all shadow-lg flex items-center gap-1 font-sans"
                    >
                      在新窗口安全预览
                      <ArrowUpRight className="w-3.5 h-3.5" />
                    </a>
                  </div>
                </div>
              ) : previewFile.type === 'video' ? (
                <video 
                  src={previewFile.remotePath} 
                  controls 
                  autoPlay
                  playsInline
                  className="max-w-full max-h-[70vh] rounded-xl shadow-2xl border border-slate-805/50 mx-auto bg-black"
                />
              ) : (previewFile.type === 'audio' || previewFile.type === 'voice') ? (
                <div className="bg-slate-900 border border-slate-805 rounded-2xl p-6 sm:p-10 max-w-md w-full shadow-2xl text-center space-y-6">
                  <div className="mx-auto w-16 h-16 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 flex items-center justify-center animate-pulse">
                    {getMediaIcon(previewFile.type, "w-8 h-8")}
                  </div>
                  <div className="space-y-1.5">
                    <h4 className="text-sm font-bold text-slate-200 line-clamp-2 px-2">{previewFile.name}</h4>
                    <p className="text-[10px] font-mono text-slate-500">大小: {formatSize(previewFile.sizeBytes)}</p>
                  </div>
                  <div className="pt-2">
                    <audio 
                      src={previewFile.remotePath} 
                      controls 
                      autoPlay
                      className="w-full"
                    />
                  </div>
                </div>
              ) : (
                <div className="bg-slate-900 border border-slate-805 rounded-2xl p-8 max-w-sm w-full text-center space-y-5 shadow-2xl">
                  <div className="mx-auto w-12 h-12 rounded-xl bg-sky-500/10 border border-sky-500/20 text-sky-400 flex items-center justify-center">
                    {getMediaIcon(previewFile.type, "w-6 h-6")}
                  </div>
                  <div className="space-y-1.5">
                    <h4 className="text-xs font-semibold text-slate-300 break-all">{previewFile.name}</h4>
                    <p className="text-[10px] font-mono text-slate-500">{previewFile.type.toUpperCase()} 文件</p>
                  </div>
                  <p className="text-[10px] text-slate-450 leading-relaxed">
                    此文件类型不支持在应用内直接流式回放。因为该文件已安全存储至 WebDAV 中，您可以直接下载或通过第三方预览服务浏览。
                  </p>
                  <div className="pt-2">
                    <a 
                      href={previewFile.remotePath} 
                      target="_blank" 
                      rel="noreferrer" 
                      className="px-4 py-2 bg-slate-800 hover:bg-slate-752 text-xs font-bold text-slate-200 rounded-lg transition-all flex items-center justify-center gap-1.5 font-sans"
                    >
                      调用 WebDAV 外部打开
                      <ArrowUpRight className="w-3.5 h-3.5" />
                    </a>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Footer Action Bar */}
          <div className="p-4 bg-slate-900/40 border-t border-slate-850/80 backdrop-blur flex flex-col sm:flex-row items-center justify-between gap-3 text-xs" onClick={e => e.stopPropagation()}>
            <span className="text-[11px] text-slate-500 font-mono">
              HASH URL: {previewFile.remotePath.substring(0, 50)}...
            </span>
            <div className="flex items-center gap-3 w-full sm:w-auto justify-end">
              <button
                onClick={() => handleCopyPath(previewFile.remotePath, previewFile.id)}
                className="w-full sm:w-auto px-4 py-2 bg-slate-800 hover:bg-slate-750 rounded-lg text-slate-300 hover:text-white transition-all text-xs font-medium flex items-center justify-center gap-1.5 active:scale-95 border border-slate-750 cursor-pointer font-sans"
              >
                {copiedId === previewFile.id ? (
                  <>
                    <Check className="w-3.5 h-3.5 text-emerald-400" />
                    已复制 WebDAV 地址
                  </>
                ) : (
                  <>
                    <Copy className="w-3.5 h-3.5" />
                    复制远程路径
                  </>
                )}
              </button>
              <a 
                href={previewFile.remotePath} 
                target="_blank" 
                rel="noreferrer" 
                className="w-full sm:w-auto px-4 py-2 bg-gradient-to-r from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 text-slate-50 rounded-lg shadow-lg shadow-indigo-950/20 text-xs font-bold flex items-center justify-center gap-1 transition-all hover:scale-[1.02] active:scale-[0.98] cursor-pointer font-sans"
              >
                直接下载 / 全屏预览
                <ArrowUpRight className="w-3.5 h-3.5" />
              </a>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
