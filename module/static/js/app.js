
class App {
    constructor() {
        this.downloadList = [];
        this.downloadedList = [];
        this.downloadState = null;
        this.intervals = {};
        this.currentFilter = 'all';
        this.lastData = [];

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
        // Toggle tabs if we keep them, or just separate sections. 
        // For now, let's assume valid DOM elements exist.

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
                    // Update Status
                    if (payload.status) {
                        this.updateStatus(payload.status);
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
            // Browser automatically reconnects, but we can handle UI state here if needed
        };
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

        this.renderDownloadingTable(filtered);
        this.renderUploadingTable(filtered);
    }

    async updateDownloadedList() {
        try {
            const res = await fetch('get_download_list?already_down=true');
            const data = await res.json();
            // Filter only 100% progress just in case, logic copied from legacy
            // Parse float to handle string "100.0" from backend
            const finished = data.filter(item => parseFloat(item.download_progress) >= 100);
            this.renderDownloadedTable(finished);
        } catch (e) {
            console.error('Error fetching downloaded list', e);
        }
    }

    updateStatus(data) {
        if (!data) return;

        const el = document.getElementById('global-speed');
        if (el) el.textContent = data.download_speed;

        // Mirror download speed to upload speed for streaming indication
        const upEl = document.getElementById('global-upload-speed');
        // If backend provides upload speed, use it, else fallback
        if (upEl) upEl.textContent = data.upload_speed || data.download_speed;
    }

    async toggleDownloadState() {
        const btn = document.getElementById('state-toggle-btn');
        const isRunning = btn.classList.contains('btn-danger');
        const action = isRunning ? 'pause' : 'continue';

        try {
            const res = await fetch(`set_download_state?state=${action}`, { method: 'POST' });
            const result = await res.text();

            // Backend returns the NEXT action label (e.g. "pause" means it is now running, so user can pause)

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
        else if (tabName === 'upload') btns[1].classList.add('active');
        else btns[2].classList.add('active');
    }

    getFileIcon(filename) {
        const ext = filename.split('.').pop().toLowerCase();
        if (['mp4', 'mkv', 'avi', 'mov', 'webm'].includes(ext)) return '🎬';
        if (['jpg', 'jpeg', 'png', 'gif', 'webp'].includes(ext)) return '🖼️';
        if (['mp3', 'wav', 'flac', 'aac', 'm4a'].includes(ext)) return '🎵';
        if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) return '📦';
        return '📄';
    }

