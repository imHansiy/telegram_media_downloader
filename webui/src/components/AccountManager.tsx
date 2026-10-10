/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import {
  ArrowRight,
  Bot,
  Check,
  Loader2,
  LockKeyhole,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  ShieldCheck,
  Smartphone,
  Square,
  Trash2,
  Users,
} from "lucide-react";
import { Modal } from "./ui/Modal";
import { BotAccessConfig, BotAccessMode, TelegramAccount } from "../types";

interface AccountManagerProps {
  accounts: TelegramAccount[];
  botAccess: BotAccessConfig;
  sessionExists: boolean;
  onCreateProfile: (name: string) => Promise<void> | void;
  onRenameProfile: (id: string, name: string) => Promise<void> | void;
  onDeleteProfile: (id: string) => Promise<void> | void;
  onDisconnectAccount: (id: string) => Promise<void> | void;
  onStartAccount: (id: string) => Promise<void> | void;
  onStopAccount: (id: string) => Promise<void> | void;
  onConnectSavedSession: (id?: string) => Promise<void>;
  onSendCode: (
    phoneNumber: string,
    apiId?: string,
    apiHash?: string,
    profileId?: string,
    createProfile?: boolean,
    profileName?: string,
  ) => Promise<void>;
  onVerifyCode: (code: string) => Promise<{ needsPassword: boolean }>;
  onVerifyPassword: (password: string) => Promise<void>;
  onRefresh: () => Promise<void>;
  onSaveBotAccess: (config: BotAccessConfig) => Promise<void> | void;
}

const defaultBotAccess: BotAccessConfig = {
  mode: "self",
  allowedUsers: [],
};

