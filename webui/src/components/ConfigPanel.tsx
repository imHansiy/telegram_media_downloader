/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from 'react';
import { 
  CloudStorageConfig, 
  SyncRule, 
  MediaType 
} from '../types';
import { 
  Save, 
  RefreshCw, 
  Check, 
  Sliders, 
  Cloud, 
  AlertTriangle, 
  Lock, 
  Key, 
  Terminal, 
  CheckCircle2, 
  HelpCircle,
  FolderSync
} from 'lucide-react';

interface ConfigPanelProps {
  config: CloudStorageConfig;
  rule: SyncRule;
  onSaveConfig: (config: CloudStorageConfig) => void;
  onSaveRule: (rule: SyncRule) => void;
  onSaveAll?: (config: CloudStorageConfig, rule: SyncRule) => void;
}

export function ConfigPanel({ 
  config, 
  rule, 
  onSaveConfig, 
  onSaveRule,
  onSaveAll,
}: ConfigPanelProps) {
  // Local states for cloud storage config
  const [davUrl, setDavUrl] = useState(config.url);
  const [davUser, setDavUser] = useState(config.username);
  const [davPass, setDavPass] = useState(config.password || '');
  const [davDir, setDavDir] = useState(config.remoteDir);
  const [dlRate, setDlRate] = useState(config.downloadRateLimitKb);
  const [ulRate, setUlRate] = useState(config.uploadRateLimitKb);

  // Local states for Sync rules
  const [mediaTypes, setMediaTypes] = useState<MediaType[]>(rule.mediaTypes);
  const [minSize, setMinSize] = useState(rule.minSizeMb);
  const [maxSize, setMaxSize] = useState(rule.maxSizeMb);
  const [savePathPattern, setSavePathPattern] = useState(rule.savePathPattern);
  const [autoSync, setAutoSync] = useState(rule.autoSync);
  const [dateThreshold, setDateThreshold] = useState(rule.dateThreshold);

  // Connection testing state
  const [testState, setTestState] = useState<'idle' | 'checking' | 'success' | 'failed'>('idle');
  const [testMsg, setTestMsg] = useState('');

  // Notification feedbacks
  const [saveFeedback, setSaveFeedback] = useState(false);

  useEffect(() => {
    setDavUrl(config.url || '');
    setDavUser(config.username || '');
    setDavPass(config.password || '');
    setDavDir(config.remoteDir || '');
    setDlRate(config.downloadRateLimitKb || 0);
    setUlRate(config.uploadRateLimitKb || 0);
  }, [config]);

  useEffect(() => {
    setMediaTypes(rule.mediaTypes || []);
    setMinSize(rule.minSizeMb || 0);
    setMaxSize(rule.maxSizeMb || 0);
    setSavePathPattern(rule.savePathPattern);
    setAutoSync(rule.autoSync);
    setDateThreshold(rule.dateThreshold);
  }, [rule]);

  const handleTestConnection = async (e: React.MouseEvent) => {
    e.preventDefault();
    if (!davUrl || !davUser) {
      setTestState('failed');
      setTestMsg('错误: 必须填写 WebDAV 服务器地址和账号名！');
      return;
    }

    setTestState('checking');
    setTestMsg('正在连接 WebDAV 服务器...');

    try {
      const response = await fetch('/test_webdav', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: davUrl,
          username: davUser,
          password: davPass,
        }),
      });
      const data = await response.json();
      setTestState(data.success ? 'success' : 'failed');
      setTestMsg(data.message || (data.success ? '连接测试成功。' : '连接测试失败。'));
    } catch (error) {
      setTestState('failed');
      setTestMsg(error instanceof Error ? error.message : String(error));
    }
  };

  const toggleMediaType = (type: MediaType) => {
    if (mediaTypes.includes(type)) {
      setMediaTypes(mediaTypes.filter(t => t !== type));
    } else {
      setMediaTypes([...mediaTypes, type]);
    }
  };

  const handleSaveAll = (e: React.FormEvent) => {
    e.preventDefault();

    const nextConfig = {
      type: 'webdav',
      url: davUrl,
      username: davUser,
      password: davPass,
      remoteDir: davDir,
      downloadRateLimitKb: Number(dlRate),
      uploadRateLimitKb: Number(ulRate)
    } as CloudStorageConfig;

    const nextRule = {
      ...rule,
      mediaTypes,
      minSizeMb: Number(minSize),
      maxSizeMb: Number(maxSize),
      savePathPattern,
      autoSync,
      dateThreshold
    } as SyncRule;

    if (onSaveAll) {
      onSaveAll(nextConfig, nextRule);
    } else {
      onSaveConfig(nextConfig);
      onSaveRule(nextRule);
    }

    // Show feedback popup toast
    setSaveFeedback(true);
    setTimeout(() => setSaveFeedback(false), 3000);
  };

  return (
    <form id="form-config-settings" onSubmit={handleSaveAll} className="space-y-6">
      
      {/* Save state notification banner */}
      {saveFeedback && (
        <div id="toast-save-success" className="bg-emerald-500/10 border border-emerald-500/30 p-3 rounded-lg flex items-center gap-2.5 text-xs text-emerald-400 font-medium animate-fadeIn">
          <Check className="w-4 h-4 bg-emerald-500/20 rounded-full p-0.5" />
          <span>同步策略与 WebDAV 云端挂载参数已成功保存并同步！后台服务将即时应用新规则。</span>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

        {/* Column 1: WebDAV Cloud Drive Config */}
        <div className="space-y-4 bg-slate-900 border border-slate-800 p-5 rounded-xl">
          <div className="flex items-center gap-2 border-b border-slate-800 pb-3">
            <Cloud className="w-4 h-4 text-indigo-400" />
            <div>
              <h3 className="text-xs font-semibold text-slate-200">云盘挂载配置 (WebDAV Target)</h3>
              <p className="text-[10px] text-slate-500">将电报文件自动传输并备份到支持 WebDAV 协议的存储提供商</p>
            </div>
          </div>

          <div className="space-y-3 text-xs">
            {/* Host url input */}
            <div className="space-y-1">
              <label className="block text-slate-400 font-medium">WebDAV 服务器地址 (Host URL)</label>
              <input
                id="input-dav-url"
                type="text"
                required
                className="w-full bg-slate-950/70 border border-slate-800 focus:border-indigo-500 rounded-lg p-2.5 text-slate-300 font-mono focus:outline-none"
                placeholder="例如: https://dav.jianguoyun.com/dav/"
                value={davUrl}
                onChange={(e) => setDavUrl(e.target.value)}
              />
              <span className="text-[10px] text-slate-500 block">
                支持坚果云、Nextcloud、OwnCloud、Alist、OneDrive WebDAV 等
              </span>
            </div>

            {/* Username password grids */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="block text-slate-400 font-medium">账号/电子邮箱 (Username)</label>
                <input
                  id="input-dav-user"
                  type="text"
                  required
                  className="w-full bg-slate-950/70 border border-slate-800 focus:border-indigo-500 rounded-lg p-2.5 text-slate-300 focus:outline-none"
                  placeholder="name@example.com"
                  value={davUser}
                  onChange={(e) => setDavUser(e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <label className="block text-slate-400 font-medium flex items-center gap-1">
                  应用授权密码 (Password)
                  <Lock className="w-3 h-3 text-slate-600" />
                </label>
                <input
                  id="input-dav-pass"
                  type="password"
                  required
                  className="w-full bg-slate-950/70 border border-slate-800 focus:border-indigo-500 rounded-lg p-2.5 text-slate-300 focus:outline-none font-mono"
                  placeholder="••••••••••••••••"
                  value={davPass}
                  onChange={(e) => setDavPass(e.target.value)}
                />
              </div>
            </div>

            {/* Sync Directory Path */}
            <div className="space-y-1">
              <label className="block text-slate-400 font-medium">云盘存储根路径 (Remote Base Directory)</label>
              <input
                id="input-dav-dir"
                type="text"
                required
                className="w-full bg-slate-950/70 border border-slate-800 focus:border-indigo-500 rounded-lg p-2.5 text-slate-300 font-mono focus:outline-none"
                placeholder="/TelegramSync"
                value={davDir}
                onChange={(e) => setDavDir(e.target.value)}
              />
            </div>

            {/* Throttles speed limit setup */}
            <div className="border-t border-slate-800 my-4 pt-3">
              <span className="text-[11px] font-semibold text-slate-400 block mb-3 uppercase tracking-wider font-mono">
                并发传输限速管理
              </span>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="block text-slate-400">下载速率上限 (0 代表不限速)</label>
                  <div className="relative">
                    <input
                      id="input-dl-rate"
                      type="number"
                      min="0"
                      className="w-full bg-slate-950/70 border border-slate-800 focus:border-indigo-500 rounded-lg p-2 pr-12 text-slate-300 font-mono focus:outline-none"
                      value={dlRate}
                      onChange={(e) => setDlRate(Number(e.target.value) || 0)}
                    />
                    <span className="absolute inset-y-0 right-0 pr-3 flex items-center text-slate-500 pointer-events-none font-mono">
                      KB/s
                    </span>
                  </div>
                </div>
                <div className="space-y-1">
                  <label className="block text-slate-400">同步上传速率 (0 代表最大带宽)</label>
                  <div className="relative">
                    <input
                      id="input-ul-rate"
                      type="number"
                      min="0"
                      className="w-full bg-slate-950/70 border border-slate-800 focus:border-indigo-500 rounded-lg p-2 pr-12 text-slate-300 font-mono focus:outline-none"
                      value={ulRate}
                      onChange={(e) => setUlRate(Number(e.target.value) || 0)}
                    />
                    <span className="absolute inset-y-0 right-0 pr-3 flex items-center text-slate-500 pointer-events-none font-mono">
                      KB/s
                    </span>
                  </div>
                </div>
              </div>
            </div>

            {/* Test button and results info panel */}
            <div className="pt-2">
              <button
                id="btn-test-dav-conn"
                type="button"
                onClick={handleTestConnection}
                disabled={testState === 'checking'}
                className="w-full py-2 bg-slate-800 hover:bg-slate-755 border border-slate-700/80 hover:border-slate-650 text-slate-200 hover:text-white rounded-lg text-xs font-semibold flex items-center justify-center gap-1.5 transition-colors cursor-pointer"
              >
                {testState === 'checking' ? (
                  <RefreshCw className="w-3.5 h-3.5 animate-spin text-indigo-400" />
                ) : (
                  <FolderSync className="w-3.5 h-3.5 text-indigo-400" />
                )}
                验证并测试 WebDAV 联通性
              </button>

              {testState !== 'idle' && (
                <div id="dav-test-feedback-box" className={`mt-3 p-3 rounded-lg border text-[11px] ${
                  testState === 'checking' 
                    ? 'bg-slate-950/40 text-slate-400 border-slate-800' 
                    : testState === 'success'
                    ? 'bg-emerald-500/5 text-emerald-400 border-emerald-500/10'
                    : 'bg-rose-500/5 text-rose-400 border-rose-500/10'
                }`}>
                  <p className="font-medium font-mono leading-relaxed">{testMsg}</p>
                </div>
              )}
            </div>

          </div>
        </div>

        {/* Column 2: Telegram Downloader rule config */}
        <div className="space-y-4 bg-slate-900 border border-slate-800 p-5 rounded-xl">
          <div className="flex items-center gap-2 border-b border-slate-800 pb-3">
            <Sliders className="w-4 h-4 text-indigo-400" />
            <div>
              <h3 className="text-xs font-semibold text-slate-200">媒体下载过滤器与目录结构 (Sync Filters)</h3>
              <p className="text-[10px] text-slate-550">定制您想要拉取的媒体文件库类型、单文件大小限额和存储归类等</p>
            </div>
          </div>

          <div className="space-y-4 text-xs">
            {/* Enable/Disable global daemon process */}
            <div className="flex items-center justify-between bg-slate-950/40 p-3 rounded-lg border border-slate-800">
              <div className="space-y-0.5 pr-2">
                <span className="text-xs font-semibold text-slate-300 block">新消息发布时自动运行 (Real-time daemon)</span>
                <span className="text-[10px] text-slate-500">当关联的频道出现新媒体资源时，是否秒级启动 WebDAV 同步服务</span>
              </div>
              <label className="relative inline-flex items-center cursor-pointer select-none">
                <input
                  id="checkbox-auto-sync"
                  type="checkbox"
                  checked={autoSync}
                  className="sr-only peer"
                  onChange={(e) => setAutoSync(e.target.checked)}
                />
                <div className="w-9 h-5 bg-slate-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-slate-300 after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-indigo-600" />
              </label>
            </div>

            {/* Checkboxes: Media types selection */}
            <div className="space-y-2">
              <label className="block text-slate-400 font-medium">同步的特定媒体品类 (Media Categories)</label>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                {[
                  { key: 'photo', label: '图片 (Photos)' },
                  { key: 'video', label: '视频 (Videos)' },
                  { key: 'document', label: '文档 (Documents)' },
                  { key: 'audio', label: '音乐/音频 (Music)' },
                  { key: 'voice', label: '语音消息 (Voice Notes)' }
                ].map(item => {
                  const active = mediaTypes.includes(item.key as MediaType);
                  return (
                    <button
                      id={`btn-toggle-media-${item.key}`}
                      key={item.key}
                      type="button"
                      onClick={() => toggleMediaType(item.key as MediaType)}
                      className={`py-2 px-3 rounded-lg border text-left transition-all font-medium flex items-center justify-between cursor-pointer ${
                        active 
                          ? 'bg-indigo-950/50 border-indigo-500/50 text-indigo-400' 
                          : 'bg-slate-950/40 border-slate-800 text-slate-500 hover:border-slate-700 hover:text-slate-400'
                      }`}
                    >
                      <span>{item.label}</span>
                      <input
                        id={`chk-${item.key}`}
                        type="checkbox"
                        checked={active}
                        readOnly
                        className="rounded border-slate-700 bg-slate-900 text-indigo-600 focus:ring-0 w-3 h-3 pointer-events-none"
                      />
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Structure Presets option key */}
            <div className="space-y-1.5">
              <label className="block text-slate-400 font-medium">
                层级分类目录布局模板 (Path Pattern Structure)
              </label>
              <div className="space-y-2">
                {[
                  { 
                    key: 'channel_media', 
                    title: '按来源与媒体归类 (推荐)', 
                    example: `${davDir}/[数据源_频道名称]/[媒体类型]/[文件名.格式]` 
                  },
                  { 
                    key: 'channel_date', 
                    title: '按来源与年月归档', 
                    example: `${davDir}/[数据源_频道名称]/[年-月]/[文件名.格式]` 
                  },
                  { 
                    key: 'date_channel', 
                    title: '按年月份合并展示', 
                    example: `${davDir}/[年-月]/[数据源_频道名称]/[文件名.格式]` 
                  }
                ].map(pattern => (
                  <label 
                    id={`label-pattern-${pattern.key}`}
                    key={pattern.key}
                    onClick={() => setSavePathPattern(pattern.key as any)}
                    className={`flex items-start gap-3 p-2.5 rounded-lg border cursor-pointer transition-all ${
                      savePathPattern === pattern.key
                        ? 'bg-slate-850 border-indigo-500/80 text-slate-100'
                        : 'bg-slate-950/60 border-slate-800/80 text-slate-400 hover:bg-slate-850/60'
                    }`}
                  >
                    <input
                      id={`radio-${pattern.key}`}
                      type="radio"
                      name="save-pattern-choice"
                      checked={savePathPattern === pattern.key}
                      readOnly
                      className="mt-0.5 text-indigo-600 border-slate-705"
                    />
                    <div className="space-y-0.5">
                      <span className="text-[11px] font-semibold text-slate-250 block">{pattern.title}</span>
                      <span className="text-[10px] text-slate-500 font-mono block break-all">{pattern.example}</span>
                    </div>
                  </label>
                ))}
              </div>
            </div>

            {/* Size limitations grids */}
            <div className="grid grid-cols-2 gap-3 pt-1">
              <div className="space-y-1">
                <label className="block text-slate-400">跳过过小文件 (MB)</label>
                <input
                  id="input-min-size-limit"
                  type="number"
                  step="0.01"
                  min="0"
                  className="w-full bg-slate-950/70 border border-slate-800 focus:border-indigo-500 rounded-lg p-2 text-slate-300 font-mono focus:outline-none"
                  value={minSize}
                  onChange={(e) => setMinSize(Number(e.target.value) || 0)}
                />
              </div>
              <div className="space-y-1">
                <label className="block text-slate-400">单文件最大限制 (MB)</label>
                <input
                  id="input-max-size-limit"
                  type="number"
                  min="1"
                  className="w-full bg-slate-950/70 border border-slate-800 focus:border-indigo-500 rounded-lg p-2 text-slate-300 font-mono focus:outline-none"
                  value={maxSize}
                  onChange={(e) => setMaxSize(Number(e.target.value) || 2000)}
                />
              </div>
            </div>

            {/* Date filter point option */}
            <div className="space-y-1 pt-1">
              <label className="block text-slate-400 font-medium select-none">
                只拉取此日期之后发布的文件 (UTC 时间设定)
              </label>
              <input
                id="input-date-threshold"
                type="date"
                className="w-full bg-slate-950/70 border border-slate-800 focus:border-indigo-500 rounded-lg p-2.5 text-slate-300 font-mono focus:outline-none text-xs"
                value={dateThreshold}
                onChange={(e) => setDateThreshold(e.target.value)}
              />
              <span className="text-[10px] text-slate-500 block">
                该日期之前的任何历史对话/频道老消息媒体都会在同步扫描中直接跳过
              </span>
            </div>

          </div>
        </div>

      </div>

      {/* Button save overall changes */}
      <div className="bg-slate-950/80 p-4 rounded-xl border border-slate-800/85 flex flex-col sm:flex-row items-center justify-between gap-4">
        <div className="flex items-start gap-2.5">
          <HelpCircle className="w-4 h-4 text-slate-500 shrink-0 mt-0.5" />
          <p className="text-[10px] text-slate-400 leading-relaxed max-w-xl">
            提示：这些同步细则是即时热交换执行的。每次触发 Telegram 客户端下载事件或流式同步扫描时，程序内部规则链条都会根据上述策略自动做出过滤选择。
          </p>
        </div>
        <button
          id="btn-save-overall-config"
          type="submit"
          className="flex items-center gap-1.5 px-6 py-2.5 hover:scale-[1.01] bg-indigo-600 hover:bg-indigo-500 font-bold rounded-lg text-xs text-white shadow-lg shadow-indigo-950/30 transition-all cursor-pointer select-none"
        >
          <Save className="w-4 h-4" />
          应用并保存全局配置
        </button>
      </div>

    </form>
  );
}