    renderDownloadingTable(data) {
        // Update count badge
        const countBadge = document.getElementById('active-count');
        if (countBadge) countBadge.textContent = data.length;

        const tbody = document.getElementById('downloading-tbody');
        if (!tbody) return;

        if (data.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="7" class="text-center py-12">
                        <div class="flex flex-col items-center justify-center opacity-50">
                            <svg class="icon mb-2" style="width:48px;height:48px;" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                            <span class="text-secondary text-sm">暂无进行中的下载任务</span>
                        </div>
                    </td>
                </tr>`;
            return;
        }

        const html = data.map(item => {
            const icon = this.getFileIcon(item.filename);
            return `
            <tr class="hover:bg-surface/50 transition-colors border-b border-border/50">
                <td class="text-center text-xl py-3">${icon}</td>
                <td class="text-secondary text-xs font-mono">${item.chat}</td>
                <td class="py-3">
                    <div class="truncate font-medium text-text text-sm" style="max-width: 320px;" title="${item.filename}">
                        ${item.filename}
                    </div>
                </td>
                <td class="py-3">
                    <span class="badge badge-soft badge-accent-soft">
                        <span class="flex items-center gap-1"><span class="w-1.5 h-1.5 rounded-full bg-accent animate-pulse"></span> 下载中</span>
                    </span>
                </td>
                <td class="text-secondary text-xs font-mono">${item.total_size}</td>
                <td style="min-width: 150px;" class="py-3">
                    <div class="flex flex-col gap-1">
                        <div class="h-1.5 w-full bg-border/30 rounded-full overflow-hidden">
                            <div class="h-full bg-accent transition-all duration-500 shadow-[0_0_10px_rgba(59,130,246,0.5)]" style="width: ${item.download_progress}%"></div>
                        </div>
                        <span class="text-[10px] font-mono text-secondary text-right w-full">${item.download_progress}%</span>
                    </div>
                </td>
                <td class="text-right font-mono text-accent text-xs font-bold py-3">${item.download_speed}</td>
            </tr>
        `}).join('');

        tbody.innerHTML = html;
    }

    renderUploadingTable(data) {
        // Update count badge
        const countBadge = document.getElementById('upload-count');
        if (countBadge) countBadge.textContent = data.length;

        const tbody = document.getElementById('uploading-tbody');
        if (!tbody) return;

        if (data.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="7" class="text-center py-12">
                        <div class="flex flex-col items-center justify-center opacity-50">
                            <svg class="icon mb-2" style="width:48px;height:48px;" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                            <span class="text-secondary text-sm">暂无上传中的任务</span>
                        </div>
                    </td>
                </tr>`;
            return;
        }

        const html = data.map(item => {
            const icon = this.getFileIcon(item.filename);
            const progress = parseFloat(item.download_progress);
            const isPaused = item.download_speed.startsWith('0') || item.download_speed === '0B/s';

            let statusHtml = '';
            if (progress >= 100) {
                statusHtml = '<span class="badge badge-soft badge-success-soft">✅ 完成</span>';
            } else if (progress <= 0) {
                statusHtml = '<span class="badge badge-soft badge-secondary-soft">⏳ 等待</span>';
            } else if (isPaused) {
                statusHtml = '<span class="badge badge-soft badge-warning-soft">⏸️ 暂停</span>';
            } else {
                statusHtml = '<span class="badge badge-soft badge-success-soft"><span class="flex items-center gap-1"><span class="w-1.5 h-1.5 rounded-full bg-success animate-pulse"></span> 上传中</span></span>';
            }

            return `
            <tr class="hover:bg-surface/50 transition-colors border-b border-border/50">
                <td class="text-center text-xl py-3">${icon}</td>
                <td class="text-secondary text-xs font-mono">${item.chat}</td>
                <td class="py-3">
                    <div class="truncate font-medium text-text text-sm" style="max-width: 320px;" title="${item.filename}">
                        ${item.filename}
                    </div>
                </td>
                <td class="py-3">
                    ${statusHtml}
                </td>
                <td class="text-secondary text-xs font-mono">${item.total_size}</td>
                <td style="min-width: 150px;" class="py-3">
                     <div class="flex flex-col gap-1">
                        <div class="h-1.5 w-full bg-border/30 rounded-full overflow-hidden">
                            <div class="h-full bg-success transition-all duration-500 shadow-[0_0_10px_rgba(16,185,129,0.5)]" style="width: ${item.download_progress}%"></div>
                        </div>
                        <span class="text-[10px] font-mono text-secondary text-right w-full">${item.download_progress}%</span>
                    </div>
                </td>
                <td class="text-right font-mono text-success text-xs font-bold py-3">${item.download_speed}</td>
            </tr>
        `}).join('');

        tbody.innerHTML = html;
    }

    renderDownloadedTable(data) {
        // Update count badge
        const countBadge = document.getElementById('history-count');
        if (countBadge) countBadge.textContent = data.length;

        const tbody = document.getElementById('downloaded-tbody');
        if (!tbody) return;

        // Limit to last 50 for performance if list is huge
        const displayData = data.slice(-50).reverse();

        if (displayData.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="5" class="text-center py-12">
                        <div class="flex flex-col items-center justify-center opacity-50">
                            <svg class="icon mb-2" style="width:48px;height:48px;" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="12" cy="12" r="10"/><path d="M8 12l2 2 4-4"/></svg>
                            <span class="text-secondary text-sm">暂无历史记录</span>
                        </div>
                    </td>
                </tr>`;
            return;
        }

        const html = displayData.map(item => {
            const icon = this.getFileIcon(item.filename);
            return `
            <tr class="hover:bg-surface/50 transition-colors border-b border-border/50">
                <td class="text-center text-xl py-3">${icon}</td>
                <td class="text-secondary text-xs font-mono">${item.id}</td>
                <td class="py-3"><div class="truncate text-text text-sm" style="max-width: 300px;" title="${item.filename}">${item.filename}</div></td>
                <td class="text-secondary text-xs font-mono">${item.total_size}</td>
                <td class="text-secondary text-xs truncate" style="max-width: 200px;" title="${item.save_path}">${item.save_path}</td>
            </tr>
        `}).join('');

        tbody.innerHTML = html;
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

        // Since event.target might be the button or child, assume button via currentTarget/closest
        const btn = event.currentTarget || event.target.closest('button');
        if (btn) btn.classList.add('active');

        // Logic
        if (window.app) {
            window.app.currentFilter = type;
            window.app.applyFilter();
        }
    };
});
