/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { kindLabels, ResourceKind } from "../library/model";
import {
  BotStatusConfig,
  BotStartupNotificationMode,
  CloudStorageConfig,
  SyncRule,
  MediaType,
  VideoClassifierConfig,
  defaultVideoClassifier,
} from "../types";
import {
  Bell,
  Check,
  Cloud,
  RefreshCw,
  Save,
  Settings2,
  Sparkles,
} from "lucide-react";

interface ConfigPanelProps {
  config: CloudStorageConfig;
  rule: SyncRule;
  statusConfig: BotStatusConfig;
  vcConfig: VideoClassifierConfig;
  onSaveConfig: (config: CloudStorageConfig) => void;
  onSaveRule: (rule: SyncRule) => void;
  onSaveAll?: (
    config: CloudStorageConfig,
    rule: SyncRule,
    statusConfig: BotStatusConfig,
    vcConfig?: VideoClassifierConfig,
  ) => Promise<void> | void;
}

export function ConfigPanel({
  config,
  rule,
  statusConfig,
  vcConfig,
  onSaveConfig,
  onSaveRule,
  onSaveAll,
}: ConfigPanelProps) {
  // Local states for cloud storage config
  const [davUrl, setDavUrl] = useState(config.url);
  const [davUser, setDavUser] = useState(config.username);
  const [davPass, setDavPass] = useState(config.password || "");
  const [davDir, setDavDir] = useState(config.remoteDir);

  // Local states for Sync rules
  const [mediaTypes, setMediaTypes] = useState<MediaType[]>(rule.mediaTypes);
  const [savePathPattern, setSavePathPattern] = useState(rule.savePathPattern);
  const [generateVideoCovers, setGenerateVideoCovers] = useState(
    rule.generateVideoCovers,
  );
  const [startupNotificationMode, setStartupNotificationMode] =
    useState<BotStartupNotificationMode>(statusConfig.startupNotificationMode);
  const [statusChatId, setStatusChatId] = useState(statusConfig.statusChatId);

  // Local states for video AI classifier
  const [vcEnable, setVcEnable] = useState(vcConfig.enable);
  const [vcApiBase, setVcApiBase] = useState(vcConfig.apiBase);
  const [vcApiKey, setVcApiKey] = useState(vcConfig.apiKey);
  const [vcModel, setVcModel] = useState(vcConfig.model);
  const [vcMaxFrames, setVcMaxFrames] = useState(vcConfig.maxFrames);
  const [vcNarrativeFrames, setVcNarrativeFrames] = useState(vcConfig.narrativeFrames);
  const [vcTimeout, setVcTimeout] = useState(vcConfig.timeoutSec);
  const [vcMaxSize, setVcMaxSize] = useState(vcConfig.maxVideoSizeMb);
  const [vcMinConfidence, setVcMinConfidence] = useState(
    vcConfig.minConfidence,
  );
  const [vcTypes, setVcTypes] = useState<ResourceKind[]>(
    vcConfig.resourceTypes,
  );
  const [vcTranscribe, setVcTranscribe] = useState(vcConfig.transcribe);
  const [vcShareSourceText, setVcShareSourceText] = useState(vcConfig.shareSourceText === true);
  const [vcUseSubtitles, setVcUseSubtitles] = useState(vcConfig.useSubtitles === true);
  const [vcAudioSeconds, setVcAudioSeconds] = useState(
    vcConfig.maxAudioSeconds,
  );
  const [vcOrganize, setVcOrganize] = useState(vcConfig.organizeFolders);

  // Connection testing state
  const [testState, setTestState] = useState<
    "idle" | "checking" | "success" | "failed"
  >("idle");
  const [testMsg, setTestMsg] = useState("");

  // Notification feedbacks
  const [saveFeedback, setSaveFeedback] = useState(false);
  const [section, setSection] = useState("storage");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    if (!dirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [dirty]);

  useEffect(() => {
    setDavUrl(config.url || "");
    setDavUser(config.username || "");
    setDavPass(config.password || "");
    setDavDir(config.remoteDir || "");
  }, [config]);

  useEffect(() => {
    setMediaTypes(rule.mediaTypes || []);
    setSavePathPattern(rule.savePathPattern);
    setGenerateVideoCovers(rule.generateVideoCovers);
  }, [rule]);

  useEffect(() => {
    setStartupNotificationMode(statusConfig.startupNotificationMode || "off");
    setStatusChatId(statusConfig.statusChatId || "");
  }, [statusConfig]);

  useEffect(() => {
    setVcEnable(vcConfig.enable === true);
    setVcApiBase(vcConfig.apiBase || defaultVideoClassifier.apiBase);
    setVcApiKey(vcConfig.apiKey || "");
    setVcModel(vcConfig.model || defaultVideoClassifier.model);
    setVcMaxFrames(vcConfig.maxFrames || defaultVideoClassifier.maxFrames);
    setVcNarrativeFrames(vcConfig.narrativeFrames || defaultVideoClassifier.narrativeFrames);
    setVcTimeout(vcConfig.timeoutSec || defaultVideoClassifier.timeoutSec);
    setVcMaxSize(
      vcConfig.maxVideoSizeMb || defaultVideoClassifier.maxVideoSizeMb,
    );
    setVcMinConfidence(
      vcConfig.minConfidence ?? defaultVideoClassifier.minConfidence,
    );
    setVcTypes(vcConfig.resourceTypes ?? defaultVideoClassifier.resourceTypes);
    setVcTranscribe(vcConfig.transcribe);
    setVcShareSourceText(vcConfig.shareSourceText === true);
    setVcUseSubtitles(vcConfig.useSubtitles === true);
    setVcAudioSeconds(vcConfig.maxAudioSeconds ?? 180);
    setVcOrganize(vcConfig.organizeFolders !== false);
  }, [vcConfig]);

  const handleTestConnection = async (e: React.MouseEvent) => {
    e.preventDefault();
    if (!davUrl || !davUser) {
      setTestState("failed");
      setTestMsg("错误: 必须填写 WebDAV 服务器地址和账号名！");
      return;
    }

    setTestState("checking");
    setTestMsg("正在连接 WebDAV 服务器...");

    try {
      const response = await fetch("/test_webdav", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: davUrl,
          username: davUser,
          password: davPass,
        }),
      });
      const data = await response.json();
      setTestState(response.ok && data.success ? "success" : "failed");
      setTestMsg(
        data.message || (data.success ? "连接测试成功。" : "连接测试失败。"),
      );
    } catch (error) {
      setTestState("failed");
      setTestMsg(error instanceof Error ? error.message : String(error));
    }
  };

  const toggleMediaType = (type: MediaType) => {
    if (mediaTypes.includes(type)) {
      setMediaTypes(mediaTypes.filter((t) => t !== type));
    } else {
      setMediaTypes([...mediaTypes, type]);
    }
  };

  const handleSaveAll = async (e: React.FormEvent) => {
    e.preventDefault();
    const invalid = e.currentTarget.querySelector<HTMLInputElement>(
      "input:invalid, select:invalid, textarea:invalid",
    );
    if (invalid) {
      const target =
        invalid.closest<HTMLElement>("[data-section]")?.dataset.section;
      if (target) setSection(target);
      const advanced = invalid.closest("details");
      if (advanced) advanced.open = true;
      requestAnimationFrame(() => {
        invalid.focus();
        invalid.reportValidity();
      });
      return;
    }

    const nextConfig = {
      ...config,
      type: "webdav",
      url: davUrl,
      username: davUser,
      password: davPass,
      remoteDir: davDir,
    } as CloudStorageConfig;

    const nextRule = {
      ...rule,
      mediaTypes,
      savePathPattern,
      generateVideoCovers,
    } as SyncRule;

    const nextStatusConfig = {
      startupNotificationMode,
      statusChatId: statusChatId.trim(),
    } as BotStatusConfig;

    const nextVcConfig = {
      enable: vcEnable,
      apiBase: vcApiBase.trim(),
      apiKey: vcApiKey.trim(),
      model: vcModel.trim(),
      maxFrames: Number(vcMaxFrames) || defaultVideoClassifier.maxFrames,
      narrativeFrames: Number(vcNarrativeFrames) || defaultVideoClassifier.narrativeFrames,
      timeoutSec: Number(vcTimeout) || defaultVideoClassifier.timeoutSec,
      maxVideoSizeMb:
        Number(vcMaxSize) || defaultVideoClassifier.maxVideoSizeMb,
      minConfidence: Number(vcMinConfidence),
      resourceTypes: vcTypes,
      transcribe: vcTranscribe,
      shareSourceText: vcShareSourceText,
      useSubtitles: vcUseSubtitles,
      maxAudioSeconds: Number(vcAudioSeconds) || 180,
      organizeFolders: vcOrganize,
    } as VideoClassifierConfig;

    if (vcEnable && vcTypes.length === 0) {
      setSaveError("请至少选择一种需要分析的资源类型。");
      setSection("analysis");
      return;
    }
    setSaving(true);
    setSaveError("");
    setSaveFeedback(false);
    try {
      if (onSaveAll)
        await onSaveAll(nextConfig, nextRule, nextStatusConfig, nextVcConfig);
      else {
        onSaveConfig(nextConfig);
        onSaveRule(nextRule);
      }
      setSaveFeedback(true);
      setDirty(false);
    } catch (reason) {
      setSaveError(
        reason instanceof Error ? reason.message : "保存失败，请重试。",
      );
    } finally {
      setSaving(false);
    }
  };

  const sections = [
    {
      id: "storage",
      label: "云盘连接",
      icon: Cloud,
      description: "配置归档文件的位置与云盘连接。",
    },
    {
      id: "rules",
      label: "下载规则",
      icon: Settings2,
      description: "选择要同步的媒体，以及归档方式。",
    },
    {
      id: "analysis",
      label: "资源分析",
      icon: Sparkles,
      description: "先识别类型，再按类别生成内容分析。",
    },
    {
      id: "notifications",
      label: "通知",
      icon: Bell,
      description: "决定启动消息发送到哪里。",
    },
  ];
  const active = sections.find((item) => item.id === section)!;
  return (
    <form
      id="form-config-settings"
      noValidate
      data-unsaved={dirty}
      onSubmit={handleSaveAll}
      onChangeCapture={() => {
        setDirty(true);
        setSaveFeedback(false);
      }}
      className="settings-workspace"
    >
      <nav className="settings-navigation" aria-label="设置分区">
        <div className="library-nav-heading">工作台设置</div>
        {sections.map(({ id, label, icon: Icon }) => (
          <button
            type="button"
            key={id}
            className={`category-link ${section === id ? "active" : ""}`}
            aria-current={section === id ? "true" : undefined}
            onClick={() => setSection(id)}
          >
            <Icon size={19} />
            {label}
          </button>
        ))}
      </nav>
      <div className="settings-main">
        <header className="workspace-page-header">
          <div className="page-heading">
            <div>
              <div className="eyebrow">设置</div>
              <h1>{active.label}</h1>
              <p>{active.description}</p>
            </div>
            <span
              className={`save-state ${dirty ? "unsaved" : ""}`}
              role="status"
            >
              {saving
                ? "正在保存…"
                : dirty
                  ? "有未保存的更改"
                  : saveFeedback
                    ? "已保存"
                    : "当前配置"}
            </span>
          </div>
        </header>
        <div className="settings-scroll">
          <fieldset disabled={saving} className="settings-fields">
            <section
              hidden={section !== "storage"}
              data-section="storage"
              className="settings-section"
              aria-label="云盘连接设置"
            >
              <div className="section-title">
                <h2>WebDAV 连接</h2>
                <p>用于归档文件和浏览真实目录。</p>
              </div>
              <div className="field-grid">
                <Field
                  label="服务器地址"
                  wide
                  hint="填写完整的 WebDAV 服务地址。"
                >
                  <input
                    type="url"
                    value={davUrl}
                    onChange={(event) => setDavUrl(event.target.value)}
                    placeholder="https://cloud.example.com/dav"
                  />
                </Field>
                <Field label="账号">
                  <input
                    autoComplete="username"
                    value={davUser}
                    onChange={(event) => setDavUser(event.target.value)}
                  />
                </Field>
                <Field label="密码">
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={davPass}
                    onChange={(event) => setDavPass(event.target.value)}
                  />
                </Field>
                <Field label="远端根目录" wide hint="资源将归档在这个目录下。">
                  <input
                    value={davDir}
                    onChange={(event) => setDavDir(event.target.value)}
                    placeholder="telegram"
                  />
                </Field>
              </div>
              <div className="connection-test">
                <button
                  type="button"
                  id="btn-test-dav-conn"
                  className="button"
                  disabled={testState === "checking"}
                  onClick={handleTestConnection}
                >
                  <RefreshCw
                    size={17}
                    className={testState === "checking" ? "spin" : ""}
                  />
                  {testState === "checking" ? "测试中…" : "测试连接"}
                </button>
                {testState !== "idle" && (
                  <p
                    role={testState === "failed" ? "alert" : "status"}
                    className={testState === "failed" ? "danger-text" : "muted"}
                  >
                    {testMsg}
                  </p>
                )}
              </div>
            </section>
            <section
              hidden={section !== "rules"}
              data-section="rules"
              className="settings-section"
              aria-label="下载规则设置"
            >
              <div className="section-title">
                <h2>同步哪些媒体</h2>
                <p>选择 Telegram 下载器接收的媒体类型。</p>
              </div>
              <div className="choice-grid">
                {[
                  ["photo", "图片"],
                  ["video", "视频"],
                  ["document", "文档"],
                  ["audio", "音乐 / 音频"],
                  ["voice", "语音消息"],
                ].map(([key, label]) => (
                  <label
                    key={key}
                    className={`choice-card ${mediaTypes.includes(key as MediaType) ? "chosen" : ""}`}
                  >
                    <input
                      type="checkbox"
                      checked={mediaTypes.includes(key as MediaType)}
                      onChange={() => toggleMediaType(key as MediaType)}
                    />
                    {label}
                  </label>
                ))}
              </div>
              <label className="toggle-row">
                <span>
                  <strong>下载后自动保存视频封面</strong>
                  <small>
                    截取视频约 10%
                    处的画面，以“视频名.mp4.cover.jpg”保存在视频旁并一起上传。不依赖
                    AI 分析，已有封面不会覆盖。
                  </small>
                </span>
                <input
                  type="checkbox"
                  aria-label="下载后自动保存视频封面"
                  checked={generateVideoCovers}
                  onChange={(event) =>
                    setGenerateVideoCovers(event.target.checked)
                  }
                />
              </label>
              <div className="section-title">
                <h2>归档目录</h2>
                <p>定义来源、日期和媒体类型的层级。</p>
              </div>
              <div className="pattern-options">
                {[
                  [
                    "channel_media",
                    "按来源与媒体类型",
                    "[来源] / [媒体类型] / 文件",
                  ],
                  ["channel_date", "按来源与月份", "[来源] / [年-月] / 文件"],
                  ["date_channel", "按月份与来源", "[年-月] / [来源] / 文件"],
                ].map(([key, label, example]) => (
                  <label
                    key={key}
                    className={`choice-card ${savePathPattern === key ? "chosen" : ""}`}
                  >
                    <input
                      type="radio"
                      name="save-pattern"
                      checked={savePathPattern === key}
                      onChange={() =>
                        setSavePathPattern(key as SyncRule["savePathPattern"])
                      }
                    />
                    <span>
                      <strong>{label}</strong>
                      <small>
                        {davDir || "根目录"} / {example}
                      </small>
                    </span>
                  </label>
                ))}
              </div>
            </section>
            <section
              hidden={section !== "analysis"}
              data-section="analysis"
              className="settings-section"
              aria-label="资源分析设置"
            >
              <label className="toggle-row">
                <span>
                  <strong>启用资源智能分析</strong>
                  <small>
                    支持视频、图片、音频、文档、压缩包和软件。包含 NSFW
                    独立分类与细分。
                  </small>
                </span>
                <input
                  type="checkbox"
                  checked={vcEnable}
                  onChange={(event) => setVcEnable(event.target.checked)}
                />
              </label>
              <div className="analysis-workflow">
                <span>
                  <b>1</b>提取内容证据
                </span>
                <span>
                  <b>2</b>识别资源类别
                </span>
                <span>
                  <b>3</b>按类别生成简介
                </span>
              </div>
              <div className="field-grid">
                <Field label="模型服务地址" wide>
                  <input
                    type="url"
                    value={vcApiBase}
                    onChange={(event) => setVcApiBase(event.target.value)}
                  />
                </Field>
                <Field label="API Key">
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={vcApiKey}
                    onChange={(event) => setVcApiKey(event.target.value)}
                  />
                </Field>
                <Field label="模型名称">
                  <input
                    value={vcModel}
                    onChange={(event) => setVcModel(event.target.value)}
                  />
                </Field>
              </div>
              <div className="section-title">
                <h2>分析资源范围</h2>
                <p>未选中的类型仍可同步，但不会自动生成内容分析。</p>
              </div>
              <div className="choice-grid">
                {(
                  [
                    "video",
                    "image",
                    "audio",
                    "document",
                    "archive",
                    "software",
                  ] as ResourceKind[]
                ).map((kind) => (
                  <label
                    key={kind}
                    className={`choice-card ${vcTypes.includes(kind) ? "chosen" : ""}`}
                  >
                    <input
                      type="checkbox"
                      checked={vcTypes.includes(kind)}
                      onChange={() =>
                        setVcTypes((current) =>
                          current.includes(kind)
                            ? current.filter((item) => item !== kind)
                            : [...current, kind],
                        )
                      }
                    />
                    {kindLabels[kind]}
                  </label>
                ))}
              </div>
              <div className="field-grid">
                <Field
                  label="分类置信度阈值"
                  hint="低于此值的结果进入待确认，不强行归类。"
                >
                  <input
                    type="number"
                    min="0"
                    max="1"
                    step="0.05"
                    value={vcMinConfidence}
                    onChange={(event) =>
                      setVcMinConfidence(Number(event.target.value))
                    }
                  />
                </Field>
                <Field label="单文件分析上限（MB）">
                  <input
                    type="number"
                    min="1"
                    value={vcMaxSize}
                    onChange={(event) =>
                      setVcMaxSize(Number(event.target.value))
                    }
                  />
                </Field>
              </div>
              <label className="toggle-row compact">
                <span>
                  <strong>按分析分类组织新上传文件</strong>
                  <small>重新分析已归档文件时不会移动其云端位置。</small>
                </span>
                <input
                  type="checkbox"
                  checked={vcOrganize}
                  onChange={(event) => setVcOrganize(event.target.checked)}
                />
              </label>
              <details className="advanced-settings">
                <summary>采样与处理参数</summary>
                <div className="field-grid">
                  <Field
                    label="分类采样帧数"
                    hint="分类识别与核验使用的概览帧数；剧情整理另外按下方预算读取时间段画面。"
                  >
                    <input
                      type="number"
                      min="1"
                      max="16"
                      value={vcMaxFrames}
                      onChange={(event) =>
                        setVcMaxFrames(Number(event.target.value))
                      }
                    />
                  </Field>
                  <Field label="剧情采样帧数" hint="默认24帧，最多8个时间窗，每窗包含局部前后画面；语音在原总秒数预算内对齐采样。更多画面会增加模型费用，仍不等于完整观看。">
                    <input type="number" min="6" max="24" value={vcNarrativeFrames}
                      onChange={(event) => setVcNarrativeFrames(Number(event.target.value))} />
                  </Field>
                  <Field
                    label="请求超时（秒）"
                    hint="按分类、简介分别计时；临时错误最多重试一次，重试等待上限翻倍（最多 300 秒），可能产生额外模型调用费用。"
                  >
                    <input
                      type="number"
                      min="1"
                      value={vcTimeout}
                      onChange={(event) =>
                        setVcTimeout(Number(event.target.value))
                      }
                    />
                  </Field>
                </div>
                <label className="toggle-row compact">
                  <span>
                    <strong>启用本地语音转写</strong>
                    <small>
                      需要已安装的本地转写依赖，语音证据用于音频和视频分析。
                    </small>
                  </span>
                  <input
                    type="checkbox"
                    checked={vcTranscribe}
                    onChange={(event) => setVcTranscribe(event.target.checked)}
                  />
                </label>
                <label className="toggle-row compact">
                  <span><strong>允许将 Telegram 文案用于分析</strong>
                    <small>默认关闭。开启并保存后，后续分析会将已保存的消息文案发送到当前配置的模型服务作为待核实线索；不附带聊天 ID 或频道名称字段。原文仍独立保存。</small></span>
                  <input type="checkbox" checked={vcShareSourceText} onChange={(event) => setVcShareSourceText(event.target.checked)} />
                </label>
                <label className="toggle-row compact">
                  <span><strong>读取本地字幕用于分析</strong>
                    <small>开启后优先读取内嵌或同名 SRT/VTT 字幕，并将选取的字幕文本发送到当前模型服务。无可用字幕时按转写设置处理；字幕可能错配，不能直接视为事实。</small></span>
                  <input type="checkbox" checked={vcUseSubtitles} onChange={(event) => setVcUseSubtitles(event.target.checked)} />
                </label>
                <Field label="最大转写时长（秒）">
                  <input
                    type="number"
                    min="1"
                    value={vcAudioSeconds}
                    onChange={(event) =>
                      setVcAudioSeconds(Number(event.target.value))
                    }
                  />
                </Field>
              </details>
            </section>
            <section
              hidden={section !== "notifications"}
              data-section="notifications"
              className="settings-section"
              aria-label="通知设置"
            >
              <div className="section-title">
                <h2>启动通知</h2>
                <p>控制 Bot 启动时的状态消息，不影响资源同步。</p>
              </div>
              <div className="pattern-options">
                {[
                  ["off", "不发送", "保持安静，不发送启动状态。"],
                  ["admin", "发送给管理员", "使用已配置的 Bot 管理员。"],
                  [
                    "status_chat",
                    "发送到指定会话",
                    "将运行状态集中发送到指定聊天。",
                  ],
                ].map(([key, label, description]) => (
                  <label
                    key={key}
                    className={`choice-card ${startupNotificationMode === key ? "chosen" : ""}`}
                  >
                    <input
                      type="radio"
                      name="startup-notification"
                      checked={startupNotificationMode === key}
                      onChange={() =>
                        setStartupNotificationMode(
                          key as BotStartupNotificationMode,
                        )
                      }
                    />
                    <span>
                      <strong>{label}</strong>
                      <small>{description}</small>
                    </span>
                  </label>
                ))}
              </div>
              {startupNotificationMode === "status_chat" && (
                <Field
                  label="状态会话 ID"
                  hint="填写 Bot 有权限发送消息的聊天 ID。"
                >
                  <input
                    value={statusChatId}
                    onChange={(event) => setStatusChatId(event.target.value)}
                    placeholder="-100…"
                  />
                </Field>
              )}
            </section>
          </fieldset>
        </div>
        <footer className="settings-savebar">
          <div aria-live="polite">
            {saveError ? (
              <p role="alert" className="danger-text">
                {saveError}
              </p>
            ) : saveFeedback ? (
              <div>
                <span className="success-text">
                  <Check size={17} />
                  设置已成功保存
                </span>
                <p className="muted">连接相关设置需重新连接后生效。</p>
              </div>
            ) : (
              <p className="muted">
                切换分区不会丢失修改，保存将应用所有分区。
              </p>
            )}
          </div>
          <button
            id="btn-save-config"
            className="button primary"
            type="submit"
            disabled={saving}
          >
            {saving ? (
              <RefreshCw size={17} className="spin" />
            ) : (
              <Save size={17} />
            )}
            {saving ? "正在保存…" : "保存设置"}
          </button>
        </footer>
      </div>
    </form>
  );
}

function Field({
  label,
  children,
  hint,
  wide = false,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
  wide?: boolean;
}) {
  return (
    <label className={`form-field ${wide ? "field-wide" : ""}`}>
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
