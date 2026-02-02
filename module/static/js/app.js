
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

    updateStatus(data) {
        if (!data) return;

        const el = document.getElementById('global-speed');
        if (el) el.textContent = data.download_speed;

        const upEl = document.getElementById('global-upload-speed');
        if (upEl) upEl.textContent = data.upload_speed || data.download_speed;
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

    renderActiveTable(data) {
        // Update count badge
        const countBadge = document.getElementById('active-count');
        if (countBadge) countBadge.textContent = data.length;

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

            // Speed display logic
            let speedDisplay = item.download_speed || '0 B/s';
            if (downloadProgress >= 100 && uploadProgress < 100) {
                speedDisplay = item.upload_speed || '上传中...';
            }

            return `
            <tr class="hover:bg-surface/50 transition-colors border-b border-border/50">
                <td class="text-center text-xl py-3">${icon}</td>
                <td class="text-secondary text-xs font-mono">${item.chat}</td>
                <td class="py-3">
                    <div class="truncate font-medium text-text text-sm" style="max-width: 280px;" title="${item.filename}">
                        ${item.filename}
                    </div>
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
                <td class="text-right font-mono text-accent text-xs font-bold py-3">${speedDisplay}</td>
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
                    <td colspan="6" class="text-center py-12">
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
            // Use remote_path if available, otherwise fall back to save_path
            const remotePath = item.remote_path || item.save_path || '-';
            return `
            <tr class="hover:bg-surface/50 transition-colors border-b border-border/50">
                <td class="text-center text-xl py-3">${icon}</td>
                <td class="text-secondary text-xs font-mono">${item.id}</td>
                <td class="py-3"><div class="truncate text-text text-sm" style="max-width: 300px;" title="${item.filename}">${item.filename}</div></td>
                <td class="text-secondary text-xs font-mono">${item.total_size}</td>
                <td class="text-secondary text-[10px] font-mono">${item.created_at || '-'}</td>
                <td class="text-secondary text-xs truncate" style="max-width: 200px;" title="${remotePath}">${remotePath}</td>
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

        const btn = event.currentTarget || event.target.closest('button');
        if (btn) btn.classList.add('active');

        // Logic
        if (window.app) {
            window.app.currentFilter = type;
            window.app.applyFilter();
        }
    };
});