export function AccountManager({
  accounts,
  botAccess,
  sessionExists,
  onCreateProfile,
  onRenameProfile,
  onDeleteProfile,
  onDisconnectAccount,
  onStartAccount,
  onStopAccount,
  onConnectSavedSession,
  onSendCode,
  onVerifyCode,
  onVerifyPassword,
  onRefresh,
  onSaveBotAccess,
}: AccountManagerProps) {
  const [step, setStep] = useState<"list" | "phone" | "code" | "password">(
    "list",
  );
  const [phoneNumber, setPhoneNumber] = useState("");
  const [apiId, setApiId] = useState("");
  const [apiHash, setApiHash] = useState("");
  const [useConfiguredApi, setUseConfiguredApi] = useState(true);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [botSettingsOpen, setBotSettingsOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [confirmAccount, setConfirmAccount] = useState<{
    account: TelegramAccount;
    action: "delete" | "logout";
  } | null>(null);
  const [botAccessMode, setBotAccessMode] = useState<BotAccessMode>(
    defaultBotAccess.mode,
  );
  const [allowedUsersText, setAllowedUsersText] = useState(
    defaultBotAccess.allowedUsers.join("\n"),
  );
  const [accessFeedback, setAccessFeedback] = useState("");
  const [newProfileName, setNewProfileName] = useState("");
  const [renamingProfileId, setRenamingProfileId] = useState<string | null>(
    null,
  );
  const [renameValue, setRenameValue] = useState("");
  const [loginTargetProfileId, setLoginTargetProfileId] = useState<
    string | null
  >(null);

  const accessModes: Array<{
    mode: BotAccessMode;
    label: string;
    description: string;
  }> = [
    {
      mode: "self",
      label: "仅账号本人",
      description: "已登录账号本人可投递资源。",
    },
    {
      mode: "allowed",
      label: "指定用户",
      description: "仅允许列表内用户私聊投递。",
    },
    {
      mode: "public",
      label: "全部用户",
      description: "任何人都可私聊投递，会增加下载和存储使用量。",
    },
  ];

  const selectedAccessLabel = () =>
    accessModes.find((item) => item.mode === botAccess.mode)?.label ||
    "仅账号本人";
  const loginTargetAccount = accounts.find(
    (acct) => acct.id === loginTargetProfileId,
  );

  const profileLabel = (acct: TelegramAccount) =>
    acct.profileName || acct.firstName || "Telegram Profile";

  const parseAllowedUsers = (value: string) =>
    value
      .split(/[\n,，;；]+/)
      .map((item) => item.trim())
      .filter(Boolean);

  useEffect(() => {
    setBotAccessMode(botAccess.mode);
    setAllowedUsersText(botAccess.allowedUsers.join("\n"));
  }, [botAccess]);

  const run = async (fn: () => Promise<void>) => {
    setLoading(true);
    setErrorMessage("");
    try {
      await fn();
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  };

  const handleSendCode = (event: React.FormEvent) => {
    event.preventDefault();
    run(async () => {
      const cleanPhoneNumber = phoneNumber.trim();
      const cleanApiId = apiId.trim();
      const cleanApiHash = apiHash.trim();
      await onSendCode(
        cleanPhoneNumber,
        useConfiguredApi ? undefined : cleanApiId,
        useConfiguredApi ? undefined : cleanApiHash,
        loginTargetProfileId || undefined,
        !loginTargetProfileId,
        loginTargetProfileId && loginTargetAccount
          ? profileLabel(loginTargetAccount)
          : cleanPhoneNumber,
      );
      setStep("code");
    });
  };

  const handleVerifyCode = (event: React.FormEvent) => {
    event.preventDefault();
    run(async () => {
      const result = await onVerifyCode(code);
      if (result.needsPassword) {
        setStep("password");
      } else {
        setStep("list");
        setCode("");
        setLoginTargetProfileId(null);
      }
    });
  };

  const handleVerifyPassword = (event: React.FormEvent) => {
    event.preventDefault();
    run(async () => {
      await onVerifyPassword(password);
      setStep("list");
      setCode("");
      setPassword("");
      setLoginTargetProfileId(null);
    });
  };

  const handleCreateProfile = () => {
    run(async () => {
      const fallbackName = `账户档案 ${accounts.length + 1}`;
      await onCreateProfile(newProfileName.trim() || fallbackName);
      setNewProfileName("");
      setCreateOpen(false);
    });
  };

  const startRename = (acct: TelegramAccount) => {
    setRenamingProfileId(acct.id);
    setRenameValue(profileLabel(acct));
  };

  const handleRename = (acct: TelegramAccount) => {
    run(async () => {
      const nextName = renameValue.trim();
      if (!nextName) return;
      await onRenameProfile(acct.id, nextName);
      setRenamingProfileId(null);
      setRenameValue("");
    });
  };

  const handleDestructiveAction = () => {
    if (!confirmAccount) return;
    run(async () => {
      if (confirmAccount.action === "delete")
        await onDeleteProfile(confirmAccount.account.id);
      else await onDisconnectAccount(confirmAccount.account.id);
      setConfirmAccount(null);
    });
  };

  const startLogin = (profileId?: string, defaultPhone?: string) => {
    setErrorMessage("");
    setLoginTargetProfileId(profileId || null);
    setPhoneNumber(defaultPhone || "");
    setCode("");
    setPassword("");
    setStep("phone");
  };

  const handleSaveBotAccess = () => {
    run(async () => {
      const nextAccess = {
        mode: botAccessMode,
        allowedUsers: parseAllowedUsers(allowedUsersText),
      } as BotAccessConfig;
      if (nextAccess.mode === "allowed" && !nextAccess.allowedUsers.length)
        throw new Error("请填写至少一个允许投递的用户。");
      await onSaveBotAccess(nextAccess);
      setAccessFeedback("Bot 投递权限已保存。");
      setTimeout(() => setAccessFeedback(""), 2500);
    });
  };

  const cancelLogin = () => {
    setStep("list");
    setLoginTargetProfileId(null);
    setCode("");
    setPassword("");
    setErrorMessage("");
  };
  const running = accounts.filter(
    (account) => account.isRunning || account.status === "connected",
  ).length;

  return (
    <div className="accounts-workspace">
      <header className="workspace-page-header">
        <div className="page-heading">
          <div>
            <div className="eyebrow">TELEGRAM 接入</div>
            <h1>
              账号管理<span className="heading-count">{accounts.length}</span>
            </h1>
            <p>{running} 个账号运行中 · 连接、管理与控制你的同步会话。</p>
          </div>
          <div className="button-row">
            <button
              className="icon-button"
              aria-label="刷新账号"
              disabled={loading}
              onClick={() => run(onRefresh)}
            >
              <RefreshCw size={19} className={loading ? "spin" : ""} />
            </button>
            <button
              className="button"
              disabled={loading}
              onClick={() => {
                setErrorMessage("");
                setCreateOpen(true);
              }}
            >
              <Plus size={17} />
              创建档案
            </button>
            <button
              className="button primary"
              disabled={loading}
              onClick={() => startLogin()}
            >
              <Smartphone size={17} />
              添加账号
            </button>
          </div>
        </div>
      </header>
      <div className="accounts-scroll">
        {errorMessage &&
          step === "list" &&
          !createOpen &&
          !confirmAccount &&
          !botSettingsOpen &&
          !renamingProfileId && (
            <p className="notice error" role="alert">
              {errorMessage}
            </p>
          )}
        <div className="account-grid">
          {accounts.map((acct) => {
            const hasSession = Boolean(acct.hasSession);
            const isRunning = Boolean(
              acct.isRunning || acct.status === "connected",
            );
            return (
              <article
                className="account-card"
                id={`acct-card-${acct.id}`}
                key={acct.id}
                aria-label={profileLabel(acct)}
              >
                <div className="account-card-top">
                  <span className="account-avatar">
                    {profileLabel(acct).slice(0, 1).toUpperCase()}
                  </span>
                  <div>
                    <h2>{profileLabel(acct)}</h2>
                    <p>
                      {acct.username
                        ? `@${acct.username.replace(/^@/, "")}`
                        : acct.userId || "尚未登录"}
                    </p>
                  </div>
                  <span
                    className={`status-pill ${isRunning ? "completed" : "pending"}`}
                  >
                    {isRunning ? "运行中" : hasSession ? "已停止" : "未登录"}
                  </span>
                </div>
                <dl className="account-facts">
                  <div>
                    <dt>手机号</dt>
                    <dd>{acct.phoneNumber || "未绑定"}</dd>
                  </div>
                  <div>
                    <dt>会话</dt>
                    <dd>
                      {hasSession ? "已保存，可直接启动" : "需要手机号验证"}
                    </dd>
                  </div>
                </dl>
                {acct.runtimeMessage && (
                  <p
                    className={`account-runtime ${acct.runtimeStatus === "error" ? "danger-text" : ""}`}
                  >
                    {acct.runtimeMessage}
                  </p>
                )}
                <div className="account-card-actions">
                  {!hasSession ? (
                    <button
                      className="button primary"
                      disabled={loading}
                      onClick={() => startLogin(acct.id, acct.phoneNumber)}
                    >
                      <ArrowRight size={17} />
                      登录账号
                    </button>
                  ) : isRunning ? (
                    <button
                      className="button"
                      disabled={loading}
                      onClick={() =>
                        run(() => Promise.resolve(onStopAccount(acct.id)))
                      }
                    >
                      <Square size={15} />
                      停止同步
                    </button>
                  ) : (
                    <button
                      className="button primary"
                      disabled={loading}
                      onClick={() =>
                        run(() => Promise.resolve(onStartAccount(acct.id)))
                      }
                    >
                      <Play size={16} />
                      启动同步
                    </button>
                  )}
                  <button
                    className="icon-button"
                    aria-label="重命名账号档案"
                    disabled={loading}
                    onClick={() => {
                      setErrorMessage("");
                      startRename(acct);
                    }}
                  >
                    <Pencil size={17} />
                  </button>
                </div>
                <details className="account-more">
                  <summary>会话详情与更多操作</summary>
                  <dl>
                    <div>
                      <dt>Session</dt>
                      <dd>{acct.sessionName || "未保存"}</dd>
                    </div>
                    <div>
                      <dt>运行状态</dt>
                      <dd>
                        {acct.runtimeStatus ||
                          (isRunning ? "running" : "stopped")}
                      </dd>
                    </div>
                  </dl>
                  <div className="button-row">
                    {hasSession && (
                      <button
                        className="text-button danger-text"
                        disabled={loading}
                        onClick={() => {
                          setErrorMessage("");
                          setConfirmAccount({
                            account: acct,
                            action: "logout",
                          });
                        }}
                      >
                        清除登录会话
                      </button>
                    )}
                    <button
                      className="text-button danger-text"
                      disabled={loading}
                      onClick={() => {
                        setErrorMessage("");
                        setConfirmAccount({ account: acct, action: "delete" });
                      }}
                    >
                      <Trash2 size={15} />
                      删除档案
                    </button>
                  </div>
                </details>
              </article>
            );
          })}
        </div>
        {accounts.length === 0 && (
          <div className="account-empty">
            <span className="empty-symbol">
              <Users size={35} />
            </span>
            <div>
              <h2>连接第一个 Telegram 账号</h2>
              <p>
                登录后即可接收媒体、跟踪传输和管理归档内容。验证码会发送至你的
                Telegram 客户端。
              </p>
              <div className="button-row">
                <button
                  className="button primary"
                  disabled={loading}
                  onClick={() => startLogin()}
                >
                  使用手机号登录
                  <ArrowRight size={17} />
                </button>
                {sessionExists && (
                  <button
                    className="button"
                    disabled={loading}
                    onClick={() => run(() => onConnectSavedSession())}
                  >
                    连接已保存 Session
                  </button>
                )}
              </div>
            </div>
          </div>
        )}
        <section className="bot-access-strip">
          <span className="resource-icon">
            <Bot size={25} />
          </span>
          <div>
            <h2>Bot 投递权限</h2>
            <p>当前：{selectedAccessLabel()}。控制谁可以向 Bot 提交资源。</p>
          </div>
          <button
            className="button"
            disabled={loading}
            onClick={() => {
              setErrorMessage("");
              setBotAccessMode(botAccess.mode);
              setAllowedUsersText(botAccess.allowedUsers.join("\n"));
              setAccessFeedback("");
              setBotSettingsOpen(true);
            }}
          >
            管理权限
          </button>
        </section>
      </div>
      {step !== "list" && (
        <Modal
          title={
            loginTargetAccount
              ? `登录到：${profileLabel(loginTargetAccount)}`
              : "连接 Telegram 账号"
          }
          onClose={cancelLogin}
          busy={loading}
        >
          <ol className="login-steps" aria-label="登录进度">
            {[
              ["phone", "手机号"],
              ["code", "验证码"],
              ["password", "二步验证"],
            ].map(([id, label], index) => (
              <li key={id} aria-current={step === id ? "step" : undefined}>
                <span>{index + 1}</span>
                {label}
              </li>
            ))}
          </ol>
          {errorMessage && (
            <p className="notice error" role="alert">
              {errorMessage}
            </p>
          )}
          {step === "phone" && (
            <form onSubmit={handleSendCode} className="login-form">
              <label className="form-field">
                <span>手机号</span>
                <input
                  data-initial-focus
                  type="tel"
                  autoComplete="tel"
                  required
                  placeholder="+86…"
                  value={phoneNumber}
                  onChange={(event) => setPhoneNumber(event.target.value)}
                />
                <small>
                  包括国家 / 地区代码，验证码将发送到 Telegram 客户端。
                </small>
              </label>
              <label className="checkbox-line">
                <input
                  type="checkbox"
                  checked={useConfiguredApi}
                  onChange={(event) =>
                    setUseConfiguredApi(event.target.checked)
                  }
                />
                使用全局配置的 API 凭据
              </label>
              {!useConfiguredApi && (
                <div className="field-grid">
                  <label className="form-field">
                    <span>API ID</span>
                    <input
                      required
                      inputMode="numeric"
                      value={apiId}
                      onChange={(event) => setApiId(event.target.value)}
                    />
                  </label>
                  <label className="form-field">
                    <span>API Hash</span>
                    <input
                      required
                      type="password"
                      autoComplete="new-password"
                      value={apiHash}
                      onChange={(event) => setApiHash(event.target.value)}
                    />
                  </label>
                </div>
              )}
              <div className="dialog-actions">
                <button
                  type="button"
                  className="button"
                  disabled={loading}
                  onClick={cancelLogin}
                >
                  取消
                </button>
                <button
                  className="button primary"
                  disabled={loading}
                  type="submit"
                >
                  {loading ? (
                    <Loader2 className="spin" size={17} />
                  ) : (
                    <ArrowRight size={17} />
                  )}
                  发送验证码
                </button>
              </div>
            </form>
          )}
          {step === "code" && (
            <form onSubmit={handleVerifyCode} className="login-form">
              <p>
                验证码已发送至 <strong>{phoneNumber}</strong> 的 Telegram
                客户端。
              </p>
              <label className="form-field">
                <span>验证码</span>
                <input
                  autoFocus
                  className="code-input"
                  required
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  value={code}
                  onChange={(event) => setCode(event.target.value)}
                />
              </label>
              <div className="dialog-actions">
                <button
                  type="button"
                  className="button"
                  disabled={loading}
                  onClick={() => {
                    setErrorMessage("");
                    setStep("phone");
                  }}
                >
                  返回手机号
                </button>
                <button className="button primary" disabled={loading}>
                  验证并继续
                  <ArrowRight size={17} />
                </button>
              </div>
            </form>
          )}
          {step === "password" && (
            <form onSubmit={handleVerifyPassword} className="login-form">
              <p className="notice">
                <ShieldCheck size={19} />
                此账号已启用 Telegram 二步验证。
              </p>
              <label className="form-field">
                <span>二步验证密码</span>
                <input
                  autoFocus
                  required
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
              </label>
              <div className="dialog-actions">
                <button className="button primary" disabled={loading}>
                  完成登录
                  <Check size={17} />
                </button>
              </div>
            </form>
          )}
        </Modal>
      )}
      {createOpen && (
        <Modal
          title="创建账号档案"
          onClose={() => setCreateOpen(false)}
          busy={loading}
        >
          <form
            className="login-form"
            onSubmit={(event) => {
              event.preventDefault();
              handleCreateProfile();
            }}
          >
            <p>先创建一个空档案，稍后可单独登录和启动。</p>
            <label className="form-field">
              <span>档案名称</span>
              <input
                data-initial-focus
                value={newProfileName}
                onChange={(event) => setNewProfileName(event.target.value)}
                placeholder={`账户档案 ${accounts.length + 1}`}
              />
            </label>
            {errorMessage && (
              <p className="notice error" role="alert">
                {errorMessage}
              </p>
            )}
            <div className="dialog-actions">
              <button className="button primary" disabled={loading}>
                创建档案
              </button>
            </div>
          </form>
        </Modal>
      )}
      {renamingProfileId && (
        <Modal
          title="重命名账号档案"
          onClose={() => setRenamingProfileId(null)}
          busy={loading}
        >
          <form
            className="login-form"
            onSubmit={(event) => {
              event.preventDefault();
              const acct = accounts.find(
                (item) => item.id === renamingProfileId,
              );
              if (acct) handleRename(acct);
            }}
          >
            <label className="form-field">
              <span>档案名称</span>
              <input
                data-initial-focus
                required
                value={renameValue}
                onChange={(event) => setRenameValue(event.target.value)}
              />
            </label>
            {errorMessage && (
              <p className="notice error" role="alert">
                {errorMessage}
              </p>
            )}
            <div className="dialog-actions">
              <button className="button primary" disabled={loading}>
                保存名称
              </button>
            </div>
          </form>
        </Modal>
      )}
      {confirmAccount && (
        <Modal
          title={
            confirmAccount.action === "delete" ? "删除账号档案" : "清除登录会话"
          }
          onClose={() => setConfirmAccount(null)}
          busy={loading}
        >
          <p className="confirm-filename">
            {profileLabel(confirmAccount.account)}
          </p>
          <p>
            {confirmAccount.action === "delete"
              ? "将删除此账号档案。此操作不能在界面中撤销，继续前请确认目标账号。"
              : "将清除这个账号的登录会话，之后需要重新登录。若只想暂停同步，请使用「停止同步」。"}
          </p>
          {errorMessage && (
            <p className="notice error" role="alert">
              {errorMessage}
            </p>
          )}
          <div className="dialog-actions">
            <button
              className="button"
              disabled={loading}
              onClick={() => setConfirmAccount(null)}
            >
              取消
            </button>
            <button
              className="button danger"
              disabled={loading}
              onClick={handleDestructiveAction}
            >
              确认{confirmAccount.action === "delete" ? "删除档案" : "清除会话"}
            </button>
          </div>
        </Modal>
      )}
      {botSettingsOpen && (
        <Modal
          title="Bot 投递权限"
          onClose={() => setBotSettingsOpen(false)}
          busy={loading}
        >
          <div className="pattern-options">
            {accessModes.map((item) => (
              <label
                key={item.mode}
                className={`choice-card ${botAccessMode === item.mode ? "chosen" : ""}`}
              >
                <input
                  type="radio"
                  name="bot-access"
                  checked={botAccessMode === item.mode}
                  onChange={() => {
                    setBotAccessMode(item.mode);
                    setAccessFeedback("");
                  }}
                />
                <span>
                  <strong>{item.label}</strong>
                  <small>{item.description}</small>
                </span>
              </label>
            ))}
          </div>
          {botAccessMode === "allowed" && (
            <label className="form-field">
              <span>允许的用户</span>
              <textarea
                id="textarea-bot-allowed-users"
                rows={4}
                value={allowedUsersText}
                onChange={(event) => setAllowedUsersText(event.target.value)}
                placeholder="每行一个用户 ID 或用户名"
              />
            </label>
          )}
          {botAccessMode === "public" && (
            <p className="notice warning">
              <LockKeyhole size={18} />
              公开投递会允许任何人使用 Bot 的下载资源，请确认这是你需要的权限。
            </p>
          )}
          {errorMessage && (
            <p className="notice error" role="alert">
              {errorMessage}
            </p>
          )}
          {accessFeedback && (
            <p className="success-text" role="status">
              {accessFeedback}
            </p>
          )}
          <div className="dialog-actions">
            <button
              className="button primary"
              disabled={loading}
              onClick={handleSaveBotAccess}
            >
              保存权限
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
