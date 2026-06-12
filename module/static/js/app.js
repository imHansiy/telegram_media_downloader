
class App {
    constructor() {
        this.downloadList = [];
        this.downloadedList = [];
        this.downloadState = null;
        this.intervals = {};
        this.currentFilter = 'all';
        this.lastData = [];
        this.historyCollapsedFolders = new Set();

        this.init();
    }

    init() {
        // Run globally
        this.fetchVersion();

        // Only run on dashboard
        if (document.getElementById('dashboard-view')) {
            this.setupSSE();
            this.setupEventListeners();
        }
    }

    setupEventListeners() {
        const stateBtn = document.getElementById('state-toggle-btn');
        if (stateBtn) {
            stateBtn.addEventListener('click', () => this.toggleDownloadState());
        }
    }

    async fetchVersion() {
        try {
            const res = await fetch('get_app_version');
            const text = await res.text();
            const el = document.getElementById('app-version');
            if (el) el.textContent = text;
        } catch (e) {
            console.error('Failed to fetch version', e);
        }
    }

    setupSSE() {
        if (this.evtSource) {
            this.evtSource.close();
        }

        this.evtSource = new EventSource('stream');

        this.evtSource.onmessage = (event) => {
            try {
                const payload = JSON.parse(event.data);

                if (payload.type === 'update') {
                    if (payload.status) {
                        this.updateMetric('stat-download-speed', payload.status.download_speed || '0 B/s');
                        this.updateMetric('stat-upload-speed', payload.status.upload_speed || '0 B/s');
                    }

                    // Update Active Tasks
                    if (payload.tasks) {
                        this.lastData = payload.tasks;
                        this.applyFilter();
                    }

                    // Update History (if present)
                    if (payload.history) {
                        this.renderDownloadedTable(payload.history);
                    }
                }
            } catch (e) {
                console.error('Error parsing SSE data', e);
            }
        };

        this.evtSource.onerror = (err) => {
            console.error('SSE Error:', err);
        };
    }

    updateMetric(id, value) {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    }

    async updateDownloadList() {
        try {
            const res = await fetch('get_download_list?already_down=false');
            const data = await res.json();
            this.lastData = data;
            this.applyFilter();
        } catch (e) {
            console.error('Error fetching download list', e);
        }
    }

    applyFilter() {
        if (!this.lastData) return;

        let filtered = this.lastData;

        if (this.currentFilter !== 'all') {
            filtered = this.lastData.filter(item => {
                const ext = item.filename.split('.').pop().toLowerCase();
                if (this.currentFilter === 'video') return ['mp4', 'mkv', 'avi', 'mov', 'webm'].includes(ext);
                if (this.currentFilter === 'image') return ['jpg', 'jpeg', 'png', 'gif', 'webp'].includes(ext);
                if (this.currentFilter === 'audio') return ['mp3', 'wav', 'flac', 'aac', 'm4a'].includes(ext);
                if (this.currentFilter === 'other') {
                    const known = ['mp4', 'mkv', 'avi', 'mov', 'webm', 'jpg', 'jpeg', 'png', 'gif', 'webp', 'mp3', 'wav', 'flac', 'aac', 'm4a'];
                    return !known.includes(ext);
                }
                return true;
            });
        }

        this.renderActiveTable(filtered);
    }

    async updateDownloadedList() {
        try {
            const res = await fetch('get_download_list?already_down=true');
            const data = await res.json();
            const finished = data.filter(item => parseFloat(item.download_progress) >= 100);
            this.renderDownloadedTable(finished);
        } catch (e) {
            console.error('Error fetching downloaded list', e);
        }
    }



    async toggleDownloadState() {
        const btn = document.getElementById('state-toggle-btn');
        const isRunning = btn.classList.contains('btn-danger');
        const action = isRunning ? 'pause' : 'continue';

        try {
            const res = await fetch(`set_download_state?state=${action}`, { method: 'POST' });
            const result = await res.text();

            if (result === 'pause') {
                btn.classList.remove('btn-primary');
                btn.classList.add('btn-danger');
                btn.innerHTML = '停止下载';
            } else {
                btn.classList.remove('btn-danger');
                btn.classList.add('btn-primary');
                btn.innerHTML = '开始下载';
            }

        } catch (e) {
            console.error('Error toggling state', e);
        }
    }

    switchTab(tabName) {
        // Content switching
        document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
        document.getElementById(`tab-${tabName}`).classList.remove('hidden');

        // Tab styling switching
        const btns = document.querySelectorAll('.tab-btn');
        btns.forEach(btn => btn.classList.remove('active'));

        if (tabName === 'active') btns[0].classList.add('active');
        else btns[1].classList.add('active');
    }

    getFileIcon(filename) {
        const ext = filename.split('.').pop().toLowerCase();
        if (['mp4', 'mkv', 'avi', 'mov', 'webm'].includes(ext)) return '🎬';
        if (['jpg', 'jpeg', 'png', 'gif', 'webp'].includes(ext)) return '🖼️';
        if (['mp3', 'wav', 'flac', 'aac', 'm4a'].includes(ext)) return '🎵';
        if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) return '📦';
        return '📄';
    }

    getStatusBadgeClass(status) {
        switch (status) {
            case '上传中':
                return 'bg-success/20 text-success border border-success/30';
            case '下载中':
                return 'bg-accent/20 text-accent border border-accent/30';
            case '准备上传':
                return 'bg-warning/20 text-warning border border-warning/30';
            case '正在完成...':
                return 'bg-purple-500/20 text-purple-400 border border-purple-500/30';
            case '已完成':
                return 'bg-success/20 text-success border border-success/30';
            default:
                return 'bg-secondary/20 text-secondary border border-secondary/30';
        }
    }

    renderActiveTable(data) {
        // Update count badge
        const countBadge = document.getElementById('active-count');
        if (countBadge) countBadge.textContent = data.length;
        this.updateMetric('stat-active-total', data.length);

        const tbody = document.getElementById('active-tbody');
        if (!tbody) return;

        if (data.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="8" class="text-center py-12">
                        <div class="flex flex-col items-center justify-center opacity-50">
                            <svg class="icon mb-2" style="width:48px;height:48px;" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                            <span class="text-secondary text-sm">暂无进行中的任务</span>
                        </div>
                    </td>
                </tr>`;
            return;
        }

        const html = data.map(item => {
            const icon = this.getFileIcon(item.filename);
            const downloadProgress = parseFloat(item.download_progress) || 0;
            const uploadProgress = parseFloat(item.upload_progress) || 0;

            // Speed values
            const dlSpeedStr = item.download_speed || '0 B/s';
            const ulSpeedStr = item.upload_speed || '0 B/s';

            return `
            <tr class="hover:bg-surface/50 transition-colors border-b border-border/50">
                <td class="text-center text-xl py-3">${icon}</td>
                <td class="text-secondary text-xs font-mono">${item.chat}</td>
                <td class="py-3">
                    <div class="truncate font-medium text-text text-sm" style="max-width: 280px;" title="${item.filename}">
                        ${item.filename}
                    </div>
                    <span class="inline-flex items-center px-2 py-0.5 mt-1 rounded-full text-[10px] font-medium ${this.getStatusBadgeClass(item.status)}">
                        ${item.status || '等待中'}
                    </span>
                </td>
                <td class="text-secondary text-xs font-mono">${item.total_size}</td>
                <td class="text-secondary text-[10px] font-mono">${item.created_at || '-'}</td>
                <td style="min-width: 120px;" class="py-3">
                    <div class="flex flex-col gap-1">
                        <div class="h-1.5 w-full bg-border/30 rounded-full overflow-hidden">
                            <div class="h-full bg-accent transition-all duration-500 shadow-[0_0_10px_rgba(59,130,246,0.5)]" style="width: ${downloadProgress}%"></div>
                        </div>
                        <span class="text-[10px] font-mono text-secondary text-right w-full">${downloadProgress.toFixed(1)}%</span>
                    </div>
                </td>
                <td style="min-width: 120px;" class="py-3">
                    <div class="flex flex-col gap-1">
                        <div class="h-1.5 w-full bg-border/30 rounded-full overflow-hidden">
                            <div class="h-full bg-success transition-all duration-500 shadow-[0_0_10px_rgba(16,185,129,0.5)]" style="width: ${uploadProgress}%"></div>
                        </div>
                        <span class="text-[10px] font-mono text-secondary text-right w-full">${uploadProgress.toFixed(1)}%</span>
                    </div>
                </td>
                <td class="text-right font-mono text-xs py-3" style="min-width: 90px;">
                    <div class="flex flex-col gap-0.5">
                        <span class="text-accent font-bold" title="下载速度">↓ ${dlSpeedStr}</span>
                        <span class="text-success font-bold" title="上传速度">↑ ${ulSpeedStr}</span>
                    </div>
                </td>
                <td class="text-right py-3 px-4">
                    <div class="flex justify-end gap-1">
                        ${item.state === 'paused' ?
                    `<button onclick="taskAction('resume', '${item.chat}', '${item.id}')" class="p-1.5 text-success hover:bg-success/10 rounded transition-colors" title="继续">
                                <svg class="icon w-4 h-4" viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
                             </button>` :
                    `<button onclick="taskAction('pause', '${item.chat}', '${item.id}')" class="p-1.5 text-warning hover:bg-warning/10 rounded transition-colors" title="暂停">
                                <svg class="icon w-4 h-4" viewBox="0 0 24 24"><rect x="6" y="4" width="4" height="16"></rect><rect x="14" y="4" width="4" height="16"></rect></svg>
                             </button>`
                }
                        <button onclick="taskAction('delete', '${item.chat}', '${item.id}')" class="p-1.5 text-danger hover:bg-danger/10 rounded transition-colors" title="取消任务">
                            <svg class="icon w-4 h-4" viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                        </button>
                    </div>
                </td>
            </tr>
        `}).join('');

        tbody.innerHTML = html;
    }

    escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, char => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        }[char]));
    }

    getFileExtension(filename) {
        const parts = String(filename || '').split('.');
        return parts.length > 1 ? parts.pop().toLowerCase() : '';
    }

    getFileTypeRank(filename) {
        const ext = this.getFileExtension(filename);
        if (['mp4', 'mkv', 'avi', 'mov', 'webm'].includes(ext)) return 1;
        if (['jpg', 'jpeg', 'png', 'gif', 'webp'].includes(ext)) return 2;
        if (['mp3', 'wav', 'flac', 'aac', 'm4a'].includes(ext)) return 3;
        if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) return 4;
        if (['pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt'].includes(ext)) return 5;
        return 6;
    }

    compareNames(a, b) {
        return String(a || '').localeCompare(String(b || ''), 'zh-Hans', {
            numeric: true,
            sensitivity: 'base'
        });
    }

    getHistoryTimestamp(item) {
        const raw = Number(item.completed_ts || item.created_ts || 0);
        if (Number.isFinite(raw) && raw > 0) return raw;

        const text = item.completed_at || item.created_at || '';
        const parsed = Date.parse(String(text).replace(' ', 'T'));
        return Number.isNaN(parsed) ? 0 : parsed / 1000;
    }

    getHistoryPathParts(item) {
        const fallbackName = item.filename || 'Unknown';
        const rawPath = item.relative_path || item.remote_path || item.save_path || fallbackName;
        const parts = String(rawPath)
            .replace(/\\/g, '/')
            .split('/')
            .filter(Boolean);

        if (parts.length === 0) return [fallbackName];

        const lastPart = parts[parts.length - 1];
        if (String(lastPart).toLowerCase() !== String(fallbackName).toLowerCase()) {
            parts.push(fallbackName);
        }

        return parts;
    }

    touchHistoryFolder(folder, item, ts) {
        folder.count += 1;
        if (ts >= folder.latestTs) {
            folder.latestTs = ts;
            folder.latestLabel = item.completed_at || item.created_at || '-';
        }
    }

    buildHistoryTree(data) {
        const root = {
            name: '',
            path: '',
            children: new Map(),
            files: [],
            count: 0,
            latestTs: 0,
            latestLabel: '-'
        };

        data.forEach(item => {
            const parts = this.getHistoryPathParts(item);
            const fileName = parts.pop() || item.filename || 'Unknown';
            const ts = this.getHistoryTimestamp(item);
            let folder = root;

            this.touchHistoryFolder(folder, item, ts);

            parts.forEach(part => {
                const childPath = folder.path ? `${folder.path}/${part}` : part;
                if (!folder.children.has(part)) {
                    folder.children.set(part, {
                        name: part,
                        path: childPath,
                        children: new Map(),
                        files: [],
                        count: 0,
                        latestTs: 0,
                        latestLabel: '-'
                    });
                }

                folder = folder.children.get(part);
                this.touchHistoryFolder(folder, item, ts);
            });

            folder.files.push({
                ...item,
                filename: item.filename || fileName,
                _historyTs: ts,
                _historyExt: this.getFileExtension(item.filename || fileName),
                _historyTypeRank: this.getFileTypeRank(item.filename || fileName)
            });
        });

        return root;
    }

    getSortedHistoryFolders(folder, depth) {
        return Array.from(folder.children.values()).sort((a, b) => {
            if (depth === 0 && b.latestTs !== a.latestTs) {
                return b.latestTs - a.latestTs;
            }

            return this.compareNames(a.name, b.name);
        });
    }

    getSortedHistoryFiles(folder) {
        return folder.files.slice().sort((a, b) => {
            if (a._historyTypeRank !== b._historyTypeRank) {
                return a._historyTypeRank - b._historyTypeRank;
            }

            const extOrder = this.compareNames(a._historyExt, b._historyExt);
            if (extOrder !== 0) return extOrder;

            return this.compareNames(a.filename, b.filename);
        });
    }

    renderHistoryFolderRow(folder, depth) {
        const folderPath = this.escapeHtml(folder.path);
        const folderName = this.escapeHtml(folder.name);
        const folderArg = this.escapeHtml(JSON.stringify(folder.path));
        const collapsed = this.historyCollapsedFolders.has(folder.path);
        const indent = Math.min(depth * 22, 132);
        const remotePath = this.escapeHtml(folder.path);

        return `
            <tr class="history-folder-row" data-folder-path="${folderPath}">
                <td colspan="7" class="history-folder-cell">
                    <button onclick="toggleHistoryFolder(${folderArg})" class="history-folder-card" style="margin-left: ${indent}px; width: calc(100% - ${indent}px);" title="${folderPath}">
                        <span class="history-folder-art" aria-hidden="true"></span>
                        <span class="history-folder-main">
                            <span class="history-folder-title">
                                <span class="history-folder-chevron">${collapsed ? '▸' : '▾'}</span>
                                <span class="truncate">${folderName}</span>
                            </span>
                            <span class="history-folder-meta">
                                <span>${folder.count} 项</span>
                                <span>${this.escapeHtml(folder.latestLabel)}</span>
                                <span class="truncate">${remotePath}</span>
                            </span>
                        </span>
                    </button>
                </td>
            </tr>`;
    }

    renderHistoryFileRow(item, depth) {
        const icon = this.getFileIcon(item.filename);
        const remotePath = item.remote_path || item.save_path || '-';
        const indent = Math.min(depth * 18, 108);

        return `
            <tr class="hover:bg-surface/50 transition-colors border-b border-border/50" data-chat="${this.escapeHtml(item.chat)}" data-id="${this.escapeHtml(item.id)}">
                <td class="text-center text-xl py-3">${icon}</td>
                <td class="text-secondary text-xs font-mono">${this.escapeHtml(item.id)}</td>
                <td class="py-3">
                    <div class="history-name-cell text-text text-sm" style="padding-left: ${indent}px;" title="${this.escapeHtml(item.filename)}">
                        <span class="truncate">${this.escapeHtml(item.filename)}</span>
                    </div>
                </td>
                <td class="text-secondary text-xs font-mono">${this.escapeHtml(item.total_size)}</td>
                <td class="text-secondary text-[10px] font-mono">${this.escapeHtml(item.completed_at || item.created_at || '-')}</td>
                <td class="text-secondary text-xs truncate" style="max-width: 240px;" title="${this.escapeHtml(remotePath)}">${this.escapeHtml(remotePath)}</td>
                <td class="text-center py-3">
                    <button onclick="removeTask('${this.escapeHtml(item.chat)}', '${this.escapeHtml(item.id)}')"
                        class="text-secondary hover:text-danger transition-colors p-1 rounded hover:bg-danger/10"
                        title="删除此记录">
                        <svg class="icon" style="width:16px;height:16px;" viewBox="0 0 24 24">
                            <polyline points="3 6 5 6 21 6"></polyline>
                            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                        </svg>
                    </button>
                </td>
            </tr>`;
    }

    renderHistoryTreeRows(folder, depth = 0) {
        const rows = [];

        this.getSortedHistoryFolders(folder, depth).forEach(child => {
            rows.push(this.renderHistoryFolderRow(child, depth));
            if (!this.historyCollapsedFolders.has(child.path)) {
                rows.push(this.renderHistoryTreeRows(child, depth + 1));
            }
        });

        this.getSortedHistoryFiles(folder).forEach(item => {
            rows.push(this.renderHistoryFileRow(item, depth));
        });

        return rows.join('');
    }

    renderDownloadedTable(data) {
        // Update count badge
        const countBadge = document.getElementById('history-count');
        if (countBadge) countBadge.textContent = data.length;
        this.updateMetric('stat-history-total', data.length);
        this.downloadedList = Array.isArray(data) ? data : [];

        const tbody = document.getElementById('downloaded-tbody');
        if (!tbody) return;

        if (this.downloadedList.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="7" class="text-center py-12">
                        <div class="flex flex-col items-center justify-center opacity-50">
                            <svg class="icon mb-2" style="width:48px;height:48px;" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="12" cy="12" r="10"/><path d="M8 12l2 2 4-4"/></svg>
                            <span class="text-secondary text-sm">暂无历史记录</span>
                        </div>
                    </td>
                </tr>`;
            return;
        }

        const tree = this.buildHistoryTree(this.downloadedList);
        tbody.innerHTML = this.renderHistoryTreeRows(tree);
    }

    toggleHistoryFolder(path) {
        if (this.historyCollapsedFolders.has(path)) {
            this.historyCollapsedFolders.delete(path);
        } else {
            this.historyCollapsedFolders.add(path);
        }

        this.renderDownloadedTable(this.downloadedList || []);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.app = new App();

    // Bind global functions for inline HTML onclicks (if module system not used)
    window.switchTab = (tabName) => window.app.switchTab(tabName);
    window.filterType = (type) => {
        console.log('Filter:', type);

        // Update UI
        document.querySelectorAll('.filter-chip').forEach(el => {
            el.classList.remove('active');
        });

        const btn = event.currentTarget || event.target.closest('button');
        if (btn) btn.classList.add('active');

        // Logic
        if (window.app) {
            window.app.currentFilter = type;
            window.app.applyFilter();
        }
    };
    window.toggleHistoryFolder = (path) => window.app.toggleHistoryFolder(path);
});

// Clear all history
async function clearHistory() {
    if (!confirm('确定要清空所有历史记录吗？此操作不可恢复。')) {
        return;
    }

    try {
        const response = await fetch('/clear_history', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const result = await response.json();
        if (result.success) {
            // Clear the table
            const tbody = document.getElementById('downloaded-tbody');
            if (tbody) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="7" class="text-center py-12">
                            <div class="flex flex-col items-center justify-center opacity-50">
                                <svg class="icon mb-2" style="width:48px;height:48px;" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="12" cy="12" r="10"/><path d="M8 12l2 2 4-4"/></svg>
                                <span class="text-secondary text-sm">暂无历史记录</span>
                            </div>
                        </td>
                    </tr>`;
            }
            // Update count
            const countBadge = document.getElementById('history-count');
            if (countBadge) countBadge.textContent = '0';

            console.log('历史记录已清空');
        } else {
            alert('清空失败: ' + result.message);
        }
    } catch (error) {
        console.error('清空历史失败:', error);
        alert('清空历史失败，请查看控制台');
    }
}

// Remove single task
async function removeTask(chatId, messageId) {
    try {
        const response = await fetch('/remove_task', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ chat_id: chatId, message_id: messageId })
        });

        const result = await response.json();
        if (result.success) {
            if (window.app) {
                window.app.downloadedList = (window.app.downloadedList || []).filter(item => (
                    String(item.chat) !== String(chatId) || String(item.id) !== String(messageId)
                ));
                window.app.renderDownloadedTable(window.app.downloadedList);
            } else {
                const row = document.querySelector(`tr[data-chat="${chatId}"][data-id="${messageId}"]`);
                if (row) row.remove();
            }
        } else {
            console.error('删除失败:', result.message);
        }
    } catch (error) {
        console.error('删除任务失败:', error);
    }
}

// Global task action (pause/resume/delete)
async function taskAction(action, chatId, messageId) {
    if (action === 'delete' && !confirm('确定要取消并删除此任务吗？')) {
        return;
    }

    try {
        const response = await fetch('/task_control', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: action, chat_id: chatId, message_id: messageId })
        });

        const result = await response.json();
        if (result.success) {
            console.log(`任务 ${messageId} 已 ${action}`);
            // The UI will update automatically via SSE on the next tick
        } else {
            alert('操作失败: ' + result.message);
        }
    } catch (error) {
        console.error('任务操作错误:', error);
    }
}
